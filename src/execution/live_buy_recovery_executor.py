from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.execution.failed_transaction_reconciliation import (
    BLOCK as FAILED_BLOCK,
    HOLD as FAILED_HOLD,
    RECONCILED as FAILED_RECONCILED,
    UNKNOWN as FAILED_UNKNOWN,
    reconcile_failed_transaction,
)
from src.execution.live_buy_recovery_discovery import (
    ARMED_SIGNED,
    PASS as DISCOVERY_PASS,
    PRISTINE_SIGNED,
    SUBMITTED as RECOVERY_SUBMITTED,
    UNKNOWN as DISCOVERY_UNKNOWN,
    discover_live_buy_recovery_candidates,
)
from src.execution.signed_transaction_reconciliation import (
    BLOCK as ABSENT_BLOCK,
    HOLD as ABSENT_HOLD,
    RECONCILED as ABSENT_RECONCILED,
    UNKNOWN as ABSENT_UNKNOWN,
    reconcile_signed_transaction,
)
from src.execution.signed_transaction_status import (
    ABSENT_EXPIRED,
    ABSENT_STILL_VALID,
    KNOWN,
    UNKNOWN as STATUS_UNKNOWN,
    resolve_signed_transaction_status,
)
from src.execution.signed_transaction_submission import (
    BLOCK as SUBMISSION_BLOCK,
    RECONCILIATION_REQUIRED,
    SUBMITTED as SUBMISSION_SUBMITTED,
    UNKNOWN as SUBMISSION_UNKNOWN,
    submit_signed_transaction_once,
)
from src.execution.successful_buy_reconciliation import (
    BLOCK as SUCCESS_BLOCK,
    HOLD as SUCCESS_HOLD,
    RECONCILED as SUCCESS_RECONCILED,
    UNKNOWN as SUCCESS_UNKNOWN,
    reconcile_successful_buy,
)
from src.portfolio.live_reservations import (
    DB_PATH,
)


LIVE_BUY_RECOVERY_EXECUTOR_VERSION = (
    "live-buy-recovery-executor-v1"
)

IDLE = "IDLE"
ADVANCED = "ADVANCED"
RECONCILED = "RECONCILED"
HOLD = "HOLD"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"

DISCOVERY = "DISCOVERY"
STATUS = "STATUS"
RECONCILE_SUCCESS = "RECONCILE_SUCCESS"
RECONCILE_FAILURE = "RECONCILE_FAILURE"
RECONCILE_ABSENT = "RECONCILE_ABSENT"
RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
SUBMIT = "SUBMIT"
COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class LiveBuyRecoveryExecutionResult:
    executor_version: str

    status: str
    stage: str
    reasons: tuple[str, ...]

    reservation_id: str | None
    recovery_state: str | None
    transaction_signature: str | None

    status_observation_state: str | None

    child_version: str | None
    child_status: str | None

    relay_invoked: bool


