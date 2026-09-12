from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.live_pump_sell_authorization import (
    LivePumpSellAuthorization,
)
from src.execution.live_sell_failed_reconciliation import (
    BLOCK as FAILED_BLOCK,
    HOLD as FAILED_HOLD,
    RECONCILED as FAILED_RECONCILED,
    UNKNOWN as FAILED_UNKNOWN,
    reconcile_failed_live_sell,
)
from src.execution.live_sell_successful_reconciliation import (
    BLOCK as SUCCESS_BLOCK,
    HOLD as SUCCESS_HOLD,
    RECONCILED as SUCCESS_RECONCILED,
    UNKNOWN as SUCCESS_UNKNOWN,
    reconcile_successful_live_sell,
)
from src.execution.live_sell_transaction_status import (
    ABSENT_EXPIRED,
    ABSENT_STILL_VALID,
    KNOWN,
    UNKNOWN as STATUS_UNKNOWN,
    LIVE_SELL_TRANSACTION_STATUS_RESOLVER_VERSION,
    resolve_live_sell_transaction_status,
)
from src.portfolio.live_reservations import (
    DB_PATH,
    get_connection,
)
from src.portfolio.live_sell_absent_expired_release import (
    BLOCK as EXPIRED_BLOCK,
    PASS as EXPIRED_PASS,
    UNKNOWN as EXPIRED_UNKNOWN,
    RECONCILED_ABSENT_EXPIRED_SELL_REASON,
    release_reconciled_absent_expired_sell_claim,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    CONSUMED,
    RELEASED,
    LiveSellInventoryClaim,
    _authorization_contract_valid,
    _claim_identity_matches_authorization,
    _claim_matches_authorization,
    _load_claim,
)
from src.portfolio.live_sell_execution_records import (
    BLOCK as EXECUTION_BLOCK,
    PASS as EXECUTION_PASS,
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED,
    LiveSellExecutionRecord,
    _record_contract_valid,
    load_live_sell_execution_record_read_only,
)
from src.portfolio.live_sell_success_accounting import (
    RECONCILED_SUCCESSFUL_SELL_REASON,
)
from src.portfolio.live_sell_transaction_journal import (
    RECONCILED_FAILED_SELL_REASON,
)


LIVE_SELL_RECONCILIATION_VERSION = (
    "live-sell-reconciliation-v1"
)

RECONCILED = "RECONCILED"
HOLD = "HOLD"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"

SUCCESS = "SUCCESS"
FAILED = "FAILED"
EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class LiveSellReconciliationResult:
    executor_version: str

    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    route: str | None

    status_observation_state: str | None

    transaction_signature: str | None
    transaction_error: Any

    child_status: str | None

    changed: bool


def _execution_status_reconcilable(
    status: str,
) -> bool:
    return status in (
        SIGNED,
        SUBMISSION_ARMED,
        SUBMITTED,
    )


def _artifact_identity(
    execution: LiveSellExecutionRecord,
) -> tuple:
    """
    Immutable signed SELL artifact identity.

    Submission metadata may advance without changing
    the already-signed transaction.
    """
    return (
        execution.record_version,
        execution.authorization_version,
        execution.authorization_sha256,

        execution.wallet_pubkey,
        execution.mint,
        execution.tokens_to_sell,

        execution.message_sha256,

        execution.transaction_signature,
        execution.signed_transaction_sha256,
        execution.signed_transaction_bytes,

        execution.blockhash_context_version,
        execution.recent_blockhash,
        execution.last_valid_block_height,
        execution.blockhash_rpc_slot,

        execution.signed_at,
    )


def _execution_matches_authorization(
    *,
    execution: LiveSellExecutionRecord,
    authorization: LivePumpSellAuthorization,
) -> bool:
    return (
        _record_contract_valid(
            execution
        )
        and execution.authorization_version
        == authorization.authorization_version
        and execution.authorization_sha256
        == authorization.authorization_sha256
        and execution.wallet_pubkey
        == authorization.wallet_pubkey
        and execution.mint
        == authorization.mint
        and execution.tokens_to_sell
        == authorization.tokens_to_sell
        and _execution_status_reconcilable(
            execution.status
        )
    )


