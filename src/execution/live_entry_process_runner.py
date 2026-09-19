from __future__ import annotations

import asyncio
from typing import Any

from src.execution.live_buy_execution_config import (
    LIVE_BUY_EXECUTION_CONFIG_VERSION,
    LiveBuyExecutionConfig,
)
from src.execution.live_entry_candidate_scheduler import (
    LIVE_ENTRY_CANDIDATE_SCHEDULER_VERSION,
    LiveEntryCandidateScheduler,
)
from src.execution.live_entry_capital_handoff import (
    LIVE_ENTRY_CAPITAL_HANDOFF_VERSION,
    run_live_entry_capital_handoff_once,
)
from src.execution.live_entry_evidence_only_config import (
    LIVE_ENTRY_EVIDENCE_ONLY_CONFIG_VERSION,
    LiveEntryEvidenceOnlyConfig,
)
from src.execution.live_entry_result_mailbox import (
    LIVE_ENTRY_RESULT_MAILBOX_VERSION,
    LiveEntryResultMailbox,
)
from src.execution.live_operating_config import (
    LIVE_OPERATING_CONFIG_VERSION,
    LiveOperatingConfig,
)
from src.execution.live_process_owner import (
    LIVE_PROCESS_OWNER_VERSION,
    LiveProcessOwner,
)


LIVE_ENTRY_PROCESS_RUNNER_VERSION = (
    "live-entry-process-runner-v1"
)

LIVE_ENTRY_PROCESS_RECOVERY_STOPPED = (
    "LIVE_ENTRY_PROCESS_RECOVERY_STOPPED"
)

LIVE_ENTRY_PROCESS_RECOVERY_CANCELLED = (
    "LIVE_ENTRY_PROCESS_RECOVERY_CANCELLED"
)

LIVE_ENTRY_PROCESS_COLLECTOR_STOPPED = (
    "LIVE_ENTRY_PROCESS_COLLECTOR_STOPPED"
)

LIVE_ENTRY_PROCESS_COLLECTOR_CANCELLED = (
    "LIVE_ENTRY_PROCESS_COLLECTOR_CANCELLED"
)

LIVE_ENTRY_PROCESS_CONSUMER_STOPPED = (
    "LIVE_ENTRY_PROCESS_CONSUMER_STOPPED"
)

LIVE_ENTRY_PROCESS_CONSUMER_CANCELLED = (
    "LIVE_ENTRY_PROCESS_CONSUMER_CANCELLED"
)

LIVE_ENTRY_PROCESS_MAILBOX_FAILED = (
    "LIVE_ENTRY_PROCESS_MAILBOX_FAILED"
)


class LiveEntryProcessRunnerError(
    RuntimeError
):
    pass


def _valid_operating_config(
    value: object,
) -> bool:
    return (
        isinstance(
            value,
            LiveOperatingConfig,
        )
        and LIVE_OPERATING_CONFIG_VERSION
        == "live-operating-config-v1"
    )


def _valid_evidence_config(
    value: object,
) -> bool:
    return (
        isinstance(
            value,
            LiveEntryEvidenceOnlyConfig,
        )
        and LIVE_ENTRY_EVIDENCE_ONLY_CONFIG_VERSION
        == "live-entry-evidence-only-config-v1"
    )


def _valid_execution_config(
    value: object,
) -> bool:
    return (
        isinstance(
            value,
            LiveBuyExecutionConfig,
        )
        and LIVE_BUY_EXECUTION_CONFIG_VERSION
        == "live-buy-execution-config-v1"
    )


def _components_are_compatible(
) -> bool:
    return (
        LIVE_PROCESS_OWNER_VERSION
        == "live-process-owner-v1"
        and LIVE_ENTRY_CANDIDATE_SCHEDULER_VERSION
        == "live-entry-candidate-scheduler-v2"
        and LIVE_ENTRY_RESULT_MAILBOX_VERSION
        == "live-entry-result-mailbox-v1"
        and LIVE_ENTRY_CAPITAL_HANDOFF_VERSION
        == "live-entry-capital-handoff-v1"
    )


async def _cancel_and_settle(
    task: asyncio.Task[Any],
) -> None:
    if not task.done():
        task.cancel()

    try:
        await task

    except asyncio.CancelledError:
        pass


async def _settle_without_cancelling(
    task: asyncio.Task[Any],
) -> None:
    """
    Settle an already-owned task without injecting cancellation.

    Used for the capital consumer so an already-started handoff may
    reach its downstream durable return point before owner authority is
    released.
    """

    try:
        await task

    except asyncio.CancelledError:
        pass


