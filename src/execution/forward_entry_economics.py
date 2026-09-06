from __future__ import annotations

import argparse
import math
import sqlite3
import time
from collections import Counter
from pathlib import Path
from statistics import median

import numpy as np

from src.execution.pump_execution_simulator import (
    SIMULATOR_VERSION,
    calculate_exact_input_buy,
    reconstruct_pre_state_from_trade,
)


DB_PATH = Path("logs/delve_meme.db")

ANALYSIS_VERSION = "forward-entry-economics-v1"

COHORT_VERSION = "forward-confirmation-cohort-v1"

GRADER_VERSION = "model-shadow-grader-v1"

ARTIFACT_SHA256 = (
    "22e40605a87d06bddc382edf877274c628e40a1845530ef00a043ea4a7c1177c"
)

SOL_QUOTE_MINT = "11111111111111111111111111111111"

LAMPORTS_PER_SOL = 1_000_000_000
BPS_DENOMINATOR = 10_000

#
# These are probes, NOT final position sizes.
#
ORDER_SIZES_SOL = (
    0.005,
    0.010,
    0.025,
    0.050,
    0.100,
)

#
# Fee inference is historical only.
# The live agent will eventually use an
# on-chain fee resolver.
#
MAX_INFERRED_FEE_BPS = 10_000


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(
        DB_PATH,
        timeout=30.0,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA busy_timeout = 30000"
    )

    return connection


