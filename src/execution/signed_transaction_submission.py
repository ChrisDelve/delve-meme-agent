from dataclasses import dataclass
from pathlib import Path

from src.execution.signed_transaction_status import (
    ABSENT_EXPIRED,
    ABSENT_STILL_VALID,
    KNOWN,
    UNKNOWN as STATUS_UNKNOWN,
    resolve_signed_transaction_status,
)
from src.execution.solana_single_attempt_rpc import (
    SingleAttemptRpcWriteError,
    SingleAttemptSolanaRpcClient,
)
from src.portfolio.live_reservations import (
    DB_PATH,
    RESERVATION_VERSION,
    SIGNED,
    SUBMITTED as RESERVATION_SUBMITTED,
    LiveCapitalReservation,
    acknowledge_reservation_submitted,
    arm_reservation_submission,
    load_capital_reservation_read_only,
)


SIGNED_TRANSACTION_SUBMISSION_VERSION = (
    "signed-transaction-submission-v1"
)

SUBMITTED = "SUBMITTED"
BLOCK = "BLOCK"
RECONCILIATION_REQUIRED = (
    "RECONCILIATION_REQUIRED"
)
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SignedTransactionSubmissionResult:
    executor_version: str
    status: str
    reasons: tuple[str, ...]

    reservation_id: str
    transaction_signature: str | None

    status_observation_state: str | None

    relay_invoked: bool
    returned_signature: str | None

    reservation_status: str | None
    submission_started_at: float | None
    submitted_at: float | None


