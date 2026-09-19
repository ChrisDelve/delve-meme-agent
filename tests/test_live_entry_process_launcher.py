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
    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_ENTRY_PROCESS_LAUNCHER_VERSION,
            "live-entry-process-launcher-v1",
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

        lifecycle = AsyncMock(
            return_value=None
        )

        dotenv_path = Path(
            "/tmp/delve-production.env"
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
                new=lifecycle,
            ),
        ):
            await (
                run_bootstrapped_live_entry_process(
                    dotenv_path=dotenv_path
                )
            )

        install.assert_called_once()

        shutdown_event = (
            install.call_args.kwargs[
                "shutdown_event"
            ]
        )

        self.assertIsInstance(
            shutdown_event,
            asyncio.Event,
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

        lifecycle.assert_awaited_once_with(
            operating_config=operating_config,
            evidence_config=evidence_config,
            execution_config=execution_config,
            sell_supervisor_config=sell_config,
            shutdown_event=shutdown_event,
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

    async def test_bootstrap_order_is_explicit(
        self,
    ):
        loop = Mock()
        events = []

        def operating(
            *,
            dotenv_path,
        ):
            events.append(
                "operating"
            )
            return object()

        def evidence(
            *,
            dotenv_path,
        ):
            events.append(
                "evidence"
            )
            return object()

        def execution(
            *,
            dotenv_path,
        ):
            events.append(
                "execution"
            )
            return object()

        def sell(
            *,
            dotenv_path,
        ):
            events.append(
                "sell"
            )
            return object()

        async def lifecycle(
            **kwargs,
        ):
            del kwargs

            events.append(
                "lifecycle"
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
                "evidence",
                "execution",
                "sell",
                "lifecycle",
            ],
        )

    async def test_pending_shutdown_is_visible_before_lifecycle(
        self,
    ):
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
            evidence_config,
            execution_config,
            sell_supervisor_config,
            shutdown_event,
        ):
            del (
                operating_config,
                evidence_config,
                execution_config,
                sell_supervisor_config,
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
                return_value=object(),
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_buy_execution_config",
                return_value=object(),
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_sell_supervisor_config",
                return_value=object(),
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

        lifecycle_mock.assert_awaited_once()

    async def test_bootstrap_failure_removes_handlers_and_stops_composition(
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
                    "bad operating config"
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
                "^bad operating config$",
            ):
                await (
                    run_bootstrapped_live_entry_process()
                )

        evidence.assert_not_called()
        execution.assert_not_called()
        sell.assert_not_called()
        lifecycle.assert_not_awaited()

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

    async def test_lifecycle_failure_still_removes_handlers(
        self,
    ):
        loop = Mock()

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
                return_value=object(),
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_entry_evidence_only_config",
                return_value=object(),
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_buy_execution_config",
                return_value=object(),
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_sell_supervisor_config",
                return_value=object(),
            ),
            patch(
                f"{MODULE}."
                "run_live_entry_process",
                new=AsyncMock(
                    side_effect=RuntimeError(
                        "live process failed"
                    )
                ),
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^live process failed$",
            ):
                await (
                    run_bootstrapped_live_entry_process()
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


if __name__ == "__main__":
    unittest.main()
