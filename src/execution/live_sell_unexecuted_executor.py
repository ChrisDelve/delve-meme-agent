from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.live_blockhash_context import (
    LIVE_BLOCKHASH_CONTEXT_VERSION,
    resolve_live_blockhash_context,
)
from src.execution.live_pump_global_state import (
    LIVE_PUMP_GLOBAL_STATE_VERSION,
    resolve_live_pump_global_state,
)
from src.execution.live_sell_unexecuted_discovery import (
    LIVE_SELL_UNEXECUTED_DISCOVERY_VERSION,
    PASS as DISCOVERY_PASS,
    LiveSellUnexecutedCandidate,
    discover_live_sell_unexecuted_candidates,
)
from src.execution.pump_sell_v2_account_context import (
    PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION,
    resolve_pump_sell_v2_account_context,
)
from src.execution.pump_sell_v2_pre_sign_validation import (
    APPROVE as PRE_SIGN_APPROVE,
    DENY as PRE_SIGN_DENY,
    UNKNOWN as PRE_SIGN_UNKNOWN,
    PUMP_SELL_V2_PRE_SIGN_VALIDATION_VERSION,
    validate_pump_sell_v2_pre_sign,
)
from src.execution.pump_sell_v2_signing import (
    BLOCK as SIGNING_BLOCK,
    PASS as SIGNING_PASS,
    UNKNOWN as SIGNING_UNKNOWN,
    PUMP_SELL_V2_SIGNING_VERSION,
    MessageSigner,
    sign_and_bind_pump_sell_v2,
)
from src.execution.pump_sell_v2_unsigned_message import (
    MAX_COMPUTE_UNIT_LIMIT,
    PUMP_SELL_V2_UNSIGNED_MESSAGE_VERSION,
    build_unsigned_pump_sell_v2_message,
)
from src.portfolio.live_reservations import (
    DB_PATH,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
)


LIVE_SELL_UNEXECUTED_EXECUTOR_VERSION = (
    "live-sell-unexecuted-executor-v2"
)

IDLE = "IDLE"
SIGNED = "SIGNED"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveSellUnexecutedExecutionResult:
    executor_version: str

    status: str
    reasons: tuple[str, ...]

    discovered_candidates: int

    authorization_sha256: str | None

    global_rpc_slot: int | None
    blockhash_min_context_slot: int | None
    blockhash_rpc_slot: int | None

    message_sha256: str | None

    pre_sign_status: str | None
    signing_status: str | None

    transaction_signature: str | None


def _valid_sha256(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            str,
        )
        and len(value) == 64
        and all(
            character
            in "0123456789abcdef"
            for character in value
        )
    )


