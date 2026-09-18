from __future__ import annotations

import asyncio
from pathlib import Path
import signal
import sys
from types import (
    ModuleType,
    SimpleNamespace,
)
import unittest
from unittest.mock import (
    AsyncMock,
    Mock,
    call,
    patch,
)

from src.execution.live_entry_evidence_only_config import (
    LiveEntryEvidenceOnlyConfig,
)
from src.execution.live_entry_evidence_only_runner import (
    EVIDENCE_ONLY_COLLECTOR_CANCELLED,
    EVIDENCE_ONLY_COLLECTOR_STOPPED,
    EVIDENCE_ONLY_SIGNAL_HANDLER_UNAVAILABLE,
    LIVE_ENTRY_EVIDENCE_ONLY_RUNNER_VERSION,
    LiveEntryEvidenceOnlyRunnerError,
    _install_shutdown_signal_handlers,
    _run_market_collector_with_scheduler,
    run_bootstrapped_live_entry_evidence_only_process,
    run_live_entry_evidence_only_process,
)


MODULE = (
    "src.execution."
    "live_entry_evidence_only_runner"
)


class LiveEntryEvidenceOnlyRunnerTests(
    unittest.IsolatedAsyncioTestCase
):
    def config(
        self,
    ):
        return (
            LiveEntryEvidenceOnlyConfig(
                min_probability_2x_15m=0.40,
                max_candidate_age_seconds=5,
                max_concurrency=2,
                max_pending_tasks=8,
            )
        )

    def fake_scheduler(
        self,
    ):
        scheduler = SimpleNamespace(
            closed=False,
        )

        async def close():
            scheduler.closed = True

        scheduler.close = AsyncMock(
            side_effect=close
        )

        return scheduler

    async def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_ENTRY_EVIDENCE_ONLY_RUNNER_VERSION,
            (
                "live-entry-evidence-only-"
                "runner-v1"
            ),
        )

    async def test_invalid_config_fails_before_scheduler(
        self,
    ):
        scheduler_type = Mock()

        with patch(
            f"{MODULE}."
            "LiveEntryCandidateScheduler",
            new=scheduler_type,
        ):
            with self.assertRaisesRegex(
                TypeError,
                (
                    "^config must be "
                    "LiveEntryEvidenceOnlyConfig$"
                ),
            ):
                await (
                    run_live_entry_evidence_only_process(
                        config=object(),
                        shutdown_event=asyncio.Event(),
                    )
                )

        scheduler_type.assert_not_called()

    async def test_invalid_shutdown_event_fails_before_scheduler(
        self,
    ):
        scheduler_type = Mock()

        with patch(
            f"{MODULE}."
            "LiveEntryCandidateScheduler",
            new=scheduler_type,
        ):
            with self.assertRaisesRegex(
                TypeError,
                (
                    "^shutdown_event must be "
                    "asyncio.Event$"
                ),
            ):
                await (
                    run_live_entry_evidence_only_process(
                        config=self.config(),
                        shutdown_event=object(),
                    )
                )

        scheduler_type.assert_not_called()

    async def test_component_mismatch_fails_before_scheduler(
        self,
    ):
        scheduler_type = Mock()

        with (
            patch(
                f"{MODULE}."
                "LIVE_ENTRY_CANDIDATE_SCHEDULER_VERSION",
                "wrong-version",
            ),
            patch(
                f"{MODULE}."
                "LiveEntryCandidateScheduler",
                new=scheduler_type,
            ),
        ):
            with self.assertRaisesRegex(
                LiveEntryEvidenceOnlyRunnerError,
                (
                    "^EVIDENCE_ONLY_RUNNER_"
                    "COMPONENT_VERSION_MISMATCH$"
                ),
            ):
                await (
                    run_live_entry_evidence_only_process(
                        config=self.config(),
                        shutdown_event=asyncio.Event(),
                    )
                )

        scheduler_type.assert_not_called()

    async def test_preset_shutdown_returns_before_scheduler(
        self,
    ):
        shutdown_event = asyncio.Event()
        shutdown_event.set()

        scheduler_type = Mock()

        with patch(
            f"{MODULE}."
            "LiveEntryCandidateScheduler",
            new=scheduler_type,
        ):
            await (
                run_live_entry_evidence_only_process(
                    config=self.config(),
                    shutdown_event=shutdown_event,
                )
            )

        scheduler_type.assert_not_called()

    async def test_lazy_collector_adapter_forwards_exact_scheduler(
        self,
    ):
        scheduler = self.fake_scheduler()

        collector_module = ModuleType(
            "src.data.market_collector"
        )

        run_market_collector = AsyncMock(
            return_value=None
        )

        collector_module.run_market_collector = (
            run_market_collector
        )

        with patch.dict(
            sys.modules,
            {
                "src.data.market_collector":
                    collector_module
            },
        ):
            await (
                _run_market_collector_with_scheduler(
                    scheduler=scheduler
                )
            )

        run_market_collector.assert_awaited_once_with(
            live_entry_scheduler=scheduler
        )

    async def test_composition_maps_exact_config_to_scheduler(
        self,
    ):
        config = self.config()
        shutdown_event = asyncio.Event()

        scheduler = self.fake_scheduler()

        scheduler_type = Mock(
            return_value=scheduler
        )

        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def collector(
            *,
            scheduler,
        ):
            started.set()

            try:
                await asyncio.Event().wait()

            except asyncio.CancelledError:
                cancelled.set()
                raise

        with (
            patch(
                f"{MODULE}."
                "LiveEntryCandidateScheduler",
                new=scheduler_type,
            ),
            patch(
                f"{MODULE}."
                "_run_market_collector_with_scheduler",
                side_effect=collector,
            ),
        ):
            task = asyncio.create_task(
                run_live_entry_evidence_only_process(
                    config=config,
                    shutdown_event=shutdown_event,
                )
            )

            await started.wait()

            shutdown_event.set()

            await task

        scheduler_type.assert_called_once()

        kwargs = (
            scheduler_type.call_args.kwargs
        )

        self.assertEqual(
            kwargs["policy"],
            config.policy,
        )
        self.assertEqual(
            kwargs["max_concurrency"],
            config.max_concurrency,
        )
        self.assertEqual(
            kwargs["max_pending_tasks"],
            config.max_pending_tasks,
        )

        self.assertTrue(
            cancelled.is_set()
        )
        self.assertTrue(
            scheduler.closed
        )

    async def test_collector_normal_return_fails_closed(
        self,
    ):
        scheduler = self.fake_scheduler()

        with (
            patch(
                f"{MODULE}."
                "LiveEntryCandidateScheduler",
                return_value=scheduler,
            ),
            patch(
                f"{MODULE}."
                "_run_market_collector_with_scheduler",
                new=AsyncMock(
                    return_value=None
                ),
            ),
        ):
            with self.assertRaisesRegex(
                LiveEntryEvidenceOnlyRunnerError,
                (
                    "^"
                    + EVIDENCE_ONLY_COLLECTOR_STOPPED
                    + "$"
                ),
            ):
                await (
                    run_live_entry_evidence_only_process(
                        config=self.config(),
                        shutdown_event=asyncio.Event(),
                    )
                )

        self.assertTrue(
            scheduler.closed
        )

    async def test_independently_cancelled_collector_fails_closed(
        self,
    ):
        scheduler = self.fake_scheduler()

        async def collector(
            *,
            scheduler,
        ):
            raise asyncio.CancelledError

        with (
            patch(
                f"{MODULE}."
                "LiveEntryCandidateScheduler",
                return_value=scheduler,
            ),
            patch(
                f"{MODULE}."
                "_run_market_collector_with_scheduler",
                side_effect=collector,
            ),
        ):
            with self.assertRaisesRegex(
                LiveEntryEvidenceOnlyRunnerError,
                (
                    "^"
                    + EVIDENCE_ONLY_COLLECTOR_CANCELLED
                    + "$"
                ),
            ):
                await (
                    run_live_entry_evidence_only_process(
                        config=self.config(),
                        shutdown_event=asyncio.Event(),
                    )
                )

        self.assertTrue(
            scheduler.closed
        )

    async def test_collector_exception_propagates_and_scheduler_closes(
        self,
    ):
        scheduler = self.fake_scheduler()

        with (
            patch(
                f"{MODULE}."
                "LiveEntryCandidateScheduler",
                return_value=scheduler,
            ),
            patch(
                f"{MODULE}."
                "_run_market_collector_with_scheduler",
                new=AsyncMock(
                    side_effect=RuntimeError(
                        "collector boom"
                    )
                ),
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^collector boom$",
            ):
                await (
                    run_live_entry_evidence_only_process(
                        config=self.config(),
                        shutdown_event=asyncio.Event(),
                    )
                )

        self.assertTrue(
            scheduler.closed
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
            "/tmp/test-evidence.env"
        )

        with (
            patch(
                f"{MODULE}."
                "_install_shutdown_signal_handlers",
                new=install,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_entry_evidence_only_config",
                new=bootstrap,
            ),
            patch(
                f"{MODULE}."
                "run_live_entry_evidence_only_process",
                new=lifecycle,
            ),
        ):
            await (
                run_bootstrapped_live_entry_evidence_only_process(
                    dotenv_path=dotenv_path
                )
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

        with (
            patch(
                f"{MODULE}."
                "_install_shutdown_signal_handlers",
                side_effect=install,
            ),
            patch(
                f"{MODULE}."
                "bootstrap_live_entry_evidence_only_config",
                side_effect=bootstrap,
            ),
            patch(
                f"{MODULE}."
                "run_live_entry_evidence_only_process",
                side_effect=lifecycle,
            ),
        ):
            await (
                run_bootstrapped_live_entry_evidence_only_process()
            )

    async def test_bootstrap_failure_still_removes_handlers(
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
                "bootstrap_live_entry_evidence_only_config",
                side_effect=ValueError(
                    "bad config"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "^bad config$",
            ):
                await (
                    run_bootstrapped_live_entry_evidence_only_process()
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
            f"{MODULE}."
            "asyncio.get_running_loop",
            return_value=loop,
        ):
            with self.assertRaisesRegex(
                LiveEntryEvidenceOnlyRunnerError,
                (
                    "^"
                    + EVIDENCE_ONLY_SIGNAL_HANDLER_UNAVAILABLE
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
