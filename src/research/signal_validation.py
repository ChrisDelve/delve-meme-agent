import argparse
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path

import numpy as np
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from src.research.frozen_signal_spec import (
    SIGNAL_SPEC,
    SPEC_VERSION,
    canonical_spec_json,
    spec_fingerprint,
)


DB_PATH = Path("logs/delve_meme.db")

VALIDATION_VERSION = "signal-validation-v1"

FEATURES = list(
    SIGNAL_SPEC["features"]
)

COHORT_VERSION = SIGNAL_SPEC[
    "research_provenance"
]["cohort_version"]

FEATURE_VERSION = SIGNAL_SPEC[
    "research_provenance"
]["feature_version"]

LABEL_VERSION = SIGNAL_SPEC[
    "research_provenance"
]["label_version"]

BOOTSTRAP_RESAMPLES = SIGNAL_SPEC[
    "validation_metrics"
]["bootstrap"]["resamples"]

BOOTSTRAP_SEED = SIGNAL_SPEC[
    "validation_metrics"
]["bootstrap"]["seed"]

CONFIDENCE_LEVEL = SIGNAL_SPEC[
    "validation_metrics"
]["bootstrap"]["confidence_level"]

MODEL_CONFIG = SIGNAL_SPEC["model"]

ADVANCE_RULE = SIGNAL_SPEC[
    "advance_rule"
]["conditions"]

