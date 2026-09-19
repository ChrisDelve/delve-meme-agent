from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.execution.live_entry_candidate_pipeline import (
    ADAPTER,
    POLICY,
    LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION,
    LiveEntryCandidatePipelineResult,
    prepare_live_entry_candidate_policy_once,
    resolve_live_entry_candidate_evidence_once,
)
from src.strategies.live_entry_policy import (
    LIVE_ENTRY_POLICY_VERSION,
    LiveEntryPolicy,
)


LIVE_ENTRY_CANDIDATE_SCHEDULER_VERSION = (
    "live-entry-candidate-scheduler-v2"
)

LIVE_ENTRY_SCHEDULE_RESULT_VERSION = (
    "live-entry-schedule-result-v2"
)

SCHEDULED = "SCHEDULED"
RESOLVED = "RESOLVED"
BLOCKED = "BLOCKED"

SAME_MINT_IN_FLIGHT = "SAME_MINT_IN_FLIGHT"
SCHEDULER_AT_CAPACITY = "SCHEDULER_AT_CAPACITY"
SCHEDULER_CLOSED = "SCHEDULER_CLOSED"


@dataclass(
    frozen=True,
    slots=True,
)
class LiveEntryScheduleResult:
    result_version: str
    status: str
    reasons: tuple[str, ...]
    task: (
        asyncio.Task[
            LiveEntryCandidatePipelineResult
        ]
        | None
    )
    result: (
        LiveEntryCandidatePipelineResult | None
    ) = None

    def __post_init__(
        self,
    ) -> None:
        if (
            self.result_version
            != LIVE_ENTRY_SCHEDULE_RESULT_VERSION
        ):
            raise ValueError(
                "result_version is invalid"
            )

        if self.status not in (
            SCHEDULED,
            RESOLVED,
            BLOCKED,
        ):
            raise ValueError(
                "status is invalid"
            )

        if not isinstance(
            self.reasons,
            tuple,
        ):
            raise ValueError(
                "reasons must be tuple"
            )

        if self.status == SCHEDULED:
            if self.reasons:
                raise ValueError(
                    "SCHEDULED cannot contain reasons"
                )

            if not isinstance(
                self.task,
                asyncio.Task,
            ):
                raise ValueError(
                    "SCHEDULED requires task"
                )

            if self.result is not None:
                raise ValueError(
                    "SCHEDULED cannot contain result"
                )

            return

        if self.status == RESOLVED:
            if self.reasons:
                raise ValueError(
                    "RESOLVED cannot contain "
                    "scheduler reasons"
                )

            if self.task is not None:
                raise ValueError(
                    "RESOLVED cannot contain task"
                )

            if not isinstance(
                self.result,
                LiveEntryCandidatePipelineResult,
            ):
                raise ValueError(
                    "RESOLVED requires pipeline result"
                )

            if self.result.stage not in (
                ADAPTER,
                POLICY,
            ):
                raise ValueError(
                    "RESOLVED requires terminal "
                    "pre-evidence stage"
                )

            return

        if not self.reasons:
            raise ValueError(
                "BLOCKED requires reasons"
            )

        if self.task is not None:
            raise ValueError(
                "BLOCKED cannot contain task"
            )

        if self.result is not None:
            raise ValueError(
                "BLOCKED cannot contain result"
            )

    @property
    def scheduled(
        self,
    ) -> bool:
        return self.status == SCHEDULED

    @property
    def resolved(
        self,
    ) -> bool:
        return self.status == RESOLVED


