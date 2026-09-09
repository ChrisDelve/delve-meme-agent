from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.signed_transaction_receipt import (
    RESOLVED as RECEIPT_RESOLVED,
    SIGNED_TRANSACTION_RECEIPT_VERSION,
)
from src.execution.successful_pump_buy_fill import (
    BLOCK as FILL_BLOCK,
    PROVEN as FILL_PROVEN,
    UNKNOWN as FILL_UNKNOWN,
    SUCCESSFUL_PUMP_BUY_FILL_VERSION,
    resolve_successful_pump_buy_fill,
)
from src.portfolio.live_positions import (
    BLOCK as ACCOUNTING_BLOCK,
    PASS as ACCOUNTING_PASS,
    UNKNOWN as ACCOUNTING_UNKNOWN,
    LIVE_POSITION_VERSION,
    RECONCILED_SUCCESSFUL_BUY_REASON,
    load_live_position_read_only,
    record_successful_buy_and_open_position,
)
from src.portfolio.live_reservations import (
    BUY,
    DB_PATH,
    RELEASED,
    RESERVATION_VERSION,
    SIGNED,
    SUBMITTED,
    LiveCapitalReservation,
    load_capital_reservation_read_only,
)
from src.portfolio.live_transaction_journal import (
    LIVE_TRANSACTION_JOURNAL_VERSION,
    SUCCESS,
    load_transaction_journal_entry_read_only,
)


SUCCESSFUL_BUY_RECONCILIATION_VERSION = (
    "successful-buy-reconciliation-v1"
)

RECONCILED = "RECONCILED"
HOLD = "HOLD"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SuccessfulBuyReconciliationResult:
    executor_version: str

    status: str
    reasons: tuple[str, ...]

    reservation_id: str
    transaction_signature: str | None

    fill_status: str | None
    receipt_slot: int | None

    reservation_status: str | None
    terminal_at: float | None
    terminal_reason: str | None

    journal_outcome: str | None

    position_id: int | None
    position_status: str | None

    changed: bool


def _strict_nonnegative_int(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    )


def _strict_positive_int(
    value: Any,
) -> bool:
    return (
        _strict_nonnegative_int(value)
        and value > 0
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


def _nonempty_string(
    value: Any,
) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
    )


def _artifact_identity(
    reservation: LiveCapitalReservation,
) -> tuple:
    """
    Immutable successful-BUY authority.

    Successful accounting depends on both the signed
    transaction identity and the exact reserved
    economic authority.
    """

    return (
        reservation.reservation_version,
        reservation.wallet_pubkey,
        reservation.mint,
        reservation.side,

        reservation.spend_lamports,
        reservation.wallet_cost_lamports,

        reservation.signed_at,
        reservation.transaction_signature,
        reservation.signed_message_sha256,
        reservation.signed_transaction_sha256,
        reservation.signed_transaction_bytes,

        reservation.recent_blockhash,
        reservation.last_valid_block_height,
        reservation.blockhash_rpc_slot,
    )


