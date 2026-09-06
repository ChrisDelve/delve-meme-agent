from __future__ import annotations

import argparse
import math
import sqlite3
from collections import Counter
from pathlib import Path
from statistics import median


DB_PATH = Path("logs/delve_meme.db")

SOL_QUOTE_MINT = "11111111111111111111111111111111"

AUDIT_VERSION = "pump-math-audit-v1"

# Candidate fee schedule observed in current data.
PROTOCOL_FEE_BPS = 95
CREATOR_FEE_BPS = 30

BPS_DENOMINATOR = 10_000


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


def ceil_div(
    numerator: int,
    denominator: int,
) -> int:
    if denominator <= 0:
        raise ValueError(
            "ceil_div denominator must be positive."
        )

    return (
        numerator
        + denominator
        - 1
    ) // denominator


def ceil_fee(
    quote_amount: int,
    fee_bps: int,
) -> int:
    return ceil_div(
        quote_amount * fee_bps,
        BPS_DENOMINATOR,
    )


def load_rows(
    connection: sqlite3.Connection,
    limit: int | None,
) -> list[sqlite3.Row]:
    sql = """
        SELECT
            rowid AS trade_rowid,

            signature,
            mint,
            wallet,
            side,

            slot,
            trade_timestamp,
            observed_at,

            quote_amount,
            token_amount,

            protocol_fee_lamports,
            creator_fee_lamports,

            ix_name,
            mayhem_mode,

            virtual_sol_reserves,
            virtual_token_reserves,

            real_sol_reserves,
            real_token_reserves

        FROM trades

        WHERE
            quote_mint = ?

            AND side IN (
                'BUY',
                'SELL'
            )

            AND quote_amount > 0

            AND token_amount > 0

            AND virtual_sol_reserves
                IS NOT NULL

            AND virtual_token_reserves
                IS NOT NULL

            AND virtual_sol_reserves > 0

            AND virtual_token_reserves > 0

        ORDER BY rowid
    """

    parameters: list = [
        SOL_QUOTE_MINT
    ]

    if limit is not None:
        sql += "\nLIMIT ?"
        parameters.append(limit)

    return connection.execute(
        sql,
        parameters,
    ).fetchall()


def reconstruct_pre_state(
    row: sqlite3.Row,
) -> tuple[int, int] | None:
    quote_amount = int(
        row["quote_amount"]
    )

    token_amount = int(
        row["token_amount"]
    )

    post_sol = int(
        row["virtual_sol_reserves"]
    )

    post_token = int(
        row["virtual_token_reserves"]
    )

    if row["side"] == "BUY":
        pre_sol = (
            post_sol
            - quote_amount
        )

        pre_token = (
            post_token
            + token_amount
        )

    elif row["side"] == "SELL":
        pre_sol = (
            post_sol
            + quote_amount
        )

        pre_token = (
            post_token
            - token_amount
        )

    else:
        return None

    if (
        pre_sol <= 0
        or pre_token <= 0
    ):
        return None

    return (
        pre_sol,
        pre_token,
    )


def expected_trade_amount(
    row: sqlite3.Row,
    pre_sol: int,
    pre_token: int,
) -> int:
    invariant = (
        pre_sol
        * pre_token
    )

    quote_amount = int(
        row["quote_amount"]
    )

    token_amount = int(
        row["token_amount"]
    )

    if row["side"] == "BUY":
        new_sol = (
            pre_sol
            + quote_amount
        )

        expected_post_token = ceil_div(
            invariant,
            new_sol,
        )

        expected_token_out = (
            pre_token
            - expected_post_token
        )

        return expected_token_out

    if row["side"] == "SELL":
        new_token = (
            pre_token
            + token_amount
        )

        expected_post_sol = ceil_div(
            invariant,
            new_token,
        )

        expected_quote_out = (
            pre_sol
            - expected_post_sol
        )

        return expected_quote_out

    raise ValueError(
        f"Unsupported side: {row['side']}"
    )


def actual_trade_amount(
    row: sqlite3.Row,
) -> int:
    if row["side"] == "BUY":
        return int(
            row["token_amount"]
        )

    if row["side"] == "SELL":
        return int(
            row["quote_amount"]
        )

    raise ValueError(
        f"Unsupported side: {row['side']}"
    )


def percentage(
    numerator: int,
    denominator: int,
) -> float:
    if denominator == 0:
        return 0.0

    return (
        100.0
        * numerator
        / denominator
    )


def inferred_bps(
    fee: int,
    quote_amount: int,
) -> float:
    if quote_amount <= 0:
        return 0.0

    return (
        BPS_DENOMINATOR
        * fee
        / quote_amount
    )


