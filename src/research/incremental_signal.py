import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score


DB_PATH = Path("logs/delve_meme.db")

ANALYSIS_VERSION = "incremental-signal-v1"
COHORT_VERSION = "research-cohort-v1"
FEATURE_VERSION = "derived-features-v1"
LABEL_VERSION = "fixed-horizon-labels-v1"
REDUNDANCY_VERSION = "redundancy-analysis-v1"

# ------------------------------------------------------------------
# LOCKED PROVISIONAL REPRESENTATIVES
#
# These were selected entirely from Session-2 development research.
# Hard-redundant cross-cluster alternatives were removed before this
# incremental test.
# ------------------------------------------------------------------

PROVISIONAL_FEATURES = [
    "mayhem_mode",
    "sell_rate_60s",
    "buys_per_unique_buyer_15s",
    "entry_rank",
    "sell_rate_accel_15v45",
    "prior_trade_gap_seconds",
    "buy_rate_15s",
    "trade_rate_accel_15v45",
    "sol_imbalance_60s",
    "virtual_sol_reserves_pre_sol",
    "entry_age_seconds",
    "trade_imbalance_15s",
]

# ------------------------------------------------------------------
# INTERNAL DEVELOPMENT VALIDATION
#
# Mints, never individual rows, determine fold membership.
# Hash assignment is outcome-independent and deterministic.
# ------------------------------------------------------------------

N_FOLDS = 5

REPEAT_SALTS = [
    "delve-incremental-v1-a",
    "delve-incremental-v1-b",
    "delve-incremental-v1-c",
]

# ------------------------------------------------------------------
# SELECTION POLICY — LOCKED BEFORE SESSION 3
# ------------------------------------------------------------------

MAX_SELECTED_FEATURES = 8

# Candidate must improve weighted development log loss by at least
# this amount on average.
MIN_MEAN_LOGLOSS_GAIN = 0.0010

# It must improve most individual mint-separated folds.
MIN_POSITIVE_FOLD_RATE = 0.70

# And the average gain must be positive in every repeat.
MIN_POSITIVE_REPEAT_RATE = 1.00

# Probability-quality metric must not deteriorate.
MIN_MEAN_BRIER_GAIN = 0.0

LOGISTIC_C = 1.0

