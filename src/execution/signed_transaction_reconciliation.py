from dataclasses import dataclass
from pathlib import Path

from src.execution.signed_transaction_status import (
    ABSENT_EXPIRED,
    ABSENT_STILL_VALID,
    KNOWN,
    UNKNOWN as STATUS_UNKNOWN,
    resolve_signed_transaction_status,
)
from src.portfolio.live_reservations import (
    DB_PATH,
    RECONCILED_ABSENT_EXPIRED_REASON,
    RELEASED,
    RESERVATION_VERSION,
    SIGNED,
    SUBMITTED as RESERVATION_SUBMITTED,
    LiveCapitalReservation,
    load_capital_reservation_read_only,
    release_reconciled_absent_expired_reservation,
)


SIGNED_TRANSACTION_RECONCILIATION_VERSION = (
    "signed-transaction-reconciliation-v1"
)

RECONCILED = "RECONCILED"
HOLD = "HOLD"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SignedTransactionReconciliationResult:
    executor_version: str
    status: str
    reasons: tuple[str, ...]

    reservation_id: str
    transaction_signature: str | None

    status_observation_state: str | None

    reservation_status: str | None
    terminal_at: float | None
    terminal_reason: str | None

    changed: bool


def _artifact_identity(
    reservation: LiveCapitalReservation,
) -> tuple:
    """
    Immutable signed-transaction identity carried
    by Reservation v6.
    """

    return (
        reservation.reservation_version,
        reservation.signed_at,
        reservation.transaction_signature,
        reservation.signed_message_sha256,
        reservation.signed_transaction_sha256,
        reservation.signed_transaction_bytes,
        reservation.recent_blockhash,
        reservation.last_valid_block_height,
        reservation.blockhash_rpc_slot,
    )


def _terminal_reconciliation_is_coherent(
    reservation: LiveCapitalReservation,
) -> bool:
    """
    Validate enough terminal provenance to allow an
    already-reconciled RELEASED row to be treated as
    idempotently complete without another RPC read.
    """

    signature = reservation.transaction_signature

    transaction_sha256 = (
        reservation.signed_transaction_sha256
    )

    last_valid_block_height = (
        reservation.last_valid_block_height
    )

    blockhash_rpc_slot = (
        reservation.blockhash_rpc_slot
    )

    return (
        reservation.status == RELEASED
        and reservation.terminal_at is not None
        and reservation.terminal_reason
        == RECONCILED_ABSENT_EXPIRED_REASON
        and isinstance(signature, str)
        and bool(signature)
        and isinstance(
            transaction_sha256,
            str,
        )
        and len(transaction_sha256) == 64
        and all(
            character
            in "0123456789abcdef"
            for character
            in transaction_sha256
        )
        and isinstance(
            last_valid_block_height,
            int,
        )
        and not isinstance(
            last_valid_block_height,
            bool,
        )
        and last_valid_block_height >= 0
        and isinstance(
            blockhash_rpc_slot,
            int,
        )
        and not isinstance(
            blockhash_rpc_slot,
            bool,
        )
        and blockhash_rpc_slot >= 0
    )


