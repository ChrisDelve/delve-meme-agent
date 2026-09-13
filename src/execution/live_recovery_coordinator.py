from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.live_buy_recovery_discovery import (
    ARMED_SIGNED,
    PASS as BUY_DISCOVERY_PASS,
    PRISTINE_SIGNED,
    SUBMITTED as BUY_SUBMITTED,
    UNKNOWN as BUY_DISCOVERY_UNKNOWN,
    LIVE_BUY_RECOVERY_DISCOVERY_VERSION,
    discover_live_buy_recovery_candidates,
)
from src.execution.live_buy_recovery_executor import (
    ADVANCED as BUY_ADVANCED,
    BLOCK as BUY_BLOCK,
    HOLD as BUY_HOLD,
    IDLE as BUY_IDLE,
    RECONCILED as BUY_RECONCILED,
    UNKNOWN as BUY_UNKNOWN,
    LIVE_BUY_RECOVERY_EXECUTOR_VERSION,
    recover_one_live_buy_once,
)
from src.execution.live_sell_recovery_discovery import (
    PASS as SELL_DISCOVERY_PASS,
    UNKNOWN as SELL_DISCOVERY_UNKNOWN,
    LIVE_SELL_RECOVERY_DISCOVERY_VERSION,
    discover_live_sell_recovery_candidates,
)
from src.execution.live_sell_recovery_executor import (
    ADVANCED as SELL_ADVANCED,
    BLOCK as SELL_BLOCK,
    HOLD as SELL_HOLD,
    IDLE as SELL_IDLE,
    RECONCILED as SELL_RECONCILED,
    UNKNOWN as SELL_UNKNOWN,
    LIVE_SELL_RECOVERY_EXECUTOR_VERSION,
    recover_one_live_sell_once,
)
from src.portfolio.live_reservations import (
    DB_PATH,
)
from src.portfolio.live_sell_execution_records import (
    SIGNED as SELL_SIGNED,
    SUBMISSION_ARMED as SELL_SUBMISSION_ARMED,
    SUBMITTED as SELL_SUBMITTED,
)


LIVE_RECOVERY_COORDINATOR_VERSION = (
    "live-recovery-coordinator-v1"
)

BUY = "BUY"
SELL = "SELL"

IDLE = "IDLE"
ADVANCED = "ADVANCED"
RECONCILED = "RECONCILED"
HOLD = "HOLD"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"

DISCOVERY = "DISCOVERY"
EXECUTE = "EXECUTE"
COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class LiveRecoveryCoordinatorResult:
    coordinator_version: str

    status: str
    stage: str
    reasons: tuple[str, ...]

    selected_side: str | None
    selected_state: str | None
    selected_signed_at: float | None
    selected_identity: str | None

    buy_candidates: int
    sell_candidates: int

    child_version: str | None
    child_status: str | None


def _valid_reasons(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            tuple,
        )
        and all(
            isinstance(
                reason,
                str,
            )
            and bool(
                reason,
            )
            for reason in value
        )
    )


def _positive_finite_float(
    value: Any,
) -> float | None:
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            (int, float),
        )
    ):
        return None

    result = float(
        value
    )

    if (
        not math.isfinite(
            result
        )
        or result <= 0.0
    ):
        return None

    return result


def _nonempty_text(
    value: Any,
) -> str | None:
    if not isinstance(
        value,
        str,
    ):
        return None

    normalized = value.strip()

    if not normalized:
        return None

    return normalized


def _valid_discovery_contract(
    result: Any,
    *,
    expected_version: str,
    pass_status: str,
    unknown_status: str,
) -> bool:
    return (
        getattr(
            result,
            "resolver_version",
            None,
        )
        == expected_version
        and getattr(
            result,
            "status",
            None,
        )
        in (
            pass_status,
            unknown_status,
        )
        and _valid_reasons(
            getattr(
                result,
                "reasons",
                None,
            )
        )
        and isinstance(
            getattr(
                result,
                "candidates",
                None,
            ),
            tuple,
        )
    )


