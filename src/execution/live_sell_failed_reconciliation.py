from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.live_pump_sell_authorization import (
    LivePumpSellAuthorization,
)
from src.execution.live_sell_transaction_receipt import (
    BLOCK as RECEIPT_BLOCK,
    LIVE_SELL_TRANSACTION_RECEIPT_VERSION,
    RESOLVED as RECEIPT_RESOLVED,
    UNKNOWN as RECEIPT_UNKNOWN,
    resolve_live_sell_transaction_receipt,
)
from src.execution.live_sell_transaction_status import (
    KNOWN,
)
from src.portfolio.live_reservations import (
    DB_PATH,
    get_connection,
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
from src.portfolio.live_sell_transaction_journal import (
    BLOCK as JOURNAL_BLOCK,
    FAILED,
    LIVE_SELL_TRANSACTION_JOURNAL_VERSION,
    PASS as JOURNAL_PASS,
    RECONCILED_FAILED_SELL_REASON,
    UNKNOWN as JOURNAL_UNKNOWN,
    LiveSellTransactionJournalEntry,
    load_live_sell_transaction_journal_entry_read_only,
    record_failed_sell_and_release_claim,
)


LIVE_SELL_FAILED_RECONCILIATION_VERSION = (
    "live-sell-failed-reconciliation-v1"
)

RECONCILED = "RECONCILED"
HOLD = "HOLD"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveSellFailedReconciliationResult:
    executor_version: str

    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    transaction_signature: str | None

    receipt_status: str | None
    receipt_slot: int | None

    execution_status: str | None

    claim_status: str | None
    terminal_at: float | None
    terminal_reason: str | None

    journal_outcome: str | None

    changed: bool


def _strict_nonnegative_int(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            int,
        )
        and not isinstance(
            value,
            bool,
        )
        and value >= 0
    )


def _strict_timestamp(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            (int, float),
        )
        and not isinstance(
            value,
            bool,
        )
        and float(value) > 0
    )


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
    Immutable signed-artifact identity.

    Submission status and submission timestamps are
    deliberately excluded because SIGNED -> ARMED ->
    SUBMITTED may advance concurrently without changing
    the signed transaction.
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


def _receipt_binding_matches(
    *,
    receipt,
    execution: LiveSellExecutionRecord,
    authorization: LivePumpSellAuthorization,
) -> bool:
    if (
        receipt.authorization_sha256
        != authorization.authorization_sha256
        or receipt.transaction_signature
        != execution.transaction_signature
        or receipt.execution_status
        not in (
            SIGNED,
            SUBMISSION_ARMED,
            SUBMITTED,
        )
        or receipt.status_observation_state
        != KNOWN
        or receipt.persisted_transaction_sha256
        != execution.signed_transaction_sha256
        or receipt.receipt_transaction_sha256
        != execution.signed_transaction_sha256
        or receipt.fee_payer_pubkey
        != execution.wallet_pubkey
        or receipt.last_valid_block_height
        != execution.last_valid_block_height
        or receipt.blockhash_rpc_slot
        != execution.blockhash_rpc_slot
        or not _strict_nonnegative_int(
            receipt.status_transaction_slot
        )
        or not _strict_nonnegative_int(
            receipt.receipt_slot
        )
        or receipt.status_transaction_slot
        != receipt.receipt_slot
        or not _strict_nonnegative_int(
            receipt.fee_lamports
        )
        or not _strict_nonnegative_int(
            receipt.fee_payer_pre_balance_lamports
        )
        or not _strict_nonnegative_int(
            receipt.fee_payer_post_balance_lamports
        )
        or not isinstance(
            receipt.fee_payer_balance_delta_lamports,
            int,
        )
        or isinstance(
            receipt.fee_payer_balance_delta_lamports,
            bool,
        )
    ):
        return False

    if (
        receipt.block_time is not None
        and not _strict_nonnegative_int(
            receipt.block_time
        )
    ):
        return False

    if (
        receipt.fee_payer_post_balance_lamports
        - receipt.fee_payer_pre_balance_lamports
        != receipt.fee_payer_balance_delta_lamports
    ):
        return False

    if (
        receipt.fee_payer_balance_delta_lamports
        != -receipt.fee_lamports
    ):
        return False

    return True