async def _run_market_collector_with_live_entry(
    *,
    scheduler: LiveEntryCandidateScheduler,
    mailbox: LiveEntryResultMailbox,
) -> None:
    """
    Lazy collector boundary.

    Importing this runner must not itself import market_collector,
    load Helius configuration, open databases, or begin RPC work.
    """

    from src.data.market_collector import (
        run_market_collector,
    )

    await run_market_collector(
        live_entry_scheduler=scheduler,
        live_entry_result_mailbox=mailbox,
    )


async def _run_live_entry_capital_consumer(
    *,
    owner: LiveProcessOwner,
    mailbox: LiveEntryResultMailbox,
    execution_config: LiveBuyExecutionConfig,
    stop_event: asyncio.Event,
) -> None:
    """
    Serially consume canonical evidence-ready results.

    stop_event closes admission to NEW handoffs.

    If stop becomes set while a handoff is already running, that
    handoff is allowed to return normally. No second handoff may begin.

    This loop performs no retry and owns no candidate/evidence logic.
    """

    while True:
        if stop_event.is_set():
            return

        receive_task = asyncio.create_task(
            mailbox.receive(),
            name=(
                "delve-live-entry-"
                "mailbox-receive"
            ),
        )

        stop_task = asyncio.create_task(
            stop_event.wait(),
            name=(
                "delve-live-entry-"
                "consumer-stop-wait"
            ),
        )

        try:
            done, _ = await asyncio.wait(
                {
                    receive_task,
                    stop_task,
                },
                return_when=(
                    asyncio.FIRST_COMPLETED
                ),
            )

            #
            # A completed receive wins over a simultaneous stop only
            # long enough to surface receive failure deterministically.
            # stop_event is checked again before BUY authority can be
            # invoked.
            #
            if receive_task in done:
                pipeline_result = (
                    await receive_task
                )

                if stop_event.is_set():
                    return

                await (
                    run_live_entry_capital_handoff_once(
                        owner=owner,
                        pipeline_result=(
                            pipeline_result
                        ),
                        execution_config=(
                            execution_config
                        ),
                    )
                )

                continue

            return

        finally:
            for child in (
                receive_task,
                stop_task,
            ):
                if not child.done():
                    child.cancel()

            await asyncio.gather(
                receive_task,
                stop_task,
                return_exceptions=True,
            )