def run_audit(
    rows: list[sqlite3.Row],
    examples: int,
) -> None:
    total = 0
    invalid_pre_state = 0

    side_counts = Counter()

    exact_math = Counter()
    within_one = Counter()

    mismatches: list[dict] = []

    protocol_fee_exact = 0
    creator_fee_exact = 0
    both_fee_exact = 0

    protocol_fee_available = 0
    creator_fee_available = 0
    both_fee_available = 0

    protocol_bps_values: list[float] = []
    creator_bps_values: list[float] = []
    total_fee_bps_values: list[float] = []

    fee_profiles = Counter()

    for row in rows:
        pre_state = reconstruct_pre_state(
            row
        )

        if pre_state is None:
            invalid_pre_state += 1
            continue

        pre_sol, pre_token = pre_state

        total += 1

        side = row["side"]

        side_counts[side] += 1

        expected = expected_trade_amount(
            row,
            pre_sol,
            pre_token,
        )

        actual = actual_trade_amount(
            row
        )

        delta = (
            actual
            - expected
        )

        if delta == 0:
            exact_math["ALL"] += 1
            exact_math[side] += 1

        if abs(delta) <= 1:
            within_one["ALL"] += 1
            within_one[side] += 1

        if (
            delta != 0
            and len(mismatches) < examples
        ):
            mismatches.append(
                {
                    "signature": (
                        row["signature"]
                    ),
                    "mint": row["mint"],
                    "side": side,
                    "slot": row["slot"],
                    "quote_amount": (
                        int(
                            row[
                                "quote_amount"
                            ]
                        )
                    ),
                    "token_amount": (
                        int(
                            row[
                                "token_amount"
                            ]
                        )
                    ),
                    "pre_sol": pre_sol,
                    "pre_token": pre_token,
                    "post_sol": (
                        int(
                            row[
                                "virtual_sol_reserves"
                            ]
                        )
                    ),
                    "post_token": (
                        int(
                            row[
                                "virtual_token_reserves"
                            ]
                        )
                    ),
                    "expected": expected,
                    "actual": actual,
                    "delta": delta,
                    "ix_name": row["ix_name"],
                    "mayhem_mode": (
                        row["mayhem_mode"]
                    ),
                }
            )

        quote_amount = int(
            row["quote_amount"]
        )

        protocol_fee = (
            int(
                row[
                    "protocol_fee_lamports"
                ]
            )
            if (
                row[
                    "protocol_fee_lamports"
                ]
                is not None
            )
            else None
        )

        creator_fee = (
            int(
                row[
                    "creator_fee_lamports"
                ]
            )
            if (
                row[
                    "creator_fee_lamports"
                ]
                is not None
            )
            else None
        )

        if protocol_fee is not None:
            protocol_fee_available += 1

            expected_protocol_fee = (
                ceil_fee(
                    quote_amount,
                    PROTOCOL_FEE_BPS,
                )
            )

            if (
                protocol_fee
                == expected_protocol_fee
            ):
                protocol_fee_exact += 1

            protocol_bps_values.append(
                inferred_bps(
                    protocol_fee,
                    quote_amount,
                )
            )

        if creator_fee is not None:
            creator_fee_available += 1

            expected_creator_fee = (
                ceil_fee(
                    quote_amount,
                    CREATOR_FEE_BPS,
                )
            )

            if (
                creator_fee
                == expected_creator_fee
            ):
                creator_fee_exact += 1

            creator_bps_values.append(
                inferred_bps(
                    creator_fee,
                    quote_amount,
                )
            )

        if (
            protocol_fee is not None
            and creator_fee is not None
        ):
            both_fee_available += 1

            expected_protocol_fee = (
                ceil_fee(
                    quote_amount,
                    PROTOCOL_FEE_BPS,
                )
            )

            expected_creator_fee = (
                ceil_fee(
                    quote_amount,
                    CREATOR_FEE_BPS,
                )
            )

            if (
                protocol_fee
                == expected_protocol_fee
                and creator_fee
                == expected_creator_fee
            ):
                both_fee_exact += 1

            protocol_bps = inferred_bps(
                protocol_fee,
                quote_amount,
            )

            creator_bps = inferred_bps(
                creator_fee,
                quote_amount,
            )

            total_fee_bps_values.append(
                protocol_bps
                + creator_bps
            )

            profile = (
                round(
                    protocol_bps,
                    1,
                ),
                round(
                    creator_bps,
                    1,
                ),
            )

            fee_profiles[
                profile
            ] += 1

    print()
    print("=" * 78)

    print(
        "DELVE MEME AGENT — "
        "PUMP TRADE MATH AUDIT"
    )

    print("=" * 78)

    print(
        f"Audit version:            "
        f"{AUDIT_VERSION}"
    )

    print(
        f"Rows loaded:              "
        f"{len(rows):,}"
    )

    print(
        f"Rows audited:             "
        f"{total:,}"
    )

    print(
        f"Invalid reconstructed:    "
        f"{invalid_pre_state:,}"
    )

    print()

    print("CONSTANT-PRODUCT TRADE MATH")
    print("-" * 78)

    print(
        f"Exact overall:            "
        f"{exact_math['ALL']:,} "
        f"("
        f"{percentage(exact_math['ALL'], total):.4f}%"
        f")"
    )

    print(
        f"Within ±1 raw unit:       "
        f"{within_one['ALL']:,} "
        f"("
        f"{percentage(within_one['ALL'], total):.4f}%"
        f")"
    )

    print()

    for side in (
        "BUY",
        "SELL",
    ):
        count = side_counts[
            side
        ]

        print(
            f"{side:<4} exact:              "
            f"{exact_math[side]:,}/"
            f"{count:,} "
            f"("
            f"{percentage(exact_math[side], count):.4f}%"
            f")"
        )

        print(
            f"{side:<4} within ±1:          "
            f"{within_one[side]:,}/"
            f"{count:,} "
            f"("
            f"{percentage(within_one[side], count):.4f}%"
            f")"
        )

    print()
    print("FEE CONTRACT AUDIT")
    print("-" * 78)

    print(
        f"Candidate protocol fee:   "
        f"{PROTOCOL_FEE_BPS} bps"
    )

    print(
        f"Candidate creator fee:    "
        f"{CREATOR_FEE_BPS} bps"
    )

    print(
        f"Candidate total fee:      "
        f"{PROTOCOL_FEE_BPS + CREATOR_FEE_BPS} bps"
    )

    print()

    print(
        f"Protocol exact:           "
        f"{protocol_fee_exact:,}/"
        f"{protocol_fee_available:,} "
        f"("
        f"{percentage(protocol_fee_exact, protocol_fee_available):.3f}%"
        f")"
    )

    print(
        f"Creator exact:            "
        f"{creator_fee_exact:,}/"
        f"{creator_fee_available:,} "
        f"("
        f"{percentage(creator_fee_exact, creator_fee_available):.3f}%"
        f")"
    )

    print(
        f"Both exact:               "
        f"{both_fee_exact:,}/"
        f"{both_fee_available:,} "
        f"("
        f"{percentage(both_fee_exact, both_fee_available):.3f}%"
        f")"
    )

    if protocol_bps_values:
        print()

        print(
            f"Median protocol bps:      "
            f"{median(protocol_bps_values):.4f}"
        )

    if creator_bps_values:
        print(
            f"Median creator bps:       "
            f"{median(creator_bps_values):.4f}"
        )

    if total_fee_bps_values:
        print(
            f"Median total bps:         "
            f"{median(total_fee_bps_values):.4f}"
        )

    if fee_profiles:
        print()
        print(
            "Most common inferred "
            "fee profiles "
            "(protocol bps, creator bps):"
        )

        for (
            protocol_bps,
            creator_bps,
        ), count in fee_profiles.most_common(
            10
        ):
            print(
                f"  "
                f"{protocol_bps:>7.1f}, "
                f"{creator_bps:>7.1f}  "
                f"n={count:,}"
            )

    print()
    print("FIRST MATH MISMATCHES")
    print("-" * 78)

    if not mismatches:
        print(
            "No constant-product mismatches found."
        )
    else:
        for index, item in enumerate(
            mismatches,
            start=1,
        ):
            print()

            print(
                f"[{index}] "
                f"{item['side']} | "
                f"{item['mint']}"
            )

            print(
                f"Signature:        "
                f"{item['signature']}"
            )

            print(
                f"Slot:             "
                f"{item['slot']}"
            )

            print(
                f"Instruction:      "
                f"{item['ix_name']}"
            )

            print(
                f"Mayhem:           "
                f"{item['mayhem_mode']}"
            )

            print(
                f"Pre vSOL:         "
                f"{item['pre_sol']}"
            )

            print(
                f"Pre vToken:       "
                f"{item['pre_token']}"
            )

            print(
                f"Quote amount:     "
                f"{item['quote_amount']}"
            )

            print(
                f"Token amount:     "
                f"{item['token_amount']}"
            )

            print(
                f"Expected amount:  "
                f"{item['expected']}"
            )

            print(
                f"Actual amount:    "
                f"{item['actual']}"
            )

            print(
                f"Delta:            "
                f"{item['delta']:+d}"
            )

    print()
    print("=" * 78)

    print(
        "PRE-STATE SOURCE: "
        "CURRENT TRADE'S OWN POST-STATE"
    )

    print(
        "NEIGHBORING TRADE ORDER REQUIRED: NO"
    )

    print(
        "DATABASE MUTATION: NO"
    )

    print(
        "MODEL / SIGNAL CHANGES: NO"
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )

    print("=" * 78)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--limit",
        type=int,
        default=50_000,
        help=(
            "Number of SOL-quoted Pump trades "
            "to audit. Default: 50000."
        ),
    )

    parser.add_argument(
        "--examples",
        type=int,
        default=5,
        help=(
            "Number of constant-product "
            "mismatches to display."
        ),
    )

    args = parser.parse_args()

    if (
        args.limit is not None
        and args.limit <= 0
    ):
        raise ValueError(
            "--limit must be positive."
        )

    with get_connection() as connection:
        rows = load_rows(
            connection,
            args.limit,
        )

    if not rows:
        raise RuntimeError(
            "No eligible SOL-quoted trades found."
        )

    run_audit(
        rows,
        args.examples,
    )


if __name__ == "__main__":
    main()