def _failed_terminal_is_coherent(
    *,
    authorization: LivePumpSellAuthorization,
    claim: LiveSellInventoryClaim,
    execution: LiveSellExecutionRecord,
    entry: LiveSellTransactionJournalEntry | None,
) -> bool:
    if (
        not _claim_identity_matches_authorization(
            claim=claim,
            authorization=authorization,
        )
        or claim.status != RELEASED
        or claim.terminal_at is None
        or not _strict_timestamp(
            claim.terminal_at
        )
        or claim.terminal_reason
        != RECONCILED_FAILED_SELL_REASON
        or not _execution_matches_authorization(
            execution=execution,
            authorization=authorization,
        )
        or entry is None
    ):
        return False

    if (
        entry.journal_version
        != LIVE_SELL_TRANSACTION_JOURNAL_VERSION
        or entry.outcome != FAILED
        or entry.authorization_sha256
        != authorization.authorization_sha256
        or entry.transaction_signature
        != execution.transaction_signature
        or entry.wallet_pubkey
        != authorization.wallet_pubkey
        or entry.fee_payer_pubkey
        != authorization.wallet_pubkey
        or entry.mint
        != authorization.mint
        or entry.tokens_to_sell
        != authorization.tokens_to_sell
        or entry.signed_transaction_sha256
        != execution.signed_transaction_sha256
        or entry.receipt_transaction_sha256
        != execution.signed_transaction_sha256
        or entry.receipt_resolver_version
        != LIVE_SELL_TRANSACTION_RECEIPT_VERSION
        or entry.last_valid_block_height
        != execution.last_valid_block_height
        or entry.blockhash_rpc_slot
        != execution.blockhash_rpc_slot
        or entry.recorded_at
        != claim.terminal_at
        or not _strict_nonnegative_int(
            entry.slot
        )
        or not _strict_nonnegative_int(
            entry.fee_lamports
        )
        or not _strict_nonnegative_int(
            entry.fee_payer_pre_balance_lamports
        )
        or not _strict_nonnegative_int(
            entry.fee_payer_post_balance_lamports
        )
        or not isinstance(
            entry.fee_payer_balance_delta_lamports,
            int,
        )
        or isinstance(
            entry.fee_payer_balance_delta_lamports,
            bool,
        )
    ):
        return False

    if (
        entry.block_time is not None
        and not _strict_nonnegative_int(
            entry.block_time
        )
    ):
        return False

    if (
        entry.fee_payer_post_balance_lamports
        - entry.fee_payer_pre_balance_lamports
        != entry.fee_payer_balance_delta_lamports
    ):
        return False

    if (
        entry.fee_payer_balance_delta_lamports
        != -entry.fee_lamports
    ):
        return False

    if (
        not isinstance(
            entry.transaction_error_json,
            str,
        )
        or not entry.transaction_error_json
    ):
        return False

    try:
        transaction_error = json.loads(
            entry.transaction_error_json
        )

    except (
        TypeError,
        ValueError,
    ):
        return False

    return transaction_error is not None


