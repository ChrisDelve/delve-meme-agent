from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solders.pubkey import Pubkey

from src.execution.live_buy_entry_executor import (
    BLOCK as ENTRY_BLOCK,
    SIGNED as ENTRY_SIGNED,
    UNKNOWN as ENTRY_UNKNOWN,
    LIVE_BUY_ENTRY_EXECUTOR_VERSION,
    LiveBuyEntryExecutionResult,
    execute_live_buy_entry_once,
)
from src.execution.live_buy_recovery_executor import (
    ADVANCED as RECOVERY_ADVANCED,
    BLOCK as RECOVERY_BLOCK,
    HOLD as RECOVERY_HOLD,
    IDLE as RECOVERY_IDLE,
    RECONCILED as RECOVERY_RECONCILED,
    UNKNOWN as RECOVERY_UNKNOWN,
    LIVE_BUY_RECOVERY_EXECUTOR_VERSION,
    LiveBuyRecoveryExecutionResult,
    recover_one_live_buy_once,
)
from src.execution.live_curve_state import (
    LivePumpCurveState,
)
from src.execution.model_entry_candidate import (
    ModelEntryCandidate,
)
from src.execution.pump_buy_v2_signing import (
    MessageSigner,
)
from src.portfolio.live_reservations import (
    DB_PATH,
)
from src.risk.risk_governor import (
    RiskPolicy,
)
from src.safety.token_safety_gate import (
    TokenSafetyGateResult,
)


LIVE_BUY_RUNTIME_VERSION = (
    "live-buy-runtime-v4"
)

IDLE = "IDLE"
HALTED = "HALTED"

ADVANCED = "ADVANCED"
RECONCILED = "RECONCILED"
HOLD = "HOLD"

SIGNED = "SIGNED"

BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"

INIT = "INIT"
RECOVERY = "RECOVERY"
KILL = "KILL"
ENTRY = "ENTRY"
COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class LiveBuyRuntimeResult:
    runtime_version: str

    status: str
    stage: str
    reasons: tuple[str, ...]

    recovery: (
        LiveBuyRecoveryExecutionResult
        | None
    )

    entry: (
        LiveBuyEntryExecutionResult
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
            LiveBuyRecoveryExecutionResult,
        )
        and result.executor_version
        == LIVE_BUY_RECOVERY_EXECUTOR_VERSION
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


def _entry_contract_valid(
    result: Any,
) -> bool:
    return (
        isinstance(
            result,
            LiveBuyEntryExecutionResult,
        )
        and result.executor_version
        == LIVE_BUY_ENTRY_EXECUTOR_VERSION
        and result.status
        in (
            ENTRY_SIGNED,
            ENTRY_BLOCK,
            ENTRY_UNKNOWN,
        )
        and _valid_reasons(
            result.reasons
        )
    )