async def run_live_entry_process(
    *,
    operating_config: LiveOperatingConfig,
    evidence_config: LiveEntryEvidenceOnlyConfig,
    execution_config: LiveBuyExecutionConfig,
    shutdown_event: asyncio.Event,
) -> None:
    """
    Run the supervised production live-entry lifecycle.

    Authority ordering:

        explicit validated configs
            ↓
        bounded scheduler + passive mailbox
            ↓
        one LiveProcessOwner / host-global lease
            ↓
        recovery + collector + serial capital consumer
            ↓
        shutdown OR child failure OR mailbox failure
            ↓
        stop NEW handoffs
            ↓
        stop collector / close scheduler
            ↓
        allow already-started handoff to settle
            ↓
        stop recovery
            ↓
        owner.close()
            ↓
        process authority lease released

    The mailbox capacity deliberately equals evidence_config's explicit
    max_pending_tasks. No additional production default or hidden queue
    bound is introduced.

    Unexpected recovery, collector, consumer, or mailbox termination is
    process-fatal.

    No bootstrapping or signal-handler ownership exists here.
    """

    if not _valid_operating_config(
        operating_config
    ):
        raise TypeError(
            "operating_config must be "
            "LiveOperatingConfig"
        )

    if not _valid_evidence_config(
        evidence_config
    ):
        raise TypeError(
            "evidence_config must be "
            "LiveEntryEvidenceOnlyConfig"
        )

    if not _valid_execution_config(
        execution_config
    ):
        raise TypeError(
            "execution_config must be "
            "LiveBuyExecutionConfig"
        )

    if not isinstance(
        shutdown_event,
        asyncio.Event,
    ):
        raise TypeError(
            "shutdown_event must be asyncio.Event"
        )

    if not _components_are_compatible():
        raise LiveEntryProcessRunnerError(
            "LIVE_ENTRY_PROCESS_RUNNER_"
            "COMPONENT_VERSION_MISMATCH"
        )

    #
    # A shutdown request which already exists must not acquire process
    # authority or begin evidence/RPC work merely to tear it down.
    #
    if shutdown_event.is_set():
        return

    scheduler = (
        LiveEntryCandidateScheduler(
            policy=evidence_config.policy,
            max_concurrency=(
                evidence_config.max_concurrency
            ),
            max_pending_tasks=(
                evidence_config.max_pending_tasks
            ),
        )
    )

    mailbox: (
        LiveEntryResultMailbox | None
    ) = None

    owner: LiveProcessOwner | None = None

    recovery_task: (
        asyncio.Task[Any] | None
    ) = None

    collector_task: (
        asyncio.Task[Any] | None
    ) = None

    consumer_task: (
        asyncio.Task[Any] | None
    ) = None

    mailbox_failure_task: (
        asyncio.Task[Any] | None
    ) = None

    shutdown_task: (
        asyncio.Task[Any] | None
    ) = None

    consumer_stop_event = asyncio.Event()

    try:
        mailbox = LiveEntryResultMailbox(
            max_pending_results=(
                evidence_config.max_pending_tasks
            )
        )

        owner = LiveProcessOwner(
            config=operating_config
        )

        recovery_task = asyncio.create_task(
            owner.run_recovery_service(),
            name="delve-live-entry-recovery",
        )

        collector_task = asyncio.create_task(
            _run_market_collector_with_live_entry(
                scheduler=scheduler,
                mailbox=mailbox,
            ),
            name="delve-live-entry-collector",
        )

        consumer_task = asyncio.create_task(
            _run_live_entry_capital_consumer(
                owner=owner,
                mailbox=mailbox,
                execution_config=(
                    execution_config
                ),
                stop_event=(
                    consumer_stop_event
                ),
            ),
            name=(
                "delve-live-entry-"
                "capital-consumer"
            ),
        )

        mailbox_failure_task = (
            asyncio.create_task(
                mailbox.wait_failed(),
                name=(
                    "delve-live-entry-"
                    "mailbox-failure-wait"
                ),
            )
        )

        shutdown_task = asyncio.create_task(
            shutdown_event.wait(),
            name=(
                "delve-live-entry-"
                "shutdown-wait"
            ),
        )

        done, _ = await asyncio.wait(
            {
                recovery_task,
                collector_task,
                consumer_task,
                mailbox_failure_task,
                shutdown_task,
            },
            return_when=(
                asyncio.FIRST_COMPLETED
            ),
        )

        #
        # Fatal child conditions win over a simultaneous shutdown so a
        # real process failure cannot be hidden by SIGINT/SIGTERM.
        #
        if recovery_task in done:
            try:
                await recovery_task

            except asyncio.CancelledError:
                raise (
                    LiveEntryProcessRunnerError(
                        LIVE_ENTRY_PROCESS_RECOVERY_CANCELLED
                    )
                ) from None

            raise LiveEntryProcessRunnerError(
                LIVE_ENTRY_PROCESS_RECOVERY_STOPPED
            )

        if collector_task in done:
            try:
                await collector_task

            except asyncio.CancelledError:
                raise (
                    LiveEntryProcessRunnerError(
                        LIVE_ENTRY_PROCESS_COLLECTOR_CANCELLED
                    )
                ) from None

            raise LiveEntryProcessRunnerError(
                LIVE_ENTRY_PROCESS_COLLECTOR_STOPPED
            )

        if mailbox_failure_task in done:
            await mailbox_failure_task

            reason = (
                mailbox.failure_reason
                or "UNKNOWN"
            )

            raise LiveEntryProcessRunnerError(
                f"{LIVE_ENTRY_PROCESS_MAILBOX_FAILED}:"
                f"{reason}"
            )

        if consumer_task in done:
            try:
                await consumer_task

            except asyncio.CancelledError:
                raise (
                    LiveEntryProcessRunnerError(
                        LIVE_ENTRY_PROCESS_CONSUMER_CANCELLED
                    )
                ) from None

            raise LiveEntryProcessRunnerError(
                LIVE_ENTRY_PROCESS_CONSUMER_STOPPED
            )

        #
        # Otherwise the orderly shutdown task won.
        #

    finally:
        #
        # First close admission to NEW capital handoffs.
        #
        consumer_stop_event.set()

        try:
            #
            # Stop event production. market_collector owns scheduler
            # shutdown in its normal cancellation path.
            #
            if collector_task is not None:
                await _cancel_and_settle(
                    collector_task
                )

        finally:
            try:
                #
                # Backstop scheduler ownership if collector never
                # entered its coroutine body.
                #
                if not scheduler.closed:
                    await scheduler.close()

            finally:
                try:
                    #
                    # Deliberately DO NOT cancel this task. If a BUY
                    # handoff already crossed the authority boundary,
                    # allow it to reach its durable downstream return
                    # point before recovery or owner authority stops.
                    #
                    if consumer_task is not None:
                        await (
                            _settle_without_cancelling(
                                consumer_task
                            )
                        )

                finally:
                    try:
                        if (
                            mailbox_failure_task
                            is not None
                        ):
                            await _cancel_and_settle(
                                mailbox_failure_task
                            )

                    finally:
                        try:
                            if shutdown_task is not None:
                                await _cancel_and_settle(
                                    shutdown_task
                                )

                        finally:
                            try:
                                #
                                # Recovery remains alive while an
                                # already-started handoff settles.
                                #
                                if recovery_task is not None:
                                    await _cancel_and_settle(
                                        recovery_task
                                    )

                            finally:
                                if owner is not None:
                                    owner.close()
