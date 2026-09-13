from __future__ import annotations

from dataclasses import FrozenInstanceError
import inspect
import math
import unittest

from src.execution.model_entry_candidate import (
    EXPECTED_ARTIFACT_VERSION,
    EXPECTED_MODEL_SHADOW_VERSION,
    make_model_entry_candidate,
)
from src.strategies.live_entry_policy import (
    LIVE_ENTRY_POLICY_VERSION,
    PASS,
    REJECT,
    SOL_QUOTE_MINT,
    UNKNOWN,
    LiveEntryPolicy,
    evaluate_live_entry_candidate,
)


class LiveEntryPolicyTests(
    unittest.TestCase
):
    def setUp(self):
        self.policy = LiveEntryPolicy(
            min_probability_2x_15m=0.40,
            max_candidate_age_seconds=5,
        )

    def candidate(
        self,
        **updates,
    ):
        values = {
            "entry_signature":
                "entry-signature-1",
            "mint": "mint-1",
            "event_user": "event-user-1",
            "quote_mint":
                SOL_QUOTE_MINT,
            "slot": 123,
            "trade_timestamp": 1_000,
            "observed_at": 1_001,
            "predicted_at": 1_002,
            "model_shadow_version": (
                EXPECTED_MODEL_SHADOW_VERSION
            ),
            "artifact_version": (
                EXPECTED_ARTIFACT_VERSION
            ),
            "artifact_sha256":
                "ab" * 32,
            "model_eligible": True,
            "probability_2x_15m": 0.42,
            "signal_virtual_quote_reserves":
                30_000_000_000,
            "signal_virtual_token_reserves":
                1_000_000_000_000,
        }

        values.update(
            updates
        )

        return (
            make_model_entry_candidate(
                **values
            )
        )

    def evaluate(
        self,
        *,
        candidate=None,
        evaluated_at=1_004,
        policy=None,
    ):
        if candidate is None:
            candidate = self.candidate()

        if policy is None:
            policy = self.policy

        return evaluate_live_entry_candidate(
            candidate=candidate,
            evaluated_at=evaluated_at,
            policy=policy,
        )

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_ENTRY_POLICY_VERSION,
            "live-entry-policy-v1",
        )

    def test_policy_requires_explicit_parameters(
        self,
    ):
        signature = inspect.signature(
            LiveEntryPolicy
        )

        for parameter in (
            signature.parameters.values()
        ):
            self.assertIs(
                parameter.default,
                inspect.Parameter.empty,
            )

    def test_policy_is_frozen(
        self,
    ):
        with self.assertRaises(
            FrozenInstanceError
        ):
            self.policy.max_candidate_age_seconds = 10

    def test_valid_candidate_passes(
        self,
    ):
        result = self.evaluate()

        self.assertEqual(
            result.status,
            PASS,
        )
        self.assertEqual(
            result.reasons,
            (),
        )
        self.assertTrue(
            result.should_resolve_evidence
        )
        self.assertEqual(
            result.entry_signature,
            "entry-signature-1",
        )
        self.assertEqual(
            result.mint,
            "mint-1",
        )
        self.assertEqual(
            result.probability_2x_15m,
            0.42,
        )
        self.assertEqual(
            result.candidate_age_seconds,
            2,
        )
        self.assertIs(
            result.policy,
            self.policy,
        )

    def test_decision_is_frozen(
        self,
    ):
        result = self.evaluate()

        with self.assertRaises(
            FrozenInstanceError
        ):
            result.status = REJECT

    def test_exact_probability_boundary_passes(
        self,
    ):
        result = self.evaluate(
            candidate=self.candidate(
                probability_2x_15m=0.40
            )
        )

        self.assertEqual(
            result.status,
            PASS,
        )

    def test_exact_age_boundary_passes(
        self,
    ):
        result = self.evaluate(
            evaluated_at=1_007,
        )

        self.assertEqual(
            result.status,
            PASS,
        )
        self.assertEqual(
            result.candidate_age_seconds,
            5,
        )

    def test_probability_below_threshold_rejects(
        self,
    ):
        result = self.evaluate(
            candidate=self.candidate(
                probability_2x_15m=0.399
            )
        )

        self.assertEqual(
            result.status,
            REJECT,
        )
        self.assertEqual(
            result.reasons,
            (
                "PROBABILITY_BELOW_THRESHOLD",
            ),
        )
        self.assertFalse(
            result.should_resolve_evidence
        )

    def test_stale_candidate_rejects(
        self,
    ):
        result = self.evaluate(
            evaluated_at=1_008,
        )

        self.assertEqual(
            result.status,
            REJECT,
        )
        self.assertEqual(
            result.reasons,
            (
                "CANDIDATE_STALE",
            ),
        )
        self.assertEqual(
            result.candidate_age_seconds,
            6,
        )

    def test_non_sol_quote_rejects(
        self,
    ):
        result = self.evaluate(
            candidate=self.candidate(
                quote_mint="other-quote"
            )
        )

        self.assertEqual(
            result.status,
            REJECT,
        )
        self.assertEqual(
            result.reasons,
            (
                "UNSUPPORTED_QUOTE_MINT",
            ),
        )

    def test_invalid_policy_type_is_unknown(
        self,
    ):
        result = (
            evaluate_live_entry_candidate(
                candidate=self.candidate(),
                evaluated_at=1_004,
                policy=object(),
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.reasons,
            (
                "INVALID_LIVE_ENTRY_POLICY",
            ),
        )
        self.assertIsNone(
            result.policy
        )

    def test_invalid_probability_threshold_is_unknown(
        self,
    ):
        for invalid in (
            -0.01,
            1.01,
            math.inf,
            -math.inf,
            math.nan,
            True,
            None,
        ):
            with self.subTest(
                invalid=invalid
            ):
                policy = LiveEntryPolicy(
                    min_probability_2x_15m=(
                        invalid
                    ),
                    max_candidate_age_seconds=5,
                )

                result = self.evaluate(
                    policy=policy
                )

                self.assertEqual(
                    result.status,
                    UNKNOWN,
                )
                self.assertEqual(
                    result.reasons,
                    (
                        "INVALID_MIN_PROBABILITY_2X_15M",
                    ),
                )

    def test_invalid_max_age_is_unknown(
        self,
    ):
        for invalid in (
            0,
            -1,
            True,
            1.5,
            None,
        ):
            with self.subTest(
                invalid=invalid
            ):
                policy = LiveEntryPolicy(
                    min_probability_2x_15m=0.40,
                    max_candidate_age_seconds=(
                        invalid
                    ),
                )

                result = self.evaluate(
                    policy=policy
                )

                self.assertEqual(
                    result.status,
                    UNKNOWN,
                )
                self.assertEqual(
                    result.reasons,
                    (
                        "INVALID_MAX_CANDIDATE_AGE_SECONDS",
                    ),
                )

    def test_invalid_evaluated_at_is_unknown(
        self,
    ):
        for invalid in (
            0,
            -1,
            True,
            1.5,
            None,
        ):
            with self.subTest(
                invalid=invalid
            ):
                result = self.evaluate(
                    evaluated_at=invalid
                )

                self.assertEqual(
                    result.status,
                    UNKNOWN,
                )
                self.assertEqual(
                    result.reasons,
                    (
                        "INVALID_EVALUATED_AT",
                    ),
                )
                self.assertEqual(
                    result.evaluated_at,
                    0,
                )

    def test_invalid_candidate_type_is_unknown(
        self,
    ):
        result = (
            evaluate_live_entry_candidate(
                candidate=object(),
                evaluated_at=1_004,
                policy=self.policy,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.reasons,
            (
                "INVALID_MODEL_ENTRY_CANDIDATE",
            ),
        )

    def test_tampered_candidate_version_is_unknown(
        self,
    ):
        candidate = self.candidate()

        object.__setattr__(
            candidate,
            "candidate_version",
            "tampered-version",
        )

        result = self.evaluate(
            candidate=candidate
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.reasons,
            (
                "INVALID_MODEL_ENTRY_CANDIDATE",
            ),
        )

    def test_tampered_model_eligibility_is_unknown(
        self,
    ):
        candidate = self.candidate()

        object.__setattr__(
            candidate,
            "model_eligible",
            False,
        )

        result = self.evaluate(
            candidate=candidate
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.reasons,
            (
                "INVALID_MODEL_ELIGIBILITY",
            ),
        )

    def test_tampered_probability_is_unknown(
        self,
    ):
        for invalid in (
            -0.01,
            1.01,
            math.nan,
            True,
        ):
            with self.subTest(
                invalid=invalid
            ):
                candidate = self.candidate()

                object.__setattr__(
                    candidate,
                    "probability_2x_15m",
                    invalid,
                )

                result = self.evaluate(
                    candidate=candidate
                )

                self.assertEqual(
                    result.status,
                    UNKNOWN,
                )
                self.assertEqual(
                    result.reasons,
                    (
                        "INVALID_CANDIDATE_PROBABILITY",
                    ),
                )

    def test_zero_candidate_timestamp_is_unknown(
        self,
    ):
        candidate = self.candidate()

        object.__setattr__(
            candidate,
            "trade_timestamp",
            0,
        )

        result = self.evaluate(
            candidate=candidate
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.reasons,
            (
                "INVALID_CANDIDATE_TIMESTAMPS",
            ),
        )

    def test_trade_after_observation_is_unknown(
        self,
    ):
        result = self.evaluate(
            candidate=self.candidate(
                trade_timestamp=1_002,
                observed_at=1_001,
                predicted_at=1_003,
            ),
            evaluated_at=1_004,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.reasons,
            (
                "TRADE_AFTER_OBSERVATION",
            ),
        )

    def test_observation_after_prediction_is_unknown(
        self,
    ):
        result = self.evaluate(
            candidate=self.candidate(
                observed_at=1_003,
                predicted_at=1_002,
            ),
            evaluated_at=1_004,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.reasons,
            (
                "OBSERVATION_AFTER_PREDICTION",
            ),
        )

    def test_prediction_after_evaluation_is_unknown(
        self,
    ):
        result = self.evaluate(
            candidate=self.candidate(
                predicted_at=1_005,
            ),
            evaluated_at=1_004,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.reasons,
            (
                "PREDICTION_AFTER_EVALUATION",
            ),
        )


if __name__ == "__main__":
    unittest.main()