def _buy_candidate_metadata(
    candidate: Any,
) -> tuple[
    int,
    str,
    float,
    str,
] | None:
    recovery_state = getattr(
        candidate,
        "recovery_state",
        None,
    )

    priority = {
        ARMED_SIGNED: 0,
        BUY_SUBMITTED: 1,
        PRISTINE_SIGNED: 2,
    }

    if recovery_state not in priority:
        return None

    reservation = getattr(
        candidate,
        "reservation",
        None,
    )

    if reservation is None:
        return None

    signed_at = _positive_finite_float(
        getattr(
            reservation,
            "signed_at",
            None,
        )
    )

    reservation_id = _nonempty_text(
        getattr(
            reservation,
            "reservation_id",
            None,
        )
    )

    if (
        signed_at is None
        or reservation_id is None
    ):
        return None

    return (
        priority[
            recovery_state
        ],
        recovery_state,
        signed_at,
        reservation_id,
    )


def _select_sell_candidate(
    candidates: tuple,
    *,
    allow_submission: bool,
) -> Any | None:
    if not candidates:
        return None

    if allow_submission:
        #
        # Mirror recover_one_live_sell_once():
        # normal mode preserves discovery's oldest-artifact
        # ordering.
        #
        return candidates[0]

    #
    # Mirror SELL recovery's reconciliation-only behavior:
    # prefer an already-relayed obligation so an old pristine
    # SIGNED artifact cannot starve reconciliation.
    #
    return next(
        (
            candidate
            for candidate in candidates
            if getattr(
                candidate,
                "execution_status",
                None,
            )
            in (
                SELL_SUBMISSION_ARMED,
                SELL_SUBMITTED,
            )
        ),
        candidates[0],
    )


def _sell_candidate_metadata(
    candidate: Any,
) -> tuple[
    int,
    str,
    float,
    str,
] | None:
    execution_status = getattr(
        candidate,
        "execution_status",
        None,
    )

    priority = {
        SELL_SUBMISSION_ARMED: 0,
        SELL_SUBMITTED: 1,
        SELL_SIGNED: 2,
    }

    if execution_status not in priority:
        return None

    signed_at = _positive_finite_float(
        getattr(
            candidate,
            "signed_at",
            None,
        )
    )

    authorization_sha256 = _nonempty_text(
        getattr(
            candidate,
            "authorization_sha256",
            None,
        )
    )

    if (
        signed_at is None
        or authorization_sha256 is None
    ):
        return None

    return (
        priority[
            execution_status
        ],
        execution_status,
        signed_at,
        authorization_sha256,
    )


def _child_contract_valid(
    result: Any,
    *,
    expected_version: str,
    allowed_statuses: tuple[str, ...],
) -> bool:
    return (
        getattr(
            result,
            "executor_version",
            None,
        )
        == expected_version
        and getattr(
            result,
            "status",
            None,
        )
        in allowed_statuses
        and _valid_reasons(
            getattr(
                result,
                "reasons",
                None,
            )
        )
    )


