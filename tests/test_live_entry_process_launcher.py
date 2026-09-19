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

from src.execution.live_entry_process_launcher import (
    LIVE_ENTRY_PROCESS_LAUNCHER_COMPONENT_VERSION_MISMATCH,
    LIVE_ENTRY_PROCESS_LAUNCHER_VERSION,
    LiveEntryProcessLauncherError,
    run_bootstrapped_live_entry_process,
)


MODULE = (
    "src.execution.live_entry_process_launcher"
)


class LiveEntryProcessLauncherTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(
        self,
    ):
        self.preflight = AsyncMock(
            return_value=object()
        )

        self.preflight_patcher = patch(
            f"{MODULE}."
            "run_live_startup_preflight",
            new=self.preflight,
        )

        self.preflight_patcher.start()

        self.addCleanup(
            self.preflight_patcher.stop
        )

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_ENTRY_PROCESS_LAUNCHER_VERSION,
            "live-entry-process-launcher-v4",
        )

    async def test_component_mismatch_fails_before_signal_or_bootstrap(
        self,
    ):
        install = Mock()
        operating = Mock()
        evidence = Mock()
        execution = Mock()
        sell = Mock()
        lifecycle = AsyncMock()

        with (
            patch(
                f"{MODULE}."
                "LIVE_ENTRY_PROCESS_RUNNER_VERSION",
                "unexpected-version",
            ),
            patch(
                f"{MODULE}."
                "_install_shutdown_signal_handlers",
                new=install,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_operating_config",
                new=operating,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_entry_evidence_only_config",
                new=evidence,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_buy_execution_config",
                new=execution,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_sell_supervisor_config",
                new=sell,
            ),
            patch(
                f"{MODULE}."
                "run_live_entry_process",
                new=lifecycle,
            ),
        ):
            with self.assertRaisesRegex(
                LiveEntryProcessLauncherError,
                (
                    "^"
                    + LIVE_ENTRY_PROCESS_LAUNCHER_COMPONENT_VERSION_MISMATCH
                    + "$"
                ),
            ):
                await (
                    run_bootstrapped_live_entry_process()
                )

        install.assert_not_called()
        operating.assert_not_called()
        evidence.assert_not_called()
        execution.assert_not_called()
        sell.assert_not_called()
        lifecycle.assert_not_awaited()
        self.preflight.assert_not_awaited()

    async def test_exact_bootstrap_composition_and_handler_cleanup(
        self,
    ):
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

        operating_config = object()
        evidence_config = object()
        execution_config = object()
        sell_config = object()

        operating = Mock(
            return_value=operating_config
        )
        evidence = Mock(
            return_value=evidence_config
        )
        execution = Mock(
            return_value=execution_config
        )
        sell = Mock(
            return_value=sell_config
        )

        dotenv_path = Path(
            "/tmp/delve-production.env"
        )

        captured = {}

        async def lifecycle(
            *,
            operating_config,
            fresh_config_loader,
            shutdown_event,
        ):
            captured["operating"] = (
                operating_config
            )
            captured["loader"] = (
                fresh_config_loader
            )
            captured["shutdown"] = (
                shutdown_event
            )

            loaded = fresh_config_loader()

            self.assertEqual(
                loaded,
                (
                    evidence_config,
                    execution_config,
                    sell_config,
                ),
            )

        lifecycle_mock = AsyncMock(
            side_effect=lifecycle
        )

        with (
            patch(
                f"{MODULE}."
                "_install_shutdown_signal_handlers",
                new=install,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_operating_config",
                new=operating,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_entry_evidence_only_config",
                new=evidence,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_buy_execution_config",
                new=execution,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_sell_supervisor_config",
                new=sell,
            ),
            patch(
                f"{MODULE}."
                "run_live_entry_process",
                new=lifecycle_mock,
            ),
        ):
            await (
                run_bootstrapped_live_entry_process(
                    dotenv_path=dotenv_path
                )
            )

        operating.assert_called_once_with(
            dotenv_path=dotenv_path
        )

        evidence.assert_called_once_with(
            dotenv_path=dotenv_path
        )
        execution.assert_called_once_with(
            dotenv_path=dotenv_path
        )
        sell.assert_called_once_with(
            dotenv_path=dotenv_path
        )

        self.preflight.assert_awaited_once_with(
            operating_config=operating_config
        )

        lifecycle_mock.assert_awaited_once()

        self.assertIs(
            captured["operating"],
            operating_config,
        )
        self.assertIsInstance(
            captured["shutdown"],
            asyncio.Event,
        )
        self.assertTrue(
            callable(
                captured["loader"]
            )
        )

        self.assertEqual(
            loop.remove_signal_handler.call_args_list,
            [
                call(signal.SIGINT),
                call(signal.SIGTERM),
            ],
        )

    async def test_bootstrap_order_is_explicit(
        self,
    ):
        loop = Mock()
        events = []

        operating_config = object()
        evidence_config = object()
        execution_config = object()
        sell_config = object()

        def operating(
            *,
            dotenv_path,
        ):
            del dotenv_path
            events.append(
                "operating"
            )
            return operating_config

        def evidence(
            *,
            dotenv_path,
        ):
            del dotenv_path
            events.append(
                "evidence"
            )
            return evidence_config

        def execution(
            *,
            dotenv_path,
        ):
            del dotenv_path
            events.append(
                "execution"
            )
            return execution_config

        def sell(
            *,
            dotenv_path,
        ):
            del dotenv_path
            events.append(
                "sell"
            )
            return sell_config

        async def preflight(
            *,
            operating_config,
        ):
            del operating_config
            events.append(
                "preflight"
            )

        async def lifecycle(
            *,
            operating_config,
            fresh_config_loader,
            shutdown_event,
        ):
            del (
                operating_config,
                shutdown_event,
            )

            events.append(
                "lifecycle"
            )

            loaded = fresh_config_loader()

            self.assertEqual(
                loaded,
                (
                    evidence_config,
                    execution_config,
                    sell_config,
                ),
            )

        self.preflight.side_effect = (
            preflight
        )

        with (
            patch(
                f"{MODULE}."
                "_install_shutdown_signal_handlers",
                return_value=(
                    loop,
                    (),
                ),
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_operating_config",
                side_effect=operating,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_entry_evidence_only_config",
                side_effect=evidence,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_buy_execution_config",
                side_effect=execution,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_sell_supervisor_config",
                side_effect=sell,
            ),
            patch(
                f"{MODULE}."
                "run_live_entry_process",
                new=AsyncMock(
                    side_effect=lifecycle
                ),
            ),
        ):
            await (
                run_bootstrapped_live_entry_process(
                    dotenv_path=None
                )
            )

        self.assertEqual(
            events,
            [
                "operating",
                "preflight",
                "lifecycle",
                "evidence",
                "execution",
                "sell",
            ],
        )

    async def test_pending_shutdown_is_visible_before_lifecycle(
        self,
    ):
        loop = asyncio.get_running_loop()
        captured = {}

        evidence = Mock()
        execution = Mock()
        sell = Mock()

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

        def operating(
            *,
            dotenv_path,
        ):
            del dotenv_path

            loop.call_soon(
                captured[
                    "shutdown_event"
                ].set
            )

            return object()

        async def lifecycle(
            *,
            operating_config,
            fresh_config_loader,
            shutdown_event,
        ):
            del (
                operating_config,
                fresh_config_loader,
            )

            self.assertTrue(
                shutdown_event.is_set()
            )

        lifecycle_mock = AsyncMock(
            side_effect=lifecycle
        )

        with (
            patch(
                f"{MODULE}."
                "_install_shutdown_signal_handlers",
                side_effect=install,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_operating_config",
                side_effect=operating,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_entry_evidence_only_config",
                new=evidence,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_buy_execution_config",
                new=execution,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_sell_supervisor_config",
                new=sell,
            ),
            patch(
                f"{MODULE}."
                "run_live_entry_process",
                new=lifecycle_mock,
            ),
        ):
            await (
                run_bootstrapped_live_entry_process()
            )

        self.preflight.assert_not_awaited()

        evidence.assert_not_called()
        execution.assert_not_called()
        sell.assert_not_called()

        lifecycle_mock.assert_awaited_once()

    async def test_operating_bootstrap_failure_removes_handlers_and_stops_composition(
        self,
    ):
        loop = Mock()

        evidence = Mock()
        execution = Mock()
        sell = Mock()
        lifecycle = AsyncMock()

        with (
            patch(
                f"{MODULE}."
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
                f"{MODULE}."
                "bootstrap_live_operating_config",
                side_effect=RuntimeError(
                    "bootstrap failed"
                ),
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_entry_evidence_only_config",
                new=evidence,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_buy_execution_config",
                new=execution,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_sell_supervisor_config",
                new=sell,
            ),
            patch(
                f"{MODULE}."
                "run_live_entry_process",
                new=lifecycle,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^bootstrap failed$",
            ):
                await (
                    run_bootstrapped_live_entry_process()
                )

        self.preflight.assert_not_awaited()
        evidence.assert_not_called()
        execution.assert_not_called()
        sell.assert_not_called()
        lifecycle.assert_not_awaited()

        self.assertEqual(
            loop.remove_signal_handler.call_args_list,
            [
                call(signal.SIGINT),
                call(signal.SIGTERM),
            ],
        )

    async def test_lifecycle_failure_still_removes_handlers(
        self,
    ):
        loop = Mock()
        operating_config = object()

        evidence = Mock()
        execution = Mock()
        sell = Mock()

        lifecycle = AsyncMock(
            side_effect=RuntimeError(
                "lifecycle failed"
            )
        )

        with (
            patch(
                f"{MODULE}."
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
                f"{MODULE}."
                "bootstrap_live_operating_config",
                return_value=operating_config,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_entry_evidence_only_config",
                new=evidence,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_buy_execution_config",
                new=execution,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_sell_supervisor_config",
                new=sell,
            ),
            patch(
                f"{MODULE}."
                "run_live_entry_process",
                new=lifecycle,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^lifecycle failed$",
            ):
                await (
                    run_bootstrapped_live_entry_process()
                )

        self.preflight.assert_awaited_once_with(
            operating_config=operating_config
        )

        #
        # Lifecycle failed before invoking the lazy loader.
        #
        evidence.assert_not_called()
        execution.assert_not_called()
        sell.assert_not_called()

        self.assertEqual(
            loop.remove_signal_handler.call_args_list,
            [
                call(signal.SIGINT),
                call(signal.SIGTERM),
            ],
        )

    async def test_signal_install_failure_prevents_all_bootstrap(
        self,
    ):
        operating = Mock()
        evidence = Mock()
        execution = Mock()
        sell = Mock()
        lifecycle = AsyncMock()

        with (
            patch(
                f"{MODULE}."
                "_install_shutdown_signal_handlers",
                side_effect=RuntimeError(
                    "signal install failed"
                ),
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_operating_config",
                new=operating,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_entry_evidence_only_config",
                new=evidence,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_buy_execution_config",
                new=execution,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_sell_supervisor_config",
                new=sell,
            ),
            patch(
                f"{MODULE}."
                "run_live_entry_process",
                new=lifecycle,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^signal install failed$",
            ):
                await (
                    run_bootstrapped_live_entry_process()
                )

        operating.assert_not_called()
        evidence.assert_not_called()
        execution.assert_not_called()
        sell.assert_not_called()
        lifecycle.assert_not_awaited()
        self.preflight.assert_not_awaited()

    async def test_preflight_runs_before_lifecycle_and_fresh_config(
        self,
    ):
        events = []
        loop = Mock()

        operating_config = object()

        evidence = Mock(
            side_effect=lambda **kwargs: (
                events.append("evidence"),
                object(),
            )[1]
        )
        execution = Mock(
            side_effect=lambda **kwargs: (
                events.append("execution"),
                object(),
            )[1]
        )
        sell = Mock(
            side_effect=lambda **kwargs: (
                events.append("sell"),
                object(),
            )[1]
        )

        async def preflight(
            *,
            operating_config,
        ):
            del operating_config
            events.append(
                "preflight"
            )

        async def lifecycle(
            *,
            operating_config,
            fresh_config_loader,
            shutdown_event,
        ):
            del (
                operating_config,
                shutdown_event,
            )

            events.append(
                "lifecycle"
            )

            fresh_config_loader()

        self.preflight.side_effect = (
            preflight
        )

        with (
            patch(
                f"{MODULE}."
                "_install_shutdown_signal_handlers",
                return_value=(
                    loop,
                    (),
                ),
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_operating_config",
                return_value=operating_config,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_entry_evidence_only_config",
                new=evidence,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_buy_execution_config",
                new=execution,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_sell_supervisor_config",
                new=sell,
            ),
            patch(
                f"{MODULE}."
                "run_live_entry_process",
                new=AsyncMock(
                    side_effect=lifecycle
                ),
            ),
        ):
            await (
                run_bootstrapped_live_entry_process()
            )

        self.assertEqual(
            events,
            [
                "preflight",
                "lifecycle",
                "evidence",
                "execution",
                "sell",
            ],
        )

    async def test_pending_shutdown_skips_preflight_and_fresh_config(
        self,
    ):
        loop = asyncio.get_running_loop()
        captured = {}

        operating_config = object()

        evidence = Mock()
        execution = Mock()
        sell = Mock()

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

        def operating(
            *,
            dotenv_path,
        ):
            del dotenv_path

            loop.call_soon(
                captured[
                    "shutdown_event"
                ].set
            )

            return operating_config

        async def lifecycle(
            *,
            operating_config,
            fresh_config_loader,
            shutdown_event,
        ):
            del (
                operating_config,
                fresh_config_loader,
            )

            self.assertTrue(
                shutdown_event.is_set()
            )

        lifecycle_mock = AsyncMock(
            side_effect=lifecycle
        )

        with (
            patch(
                f"{MODULE}."
                "_install_shutdown_signal_handlers",
                side_effect=install,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_operating_config",
                side_effect=operating,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_entry_evidence_only_config",
                new=evidence,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_buy_execution_config",
                new=execution,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_sell_supervisor_config",
                new=sell,
            ),
            patch(
                f"{MODULE}."
                "run_live_entry_process",
                new=lifecycle_mock,
            ),
        ):
            await (
                run_bootstrapped_live_entry_process()
            )

        self.preflight.assert_not_awaited()

        evidence.assert_not_called()
        execution.assert_not_called()
        sell.assert_not_called()

        lifecycle_mock.assert_awaited_once()

    async def test_preflight_failure_prevents_lifecycle_and_fresh_config(
        self,
    ):
        loop = Mock()
        operating_config = object()

        evidence = Mock()
        execution = Mock()
        sell = Mock()
        lifecycle = AsyncMock()

        self.preflight.side_effect = (
            RuntimeError(
                "preflight failed"
            )
        )

        with (
            patch(
                f"{MODULE}."
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
                f"{MODULE}."
                "bootstrap_live_operating_config",
                return_value=operating_config,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_entry_evidence_only_config",
                new=evidence,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_buy_execution_config",
                new=execution,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_sell_supervisor_config",
                new=sell,
            ),
            patch(
                f"{MODULE}."
                "run_live_entry_process",
                new=lifecycle,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^preflight failed$",
            ):
                await (
                    run_bootstrapped_live_entry_process()
                )

        self.preflight.assert_awaited_once_with(
            operating_config=operating_config
        )

        evidence.assert_not_called()
        execution.assert_not_called()
        sell.assert_not_called()
        lifecycle.assert_not_awaited()

        self.assertEqual(
            loop.remove_signal_handler.call_args_list,
            [
                call(signal.SIGINT),
                call(signal.SIGTERM),
            ],
        )


if __name__ == "__main__":
    unittest.main()
