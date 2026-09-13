from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from src.execution.model_entry_candidate import (
    EXPECTED_ARTIFACT_VERSION,
    EXPECTED_MODEL_SHADOW_VERSION,
    MODEL_ENTRY_CANDIDATE_VERSION,
)
from src.execution.model_entry_candidate_adapter import (
    MODEL_ENTRY_CANDIDATE_ADAPTER_VERSION,
    adapt_model_entry_candidate,
)


class ModelEntryCandidateAdapterTests(
    unittest.TestCase
):
    def setUp(self):
        self.event = {
            "entry_signature":
                "signature-1",
            "mint": "mint-1",
            "event_user": "wallet-1",
            "quote_mint":
                "11111111111111111111111111111111",
            "slot": 123,
            "trade_timestamp": 1_700_000_000,
            "observed_at": 1_700_000_001,
            "signal_virtual_quote_reserves":
                30_000_000_000,
            "signal_virtual_token_reserves":
                1_000_000_000_000,
        }

        self.prediction = {
            "entry_signature":
                self.event["entry_signature"],
            "shadow_version":
                EXPECTED_MODEL_SHADOW_VERSION,
            "artifact_version":
                EXPECTED_ARTIFACT_VERSION,
            "artifact_sha256":
                "ab" * 32,
            "mint":
                self.event["mint"],
            "wallet":
                self.event["event_user"],
            "quote_mint":
                self.event["quote_mint"],
            "slot":
                self.event["slot"],
            "trade_timestamp":
                self.event["trade_timestamp"],
            "observed_at":
                self.event["observed_at"],
            "model_eligible": 1,
            "probability_2x_15m": 0.42,
            "predicted_at":
                1_700_000_002,
        }

    def adapt(
        self,
        *,
        prediction=None,
        event=None,
    ):
        if prediction is None:
            prediction = dict(
                self.prediction
            )

        if event is None:
            event = dict(
                self.event
            )

        return adapt_model_entry_candidate(
            prediction=prediction,
            **event,
        )

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            MODEL_ENTRY_CANDIDATE_ADAPTER_VERSION,
            "model-entry-candidate-adapter-v1",
        )

    def test_exact_prediction_and_event_bind_candidate(
        self,
    ):
        candidate = self.adapt()

        self.assertEqual(
            candidate.candidate_version,
            MODEL_ENTRY_CANDIDATE_VERSION,
        )
        self.assertEqual(
            candidate.entry_signature,
            self.event["entry_signature"],
        )
        self.assertEqual(
            candidate.mint,
            self.event["mint"],
        )
        self.assertEqual(
            candidate.event_user,
            self.event["event_user"],
        )
        self.assertEqual(
            candidate.quote_mint,
            self.event["quote_mint"],
        )
        self.assertEqual(
            candidate.slot,
            self.event["slot"],
        )
        self.assertEqual(
            candidate.trade_timestamp,
            self.event["trade_timestamp"],
        )
        self.assertEqual(
            candidate.observed_at,
            self.event["observed_at"],
        )
        self.assertEqual(
            candidate.predicted_at,
            self.prediction["predicted_at"],
        )
        self.assertEqual(
            candidate.model_shadow_version,
            EXPECTED_MODEL_SHADOW_VERSION,
        )
        self.assertEqual(
            candidate.artifact_version,
            EXPECTED_ARTIFACT_VERSION,
        )
        self.assertEqual(
            candidate.artifact_sha256,
            self.prediction[
                "artifact_sha256"
            ],
        )
        self.assertIs(
            candidate.model_eligible,
            True,
        )
        self.assertEqual(
            candidate.probability_2x_15m,
            0.42,
        )
        self.assertEqual(
            candidate.signal_virtual_quote_reserves,
            self.event[
                "signal_virtual_quote_reserves"
            ],
        )
        self.assertEqual(
            candidate.signal_virtual_token_reserves,
            self.event[
                "signal_virtual_token_reserves"
            ],
        )

    def test_result_is_frozen_candidate(
        self,
    ):
        candidate = self.adapt()

        with self.assertRaises(
            FrozenInstanceError
        ):
            candidate.mint = "changed"

    def test_invalid_prediction_type_is_rejected(
        self,
    ):
        with self.assertRaisesRegex(
            TypeError,
            "^prediction must be a mapping$",
        ):
            adapt_model_entry_candidate(
                prediction=object(),
                **self.event,
            )

    def test_required_prediction_fields_must_exist(
        self,
    ):
        required = (
            "entry_signature",
            "mint",
            "wallet",
            "quote_mint",
            "slot",
            "trade_timestamp",
            "observed_at",
            "shadow_version",
            "model_eligible",
            "artifact_version",
            "artifact_sha256",
            "probability_2x_15m",
            "predicted_at",
        )

        for field in required:
            with self.subTest(
                field=field
            ):
                prediction = dict(
                    self.prediction
                )
                prediction.pop(
                    field
                )

                with self.assertRaisesRegex(
                    ValueError,
                    (
                        "^prediction field "
                        + field
                        + " is missing$"
                    ),
                ):
                    self.adapt(
                        prediction=prediction
                    )

    def test_prediction_identity_must_match_event_exactly(
        self,
    ):
        cases = (
            (
                "entry_signature",
                "entry_signature",
                "different-signature",
            ),
            (
                "mint",
                "mint",
                "different-mint",
            ),
            (
                "wallet",
                "event_user",
                "different-wallet",
            ),
            (
                "quote_mint",
                "quote_mint",
                "different-quote",
            ),
            (
                "slot",
                "slot",
                124,
            ),
            (
                "trade_timestamp",
                "trade_timestamp",
                1_700_000_003,
            ),
            (
                "observed_at",
                "observed_at",
                1_700_000_004,
            ),
        )

        for (
            prediction_field,
            event_field,
            conflicting,
        ) in cases:
            with self.subTest(
                field=prediction_field
            ):
                event = dict(
                    self.event
                )
                event[event_field] = (
                    conflicting
                )

                with self.assertRaisesRegex(
                    ValueError,
                    (
                        "^prediction "
                        + prediction_field
                        + " mismatch$"
                    ),
                ):
                    self.adapt(
                        event=event
                    )

    def test_identity_binding_is_type_strict(
        self,
    ):
        prediction = dict(
            self.prediction
        )
        prediction["slot"] = True

        event = dict(
            self.event
        )
        event["slot"] = 1

        with self.assertRaisesRegex(
            ValueError,
            "^prediction slot mismatch$",
        ):
            self.adapt(
                prediction=prediction,
                event=event,
            )

    def test_shadow_version_must_match_candidate_contract(
        self,
    ):
        prediction = dict(
            self.prediction
        )
        prediction[
            "shadow_version"
        ] = "unexpected-version"

        with self.assertRaisesRegex(
            ValueError,
            "^prediction shadow_version mismatch$",
        ):
            self.adapt(
                prediction=prediction
            )

    def test_sqlite_integer_one_is_exactly_eligible(
        self,
    ):
        candidate = self.adapt()

        self.assertIs(
            candidate.model_eligible,
            True,
        )

    def test_boolean_true_is_exactly_eligible(
        self,
    ):
        prediction = dict(
            self.prediction
        )
        prediction[
            "model_eligible"
        ] = True

        candidate = self.adapt(
            prediction=prediction
        )

        self.assertIs(
            candidate.model_eligible,
            True,
        )

    def test_false_or_zero_prediction_is_not_eligible(
        self,
    ):
        for invalid in (
            False,
            0,
        ):
            with self.subTest(
                invalid=invalid
            ):
                prediction = dict(
                    self.prediction
                )
                prediction[
                    "model_eligible"
                ] = invalid

                with self.assertRaisesRegex(
                    ValueError,
                    (
                        "^prediction is "
                        "not exactly eligible$"
                    ),
                ):
                    self.adapt(
                        prediction=prediction
                    )

    def test_truthy_eligibility_coercions_are_rejected(
        self,
    ):
        for invalid in (
            "1",
            "true",
            1.0,
            [1],
            object(),
        ):
            with self.subTest(
                invalid=invalid
            ):
                prediction = dict(
                    self.prediction
                )
                prediction[
                    "model_eligible"
                ] = invalid

                with self.assertRaisesRegex(
                    ValueError,
                    (
                        "^prediction is "
                        "not exactly eligible$"
                    ),
                ):
                    self.adapt(
                        prediction=prediction
                    )

    def test_invalid_artifact_version_fails_closed(
        self,
    ):
        prediction = dict(
            self.prediction
        )
        prediction[
            "artifact_version"
        ] = "unexpected-version"

        with self.assertRaisesRegex(
            ValueError,
            "^artifact_version is invalid$",
        ):
            self.adapt(
                prediction=prediction
            )

    def test_invalid_artifact_sha256_fails_closed(
        self,
    ):
        prediction = dict(
            self.prediction
        )
        prediction[
            "artifact_sha256"
        ] = "bad"

        with self.assertRaisesRegex(
            ValueError,
            "^artifact_sha256 is invalid$",
        ):
            self.adapt(
                prediction=prediction
            )

    def test_invalid_probability_fails_closed(
        self,
    ):
        prediction = dict(
            self.prediction
        )
        prediction[
            "probability_2x_15m"
        ] = None

        with self.assertRaisesRegex(
            ValueError,
            "^probability_2x_15m is invalid$",
        ):
            self.adapt(
                prediction=prediction
            )

    def test_invalid_predicted_at_fails_closed(
        self,
    ):
        prediction = dict(
            self.prediction
        )
        prediction[
            "predicted_at"
        ] = -1

        with self.assertRaisesRegex(
            ValueError,
            "^predicted_at is invalid$",
        ):
            self.adapt(
                prediction=prediction
            )

    def test_invalid_event_reserves_fail_closed(
        self,
    ):
        for field in (
            "signal_virtual_quote_reserves",
            "signal_virtual_token_reserves",
        ):
            with self.subTest(
                field=field
            ):
                event = dict(
                    self.event
                )
                event[field] = 0

                with self.assertRaisesRegex(
                    ValueError,
                    (
                        "^"
                        + field
                        + " is invalid$"
                    ),
                ):
                    self.adapt(
                        event=event
                    )

    def test_adapter_does_not_mutate_prediction(
        self,
    ):
        prediction = dict(
            self.prediction
        )
        before = dict(
            prediction
        )

        self.adapt(
            prediction=prediction
        )

        self.assertEqual(
            prediction,
            before,
        )


if __name__ == "__main__":
    unittest.main()
