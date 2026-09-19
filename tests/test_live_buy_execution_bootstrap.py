from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import (
    Mock,
    patch,
)

import src.execution.live_buy_execution_bootstrap as bootstrap_module

from src.execution.live_buy_execution_bootstrap import (
    BUY_EXECUTION_BUY_BASE_NETWORK_FEE_LAMPORTS_ENV,
    BUY_EXECUTION_BUY_PRIORITY_FEE_LAMPORTS_ENV,
    BUY_EXECUTION_BUY_RENT_LAMPORTS_ENV,
    BUY_EXECUTION_BUY_SLIPPAGE_BPS_ENV,
    BUY_EXECUTION_COMPUTE_UNIT_LIMIT_ENV,
    BUY_EXECUTION_EXIT_BASE_NETWORK_FEE_LAMPORTS_ENV,
    BUY_EXECUTION_EXIT_PRIORITY_FEE_LAMPORTS_ENV,
    BUY_EXECUTION_EXIT_SLIPPAGE_BPS_ENV,
    BUY_EXECUTION_MAX_AUTHORIZATION_AGE_SECONDS_ENV,
    BUY_EXECUTION_PROTECTED_CASH_LAMPORTS_ENV,
    BUY_EXECUTION_RESERVATION_TTL_SECONDS_ENV,
    BUY_EXECUTION_WALLET_PUBKEY_ENV,
    LIVE_BUY_EXECUTION_BOOTSTRAP_VERSION,
    LiveBuyExecutionBootstrapError,
    bootstrap_live_buy_execution_config,
)


MODULE = (
    "src.execution.live_buy_execution_bootstrap"
)

SOLANA_KEYPAIR_ENV = (
    "DELVE_SOLANA_KEYPAIR_BASE58"
)

