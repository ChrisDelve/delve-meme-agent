from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import numpy as np

from src.models.validated_signal import (
    DEFAULT_ARTIFACT_PATH,
    ValidatedSignal,
)

from src.research.signal_validation import (
    apply_weighted_ecdf as research_apply_weighted_ecdf,
)


DB_PATH = Path(
    "logs/delve_meme.db"
)

COHORT_VERSION = (
    "research-cohort-v1"
)

DERIVED_VERSION = (
    "derived-features-v1"
)

SAMPLE_LIMIT = 5000

ABS_TOLERANCE = 1e-7


def sigmoid(value):
    if value >= 0:
        exponent = math.exp(
            -value
        )

        return 1.0 / (
            1.0 + exponent
        )

    exponent = math.exp(
        value
    )

    return exponent / (
        1.0 + exponent
    )


def load_development_rows():
    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    rows = connection.execute(
        """
        SELECT
            d.entry_signature,
            d.mayhem_mode,
            d.buy_rate_15s

        FROM derived_features d

        JOIN research_cohort rc
          ON rc.entry_signature =
             d.entry_signature

        WHERE
            d.derived_version = ?
            AND rc.cohort_version = ?
            AND rc.split = 'development'
            AND rc.eligible_15m = 1

        ORDER BY
            d.entry_signature ASC

        LIMIT ?
        """,
        (
            DERIVED_VERSION,
            COHORT_VERSION,
            SAMPLE_LIMIT,
        ),
    ).fetchall()

    connection.close()

    return rows


def raw_array(value):
    if value is None:
        return np.asarray(
            [np.nan],
            dtype=np.float64,
        )

    return np.asarray(
        [float(value)],
        dtype=np.float64,
    )


def research_transform(
    signal,
    *,
    mayhem_mode,
    buy_rate_15s,
):
    raw = {
        "mayhem_mode":
            mayhem_mode,

        "buy_rate_15s":
            buy_rate_15s,
    }

    parts = []

    for feature in signal.raw_features:
        transformed = (
            research_apply_weighted_ecdf(
                raw_array(
                    raw[feature]
                ),
                signal.ecdfs[
                    feature
                ],
            )
        )

        parts.extend(
            [
                float(
                    transformed[
                        0,
                        0,
                    ]
                ),

                float(
                    transformed[
                        0,
                        1,
                    ]
                ),
            ]
        )

    return parts


def research_probability(
    signal,
    vector,
):
    logit = signal.intercept

    for (
        coefficient,
        value,
    ) in zip(
        signal.coefficients,
        vector,
        strict=True,
    ):
        logit += (
            coefficient
            * value
        )

    return sigmoid(
        logit
    )


def main():
    signal = ValidatedSignal(
        DEFAULT_ARTIFACT_PATH
    )

    rows = (
        load_development_rows()
    )

    if not rows:
        raise RuntimeError(
            "No development rows found "
            "for parity audit."
        )

    transform_mismatches = 0
    probability_mismatches = 0

    max_transform_delta = 0.0
    max_probability_delta = 0.0

    worst_transform_signature = None
    worst_probability_signature = None

    for row in rows:
        production = signal.score(
            mayhem_mode=
                row["mayhem_mode"],

            buy_rate_15s=
                row["buy_rate_15s"],
        )

        production_vector = [
            production[
                "transformed_features"
            ][column]

            for column
            in signal.feature_columns
        ]

        reference_vector = (
            research_transform(
                signal,
                mayhem_mode=
                    row["mayhem_mode"],

                buy_rate_15s=
                    row["buy_rate_15s"],
            )
        )

        transform_delta = max(
            abs(
                production_value
                - reference_value
            )

            for (
                production_value,
                reference_value,
            )

            in zip(
                production_vector,
                reference_vector,
                strict=True,
            )
        )

        if (
            transform_delta
            > max_transform_delta
        ):
            max_transform_delta = (
                transform_delta
            )

            worst_transform_signature = (
                row[
                    "entry_signature"
                ]
            )

        if (
            transform_delta
            > ABS_TOLERANCE
        ):
            transform_mismatches += 1

        reference_probability = (
            research_probability(
                signal,
                reference_vector,
            )
        )

        probability_delta = abs(
            production[
                "probability"
            ]
            - reference_probability
        )

        if (
            probability_delta
            > max_probability_delta
        ):
            max_probability_delta = (
                probability_delta
            )

            worst_probability_signature = (
                row[
                    "entry_signature"
                ]
            )

        if (
            probability_delta
            > ABS_TOLERANCE
        ):
            probability_mismatches += 1

    print()
    print("=" * 76)

    print(
        "DELVE MEME AGENT — "
        "INFERENCE PARITY AUDIT"
    )

    print("=" * 76)

    print(
        f"Artifact:                 "
        f"{signal.artifact_sha256}"
    )

    print(
        f"Development rows tested:  "
        f"{len(rows)}"
    )

    print(
        f"Tolerance:                "
        f"{ABS_TOLERANCE:.1e}"
    )

    print()

    print("TRANSFORM PARITY")
    print("-" * 76)

    print(
        f"Mismatches:               "
        f"{transform_mismatches}"
    )

    print(
        f"Maximum delta:            "
        f"{max_transform_delta:.12g}"
    )

    print(
        f"Worst signature:          "
        f"{worst_transform_signature}"
    )

    print()

    print("PROBABILITY PARITY")
    print("-" * 76)

    print(
        f"Mismatches:               "
        f"{probability_mismatches}"
    )

    print(
        f"Maximum delta:            "
        f"{max_probability_delta:.12g}"
    )

    print(
        f"Worst signature:          "
        f"{worst_probability_signature}"
    )

    print()

    passed = (
        transform_mismatches == 0
        and probability_mismatches == 0
    )

    print(
        "FINAL RESULT: "
        + (
            "PASS"
            if passed
            else "FAIL"
        )
    )

    print(
        "VALIDATION HOLDOUT READ: NO"
    )

    print(
        "MODEL REFIT: NO"
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )

    print("=" * 76)

    if not passed:
        raise RuntimeError(
            "Production inference does not "
            "match sealed research semantics."
        )


if __name__ == "__main__":
    main()