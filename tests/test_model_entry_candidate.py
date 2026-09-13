from __future__ import annotations

from dataclasses import FrozenInstanceError
import math
import unittest

from src.execution.model_entry_candidate import (
    EXPECTED_ARTIFACT_VERSION,
    EXPECTED_MODEL_SHADOW_VERSION,
    MODEL_ENTRY_CANDIDATE_VERSION,
    ModelEntryCandidate,
    make_model_entry_candidate,
)


class ModelEntryCandidateTests(
    unittest.TestCase
):
    @staticmethod
    def kwargs(
    ):
        return {
            "entry_signature": "signature-1",
            "mint": "mint-1",
            "event_user": "wallet-1",
            "quote_mint": "quote-1",
            "slot": 123,
            "trade_timestamp": 1_700_000_000,
            "observed_at": 1_700_000_001,
            "predicted_at": 1_700_000_002,
            "model_shadow_version": (
                EXPECTED_MODEL_SHADOW_VERSION
            ),
            "artifact_version": (
                EXPECTED_ARTIFACT_VERSION
            ),
            "artifact_sha256": "a" * 64,
            "model_eligible": True,
            "probability_2x_15m": 0.42,
            "signal_virtual_quote_reserves": (
                30_000_000_000
            ),
            "signal_virtual_token_reserves": (
                1_000_000_000_000
            ),
        }

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            MODEL_ENTRY_CANDIDATE_VERSION,
            "model-entry-candidate-v1",
        )

    def test_factory_binds_exact_candidate_evidence(
        self,
    ):
        kwargs = self.kwargs()

        candidate = (
            make_model_entry_candidate(
                **kwargs
            )
        )

        self.assertEqual(
            candidate.candidate_version,
            MODEL_ENTRY_CANDIDATE_VERSION,
        )

        for name, expected in (
            kwargs.items()
        ):
            self.assertEqual(
                getattr(
                    candidate,
                    name,
                ),
                expected,
            )

    def test_candidate_is_frozen(
        self,
    ):
        candidate = (
            make_model_entry_candidate(
                **self.kwargs()
            )
        )

        with self.assertRaises(
            FrozenInstanceError
        ):
            candidate.mint = "changed"

    def test_blank_identity_fields_are_rejected(
        self,
    ):
        for field in (
            "entry_signature",
            "mint",
            "event_user",
            "quote_mint",
        ):
            with self.subTest(
                field=field
            ):
                kwargs = self.kwargs()
                kwargs[field] = ""

                with self.assertRaises(
                    ValueError
                ):
                    make_model_entry_candidate(
                        **kwargs
                    )

    def test_invalid_slot_and_timestamps_are_rejected(
        self,
    ):
        for field in (
            "slot",
            "trade_timestamp",
            "observed_at",
            "predicted_at",
        ):
            for invalid in (
                -1,
                True,
                1.5,
            ):
                with self.subTest(
                    field=field,
                    invalid=invalid,
                ):
                    kwargs = self.kwargs()
                    kwargs[field] = invalid

                    with self.assertRaises(
                        ValueError
                    ):
                        make_model_entry_candidate(
                            **kwargs
                        )

    def test_none_slot_is_allowed(
        self,
    ):
        kwargs = self.kwargs()
        kwargs["slot"] = None

        candidate = (
            make_model_entry_candidate(
                **kwargs
            )
        )

        self.assertIsNone(
            candidate.slot
        )

    def test_component_versions_must_match(
        self,
    ):
        for field in (
            "model_shadow_version",
            "artifact_version",
        ):
            with self.subTest(
                field=field
            ):
                kwargs = self.kwargs()
                kwargs[field] = (
                    "unexpected-version"
                )

                with self.assertRaises(
                    ValueError
                ):
                    make_model_entry_candidate(
                        **kwargs
                    )

    def test_artifact_sha256_must_be_exact_hex_digest(
        self,
    ):
        for invalid in (
            "",
            "a" * 63,
            "a" * 65,
            "z" * 64,
        ):
            with self.subTest(
                invalid=invalid
            ):
                kwargs = self.kwargs()
                kwargs[
                    "artifact_sha256"
                ] = invalid

                with self.assertRaises(
                    ValueError
                ):
                    make_model_entry_candidate(
                        **kwargs
                    )

    def test_model_eligibility_is_explicit_and_required(
        self,
    ):
        for invalid in (
            False,
            1,
            None,
        ):
            with self.subTest(
                invalid=invalid
            ):
                kwargs = self.kwargs()
                kwargs[
                    "model_eligible"
                ] = invalid

                with self.assertRaises(
                    ValueError
                ):
                    make_model_entry_candidate(
                        **kwargs
                    )

    def test_probability_must_be_finite_unit_interval(
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
                kwargs = self.kwargs()
                kwargs[
                    "probability_2x_15m"
                ] = invalid

                with self.assertRaises(
                    ValueError
                ):
                    make_model_entry_candidate(
                        **kwargs
                    )

    def test_probability_is_normalized_to_float(
        self,
    ):
        kwargs = self.kwargs()
        kwargs[
            "probability_2x_15m"
        ] = 1

        candidate = (
            make_model_entry_candidate(
                **kwargs
            )
        )

        self.assertEqual(
            candidate.probability_2x_15m,
            1.0,
        )

        self.assertIsInstance(
            candidate.probability_2x_15m,
            float,
        )

    def test_signal_virtual_reserves_must_be_positive_integers(
        self,
    ):
        for field in (
            "signal_virtual_quote_reserves",
            "signal_virtual_token_reserves",
        ):
            for invalid in (
                0,
                -1,
                True,
                1.5,
            ):
                with self.subTest(
                    field=field,
                    invalid=invalid,
                ):
                    kwargs = self.kwargs()
                    kwargs[field] = invalid

                    with self.assertRaises(
                        ValueError
                    ):
                        make_model_entry_candidate(
                            **kwargs
                        )

    def test_direct_wrong_candidate_version_is_rejected(
        self,
    ):
        kwargs = self.kwargs()

        with self.assertRaisesRegex(
            ValueError,
            "^candidate_version is invalid$",
        ):
            ModelEntryCandidate(
                candidate_version=(
                    "unexpected-version"
                ),
                **kwargs,
            )


if __name__ == "__main__":
    unittest.main()