WALLET = (
    "So11111111111111111111111111111111111111112"
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


class LiveBuyExecutionBootstrapTests(
    unittest.TestCase
):
    @staticmethod
    def environment(
        **overrides,
    ):
        values = {
            BUY_EXECUTION_WALLET_PUBKEY_ENV:
                WALLET,
            BUY_EXECUTION_PROTECTED_CASH_LAMPORTS_ENV:
                "50000000",
            BUY_EXECUTION_BUY_SLIPPAGE_BPS_ENV:
                "300",
            BUY_EXECUTION_BUY_BASE_NETWORK_FEE_LAMPORTS_ENV:
                "5000",
            BUY_EXECUTION_BUY_PRIORITY_FEE_LAMPORTS_ENV:
                "10000",
            BUY_EXECUTION_BUY_RENT_LAMPORTS_ENV:
                "2039280",
            BUY_EXECUTION_EXIT_SLIPPAGE_BPS_ENV:
                "300",
            BUY_EXECUTION_EXIT_BASE_NETWORK_FEE_LAMPORTS_ENV:
                "5000",
            BUY_EXECUTION_EXIT_PRIORITY_FEE_LAMPORTS_ENV:
                "10000",
            BUY_EXECUTION_RESERVATION_TTL_SECONDS_ENV:
                "30.0",
            BUY_EXECUTION_MAX_AUTHORIZATION_AGE_SECONDS_ENV:
                "10.0",
            BUY_EXECUTION_COMPUTE_UNIT_LIMIT_ENV:
                "300000",
        }

        values.update(
            overrides
        )

        return values

    @staticmethod
    def env_names():
        return (
            BUY_EXECUTION_WALLET_PUBKEY_ENV,
            BUY_EXECUTION_PROTECTED_CASH_LAMPORTS_ENV,
            BUY_EXECUTION_BUY_SLIPPAGE_BPS_ENV,
            BUY_EXECUTION_BUY_BASE_NETWORK_FEE_LAMPORTS_ENV,
            BUY_EXECUTION_BUY_PRIORITY_FEE_LAMPORTS_ENV,
            BUY_EXECUTION_BUY_RENT_LAMPORTS_ENV,
            BUY_EXECUTION_EXIT_SLIPPAGE_BPS_ENV,
            BUY_EXECUTION_EXIT_BASE_NETWORK_FEE_LAMPORTS_ENV,
            BUY_EXECUTION_EXIT_PRIORITY_FEE_LAMPORTS_ENV,
            BUY_EXECUTION_RESERVATION_TTL_SECONDS_ENV,
            BUY_EXECUTION_MAX_AUTHORIZATION_AGE_SECONDS_ENV,
            BUY_EXECUTION_COMPUTE_UNIT_LIMIT_ENV,
        )

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_BUY_EXECUTION_BOOTSTRAP_VERSION,
            "live-buy-execution-bootstrap-v1",
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
                bootstrap_live_buy_execution_config()
            )

        dotenv_loader.assert_called_once_with(
            dotenv_path=Path(".env"),
            override=False,
        )

        self.assertEqual(
            config.wallet_pubkey,
            WALLET,
        )
        self.assertEqual(
            config.protected_cash_lamports,
            50_000_000,
        )
        self.assertEqual(
            config.buy_slippage_bps,
            300,
        )
        self.assertEqual(
            config.buy_base_network_fee_lamports,
            5_000,
        )
        self.assertEqual(
            config.buy_priority_fee_lamports,
            10_000,
        )
        self.assertEqual(
            config.buy_rent_lamports,
            2_039_280,
        )
        self.assertEqual(
            config.exit_slippage_bps,
            300,
        )
        self.assertEqual(
            config.exit_base_network_fee_lamports,
            5_000,
        )
        self.assertEqual(
            config.exit_priority_fee_lamports,
            10_000,
        )
        self.assertEqual(
            config.reservation_ttl_seconds,
            30.0,
        )
        self.assertEqual(
            config.max_authorization_age_seconds,
            10.0,
        )
        self.assertEqual(
            config.compute_unit_limit,
            300_000,
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

                with (
                    patch.dict(
                        os.environ,
                        values,
                        clear=True,
                    ),
                    patch(
                        f"{MODULE}.load_dotenv",
                    ),
                ):
                    with self.assertRaisesRegex(
                        LiveBuyExecutionBootstrapError,
                        (
                            "^BUY_EXECUTION_ENV_MISSING:"
                            + name
                            + "$"
                        ),
                    ):
                        bootstrap_live_buy_execution_config()

    def test_blank_or_whitespace_env_fails_closed(
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

                    with (
                        patch.dict(
                            os.environ,
                            values,
                            clear=True,
                        ),
                        patch(
                            f"{MODULE}.load_dotenv",
                        ),
                    ):
                        with self.assertRaisesRegex(
                            LiveBuyExecutionBootstrapError,
                            (
                                "^BUY_EXECUTION_ENV_INVALID:"
                                + name
                                + "$"
                            ),
                        ):
                            bootstrap_live_buy_execution_config()

    def test_invalid_integer_text_fails_closed(
        self,
    ):
        values = self.environment(
            **{
                BUY_EXECUTION_PROTECTED_CASH_LAMPORTS_ENV:
                    "1.5",
            }
        )

        with (
            patch.dict(
                os.environ,
                values,
                clear=True,
            ),
            patch(
                f"{MODULE}.load_dotenv",
            ),
        ):
            with self.assertRaisesRegex(
                LiveBuyExecutionBootstrapError,
                (
                    "^BUY_EXECUTION_ENV_INVALID:"
                    + BUY_EXECUTION_PROTECTED_CASH_LAMPORTS_ENV
                    + "$"
                ),
            ):
                bootstrap_live_buy_execution_config()

    def test_invalid_float_text_fails_closed(
        self,
    ):
        values = self.environment(
            **{
                BUY_EXECUTION_RESERVATION_TTL_SECONDS_ENV:
                    "not-a-number",
            }
        )

        with (
            patch.dict(
                os.environ,
                values,
                clear=True,
            ),
            patch(
                f"{MODULE}.load_dotenv",
            ),
        ):
            with self.assertRaisesRegex(
                LiveBuyExecutionBootstrapError,
                (
                    "^BUY_EXECUTION_ENV_INVALID:"
                    + BUY_EXECUTION_RESERVATION_TTL_SECONDS_ENV
                    + "$"
                ),
            ):
                bootstrap_live_buy_execution_config()

    def test_constructor_validation_failure_is_sanitized(
        self,
    ):
        values = self.environment(
            **{
                BUY_EXECUTION_BUY_SLIPPAGE_BPS_ENV:
                    "10001",
            }
        )

        with (
            patch.dict(
                os.environ,
                values,
                clear=True,
            ),
            patch(
                f"{MODULE}.load_dotenv",
            ),
        ):
            with self.assertRaisesRegex(
                LiveBuyExecutionBootstrapError,
                (
                    "^BUY_EXECUTION_BOOTSTRAP_"
                    "CONFIG_INVALID$"
                ),
            ):
                bootstrap_live_buy_execution_config()

    def test_component_mismatch_fails_before_dotenv_or_env_reads(
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
                "LIVE_BUY_EXECUTION_CONFIG_VERSION",
                "unexpected-version",
            ),
        ):
            with self.assertRaisesRegex(
                LiveBuyExecutionBootstrapError,
                (
                    "^BUY_EXECUTION_BOOTSTRAP_"
                    "COMPONENT_VERSION_MISMATCH$"
                ),
            ):
                bootstrap_live_buy_execution_config()

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
            bootstrap_live_buy_execution_config()

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
                bootstrap_live_buy_execution_config(
                    dotenv_path=None
                )
            )

        dotenv_loader.assert_not_called()

        self.assertEqual(
            config.wallet_pubkey,
            WALLET,
        )


if __name__ == "__main__":
    unittest.main()
