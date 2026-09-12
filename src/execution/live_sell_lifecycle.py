from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.live_pump_sell_authorization import (
    LivePumpSellAuthorization,
)
from src.execution.live_sell_reconciliation import (
    BLOCK as RECONCILIATION_BLOCK,
    HOLD as RECONCILIATION_HOLD,
    RECONCILED as RECONCILIATION_RECONCILED,
    UNKNOWN as RECONCILIATION_UNKNOWN,
    reconcile_live_sell,
)
from src.execution.live_sell_transaction_status import (
    ABSENT_EXPIRED,
    ABSENT_STILL_VALID,
    KNOWN,
)
from src.execution.pump_sell_v2_account_context import (
    PumpSellV2AccountContext,
)
from src.execution.pump_sell_v2_pre_sign_validation import (
    PumpSellV2PreSignValidation,
)
from src.execution.pump_sell_v2_signing import (
    BLOCK as SIGNING_BLOCK,
    PASS as SIGNING_PASS,
    UNKNOWN as SIGNING_UNKNOWN,
    MessageSigner,
    sign_and_bind_pump_sell_v2,
)
from src.execution.pump_sell_v2_submission import (
    BLOCK as SUBMISSION_BLOCK,
    RECONCILIATION_REQUIRED,
    SUBMITTED as SUBMISSION_SUBMITTED,
    UNKNOWN as SUBMISSION_UNKNOWN,
    submit_pump_sell_v2_once,
)
from src.execution.pump_sell_v2_unsigned_message import (
    PumpSellV2UnsignedMessagePlan,
)
from src.portfolio.live_reservations import (
    DB_PATH,
    get_connection,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    BLOCK as CLAIM_BLOCK,
    CONSUMED,
    PASS as CLAIM_PASS,
    RELEASED,
    UNKNOWN as CLAIM_UNKNOWN,
    LiveSellInventoryClaim,
    _authorization_contract_valid,
    _claim_identity_matches_authorization,
    _load_claim,
    acquire_live_sell_inventory_claim,
)
from src.portfolio.live_sell_execution_records import (
    BLOCK as EXECUTION_BLOCK,
    PASS as EXECUTION_PASS,
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED as EXECUTION_SUBMITTED,
    UNKNOWN as EXECUTION_UNKNOWN,
    LiveSellExecutionRecord,
    _record_contract_valid,
    load_live_sell_execution_record_read_only,
)
from src.portfolio.live_sell_unexecuted_release import (
    BLOCK as RELEASE_BLOCK,
    PASS as RELEASE_PASS,
    RELEASED_UNEXECUTED_SELL_REASON,
    UNKNOWN as RELEASE_UNKNOWN,
    release_unexecuted_live_sell_claim,
)


LIVE_SELL_LIFECYCLE_VERSION = (
    "live-sell-lifecycle-v1"
)

ADVANCED = "ADVANCED"
RECONCILED = "RECONCILED"
ABORTED = "ABORTED"
HOLD = "HOLD"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"

CLAIM = "CLAIM"
SIGN = "SIGN"
SUBMIT = "SUBMIT"
RECONCILE = "RECONCILE"
ABORT = "ABORT"
TERMINAL = "TERMINAL"


@dataclass(frozen=True)
class LiveSellLifecycleResult:
    executor_version: str

    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    stage: str

    claim_status: str | None
    execution_status: str | None

    transaction_signature: str | None

    child_status: str | None


