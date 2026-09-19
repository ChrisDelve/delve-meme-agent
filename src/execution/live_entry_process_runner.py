from __future__ import annotations

import asyncio
from typing import Any, Callable

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
from src.execution.live_sell_supervisor_config import (
    LIVE_SELL_SUPERVISOR_CONFIG_VERSION,
    LiveSellSupervisorConfig,
)
from src.execution.live_sell_supervisor_service import (
    LIVE_SELL_SUPERVISOR_SERVICE_VERSION,
    run_live_sell_supervisor_service,
)


LIVE_ENTRY_PROCESS_RUNNER_VERSION = (
    "live-entry-process-runner-v3"
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

LIVE_ENTRY_PROCESS_SELL_STOPPED = (
    "LIVE_ENTRY_PROCESS_SELL_STOPPED"
)

LIVE_ENTRY_PROCESS_SELL_CANCELLED = (
    "LIVE_ENTRY_PROCESS_SELL_CANCELLED"
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


def _valid_sell_supervisor_config(
    value: object,
) -> bool:
    return (
        isinstance(
            value,
            LiveSellSupervisorConfig,
        )
        and LIVE_SELL_SUPERVISOR_CONFIG_VERSION
        == "live-sell-supervisor-config-v1"
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
        and LIVE_SELL_SUPERVISOR_SERVICE_VERSION
        == "live-sell-supervisor-service-v1"
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
    Settle an already-owned capital task without injecting cancellation.

    Repeated cancellation of the surrounding process runner must not
    propagate into a BUY or SELL authority call which is already in
    flight. Child failure remains visible.
    """

    while not task.done():
        try:
            await asyncio.shield(
                task
            )

        except asyncio.CancelledError:
            continue

    try:
        task.result()

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



LiveFreshConfigLoader = Callable[
    [],
    tuple[
        LiveEntryEvidenceOnlyConfig,
        LiveBuyExecutionConfig,
        LiveSellSupervisorConfig,
    ],
]


async def run_live_entry_process(
    *,
    operating_config: LiveOperatingConfig,
    evidence_config: LiveEntryEvidenceOnlyConfig | None = None,
    execution_config: LiveBuyExecutionConfig | None = None,
    sell_supervisor_config: LiveSellSupervisorConfig | None = None,
    fresh_config_loader: LiveFreshConfigLoader | None = None,
    shutdown_event: asyncio.Event,
) -> None:
    """
    Run the supervised production live-entry lifecycle.

    Authority ordering:

        validated operating config
            ↓
        one LiveProcessOwner / host-global lease
            ↓
        recovery receives event-loop handoff
            ↓
        resolve + validate fresh-capital configs
            ↓
        bounded scheduler + passive mailbox
            ↓
        collector + serial BUY consumer
                 + continuous SELL supervisor
            ↓
        shutdown OR child failure OR mailbox failure
            ↓
        stop NEW BUY handoffs + NEW SELL ticks
            ↓
        stop collector / close scheduler
            ↓
        allow already-started BUY/SELL authority to settle
            ↓
        stop recovery
            ↓
        owner.close()
            ↓
        process authority lease released

    Production may supply fresh_config_loader so evidence / BUY / SELL
    bootstrap cannot prevent recovery from becoming reachable first.

    Direct callers may continue supplying the three already-built config
    objects. Those objects are validated only after recovery receives its
    initial event-loop handoff.

    The mailbox capacity deliberately equals evidence_config's explicit
    max_pending_tasks. No additional production default or hidden queue
    bound is introduced.

    Unexpected recovery, collector, BUY consumer, SELL supervisor, or
    mailbox termination is process-fatal.

    No bootstrapping or signal-handler ownership exists here.
    """

    if not _valid_operating_config(
        operating_config
    ):
        raise TypeError(
            "operating_config must be "
            "LiveOperatingConfig"
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

    scheduler: (
        LiveEntryCandidateScheduler | None
    ) = None

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

    sell_task: (
        asyncio.Task[Any] | None
    ) = None

    mailbox_failure_task: (
        asyncio.Task[Any] | None
    ) = None

    shutdown_task: (
        asyncio.Task[Any] | None
    ) = None

    consumer_stop_event = asyncio.Event()
    sell_stop_event = asyncio.Event()

    try:
        owner = LiveProcessOwner(
            config=operating_config
        )

        recovery_task = asyncio.create_task(
            owner.run_recovery_service(),
            name="delve-live-entry-recovery",
        )

        #
        # Recovery-first startup barrier.
        #
        # Give the recovery task an event-loop turn before ANY
        # fresh-capital configuration may be resolved.
        #
        await asyncio.sleep(
            0
        )

        #
        # Recovery failure always wins over fresh-capital startup.
        #
        if recovery_task.done():
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

        if shutdown_event.is_set():
            return

        #
        # Production launcher mode:
        #
        # Resolve evidence / BUY / SELL configuration only AFTER
        # recovery has entered the event loop.
        #
        if fresh_config_loader is not None:
            if (
                evidence_config is not None
                or execution_config is not None
                or sell_supervisor_config is not None
            ):
                raise TypeError(
                    "fresh config source is ambiguous"
                )

            if not callable(
                fresh_config_loader
            ):
                raise TypeError(
                    "fresh_config_loader must be callable"
                )

            loaded = fresh_config_loader()

            if (
                not isinstance(
                    loaded,
                    tuple,
                )
                or len(
                    loaded
                ) != 3
            ):
                raise TypeError(
                    "fresh_config_loader must return "
                    "three configs"
                )

            (
                evidence_config,
                execution_config,
                sell_supervisor_config,
            ) = loaded

        #
        # A SIGINT/SIGTERM or recovery failure may have occurred
        # while the synchronous loader was running.
        #
        await asyncio.sleep(
            0
        )

        if recovery_task.done():
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

        if shutdown_event.is_set():
            return

        #
        # Only now may fresh-capital configuration become a
        # prerequisite for collector / BUY / SELL startup.
        #
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

        if not _valid_sell_supervisor_config(
            sell_supervisor_config
        ):
            raise TypeError(
                "sell_supervisor_config must be "
                "LiveSellSupervisorConfig"
            )

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

        mailbox = LiveEntryResultMailbox(
            max_pending_results=(
                evidence_config.max_pending_tasks
            )
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

        sell_task = asyncio.create_task(
            run_live_sell_supervisor_service(
                owner=owner,
                supervisor_config=(
                    sell_supervisor_config
                ),
                execution_config=(
                    execution_config
                ),
                stop_event=(
                    sell_stop_event
                ),
            ),
            name=(
                "delve-live-entry-"
                "sell-supervisor"
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
                sell_task,
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

        if sell_task in done:
            try:
                await sell_task

            except asyncio.CancelledError:
                raise (
                    LiveEntryProcessRunnerError(
                        LIVE_ENTRY_PROCESS_SELL_CANCELLED
                    )
                ) from None

            raise LiveEntryProcessRunnerError(
                LIVE_ENTRY_PROCESS_SELL_STOPPED
            )

        #
        # Otherwise the orderly shutdown task won.
        #

    finally:
        #
        # First close admission to NEW capital authority.
        #
        # BUY: no new handoffs.
        # SELL: no new supervisor ticks.
        #
        consumer_stop_event.set()
        sell_stop_event.set()

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
                if (
                    scheduler is not None
                    and not scheduler.closed
                ):
                    await scheduler.close()

            finally:
                try:
                    #
                    # Deliberately DO NOT cancel either capital task.
                    #
                    # If BUY or SELL already crossed the authority
                    # boundary, allow it to reach its durable downstream
                    # return point before recovery or owner authority
                    # stops.
                    #
                    try:
                        if consumer_task is not None:
                            await (
                                _settle_without_cancelling(
                                    consumer_task
                                )
                            )

                    finally:
                        if sell_task is not None:
                            await (
                                _settle_without_cancelling(
                                    sell_task
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
