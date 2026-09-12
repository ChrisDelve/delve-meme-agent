from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.live_exit_controller import (
    BLOCK as EXIT_BLOCK,
    CLAIMED as EXIT_CLAIMED,
    IDLE as EXIT_IDLE,
    UNKNOWN as EXIT_UNKNOWN,
    LIVE_EXIT_CONTROLLER_VERSION,
    LiveExitControllerResult,
    control_live_exit_once,
)
from src.execution.live_sell_recovery_executor import (
    ADVANCED as RECOVERY_ADVANCED,
    BLOCK as RECOVERY_BLOCK,
    HOLD as RECOVERY_HOLD,
    IDLE as RECOVERY_IDLE,
    RECONCILED as RECOVERY_RECONCILED,
    UNKNOWN as RECOVERY_UNKNOWN,
    LIVE_SELL_RECOVERY_EXECUTOR_VERSION,
    LiveSellRecoveryExecutionResult,
    recover_one_live_sell_once,
)
from src.execution.live_sell_unexecuted_executor import (
    BLOCK as SIGNING_BLOCK,
    IDLE as SIGNING_IDLE,
    SIGNED as SIGNING_SIGNED,
    UNKNOWN as SIGNING_UNKNOWN,
    LIVE_SELL_UNEXECUTED_EXECUTOR_VERSION,
    LiveSellUnexecutedExecutionResult,
    sign_one_unexecuted_live_sell_once,
)
from src.execution.pump_sell_v2_signing import (
    MessageSigner,
)
from src.portfolio.live_reservations import DB_PATH
from src.strategies.live_exit_policy import (
    LiveExitPolicy,
)


LIVE_SELL_RUNTIME_VERSION = (
    "live-sell-runtime-v1"
)

IDLE = "IDLE"
HALTED = "HALTED"
ADVANCED = "ADVANCED"
RECONCILED = "RECONCILED"
HOLD = "HOLD"
SIGNED = "SIGNED"
CLAIMED = "CLAIMED"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"

INIT = "INIT"
RECOVERY = "RECOVERY"
KILL = "KILL"
SIGNING = "SIGNING"
EXIT = "EXIT"
COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class LiveSellRuntimeResult:
    runtime_version: str

    status: str
    stage: str
    reasons: tuple[str, ...]

    recovery: (
        LiveSellRecoveryExecutionResult
        | None
    )

    signing: (
        LiveSellUnexecutedExecutionResult
        | None
    )

    controller: (
        LiveExitControllerResult
        | None
    )


def _valid_reasons(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            tuple,
        )
        and all(
            isinstance(
                reason,
                str,
            )
            and bool(
                reason
            )
            for reason in value
        )
    )


def _recovery_contract_valid(
    result: Any,
) -> bool:
    return (
        isinstance(
            result,
            LiveSellRecoveryExecutionResult,
        )
        and result.executor_version
        == LIVE_SELL_RECOVERY_EXECUTOR_VERSION
        and result.status
        in (
            RECOVERY_IDLE,
            RECOVERY_ADVANCED,
            RECOVERY_RECONCILED,
            RECOVERY_HOLD,
            RECOVERY_BLOCK,
            RECOVERY_UNKNOWN,
        )
        and _valid_reasons(
            result.reasons
        )
    )


def _signing_contract_valid(
    result: Any,
) -> bool:
    return (
        isinstance(
            result,
            LiveSellUnexecutedExecutionResult,
        )
        and result.executor_version
        == LIVE_SELL_UNEXECUTED_EXECUTOR_VERSION
        and result.status
        in (
            SIGNING_IDLE,
            SIGNING_SIGNED,
            SIGNING_BLOCK,
            SIGNING_UNKNOWN,
        )
        and _valid_reasons(
            result.reasons
        )
    )


def _controller_contract_valid(
    result: Any,
) -> bool:
    return (
        isinstance(
            result,
            LiveExitControllerResult,
        )
        and result.controller_version
        == LIVE_EXIT_CONTROLLER_VERSION
        and result.status
        in (
            EXIT_IDLE,
            EXIT_CLAIMED,
            EXIT_BLOCK,
            EXIT_UNKNOWN,
        )
        and _valid_reasons(
            result.reasons
        )
    )