def _table_exists(
    *,
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    row = connection.execute(
        """
        SELECT 1

        FROM sqlite_master

        WHERE type = 'table'
          AND name = ?

        LIMIT 1
        """,
        (
            table_name,
        ),
    ).fetchone()

    return row is not None


def _load_claim_read_only(
    *,
    authorization_sha256: str,
    db_path: Path,
) -> LiveSellInventoryClaim | None:
    """
    Read an exact SELL claim without initializing schema.
    """
    connection = get_connection(
        db_path
    )

    try:
        if not _table_exists(
            connection=connection,
            table_name=(
                "live_sell_inventory_claims"
            ),
        ):
            return None

        return _load_claim(
            connection=connection,
            authorization_sha256=(
                authorization_sha256
            ),
        )

    finally:
        connection.close()


def _execution_result_means_absent(
    result: Any,
) -> bool:
    """
    Fresh pre-sign state may legitimately have either:

    - no execution table yet, or
    - an initialized table with no row for this SELL.

    No other loader failure is treated as absence.
    """
    reasons = tuple(
        getattr(
            result,
            "reasons",
            (),
        )
    )

    record = getattr(
        result,
        "record",
        None,
    )

    if record is not None:
        return False

    return (
        "LIVE_SELL_EXECUTION_TABLE_NOT_FOUND"
        in reasons
        or "SELL_EXECUTION_RECORD_NOT_FOUND"
        in reasons
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
    )


async def advance_authorized_live_sell_once(
    *,
    authorization: LivePumpSellAuthorization,
    context: PumpSellV2AccountContext | None = None,
    message_plan: PumpSellV2UnsignedMessagePlan | None = None,
    network_validation: PumpSellV2PreSignValidation | None = None,
    signer: MessageSigner | None = None,
    abort_unexecuted: bool = False,
    db_path: Path = DB_PATH,
) -> LiveSellLifecycleResult:
    """
    Advance one already-authorized Pump SELL by at most
    one consequential durable authority boundary.

    This controller owns sequencing only.

    It does NOT:
    - construct authorization
    - build account context
    - build an unsigned message
    - perform pre-sign validation
    - sign directly
    - send directly
    - inspect receipts directly
    - mutate positions or PnL directly
    - retry a transaction relay

    Recovery rules:

    no claim
        -> acquire claim only

    ACTIVE + no execution
        -> sign/bind only, or explicitly abort

    SIGNED
        -> reconcile status first
        -> submit once only when exact status remains
           ABSENT_STILL_VALID

    SUBMISSION_ARMED / SUBMITTED
        -> reconcile only

    RELEASED_UNEXECUTED_SELL
        -> terminal ABORTED, never reconcile as landed
    """
    authorization_sha256 = (
        getattr(
            authorization,
            "authorization_sha256",
            "",
        )
    )

    claim_status: str | None = None
    execution_status: str | None = None
    transaction_signature: str | None = None

    def finish(
        status: str,
        stage: str,
        *reasons: str,
        child_status: str | None = None,
    ) -> LiveSellLifecycleResult:
        return LiveSellLifecycleResult(
            executor_version=(
                LIVE_SELL_LIFECYCLE_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
                if isinstance(
                    authorization_sha256,
                    str,
                )
                else ""
            ),
            stage=stage,
            claim_status=claim_status,
            execution_status=(
                execution_status
            ),
            transaction_signature=(
                transaction_signature
            ),
            child_status=child_status,
        )

    if not isinstance(
        abort_unexecuted,
        bool,
    ):
        return finish(
            UNKNOWN,
            TERMINAL,
            "SELL_ABORT_FLAG_INVALID",
        )

    try:
        authorization_valid = (
            _authorization_contract_valid(
                authorization
            )
        )

    except Exception:
        authorization_valid = False

    if not authorization_valid:
        return finish(
            UNKNOWN,
            TERMINAL,
            "SELL_AUTHORIZATION_CONTRACT_INVALID",
        )

    authorization_sha256 = (
        authorization.authorization_sha256
    )

    try:
        normalized_path = Path(
            db_path
        )

        if not normalized_path.exists():
            return finish(
                UNKNOWN,
                TERMINAL,
                "LIVE_DATABASE_NOT_FOUND",
            )

    except Exception:
        return finish(
            UNKNOWN,
            TERMINAL,
            "LIVE_DATABASE_PATH_INVALID",
        )

    #
    # ----------------------------------------------------
    # 1. Read claim state.
    # ----------------------------------------------------
    #
    try:
        claim = _load_claim_read_only(
            authorization_sha256=(
                authorization_sha256
            ),
            db_path=normalized_path,
        )

    except Exception:
        return finish(
            UNKNOWN,
            CLAIM,
            "SELL_CLAIM_READ_FAILED",
        )

    #
    # No claim yet: one invocation may acquire only.
    #
    if claim is None:
        if abort_unexecuted:
            return finish(
                ABORTED,
                ABORT,
                "SELL_NOT_CLAIMED",
            )

        try:
            claim_result = (
                acquire_live_sell_inventory_claim(
                    authorization=authorization,
                    db_path=normalized_path,
                )
            )

        except Exception:
            return finish(
                UNKNOWN,
                CLAIM,
                "SELL_CLAIM_ACQUISITION_FAILED",
            )

        child_status = getattr(
            claim_result,
            "status",
            None,
        )

        acquired_claim = getattr(
            claim_result,
            "claim",
            None,
        )

        if child_status == CLAIM_UNKNOWN:
            return finish(
                UNKNOWN,
                CLAIM,
                "SELL_CLAIM_ACQUISITION_UNKNOWN",
                *tuple(
                    getattr(
                        claim_result,
                        "reasons",
                        (),
                    )
                ),
                child_status=child_status,
            )

        if child_status == CLAIM_BLOCK:
            return finish(
                BLOCK,
                CLAIM,
                "SELL_CLAIM_ACQUISITION_BLOCKED",
                *tuple(
                    getattr(
                        claim_result,
                        "reasons",
                        (),
                    )
                ),
                child_status=child_status,
            )

        if (
            child_status != CLAIM_PASS
            or acquired_claim is None
        ):
            return finish(
                UNKNOWN,
                CLAIM,
                "SELL_CLAIM_ACQUISITION_STATUS_INVALID",
                child_status=child_status,
            )

        if not _claim_identity_matches_authorization(
            claim=acquired_claim,
            authorization=authorization,
        ):
            return finish(
                UNKNOWN,
                CLAIM,
                "SELL_ACQUIRED_CLAIM_AUTHORIZATION_MISMATCH",
                child_status=child_status,
            )

        claim_status = (
            acquired_claim.status
        )

        if claim_status == ACTIVE:
            if getattr(
                claim_result,
                "changed",
                False,
            ):
                return finish(
                    ADVANCED,
                    CLAIM,
                    "SELL_CLAIM_ACQUIRED",
                    child_status=child_status,
                )

            #
            # Another actor may have won the identical
            # acquisition race. Do not cross straight
            # into signing in the same invocation.
            #
            return finish(
                HOLD,
                CLAIM,
                "SELL_ACTIVE_CLAIM_DISCOVERED_RETRY",
                child_status=child_status,
            )

        if (
            claim_status == RELEASED
            and acquired_claim.terminal_reason
            == RELEASED_UNEXECUTED_SELL_REASON
        ):
            return finish(
                ABORTED,
                ABORT,
                "SELL_ALREADY_ABORTED_UNEXECUTED",
                child_status=child_status,
            )

        return finish(
            HOLD,
            TERMINAL,
            "SELL_TERMINAL_CLAIM_DISCOVERED_RETRY",
            child_status=child_status,
        )

    if not _claim_identity_matches_authorization(
        claim=claim,
        authorization=authorization,
    ):
        return finish(
            UNKNOWN,
            CLAIM,
            "SELL_CLAIM_AUTHORIZATION_MISMATCH",
        )

    claim_status = claim.status

    #
    # ----------------------------------------------------
    # 2. Special terminal state: intentionally aborted
    #    before any durable execution artifact.
    # ----------------------------------------------------
    #
    if (
        claim.status == RELEASED
        and claim.terminal_reason
        == RELEASED_UNEXECUTED_SELL_REASON
    ):
        try:
            execution_result = (
                load_live_sell_execution_record_read_only(
                    authorization_sha256=(
                        authorization_sha256
                    ),
                    db_path=normalized_path,
                )
            )

        except Exception:
            return finish(
                UNKNOWN,
                ABORT,
                "SELL_ABORTED_EXECUTION_CHECK_FAILED",
            )

        if _execution_result_means_absent(
            execution_result
        ):
            return finish(
                ABORTED,
                ABORT,
                "SELL_ABORTED_UNEXECUTED",
                child_status=(
                    getattr(
                        execution_result,
                        "status",
                        None,
                    )
                ),
            )

        existing_execution = getattr(
            execution_result,
            "record",
            None,
        )

        if existing_execution is not None:
            execution_status = (
                existing_execution.status
            )
            transaction_signature = (
                existing_execution
                .transaction_signature
            )

            return finish(
                UNKNOWN,
                ABORT,
                "ABORTED_SELL_HAS_DURABLE_EXECUTION",
                child_status=(
                    getattr(
                        execution_result,
                        "status",
                        None,
                    )
                ),
            )

        if (
            getattr(
                execution_result,
                "status",
                None,
            )
            == EXECUTION_BLOCK
        ):
            return finish(
                BLOCK,
                ABORT,
                "ABORTED_SELL_EXECUTION_CHECK_BLOCKED",
                *tuple(
                    getattr(
                        execution_result,
                        "reasons",
                        (),
                    )
                ),
                child_status=EXECUTION_BLOCK,
            )

        return finish(
            UNKNOWN,
            ABORT,
            "ABORTED_SELL_EXECUTION_CHECK_UNKNOWN",
            *tuple(
                getattr(
                    execution_result,
                    "reasons",
                    (),
                )
            ),
            child_status=(
                getattr(
                    execution_result,
                    "status",
                    None,
                )
            ),
        )

    #
    # ----------------------------------------------------
    # 3. Normal terminal claim states belong to the
    #    reconciliation router.
    # ----------------------------------------------------
    #
    if claim.status in (
        RELEASED,
        CONSUMED,
    ):
        try:
            reconciliation = (
                await reconcile_live_sell(
                    authorization=authorization,
                    db_path=normalized_path,
                )
            )

        except Exception:
            return finish(
                UNKNOWN,
                RECONCILE,
                "SELL_RECONCILIATION_FAILED",
            )

        child_status = getattr(
            reconciliation,
            "status",
            None,
        )

        transaction_signature = getattr(
            reconciliation,
            "transaction_signature",
            None,
        )

        if child_status == RECONCILIATION_RECONCILED:
            return finish(
                RECONCILED,
                TERMINAL,
                *tuple(
                    getattr(
                        reconciliation,
                        "reasons",
                        (),
                    )
                ),
                child_status=child_status,
            )

        if child_status == RECONCILIATION_HOLD:
            return finish(
                HOLD,
                RECONCILE,
                *tuple(
                    getattr(
                        reconciliation,
                        "reasons",
                        (),
                    )
                ),
                child_status=child_status,
            )

        if child_status == RECONCILIATION_BLOCK:
            return finish(
                BLOCK,
                RECONCILE,
                *tuple(
                    getattr(
                        reconciliation,
                        "reasons",
                        (),
                    )
                ),
                child_status=child_status,
            )

        return finish(
            UNKNOWN,
            RECONCILE,
            *tuple(
                getattr(
                    reconciliation,
                    "reasons",
                    (),
                )
            ),
            child_status=child_status,
        )

    if claim.status != ACTIVE:
        return finish(
            UNKNOWN,
            CLAIM,
            "SELL_CLAIM_STATUS_INVALID",
        )

    #
    # ----------------------------------------------------
    # 4. ACTIVE claim: inspect durable execution state.
    # ----------------------------------------------------
    #
    try:
        execution_result = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=normalized_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            SIGN,
            "SELL_EXECUTION_READ_FAILED",
        )

    #
    # ACTIVE + no execution: sign once, or explicitly
    # abort. No submission may occur in this invocation.
    #
    if _execution_result_means_absent(
        execution_result
    ):
        if abort_unexecuted:
            try:
                release = (
                    release_unexecuted_live_sell_claim(
                        authorization_sha256=(
                            authorization_sha256
                        ),
                        db_path=normalized_path,
                    )
                )

            except Exception:
                return finish(
                    UNKNOWN,
                    ABORT,
                    "SELL_UNEXECUTED_RELEASE_FAILED",
                )

            child_status = getattr(
                release,
                "status",
                None,
            )

            released_claim = getattr(
                release,
                "claim",
                None,
            )

            if released_claim is not None:
                claim_status = (
                    released_claim.status
                )

            if child_status == RELEASE_PASS:
                return finish(
                    ABORTED,
                    ABORT,
                    "SELL_UNEXECUTED_RELEASED",
                    *tuple(
                        getattr(
                            release,
                            "reasons",
                            (),
                        )
                    ),
                    child_status=child_status,
                )

            if (
                child_status == RELEASE_BLOCK
                and "SELL_EXECUTION_RECORD_EXISTS"
                in tuple(
                    getattr(
                        release,
                        "reasons",
                        (),
                    )
                )
            ):
                return finish(
                    HOLD,
                    ABORT,
                    "SELL_ABORT_LOST_TO_EXECUTION_RETRY",
                    child_status=child_status,
                )

            if child_status == RELEASE_BLOCK:
                return finish(
                    BLOCK,
                    ABORT,
                    *tuple(
                        getattr(
                            release,
                            "reasons",
                            (),
                        )
                    ),
                    child_status=child_status,
                )

            return finish(
                UNKNOWN,
                ABORT,
                *tuple(
                    getattr(
                        release,
                        "reasons",
                        (),
                    )
                ),
                child_status=child_status,
            )

        if any(
            value is None
            for value in (
                context,
                message_plan,
                network_validation,
                signer,
            )
        ):
            return finish(
                HOLD,
                SIGN,
                "SELL_SIGNING_INPUTS_REQUIRED",
            )

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
                SIGN,
                "SELL_SIGNING_FAILED",
            )

        child_status = getattr(
            signing,
            "status",
            None,
        )

        transaction_signature = getattr(
            signing,
            "transaction_signature",
            None,
        )

        if child_status == SIGNING_PASS:
            if not getattr(
                signing,
                "is_durably_signed",
                False,
            ):
                return finish(
                    UNKNOWN,
                    SIGN,
                    "SELL_SIGNING_PASS_NOT_DURABLE",
                    child_status=child_status,
                )

            execution_status = SIGNED

            return finish(
                ADVANCED,
                SIGN,
                "SELL_DURABLY_SIGNED",
                child_status=child_status,
            )

        if (
            child_status == SIGNING_BLOCK
            and "SELL_EXECUTION_ALREADY_ADVANCED"
            in tuple(
                getattr(
                    signing,
                    "reasons",
                    (),
                )
            )
        ):
            return finish(
                HOLD,
                SIGN,
                "SELL_EXECUTION_ADVANCED_DURING_SIGN_RETRY",
                child_status=child_status,
            )

        if child_status == SIGNING_BLOCK:
            return finish(
                BLOCK,
                SIGN,
                *tuple(
                    getattr(
                        signing,
                        "reasons",
                        (),
                    )
                ),
                child_status=child_status,
            )

        if child_status == SIGNING_UNKNOWN:
            return finish(
                UNKNOWN,
                SIGN,
                *tuple(
                    getattr(
                        signing,
                        "reasons",
                        (),
                    )
                ),
                child_status=child_status,
            )

        return finish(
            UNKNOWN,
            SIGN,
            "SELL_SIGNING_STATUS_INVALID",
            child_status=child_status,
        )

    #
    # Any non-absence execution-loader failure fails
    # closed.
    #
    execution_loader_status = getattr(
        execution_result,
        "status",
        None,
    )

    execution = getattr(
        execution_result,
        "record",
        None,
    )

    if (
        execution_loader_status
        != EXECUTION_PASS
        or execution is None
    ):
        if execution_loader_status == EXECUTION_BLOCK:
            return finish(
                BLOCK,
                TERMINAL,
                "SELL_EXECUTION_LEDGER_BLOCKED",
                *tuple(
                    getattr(
                        execution_result,
                        "reasons",
                        (),
                    )
                ),
                child_status=execution_loader_status,
            )

        return finish(
            UNKNOWN,
            TERMINAL,
            "SELL_EXECUTION_LEDGER_UNKNOWN",
            *tuple(
                getattr(
                    execution_result,
                    "reasons",
                    (),
                )
            ),
            child_status=execution_loader_status,
        )

    if not _execution_matches_authorization(
        execution=execution,
        authorization=authorization,
    ):
        return finish(
            UNKNOWN,
            TERMINAL,
            "SELL_EXECUTION_AUTHORIZATION_MISMATCH",
            child_status=execution_loader_status,
        )

    execution_status = execution.status
    transaction_signature = (
        execution.transaction_signature
    )

    #
    # ----------------------------------------------------
    # 5. Durable SIGNED state.
    #
    # Reconcile BEFORE any relay attempt. This protects
    # restart cases where the exact artifact was landed
    # externally, or landed before the local process
    # durably recorded submission.
    # ----------------------------------------------------
    #
    if execution.status == SIGNED:
        if abort_unexecuted:
            return finish(
                BLOCK,
                ABORT,
                "SELL_ABORT_TOO_LATE_EXECUTION_EXISTS",
            )

        try:
            reconciliation = (
                await reconcile_live_sell(
                    authorization=authorization,
                    db_path=normalized_path,
                )
            )

        except Exception:
            return finish(
                UNKNOWN,
                RECONCILE,
                "SELL_RECONCILIATION_FAILED",
            )

        reconciliation_status = getattr(
            reconciliation,
            "status",
            None,
        )

        observation_state = getattr(
            reconciliation,
            "status_observation_state",
            None,
        )

        transaction_signature = (
            getattr(
                reconciliation,
                "transaction_signature",
                None,
            )
            or transaction_signature
        )

        #
        # Anything except exact ABSENT_STILL_VALID ends
        # this invocation without relay authority.
        #
        if (
            reconciliation_status
            == RECONCILIATION_RECONCILED
        ):
            return finish(
                RECONCILED,
                TERMINAL,
                *tuple(
                    getattr(
                        reconciliation,
                        "reasons",
                        (),
                    )
                ),
                child_status=(
                    reconciliation_status
                ),
            )

        if (
            reconciliation_status
            == RECONCILIATION_BLOCK
        ):
            return finish(
                BLOCK,
                RECONCILE,
                *tuple(
                    getattr(
                        reconciliation,
                        "reasons",
                        (),
                    )
                ),
                child_status=(
                    reconciliation_status
                ),
            )

        if (
            reconciliation_status
            == RECONCILIATION_UNKNOWN
        ):
            return finish(
                UNKNOWN,
                RECONCILE,
                *tuple(
                    getattr(
                        reconciliation,
                        "reasons",
                        (),
                    )
                ),
                child_status=(
                    reconciliation_status
                ),
            )

        if (
            reconciliation_status
            != RECONCILIATION_HOLD
            or observation_state
            != ABSENT_STILL_VALID
        ):
            return finish(
                HOLD,
                RECONCILE,
                *tuple(
                    getattr(
                        reconciliation,
                        "reasons",
                        (),
                    )
                ),
                child_status=(
                    reconciliation_status
                ),
            )

        #
        # Exact chain proof says the durable artifact is
        # absent and still valid. The submission executor
        # independently rechecks all authority before its
        # one permitted relay attempt.
        #
        try:
            submission = (
                await submit_pump_sell_v2_once(
                    authorization=authorization,
                    db_path=normalized_path,
                )
            )

        except Exception:
            return finish(
                UNKNOWN,
                SUBMIT,
                "SELL_SUBMISSION_FAILED",
            )

        submission_status = getattr(
            submission,
            "status",
            None,
        )

        execution_status = getattr(
            submission,
            "execution_status",
            None,
        )

        transaction_signature = (
            getattr(
                submission,
                "transaction_signature",
                None,
            )
            or transaction_signature
        )

        if submission_status == SUBMISSION_SUBMITTED:
            return finish(
                ADVANCED,
                SUBMIT,
                "SELL_SUBMITTED_ONCE",
                *tuple(
                    getattr(
                        submission,
                        "reasons",
                        (),
                    )
                ),
                child_status=submission_status,
            )

        if (
            submission_status
            == RECONCILIATION_REQUIRED
        ):
            return finish(
                HOLD,
                RECONCILE,
                "SELL_SUBMISSION_RECONCILIATION_REQUIRED",
                *tuple(
                    getattr(
                        submission,
                        "reasons",
                        (),
                    )
                ),
                child_status=submission_status,
            )

        if submission_status == SUBMISSION_BLOCK:
            if getattr(
                submission,
                "relay_invoked",
                False,
            ):
                return finish(
                    UNKNOWN,
                    RECONCILE,
                    "SELL_SUBMISSION_BLOCK_AFTER_RELAY",
                    *tuple(
                        getattr(
                            submission,
                            "reasons",
                            (),
                        )
                    ),
                    child_status=submission_status,
                )

            submission_observation = getattr(
                submission,
                "status_observation_state",
                None,
            )

            if (
                submission_observation
                in (
                    KNOWN,
                    ABSENT_EXPIRED,
                )
                or execution_status
                in (
                    SUBMISSION_ARMED,
                    EXECUTION_SUBMITTED,
                )
            ):
                return finish(
                    HOLD,
                    RECONCILE,
                    "SELL_SUBMISSION_RECONCILIATION_REQUIRED",
                    *tuple(
                        getattr(
                            submission,
                            "reasons",
                            (),
                        )
                    ),
                    child_status=submission_status,
                )

            return finish(
                BLOCK,
                SUBMIT,
                *tuple(
                    getattr(
                        submission,
                        "reasons",
                        (),
                    )
                ),
                child_status=submission_status,
            )

        if submission_status == SUBMISSION_UNKNOWN:
            return finish(
                UNKNOWN,
                SUBMIT,
                *tuple(
                    getattr(
                        submission,
                        "reasons",
                        (),
                    )
                ),
                child_status=submission_status,
            )

        return finish(
            UNKNOWN,
            SUBMIT,
            "SELL_SUBMISSION_STATUS_INVALID",
            child_status=submission_status,
        )

    #
    # ----------------------------------------------------
    # 6. Once armed or submitted, relay authority is gone.
    #    Reconciliation is the only legal next action.
    # ----------------------------------------------------
    #
    if execution.status in (
        SUBMISSION_ARMED,
        EXECUTION_SUBMITTED,
    ):
        if abort_unexecuted:
            return finish(
                BLOCK,
                ABORT,
                "SELL_ABORT_TOO_LATE_EXECUTION_EXISTS",
            )

        try:
            reconciliation = (
                await reconcile_live_sell(
                    authorization=authorization,
                    db_path=normalized_path,
                )
            )

        except Exception:
            return finish(
                UNKNOWN,
                RECONCILE,
                "SELL_RECONCILIATION_FAILED",
            )

        child_status = getattr(
            reconciliation,
            "status",
            None,
        )

        transaction_signature = (
            getattr(
                reconciliation,
                "transaction_signature",
                None,
            )
            or transaction_signature
        )

        if child_status == RECONCILIATION_RECONCILED:
            return finish(
                RECONCILED,
                TERMINAL,
                *tuple(
                    getattr(
                        reconciliation,
                        "reasons",
                        (),
                    )
                ),
                child_status=child_status,
            )

        if child_status == RECONCILIATION_HOLD:
            return finish(
                HOLD,
                RECONCILE,
                *tuple(
                    getattr(
                        reconciliation,
                        "reasons",
                        (),
                    )
                ),
                child_status=child_status,
            )

        if child_status == RECONCILIATION_BLOCK:
            return finish(
                BLOCK,
                RECONCILE,
                *tuple(
                    getattr(
                        reconciliation,
                        "reasons",
                        (),
                    )
                ),
                child_status=child_status,
            )

        if child_status == RECONCILIATION_UNKNOWN:
            return finish(
                UNKNOWN,
                RECONCILE,
                *tuple(
                    getattr(
                        reconciliation,
                        "reasons",
                        (),
                    )
                ),
                child_status=child_status,
            )

        return finish(
            UNKNOWN,
            RECONCILE,
            "SELL_RECONCILIATION_STATUS_INVALID",
            child_status=child_status,
        )

    return finish(
        UNKNOWN,
        TERMINAL,
        "SELL_EXECUTION_STATUS_INVALID",
    )
