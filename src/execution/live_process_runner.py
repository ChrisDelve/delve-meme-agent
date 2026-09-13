from __future__ import annotations

import asyncio
from pathlib import Path
import signal
from typing import Any

from src.execution.live_bootstrap_config import (
    LIVE_BOOTSTRAP_CONFIG_VERSION,
    bootstrap_live_operating_config,
)
from src.execution.live_operating_config import (
    LIVE_OPERATING_CONFIG_VERSION,
    LiveOperatingConfig,
)
from src.execution.live_process_owner import (
    LIVE_PROCESS_OWNER_VERSION,
    LiveProcessOwner,
)


LIVE_PROCESS_RUNNER_VERSION = (
    "live-process-runner-v1"
)

LIVE_PROCESS_RECOVERY_STOPPED = (
    "LIVE_PROCESS_RECOVERY_STOPPED"
)

LIVE_PROCESS_RECOVERY_CANCELLED = (
    "LIVE_PROCESS_RECOVERY_CANCELLED"
)

LIVE_PROCESS_SIGNAL_HANDLER_UNAVAILABLE = (
    "LIVE_PROCESS_SIGNAL_HANDLER_UNAVAILABLE"
)


class LiveProcessRunnerError(
    RuntimeError
):
    pass


def _valid_config(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            LiveOperatingConfig,
        )
        and LIVE_OPERATING_CONFIG_VERSION
        == "live-operating-config-v1"
    )


def _components_are_compatible(
) -> bool:
    return (
        LIVE_BOOTSTRAP_CONFIG_VERSION
        == "live-bootstrap-config-v1"
        and LIVE_PROCESS_OWNER_VERSION
        == "live-process-owner-v1"
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


async def run_live_process(
    *,
    config: LiveOperatingConfig,
    shutdown_event: asyncio.Event,
) -> None:
    """
    Run the recovery-only production live lifecycle.

    Authority ordering:

        validated operating config
            ↓
        LiveProcessOwner construction
            ↓
        host-global process lease held
            ↓
        one recovery task through owner
            ↓
        shutdown OR recovery termination
            ↓
        recovery cancelled/settled
            ↓
        owner closed
            ↓
        process lease released

    Fresh BUY and SELL authority is deliberately absent from this
    runner.

    Recovery is supervised. If it terminates normally or is
    independently cancelled before shutdown, the runner fails
    closed instead of remaining alive without recovery.
    """
    if not _valid_config(
        config
    ):
        raise TypeError(
            "config must be LiveOperatingConfig"
        )

    if not isinstance(
        shutdown_event,
        asyncio.Event,
    ):
        raise TypeError(
            "shutdown_event must be asyncio.Event"
        )

    if not _components_are_compatible():
        raise LiveProcessRunnerError(
            "LIVE_PROCESS_RUNNER_COMPONENT_VERSION_MISMATCH"
        )

    # A shutdown request that already exists must not acquire
    # process authority merely to tear it back down immediately.
    if shutdown_event.is_set():
        return

    owner = LiveProcessOwner(
        config=config
    )

    recovery_task: asyncio.Task[Any] | None = None
    shutdown_task: asyncio.Task[Any] | None = None

    try:
        recovery_task = asyncio.create_task(
            owner.run_recovery_service(),
            name="delve-live-recovery",
        )

        shutdown_task = asyncio.create_task(
            shutdown_event.wait(),
            name="delve-live-shutdown-wait",
        )

        done, _ = await asyncio.wait(
            {
                recovery_task,
                shutdown_task,
            },
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Recovery is designed to be long-running. Any termination
        # before an orderly shutdown request is process-fatal.
        #
        # If recovery and shutdown become ready in the same event-loop
        # turn, recovery wins so an actual recovery failure cannot be
        # hidden by the shutdown event.
        if recovery_task in done:
            try:
                await recovery_task
            except asyncio.CancelledError:
                raise LiveProcessRunnerError(
                    LIVE_PROCESS_RECOVERY_CANCELLED
                ) from None

            raise LiveProcessRunnerError(
                LIVE_PROCESS_RECOVERY_STOPPED
            )

        # Orderly shutdown path.
        recovery_task.cancel()

        try:
            await recovery_task
        except asyncio.CancelledError:
            pass

    finally:
        # Always settle both child tasks before releasing the owner
        # lease. Nested finally blocks guarantee owner.close() still
        # runs if task settlement itself exposes an unexpected error.
        try:
            if shutdown_task is not None:
                await _cancel_and_settle(
                    shutdown_task
                )
        finally:
            try:
                if recovery_task is not None:
                    await _cancel_and_settle(
                        recovery_task
                    )
            finally:
                owner.close()


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

        raise LiveProcessRunnerError(
            LIVE_PROCESS_SIGNAL_HANDLER_UNAVAILABLE
        ) from None

    return (
        loop,
        tuple(
            installed
        ),
    )


async def run_bootstrapped_live_process(
    *,
    dotenv_path: Path | str | None = Path(
        ".env"
    ),
) -> None:
    """
    Bootstrap non-secret live policy and run recovery lifecycle.

    Signal handlers are installed before bootstrap so SIGINT/SIGTERM
    received during configuration loading become an orderly shutdown
    request. run_live_process() checks a pre-set shutdown event before
    constructing LiveProcessOwner, so no process lease is acquired
    after shutdown has already been requested.
    """
    shutdown_event = asyncio.Event()

    loop, installed_signals = (
        _install_shutdown_signal_handlers(
            shutdown_event=shutdown_event
        )
    )

    try:
        config = bootstrap_live_operating_config(
            dotenv_path=dotenv_path
        )

        # Signal callbacks installed with add_signal_handler() are
        # scheduled onto the event loop. Bootstrap above is
        # synchronous, so explicitly yield once before lifecycle
        # authority can be acquired. A shutdown signal received
        # during bootstrap can therefore set shutdown_event before
        # run_live_process() considers constructing LiveProcessOwner.
        await asyncio.sleep(
            0
        )

        await run_live_process(
            config=config,
            shutdown_event=shutdown_event,
        )

    finally:
        for process_signal in installed_signals:
            loop.remove_signal_handler(
                process_signal
            )


def main(
) -> None:
    asyncio.run(
        run_bootstrapped_live_process()
    )


if __name__ == "__main__":
    main()
