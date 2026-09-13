import asyncio
import math
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    patch,
)

from src.execution.live_recovery_coordinator import (
    ADVANCED,
    COMPLETE,
    IDLE,
    LIVE_RECOVERY_COORDINATOR_VERSION,
)
from src.execution.live_recovery_heartbeat import (
    LIVE_RECOVERY_HEARTBEAT_INTERVAL_SECONDS,
    LIVE_RECOVERY_HEARTBEAT_VERSION,
    run_live_recovery_heartbeat,
)


MODULE = (
    "src.execution.live_recovery_heartbeat"
)


class LiveRecoveryHeartbeatTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(
        self,
    ):
        self.db_path = Path(
            "/tmp/live-recovery-heartbeat-test.sqlite3"
        )

    def result(
        self,
        *,
        status=IDLE,
        stage=COMPLETE,
        reasons=(),
        side=None,
        state=None,
        version=(
            LIVE_RECOVERY_COORDINATOR_VERSION
        ),
    ):
        return SimpleNamespace(
            coordinator_version=version,
            status=status,
            stage=stage,
            reasons=tuple(
                reasons
            ),
            selected_side=side,
            selected_state=state,
        )

    def test_version_and_interval_are_locked(
        self,
    ):
        self.assertEqual(
            LIVE_RECOVERY_HEARTBEAT_VERSION,
            "live-recovery-heartbeat-v1",
        )

        self.assertEqual(
            LIVE_RECOVERY_HEARTBEAT_INTERVAL_SECONDS,
            5.0,
        )

    async def test_submission_authority_must_be_explicit(
        self,
    ):
        coordinator = AsyncMock()

        with patch(
            f"{MODULE}.recover_one_live_obligation_once",
            new=coordinator,
        ):
            with self.assertRaises(
                TypeError
            ):
                await run_live_recovery_heartbeat(
                    db_path=self.db_path,
                )

        coordinator.assert_not_awaited()

    async def test_invalid_allow_submission_fails_before_loop(
        self,
    ):
        coordinator = AsyncMock()

        with patch(
            f"{MODULE}.recover_one_live_obligation_once",
            new=coordinator,
        ):
            with self.assertRaises(
                TypeError
            ):
                await run_live_recovery_heartbeat(
                    allow_submission="yes",
                    db_path=self.db_path,
                )

        coordinator.assert_not_awaited()

    async def test_invalid_interval_fails_before_loop(
        self,
    ):
        coordinator = AsyncMock()

        with patch(
            f"{MODULE}.recover_one_live_obligation_once",
            new=coordinator,
        ):
            for invalid in (
                0,
                -1,
                float("nan"),
                float("inf"),
                True,
            ):
                with self.subTest(
                    invalid=invalid
                ):
                    with self.assertRaises(
                        ValueError
                    ):
                        await run_live_recovery_heartbeat(
                            allow_submission=True,
                            db_path=self.db_path,
                            interval_seconds=invalid,
                        )

        coordinator.assert_not_awaited()

    async def test_one_iteration_calls_coordinator_once(
        self,
    ):
        coordinator = AsyncMock(
            return_value=self.result()
        )

        sleep = AsyncMock(
            side_effect=asyncio.CancelledError
        )

        with (
            patch(
                f"{MODULE}.recover_one_live_obligation_once",
                new=coordinator,
            ),
            patch(
                f"{MODULE}.asyncio.sleep",
                new=sleep,
            ),
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await run_live_recovery_heartbeat(
                    allow_submission=True,
                    db_path=self.db_path,
                    interval_seconds=5.0,
                )

        coordinator.assert_awaited_once_with(
            allow_submission=True,
            db_path=self.db_path,
        )

        self.assertEqual(
            sleep.await_count,
            1,
        )

    async def test_submission_mode_is_forwarded_exactly(
        self,
    ):
        coordinator = AsyncMock(
            return_value=self.result()
        )

        sleep = AsyncMock(
            side_effect=asyncio.CancelledError
        )

        with (
            patch(
                f"{MODULE}.recover_one_live_obligation_once",
                new=coordinator,
            ),
            patch(
                f"{MODULE}.asyncio.sleep",
                new=sleep,
            ),
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await run_live_recovery_heartbeat(
                    allow_submission=False,
                    db_path=self.db_path,
                    interval_seconds=5.0,
                )

        coordinator.assert_awaited_once_with(
            allow_submission=False,
            db_path=self.db_path,
        )

    async def test_coordinator_cancellation_propagates_without_sleep(
        self,
    ):
        coordinator = AsyncMock(
            side_effect=asyncio.CancelledError
        )

        sleep = AsyncMock()

        with (
            patch(
                f"{MODULE}.recover_one_live_obligation_once",
                new=coordinator,
            ),
            patch(
                f"{MODULE}.asyncio.sleep",
                new=sleep,
            ),
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await run_live_recovery_heartbeat(
                    allow_submission=True,
                    db_path=self.db_path,
                )

        coordinator.assert_awaited_once()
        sleep.assert_not_awaited()

    async def test_unexpected_exception_isolated_and_next_tick_runs(
        self,
    ):
        coordinator = AsyncMock(
            side_effect=(
                RuntimeError(
                    "boom"
                ),
                self.result(),
            )
        )

        sleep = AsyncMock(
            side_effect=(
                None,
                asyncio.CancelledError(),
            )
        )

        with (
            patch(
                f"{MODULE}.recover_one_live_obligation_once",
                new=coordinator,
            ),
            patch(
                f"{MODULE}.asyncio.sleep",
                new=sleep,
            ),
            patch(
                "builtins.print"
            ) as output,
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await run_live_recovery_heartbeat(
                    allow_submission=True,
                    db_path=self.db_path,
                    interval_seconds=5.0,
                )

        self.assertEqual(
            coordinator.await_count,
            2,
        )

        output.assert_any_call(
            "⚠️ LIVE RECOVERY HEARTBEAT ERROR | "
            "RuntimeError: boom"
        )

    async def test_idle_tick_is_silent(
        self,
    ):
        coordinator = AsyncMock(
            return_value=self.result(
                status=IDLE,
            )
        )

        sleep = AsyncMock(
            side_effect=asyncio.CancelledError
        )

        with (
            patch(
                f"{MODULE}.recover_one_live_obligation_once",
                new=coordinator,
            ),
            patch(
                f"{MODULE}.asyncio.sleep",
                new=sleep,
            ),
            patch(
                "builtins.print"
            ) as output,
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await run_live_recovery_heartbeat(
                    allow_submission=True,
                    db_path=self.db_path,
                )

        output.assert_not_called()

    async def test_non_idle_result_is_visible(
        self,
    ):
        coordinator = AsyncMock(
            return_value=self.result(
                status=ADVANCED,
                stage="EXECUTE",
                reasons=("SUBMITTED",),
                side="BUY",
                state="PRISTINE_SIGNED",
            )
        )

        sleep = AsyncMock(
            side_effect=asyncio.CancelledError
        )

        with (
            patch(
                f"{MODULE}.recover_one_live_obligation_once",
                new=coordinator,
            ),
            patch(
                f"{MODULE}.asyncio.sleep",
                new=sleep,
            ),
            patch(
                "builtins.print"
            ) as output,
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await run_live_recovery_heartbeat(
                    allow_submission=True,
                    db_path=self.db_path,
                )

        output.assert_called_once_with(
            "🔁 LIVE RECOVERY | "
            "status=ADVANCED | "
            "side=BUY | "
            "state=PRISTINE_SIGNED | "
            "reasons=SUBMITTED"
        )

    async def test_malformed_coordinator_result_is_visible_and_isolated(
        self,
    ):
        coordinator = AsyncMock(
            return_value=self.result(
                version="wrong-version",
            )
        )

        sleep = AsyncMock(
            side_effect=asyncio.CancelledError
        )

        with (
            patch(
                f"{MODULE}.recover_one_live_obligation_once",
                new=coordinator,
            ),
            patch(
                f"{MODULE}.asyncio.sleep",
                new=sleep,
            ),
            patch(
                "builtins.print"
            ) as output,
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await run_live_recovery_heartbeat(
                    allow_submission=True,
                    db_path=self.db_path,
                )

        output.assert_called_once_with(
            "⚠️ LIVE RECOVERY HEARTBEAT "
            "CONTRACT ERROR"
        )

    async def test_monotonic_cadence_accounts_for_work_time(
        self,
    ):
        coordinator = AsyncMock(
            return_value=self.result()
        )

        sleep = AsyncMock(
            side_effect=asyncio.CancelledError
        )

        with (
            patch(
                f"{MODULE}.recover_one_live_obligation_once",
                new=coordinator,
            ),
            patch(
                f"{MODULE}.asyncio.sleep",
                new=sleep,
            ),
            patch(
                f"{MODULE}.time.monotonic",
                side_effect=(
                    100.0,
                    101.5,
                ),
            ),
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await run_live_recovery_heartbeat(
                    allow_submission=True,
                    db_path=self.db_path,
                    interval_seconds=5.0,
                )

        sleep.assert_awaited_once_with(
            3.5
        )

    async def test_minimum_delay_prevents_tight_loop_after_slow_tick(
        self,
    ):
        coordinator = AsyncMock(
            return_value=self.result()
        )

        sleep = AsyncMock(
            side_effect=asyncio.CancelledError
        )

        with (
            patch(
                f"{MODULE}.recover_one_live_obligation_once",
                new=coordinator,
            ),
            patch(
                f"{MODULE}.asyncio.sleep",
                new=sleep,
            ),
            patch(
                f"{MODULE}.time.monotonic",
                side_effect=(
                    100.0,
                    110.0,
                ),
            ),
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await run_live_recovery_heartbeat(
                    allow_submission=True,
                    db_path=self.db_path,
                    interval_seconds=5.0,
                )

        sleep.assert_awaited_once_with(
            0.1
        )


if __name__ == "__main__":
    unittest.main()
