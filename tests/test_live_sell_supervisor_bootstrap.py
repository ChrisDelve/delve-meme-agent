from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import (
    Mock,
    patch,
)

import src.execution.live_sell_supervisor_bootstrap as bootstrap_module
from src.execution.live_sell_supervisor_bootstrap import (
    LIVE_SELL_SUPERVISOR_BOOTSTRAP_VERSION,
    SELL_SUPERVISOR_EVALUATION_INTERVAL_SECONDS_ENV,
    SELL_SUPERVISOR_MAX_HOLD_SECONDS_ENV,
    SELL_SUPERVISOR_STOP_LOSS_RETURN_BPS_ENV,
    SELL_SUPERVISOR_TAKE_PROFIT_RETURN_BPS_ENV,
    LiveSellSupervisorBootstrapError,
    bootstrap_live_sell_supervisor_config,
)


MODULE = (
    "src.execution.live_sell_supervisor_bootstrap"
)

SOLANA_KEYPAIR_ENV = (
    "DELVE_SOLANA_KEYPAIR_BASE58"
)


class TrackingEnvironment(
    dict
):
    def __init__(
        self,
        *args,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
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


class LiveSellSupervisorBootstrapTests(
    unittest.TestCase
):
    @staticmethod
    def environment(
        **overrides,
    ):
        values = {
            SELL_SUPERVISOR_TAKE_PROFIT_RETURN_BPS_ENV:
                "2000",
            SELL_SUPERVISOR_STOP_LOSS_RETURN_BPS_ENV:
                "-4000",
            SELL_SUPERVISOR_MAX_HOLD_SECONDS_ENV:
                "900",
            SELL_SUPERVISOR_EVALUATION_INTERVAL_SECONDS_ENV:
                "2.0",
        }

        values.update(
            overrides
        )

        return values

    @staticmethod
    def env_names():
        return (
            SELL_SUPERVISOR_TAKE_PROFIT_RETURN_BPS_ENV,
            SELL_SUPERVISOR_STOP_LOSS_RETURN_BPS_ENV,
            SELL_SUPERVISOR_MAX_HOLD_SECONDS_ENV,
            SELL_SUPERVISOR_EVALUATION_INTERVAL_SECONDS_ENV,
        )

    def test_version_and_environment_names_are_locked(
        self,
    ):
        self.assertEqual(
            LIVE_SELL_SUPERVISOR_BOOTSTRAP_VERSION,
            "live-sell-supervisor-bootstrap-v1",
        )

        self.assertEqual(
            SELL_SUPERVISOR_TAKE_PROFIT_RETURN_BPS_ENV,
            (
                "DELVE_LIVE_SELL_SUPERVISOR_"
                "TAKE_PROFIT_RETURN_BPS"
            ),
        )

        self.assertEqual(
            SELL_SUPERVISOR_STOP_LOSS_RETURN_BPS_ENV,
            (
                "DELVE_LIVE_SELL_SUPERVISOR_"
                "STOP_LOSS_RETURN_BPS"
            ),
        )

        self.assertEqual(
            SELL_SUPERVISOR_MAX_HOLD_SECONDS_ENV,
            (
                "DELVE_LIVE_SELL_SUPERVISOR_"
                "MAX_HOLD_SECONDS"
            ),
        )

        self.assertEqual(
            SELL_SUPERVISOR_EVALUATION_INTERVAL_SECONDS_ENV,
            (
                "DELVE_LIVE_SELL_SUPERVISOR_"
                "EVALUATION_INTERVAL_SECONDS"
            ),
        )

    def test_constructs_exact_config(
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
                bootstrap_live_sell_supervisor_config()
            )

        dotenv_loader.assert_called_once_with(
            dotenv_path=Path(".env"),
            override=False,
        )

        self.assertEqual(
            config.take_profit_return_bps,
            2_000,
        )

        self.assertEqual(
            config.stop_loss_return_bps,
            -4_000,
        )

        self.assertEqual(
            config.max_hold_seconds,
            900,
        )

        self.assertEqual(
            config.evaluation_interval_seconds,
            2.0,
        )

    def test_explicit_none_disables_max_hold(
        self,
    ):
        values = self.environment(
            **{
                SELL_SUPERVISOR_MAX_HOLD_SECONDS_ENV:
                    "none",
            }
        )

        with patch.dict(
            os.environ,
            values,
            clear=True,
        ):
            config = (
                bootstrap_live_sell_supervisor_config(
                    dotenv_path=None
                )
            )

        self.assertIsNone(
            config.max_hold_seconds
        )

    def test_missing_required_env_fails_closed(
        self,
    ):
        for name in self.env_names():
            with self.subTest(
                name=name
            ):
                values = self.environment()

                values.pop(
                    name
                )

                with patch.dict(
                    os.environ,
                    values,
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        LiveSellSupervisorBootstrapError,
                        (
                            "^SELL_SUPERVISOR_ENV_MISSING:"
                            + name
                            + "$"
                        ),
                    ):
                        bootstrap_live_sell_supervisor_config(
                            dotenv_path=None
                        )

    def test_blank_or_noncanonical_env_fails_closed(
        self,
    ):
        for name in self.env_names():
            for invalid in (
                "",
                " ",
                " 1",
                "1 ",
            ):
                with self.subTest(
                    name=name,
                    invalid=invalid,
                ):
                    values = self.environment(
                        **{
                            name: invalid,
                        }
                    )

                    with patch.dict(
                        os.environ,
                        values,
                        clear=True,
                    ):
                        with self.assertRaisesRegex(
                            LiveSellSupervisorBootstrapError,
                            (
                                "^SELL_SUPERVISOR_ENV_INVALID:"
                                + name
                                + "$"
                            ),
                        ):
                            bootstrap_live_sell_supervisor_config(
                                dotenv_path=None
                            )

    def test_invalid_integer_and_float_text_fail_closed(
        self,
    ):
        cases = (
            (
                SELL_SUPERVISOR_TAKE_PROFIT_RETURN_BPS_ENV,
                "2.5",
            ),
            (
                SELL_SUPERVISOR_STOP_LOSS_RETURN_BPS_ENV,
                "-4.5",
            ),
            (
                SELL_SUPERVISOR_MAX_HOLD_SECONDS_ENV,
                "900.0",
            ),
            (
                SELL_SUPERVISOR_EVALUATION_INTERVAL_SECONDS_ENV,
                "nan",
            ),
            (
                SELL_SUPERVISOR_EVALUATION_INTERVAL_SECONDS_ENV,
                "inf",
            ),
        )

        for name, invalid in cases:
            with self.subTest(
                name=name,
                invalid=invalid,
            ):
                values = self.environment(
                    **{
                        name: invalid,
                    }
                )

                with patch.dict(
                    os.environ,
                    values,
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        LiveSellSupervisorBootstrapError,
                        (
                            "^SELL_SUPERVISOR_ENV_INVALID:"
                            + name
                            + "$"
                        ),
                    ):
                        bootstrap_live_sell_supervisor_config(
                            dotenv_path=None
                        )

    def test_constructor_validation_failure_is_sanitized(
        self,
    ):
        cases = (
            {
                SELL_SUPERVISOR_TAKE_PROFIT_RETURN_BPS_ENV:
                    "0",
            },
            {
                SELL_SUPERVISOR_STOP_LOSS_RETURN_BPS_ENV:
                    "-10000",
            },
            {
                SELL_SUPERVISOR_MAX_HOLD_SECONDS_ENV:
                    "0",
            },
            {
                SELL_SUPERVISOR_EVALUATION_INTERVAL_SECONDS_ENV:
                    "0.0",
            },
        )

        for overrides in cases:
            with self.subTest(
                overrides=overrides
            ):
                with patch.dict(
                    os.environ,
                    self.environment(
                        **overrides
                    ),
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        LiveSellSupervisorBootstrapError,
                        (
                            "^SELL_SUPERVISOR_BOOTSTRAP_"
                            "CONFIG_INVALID$"
                        ),
                    ):
                        bootstrap_live_sell_supervisor_config(
                            dotenv_path=None
                        )

    def test_component_mismatch_precedes_dotenv_and_env_reads(
        self,
    ):
        environment = TrackingEnvironment(
            self.environment()
        )

        dotenv_loader = Mock()

        with (
            patch.object(
                bootstrap_module.os,
                "environ",
                environment,
            ),
            patch(
                f"{MODULE}.load_dotenv",
                new=dotenv_loader,
            ),
            patch(
                f"{MODULE}."
                "LIVE_SELL_SUPERVISOR_CONFIG_VERSION",
                "unexpected-version",
            ),
        ):
            with self.assertRaisesRegex(
                LiveSellSupervisorBootstrapError,
                (
                    "^SELL_SUPERVISOR_BOOTSTRAP_"
                    "COMPONENT_VERSION_MISMATCH$"
                ),
            ):
                bootstrap_live_sell_supervisor_config()

        dotenv_loader.assert_not_called()

        self.assertEqual(
            environment.read_keys,
            [],
        )

    def test_bootstrap_reads_only_declared_nonsecret_environment(
        self,
    ):
        values = self.environment()

        values[
            SOLANA_KEYPAIR_ENV
        ] = "must-not-be-read"

        values[
            "DELVE_LIVE_DB_PATH"
        ] = "must-not-be-read"

        environment = TrackingEnvironment(
            values
        )

        with (
            patch.object(
                bootstrap_module.os,
                "environ",
                environment,
            ),
            patch(
                f"{MODULE}.load_dotenv",
            ),
        ):
            bootstrap_live_sell_supervisor_config(
                dotenv_path=None
            )

        self.assertEqual(
            set(
                environment.read_keys
            ),
            set(
                self.env_names()
            ),
        )

        self.assertEqual(
            len(
                environment.read_keys
            ),
            len(
                self.env_names()
            ),
        )

        self.assertNotIn(
            SOLANA_KEYPAIR_ENV,
            environment.read_keys,
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
                bootstrap_live_sell_supervisor_config(
                    dotenv_path=None
                )
            )

        dotenv_loader.assert_not_called()

        self.assertEqual(
            config.take_profit_return_bps,
            2_000,
        )


if __name__ == "__main__":
    unittest.main()