def _nonnegative_int(
    value: Any,
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


def _candidate_contract_valid(
    candidate: Any,
) -> bool:
    authorization_sha256 = getattr(
        candidate,
        "authorization_sha256",
        None,
    )

    authorization = getattr(
        candidate,
        "authorization",
        None,
    )

    claim = getattr(
        candidate,
        "claim",
        None,
    )

    if (
        not _valid_sha256(
            authorization_sha256
        )
        or authorization is None
        or claim is None
    ):
        return False

    return (
        getattr(
            authorization,
            "authorization_sha256",
            None,
        )
        == authorization_sha256

        and getattr(
            claim,
            "authorization_sha256",
            None,
        )
        == authorization_sha256

        and getattr(
            claim,
            "status",
            None,
        )
        == ACTIVE

        and getattr(
            authorization,
            "wallet_pubkey",
            None,
        )
        == getattr(
            claim,
            "wallet_pubkey",
            None,
        )

        and getattr(
            authorization,
            "mint",
            None,
        )
        == getattr(
            claim,
            "mint",
            None,
        )

        and getattr(
            authorization,
            "tokens_to_sell",
            None,
        )
        == getattr(
            claim,
            "tokens_to_sell",
            None,
        )

        and getattr(
            authorization,
            "allocation",
            None,
        )
        == getattr(
            claim,
            "allocation",
            None,
        )
    )


async def sign_one_unexecuted_live_sell_once(
    *,
    signer: MessageSigner | None,
    compute_unit_limit: int,
    db_path: Path = DB_PATH,
) -> LiveSellUnexecutedExecutionResult:
    """
    Prepare and durably sign at most one ACTIVE,
    execution-free live SELL.

    The oldest validated unexecuted candidate is the
    only candidate considered during one invocation.

    This executor may:
    - discover one durable authorization;
    - resolve fresh Pump Global state;
    - resolve fresh blockhash state;
    - build one unsigned SELL message;
    - run final pre-sign validation;
    - delegate exactly one signing/binding attempt.

    This executor must NOT:
    - authorize a new SELL;
    - acquire or release a claim;
    - submit a transaction;
    - reconcile a transaction;
    - invoke the live SELL lifecycle;
    - process multiple candidates;
    - retry signing.
    """

    discovered_candidates = 0

    authorization_sha256: str | None = None

    global_rpc_slot: int | None = None
    blockhash_min_context_slot: int | None = None
    blockhash_rpc_slot: int | None = None

    message_sha256: str | None = None

    pre_sign_status: str | None = None
    signing_status: str | None = None

    transaction_signature: str | None = None

    def finish(
        status: str,
        *reasons: str,
    ) -> LiveSellUnexecutedExecutionResult:
        return LiveSellUnexecutedExecutionResult(
            executor_version=(
                LIVE_SELL_UNEXECUTED_EXECUTOR_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            discovered_candidates=(
                discovered_candidates
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            global_rpc_slot=(
                global_rpc_slot
            ),
            blockhash_min_context_slot=(
                blockhash_min_context_slot
            ),
            blockhash_rpc_slot=(
                blockhash_rpc_slot
            ),
            message_sha256=(
                message_sha256
            ),
            pre_sign_status=(
                pre_sign_status
            ),
            signing_status=(
                signing_status
            ),
            transaction_signature=(
                transaction_signature
            ),
        )

    try:
        normalized_path = Path(
            db_path
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_DATABASE_PATH_INVALID",
        )

    try:
        discovery = (
            discover_live_sell_unexecuted_candidates(
                db_path=normalized_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_SELL_UNEXECUTED_DISCOVERY_EXCEPTION",
        )

    if (
        getattr(
            discovery,
            "resolver_version",
            None,
        )
        != LIVE_SELL_UNEXECUTED_DISCOVERY_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_UNEXECUTED_DISCOVERY_VERSION_MISMATCH",
        )

    if (
        getattr(
            discovery,
            "status",
            None,
        )
        != DISCOVERY_PASS
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_UNEXECUTED_DISCOVERY_FAILED",
            *tuple(
                getattr(
                    discovery,
                    "reasons",
                    (),
                )
            ),
        )

    candidates = getattr(
        discovery,
        "candidates",
        None,
    )

    if not isinstance(
        candidates,
        tuple,
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_UNEXECUTED_CANDIDATES_INVALID",
        )

    discovered_candidates = len(
        candidates
    )

    if not candidates:
        return finish(
            IDLE,
        )

    candidate = candidates[0]

    if not _candidate_contract_valid(
        candidate
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_UNEXECUTED_CANDIDATE_INVALID",
        )

    #
    # Signing-only configuration is irrelevant until discovery
    # proves that an actual durable SELL candidate needs signing.
    #
    # Empty discovery above must remain IDLE even when the process
    # has no signer or signing compute configuration available.
    #
    if (
        not isinstance(
            compute_unit_limit,
            int,
        )
        or isinstance(
            compute_unit_limit,
            bool,
        )
        or compute_unit_limit <= 0
        or compute_unit_limit
        > MAX_COMPUTE_UNIT_LIMIT
    ):
        return finish(
            BLOCK,
            "LIVE_SELL_COMPUTE_UNIT_LIMIT_INVALID",
        )

    if signer is None:
        return finish(
            BLOCK,
            "LIVE_SELL_SIGNER_REQUIRED",
        )

    authorization = (
        candidate.authorization
    )

    authorization_sha256 = (
        candidate.authorization_sha256
    )

    authorization_fee_rpc_slot = (
        _nonnegative_int(
            getattr(
                authorization,
                "fee_rpc_slot",
                None,
            )
        )
    )

    if authorization_fee_rpc_slot is None:
        return finish(
            UNKNOWN,
            "LIVE_SELL_AUTHORIZATION_FEE_SLOT_INVALID",
        )

    #
    # Pump Global must not predate the economic
    # authorization that established the SELL fee state.
    #
    try:
        global_state = (
            await resolve_live_pump_global_state(
                min_context_slot=(
                    authorization_fee_rpc_slot
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_SELL_GLOBAL_STATE_RESOLUTION_FAILED",
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
            "LIVE_SELL_GLOBAL_STATE_VERSION_MISMATCH",
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
        < authorization_fee_rpc_slot
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_GLOBAL_STATE_SLOT_INVALID",
        )

    try:
        context = (
            resolve_pump_sell_v2_account_context(
                authorization=authorization,
                global_state=global_state,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_SELL_ACCOUNT_CONTEXT_BUILD_FAILED",
        )

    if (
        getattr(
            context,
            "resolver_version",
            None,
        )
        != PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_ACCOUNT_CONTEXT_VERSION_MISMATCH",
        )

    if (
        getattr(
            context,
            "authorization_sha256",
            None,
        )
        != authorization_sha256
        or getattr(
            context,
            "authorization_fee_rpc_slot",
            None,
        )
        != authorization_fee_rpc_slot
        or getattr(
            context,
            "global_rpc_slot",
            None,
        )
        != global_rpc_slot
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_ACCOUNT_CONTEXT_BINDING_MISMATCH",
        )

    blockhash_min_context_slot = max(
        authorization_fee_rpc_slot,
        global_rpc_slot,
    )

    try:
        blockhash_context = (
            await resolve_live_blockhash_context(
                min_context_slot=(
                    blockhash_min_context_slot
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_SELL_BLOCKHASH_RESOLUTION_FAILED",
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
            "LIVE_SELL_BLOCKHASH_VERSION_MISMATCH",
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
            "LIVE_SELL_BLOCKHASH_SLOT_INVALID",
        )

    try:
        message_plan = (
            build_unsigned_pump_sell_v2_message(
                authorization=authorization,
                context=context,
                blockhash_context=(
                    blockhash_context
                ),
                compute_unit_limit=(
                    compute_unit_limit
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_SELL_MESSAGE_BUILD_FAILED",
        )

    if (
        getattr(
            message_plan,
            "builder_version",
            None,
        )
        != PUMP_SELL_V2_UNSIGNED_MESSAGE_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_MESSAGE_VERSION_MISMATCH",
        )

    message_sha256 = getattr(
        message_plan,
        "message_sha256",
        None,
    )

    if (
        not _valid_sha256(
            message_sha256
        )
        or getattr(
            message_plan,
            "authorization_sha256",
            None,
        )
        != authorization_sha256
        or getattr(
            message_plan,
            "authorization_fee_rpc_slot",
            None,
        )
        != authorization_fee_rpc_slot
        or getattr(
            message_plan,
            "account_context_global_rpc_slot",
            None,
        )
        != global_rpc_slot
        or getattr(
            message_plan,
            "blockhash_min_context_slot",
            None,
        )
        != blockhash_min_context_slot
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
        != compute_unit_limit
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_MESSAGE_BINDING_MISMATCH",
        )

    try:
        network_validation = (
            await validate_pump_sell_v2_pre_sign(
                authorization=authorization,
                context=context,
                message_plan=message_plan,
                db_path=normalized_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_SELL_PRE_SIGN_VALIDATION_EXCEPTION",
        )

    if (
        getattr(
            network_validation,
            "validator_version",
            None,
        )
        != PUMP_SELL_V2_PRE_SIGN_VALIDATION_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_PRE_SIGN_VERSION_MISMATCH",
        )

    pre_sign_status = getattr(
        network_validation,
        "status",
        None,
    )

    if (
        getattr(
            network_validation,
            "authorization_sha256",
            None,
        )
        != authorization_sha256
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_PRE_SIGN_BINDING_MISMATCH",
        )

    if pre_sign_status == PRE_SIGN_DENY:
        return finish(
            BLOCK,
            *tuple(
                getattr(
                    network_validation,
                    "reasons",
                    (),
                )
            ),
        )

    if pre_sign_status == PRE_SIGN_UNKNOWN:
        return finish(
            UNKNOWN,
            *tuple(
                getattr(
                    network_validation,
                    "reasons",
                    (),
                )
            ),
        )

    if pre_sign_status != PRE_SIGN_APPROVE:
        return finish(
            UNKNOWN,
            "LIVE_SELL_PRE_SIGN_STATUS_INVALID",
        )

    #
    # This is the sole consequential authority in this
    # executor. sign_and_bind_pump_sell_v2() can create
    # durable SIGNED state but cannot submit.
    #
    try:
        signing = (
            await sign_and_bind_pump_sell_v2(
                authorization=authorization,
                context=context,
                message_plan=message_plan,
                network_validation=(
                    network_validation
                ),
                signer=signer,
                db_path=normalized_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_SELL_SIGNING_EXCEPTION",
        )

    if (
        getattr(
            signing,
            "signer_version",
            None,
        )
        != PUMP_SELL_V2_SIGNING_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_SIGNING_VERSION_MISMATCH",
        )

    signing_status = getattr(
        signing,
        "status",
        None,
    )

    if (
        getattr(
            signing,
            "authorization_sha256",
            None,
        )
        != authorization_sha256
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_SIGNING_BINDING_MISMATCH",
        )

    transaction_signature = getattr(
        signing,
        "transaction_signature",
        None,
    )

    if signing_status == SIGNING_PASS:
        if not getattr(
            signing,
            "is_durably_signed",
            False,
        ):
            return finish(
                UNKNOWN,
                "LIVE_SELL_SIGNING_PASS_NOT_DURABLE",
            )

        if (
            not isinstance(
                transaction_signature,
                str,
            )
            or not transaction_signature.strip()
        ):
            return finish(
                UNKNOWN,
                "LIVE_SELL_SIGNING_SIGNATURE_MISSING",
            )

        return finish(
            SIGNED,
            *tuple(
                getattr(
                    signing,
                    "reasons",
                    (),
                )
            ),
        )

    if signing_status == SIGNING_BLOCK:
        return finish(
            BLOCK,
            *tuple(
                getattr(
                    signing,
                    "reasons",
                    (),
                )
            ),
        )

    if signing_status == SIGNING_UNKNOWN:
        return finish(
            UNKNOWN,
            *tuple(
                getattr(
                    signing,
                    "reasons",
                    (),
                )
            ),
        )

    return finish(
        UNKNOWN,
        "LIVE_SELL_SIGNING_STATUS_INVALID",
    )