def _load_claim_read_only(
    *,
    authorization_sha256: str,
    db_path: Path,
) -> LiveSellInventoryClaim | None:
    connection = get_connection(
        db_path
    )

    try:
        return _load_claim(
            connection=connection,
            authorization_sha256=(
                authorization_sha256
            ),
        )

    finally:
        connection.close()


async def reconcile_live_sell(
    *,
    authorization: LivePumpSellAuthorization,
    db_path: Path = DB_PATH,
) -> LiveSellReconciliationResult:
    """
    Route exactly one durable live SELL into its
    terminal reconciliation branch.

    This controller has no signing, send, receipt
    parsing, accounting, position mutation, claim
    mutation, or transaction-journal authority.

    Branch authority remains delegated to:

      successful SELL reconciler
      failed SELL reconciler
      ABSENT_EXPIRED claim-release primitive
    """
    authorization_sha256 = (
        authorization.authorization_sha256
        if isinstance(
            authorization,
            LivePumpSellAuthorization,
        )
        else ""
    )

    route: str | None = None
    observation_state: str | None = None
    transaction_signature: str | None = None
    transaction_error: Any = None
    child_status: str | None = None

    def finish(
        status: str,
        *reasons: str,
        changed: bool = False,
    ) -> LiveSellReconciliationResult:
        return LiveSellReconciliationResult(
            executor_version=(
                LIVE_SELL_RECONCILIATION_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            authorization_sha256=(
                authorization_sha256
            ),
            route=route,
            status_observation_state=(
                observation_state
            ),
            transaction_signature=(
                transaction_signature
            ),
            transaction_error=(
                transaction_error
            ),
            child_status=child_status,
            changed=changed,
        )

    async def route_success():
        nonlocal route
        nonlocal child_status

        route = SUCCESS

        try:
            result = (
                await reconcile_successful_live_sell(
                    authorization=authorization,
                    db_path=db_path,
                )
            )
        except Exception:
            return finish(
                UNKNOWN,
                "SUCCESSFUL_SELL_RECONCILER_FAILED",
            )

        child_status = result.status

        if result.status == SUCCESS_RECONCILED:
            return finish(
                RECONCILED,
                *result.reasons,
                changed=result.changed,
            )

        if result.status == SUCCESS_HOLD:
            return finish(
                HOLD,
                *result.reasons,
                changed=False,
            )

        if result.status == SUCCESS_BLOCK:
            return finish(
                BLOCK,
                *result.reasons,
                changed=False,
            )

        if result.status == SUCCESS_UNKNOWN:
            return finish(
                UNKNOWN,
                *result.reasons,
                changed=False,
            )

        return finish(
            UNKNOWN,
            "SUCCESSFUL_SELL_RECONCILER_STATUS_INVALID",
        )

    async def route_failed():
        nonlocal route
        nonlocal child_status

        route = FAILED

        try:
            result = (
                await reconcile_failed_live_sell(
                    authorization=authorization,
                    db_path=db_path,
                )
            )
        except Exception:
            return finish(
                UNKNOWN,
                "FAILED_SELL_RECONCILER_FAILED",
            )

        child_status = result.status

        if result.status == FAILED_RECONCILED:
            return finish(
                RECONCILED,
                *result.reasons,
                changed=result.changed,
            )

        if result.status == FAILED_HOLD:
            return finish(
                HOLD,
                *result.reasons,
                changed=False,
            )

        if result.status == FAILED_BLOCK:
            return finish(
                BLOCK,
                *result.reasons,
                changed=False,
            )

        if result.status == FAILED_UNKNOWN:
            return finish(
                UNKNOWN,
                *result.reasons,
                changed=False,
            )

        return finish(
            UNKNOWN,
            "FAILED_SELL_RECONCILER_STATUS_INVALID",
        )

    def route_expired(
        execution: LiveSellExecutionRecord,
    ):
        nonlocal route
        nonlocal child_status

        route = EXPIRED

        try:
            result = (
                release_reconciled_absent_expired_sell_claim(
                    authorization=authorization,
                    transaction_signature=(
                        execution.transaction_signature
                    ),
                    signed_transaction_sha256=(
                        execution
                        .signed_transaction_sha256
                    ),
                    last_valid_block_height=(
                        execution
                        .last_valid_block_height
                    ),
                    blockhash_rpc_slot=(
                        execution.blockhash_rpc_slot
                    ),
                    db_path=db_path,
                )
            )
        except Exception:
            return finish(
                UNKNOWN,
                "EXPIRED_SELL_RELEASE_FAILED",
            )

        child_status = result.status

        if result.status == EXPIRED_PASS:
            return finish(
                RECONCILED,
                *result.reasons,
                changed=result.changed,
            )

        if result.status == EXPIRED_BLOCK:
            return finish(
                BLOCK,
                *result.reasons,
                changed=False,
            )

        if result.status == EXPIRED_UNKNOWN:
            return finish(
                UNKNOWN,
                *result.reasons,
                changed=False,
            )

        return finish(
            UNKNOWN,
            "EXPIRED_SELL_RELEASE_STATUS_INVALID",
        )

    async def route_terminal(
        *,
        claim: LiveSellInventoryClaim,
        execution: LiveSellExecutionRecord,
    ):
        """
        Recover already-terminal local state without
        repeating chain status observation.
        """
        if claim.status == CONSUMED:
            if (
                claim.terminal_reason
                == RECONCILED_SUCCESSFUL_SELL_REASON
            ):
                return await route_success()

            return finish(
                BLOCK,
                "SELL_CLAIM_CONSUMED_OTHER_REASON",
            )

        if claim.status == RELEASED:
            if (
                claim.terminal_reason
                == RECONCILED_FAILED_SELL_REASON
            ):
                return await route_failed()

            if (
                claim.terminal_reason
                == RECONCILED_ABSENT_EXPIRED_SELL_REASON
            ):
                return route_expired(
                    execution
                )

            return finish(
                BLOCK,
                "SELL_CLAIM_RELEASED_OTHER_REASON",
            )

        return None

    if (
        not isinstance(
            authorization,
            LivePumpSellAuthorization,
        )
        or not _authorization_contract_valid(
            authorization
        )
    ):
        return finish(
            BLOCK,
            "SELL_AUTHORIZATION_CONTRACT_INVALID",
        )

    authorization_sha256 = (
        authorization.authorization_sha256
    )

    #
    # Establish exact local authority before any chain
    # observation.
    #
    try:
        initial_claim = (
            _load_claim_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

        initial_execution_result = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_LOCAL_STATE_READ_FAILED",
        )

    if initial_claim is None:
        return finish(
            UNKNOWN,
            "SELL_CLAIM_NOT_FOUND",
        )

    if (
        initial_execution_result.status
        != EXECUTION_PASS
        or initial_execution_result.record
        is None
    ):
        return finish(
            (
                BLOCK
                if initial_execution_result.status
                == EXECUTION_BLOCK
                else UNKNOWN
            ),
            "SELL_EXECUTION_UNAVAILABLE",
            *initial_execution_result.reasons,
        )

    initial_execution = (
        initial_execution_result.record
    )

    transaction_signature = (
        initial_execution.transaction_signature
    )

    if not _execution_matches_authorization(
        execution=initial_execution,
        authorization=authorization,
    ):
        return finish(
            BLOCK,
            "SELL_EXECUTION_AUTHORIZATION_MISMATCH",
        )

    if not _claim_identity_matches_authorization(
        claim=initial_claim,
        authorization=authorization,
    ):
        return finish(
            BLOCK,
            "SELL_CLAIM_AUTHORIZATION_MISMATCH",
        )

    #
    # Terminal recovery must remain local.
    #
    terminal_result = await route_terminal(
        claim=initial_claim,
        execution=initial_execution,
    )

    if terminal_result is not None:
        return terminal_result

    if (
        initial_claim.status != ACTIVE
        or not _claim_matches_authorization(
            claim=initial_claim,
            authorization=authorization,
        )
    ):
        return finish(
            BLOCK,
            "ACTIVE_SELL_CLAIM_CONTRACT_MISMATCH",
        )

    initial_identity = (
        _artifact_identity(
            initial_execution
        )
    )

    #
    # Cheap read-only routing observation.
    #
    try:
        observation = (
            await resolve_live_sell_transaction_status(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_STATUS_RESOLUTION_FAILED",
        )

    observation_state = observation.state
    transaction_error = (
        observation.transaction_error
    )

    if (
        observation.resolver_version
        != LIVE_SELL_TRANSACTION_STATUS_RESOLVER_VERSION
        or observation.authorization_sha256
        != authorization_sha256
        or observation.transaction_signature
        != initial_execution.transaction_signature
        or observation.last_valid_block_height
        != initial_execution.last_valid_block_height
        or observation.blockhash_rpc_slot
        != initial_execution.blockhash_rpc_slot
        or not _execution_status_reconcilable(
            observation.execution_status
        )
    ):
        return finish(
            UNKNOWN,
            "SELL_STATUS_OBSERVATION_BINDING_MISMATCH",
        )

    if observation.state not in (
        KNOWN,
        ABSENT_STILL_VALID,
        ABSENT_EXPIRED,
        STATUS_UNKNOWN,
    ):
        return finish(
            UNKNOWN,
            "SELL_STATUS_OBSERVATION_STATE_INVALID",
        )

    if observation.state == STATUS_UNKNOWN:
        return finish(
            UNKNOWN,
            "SELL_STATUS_OBSERVATION_UNKNOWN",
            *observation.reasons,
        )

    if observation.state == ABSENT_STILL_VALID:
        return finish(
            HOLD,
            "SELL_TRANSACTION_ABSENT_STILL_VALID",
            *observation.reasons,
        )

    #
    # ABSENT_EXPIRED is release authority, so enforce
    # its critical provenance again at the router
    # boundary.
    #
    if observation.state == ABSENT_EXPIRED:
        if (
            observation.history_searched is not True
            or not isinstance(
                observation.current_block_height,
                int,
            )
            or isinstance(
                observation.current_block_height,
                bool,
            )
            or observation.current_block_height
            <= initial_execution
            .last_valid_block_height
        ):
            return finish(
                UNKNOWN,
                "SELL_ABSENT_EXPIRED_PROOF_INVALID",
            )

    #
    # Re-read all local authority immediately after
    # chain observation and before selecting a
    # mutation-owning child.
    #
    try:
        current_claim = (
            _load_claim_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

        current_execution_result = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_LOCAL_STATE_REREAD_FAILED",
        )

    if current_claim is None:
        return finish(
            UNKNOWN,
            "SELL_CLAIM_DISAPPEARED",
        )

    if (
        current_execution_result.status
        != EXECUTION_PASS
        or current_execution_result.record
        is None
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_REREAD_UNAVAILABLE",
            *current_execution_result.reasons,
        )

    current_execution = (
        current_execution_result.record
    )

    transaction_signature = (
        current_execution.transaction_signature
    )

    if (
        not _execution_matches_authorization(
            execution=current_execution,
            authorization=authorization,
        )
        or _artifact_identity(
            current_execution
        )
        != initial_identity
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_CHANGED_DURING_STATUS_CHECK",
        )

    if not _claim_identity_matches_authorization(
        claim=current_claim,
        authorization=authorization,
    ):
        return finish(
            UNKNOWN,
            "SELL_CLAIM_CHANGED_DURING_STATUS_CHECK",
        )

    #
    # Another reconciler may have completed while the
    # status RPC was in flight.
    #
    terminal_result = await route_terminal(
        claim=current_claim,
        execution=current_execution,
    )

    if terminal_result is not None:
        return terminal_result

    if (
        current_claim.status != ACTIVE
        or not _claim_matches_authorization(
            claim=current_claim,
            authorization=authorization,
        )
    ):
        return finish(
            UNKNOWN,
            "SELL_CLAIM_CHANGED_DURING_STATUS_CHECK",
        )

    if observation.state == ABSENT_EXPIRED:
        return route_expired(
            current_execution
        )

    if observation.state != KNOWN:
        return finish(
            UNKNOWN,
            "SELL_ROUTING_STATE_INVALID",
        )

    #
    # KNOWN means the transaction landed.
    #
    # The status observation only decides which
    # proof-owning child gets control. Each child
    # independently verifies the exact receipt/fill.
    #
    if observation.transaction_error is None:
        return await route_success()

    return await route_failed()
