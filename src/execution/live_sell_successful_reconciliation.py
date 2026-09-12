from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.live_pump_sell_authorization import (
    LivePumpSellAuthorization,
)
from src.execution.live_sell_transaction_receipt import (
    LIVE_SELL_TRANSACTION_RECEIPT_VERSION,
)
from src.execution.successful_pump_sell_fill import (
    BLOCK as FILL_BLOCK,
    PROVEN as FILL_PROVEN,
    UNKNOWN as FILL_UNKNOWN,
    resolve_successful_pump_sell_fill,
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
from src.portfolio.live_sell_success_accounting import (
    BLOCK as ACCOUNTING_BLOCK,
    PASS as ACCOUNTING_PASS,
    UNKNOWN as ACCOUNTING_UNKNOWN,
    LIVE_SELL_SUCCESS_ACCOUNTING_VERSION,
    RECONCILED_SUCCESSFUL_SELL_REASON,
    SuccessfulSellAccountingRecord,
    _accounting_record_valid,
    _fill_contract_valid,
    _load_success_accounting,
    record_successful_sell_and_consume_claim,
)
from src.portfolio.live_sell_transaction_journal import (
    LIVE_SELL_TRANSACTION_JOURNAL_VERSION,
    SUCCESS,
    LiveSellTransactionJournalEntry,
    load_live_sell_transaction_journal_entry_read_only,
)


LIVE_SELL_SUCCESSFUL_RECONCILIATION_VERSION = (
    "live-sell-successful-reconciliation-v1"
)

RECONCILED = "RECONCILED"
HOLD = "HOLD"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveSellSuccessfulReconciliationResult:
    executor_version: str

    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    transaction_signature: str | None

    fill_status: str | None
    receipt_slot: int | None

    execution_status: str | None

    claim_status: str | None
    terminal_at: float | None
    terminal_reason: str | None

    journal_outcome: str | None

    net_wallet_proceeds_lamports: int | None
    total_realized_pnl_lamports: int | None

    changed: bool


def _strict_nonnegative_int(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
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
    Immutable signed SELL artifact identity.

    Submission state may advance concurrently from
    SIGNED -> SUBMISSION_ARMED -> SUBMITTED without
    changing the signed transaction.
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


def _load_success_accounting_read_only(
    *,
    authorization_sha256: str,
    db_path: Path,
) -> SuccessfulSellAccountingRecord | None:
    connection = get_connection(
        db_path
    )

    try:
        return _load_success_accounting(
            connection=connection,
            authorization_sha256=(
                authorization_sha256
            ),
        )

    finally:
        connection.close()


def _accounting_matches_claim(
    *,
    accounting: SuccessfulSellAccountingRecord,
    claim: LiveSellInventoryClaim,
) -> bool:
    if (
        not _accounting_record_valid(
            accounting
        )
        or accounting.accounting_version
        != LIVE_SELL_SUCCESS_ACCOUNTING_VERSION
        or accounting.authorization_sha256
        != claim.authorization_sha256
        or len(accounting.lots)
        != len(
            claim.allocation.allocations
        )
    ):
        return False

    try:
        for claim_lot, accounting_lot in zip(
            claim.allocation.allocations,
            accounting.lots,
            strict=True,
        ):
            if (
                accounting_lot.position_id
                != claim_lot.position_id
                or accounting_lot.position_version
                != claim_lot.position_version

                or accounting_lot.tokens_before
                != claim_lot.tokens_before
                or accounting_lot.tokens_sold
                != claim_lot.tokens_to_sell
                or accounting_lot.tokens_after
                != claim_lot.tokens_after

                or accounting_lot.exposure_before_lamports
                != claim_lot.exposure_before_lamports
                or accounting_lot.exposure_reduction_lamports
                != claim_lot.exposure_reduction_lamports
                or accounting_lot.exposure_after_lamports
                != claim_lot.exposure_after_lamports

                or accounting_lot.cost_basis_before_lamports
                != claim_lot.cost_basis_before_lamports
                or accounting_lot.cost_basis_reduction_lamports
                != claim_lot.cost_basis_reduction_lamports
                or accounting_lot.cost_basis_after_lamports
                != claim_lot.cost_basis_after_lamports

                or accounting_lot.cumulative_net_proceeds_before_lamports
                != claim_lot.cumulative_net_proceeds_before_lamports

                or accounting_lot.cumulative_realized_pnl_before_lamports
                != claim_lot.cumulative_realized_pnl_before_lamports
            ):
                return False

    except Exception:
        return False

    return True


def _success_terminal_is_coherent(
    *,
    authorization: LivePumpSellAuthorization,
    claim: LiveSellInventoryClaim,
    execution: LiveSellExecutionRecord,
    journal: LiveSellTransactionJournalEntry | None,
    accounting: SuccessfulSellAccountingRecord | None,
) -> bool:
    """
    Verify immutable local provenance for an already
    reconciled successful SELL.

    Mutable live-position rows are intentionally not
    compared here. A later successful SELL may already
    have reduced the same FIFO lot again.
    """
    try:
        if (
            claim.status != CONSUMED
            or claim.terminal_at is None
            or not _strict_timestamp(
                claim.terminal_at
            )
            or claim.terminal_reason
            != RECONCILED_SUCCESSFUL_SELL_REASON
            or not _claim_identity_matches_authorization(
                claim=claim,
                authorization=authorization,
            )

            or not _execution_matches_authorization(
                execution=execution,
                authorization=authorization,
            )

            or journal is None
            or accounting is None
        ):
            return False

        if (
            journal.journal_version
            != LIVE_SELL_TRANSACTION_JOURNAL_VERSION

            or journal.authorization_sha256
            != authorization.authorization_sha256
            or journal.transaction_signature
            != execution.transaction_signature

            or journal.wallet_pubkey
            != authorization.wallet_pubkey
            or journal.mint
            != authorization.mint
            or journal.tokens_to_sell
            != authorization.tokens_to_sell

            or journal.signed_transaction_sha256
            != execution.signed_transaction_sha256
            or journal.receipt_transaction_sha256
            != execution.signed_transaction_sha256

            or journal.receipt_resolver_version
            != LIVE_SELL_TRANSACTION_RECEIPT_VERSION

            or not _strict_nonnegative_int(
                journal.slot
            )
            or (
                journal.block_time is not None
                and not _strict_nonnegative_int(
                    journal.block_time
                )
            )

            or journal.outcome != SUCCESS
            or journal.transaction_error_json
            != "null"

            or not _strict_nonnegative_int(
                journal.fee_lamports
            )

            or journal.fee_payer_pubkey
            != authorization.wallet_pubkey
            or not _strict_nonnegative_int(
                journal.fee_payer_pre_balance_lamports
            )
            or not _strict_nonnegative_int(
                journal.fee_payer_post_balance_lamports
            )
            or not isinstance(
                journal.fee_payer_balance_delta_lamports,
                int,
            )
            or isinstance(
                journal.fee_payer_balance_delta_lamports,
                bool,
            )
            or (
                journal.fee_payer_post_balance_lamports
                - journal.fee_payer_pre_balance_lamports
                != journal.fee_payer_balance_delta_lamports
            )

            or journal.last_valid_block_height
            != execution.last_valid_block_height
            or journal.blockhash_rpc_slot
            != execution.blockhash_rpc_slot

            or not _strict_timestamp(
                journal.recorded_at
            )
        ):
            return False

        if (
            not _accounting_matches_claim(
                accounting=accounting,
                claim=claim,
            )
            or accounting.transaction_fee_lamports
            != journal.fee_lamports
        ):
            return False

    except Exception:
        return False

    return True


def _fill_binding_matches(
    *,
    fill,
    authorization: LivePumpSellAuthorization,
    execution: LiveSellExecutionRecord,
) -> bool:
    try:
        return (
            _fill_contract_valid(
                authorization=authorization,
                fill=fill,
            )
            and fill.transaction_signature
            == execution.transaction_signature
            and fill.persisted_transaction_sha256
            == execution.signed_transaction_sha256
            and fill.observed_transaction_sha256
            == execution.signed_transaction_sha256
        )

    except Exception:
        return False


async def reconcile_successful_live_sell(
    *,
    authorization: LivePumpSellAuthorization,
    db_path: Path = DB_PATH,
) -> LiveSellSuccessfulReconciliationResult:
    authorization_sha256 = (
        authorization.authorization_sha256
        if isinstance(
            authorization,
            LivePumpSellAuthorization,
        )
        else ""
    )

    transaction_signature: str | None = None
    fill_status: str | None = None
    receipt_slot: int | None = None

    execution_status: str | None = None

    claim_status: str | None = None
    terminal_at: float | None = None
    terminal_reason: str | None = None

    journal_outcome: str | None = None

    net_wallet_proceeds_lamports: int | None = None
    total_realized_pnl_lamports: int | None = None

    def finish(
        status: str,
        *reasons: str,
        changed: bool = False,
    ) -> LiveSellSuccessfulReconciliationResult:
        return LiveSellSuccessfulReconciliationResult(
            executor_version=(
                LIVE_SELL_SUCCESSFUL_RECONCILIATION_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            authorization_sha256=(
                authorization_sha256
            ),
            transaction_signature=(
                transaction_signature
            ),
            fill_status=fill_status,
            receipt_slot=receipt_slot,
            execution_status=execution_status,
            claim_status=claim_status,
            terminal_at=terminal_at,
            terminal_reason=terminal_reason,
            journal_outcome=journal_outcome,
            net_wallet_proceeds_lamports=(
                net_wallet_proceeds_lamports
            ),
            total_realized_pnl_lamports=(
                total_realized_pnl_lamports
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
            BLOCK,
            "SELL_AUTHORIZATION_CONTRACT_INVALID",
        )

    authorization_sha256 = (
        authorization.authorization_sha256
    )

    #
    # Initial immutable local authority snapshot.
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

    claim_status = initial_claim.status
    terminal_at = initial_claim.terminal_at
    terminal_reason = initial_claim.terminal_reason

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

    execution_status = (
        initial_execution.status
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

    #
    # Crash-safe/idempotent local recovery.
    #
    # A coherently CONSUMED successful SELL must never
    # hit chain RPC again merely because the caller
    # retried reconciliation.
    #
    if initial_claim.status == CONSUMED:
        if (
            initial_claim.terminal_reason
            != RECONCILED_SUCCESSFUL_SELL_REASON
        ):
            return finish(
                BLOCK,
                "SELL_CLAIM_ALREADY_CONSUMED_OTHER_REASON",
            )

        try:
            journal = (
                load_live_sell_transaction_journal_entry_read_only(
                    authorization_sha256=(
                        authorization_sha256
                    ),
                    db_path=db_path,
                )
            )

            accounting = (
                _load_success_accounting_read_only(
                    authorization_sha256=(
                        authorization_sha256
                    ),
                    db_path=db_path,
                )
            )

        except Exception:
            return finish(
                UNKNOWN,
                "LOCAL_SUCCESS_STATE_READ_FAILED",
            )

        if journal is not None:
            journal_outcome = journal.outcome

        if accounting is not None:
            net_wallet_proceeds_lamports = (
                accounting
                .net_wallet_proceeds_lamports
            )
            total_realized_pnl_lamports = (
                accounting
                .total_realized_pnl_lamports
            )

        if not _success_terminal_is_coherent(
            authorization=authorization,
            claim=initial_claim,
            execution=initial_execution,
            journal=journal,
            accounting=accounting,
        ):
            return finish(
                UNKNOWN,
                "SUCCESSFUL_SELL_TERMINAL_STATE_INCOHERENT",
            )

        return finish(
            RECONCILED,
            changed=False,
        )

    if initial_claim.status == RELEASED:
        return finish(
            BLOCK,
            "SELL_CLAIM_ALREADY_RELEASED",
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
            "ACTIVE_SELL_CLAIM_CONTRACT_MISMATCH",
        )

    initial_identity = (
        _artifact_identity(
            initial_execution
        )
    )

    #
    # Resolve exact landed successful transaction and
    # Pump economic proof.
    #
    try:
        fill = (
            await resolve_successful_pump_sell_fill(
                authorization=authorization,
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SUCCESSFUL_SELL_FILL_RESOLUTION_FAILED",
        )

    fill_status = fill.status
    receipt_slot = fill.receipt_slot

    if fill.status == FILL_UNKNOWN:
        return finish(
            UNKNOWN,
            "SUCCESSFUL_SELL_FILL_UNKNOWN",
            *fill.reasons,
        )

    if fill.status == FILL_BLOCK:
        return finish(
            HOLD,
            "SUCCESSFUL_SELL_FILL_BLOCKED",
            *fill.reasons,
        )

    if fill.status != FILL_PROVEN:
        return finish(
            UNKNOWN,
            "SUCCESSFUL_SELL_FILL_STATUS_INVALID",
        )

    if not _fill_binding_matches(
        fill=fill,
        authorization=authorization,
        execution=initial_execution,
    ):
        return finish(
            UNKNOWN,
            "SUCCESSFUL_SELL_FILL_BINDING_MISMATCH",
        )

    #
    # Re-read immediately before mutation authority.
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

    claim_status = current_claim.status
    terminal_at = current_claim.terminal_at
    terminal_reason = current_claim.terminal_reason

    #
    # Another process may have completed this exact
    # successful accounting transition while proof
    # resolution was in flight.
    #
    if current_claim.status == CONSUMED:
        if (
            current_claim.terminal_reason
            != RECONCILED_SUCCESSFUL_SELL_REASON
        ):
            return finish(
                BLOCK,
                "SELL_CLAIM_CONSUMED_DURING_FILL_OTHER_REASON",
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
        transaction_signature = (
            current_execution.transaction_signature
        )

        try:
            journal = (
                load_live_sell_transaction_journal_entry_read_only(
                    authorization_sha256=(
                        authorization_sha256
                    ),
                    db_path=db_path,
                )
            )

            accounting = (
                _load_success_accounting_read_only(
                    authorization_sha256=(
                        authorization_sha256
                    ),
                    db_path=db_path,
                )
            )

        except Exception:
            return finish(
                UNKNOWN,
                "LOCAL_SUCCESS_STATE_READ_FAILED",
            )

        if journal is not None:
            journal_outcome = journal.outcome

        if accounting is not None:
            net_wallet_proceeds_lamports = (
                accounting
                .net_wallet_proceeds_lamports
            )
            total_realized_pnl_lamports = (
                accounting
                .total_realized_pnl_lamports
            )

        if (
            _artifact_identity(
                current_execution
            )
            != initial_identity
            or not _success_terminal_is_coherent(
                authorization=authorization,
                claim=current_claim,
                execution=current_execution,
                journal=journal,
                accounting=accounting,
            )
        ):
            return finish(
                UNKNOWN,
                "CONCURRENT_SUCCESS_STATE_INCOHERENT",
            )

        return finish(
            RECONCILED,
            changed=False,
        )

    if current_claim.status == RELEASED:
        return finish(
            BLOCK,
            "SELL_CLAIM_RELEASED_DURING_SUCCESS_RECONCILIATION",
        )

    if (
        current_claim.status != ACTIVE
        or not _claim_matches_authorization(
            claim=current_claim,
            authorization=authorization,
        )
    ):
        return finish(
            UNKNOWN,
            "SELL_CLAIM_CHANGED_DURING_SUCCESS_RECONCILIATION",
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

    execution_status = current_execution.status
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
            "SELL_EXECUTION_CHANGED_DURING_SUCCESS_RECONCILIATION",
        )

    #
    # Exactly one mutation authority.
    #
    try:
        accounting_result = (
            record_successful_sell_and_consume_claim(
                authorization=authorization,
                fill=fill,
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SUCCESSFUL_SELL_ACCOUNTING_FAILED",
        )

    claim_status = (
        accounting_result.claim_status
    )
    terminal_at = (
        accounting_result.terminal_at
    )
    terminal_reason = (
        accounting_result.terminal_reason
    )

    if accounting_result.journal is not None:
        journal_outcome = (
            accounting_result.journal.outcome
        )

    if accounting_result.accounting is not None:
        net_wallet_proceeds_lamports = (
            accounting_result
            .accounting
            .net_wallet_proceeds_lamports
        )
        total_realized_pnl_lamports = (
            accounting_result
            .accounting
            .total_realized_pnl_lamports
        )

    if accounting_result.status == ACCOUNTING_BLOCK:
        return finish(
            HOLD,
            "SUCCESSFUL_SELL_ACCOUNTING_BLOCKED",
            *accounting_result.reasons,
        )

    if accounting_result.status == ACCOUNTING_UNKNOWN:
        return finish(
            UNKNOWN,
            "SUCCESSFUL_SELL_ACCOUNTING_UNKNOWN",
            *accounting_result.reasons,
        )

    if accounting_result.status != ACCOUNTING_PASS:
        return finish(
            UNKNOWN,
            "SUCCESSFUL_SELL_ACCOUNTING_STATUS_INVALID",
        )

    #
    # Final local proof of coherent terminal state.
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

        final_journal = (
            load_live_sell_transaction_journal_entry_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

        final_accounting = (
            _load_success_accounting_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "FINAL_SUCCESS_STATE_READ_FAILED",
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
            "FINAL_SUCCESS_STATE_MISSING",
        )

    final_execution = (
        final_execution_result.record
    )

    claim_status = final_claim.status
    terminal_at = final_claim.terminal_at
    terminal_reason = final_claim.terminal_reason

    execution_status = final_execution.status
    transaction_signature = (
        final_execution.transaction_signature
    )

    if final_journal is not None:
        journal_outcome = (
            final_journal.outcome
        )

    if final_accounting is not None:
        net_wallet_proceeds_lamports = (
            final_accounting
            .net_wallet_proceeds_lamports
        )
        total_realized_pnl_lamports = (
            final_accounting
            .total_realized_pnl_lamports
        )

    if (
        _artifact_identity(
            final_execution
        )
        != initial_identity
        or not _success_terminal_is_coherent(
            authorization=authorization,
            claim=final_claim,
            execution=final_execution,
            journal=final_journal,
            accounting=final_accounting,
        )
    ):
        return finish(
            UNKNOWN,
            "FINAL_SUCCESS_STATE_INCOHERENT",
        )

    return finish(
        RECONCILED,
        changed=accounting_result.changed,
    )
