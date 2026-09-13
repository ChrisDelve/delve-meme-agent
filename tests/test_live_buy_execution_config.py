from __future__ import annotations

from dataclasses import FrozenInstanceError
import math
import unittest

from src.execution.live_buy_execution_config import (
    BPS_DENOMINATOR,
    LIVE_BUY_EXECUTION_CONFIG_VERSION,
    MAX_COMPUTE_UNIT_LIMIT,
    SQLITE_INT_MAX,
    LiveBuyExecutionConfig,
)


WALLET = (
    "So11111111111111111111111111111111111111112"
)


class LiveBuyExecutionConfigTests(
    unittest.TestCase
):
    @staticmethod
    def kwargs():
        return {
            "config_version": (
                LIVE_BUY_EXECUTION_CONFIG_VERSION
            ),
            "wallet_pubkey": WALLET,
            "protected_cash_lamports": (
                50_000_000
            ),
            "buy_slippage_bps": 300,
            "buy_base_network_fee_lamports": (
                5_000
            ),
            "buy_priority_fee_lamports": (
                10_000
            ),
            "buy_rent_lamports": (
                2_039_280
            ),
            "exit_slippage_bps": 300,
            "exit_base_network_fee_lamports": (
                5_000
            ),
            "exit_priority_fee_lamports": (
                10_000
            ),
            "reservation_ttl_seconds": 30.0,
            "max_authorization_age_seconds": (
                10.0
            ),
            "compute_unit_limit": 300_000,
        }

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_BUY_EXECUTION_CONFIG_VERSION,
            "live-buy-execution-config-v1",
        )

    def test_valid_config_binds_exact_policy(
        self,
    ):
        kwargs = self.kwargs()

        config = LiveBuyExecutionConfig(
            **kwargs
        )

        for name, expected in (
            kwargs.items()
        ):
            self.assertEqual(
                getattr(
                    config,
                    name,
                ),
                expected,
            )

        self.assertIsInstance(
            config.reservation_ttl_seconds,
            float,
        )

        self.assertIsInstance(
            config.max_authorization_age_seconds,
            float,
        )

    def test_config_is_frozen(
        self,
    ):
        config = LiveBuyExecutionConfig(
            **self.kwargs()
        )

        with self.assertRaises(
            FrozenInstanceError
        ):
            config.compute_unit_limit = 1

    def test_wrong_version_is_rejected(
        self,
    ):
        kwargs = self.kwargs()
        kwargs["config_version"] = (
            "unexpected-version"
        )

        with self.assertRaisesRegex(
            ValueError,
            "^config_version is invalid$",
        ):
            LiveBuyExecutionConfig(
                **kwargs
            )

    def test_invalid_wallet_is_rejected(
        self,
    ):
        for invalid in (
            "",
            " bad-wallet",
            "bad-wallet",
            "11111111111111111111111111111111",
            None,
        ):
            with self.subTest(
                invalid=invalid
            ):
                kwargs = self.kwargs()
                kwargs["wallet_pubkey"] = invalid

                with self.assertRaisesRegex(
                    ValueError,
                    "^wallet_pubkey is invalid$",
                ):
                    LiveBuyExecutionConfig(
                        **kwargs
                    )

    def test_bps_boundaries_are_allowed(
        self,
    ):
        for field in (
            "buy_slippage_bps",
            "exit_slippage_bps",
        ):
            for value in (
                0,
                BPS_DENOMINATOR,
            ):
                with self.subTest(
                    field=field,
                    value=value,
                ):
                    kwargs = self.kwargs()
                    kwargs[field] = value

                    config = (
                        LiveBuyExecutionConfig(
                            **kwargs
                        )
                    )

                    self.assertEqual(
                        getattr(
                            config,
                            field,
                        ),
                        value,
                    )

    def test_invalid_bps_are_rejected(
        self,
    ):
        for field in (
            "buy_slippage_bps",
            "exit_slippage_bps",
        ):
            for invalid in (
                -1,
                BPS_DENOMINATOR + 1,
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
                        LiveBuyExecutionConfig(
                            **kwargs
                        )

    def test_nonnegative_lamport_fields_accept_boundaries(
        self,
    ):
        fields = (
            "protected_cash_lamports",
            "buy_base_network_fee_lamports",
            "buy_priority_fee_lamports",
            "buy_rent_lamports",
            "exit_base_network_fee_lamports",
            "exit_priority_fee_lamports",
        )

        for field in fields:
            for value in (
                0,
                SQLITE_INT_MAX,
            ):
                with self.subTest(
                    field=field,
                    value=value,
                ):
                    kwargs = self.kwargs()
                    kwargs[field] = value

                    config = (
                        LiveBuyExecutionConfig(
                            **kwargs
                        )
                    )

                    self.assertEqual(
                        getattr(
                            config,
                            field,
                        ),
                        value,
                    )

    def test_invalid_lamport_fields_are_rejected(
        self,
    ):
        fields = (
            "protected_cash_lamports",
            "buy_base_network_fee_lamports",
            "buy_priority_fee_lamports",
            "buy_rent_lamports",
            "exit_base_network_fee_lamports",
            "exit_priority_fee_lamports",
        )

        for field in fields:
            for invalid in (
                -1,
                SQLITE_INT_MAX + 1,
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
                        LiveBuyExecutionConfig(
                            **kwargs
                        )

    def test_positive_time_fields_are_normalized(
        self,
    ):
        for field in (
            "reservation_ttl_seconds",
            "max_authorization_age_seconds",
        ):
            kwargs = self.kwargs()
            kwargs[field] = 1

            config = (
                LiveBuyExecutionConfig(
                    **kwargs
                )
            )

            self.assertEqual(
                getattr(
                    config,
                    field,
                ),
                1.0,
            )

            self.assertIsInstance(
                getattr(
                    config,
                    field,
                ),
                float,
            )

    def test_invalid_time_fields_are_rejected(
        self,
    ):
        for field in (
            "reservation_ttl_seconds",
            "max_authorization_age_seconds",
        ):
            for invalid in (
                0,
                -1,
                math.inf,
                -math.inf,
                math.nan,
                True,
                None,
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
                        LiveBuyExecutionConfig(
                            **kwargs
                        )

    def test_compute_unit_limit_boundaries_are_allowed(
        self,
    ):
        for value in (
            1,
            MAX_COMPUTE_UNIT_LIMIT,
        ):
            kwargs = self.kwargs()
            kwargs[
                "compute_unit_limit"
            ] = value

            config = (
                LiveBuyExecutionConfig(
                    **kwargs
                )
            )

            self.assertEqual(
                config.compute_unit_limit,
                value,
            )

    def test_invalid_compute_unit_limit_is_rejected(
        self,
    ):
        for invalid in (
            0,
            -1,
            MAX_COMPUTE_UNIT_LIMIT + 1,
            True,
            1.5,
            None,
        ):
            with self.subTest(
                invalid=invalid
            ):
                kwargs = self.kwargs()
                kwargs[
                    "compute_unit_limit"
                ] = invalid

                with self.assertRaises(
                    ValueError
                ):
                    LiveBuyExecutionConfig(
                        **kwargs
                    )


if __name__ == "__main__":
    unittest.main()
