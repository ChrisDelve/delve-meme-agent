from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from src.execution.execution_quality_gate import (
    ABORT as EXECUTION_ABORT,
    GATE_VERSION as EXECUTION_GATE_VERSION,
    PASS as EXECUTION_PASS,
    UNKNOWN as EXECUTION_UNKNOWN,
    curve_state_from_live_curve,
    evaluate_execution_quality,
)
from src.execution.live_blockhash_context import (
    LIVE_BLOCKHASH_CONTEXT_VERSION,
    resolve_live_blockhash_context,
)
from src.execution.live_curve_state import (
    LivePumpCurveState,
)
from src.execution.live_pump_global_state import (
    LIVE_PUMP_GLOBAL_STATE_VERSION,
    resolve_live_pump_global_state,
)
from src.execution.order_authorization import (
    AUTHORIZATION_VERSION,
    AUTHORIZE,
    BUY,
    DENY as AUTHORIZATION_DENY,
    authorize_pump_buy,
)
from src.execution.pump_buy_v2_account_context import (
    PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION,
    resolve_pump_buy_v2_account_context,
)
from src.execution.pump_buy_v2_execution_preflight import (
    APPROVE as PREFLIGHT_APPROVE,
    DENY as PREFLIGHT_DENY,
    UNKNOWN as PREFLIGHT_UNKNOWN,
    PUMP_BUY_V2_EXECUTION_PREFLIGHT_VERSION,
    preflight_pump_buy_v2_execution,
)
from src.execution.pump_buy_v2_pre_sign_validation import (
    APPROVE as PRE_SIGN_APPROVE,
    DENY as PRE_SIGN_DENY,
    UNKNOWN as PRE_SIGN_UNKNOWN,
    PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION,
    validate_pump_buy_v2_pre_sign,
)
from src.execution.pump_buy_v2_signing import (
    BLOCK as SIGNING_BLOCK,
    PASS as SIGNING_PASS,
    UNKNOWN as SIGNING_UNKNOWN,
    PUMP_BUY_V2_SIGNING_VERSION,
    MessageSigner,
    sign_and_bind_pump_buy_v2,
)
from src.execution.pump_buy_v2_unsigned_message import (
    PUMP_BUY_V2_UNSIGNED_MESSAGE_VERSION,
    build_unsigned_pump_buy_v2_message,
)
from src.portfolio.live_pump_buy_reservation import (
    BLOCK as RESERVATION_BLOCK,
    PASS as RESERVATION_PASS,
    UNKNOWN as RESERVATION_UNKNOWN,
    LIVE_PUMP_BUY_RESERVATION_VERSION,
    reserve_live_pump_buy,
)
from src.portfolio.live_reservations import (
    ACTIVE,
    DB_PATH,
    RESERVATION_VERSION,
)
from src.risk.risk_governor import (
    RiskPolicy,
)
from src.safety.token_safety_gate import (
    PASS as SAFETY_PASS,
    REJECT as SAFETY_REJECT,
    UNKNOWN as SAFETY_UNKNOWN,
    TokenSafetyGateResult,
)


LIVE_BUY_ENTRY_EXECUTOR_VERSION = (
    "live-buy-entry-executor-v2"
)

SIGNED = "SIGNED"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"

VALIDATE = "VALIDATE"
RESERVE = "RESERVE"
EXECUTION = "EXECUTION"
AUTHORIZE = "AUTHORIZE"
GLOBAL_STATE = "GLOBAL_STATE"
ACCOUNT_CONTEXT = "ACCOUNT_CONTEXT"
BLOCKHASH = "BLOCKHASH"
MESSAGE = "MESSAGE"
PRE_SIGN = "PRE_SIGN"
PREFLIGHT = "PREFLIGHT"
SIGN = "SIGN"
COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class LiveBuyEntryExecutionResult:
    executor_version: str

    status: str
    stage: str
    reasons: tuple[str, ...]

    wallet_pubkey: str
    mint: str

    reservation_id: str | None
    authorization_version: str | None

    global_rpc_slot: int | None
    blockhash_rpc_slot: int | None

    message_sha256: str | None

    signing_version: str | None
    signing_status: str | None

    transaction_signature: str | None
    signed_transaction_sha256: str | None
    signed_at: float | None