async def recover_one_live_buy_once(
    *,
    allow_submission: bool = True,
    db_path: Path = DB_PATH,
) -> LiveBuyRecoveryExecutionResult:
    """
    Recover at most one durable live BUY obligation.

    Authority is deliberately narrow:

      1. discover unresolved signed BUY authority;
      2. resolve exact chain status;
      3. route KNOWN success/failure to the existing
         dedicated reconciliation executors;
      4. route ABSENT_EXPIRED to the existing exact
         absent/expired reconciliation executor;
      5. relay exactly once only when:
           - the reservation is PRISTINE_SIGNED;
           - chain state is ABSENT_STILL_VALID;
           - allow_submission is True.

    ARMED_SIGNED and SUBMITTED reservations never regain
    relay authority.

    This executor has no:
      - transaction construction;
      - signing;
      - re-signing;
      - retry loop;
      - strategy authority;
      - direct database-write authority.
    """

    reservation_id: str | None = None
    recovery_state: str | None = None
    transaction_signature: str | None = None
    observation_state: str | None = None

    def finish(
        status: str,
        stage: str,
        *reasons: str,
        child_version: str | None = None,
        child_status: str | None = None,
        relay_invoked: bool = False,
    ) -> LiveBuyRecoveryExecutionResult:
        return LiveBuyRecoveryExecutionResult(
            executor_version=(
                LIVE_BUY_RECOVERY_EXECUTOR_VERSION
            ),
            status=status,
            stage=stage,
            reasons=tuple(reasons),
            reservation_id=reservation_id,
            recovery_state=recovery_state,
            transaction_signature=(
                transaction_signature
            ),
            status_observation_state=(
                observation_state
            ),
            child_version=child_version,
            child_status=child_status,
            relay_invoked=relay_invoked,
        )

    if not isinstance(
        allow_submission,
        bool,
    ):
        return finish(
            UNKNOWN,
            DISCOVERY,
            "INVALID_ALLOW_SUBMISSION",
        )

    try:
        normalized_path = Path(
            db_path
        )
    except Exception:
        return finish(
            UNKNOWN,
            DISCOVERY,
            "LIVE_DATABASE_PATH_INVALID",
        )

    try:
        discovery = (
            discover_live_buy_recovery_candidates(
                db_path=normalized_path
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            DISCOVERY,
            "LIVE_BUY_RECOVERY_DISCOVERY_EXCEPTION",
        )

    discovery_status = getattr(
        discovery,
        "status",
        None,
    )

    if discovery_status == DISCOVERY_UNKNOWN:
        return finish(
            UNKNOWN,
            DISCOVERY,
            *tuple(
                getattr(
                    discovery,
                    "reasons",
                    (),
                )
            ),
        )

    if discovery_status != DISCOVERY_PASS:
        return finish(
            UNKNOWN,
            DISCOVERY,
            "LIVE_BUY_RECOVERY_DISCOVERY_STATUS_INVALID",
        )

    candidates = getattr(
        discovery,
        "candidates",
        (),
    )

    if not candidates:
        return finish(
            IDLE,
            COMPLETE,
        )

    candidate = candidates[0]

    recovery_state = getattr(
        candidate,
        "recovery_state",
        None,
    )

    reservation = getattr(
        candidate,
        "reservation",
        None,
    )

    reservation_id = getattr(
        reservation,
        "reservation_id",
        None,
    )

    transaction_signature = getattr(
        reservation,
        "transaction_signature",
        None,
    )

    if (
        recovery_state
        not in (
            PRISTINE_SIGNED,
            ARMED_SIGNED,
            RECOVERY_SUBMITTED,
        )
        or not isinstance(
            reservation_id,
            str,
        )
        or not reservation_id.strip()
    ):
        return finish(
            UNKNOWN,
            DISCOVERY,
            "LIVE_BUY_RECOVERY_CANDIDATE_INVALID",
        )

    reservation_id = reservation_id.strip()

    try:
        observation = (
            await resolve_signed_transaction_status(
                reservation_id=reservation_id,
                db_path=normalized_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            STATUS,
            "LIVE_BUY_STATUS_RESOLUTION_EXCEPTION",
        )

    observation_state = getattr(
        observation,
        "state",
        None,
    )

    observation_reasons = tuple(
        getattr(
            observation,
            "reasons",
            (),
        )
    )

    if observation_state == STATUS_UNKNOWN:
        return finish(
            UNKNOWN,
            STATUS,
            *observation_reasons,
            child_version=getattr(
                observation,
                "resolver_version",
                None,
            ),
            child_status=observation_state,
        )

    async def route_reconciliation(
        *,
        reconciler,
        stage: str,
        reconciled_status: str,
        hold_status: str,
        block_status: str,
        unknown_status: str,
    ) -> LiveBuyRecoveryExecutionResult:
        try:
            child = await reconciler(
                reservation_id=reservation_id,
                db_path=normalized_path,
            )
        except Exception:
            return finish(
                UNKNOWN,
                stage,
                "LIVE_BUY_RECONCILIATION_EXCEPTION",
            )

        child_status = getattr(
            child,
            "status",
            None,
        )

        child_version = getattr(
            child,
            "executor_version",
            None,
        )

        child_reasons = tuple(
            getattr(
                child,
                "reasons",
                (),
            )
        )

        if child_status == reconciled_status:
            return finish(
                RECONCILED,
                stage,
                *child_reasons,
                child_version=child_version,
                child_status=child_status,
            )

        if child_status == hold_status:
            return finish(
                HOLD,
                stage,
                *child_reasons,
                child_version=child_version,
                child_status=child_status,
            )

        if child_status == block_status:
            return finish(
                BLOCK,
                stage,
                *child_reasons,
                child_version=child_version,
                child_status=child_status,
            )

        if child_status == unknown_status:
            return finish(
                UNKNOWN,
                stage,
                *child_reasons,
                child_version=child_version,
                child_status=child_status,
            )

        return finish(
            UNKNOWN,
            stage,
            "LIVE_BUY_RECONCILIATION_STATUS_INVALID",
            child_version=child_version,
            child_status=child_status,
        )

    if observation_state == KNOWN:
        transaction_error = getattr(
            observation,
            "transaction_error",
            None,
        )

        if transaction_error is None:
            return await route_reconciliation(
                reconciler=(
                    reconcile_successful_buy
                ),
                stage=RECONCILE_SUCCESS,
                reconciled_status=(
                    SUCCESS_RECONCILED
                ),
                hold_status=SUCCESS_HOLD,
                block_status=SUCCESS_BLOCK,
                unknown_status=SUCCESS_UNKNOWN,
            )

        return await route_reconciliation(
            reconciler=(
                reconcile_failed_transaction
            ),
            stage=RECONCILE_FAILURE,
            reconciled_status=(
                FAILED_RECONCILED
            ),
            hold_status=FAILED_HOLD,
            block_status=FAILED_BLOCK,
            unknown_status=FAILED_UNKNOWN,
        )

    if observation_state == ABSENT_EXPIRED:
        return await route_reconciliation(
            reconciler=(
                reconcile_signed_transaction
            ),
            stage=RECONCILE_ABSENT,
            reconciled_status=(
                ABSENT_RECONCILED
            ),
            hold_status=ABSENT_HOLD,
            block_status=ABSENT_BLOCK,
            unknown_status=ABSENT_UNKNOWN,
        )

    if observation_state != ABSENT_STILL_VALID:
        return finish(
            UNKNOWN,
            STATUS,
            "LIVE_BUY_STATUS_STATE_INVALID",
            child_version=getattr(
                observation,
                "resolver_version",
                None,
            ),
            child_status=observation_state,
        )

    #
    # An armed or acknowledged reservation has crossed the
    # durable relay boundary. It can never regain send
    # authority, even if the chain currently reports the
    # signature absent and still valid.
    #
    if recovery_state in (
        ARMED_SIGNED,
        RECOVERY_SUBMITTED,
    ):
        return finish(
            HOLD,
            STATUS,
            "LIVE_BUY_RELAY_AUTHORITY_GONE",
            child_version=getattr(
                observation,
                "resolver_version",
                None,
            ),
            child_status=observation_state,
        )

    if recovery_state != PRISTINE_SIGNED:
        return finish(
            UNKNOWN,
            STATUS,
            "LIVE_BUY_RECOVERY_STATE_INVALID",
        )

    if not allow_submission:
        return finish(
            HOLD,
            STATUS,
            "LIVE_BUY_SUBMISSION_DISABLED",
            child_version=getattr(
                observation,
                "resolver_version",
                None,
            ),
            child_status=observation_state,
        )

    try:
        submission = (
            await submit_signed_transaction_once(
                reservation_id=reservation_id,
                db_path=normalized_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            SUBMIT,
            "LIVE_BUY_SUBMISSION_EXCEPTION",
        )

    submission_status = getattr(
        submission,
        "status",
        None,
    )

    submission_version = getattr(
        submission,
        "executor_version",
        None,
    )

    submission_reasons = tuple(
        getattr(
            submission,
            "reasons",
            (),
        )
    )

    relay_invoked = bool(
        getattr(
            submission,
            "relay_invoked",
            False,
        )
    )

    if submission_status == SUBMISSION_SUBMITTED:
        return finish(
            ADVANCED,
            SUBMIT,
            "LIVE_BUY_SUBMITTED_ONCE",
            *submission_reasons,
            child_version=submission_version,
            child_status=submission_status,
            relay_invoked=relay_invoked,
        )

    if (
        submission_status
        == RECONCILIATION_REQUIRED
    ):
        return finish(
            HOLD,
            RECONCILE_REQUIRED,
            "LIVE_BUY_SUBMISSION_RECONCILIATION_REQUIRED",
            *submission_reasons,
            child_version=submission_version,
            child_status=submission_status,
            relay_invoked=relay_invoked,
        )

    if submission_status == SUBMISSION_BLOCK:
        if relay_invoked:
            return finish(
                UNKNOWN,
                RECONCILE_REQUIRED,
                "LIVE_BUY_SUBMISSION_BLOCK_AFTER_RELAY",
                *submission_reasons,
                child_version=submission_version,
                child_status=submission_status,
                relay_invoked=True,
            )

        return finish(
            BLOCK,
            SUBMIT,
            *submission_reasons,
            child_version=submission_version,
            child_status=submission_status,
            relay_invoked=False,
        )

    if submission_status == SUBMISSION_UNKNOWN:
        return finish(
            UNKNOWN,
            SUBMIT,
            *submission_reasons,
            child_version=submission_version,
            child_status=submission_status,
            relay_invoked=relay_invoked,
        )

    return finish(
        UNKNOWN,
        SUBMIT,
        "LIVE_BUY_SUBMISSION_STATUS_INVALID",
        child_version=submission_version,
        child_status=submission_status,
        relay_invoked=relay_invoked,
    )
