from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.live_pump_sell_authorization import (
    LIVE_PUMP_SELL_AUTHORIZATION_VERSION,
    LivePumpSellAuthorization,
)
from src.execution.live_sell_transaction_status import (
    ABSENT_EXPIRED,
    ABSENT_STILL_VALID,
    KNOWN,
    UNKNOWN as STATUS_UNKNOWN,
    resolve_live_sell_transaction_status,
)
from src.execution.solana_single_attempt_rpc import (
    SingleAttemptRpcWriteError,
    SingleAttemptSolanaRpcClient,
)
from src.portfolio.live_reservations import (
    DB_PATH,
)
from src.portfolio.live_sell_execution_records import (
    BLOCK as EXECUTION_BLOCK,
    PASS as EXECUTION_PASS,
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED as EXECUTION_SUBMITTED,
    UNKNOWN as EXECUTION_UNKNOWN,
    LiveSellExecutionRecord,
    acknowledge_live_sell_submitted,
    arm_live_sell_submission,
    load_live_sell_execution_record_read_only,
)


PUMP_SELL_V2_SUBMISSION_VERSION = (
    "pump-sell-v2-submission-v1"
)

SUBMITTED = "SUBMITTED"
RECONCILIATION_REQUIRED = (
    "RECONCILIATION_REQUIRED"
)
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PumpSellV2SubmissionResult:
    executor_version: str

    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    transaction_signature: str | None
    status_observation_state: str | None

    relay_invoked: bool
    returned_signature: str | None

    execution_status: str | None

    submission_started_at: float | None
    submitted_at: float | None


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


def _artifact_identity(
    record: LiveSellExecutionRecord,
) -> tuple:
    """
    Immutable identity of one exact signed SELL
    transaction.

    Submission lifecycle metadata is intentionally
    excluded because SIGNED -> SUBMISSION_ARMED ->
    SUBMITTED changes those fields without changing
    the transaction itself.
    """

    return (
        record.record_version,

        record.authorization_version,
        record.authorization_sha256,

        record.wallet_pubkey,
        record.mint,
        record.tokens_to_sell,

        record.message_sha256,

        record.transaction_signature,
        record.signed_transaction_sha256,
        record.signed_transaction_bytes,

        record.blockhash_context_version,
        record.recent_blockhash,
        record.last_valid_block_height,
        record.blockhash_rpc_slot,

        record.signed_at,
    )


def _pristine_signed(
    record: LiveSellExecutionRecord,
) -> bool:
    return (
        record.status == SIGNED
        and record.submission_started_at
        is None
        and record.submission_attempt_count
        == 0
        and record.submitted_at is None
    )


def _armed_once(
    record: LiveSellExecutionRecord,
) -> bool:
    return (
        record.status
        == SUBMISSION_ARMED
        and record.submission_started_at
        is not None
        and record.submission_attempt_count
        == 1
        and record.submitted_at is None
    )


def _submitted_once(
    record: LiveSellExecutionRecord,
) -> bool:
    return (
        record.status
        == EXECUTION_SUBMITTED
        and record.submission_started_at
        is not None
        and record.submission_attempt_count
        == 1
        and record.submitted_at
        is not None
        and record.submitted_at
        >= record.submission_started_at
    )