async def reconcile_signed_transaction(
    *,
    reservation_id: str,
    db_path: Path = DB_PATH,
) -> SignedTransactionReconciliationResult:
    """
    Reconcile one persisted signed transaction.

    v1 is intentionally narrow.

    Only ABSENT_EXPIRED may cause a ledger mutation.

    ABSENT_STILL_VALID:
        hold capital.

    KNOWN:
        hold capital for later success/failure
        reconciliation.

    UNKNOWN:
        hold capital and report uncertainty.

    ABSENT_EXPIRED:
        re-read exact reservation authority,
        prove immutable artifact identity is unchanged,
        then invoke the dedicated atomic ledger
        transition.

    This executor has:
    - no transaction construction
    - no signing
    - no send authority
    - no resend authority
    - no retry authority
    - no live-position authority
    """

    transaction_signature: (
        str | None
    ) = None

    status_observation_state: (
        str | None
    ) = None

    reservation_status: (
        str | None
    ) = None

    terminal_at: (
        float | None
    ) = None

    terminal_reason: (
        str | None
    ) = None

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
    ) -> SignedTransactionReconciliationResult:
        return SignedTransactionReconciliationResult(
            executor_version=(
                SIGNED_TRANSACTION_RECONCILIATION_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            reservation_id=reservation_id,
            transaction_signature=(
                transaction_signature
            ),
            status_observation_state=(
                status_observation_state
            ),
            reservation_status=(
                reservation_status
            ),
            terminal_at=terminal_at,
            terminal_reason=terminal_reason,
            changed=changed,
        )

    if not reservation_id:
        return finish(
            UNKNOWN,
            "INVALID_RESERVATION_ID",
        )

    #
    # Initial observational snapshot.
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
            UNKNOWN,
            "RESERVATION_VERSION_MISMATCH",
        )

    #
    # Correctly reconciled terminal rows are
    # idempotently complete. Do not perform another
    # chain read.
    #
    if initial.status == RELEASED:
        if (
            initial.terminal_reason
            != RECONCILED_ABSENT_EXPIRED_REASON
        ):
            return finish(
                BLOCK,
                "RESERVATION_TERMINAL_OTHER_REASON",
            )

        if not _terminal_reconciliation_is_coherent(
            initial
        ):
            return finish(
                UNKNOWN,
                "RECONCILED_TERMINAL_RECORD_INCONSISTENT",
            )

        return finish(
            RECONCILED,
            changed=False,
        )

    if initial.status not in (
        SIGNED,
        RESERVATION_SUBMITTED,
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
    # Independent chain observation.
    #
    try:
        observation = (
            await resolve_signed_transaction_status(
                reservation_id=reservation_id,
                db_path=db_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            "STATUS_RESOLUTION_FAILED",
        )

    status_observation_state = (
        observation.state
    )

    if observation.state == STATUS_UNKNOWN:
        return finish(
            UNKNOWN,
            "STATUS_OBSERVATION_UNKNOWN",
            *observation.reasons,
        )

    if observation.state == KNOWN:
        return finish(
            HOLD,
            "TRANSACTION_KNOWN_REQUIRES_FURTHER_RECONCILIATION",
        )

    if (
        observation.state
        == ABSENT_STILL_VALID
    ):
        return finish(
            HOLD,
            "TRANSACTION_STILL_VALID",
        )

    if (
        observation.state
        != ABSENT_EXPIRED
    ):
        return finish(
            UNKNOWN,
            "STATUS_OBSERVATION_INVALID",
        )

    #
    # Bind the expiration proof to the exact
    # reservation/status artifact observed.
    #
    if (
        observation.reservation_id
        != reservation_id
        or observation.transaction_signature
        != initial.transaction_signature
        or observation.last_valid_block_height
        != initial.last_valid_block_height
        or observation.blockhash_rpc_slot
        != initial.blockhash_rpc_slot
    ):
        return finish(
            UNKNOWN,
            "STATUS_OBSERVATION_BINDING_MISMATCH",
        )

    #
    # Re-read immediately before acquiring ledger
    # mutation authority.
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

    reservation_status = current.status
    terminal_at = current.terminal_at
    terminal_reason = current.terminal_reason

    #
    # Another reconciler may have completed the
    # exact same terminal transition while status
    # resolution was in flight.
    #
    if current.status == RELEASED:
        if (
            current.terminal_reason
            == RECONCILED_ABSENT_EXPIRED_REASON
            and _terminal_reconciliation_is_coherent(
                current
            )
            and _artifact_identity(current)
            == initial_identity
        ):
            return finish(
                RECONCILED,
                changed=False,
            )

        return finish(
            UNKNOWN,
            "RESERVATION_CHANGED_DURING_STATUS_CHECK",
        )

    if current.status not in (
        SIGNED,
        RESERVATION_SUBMITTED,
    ):
        return finish(
            UNKNOWN,
            "RESERVATION_CHANGED_DURING_STATUS_CHECK",
        )

    if (
        _artifact_identity(current)
        != initial_identity
    ):
        return finish(
            UNKNOWN,
            "RESERVATION_CHANGED_DURING_STATUS_CHECK",
        )

    #
    # ABSENT_EXPIRED has been established and bound
    # to an unchanged exact signed artifact.
    #
    try:
        transition = (
            release_reconciled_absent_expired_reservation(
                reservation_id=reservation_id,
                transaction_signature=(
                    current.transaction_signature
                ),
                signed_transaction_sha256=(
                    current.signed_transaction_sha256
                ),
                last_valid_block_height=(
                    current.last_valid_block_height
                ),
                blockhash_rpc_slot=(
                    current.blockhash_rpc_slot
                ),
                db_path=db_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            "RECONCILIATION_RELEASE_FAILED",
        )

    if transition.status != "PASS":
        return finish(
            (
                BLOCK
                if transition.status == "BLOCK"
                else UNKNOWN
            ),
            "RECONCILIATION_RELEASE_FAILED",
            *transition.reasons,
        )

    persisted = transition.reservation

    if persisted is None:
        return finish(
            UNKNOWN,
            "RECONCILED_RESERVATION_MISSING",
        )

    transaction_signature = (
        persisted.transaction_signature
    )

    reservation_status = persisted.status
    terminal_at = persisted.terminal_at
    terminal_reason = persisted.terminal_reason

    if (
        persisted.status != RELEASED
        or persisted.terminal_at is None
        or persisted.terminal_reason
        != RECONCILED_ABSENT_EXPIRED_REASON
        or _artifact_identity(persisted)
        != initial_identity
    ):
        return finish(
            UNKNOWN,
            "RECONCILED_RESERVATION_VERIFICATION_FAILED",
        )

    return finish(
        RECONCILED,
        changed=bool(
            transition.changed
        ),
    )