async def run_live_buy_once(
    *,
    kill_switch: bool,

    mint: str,
    wallet_pubkey: str,

    protected_cash_lamports: int,

    live_curve: LivePumpCurveState,
    safety: TokenSafetyGateResult,

    candidate: ModelEntryCandidate | None = None,

    signal_virtual_quote_reserves: int | None = None,
    signal_virtual_token_reserves: int | None = None,

    protocol_fee_bps: int,
    creator_fee_bps: int,

    buy_slippage_bps: int,
    buy_base_network_fee_lamports: int,
    buy_priority_fee_lamports: int,
    buy_rent_lamports: int,

    exit_slippage_bps: int,
    exit_base_network_fee_lamports: int,
    exit_priority_fee_lamports: int,

    reservation_ttl_seconds: float,
    max_authorization_age_seconds: float,

    compute_unit_limit: int | None,
    signer: MessageSigner | None,

    policy: RiskPolicy | None = None,
    min_context_slot: int | None = None,
    db_path: Path = DB_PATH,
) -> LiveBuyRuntimeResult:
    """
    Coordinate at most one consequential live-BUY
    authority path.

    Priority:

      1. recover one existing execution-bearing BUY;
      2. if operational kill is active, stop;
      3. prove fresh-entry signer availability and identity;
      4. admit/create/prepare/sign at most one fresh BUY entry.

    Recovery always has priority over fresh entry work.

    Kill semantics:

      - recovery remains enabled;
      - recovery receives allow_submission=False;
      - existing ARMED/SUBMITTED obligations may still
        reconcile;
      - an existing PRISTINE_SIGNED BUY may not relay;
      - no fresh reservation, authorization, construction,
        preflight, or signing may occur.

    Fresh-entry semantics:

      - the entry child may move no reservation -> ACTIVE
        -> SIGNED;
      - the runtime stops after that child returns;
      - a freshly signed BUY is never submitted in the
        same runtime invocation.

    This coordinator performs no direct:
      - discovery;
      - reservation;
      - authorization;
      - transaction construction;
      - signing;
      - submission;
      - reconciliation;
      - database mutation;
      - retry;
      - loop;
      - candidate/strategy selection.

    Entry-specific validation deliberately remains inside
    execute_live_buy_entry_once() except for signer availability
    and identity. Those are proven here only after recovery and
    kill, but before the child may reserve capital.

    Bad fresh-entry inputs must never suppress higher-priority
    recovery.
    """

    recovery_result: (
        LiveBuyRecoveryExecutionResult
        | None
    ) = None

    entry_result: (
        LiveBuyEntryExecutionResult
        | None
    ) = None

    def finish(
        status: str,
        stage: str,
        *reasons: str,
    ) -> LiveBuyRuntimeResult:
        return LiveBuyRuntimeResult(
            runtime_version=(
                LIVE_BUY_RUNTIME_VERSION
            ),
            status=status,
            stage=stage,
            reasons=tuple(
                reasons
            ),
            recovery=recovery_result,
            entry=entry_result,
        )

    #
    # --------------------------------------------------------
    # Only coordinator inputs required for recovery are
    # validated before recovery.
    #
    # Do not validate signer, compute limit, candidate
    # evidence, or BUY economics here. Recovery must remain
    # reachable even when fresh-entry configuration is bad.
    # --------------------------------------------------------
    #
    if not isinstance(
        kill_switch,
        bool,
    ):
        return finish(
            UNKNOWN,
            INIT,
            "LIVE_BUY_RUNTIME_KILL_SWITCH_INVALID",
        )

    try:
        normalized_path = Path(
            db_path
        )
    except Exception:
        return finish(
            UNKNOWN,
            INIT,
            "LIVE_BUY_RUNTIME_DATABASE_PATH_INVALID",
        )

    #
    # --------------------------------------------------------
    # 1. Existing execution-bearing BUY always has priority.
    # --------------------------------------------------------
    #
    try:
        recovery_result = (
            await recover_one_live_buy_once(
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
            "LIVE_BUY_RUNTIME_RECOVERY_EXCEPTION",
        )

    if not _recovery_contract_valid(
        recovery_result
    ):
        return finish(
            UNKNOWN,
            RECOVERY,
            "LIVE_BUY_RUNTIME_RECOVERY_CONTRACT_INVALID",
        )

    if (
        kill_switch
        and recovery_result.relay_invoked
    ):
        return finish(
            UNKNOWN,
            RECOVERY,
            "LIVE_BUY_RUNTIME_KILL_RELAY_VIOLATION",
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
                "LIVE_BUY_RUNTIME_RECOVERY_STATUS_UNRECOGNIZED",
            )

        return finish(
            runtime_status,
            RECOVERY,
            "LIVE_BUY_RUNTIME_RECOVERY_STOP",
            *recovery_result.reasons,
        )

    #
    # --------------------------------------------------------
    # 2. Operational kill.
    #
    # Recovery above was still allowed to inspect and
    # reconcile existing obligations, but with relay
    # authority disabled.
    #
    # Nothing below this point may create or sign new BUY
    # authority.
    # --------------------------------------------------------
    #
    if kill_switch:
        return finish(
            HALTED,
            KILL,
            "LIVE_BUY_RUNTIME_KILL_ACTIVE",
        )

    #
    # --------------------------------------------------------
    # 3. Prove fresh-entry signer availability and identity.
    #
    # This occurs only after higher-priority recovery and kill.
    # It must occur before execute_live_buy_entry_once(), because
    # that child may create an ACTIVE capital reservation.
    #
    # With LazyEnvironmentMessageSigner this is the first point
    # at which environment-backed key authority is touched.
    # --------------------------------------------------------
    #
    if signer is None:
        return finish(
            UNKNOWN,
            ENTRY,
            "LIVE_BUY_RUNTIME_SIGNER_REQUIRED",
        )

    try:
        signer_pubkey = signer.pubkey()

    except Exception:
        return finish(
            UNKNOWN,
            ENTRY,
            "LIVE_BUY_RUNTIME_SIGNER_PUBKEY_FAILED",
        )

    if not isinstance(
        signer_pubkey,
        Pubkey,
    ):
        return finish(
            BLOCK,
            ENTRY,
            "LIVE_BUY_RUNTIME_SIGNER_PUBKEY_INVALID",
        )

    expected_wallet = (
        wallet_pubkey.strip()
        if isinstance(
            wallet_pubkey,
            str,
        )
        else None
    )

    if (
        expected_wallet
        and str(
            signer_pubkey
        )
        != expected_wallet
    ):
        return finish(
            BLOCK,
            ENTRY,
            "LIVE_BUY_RUNTIME_SIGNER_PUBKEY_MISMATCH",
        )

    #
    # --------------------------------------------------------
    # 4. Completely idle recovery permits one fresh bounded
    # BUY entry.
    #
    # Remaining fresh-entry validation belongs to the child.
    # --------------------------------------------------------
    #
    try:
        entry_result = (
            await execute_live_buy_entry_once(
                mint=mint,
                wallet_pubkey=(
                    wallet_pubkey
                ),
                protected_cash_lamports=(
                    protected_cash_lamports
                ),
                live_curve=live_curve,
                safety=safety,
                candidate=candidate,
                signal_virtual_quote_reserves=(
                    signal_virtual_quote_reserves
                ),
                signal_virtual_token_reserves=(
                    signal_virtual_token_reserves
                ),
                protocol_fee_bps=(
                    protocol_fee_bps
                ),
                creator_fee_bps=(
                    creator_fee_bps
                ),
                buy_slippage_bps=(
                    buy_slippage_bps
                ),
                buy_base_network_fee_lamports=(
                    buy_base_network_fee_lamports
                ),
                buy_priority_fee_lamports=(
                    buy_priority_fee_lamports
                ),
                buy_rent_lamports=(
                    buy_rent_lamports
                ),
                exit_slippage_bps=(
                    exit_slippage_bps
                ),
                exit_base_network_fee_lamports=(
                    exit_base_network_fee_lamports
                ),
                exit_priority_fee_lamports=(
                    exit_priority_fee_lamports
                ),
                reservation_ttl_seconds=(
                    reservation_ttl_seconds
                ),
                max_authorization_age_seconds=(
                    max_authorization_age_seconds
                ),
                compute_unit_limit=(
                    compute_unit_limit
                ),
                signer=signer,
                policy=policy,
                min_context_slot=(
                    min_context_slot
                ),
                db_path=normalized_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            ENTRY,
            "LIVE_BUY_RUNTIME_ENTRY_EXCEPTION",
        )

    if not _entry_contract_valid(
        entry_result
    ):
        return finish(
            UNKNOWN,
            ENTRY,
            "LIVE_BUY_RUNTIME_ENTRY_CONTRACT_INVALID",
        )

    if (
        entry_result.status
        == ENTRY_SIGNED
        and (
            entry_result.wallet_pubkey
            != (
                wallet_pubkey.strip()
                if isinstance(
                    wallet_pubkey,
                    str,
                )
                else wallet_pubkey
            )
            or entry_result.mint
            != (
                mint.strip()
                if isinstance(
                    mint,
                    str,
                )
                else mint
            )
        )
    ):
        return finish(
            UNKNOWN,
            ENTRY,
            "LIVE_BUY_RUNTIME_ENTRY_IDENTITY_MISMATCH",
        )

    if (
        entry_result.status
        == ENTRY_SIGNED
    ):
        return finish(
            SIGNED,
            ENTRY,
            "LIVE_BUY_RUNTIME_ENTRY_SIGNED",
            *entry_result.reasons,
        )

    if (
        entry_result.status
        == ENTRY_BLOCK
    ):
        return finish(
            BLOCK,
            ENTRY,
            "LIVE_BUY_RUNTIME_ENTRY_BLOCK",
            *entry_result.reasons,
        )

    if (
        entry_result.status
        == ENTRY_UNKNOWN
    ):
        return finish(
            UNKNOWN,
            ENTRY,
            "LIVE_BUY_RUNTIME_ENTRY_UNKNOWN",
            *entry_result.reasons,
        )

    return finish(
        UNKNOWN,
        ENTRY,
        "LIVE_BUY_RUNTIME_ENTRY_STATUS_UNRECOGNIZED",
    )