async def run_live_sell_once(
    *,
    kill_switch: bool,

    signer: MessageSigner | None,
    compute_unit_limit: int | None,

    wallet_pubkey: str,
    evaluated_at: int,
    policy: LiveExitPolicy,

    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,

    min_context_slot: int | None = None,
    db_path: Path = DB_PATH,
) -> LiveSellRuntimeResult:
    """
    Coordinate at most one consequential live-SELL
    authority transition.

    Priority:
      1. recover existing execution-bearing SELL;
      2. if kill is active, stop;
      3. sign one existing claimed/unexecuted SELL;
      4. evaluate/initiate at most one new exit.

    One invocation never intentionally performs more than
    one consequential forward authority transition.

    Kill semantics:
      - recovery remains enabled;
      - recovery is reconciliation-only and may not relay
        a SIGNED transaction;
      - signing is disabled;
      - new SELL authorization/claim is disabled.

    This coordinator does NOT:
      - discover execution state directly;
      - build transactions directly;
      - sign directly;
      - submit directly;
      - reconcile directly;
      - authorize/claim directly;
      - retry;
      - loop;
      - process multiple SELLs.
    """

    recovery_result: (
        LiveSellRecoveryExecutionResult
        | None
    ) = None

    signing_result: (
        LiveSellUnexecutedExecutionResult
        | None
    ) = None

    controller_result: (
        LiveExitControllerResult
        | None
    ) = None

    def finish(
        status: str,
        stage: str,
        *reasons: str,
    ) -> LiveSellRuntimeResult:
        return LiveSellRuntimeResult(
            runtime_version=(
                LIVE_SELL_RUNTIME_VERSION
            ),
            status=status,
            stage=stage,
            reasons=tuple(
                reasons
            ),
            recovery=recovery_result,
            signing=signing_result,
            controller=controller_result,
        )

    # --------------------------------------------------------
    # Only coordinator-level inputs required to perform the
    # highest-priority recovery step are validated here.
    #
    # Do NOT validate signer/compute/controller inputs before
    # recovery. Doing so could suppress required reconciliation.
    # --------------------------------------------------------

    if not isinstance(
        kill_switch,
        bool,
    ):
        return finish(
            UNKNOWN,
            INIT,
            "LIVE_SELL_RUNTIME_KILL_SWITCH_INVALID",
        )

    try:
        normalized_path = Path(
            db_path
        )
    except Exception:
        return finish(
            UNKNOWN,
            INIT,
            "LIVE_SELL_RUNTIME_DATABASE_PATH_INVALID",
        )

    # --------------------------------------------------------
    # 1. Existing execution-bearing SELL always has priority.
    # --------------------------------------------------------

    try:
        recovery_result = (
            await recover_one_live_sell_once(
                allow_submission=(
                    not kill_switch
                ),
                db_path=normalized_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            RECOVERY,
            "LIVE_SELL_RUNTIME_RECOVERY_EXCEPTION",
        )

    if not _recovery_contract_valid(
        recovery_result
    ):
        return finish(
            UNKNOWN,
            RECOVERY,
            "LIVE_SELL_RUNTIME_RECOVERY_CONTRACT_INVALID",
        )

    if (
        recovery_result.status
        != RECOVERY_IDLE
    ):
        status_map = {
            RECOVERY_ADVANCED: ADVANCED,
            RECOVERY_RECONCILED: RECONCILED,
            RECOVERY_HOLD: HOLD,
            RECOVERY_BLOCK: BLOCK,
            RECOVERY_UNKNOWN: UNKNOWN,
        }

        runtime_status = status_map.get(
            recovery_result.status
        )

        if runtime_status is None:
            return finish(
                UNKNOWN,
                RECOVERY,
                "LIVE_SELL_RUNTIME_RECOVERY_STATUS_UNRECOGNIZED",
            )

        return finish(
            runtime_status,
            RECOVERY,
            "LIVE_SELL_RUNTIME_RECOVERY_STOP",
            *recovery_result.reasons,
        )

    # --------------------------------------------------------
    # 2. Operational kill:
    #
    # Recovery above was allowed to inspect/reconcile existing
    # obligations with allow_submission=False.
    #
    # Nothing below this point may sign or create new authority.
    # --------------------------------------------------------

    if kill_switch:
        return finish(
            HALTED,
            KILL,
            "LIVE_SELL_RUNTIME_KILL_ACTIVE",
        )

    # --------------------------------------------------------
    # 3. Existing claimed/unexecuted SELL precedes a new exit.
    #
    # Signer and compute-limit validation deliberately belongs
    # to this child executor and occurs only after recovery.
    # --------------------------------------------------------

    try:
        signing_result = (
            await sign_one_unexecuted_live_sell_once(
                signer=signer,
                compute_unit_limit=(
                    compute_unit_limit
                ),
                db_path=normalized_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            SIGNING,
            "LIVE_SELL_RUNTIME_SIGNING_EXCEPTION",
        )

    if not _signing_contract_valid(
        signing_result
    ):
        return finish(
            UNKNOWN,
            SIGNING,
            "LIVE_SELL_RUNTIME_SIGNING_CONTRACT_INVALID",
        )

    if (
        signing_result.status
        != SIGNING_IDLE
    ):
        status_map = {
            SIGNING_SIGNED: SIGNED,
            SIGNING_BLOCK: BLOCK,
            SIGNING_UNKNOWN: UNKNOWN,
        }

        runtime_status = status_map.get(
            signing_result.status
        )

        if runtime_status is None:
            return finish(
                UNKNOWN,
                SIGNING,
                "LIVE_SELL_RUNTIME_SIGNING_STATUS_UNRECOGNIZED",
            )

        return finish(
            runtime_status,
            SIGNING,
            "LIVE_SELL_RUNTIME_SIGNING_STOP",
            *signing_result.reasons,
        )

    # --------------------------------------------------------
    # 4. Only a completely idle recovery + signing pass may
    # evaluate and create one new SELL authorization/claim.
    # --------------------------------------------------------

    try:
        controller_result = (
            await control_live_exit_once(
                wallet_pubkey=wallet_pubkey,
                evaluated_at=evaluated_at,
                policy=policy,
                slippage_bps=slippage_bps,
                base_network_fee_lamports=(
                    base_network_fee_lamports
                ),
                priority_fee_lamports=(
                    priority_fee_lamports
                ),
                min_context_slot=(
                    min_context_slot
                ),
                db_path=normalized_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            EXIT,
            "LIVE_SELL_RUNTIME_EXIT_CONTROLLER_EXCEPTION",
        )

    if not _controller_contract_valid(
        controller_result
    ):
        return finish(
            UNKNOWN,
            EXIT,
            "LIVE_SELL_RUNTIME_EXIT_CONTROLLER_CONTRACT_INVALID",
        )

    if (
        controller_result.status
        == EXIT_CLAIMED
    ):
        return finish(
            CLAIMED,
            EXIT,
            "LIVE_SELL_RUNTIME_EXIT_CLAIMED",
            *controller_result.reasons,
        )

    if (
        controller_result.status
        == EXIT_BLOCK
    ):
        return finish(
            BLOCK,
            EXIT,
            "LIVE_SELL_RUNTIME_EXIT_BLOCK",
            *controller_result.reasons,
        )

    if (
        controller_result.status
        == EXIT_UNKNOWN
    ):
        return finish(
            UNKNOWN,
            EXIT,
            "LIVE_SELL_RUNTIME_EXIT_UNKNOWN",
            *controller_result.reasons,
        )

    if (
        controller_result.status
        != EXIT_IDLE
    ):
        return finish(
            UNKNOWN,
            EXIT,
            "LIVE_SELL_RUNTIME_EXIT_STATUS_UNRECOGNIZED",
        )

    return finish(
        IDLE,
        COMPLETE,
        "LIVE_SELL_RUNTIME_IDLE",
    )
