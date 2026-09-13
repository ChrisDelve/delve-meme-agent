import inspect
import unittest
from pathlib import Path

from src.execution.live_operating_config import (
    LIVE_OPERATING_CONFIG_VERSION,
    LiveOperatingConfig,
)
from src.risk.risk_governor import (
    RiskPolicy,
)


class LiveOperatingConfigTests(
    unittest.TestCase
):
    def config(
        self,
        **overrides,
    ):
        values = {
            "db_path": Path(
                "logs/test-live.sqlite3"
            ),
            "recovery_interval_seconds": 5.0,
            "operational_kill": False,
            "max_trade_equity_bps": 100,
            "max_total_exposure_bps": 500,
            "max_daily_loss_bps": 300,
            "max_drawdown_bps": 500,
            "max_open_positions": 5,
            "min_trade_lamports": 1_000_000,
            "max_size_price_impact_bps": 200.0,
        }

        values.update(
            overrides
        )

        return LiveOperatingConfig(
            **values
        )

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_OPERATING_CONFIG_VERSION,
            "live-operating-config-v1",
        )

    def test_normal_mode_derives_recovery_submission_authority(
        self,
    ):
        config = self.config(
            operational_kill=False,
        )

        self.assertTrue(
            config.recovery_allow_submission
        )

    def test_kill_mode_derives_reconciliation_only_recovery(
        self,
    ):
        config = self.config(
            operational_kill=True,
        )

        self.assertFalse(
            config.recovery_allow_submission
        )

    def test_operational_kill_must_be_bool(
        self,
    ):
        for invalid in (
            0,
            1,
            "false",
            None,
        ):
            with self.subTest(
                invalid=invalid
            ):
                with self.assertRaises(
                    TypeError
                ):
                    self.config(
                        operational_kill=invalid
                    )

    def test_recovery_interval_must_be_positive_finite_numeric(
        self,
    ):
        for invalid in (
            0,
            -1,
            float("nan"),
            float("inf"),
        ):
            with self.subTest(
                invalid=invalid
            ):
                with self.assertRaises(
                    ValueError
                ):
                    self.config(
                        recovery_interval_seconds=(
                            invalid
                        )
                    )

        for invalid in (
            True,
            "5",
            None,
        ):
            with self.subTest(
                invalid=invalid
            ):
                with self.assertRaises(
                    TypeError
                ):
                    self.config(
                        recovery_interval_seconds=(
                            invalid
                        )
                    )

    def test_database_path_is_normalized(
        self,
    ):
        config = self.config(
            db_path="logs/live.sqlite3",
        )

        self.assertEqual(
            config.db_path,
            Path(
                "logs/live.sqlite3"
            ),
        )

    def test_blank_database_path_is_rejected(
        self,
    ):
        with self.assertRaises(
            ValueError
        ):
            self.config(
                db_path="   "
            )

    def test_integer_risk_fields_reject_bool_and_non_int(
        self,
    ):
        fields = (
            "max_trade_equity_bps",
            "max_total_exposure_bps",
            "max_daily_loss_bps",
            "max_drawdown_bps",
            "max_open_positions",
            "min_trade_lamports",
        )

        for field in fields:
            for invalid in (
                True,
                1.5,
                "1",
            ):
                with self.subTest(
                    field=field,
                    invalid=invalid,
                ):
                    with self.assertRaises(
                        TypeError
                    ):
                        self.config(
                            **{
                                field: invalid
                            }
                        )

    def test_invalid_policy_ranges_are_rejected(
        self,
    ):
        cases = (
            (
                "max_trade_equity_bps",
                -1,
            ),
            (
                "max_trade_equity_bps",
                10_001,
            ),
            (
                "max_total_exposure_bps",
                10_001,
            ),
            (
                "max_daily_loss_bps",
                10_001,
            ),
            (
                "max_drawdown_bps",
                10_001,
            ),
            (
                "max_open_positions",
                0,
            ),
            (
                "min_trade_lamports",
                0,
            ),
        )

        for field, invalid in cases:
            with self.subTest(
                field=field,
                invalid=invalid,
            ):
                with self.assertRaises(
                    ValueError
                ):
                    self.config(
                        **{
                            field: invalid
                        }
                    )

    def test_price_impact_must_be_nonnegative_finite_numeric(
        self,
    ):
        for invalid in (
            -1.0,
            float("nan"),
            float("inf"),
        ):
            with self.subTest(
                invalid=invalid
            ):
                with self.assertRaises(
                    ValueError
                ):
                    self.config(
                        max_size_price_impact_bps=(
                            invalid
                        )
                    )

        for invalid in (
            True,
            "200",
            None,
        ):
            with self.subTest(
                invalid=invalid
            ):
                with self.assertRaises(
                    TypeError
                ):
                    self.config(
                        max_size_price_impact_bps=(
                            invalid
                        )
                    )

    def test_risk_policy_is_constructed_from_exact_config_values(
        self,
    ):
        config = self.config(
            max_trade_equity_bps=73,
            max_total_exposure_bps=411,
            max_daily_loss_bps=222,
            max_drawdown_bps=333,
            max_open_positions=4,
            min_trade_lamports=2_345_678,
            max_size_price_impact_bps=123.5,
        )

        policy = config.risk_policy

        self.assertIsInstance(
            policy,
            RiskPolicy,
        )

        self.assertEqual(
            policy.max_trade_equity_bps,
            73,
        )
        self.assertEqual(
            policy.max_total_exposure_bps,
            411,
        )
        self.assertEqual(
            policy.max_daily_loss_bps,
            222,
        )
        self.assertEqual(
            policy.max_drawdown_bps,
            333,
        )
        self.assertEqual(
            policy.max_open_positions,
            4,
        )
        self.assertEqual(
            policy.min_trade_lamports,
            2_345_678,
        )
        self.assertEqual(
            policy.max_size_price_impact_bps,
            123.5,
        )

    def test_live_risk_values_have_no_constructor_defaults(
        self,
    ):
        signature = inspect.signature(
            LiveOperatingConfig
        )

        required = (
            "max_trade_equity_bps",
            "max_total_exposure_bps",
            "max_daily_loss_bps",
            "max_drawdown_bps",
            "max_open_positions",
            "min_trade_lamports",
            "max_size_price_impact_bps",
        )

        for name in required:
            with self.subTest(
                name=name
            ):
                self.assertIs(
                    signature.parameters[
                        name
                    ].default,
                    inspect.Parameter.empty,
                )

    def test_config_is_frozen(
        self,
    ):
        config = self.config()

        with self.assertRaises(
            Exception
        ):
            config.operational_kill = True


if __name__ == "__main__":
    unittest.main()
