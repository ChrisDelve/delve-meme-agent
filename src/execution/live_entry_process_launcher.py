from __future__ import annotations

import asyncio
from pathlib import Path

from src.execution.live_bootstrap_config import (
    LIVE_BOOTSTRAP_CONFIG_VERSION,
    bootstrap_live_operating_config,
)
from src.execution.live_buy_execution_bootstrap import (
    LIVE_BUY_EXECUTION_BOOTSTRAP_VERSION,
    bootstrap_live_buy_execution_config,
)
from src.execution.live_entry_evidence_only_bootstrap import (
    LIVE_ENTRY_EVIDENCE_ONLY_BOOTSTRAP_VERSION,
    bootstrap_live_entry_evidence_only_config,
)
from src.execution.live_entry_process_runner import (
    LIVE_ENTRY_PROCESS_RUNNER_VERSION,
    run_live_entry_process,
)
from src.execution.live_process_runner import (
    LIVE_PROCESS_RUNNER_VERSION,
    _install_shutdown_signal_handlers,
)
from src.execution.live_sell_supervisor_bootstrap import (
    LIVE_SELL_SUPERVISOR_BOOTSTRAP_VERSION,
    bootstrap_live_sell_supervisor_config,
)
from src.execution.live_startup_preflight import (
    LIVE_STARTUP_PREFLIGHT_VERSION,
    run_live_startup_preflight,
)


LIVE_ENTRY_PROCESS_LAUNCHER_VERSION = (
    "live-entry-process-launcher-v2"
)

LIVE_ENTRY_PROCESS_LAUNCHER_COMPONENT_VERSION_MISMATCH = (
    "LIVE_ENTRY_PROCESS_LAUNCHER_COMPONENT_VERSION_MISMATCH"
)


class LiveEntryProcessLauncherError(
    RuntimeError
):
    pass


def _components_are_compatible(
) -> bool:
    return (
        LIVE_BOOTSTRAP_CONFIG_VERSION
        == "live-bootstrap-config-v1"
        and LIVE_BUY_EXECUTION_BOOTSTRAP_VERSION
        == "live-buy-execution-bootstrap-v1"
        and LIVE_ENTRY_EVIDENCE_ONLY_BOOTSTRAP_VERSION
        == "live-entry-evidence-only-bootstrap-v1"
        and LIVE_SELL_SUPERVISOR_BOOTSTRAP_VERSION
        == "live-sell-supervisor-bootstrap-v1"
        and LIVE_STARTUP_PREFLIGHT_VERSION
        == "live-startup-preflight-v1"
        and LIVE_ENTRY_PROCESS_RUNNER_VERSION
        == "live-entry-process-runner-v2"
        and LIVE_PROCESS_RUNNER_VERSION
        == "live-process-runner-v1"
    )


async def run_bootstrapped_live_entry_process(
    *,
    dotenv_path: Path | str | None = Path(
        ".env"
    ),
) -> None:
    """
    Bootstrap and run the unified production live process.

    Ownership boundary:

        install SIGINT/SIGTERM handlers
                    ↓
        load four explicit non-secret configs
                    ↓
        yield once for any pending shutdown signal
                    ↓
        if still active: startup preflight
                    ↓
        run_live_entry_process(...)
                    ↓
        recovery + BUY + SELL under one LiveProcessOwner
                    ↓
        remove signal handlers

    This launcher does not construct a signer, RPC client, database
    connection, transaction, or process authority owner itself.
    """
    if not _components_are_compatible():
        raise LiveEntryProcessLauncherError(
            LIVE_ENTRY_PROCESS_LAUNCHER_COMPONENT_VERSION_MISMATCH
        )

    shutdown_event = asyncio.Event()

    loop, installed_signals = (
        _install_shutdown_signal_handlers(
            shutdown_event=shutdown_event
        )
    )

    try:
        operating_config = (
            bootstrap_live_operating_config(
                dotenv_path=dotenv_path
            )
        )

        evidence_config = (
            bootstrap_live_entry_evidence_only_config(
                dotenv_path=dotenv_path
            )
        )

        execution_config = (
            bootstrap_live_buy_execution_config(
                dotenv_path=dotenv_path
            )
        )

        sell_supervisor_config = (
            bootstrap_live_sell_supervisor_config(
                dotenv_path=dotenv_path
            )
        )

        #
        # add_signal_handler() schedules its callback on the event
        # loop. All bootstraps above are synchronous, so yield once
        # before the unified runner may acquire process authority.
        #
        # If SIGINT/SIGTERM arrived during configuration loading,
        # shutdown_event becomes set here and run_live_entry_process()
        # returns before scheduler/mailbox/LiveProcessOwner creation.
        #
        await asyncio.sleep(
            0
        )

        #
        # Do not touch signer / database / RPC merely to honor a
        # shutdown which arrived during synchronous configuration.
        #
        # The unified runner still receives the already-set event so
        # its own preexisting-shutdown invariant remains authoritative.
        #
        if not shutdown_event.is_set():
            await run_live_startup_preflight(
                operating_config=operating_config,
                execution_config=execution_config,
            )

        await run_live_entry_process(
            operating_config=operating_config,
            evidence_config=evidence_config,
            execution_config=execution_config,
            sell_supervisor_config=(
                sell_supervisor_config
            ),
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
        run_bootstrapped_live_entry_process()
    )


if __name__ == "__main__":
    main()
