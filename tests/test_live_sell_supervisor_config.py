from __future__ import annotations

from dataclasses import FrozenInstanceError
import math
import unittest

from src.execution.live_sell_supervisor_config import (
    LIVE_SELL_SUPERVISOR_CONFIG_VERSION,
    U64_MAX,
    LiveSellSupervisorConfig,
)
from src.strategies.live_exit_policy import (
    LIVE_EXIT_POLICY_VERSION,
    LiveExitPolicy,
)


class LiveSellSupervisorConfigTests(
    unittest.TestCase
):
    @staticmethod
    def config(
        **updates,
    ) -> LiveSellSupervisorConfig:
        values = {
            "config_version": (
                LIVE_SELL_SUPERVISOR_CONFIG_VERSION
            ),
            "take_profit_return_bps": 2_000,
            "stop_loss_return_bps": -4_000,
            "max_hold_seconds": 900,
            "evaluation_interval_seconds": 2.0,
        }

        values.update(
            updates
        )

        return LiveSellSupervisorConfig(
            **values
        )

    def test_version_is_locked_and_policy_is_canonical(
        self,
    ):
        config = self.config()

        self.assertEqual(
            LIVE_SELL_SUPERVISOR_CONFIG_VERSION,
            "live-sell-supervisor-config-v1",
        )

        self.assertEqual(
            LIVE_EXIT_POLICY_VERSION,
            "live-exit-policy-v1",
        )

        self.assertEqual(
            config.policy,
            LiveExitPolicy(
                take_profit_return_bps=2_000,
                stop_loss_return_bps=-4_000,
                max_hold_seconds=900,
            ),
        )

    def test_interval_is_normalized_to_float(
        self,
    ):
        config = self.config(
            evaluation_interval_seconds=2,
        )

        self.assertEqual(
            config.evaluation_interval_seconds,
            2.0,
        )

        self.assertIsInstance(
            config.evaluation_interval_seconds,
            float,
        )

    def test_take_profit_requires_positive_int(
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
                invalid=invalid,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "^take_profit_return_bps is invalid$",
                ):
                    self.config(
                        take_profit_return_bps=invalid,
                    )

    def test_stop_loss_requires_strict_loss_range(
        self,
    ):
        for invalid in (
            0,
            1,
            -10_000,
            -10_001,
            True,
            1.5,
            None,
        ):
            with self.subTest(
                invalid=invalid,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "^stop_loss_return_bps is invalid$",
                ):
                    self.config(
                        stop_loss_return_bps=invalid,
                    )

    def test_max_hold_is_optional_positive_u64(
        self,
    ):
        disabled = self.config(
            max_hold_seconds=None,
        )

        self.assertIsNone(
            disabled.max_hold_seconds
        )

        upper = self.config(
            max_hold_seconds=U64_MAX,
        )

        self.assertEqual(
            upper.max_hold_seconds,
            U64_MAX,
        )

        for invalid in (
            0,
            -1,
            U64_MAX + 1,
            True,
            1.5,
        ):
            with self.subTest(
                invalid=invalid,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "^max_hold_seconds is invalid$",
                ):
                    self.config(
                        max_hold_seconds=invalid,
                    )

    def test_interval_requires_positive_finite_number(
        self,
    ):
        for invalid in (
            0,
            -1,
            True,
            None,
            float("nan"),
            float("inf"),
            -float("inf"),
        ):
            with self.subTest(
                invalid=invalid,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "^evaluation_interval_seconds is invalid$",
                ):
                    self.config(
                        evaluation_interval_seconds=invalid,
                    )

        self.assertTrue(
            math.isfinite(
                self.config(
                    evaluation_interval_seconds=0.25,
                ).evaluation_interval_seconds
            )
        )

    def test_config_is_frozen(
        self,
    ):
        config = self.config()

        with self.assertRaises(
            FrozenInstanceError
        ):
            config.evaluation_interval_seconds = 10.0


if __name__ == "__main__":
    unittest.main()
