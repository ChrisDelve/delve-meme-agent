from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
)

from src.execution.live_authority_gate import (
    LiveAuthorityGate,
)
from src.execution.live_execution_composition import (
    run_production_live_buy_once,
    run_production_live_sell_once,
)
from src.execution.live_operating_config import (
    LiveOperatingConfig,
)
from src.execution.live_recovery_coordinator import (
    COMPLETE,
    IDLE,
    LIVE_RECOVERY_COORDINATOR_VERSION,
)
from src.execution.live_recovery_heartbeat import (
    run_live_recovery_heartbeat,
)
from src.execution.live_recovery_service import (
    run_live_recovery_service,
)
from src.strategies.live_exit_policy import (
    LiveExitPolicy,
)


HEARTBEAT_MODULE = (
    "src.execution.live_recovery_heartbeat"
)

COMPOSITION_MODULE = (
    "src.execution.live_execution_composition"
)

SERVICE_MODULE = (
    "src.execution.live_recovery_service"
)


class LiveAuthorityGateWiringTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(
        self,
    ):
        self.db_path = Path(
            "/tmp/live-authority-gate-wiring.db"
        )

        self.wallet = (
            "11111111111111111111111111111112"
        )

    def config(
        self,
    ) -> LiveOperatingConfig:
        return LiveOperatingConfig(
            db_path=self.db_path,
            recovery_interval_seconds=5.0,
            operational_kill=True,
            max_trade_equity_bps=500,
            max_total_exposure_bps=2_000,
            max_daily_loss_bps=1_000,
            max_drawdown_bps=2_000,
            max_open_positions=3,
            min_trade_lamports=1_000_000,
            max_size_price_impact_bps=500.0,
        )

    def recovery_result(
        self,
    ):
        return SimpleNamespace(
            coordinator_version=(
                LIVE_RECOVERY_COORDINATOR_VERSION
            ),
            status=IDLE,
            stage=COMPLETE,
            reasons=(),
            selected_side=None,
            selected_state=None,
        )

    def buy_inputs(
        self,
    ) -> dict:
        return {
            "mint": "mint",
            "wallet_pubkey": self.wallet,
            "protected_cash_lamports": 0,
            "live_curve": object(),
            "safety": object(),
            "protocol_fee_bps": 100,
            "creator_fee_bps": 50,
            "buy_slippage_bps": 500,
            "buy_base_network_fee_lamports": 5_000,
            "buy_priority_fee_lamports": 7_000,
            "buy_rent_lamports": 2_000,
            "exit_slippage_bps": 500,
            "exit_base_network_fee_lamports": 5_000,
            "exit_priority_fee_lamports": 7_000,
            "reservation_ttl_seconds": 30.0,
            "max_authorization_age_seconds": 30.0,
            "compute_unit_limit": 250_000,
            "min_context_slot": 90,
        }

    def sell_inputs(
        self,
    ) -> dict:
        return {
            "compute_unit_limit": 250_000,
            "wallet_pubkey": self.wallet,
            "evaluated_at": 2_000,
            "policy": LiveExitPolicy(
                take_profit_return_bps=2_000,
                stop_loss_return_bps=-4_000,
                max_hold_seconds=900,
            ),
            "slippage_bps": 300,
            "base_network_fee_lamports": 5_000,
            "priority_fee_lamports": 7_000,
            "min_context_slot": 650,
        }

    async def test_heartbeat_waits_for_shared_gate(
        self,
    ):
        gate = LiveAuthorityGate()

        coordinator = AsyncMock(
            return_value=self.recovery_result()
        )

        sleep = AsyncMock(
            side_effect=asyncio.CancelledError
        )

        yield_once = asyncio.sleep

        with (
            patch(
                f"{HEARTBEAT_MODULE}."
                "recover_one_live_obligation_once",
                new=coordinator,
            ),
            patch(
                f"{HEARTBEAT_MODULE}."
                "asyncio.sleep",
                new=sleep,
            ),
        ):
            async with gate:
                task = asyncio.create_task(
                    run_live_recovery_heartbeat(
                        allow_submission=False,
                        authority_gate=gate,
                        db_path=self.db_path,
                        interval_seconds=5.0,
                    )
                )

                await yield_once(
                    0
                )

                coordinator.assert_not_awaited()

            with self.assertRaises(
                asyncio.CancelledError
            ):
                await task

        coordinator.assert_awaited_once()

    async def test_heartbeat_releases_gate_before_sleep(
        self,
    ):
        gate = LiveAuthorityGate()

        coordinator = AsyncMock(
            return_value=self.recovery_result()
        )

        sleep_acquired_gate = (
            asyncio.Event()
        )

        async def sleep(
            _delay,
        ):
            async with gate:
                sleep_acquired_gate.set()

            raise asyncio.CancelledError

        with (
            patch(
                f"{HEARTBEAT_MODULE}."
                "recover_one_live_obligation_once",
                new=coordinator,
            ),
            patch(
                f"{HEARTBEAT_MODULE}."
                "asyncio.sleep",
                new=sleep,
            ),
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await run_live_recovery_heartbeat(
                    allow_submission=False,
                    authority_gate=gate,
                    db_path=self.db_path,
                    interval_seconds=5.0,
                )

        self.assertTrue(
            sleep_acquired_gate.is_set()
        )

    async def test_buy_runtime_waits_for_shared_gate(
        self,
    ):
        gate = LiveAuthorityGate()

        runtime_result = object()

        runtime = AsyncMock(
            return_value=runtime_result
        )

        signer = Mock()

        with (
            patch(
                f"{COMPOSITION_MODULE}."
                "LazyEnvironmentMessageSigner",
                return_value=signer,
            ),
            patch(
                f"{COMPOSITION_MODULE}."
                "run_live_buy_once",
                new=runtime,
            ),
        ):
            async with gate:
                task = asyncio.create_task(
                    run_production_live_buy_once(
                        config=self.config(),
                        authority_gate=gate,
                        **self.buy_inputs(),
                    )
                )

                await asyncio.sleep(
                    0
                )

                runtime.assert_not_awaited()

            result = await task

        self.assertIs(
            result,
            runtime_result,
        )

        runtime.assert_awaited_once()

    async def test_sell_runtime_waits_for_shared_gate(
        self,
    ):
        gate = LiveAuthorityGate()

        runtime_result = object()

        runtime = AsyncMock(
            return_value=runtime_result
        )

        signer = Mock()

        with (
            patch(
                f"{COMPOSITION_MODULE}."
                "LazyEnvironmentMessageSigner",
                return_value=signer,
            ),
            patch(
                f"{COMPOSITION_MODULE}."
                "run_live_sell_once",
                new=runtime,
            ),
        ):
            async with gate:
                task = asyncio.create_task(
                    run_production_live_sell_once(
                        config=self.config(),
                        authority_gate=gate,
                        **self.sell_inputs(),
                    )
                )

                await asyncio.sleep(
                    0
                )

                runtime.assert_not_awaited()

            result = await task

        self.assertIs(
            result,
            runtime_result,
        )

        runtime.assert_awaited_once()

    async def test_invalid_gate_fails_before_authority(
        self,
    ):
        buy_runtime = AsyncMock()
        signer_constructor = Mock()

        with (
            patch(
                f"{COMPOSITION_MODULE}."
                "LazyEnvironmentMessageSigner",
                new=signer_constructor,
            ),
            patch(
                f"{COMPOSITION_MODULE}."
                "run_live_buy_once",
                new=buy_runtime,
            ),
        ):
            with self.assertRaisesRegex(
                TypeError,
                (
                    "^authority_gate must be "
                    "LiveAuthorityGate$"
                ),
            ):
                await run_production_live_buy_once(
                    config=self.config(),
                    authority_gate=object(),
                    **self.buy_inputs(),
                )

        signer_constructor.assert_not_called()
        buy_runtime.assert_not_awaited()

    async def test_invalid_heartbeat_gate_fails_before_recovery(
        self,
    ):
        coordinator = AsyncMock()

        with patch(
            f"{HEARTBEAT_MODULE}."
            "recover_one_live_obligation_once",
            new=coordinator,
        ):
            with self.assertRaisesRegex(
                TypeError,
                (
                    "^authority_gate must be "
                    "LiveAuthorityGate$"
                ),
            ):
                await run_live_recovery_heartbeat(
                    allow_submission=False,
                    authority_gate=object(),
                    db_path=self.db_path,
                    interval_seconds=5.0,
                )

        coordinator.assert_not_awaited()

    async def test_invalid_service_gate_fails_before_heartbeat(
        self,
    ):
        heartbeat = AsyncMock()

        with patch(
            f"{SERVICE_MODULE}."
            "run_live_recovery_heartbeat",
            new=heartbeat,
        ):
            with self.assertRaisesRegex(
                TypeError,
                (
                    "^authority_gate must be "
                    "LiveAuthorityGate$"
                ),
            ):
                await run_live_recovery_service(
                    config=self.config(),
                    authority_gate=object(),
                )

        heartbeat.assert_not_awaited()

    async def test_invalid_sell_gate_fails_before_authority(
        self,
    ):
        sell_runtime = AsyncMock()
        signer_constructor = Mock()

        with (
            patch(
                f"{COMPOSITION_MODULE}."
                "LazyEnvironmentMessageSigner",
                new=signer_constructor,
            ),
            patch(
                f"{COMPOSITION_MODULE}."
                "run_live_sell_once",
                new=sell_runtime,
            ),
        ):
            with self.assertRaisesRegex(
                TypeError,
                (
                    "^authority_gate must be "
                    "LiveAuthorityGate$"
                ),
            ):
                await run_production_live_sell_once(
                    config=self.config(),
                    authority_gate=object(),
                    **self.sell_inputs(),
                )

        signer_constructor.assert_not_called()
        sell_runtime.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
