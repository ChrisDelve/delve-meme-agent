from __future__ import annotations

import asyncio
from pathlib import Path
import signal
import unittest
from unittest.mock import (
    AsyncMock,
    Mock,
    call,
    patch,
)

from src.execution.live_operating_config import (
    LiveOperatingConfig,
)
from src.execution.live_process_runner import (
    LIVE_PROCESS_RECOVERY_CANCELLED,
    LIVE_PROCESS_RECOVERY_STOPPED,
    LIVE_PROCESS_RUNNER_VERSION,
    LIVE_PROCESS_SIGNAL_HANDLER_UNAVAILABLE,
    LiveProcessRunnerError,
    _install_shutdown_signal_handlers,
    run_bootstrapped_live_process,
    run_live_process,
)


RUNNER_MODULE = (
    "src.execution.live_process_runner"
)


class LiveProcessRunnerTests(
    unittest.IsolatedAsyncioTestCase
):
    @staticmethod
    def config(
    ) -> LiveOperatingConfig:
        return LiveOperatingConfig(
            db_path=Path(
                "/tmp/"
                "delve-live-runner-test.db"
            ),
            recovery_interval_seconds=5.0,
            operational_kill=False,
            max_trade_equity_bps=100,
            max_total_exposure_bps=1000,
            max_daily_loss_bps=500,
            max_drawdown_bps=1000,
            max_open_positions=5,
            min_trade_lamports=1_000_000,
            max_size_price_impact_bps=500,
        )

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_PROCESS_RUNNER_VERSION,
            "live-process-runner-v1",
        )

    async def test_invalid_config_fails_before_owner(
        self,
    ):
        owner_constructor = Mock()

        with patch(
            f"{RUNNER_MODULE}."
            "LiveProcessOwner",
            new=owner_constructor,
        ):
            with self.assertRaisesRegex(
                TypeError,
                "^config must be LiveOperatingConfig$",
            ):
                await run_live_process(
                    config=object(),
                    shutdown_event=asyncio.Event(),
                )

        owner_constructor.assert_not_called()

    async def test_invalid_shutdown_event_fails_before_owner(
        self,
    ):
        owner_constructor = Mock()

        with patch(
            f"{RUNNER_MODULE}."
            "LiveProcessOwner",
            new=owner_constructor,
        ):
            with self.assertRaisesRegex(
                TypeError,
                (
                    "^shutdown_event must be "
                    "asyncio.Event$"
                ),
            ):
                await run_live_process(
                    config=self.config(),
                    shutdown_event=object(),
                )

        owner_constructor.assert_not_called()

    async def test_component_version_mismatch_fails_before_owner(
        self,
    ):
        owner_constructor = Mock()

        with (
            patch(
                f"{RUNNER_MODULE}."
                "LIVE_PROCESS_OWNER_VERSION",
                new="unexpected-version",
            ),
            patch(
                f"{RUNNER_MODULE}."
                "LiveProcessOwner",
                new=owner_constructor,
            ),
        ):
            with self.assertRaisesRegex(
                LiveProcessRunnerError,
                (
                    "^LIVE_PROCESS_RUNNER_"
                    "COMPONENT_VERSION_MISMATCH$"
                ),
            ):
                await run_live_process(
                    config=self.config(),
                    shutdown_event=asyncio.Event(),
                )

        owner_constructor.assert_not_called()

    async def test_preexisting_shutdown_does_not_acquire_owner(
        self,
    ):
        shutdown_event = asyncio.Event()
        shutdown_event.set()

        owner_constructor = Mock()

        with patch(
            f"{RUNNER_MODULE}."
            "LiveProcessOwner",
            new=owner_constructor,
        ):
            await run_live_process(
                config=self.config(),
                shutdown_event=shutdown_event,
            )

        owner_constructor.assert_not_called()

    async def test_shutdown_cancels_recovery_then_closes_owner(
        self,
    ):
        shutdown_event = asyncio.Event()
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def recovery(
        ):
            entered.set()

            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        owner = Mock()
        owner.run_recovery_service = Mock(
            side_effect=lambda: recovery()
        )
        owner.close = Mock()

        with patch(
            f"{RUNNER_MODULE}."
            "LiveProcessOwner",
            return_value=owner,
        ):
            task = asyncio.create_task(
                run_live_process(
                    config=self.config(),
                    shutdown_event=shutdown_event,
                )
            )

            await entered.wait()

            shutdown_event.set()

            await task

        self.assertTrue(
            cancelled.is_set()
        )

        owner.close.assert_called_once_with()

    async def test_recovery_exception_propagates_and_closes_owner(
        self,
    ):
        shutdown_event = asyncio.Event()

        owner = Mock()
        owner.run_recovery_service = AsyncMock(
            side_effect=RuntimeError(
                "recovery failed"
            )
        )
        owner.close = Mock()

        with patch(
            f"{RUNNER_MODULE}."
            "LiveProcessOwner",
            return_value=owner,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^recovery failed$",
            ):
                await run_live_process(
                    config=self.config(),
                    shutdown_event=shutdown_event,
                )

        owner.close.assert_called_once_with()

    async def test_normal_recovery_return_fails_closed(
        self,
    ):
        owner = Mock()
        owner.run_recovery_service = AsyncMock(
            return_value=None
        )
        owner.close = Mock()

        with patch(
            f"{RUNNER_MODULE}."
            "LiveProcessOwner",
            return_value=owner,
        ):
            with self.assertRaisesRegex(
                LiveProcessRunnerError,
                (
                    "^"
                    + LIVE_PROCESS_RECOVERY_STOPPED
                    + "$"
                ),
            ):
                await run_live_process(
                    config=self.config(),
                    shutdown_event=asyncio.Event(),
                )

        owner.close.assert_called_once_with()

    async def test_independent_recovery_cancellation_fails_closed(
        self,
    ):
        entered = asyncio.Event()

        async def recovery(
        ):
            entered.set()
            raise asyncio.CancelledError

        owner = Mock()
        owner.run_recovery_service = Mock(
            side_effect=lambda: recovery()
        )
        owner.close = Mock()

        with patch(
            f"{RUNNER_MODULE}."
            "LiveProcessOwner",
            return_value=owner,
        ):
            with self.assertRaisesRegex(
                LiveProcessRunnerError,
                (
                    "^"
                    + LIVE_PROCESS_RECOVERY_CANCELLED
                    + "$"
                ),
            ):
                await run_live_process(
                    config=self.config(),
                    shutdown_event=asyncio.Event(),
                )

        self.assertTrue(
            entered.is_set()
        )

        owner.close.assert_called_once_with()

    async def test_runner_cancellation_settles_recovery_before_close(
        self,
    ):
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        events = []

        async def recovery(
        ):
            entered.set()

            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append(
                    "recovery-cancelled"
                )
                cancelled.set()
                raise

        owner = Mock()
        owner.run_recovery_service = Mock(
            side_effect=lambda: recovery()
        )

        def close_owner(
        ):
            events.append(
                "owner-closed"
            )

        owner.close = Mock(
            side_effect=close_owner
        )

        with patch(
            f"{RUNNER_MODULE}."
            "LiveProcessOwner",
            return_value=owner,
        ):
            task = asyncio.create_task(
                run_live_process(
                    config=self.config(),
                    shutdown_event=asyncio.Event(),
                )
            )

            await entered.wait()

            task.cancel()

            with self.assertRaises(
                asyncio.CancelledError
            ):
                await task

        self.assertTrue(
            cancelled.is_set()
        )

        self.assertEqual(
            events,
            [
                "recovery-cancelled",
                "owner-closed",
            ],
        )

    async def test_bootstrap_forwards_config_and_removes_handlers(
        self,
    ):
        config = self.config()

        loop = Mock()

        install = Mock(
            return_value=(
                loop,
                (
                    signal.SIGINT,
                    signal.SIGTERM,
                ),
            )
        )

        bootstrap = Mock(
            return_value=config
        )

        lifecycle = AsyncMock(
            return_value=None
        )

        dotenv_path = Path(
            "/tmp/test-live.env"
        )

        with (
            patch(
                f"{RUNNER_MODULE}."
                "_install_shutdown_signal_handlers",
                new=install,
            ),
            patch(
                f"{RUNNER_MODULE}."
                "bootstrap_live_operating_config",
                new=bootstrap,
            ),
            patch(
                f"{RUNNER_MODULE}."
                "run_live_process",
                new=lifecycle,
            ),
        ):
            await run_bootstrapped_live_process(
                dotenv_path=dotenv_path
            )

        bootstrap.assert_called_once_with(
            dotenv_path=dotenv_path
        )

        lifecycle.assert_awaited_once()

        self.assertIs(
            lifecycle.await_args.kwargs[
                "config"
            ],
            config,
        )

        self.assertIsInstance(
            lifecycle.await_args.kwargs[
                "shutdown_event"
            ],
            asyncio.Event,
        )

        self.assertEqual(
            loop.remove_signal_handler.call_args_list,
            [
                call(
                    signal.SIGINT
                ),
                call(
                    signal.SIGTERM
                ),
            ],
        )

    async def test_bootstrap_yields_to_pending_shutdown_before_lifecycle(
        self,
    ):
        config = self.config()

        loop = asyncio.get_running_loop()
        captured = {}

        def install(
            *,
            shutdown_event,
        ):
            captured[
                "shutdown_event"
            ] = shutdown_event

            return (
                loop,
                (),
            )

        def bootstrap(
            *,
            dotenv_path,
        ):
            loop.call_soon(
                captured[
                    "shutdown_event"
                ].set
            )

            return config

        async def lifecycle(
            *,
            config,
            shutdown_event,
        ):
            self.assertTrue(
                shutdown_event.is_set()
            )

        lifecycle_mock = AsyncMock(
            side_effect=lifecycle
        )

        with (
            patch(
                f"{RUNNER_MODULE}."
                "_install_shutdown_signal_handlers",
                side_effect=install,
            ),
            patch(
                f"{RUNNER_MODULE}."
                "bootstrap_live_operating_config",
                side_effect=bootstrap,
            ),
            patch(
                f"{RUNNER_MODULE}."
                "run_live_process",
                new=lifecycle_mock,
            ),
        ):
            await run_bootstrapped_live_process()

        lifecycle_mock.assert_awaited_once()

    async def test_bootstrap_failure_still_removes_handlers(
        self,
    ):
        loop = Mock()

        with (
            patch(
                f"{RUNNER_MODULE}."
                "_install_shutdown_signal_handlers",
                return_value=(
                    loop,
                    (
                        signal.SIGINT,
                        signal.SIGTERM,
                    ),
                ),
            ),
            patch(
                f"{RUNNER_MODULE}."
                "bootstrap_live_operating_config",
                side_effect=ValueError(
                    "bad config"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "^bad config$",
            ):
                await run_bootstrapped_live_process()

        self.assertEqual(
            loop.remove_signal_handler.call_args_list,
            [
                call(
                    signal.SIGINT
                ),
                call(
                    signal.SIGTERM
                ),
            ],
        )

    async def test_signal_install_failure_cleans_partial_handlers(
        self,
    ):
        shutdown_event = asyncio.Event()

        loop = Mock()

        loop.add_signal_handler.side_effect = [
            None,
            NotImplementedError(),
        ]

        with patch(
            f"{RUNNER_MODULE}."
            "asyncio.get_running_loop",
            return_value=loop,
        ):
            with self.assertRaisesRegex(
                LiveProcessRunnerError,
                (
                    "^"
                    + LIVE_PROCESS_SIGNAL_HANDLER_UNAVAILABLE
                    + "$"
                ),
            ):
                _install_shutdown_signal_handlers(
                    shutdown_event=shutdown_event
                )

        self.assertEqual(
            loop.remove_signal_handler.call_args_list,
            [
                call(
                    signal.SIGINT
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
