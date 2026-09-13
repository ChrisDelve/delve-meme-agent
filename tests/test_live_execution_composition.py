from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import (
    AsyncMock,
    Mock,
    PropertyMock,
    patch,
)

from src.execution.live_execution_composition import (
    LIVE_EXECUTION_COMPOSITION_VERSION,
    run_production_live_buy_once,
    run_production_live_sell_once,
)
from src.execution.live_operating_config import (
    LiveOperatingConfig,
)
from src.strategies.live_exit_policy import (
    LiveExitPolicy,
)


MODULE = (
    "src.execution.live_execution_composition"
)


class LiveExecutionCompositionTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(
        self,
    ):
        self.db_path = Path(
            "/tmp/live-execution-composition.db"
        )

        self.wallet = (
            "11111111111111111111111111111112"
        )

        self.live_curve = object()
        self.safety = object()

    def config(
        self,
        *,
        operational_kill=False,
    ) -> LiveOperatingConfig:
        return LiveOperatingConfig(
            db_path=self.db_path,
            recovery_interval_seconds=1.0,
            operational_kill=operational_kill,
            max_trade_equity_bps=500,
            max_total_exposure_bps=2_000,
            max_daily_loss_bps=1_000,
            max_drawdown_bps=2_000,
            max_open_positions=3,
            min_trade_lamports=1_000_000,
            max_size_price_impact_bps=500.0,
        )

    def buy_inputs(
        self,
    ) -> dict:
        return {
            "mint": "mint",
            "wallet_pubkey": self.wallet,
            "protected_cash_lamports": 123_000_000,
            "live_curve": self.live_curve,
            "safety": self.safety,
            "signal_virtual_quote_reserves":
                50_000_000_000,
            "signal_virtual_token_reserves":
                100_000_000_000,
            "protocol_fee_bps": 100,
            "creator_fee_bps": 50,
            "buy_slippage_bps": 500,
            "buy_base_network_fee_lamports":
                5_000,
            "buy_priority_fee_lamports":
                7_000,
            "buy_rent_lamports": 2_000,
            "exit_slippage_bps": 500,
            "exit_base_network_fee_lamports":
                5_000,
            "exit_priority_fee_lamports":
                7_000,
            "reservation_ttl_seconds": 30.0,
            "max_authorization_age_seconds":
                30.0,
            "compute_unit_limit": 250_000,
            "min_context_slot": 90,
        }

    def sell_inputs(
        self,
        *,
        policy,
    ) -> dict:
        return {
            "compute_unit_limit": 250_000,
            "wallet_pubkey": self.wallet,
            "evaluated_at": 2_000,
            "policy": policy,
            "slippage_bps": 300,
            "base_network_fee_lamports":
                5_000,
            "priority_fee_lamports": 7_000,
            "min_context_slot": 650,
        }

    def inert_signer(
        self,
    ) -> Mock:
        signer = Mock()

        signer.pubkey = Mock()
        signer.sign_message = Mock()

        return signer

    def test_public_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_EXECUTION_COMPOSITION_VERSION,
            "live-execution-composition-v1",
        )

    async def test_invalid_buy_config_stops_before_signer_and_runtime(
        self,
    ):
        signer_constructor = Mock()
        runtime = AsyncMock()

        with (
            patch(
                f"{MODULE}."
                "LazyEnvironmentMessageSigner",
                new=signer_constructor,
            ),
            patch(
                f"{MODULE}.run_live_buy_once",
                new=runtime,
            ),
        ):
            with self.assertRaises(
                TypeError
            ):
                await run_production_live_buy_once(
                    config=object(),
                    **self.buy_inputs(),
                )

        signer_constructor.assert_not_called()
        runtime.assert_not_awaited()

    async def test_invalid_sell_config_stops_before_signer_and_runtime(
        self,
    ):
        signer_constructor = Mock()
        runtime = AsyncMock()

        exit_policy = LiveExitPolicy(
            take_profit_return_bps=2_000,
            stop_loss_return_bps=-4_000,
            max_hold_seconds=900,
        )

        with (
            patch(
                f"{MODULE}."
                "LazyEnvironmentMessageSigner",
                new=signer_constructor,
            ),
            patch(
                f"{MODULE}.run_live_sell_once",
                new=runtime,
            ),
        ):
            with self.assertRaises(
                TypeError
            ):
                await run_production_live_sell_once(
                    config=object(),
                    **self.sell_inputs(
                        policy=exit_policy,
                    ),
                )

        signer_constructor.assert_not_called()
        runtime.assert_not_awaited()

    async def test_buy_forwards_exact_runtime_inputs_and_config_authority(
        self,
    ):
        config = self.config(
            operational_kill=True,
        )

        signer = self.inert_signer()

        signer_constructor = Mock(
            return_value=signer
        )

        runtime_result = object()

        runtime = AsyncMock(
            return_value=runtime_result
        )

        expected_policy = object()

        inputs = self.buy_inputs()

        with (
            patch.object(
                LiveOperatingConfig,
                "risk_policy",
                new_callable=PropertyMock,
            ) as risk_policy,
            patch(
                f"{MODULE}."
                "LazyEnvironmentMessageSigner",
                new=signer_constructor,
            ),
            patch(
                f"{MODULE}.run_live_buy_once",
                new=runtime,
            ),
        ):
            risk_policy.return_value = (
                expected_policy
            )

            result = (
                await run_production_live_buy_once(
                    config=config,
                    **inputs,
                )
            )

        self.assertIs(
            result,
            runtime_result,
        )

        signer_constructor.assert_called_once_with()

        risk_policy.assert_called_once_with()

        runtime.assert_awaited_once_with(
            kill_switch=True,
            mint=inputs["mint"],
            wallet_pubkey=inputs[
                "wallet_pubkey"
            ],
            protected_cash_lamports=inputs[
                "protected_cash_lamports"
            ],
            live_curve=inputs["live_curve"],
            safety=inputs["safety"],
            signal_virtual_quote_reserves=inputs[
                "signal_virtual_quote_reserves"
            ],
            signal_virtual_token_reserves=inputs[
                "signal_virtual_token_reserves"
            ],
            protocol_fee_bps=inputs[
                "protocol_fee_bps"
            ],
            creator_fee_bps=inputs[
                "creator_fee_bps"
            ],
            buy_slippage_bps=inputs[
                "buy_slippage_bps"
            ],
            buy_base_network_fee_lamports=inputs[
                "buy_base_network_fee_lamports"
            ],
            buy_priority_fee_lamports=inputs[
                "buy_priority_fee_lamports"
            ],
            buy_rent_lamports=inputs[
                "buy_rent_lamports"
            ],
            exit_slippage_bps=inputs[
                "exit_slippage_bps"
            ],
            exit_base_network_fee_lamports=inputs[
                "exit_base_network_fee_lamports"
            ],
            exit_priority_fee_lamports=inputs[
                "exit_priority_fee_lamports"
            ],
            reservation_ttl_seconds=inputs[
                "reservation_ttl_seconds"
            ],
            max_authorization_age_seconds=inputs[
                "max_authorization_age_seconds"
            ],
            compute_unit_limit=inputs[
                "compute_unit_limit"
            ],
            signer=signer,
            policy=expected_policy,
            min_context_slot=inputs[
                "min_context_slot"
            ],
            db_path=config.db_path,
        )

        signer.pubkey.assert_not_called()
        signer.sign_message.assert_not_called()

    async def test_sell_preserves_exit_policy_and_never_reads_risk_policy(
        self,
    ):
        config = self.config(
            operational_kill=True,
        )

        exit_policy = LiveExitPolicy(
            take_profit_return_bps=2_000,
            stop_loss_return_bps=-4_000,
            max_hold_seconds=900,
        )

        signer = self.inert_signer()

        signer_constructor = Mock(
            return_value=signer
        )

        runtime_result = object()

        runtime = AsyncMock(
            return_value=runtime_result
        )

        inputs = self.sell_inputs(
            policy=exit_policy,
        )

        with (
            patch.object(
                LiveOperatingConfig,
                "risk_policy",
                new_callable=PropertyMock,
            ) as risk_policy,
            patch(
                f"{MODULE}."
                "LazyEnvironmentMessageSigner",
                new=signer_constructor,
            ),
            patch(
                f"{MODULE}.run_live_sell_once",
                new=runtime,
            ),
        ):
            risk_policy.side_effect = (
                AssertionError(
                    "SELL composition must not read "
                    "config.risk_policy"
                )
            )

            result = (
                await run_production_live_sell_once(
                    config=config,
                    **inputs,
                )
            )

        self.assertIs(
            result,
            runtime_result,
        )

        risk_policy.assert_not_called()

        signer_constructor.assert_called_once_with()

        runtime.assert_awaited_once_with(
            kill_switch=True,
            signer=signer,
            compute_unit_limit=inputs[
                "compute_unit_limit"
            ],
            wallet_pubkey=inputs[
                "wallet_pubkey"
            ],
            evaluated_at=inputs[
                "evaluated_at"
            ],
            policy=exit_policy,
            slippage_bps=inputs[
                "slippage_bps"
            ],
            base_network_fee_lamports=inputs[
                "base_network_fee_lamports"
            ],
            priority_fee_lamports=inputs[
                "priority_fee_lamports"
            ],
            min_context_slot=inputs[
                "min_context_slot"
            ],
            db_path=config.db_path,
        )

        signer.pubkey.assert_not_called()
        signer.sign_message.assert_not_called()

    async def test_runtime_exception_propagates_without_retry(
        self,
    ):
        config = self.config()

        signer = self.inert_signer()

        signer_constructor = Mock(
            return_value=signer
        )

        runtime = AsyncMock(
            side_effect=RuntimeError(
                "runtime failure"
            )
        )

        with (
            patch(
                f"{MODULE}."
                "LazyEnvironmentMessageSigner",
                new=signer_constructor,
            ),
            patch(
                f"{MODULE}.run_live_buy_once",
                new=runtime,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "runtime failure",
            ):
                await run_production_live_buy_once(
                    config=config,
                    **self.buy_inputs(),
                )

        signer_constructor.assert_called_once_with()

        self.assertEqual(
            runtime.await_count,
            1,
        )

        signer.pubkey.assert_not_called()
        signer.sign_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
