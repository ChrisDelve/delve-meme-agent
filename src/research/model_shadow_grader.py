from __future__ import annotations

import argparse
import math
import sqlite3
import time
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score


DB_PATH = Path("logs/delve_meme.db")

GRADER_VERSION = "model-shadow-grader-v1"
SHADOW_VERSION = "model-shadow-v1"

ARTIFACT_VERSION = "validated-signal-artifact-v1"
ARTIFACT_SHA256 = (
    "22e40605a87d06bddc382edf877274c628e40a1845530ef00a043ea4a7c1177c"
)

SOL_QUOTE_MINT = "11111111111111111111111111111111"

TARGET_HORIZON_SECONDS = 900
MAX_EXECUTION_DELAY_SECONDS = 10

EPSILON = 1e-15


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
        CREATE TABLE IF NOT EXISTS model_shadow_outcomes (
            prediction_signature TEXT PRIMARY KEY,

            grader_version TEXT NOT NULL,
            shadow_version TEXT NOT NULL,

            artifact_version TEXT NOT NULL,
            artifact_sha256 TEXT NOT NULL,

            mint TEXT NOT NULL,
            wallet TEXT NOT NULL,
            quote_mint TEXT NOT NULL,

            trigger_slot INTEGER,
            trigger_trade_timestamp INTEGER NOT NULL,
            predicted_at INTEGER NOT NULL,

            probability_2x_15m REAL NOT NULL,
            development_baseline_probability REAL NOT NULL,

            execution_signature TEXT NOT NULL,
            execution_slot INTEGER,
            execution_trade_timestamp INTEGER NOT NULL,
            execution_observed_at INTEGER NOT NULL,
            execution_delay_seconds INTEGER NOT NULL,

            trigger_price_raw REAL,
            execution_entry_price_raw REAL NOT NULL,

            entry_vs_trigger_bps REAL,

            peak_price_15m_raw REAL NOT NULL,
            peak_multiple_15m REAL NOT NULL,

            hit_2x_15m INTEGER NOT NULL,

            horizon_end_observed_at INTEGER NOT NULL,

            coverage_id INTEGER NOT NULL,

            graded_at INTEGER NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_model_shadow_outcomes_mint
        ON model_shadow_outcomes (
            mint
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_model_shadow_outcomes_predicted
        ON model_shadow_outcomes (
            predicted_at
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_trades_shadow_execution
        ON trades (
            mint,
            quote_mint,
            side,
            observed_at,
            slot
        )
        """
    )

    connection.commit()


def verify_prediction_contract(
    connection: sqlite3.Connection,
) -> None:
    row = connection.execute(
        """
        SELECT
            COUNT(*) AS rows,

            COUNT(
                DISTINCT artifact_sha256
            ) AS artifact_hashes,

            COUNT(
                DISTINCT artifact_version
            ) AS artifact_versions,

            COUNT(
                DISTINCT shadow_version
            ) AS shadow_versions

        FROM model_shadow_predictions

        WHERE
            model_eligible = 1
            AND shadow_version = ?
            AND artifact_version = ?
            AND artifact_sha256 = ?
        """,
        (
            SHADOW_VERSION,
            ARTIFACT_VERSION,
            ARTIFACT_SHA256,
        ),
    ).fetchone()

    if row["rows"] == 0:
        raise RuntimeError(
            "No eligible predictions exist for the "
            "locked shadow/artifact contract."
        )

    if row["artifact_hashes"] != 1:
        raise RuntimeError(
            "Artifact hash contract is not singular."
        )

    if row["artifact_versions"] != 1:
        raise RuntimeError(
            "Artifact version contract is not singular."
        )

    if row["shadow_versions"] != 1:
        raise RuntimeError(
            "Shadow version contract is not singular."
        )


def load_predictions_to_grade(
    connection: sqlite3.Connection,
    *,
    limit: int | None,
) -> list[sqlite3.Row]:
    now = int(time.time())

    sql = """
        SELECT
            p.*

        FROM model_shadow_predictions p

        LEFT JOIN model_shadow_outcomes o
            ON o.prediction_signature
               = p.entry_signature

        WHERE
            p.model_eligible = 1

            AND p.shadow_version = ?

            AND p.artifact_version = ?

            AND p.artifact_sha256 = ?

            AND p.quote_mint = ?

            AND p.probability_2x_15m
                IS NOT NULL

            AND p.predicted_at
                IS NOT NULL

            AND p.predicted_at
                <= ?

            AND o.prediction_signature
                IS NULL

        ORDER BY
            p.predicted_at,
            p.entry_signature
    """

    parameters: list = [
        SHADOW_VERSION,
        ARTIFACT_VERSION,
        ARTIFACT_SHA256,
        SOL_QUOTE_MINT,
        now - (
            MAX_EXECUTION_DELAY_SECONDS
            + TARGET_HORIZON_SECONDS
        ),
    ]

    if limit is not None:
        sql += "\nLIMIT ?"
        parameters.append(limit)

    return connection.execute(
        sql,
        parameters,
    ).fetchall()


def find_execution_trade(
    connection: sqlite3.Connection,
    prediction: sqlite3.Row,
) -> sqlite3.Row | None:
    predicted_at = int(
        prediction["predicted_at"]
    )

    maximum_observed_at = (
        predicted_at
        + MAX_EXECUTION_DELAY_SECONDS
    )


    return connection.execute(
        """
        SELECT
            signature,
            mint,
            quote_mint,
            side,
            slot,
            trade_timestamp,
            observed_at,
            quote_amount,
            token_amount,

            (
                1.0 * quote_amount
                / token_amount
            ) AS price_raw

        FROM trades

        WHERE
            mint = ?

            AND quote_mint = ?

            AND side = 'BUY'

            AND signature != ?

            AND quote_amount > 0

            AND token_amount > 0

            AND observed_at IS NOT NULL

            AND observed_at > ?

            AND observed_at <= ?

        ORDER BY
            observed_at,
            slot,
            trade_timestamp,
            signature

        LIMIT 1
        """,
        (
            prediction["mint"],
            prediction["quote_mint"],
            prediction["entry_signature"],
            predicted_at,
            maximum_observed_at,
        ),
    ).fetchone()


def find_covering_interval(
    connection: sqlite3.Connection,
    *,
    predicted_at: int,
    horizon_end: int,
) -> sqlite3.Row | None:
    now = int(time.time())

    return connection.execute(
        """
        SELECT
            id,
            started_at,
            ended_at,
            valid,
            close_reason

        FROM collector_coverage

        WHERE
            valid = 1

            AND started_at <= ?

            AND COALESCE(
                    ended_at,
                    ?
                ) >= ?

        ORDER BY
            started_at DESC,
            id DESC

        LIMIT 1
        """,
        (
            predicted_at,
            now,
            horizon_end,
        ),
    ).fetchone()


def trigger_price(
    connection: sqlite3.Connection,
    signature: str,
) -> float | None:
    row = connection.execute(
        """
        SELECT
            CASE
                WHEN
                    quote_amount > 0
                    AND token_amount > 0

                THEN
                    1.0 * quote_amount
                    / token_amount

                ELSE NULL
            END AS price_raw

        FROM trades

        WHERE signature = ?

        LIMIT 1
        """,
        (signature,),
    ).fetchone()

    if row is None:
        return None

    if row["price_raw"] is None:
        return None

    return float(
        row["price_raw"]
    )


def calculate_peak(
    connection: sqlite3.Connection,
    *,
    mint: str,
    quote_mint: str,
    execution_observed_at: int,
    execution_slot: int | None,
    execution_price: float,
) -> tuple[float, float]:
    horizon_end = (
        execution_observed_at
        + TARGET_HORIZON_SECONDS
    )

    row = connection.execute(
        """
        SELECT
            MAX(
                1.0 * quote_amount
                / token_amount
            ) AS peak_price_raw

        FROM trades

        WHERE
            mint = ?

            AND quote_mint = ?

            AND quote_amount > 0

            AND token_amount > 0

            AND observed_at IS NOT NULL

            AND observed_at >= ?

            AND observed_at <= ?

            AND (
                observed_at > ?

                OR (
                    observed_at = ?

                    AND (
                        ? IS NULL

                        OR slot IS NULL

                        OR slot >= ?
                    )
                )
            )
        """,
        (
            mint,
            quote_mint,
            execution_observed_at,
            horizon_end,
            execution_observed_at,
            execution_observed_at,
            execution_slot,
            execution_slot,
        ),
    ).fetchone()

    peak_price = (
        float(row["peak_price_raw"])
        if (
            row is not None
            and row["peak_price_raw"]
            is not None
        )
        else execution_price
    )

    peak_price = max(
        peak_price,
        execution_price,
    )

    peak_multiple = (
        peak_price
        / execution_price
    )

    return (
        peak_price,
        peak_multiple,
    )


def grade_prediction(
    connection: sqlite3.Connection,
    prediction: sqlite3.Row,
) -> str:
    execution = find_execution_trade(
        connection,
        prediction,
    )

    if execution is None:
        return "NO_EXECUTION_PROXY"

    execution_price = float(
        execution["price_raw"]
    )

    if (
        not math.isfinite(
            execution_price
        )
        or execution_price <= 0
    ):
        return "INVALID_EXECUTION_PRICE"

    execution_observed_at = int(
        execution["observed_at"]
    )

    horizon_end = (
        execution_observed_at
        + TARGET_HORIZON_SECONDS
    )

    coverage = find_covering_interval(
        connection,
        predicted_at=int(
            prediction["predicted_at"]
        ),
        horizon_end=horizon_end,
    )

    if coverage is None:
        return "NO_CERTIFIED_COVERAGE"

    peak_price, peak_multiple = (
        calculate_peak(
            connection,
            mint=prediction["mint"],
            quote_mint=prediction[
                "quote_mint"
            ],
            execution_observed_at=(
                execution_observed_at
            ),
            execution_slot=execution[
                "slot"
            ],
            execution_price=(
                execution_price
            ),
        )
    )

    hit_2x = int(
        peak_multiple >= 2.0
    )

    original_price = trigger_price(
        connection,
        prediction["entry_signature"],
    )

    if (
        original_price is not None
        and original_price > 0
    ):
        entry_vs_trigger_bps = (
            (
                execution_price
                / original_price
            )
            - 1.0
        ) * 10000.0
    else:
        entry_vs_trigger_bps = None

    execution_delay = (
        execution_observed_at
        - int(
            prediction["predicted_at"]
        )
    )

    connection.execute(
        """
        INSERT OR IGNORE INTO
        model_shadow_outcomes (
            prediction_signature,

            grader_version,
            shadow_version,

            artifact_version,
            artifact_sha256,

            mint,
            wallet,
            quote_mint,

            trigger_slot,
            trigger_trade_timestamp,
            predicted_at,

            probability_2x_15m,
            development_baseline_probability,

            execution_signature,
            execution_slot,
            execution_trade_timestamp,
            execution_observed_at,
            execution_delay_seconds,

            trigger_price_raw,
            execution_entry_price_raw,

            entry_vs_trigger_bps,

            peak_price_15m_raw,
            peak_multiple_15m,

            hit_2x_15m,

            horizon_end_observed_at,

            coverage_id,

            graded_at
        )

        VALUES (
            ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?,
            ?,
            ?, ?,
            ?,
            ?,
            ?,
            ?
        )
        """,
        (
            prediction["entry_signature"],

            GRADER_VERSION,
            SHADOW_VERSION,

            ARTIFACT_VERSION,
            ARTIFACT_SHA256,

            prediction["mint"],
            prediction["wallet"],
            prediction["quote_mint"],

            prediction["slot"],
            prediction["trade_timestamp"],
            prediction["predicted_at"],

            prediction["probability_2x_15m"],
            prediction[
                "development_baseline_probability"
            ],

            execution["signature"],
            execution["slot"],
            execution["trade_timestamp"],
            execution_observed_at,
            execution_delay,

            original_price,
            execution_price,

            entry_vs_trigger_bps,

            peak_price,
            peak_multiple,

            hit_2x,

            horizon_end,

            coverage["id"],

            int(time.time()),
        ),
    )

    return "GRADED"


def mint_weights(
    mints: list[str],
) -> np.ndarray:
    counts = Counter(mints)

    return np.asarray(
        [
            1.0 / counts[mint]
            for mint in mints
        ],
        dtype=np.float64,
    )


def weighted_average(
    values: np.ndarray,
    weights: np.ndarray,
) -> float:
    return float(
        np.average(
            values,
            weights=weights,
        )
    )


def binary_log_loss(
    targets: np.ndarray,
    probabilities: np.ndarray,
) -> np.ndarray:
    p = np.clip(
        probabilities,
        EPSILON,
        1.0 - EPSILON,
    )

    return -(
        targets * np.log(p)
        + (1.0 - targets)
        * np.log(1.0 - p)
    )


def print_summary(
    connection: sqlite3.Connection,
) -> None:
    rows = connection.execute(
        """
        SELECT
            prediction_signature,
            mint,
            wallet,

            probability_2x_15m,
            development_baseline_probability,

            execution_delay_seconds,
            entry_vs_trigger_bps,

            peak_multiple_15m,
            hit_2x_15m

        FROM model_shadow_outcomes

        WHERE
            grader_version = ?

            AND artifact_sha256 = ?

        ORDER BY
            predicted_at,
            prediction_signature
        """,
        (
            GRADER_VERSION,
            ARTIFACT_SHA256,
        ),
    ).fetchall()

    print()
    print("=" * 78)
    print(
        "DELVE MEME AGENT — "
        "FORWARD MODEL SHADOW GRADER"
    )
    print("=" * 78)

    print(
        f"Grader version:       "
        f"{GRADER_VERSION}"
    )

    print(
        f"Shadow version:       "
        f"{SHADOW_VERSION}"
    )

    print(
        f"Artifact version:     "
        f"{ARTIFACT_VERSION}"
    )

    print(
        f"Artifact SHA-256:     "
        f"{ARTIFACT_SHA256}"
    )

    print(
        f"Execution proxy:      "
        f"first later BUY"
    )

    print(
        f"Maximum entry delay:  "
        f"{MAX_EXECUTION_DELAY_SECONDS}s"
    )

    print(
        f"Outcome horizon:      "
        f"{TARGET_HORIZON_SECONDS}s"
    )

    print()

    if not rows:
        print(
            "No fully certified executable "
            "shadow outcomes yet."
        )
        return

    targets = np.asarray(
        [
            row["hit_2x_15m"]
            for row in rows
        ],
        dtype=np.float64,
    )

    probabilities = np.asarray(
        [
            row["probability_2x_15m"]
            for row in rows
        ],
        dtype=np.float64,
    )

    baselines = np.asarray(
        [
            row[
                "development_baseline_probability"
            ]
            for row in rows
        ],
        dtype=np.float64,
    )

    mints = [
        row["mint"]
        for row in rows
    ]

    weights = mint_weights(
        mints
    )

    raw_hit_rate = float(
        targets.mean()
    )

    balanced_hit_rate = weighted_average(
        targets,
        weights,
    )

    model_logloss = weighted_average(
        binary_log_loss(
            targets,
            probabilities,
        ),
        weights,
    )

    baseline_logloss = weighted_average(
        binary_log_loss(
            targets,
            baselines,
        ),
        weights,
    )

    logloss_gain = (
        baseline_logloss
        - model_logloss
    )

    model_brier = weighted_average(
        (
            probabilities
            - targets
        )
        ** 2,
        weights,
    )

    baseline_brier = weighted_average(
        (
            baselines
            - targets
        )
        ** 2,
        weights,
    )

    brier_gain = (
        baseline_brier
        - model_brier
    )

    if len(set(targets.tolist())) >= 2:
        auc = float(
            roc_auc_score(
                targets,
                probabilities,
                sample_weight=weights,
            )
        )
    else:
        auc = float("nan")

    delays = np.asarray(
        [
            row[
                "execution_delay_seconds"
            ]
            for row in rows
        ],
        dtype=np.float64,
    )

    drift_values = [
        row["entry_vs_trigger_bps"]
        for row in rows
        if (
            row["entry_vs_trigger_bps"]
            is not None
        )
    ]

    drift = np.asarray(
        drift_values,
        dtype=np.float64,
    )

    print("FORWARD SAMPLE")
    print("-" * 78)

    print(
        f"Graded entries:       "
        f"{len(rows):,}"
    )

    print(
        f"Distinct mints:       "
        f"{len(set(mints)):,}"
    )

    print(
        f"Distinct wallets:     "
        f"{len(set(row['wallet'] for row in rows)):,}"
    )

    print(
        f"Raw 2x hit rate:      "
        f"{100.0 * raw_hit_rate:.3f}%"
    )

    print(
        f"Mint-balanced hit:    "
        f"{100.0 * balanced_hit_rate:.3f}%"
    )

    print(
        f"Mean prediction:      "
        f"{weighted_average(probabilities, weights):.6f}"
    )

    print()
    print("EXECUTION PROXY")
    print("-" * 78)

    print(
        f"Median entry delay:   "
        f"{np.median(delays):.3f}s"
    )

    print(
        f"P95 entry delay:      "
        f"{np.percentile(delays, 95):.3f}s"
    )

    print(
        f"Maximum entry delay:  "
        f"{delays.max():.3f}s"
    )

    if len(drift) > 0:
        print(
            f"Median entry drift:   "
            f"{np.median(drift):+.2f} bps"
        )

        print(
            f"P95 entry drift:      "
            f"{np.percentile(drift, 95):+.2f} bps"
        )

    print()
    print("MINT-BALANCED MODEL METRICS")
    print("-" * 78)

    print(
        f"Baseline log loss:    "
        f"{baseline_logloss:.6f}"
    )

    print(
        f"Model log loss:       "
        f"{model_logloss:.6f}"
    )

    print(
        f"Log loss gain:        "
        f"{logloss_gain:+.6f}"
    )

    print()

    print(
        f"Baseline Brier:       "
        f"{baseline_brier:.6f}"
    )

    print(
        f"Model Brier:          "
        f"{model_brier:.6f}"
    )

    print(
        f"Brier gain:           "
        f"{brier_gain:+.6f}"
    )

    print()

    print(
        f"ROC AUC:              "
        f"{auc:.6f}"
    )

    print()
    print("CALIBRATION")
    print("-" * 78)

    bins = [
        (0.00, 0.05),
        (0.05, 0.10),
        (0.10, 0.15),
        (0.15, 0.20),
        (0.20, 0.30),
        (0.30, 0.40),
        (0.40, 1.01),
    ]

    for lower, upper in bins:
        mask = (
            (probabilities >= lower)
            & (probabilities < upper)
        )

        count = int(
            mask.sum()
        )

        if count == 0:
            continue

        bin_weights = weights[mask]

        average_prediction = weighted_average(
            probabilities[mask],
            bin_weights,
        )

        realized = weighted_average(
            targets[mask],
            bin_weights,
        )

        print(
            f"{lower:0.2f}–{upper:0.2f} | "
            f"n={count:6,d} | "
            f"pred={average_prediction:0.4f} | "
            f"actual={realized:0.4f}"
        )

    print()
    print(
        "VALIDATION HOLDOUT READ: NO"
    )

    print(
        "MODEL FITTING: NO"
    )

    print(
        "THRESHOLD SEARCH: NO"
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )

    print("=" * 78)


def run_grader(
    *,
    limit: int | None,
    rebuild: bool,
) -> None:
    started = time.time()

    with get_connection() as connection:
        init_table(
            connection
        )

        verify_prediction_contract(
            connection
        )

        if rebuild:
            connection.execute(
                """
                DELETE FROM model_shadow_outcomes
                WHERE grader_version = ?
                """,
                (GRADER_VERSION,),
            )

            connection.commit()

        predictions = (
            load_predictions_to_grade(
                connection,
                limit=limit,
            )
        )

        print()
        print("=" * 78)
        print(
            "DELVE MEME AGENT — "
            "SEALED FORWARD SHADOW GRADING"
        )
        print("=" * 78)

        print(
            f"Predictions scheduled: "
            f"{len(predictions):,}"
        )

        print(
            f"Entry proxy:           "
            f"first subsequent BUY"
        )

        print(
            f"Execution ceiling:     "
            f"{MAX_EXECUTION_DELAY_SECONDS}s"
        )

        print(
            f"Horizon:               "
            f"{TARGET_HORIZON_SECONDS}s"
        )

        print(
            "VALIDATION HOLDOUT READ: NO"
        )

        print(
            "MODEL FITTING: NO"
        )

        print(
            "THRESHOLD SEARCH: NO"
        )

        print(
            "LIVE CAPITAL AUTHORIZATION: NO"
        )

        print("=" * 78)

        graded = 0
        statuses = Counter()

        for index, prediction in enumerate(
            predictions,
            start=1,
        ):
            status = grade_prediction(
                connection,
                prediction,
            )

            statuses[status] += 1

            if status == "GRADED":
                graded += 1

            if index % 250 == 0:
                connection.commit()

            if index % 5000 == 0:
                print(
                    f"{index:,}/"
                    f"{len(predictions):,} "
                    f"processed | "
                    f"{graded:,} newly graded"
                )

        connection.commit()

        print()
        print(
            f"Newly graded: "
            f"{graded:,}"
        )
        print()
        print("GRADING DISPOSITIONS")
        print("-" * 78)

        for status, count in sorted(
            statuses.items(),
            key=lambda item: (
                -item[1],
                item[0],
            ),
        ):
            percentage = (
                100.0
                * count
                / len(predictions)
                if predictions
                else 0.0
            )

            print(
                f"{status:<28} "
                f"{count:>8,d} "
                f"({percentage:6.2f}%)"
            )

        print_summary(
            connection
        )

    print(
        f"\nRuntime: "
        f"{time.time() - started:.2f}s"
    )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--rebuild",
        action="store_true",
    )

    parser.add_argument(
        "--summary-only",
        action="store_true",
    )

    args = parser.parse_args()

    if args.summary_only:
        with get_connection() as connection:
            init_table(
                connection
            )
            print_summary(
                connection
            )
        return

    run_grader(
        limit=args.limit,
        rebuild=args.rebuild,
    )


if __name__ == "__main__":
    main()