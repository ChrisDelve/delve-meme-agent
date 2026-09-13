from __future__ import annotations

from dataclasses import (
    FrozenInstanceError,
)
import unittest
from unittest.mock import patch

from src.execution.live_entry_evidence_only_config import (
    LIVE_ENTRY_EVIDENCE_ONLY_CONFIG_VERSION,
    LiveEntryEvidenceOnlyConfig,
)
from src.strategies.live_entry_policy import (
    LiveEntryPolicy,
)


MODULE = (
    "src.execution."
    "live_entry_evidence_only_config"
)


class LiveEntryEvidenceOnlyConfigTests(
    unittest.TestCase
):
    def config(
        self,
        **updates,
    ):
        values = {
            "min_probability_2x_15m":
                0.40,
            "max_candidate_age_seconds":
                5,
            "max_concurrency":
                2,
            "max_pending_tasks":
                8,
        }

        values.update(
            updates
        )

        return (
            LiveEntryEvidenceOnlyConfig(
                **values
            )
        )

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_ENTRY_EVIDENCE_ONLY_CONFIG_VERSION,
            (
                "live-entry-evidence-only-"
                "config-v1"
            ),
        )

    def test_valid_config_normalizes_probability(
        self,
    ):
        config = self.config(
            min_probability_2x_15m=1
        )

        self.assertEqual(
            config.min_probability_2x_15m,
            1.0,
        )
        self.assertIsInstance(
            config.min_probability_2x_15m,
            float,
        )

    def test_probability_boundaries_are_valid(
        self,
    ):
        for value in (
            0,
            0.0,
            1,
            1.0,
        ):
            with self.subTest(
                value=value
            ):
                config = self.config(
                    min_probability_2x_15m=value
                )

                self.assertEqual(
                    config.min_probability_2x_15m,
                    float(value),
                )

    def test_probability_rejects_wrong_types(
        self,
    ):
        for value in (
            True,
            False,
            "0.4",
            None,
            [],
        ):
            with self.subTest(
                value=value
            ):
                with self.assertRaisesRegex(
                    TypeError,
                    (
                        "^min_probability_2x_15m "
                        "must be numeric$"
                    ),
                ):
                    self.config(
                        min_probability_2x_15m=(
                            value
                        )
                    )

    def test_probability_rejects_nonfinite_and_out_of_range(
        self,
    ):
        for value in (
            -0.01,
            1.01,
            float("nan"),
            float("inf"),
            float("-inf"),
        ):
            with self.subTest(
                value=value
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    (
                        "^min_probability_2x_15m "
                        "must be between 0 and 1$"
                    ),
                ):
                    self.config(
                        min_probability_2x_15m=(
                            value
                        )
                    )

    def test_integer_fields_reject_wrong_types(
        self,
    ):
        fields = (
            "max_candidate_age_seconds",
            "max_concurrency",
            "max_pending_tasks",
        )

        for field in fields:
            for value in (
                True,
                1.0,
                "1",
                None,
            ):
                with self.subTest(
                    field=field,
                    value=value,
                ):
                    with self.assertRaisesRegex(
                        TypeError,
                        (
                            "^"
                            + field
                            + " must be int$"
                        ),
                    ):
                        self.config(
                            **{
                                field: value
                            }
                        )

    def test_integer_fields_must_be_positive(
        self,
    ):
        fields = (
            "max_candidate_age_seconds",
            "max_concurrency",
            "max_pending_tasks",
        )

        for field in fields:
            for value in (
                0,
                -1,
            ):
                with self.subTest(
                    field=field,
                    value=value,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        (
                            "^"
                            + field
                            + " must be positive$"
                        ),
                    ):
                        self.config(
                            **{
                                field: value
                            }
                        )

    def test_pending_must_cover_concurrency(
        self,
    ):
        with self.assertRaisesRegex(
            ValueError,
            (
                "^max_pending_tasks must be "
                ">= max_concurrency$"
            ),
        ):
            self.config(
                max_concurrency=3,
                max_pending_tasks=2,
            )

    def test_pending_equal_concurrency_is_valid(
        self,
    ):
        config = self.config(
            max_concurrency=3,
            max_pending_tasks=3,
        )

        self.assertEqual(
            config.max_concurrency,
            3,
        )
        self.assertEqual(
            config.max_pending_tasks,
            3,
        )

    def test_policy_maps_exact_config_values(
        self,
    ):
        config = self.config(
            min_probability_2x_15m=0.55,
            max_candidate_age_seconds=7,
        )

        policy = config.policy

        self.assertIsInstance(
            policy,
            LiveEntryPolicy,
        )
        self.assertEqual(
            policy.min_probability_2x_15m,
            0.55,
        )
        self.assertEqual(
            policy.max_candidate_age_seconds,
            7,
        )

    def test_policy_is_fresh_value_object(
        self,
    ):
        config = self.config()

        first = config.policy
        second = config.policy

        self.assertIsNot(
            first,
            second,
        )
        self.assertEqual(
            first,
            second,
        )

    def test_config_is_frozen(
        self,
    ):
        config = self.config()

        with self.assertRaises(
            FrozenInstanceError
        ):
            config.max_concurrency = 99

    def test_policy_version_mismatch_fails_closed(
        self,
    ):
        with patch(
            f"{MODULE}."
            "LIVE_ENTRY_POLICY_VERSION",
            "wrong-version",
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                (
                    "^LIVE_ENTRY_EVIDENCE_ONLY_"
                    "CONFIG_POLICY_VERSION_MISMATCH$"
                ),
            ):
                self.config()


if __name__ == "__main__":
    unittest.main()
