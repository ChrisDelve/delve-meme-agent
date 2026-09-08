from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.signed_transaction_receipt import (
    BLOCK as RECEIPT_BLOCK,
    RESOLVED as RECEIPT_RESOLVED,
    UNKNOWN as RECEIPT_UNKNOWN,
    SIGNED_TRANSACTION_RECEIPT_VERSION,
    resolve_signed_transaction_receipt,
)
from src.portfolio.live_reservations import (
    DB_PATH,
    RELEASED,
    RESERVATION_VERSION,
    SIGNED,
    SUBMITTED,
    LiveCapitalReservation,
    load_capital_reservation_read_only,
)
from src.portfolio.live_transaction_journal import (
    FAILED,
    LIVE_TRANSACTION_JOURNAL_VERSION,
    PASS as JOURNAL_PASS,
    RECONCILED_FAILED_TRANSACTION_REASON,
    LiveTransactionJournalEntry,
    load_transaction_journal_entry_read_only,
    record_failed_transaction_and_release_reservation,
)


FAILED_TRANSACTION_RECONCILIATION_VERSION = (
    "failed-transaction-reconciliation-v1"
)

RECONCILED = "RECONCILED"
HOLD = "HOLD"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class FailedTransactionReconciliationResult:
    executor_version: str

    status: str
    reasons: tuple[str, ...]

    reservation_id: str
    transaction_signature: str | None

    receipt_status: str | None
    receipt_slot: int | None

    reservation_status: str | None
    terminal_at: float | None
    terminal_reason: str | None

    journal_outcome: str | None

    changed: bool


def _strict_nonnegative_int(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    )


