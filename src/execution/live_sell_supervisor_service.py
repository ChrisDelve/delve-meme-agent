from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from src.execution.live_buy_execution_config import (
    LIVE_BUY_EXECUTION_CONFIG_VERSION,
    LiveBuyExecutionConfig,
)
from src.execution.live_process_owner import (
    LIVE_PROCESS_OWNER_VERSION,
    LiveProcessOwner,
)
from src.execution.live_sell_runtime import (
    ADVANCED,
    BLOCK,
    CLAIMED,
    COMPLETE,
    EXIT,
    HALTED,
    HOLD,
    IDLE,
    INIT,
    KILL,
    LIVE_SELL_RUNTIME_VERSION,
    RECOVERY,
    RECONCILED,
    SIGNED,
    SIGNING,
    UNKNOWN,
    LiveSellRuntimeResult,
)
from src.execution.live_sell_supervisor_config import (
    LIVE_SELL_SUPERVISOR_CONFIG_VERSION,
    LiveSellSupervisorConfig,
)


LIVE_SELL_SUPERVISOR_SERVICE_VERSION = (
    "live-sell-supervisor-service-v1"
)

LIVE_SELL_SUPERVISOR_RESULT_CONTRACT_INVALID = (
    "LIVE_SELL_SUPERVISOR_RESULT_CONTRACT_INVALID"
)

LIVE_SELL_SUPERVISOR_CLOCK_INVALID = (
    "LIVE_SELL_SUPERVISOR_CLOCK_INVALID"
)

U64_MAX = (1 << 64) - 1

_VALID_RUNTIME_STATUS_STAGES = {
    IDLE: frozenset(
        (COMPLETE,)
    ),
    HALTED: frozenset(
        (KILL,)
    ),
    ADVANCED: frozenset(
        (RECOVERY,)
    ),
    RECONCILED: frozenset(
        (RECOVERY,)
    ),
    HOLD: frozenset(
        (RECOVERY,)
    ),
    SIGNED: frozenset(
        (SIGNING,)
    ),
    CLAIMED: frozenset(
        (EXIT,)
    ),
    BLOCK: frozenset(
        (
            RECOVERY,
            SIGNING,
            EXIT,
        )
    ),
    UNKNOWN: frozenset(
        (
            INIT,
            RECOVERY,
            SIGNING,
            EXIT,
        )
    ),
}


class LiveSellSupervisorServiceError(
    RuntimeError
):
    pass


def _components_are_compatible() -> bool:
    return (
        LIVE_PROCESS_OWNER_VERSION
        == "live-process-owner-v1"
        and LIVE_BUY_EXECUTION_CONFIG_VERSION
        == "live-buy-execution-config-v1"
        and LIVE_SELL_SUPERVISOR_CONFIG_VERSION
        == "live-sell-supervisor-config-v1"
        and LIVE_SELL_RUNTIME_VERSION
        == "live-sell-runtime-v1"
    )


def _runtime_result_contract_valid(
    result: Any,
) -> bool:
    return (
        isinstance(
            result,
            LiveSellRuntimeResult,
        )
        and result.runtime_version
        == LIVE_SELL_RUNTIME_VERSION
        and isinstance(
            result.status,
            str,
        )
        and isinstance(
            result.stage,
            str,
        )
        and result.stage
        in _VALID_RUNTIME_STATUS_STAGES.get(
            result.status,
            (),
        )
        and isinstance(
            result.reasons,
            tuple,
        )
        and bool(
            result.reasons
        )
        and all(
            isinstance(
                reason,
                str,
            )
            and bool(reason)
            for reason in result.reasons
        )
    )


def _current_evaluated_at() -> int:
    current = time.time()

    if (
        not isinstance(
            current,
            (int, float),
        )
        or isinstance(
            current,
            bool,
        )
    ):
        raise LiveSellSupervisorServiceError(
            LIVE_SELL_SUPERVISOR_CLOCK_INVALID
        )

    normalized = float(
        current
    )

    if (
        not math.isfinite(
            normalized
        )
        or normalized < 1.0
        or normalized > U64_MAX
    ):
        raise LiveSellSupervisorServiceError(
            LIVE_SELL_SUPERVISOR_CLOCK_INVALID
        )

    evaluated_at = int(
        normalized
    )

    if (
        evaluated_at <= 0
        or evaluated_at > U64_MAX
    ):
        raise LiveSellSupervisorServiceError(
            LIVE_SELL_SUPERVISOR_CLOCK_INVALID
        )

    return evaluated_at


async def _wait_until_stop_or_interval(
    *,
    stop_event: asyncio.Event,
    interval_seconds: float,
) -> bool:
    """
    Return True when stop was requested.

    Return False when the interval elapsed normally.
    """

    if stop_event.is_set():
        return True

    try:
        await asyncio.wait_for(
            stop_event.wait(),
            timeout=interval_seconds,
        )

    except TimeoutError:
        return False

    return True


