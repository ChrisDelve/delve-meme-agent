from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import (
    AsyncMock,
    patch,
)

from src.execution.live_entry_candidate_scheduler import (
    BLOCKED,
    LIVE_ENTRY_CANDIDATE_SCHEDULER_VERSION,
    LIVE_ENTRY_SCHEDULE_RESULT_VERSION,
    SAME_MINT_IN_FLIGHT,
    SCHEDULED,
    SCHEDULER_AT_CAPACITY,
    SCHEDULER_CLOSED,
    LiveEntryCandidateScheduler,
)
from src.strategies.live_entry_policy import (
    LiveEntryPolicy,
)


class LiveEntryCandidateSchedulerTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.policy = LiveEntryPolicy(
            min_probability_2x_15m=0.40,
            max_candidate_age_seconds=5,
        )

        self.prediction = {
            "entry_signature":
                "signature-1",
        }

        self.event = {
            "entry_signature":
                "signature-1",
            "mint": "mint-1",
            "event_user": "wallet-1",
            "quote_mint":
                "11111111111111111111111111111111",
            "slot": 123,
            "trade_timestamp": 1_000,
            "observed_at": 1_001,
            "signal_virtual_quote_reserves":
                30_000_000_000,
            "signal_virtual_token_reserves":
                1_000_000_000_000,
        }

    def scheduler(
        self,
        *,
        max_concurrency=1,
        max_pending_tasks=4,
    ):
        return LiveEntryCandidateScheduler(
            policy=self.policy,
            max_concurrency=max_concurrency,
            max_pending_tasks=max_pending_tasks,
        )

    def schedule(
        self,
        scheduler,
        *,
        prediction=None,
        **updates,
    ):
        event = dict(
            self.event
        )
        event.update(
            updates
        )

        return scheduler.schedule(
            prediction=(
                dict(self.prediction)
                if prediction is None
                else prediction
            ),
            **event,
        )

    async def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_ENTRY_CANDIDATE_SCHEDULER_VERSION,
            "live-entry-candidate-scheduler-v1",
        )
        self.assertEqual(
            LIVE_ENTRY_SCHEDULE_RESULT_VERSION,
            "live-entry-schedule-result-v1",
        )

    async def test_policy_type_is_required(
        self,
    ):
        with self.assertRaisesRegex(
            TypeError,
            "^policy must be LiveEntryPolicy$",
        ):
            LiveEntryCandidateScheduler(
                policy=object(),
                max_concurrency=1,
                max_pending_tasks=1,
            )

    async def test_limits_are_strict_positive_ints(
        self,
    ):
        for field in (
            "max_concurrency",
            "max_pending_tasks",
        ):
            for invalid in (
                0,
                -1,
                True,
                1.5,
                None,
            ):
                with self.subTest(
                    field=field,
                    invalid=invalid,
                ):
                    values = {
                        "max_concurrency": 1,
                        "max_pending_tasks": 1,
                    }
                    values[field] = invalid

                    with self.assertRaisesRegex(
                        ValueError,
                        (
                            "^"
                            + field
                            + " must be positive int$"
                        ),
                    ):
                        LiveEntryCandidateScheduler(
                            policy=self.policy,
                            **values,
                        )

    async def test_pending_limit_cannot_be_below_concurrency(
        self,
    ):
        with self.assertRaisesRegex(
            ValueError,
            (
                "^max_pending_tasks must be "
                ">= max_concurrency$"
            ),
        ):
            self.scheduler(
                max_concurrency=2,
                max_pending_tasks=1,
            )

    async def test_schedule_runs_pipeline_and_returns_task(
        self,
    ):
        scheduler = self.scheduler()

        pipeline = AsyncMock(
            return_value="result"
        )

        with (
            patch(
                "src.execution."
                "live_entry_candidate_scheduler."
                "resolve_live_entry_candidate_evidence_once",
                pipeline,
            ),
            patch(
                "src.execution."
                "live_entry_candidate_scheduler."
                "time.time",
                return_value=1_004,
            ),
        ):
            scheduled = self.schedule(
                scheduler
            )
            result = await scheduled.task

        self.assertEqual(
            scheduled.status,
            SCHEDULED,
        )
        self.assertTrue(
            scheduled.scheduled
        )
        self.assertEqual(
            scheduled.reasons,
            (),
        )
        self.assertEqual(
            result,
            "result",
        )

        pipeline.assert_awaited_once_with(
            prediction=self.prediction,
            entry_signature="signature-1",
            mint="mint-1",
            event_user="wallet-1",
            quote_mint=(
                "11111111111111111111111111111111"
            ),
            slot=123,
            trade_timestamp=1_000,
            observed_at=1_001,
            signal_virtual_quote_reserves=(
                30_000_000_000
            ),
            signal_virtual_token_reserves=(
                1_000_000_000_000
            ),
            policy=self.policy,
            evaluated_at=1_004,
        )

        await scheduler.close()

    async def test_prediction_is_snapshotted_before_task_runs(
        self,
    ):
        scheduler = self.scheduler()

        captured = {}

        async def pipeline(
            **kwargs,
        ):
            captured["prediction"] = (
                kwargs["prediction"]
            )
            return "result"

        prediction = dict(
            self.prediction
        )

        with patch(
            "src.execution."
            "live_entry_candidate_scheduler."
            "resolve_live_entry_candidate_evidence_once",
            side_effect=pipeline,
        ):
            scheduled = self.schedule(
                scheduler,
                prediction=prediction,
            )

            prediction[
                "entry_signature"
            ] = "mutated"

            await scheduled.task

        self.assertEqual(
            captured["prediction"],
            self.prediction,
        )

        await scheduler.close()

    async def test_same_mint_is_blocked_while_inflight(
        self,
    ):
        scheduler = self.scheduler()

        started = asyncio.Event()
        release = asyncio.Event()

        async def pipeline(
            **kwargs,
        ):
            started.set()
            await release.wait()
            return "result"

        with patch(
            "src.execution."
            "live_entry_candidate_scheduler."
            "resolve_live_entry_candidate_evidence_once",
            side_effect=pipeline,
        ):
            first = self.schedule(
                scheduler
            )

            await started.wait()

            second = self.schedule(
                scheduler
            )

            self.assertEqual(
                second.status,
                BLOCKED,
            )
            self.assertEqual(
                second.reasons,
                (
                    SAME_MINT_IN_FLIGHT,
                ),
            )
            self.assertIsNone(
                second.task
            )

            release.set()
            await first.task

        await asyncio.sleep(0)

        self.assertEqual(
            scheduler.active_task_count,
            0,
        )
        self.assertEqual(
            scheduler.inflight_mint_count,
            0,
        )

        await scheduler.close()

    async def test_same_mint_can_run_again_immediately_after_completion(
        self,
    ):
        scheduler = self.scheduler()

        with patch(
            "src.execution."
            "live_entry_candidate_scheduler."
            "resolve_live_entry_candidate_evidence_once",
            new_callable=AsyncMock,
            return_value="result",
        ):
            first = self.schedule(
                scheduler
            )
            await first.task
            await asyncio.sleep(0)

            second = self.schedule(
                scheduler
            )

            self.assertTrue(
                second.scheduled
            )

            await second.task

        await scheduler.close()

    async def test_total_pending_tasks_are_bounded(
        self,
    ):
        scheduler = self.scheduler(
            max_concurrency=1,
            max_pending_tasks=1,
        )

        started = asyncio.Event()
        release = asyncio.Event()

        async def pipeline(
            **kwargs,
        ):
            started.set()
            await release.wait()
            return "result"

        with patch(
            "src.execution."
            "live_entry_candidate_scheduler."
            "resolve_live_entry_candidate_evidence_once",
            side_effect=pipeline,
        ):
            first = self.schedule(
                scheduler
            )

            await started.wait()

            second = self.schedule(
                scheduler,
                entry_signature="signature-2",
                mint="mint-2",
            )

            self.assertEqual(
                second.status,
                BLOCKED,
            )
            self.assertEqual(
                second.reasons,
                (
                    SCHEDULER_AT_CAPACITY,
                ),
            )

            release.set()
            await first.task

        await scheduler.close()

    async def test_pending_limit_counts_running_and_queued_tasks(
        self,
    ):
        scheduler = self.scheduler(
            max_concurrency=1,
            max_pending_tasks=2,
        )

        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def pipeline(
            **kwargs,
        ):
            if kwargs["mint"] == "mint-1":
                first_started.set()
                await release_first.wait()

            return "result"

        with patch(
            "src.execution."
            "live_entry_candidate_scheduler."
            "resolve_live_entry_candidate_evidence_once",
            side_effect=pipeline,
        ):
            first = self.schedule(
                scheduler,
                mint="mint-1",
            )

            await first_started.wait()

            second = self.schedule(
                scheduler,
                entry_signature="signature-2",
                mint="mint-2",
            )

            self.assertTrue(
                second.scheduled
            )
            self.assertEqual(
                scheduler.active_task_count,
                2,
            )

            third = self.schedule(
                scheduler,
                entry_signature="signature-3",
                mint="mint-3",
            )

            self.assertEqual(
                third.status,
                BLOCKED,
            )
            self.assertEqual(
                third.reasons,
                (
                    SCHEDULER_AT_CAPACITY,
                ),
            )

            release_first.set()

            await asyncio.gather(
                first.task,
                second.task,
            )

        await scheduler.close()

    async def test_same_mint_exclusion_includes_queued_task(
        self,
    ):
        scheduler = self.scheduler(
            max_concurrency=1,
            max_pending_tasks=3,
        )

        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def pipeline(
            **kwargs,
        ):
            if kwargs["mint"] == "mint-1":
                first_started.set()
                await release_first.wait()

            return "result"

        with patch(
            "src.execution."
            "live_entry_candidate_scheduler."
            "resolve_live_entry_candidate_evidence_once",
            side_effect=pipeline,
        ):
            first = self.schedule(
                scheduler,
                mint="mint-1",
            )

            await first_started.wait()

            queued = self.schedule(
                scheduler,
                entry_signature="signature-2",
                mint="mint-2",
            )

            self.assertTrue(
                queued.scheduled
            )

            duplicate = self.schedule(
                scheduler,
                entry_signature="signature-3",
                mint="mint-2",
            )

            self.assertEqual(
                duplicate.status,
                BLOCKED,
            )
            self.assertEqual(
                duplicate.reasons,
                (
                    SAME_MINT_IN_FLIGHT,
                ),
            )

            release_first.set()

            await asyncio.gather(
                first.task,
                queued.task,
            )

        await scheduler.close()

    async def test_semaphore_bounds_active_pipeline_concurrency(
        self,
    ):
        scheduler = self.scheduler(
            max_concurrency=2,
            max_pending_tasks=3,
        )

        release = asyncio.Event()
        two_started = asyncio.Event()

        active = 0
        maximum_active = 0

        async def pipeline(
            **kwargs,
        ):
            nonlocal active
            nonlocal maximum_active

            active += 1
            maximum_active = max(
                maximum_active,
                active,
            )

            if active == 2:
                two_started.set()

            try:
                await release.wait()
                return "result"
            finally:
                active -= 1

        with patch(
            "src.execution."
            "live_entry_candidate_scheduler."
            "resolve_live_entry_candidate_evidence_once",
            side_effect=pipeline,
        ):
            first = self.schedule(
                scheduler,
                mint="mint-1",
            )
            second = self.schedule(
                scheduler,
                entry_signature="signature-2",
                mint="mint-2",
            )
            third = self.schedule(
                scheduler,
                entry_signature="signature-3",
                mint="mint-3",
            )

            await two_started.wait()
            await asyncio.sleep(0)

            self.assertEqual(
                maximum_active,
                2,
            )
            self.assertEqual(
                scheduler.active_task_count,
                3,
            )

            release.set()

            await asyncio.gather(
                first.task,
                second.task,
                third.task,
            )

        await scheduler.close()

    async def test_queued_candidate_age_is_measured_when_worker_starts(
        self,
    ):
        scheduler = self.scheduler(
            max_concurrency=1,
            max_pending_tasks=2,
        )

        first_started = asyncio.Event()
        second_started = asyncio.Event()
        release_first = asyncio.Event()

        evaluated = []

        async def pipeline(
            **kwargs,
        ):
            evaluated.append(
                kwargs["evaluated_at"]
            )

            if (
                kwargs["mint"]
                == "mint-1"
            ):
                first_started.set()
                await release_first.wait()
            else:
                second_started.set()

            return "result"

        with (
            patch(
                "src.execution."
                "live_entry_candidate_scheduler."
                "resolve_live_entry_candidate_evidence_once",
                side_effect=pipeline,
            ),
            patch(
                "src.execution."
                "live_entry_candidate_scheduler."
                "time.time",
                side_effect=(
                    1_004,
                    1_009,
                ),
            ),
        ):
            first = self.schedule(
                scheduler,
                mint="mint-1",
            )

            second = self.schedule(
                scheduler,
                entry_signature="signature-2",
                mint="mint-2",
            )

            await first_started.wait()
            await asyncio.sleep(0)

            self.assertEqual(
                evaluated,
                [1_004],
            )

            release_first.set()

            await second_started.wait()

            self.assertEqual(
                evaluated,
                [
                    1_004,
                    1_009,
                ],
            )

            await asyncio.gather(
                first.task,
                second.task,
            )

        await scheduler.close()

    async def test_task_exception_is_observed_and_mint_released(
        self,
    ):
        scheduler = self.scheduler()

        pipeline = AsyncMock(
            side_effect=(
                RuntimeError("boom"),
                "result",
            )
        )

        with (
            patch(
                "src.execution."
                "live_entry_candidate_scheduler."
                "resolve_live_entry_candidate_evidence_once",
                pipeline,
            ),
            patch(
                "builtins.print"
            ) as printer,
        ):
            first = self.schedule(
                scheduler
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "^boom$",
            ):
                await first.task

            await asyncio.sleep(0)

            printer.assert_called()

            second = self.schedule(
                scheduler
            )

            self.assertTrue(
                second.scheduled
            )

            await second.task

        await scheduler.close()

    async def test_close_cancels_all_work_and_blocks_future_schedule(
        self,
    ):
        scheduler = self.scheduler(
            max_concurrency=1,
            max_pending_tasks=2,
        )

        started = asyncio.Event()

        async def pipeline(
            **kwargs,
        ):
            started.set()
            await asyncio.Event().wait()

        with patch(
            "src.execution."
            "live_entry_candidate_scheduler."
            "resolve_live_entry_candidate_evidence_once",
            side_effect=pipeline,
        ):
            first = self.schedule(
                scheduler
            )
            second = self.schedule(
                scheduler,
                entry_signature="signature-2",
                mint="mint-2",
            )

            await started.wait()

            await scheduler.close()

        self.assertTrue(
            scheduler.closed
        )
        self.assertEqual(
            scheduler.active_task_count,
            0,
        )
        self.assertEqual(
            scheduler.inflight_mint_count,
            0,
        )
        self.assertTrue(
            first.task.cancelled()
        )
        self.assertTrue(
            second.task.cancelled()
        )

        blocked = self.schedule(
            scheduler,
            entry_signature="signature-3",
            mint="mint-3",
        )

        self.assertEqual(
            blocked.status,
            BLOCKED,
        )
        self.assertEqual(
            blocked.reasons,
            (
                SCHEDULER_CLOSED,
            ),
        )

    async def test_close_is_idempotent(
        self,
    ):
        scheduler = self.scheduler()

        await scheduler.close()
        await scheduler.close()

        self.assertTrue(
            scheduler.closed
        )

    async def test_schedule_result_is_frozen(
        self,
    ):
        scheduler = self.scheduler()

        with patch(
            "src.execution."
            "live_entry_candidate_scheduler."
            "resolve_live_entry_candidate_evidence_once",
            new_callable=AsyncMock,
            return_value="result",
        ):
            scheduled = self.schedule(
                scheduler
            )

            with self.assertRaises(
                FrozenInstanceError
            ):
                scheduled.status = BLOCKED

            await scheduled.task

        await scheduler.close()

    async def test_mint_must_be_nonempty_text(
        self,
    ):
        scheduler = self.scheduler()

        for invalid in (
            "",
            "   ",
            None,
            123,
        ):
            with self.subTest(
                invalid=invalid
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "^mint is invalid$",
                ):
                    self.schedule(
                        scheduler,
                        mint=invalid,
                    )

        await scheduler.close()


if __name__ == "__main__":
    unittest.main()