BOOTSTRAP_BATCH_SIZE = 64
EPSILON = 1e-12


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_result_table(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS signal_validation_results (
            validation_version TEXT PRIMARY KEY,

            spec_version TEXT NOT NULL,
            spec_fingerprint_sha256 TEXT NOT NULL,

            training_fingerprint_sha256 TEXT NOT NULL,
            validation_fingerprint_sha256 TEXT NOT NULL,

            training_rows INTEGER NOT NULL,
            training_mints INTEGER NOT NULL,
            training_weight REAL NOT NULL,

            validation_rows INTEGER NOT NULL,
            validation_mints INTEGER NOT NULL,
            validation_weight REAL NOT NULL,

            development_target_rate REAL NOT NULL,
            validation_target_rate REAL NOT NULL,

            baseline_logloss REAL NOT NULL,
            model_logloss REAL NOT NULL,
            logloss_gain REAL NOT NULL,
            logloss_ci_lower REAL NOT NULL,
            logloss_ci_upper REAL NOT NULL,

            baseline_brier REAL NOT NULL,
            model_brier REAL NOT NULL,
            brier_gain REAL NOT NULL,
            brier_ci_lower REAL NOT NULL,
            brier_ci_upper REAL NOT NULL,

            model_auc REAL NOT NULL,
            auc_ci_lower REAL NOT NULL,
            auc_ci_upper REAL NOT NULL,

            condition_logloss_gain INTEGER NOT NULL,
            condition_logloss_ci INTEGER NOT NULL,
            condition_brier_gain INTEGER NOT NULL,
            condition_auc_ci INTEGER NOT NULL,

            advance_pass INTEGER NOT NULL,

            feature_columns_json TEXT NOT NULL,
            model_intercept_json TEXT NOT NULL,
            model_coefficients_json TEXT NOT NULL,

            bootstrap_resamples INTEGER NOT NULL,
            bootstrap_seed INTEGER NOT NULL,

            validated_at INTEGER NOT NULL
        )
        """
    )

    connection.commit()


def verify_environment():
    expected_numpy = SIGNAL_SPEC[
        "software"
    ]["numpy"]

    expected_sklearn = SIGNAL_SPEC[
        "software"
    ]["scikit_learn"]

    if np.__version__ != expected_numpy:
        raise RuntimeError(
            "NumPy version mismatch.\n"
            f"Frozen:  {expected_numpy}\n"
            f"Current: {np.__version__}"
        )

    if sklearn.__version__ != expected_sklearn:
        raise RuntimeError(
            "scikit-learn version mismatch.\n"
            f"Frozen:  {expected_sklearn}\n"
            f"Current: {sklearn.__version__}"
        )


def verify_frozen_spec(connection):
    row = connection.execute(
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

    if row is None:
        raise RuntimeError(
            "Frozen signal specification "
            "is missing from the database."
        )

    current_json = canonical_spec_json()
    current_fingerprint = spec_fingerprint()

    if row["spec_json"] != current_json:
        raise RuntimeError(
            "FROZEN SPECIFICATION MISMATCH\n"
            "Current source specification differs "
            "from the persisted frozen copy."
        )

    if (
        row["fingerprint_sha256"]
        != current_fingerprint
    ):
        raise RuntimeError(
            "FROZEN FINGERPRINT MISMATCH"
        )

    return {
        "fingerprint":
            current_fingerprint,
        "frozen_at":
            row["frozen_at"],
    }


def verify_no_existing_validation(connection):
    existing = connection.execute(
        """
        SELECT
            validation_version,
            spec_fingerprint_sha256,
            validated_at,
            advance_pass

        FROM signal_validation_results

        WHERE validation_version = ?
        """,
        (VALIDATION_VERSION,),
    ).fetchone()

    if existing is not None:
        raise RuntimeError(
            "\nONE-SHOT VALIDATION ALREADY EXISTS\n"
            f"Version: {existing['validation_version']}\n"
            f"Validated at: {existing['validated_at']}\n"
            f"Advance pass: {existing['advance_pass']}\n\n"
            "The sealed validation runner will not "
            "overwrite or retest Session 3."
        )


def numeric_or_nan(value):
    if value is None:
        return np.nan

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return np.nan

    if not math.isfinite(numeric):
        return np.nan

    return numeric


def rows_to_dataset(rows):
    if not rows:
        raise RuntimeError(
            "Dataset query returned no rows."
        )

    signatures = np.array(
        [
            row["entry_signature"]
            for row in rows
        ],
        dtype=object,
    )

    mints = np.array(
        [
            row["mint"]
            for row in rows
        ],
        dtype=object,
    )

    timestamps = np.array(
        [
            int(row["entry_timestamp"])
            for row in rows
        ],
        dtype=np.int64,
    )

    weights = np.array(
        [
            float(row["row_weight"])
            for row in rows
        ],
        dtype=np.float64,
    )

    targets = np.array(
        [
            int(row["target"])
            for row in rows
        ],
        dtype=np.int8,
    )

    raw_features = {}

    for feature in FEATURES:
        raw_features[feature] = np.array(
            [
                numeric_or_nan(
                    row[feature]
                )
                for row in rows
            ],
            dtype=np.float64,
        )

    return {
        "signatures": signatures,
        "mints": mints,
        "timestamps": timestamps,
        "weights": weights,
        "targets": targets,
        "features": raw_features,
    }


def load_development_data(connection):
    feature_sql = ",\n".join(
        f'd."{feature}" AS "{feature}"'
        for feature in FEATURES
    )

    rows = connection.execute(
        f"""
        SELECT
            rc.entry_signature,
            rc.mint,
            rc.entry_timestamp,
            rc.mint_weight_15m AS row_weight,

            labels.hit_2x_15m AS target,

            {feature_sql}

        FROM research_cohort rc

        JOIN derived_features d
          ON d.entry_signature =
             rc.entry_signature
         AND d.derived_version = ?

        JOIN fixed_horizon_labels labels
          ON labels.entry_signature =
             rc.entry_signature
         AND labels.label_version = ?

        WHERE
            rc.cohort_version = ?
            AND rc.split = 'development'
            AND rc.session_id = 2
            AND rc.eligible_15m = 1
            AND rc.primary_validation = 0

        ORDER BY
            rc.mint ASC,
            rc.entry_signature ASC
        """,
        (
            FEATURE_VERSION,
            LABEL_VERSION,
            COHORT_VERSION,
        ),
    ).fetchall()

    return rows_to_dataset(rows)


def load_validation_data(connection):
    """
    THIS FUNCTION INTENTIONALLY OPENS SESSION 3.

    It must never be called by preflight mode.
    """

    feature_sql = ",\n".join(
        f'd."{feature}" AS "{feature}"'
        for feature in FEATURES
    )

    rows = connection.execute(
        f"""
        SELECT
            rc.entry_signature,
            rc.mint,
            rc.entry_timestamp,
            rc.mint_weight_15m AS row_weight,

            labels.hit_2x_15m AS target,

            {feature_sql}

        FROM research_cohort rc

        JOIN derived_features d
          ON d.entry_signature =
             rc.entry_signature
         AND d.derived_version = ?

        JOIN fixed_horizon_labels labels
          ON labels.entry_signature =
             rc.entry_signature
         AND labels.label_version = ?

        WHERE
            rc.cohort_version = ?
            AND rc.split = 'validation'
            AND rc.session_id = 3
            AND rc.eligible_15m = 1
            AND rc.primary_validation = 1
            AND rc.mint_seen_in_development = 0

        ORDER BY
            rc.mint ASC,
            rc.entry_signature ASC
        """,
        (
            FEATURE_VERSION,
            LABEL_VERSION,
            COHORT_VERSION,
        ),
    ).fetchall()

    return rows_to_dataset(rows)


def dataset_fingerprint(data):
    digest = hashlib.sha256()

    for index in range(
        len(data["targets"])
    ):
        parts = [
            str(
                data[
                    "signatures"
                ][index]
            ),
            str(
                data[
                    "mints"
                ][index]
            ),
            str(
                int(
                    data[
                        "timestamps"
                    ][index]
                )
            ),
            format(
                float(
                    data[
                        "weights"
                    ][index]
                ),
                ".17g",
            ),
            str(
                int(
                    data[
                        "targets"
                    ][index]
                )
            ),
        ]

        for feature in FEATURES:
            value = data[
                "features"
            ][feature][index]

            if np.isfinite(value):
                parts.append(
                    format(
                        float(value),
                        ".17g",
                    )
                )
            else:
                parts.append(
                    "NA"
                )

        digest.update(
            (
                "|".join(parts)
                + "\n"
            ).encode(
                "utf-8"
            )
        )

    return digest.hexdigest()


def audit_dataset(
    data,
    name,
):
    signatures = data[
        "signatures"
    ]

    mints = data["mints"]
    weights = data["weights"]
    targets = data["targets"]

    if (
        len(set(signatures))
        != len(signatures)
    ):
        raise RuntimeError(
            f"{name}: duplicate entry signatures"
        )

    if np.any(weights <= 0):
        raise RuntimeError(
            f"{name}: nonpositive row weight"
        )

    if not np.all(
        np.isin(
            targets,
            [0, 1],
        )
    ):
        raise RuntimeError(
            f"{name}: invalid target value"
        )

    unique_mints, inverse = np.unique(
        mints,
        return_inverse=True,
    )

    mint_weights = np.bincount(
        inverse,
        weights=weights,
        minlength=len(
            unique_mints
        ),
    )

    max_mint_weight_error = float(
        np.max(
            np.abs(
                mint_weights - 1.0
            )
        )
    )

    if (
        max_mint_weight_error
        > 1e-6
    ):
        raise RuntimeError(
            f"{name}: mint weighting "
            "does not reconcile to one "
            "unit per mint. "
            f"Max error="
            f"{max_mint_weight_error}"
        )

    feature_coverage = {}

    for feature in FEATURES:
        known = int(
            np.isfinite(
                data[
                    "features"
                ][feature]
            ).sum()
        )

        feature_coverage[
            feature
        ] = known

    return {
        "rows":
            len(targets),

        "mints":
            len(unique_mints),

        "total_weight":
            float(
                weights.sum()
            ),

        "weighted_target_rate":
            float(
                np.sum(
                    targets
                    * weights
                )
                / weights.sum()
            ),

        "max_mint_weight_error":
            max_mint_weight_error,

        "feature_coverage":
            feature_coverage,
    }


def fit_weighted_ecdf(
    values,
    weights,
):
    known = np.isfinite(values)

    known_values = values[known]
    known_weights = weights[known]

    if known_values.size == 0:
        return None

    unique_values, inverse = np.unique(
        known_values,
        return_inverse=True,
    )

    value_weights = np.bincount(
        inverse,
        weights=known_weights,
        minlength=len(
            unique_values
        ),
    )

    total_weight = float(
        value_weights.sum()
    )

    if total_weight <= 0:
        return None

    cumulative_before = (
        np.cumsum(
            value_weights
        )
        - value_weights
    )

    midranks = (
        cumulative_before
        + (
            value_weights
            / 2.0
        )
    ) / total_weight

    return {
        "values":
            unique_values,

        "ranks":
            midranks,
    }

def apply_weighted_ecdf(values, ecdf):
    known = np.isfinite(values)

    rank_values = np.full(
        len(values),
        0.5,
        dtype=np.float32,
    )
    missing_indicator = (~known).astype(np.float32)

    if ecdf is None:
        return np.column_stack(
            (
                rank_values,
                missing_indicator,
            )
        ).astype(np.float32, copy=False)

    reference_values = ecdf["values"]
    reference_ranks = ecdf["ranks"]

    if len(reference_values) == 1:
        rank_values[known] = 0.5
    else:
        rank_values[known] = np.interp(
            values[known],
            reference_values,
            reference_ranks,
            left=reference_ranks[0],
            right=reference_ranks[-1],
        ).astype(np.float32)

    return np.column_stack(
        (
            rank_values,
            missing_indicator,
        )
    ).astype(np.float32, copy=False)


def build_training_state(
    development,
):
    weights = development[
        "weights"
    ]

    targets = development[
        "targets"
    ]

    ecdfs = {}
    feature_parts = []
    feature_columns = []

    for feature in FEATURES:
        ecdf = fit_weighted_ecdf(
            development[
                "features"
            ][feature],
            weights,
        )

        ecdfs[feature] = ecdf

        feature_parts.append(
            apply_weighted_ecdf(
                development[
                    "features"
                ][feature],
                ecdf,
            )
        )

        feature_columns.extend(
            [
                f"{feature}__ecdf",
                f"{feature}__missing",
            ]
        )

    x_train = np.concatenate(
        feature_parts,
        axis=1,
    )

    baseline_probability = float(
        np.sum(
            targets
            * weights
        )
        / weights.sum()
    )

    model = LogisticRegression(
        penalty="l2",
        C=float(
            MODEL_CONFIG["C"]
        ),
        solver=
            MODEL_CONFIG[
                "solver"
            ],
        max_iter=int(
            MODEL_CONFIG[
                "max_iter"
            ]
        ),
        tol=float(
            MODEL_CONFIG[
                "tol"
            ]
        ),
        random_state=int(
            MODEL_CONFIG[
                "random_state"
            ]
        ),
    )

    model.fit(
        x_train,
        targets,
        sample_weight=weights,
    )

    return {
        "ecdfs":
            ecdfs,

        "model":
            model,

        "baseline_probability":
            baseline_probability,

        "feature_columns":
            feature_columns,
    }


def transform_validation(
    validation,
    training_state,
):
    parts = []

    for feature in FEATURES:
        parts.append(
            apply_weighted_ecdf(
                validation[
                    "features"
                ][feature],
                training_state[
                    "ecdfs"
                ][feature],
            )
        )

    return np.concatenate(
        parts,
        axis=1,
    )


def weighted_mean(
    values,
    weights,
):
    denominator = float(
        weights.sum()
    )

    if denominator <= 0:
        raise RuntimeError(
            "Nonpositive total weight"
        )

    return float(
        np.sum(
            values
            * weights
        )
        / denominator
    )


def logloss_vector(
    targets,
    probabilities,
):
    probabilities = np.clip(
        probabilities,
        EPSILON,
        1.0 - EPSILON,
    )

    return -(
        targets
        * np.log(
            probabilities
        )
        + (
            1 - targets
        )
        * np.log(
            1.0
            - probabilities
        )
    )


def brier_vector(
    targets,
    probabilities,
):
    return (
        probabilities
        - targets
    ) ** 2


def custom_weighted_auc(
    targets,
    probabilities,
    weights,
):
    order = np.argsort(
        probabilities,
        kind="mergesort",
    )

    sorted_probabilities = (
        probabilities[order]
    )

    sorted_targets = (
        targets[order]
    )

    sorted_weights = (
        weights[order]
    )

    group_starts = np.r_[
        0,
        np.flatnonzero(
            np.diff(
                sorted_probabilities
            )
            != 0
        )
        + 1,
    ]

    positive_weight = (
        sorted_weights
        * sorted_targets
    )

    negative_weight = (
        sorted_weights
        * (
            1 - sorted_targets
        )
    )

    positive_groups = (
        np.add.reduceat(
            positive_weight,
            group_starts,
        )
    )

    negative_groups = (
        np.add.reduceat(
            negative_weight,
            group_starts,
        )
    )

    cumulative_negative_before = (
        np.cumsum(
            negative_groups
        )
        - negative_groups
    )

    numerator = float(
        np.sum(
            positive_groups
            * (
                cumulative_negative_before
                + (
                    0.5
                    * negative_groups
                )
            )
        )
    )

    denominator = float(
        positive_groups.sum()
        * negative_groups.sum()
    )

    if denominator <= 0:
        raise RuntimeError(
            "AUC undefined because "
            "one target class has zero "
            "total weight."
        )

    return (
        numerator
        / denominator
    )


def evaluate_validation(
    validation,
    training_state,
):
    x_validation = (
        transform_validation(
            validation,
            training_state,
        )
    )

    model_probabilities = (
        training_state[
            "model"
        ].predict_proba(
            x_validation
        )[:, 1]
    )

    baseline_probabilities = np.full(
        len(
            validation[
                "targets"
            ]
        ),
        training_state[
            "baseline_probability"
        ],
        dtype=np.float64,
    )

    targets = validation[
        "targets"
    ]

    weights = validation[
        "weights"
    ]

    baseline_logloss_vector = (
        logloss_vector(
            targets,
            baseline_probabilities,
        )
    )

    model_logloss_vector = (
        logloss_vector(
            targets,
            model_probabilities,
        )
    )

    baseline_brier_vector = (
        brier_vector(
            targets,
            baseline_probabilities,
        )
    )

    model_brier_vector = (
        brier_vector(
            targets,
            model_probabilities,
        )
    )

    baseline_logloss = weighted_mean(
        baseline_logloss_vector,
        weights,
    )

    model_logloss = weighted_mean(
        model_logloss_vector,
        weights,
    )

    baseline_brier = weighted_mean(
        baseline_brier_vector,
        weights,
    )

    model_brier = weighted_mean(
        model_brier_vector,
        weights,
    )

    model_auc = custom_weighted_auc(
        targets,
        model_probabilities,
        weights,
    )

    sklearn_auc = float(
        roc_auc_score(
            targets,
            model_probabilities,
            sample_weight=weights,
        )
    )

    if (
        abs(
            model_auc
            - sklearn_auc
        )
        > 1e-10
    ):
        raise RuntimeError(
            "Custom weighted AUC does "
            "not reconcile with "
            "scikit-learn."
        )

    return {
        "x_validation":
            x_validation,

        "model_probabilities":
            model_probabilities,

        "baseline_probabilities":
            baseline_probabilities,

        "baseline_logloss_vector":
            baseline_logloss_vector,

        "model_logloss_vector":
            model_logloss_vector,

        "baseline_brier_vector":
            baseline_brier_vector,

        "model_brier_vector":
            model_brier_vector,

        "baseline_logloss":
            baseline_logloss,

        "model_logloss":
            model_logloss,

        "logloss_gain":
            baseline_logloss
            - model_logloss,

        "baseline_brier":
            baseline_brier,

        "model_brier":
            model_brier,

        "brier_gain":
            baseline_brier
            - model_brier,

        "model_auc":
            model_auc,
    }


def prepare_mint_bootstrap(
    validation,
    evaluation,
):
    mints = validation[
        "mints"
    ]

    unique_mints, mint_inverse = (
        np.unique(
            mints,
            return_inverse=True,
        )
    )

    mint_count = len(
        unique_mints
    )

    weights = validation[
        "weights"
    ]

    logloss_gain_rows = (
        evaluation[
            "baseline_logloss_vector"
        ]
        - evaluation[
            "model_logloss_vector"
        ]
    )

    brier_gain_rows = (
        evaluation[
            "baseline_brier_vector"
        ]
        - evaluation[
            "model_brier_vector"
        ]
    )

    mint_weights = np.bincount(
        mint_inverse,
        weights=weights,
        minlength=mint_count,
    )

    mint_logloss_gain = np.bincount(
        mint_inverse,
        weights=(
            weights
            * logloss_gain_rows
        ),
        minlength=mint_count,
    ) / mint_weights

    mint_brier_gain = np.bincount(
        mint_inverse,
        weights=(
            weights
            * brier_gain_rows
        ),
        minlength=mint_count,
    ) / mint_weights

    probabilities = evaluation[
        "model_probabilities"
    ]

    order = np.argsort(
        probabilities,
        kind="mergesort",
    )

    sorted_probabilities = (
        probabilities[order]
    )

    group_starts = np.r_[
        0,
        np.flatnonzero(
            np.diff(
                sorted_probabilities
            )
            != 0
        )
        + 1,
    ]

    return {
        "mint_count":
            mint_count,

        "mint_inverse_sorted":
            mint_inverse[order],

        "weights_sorted":
            weights[order],

        "targets_sorted":
            validation[
                "targets"
            ][order],

        "group_starts":
            group_starts,

        "mint_logloss_gain":
            mint_logloss_gain,

        "mint_brier_gain":
            mint_brier_gain,
    }


def run_mint_bootstrap(
    bootstrap_state,
):
    mint_count = bootstrap_state[
        "mint_count"
    ]

    rng = np.random.default_rng(
        BOOTSTRAP_SEED
    )

    probabilities = np.full(
        mint_count,
        1.0 / mint_count,
        dtype=np.float64,
    )

    logloss_samples = np.empty(
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

    mint_inverse_sorted = (
        bootstrap_state[
            "mint_inverse_sorted"
        ]
    )

    weights_sorted = (
        bootstrap_state[
            "weights_sorted"
        ]
    )

    targets_sorted = (
        bootstrap_state[
            "targets_sorted"
        ]
    )

    group_starts = (
        bootstrap_state[
            "group_starts"
        ]
    )

    print()
    print(
        "Running locked "
        f"{BOOTSTRAP_RESAMPLES:,}-resample "
        "mint bootstrap..."
    )

    for start in range(
        0,
        BOOTSTRAP_RESAMPLES,
        BOOTSTRAP_BATCH_SIZE,
    ):
        end = min(
            start
            + BOOTSTRAP_BATCH_SIZE,
            BOOTSTRAP_RESAMPLES,
        )

        batch_size = (
            end - start
        )

        counts = rng.multinomial(
            mint_count,
            probabilities,
            size=batch_size,
        )

        logloss_samples[
            start:end
        ] = (
            counts
            @ bootstrap_state[
                "mint_logloss_gain"
            ]
        ) / mint_count

        brier_samples[
            start:end
        ] = (
            counts
            @ bootstrap_state[
                "mint_brier_gain"
            ]
        ) / mint_count

        effective_weights = (
            counts[
                :,
                mint_inverse_sorted
            ]
            * weights_sorted[
                None,
                :
            ]
        )

        positive_weights = (
            effective_weights
            * targets_sorted[
                None,
                :
            ]
        )

        negative_weights = (
            effective_weights
            - positive_weights
        )

        positive_groups = np.add.reduceat(
            positive_weights,
            group_starts,
            axis=1,
        )

        negative_groups = np.add.reduceat(
            negative_weights,
            group_starts,
            axis=1,
        )

        cumulative_negative_before = (
            np.cumsum(
                negative_groups,
                axis=1,
            )
            - negative_groups
        )

        numerators = np.sum(
            positive_groups
            * (
                cumulative_negative_before
                + (
                    0.5
                    * negative_groups
                )
            ),
            axis=1,
        )

        denominators = (
            positive_groups.sum(
                axis=1
            )
            * negative_groups.sum(
                axis=1
            )
        )

        batch_auc = np.divide(
            numerators,
            denominators,
            out=np.full(
                batch_size,
                np.nan,
                dtype=np.float64,
            ),
            where=(
                denominators > 0
            ),
        )

        auc_samples[
            start:end
        ] = batch_auc

        completed = end

        if (
            completed % 1000 == 0
            or completed
            == BOOTSTRAP_RESAMPLES
        ):
            print(
                f"  {completed:,}/"
                f"{BOOTSTRAP_RESAMPLES:,}"
            )

    auc_samples = auc_samples[
        np.isfinite(
            auc_samples
        )
    ]

    if (
        len(auc_samples)
        < (
            0.99
            * BOOTSTRAP_RESAMPLES
        )
    ):
        raise RuntimeError(
            "Too many bootstrap AUC "
            "replicates were undefined."
        )

    return {
        "logloss":
            logloss_samples,

        "brier":
            brier_samples,

        "auc":
            auc_samples,
    }


def percentile_interval(samples):
    alpha = (
        1.0
        - CONFIDENCE_LEVEL
    )

    lower_probability = (
        alpha / 2.0
    )

    upper_probability = (
        1.0
        - (
            alpha / 2.0
        )
    )

    lower, upper = np.quantile(
        samples,
        [
            lower_probability,
            upper_probability,
        ],
        method="linear",
    )

    return (
        float(lower),
        float(upper),
    )


def determine_advance(
    evaluation,
    intervals,
):
    condition_logloss_gain = (
        evaluation[
            "logloss_gain"
        ]
        >= float(
            ADVANCE_RULE[
                "logloss_gain_point_estimate_minimum"
            ]
        )
    )

    condition_logloss_ci = (
        intervals[
            "logloss"
        ][0]
        > float(
            ADVANCE_RULE[
                "logloss_gain_95pct_ci_lower_bound_gt"
            ]
        )
    )

    condition_brier_gain = (
        evaluation[
            "brier_gain"
        ]
        >= float(
            ADVANCE_RULE[
                "brier_gain_point_estimate_gte"
            ]
        )
    )

    condition_auc_ci = (
        intervals[
            "auc"
        ][0]
        > float(
            ADVANCE_RULE[
                "auc_95pct_ci_lower_bound_gt"
            ]
        )
    )

    advance_pass = all(
        [
            condition_logloss_gain,
            condition_logloss_ci,
            condition_brier_gain,
            condition_auc_ci,
        ]
    )

    return {
        "condition_logloss_gain":
            condition_logloss_gain,

        "condition_logloss_ci":
            condition_logloss_ci,

        "condition_brier_gain":
            condition_brier_gain,

        "condition_auc_ci":
            condition_auc_ci,

        "advance_pass":
            advance_pass,
    }


def persist_result(
    connection,
    spec_state,
    training_fingerprint,
    validation_fingerprint,
    development_audit,
    validation_audit,
    training_state,
    evaluation,
    intervals,
    decision,
):
    validated_at = int(
        time.time()
    )

    model = training_state[
        "model"
    ]

    connection.execute(
        """
        INSERT INTO signal_validation_results (
            validation_version,

            spec_version,
            spec_fingerprint_sha256,

            training_fingerprint_sha256,
            validation_fingerprint_sha256,

            training_rows,
            training_mints,
            training_weight,

            validation_rows,
            validation_mints,
            validation_weight,

            development_target_rate,
            validation_target_rate,

            baseline_logloss,
            model_logloss,
            logloss_gain,
            logloss_ci_lower,
            logloss_ci_upper,

            baseline_brier,
            model_brier,
            brier_gain,
            brier_ci_lower,
            brier_ci_upper,

            model_auc,
            auc_ci_lower,
            auc_ci_upper,

            condition_logloss_gain,
            condition_logloss_ci,
            condition_brier_gain,
            condition_auc_ci,

            advance_pass,

            feature_columns_json,
            model_intercept_json,
            model_coefficients_json,

            bootstrap_resamples,
            bootstrap_seed,

            validated_at
        )

        VALUES (
            ?,
            ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?,
            ?, ?, ?,
            ?, ?,
            ?
        )
        """,
        (
            VALIDATION_VERSION,

            SPEC_VERSION,
            spec_state[
                "fingerprint"
            ],

            training_fingerprint,
            validation_fingerprint,

            development_audit[
                "rows"
            ],
            development_audit[
                "mints"
            ],
            development_audit[
                "total_weight"
            ],

            validation_audit[
                "rows"
            ],
            validation_audit[
                "mints"
            ],
            validation_audit[
                "total_weight"
            ],

            development_audit[
                "weighted_target_rate"
            ],
            validation_audit[
                "weighted_target_rate"
            ],

            evaluation[
                "baseline_logloss"
            ],
            evaluation[
                "model_logloss"
            ],
            evaluation[
                "logloss_gain"
            ],
            intervals[
                "logloss"
            ][0],
            intervals[
                "logloss"
            ][1],

            evaluation[
                "baseline_brier"
            ],
            evaluation[
                "model_brier"
            ],
            evaluation[
                "brier_gain"
            ],
            intervals[
                "brier"
            ][0],
            intervals[
                "brier"
            ][1],

            evaluation[
                "model_auc"
            ],
            intervals[
                "auc"
            ][0],
            intervals[
                "auc"
            ][1],

            int(
                decision[
                    "condition_logloss_gain"
                ]
            ),
            int(
                decision[
                    "condition_logloss_ci"
                ]
            ),
            int(
                decision[
                    "condition_brier_gain"
                ]
            ),
            int(
                decision[
                    "condition_auc_ci"
                ]
            ),

            int(
                decision[
                    "advance_pass"
                ]
            ),

            json.dumps(
                training_state[
                    "feature_columns"
                ]
            ),

            json.dumps(
                training_state[
                    "model"
                ].intercept_.tolist()
            ),

            json.dumps(
                training_state[
                    "model"
                ].coef_.tolist()
            ),

            BOOTSTRAP_RESAMPLES,
            BOOTSTRAP_SEED,

            validated_at,
        ),
    )

    connection.commit()

    return validated_at


def print_dataset_audit(
    name,
    audit,
):
    print()
    print(name)
    print("-" * 80)

    print(
        f"Rows:                "
        f"{audit['rows']}"
    )

    print(
        f"Mints:               "
        f"{audit['mints']}"
    )

    print(
        f"Total mint weight:   "
        f"{audit['total_weight']:.3f}"
    )

    print(
        f"Weighted target rate:"
        f" "
        f"{100.0 * audit['weighted_target_rate']:.3f}%"
    )

    print(
        f"Max mint wt error:   "
        f"{audit['max_mint_weight_error']:.3e}"
    )

    for feature in FEATURES:
        known = audit[
            "feature_coverage"
        ][feature]

        coverage = (
            100.0
            * known
            / audit["rows"]
        )

        print(
            f"{feature:<20}"
            f"{known:>8} "
            f"({coverage:>6.2f}%)"
        )


def run_preflight():
    with get_connection() as connection:
        init_result_table(
            connection
        )

        verify_environment()

        spec_state = verify_frozen_spec(
            connection
        )

        verify_no_existing_validation(
            connection
        )

        development = (
            load_development_data(
                connection
            )
        )

        development_audit = (
            audit_dataset(
                development,
                "DEVELOPMENT",
            )
        )

        development_fingerprint = (
            dataset_fingerprint(
                development
            )
        )

        training_state = (
            build_training_state(
                development
            )
        )

    print()
    print("=" * 80)
    print(
        "DELVE MEME AGENT — "
        "SEALED VALIDATION PREFLIGHT"
    )
    print("=" * 80)

    print(
        f"Validation version:  "
        f"{VALIDATION_VERSION}"
    )

    print(
        f"Frozen spec:         "
        f"{SPEC_VERSION}"
    )

    print(
        f"Spec fingerprint:    "
        f"{spec_state['fingerprint']}"
    )

    print(
        f"Frozen at:           "
        f"{spec_state['frozen_at']}"
    )

    print(
        f"NumPy:               "
        f"{np.__version__}"
    )

    print(
        f"scikit-learn:        "
        f"{sklearn.__version__}"
    )

    print_dataset_audit(
        "DEVELOPMENT TRAINING AUDIT",
        development_audit,
    )

    print()
    print("TRAINING LOCK")
    print("-" * 80)

    print(
        "Features:            "
        + ", ".join(
            FEATURES
        )
    )

    print(
        f"Baseline probability:"
        f" "
        f"{training_state['baseline_probability']:.8f}"
    )

    print(
        f"Training fingerprint:"
        f" "
        f"{development_fingerprint}"
    )

    print()
    print(
        "SESSION 3 WAS NOT READ "
        "DURING PREFLIGHT"
    )

    print(
        "VALIDATION HAS NOT RUN"
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )

    print("=" * 80)


def run_validation():
    started = time.time()

    with get_connection() as connection:
        init_result_table(
            connection
        )

        verify_environment()

        spec_state = verify_frozen_spec(
            connection
        )

        verify_no_existing_validation(
            connection
        )

        # ----------------------------------------------------------
        # Fit everything possible before Session 3 is touched.
        # ----------------------------------------------------------

        development = (
            load_development_data(
                connection
            )
        )

        development_audit = (
            audit_dataset(
                development,
                "DEVELOPMENT",
            )
        )

        training_fingerprint = (
            dataset_fingerprint(
                development
            )
        )

        training_state = (
            build_training_state(
                development
            )
        )

        print()
        print("=" * 80)
        print(
            "DELVE MEME AGENT — "
            "SEALED ONE-SHOT VALIDATION"
        )
        print("=" * 80)

        print(
            f"Spec fingerprint: "
            f"{spec_state['fingerprint']}"
        )

        print()
        print(
            "TRAINING COMPLETE BEFORE "
            "SESSION 3 ACCESS"
        )

        print(
            "OPENING SESSION 3 PRIMARY "
            "UNSEEN-MINT HOLDOUT..."
        )

        # ==========================================================
        # SESSION 3 IS FIRST READ HERE.
        # ==========================================================

        validation = (
            load_validation_data(
                connection
            )
        )

        validation_audit = (
            audit_dataset(
                validation,
                "VALIDATION",
            )
        )

        validation_fingerprint = (
            dataset_fingerprint(
                validation
            )
        )

        overlap = (
            set(
                development[
                    "mints"
                ]
            )
            & set(
                validation[
                    "mints"
                ]
            )
        )

        if overlap:
            raise RuntimeError(
                "VALIDATION CONTAMINATION: "
                f"{len(overlap)} validation "
                "mints were present in "
                "development."
            )

        evaluation = (
            evaluate_validation(
                validation,
                training_state,
            )
        )

        bootstrap_state = (
            prepare_mint_bootstrap(
                validation,
                evaluation,
            )
        )

        bootstrap_samples = (
            run_mint_bootstrap(
                bootstrap_state
            )
        )

        intervals = {
            "logloss":
                percentile_interval(
                    bootstrap_samples[
                        "logloss"
                    ]
                ),

            "brier":
                percentile_interval(
                    bootstrap_samples[
                        "brier"
                    ]
                ),

            "auc":
                percentile_interval(
                    bootstrap_samples[
                        "auc"
                    ]
                ),
        }

        decision = determine_advance(
            evaluation,
            intervals,
        )

        validated_at = persist_result(
            connection,
            spec_state,
            training_fingerprint,
            validation_fingerprint,
            development_audit,
            validation_audit,
            training_state,
            evaluation,
            intervals,
            decision,
        )

    elapsed = (
        time.time()
        - started
    )

    print_dataset_audit(
        "DEVELOPMENT TRAINING AUDIT",
        development_audit,
    )

    print_dataset_audit(
        "SESSION 3 VALIDATION AUDIT",
        validation_audit,
    )

    print()
    print("DATASET FINGERPRINTS")
    print("-" * 80)

    print(
        f"Training:   "
        f"{training_fingerprint}"
    )

    print(
        f"Validation: "
        f"{validation_fingerprint}"
    )

    print()
    print("=" * 80)
    print("ONE-SHOT VALIDATION RESULT")
    print("=" * 80)

    print(
        f"Development baseline probability: "
        f"{training_state['baseline_probability']:.8f}"
    )

    print()
    print("LOG LOSS")
    print("-" * 80)

    print(
        f"Baseline:       "
        f"{evaluation['baseline_logloss']:.6f}"
    )

    print(
        f"Model:          "
        f"{evaluation['model_logloss']:.6f}"
    )

    print(
        f"Gain:           "
        f"{evaluation['logloss_gain']:+.6f}"
    )

    print(
        f"95% CI:         "
        f"[{intervals['logloss'][0]:+.6f}, "
        f"{intervals['logloss'][1]:+.6f}]"
    )

    print()
    print("BRIER SCORE")
    print("-" * 80)

    print(
        f"Baseline:       "
        f"{evaluation['baseline_brier']:.6f}"
    )

    print(
        f"Model:          "
        f"{evaluation['model_brier']:.6f}"
    )

    print(
        f"Gain:           "
        f"{evaluation['brier_gain']:+.6f}"
    )

    print(
        f"95% CI:         "
        f"[{intervals['brier'][0]:+.6f}, "
        f"{intervals['brier'][1]:+.6f}]"
    )

    print()
    print("ROC AUC")
    print("-" * 80)

    print(
        f"Model AUC:      "
        f"{evaluation['model_auc']:.6f}"
    )

    print(
        f"95% CI:         "
        f"[{intervals['auc'][0]:.6f}, "
        f"{intervals['auc'][1]:.6f}]"
    )

    print()
    print("FROZEN ADVANCE RULE")
    print("-" * 80)

    conditions = [
        (
            "Logloss gain >= 0.005",
            decision[
                "condition_logloss_gain"
            ],
        ),
        (
            "Logloss CI lower > 0",
            decision[
                "condition_logloss_ci"
            ],
        ),
        (
            "Brier gain >= 0",
            decision[
                "condition_brier_gain"
            ],
        ),
        (
            "AUC CI lower > 0.50",
            decision[
                "condition_auc_ci"
            ],
        ),
    ]

    for label, passed in conditions:
        state = (
            "PASS"
            if passed
            else "FAIL"
        )

        print(
            f"{label:<29} {state}"
        )

    print()
    print("=" * 80)

    if decision[
        "advance_pass"
    ]:
        print(
            "FINAL DECISION: ADVANCE"
        )
    else:
        print(
            "FINAL DECISION: DO NOT ADVANCE"
        )

    print("=" * 80)

    print(
        f"Validation record time: "
        f"{validated_at}"
    )

    print(
        f"Runtime:                "
        f"{elapsed:.1f}s"
    )

    print()
    print(
        "SESSION 3 IS NOW OPENED "
        "AND MUST NOT BE USED TO "
        "RETUNE THIS SPECIFICATION."
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )

    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Sealed Delve Meme Agent "
            "signal validation runner."
        )
    )

    mode = parser.add_mutually_exclusive_group(
        required=True
    )

    mode.add_argument(
        "--preflight",
        action="store_true",
        help=(
            "Verify frozen specification "
            "and development training path "
            "without reading Session 3."
        ),
    )

    mode.add_argument(
        "--validate",
        action="store_true",
        help=(
            "Intentionally open Session 3 "
            "and execute the frozen "
            "one-shot validation."
        ),
    )

    args = parser.parse_args()

    if args.preflight:
        run_preflight()
        return

    if args.validate:
        run_validation()
        return


if __name__ == "__main__":
    main()