def _fill_binding_matches(
    *,
    fill,
    reservation: LiveCapitalReservation,
) -> bool:
    """
    Bind a PROVEN fill object to the exact economic
    authority that may be mutated.

    This does not re-resolve the transaction. It only
    prevents an accidentally mismatched proof object
    from reaching live accounting.
    """

    try:
        return (
            fill.resolver_version
            == SUCCESSFUL_PUMP_BUY_FILL_VERSION

            and fill.status == FILL_PROVEN

            and fill.reservation_id
            == reservation.reservation_id

            and fill.transaction_signature
            == reservation.transaction_signature

            and fill.receipt_status
            == RECEIPT_RESOLVED

            and _strict_nonnegative_int(
                fill.receipt_slot
            )

            and fill.mint
            == reservation.mint

            and fill.wallet_pubkey
            == reservation.wallet_pubkey

            and _nonempty_string(
                fill.base_token_program
            )

            and _nonempty_string(
                fill.associated_base_user
            )

            and _nonempty_string(
                fill.quote_mint
            )

            and _valid_sha256(
                fill.persisted_transaction_sha256
            )

            and _valid_sha256(
                fill.observed_transaction_sha256
            )

            and fill.persisted_transaction_sha256
            == reservation.signed_transaction_sha256

            and fill.observed_transaction_sha256
            == reservation.signed_transaction_sha256

            and _strict_positive_int(
                fill.authorized_token_amount
            )

            and _strict_positive_int(
                fill.trade_event_token_amount
            )

            and _strict_nonnegative_int(
                fill.token_pre_amount
            )

            and _strict_positive_int(
                fill.token_post_amount
            )

            and _strict_positive_int(
                fill.token_delta
            )

            and fill.authorized_token_amount
            == fill.trade_event_token_amount

            and fill.authorized_token_amount
            == fill.token_delta

            and (
                fill.token_post_amount
                - fill.token_pre_amount
            )
            == fill.token_delta

            and _strict_positive_int(
                fill.max_sol_cost
            )

            and fill.max_sol_cost
            == reservation.spend_lamports

            and _strict_nonnegative_int(
                fill.fee_lamports
            )

            and _strict_nonnegative_int(
                fill.wallet_pre_balance_lamports
            )

            and _strict_nonnegative_int(
                fill.wallet_post_balance_lamports
            )

            and isinstance(
                fill.wallet_balance_delta_lamports,
                int,
            )

            and not isinstance(
                fill.wallet_balance_delta_lamports,
                bool,
            )

            and (
                fill.wallet_post_balance_lamports
                - fill.wallet_pre_balance_lamports
            )
            == fill.wallet_balance_delta_lamports

            and fill.wallet_balance_delta_lamports
            < 0

            and _strict_positive_int(
                fill.wallet_cost_lamports
            )

            and (
                -fill.wallet_balance_delta_lamports
                == fill.wallet_cost_lamports
            )

            and fill.wallet_cost_lamports
            <= reservation.wallet_cost_lamports

            and fill.fee_lamports
            <= fill.wallet_cost_lamports

            and _strict_nonnegative_int(
                fill.trade_event_sol_amount
            )

            and _strict_nonnegative_int(
                fill.protocol_fee_lamports
            )

            and _strict_nonnegative_int(
                fill.creator_fee_lamports
            )

            and _strict_nonnegative_int(
                fill.cashback_lamports
            )

            and _strict_nonnegative_int(
                fill.buyback_fee_lamports
            )

            and _strict_nonnegative_int(
                fill.quote_amount
            )
        )

    except Exception:
        return False