class LiveEntryCandidateScheduler:
    """
    Explicit lifecycle owner for non-capital live-entry preparation.

    It owns only:
      - bounded total pending work;
      - bounded active concurrency;
      - same-mint in-flight exclusion;
      - task tracking;
      - exception observation;
      - shutdown cancellation.

    It owns no strategy threshold or cooldown. LiveEntryPolicy remains
    the strategy authority.

    It owns no:
      - LiveProcessOwner;
      - signer;
      - reservation;
      - BUY/SELL execution;
      - transaction construction or submission.

    Candidate age is evaluated only after the worker acquires the
    concurrency semaphore. Queue delay therefore counts against
    candidate freshness.
    """

    def __init__(
        self,
        *,
        policy: LiveEntryPolicy,
        max_concurrency: int,
        max_pending_tasks: int,
    ) -> None:
        if (
            LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION
            != "live-entry-candidate-pipeline-v1"
            or LIVE_ENTRY_POLICY_VERSION
            != "live-entry-policy-v1"
        ):
            raise RuntimeError(
                "LIVE_ENTRY_CANDIDATE_SCHEDULER_"
                "COMPONENT_VERSION_MISMATCH"
            )

        if not isinstance(
            policy,
            LiveEntryPolicy,
        ):
            raise TypeError(
                "policy must be LiveEntryPolicy"
            )

        for (
            name,
            value,
        ) in (
            (
                "max_concurrency",
                max_concurrency,
            ),
            (
                "max_pending_tasks",
                max_pending_tasks,
            ),
        ):
            if (
                isinstance(
                    value,
                    bool,
                )
                or not isinstance(
                    value,
                    int,
                )
                or value <= 0
            ):
                raise ValueError(
                    f"{name} must be positive int"
                )

        if (
            max_pending_tasks
            < max_concurrency
        ):
            raise ValueError(
                "max_pending_tasks must be "
                ">= max_concurrency"
            )

        self._loop = (
            asyncio.get_running_loop()
        )
        self._policy = policy

        self._max_concurrency = (
            max_concurrency
        )
        self._max_pending_tasks = (
            max_pending_tasks
        )

        self._semaphore = (
            asyncio.Semaphore(
                max_concurrency
            )
        )

        self._tasks: set[
            asyncio.Task[
                LiveEntryCandidatePipelineResult
            ]
        ] = set()

        self._inflight_mints: set[
            str
        ] = set()

        self._closed = False

    @property
    def closed(
        self,
    ) -> bool:
        return self._closed

    @property
    def active_task_count(
        self,
    ) -> int:
        return len(
            self._tasks
        )

    @property
    def inflight_mint_count(
        self,
    ) -> int:
        return len(
            self._inflight_mints
        )

    @property
    def max_concurrency(
        self,
    ) -> int:
        return self._max_concurrency

    @property
    def max_pending_tasks(
        self,
    ) -> int:
        return self._max_pending_tasks

    def _assert_loop(
        self,
    ) -> None:
        if (
            asyncio.get_running_loop()
            is not self._loop
        ):
            raise RuntimeError(
                "scheduler used from wrong event loop"
            )

    @staticmethod
    def _blocked(
        reason: str,
    ) -> LiveEntryScheduleResult:
        return LiveEntryScheduleResult(
            result_version=(
                LIVE_ENTRY_SCHEDULE_RESULT_VERSION
            ),
            status=BLOCKED,
            reasons=(reason,),
            task=None,
            result=None,
        )

    @staticmethod
    def _resolved(
        result: LiveEntryCandidatePipelineResult,
    ) -> LiveEntryScheduleResult:
        return LiveEntryScheduleResult(
            result_version=(
                LIVE_ENTRY_SCHEDULE_RESULT_VERSION
            ),
            status=RESOLVED,
            reasons=(),
            task=None,
            result=result,
        )

    async def _run_candidate(
        self,
        *,
        prediction: Mapping[str, Any],
        entry_signature: str,
        mint: str,
        event_user: str,
        quote_mint: str,
        slot: int | None,
        trade_timestamp: int,
        observed_at: int,
        signal_virtual_quote_reserves: int,
        signal_virtual_token_reserves: int,
    ) -> LiveEntryCandidatePipelineResult:
        async with self._semaphore:
            #
            # Deliberately measure freshness when RPC capacity is
            # actually available, not when this task was queued.
            #
            evaluated_at = int(
                time.time()
            )

            return (
                await resolve_live_entry_candidate_evidence_once(
                    prediction=prediction,
                    entry_signature=(
                        entry_signature
                    ),
                    mint=mint,
                    event_user=event_user,
                    quote_mint=quote_mint,
                    slot=slot,
                    trade_timestamp=(
                        trade_timestamp
                    ),
                    observed_at=observed_at,
                    signal_virtual_quote_reserves=(
                        signal_virtual_quote_reserves
                    ),
                    signal_virtual_token_reserves=(
                        signal_virtual_token_reserves
                    ),
                    policy=self._policy,
                    evaluated_at=evaluated_at,
                )
            )

    def _task_done(
        self,
        task: asyncio.Task[
            LiveEntryCandidatePipelineResult
        ],
        *,
        mint: str,
    ) -> None:
        self._tasks.discard(
            task
        )
        self._inflight_mints.discard(
            mint
        )

        try:
            #
            # Retrieve the result so an unexpected task exception can
            # never become "Task exception was never retrieved".
            #
            # Calling result() does not prevent another owner from
            # awaiting the returned task later.
            #
            task.result()

        except asyncio.CancelledError:
            pass

        except Exception as error:
            print(
                "⚠️ LIVE ENTRY CANDIDATE TASK ERROR | "
                f"{type(error).__name__}: "
                f"{error}"
            )

    def schedule(
        self,
        *,
        prediction: Mapping[str, Any],
        entry_signature: str,
        mint: str,
        event_user: str,
        quote_mint: str,
        slot: int | None,
        trade_timestamp: int,
        observed_at: int,
        signal_virtual_quote_reserves: int,
        signal_virtual_token_reserves: int,
    ) -> LiveEntryScheduleResult:
        self._assert_loop()

        if self._closed:
            return self._blocked(
                SCHEDULER_CLOSED
            )

        if (
            not isinstance(
                prediction,
                Mapping,
            )
        ):
            raise TypeError(
                "prediction must be a mapping"
            )

        if (
            not isinstance(
                mint,
                str,
            )
            or not mint.strip()
        ):
            raise ValueError(
                "mint is invalid"
            )

        #
        # Snapshot provenance before any evaluation or task creation.
        # Caller mutation after schedule() returns cannot alter either
        # preflight or queued evidence work.
        #
        prediction_snapshot = dict(
            prediction
        )

        #
        # Canonical synchronous preflight.
        #
        # ADAPTER/POLICY terminal outcomes consume no same-mint
        # ownership, pending capacity, semaphore slot, or evidence RPC.
        #
        preparation = (
            prepare_live_entry_candidate_policy_once(
                prediction=prediction_snapshot,
                entry_signature=entry_signature,
                mint=mint,
                event_user=event_user,
                quote_mint=quote_mint,
                slot=slot,
                trade_timestamp=trade_timestamp,
                observed_at=observed_at,
                signal_virtual_quote_reserves=(
                    signal_virtual_quote_reserves
                ),
                signal_virtual_token_reserves=(
                    signal_virtual_token_reserves
                ),
                policy=self._policy,
                evaluated_at=int(
                    time.time()
                ),
            )
        )

        if preparation.terminal_result is not None:
            return self._resolved(
                preparation.terminal_result
            )

        #
        # Only a trusted policy PASS may consume bounded scheduler
        # resources.
        #
        if mint in self._inflight_mints:
            return self._blocked(
                SAME_MINT_IN_FLIGHT
            )

        #
        # Bound TOTAL scheduled work, not merely active semaphore
        # holders. This prevents an unbounded waiting-task queue.
        #
        if (
            len(self._tasks)
            >= self._max_pending_tasks
        ):
            return self._blocked(
                SCHEDULER_AT_CAPACITY
            )

        self._inflight_mints.add(
            mint
        )

        try:
            task = self._loop.create_task(
                self._run_candidate(
                    prediction=(
                        prediction_snapshot
                    ),
                    entry_signature=(
                        entry_signature
                    ),
                    mint=mint,
                    event_user=event_user,
                    quote_mint=quote_mint,
                    slot=slot,
                    trade_timestamp=(
                        trade_timestamp
                    ),
                    observed_at=observed_at,
                    signal_virtual_quote_reserves=(
                        signal_virtual_quote_reserves
                    ),
                    signal_virtual_token_reserves=(
                        signal_virtual_token_reserves
                    ),
                )
            )

        except BaseException:
            self._inflight_mints.discard(
                mint
            )
            raise

        self._tasks.add(
            task
        )

        task.add_done_callback(
            lambda done_task, bound_mint=mint: (
                self._task_done(
                    done_task,
                    mint=bound_mint,
                )
            )
        )

        return LiveEntryScheduleResult(
            result_version=(
                LIVE_ENTRY_SCHEDULE_RESULT_VERSION
            ),
            status=SCHEDULED,
            reasons=(),
            task=task,
            result=None,
        )

    async def close(
        self,
    ) -> None:
        self._assert_loop()

        if self._closed:
            return

        #
        # Close admission before the first await so no new candidate
        # can enter while shutdown is in progress.
        #
        self._closed = True

        tasks = tuple(
            self._tasks
        )

        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(
                *tasks,
                return_exceptions=True,
            )

        #
        # Done callbacks normally remove these. Clear explicitly as a
        # final lifecycle invariant rather than depending on callback
        # scheduling order.
        #
        self._tasks.clear()
        self._inflight_mints.clear()