def _nonempty_text(
    value: object,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        return ""

    return value.strip()


def _valid_sha256(
    value: object,
) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(
            character
            in "0123456789abcdef"
            for character in value
        )
    )


def _nonnegative_int(
    value: object,
) -> int | None:
    if (
        not isinstance(
            value,
            int,
        )
        or isinstance(
            value,
            bool,
        )
        or value < 0
    ):
        return None

    return value


def _positive_int(
    value: object,
) -> int | None:
    result = _nonnegative_int(
        value
    )

    if result is None or result <= 0:
        return None

    return result


def _positive_finite_number(
    value: object,
) -> float | None:
    if (
        not isinstance(
            value,
            (
                int,
                float,
            ),
        )
        or isinstance(
            value,
            bool,
        )
    ):
        return None

    result = float(
        value
    )

    if (
        not math.isfinite(
            result
        )
        or result <= 0.0
    ):
        return None

    return result


async def execute_live_buy_entry_once(
    *,
    mint: str,
    wallet_pubkey: str,

    protected_cash_lamports: int,

    live_curve: LivePumpCurveState,
    safety: TokenSafetyGateResult,

    signal_virtual_quote_reserves: int,
    signal_virtual_token_reserves: int,

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

    compute_unit_limit: int,
    signer: MessageSigner | None,

    policy: RiskPolicy,
    min_context_slot: int | None = None,
    db_path: Path = DB_PATH,
) -> LiveBuyEntryExecutionResult:
    """
    Reserve, authorize, prepare, validate, and durably sign
    at most one live Pump BUY.

    This is intentionally the only bounded pre-sign BUY
    orchestration path.

    Durable authority boundary:

        no reservation
            -> ACTIVE reservation
            -> exact live authorization
            -> exact account/message/network validation
            -> SIGNED reservation
            -> STOP

    This executor does NOT:
      - submit a transaction;
      - retry a transaction;
      - reconcile a signed transaction;
      - recover an orphaned ACTIVE reservation;
      - release an ACTIVE reservation after a later
        preparation failure;
      - select a strategy or candidate;
      - loop over multiple BUYs.

    If execution stops after ACTIVE but before SIGNED, the
    normal Reservation-v6 ACTIVE expiry mechanism remains
    responsible for eventually releasing that capital.
    """

    normalized_wallet = _nonempty_text(
        wallet_pubkey
    )
    normalized_mint = _nonempty_text(
        mint
    )

    reservation_id: str | None = None
    authorization_version: str | None = None

    global_rpc_slot: int | None = None
    blockhash_rpc_slot: int | None = None

    message_sha256: str | None = None

    signing_version: str | None = None
    signing_status: str | None = None

    transaction_signature: str | None = None
    signed_transaction_sha256: str | None = None
    signed_at: float | None = None

    def finish(
        status: str,
        stage: str,
        *reasons: str,
    ) -> LiveBuyEntryExecutionResult:
        return LiveBuyEntryExecutionResult(
            executor_version=(
                LIVE_BUY_ENTRY_EXECUTOR_VERSION
            ),
            status=status,
            stage=stage,
            reasons=tuple(
                reasons
            ),
            wallet_pubkey=(
                normalized_wallet
            ),
            mint=normalized_mint,
            reservation_id=(
                reservation_id
            ),
            authorization_version=(
                authorization_version
            ),
            global_rpc_slot=(
                global_rpc_slot
            ),
            blockhash_rpc_slot=(
                blockhash_rpc_slot
            ),
            message_sha256=(
                message_sha256
            ),
            signing_version=(
                signing_version
            ),
            signing_status=(
                signing_status
            ),
            transaction_signature=(
                transaction_signature
            ),
            signed_transaction_sha256=(
                signed_transaction_sha256
            ),
            signed_at=signed_at,
        )

    #
    # Local configuration must fail before capital is held.
    #
    if signer is None:
        return finish(
            UNKNOWN,
            VALIDATE,
            "LIVE_BUY_SIGNER_REQUIRED",
        )

    #
    # Production entry authority may not silently inherit the
    # generic/shadow RiskPolicy defaults.
    #
    if not isinstance(
        policy,
        RiskPolicy,
    ):
        return finish(
            UNKNOWN,
            VALIDATE,
            "LIVE_BUY_POLICY_REQUIRED",
        )

    signal_quote_reserves = _positive_int(
        signal_virtual_quote_reserves
    )

    signal_token_reserves = _positive_int(
        signal_virtual_token_reserves
    )

    if (
        signal_quote_reserves is None
        or signal_token_reserves is None
    ):
        return finish(
            UNKNOWN,
            VALIDATE,
            "LIVE_BUY_SIGNAL_RESERVES_INVALID",
        )

    #
    # Safety evidence already exists before this bounded
    # capital-authority path. Malformed/non-PASS evidence must
    # not create an ACTIVE reservation.
    #
    safety_status = getattr(
        safety,
        "status",
        None,
    )

    safety_snapshot = getattr(
        safety,
        "snapshot",
        None,
    )

    safety_reasons = tuple(
        getattr(
            safety,
            "reasons",
            (),
        )
        or ()
    )

    if safety_status == SAFETY_REJECT:
        return finish(
            BLOCK,
            VALIDATE,
            "LIVE_BUY_SAFETY_REJECTED",
            *safety_reasons,
        )

    if safety_status == SAFETY_UNKNOWN:
        return finish(
            UNKNOWN,
            VALIDATE,
            "LIVE_BUY_SAFETY_UNKNOWN",
            *safety_reasons,
        )

    if safety_status != SAFETY_PASS:
        return finish(
            UNKNOWN,
            VALIDATE,
            "LIVE_BUY_SAFETY_STATUS_INVALID",
        )

    if safety_snapshot is None:
        return finish(
            UNKNOWN,
            VALIDATE,
            "LIVE_BUY_SAFETY_SNAPSHOT_MISSING",
        )

    if (
        _nonempty_text(
            getattr(
                safety_snapshot,
                "mint",
                None,
            )
        )
        != normalized_mint
    ):
        return finish(
            UNKNOWN,
            VALIDATE,
            "LIVE_BUY_SAFETY_MINT_MISMATCH",
        )

    if (
        _nonempty_text(
            getattr(
                live_curve,
                "mint",
                None,
            )
        )
        != normalized_mint
    ):
        return finish(
            UNKNOWN,
            VALIDATE,
            "LIVE_BUY_CURVE_MINT_MISMATCH",
        )

    #
    # One market-state object owns both:
    #
    #   - reservation/risk simulation; and
    #   - execution-quality simulation.
    #
    # Do not accept a second caller-supplied PumpCurveState.
    #
    try:
        curve_state = (
            curve_state_from_live_curve(
                live_curve
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            VALIDATE,
            "LIVE_BUY_CURVE_STATE_DERIVATION_FAILED",
        )

    compute_limit = _positive_int(
        compute_unit_limit
    )

    if compute_limit is None:
        return finish(
            UNKNOWN,
            VALIDATE,
            "LIVE_BUY_COMPUTE_UNIT_LIMIT_INVALID",
        )

    authorization_age = (
        _positive_finite_number(
            max_authorization_age_seconds
        )
    )

    if authorization_age is None:
        return finish(
            UNKNOWN,
            VALIDATE,
            "LIVE_BUY_AUTHORIZATION_AGE_INVALID",
        )

    if (
        min_context_slot is not None
        and _nonnegative_int(
            min_context_slot
        )
        is None
    ):
        return finish(
            UNKNOWN,
            VALIDATE,
            "LIVE_BUY_MIN_CONTEXT_SLOT_INVALID",
        )

    try:
        normalized_path = Path(
            db_path
        )
    except Exception:
        return finish(
            UNKNOWN,
            VALIDATE,
            "LIVE_DATABASE_PATH_INVALID",
        )

    #
    # --------------------------------------------------------
    # 1. Durable capital reservation.
    # --------------------------------------------------------
    #
    try:
        reservation_result = (
            await reserve_live_pump_buy(
                mint=normalized_mint,
                wallet_pubkey=(
                    normalized_wallet
                ),
                protected_cash_lamports=(
                    protected_cash_lamports
                ),
                curve_state=curve_state,
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
            RESERVE,
            "LIVE_BUY_RESERVATION_EXCEPTION",
        )

    if (
        getattr(
            reservation_result,
            "resolver_version",
            None,
        )
        != LIVE_PUMP_BUY_RESERVATION_VERSION
    ):
        return finish(
            UNKNOWN,
            RESERVE,
            "LIVE_BUY_RESERVATION_VERSION_MISMATCH",
        )

    reservation_status = getattr(
        reservation_result,
        "status",
        None,
    )

    reservation_reasons = tuple(
        getattr(
            reservation_result,
            "reasons",
            (),
        )
        or ()
    )

    if reservation_status == RESERVATION_BLOCK:
        return finish(
            BLOCK,
            RESERVE,
            *reservation_reasons,
        )

    if reservation_status == RESERVATION_UNKNOWN:
        return finish(
            UNKNOWN,
            RESERVE,
            *reservation_reasons,
        )

    if reservation_status != RESERVATION_PASS:
        return finish(
            UNKNOWN,
            RESERVE,
            "LIVE_BUY_RESERVATION_STATUS_INVALID",
        )

    decision = getattr(
        reservation_result,
        "reservation_decision",
        None,
    )

    reservation = getattr(
        decision,
        "reservation",
        None,
    )

    if (
        decision is None
        or getattr(
            decision,
            "status",
            None,
        )
        != RESERVATION_PASS
        or reservation is None
    ):
        return finish(
            UNKNOWN,
            RESERVE,
            "LIVE_BUY_PASS_RESERVATION_MISSING",
        )

    reservation_id_value = _nonempty_text(
        getattr(
            reservation,
            "reservation_id",
            None,
        )
    )

    if (
        not reservation_id_value
        or getattr(
            reservation,
            "reservation_version",
            None,
        )
        != RESERVATION_VERSION
        or getattr(
            reservation,
            "status",
            None,
        )
        != ACTIVE
        or getattr(
            reservation,
            "side",
            None,
        )
        != BUY
        or getattr(
            reservation,
            "mint",
            None,
        )
        != normalized_mint
        or getattr(
            reservation,
            "wallet_pubkey",
            None,
        )
        != normalized_wallet
    ):
        return finish(
            UNKNOWN,
            RESERVE,
            "LIVE_BUY_RESERVATION_BINDING_INVALID",
        )

    reservation_id = (
        reservation_id_value
    )

    reserved_spend = _positive_int(
        getattr(
            reservation,
            "spend_lamports",
            None,
        )
    )

    if reserved_spend is None:
        return finish(
            UNKNOWN,
            RESERVE,
            "LIVE_BUY_RESERVED_SPEND_INVALID",
        )

    #
    # --------------------------------------------------------
    # 2. Execution quality for the EXACT atomically reserved
    #    spend.
    #
    # Reservation v6 has already accounted for concurrent
    # ACTIVE/SIGNED/SUBMITTED liabilities while holding its
    # BEGIN IMMEDIATE transaction. That reserved spend is now
    # the only order size execution evidence may evaluate.
    # --------------------------------------------------------
    #
    try:
        execution = evaluate_execution_quality(
            snapshot=safety_snapshot,
            live_curve=live_curve,
            signal_virtual_quote_reserves=(
                signal_quote_reserves
            ),
            signal_virtual_token_reserves=(
                signal_token_reserves
            ),
            spendable_quote_in=(
                reserved_spend
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            slippage_bps=(
                buy_slippage_bps
            ),
            base_network_fee_lamports=(
                buy_base_network_fee_lamports
            ),
            priority_fee_lamports=(
                buy_priority_fee_lamports
            ),
            rent_lamports=(
                buy_rent_lamports
            ),
        )

    except Exception:
        return finish(
            UNKNOWN,
            EXECUTION,
            "LIVE_BUY_EXECUTION_QUALITY_EXCEPTION",
        )

    execution_version = getattr(
        execution,
        "gate_version",
        None,
    )

    execution_status = getattr(
        execution,
        "status",
        None,
    )

    execution_reasons = tuple(
        getattr(
            execution,
            "reasons",
            (),
        )
        or ()
    )

    if (
        execution_version
        != EXECUTION_GATE_VERSION
        or getattr(
            execution,
            "mint",
            None,
        )
        != normalized_mint
        or getattr(
            execution,
            "spendable_quote_in",
            None,
        )
        != reserved_spend
        or getattr(
            execution,
            "protocol_fee_bps",
            None,
        )
        != protocol_fee_bps
        or getattr(
            execution,
            "creator_fee_bps",
            None,
        )
        != creator_fee_bps
        or getattr(
            execution,
            "slippage_bps",
            None,
        )
        != buy_slippage_bps
    ):
        return finish(
            UNKNOWN,
            EXECUTION,
            "LIVE_BUY_EXECUTION_BINDING_MISMATCH",
        )

    if execution_status == EXECUTION_ABORT:
        return finish(
            BLOCK,
            EXECUTION,
            *execution_reasons,
        )

    if execution_status == EXECUTION_UNKNOWN:
        return finish(
            UNKNOWN,
            EXECUTION,
            *execution_reasons,
        )

    if execution_status != EXECUTION_PASS:
        return finish(
            UNKNOWN,
            EXECUTION,
            "LIVE_BUY_EXECUTION_STATUS_INVALID",
        )

    if getattr(
        execution,
        "simulation",
        None,
    ) is None:
        return finish(
            UNKNOWN,
            EXECUTION,
            "LIVE_BUY_EXECUTION_PASS_SIMULATION_MISSING",
        )

    #
    # --------------------------------------------------------
    # 3. Immutable BUY authorization bound to BOTH:
    #
    #      - the exact ACTIVE reservation; and
    #      - the exact execution simulation above.
    #
    # authorize_pump_buy() independently proves the execution
    # spend and simulation fingerprint against Reservation-v6.
    # --------------------------------------------------------
    #
    try:
        authorization = authorize_pump_buy(
            mint=normalized_mint,
            requested_spend_lamports=(
                reservation.spend_lamports
            ),
            reservation_id=(
                reservation_id
            ),
            live_curve=live_curve,
            safety=safety,
            execution=execution,
            max_authorization_age_seconds=(
                authorization_age
            ),
            reservation_db_path=(
                normalized_path
            ),
        )

    except Exception:
        return finish(
            UNKNOWN,
            AUTHORIZE,
            "LIVE_BUY_AUTHORIZATION_EXCEPTION",
        )

    authorization_version = getattr(
        authorization,
        "authorization_version",
        None,
    )

    authorization_status = getattr(
        authorization,
        "status",
        None,
    )

    authorization_reasons = tuple(
        getattr(
            authorization,
            "reasons",
            (),
        )
        or ()
    )

    if authorization_status == AUTHORIZATION_DENY:
        return finish(
            BLOCK,
            AUTHORIZE,
            *authorization_reasons,
        )

    if authorization_status != AUTHORIZE:
        return finish(
            UNKNOWN,
            AUTHORIZE,
            "LIVE_BUY_AUTHORIZATION_STATUS_INVALID",
        )

    if (
        authorization_version
        != AUTHORIZATION_VERSION
        or getattr(
            authorization,
            "reservation_id",
            None,
        )
        != reservation_id
        or getattr(
            authorization,
            "reservation_version",
            None,
        )
        != reservation.reservation_version
        or getattr(
            authorization,
            "mint",
            None,
        )
        != reservation.mint
        or getattr(
            authorization,
            "side",
            None,
        )
        != BUY
        or getattr(
            authorization,
            "wallet_pubkey",
            None,
        )
        != reservation.wallet_pubkey
        or getattr(
            authorization,
            "spend_lamports",
            None,
        )
        != reservation.spend_lamports
        or getattr(
            authorization,
            "wallet_cost_lamports",
            None,
        )
        != reservation.wallet_cost_lamports
        or getattr(
            authorization,
            "risk_governor_version",
            None,
        )
        != reservation.risk_governor_version
        or getattr(
            authorization,
            "simulation_sha256",
            None,
        )
        != reservation.risk_simulation_sha256
    ):
        return finish(
            UNKNOWN,
            AUTHORIZE,
            "LIVE_BUY_AUTHORIZATION_BINDING_MISMATCH",
        )

    try:
        authorization_valid = (
            authorization.is_valid()
        )
    except Exception:
        return finish(
            UNKNOWN,
            AUTHORIZE,
            "LIVE_BUY_AUTHORIZATION_VALIDITY_FAILED",
        )

    if not authorization_valid:
        return finish(
            BLOCK,
            AUTHORIZE,
            "LIVE_BUY_AUTHORIZATION_EXPIRED",
        )

    curve_rpc_slot = _nonnegative_int(
        getattr(
            authorization,
            "curve_rpc_slot",
            None,
        )
    )

    if curve_rpc_slot is None:
        return finish(
            UNKNOWN,
            AUTHORIZE,
            "LIVE_BUY_CURVE_SLOT_INVALID",
        )

    #
    # --------------------------------------------------------
    # 3. Pump Global must not predate the curve state that
    #    entered the immutable BUY authorization.
    # --------------------------------------------------------
    #
    try:
        global_state = (
            await resolve_live_pump_global_state(
                min_context_slot=(
                    curve_rpc_slot
                )
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            GLOBAL_STATE,
            "LIVE_BUY_GLOBAL_STATE_RESOLUTION_FAILED",
        )

    if (
        getattr(
            global_state,
            "resolver_version",
            None,
        )
        != LIVE_PUMP_GLOBAL_STATE_VERSION
    ):
        return finish(
            UNKNOWN,
            GLOBAL_STATE,
            "LIVE_BUY_GLOBAL_STATE_VERSION_MISMATCH",
        )

    global_rpc_slot = _nonnegative_int(
        getattr(
            global_state,
            "rpc_slot",
            None,
        )
    )

    if (
        global_rpc_slot is None
        or global_rpc_slot
        < curve_rpc_slot
    ):
        return finish(
            UNKNOWN,
            GLOBAL_STATE,
            "LIVE_BUY_GLOBAL_STATE_SLOT_INVALID",
        )

    #
    # --------------------------------------------------------
    # 4. Exact account context.
    # --------------------------------------------------------
    #
    try:
        context = (
            resolve_pump_buy_v2_account_context(
                authorization=(
                    authorization
                ),
                global_state=global_state,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            ACCOUNT_CONTEXT,
            "LIVE_BUY_ACCOUNT_CONTEXT_BUILD_FAILED",
        )

    if (
        getattr(
            context,
            "resolver_version",
            None,
        )
        != PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION
        or getattr(
            context,
            "authorization_version",
            None,
        )
        != AUTHORIZATION_VERSION
        or getattr(
            context,
            "reservation_id",
            None,
        )
        != reservation_id
        or getattr(
            context,
            "simulation_sha256",
            None,
        )
        != authorization.simulation_sha256
        or getattr(
            context,
            "global_rpc_slot",
            None,
        )
        != global_rpc_slot
        or getattr(
            context,
            "user",
            None,
        )
        != authorization.wallet_pubkey
        or getattr(
            context,
            "amount",
            None,
        )
        != authorization.token_amount
        or getattr(
            context,
            "max_sol_cost",
            None,
        )
        != authorization.max_sol_cost
    ):
        return finish(
            UNKNOWN,
            ACCOUNT_CONTEXT,
            "LIVE_BUY_ACCOUNT_CONTEXT_BINDING_MISMATCH",
        )

    #
    # --------------------------------------------------------
    # 5. Fresh blockhash. BUY pre-sign defines the chain
    #    floor as max(curve slot, Pump Global slot).
    # --------------------------------------------------------
    #
    blockhash_min_context_slot = max(
        curve_rpc_slot,
        global_rpc_slot,
    )

    try:
        blockhash_context = (
            await resolve_live_blockhash_context(
                min_context_slot=(
                    blockhash_min_context_slot
                )
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            BLOCKHASH,
            "LIVE_BUY_BLOCKHASH_RESOLUTION_FAILED",
        )

    if (
        getattr(
            blockhash_context,
            "resolver_version",
            None,
        )
        != LIVE_BLOCKHASH_CONTEXT_VERSION
    ):
        return finish(
            UNKNOWN,
            BLOCKHASH,
            "LIVE_BUY_BLOCKHASH_VERSION_MISMATCH",
        )

    blockhash_rpc_slot = _nonnegative_int(
        getattr(
            blockhash_context,
            "rpc_slot",
            None,
        )
    )

    if (
        getattr(
            blockhash_context,
            "min_context_slot",
            None,
        )
        != blockhash_min_context_slot
        or blockhash_rpc_slot is None
        or blockhash_rpc_slot
        < blockhash_min_context_slot
    ):
        return finish(
            UNKNOWN,
            BLOCKHASH,
            "LIVE_BUY_BLOCKHASH_SLOT_INVALID",
        )

    #
    # --------------------------------------------------------
    # 6. Pure unsigned message construction.
    # --------------------------------------------------------
    #
    try:
        message_plan = (
            build_unsigned_pump_buy_v2_message(
                authorization=(
                    authorization
                ),
                context=context,
                blockhash_context=(
                    blockhash_context
                ),
                compute_unit_limit=(
                    compute_limit
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            MESSAGE,
            "LIVE_BUY_MESSAGE_BUILD_FAILED",
        )

    message_sha256 = getattr(
        message_plan,
        "message_sha256",
        None,
    )

    if (
        getattr(
            message_plan,
            "builder_version",
            None,
        )
        != PUMP_BUY_V2_UNSIGNED_MESSAGE_VERSION
        or getattr(
            message_plan,
            "authorization_version",
            None,
        )
        != AUTHORIZATION_VERSION
        or getattr(
            message_plan,
            "account_context_version",
            None,
        )
        != PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION
        or getattr(
            message_plan,
            "reservation_id",
            None,
        )
        != reservation_id
        or getattr(
            message_plan,
            "simulation_sha256",
            None,
        )
        != authorization.simulation_sha256
        or getattr(
            message_plan,
            "payer",
            None,
        )
        != authorization.wallet_pubkey
        or getattr(
            message_plan,
            "blockhash_context_version",
            None,
        )
        != LIVE_BLOCKHASH_CONTEXT_VERSION
        or getattr(
            message_plan,
            "recent_blockhash",
            None,
        )
        != getattr(
            blockhash_context,
            "blockhash",
            None,
        )
        or getattr(
            message_plan,
            "last_valid_block_height",
            None,
        )
        != getattr(
            blockhash_context,
            "last_valid_block_height",
            None,
        )
        or getattr(
            message_plan,
            "blockhash_rpc_slot",
            None,
        )
        != blockhash_rpc_slot
        or getattr(
            message_plan,
            "compute_unit_limit",
            None,
        )
        != compute_limit
        or not _valid_sha256(
            message_sha256
        )
    ):
        return finish(
            UNKNOWN,
            MESSAGE,
            "LIVE_BUY_MESSAGE_BINDING_MISMATCH",
        )

    #
    # --------------------------------------------------------
    # 7. Final network/fee validation.
    # --------------------------------------------------------
    #
    try:
        network_validation = (
            await validate_pump_buy_v2_pre_sign(
                authorization=(
                    authorization
                ),
                context=context,
                message_plan=(
                    message_plan
                ),
                db_path=normalized_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            PRE_SIGN,
            "LIVE_BUY_PRE_SIGN_EXCEPTION",
        )

    if (
        getattr(
            network_validation,
            "validator_version",
            None,
        )
        != PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION
    ):
        return finish(
            UNKNOWN,
            PRE_SIGN,
            "LIVE_BUY_PRE_SIGN_VERSION_MISMATCH",
        )

    pre_sign_status = getattr(
        network_validation,
        "status",
        None,
    )

    pre_sign_reasons = tuple(
        getattr(
            network_validation,
            "reasons",
            (),
        )
        or ()
    )

    if pre_sign_status == PRE_SIGN_DENY:
        return finish(
            BLOCK,
            PRE_SIGN,
            *pre_sign_reasons,
        )

    if pre_sign_status == PRE_SIGN_UNKNOWN:
        return finish(
            UNKNOWN,
            PRE_SIGN,
            *pre_sign_reasons,
        )

    if pre_sign_status != PRE_SIGN_APPROVE:
        return finish(
            UNKNOWN,
            PRE_SIGN,
            "LIVE_BUY_PRE_SIGN_STATUS_INVALID",
        )

    #
    # --------------------------------------------------------
    # 9. Exact execution simulation/preflight.
    # --------------------------------------------------------
    #
    try:
        execution_preflight = (
            await preflight_pump_buy_v2_execution(
                authorization=(
                    authorization
                ),
                context=context,
                message_plan=(
                    message_plan
                ),
                network_validation=(
                    network_validation
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            PREFLIGHT,
            "LIVE_BUY_EXECUTION_PREFLIGHT_EXCEPTION",
        )

    if (
        getattr(
            execution_preflight,
            "validator_version",
            None,
        )
        != PUMP_BUY_V2_EXECUTION_PREFLIGHT_VERSION
    ):
        return finish(
            UNKNOWN,
            PREFLIGHT,
            "LIVE_BUY_EXECUTION_PREFLIGHT_VERSION_MISMATCH",
        )

    preflight_status = getattr(
        execution_preflight,
        "status",
        None,
    )

    preflight_reasons = tuple(
        getattr(
            execution_preflight,
            "reasons",
            (),
        )
        or ()
    )

    if preflight_status == PREFLIGHT_DENY:
        return finish(
            BLOCK,
            PREFLIGHT,
            *preflight_reasons,
        )

    if preflight_status == PREFLIGHT_UNKNOWN:
        return finish(
            UNKNOWN,
            PREFLIGHT,
            *preflight_reasons,
        )

    if preflight_status != PREFLIGHT_APPROVE:
        return finish(
            UNKNOWN,
            PREFLIGHT,
            "LIVE_BUY_EXECUTION_PREFLIGHT_STATUS_INVALID",
        )

    #
    # --------------------------------------------------------
    # 10. Sign and durably bind. STOP after SIGNED.
    # --------------------------------------------------------
    #
    try:
        signing = (
            await sign_and_bind_pump_buy_v2(
                authorization=(
                    authorization
                ),
                context=context,
                message_plan=(
                    message_plan
                ),
                network_validation=(
                    network_validation
                ),
                execution_preflight=(
                    execution_preflight
                ),
                signer=signer,
                db_path=normalized_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            SIGN,
            "LIVE_BUY_SIGNING_EXCEPTION",
        )

    signing_version = getattr(
        signing,
        "signer_version",
        None,
    )

    signing_status = getattr(
        signing,
        "status",
        None,
    )

    signing_reasons = tuple(
        getattr(
            signing,
            "reasons",
            (),
        )
        or ()
    )

    transaction_signature = getattr(
        signing,
        "transaction_signature",
        None,
    )

    signed_transaction_sha256 = getattr(
        signing,
        "signed_transaction_sha256",
        None,
    )

    signed_at = getattr(
        signing,
        "signed_at",
        None,
    )

    signing_reservation_id = getattr(
        signing,
        "reservation_id",
        None,
    )

    signing_signer_pubkey = getattr(
        signing,
        "signer_pubkey",
        None,
    )

    signing_message_sha256 = getattr(
        signing,
        "message_sha256",
        None,
    )

    signing_blockhash_context_version = getattr(
        signing,
        "blockhash_context_version",
        None,
    )

    signing_last_valid_block_height = getattr(
        signing,
        "last_valid_block_height",
        None,
    )

    signing_blockhash_rpc_slot = getattr(
        signing,
        "blockhash_rpc_slot",
        None,
    )

    if (
        signing_version
        != PUMP_BUY_V2_SIGNING_VERSION
    ):
        return finish(
            UNKNOWN,
            SIGN,
            "LIVE_BUY_SIGNING_VERSION_MISMATCH",
        )

    if signing_status == SIGNING_BLOCK:
        return finish(
            BLOCK,
            SIGN,
            *signing_reasons,
        )

    if signing_status == SIGNING_UNKNOWN:
        return finish(
            UNKNOWN,
            SIGN,
            *signing_reasons,
        )

    if signing_status != SIGNING_PASS:
        return finish(
            UNKNOWN,
            SIGN,
            "LIVE_BUY_SIGNING_STATUS_INVALID",
        )

    if not bool(
        getattr(
            signing,
            "is_durably_signed",
            False,
        )
    ):
        return finish(
            UNKNOWN,
            SIGN,
            "LIVE_BUY_SIGNING_NOT_DURABLE",
        )

    #
    # The child has crossed the durable signing boundary.
    # Before this coordinator reports SIGNED, prove the
    # returned artifact is still the exact authority chain
    # assembled above.
    #
    if (
        signing_reservation_id
        != reservation_id
        or _nonempty_text(
            signing_signer_pubkey
        )
        != authorization.wallet_pubkey
        or signing_message_sha256
        != message_sha256
        or signing_blockhash_context_version
        != LIVE_BLOCKHASH_CONTEXT_VERSION
        or signing_last_valid_block_height
        != getattr(
            message_plan,
            "last_valid_block_height",
            None,
        )
        or signing_blockhash_rpc_slot
        != blockhash_rpc_slot
    ):
        return finish(
            UNKNOWN,
            SIGN,
            "LIVE_BUY_SIGNING_BINDING_MISMATCH",
        )

    if (
        not _nonempty_text(
            transaction_signature
        )
        or not _valid_sha256(
            signed_transaction_sha256
        )
        or _positive_finite_number(
            signed_at
        )
        is None
    ):
        return finish(
            UNKNOWN,
            SIGN,
            "LIVE_BUY_SIGNED_ARTIFACT_INVALID",
        )

    return finish(
        SIGNED,
        COMPLETE,
    )
