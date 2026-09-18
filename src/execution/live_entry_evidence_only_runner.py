from __future__ import annotations

import asyncio
from pathlib import Path
import signal
from typing import Any

from src.execution.live_entry_candidate_scheduler import (
    LIVE_ENTRY_CANDIDATE_SCHEDULER_VERSION,
    LiveEntryCandidateScheduler,
)
from src.execution.live_entry_evidence_only_bootstrap import (
    LIVE_ENTRY_EVIDENCE_ONLY_BOOTSTRAP_VERSION,
    bootstrap_live_entry_evidence_only_config,
)
from src.execution.live_entry_evidence_only_config import (
    LIVE_ENTRY_EVIDENCE_ONLY_CONFIG_VERSION,
    LiveEntryEvidenceOnlyConfig,
)


LIVE_ENTRY_EVIDENCE_ONLY_RUNNER_VERSION = (
    "live-entry-evidence-only-runner-v1"
)

EVIDENCE_ONLY_COLLECTOR_STOPPED = (
    "EVIDENCE_ONLY_COLLECTOR_STOPPED"
)

EVIDENCE_ONLY_COLLECTOR_CANCELLED = (
    "EVIDENCE_ONLY_COLLECTOR_CANCELLED"
)

EVIDENCE_ONLY_SIGNAL_HANDLER_UNAVAILABLE = (
    "EVIDENCE_ONLY_SIGNAL_HANDLER_UNAVAILABLE"
)


class LiveEntryEvidenceOnlyRunnerError(
    RuntimeError
):
    pass


def _valid_config(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            LiveEntryEvidenceOnlyConfig,
        )
        and LIVE_ENTRY_EVIDENCE_ONLY_CONFIG_VERSION
        == "live-entry-evidence-only-config-v1"
    )


