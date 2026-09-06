from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path

import numpy as np

from src.research.signal_validation import (
    FEATURES,
    SPEC_VERSION,
    VALIDATION_VERSION,
    dataset_fingerprint,
    fit_weighted_ecdf,
    load_development_data,
)


DB_PATH = Path("logs/delve_meme.db")

ARTIFACT_VERSION = "validated-signal-artifact-v1"

ARTIFACT_PATH = Path(
    "artifacts/models/validated-signal-artifact-v1.json"
)


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def to_builtin(value):
    """
    Convert numpy / tuple structures into deterministic JSON-safe
    Python primitives.
    """

    if isinstance(value, np.ndarray):
        return [
            to_builtin(item)
            for item in value.tolist()
        ]

    if isinstance(value, np.generic):
        return to_builtin(value.item())

    if isinstance(value, dict):
        return {
            str(key): to_builtin(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            to_builtin(item)
            for item in value
        ]

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(
                "Artifact contains non-finite float."
            )

        return value

    if isinstance(
        value,
        (
            str,
            int,
            bool,
        ),
    ) or value is None:
        return value

    raise TypeError(
        f"Unsupported artifact value type: "
        f"{type(value)!r}"
    )


def canonical_json(value):
    return json.dumps(
        to_builtin(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_payload(payload):
    encoded = canonical_json(
        payload
    ).encode("utf-8")

    return hashlib.sha256(
        encoded
    ).hexdigest()


def init_artifact_table(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS validated_signal_artifacts (
            artifact_version TEXT PRIMARY KEY,

            validation_version TEXT NOT NULL,
            spec_version TEXT NOT NULL,

            spec_fingerprint_sha256 TEXT NOT NULL,
            training_fingerprint_sha256 TEXT NOT NULL,
            validation_fingerprint_sha256 TEXT NOT NULL,

            artifact_sha256 TEXT NOT NULL,

            feature_names_json TEXT NOT NULL,
            feature_columns_json TEXT NOT NULL,
            ecdfs_json TEXT NOT NULL,

            model_intercept_json TEXT NOT NULL,
            model_coefficients_json TEXT NOT NULL,

            development_baseline_probability REAL NOT NULL,

            validation_recorded_at INTEGER NOT NULL,
            frozen_at INTEGER NOT NULL,

            payload_json TEXT NOT NULL
        )
        """
    )

    connection.commit()


def load_validation_record(connection):
    row = connection.execute(
        """
        SELECT
            validation_version,
            spec_version,

            spec_fingerprint_sha256,
            training_fingerprint_sha256,
            validation_fingerprint_sha256,

            advance_pass,

            feature_columns_json,
            model_intercept_json,
            model_coefficients_json,

            development_target_rate,
            validated_at

        FROM signal_validation_results

        WHERE validation_version = ?
        """,
        (
            VALIDATION_VERSION,
        ),
    ).fetchone()

    if row is None:
        raise RuntimeError(
            f"No validation record exists for "
            f"{VALIDATION_VERSION}."
        )

    if int(
        row["advance_pass"]
    ) != 1:
        raise RuntimeError(
            "Validated signal did not receive "
            "ADVANCE authorization."
        )

    if (
        row["spec_version"]
        != SPEC_VERSION
    ):
        raise RuntimeError(
            "Validation record spec version "
            "does not match code."
        )

    return row


def rebuild_development_ecdfs(
    development,
):
    """
    Reconstruct ONLY the development-fitted transform.

    This does not fit or refit the logistic model.
    """

    weights = development[
        "weights"
    ]

    ecdfs = {}
    feature_columns = []

    for feature in FEATURES:
        ecdf = fit_weighted_ecdf(
            development[
                "features"
            ][feature],
            weights,
        )

        ecdfs[feature] = ecdf

        feature_columns.extend(
            [
                f"{feature}__ecdf",
                f"{feature}__missing",
            ]
        )

    return (
        ecdfs,
        feature_columns,
    )


def validate_model_dimensions(
    feature_columns,
    intercept,
    coefficients,
):
    if len(intercept) != 1:
        raise RuntimeError(
            "Expected exactly one logistic "
            "intercept."
        )

    if len(coefficients) != 1:
        raise RuntimeError(
            "Expected one coefficient row."
        )

    if (
        len(coefficients[0])
        != len(feature_columns)
    ):
        raise RuntimeError(
            "Coefficient count does not match "
            "transformed feature-column count."
        )


def write_artifact_file(
    artifact_sha256,
    payload,
):
    ARTIFACT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    document = {
        "artifact_sha256":
            artifact_sha256,
        "payload":
            payload,
    }

    serialized = (
        json.dumps(
            to_builtin(document),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )

    if ARTIFACT_PATH.exists():
        existing = json.loads(
            ARTIFACT_PATH.read_text(
                encoding="utf-8"
            )
        )

        existing_hash = existing.get(
            "artifact_sha256"
        )

        if (
            existing_hash
            != artifact_sha256
        ):
            raise RuntimeError(
                "Artifact file already exists "
                "with a DIFFERENT fingerprint. "
                "Refusing overwrite."
            )

        return False

    ARTIFACT_PATH.write_text(
        serialized,
        encoding="utf-8",
    )

    return True


def freeze_validated_signal():
    started = time.time()

    with get_connection() as connection:
        init_artifact_table(
            connection
        )

        validation = (
            load_validation_record(
                connection
            )
        )

        # ---------------------------------
        # DEVELOPMENT DATA ONLY
        # ---------------------------------

        development = (
            load_development_data(
                connection
            )
        )

        current_training_fingerprint = (
            dataset_fingerprint(
                development
            )
        )

        expected_training_fingerprint = (
            validation[
                "training_fingerprint_sha256"
            ]
        )

        if (
            current_training_fingerprint
            != expected_training_fingerprint
        ):
            raise RuntimeError(
                "Development dataset fingerprint "
                "does not match the dataset used "
                "for sealed validation."
            )

        # ---------------------------------
        # REBUILD TRANSFORM ONLY
        # ---------------------------------

        (
            ecdfs,
            feature_columns,
        ) = rebuild_development_ecdfs(
            development
        )

        expected_feature_columns = (
            json.loads(
                validation[
                    "feature_columns_json"
                ]
            )
        )

        if (
            feature_columns
            != expected_feature_columns
        ):
            raise RuntimeError(
                "Transformed feature-column "
                "ordering does not match the "
                "validated model."
            )

        # ---------------------------------
        # USE VALIDATED MODEL PARAMETERS
        # DO NOT REFIT
        # ---------------------------------

        intercept = json.loads(
            validation[
                "model_intercept_json"
            ]
        )

        coefficients = json.loads(
            validation[
                "model_coefficients_json"
            ]
        )

        validate_model_dimensions(
            feature_columns,
            intercept,
            coefficients,
        )

        # ---------------------------------
        # BUILD IMMUTABLE PAYLOAD
        # ---------------------------------

        payload = {
            "artifact_version":
                ARTIFACT_VERSION,

            "validation_version":
                validation[
                    "validation_version"
                ],

            "spec_version":
                validation[
                    "spec_version"
                ],

            "spec_fingerprint_sha256":
                validation[
                    "spec_fingerprint_sha256"
                ],

            "training_fingerprint_sha256":
                current_training_fingerprint,

            "validation_fingerprint_sha256":
                validation[
                    "validation_fingerprint_sha256"
                ],

            "target": {
                "event":
                    "2x",
                "horizon_seconds":
                    900,
            },

            "raw_features":
                list(FEATURES),

            "feature_columns":
                feature_columns,

            "transform": {
                "kind":
                    "development_fitted_weighted_ecdf",

                "missing_indicator":
                    True,

                "ecdfs":
                    to_builtin(ecdfs),
            },

            "model": {
                "kind":
                    "logistic_regression",

                "intercept":
                    intercept,

                "coefficients":
                    coefficients,
            },

            "development_baseline_probability":
                float(
                    validation[
                        "development_target_rate"
                    ]
                ),

            "validation_recorded_at":
                int(
                    validation[
                        "validated_at"
                    ]
                ),
        }

        artifact_sha256 = (
            sha256_payload(
                payload
            )
        )

        payload_json = (
            canonical_json(
                payload
            )
        )

        frozen_at = int(
            time.time()
        )

        # ---------------------------------
        # IMMUTABILITY CHECK
        # ---------------------------------

        existing = connection.execute(
            """
            SELECT
                artifact_sha256

            FROM validated_signal_artifacts

            WHERE artifact_version = ?
            """,
            (
                ARTIFACT_VERSION,
            ),
        ).fetchone()

        if existing is not None:
            if (
                existing[
                    "artifact_sha256"
                ]
                != artifact_sha256
            ):
                raise RuntimeError(
                    "Artifact version already "
                    "exists with a DIFFERENT "
                    "fingerprint. Refusing mutation."
                )

            state = (
                "ALREADY FROZEN — "
                "IDENTICAL ARTIFACT"
            )

        else:
            connection.execute(
                """
                INSERT INTO validated_signal_artifacts (
                    artifact_version,

                    validation_version,
                    spec_version,

                    spec_fingerprint_sha256,
                    training_fingerprint_sha256,
                    validation_fingerprint_sha256,

                    artifact_sha256,

                    feature_names_json,
                    feature_columns_json,
                    ecdfs_json,

                    model_intercept_json,
                    model_coefficients_json,

                    development_baseline_probability,

                    validation_recorded_at,
                    frozen_at,

                    payload_json
                )
                VALUES (
                    ?, ?, ?,
                    ?, ?, ?,
                    ?,
                    ?, ?, ?,
                    ?, ?,
                    ?,
                    ?, ?,
                    ?
                )
                """,
                (
                    ARTIFACT_VERSION,

                    validation[
                        "validation_version"
                    ],
                    validation[
                        "spec_version"
                    ],

                    validation[
                        "spec_fingerprint_sha256"
                    ],
                    current_training_fingerprint,
                    validation[
                        "validation_fingerprint_sha256"
                    ],

                    artifact_sha256,

                    canonical_json(
                        list(FEATURES)
                    ),
                    canonical_json(
                        feature_columns
                    ),
                    canonical_json(
                        ecdfs
                    ),

                    canonical_json(
                        intercept
                    ),
                    canonical_json(
                        coefficients
                    ),

                    float(
                        validation[
                            "development_target_rate"
                        ]
                    ),

                    int(
                        validation[
                            "validated_at"
                        ]
                    ),
                    frozen_at,

                    payload_json,
                ),
            )

            connection.commit()

            state = (
                "NEW VALIDATED ARTIFACT FROZEN"
            )

        file_created = (
            write_artifact_file(
                artifact_sha256,
                payload,
            )
        )

    elapsed = (
        time.time()
        - started
    )

    print()
    print("=" * 76)
    print(
        "DELVE MEME AGENT — "
        "VALIDATED SIGNAL ARTIFACT"
    )
    print("=" * 76)

    print(
        f"State:                 "
        f"{state}"
    )

    print(
        f"Artifact version:      "
        f"{ARTIFACT_VERSION}"
    )

    print(
        f"Validation version:    "
        f"{VALIDATION_VERSION}"
    )

    print(
        f"Spec version:          "
        f"{SPEC_VERSION}"
    )

    print()

    print("MODEL CONTRACT")
    print("-" * 76)

    print(
        f"Raw features:          "
        f"{', '.join(FEATURES)}"
    )

    print(
        f"Transformed columns:   "
        f"{len(feature_columns)}"
    )

    for column in feature_columns:
        print(
            f"  - {column}"
        )

    print()

    print(
        f"Intercept:             "
        f"{intercept}"
    )

    print(
        f"Coefficients:          "
        f"{coefficients}"
    )

    print()

    print("PROVENANCE")
    print("-" * 76)

    print(
        f"Spec fingerprint:      "
        f"{validation['spec_fingerprint_sha256']}"
    )

    print(
        f"Training fingerprint:  "
        f"{current_training_fingerprint}"
    )

    print(
        f"Validation fingerprint:"
        f"  "
        f"{validation['validation_fingerprint_sha256']}"
    )

    print()

    print("ARTIFACT")
    print("-" * 76)

    print(
        f"SHA-256:               "
        f"{artifact_sha256}"
    )

    print(
        f"File:                  "
        f"{ARTIFACT_PATH}"
    )

    print(
        f"File created:          "
        f"{'YES' if file_created else 'NO — already identical'}"
    )

    print()

    print(
        "SESSION 3 WAS NOT USED TO FIT OR "
        "MODIFY THIS ARTIFACT."
    )

    print(
        "LOGISTIC MODEL WAS NOT REFIT."
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )

    print()

    print(
        f"Runtime:               "
        f"{elapsed:.2f}s"
    )

    print("=" * 76)


def main():
    freeze_validated_signal()


if __name__ == "__main__":
    main()