async def submit_pump_sell_v2_once(
    *,
    authorization: LivePumpSellAuthorization,
    db_path: Path = DB_PATH,
) -> PumpSellV2SubmissionResult:
    """
    Relay one exact, durably persisted Pump SELL_V2
    signed transaction at most once from this local
    submission authority.

    This executor NEVER:
    - signs
    - rebuilds a transaction
    - retries a relay
    - rearms an existing armed transaction
    - mutates claims
    - mutates positions
    - reconciles chain outcome
    - fetches transaction receipts

    A network write is permitted only after this
    invocation freshly transitions the exact durable
    execution record:

        SIGNED -> SUBMISSION_ARMED

    Any uncertainty after that durable boundary is
    RECONCILIATION_REQUIRED.
    """

    authorization_sha256 = ""

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

    execution_status: (
        str | None
    ) = None

    submission_started_at: (
        float | None
    ) = None

    submitted_at: (
        float | None
    ) = None

    def finish(
        status: str,
        *reasons: str,
    ) -> PumpSellV2SubmissionResult:
        return PumpSellV2SubmissionResult(
            executor_version=(
                PUMP_SELL_V2_SUBMISSION_VERSION
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
            status_observation_state=(
                status_observation_state
            ),
            relay_invoked=(
                relay_invoked
            ),
            returned_signature=(
                returned_signature
            ),
            execution_status=(
                execution_status
            ),
            submission_started_at=(
                submission_started_at
            ),
            submitted_at=(
                submitted_at
            ),
        )

    #
    # Basic authorization identity validation must
    # happen before any chain observation.
    #
    if not isinstance(
        authorization,
        LivePumpSellAuthorization,
    ):
        return finish(
            BLOCK,
            "INVALID_SELL_AUTHORIZATION",
        )

    if (
        authorization.authorization_version
        != LIVE_PUMP_SELL_AUTHORIZATION_VERSION
    ):
        return finish(
            BLOCK,
            "SELL_AUTHORIZATION_VERSION_MISMATCH",
        )

    authorization_sha256 = (
        authorization.authorization_sha256
        if isinstance(
            authorization.authorization_sha256,
            str,
        )
        else ""
    )

    if not _valid_sha256(
        authorization_sha256
    ):
        return finish(
            BLOCK,
            "INVALID_SELL_AUTHORIZATION_SHA256",
        )

    #
    # First durable execution read.
    #
    try:
        initial_result = (
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
            "SELL_EXECUTION_RECORD_READ_FAILED",
        )

    if (
        initial_result.status
        != EXECUTION_PASS
        or initial_result.record
        is None
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_RECORD_UNAVAILABLE",
            *initial_result.reasons,
        )

    initial = initial_result.record

    transaction_signature = (
        initial.transaction_signature
    )

    execution_status = (
        initial.status
    )

    submission_started_at = (
        initial.submission_started_at
    )

    submitted_at = (
        initial.submitted_at
    )

    #
    # A previous invocation may already have crossed
    # the irreversible local send boundary.
    #
    # Never status-check-and-resend from here.
    #
    if initial.status == SUBMISSION_ARMED:
        return finish(
            RECONCILIATION_REQUIRED,
            "SELL_SUBMISSION_ALREADY_ARMED",
        )

    if initial.status == EXECUTION_SUBMITTED:
        return finish(
            RECONCILIATION_REQUIRED,
            "SELL_ALREADY_SUBMITTED",
        )

    if not _pristine_signed(
        initial
    ):
        return finish(
            BLOCK,
            "SELL_EXECUTION_NOT_PRISTINE_SIGNED",
        )

    initial_identity = (
        _artifact_identity(
            initial
        )
    )

    #
    # Pre-arm chain observation.
    #
    # Only exact ABSENT_STILL_VALID may continue.
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

    status_observation_state = (
        observation.state
    )

    #
    # Bind the status observation back to the exact
    # signed artifact we loaded above.
    #
    if (
        observation.authorization_sha256
        != authorization_sha256
        or observation.transaction_signature
        != transaction_signature
        or observation.execution_status
        != SIGNED
        or observation.last_valid_block_height
        != initial.last_valid_block_height
        or observation.blockhash_rpc_slot
        != initial.blockhash_rpc_slot
    ):
        return finish(
            UNKNOWN,
            "SELL_STATUS_OBSERVATION_BINDING_MISMATCH",
        )

    if observation.state == STATUS_UNKNOWN:
        return finish(
            UNKNOWN,
            "SELL_STATUS_UNKNOWN",
            *observation.reasons,
        )

    if observation.state == KNOWN:
        return finish(
            RECONCILIATION_REQUIRED,
            "SELL_TRANSACTION_ALREADY_KNOWN",
        )

    if observation.state == ABSENT_EXPIRED:
        return finish(
            RECONCILIATION_REQUIRED,
            "SELL_TRANSACTION_ABSENT_EXPIRED",
        )

    if (
        observation.state
        != ABSENT_STILL_VALID
    ):
        return finish(
            UNKNOWN,
            "SELL_STATUS_STATE_INVALID",
        )

    #
    # Re-read after network observation.
    #
    # Another worker may have armed or submitted the
    # transaction while the status RPC was in flight.
    #
    try:
        reread_result = (
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
        reread_result.status
        != EXECUTION_PASS
        or reread_result.record
        is None
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_REREAD_UNAVAILABLE",
            *reread_result.reasons,
        )

    reread = reread_result.record

    execution_status = (
        reread.status
    )

    submission_started_at = (
        reread.submission_started_at
    )

    submitted_at = (
        reread.submitted_at
    )

    if reread.status in (
        SUBMISSION_ARMED,
        EXECUTION_SUBMITTED,
    ):
        return finish(
            RECONCILIATION_REQUIRED,
            "SELL_SUBMISSION_STATE_ADVANCED_DURING_STATUS_CHECK",
        )

    if (
        _artifact_identity(
            reread
        )
        != initial_identity
    ):
        return finish(
            UNKNOWN,
            "SELL_ARTIFACT_CHANGED_DURING_STATUS_CHECK",
        )

    if not _pristine_signed(
        reread
    ):
        return finish(
            BLOCK,
            "SELL_EXECUTION_NOT_PRISTINE_AFTER_STATUS",
        )

    #
    # Acquire the irreversible local send boundary.
    #
    try:
        arm = arm_live_sell_submission(
            authorization=authorization,
            transaction_signature=(
                reread.transaction_signature
            ),
            signed_transaction_sha256=(
                reread.signed_transaction_sha256
            ),
            db_path=db_path,
        )

    except Exception:
        #
        # An exception does not prove that the durable
        # arm failed to commit. Re-read before deciding
        # whether this is ordinary UNKNOWN or requires
        # reconciliation.
        #
        try:
            arm_check = (
                load_live_sell_execution_record_read_only(
                    authorization_sha256=(
                        authorization_sha256
                    ),
                    db_path=db_path,
                )
            )

        except Exception:
            return finish(
                RECONCILIATION_REQUIRED,
                "SELL_SUBMISSION_ARM_OUTCOME_UNKNOWN",
            )

        if (
            arm_check.status
            == EXECUTION_PASS
            and arm_check.record
            is not None
            and arm_check.record.status
            in (
                SUBMISSION_ARMED,
                EXECUTION_SUBMITTED,
            )
        ):
            execution_status = (
                arm_check.record.status
            )

            submission_started_at = (
                arm_check.record
                .submission_started_at
            )

            submitted_at = (
                arm_check.record.submitted_at
            )

            return finish(
                RECONCILIATION_REQUIRED,
                "SELL_SUBMISSION_ARM_OUTCOME_UNKNOWN",
            )

        return finish(
            UNKNOWN,
            "SELL_SUBMISSION_ARM_FAILED",
        )

    armed = arm.record

    if (
        arm.status != EXECUTION_PASS
        or armed is None
        or not arm.changed
    ):
        #
        # If another worker won the arm race, the
        # transaction may already be in flight.
        #
        if (
            armed is not None
            and armed.status
            in (
                SUBMISSION_ARMED,
                EXECUTION_SUBMITTED,
            )
        ):
            execution_status = (
                armed.status
            )

            submission_started_at = (
                armed.submission_started_at
            )

            submitted_at = (
                armed.submitted_at
            )

            return finish(
                RECONCILIATION_REQUIRED,
                "SELL_SUBMISSION_ARM_NOT_ACQUIRED",
                *arm.reasons,
            )

        if arm.status == EXECUTION_BLOCK:
            return finish(
                BLOCK,
                "SELL_SUBMISSION_ARM_BLOCKED",
                *arm.reasons,
            )

        if arm.status == EXECUTION_UNKNOWN:
            return finish(
                UNKNOWN,
                "SELL_SUBMISSION_ARM_UNKNOWN",
                *arm.reasons,
            )

        return finish(
            UNKNOWN,
            "SELL_SUBMISSION_ARM_RESULT_INVALID",
            *arm.reasons,
        )

    #
    # From this point forward, the durable arm was
    # acquired by THIS invocation.
    #
    # Every uncertainty is reconciliation-required.
    #
    execution_status = (
        armed.status
    )

    transaction_signature = (
        armed.transaction_signature
    )

    submission_started_at = (
        armed.submission_started_at
    )

    submitted_at = (
        armed.submitted_at
    )

    if (
        not _armed_once(
            armed
        )
        or _artifact_identity(
            armed
        )
        != initial_identity
    ):
        return finish(
            RECONCILIATION_REQUIRED,
            "ARMED_SELL_EXECUTION_VERIFICATION_FAILED",
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
    ):
        return finish(
            RECONCILIATION_REQUIRED,
            "ARMED_SELL_RELAY_INPUT_INVALID",
        )

    #
    # Exactly one relay invocation.
    #
    try:
        async with (
            SingleAttemptSolanaRpcClient()
        ) as writer:
            relay_invoked = True

            returned_signature = (
                await writer.send_transaction_once(
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
            "SELL_RPC_WRITE_OUTCOME_UNCERTAIN",
            error.reason,
        )

    except Exception:
        return finish(
            RECONCILIATION_REQUIRED,
            "SELL_UNEXPECTED_SEND_FAILURE",
        )

    if (
        returned_signature
        != transaction_signature
    ):
        return finish(
            RECONCILIATION_REQUIRED,
            "SELL_RETURNED_SIGNATURE_MISMATCH",
        )

    #
    # Exact signature returned. Persist only the
    # acknowledgment that the RPC accepted the exact
    # signed transaction.
    #
    try:
        acknowledged = (
            acknowledge_live_sell_submitted(
                authorization=authorization,
                transaction_signature=(
                    transaction_signature
                ),
                signed_transaction_sha256=(
                    armed.signed_transaction_sha256
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            RECONCILIATION_REQUIRED,
            "SELL_SUBMISSION_ACKNOWLEDGMENT_FAILED",
        )

    if (
        acknowledged.status
        != EXECUTION_PASS
        or acknowledged.record
        is None
    ):
        return finish(
            RECONCILIATION_REQUIRED,
            "SELL_SUBMISSION_ACKNOWLEDGMENT_FAILED",
            *acknowledged.reasons,
        )

    persisted = (
        acknowledged.record
    )

    execution_status = (
        persisted.status
    )

    submission_started_at = (
        persisted.submission_started_at
    )

    submitted_at = (
        persisted.submitted_at
    )

    if (
        not _submitted_once(
            persisted
        )
        or _artifact_identity(
            persisted
        )
        != initial_identity
        or persisted.transaction_signature
        != returned_signature
    ):
        return finish(
            RECONCILIATION_REQUIRED,
            "SUBMITTED_SELL_EXECUTION_VERIFICATION_FAILED",
        )

    return finish(
        SUBMITTED,
    )