async def recover_one_live_obligation_once(
    *,
    allow_submission: bool = True,
    db_path: Path = DB_PATH,
) -> LiveRecoveryCoordinatorResult:
    """
    Coordinate recovery of at most one durable live execution
    obligation across BUY and SELL.

    Authority is deliberately narrow:

      1. read both recovery discovery snapshots;
      2. fail closed if either discovery is uncertain;
      3. identify the exact candidate each side's executor
         would currently prefer;
      4. select one side using:
           a. execution ambiguity priority;
           b. oldest signed_at;
           c. deterministic side/identity tie-breakers;
      5. delegate exactly once to that side's existing
         recovery executor;
      6. stop.

    The selected recovery executor re-discovers its own current
    candidate before acting. The coordinator does not pass
    stale durable authority across that race boundary.

    This coordinator has no:
      - signer loading;
      - transaction construction;
      - signing;
      - direct submission;
      - direct reconciliation;
      - direct database mutation;
      - retry;
      - loop;
      - strategy/candidate-entry authority.
    """
    selected_side: str | None = None
    selected_state: str | None = None
    selected_signed_at: float | None = None
    selected_identity: str | None = None

    buy_candidates = 0
    sell_candidates = 0

    def finish(
        status: str,
        stage: str,
        *reasons: str,
        child_version: str | None = None,
        child_status: str | None = None,
    ) -> LiveRecoveryCoordinatorResult:
        return LiveRecoveryCoordinatorResult(
            coordinator_version=(
                LIVE_RECOVERY_COORDINATOR_VERSION
            ),
            status=status,
            stage=stage,
            reasons=tuple(
                reasons
            ),
            selected_side=selected_side,
            selected_state=selected_state,
            selected_signed_at=(
                selected_signed_at
            ),
            selected_identity=(
                selected_identity
            ),
            buy_candidates=buy_candidates,
            sell_candidates=sell_candidates,
            child_version=child_version,
            child_status=child_status,
        )

    if not isinstance(
        allow_submission,
        bool,
    ):
        return finish(
            UNKNOWN,
            DISCOVERY,
            "LIVE_RECOVERY_ALLOW_SUBMISSION_INVALID",
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
        buy_discovery = (
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

    try:
        sell_discovery = (
            discover_live_sell_recovery_candidates(
                db_path=normalized_path
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            DISCOVERY,
            "LIVE_SELL_RECOVERY_DISCOVERY_EXCEPTION",
        )

    if not _valid_discovery_contract(
        buy_discovery,
        expected_version=(
            LIVE_BUY_RECOVERY_DISCOVERY_VERSION
        ),
        pass_status=BUY_DISCOVERY_PASS,
        unknown_status=(
            BUY_DISCOVERY_UNKNOWN
        ),
    ):
        return finish(
            UNKNOWN,
            DISCOVERY,
            "LIVE_BUY_RECOVERY_DISCOVERY_CONTRACT_INVALID",
        )

    if not _valid_discovery_contract(
        sell_discovery,
        expected_version=(
            LIVE_SELL_RECOVERY_DISCOVERY_VERSION
        ),
        pass_status=SELL_DISCOVERY_PASS,
        unknown_status=(
            SELL_DISCOVERY_UNKNOWN
        ),
    ):
        return finish(
            UNKNOWN,
            DISCOVERY,
            "LIVE_SELL_RECOVERY_DISCOVERY_CONTRACT_INVALID",
        )

    buy_rows = buy_discovery.candidates
    sell_rows = sell_discovery.candidates

    buy_candidates = len(
        buy_rows
    )
    sell_candidates = len(
        sell_rows
    )

    #
    # Global selection is unsafe if either read-only discovery
    # is uncertain: the unknown side might contain the more
    # urgent durable obligation.
    #
    if (
        buy_discovery.status
        == BUY_DISCOVERY_UNKNOWN
    ):
        return finish(
            UNKNOWN,
            DISCOVERY,
            "LIVE_BUY_RECOVERY_DISCOVERY_UNKNOWN",
            *buy_discovery.reasons,
        )

    if (
        sell_discovery.status
        == SELL_DISCOVERY_UNKNOWN
    ):
        return finish(
            UNKNOWN,
            DISCOVERY,
            "LIVE_SELL_RECOVERY_DISCOVERY_UNKNOWN",
            *sell_discovery.reasons,
        )

    if (
        buy_discovery.status
        != BUY_DISCOVERY_PASS
        or sell_discovery.status
        != SELL_DISCOVERY_PASS
    ):
        return finish(
            UNKNOWN,
            DISCOVERY,
            "LIVE_RECOVERY_DISCOVERY_STATUS_INVALID",
        )

    options: list[
        tuple[
            tuple[
                int,
                float,
                int,
                str,
            ],
            str,
            str,
            float,
            str,
        ]
    ] = []

    if buy_rows:
        buy_metadata = (
            _buy_candidate_metadata(
                buy_rows[0]
            )
        )

        if buy_metadata is None:
            return finish(
                UNKNOWN,
                DISCOVERY,
                "LIVE_BUY_RECOVERY_CANDIDATE_INVALID",
            )

        (
            priority,
            state,
            signed_at,
            identity,
        ) = buy_metadata

        options.append(
            (
                (
                    priority,
                    signed_at,
                    0,
                    identity,
                ),
                BUY,
                state,
                signed_at,
                identity,
            )
        )

    sell_candidate = _select_sell_candidate(
        sell_rows,
        allow_submission=allow_submission,
    )

    if sell_candidate is not None:
        sell_metadata = (
            _sell_candidate_metadata(
                sell_candidate
            )
        )

        if sell_metadata is None:
            return finish(
                UNKNOWN,
                DISCOVERY,
                "LIVE_SELL_RECOVERY_CANDIDATE_INVALID",
            )

        (
            priority,
            state,
            signed_at,
            identity,
        ) = sell_metadata

        options.append(
            (
                (
                    priority,
                    signed_at,
                    1,
                    identity,
                ),
                SELL,
                state,
                signed_at,
                identity,
            )
        )

    if not options:
        return finish(
            IDLE,
            COMPLETE,
        )

    (
        _,
        selected_side,
        selected_state,
        selected_signed_at,
        selected_identity,
    ) = min(
        options,
        key=lambda option: option[0],
    )

    if selected_side == BUY:
        try:
            child = (
                await recover_one_live_buy_once(
                    allow_submission=(
                        allow_submission
                    ),
                    db_path=normalized_path,
                )
            )
        except Exception:
            return finish(
                UNKNOWN,
                EXECUTE,
                "LIVE_BUY_RECOVERY_EXECUTOR_EXCEPTION",
            )

        allowed_statuses = (
            BUY_IDLE,
            BUY_ADVANCED,
            BUY_RECONCILED,
            BUY_HOLD,
            BUY_BLOCK,
            BUY_UNKNOWN,
        )

        if not _child_contract_valid(
            child,
            expected_version=(
                LIVE_BUY_RECOVERY_EXECUTOR_VERSION
            ),
            allowed_statuses=(
                allowed_statuses
            ),
        ):
            return finish(
                UNKNOWN,
                EXECUTE,
                "LIVE_BUY_RECOVERY_EXECUTOR_CONTRACT_INVALID",
            )

    elif selected_side == SELL:
        try:
            child = (
                await recover_one_live_sell_once(
                    allow_submission=(
                        allow_submission
                    ),
                    db_path=normalized_path,
                )
            )
        except Exception:
            return finish(
                UNKNOWN,
                EXECUTE,
                "LIVE_SELL_RECOVERY_EXECUTOR_EXCEPTION",
            )

        allowed_statuses = (
            SELL_IDLE,
            SELL_ADVANCED,
            SELL_RECONCILED,
            SELL_HOLD,
            SELL_BLOCK,
            SELL_UNKNOWN,
        )

        if not _child_contract_valid(
            child,
            expected_version=(
                LIVE_SELL_RECOVERY_EXECUTOR_VERSION
            ),
            allowed_statuses=(
                allowed_statuses
            ),
        ):
            return finish(
                UNKNOWN,
                EXECUTE,
                "LIVE_SELL_RECOVERY_EXECUTOR_CONTRACT_INVALID",
            )

    else:
        return finish(
            UNKNOWN,
            EXECUTE,
            "LIVE_RECOVERY_SELECTED_SIDE_INVALID",
        )

    child_status = child.status

    status_map = {
        BUY_IDLE: IDLE,
        BUY_ADVANCED: ADVANCED,
        BUY_RECONCILED: RECONCILED,
        BUY_HOLD: HOLD,
        BUY_BLOCK: BLOCK,
        BUY_UNKNOWN: UNKNOWN,
    }

    return finish(
        status_map[
            child_status
        ],
        EXECUTE,
        *child.reasons,
        child_version=(
            child.executor_version
        ),
        child_status=(
            child_status
        ),
    )
