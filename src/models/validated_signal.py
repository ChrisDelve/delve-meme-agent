from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


DEFAULT_ARTIFACT_PATH = Path(
    "artifacts/models/validated-signal-artifact-v1.json"
)

EXPECTED_ARTIFACT_VERSION = (
    "validated-signal-artifact-v1"
)

EXPECTED_RAW_FEATURES = [
    "mayhem_mode",
    "buy_rate_15s",
]

EXPECTED_FEATURE_COLUMNS = [
    "mayhem_mode__ecdf",
    "mayhem_mode__missing",
    "buy_rate_15s__ecdf",
    "buy_rate_15s__missing",
]


def canonical_json(value):
    return json.dumps(
        value,
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


def finite_or_missing(value):
    if value is None:
        return None

    try:
        numeric = float(
            value
        )
    except (
        TypeError,
        ValueError,
    ) as error:
        raise ValueError(
            f"Feature value {value!r} "
            f"is not numeric."
        ) from error

    if not math.isfinite(
        numeric
    ):
        return None

    return numeric


def apply_weighted_ecdf(
    value,
    ecdf,
):
    """
    Reproduce the frozen development-fitted
    weighted ECDF transformation exactly,
    including the float32 output semantics
    used by the sealed research implementation.

    Output:
        (
            ecdf_rank,
            missing_indicator,
        )
    """

    numeric = finite_or_missing(
        value
    )

    if numeric is None:
        transformed = np.asarray(
            [
                0.5,
                1.0,
            ],
            dtype=np.float32,
        )

        return (
            float(transformed[0]),
            float(transformed[1]),
        )

    if ecdf is None:
        transformed = np.asarray(
            [
                0.5,
                0.0,
            ],
            dtype=np.float32,
        )

        return (
            float(transformed[0]),
            float(transformed[1]),
        )

    reference_values = np.asarray(
        ecdf["values"],
        dtype=np.float64,
    )

    reference_ranks = np.asarray(
        ecdf["ranks"],
        dtype=np.float64,
    )

    if (
        reference_values.ndim != 1
        or reference_ranks.ndim != 1
    ):
        raise RuntimeError(
            "Frozen ECDF arrays must be "
            "one-dimensional."
        )

    if (
        len(reference_values)
        != len(reference_ranks)
    ):
        raise RuntimeError(
            "Frozen ECDF values/ranks "
            "length mismatch."
        )

    if len(reference_values) == 0:
        raise RuntimeError(
            "Frozen ECDF contains no "
            "reference values."
        )

    if len(reference_values) == 1:
        rank = 0.5

    else:
        rank = np.interp(
            numeric,
            reference_values,
            reference_ranks,
            left=reference_ranks[0],
            right=reference_ranks[-1],
        )

    transformed = np.asarray(
        [
            rank,
            0.0,
        ],
        dtype=np.float32,
    )

    return (
        float(transformed[0]),
        float(transformed[1]),
    )


class ValidatedSignal:
    def __init__(
        self,
        artifact_path=DEFAULT_ARTIFACT_PATH,
    ):
        self.artifact_path = Path(
            artifact_path
        )

        self.document = (
            self._load_document()
        )

        self.payload = (
            self.document[
                "payload"
            ]
        )

        self.artifact_sha256 = (
            self.document[
                "artifact_sha256"
            ]
        )

        self._verify_artifact()

        self.raw_features = list(
            self.payload[
                "raw_features"
            ]
        )

        self.feature_columns = list(
            self.payload[
                "feature_columns"
            ]
        )

        self.ecdfs = (
            self.payload[
                "transform"
            ][
                "ecdfs"
            ]
        )

        self.intercept = float(
            self.payload[
                "model"
            ][
                "intercept"
            ][0]
        )

        coefficient_rows = (
            self.payload[
                "model"
            ][
                "coefficients"
            ]
        )

        self.coefficients = [
            float(value)
            for value
            in coefficient_rows[0]
        ]

        self.baseline_probability = float(
            self.payload[
                "development_baseline_probability"
            ]
        )

        self._verify_model_contract()

    def _load_document(self):
        if not self.artifact_path.exists():
            raise FileNotFoundError(
                f"Validated signal artifact "
                f"not found: "
                f"{self.artifact_path}"
            )

        try:
            document = json.loads(
                self.artifact_path.read_text(
                    encoding="utf-8"
                )
            )

        except json.JSONDecodeError as error:
            raise RuntimeError(
                "Validated signal artifact "
                "is not valid JSON."
            ) from error

        if not isinstance(
            document,
            dict,
        ):
            raise RuntimeError(
                "Validated signal artifact "
                "root must be an object."
            )

        if (
            "artifact_sha256"
            not in document
        ):
            raise RuntimeError(
                "Artifact SHA-256 is missing."
            )

        if "payload" not in document:
            raise RuntimeError(
                "Artifact payload is missing."
            )

        return document

    def _verify_artifact(self):
        calculated_hash = (
            sha256_payload(
                self.payload
            )
        )

        if (
            calculated_hash
            != self.artifact_sha256
        ):
            raise RuntimeError(
                "VALIDATED SIGNAL ARTIFACT "
                "HASH MISMATCH. Refusing "
                "inference."
            )

        artifact_version = (
            self.payload.get(
                "artifact_version"
            )
        )

        if (
            artifact_version
            != EXPECTED_ARTIFACT_VERSION
        ):
            raise RuntimeError(
                "Unexpected validated signal "
                f"artifact version: "
                f"{artifact_version!r}"
            )

    def _verify_model_contract(self):
        if (
            self.raw_features
            != EXPECTED_RAW_FEATURES
        ):
            raise RuntimeError(
                "Frozen raw-feature ordering "
                "does not match inference "
                "contract."
            )

        if (
            self.feature_columns
            != EXPECTED_FEATURE_COLUMNS
        ):
            raise RuntimeError(
                "Frozen transformed-column "
                "ordering does not match "
                "inference contract."
            )

        if set(
            self.ecdfs.keys()
        ) != set(
            self.raw_features
        ):
            raise RuntimeError(
                "Frozen ECDF feature set "
                "does not match raw features."
            )

        if len(
            self.coefficients
        ) != len(
            self.feature_columns
        ):
            raise RuntimeError(
                "Frozen coefficient count "
                "does not match transformed "
                "feature count."
            )

        if not math.isfinite(
            self.intercept
        ):
            raise RuntimeError(
                "Frozen intercept is not finite."
            )

        if not all(
            math.isfinite(value)
            for value
            in self.coefficients
        ):
            raise RuntimeError(
                "Frozen coefficients contain "
                "non-finite values."
            )

        model_kind = (
            self.payload[
                "model"
            ].get(
                "kind"
            )
        )

        if (
            model_kind
            != "logistic_regression"
        ):
            raise RuntimeError(
                "Unexpected frozen model kind."
            )

        transform_kind = (
            self.payload[
                "transform"
            ].get(
                "kind"
            )
        )

        if (
            transform_kind
            !=
            "development_fitted_weighted_ecdf"
        ):
            raise RuntimeError(
                "Unexpected frozen transform "
                "kind."
            )

    def transform(
        self,
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

        transformed = []
        transformed_named = {}

        for feature in self.raw_features:
            (
                rank,
                missing,
            ) = apply_weighted_ecdf(
                raw[feature],
                self.ecdfs[
                    feature
                ],
            )

            rank_name = (
                f"{feature}__ecdf"
            )

            missing_name = (
                f"{feature}__missing"
            )

            transformed.extend(
                [
                    rank,
                    missing,
                ]
            )

            transformed_named[
                rank_name
            ] = rank

            transformed_named[
                missing_name
            ] = missing

        if list(
            transformed_named.keys()
        ) != self.feature_columns:
            raise RuntimeError(
                "Runtime transformed feature "
                "ordering diverged from frozen "
                "model contract."
            )

        return {
            "vector":
                transformed,
            "named":
                transformed_named,
        }

    def predict_logit(
        self,
        *,
        mayhem_mode,
        buy_rate_15s,
    ):
        transformed = self.transform(
            mayhem_mode=mayhem_mode,
            buy_rate_15s=buy_rate_15s,
        )

        values = transformed[
            "vector"
        ]

        logit = self.intercept

        for (
            coefficient,
            value,
        ) in zip(
            self.coefficients,
            values,
            strict=True,
        ):
            logit += (
                coefficient
                * value
            )

        return float(
            logit
        )

    def predict_probability(
        self,
        *,
        mayhem_mode,
        buy_rate_15s,
    ):
        logit = self.predict_logit(
            mayhem_mode=mayhem_mode,
            buy_rate_15s=buy_rate_15s,
        )

        return sigmoid(
            logit
        )

    def score(
        self,
        *,
        mayhem_mode,
        buy_rate_15s,
    ):
        transformed = self.transform(
            mayhem_mode=mayhem_mode,
            buy_rate_15s=buy_rate_15s,
        )

        logit = self.intercept

        for (
            coefficient,
            value,
        ) in zip(
            self.coefficients,
            transformed["vector"],
            strict=True,
        ):
            logit += (
                coefficient
                * value
            )

        probability = sigmoid(
            logit
        )

        return {
            "artifact_version":
                self.payload[
                    "artifact_version"
                ],

            "artifact_sha256":
                self.artifact_sha256,

            "target":
                self.payload[
                    "target"
                ],

            "raw_features": {
                "mayhem_mode":
                    mayhem_mode,
                "buy_rate_15s":
                    buy_rate_15s,
            },

            "transformed_features":
                transformed[
                    "named"
                ],

            "logit":
                float(logit),

            "probability":
                float(probability),

            "development_baseline_probability":
                self.baseline_probability,
        }

    def verification_summary(self):
        return {
            "artifact_version":
                self.payload[
                    "artifact_version"
                ],

            "artifact_sha256":
                self.artifact_sha256,

            "spec_version":
                self.payload[
                    "spec_version"
                ],

            "validation_version":
                self.payload[
                    "validation_version"
                ],

            "raw_features":
                self.raw_features,

            "feature_columns":
                self.feature_columns,

            "coefficient_count":
                len(
                    self.coefficients
                ),

            "artifact_verified":
                True,

            "live_capital_authorized":
                False,
        }


def print_verification(
    signal,
):
    summary = (
        signal.verification_summary()
    )

    print()
    print("=" * 76)
    print(
        "DELVE MEME AGENT — "
        "VALIDATED SIGNAL INFERENCE"
    )
    print("=" * 76)

    print(
        f"Artifact version:       "
        f"{summary['artifact_version']}"
    )

    print(
        f"Artifact SHA-256:       "
        f"{summary['artifact_sha256']}"
    )

    print(
        f"Spec version:           "
        f"{summary['spec_version']}"
    )

    print(
        f"Validation version:     "
        f"{summary['validation_version']}"
    )

    print()

    print("MODEL CONTRACT")
    print("-" * 76)

    print(
        "Raw features:           "
        + ", ".join(
            summary[
                "raw_features"
            ]
        )
    )

    print(
        f"Transformed columns:    "
        f"{len(summary['feature_columns'])}"
    )

    for column in summary[
        "feature_columns"
    ]:
        print(
            f"  - {column}"
        )

    print()

    print(
        f"Coefficients:           "
        f"{summary['coefficient_count']}"
    )

    print()

    print(
        "ARTIFACT HASH VERIFIED: YES"
    )

    print(
        "RESEARCH / LABEL ACCESS: NONE"
    )

    print(
        "MODEL FITTING: NONE"
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )

    print("=" * 76)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Validated Delve Meme Agent "
            "signal inference."
        )
    )

    parser.add_argument(
        "--artifact",
        default=str(
            DEFAULT_ARTIFACT_PATH
        ),
    )

    parser.add_argument(
        "--verify",
        action="store_true",
    )

    parser.add_argument(
        "--mayhem-mode",
        type=float,
    )

    parser.add_argument(
        "--buy-rate-15s",
        type=float,
    )

    args = parser.parse_args()

    signal = ValidatedSignal(
        args.artifact
    )

    if args.verify:
        print_verification(
            signal
        )

        return

    if (
        args.mayhem_mode is None
        and args.buy_rate_15s is None
    ):
        print_verification(
            signal
        )

        return

    result = signal.score(
        mayhem_mode=args.mayhem_mode,
        buy_rate_15s=args.buy_rate_15s,
    )

    print(
        json.dumps(
            result,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()