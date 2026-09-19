from __future__ import annotations

import asyncio
from typing import Any

from src.execution.live_entry_candidate_pipeline import (
    LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION,
    LiveEntryCandidatePipelineResult,
)


LIVE_ENTRY_RESULT_MAILBOX_VERSION = (
    "live-entry-result-mailbox-v1"
)

PUBLISHED = "PUBLISHED"
IGNORED = "IGNORED"
FAILED = "FAILED"

MAILBOX_AT_CAPACITY = (
    "LIVE_ENTRY_RESULT_MAILBOX_AT_CAPACITY"
)

MAILBOX_RESULT_CONTRACT_INVALID = (
    "LIVE_ENTRY_RESULT_MAILBOX_RESULT_CONTRACT_INVALID"
)


class LiveEntryResultMailboxError(
    RuntimeError
):
    pass


class LiveEntryResultMailbox:
    """
    Passive bounded transport for canonical evidence-ready pipeline
    results.

    The mailbox deliberately owns no:
      - strategy policy;
      - candidate evaluation;
      - evidence RPC;
      - LiveProcessOwner;
      - signer;
      - reservation;
      - BUY/SELL authority;
      - capital handoff invocation;
      - transaction construction/signing/submission;
      - SQLite access;
      - retry loop;
      - background task.

    publish() is synchronous and non-blocking.

    Non-evidence-ready pipeline results are IGNORED.

    If an evidence-ready result cannot be queued because capacity is
    exhausted, the mailbox enters a permanent FAILED state and retains
    the exact result which could not be queued. A future process
    supervisor must treat that failure as process-fatal rather than
    silently dropping a capital-eligible result.
    """

    __slots__ = (
        "_failed",
        "_failure_event",
        "_failure_item",
        "_failure_reason",
        "_loop",
        "_max_pending_results",
        "_queue",
    )

    def __init__(
        self,
        *,
        max_pending_results: int,
    ) -> None:
        if (
            LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION
            != "live-entry-candidate-pipeline-v1"
        ):
            raise LiveEntryResultMailboxError(
                "LIVE_ENTRY_RESULT_MAILBOX_"
                "COMPONENT_VERSION_MISMATCH"
            )

        if (
            isinstance(
                max_pending_results,
                bool,
            )
            or not isinstance(
                max_pending_results,
                int,
            )
            or max_pending_results <= 0
        ):
            raise ValueError(
                "max_pending_results must be positive int"
            )

        self._loop = (
            asyncio.get_running_loop()
        )

        self._max_pending_results = (
            max_pending_results
        )

        self._queue: asyncio.Queue[
            LiveEntryCandidatePipelineResult
        ] = asyncio.Queue(
            maxsize=max_pending_results
        )

        self._failure_event = asyncio.Event()

        self._failed = False
        self._failure_reason: str | None = None
        self._failure_item: Any | None = None

    def _assert_loop(
        self,
    ) -> None:
        if (
            asyncio.get_running_loop()
            is not self._loop
        ):
            raise RuntimeError(
                "mailbox used from wrong event loop"
            )

    @staticmethod
    def _valid_result(
        value: object,
    ) -> bool:
        return (
            isinstance(
                value,
                LiveEntryCandidatePipelineResult,
            )
            and value.pipeline_version
            == LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION
        )

    def _fail(
        self,
        *,
        reason: str,
        item: object,
    ) -> None:
        if self._failed:
            return

        self._failed = True
        self._failure_reason = reason
        self._failure_item = item
        self._failure_event.set()

    @property
    def max_pending_results(
        self,
    ) -> int:
        return self._max_pending_results

    @property
    def pending_result_count(
        self,
    ) -> int:
        return self._queue.qsize()

    @property
    def failed(
        self,
    ) -> bool:
        return self._failed

    @property
    def failure_reason(
        self,
    ) -> str | None:
        return self._failure_reason

    @property
    def failure_item(
        self,
    ) -> object | None:
        return self._failure_item

    def publish(
        self,
        result: object,
    ) -> str:
        """
        Offer one completed pipeline result without blocking.

        Only canonical evidence-ready results consume queue capacity.
        """

        self._assert_loop()

        if self._failed:
            return FAILED

        if not self._valid_result(
            result
        ):
            self._fail(
                reason=(
                    MAILBOX_RESULT_CONTRACT_INVALID
                ),
                item=result,
            )

            return FAILED

        if not result.evidence_ready:
            return IGNORED

        try:
            self._queue.put_nowait(
                result
            )

        except asyncio.QueueFull:
            self._fail(
                reason=MAILBOX_AT_CAPACITY,
                item=result,
            )

            return FAILED

        return PUBLISHED

    async def receive(
        self,
    ) -> LiveEntryCandidatePipelineResult:
        """
        Receive one queued evidence-ready result.

        Once mailbox integrity has failed, consumption fails closed.
        """

        self._assert_loop()

        if self._failed:
            raise LiveEntryResultMailboxError(
                self._failure_reason
                or "LIVE_ENTRY_RESULT_MAILBOX_FAILED"
            )

        return await self._queue.get()

    async def wait_failed(
        self,
    ) -> None:
        """
        Wait until the mailbox enters its permanent failed state.
        """

        self._assert_loop()

        await self._failure_event.wait()
