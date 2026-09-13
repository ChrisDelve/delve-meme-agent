import asyncio
import unittest
from pathlib import Path
from unittest.mock import (
    AsyncMock,
    patch,
)

from src.execution.live_authority_gate import (
    LiveAuthorityGate,
)
from src.execution.live_operating_config import (
    LiveOperatingConfig,
)
from src.execution.live_recovery_service import (
    LIVE_RECOVERY_SERVICE_VERSION,
    run_live_recovery_service,
)


MODULE = (
    "src.execution.live_recovery_service"
)


class LiveRecoveryServiceTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(
        self,
    ):
        self.authority_gate = (
            LiveAuthorityGate()
        )

    def config(
        self,
        *,
        operational_kill: bool,
    ) -> LiveOperatingConfig:
        return LiveOperatingConfig(
            db_path=Path(
                "logs/live-service-test.sqlite3"
            ),
            recovery_interval_seconds=7.5,
            operational_kill=(
                operational_kill
            ),
            max_trade_equity_bps=100,
            max_total_exposure_bps=500,
            max_daily_loss_bps=300,
            max_drawdown_bps=500,
            max_open_positions=5,
            min_trade_lamports=1_000_000,
            max_size_price_impact_bps=200.0,
        )

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_RECOVERY_SERVICE_VERSION,
            "live-recovery-service-v2",
        )

    async def test_invalid_config_fails_before_heartbeat(
        self,
    ):
        heartbeat = AsyncMock()

        with patch(
            f"{MODULE}.run_live_recovery_heartbeat",
            new=heartbeat,
        ):
            with self.assertRaises(
                TypeError
            ):
                await run_live_recovery_service(
                    authority_gate=self.authority_gate,
                    config=None
                )

        heartbeat.assert_not_awaited()

    async def test_normal_mode_forwards_submission_authority(
        self,
    ):
        config = self.config(
            operational_kill=False,
        )

        heartbeat = AsyncMock(
            side_effect=asyncio.CancelledError
        )

        with patch(
            f"{MODULE}.run_live_recovery_heartbeat",
            new=heartbeat,
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await run_live_recovery_service(
                    authority_gate=self.authority_gate,
                    config=config
                )

        heartbeat.assert_awaited_once_with(
            authority_gate=self.authority_gate,
            allow_submission=True,
            db_path=config.db_path,
            interval_seconds=(
                config.recovery_interval_seconds
            ),
        )

    async def test_kill_mode_forwards_reconciliation_only(
        self,
    ):
        config = self.config(
            operational_kill=True,
        )

        heartbeat = AsyncMock(
            side_effect=asyncio.CancelledError
        )

        with patch(
            f"{MODULE}.run_live_recovery_heartbeat",
            new=heartbeat,
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await run_live_recovery_service(
                    authority_gate=self.authority_gate,
                    config=config
                )

        heartbeat.assert_awaited_once_with(
            authority_gate=self.authority_gate,
            allow_submission=False,
            db_path=config.db_path,
            interval_seconds=(
                config.recovery_interval_seconds
            ),
        )

    async def test_heartbeat_exception_propagates(
        self,
    ):
        config = self.config(
            operational_kill=False,
        )

        heartbeat = AsyncMock(
            side_effect=RuntimeError(
                "heartbeat failed"
            )
        )

        with patch(
            f"{MODULE}.run_live_recovery_heartbeat",
            new=heartbeat,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "heartbeat failed",
            ):
                await run_live_recovery_service(
                    authority_gate=self.authority_gate,
                    config=config
                )

        heartbeat.assert_awaited_once()

    async def test_service_does_not_invoke_heartbeat_twice(
        self,
    ):
        config = self.config(
            operational_kill=False,
        )

        heartbeat = AsyncMock(
            side_effect=asyncio.CancelledError
        )

        with patch(
            f"{MODULE}.run_live_recovery_heartbeat",
            new=heartbeat,
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await run_live_recovery_service(
                    authority_gate=self.authority_gate,
                    config=config
                )

        self.assertEqual(
            heartbeat.await_count,
            1,
        )


if __name__ == "__main__":
    unittest.main()
