from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import (
    Mock,
    patch,
)

from src.execution.live_entry_evidence_only_bootstrap import (
    EVIDENCE_MAX_CANDIDATE_AGE_ENV,
    EVIDENCE_MAX_CONCURRENCY_ENV,
    EVIDENCE_MAX_PENDING_TASKS_ENV,
    EVIDENCE_MIN_PROBABILITY_ENV,
    LIVE_ENTRY_EVIDENCE_ONLY_BOOTSTRAP_VERSION,
    LiveEntryEvidenceOnlyBootstrapError,
    bootstrap_live_entry_evidence_only_config,
)


MODULE = (
    "src.execution."
    "live_entry_evidence_only_bootstrap"
)


class TrackingEnvironment(
    dict
):
    def __init__(
        self,
        values,
    ):
        super().__init__(
            values
        )
        self.read_keys = []

    def get(
        self,
        key,
        default=None,
    ):
        self.read_keys.append(
            key
        )
        return super().get(
            key,
            default,
        )


class LiveEntryEvidenceOnlyBootstrapTests(
    unittest.TestCase
):
    def environment(
        self,
        **updates,
    ):
        values = {
            EVIDENCE_MIN_PROBABILITY_ENV:
                "0.40",
            EVIDENCE_MAX_CANDIDATE_AGE_ENV:
                "5",
            EVIDENCE_MAX_CONCURRENCY_ENV:
                "2",
            EVIDENCE_MAX_PENDING_TASKS_ENV:
                "8",
        }

        values.update(
            updates
        )

        return values

    def test_version_and_environment_names_are_locked(
        self,
    ):
        self.assertEqual(
            LIVE_ENTRY_EVIDENCE_ONLY_BOOTSTRAP_VERSION,
            (
                "live-entry-evidence-only-"
                "bootstrap-v1"
            ),
        )

        self.assertEqual(
            EVIDENCE_MIN_PROBABILITY_ENV,
            (
                "DELVE_LIVE_ENTRY_EVIDENCE_"
                "MIN_PROBABILITY_2X_15M"
            ),
        )
        self.assertEqual(
            EVIDENCE_MAX_CANDIDATE_AGE_ENV,
            (
                "DELVE_LIVE_ENTRY_EVIDENCE_"
                "MAX_CANDIDATE_AGE_SECONDS"
            ),
        )
        self.assertEqual(
            EVIDENCE_MAX_CONCURRENCY_ENV,
            (
                "DELVE_LIVE_ENTRY_EVIDENCE_"
                "MAX_CONCURRENCY"
            ),
        )
        self.assertEqual(
            EVIDENCE_MAX_PENDING_TASKS_ENV,
            (
                "DELVE_LIVE_ENTRY_EVIDENCE_"
                "MAX_PENDING_TASKS"
            ),
        )

    def test_bootstrap_loads_dotenv_without_override(
        self,
    ):
        dotenv_loader = Mock()

        with (
            patch.dict(
                os.environ,
                self.environment(),
                clear=True,
            ),
            patch(
                f"{MODULE}.load_dotenv",
                new=dotenv_loader,
            ),
        ):
            config = (
                bootstrap_live_entry_evidence_only_config()
            )

        dotenv_loader.assert_called_once_with(
            dotenv_path=Path(".env"),
            override=False,
        )

        self.assertEqual(
            config.min_probability_2x_15m,
            0.40,
        )
        self.assertEqual(
            config.max_candidate_age_seconds,
            5,
        )
        self.assertEqual(
            config.max_concurrency,
            2,
        )
        self.assertEqual(
            config.max_pending_tasks,
            8,
        )

    def test_none_dotenv_path_skips_loader(
        self,
    ):
        dotenv_loader = Mock()

        with (
            patch.dict(
                os.environ,
                self.environment(),
                clear=True,
            ),
            patch(
                f"{MODULE}.load_dotenv",
                new=dotenv_loader,
            ),
        ):
            config = (
                bootstrap_live_entry_evidence_only_config(
                    dotenv_path=None
                )
            )

        dotenv_loader.assert_not_called()

        self.assertEqual(
            config.max_concurrency,
            2,
        )

    def test_blank_dotenv_path_rejected_before_loading(
        self,
    ):
        dotenv_loader = Mock()

        with patch(
            f"{MODULE}.load_dotenv",
            new=dotenv_loader,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "^dotenv_path is invalid$",
            ):
                bootstrap_live_entry_evidence_only_config(
                    dotenv_path="   "
                )

        dotenv_loader.assert_not_called()

    def test_missing_required_values_fail_closed(
        self,
    ):
        names = (
            EVIDENCE_MIN_PROBABILITY_ENV,
            EVIDENCE_MAX_CANDIDATE_AGE_ENV,
            EVIDENCE_MAX_CONCURRENCY_ENV,
            EVIDENCE_MAX_PENDING_TASKS_ENV,
        )

        for name in names:
            with self.subTest(
                name=name
            ):
                values = (
                    self.environment()
                )
                values.pop(
                    name
                )

                with patch.dict(
                    os.environ,
                    values,
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        LiveEntryEvidenceOnlyBootstrapError,
                        (
                            "^EVIDENCE_ENV_MISSING:"
                            + name
                            + "$"
                        ),
                    ):
                        bootstrap_live_entry_evidence_only_config(
                            dotenv_path=None
                        )

    def test_blank_required_values_fail_closed(
        self,
    ):
        names = (
            EVIDENCE_MIN_PROBABILITY_ENV,
            EVIDENCE_MAX_CANDIDATE_AGE_ENV,
            EVIDENCE_MAX_CONCURRENCY_ENV,
            EVIDENCE_MAX_PENDING_TASKS_ENV,
        )

        for name in names:
            for value in (
                "",
                "   ",
            ):
                with self.subTest(
                    name=name,
                    value=value,
                ):
                    values = (
                        self.environment(
                            **{
                                name: value
                            }
                        )
                    )

                    with patch.dict(
                        os.environ,
                        values,
                        clear=True,
                    ):
                        with self.assertRaisesRegex(
                            LiveEntryEvidenceOnlyBootstrapError,
                            (
                                "^EVIDENCE_ENV_BLANK:"
                                + name
                                + "$"
                            ),
                        ):
                            bootstrap_live_entry_evidence_only_config(
                                dotenv_path=None
                            )

    def test_invalid_probability_fails_closed(
        self,
    ):
        for invalid in (
            "abc",
            "-0.01",
            "1.01",
            "nan",
            "inf",
            "-inf",
        ):
            with self.subTest(
                invalid=invalid
            ):
                values = self.environment(
                    **{
                        EVIDENCE_MIN_PROBABILITY_ENV:
                            invalid
                    }
                )

                with patch.dict(
                    os.environ,
                    values,
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        LiveEntryEvidenceOnlyBootstrapError,
                        (
                            "^EVIDENCE_ENV_INVALID:"
                            + EVIDENCE_MIN_PROBABILITY_ENV
                            + "$"
                        ),
                    ):
                        bootstrap_live_entry_evidence_only_config(
                            dotenv_path=None
                        )

    def test_probability_boundaries_are_allowed(
        self,
    ):
        for value in (
            "0",
            "0.0",
            "1",
            "1.0",
        ):
            with self.subTest(
                value=value
            ):
                values = self.environment(
                    **{
                        EVIDENCE_MIN_PROBABILITY_ENV:
                            value
                    }
                )

                with patch.dict(
                    os.environ,
                    values,
                    clear=True,
                ):
                    config = (
                        bootstrap_live_entry_evidence_only_config(
                            dotenv_path=None
                        )
                    )

                self.assertEqual(
                    config.min_probability_2x_15m,
                    float(value),
                )

    def test_invalid_integer_text_fails_closed(
        self,
    ):
        cases = (
            (
                EVIDENCE_MAX_CANDIDATE_AGE_ENV,
                "5.0",
            ),
            (
                EVIDENCE_MAX_CANDIDATE_AGE_ENV,
                "abc",
            ),
            (
                EVIDENCE_MAX_CONCURRENCY_ENV,
                "1.5",
            ),
            (
                EVIDENCE_MAX_PENDING_TASKS_ENV,
                "nan",
            ),
        )

        for name, invalid in cases:
            with self.subTest(
                name=name,
                invalid=invalid,
            ):
                values = self.environment(
                    **{
                        name: invalid
                    }
                )

                with patch.dict(
                    os.environ,
                    values,
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        LiveEntryEvidenceOnlyBootstrapError,
                        (
                            "^EVIDENCE_ENV_INVALID:"
                            + name
                            + "$"
                        ),
                    ):
                        bootstrap_live_entry_evidence_only_config(
                            dotenv_path=None
                        )

    def test_nonpositive_integer_values_fail_closed(
        self,
    ):
        names = (
            EVIDENCE_MAX_CANDIDATE_AGE_ENV,
            EVIDENCE_MAX_CONCURRENCY_ENV,
            EVIDENCE_MAX_PENDING_TASKS_ENV,
        )

        for name in names:
            for invalid in (
                "0",
                "-1",
            ):
                with self.subTest(
                    name=name,
                    invalid=invalid,
                ):
                    values = self.environment(
                        **{
                            name: invalid
                        }
                    )

                    with patch.dict(
                        os.environ,
                        values,
                        clear=True,
                    ):
                        with self.assertRaisesRegex(
                            LiveEntryEvidenceOnlyBootstrapError,
                            (
                                "^EVIDENCE_ENV_INVALID:"
                                + name
                                + "$"
                            ),
                        ):
                            bootstrap_live_entry_evidence_only_config(
                                dotenv_path=None
                            )

    def test_pending_below_concurrency_fails_closed(
        self,
    ):
        values = self.environment(
            **{
                EVIDENCE_MAX_CONCURRENCY_ENV:
                    "4",
                EVIDENCE_MAX_PENDING_TASKS_ENV:
                    "3",
            }
        )

        with patch.dict(
            os.environ,
            values,
            clear=True,
        ):
            with self.assertRaisesRegex(
                LiveEntryEvidenceOnlyBootstrapError,
                (
                    "^EVIDENCE_ENV_INVALID:"
                    + EVIDENCE_MAX_PENDING_TASKS_ENV
                    + "$"
                ),
            ):
                bootstrap_live_entry_evidence_only_config(
                    dotenv_path=None
                )

    def test_bootstrap_reads_only_evidence_environment_keys(
        self,
    ):
        values = self.environment()

        values[
            "DELVE_SOLANA_KEYPAIR_BASE58"
        ] = "must-not-be-read"
        values[
            "DELVE_LIVE_DB_PATH"
        ] = "must-not-be-read"
        values[
            "DELVE_LIVE_OPERATIONAL_KILL"
        ] = "must-not-be-read"

        environment = (
            TrackingEnvironment(
                values
            )
        )

        with (
            patch(
                f"{MODULE}.os.environ",
                new=environment,
            ),
            patch(
                f"{MODULE}.load_dotenv",
                new=Mock(),
            ),
        ):
            bootstrap_live_entry_evidence_only_config(
                dotenv_path=None
            )

        self.assertEqual(
            environment.read_keys,
            [
                EVIDENCE_MIN_PROBABILITY_ENV,
                EVIDENCE_MAX_CANDIDATE_AGE_ENV,
                EVIDENCE_MAX_CONCURRENCY_ENV,
                EVIDENCE_MAX_PENDING_TASKS_ENV,
            ],
        )

    def test_component_version_mismatch_fails_before_environment_read(
        self,
    ):
        environment = (
            TrackingEnvironment(
                self.environment()
            )
        )

        dotenv_loader = Mock()

        with (
            patch(
                f"{MODULE}."
                "LIVE_ENTRY_EVIDENCE_ONLY_CONFIG_VERSION",
                "wrong-version",
            ),
            patch(
                f"{MODULE}.os.environ",
                new=environment,
            ),
            patch(
                f"{MODULE}.load_dotenv",
                new=dotenv_loader,
            ),
        ):
            with self.assertRaisesRegex(
                LiveEntryEvidenceOnlyBootstrapError,
                (
                    "^EVIDENCE_BOOTSTRAP_"
                    "COMPONENT_VERSION_MISMATCH$"
                ),
            ):
                bootstrap_live_entry_evidence_only_config()

        self.assertEqual(
            environment.read_keys,
            [],
        )
        dotenv_loader.assert_not_called()

    def test_constructor_failure_is_sanitized(
        self,
    ):
        values = self.environment()

        with (
            patch.dict(
                os.environ,
                values,
                clear=True,
            ),
            patch(
                f"{MODULE}."
                "LiveEntryEvidenceOnlyConfig",
                side_effect=RuntimeError(
                    "internal detail"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                LiveEntryEvidenceOnlyBootstrapError,
                (
                    "^EVIDENCE_BOOTSTRAP_"
                    "CONFIG_INVALID$"
                ),
            ):
                bootstrap_live_entry_evidence_only_config(
                    dotenv_path=None
                )


if __name__ == "__main__":
    unittest.main()