def init_table(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        forward_entry_economics (
            prediction_signature TEXT NOT NULL,

            cohort_version TEXT NOT NULL,
            analysis_version TEXT NOT NULL,
            simulator_version TEXT NOT NULL,

            scenario_key TEXT NOT NULL,

            mint TEXT NOT NULL,
            predicted_at INTEGER NOT NULL,
            probability_2x_15m REAL,

            execution_signature TEXT NOT NULL,
            execution_delay_seconds INTEGER NOT NULL,

            order_size_lamports INTEGER NOT NULL,

            protocol_fee_bps INTEGER,
            creator_fee_bps INTEGER,

            fee_inference_status TEXT NOT NULL,

            trigger_price_raw REAL,
            execution_proxy_price_raw REAL,

            market_drift_bps REAL,

            executable INTEGER NOT NULL,
            ineligible_reason TEXT,

            tokens_out INTEGER,

            curve_quote_in INTEGER,

            pump_fee_lamports INTEGER,

            network_overhead_lamports INTEGER,

            total_wallet_cost_lamports INTEGER,

            curve_price_impact_bps REAL,

            all_in_entry_price_raw REAL,

            all_in_vs_signal_bps REAL,

            recorded_at INTEGER NOT NULL,

            PRIMARY KEY (
                prediction_signature,
                order_size_lamports,
                scenario_key
            )
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_forward_entry_economics_mint
        ON forward_entry_economics (
            mint,
            order_size_lamports
        )
        """
    )

    connection.commit()


def verify_cohort(
    connection: sqlite3.Connection,
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT
            cohort_version,
            prediction_count,
            mint_count,
            prediction_set_sha256,
            mint_set_sha256

        FROM forward_confirmation_cohorts

        WHERE cohort_version = ?
        """,
        (COHORT_VERSION,),
    ).fetchone()

    if row is None:
        raise RuntimeError(
            "Frozen confirmation cohort not found."
        )

    if int(row["mint_count"]) < 500:
        raise RuntimeError(
            "Frozen cohort contains fewer than "
            "500 distinct mints."
        )

    return row


def load_rows(
    connection: sqlite3.Connection,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT
            cp.entry_signature
                AS prediction_signature,

            cp.mint,
            cp.predicted_at,

            p.probability_2x_15m,

            1.0
            * trigger.virtual_sol_reserves
            / trigger.virtual_token_reserves
                AS signal_post_spot_raw,

            1.0
            * (
                t.virtual_sol_reserves
                - t.quote_amount
            )
            / (
                t.virtual_token_reserves
                + t.token_amount
            )
                AS execution_pre_spot_raw,
            
            o.execution_signature,
            o.execution_delay_seconds,

            o.trigger_price_raw,
            o.execution_entry_price_raw,

            t.signature,
            t.wallet,
            t.side,
            t.quote_mint,

            t.slot,
            t.trade_timestamp,
            t.observed_at,

            t.quote_amount,
            t.token_amount,

            t.protocol_fee_lamports,
            t.creator_fee_lamports,

            t.ix_name,
            t.mayhem_mode,

            t.virtual_sol_reserves,
            t.virtual_token_reserves,

            t.real_sol_reserves,
            t.real_token_reserves

        FROM
            forward_confirmation_cohort_predictions cp

        JOIN model_shadow_predictions p
            ON p.entry_signature
               = cp.entry_signature

        JOIN trades trigger
            ON trigger.signature
                = cp.entry_signature
               
        JOIN model_shadow_outcomes o
            ON o.prediction_signature
               = cp.entry_signature

        JOIN trades t
            ON t.signature
               = o.execution_signature

        WHERE
            cp.cohort_version = ?

            AND o.grader_version = ?

            AND o.artifact_sha256 = ?

            AND t.quote_mint = ?

            AND t.side = 'BUY'

        ORDER BY
            cp.predicted_at,
            cp.entry_signature
        """,
        (
            COHORT_VERSION,
            GRADER_VERSION,
            ARTIFACT_SHA256,
            SOL_QUOTE_MINT,
        ),
    ).fetchall()


def infer_fee_bps_range(
    *,
    quote_amount: int,
    fee_lamports: int,
) -> tuple[int, int] | None:
    """
    For:

        fee = ceil(
            quote_amount * fee_bps / 10_000
        )

    solve for all integer fee_bps values
    consistent with the observed fee.

    We require an exact unique historical
    fee rate before replaying a hypothetical
    differently-sized order.
    """

    if quote_amount <= 0:
        return None

    if fee_lamports < 0:
        return None

    if fee_lamports == 0:
        return (0, 0)

    lower = (
        (
            (fee_lamports - 1)
            * BPS_DENOMINATOR
        )
        // quote_amount
    ) + 1

    upper = (
        fee_lamports
        * BPS_DENOMINATOR
    ) // quote_amount

    lower = max(
        0,
        lower,
    )

    upper = min(
        MAX_INFERRED_FEE_BPS,
        upper,
    )

    if lower > upper:
        return None

    return (
        lower,
        upper,
    )


def resolve_historical_fee_bps(
    row: sqlite3.Row,
) -> tuple[
    str,
    int | None,
    int | None,
]:
    quote_amount = int(
        row["quote_amount"]
    )

    protocol_fee = row[
        "protocol_fee_lamports"
    ]

    creator_fee = row[
        "creator_fee_lamports"
    ]

    if (
        protocol_fee is None
        or creator_fee is None
    ):
        return (
            "MISSING_FEE_DATA",
            None,
            None,
        )

    protocol_range = (
        infer_fee_bps_range(
            quote_amount=quote_amount,
            fee_lamports=int(
                protocol_fee
            ),
        )
    )

    creator_range = (
        infer_fee_bps_range(
            quote_amount=quote_amount,
            fee_lamports=int(
                creator_fee
            ),
        )
    )

    if protocol_range is None:
        return (
            "PROTOCOL_FEE_NO_EXACT_BPS",
            None,
            None,
        )

    if creator_range is None:
        return (
            "CREATOR_FEE_NO_EXACT_BPS",
            None,
            None,
        )

    if (
        protocol_range[0]
        != protocol_range[1]
    ):
        return (
            "AMBIGUOUS_PROTOCOL_FEE_BPS",
            None,
            None,
        )

    if (
        creator_range[0]
        != creator_range[1]
    ):
        return (
            "AMBIGUOUS_CREATOR_FEE_BPS",
            None,
            None,
        )

    return (
        "RESOLVED",
        protocol_range[0],
        creator_range[0],
    )


def sol_to_lamports(
    amount_sol: float,
) -> int:
    return int(
        round(
            amount_sol
            * LAMPORTS_PER_SOL
        )
    )


def scenario_key(
    *,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    rent_lamports: int,
    slippage_bps: int,
) -> str:
    return (
        f"base={base_network_fee_lamports};"
        f"priority={priority_fee_lamports};"
        f"rent={rent_lamports};"
        f"slippage={slippage_bps}"
    )


def safe_bps(
    numerator_price: float | None,
    denominator_price: float | None,
) -> float | None:
    if (
        numerator_price is None
        or denominator_price is None
        or denominator_price <= 0
        or numerator_price <= 0
    ):
        return None

    return (
        (
            numerator_price
            / denominator_price
        )
        - 1.0
    ) * BPS_DENOMINATOR


def record_unresolved(
    connection: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    order_size_lamports: int,
    scenario: str,
    fee_status: str,
    network_overhead: int,
) -> None:
    market_drift = safe_bps(
        row["execution_pre_spot_raw"],
        row["signal_post_spot_raw"],
    )

    connection.execute(
        """
        INSERT OR REPLACE INTO
        forward_entry_economics (
            prediction_signature,

            cohort_version,
            analysis_version,
            simulator_version,

            scenario_key,

            mint,
            predicted_at,
            probability_2x_15m,

            execution_signature,
            execution_delay_seconds,

            order_size_lamports,

            protocol_fee_bps,
            creator_fee_bps,

            fee_inference_status,

            trigger_price_raw,
            execution_proxy_price_raw,

            market_drift_bps,

            executable,
            ineligible_reason,

            tokens_out,

            curve_quote_in,

            pump_fee_lamports,

            network_overhead_lamports,

            total_wallet_cost_lamports,

            curve_price_impact_bps,

            all_in_entry_price_raw,

            all_in_vs_signal_bps,

            recorded_at
        )

        VALUES (
            ?, ?, ?, ?,
            ?,
            ?, ?, ?,
            ?, ?,
            ?,
            NULL, NULL,
            ?,
            ?, ?,
            ?,
            0, ?,
            NULL,
            NULL,
            NULL,
            ?,
            NULL,
            NULL,
            NULL,
            NULL,
            ?
        )
        """,
        (
            row[
                "prediction_signature"
            ],

            COHORT_VERSION,
            ANALYSIS_VERSION,
            SIMULATOR_VERSION,

            scenario,

            row["mint"],
            row["predicted_at"],
            row[
                "probability_2x_15m"
            ],

            row[
                "execution_signature"
            ],

            row[
                "execution_delay_seconds"
            ],

            order_size_lamports,

            fee_status,

            row["trigger_price_raw"],
            row[
                "execution_entry_price_raw"
            ],

            market_drift,

            fee_status,

            network_overhead,

            int(time.time()),
        ),
    )


def record_simulation(
    connection: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    order_size_lamports: int,
    scenario: str,
    protocol_fee_bps: int,
    creator_fee_bps: int,
    result,
) -> None:
    market_drift = safe_bps(
        row["execution_pre_spot_raw"],
        row["signal_post_spot_raw"],
    )

    all_in_vs_signal = safe_bps(
        (
            result.all_in_entry_price_raw
            if result.executable
            else None
        ),
        row["signal_post_spot_raw"],
    )

    connection.execute(
        """
        INSERT OR REPLACE INTO
        forward_entry_economics (
            prediction_signature,

            cohort_version,
            analysis_version,
            simulator_version,

            scenario_key,

            mint,
            predicted_at,
            probability_2x_15m,

            execution_signature,
            execution_delay_seconds,

            order_size_lamports,

            protocol_fee_bps,
            creator_fee_bps,

            fee_inference_status,

            trigger_price_raw,
            execution_proxy_price_raw,

            market_drift_bps,

            executable,
            ineligible_reason,

            tokens_out,

            curve_quote_in,

            pump_fee_lamports,

            network_overhead_lamports,

            total_wallet_cost_lamports,

            curve_price_impact_bps,

            all_in_entry_price_raw,

            all_in_vs_signal_bps,

            recorded_at
        )

        VALUES (
            ?, ?, ?, ?,
            ?,
            ?, ?, ?,
            ?, ?,
            ?,
            ?, ?,
            'RESOLVED',
            ?, ?,
            ?,
            ?, ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?
        )
        """,
        (
            row[
                "prediction_signature"
            ],

            COHORT_VERSION,
            ANALYSIS_VERSION,
            SIMULATOR_VERSION,

            scenario,

            row["mint"],
            row["predicted_at"],
            row[
                "probability_2x_15m"
            ],

            row[
                "execution_signature"
            ],

            row[
                "execution_delay_seconds"
            ],

            order_size_lamports,

            protocol_fee_bps,
            creator_fee_bps,

            row["trigger_price_raw"],
            row[
                "execution_entry_price_raw"
            ],

            market_drift,

            int(
                result.executable
            ),

            result.ineligible_reason,

            result.tokens_out,

            result.curve_quote_in,

            result.pump_fees_total,

            result.total_transaction_overhead_lamports,

            result.total_wallet_cost_lamports,

            result.price_impact_bps,

            (
                result.all_in_entry_price_raw
                if result.executable
                else None
            ),

            all_in_vs_signal,

            int(time.time()),
        ),
    )


def mint_balanced_weights(
    mints: list[str],
) -> np.ndarray:
    counts = Counter(
        mints
    )

    return np.asarray(
        [
            1.0 / counts[mint]
            for mint in mints
        ],
        dtype=np.float64,
    )


def weighted_percentile(
    values: np.ndarray,
    weights: np.ndarray,
    percentile: float,
) -> float:
    if len(values) == 0:
        return float("nan")

    order = np.argsort(
        values,
        kind="mergesort",
    )

    ordered_values = values[
        order
    ]

    ordered_weights = weights[
        order
    ]

    cumulative = np.cumsum(
        ordered_weights
    )

    total = cumulative[-1]

    threshold = (
        percentile
        / 100.0
        * total
    )

    index = int(
        np.searchsorted(
            cumulative,
            threshold,
            side="left",
        )
    )

    index = min(
        index,
        len(
            ordered_values
        ) - 1,
    )

    return float(
        ordered_values[
            index
        ]
    )


def print_summary(
    connection: sqlite3.Connection,
    *,
    scenario: str,
) -> None:
    print()
    print("=" * 92)

    print(
        "DELVE MEME AGENT — "
        "FORWARD ENTRY ECONOMICS"
    )

    print("=" * 92)

    print(
        f"Analysis version:       "
        f"{ANALYSIS_VERSION}"
    )

    print(
        f"Cohort:                 "
        f"{COHORT_VERSION}"
    )

    print(
        f"Simulator:              "
        f"{SIMULATOR_VERSION}"
    )

    print(
        f"Scenario:               "
        f"{scenario}"
    )

    print()
    print(
        "Order sizes are execution probes, "
        "NOT final position sizing."
    )

    for order_size_sol in (
        ORDER_SIZES_SOL
    ):
        order_lamports = (
            sol_to_lamports(
                order_size_sol
            )
        )

        rows = connection.execute(
            """
            SELECT
                mint,

                fee_inference_status,

                executable,
                ineligible_reason,

                market_drift_bps,

                curve_price_impact_bps,

                all_in_vs_signal_bps,

                execution_delay_seconds,

                protocol_fee_bps,
                creator_fee_bps

            FROM forward_entry_economics

            WHERE
                cohort_version = ?

                AND analysis_version = ?

                AND order_size_lamports = ?

                AND scenario_key = ?
            """,
            (
                COHORT_VERSION,
                ANALYSIS_VERSION,
                order_lamports,
                scenario,
            ),
        ).fetchall()

        total = len(rows)

        resolved = [
            row
            for row in rows
            if (
                row[
                    "fee_inference_status"
                ]
                == "RESOLVED"
            )
        ]

        executable = [
            row
            for row in resolved
            if row["executable"] == 1
        ]

        total_mints = len(
            {
                row["mint"]
                for row in rows
            }
        )

        resolved_mints = len(
            {
                row["mint"]
                for row in resolved
            }
        )

        executable_mints = len(
            {
                row["mint"]
                for row in executable
            }
        )

        print()
        print("-" * 92)

        print(
            f"ORDER SIZE: "
            f"{order_size_sol:.3f} SOL"
        )

        print("-" * 92)

        print(
            f"Cohort rows:            "
            f"{total:,}"
        )

        print(
            f"Cohort mints:           "
            f"{total_mints:,}"
        )

        print(
            f"Fee-resolved rows:      "
            f"{len(resolved):,} "
            f"("
            f"{100.0 * len(resolved) / total if total else 0.0:.2f}%"
            f")"
        )

        print(
            f"Fee-resolved mints:     "
            f"{resolved_mints:,}"
        )

        print(
            f"Executable rows:        "
            f"{len(executable):,} "
            f"("
            f"{100.0 * len(executable) / len(resolved) if resolved else 0.0:.2f}% "
            f"of fee-resolved"
            f")"
        )

        print(
            f"Executable mints:       "
            f"{executable_mints:,}"
        )

        if not executable:
            continue

        mints = [
            row["mint"]
            for row in executable
        ]

        weights = mint_balanced_weights(
            mints
        )

        market_drift = np.asarray(
            [
                row[
                    "market_drift_bps"
                ]
                for row in executable
                if (
                    row[
                        "market_drift_bps"
                    ]
                    is not None
                )
            ],
            dtype=np.float64,
        )

        curve_impact = np.asarray(
            [
                row[
                    "curve_price_impact_bps"
                ]
                for row in executable
                if (
                    row[
                        "curve_price_impact_bps"
                    ]
                    is not None
                )
            ],
            dtype=np.float64,
        )

        all_in = np.asarray(
            [
                row[
                    "all_in_vs_signal_bps"
                ]
                for row in executable
                if (
                    row[
                        "all_in_vs_signal_bps"
                    ]
                    is not None
                )
            ],
            dtype=np.float64,
        )

        delays = np.asarray(
            [
                row[
                    "execution_delay_seconds"
                ]
                for row in executable
            ],
            dtype=np.float64,
        )

        #
        # All executable rows should have these
        # values, so their arrays should align
        # with weights. Fail rather than silently
        # report malformed statistics.
        #
        if (
            len(market_drift)
            != len(executable)
            or len(curve_impact)
            != len(executable)
            or len(all_in)
            != len(executable)
        ):
            raise RuntimeError(
                "Executable economics rows contain "
                "unexpected NULL metrics."
            )

        print()
        print(
            "MINT-BALANCED ENTRY DEGRADATION"
        )

        print(
            f"Market drift median:    "
            f"{weighted_percentile(market_drift, weights, 50):+.2f} bps"
        )

        print(
            f"Market drift P95:       "
            f"{weighted_percentile(market_drift, weights, 95):+.2f} bps"
        )

        print()

        print(
            f"Own curve impact med:   "
            f"{weighted_percentile(curve_impact, weights, 50):+.2f} bps"
        )

        print(
            f"Own curve impact P95:   "
            f"{weighted_percentile(curve_impact, weights, 95):+.2f} bps"
        )

        print()

        print(
            f"ALL-IN vs signal med:   "
            f"{weighted_percentile(all_in, weights, 50):+.2f} bps"
        )

        print(
            f"ALL-IN vs signal P95:   "
            f"{weighted_percentile(all_in, weights, 95):+.2f} bps"
        )

        print()

        print(
            f"Execution delay median: "
            f"{weighted_percentile(delays, weights, 50):.2f}s"
        )

        print(
            f"Execution delay P95:    "
            f"{weighted_percentile(delays, weights, 95):.2f}s"
        )

        for threshold_bps in (
            500,
            1000,
            2000,
            4000,
        ):
            rate = float(
                np.average(
                    (
                        all_in
                        >= threshold_bps
                    ).astype(
                        np.float64
                    ),
                    weights=weights,
                )
            )

            print(
                f"All-in degradation >= "
                f"{threshold_bps / 100:.0f}%: "
                f"{100.0 * rate:.2f}%"
            )

        reasons = Counter(
            row[
                "ineligible_reason"
            ]
            for row in resolved
            if row["executable"] == 0
        )

        if reasons:
            print()
            print(
                "EXECUTION FAILURES"
            )

            for reason, count in (
                reasons.most_common()
            ):
                print(
                    f"  "
                    f"{reason or 'UNKNOWN':<40}"
                    f"{count:>8,d}"
                )

    print()
    print("=" * 92)

    print(
        "OUTCOME / EXIT LABELS READ: NO"
    )

    print(
        "THRESHOLD SEARCH: NO"
    )

    print(
        "POSITION SIZING POLICY: NOT SET"
    )

    print(
        "PRIORITY-FEE MODEL: "
        "FIXED SCENARIO INPUT"
    )

    print(
        "LIVE FEE RESOLVER: NOT YET CONNECTED"
    )

    print(
        "TRANSACTION SUBMISSION: NO"
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )

    print("=" * 92)


def run(
    *,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    rent_lamports: int,
    slippage_bps: int,
    rebuild: bool,
) -> None:
    for name, value in (
        (
            "base_network_fee_lamports",
            base_network_fee_lamports,
        ),
        (
            "priority_fee_lamports",
            priority_fee_lamports,
        ),
        (
            "rent_lamports",
            rent_lamports,
        ),
        (
            "slippage_bps",
            slippage_bps,
        ),
    ):
        if value < 0:
            raise ValueError(
                f"{name} cannot be negative."
            )

    scenario = scenario_key(
        base_network_fee_lamports=(
            base_network_fee_lamports
        ),
        priority_fee_lamports=(
            priority_fee_lamports
        ),
        rent_lamports=rent_lamports,
        slippage_bps=slippage_bps,
    )

    network_overhead = (
        base_network_fee_lamports
        + priority_fee_lamports
        + rent_lamports
    )

    started = time.time()

    with get_connection() as connection:
        init_table(
            connection
        )

        cohort = verify_cohort(
            connection
        )

        rows = load_rows(
            connection
        )

        if (
            len(rows)
            != int(
                cohort[
                    "prediction_count"
                ]
            )
        ):
            raise RuntimeError(
                "Loaded entry-economics rows "
                "do not match frozen cohort size."
            )

        if rebuild:
            connection.execute(
                """
                DELETE FROM
                    forward_entry_economics

                WHERE
                    cohort_version = ?

                    AND analysis_version = ?

                    AND scenario_key = ?
                """,
                (
                    COHORT_VERSION,
                    ANALYSIS_VERSION,
                    scenario,
                ),
            )

            connection.commit()

        print()
        print("=" * 92)

        print(
            "DELVE MEME AGENT — "
            "FORWARD ENTRY ECONOMICS REPLAY"
        )

        print("=" * 92)

        print(
            f"Frozen predictions:     "
            f"{len(rows):,}"
        )

        print(
            f"Frozen mints:           "
            f"{cohort['mint_count']:,}"
        )

        print(
            f"Order-size probes:      "
            f"{', '.join(f'{x:.3f}' for x in ORDER_SIZES_SOL)} SOL"
        )

        print(
            f"Base network fee:       "
            f"{base_network_fee_lamports:,} lamports"
        )

        print(
            f"Priority fee:           "
            f"{priority_fee_lamports:,} lamports"
        )

        print(
            f"Rent/account overhead:  "
            f"{rent_lamports:,} lamports"
        )

        print(
            f"Slippage input:         "
            f"{slippage_bps} bps"
        )

        print()
        print(
            "Historical fee rates are "
            "inferred from each execution "
            "anchor's observed fees."
        )

        print("=" * 92)

        dispositions = Counter()

        for index, row in enumerate(
            rows,
            start=1,
        ):
            (
                fee_status,
                protocol_fee_bps,
                creator_fee_bps,
            ) = resolve_historical_fee_bps(
                row
            )

            for order_size_sol in (
                ORDER_SIZES_SOL
            ):
                order_lamports = (
                    sol_to_lamports(
                        order_size_sol
                    )
                )

                if (
                    fee_status
                    != "RESOLVED"
                ):
                    record_unresolved(
                        connection,
                        row=row,
                        order_size_lamports=(
                            order_lamports
                        ),
                        scenario=scenario,
                        fee_status=(
                            fee_status
                        ),
                        network_overhead=(
                            network_overhead
                        ),
                    )

                    dispositions[
                        fee_status
                    ] += 1

                    continue

                try:
                    state = (
                        reconstruct_pre_state_from_trade(
                            row
                        )
                    )

                    result = (
                        calculate_exact_input_buy(
                            state=state,

                            spendable_quote_in=(
                                order_lamports
                            ),

                            protocol_fee_bps=(
                                protocol_fee_bps
                            ),

                            creator_fee_bps=(
                                creator_fee_bps
                            ),

                            slippage_bps=(
                                slippage_bps
                            ),

                            base_network_fee_lamports=(
                                base_network_fee_lamports
                            ),

                            priority_fee_lamports=(
                                priority_fee_lamports
                            ),

                            rent_lamports=(
                                rent_lamports
                            ),
                        )
                    )

                    record_simulation(
                        connection,
                        row=row,
                        order_size_lamports=(
                            order_lamports
                        ),
                        scenario=scenario,
                        protocol_fee_bps=(
                            protocol_fee_bps
                        ),
                        creator_fee_bps=(
                            creator_fee_bps
                        ),
                        result=result,
                    )

                    if result.executable:
                        dispositions[
                            "EXECUTABLE"
                        ] += 1
                    else:
                        dispositions[
                            result.ineligible_reason
                            or "NOT_EXECUTABLE"
                        ] += 1

                except Exception as error:
                    error_status = (
                        "SIMULATION_ERROR_"
                        + type(error).__name__
                    )

                    record_unresolved(
                        connection,
                        row=row,
                        order_size_lamports=(
                            order_lamports
                        ),
                        scenario=scenario,
                        fee_status=(
                            error_status
                        ),
                        network_overhead=(
                            network_overhead
                        ),
                    )

                    dispositions[
                        error_status
                    ] += 1

            if index % 250 == 0:
                connection.commit()

            if index % 5000 == 0:
                print(
                    f"{index:,}/"
                    f"{len(rows):,} "
                    f"predictions processed"
                )

        connection.commit()

        print()
        print("PROCESSING DISPOSITIONS")
        print("-" * 92)

        total_attempts = (
            len(rows)
            * len(
                ORDER_SIZES_SOL
            )
        )

        for status, count in (
            dispositions.most_common()
        ):
            print(
                f"{status:<45}"
                f"{count:>10,d} "
                f"("
                f"{100.0 * count / total_attempts:6.2f}%"
                f")"
            )

        print_summary(
            connection,
            scenario=scenario,
        )

    print()
    print(
        f"Runtime: "
        f"{time.time() - started:.2f}s"
    )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--base-network-fee-lamports",
        type=int,
        default=5000,
    )

    parser.add_argument(
        "--priority-fee-lamports",
        type=int,
        default=0,
    )

    parser.add_argument(
        "--rent-lamports",
        type=int,
        default=0,
    )

    parser.add_argument(
        "--slippage-bps",
        type=int,
        default=500,
    )

    parser.add_argument(
        "--rebuild",
        action="store_true",
    )

    args = parser.parse_args()

    run(
        base_network_fee_lamports=(
            args.base_network_fee_lamports
        ),

        priority_fee_lamports=(
            args.priority_fee_lamports
        ),

        rent_lamports=(
            args.rent_lamports
        ),

        slippage_bps=(
            args.slippage_bps
        ),

        rebuild=args.rebuild,
    )


if __name__ == "__main__":
    main()