def _success_terminal_is_coherent(
    *,
    reservation,
    journal,
    position,
) -> bool:
    """
    Verify immutable local provenance for an already
    completed successful BUY.

    Mutable position fields such as tokens_held,
    remaining exposure and remaining cost basis are
    intentionally NOT required to equal their entry
    values. Future SELL accounting may change them.
    """

    try:
        if (
            reservation.reservation_version
            != RESERVATION_VERSION

            or reservation.status
            != RELEASED

            or reservation.side
            != BUY

            or reservation.terminal_at
            is None

            or reservation.terminal_reason
            != RECONCILED_SUCCESSFUL_BUY_REASON

            or journal is None
            or position is None
        ):
            return False

        if (
            journal.journal_version
            != LIVE_TRANSACTION_JOURNAL_VERSION

            or journal.outcome
            != SUCCESS

            or journal.transaction_error_json
            != "null"

            or journal.reservation_id
            != reservation.reservation_id

            or journal.transaction_signature
            != reservation.transaction_signature

            or journal.wallet_pubkey
            != reservation.wallet_pubkey

            or journal.fee_payer_pubkey
            != reservation.wallet_pubkey

            or journal.mint
            != reservation.mint

            or journal.side
            != BUY

            or journal.signed_transaction_sha256
            != reservation.signed_transaction_sha256

            or journal.receipt_transaction_sha256
            != reservation.signed_transaction_sha256

            or journal.receipt_resolver_version
            != SIGNED_TRANSACTION_RECEIPT_VERSION

            or journal.last_valid_block_height
            != reservation.last_valid_block_height

            or journal.blockhash_rpc_slot
            != reservation.blockhash_rpc_slot

            or journal.recorded_at
            != reservation.terminal_at
        ):
            return False

        if (
            position.position_version
            != LIVE_POSITION_VERSION

            or not _strict_positive_int(
                position.position_id
            )

            or position.reservation_id
            != reservation.reservation_id

            or position.entry_signature
            != reservation.transaction_signature

            or position.wallet_pubkey
            != reservation.wallet_pubkey

            or position.mint
            != reservation.mint

            or not _nonempty_string(
                position.status
            )

            or position.fill_resolver_version
            != SUCCESSFUL_PUMP_BUY_FILL_VERSION

            or position.signed_transaction_sha256
            != reservation.signed_transaction_sha256

            or position.observed_transaction_sha256
            != reservation.signed_transaction_sha256

            or position.entry_slot
            != journal.slot

            or position.entry_block_time
            != journal.block_time

            or position.created_at
            != reservation.terminal_at
        ):
            return False

        if (
            not _nonempty_string(
                position.base_token_program
            )

            or not _nonempty_string(
                position.associated_base_user
            )

            or not _nonempty_string(
                position.quote_mint
            )

            or not _strict_positive_int(
                position.authorized_token_amount
            )

            or position.authorized_token_amount
            != position.trade_event_token_amount

            or not _strict_nonnegative_int(
                position.token_pre_amount
            )

            or not _strict_positive_int(
                position.token_post_amount
            )

            or not _strict_positive_int(
                position.entry_tokens
            )

            or (
                position.token_post_amount
                - position.token_pre_amount
            )
            != position.entry_tokens

            or position.authorized_token_amount
            != position.entry_tokens
        ):
            return False

        if (
            not _strict_positive_int(
                reservation.spend_lamports
            )

            or not _strict_positive_int(
                reservation.wallet_cost_lamports
            )

            or position.authorized_max_sol_cost_lamports
            != reservation.spend_lamports

            or position.entry_exposure_lamports
            != reservation.spend_lamports

            or not _strict_positive_int(
                position.entry_wallet_cost_lamports
            )

            or position.entry_wallet_cost_lamports
            > reservation.wallet_cost_lamports
        ):
            return False

        if (
            not _strict_nonnegative_int(
                journal.fee_lamports
            )

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
            )
            != journal.fee_payer_balance_delta_lamports

            or journal.fee_payer_balance_delta_lamports
            >= 0

            or (
                -journal.fee_payer_balance_delta_lamports
                != position.entry_wallet_cost_lamports
            )

            or journal.fee_lamports
            > position.entry_wallet_cost_lamports
        ):
            return False

        if (
            position.network_fee_lamports
            != journal.fee_lamports

            or position.wallet_pre_balance_lamports
            != journal.fee_payer_pre_balance_lamports

            or position.wallet_post_balance_lamports
            != journal.fee_payer_post_balance_lamports

            or position.wallet_balance_delta_lamports
            != journal.fee_payer_balance_delta_lamports
        ):
            return False

        return True

    except Exception:
        return False