def _components_are_compatible(
) -> bool:
    return (
        LIVE_ENTRY_EVIDENCE_ONLY_BOOTSTRAP_VERSION
        == "live-entry-evidence-only-bootstrap-v1"
        and LIVE_ENTRY_CANDIDATE_SCHEDULER_VERSION
        == "live-entry-candidate-scheduler-v1"
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


async def _run_market_collector_with_scheduler(
    *,
    scheduler: LiveEntryCandidateScheduler,
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
        live_entry_scheduler=scheduler
    )


async def run_live_entry_evidence_only_process(
    *,
    config: LiveEntryEvidenceOnlyConfig,
    shutdown_event: asyncio.Event,
) -> None:
    """
    Run the supervised evidence-only collector lifecycle.

    Authority boundary:

        explicit evidence-only config
            ↓
        LiveEntryPolicy from config
            ↓
        bounded LiveEntryCandidateScheduler
            ↓
        market collector
            ↓
        candidate → policy → fresh evidence only

    No LiveProcessOwner, signer, reservation, transaction, BUY, or
    SELL authority exists in this runner.

    Unexpected collector termination is process-fatal. Orderly
    shutdown cancels and settles the collector. Scheduler close is
    also performed as an idempotent final backstop in case the
    collector task is cancelled before its coroutine body starts.
    """

    if not _valid_config(
        config
    ):
        raise TypeError(
            "config must be "
            "LiveEntryEvidenceOnlyConfig"
        )

    if not isinstance(
        shutdown_event,
        asyncio.Event,
    ):
        raise TypeError(
            "shutdown_event must be asyncio.Event"
        )

    if not _components_are_compatible():
        raise (
            LiveEntryEvidenceOnlyRunnerError(
                "EVIDENCE_ONLY_RUNNER_"
                "COMPONENT_VERSION_MISMATCH"
            )
        )

    #
    # A shutdown already requested must not construct a scheduler or
    # enter collector/RPC lifecycle merely to tear it down.
    #
    if shutdown_event.is_set():
        return

    scheduler = (
        LiveEntryCandidateScheduler(
            policy=config.policy,
            max_concurrency=(
                config.max_concurrency
            ),
            max_pending_tasks=(
                config.max_pending_tasks
            ),
        )
    )

    collector_task: (
        asyncio.Task[Any] | None
    ) = None

    shutdown_task: (
        asyncio.Task[Any] | None
    ) = None

    try:
        collector_task = asyncio.create_task(
            _run_market_collector_with_scheduler(
                scheduler=scheduler
            ),
            name=(
                "delve-live-entry-"
                "evidence-collector"
            ),
        )

        shutdown_task = asyncio.create_task(
            shutdown_event.wait(),
            name=(
                "delve-live-entry-"
                "evidence-shutdown-wait"
            ),
        )

        done, _ = await asyncio.wait(
            {
                collector_task,
                shutdown_task,
            },
            return_when=(
                asyncio.FIRST_COMPLETED
            ),
        )

        #
        # The collector is designed to run until shutdown.
        #
        # If collector termination and shutdown become visible in the
        # same event-loop turn, collector termination wins so a real
        # collector failure cannot be hidden by shutdown.
        #
        if collector_task in done:
            try:
                await collector_task

            except asyncio.CancelledError:
                raise (
                    LiveEntryEvidenceOnlyRunnerError(
                        EVIDENCE_ONLY_COLLECTOR_CANCELLED
                    )
                ) from None

            raise (
                LiveEntryEvidenceOnlyRunnerError(
                    EVIDENCE_ONLY_COLLECTOR_STOPPED
                )
            )

        #
        # Orderly shutdown.
        #
        collector_task.cancel()

        try:
            await collector_task

        except asyncio.CancelledError:
            pass

    finally:
        #
        # Settle every owned task before returning.
        #
        # The collector normally closes the scheduler itself, but a
        # task cancelled before first execution may never enter the
        # collector coroutine. The final scheduler close is therefore
        # an idempotent lifecycle backstop.
        #
        try:
            if shutdown_task is not None:
                await _cancel_and_settle(
                    shutdown_task
                )

        finally:
            try:
                if collector_task is not None:
                    await _cancel_and_settle(
                        collector_task
                    )

            finally:
                if not scheduler.closed:
                    await scheduler.close()


def _install_shutdown_signal_handlers(
    *,
    shutdown_event: asyncio.Event,
) -> tuple[
    asyncio.AbstractEventLoop,
    tuple[signal.Signals, ...],
]:
    loop = asyncio.get_running_loop()

    installed: list[
        signal.Signals
    ] = []

    try:
        for process_signal in (
            signal.SIGINT,
            signal.SIGTERM,
        ):
            loop.add_signal_handler(
                process_signal,
                shutdown_event.set,
            )

            installed.append(
                process_signal
            )

    except (
        NotImplementedError,
        RuntimeError,
        ValueError,
    ):
        for process_signal in installed:
            loop.remove_signal_handler(
                process_signal
            )

        raise (
            LiveEntryEvidenceOnlyRunnerError(
                EVIDENCE_ONLY_SIGNAL_HANDLER_UNAVAILABLE
            )
        ) from None

    return (
        loop,
        tuple(
            installed
        ),
    )


async def run_bootstrapped_live_entry_evidence_only_process(
    *,
    dotenv_path: Path | str | None = Path(
        ".env"
    ),
) -> None:
    """
    Bootstrap explicit evidence-only policy and run the supervised
    collector lifecycle.

    Signal handlers are installed before configuration loading. A
    queued shutdown signal therefore gets a chance to become visible
    before scheduler/RPC lifecycle begins.
    """

    shutdown_event = asyncio.Event()

    loop, installed_signals = (
        _install_shutdown_signal_handlers(
            shutdown_event=shutdown_event
        )
    )

    try:
        config = (
            bootstrap_live_entry_evidence_only_config(
                dotenv_path=dotenv_path
            )
        )

        #
        # Allow any signal callback queued while synchronous bootstrap
        # was running to set shutdown_event before lifecycle begins.
        #
        await asyncio.sleep(0)

        await (
            run_live_entry_evidence_only_process(
                config=config,
                shutdown_event=shutdown_event,
            )
        )

    finally:
        for process_signal in installed_signals:
            loop.remove_signal_handler(
                process_signal
            )


def main() -> None:
    asyncio.run(
        run_bootstrapped_live_entry_evidence_only_process()
    )


if __name__ == "__main__":
    main()
