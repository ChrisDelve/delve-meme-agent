from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score

from src.research.forward_confirmation_spec import (
    ARTIFACT_SHA256,
    ARTIFACT_VERSION,
    BOOTSTRAP_RESAMPLES,
    FREEZE_EPOCH,
    GRADER_VERSION,
    MAX_EXECUTION_DELAY_SECONDS,
    MIN_DISTINCT_MINTS,
    PRIMARY_METRICS,
    SHADOW_VERSION,
    SOL_QUOTE_MINT,
    SPEC_VERSION,
    TARGET_HORIZON_SECONDS,
    build_spec,
    fingerprint,
)

from src.research.model_shadow_grader import (
    GRADER_VERSION as IMPLEMENTED_GRADER_VERSION,
    grade_prediction,
)


DB_PATH = Path("logs/delve_meme.db")

COHORT_VERSION = "forward-confirmation-cohort-v1"
EVALUATOR_VERSION = "forward-confirmation-evaluator-v1"

BOOTSTRAP_SEED = 20260906
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


def init_tables(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        forward_confirmation_cohorts (
            cohort_version TEXT PRIMARY KEY,
            spec_version TEXT NOT NULL,
            spec_sha256 TEXT NOT NULL,
            grader_version TEXT NOT NULL,
            cutoff_epoch INTEGER NOT NULL,
            prediction_count INTEGER NOT NULL,
            mint_count INTEGER NOT NULL,
            prediction_set_sha256 TEXT NOT NULL,
            mint_set_sha256 TEXT NOT NULL,
            frozen_at INTEGER NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        forward_confirmation_cohort_predictions (
            cohort_version TEXT NOT NULL,
            entry_signature TEXT NOT NULL,
            mint TEXT NOT NULL,
            predicted_at INTEGER NOT NULL,

            PRIMARY KEY (
                cohort_version,
                entry_signature
            )
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        forward_confirmation_results (
            cohort_version TEXT PRIMARY KEY,

            evaluator_version TEXT NOT NULL,

            spec_version TEXT NOT NULL,
            spec_sha256 TEXT NOT NULL,

            prediction_count INTEGER NOT NULL,
            mint_count INTEGER NOT NULL,

            baseline_logloss REAL NOT NULL,
            model_logloss REAL NOT NULL,
            logloss_gain REAL NOT NULL,
            logloss_ci_low REAL NOT NULL,
            logloss_ci_high REAL NOT NULL,

            baseline_brier REAL NOT NULL,
            model_brier REAL NOT NULL,
            brier_gain REAL NOT NULL,
            brier_ci_low REAL NOT NULL,
            brier_ci_high REAL NOT NULL,

            auc REAL NOT NULL,
            auc_ci_low REAL NOT NULL,
            auc_ci_high REAL NOT NULL,

            advance_pass INTEGER NOT NULL,

            result_json TEXT NOT NULL,

            evaluated_at INTEGER NOT NULL
        )
        """
    )

    connection.commit()


def verify_contract(
    connection: sqlite3.Connection,
) -> str:
    if IMPLEMENTED_GRADER_VERSION != GRADER_VERSION:
        raise RuntimeError(
            "Implemented grader version does not "
            "match frozen confirmation specification."
        )

    expected_sha256 = fingerprint(
        build_spec()
    )

    row = connection.execute(
        """
        SELECT
            spec_sha256,
            freeze_epoch_exclusive

        FROM forward_confirmation_specs

        WHERE spec_version = ?
        """,
        (SPEC_VERSION,),
    ).fetchone()

    if row is None:
        raise RuntimeError(
            "Frozen confirmation spec not found."
        )

    if row["spec_sha256"] != expected_sha256:
        raise RuntimeError(
            "Confirmation spec hash mismatch."
        )

    if int(row["freeze_epoch_exclusive"]) != FREEZE_EPOCH:
        raise RuntimeError(
            "Confirmation freeze epoch mismatch."
        )

    return expected_sha256


def sha256_lines(
    values: list[str],
) -> str:
    payload = "\n".join(values)

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


def freeze_cohort() -> None:
    cutoff = int(time.time())

    settle_seconds = (
        MAX_EXECUTION_DELAY_SECONDS
        + TARGET_HORIZON_SECONDS
    )

    with get_connection() as connection:
        init_tables(connection)

        spec_sha256 = verify_contract(
            connection
        )

        existing = connection.execute(
            """
            SELECT *
            FROM forward_confirmation_cohorts
            WHERE cohort_version = ?
            """,
            (COHORT_VERSION,),
        ).fetchone()

        if existing is not None:
            print()
            print(
                "ALREADY FROZEN — EXISTING "
                "CONFIRMATION COHORT"
            )
            print(
                f"Cohort version:       "
                f"{existing['cohort_version']}"
            )
            print(
                f"Cutoff epoch:         "
                f"{existing['cutoff_epoch']}"
            )
            print(
                f"Predictions:          "
                f"{existing['prediction_count']:,}"
            )
            print(
                f"Distinct mints:       "
                f"{existing['mint_count']:,}"
            )
            print(
                f"Prediction set hash:  "
                f"{existing['prediction_set_sha256']}"
            )
            print(
                f"Mint set hash:        "
                f"{existing['mint_set_sha256']}"
            )
            return

        rows = connection.execute(
            """
            WITH eligible AS (
                SELECT
                    p.entry_signature,
                    p.mint,
                    p.predicted_at

                FROM model_shadow_predictions p

                WHERE
                    p.predicted_at > ?

                    AND p.predicted_at
                        <= ? - ?

                    AND p.model_eligible = 1

                    AND p.shadow_version = ?

                    AND p.artifact_version = ?

                    AND p.artifact_sha256 = ?

                    AND p.quote_mint = ?
            ),

            execution AS (
                SELECT
                    e.*,

                    (
                        SELECT
                            MIN(t.observed_at)

                        FROM trades t

                        WHERE
                            t.mint = e.mint

                            AND t.quote_mint = ?

                            AND t.side = 'BUY'

                            AND t.signature
                                != e.entry_signature

                            AND t.quote_amount > 0

                            AND t.token_amount > 0

                            AND t.observed_at
                                IS NOT NULL

                            AND t.observed_at
                                > e.predicted_at

                            AND t.observed_at
                                <= (
                                    e.predicted_at
                                    + ?
                                )
                    ) AS execution_observed_at

                FROM eligible e
            ),

            certified AS (
                SELECT
                    x.*

                FROM execution x

                WHERE
                    x.execution_observed_at
                        IS NOT NULL

                    AND EXISTS (
                        SELECT 1

                        FROM collector_coverage c

                        WHERE
                            c.valid = 1

                            AND c.started_at
                                <= x.predicted_at

                            AND COALESCE(
                                    c.ended_at,
                                    ?
                                )
                                >= (
                                    x.execution_observed_at
                                    + ?
                                )
                    )
            )

            SELECT
                entry_signature,
                mint,
                predicted_at

            FROM certified

            ORDER BY
                predicted_at,
                entry_signature
            """,
            (
                FREEZE_EPOCH,
                cutoff,
                settle_seconds,

                SHADOW_VERSION,
                ARTIFACT_VERSION,
                ARTIFACT_SHA256,
                SOL_QUOTE_MINT,

                SOL_QUOTE_MINT,
                MAX_EXECUTION_DELAY_SECONDS,

                cutoff,
                TARGET_HORIZON_SECONDS,
            ),
        ).fetchall()

        if not rows:
            raise RuntimeError(
                "No certified confirmation rows found."
            )

        mints = sorted(
            {
                row["mint"]
                for row in rows
            }
        )

        if len(mints) < MIN_DISTINCT_MINTS:
            raise RuntimeError(
                f"Only {len(mints):,} certified mints. "
                f"Need {MIN_DISTINCT_MINTS:,}."
            )

        prediction_lines = [
            (
                f"{row['entry_signature']}|"
                f"{row['mint']}|"
                f"{row['predicted_at']}"
            )
            for row in rows
        ]

        prediction_hash = sha256_lines(
            prediction_lines
        )

        mint_hash = sha256_lines(
            mints
        )

        connection.execute(
            """
            INSERT INTO forward_confirmation_cohorts (
                cohort_version,
                spec_version,
                spec_sha256,
                grader_version,
                cutoff_epoch,
                prediction_count,
                mint_count,
                prediction_set_sha256,
                mint_set_sha256,
                frozen_at
            )

            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                COHORT_VERSION,
                SPEC_VERSION,
                spec_sha256,
                GRADER_VERSION,
                cutoff,
                len(rows),
                len(mints),
                prediction_hash,
                mint_hash,
                cutoff,
            ),
        )

        connection.executemany(
            """
            INSERT INTO
            forward_confirmation_cohort_predictions (
                cohort_version,
                entry_signature,
                mint,
                predicted_at
            )

            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    COHORT_VERSION,
                    row["entry_signature"],
                    row["mint"],
                    row["predicted_at"],
                )
                for row in rows
            ],
        )

        connection.commit()

    print()
    print("=" * 78)
    print(
        "FORWARD CONFIRMATION COHORT FROZEN"
    )
    print("=" * 78)

    print(
        f"Cohort version:       "
        f"{COHORT_VERSION}"
    )

    print(
        f"Spec SHA-256:         "
        f"{spec_sha256}"
    )

    print(
        f"Cutoff epoch:         "
        f"{cutoff}"
    )

    print(
        f"Predictions:          "
        f"{len(rows):,}"
    )

    print(
        f"Distinct mints:       "
        f"{len(mints):,}"
    )

    print(
        f"Prediction set hash:  "
        f"{prediction_hash}"
    )

    print(
        f"Mint set hash:        "
        f"{mint_hash}"
    )

    print()
    print(
        "OUTCOME LABELS READ: NO"
    )

    print(
        "MODEL PERFORMANCE READ: NO"
    )

    print(
        "COHORT IS NOW IMMUTABLE"
    )

    print("=" * 78)


def binary_logloss(
    target: np.ndarray,
    probability: np.ndarray,
) -> np.ndarray:
    p = np.clip(
        probability,
        EPSILON,
        1.0 - EPSILON,
    )

    return -(
        target * np.log(p)
        + (1.0 - target)
        * np.log(1.0 - p)
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


def bootstrap_metrics(
    *,
    targets: np.ndarray,
    probabilities: np.ndarray,
    baselines: np.ndarray,
    mints: list[str],
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
]:
    unique_mints = sorted(
        set(mints)
    )

    mint_to_index = {
        mint: index
        for index, mint
        in enumerate(unique_mints)
    }

    mint_index = np.asarray(
        [
            mint_to_index[mint]
            for mint in mints
        ],
        dtype=np.int64,
    )

    mint_count = len(
        unique_mints
    )

    rows_per_mint = np.bincount(
        mint_index,
        minlength=mint_count,
    ).astype(np.float64)

    model_ll = binary_logloss(
        targets,
        probabilities,
    )

    baseline_ll = binary_logloss(
        targets,
        baselines,
    )

    ll_gain_row = (
        baseline_ll
        - model_ll
    )

    model_brier = (
        probabilities
        - targets
    ) ** 2

    baseline_brier = (
        baselines
        - targets
    ) ** 2

    brier_gain_row = (
        baseline_brier
        - model_brier
    )

    mint_ll_gain = np.zeros(
        mint_count,
        dtype=np.float64,
    )

    mint_brier_gain = np.zeros(
        mint_count,
        dtype=np.float64,
    )

    np.add.at(
        mint_ll_gain,
        mint_index,
        ll_gain_row,
    )

    np.add.at(
        mint_brier_gain,
        mint_index,
        brier_gain_row,
    )

    mint_ll_gain /= rows_per_mint
    mint_brier_gain /= rows_per_mint

    order = np.argsort(
        probabilities,
        kind="mergesort",
    )

    sorted_probability = probabilities[
        order
    ]

    sorted_target = targets[
        order
    ]

    sorted_mint_index = mint_index[
        order
    ]

    sorted_rows_per_mint = (
        rows_per_mint[
            sorted_mint_index
        ]
    )

    starts = np.concatenate(
        (
            np.asarray([0]),
            np.flatnonzero(
                np.diff(
                    sorted_probability
                )
                != 0
            )
            + 1,
        )
    )

    rng = np.random.default_rng(
        BOOTSTRAP_SEED
    )

    ll_samples = np.empty(
        BOOTSTRAP_RESAMPLES,
        dtype=np.float64,
    )

    brier_samples = np.empty(
        BOOTSTRAP_RESAMPLES,
        dtype=np.float64,
    )

    auc_samples = np.empty(
        BOOTSTRAP_RESAMPLES,
        dtype=np.float64,
    )

    batch_size = 50

    uniform_probability = np.full(
        mint_count,
        1.0 / mint_count,
        dtype=np.float64,
    )

    offset = 0

    while offset < BOOTSTRAP_RESAMPLES:
        current_batch = min(
            batch_size,
            BOOTSTRAP_RESAMPLES
            - offset,
        )

        counts = rng.multinomial(
            mint_count,
            uniform_probability,
            size=current_batch,
        ).astype(np.float64)

        ll_samples[
            offset:offset + current_batch
        ] = (
            counts @ mint_ll_gain
        ) / mint_count

        brier_samples[
            offset:offset + current_batch
        ] = (
            counts @ mint_brier_gain
        ) / mint_count

        row_weights = (
            counts[
                :,
                sorted_mint_index
            ]
            / sorted_rows_per_mint[
                None,
                :
            ]
        )

        positive_weights = (
            row_weights
            * sorted_target[
                None,
                :
            ]
        )

        negative_weights = (
            row_weights
            * (
                1.0
                - sorted_target[
                    None,
                    :
                ]
            )
        )

        positive_group = np.add.reduceat(
            positive_weights,
            starts,
            axis=1,
        )

        negative_group = np.add.reduceat(
            negative_weights,
            starts,
            axis=1,
        )

        negative_before = (
            np.cumsum(
                negative_group,
                axis=1,
            )
            - negative_group
        )

        numerator = np.sum(
            positive_group
            * (
                negative_before
                + 0.5
                * negative_group
            ),
            axis=1,
        )

        total_positive = np.sum(
            positive_group,
            axis=1,
        )

        total_negative = np.sum(
            negative_group,
            axis=1,
        )

        denominator = (
            total_positive
            * total_negative
        )

        batch_auc = np.divide(
            numerator,
            denominator,
            out=np.full(
                current_batch,
                np.nan,
                dtype=np.float64,
            ),
            where=denominator > 0,
        )

        auc_samples[
            offset:offset + current_batch
        ] = batch_auc

        offset += current_batch

    return (
        ll_samples,
        brier_samples,
        auc_samples,
    )


def evaluate() -> None:
    with get_connection() as connection:
        init_tables(connection)

        spec_sha256 = verify_contract(
            connection
        )

        existing_result = connection.execute(
            """
            SELECT result_json
            FROM forward_confirmation_results
            WHERE cohort_version = ?
            """,
            (COHORT_VERSION,),
        ).fetchone()

        if existing_result is not None:
            print(
                "ALREADY EVALUATED — "
                "CONFIRMATION RESULT IS SEALED"
            )
            print()

            result = json.loads(
                existing_result[
                    "result_json"
                ]
            )

            print(
                json.dumps(
                    result,
                    indent=2,
                    sort_keys=True,
                )
            )
            return

        cohort = connection.execute(
            """
            SELECT *
            FROM forward_confirmation_cohorts
            WHERE cohort_version = ?
            """,
            (COHORT_VERSION,),
        ).fetchone()

        if cohort is None:
            raise RuntimeError(
                "Freeze the confirmation cohort first."
            )

        predictions = connection.execute(
            """
            SELECT
                p.*

            FROM
                forward_confirmation_cohort_predictions cp

            JOIN model_shadow_predictions p
                ON p.entry_signature
                   = cp.entry_signature

            WHERE
                cp.cohort_version = ?

            ORDER BY
                cp.predicted_at,
                cp.entry_signature
            """,
            (COHORT_VERSION,),
        ).fetchall()

        if len(predictions) != int(
            cohort["prediction_count"]
        ):
            raise RuntimeError(
                "Frozen cohort prediction count mismatch."
            )

        print()
        print("=" * 78)
        print(
            "OPENING LOCKED FORWARD "
            "CONFIRMATION COHORT"
        )
        print("=" * 78)

        print(
            f"Predictions:          "
            f"{len(predictions):,}"
        )

        print(
            f"Distinct mints:       "
            f"{cohort['mint_count']:,}"
        )

        print(
            f"Spec SHA-256:         "
            f"{spec_sha256}"
        )

        print(
            f"Prediction set hash:  "
            f"{cohort['prediction_set_sha256']}"
        )

        print()
        print(
            "MODEL FITTING: NO"
        )

        print(
            "RECALIBRATION: NO"
        )

        print(
            "THRESHOLD SEARCH: NO"
        )

        print(
            "LIVE CAPITAL AUTHORIZATION: NO"
        )

        print("=" * 78)

        for index, prediction in enumerate(
            predictions,
            start=1,
        ):
            status = grade_prediction(
                connection,
                prediction,
            )

            if status != "GRADED":
                raise RuntimeError(
                    "Frozen certified prediction "
                    f"failed grading: "
                    f"{prediction['entry_signature']} "
                    f"status={status}"
                )

            if index % 500 == 0:
                connection.commit()

            if index % 10000 == 0:
                print(
                    f"{index:,}/"
                    f"{len(predictions):,} "
                    f"outcomes reconstructed"
                )

        connection.commit()

        rows = connection.execute(
            """
            SELECT
                p.mint,
                p.probability_2x_15m,
                p.development_baseline_probability,

                o.hit_2x_15m,
                o.execution_delay_seconds,
                o.entry_vs_trigger_bps

            FROM
                forward_confirmation_cohort_predictions cp

            JOIN model_shadow_predictions p
                ON p.entry_signature
                   = cp.entry_signature

            JOIN model_shadow_outcomes o
                ON o.prediction_signature
                   = cp.entry_signature

            WHERE
                cp.cohort_version = ?

                AND o.grader_version = ?

                AND o.artifact_sha256 = ?
            """,
            (
                COHORT_VERSION,
                GRADER_VERSION,
                ARTIFACT_SHA256,
            ),
        ).fetchall()

        if len(rows) != len(predictions):
            raise RuntimeError(
                "Outcome count does not match "
                "frozen prediction count."
            )

        targets = np.asarray(
            [
                row["hit_2x_15m"]
                for row in rows
            ],
            dtype=np.float64,
        )

        probabilities = np.asarray(
            [
                row[
                    "probability_2x_15m"
                ]
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

        counts = Counter(
            mints
        )

        weights = np.asarray(
            [
                1.0 / counts[mint]
                for mint in mints
            ],
            dtype=np.float64,
        )

        baseline_logloss = weighted_average(
            binary_logloss(
                targets,
                baselines,
            ),
            weights,
        )

        model_logloss = weighted_average(
            binary_logloss(
                targets,
                probabilities,
            ),
            weights,
        )

        logloss_gain = (
            baseline_logloss
            - model_logloss
        )

        baseline_brier = weighted_average(
            (
                baselines
                - targets
            ) ** 2,
            weights,
        )

        model_brier = weighted_average(
            (
                probabilities
                - targets
            ) ** 2,
            weights,
        )

        brier_gain = (
            baseline_brier
            - model_brier
        )

        auc = float(
            roc_auc_score(
                targets,
                probabilities,
                sample_weight=weights,
            )
        )

        print()
        print(
            f"Running "
            f"{BOOTSTRAP_RESAMPLES:,} "
            f"mint bootstrap resamples..."
        )

        (
            ll_bootstrap,
            brier_bootstrap,
            auc_bootstrap,
        ) = bootstrap_metrics(
            targets=targets,
            probabilities=probabilities,
            baselines=baselines,
            mints=mints,
        )

        ll_ci = np.nanpercentile(
            ll_bootstrap,
            [2.5, 97.5],
        )

        brier_ci = np.nanpercentile(
            brier_bootstrap,
            [2.5, 97.5],
        )

        auc_ci = np.nanpercentile(
            auc_bootstrap,
            [2.5, 97.5],
        )

        condition_logloss_gain = (
            logloss_gain
            >= PRIMARY_METRICS[
                "logloss_gain_minimum"
            ]
        )

        condition_logloss_ci = (
            ll_ci[0]
            > PRIMARY_METRICS[
                "logloss_ci_lower_minimum"
            ]
        )

        condition_brier = (
            brier_gain
            >= PRIMARY_METRICS[
                "brier_gain_minimum"
            ]
        )

        condition_auc = (
            auc_ci[0]
            > PRIMARY_METRICS[
                "auc_ci_lower_minimum"
            ]
        )

        advance_pass = all(
            (
                condition_logloss_gain,
                condition_logloss_ci,
                condition_brier,
                condition_auc,
            )
        )

        drift = np.asarray(
            [
                row["entry_vs_trigger_bps"]
                for row in rows
                if (
                    row["entry_vs_trigger_bps"]
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
                for row in rows
            ],
            dtype=np.float64,
        )

        result = {
            "cohort_version": COHORT_VERSION,
            "evaluator_version": EVALUATOR_VERSION,
            "spec_version": SPEC_VERSION,
            "spec_sha256": spec_sha256,

            "prediction_count": len(rows),
            "mint_count": len(
                set(mints)
            ),

            "mint_balanced_hit_rate": (
                weighted_average(
                    targets,
                    weights,
                )
            ),

            "mean_prediction": (
                weighted_average(
                    probabilities,
                    weights,
                )
            ),

            "baseline_logloss": (
                baseline_logloss
            ),

            "model_logloss": (
                model_logloss
            ),

            "logloss_gain": (
                logloss_gain
            ),

            "logloss_ci_low": float(
                ll_ci[0]
            ),

            "logloss_ci_high": float(
                ll_ci[1]
            ),

            "baseline_brier": (
                baseline_brier
            ),

            "model_brier": (
                model_brier
            ),

            "brier_gain": (
                brier_gain
            ),

            "brier_ci_low": float(
                brier_ci[0]
            ),

            "brier_ci_high": float(
                brier_ci[1]
            ),

            "auc": auc,

            "auc_ci_low": float(
                auc_ci[0]
            ),

            "auc_ci_high": float(
                auc_ci[1]
            ),

            "median_execution_delay_seconds": (
                float(
                    np.median(
                        delays
                    )
                )
            ),

            "p95_execution_delay_seconds": (
                float(
                    np.percentile(
                        delays,
                        95,
                    )
                )
            ),

            "median_entry_drift_bps": (
                float(
                    np.median(
                        drift
                    )
                )
                if len(drift)
                else None
            ),

            "p95_entry_drift_bps": (
                float(
                    np.percentile(
                        drift,
                        95,
                    )
                )
                if len(drift)
                else None
            ),

            "condition_logloss_gain": (
                bool(
                    condition_logloss_gain
                )
            ),

            "condition_logloss_ci": (
                bool(
                    condition_logloss_ci
                )
            ),

            "condition_brier": (
                bool(
                    condition_brier
                )
            ),

            "condition_auc": (
                bool(
                    condition_auc
                )
            ),

            "advance_pass": bool(
                advance_pass
            ),
        }

        result_json = json.dumps(
            result,
            sort_keys=True,
            separators=(",", ":"),
        )

        connection.execute(
            """
            INSERT INTO forward_confirmation_results (
                cohort_version,
                evaluator_version,

                spec_version,
                spec_sha256,

                prediction_count,
                mint_count,

                baseline_logloss,
                model_logloss,
                logloss_gain,
                logloss_ci_low,
                logloss_ci_high,

                baseline_brier,
                model_brier,
                brier_gain,
                brier_ci_low,
                brier_ci_high,

                auc,
                auc_ci_low,
                auc_ci_high,

                advance_pass,

                result_json,

                evaluated_at
            )

            VALUES (
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?,
                ?,
                ?
            )
            """,
            (
                COHORT_VERSION,
                EVALUATOR_VERSION,

                SPEC_VERSION,
                spec_sha256,

                len(rows),
                len(set(mints)),

                baseline_logloss,
                model_logloss,
                logloss_gain,
                float(ll_ci[0]),
                float(ll_ci[1]),

                baseline_brier,
                model_brier,
                brier_gain,
                float(brier_ci[0]),
                float(brier_ci[1]),

                auc,
                float(auc_ci[0]),
                float(auc_ci[1]),

                int(advance_pass),

                result_json,

                int(time.time()),
            ),
        )

        connection.commit()

    print()
    print("=" * 78)
    print(
        "LOCKED FORWARD CONFIRMATION RESULT"
    )
    print("=" * 78)

    print(
        f"Predictions:          "
        f"{result['prediction_count']:,}"
    )

    print(
        f"Distinct mints:       "
        f"{result['mint_count']:,}"
    )

    print()

    print(
        f"Mint-balanced 2x:     "
        f"{100.0 * result['mint_balanced_hit_rate']:.3f}%"
    )

    print(
        f"Mean prediction:      "
        f"{result['mean_prediction']:.6f}"
    )

    print()

    print(
        f"Baseline log loss:    "
        f"{baseline_logloss:.6f}"
    )

    print(
        f"Model log loss:       "
        f"{model_logloss:.6f}"
    )

    print(
        f"Log-loss gain:        "
        f"{logloss_gain:+.6f}"
    )

    print(
        f"95% CI:               "
        f"[{ll_ci[0]:+.6f}, "
        f"{ll_ci[1]:+.6f}]"
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

    print(
        f"95% CI:               "
        f"[{brier_ci[0]:+.6f}, "
        f"{brier_ci[1]:+.6f}]"
    )

    print()

    print(
        f"ROC AUC:              "
        f"{auc:.6f}"
    )

    print(
        f"95% CI:               "
        f"[{auc_ci[0]:.6f}, "
        f"{auc_ci[1]:.6f}]"
    )

    print()

    print(
        f"Median exec delay:    "
        f"{result['median_execution_delay_seconds']:.2f}s"
    )

    print(
        f"P95 exec delay:       "
        f"{result['p95_execution_delay_seconds']:.2f}s"
    )

    print(
        f"Median entry drift:   "
        f"{result['median_entry_drift_bps']:+.2f} bps"
    )

    print(
        f"P95 entry drift:      "
        f"{result['p95_entry_drift_bps']:+.2f} bps"
    )

    print()
    print("LOCKED DECISION CONDITIONS")
    print("-" * 78)

    print(
        f"Log-loss gain >= "
        f"{PRIMARY_METRICS['logloss_gain_minimum']}: "
        f"{'PASS' if condition_logloss_gain else 'FAIL'}"
    )

    print(
        "Log-loss CI lower > 0: "
        f"{'PASS' if condition_logloss_ci else 'FAIL'}"
    )

    print(
        "Brier gain >= 0:       "
        f"{'PASS' if condition_brier else 'FAIL'}"
    )

    print(
        "AUC CI lower > 0.50:   "
        f"{'PASS' if condition_auc else 'FAIL'}"
    )

    print()
    print(
        "FINAL DECISION: "
        f"{'ADVANCE' if advance_pass else 'DO NOT ADVANCE'}"
    )

    print()
    print(
        "MODEL REFIT: NO"
    )

    print(
        "RECALIBRATION: NO"
    )

    print(
        "THRESHOLD SEARCH: NO"
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )

    print("=" * 78)


def main() -> None:
    parser = argparse.ArgumentParser()

    group = parser.add_mutually_exclusive_group(
        required=True
    )

    group.add_argument(
        "--freeze-cohort",
        action="store_true",
    )

    group.add_argument(
        "--evaluate",
        action="store_true",
    )

    args = parser.parse_args()

    if args.freeze_cohort:
        freeze_cohort()
        return

    if args.evaluate:
        evaluate()
        return


if __name__ == "__main__":
    main()