def _artifact_identity(
    reservation: LiveCapitalReservation,
) -> tuple:
    """
    Identity of the immutable signed transaction
    authority carried by Reservation v6.
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


async def submit_signed_transaction_once(
    *,
    reservation_id: str,
    db_path: Path = DB_PATH,
) -> SignedTransactionSubmissionResult:
    """
    Relay exactly one already-signed transaction.

    Authority sequence:

    1. Load authoritative read-only reservation.
    2. Require SIGNED and never previously armed.
    3. Resolve chain status.
    4. Require ABSENT_STILL_VALID.
    5. Re-read reservation and prove it did not
       change during the status observation.
    6. Atomically acquire the durable submission
       boundary.
    7. Perform exactly one write-RPC invocation.
    8. Require returned signature to equal the
       persisted transaction signature.
    9. Atomically acknowledge SUBMITTED.

    There is:
    - no transaction construction
    - no signing
    - no re-signing
    - no network retry
    - no automatic second relay

    Any uncertainty after the durable arm boundary
    becomes RECONCILIATION_REQUIRED.
    """

    transaction_signature: (
        str | None
    ) = None

    status_observation_state: (
        str | None
    ) = None

    relay_invoked = False

    returned_signature: (
        str | None
    ) = None

    reservation_status: (
        str | None
    ) = None

    submission_started_at: (
        float | None
    ) = None

    submitted_at: (
        float | None
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
    ) -> SignedTransactionSubmissionResult:
        return SignedTransactionSubmissionResult(
            executor_version=(
                SIGNED_TRANSACTION_SUBMISSION_VERSION
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
            relay_invoked=relay_invoked,
            returned_signature=(
                returned_signature
            ),
            reservation_status=(
                reservation_status
            ),
            submission_started_at=(
                submission_started_at
            ),
            submitted_at=submitted_at,
        )

    if not reservation_id:
        return finish(
            UNKNOWN,
            "INVALID_RESERVATION_ID",
        )

    #
    # Local authority check before performing
    # another chain read.
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

    reservation_status = initial.status
    transaction_signature = (
        initial.transaction_signature
    )
    submission_started_at = (
        initial.submission_started_at
    )
    submitted_at = initial.submitted_at

    if (
        initial.reservation_version
        != RESERVATION_VERSION
    ):
        return finish(
            UNKNOWN,
            "RESERVATION_VERSION_MISMATCH",
        )

    if initial.status == RESERVATION_SUBMITTED:
        return finish(
            RECONCILIATION_REQUIRED,
            "RESERVATION_ALREADY_SUBMITTED",
        )

    if initial.status != SIGNED:
        return finish(
            BLOCK,
            "RESERVATION_NOT_SIGNED",
        )

    if initial.submitted_at is not None:
        return finish(
            UNKNOWN,
            "SUBMISSION_METADATA_INCONSISTENT",
        )

    if (
        initial.submission_started_at
        is not None
    ):
        if (
            initial.submission_attempt_count
            >= 1
        ):
            return finish(
                RECONCILIATION_REQUIRED,
                "SUBMISSION_ALREADY_ARMED_REQUIRES_RECONCILIATION",
            )

        return finish(
            UNKNOWN,
            "SUBMISSION_METADATA_INCONSISTENT",
        )

    if (
        initial.submission_attempt_count
        != 0
    ):
        return finish(
            UNKNOWN,
            "SUBMISSION_METADATA_INCONSISTENT",
        )

    initial_identity = (
        _artifact_identity(
            initial
        )
    )

    #
    # Status-before-send.
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

    if observation.state == KNOWN:
        return finish(
            RECONCILIATION_REQUIRED,
            "TRANSACTION_ALREADY_KNOWN",
        )

    if observation.state == ABSENT_EXPIRED:
        return finish(
            RECONCILIATION_REQUIRED,
            "TRANSACTION_EXPIRED_REQUIRES_RECONCILIATION",
        )

    if observation.state == STATUS_UNKNOWN:
        return finish(
            UNKNOWN,
            "STATUS_OBSERVATION_UNKNOWN",
            *observation.reasons,
        )

    if (
        observation.state
        != ABSENT_STILL_VALID
    ):
        return finish(
            UNKNOWN,
            "STATUS_OBSERVATION_INVALID",
        )

    #
    # Bind the observation to the exact reservation
    # evidence we loaded before it.
    #
    if (
        observation.transaction_signature
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
    # Re-read immediately before acquiring send
    # authority. This closes the normal concurrent
    # executor race:
    #
    # both may observe ABSENT_STILL_VALID,
    # but only one may arm.
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
    submission_started_at = (
        current.submission_started_at
    )
    submitted_at = current.submitted_at

    if current.status == RESERVATION_SUBMITTED:
        return finish(
            RECONCILIATION_REQUIRED,
            "RESERVATION_BECAME_SUBMITTED",
        )

    if (
        current.status == SIGNED
        and current.submission_started_at
        is not None
        and current.submission_attempt_count
        >= 1
    ):
        return finish(
            RECONCILIATION_REQUIRED,
            "SUBMISSION_ALREADY_ARMED_REQUIRES_RECONCILIATION",
        )

    if current.status != SIGNED:
        return finish(
            BLOCK,
            "RESERVATION_NOT_SIGNED",
        )

    if (
        current.submission_started_at
        is not None
        or current.submission_attempt_count
        != 0
        or current.submitted_at
        is not None
    ):
        return finish(
            UNKNOWN,
            "SUBMISSION_METADATA_INCONSISTENT",
        )

    if (
        _artifact_identity(
            current
        )
        != initial_identity
    ):
        return finish(
            UNKNOWN,
            "RESERVATION_CHANGED_DURING_STATUS_CHECK",
        )

    #
    # Durable send authority.
    #
    try:
        arm = arm_reservation_submission(
            reservation_id=reservation_id,
            db_path=db_path,
        )
    except Exception:
        return finish(
            UNKNOWN,
            "SUBMISSION_ARM_FAILED",
        )

    if arm.status != "PASS":
        if (
            "SUBMISSION_ALREADY_ARMED_REQUIRES_RECONCILIATION"
            in arm.reasons
        ):
            return finish(
                RECONCILIATION_REQUIRED,
                *arm.reasons,
            )

        return finish(
            (
                BLOCK
                if arm.status == "BLOCK"
                else UNKNOWN
            ),
            "SUBMISSION_ARM_FAILED",
            *arm.reasons,
        )

    #
    # PASS is not enough. This invocation must
    # have newly acquired the boundary.
    #
    if not arm.changed:
        return finish(
            RECONCILIATION_REQUIRED,
            "SUBMISSION_ARM_DID_NOT_ACQUIRE_SEND_AUTHORITY",
        )

    armed = arm.reservation

    if armed is None:
        return finish(
            RECONCILIATION_REQUIRED,
            "ARMED_RESERVATION_MISSING",
        )

    reservation_status = armed.status
    transaction_signature = (
        armed.transaction_signature
    )
    submission_started_at = (
        armed.submission_started_at
    )
    submitted_at = armed.submitted_at

    if (
        armed.status != SIGNED
        or armed.submission_started_at
        is None
        or armed.submission_attempt_count
        != 1
        or armed.submitted_at is not None
        or _artifact_identity(
            armed
        )
        != initial_identity
    ):
        return finish(
            RECONCILIATION_REQUIRED,
            "ARMED_RESERVATION_VERIFICATION_FAILED",
        )

    transaction_bytes = (
        armed.signed_transaction_bytes
    )

    blockhash_rpc_slot = (
        armed.blockhash_rpc_slot
    )

    if (
        not isinstance(
            transaction_bytes,
            bytes,
        )
        or not transaction_bytes
        or not isinstance(
            blockhash_rpc_slot,
            int,
        )
        or isinstance(
            blockhash_rpc_slot,
            bool,
        )
        or blockhash_rpc_slot < 0
        or not isinstance(
            transaction_signature,
            str,
        )
        or not transaction_signature
    ):
        return finish(
            RECONCILIATION_REQUIRED,
            "ARMED_TRANSACTION_ARTIFACT_INVALID",
        )

    #
    # The arm boundary has been crossed.
    #
    # From here forward ALL uncertainty requires
    # reconciliation. There is no automatic retry.
    #
    try:
        async with (
            SingleAttemptSolanaRpcClient()
            as rpc
        ):
            relay_invoked = True

            returned_signature = (
                await rpc.send_transaction_once(
                    signed_transaction_bytes=(
                        transaction_bytes
                    ),
                    min_context_slot=(
                        blockhash_rpc_slot
                    ),
                )
            )

    except SingleAttemptRpcWriteError as error:
        return finish(
            RECONCILIATION_REQUIRED,
            "SEND_OUTCOME_REQUIRES_RECONCILIATION",
            error.reason,
        )

    except Exception:
        return finish(
            RECONCILIATION_REQUIRED,
            "SEND_OUTCOME_REQUIRES_RECONCILIATION",
            "UNEXPECTED_SEND_FAILURE",
        )

    if (
        returned_signature
        != transaction_signature
    ):
        return finish(
            RECONCILIATION_REQUIRED,
            "RETURNED_SIGNATURE_MISMATCH",
        )

    #
    # Exact signature returned. Persist the
    # acknowledgment.
    #
    try:
        acknowledged = (
            acknowledge_reservation_submitted(
                reservation_id=reservation_id,
                transaction_signature=(
                    transaction_signature
                ),
                db_path=db_path,
            )
        )
    except Exception:
        return finish(
            RECONCILIATION_REQUIRED,
            "SUBMISSION_ACKNOWLEDGMENT_FAILED",
        )

    if acknowledged.status != "PASS":
        return finish(
            RECONCILIATION_REQUIRED,
            "SUBMISSION_ACKNOWLEDGMENT_FAILED",
            *acknowledged.reasons,
        )

    persisted = acknowledged.reservation

    if persisted is None:
        return finish(
            RECONCILIATION_REQUIRED,
            "SUBMITTED_RESERVATION_MISSING",
        )

    reservation_status = persisted.status
    submission_started_at = (
        persisted.submission_started_at
    )
    submitted_at = persisted.submitted_at

    if (
        persisted.status
        != RESERVATION_SUBMITTED
        or persisted.transaction_signature
        != transaction_signature
        or persisted.submission_started_at
        is None
        or persisted.submission_attempt_count
        < 1
        or persisted.submitted_at
        is None
        or _artifact_identity(
            persisted
        )
        != initial_identity
    ):
        return finish(
            RECONCILIATION_REQUIRED,
            "SUBMITTED_LEDGER_VERIFICATION_FAILED",
        )

    return finish(
        SUBMITTED,
    )