async def _run_sell_tick(
    *,
    owner: LiveProcessOwner,
    supervisor_config: LiveSellSupervisorConfig,
    execution_config: LiveBuyExecutionConfig,
    evaluated_at: int,
) -> LiveSellRuntimeResult:
    """
    Run exactly one owner-level SELL invocation.

    If the surrounding service is externally cancelled after this tick
    has begun, settle the already-running owner call before allowing
    cancellation to propagate. No SELL tick is abandoned mid-authority
    lifecycle merely because the service task was cancelled.
    """

    task = asyncio.create_task(
        owner.run_sell_once(
            compute_unit_limit=(
                execution_config.compute_unit_limit
            ),
            wallet_pubkey=(
                execution_config.wallet_pubkey
            ),
            evaluated_at=evaluated_at,
            policy=supervisor_config.policy,
            slippage_bps=(
                execution_config.exit_slippage_bps
            ),
            base_network_fee_lamports=(
                execution_config
                .exit_base_network_fee_lamports
            ),
            priority_fee_lamports=(
                execution_config
                .exit_priority_fee_lamports
            ),
        )
    )

    cancellation: (
        asyncio.CancelledError | None
    ) = None

    try:
        result = await asyncio.shield(
            task
        )

    except asyncio.CancelledError as error:
        cancellation = error

        #
        # The owner call already crossed the authority boundary.
        # Settle it before honoring external cancellation.
        #
        # Continue shielding while it remains active so repeated
        # cancellation requests cannot propagate into the owner task.
        #
        while not task.done():
            try:
                await asyncio.shield(
                    task
                )

            except asyncio.CancelledError as repeated:
                cancellation = repeated

        #
        # task.result() now either returns the completed runtime result
        # or raises the owner's own failure. An owner failure therefore
        # wins over external cancellation.
        #
        result = task.result()

    if not _runtime_result_contract_valid(
        result
    ):
        raise LiveSellSupervisorServiceError(
            LIVE_SELL_SUPERVISOR_RESULT_CONTRACT_INVALID
        )

    if cancellation is not None:
        raise cancellation

    return result


async def run_live_sell_supervisor_service(
    *,
    owner: LiveProcessOwner,
    supervisor_config: LiveSellSupervisorConfig,
    execution_config: LiveBuyExecutionConfig,
    stop_event: asyncio.Event,
) -> None:
    """
    Continuously supervise the existing one-shot live SELL runtime.

    Authority boundary:

        explicit supervisor policy/cadence
                +
        existing execution assumptions
                ↓
        one process-lifetime LiveProcessOwner
                ↓
        owner.run_sell_once(...)
                ↓
        existing production SELL composition/runtime

    The service is deliberately serial:
      - one SELL invocation at a time;
      - no per-mint tasks;
      - no overlapping SELL authority;
      - no retry inside a tick;
      - no signer construction;
      - no direct RPC or SQLite access;
      - no transaction construction/signing/submission.

    Every completed valid runtime status is a completed tick, including
    BLOCK and UNKNOWN. Those statuses are fail-closed runtime outcomes,
    not service crashes. Structural contract violations and raised owner
    failures propagate.

    A stop request prevents the next tick. It does not cancel an
    already-running owner SELL invocation.
    """

    if not _components_are_compatible():
        raise LiveSellSupervisorServiceError(
            "LIVE_SELL_SUPERVISOR_"
            "COMPONENT_VERSION_MISMATCH"
        )

    if not isinstance(
        owner,
        LiveProcessOwner,
    ):
        raise TypeError(
            "owner must be LiveProcessOwner"
        )

    if not isinstance(
        supervisor_config,
        LiveSellSupervisorConfig,
    ):
        raise TypeError(
            "supervisor_config must be "
            "LiveSellSupervisorConfig"
        )

    if not isinstance(
        execution_config,
        LiveBuyExecutionConfig,
    ):
        raise TypeError(
            "execution_config must be "
            "LiveBuyExecutionConfig"
        )

    if not isinstance(
        stop_event,
        asyncio.Event,
    ):
        raise TypeError(
            "stop_event must be asyncio.Event"
        )

    if stop_event.is_set():
        return

    while True:
        evaluated_at = (
            _current_evaluated_at()
        )

        await _run_sell_tick(
            owner=owner,
            supervisor_config=(
                supervisor_config
            ),
            execution_config=(
                execution_config
            ),
            evaluated_at=evaluated_at,
        )

        if stop_event.is_set():
            return

        should_stop = (
            await _wait_until_stop_or_interval(
                stop_event=stop_event,
                interval_seconds=(
                    supervisor_config
                    .evaluation_interval_seconds
                ),
            )
        )

        if should_stop:
            return
