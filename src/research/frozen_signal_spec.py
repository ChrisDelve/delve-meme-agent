import hashlib
import json
import sqlite3
import time
from pathlib import Path


DB_PATH = Path("logs/delve_meme.db")

SPEC_VERSION = "frozen-signal-spec-v1"

COHORT_VERSION = "research-cohort-v1"
FEATURE_VERSION = "derived-features-v1"
LABEL_VERSION = "fixed-horizon-labels-v1"
REDUNDANCY_VERSION = "redundancy-analysis-v1"
INCREMENTAL_VERSION = "incremental-signal-v1"

EXPECTED_SELECTED_FEATURES = [
    "mayhem_mode",
    "buy_rate_15s",
]


SIGNAL_SPEC = {
    "spec_version": SPEC_VERSION,

    "purpose": (
        "One-shot out-of-development validation of a "
        "pre-entry model predicting 2x within 15 minutes."
    ),

    "research_provenance": {
        "cohort_version": COHORT_VERSION,
        "feature_version": FEATURE_VERSION,
        "label_version": LABEL_VERSION,
        "redundancy_version": REDUNDANCY_VERSION,
        "incremental_version": INCREMENTAL_VERSION,
    },

    "target": {
        "column": "hit_2x_15m",
        "description": "Token reaches at least 2x entry price within 15 minutes.",
        "horizon_seconds": 900,
    },

    "features": [
        "mayhem_mode",
        "buy_rate_15s",
    ],

    "training_universe": {
        "cohort_split": "development",
        "collector_session": 2,
        "eligible_15m": 1,
        "primary_validation": 0,
    },

    "validation_universe": {
        "cohort_split": "validation",
        "collector_session": 3,
        "eligible_15m": 1,
        "primary_validation": 1,
        "mint_seen_in_development": 0,
        "description": (
            "Session-3 eligible 15-minute observations "
            "whose mint was unseen in development."
        ),
    },

    "weighting": {
        "column": "mint_weight_15m",
        "policy": "Each mint contributes exactly one total unit of weight.",
    },

    "transformation": {
        "kind": "weighted_ecdf",
        "fit_on": "development_only",
        "apply_to_validation_without_refit": True,
        "unknown_or_missing_rank_value": 0.5,
        "include_missing_indicator": True,
        "validation_distribution_must_not_affect_transform": True,
    },

    "model": {
        "library": "scikit-learn",
        "class": "LogisticRegression",
        "penalty": "l2",
        "C": 1.0,
        "solver": "lbfgs",
        "max_iter": 2000,
        "tol": 1e-7,
        "random_state": 0,
        "probability_output": True,
    },

    "software": {
        "numpy": "2.5.2",
        "scikit_learn": "1.9.0",
    },

    "baseline": {
        "kind": "development_intercept",
        "description": (
            "Mint-weighted development target rate. "
            "The baseline probability is estimated on development "
            "and applied unchanged to validation."
        ),
        "validation_target_rate_must_not_set_baseline": True,
    },

    "validation_metrics": {
        "primary": "mint_weighted_logloss_gain_vs_baseline",

        "secondary": [
            "mint_weighted_brier_gain_vs_baseline",
            "mint_weighted_roc_auc",
        ],

        "bootstrap": {
            "unit": "mint",
            "resamples": 10000,
            "confidence_level": 0.95,
            "method": "percentile",
            "seed": 20260906,
        },
    },

    "advance_rule": {
        "all_conditions_required": True,

        "conditions": {
            "logloss_gain_point_estimate_minimum": 0.005,
            "logloss_gain_95pct_ci_lower_bound_gt": 0.0,

            "brier_gain_point_estimate_gte": 0.0,

            "auc_95pct_ci_lower_bound_gt": 0.5,
        },

        "interpretation": (
            "The model advances only if it demonstrates both "
            "statistically credible and practically nontrivial "
            "predictive improvement on untouched validation mints."
        ),
    },

    "development_selection_policy": {
        "selected_features": [
            "mayhem_mode",
            "buy_rate_15s",
        ],

        "rejected_near_threshold_feature": {
            "feature": "entry_rank",
            "mean_logloss_gain": 0.000947,
            "reason": (
                "Failed locked 0.001 minimum development "
                "incremental logloss-gain threshold and "
                "slightly worsened Brier score."
            ),
        },
    },

    "explicitly_prohibited_after_freeze": [
        "Adding entry_rank because it nearly passed development selection.",
        "Adding additional market-state features.",
        "Adding wallet alpha.",
        "Adding X or social-media features.",
        "Changing feature definitions.",
        "Changing the 15-minute target.",
        "Changing model class.",
        "Changing logistic-regression hyperparameters.",
        "Changing weighted-ECDF transformation.",
        "Changing missing-value handling.",
        "Changing mint weighting.",
        "Changing validation membership.",
        "Changing validation success thresholds after seeing results.",
        "Hyperparameter search on Session 3.",
        "Feature selection on Session 3.",
        "Probability recalibration using Session 3.",
        "Interaction hunting using Session 3.",
        "Repeatedly testing alternate models against Session 3.",
    ],

    "live_capital_authorization": False,

    "next_stage_if_validation_passes": (
        "Execution-aware research, decision-threshold design, "
        "exit modeling, transaction-cost simulation, risk governor, "
        "and forward shadow validation. Passing this validation "
        "does not authorize live trading."
    ),
}


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def canonical_spec_json():
    return json.dumps(
        SIGNAL_SPEC,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def spec_fingerprint():
    payload = canonical_spec_json().encode(
        "utf-8"
    )

    return hashlib.sha256(
        payload
    ).hexdigest()


def init_table(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS frozen_signal_specs (
            spec_version TEXT PRIMARY KEY,

            fingerprint_sha256 TEXT NOT NULL,
            spec_json TEXT NOT NULL,

            frozen_at INTEGER NOT NULL
        )
        """
    )

    connection.commit()


def verify_selected_features(connection):
    rows = connection.execute(
        """
        SELECT
            selection_order,
            feature_name

        FROM incremental_signal_selected

        WHERE analysis_version = ?

        ORDER BY selection_order ASC
        """,
        (INCREMENTAL_VERSION,),
    ).fetchall()

    selected = [
        row["feature_name"]
        for row in rows
    ]

    if selected != EXPECTED_SELECTED_FEATURES:
        raise RuntimeError(
            "Certified development selection does not match "
            "the signal specification.\n"
            f"Expected: {EXPECTED_SELECTED_FEATURES}\n"
            f"Found:    {selected}"
        )


def freeze_spec():
    fingerprint = spec_fingerprint()
    spec_json = canonical_spec_json()

    with get_connection() as connection:
        init_table(connection)

        # This query reads only the DEVELOPMENT selection artifact.
        # It does not inspect Session 3.
        verify_selected_features(
            connection
        )

        existing = connection.execute(
            """
            SELECT
                fingerprint_sha256,
                spec_json,
                frozen_at

            FROM frozen_signal_specs

            WHERE spec_version = ?
            """,
            (SPEC_VERSION,),
        ).fetchone()

        if existing is not None:
            if (
                existing["fingerprint_sha256"]
                != fingerprint
                or existing["spec_json"]
                != spec_json
            ):
                raise RuntimeError(
                    "\nIMMUTABILITY VIOLATION\n"
                    "A different specification is already "
                    f"frozen under {SPEC_VERSION}.\n"
                    "Do not overwrite a frozen validation "
                    "specification."
                )

            frozen_at = existing[
                "frozen_at"
            ]

            state = (
                "ALREADY FROZEN — "
                "IDENTICAL SPECIFICATION"
            )

        else:
            frozen_at = int(
                time.time()
            )

            connection.execute(
                """
                INSERT INTO frozen_signal_specs (
                    spec_version,
                    fingerprint_sha256,
                    spec_json,
                    frozen_at
                )

                VALUES (?, ?, ?, ?)
                """,
                (
                    SPEC_VERSION,
                    fingerprint,
                    spec_json,
                    frozen_at,
                ),
            )

            connection.commit()

            state = (
                "NEW SPECIFICATION FROZEN"
            )

    print()
    print("=" * 80)
    print(
        "DELVE MEME AGENT — "
        "FROZEN SIGNAL SPECIFICATION"
    )
    print("=" * 80)

    print(f"State:              {state}")
    print(f"Version:            {SPEC_VERSION}")
    print(f"Frozen at:          {frozen_at}")

    print()
    print("FINGERPRINT")
    print("-" * 80)
    print(fingerprint)

    print()
    print("LOCKED HYPOTHESIS")
    print("-" * 80)
    print(
        "Target:             "
        "2x within 15 minutes"
    )
    print(
        "Feature 1:          "
        "mayhem_mode"
    )
    print(
        "Feature 2:          "
        "buy_rate_15s"
    )
    print(
        "Training:           "
        "Session 2 development"
    )
    print(
        "Validation:         "
        "Session 3 primary unseen mints"
    )
    print(
        "Weighting:          "
        "one total unit per mint"
    )
    print(
        "Transform:          "
        "development-fit weighted ECDF"
    )
    print(
        "Model:              "
        "L2 logistic regression"
    )

    print()
    print("LOCKED VALIDATION RULE")
    print("-" * 80)
    print(
        "Logloss gain:       "
        ">= 0.005"
    )
    print(
        "Logloss 95% CI:     "
        "lower bound > 0"
    )
    print(
        "Brier gain:         "
        ">= 0"
    )
    print(
        "AUC 95% CI:         "
        "lower bound > 0.50"
    )
    print(
        "Bootstrap:          "
        "10,000 mint resamples"
    )

    print()
    print(
        "SESSION 3 HAS NOT BEEN READ "
        "BY THIS SCRIPT"
    )
    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )
    print("=" * 80)


if __name__ == "__main__":
    freeze_spec()