async def reconcile_successful_buy(
    *,
    reservation_id: str,
    db_path: Path = DB_PATH,
) -> SuccessfulBuyReconciliationResult:
    """
    Reconcile exactly one proven successful Pump BUY.

    This executor has no:
    - direct database-write authority
    - signing authority
    - send authority
    - transaction-build authority
    - strategy authority
    - duplicated transaction/fill decoding

    One PROVEN fill may reach exactly one atomic
    successful-BUY accounting primitive.
    """

    transaction_signature: (
        str | None
    ) = None

    fill_status: str | None = None
    receipt_slot: int | None = None

    reservation_status: str | None = None
    terminal_at: float | None = None
    terminal_reason: str | None = None

    journal_outcome: str | None = None

    position_id: int | None = None
    position_status: str | None = None

    if not isinstance(
        reservation_id,
        str,
    ):
        reservation_id = ""

    reservation_id = (
        reservation_id.strip()
    )

    def finish(
        status: str,
        *reasons: str,
        changed: bool = False,
    ) -> SuccessfulBuyReconciliationResult:
        return SuccessfulBuyReconciliationResult(
            executor_version=(
                SUCCESSFUL_BUY_RECONCILIATION_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            reservation_id=reservation_id,
            transaction_signature=(
                transaction_signature
            ),
            fill_status=fill_status,
            receipt_slot=receipt_slot,
            reservation_status=(
                reservation_status
            ),
            terminal_at=terminal_at,
            terminal_reason=(
                terminal_reason
            ),
            journal_outcome=(
                journal_outcome
            ),
            position_id=position_id,
            position_status=(
                position_status
            ),
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

    reservation_status = initial.status
    terminal_at = initial.terminal_at
    terminal_reason = initial.terminal_reason

    if (
        initial.reservation_version
        != RESERVATION_VERSION
    ):
        return finish(
            BLOCK,
            "RESERVATION_VERSION_MISMATCH",
        )

    #
    # Crash-safe/idempotent local recovery:
    # an already-accounted successful BUY must not
    # call chain RPC again.
    #
    if initial.status == RELEASED:
        if (
            initial.terminal_reason
            != RECONCILED_SUCCESSFUL_BUY_REASON
        ):
            return finish(
                BLOCK,
                "RESERVATION_ALREADY_TERMINAL_OTHER_REASON",
            )

        try:
            journal = (
                load_transaction_journal_entry_read_only(
                    reservation_id=reservation_id,
                    db_path=db_path,
                )
            )

            position = (
                load_live_position_read_only(
                    reservation_id=reservation_id,
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

        if position is not None:
            position_id = position.position_id
            position_status = position.status

        if not _success_terminal_is_coherent(
            reservation=initial,
            journal=journal,
            position=position,
        ):
            return finish(
                UNKNOWN,
                "SUCCESSFUL_BUY_TERMINAL_STATE_INCOHERENT",
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
            "RESERVATION_NOT_SIGNED_OR_SUBMITTED",
        )

    if initial.side != BUY:
        return finish(
            BLOCK,
            "RESERVATION_NOT_BUY",
        )

    initial_identity = (
        _artifact_identity(
            initial
        )
    )

    #
    # Resolve the exact already-landed successful
    # transaction and Pump fill. This resolver owns
    # all RPC/transaction/event/token proof.
    #
    try:
        fill = (
            await resolve_successful_pump_buy_fill(
                reservation_id=reservation_id,
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SUCCESSFUL_FILL_RESOLUTION_FAILED",
        )

    fill_status = fill.status
    receipt_slot = fill.receipt_slot

    if fill.status == FILL_UNKNOWN:
        return finish(
            UNKNOWN,
            "SUCCESSFUL_FILL_UNKNOWN",
            *fill.reasons,
        )

    if fill.status == FILL_BLOCK:
        return finish(
            HOLD,
            "SUCCESSFUL_FILL_BLOCKED",
            *fill.reasons,
        )

    if fill.status != FILL_PROVEN:
        return finish(
            UNKNOWN,
            "SUCCESSFUL_FILL_STATUS_INVALID",
        )

    if not _fill_binding_matches(
        fill=fill,
        reservation=initial,
    ):
        return finish(
            UNKNOWN,
            "SUCCESSFUL_FILL_BINDING_MISMATCH",
        )

    #
    # Re-read immediately before the mutation
    # authority. A concurrent local change cannot
    # inherit stale fill evidence.
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

    reservation_status = current.status
    terminal_at = current.terminal_at
    terminal_reason = current.terminal_reason

    #
    # Another process may have completed the exact
    # same successful accounting transition while
    # fill resolution was in flight.
    #
    if current.status == RELEASED:
        if (
            current.terminal_reason
            != RECONCILED_SUCCESSFUL_BUY_REASON
        ):
            return finish(
                BLOCK,
                "RESERVATION_TERMINATED_DURING_FILL",
            )

        try:
            journal = (
                load_transaction_journal_entry_read_only(
                    reservation_id=reservation_id,
                    db_path=db_path,
                )
            )

            position = (
                load_live_position_read_only(
                    reservation_id=reservation_id,
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

        if position is not None:
            position_id = position.position_id
            position_status = position.status

        if not _success_terminal_is_coherent(
            reservation=current,
            journal=journal,
            position=position,
        ):
            return finish(
                UNKNOWN,
                "CONCURRENT_SUCCESS_STATE_INCOHERENT",
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
            "RESERVATION_CHANGED_DURING_SUCCESS_RECONCILIATION",
        )

    #
    # Exactly one mutation authority.
    #
    try:
        accounting = (
            record_successful_buy_and_open_position(
                reservation_id=reservation_id,

                fill_resolver_version=(
                    fill.resolver_version
                ),

                transaction_signature=(
                    fill.transaction_signature
                ),

                signed_transaction_sha256=(
                    fill.persisted_transaction_sha256
                ),

                observed_transaction_sha256=(
                    fill.observed_transaction_sha256
                ),

                entry_slot=fill.receipt_slot,
                block_time=fill.block_time,

                mint=fill.mint,
                wallet_pubkey=(
                    fill.wallet_pubkey
                ),

                base_token_program=(
                    fill.base_token_program
                ),

                associated_base_user=(
                    fill.associated_base_user
                ),

                authorized_token_amount=(
                    fill.authorized_token_amount
                ),

                max_sol_cost=(
                    fill.max_sol_cost
                ),

                trade_event_token_amount=(
                    fill.trade_event_token_amount
                ),

                token_pre_amount=(
                    fill.token_pre_amount
                ),

                token_post_amount=(
                    fill.token_post_amount
                ),

                token_delta=(
                    fill.token_delta
                ),

                fee_lamports=(
                    fill.fee_lamports
                ),

                wallet_pre_balance_lamports=(
                    fill.wallet_pre_balance_lamports
                ),

                wallet_post_balance_lamports=(
                    fill.wallet_post_balance_lamports
                ),

                wallet_balance_delta_lamports=(
                    fill.wallet_balance_delta_lamports
                ),

                wallet_cost_lamports=(
                    fill.wallet_cost_lamports
                ),

                trade_event_sol_amount=(
                    fill.trade_event_sol_amount
                ),

                protocol_fee_lamports=(
                    fill.protocol_fee_lamports
                ),

                creator_fee_lamports=(
                    fill.creator_fee_lamports
                ),

                cashback_lamports=(
                    fill.cashback_lamports
                ),

                buyback_fee_lamports=(
                    fill.buyback_fee_lamports
                ),

                quote_mint=(
                    fill.quote_mint
                ),

                quote_amount=(
                    fill.quote_amount
                ),

                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SUCCESSFUL_BUY_ACCOUNTING_FAILED",
        )

    if accounting.status == ACCOUNTING_BLOCK:
        return finish(
            HOLD,
            "SUCCESSFUL_BUY_ACCOUNTING_BLOCKED",
            *accounting.reasons,
        )

    if accounting.status == ACCOUNTING_UNKNOWN:
        return finish(
            UNKNOWN,
            "SUCCESSFUL_BUY_ACCOUNTING_UNKNOWN",
            *accounting.reasons,
        )

    if accounting.status != ACCOUNTING_PASS:
        return finish(
            UNKNOWN,
            "SUCCESSFUL_BUY_ACCOUNTING_STATUS_INVALID",
        )

    #
    # Never trust a mutation return object alone.
    # Re-read all three authoritative local records.
    #
    try:
        final_reservation = (
            load_capital_reservation_read_only(
                reservation_id=reservation_id,
                db_path=db_path,
            )
        )

        journal = (
            load_transaction_journal_entry_read_only(
                reservation_id=reservation_id,
                db_path=db_path,
            )
        )

        position = (
            load_live_position_read_only(
                reservation_id=reservation_id,
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "FINAL_SUCCESS_STATE_READ_FAILED",
        )

    if final_reservation is None:
        return finish(
            UNKNOWN,
            "FINAL_RESERVATION_MISSING",
        )

    reservation_status = (
        final_reservation.status
    )

    terminal_at = (
        final_reservation.terminal_at
    )

    terminal_reason = (
        final_reservation.terminal_reason
    )

    if journal is not None:
        journal_outcome = journal.outcome

    if position is not None:
        position_id = position.position_id
        position_status = position.status

    if not _success_terminal_is_coherent(
        reservation=final_reservation,
        journal=journal,
        position=position,
    ):
        return finish(
            UNKNOWN,
            "FINAL_SUCCESS_STATE_INCOHERENT",
        )

    return finish(
        RECONCILED,
        changed=bool(
            accounting.changed
        ),
    )