EPSILON = 1e-9


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_tables(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS incremental_signal_candidates (
            analysis_version TEXT NOT NULL,

            step INTEGER NOT NULL,
            candidate_feature TEXT NOT NULL,

            base_features_json TEXT NOT NULL,
            trial_features_json TEXT NOT NULL,

            mean_base_logloss REAL NOT NULL,
            mean_trial_logloss REAL NOT NULL,

            mean_logloss_gain REAL NOT NULL,
            std_logloss_gain REAL NOT NULL,

            positive_fold_rate REAL NOT NULL,
            positive_repeat_rate REAL NOT NULL,

            mean_brier_gain REAL NOT NULL,
            mean_auc_gain REAL,

            selected INTEGER NOT NULL,

            built_at INTEGER NOT NULL,

            PRIMARY KEY (
                analysis_version,
                step,
                candidate_feature
            )
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS incremental_signal_fold_metrics (
            analysis_version TEXT NOT NULL,

            step INTEGER NOT NULL,
            candidate_feature TEXT NOT NULL,

            repeat_number INTEGER NOT NULL,
            fold_number INTEGER NOT NULL,

            test_rows INTEGER NOT NULL,
            test_mints INTEGER NOT NULL,
            test_weight REAL NOT NULL,

            base_logloss REAL NOT NULL,
            trial_logloss REAL NOT NULL,
            logloss_gain REAL NOT NULL,

            base_brier REAL NOT NULL,
            trial_brier REAL NOT NULL,
            brier_gain REAL NOT NULL,

            base_auc REAL,
            trial_auc REAL,
            auc_gain REAL,

            built_at INTEGER NOT NULL,

            PRIMARY KEY (
                analysis_version,
                step,
                candidate_feature,
                repeat_number,
                fold_number
            )
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS incremental_signal_selected (
            analysis_version TEXT NOT NULL,

            selection_order INTEGER NOT NULL,
            feature_name TEXT NOT NULL,

            selected_features_json TEXT NOT NULL,

            mean_logloss_gain REAL NOT NULL,
            positive_fold_rate REAL NOT NULL,
            positive_repeat_rate REAL NOT NULL,
            mean_brier_gain REAL NOT NULL,
            mean_auc_gain REAL,

            built_at INTEGER NOT NULL,

            PRIMARY KEY (
                analysis_version,
                selection_order
            )
        )
        """
    )

    connection.commit()


def verify_provisional_features(connection):
    placeholders = ",".join(
        "?"
        for _ in PROVISIONAL_FEATURES
    )

    rows = connection.execute(
        f"""
        SELECT
            feature_name,
            is_representative

        FROM feature_redundancy_clusters

        WHERE
            analysis_version = ?
            AND feature_name IN ({placeholders})
        """,
        (
            REDUNDANCY_VERSION,
            *PROVISIONAL_FEATURES,
        ),
    ).fetchall()

    found = {
        row["feature_name"]:
        row["is_representative"]
        for row in rows
    }

    missing = [
        feature
        for feature in PROVISIONAL_FEATURES
        if feature not in found
    ]

    nonrepresentatives = [
        feature
        for feature in PROVISIONAL_FEATURES
        if found.get(feature) != 1
    ]

    if missing:
        raise RuntimeError(
            "Provisional features missing from certified "
            f"redundancy analysis: {missing}"
        )

    if nonrepresentatives:
        raise RuntimeError(
            "Provisional features are not certified "
            f"representatives: {nonrepresentatives}"
        )


def load_development_data(connection):
    feature_sql = ",\n".join(
        f'd."{feature}" AS "{feature}"'
        for feature in PROVISIONAL_FEATURES
    )

    rows = connection.execute(
        f"""
        SELECT
            rc.entry_signature,
            rc.mint,
            rc.mint_weight_15m AS row_weight,

            labels.hit_2x_15m AS target,

            {feature_sql}

        FROM research_cohort rc

        JOIN derived_features d
          ON d.entry_signature = rc.entry_signature
         AND d.derived_version = ?

        JOIN fixed_horizon_labels labels
          ON labels.entry_signature = rc.entry_signature
         AND labels.label_version = ?

        WHERE
            rc.cohort_version = ?
            AND rc.split = 'development'
            AND rc.eligible_15m = 1

        ORDER BY
            rc.entry_timestamp ASC,
            rc.entry_signature ASC
        """,
        (
            FEATURE_VERSION,
            LABEL_VERSION,
            COHORT_VERSION,
        ),
    ).fetchall()

    if not rows:
        raise RuntimeError(
            "No development rows found"
        )

    mints = np.array(
        [
            row["mint"]
            for row in rows
        ],
        dtype=object,
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

    for feature in PROVISIONAL_FEATURES:
        values = np.full(
            len(rows),
            np.nan,
            dtype=np.float64,
        )

        for index, row in enumerate(rows):
            value = row[feature]

            if value is None:
                continue

            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue

            if math.isfinite(numeric):
                values[index] = numeric

        raw_features[feature] = values

    return {
        "mints": mints,
        "weights": weights,
        "targets": targets,
        "features": raw_features,
    }


def fold_for_mint(
    mint,
    salt,
):
    digest = hashlib.sha256(
        f"{salt}|{mint}".encode("utf-8")
    ).hexdigest()

    integer = int(
        digest[:16],
        16,
    )

    return integer % N_FOLDS


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
        minlength=len(unique_values),
    )

    total_weight = float(
        value_weights.sum()
    )

    if total_weight <= 0:
        return None

    cumulative_before = (
        np.cumsum(value_weights)
        - value_weights
    )

    midranks = (
        cumulative_before
        + (value_weights / 2.0)
    ) / total_weight

    return {
        "values": unique_values,
        "ranks": midranks,
    }


def apply_weighted_ecdf(
    values,
    ecdf,
):
    known = np.isfinite(values)

    rank_values = np.full(
        len(values),
        0.5,
        dtype=np.float32,
    )

    missing_indicator = (
        ~known
    ).astype(
        np.float32
    )

    if ecdf is None:
        return np.column_stack(
            (
                rank_values,
                missing_indicator,
            )
        ).astype(
            np.float32,
            copy=False,
        )

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
        ).astype(
            np.float32
        )

    return np.column_stack(
        (
            rank_values,
            missing_indicator,
        )
    ).astype(
        np.float32,
        copy=False,
    )


def build_fold_cache(data):
    mints = data["mints"]
    weights = data["weights"]
    targets = data["targets"]

    cache = []

    for repeat_number, salt in enumerate(
        REPEAT_SALTS,
        start=1,
    ):
        fold_ids = np.array(
            [
                fold_for_mint(
                    mint,
                    salt,
                )
                for mint in mints
            ],
            dtype=np.int8,
        )

        for fold_number in range(
            N_FOLDS
        ):
            test_mask = (
                fold_ids == fold_number
            )

            train_mask = ~test_mask

            train_indices = np.flatnonzero(
                train_mask
            )

            test_indices = np.flatnonzero(
                test_mask
            )

            if (
                len(train_indices) == 0
                or len(test_indices) == 0
            ):
                raise RuntimeError(
                    "Empty internal CV fold"
                )

            feature_train_parts = []
            feature_test_parts = []

            column_map = {}

            current_column = 0

            for feature in PROVISIONAL_FEATURES:
                raw_values = data[
                    "features"
                ][feature]

                ecdf = fit_weighted_ecdf(
                    raw_values[
                        train_indices
                    ],
                    weights[
                        train_indices
                    ],
                )

                train_part = (
                    apply_weighted_ecdf(
                        raw_values[
                            train_indices
                        ],
                        ecdf,
                    )
                )

                test_part = (
                    apply_weighted_ecdf(
                        raw_values[
                            test_indices
                        ],
                        ecdf,
                    )
                )

                feature_train_parts.append(
                    train_part
                )

                feature_test_parts.append(
                    test_part
                )

                column_map[feature] = (
                    current_column,
                    current_column + 1,
                )

                current_column += 2

            x_train = np.concatenate(
                feature_train_parts,
                axis=1,
            )

            x_test = np.concatenate(
                feature_test_parts,
                axis=1,
            )

            test_mints = set(
                mints[test_indices]
            )

            cache.append(
                {
                    "repeat_number":
                        repeat_number,
                    "fold_number":
                        fold_number + 1,

                    "x_train":
                        x_train,
                    "x_test":
                        x_test,

                    "y_train":
                        targets[
                            train_indices
                        ],
                    "y_test":
                        targets[
                            test_indices
                        ],

                    "w_train":
                        weights[
                            train_indices
                        ],
                    "w_test":
                        weights[
                            test_indices
                        ],

                    "test_rows":
                        len(test_indices),
                    "test_mints":
                        len(test_mints),
                    "test_weight":
                        float(
                            weights[
                                test_indices
                            ].sum()
                        ),

                    "column_map":
                        column_map,
                }
            )

    return cache


def feature_columns(
    feature_names,
    column_map,
):
    columns = []

    for feature in feature_names:
        start, end = column_map[
            feature
        ]

        columns.extend(
            range(
                start,
                end + 1,
            )
        )

    return columns


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
            values * weights
        ) / denominator
    )


def weighted_logloss(
    y,
    probabilities,
    weights,
):
    probabilities = np.clip(
        probabilities,
        EPSILON,
        1.0 - EPSILON,
    )

    losses = -(
        y * np.log(probabilities)
        + (1 - y)
        * np.log(
            1.0 - probabilities
        )
    )

    return weighted_mean(
        losses,
        weights,
    )


def weighted_brier(
    y,
    probabilities,
    weights,
):
    errors = (
        probabilities - y
    ) ** 2

    return weighted_mean(
        errors,
        weights,
    )


def weighted_auc(
    y,
    probabilities,
    weights,
):
    if len(
        np.unique(y)
    ) < 2:
        return None

    try:
        return float(
            roc_auc_score(
                y,
                probabilities,
                sample_weight=weights,
            )
        )
    except ValueError:
        return None


def predict_feature_set(
    fold,
    feature_names,
):
    y_train = fold["y_train"]
    y_test = fold["y_test"]

    w_train = fold["w_train"]
    w_test = fold["w_test"]

    if not feature_names:
        probability = weighted_mean(
            y_train,
            w_train,
        )

        predictions = np.full(
            len(y_test),
            probability,
            dtype=np.float64,
        )

    else:
        columns = feature_columns(
            feature_names,
            fold["column_map"],
        )

        x_train = fold[
            "x_train"
        ][:, columns]

        x_test = fold[
            "x_test"
        ][:, columns]

        model = LogisticRegression(
            C=LOGISTIC_C,
            solver="lbfgs",
            max_iter=2000,
            tol=1e-7,
            random_state=0,
        )

        model.fit(
            x_train,
            y_train,
            sample_weight=w_train,
        )

        predictions = model.predict_proba(
            x_test
        )[:, 1]

    return {
        "logloss":
            weighted_logloss(
                y_test,
                predictions,
                w_test,
            ),

        "brier":
            weighted_brier(
                y_test,
                predictions,
                w_test,
            ),

        "auc":
            weighted_auc(
                y_test,
                predictions,
                w_test,
            ),
    }


def evaluate_feature_set(
    fold_cache,
    feature_names,
):
    results = []

    for fold in fold_cache:
        metrics = predict_feature_set(
            fold,
            feature_names,
        )

        results.append(
            {
                "repeat_number":
                    fold[
                        "repeat_number"
                    ],

                "fold_number":
                    fold[
                        "fold_number"
                    ],

                "test_rows":
                    fold[
                        "test_rows"
                    ],

                "test_mints":
                    fold[
                        "test_mints"
                    ],

                "test_weight":
                    fold[
                        "test_weight"
                    ],

                **metrics,
            }
        )

    return results


def summarize_trial(
    base_results,
    trial_results,
):
    logloss_gains = []
    brier_gains = []
    auc_gains = []

    repeat_gains = {}

    for base, trial in zip(
        base_results,
        trial_results,
    ):
        logloss_gain = (
            base["logloss"]
            - trial["logloss"]
        )

        brier_gain = (
            base["brier"]
            - trial["brier"]
        )

        logloss_gains.append(
            logloss_gain
        )

        brier_gains.append(
            brier_gain
        )

        if (
            base["auc"] is not None
            and trial["auc"]
            is not None
        ):
            auc_gains.append(
                trial["auc"]
                - base["auc"]
            )

        repeat_number = base[
            "repeat_number"
        ]

        repeat_gains.setdefault(
            repeat_number,
            [],
        ).append(
            logloss_gain
        )

    repeat_mean_gains = [
        float(
            np.mean(gains)
        )
        for gains
        in repeat_gains.values()
    ]

    return {
        "mean_base_logloss":
            float(
                np.mean(
                    [
                        result[
                            "logloss"
                        ]
                        for result
                        in base_results
                    ]
                )
            ),

        "mean_trial_logloss":
            float(
                np.mean(
                    [
                        result[
                            "logloss"
                        ]
                        for result
                        in trial_results
                    ]
                )
            ),

        "mean_logloss_gain":
            float(
                np.mean(
                    logloss_gains
                )
            ),

        "std_logloss_gain":
            float(
                np.std(
                    logloss_gains,
                    ddof=0,
                )
            ),

        "positive_fold_rate":
            float(
                np.mean(
                    np.array(
                        logloss_gains
                    ) > 0
                )
            ),

        "positive_repeat_rate":
            float(
                np.mean(
                    np.array(
                        repeat_mean_gains
                    ) > 0
                )
            ),

        "mean_brier_gain":
            float(
                np.mean(
                    brier_gains
                )
            ),

        "mean_auc_gain":
            (
                float(
                    np.mean(
                        auc_gains
                    )
                )
                if auc_gains
                else None
            ),

        "fold_logloss_gains":
            logloss_gains,

        "fold_brier_gains":
            brier_gains,

        "fold_auc_gains":
            auc_gains,
    }


def passes_selection_policy(
    summary,
):
    return (
        summary[
            "mean_logloss_gain"
        ]
        >= MIN_MEAN_LOGLOSS_GAIN

        and summary[
            "positive_fold_rate"
        ]
        >= MIN_POSITIVE_FOLD_RATE

        and summary[
            "positive_repeat_rate"
        ]
        >= MIN_POSITIVE_REPEAT_RATE

        and summary[
            "mean_brier_gain"
        ]
        >= MIN_MEAN_BRIER_GAIN
    )


def insert_candidate_result(
    connection,
    step,
    candidate,
    base_features,
    trial_features,
    summary,
    selected,
    built_at,
):
    connection.execute(
        """
        INSERT INTO incremental_signal_candidates (
            analysis_version,

            step,
            candidate_feature,

            base_features_json,
            trial_features_json,

            mean_base_logloss,
            mean_trial_logloss,

            mean_logloss_gain,
            std_logloss_gain,

            positive_fold_rate,
            positive_repeat_rate,

            mean_brier_gain,
            mean_auc_gain,

            selected,

            built_at
        )

        VALUES (
            ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?,
            ?
        )
        """,
        (
            ANALYSIS_VERSION,

            step,
            candidate,

            json.dumps(
                base_features
            ),
            json.dumps(
                trial_features
            ),

            summary[
                "mean_base_logloss"
            ],
            summary[
                "mean_trial_logloss"
            ],

            summary[
                "mean_logloss_gain"
            ],
            summary[
                "std_logloss_gain"
            ],

            summary[
                "positive_fold_rate"
            ],
            summary[
                "positive_repeat_rate"
            ],

            summary[
                "mean_brier_gain"
            ],
            summary[
                "mean_auc_gain"
            ],

            int(selected),

            built_at,
        ),
    )


def insert_fold_metrics(
    connection,
    step,
    candidate,
    base_results,
    trial_results,
    built_at,
):
    for base, trial in zip(
        base_results,
        trial_results,
    ):
        base_auc = base["auc"]
        trial_auc = trial["auc"]

        auc_gain = (
            trial_auc - base_auc
            if (
                base_auc is not None
                and trial_auc
                is not None
            )
            else None
        )

        connection.execute(
            """
            INSERT INTO incremental_signal_fold_metrics (
                analysis_version,

                step,
                candidate_feature,

                repeat_number,
                fold_number,

                test_rows,
                test_mints,
                test_weight,

                base_logloss,
                trial_logloss,
                logloss_gain,

                base_brier,
                trial_brier,
                brier_gain,

                base_auc,
                trial_auc,
                auc_gain,

                built_at
            )

            VALUES (
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?
            )
            """,
            (
                ANALYSIS_VERSION,

                step,
                candidate,

                base[
                    "repeat_number"
                ],
                base[
                    "fold_number"
                ],

                base[
                    "test_rows"
                ],
                base[
                    "test_mints"
                ],
                base[
                    "test_weight"
                ],

                base[
                    "logloss"
                ],
                trial[
                    "logloss"
                ],
                (
                    base[
                        "logloss"
                    ]
                    - trial[
                        "logloss"
                    ]
                ),

                base[
                    "brier"
                ],
                trial[
                    "brier"
                ],
                (
                    base[
                        "brier"
                    ]
                    - trial[
                        "brier"
                    ]
                ),

                base_auc,
                trial_auc,
                auc_gain,

                built_at,
            ),
        )


def rebuild_incremental_signal():
    started = time.time()
    built_at = int(time.time())

    with get_connection() as connection:
        init_tables(connection)

        verify_provisional_features(
            connection
        )

        print()
        print("=" * 80)
        print(
            "DELVE MEME AGENT — "
            "INCREMENTAL SIGNAL ANALYSIS"
        )
        print("=" * 80)
        print(
            f"Version:             "
            f"{ANALYSIS_VERSION}"
        )
        print(
            "Split:               "
            "DEVELOPMENT ONLY"
        )
        print(
            "Primary target:      "
            "2x within 15 minutes"
        )
        print(
            "CV unit:             "
            "mint"
        )
        print(
            f"CV design:           "
            f"{len(REPEAT_SALTS)} repeats "
            f"x {N_FOLDS} folds"
        )
        print(
            "Transform:           "
            "training-only weighted ECDF"
        )
        print(
            "Model:               "
            "L2 logistic regression"
        )
        print(
            "Weighting:           "
            "mint-balanced"
        )
        print(
            "VALIDATION DATA IS NOT READ"
        )
        print("=" * 80)

        connection.execute(
            """
            DELETE FROM incremental_signal_fold_metrics
            WHERE analysis_version = ?
            """,
            (ANALYSIS_VERSION,),
        )

        connection.execute(
            """
            DELETE FROM incremental_signal_candidates
            WHERE analysis_version = ?
            """,
            (ANALYSIS_VERSION,),
        )

        connection.execute(
            """
            DELETE FROM incremental_signal_selected
            WHERE analysis_version = ?
            """,
            (ANALYSIS_VERSION,),
        )

        data = load_development_data(
            connection
        )

        print()
        print("INPUT")
        print("-" * 80)
        print(
            f"Rows:                "
            f"{len(data['targets'])}"
        )
        print(
            f"Mints:               "
            f"{len(set(data['mints']))}"
        )
        print(
            f"Total mint weight:   "
            f"{data['weights'].sum():.3f}"
        )
        print(
            f"Provisional features:"
            f" {len(PROVISIONAL_FEATURES)}"
        )

        print()
        print(
            "Building deterministic "
            "mint-separated fold cache..."
        )

        fold_cache = build_fold_cache(
            data
        )

        print()
        print("FOLD AUDIT")
        print("-" * 80)

        for fold in fold_cache:
            print(
                f"repeat={fold['repeat_number']} "
                f"fold={fold['fold_number']}  "
                f"rows={fold['test_rows']:<7} "
                f"mints={fold['test_mints']:<4} "
                f"weight={fold['test_weight']:.2f}"
            )

        selected_features = []
        remaining_features = list(
            PROVISIONAL_FEATURES
        )

        base_results = (
            evaluate_feature_set(
                fold_cache,
                selected_features,
            )
        )

        print()
        print("=" * 80)
        print("FORWARD INCREMENTAL SELECTION")
        print("=" * 80)

        selection_order = 0

        for step in range(
            1,
            MAX_SELECTED_FEATURES + 1,
        ):
            if not remaining_features:
                break

            print()
            print(
                f"STEP {step}"
            )
            print("-" * 80)
            print(
                "Current features: "
                + (
                    ", ".join(
                        selected_features
                    )
                    if selected_features
                    else "[intercept only]"
                )
            )

            candidate_results = []

            for candidate in (
                remaining_features
            ):
                trial_features = (
                    selected_features
                    + [candidate]
                )

                trial_results = (
                    evaluate_feature_set(
                        fold_cache,
                        trial_features,
                    )
                )

                summary = summarize_trial(
                    base_results,
                    trial_results,
                )

                candidate_results.append(
                    {
                        "feature":
                            candidate,
                        "trial_features":
                            trial_features,
                        "trial_results":
                            trial_results,
                        "summary":
                            summary,
                    }
                )

            candidate_results.sort(
                key=lambda item: (
                    item["summary"][
                        "mean_logloss_gain"
                    ],
                    item["summary"][
                        "positive_fold_rate"
                    ],
                    item["summary"][
                        "mean_brier_gain"
                    ],
                ),
                reverse=True,
            )

            print()
            print(
                "Top incremental candidates:"
            )

            for item in (
                candidate_results[:5]
            ):
                summary = item[
                    "summary"
                ]

                auc_text = (
                    f"{summary['mean_auc_gain']:+.5f}"
                    if summary[
                        "mean_auc_gain"
                    ] is not None
                    else "N/A"
                )

                print(
                    f"{item['feature']:<34}"
                    f"logloss_gain="
                    f"{summary['mean_logloss_gain']:+.6f}  "
                    f"fold+={100.0 * summary['positive_fold_rate']:>5.1f}%  "
                    f"repeat+={100.0 * summary['positive_repeat_rate']:>5.1f}%  "
                    f"brier_gain="
                    f"{summary['mean_brier_gain']:+.6f}  "
                    f"auc_gain={auc_text}"
                )

            winner = (
                candidate_results[0]
            )

            winner_passes = (
                passes_selection_policy(
                    winner[
                        "summary"
                    ]
                )
            )

            for item in candidate_results:
                selected = (
                    item["feature"]
                    == winner["feature"]
                    and winner_passes
                )

                insert_candidate_result(
                    connection,
                    step,
                    item["feature"],
                    selected_features,
                    item["trial_features"],
                    item["summary"],
                    selected,
                    built_at,
                )

                insert_fold_metrics(
                    connection,
                    step,
                    item["feature"],
                    base_results,
                    item["trial_results"],
                    built_at,
                )

            if not winner_passes:
                print()
                print(
                    "SELECTION STOPPED"
                )
                print(
                    "Best remaining feature "
                    "did not satisfy the locked "
                    "incremental-information policy."
                )

                connection.commit()
                break

            selection_order += 1

            selected_features.append(
                winner["feature"]
            )

            remaining_features.remove(
                winner["feature"]
            )

            base_results = winner[
                "trial_results"
            ]

            summary = winner[
                "summary"
            ]

            connection.execute(
                """
                INSERT INTO incremental_signal_selected (
                    analysis_version,

                    selection_order,
                    feature_name,

                    selected_features_json,

                    mean_logloss_gain,
                    positive_fold_rate,
                    positive_repeat_rate,
                    mean_brier_gain,
                    mean_auc_gain,

                    built_at
                )

                VALUES (
                    ?, ?, ?,
                    ?,
                    ?, ?, ?, ?, ?,
                    ?
                )
                """,
                (
                    ANALYSIS_VERSION,

                    selection_order,
                    winner[
                        "feature"
                    ],

                    json.dumps(
                        selected_features
                    ),

                    summary[
                        "mean_logloss_gain"
                    ],
                    summary[
                        "positive_fold_rate"
                    ],
                    summary[
                        "positive_repeat_rate"
                    ],
                    summary[
                        "mean_brier_gain"
                    ],
                    summary[
                        "mean_auc_gain"
                    ],

                    built_at,
                ),
            )

            connection.commit()

            print()
            print(
                "SELECTED: "
                f"{winner['feature']}"
            )

        final_metrics = (
            evaluate_feature_set(
                fold_cache,
                selected_features,
            )
        )

        intercept_metrics = (
            evaluate_feature_set(
                fold_cache,
                [],
            )
        )

        final_summary = summarize_trial(
            intercept_metrics,
            final_metrics,
        )

        elapsed = (
            time.time() - started
        )

        print()
        print("=" * 80)
        print("FINAL DEVELOPMENT SHORTLIST")
        print("=" * 80)

        if selected_features:
            for index, feature in enumerate(
                selected_features,
                start=1,
            ):
                print(
                    f"{index:02d}. {feature}"
                )

        else:
            print(
                "No feature survived "
                "incremental selection."
            )

        print()
        print(
            f"Selected features:     "
            f"{len(selected_features)}"
        )
        print(
            f"Intercept log loss:    "
            f"{final_summary['mean_base_logloss']:.6f}"
        )
        print(
            f"Final log loss:        "
            f"{final_summary['mean_trial_logloss']:.6f}"
        )
        print(
            f"Total logloss gain:    "
            f"{final_summary['mean_logloss_gain']:+.6f}"
        )
        print(
            f"Total Brier gain:      "
            f"{final_summary['mean_brier_gain']:+.6f}"
        )

        if (
            final_summary[
                "mean_auc_gain"
            ] is not None
        ):
            print(
                f"Total AUC gain:        "
                f"{final_summary['mean_auc_gain']:+.6f}"
            )

        print(
            f"Runtime:               "
            f"{elapsed:.1f}s"
        )
        print("=" * 80)


if __name__ == "__main__":
    rebuild_incremental_signal()