def _valid_sha256(
    value: Any,
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


def _artifact_identity(
    reservation: LiveCapitalReservation,
) -> tuple:
    return (
        reservation.reservation_version,
        reservation.wallet_pubkey,
        reservation.mint,
        reservation.side,
        reservation.signed_at,
        reservation.transaction_signature,
        reservation.signed_message_sha256,
        reservation.signed_transaction_sha256,
        reservation.signed_transaction_bytes,
        reservation.recent_blockhash,
        reservation.last_valid_block_height,
        reservation.blockhash_rpc_slot,
    )


def _failed_terminal_is_coherent(
    reservation: LiveCapitalReservation,
    entry: LiveTransactionJournalEntry | None,
) -> bool:
    if (
        reservation.reservation_version
        != RESERVATION_VERSION
        or reservation.status
        != RELEASED
        or reservation.terminal_at
        is None
        or reservation.terminal_reason
        != RECONCILED_FAILED_TRANSACTION_REASON
        or entry is None
    ):
        return False

    if (
        entry.journal_version
        != LIVE_TRANSACTION_JOURNAL_VERSION
        or entry.outcome
        != FAILED
        or entry.reservation_id
        != reservation.reservation_id
        or entry.transaction_signature
        != reservation.transaction_signature
        or entry.wallet_pubkey
        != reservation.wallet_pubkey
        or entry.fee_payer_pubkey
        != reservation.wallet_pubkey
        or entry.mint
        != reservation.mint
        or entry.side
        != reservation.side
        or entry.signed_transaction_sha256
        != reservation.signed_transaction_sha256
        or entry.receipt_transaction_sha256
        != reservation.signed_transaction_sha256
        or entry.receipt_resolver_version
        != SIGNED_TRANSACTION_RECEIPT_VERSION
        or entry.last_valid_block_height
        != reservation.last_valid_block_height
        or entry.blockhash_rpc_slot
        != reservation.blockhash_rpc_slot
        or entry.recorded_at
        != reservation.terminal_at
    ):
        return False

    if (
        not _strict_nonnegative_int(
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
        decoded_error = json.loads(
            entry.transaction_error_json
        )
    except (
        TypeError,
        ValueError,
    ):
        return False

    if decoded_error is None:
        return False

    return True


def _receipt_binding_matches(
    *,
    receipt,
    reservation: LiveCapitalReservation,
) -> bool:
    return (
        receipt.reservation_id
        == reservation.reservation_id
        and receipt.transaction_signature
        == reservation.transaction_signature
        and receipt.persisted_transaction_sha256
        == reservation.signed_transaction_sha256
        and receipt.receipt_transaction_sha256
        == reservation.signed_transaction_sha256
        and receipt.fee_payer_pubkey
        == reservation.wallet_pubkey
        and _strict_nonnegative_int(
            receipt.receipt_slot
        )
        and _strict_nonnegative_int(
            receipt.fee_lamports
        )
        and _strict_nonnegative_int(
            receipt.fee_payer_pre_balance_lamports
        )
        and _strict_nonnegative_int(
            receipt.fee_payer_post_balance_lamports
        )
        and isinstance(
            receipt.fee_payer_balance_delta_lamports,
            int,
        )
        and not isinstance(
            receipt.fee_payer_balance_delta_lamports,
            bool,
        )
        and (
            receipt.fee_payer_post_balance_lamports
            - receipt.fee_payer_pre_balance_lamports
            == receipt.fee_payer_balance_delta_lamports
        )
        and (
            receipt.fee_payer_balance_delta_lamports
            == -receipt.fee_lamports
        )
    )


async def reconcile_failed_transaction(
    *,
    reservation_id: str,
    db_path: Path = DB_PATH,
) -> FailedTransactionReconciliationResult:
    """
    Reconcile exactly one landed failed transaction.

    This executor has no:
    - send authority
    - signing authority
    - transaction-build authority
    - position authority
    - direct database-write authority

    Only a RESOLVED landed receipt with a non-null
    transaction error may reach the single atomic
    journal/release mutation primitive.

    Successful transactions remain held until live
    fill/position accounting exists.
    """

    transaction_signature: (
        str | None
    ) = None

    receipt_status: str | None = None
    receipt_slot: int | None = None

    reservation_status: str | None = None
    terminal_at: float | None = None
    terminal_reason: str | None = None

    journal_outcome: str | None = None

    if not isinstance(
        reservation_id,
        str,
    ):
        reservation_id = ""

    reservation_id = reservation_id.strip()

    def finish(
        status: str,
        *reasons: str,
        changed: bool = False,
    ) -> FailedTransactionReconciliationResult:
        return FailedTransactionReconciliationResult(
            executor_version=(
                FAILED_TRANSACTION_RECONCILIATION_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            reservation_id=reservation_id,
            transaction_signature=(
                transaction_signature
            ),
            receipt_status=receipt_status,
            receipt_slot=receipt_slot,
            reservation_status=(
                reservation_status
            ),
            terminal_at=terminal_at,
            terminal_reason=terminal_reason,
            journal_outcome=journal_outcome,
            changed=changed,
        )

    if not reservation_id:
        return finish(
            UNKNOWN,
            "INVALID_RESERVATION_ID",
        )

    #
    # Initial local authority snapshot.
    #
    try:
        initial = (
            load_capital_reservation_read_only(
                reservation_id=reservation_id,
                db_path=db_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            "RESERVATION_READ_FAILED",
        )

    if initial is None:
        return finish(
            UNKNOWN,
            "RESERVATION_NOT_FOUND",
        )

    transaction_signature = (
        initial.transaction_signature
    )

    reservation_status = (
        initial.status
    )

    terminal_at = (
        initial.terminal_at
    )

    terminal_reason = (
        initial.terminal_reason
    )

    if (
        initial.reservation_version
        != RESERVATION_VERSION
    ):
        return finish(
            BLOCK,
            "RESERVATION_VERSION_MISMATCH",
        )

    #
    # Already-completed failed reconciliation is
    # recovered locally without chain observation.
    #
    if initial.status == RELEASED:
        if (
            initial.terminal_reason
            != RECONCILED_FAILED_TRANSACTION_REASON
        ):
            return finish(
                BLOCK,
                "FAILED_RECONCILIATION_TERMINAL_REASON_MISMATCH",
            )

        try:
            entry = (
                load_transaction_journal_entry_read_only(
                    reservation_id=reservation_id,
                    db_path=db_path,
                )
            )
        except Exception:
            return finish(
                UNKNOWN,
                "FAILED_RECONCILIATION_JOURNAL_READ_FAILED",
            )

        if not _failed_terminal_is_coherent(
            initial,
            entry,
        ):
            return finish(
                UNKNOWN,
                "FAILED_RECONCILIATION_JOURNAL_INCOHERENT",
            )

        journal_outcome = (
            entry.outcome
        )

        return finish(
            RECONCILED,
            changed=False,
        )

    if initial.status not in (
        SIGNED,
        SUBMITTED,
    ):
        return finish(
            BLOCK,
            "RESERVATION_NOT_RECONCILABLE",
        )

    initial_identity = (
        _artifact_identity(
            initial
        )
    )

    #
    # Obtain exact landed transaction evidence.
    #
    try:
        receipt = (
            await resolve_signed_transaction_receipt(
                reservation_id=reservation_id,
                db_path=db_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            "RECEIPT_RESOLUTION_FAILED",
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
            "RECEIPT_UNKNOWN",
            *receipt.reasons,
        )

    if receipt.status == RECEIPT_BLOCK:
        return finish(
            HOLD,
            "TRANSACTION_NOT_READY_FOR_FAILED_RECONCILIATION",
            *receipt.reasons,
        )

    if receipt.status != RECEIPT_RESOLVED:
        return finish(
            UNKNOWN,
            "UNEXPECTED_RECEIPT_STATUS",
        )

    #
    # Successful landed transactions require live
    # fill/position accounting and must stay held.
    #
    if receipt.transaction_error is None:
        return finish(
            HOLD,
            "SUCCESSFUL_TRANSACTION_REQUIRES_FILL_RECONCILIATION",
        )

    if (
        receipt.resolver_version
        != SIGNED_TRANSACTION_RECEIPT_VERSION
        or not _receipt_binding_matches(
            receipt=receipt,
            reservation=initial,
        )
    ):
        return finish(
            UNKNOWN,
            "FAILED_RECEIPT_BINDING_MISMATCH",
        )

    #
    # Re-read after receipt observation.
    #
    try:
        current = (
            load_capital_reservation_read_only(
                reservation_id=reservation_id,
                db_path=db_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            "RESERVATION_REREAD_FAILED",
        )

    if current is None:
        return finish(
            UNKNOWN,
            "RESERVATION_DISAPPEARED",
        )

    transaction_signature = (
        current.transaction_signature
    )

    reservation_status = (
        current.status
    )

    terminal_at = (
        current.terminal_at
    )

    terminal_reason = (
        current.terminal_reason
    )

    #
    # Another reconciler may have atomically won
    # while receipt resolution was running.
    #
    if current.status == RELEASED:
        if (
            current.terminal_reason
            != RECONCILED_FAILED_TRANSACTION_REASON
        ):
            return finish(
                BLOCK,
                "FAILED_RECONCILIATION_TERMINAL_REASON_MISMATCH",
            )

        try:
            entry = (
                load_transaction_journal_entry_read_only(
                    reservation_id=reservation_id,
                    db_path=db_path,
                )
            )
        except Exception:
            return finish(
                UNKNOWN,
                "FAILED_RECONCILIATION_JOURNAL_READ_FAILED",
            )

        if (
            _artifact_identity(current)
            != initial_identity
            or not _failed_terminal_is_coherent(
                current,
                entry,
            )
        ):
            return finish(
                UNKNOWN,
                "FAILED_RECONCILIATION_CONCURRENT_STATE_INCOHERENT",
            )

        journal_outcome = (
            entry.outcome
        )

        return finish(
            RECONCILED,
            changed=False,
        )

    if (
        current.status
        not in (
            SIGNED,
            SUBMITTED,
        )
        or _artifact_identity(current)
        != initial_identity
    ):
        return finish(
            UNKNOWN,
            "RESERVATION_CHANGED_DURING_RECEIPT_CHECK",
        )

    #
    # Single mutation authority call.
    #
    try:
        transition = (
            record_failed_transaction_and_release_reservation(
                reservation_id=reservation_id,
                receipt_resolver_version=(
                    receipt.resolver_version
                ),
                transaction_signature=(
                    receipt.transaction_signature
                ),
                signed_transaction_sha256=(
                    current.signed_transaction_sha256
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
            "FAILED_TRANSACTION_LEDGER_TRANSITION_FAILED",
        )

    if (
        transition.status
        != JOURNAL_PASS
    ):
        return finish(
            UNKNOWN,
            "FAILED_TRANSACTION_LEDGER_TRANSITION_REJECTED",
            *transition.reasons,
        )

    #
    # Verify the authoritative post-transition state
    # through read-only interfaces.
    #
    try:
        persisted = (
            load_capital_reservation_read_only(
                reservation_id=reservation_id,
                db_path=db_path,
            )
        )

        entry = (
            load_transaction_journal_entry_read_only(
                reservation_id=reservation_id,
                db_path=db_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            "FAILED_RECONCILIATION_FINAL_READ_FAILED",
        )

    if (
        persisted is None
        or _artifact_identity(persisted)
        != initial_identity
        or not _failed_terminal_is_coherent(
            persisted,
            entry,
        )
    ):
        return finish(
            UNKNOWN,
            "FAILED_RECONCILIATION_FINAL_VERIFICATION_FAILED",
        )

    transaction_signature = (
        persisted.transaction_signature
    )

    reservation_status = (
        persisted.status
    )

    terminal_at = (
        persisted.terminal_at
    )

    terminal_reason = (
        persisted.terminal_reason
    )

    journal_outcome = (
        entry.outcome
    )

    return finish(
        RECONCILED,
        changed=transition.changed,
    )