async def reconcile_failed_live_sell(
    *,
    authorization: LivePumpSellAuthorization,
    db_path: Path = DB_PATH,
) -> LiveSellFailedReconciliationResult:
    """
    Reconcile exactly one landed failed SELL.

    This executor has no:
    - signing authority
    - transaction-build authority
    - send authority
    - direct database-write authority
    - position mutation authority
    - PnL/accounting authority

    Only a RESOLVED landed receipt with a non-null
    transaction error may reach the existing atomic
    failed-SELL journal/release primitive.

    Successful landed SELLs remain held for the future
    successful-fill/economic reconciliation path.
    """

    authorization_sha256 = ""

    transaction_signature: (
        str | None
    ) = None

    receipt_status: (
        str | None
    ) = None

    receipt_slot: (
        int | None
    ) = None

    execution_status: (
        str | None
    ) = None

    claim_status: (
        str | None
    ) = None

    terminal_at: (
        float | None
    ) = None

    terminal_reason: (
        str | None
    ) = None

    journal_outcome: (
        str | None
    ) = None

    def finish(
        status: str,
        *reasons: str,
        changed: bool = False,
    ) -> LiveSellFailedReconciliationResult:
        return LiveSellFailedReconciliationResult(
            executor_version=(
                LIVE_SELL_FAILED_RECONCILIATION_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            transaction_signature=(
                transaction_signature
            ),
            receipt_status=(
                receipt_status
            ),
            receipt_slot=(
                receipt_slot
            ),
            execution_status=(
                execution_status
            ),
            claim_status=(
                claim_status
            ),
            terminal_at=(
                terminal_at
            ),
            terminal_reason=(
                terminal_reason
            ),
            journal_outcome=(
                journal_outcome
            ),
            changed=changed,
        )

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
            UNKNOWN,
            "SELL_AUTHORIZATION_CONTRACT_INVALID",
        )

    authorization_sha256 = (
        authorization.authorization_sha256
    )

    #
    # Initial claim snapshot must support both ACTIVE
    # work and local recovery of an already-completed
    # failed reconciliation.
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

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_CLAIM_READ_FAILED",
        )

    if initial_claim is None:
        return finish(
            UNKNOWN,
            "SELL_CLAIM_NOT_FOUND",
        )

    claim_status = (
        initial_claim.status
    )

    terminal_at = (
        initial_claim.terminal_at
    )

    terminal_reason = (
        initial_claim.terminal_reason
    )

    if not _claim_identity_matches_authorization(
        claim=initial_claim,
        authorization=authorization,
    ):
        return finish(
            BLOCK,
            "SELL_CLAIM_IDENTITY_MISMATCH",
        )

    #
    # The exact signed execution artifact remains part
    # of terminal evidence even after claim release.
    #
    try:
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
            "SELL_EXECUTION_READ_FAILED",
        )

    if (
        initial_execution_result.status
        != EXECUTION_PASS
        or initial_execution_result.record
        is None
    ):
        mapped_status = (
            BLOCK
            if initial_execution_result.status
            == EXECUTION_BLOCK
            else UNKNOWN
        )

        return finish(
            mapped_status,
            "SELL_EXECUTION_UNAVAILABLE",
            *initial_execution_result.reasons,
        )

    initial_execution = (
        initial_execution_result.record
    )

    transaction_signature = (
        initial_execution.transaction_signature
    )

    execution_status = (
        initial_execution.status
    )

    if not _execution_matches_authorization(
        execution=initial_execution,
        authorization=authorization,
    ):
        return finish(
            BLOCK,
            "SELL_EXECUTION_AUTHORIZATION_MISMATCH",
        )

    #
    # Already-completed failed reconciliation is
    # recovered entirely from durable local evidence.
    #
    if initial_claim.status == RELEASED:
        if (
            initial_claim.terminal_reason
            != RECONCILED_FAILED_SELL_REASON
        ):
            return finish(
                BLOCK,
                "FAILED_SELL_TERMINAL_REASON_MISMATCH",
            )

        try:
            entry = (
                load_live_sell_transaction_journal_entry_read_only(
                    authorization_sha256=(
                        authorization_sha256
                    ),
                    db_path=db_path,
                )
            )

        except Exception:
            return finish(
                UNKNOWN,
                "FAILED_SELL_JOURNAL_READ_FAILED",
            )

        if not _failed_terminal_is_coherent(
            authorization=authorization,
            claim=initial_claim,
            execution=initial_execution,
            entry=entry,
        ):
            return finish(
                UNKNOWN,
                "FAILED_SELL_TERMINAL_INCOHERENT",
            )

        journal_outcome = (
            entry.outcome
        )

        return finish(
            RECONCILED,
            changed=False,
        )

    if initial_claim.status == CONSUMED:
        return finish(
            BLOCK,
            "SELL_CLAIM_ALREADY_CONSUMED",
        )

    if (
        initial_claim.status != ACTIVE
        or not _claim_matches_authorization(
            claim=initial_claim,
            authorization=authorization,
        )
    ):
        return finish(
            BLOCK,
            "SELL_CLAIM_NOT_RECONCILABLE",
        )

    initial_artifact_identity = (
        _artifact_identity(
            initial_execution
        )
    )

    #
    # Obtain exact landed chain receipt evidence.
    #
    try:
        receipt = (
            await resolve_live_sell_transaction_receipt(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_RECEIPT_RESOLUTION_FAILED",
        )

    receipt_status = (
        receipt.status
    )

    receipt_slot = (
        receipt.receipt_slot
    )

    if receipt.status == RECEIPT_UNKNOWN:
        return finish(
            UNKNOWN,
            "SELL_RECEIPT_UNKNOWN",
            *receipt.reasons,
        )

    if receipt.status == RECEIPT_BLOCK:
        return finish(
            HOLD,
            "TRANSACTION_NOT_READY_FOR_FAILED_SELL_RECONCILIATION",
            *receipt.reasons,
        )

    if receipt.status != RECEIPT_RESOLVED:
        return finish(
            UNKNOWN,
            "UNEXPECTED_SELL_RECEIPT_STATUS",
        )

    #
    # Successful SELLs must not reach the failed path.
    #
    if receipt.transaction_error is None:
        return finish(
            HOLD,
            "SUCCESSFUL_SELL_REQUIRES_FILL_RECONCILIATION",
        )

    if (
        receipt.resolver_version
        != LIVE_SELL_TRANSACTION_RECEIPT_VERSION
        or not _receipt_binding_matches(
            receipt=receipt,
            execution=initial_execution,
            authorization=authorization,
        )
    ):
        return finish(
            UNKNOWN,
            "FAILED_SELL_RECEIPT_BINDING_MISMATCH",
        )

    #
    # Re-read the exact execution artifact after the
    # chain observation. Status may advance, but the
    # signed artifact may not change.
    #
    try:
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
            "SELL_EXECUTION_REREAD_FAILED",
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

    execution_status = (
        current_execution.status
    )

    if (
        not _execution_matches_authorization(
            execution=current_execution,
            authorization=authorization,
        )
        or _artifact_identity(
            current_execution
        )
        != initial_artifact_identity
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_CHANGED_DURING_RECEIPT_CHECK",
        )

    #
    # The only write-capable call in this executor.
    #
    try:
        ledger_result = (
            record_failed_sell_and_release_claim(
                authorization=authorization,
                receipt_resolver_version=(
                    receipt.resolver_version
                ),
                transaction_signature=(
                    receipt.transaction_signature
                ),
                signed_transaction_sha256=(
                    receipt.persisted_transaction_sha256
                ),
                receipt_transaction_sha256=(
                    receipt.receipt_transaction_sha256
                ),
                receipt_slot=(
                    receipt.receipt_slot
                ),
                block_time=(
                    receipt.block_time
                ),
                transaction_error=(
                    receipt.transaction_error
                ),
                fee_lamports=(
                    receipt.fee_lamports
                ),
                fee_payer_pubkey=(
                    receipt.fee_payer_pubkey
                ),
                fee_payer_pre_balance_lamports=(
                    receipt
                    .fee_payer_pre_balance_lamports
                ),
                fee_payer_post_balance_lamports=(
                    receipt
                    .fee_payer_post_balance_lamports
                ),
                fee_payer_balance_delta_lamports=(
                    receipt
                    .fee_payer_balance_delta_lamports
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "FAILED_SELL_LEDGER_MUTATION_FAILED",
        )

    claim_status = (
        ledger_result.claim_status
    )

    terminal_at = (
        ledger_result.terminal_at
    )

    terminal_reason = (
        ledger_result.terminal_reason
    )

    if ledger_result.status == JOURNAL_BLOCK:
        return finish(
            BLOCK,
            "FAILED_SELL_LEDGER_BLOCKED",
            *ledger_result.reasons,
        )

    if ledger_result.status == JOURNAL_UNKNOWN:
        return finish(
            UNKNOWN,
            "FAILED_SELL_LEDGER_UNKNOWN",
            *ledger_result.reasons,
        )

    if (
        ledger_result.status
        != JOURNAL_PASS
    ):
        return finish(
            UNKNOWN,
            "UNEXPECTED_FAILED_SELL_LEDGER_STATUS",
        )

    #
    # Independent durable readback after the atomic
    # transition.
    #
    try:
        final_claim = (
            _load_claim_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

        final_execution_result = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

        final_entry = (
            load_live_sell_transaction_journal_entry_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "FAILED_SELL_FINAL_READBACK_FAILED",
        )

    if (
        final_claim is None
        or final_execution_result.status
        != EXECUTION_PASS
        or final_execution_result.record
        is None
    ):
        return finish(
            UNKNOWN,
            "FAILED_SELL_FINAL_READBACK_MISSING",
        )

    final_execution = (
        final_execution_result.record
    )

    claim_status = (
        final_claim.status
    )

    terminal_at = (
        final_claim.terminal_at
    )

    terminal_reason = (
        final_claim.terminal_reason
    )

    execution_status = (
        final_execution.status
    )

    if (
        _artifact_identity(
            final_execution
        )
        != initial_artifact_identity
        or not _failed_terminal_is_coherent(
            authorization=authorization,
            claim=final_claim,
            execution=final_execution,
            entry=final_entry,
        )
    ):
        return finish(
            UNKNOWN,
            "FAILED_SELL_FINAL_STATE_INCOHERENT",
        )

    journal_outcome = (
        final_entry.outcome
    )

    return finish(
        RECONCILED,
        changed=ledger_result.changed,
    )
