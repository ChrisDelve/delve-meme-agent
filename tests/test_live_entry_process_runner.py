from __future__ import annotations

import asyncio
from pathlib import Path
import unittest
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
)

from src.execution.live_buy_execution_config import (
    LIVE_BUY_EXECUTION_CONFIG_VERSION,
    LiveBuyExecutionConfig,
)
from src.execution.live_entry_evidence_only_config import (
    LiveEntryEvidenceOnlyConfig,
)
from src.execution.live_entry_process_runner import (
    LIVE_ENTRY_PROCESS_COLLECTOR_STOPPED,
    LIVE_ENTRY_PROCESS_MAILBOX_FAILED,
    LIVE_ENTRY_PROCESS_RECOVERY_STOPPED,
    LIVE_ENTRY_PROCESS_RUNNER_VERSION,
    LiveEntryProcessRunnerError,
    _run_live_entry_capital_consumer,
    run_live_entry_process,
)
from src.execution.live_entry_result_mailbox import (
    MAILBOX_AT_CAPACITY,
)
from src.execution.live_operating_config import (
    LiveOperatingConfig,
)


MODULE = (
    "src.execution.live_entry_process_runner"
)

WALLET = (
    "So11111111111111111111111111111111111111112"
)


class LiveEntryProcessRunnerTests(
    unittest.IsolatedAsyncioTestCase
):
    @staticmethod
    def operating_config(
    ) -> LiveOperatingConfig:
        return LiveOperatingConfig(
            db_path=Path(
                "/tmp/"
                "delve-live-entry-process-test.db"
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

    @staticmethod
    def evidence_config(
    ) -> LiveEntryEvidenceOnlyConfig:
        return LiveEntryEvidenceOnlyConfig(
            min_probability_2x_15m=0.40,
            max_candidate_age_seconds=5,
            max_concurrency=1,
            max_pending_tasks=4,
        )

    @staticmethod
    def execution_config(
    ) -> LiveBuyExecutionConfig:
        return LiveBuyExecutionConfig(
            config_version=(
                LIVE_BUY_EXECUTION_CONFIG_VERSION
            ),
            wallet_pubkey=WALLET,
            protected_cash_lamports=(
                50_000_000
            ),
            buy_slippage_bps=300,
            buy_base_network_fee_lamports=(
                5_000
            ),
            buy_priority_fee_lamports=(
                10_000
            ),
            buy_rent_lamports=(
                2_039_280
            ),
            exit_slippage_bps=300,
            exit_base_network_fee_lamports=(
                5_000
            ),
            exit_priority_fee_lamports=(
                10_000
            ),
            reservation_ttl_seconds=30.0,
            max_authorization_age_seconds=(
                10.0
            ),
            compute_unit_limit=300_000,
        )

    @staticmethod
    def scheduler():
        scheduler = Mock()

        scheduler.closed = False

        async def close():
            scheduler.closed = True

        scheduler.close = AsyncMock(
            side_effect=close
        )

        return scheduler

    @staticmethod
    async def forever():
        await asyncio.Event().wait()

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_ENTRY_PROCESS_RUNNER_VERSION,
            "live-entry-process-runner-v1",
        )

    async def test_preexisting_shutdown_acquires_no_process_authority(
        self,
    ):
        shutdown_event = asyncio.Event()
        shutdown_event.set()

        scheduler_constructor = Mock()
        mailbox_constructor = Mock()
        owner_constructor = Mock()

        with (
            patch(
                f"{MODULE}."
                "LiveEntryCandidateScheduler",
                new=scheduler_constructor,
            ),
            patch(
                f"{MODULE}."
                "LiveEntryResultMailbox",
                new=mailbox_constructor,
            ),
            patch(
                f"{MODULE}.LiveProcessOwner",
                new=owner_constructor,
            ),
        ):
            await run_live_entry_process(
                operating_config=(
                    self.operating_config()
                ),
                evidence_config=(
                    self.evidence_config()
                ),
                execution_config=(
                    self.execution_config()
                ),
                shutdown_event=shutdown_event,
            )

        scheduler_constructor.assert_not_called()
        mailbox_constructor.assert_not_called()
        owner_constructor.assert_not_called()

    async def test_exact_composition_and_orderly_shutdown(
        self,
    ):
        operating = self.operating_config()
        evidence = self.evidence_config()
        execution = self.execution_config()

        scheduler = self.scheduler()
        mailbox = Mock()
        mailbox.wait_failed = AsyncMock(
            side_effect=self.forever
        )
        owner = Mock()

        owner.run_recovery_service = AsyncMock(
            side_effect=self.forever
        )
        owner.close = Mock()

        scheduler_constructor = Mock(
            return_value=scheduler
        )

        mailbox_constructor = Mock(
            return_value=mailbox
        )

        owner_constructor = Mock(
            return_value=owner
        )

        collector_started = asyncio.Event()

        async def collector_stub(
            *,
            scheduler,
            mailbox,
        ):
            collector_started.set()
            await asyncio.Event().wait()

        async def consumer_stub(
            *,
            owner,
            mailbox,
            execution_config,
            stop_event,
        ):
            await stop_event.wait()

        collector = AsyncMock(
            side_effect=collector_stub
        )

        consumer = AsyncMock(
            side_effect=consumer_stub
        )

        shutdown_event = asyncio.Event()

        with (
            patch(
                f"{MODULE}."
                "LiveEntryCandidateScheduler",
                new=scheduler_constructor,
            ),
            patch(
                f"{MODULE}."
                "LiveEntryResultMailbox",
                new=mailbox_constructor,
            ),
            patch(
                f"{MODULE}.LiveProcessOwner",
                new=owner_constructor,
            ),
            patch(
                f"{MODULE}."
                "_run_market_collector_with_live_entry",
                new=collector,
            ),
            patch(
                f"{MODULE}."
                "_run_live_entry_capital_consumer",
                new=consumer,
            ),
        ):
            runner = asyncio.create_task(
                run_live_entry_process(
                    operating_config=operating,
                    evidence_config=evidence,
                    execution_config=execution,
                    shutdown_event=shutdown_event,
                )
            )

            await collector_started.wait()

            shutdown_event.set()

            await runner

        scheduler_constructor.assert_called_once_with(
            policy=evidence.policy,
            max_concurrency=(
                evidence.max_concurrency
            ),
            max_pending_tasks=(
                evidence.max_pending_tasks
            ),
        )

        mailbox_constructor.assert_called_once_with(
            max_pending_results=(
                evidence.max_pending_tasks
            )
        )

        owner_constructor.assert_called_once_with(
            config=operating
        )

        collector.assert_awaited_once_with(
            scheduler=scheduler,
            mailbox=mailbox,
        )

        self.assertEqual(
            consumer.await_count,
            1,
        )

        consumer_kwargs = (
            consumer.await_args.kwargs
        )

        self.assertIs(
            consumer_kwargs["owner"],
            owner,
        )
        self.assertIs(
            consumer_kwargs["mailbox"],
            mailbox,
        )
        self.assertIs(
            consumer_kwargs[
                "execution_config"
            ],
            execution,
        )

        self.assertTrue(
            scheduler.closed
        )

        owner.close.assert_called_once_with()

    async def test_consumer_stop_prevents_new_handoff(
        self,
    ):
        owner = Mock()
        execution = Mock()

        receive_gate = asyncio.Event()

        mailbox = Mock()
        mailbox.receive = AsyncMock(
            side_effect=receive_gate.wait
        )

        stop_event = asyncio.Event()

        handoff = AsyncMock()

        with patch(
            f"{MODULE}."
            "run_live_entry_capital_handoff_once",
            new=handoff,
        ):
            consumer = asyncio.create_task(
                _run_live_entry_capital_consumer(
                    owner=owner,
                    mailbox=mailbox,
                    execution_config=execution,
                    stop_event=stop_event,
                )
            )

            await asyncio.sleep(0)

            stop_event.set()

            await consumer

        handoff.assert_not_awaited()

    async def test_active_handoff_is_not_cancelled_by_stop(
        self,
    ):
        owner = Mock()
        execution = Mock()
        pipeline_result = object()

        mailbox = Mock()
        mailbox.receive = AsyncMock(
            return_value=pipeline_result
        )

        stop_event = asyncio.Event()
        handoff_started = asyncio.Event()
        release_handoff = asyncio.Event()

        async def handoff_stub(
            *,
            owner,
            pipeline_result,
            execution_config,
        ):
            handoff_started.set()

            await release_handoff.wait()

            return object()

        handoff = AsyncMock(
            side_effect=handoff_stub
        )

        with patch(
            f"{MODULE}."
            "run_live_entry_capital_handoff_once",
            new=handoff,
        ):
            consumer = asyncio.create_task(
                _run_live_entry_capital_consumer(
                    owner=owner,
                    mailbox=mailbox,
                    execution_config=execution,
                    stop_event=stop_event,
                )
            )

            await handoff_started.wait()

            stop_event.set()

            await asyncio.sleep(0)

            self.assertFalse(
                consumer.done()
            )

            release_handoff.set()

            await consumer

        handoff.assert_awaited_once_with(
            owner=owner,
            pipeline_result=pipeline_result,
            execution_config=execution,
        )

    async def test_recovery_exception_propagates_and_closes_owner(
        self,
    ):
        scheduler = self.scheduler()
        mailbox = Mock()
        mailbox.wait_failed = AsyncMock(
            side_effect=self.forever
        )
        owner = Mock()

        owner.run_recovery_service = AsyncMock(
            side_effect=RuntimeError(
                "recovery boom"
            )
        )
        owner.close = Mock()

        async def collector_stub(
            *,
            scheduler,
            mailbox,
        ):
            await asyncio.Event().wait()

        async def consumer_stub(
            *,
            owner,
            mailbox,
            execution_config,
            stop_event,
        ):
            await stop_event.wait()

        with (
            patch(
                f"{MODULE}."
                "LiveEntryCandidateScheduler",
                return_value=scheduler,
            ),
            patch(
                f"{MODULE}."
                "LiveEntryResultMailbox",
                return_value=mailbox,
            ),
            patch(
                f"{MODULE}.LiveProcessOwner",
                return_value=owner,
            ),
            patch(
                f"{MODULE}."
                "_run_market_collector_with_live_entry",
                new=AsyncMock(
                    side_effect=collector_stub
                ),
            ),
            patch(
                f"{MODULE}."
                "_run_live_entry_capital_consumer",
                new=AsyncMock(
                    side_effect=consumer_stub
                ),
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^recovery boom$",
            ):
                await run_live_entry_process(
                    operating_config=(
                        self.operating_config()
                    ),
                    evidence_config=(
                        self.evidence_config()
                    ),
                    execution_config=(
                        self.execution_config()
                    ),
                    shutdown_event=(
                        asyncio.Event()
                    ),
                )

        self.assertTrue(
            scheduler.closed
        )

        owner.close.assert_called_once_with()

    async def test_normal_collector_return_is_process_fatal(
        self,
    ):
        scheduler = self.scheduler()
        mailbox = Mock()
        mailbox.wait_failed = AsyncMock(
            side_effect=self.forever
        )
        owner = Mock()

        owner.run_recovery_service = AsyncMock(
            side_effect=self.forever
        )
        owner.close = Mock()

        async def consumer_stub(
            *,
            owner,
            mailbox,
            execution_config,
            stop_event,
        ):
            await stop_event.wait()

        with (
            patch(
                f"{MODULE}."
                "LiveEntryCandidateScheduler",
                return_value=scheduler,
            ),
            patch(
                f"{MODULE}."
                "LiveEntryResultMailbox",
                return_value=mailbox,
            ),
            patch(
                f"{MODULE}.LiveProcessOwner",
                return_value=owner,
            ),
            patch(
                f"{MODULE}."
                "_run_market_collector_with_live_entry",
                new=AsyncMock(
                    return_value=None
                ),
            ),
            patch(
                f"{MODULE}."
                "_run_live_entry_capital_consumer",
                new=AsyncMock(
                    side_effect=consumer_stub
                ),
            ),
        ):
            with self.assertRaisesRegex(
                LiveEntryProcessRunnerError,
                (
                    "^"
                    + LIVE_ENTRY_PROCESS_COLLECTOR_STOPPED
                    + "$"
                ),
            ):
                await run_live_entry_process(
                    operating_config=(
                        self.operating_config()
                    ),
                    evidence_config=(
                        self.evidence_config()
                    ),
                    execution_config=(
                        self.execution_config()
                    ),
                    shutdown_event=(
                        asyncio.Event()
                    ),
                )

        owner.close.assert_called_once_with()

    async def test_mailbox_failure_is_process_fatal(
        self,
    ):
        scheduler = self.scheduler()

        mailbox = Mock()
        mailbox.failure_reason = (
            MAILBOX_AT_CAPACITY
        )
        mailbox.wait_failed = AsyncMock(
            return_value=None
        )

        owner = Mock()
        owner.run_recovery_service = AsyncMock(
            side_effect=self.forever
        )
        owner.close = Mock()

        async def collector_stub(
            *,
            scheduler,
            mailbox,
        ):
            await asyncio.Event().wait()

        async def consumer_stub(
            *,
            owner,
            mailbox,
            execution_config,
            stop_event,
        ):
            await stop_event.wait()

        with (
            patch(
                f"{MODULE}."
                "LiveEntryCandidateScheduler",
                return_value=scheduler,
            ),
            patch(
                f"{MODULE}."
                "LiveEntryResultMailbox",
                return_value=mailbox,
            ),
            patch(
                f"{MODULE}.LiveProcessOwner",
                return_value=owner,
            ),
            patch(
                f"{MODULE}."
                "_run_market_collector_with_live_entry",
                new=AsyncMock(
                    side_effect=collector_stub
                ),
            ),
            patch(
                f"{MODULE}."
                "_run_live_entry_capital_consumer",
                new=AsyncMock(
                    side_effect=consumer_stub
                ),
            ),
        ):
            with self.assertRaisesRegex(
                LiveEntryProcessRunnerError,
                (
                    "^"
                    + LIVE_ENTRY_PROCESS_MAILBOX_FAILED
                    + ":"
                    + MAILBOX_AT_CAPACITY
                    + "$"
                ),
            ):
                await run_live_entry_process(
                    operating_config=(
                        self.operating_config()
                    ),
                    evidence_config=(
                        self.evidence_config()
                    ),
                    execution_config=(
                        self.execution_config()
                    ),
                    shutdown_event=(
                        asyncio.Event()
                    ),
                )

        owner.close.assert_called_once_with()

    async def test_consumer_exception_propagates_and_closes_owner(
        self,
    ):
        scheduler = self.scheduler()
        mailbox = Mock()

        mailbox.wait_failed = AsyncMock(
            side_effect=self.forever
        )

        owner = Mock()
        owner.run_recovery_service = AsyncMock(
            side_effect=self.forever
        )
        owner.close = Mock()

        async def collector_stub(
            *,
            scheduler,
            mailbox,
        ):
            await asyncio.Event().wait()

        consumer = AsyncMock(
            side_effect=RuntimeError(
                "handoff boom"
            )
        )

        with (
            patch(
                f"{MODULE}."
                "LiveEntryCandidateScheduler",
                return_value=scheduler,
            ),
            patch(
                f"{MODULE}."
                "LiveEntryResultMailbox",
                return_value=mailbox,
            ),
            patch(
                f"{MODULE}.LiveProcessOwner",
                return_value=owner,
            ),
            patch(
                f"{MODULE}."
                "_run_market_collector_with_live_entry",
                new=AsyncMock(
                    side_effect=collector_stub
                ),
            ),
            patch(
                f"{MODULE}."
                "_run_live_entry_capital_consumer",
                new=consumer,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^handoff boom$",
            ):
                await run_live_entry_process(
                    operating_config=(
                        self.operating_config()
                    ),
                    evidence_config=(
                        self.evidence_config()
                    ),
                    execution_config=(
                        self.execution_config()
                    ),
                    shutdown_event=(
                        asyncio.Event()
                    ),
                )

        owner.close.assert_called_once_with()


    async def test_runner_cancellation_waits_for_active_consumer_before_close(
        self,
    ):
        scheduler = self.scheduler()

        mailbox = Mock()
        mailbox.wait_failed = AsyncMock(
            side_effect=self.forever
        )

        owner = Mock()
        owner.run_recovery_service = AsyncMock(
            side_effect=self.forever
        )

        lifecycle = []

        def close_owner():
            lifecycle.append(
                "owner-close"
            )

        owner.close = Mock(
            side_effect=close_owner
        )

        consumer_started = asyncio.Event()
        release_consumer = asyncio.Event()

        async def collector_stub(
            *,
            scheduler,
            mailbox,
        ):
            await asyncio.Event().wait()

        async def consumer_stub(
            *,
            owner,
            mailbox,
            execution_config,
            stop_event,
        ):
            consumer_started.set()

            await release_consumer.wait()

            lifecycle.append(
                "consumer-return"
            )

        with (
            patch(
                f"{MODULE}."
                "LiveEntryCandidateScheduler",
                return_value=scheduler,
            ),
            patch(
                f"{MODULE}."
                "LiveEntryResultMailbox",
                return_value=mailbox,
            ),
            patch(
                f"{MODULE}.LiveProcessOwner",
                return_value=owner,
            ),
            patch(
                f"{MODULE}."
                "_run_market_collector_with_live_entry",
                new=AsyncMock(
                    side_effect=collector_stub
                ),
            ),
            patch(
                f"{MODULE}."
                "_run_live_entry_capital_consumer",
                new=AsyncMock(
                    side_effect=consumer_stub
                ),
            ),
        ):
            runner = asyncio.create_task(
                run_live_entry_process(
                    operating_config=(
                        self.operating_config()
                    ),
                    evidence_config=(
                        self.evidence_config()
                    ),
                    execution_config=(
                        self.execution_config()
                    ),
                    shutdown_event=(
                        asyncio.Event()
                    ),
                )
            )

            await consumer_started.wait()

            runner.cancel()

            await asyncio.sleep(0)

            self.assertFalse(
                runner.done()
            )

            owner.close.assert_not_called()

            release_consumer.set()

            with self.assertRaises(
                asyncio.CancelledError
            ):
                await runner

        self.assertEqual(
            lifecycle,
            [
                "consumer-return",
                "owner-close",
            ],
        )

        owner.close.assert_called_once_with()

    async def test_recovery_failure_wins_over_simultaneous_shutdown(
        self,
    ):
        scheduler = self.scheduler()

        mailbox = Mock()
        mailbox.wait_failed = AsyncMock(
            side_effect=self.forever
        )

        shutdown_event = asyncio.Event()

        async def recovery_stub():
            shutdown_event.set()

        owner = Mock()
        owner.run_recovery_service = AsyncMock(
            side_effect=recovery_stub
        )
        owner.close = Mock()

        async def collector_stub(
            *,
            scheduler,
            mailbox,
        ):
            await asyncio.Event().wait()

        async def consumer_stub(
            *,
            owner,
            mailbox,
            execution_config,
            stop_event,
        ):
            await stop_event.wait()

        with (
            patch(
                f"{MODULE}."
                "LiveEntryCandidateScheduler",
                return_value=scheduler,
            ),
            patch(
                f"{MODULE}."
                "LiveEntryResultMailbox",
                return_value=mailbox,
            ),
            patch(
                f"{MODULE}.LiveProcessOwner",
                return_value=owner,
            ),
            patch(
                f"{MODULE}."
                "_run_market_collector_with_live_entry",
                new=AsyncMock(
                    side_effect=collector_stub
                ),
            ),
            patch(
                f"{MODULE}."
                "_run_live_entry_capital_consumer",
                new=AsyncMock(
                    side_effect=consumer_stub
                ),
            ),
        ):
            with self.assertRaisesRegex(
                LiveEntryProcessRunnerError,
                (
                    "^"
                    + LIVE_ENTRY_PROCESS_RECOVERY_STOPPED
                    + "$"
                ),
            ):
                await run_live_entry_process(
                    operating_config=(
                        self.operating_config()
                    ),
                    evidence_config=(
                        self.evidence_config()
                    ),
                    execution_config=(
                        self.execution_config()
                    ),
                    shutdown_event=shutdown_event,
                )

        self.assertTrue(
            shutdown_event.is_set()
        )

        owner.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
