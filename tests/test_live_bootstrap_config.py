from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import (
    Mock,
    patch,
)

from src.execution.live_bootstrap_config import (
    LIVE_BOOTSTRAP_CONFIG_VERSION,
    LIVE_DB_PATH_ENV,
    LIVE_MAX_DAILY_LOSS_BPS_ENV,
    LIVE_MAX_DRAWDOWN_BPS_ENV,
    LIVE_MAX_OPEN_POSITIONS_ENV,
    LIVE_MAX_SIZE_PRICE_IMPACT_BPS_ENV,
    LIVE_MAX_TOTAL_EXPOSURE_BPS_ENV,
    LIVE_MAX_TRADE_EQUITY_BPS_ENV,
    LIVE_MIN_TRADE_LAMPORTS_ENV,
    LIVE_OPERATIONAL_KILL_ENV,
    LIVE_RECOVERY_INTERVAL_SECONDS_ENV,
    LiveBootstrapConfigurationError,
    bootstrap_live_operating_config,
)


MODULE = (
    "src.execution.live_bootstrap_config"
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


class LiveBootstrapConfigTests(
    unittest.TestCase
):
    def environment(
        self,
        **overrides,
    ):
        values = {
            LIVE_DB_PATH_ENV:
                "logs/delve_live.db",
            LIVE_RECOVERY_INTERVAL_SECONDS_ENV:
                "5.0",
            LIVE_OPERATIONAL_KILL_ENV:
                "true",
            LIVE_MAX_TRADE_EQUITY_BPS_ENV:
                "500",
            LIVE_MAX_TOTAL_EXPOSURE_BPS_ENV:
                "2000",
            LIVE_MAX_DAILY_LOSS_BPS_ENV:
                "1000",
            LIVE_MAX_DRAWDOWN_BPS_ENV:
                "2000",
            LIVE_MAX_OPEN_POSITIONS_ENV:
                "3",
            LIVE_MIN_TRADE_LAMPORTS_ENV:
                "1000000",
            LIVE_MAX_SIZE_PRICE_IMPACT_BPS_ENV:
                "500.0",
        }

        values.update(
            overrides
        )

        return values

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_BOOTSTRAP_CONFIG_VERSION,
            "live-bootstrap-config-v1",
        )

    def test_constructs_exact_operating_config(
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
                bootstrap_live_operating_config()
            )

        dotenv_loader.assert_called_once_with(
            dotenv_path=Path(".env"),
            override=False,
        )

        self.assertEqual(
            config.db_path,
            Path("logs/delve_live.db"),
        )
        self.assertEqual(
            config.recovery_interval_seconds,
            5.0,
        )
        self.assertIs(
            config.operational_kill,
            True,
        )
        self.assertEqual(
            config.max_trade_equity_bps,
            500,
        )
        self.assertEqual(
            config.max_total_exposure_bps,
            2_000,
        )
        self.assertEqual(
            config.max_daily_loss_bps,
            1_000,
        )
        self.assertEqual(
            config.max_drawdown_bps,
            2_000,
        )
        self.assertEqual(
            config.max_open_positions,
            3,
        )
        self.assertEqual(
            config.min_trade_lamports,
            1_000_000,
        )
        self.assertEqual(
            config.max_size_price_impact_bps,
            500.0,
        )

    def test_none_dotenv_path_skips_population(
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
                bootstrap_live_operating_config(
                    dotenv_path=None
                )
            )

        dotenv_loader.assert_not_called()

        self.assertEqual(
            config.db_path,
            Path("logs/delve_live.db"),
        )

    def test_process_environment_wins_over_dotenv(
        self,
    ):
        values = self.environment(
            **{
                LIVE_OPERATIONAL_KILL_ENV:
                    "true",
            }
        )

        with TemporaryDirectory() as directory:
            dotenv_path = (
                Path(directory)
                / ".env"
            )

            dotenv_path.write_text(
                (
                    f"{LIVE_OPERATIONAL_KILL_ENV}"
                    "=false\n"
                ),
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                values,
                clear=True,
            ):
                config = (
                    bootstrap_live_operating_config(
                        dotenv_path=dotenv_path
                    )
                )

                self.assertEqual(
                    os.environ[
                        LIVE_OPERATIONAL_KILL_ENV
                    ],
                    "true",
                )

        self.assertIs(
            config.operational_kill,
            True,
        )

    def test_missing_required_value_fails_closed(
        self,
    ):
        values = self.environment()

        del values[
            LIVE_MAX_OPEN_POSITIONS_ENV
        ]

        with patch.dict(
            os.environ,
            values,
            clear=True,
        ):
            with self.assertRaisesRegex(
                LiveBootstrapConfigurationError,
                (
                    "^LIVE_ENV_MISSING:"
                    f"{LIVE_MAX_OPEN_POSITIONS_ENV}$"
                ),
            ):
                bootstrap_live_operating_config(
                    dotenv_path=None
                )

    def test_whitespace_is_not_silently_normalized(
        self,
    ):
        values = self.environment(
            **{
                LIVE_DB_PATH_ENV:
                    " logs/delve_live.db",
            }
        )

        with patch.dict(
            os.environ,
            values,
            clear=True,
        ):
            with self.assertRaisesRegex(
                LiveBootstrapConfigurationError,
                (
                    "^LIVE_ENV_INVALID:"
                    f"{LIVE_DB_PATH_ENV}$"
                ),
            ):
                bootstrap_live_operating_config(
                    dotenv_path=None
                )

    def test_operational_kill_requires_exact_boolean(
        self,
    ):
        for invalid in (
            "TRUE",
            "FALSE",
            "1",
            "0",
            "yes",
            " true",
            "false ",
        ):
            with self.subTest(
                invalid=invalid
            ):
                values = self.environment(
                    **{
                        LIVE_OPERATIONAL_KILL_ENV:
                            invalid,
                    }
                )

                with patch.dict(
                    os.environ,
                    values,
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        LiveBootstrapConfigurationError,
                        (
                            "^LIVE_ENV_INVALID:"
                            f"{LIVE_OPERATIONAL_KILL_ENV}$"
                        ),
                    ):
                        bootstrap_live_operating_config(
                            dotenv_path=None
                        )

    def test_invalid_numeric_text_fails_closed(
        self,
    ):
        cases = (
            (
                LIVE_MAX_OPEN_POSITIONS_ENV,
                "3.0",
            ),
            (
                LIVE_MIN_TRADE_LAMPORTS_ENV,
                "-1",
            ),
            (
                LIVE_MAX_SIZE_PRICE_IMPACT_BPS_ENV,
                "nan",
            ),
            (
                LIVE_RECOVERY_INTERVAL_SECONDS_ENV,
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
                        LiveBootstrapConfigurationError,
                        (
                            "^LIVE_ENV_INVALID:"
                            f"{name}$"
                        ),
                    ):
                        bootstrap_live_operating_config(
                            dotenv_path=None
                        )

    def test_bootstrap_never_reads_signer_secret(
        self,
    ):
        values = self.environment()
        values[
            SOLANA_KEYPAIR_ENV
        ] = "must-not-be-read"

        environment = TrackingEnvironment(
            values
        )

        dotenv_loader = Mock()

        with (
            patch(
                f"{MODULE}.os.environ",
                new=environment,
            ),
            patch(
                f"{MODULE}.load_dotenv",
                new=dotenv_loader,
            ),
        ):
            config = (
                bootstrap_live_operating_config()
            )

        self.assertNotIn(
            SOLANA_KEYPAIR_ENV,
            environment.read_keys,
        )

        dotenv_loader.assert_called_once_with(
            dotenv_path=Path(".env"),
            override=False,
        )

        self.assertEqual(
            config.db_path,
            Path("logs/delve_live.db"),
        )

    def test_blank_dotenv_path_is_rejected_before_loading(
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
                bootstrap_live_operating_config(
                    dotenv_path="   "
                )

        dotenv_loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
