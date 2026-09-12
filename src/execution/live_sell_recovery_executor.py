from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.live_sell_lifecycle import (
    ABORTED as LIFECYCLE_ABORTED,
    ADVANCED as LIFECYCLE_ADVANCED,
    BLOCK as LIFECYCLE_BLOCK,
    HOLD as LIFECYCLE_HOLD,
    RECONCILED as LIFECYCLE_RECONCILED,
    UNKNOWN as LIFECYCLE_UNKNOWN,
    LIVE_SELL_LIFECYCLE_VERSION,
    advance_authorized_live_sell_once,
)
from src.execution.live_sell_recovery_discovery import (
    PASS as DISCOVERY_PASS,
    LIVE_SELL_RECOVERY_DISCOVERY_VERSION,
    LiveSellRecoveryCandidate,
    discover_live_sell_recovery_candidates,
)
from src.portfolio.live_reservations import (
    DB_PATH,
)
from src.portfolio.live_sell_execution_records import (
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED,
)


LIVE_SELL_RECOVERY_EXECUTOR_VERSION = (
    "live-sell-recovery-executor-v1"
)

IDLE = "IDLE"
ADVANCED = "ADVANCED"
RECONCILED = "RECONCILED"
HOLD = "HOLD"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveSellRecoveryExecutionResult:
    executor_version: str

    status: str
    reasons: tuple[str, ...]

    discovered_candidates: int

    authorization_sha256: str | None
    execution_status: str | None

    lifecycle_status: str | None
    lifecycle_stage: str | None

    transaction_signature: str | None


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


def _candidate_contract_valid(
    candidate: LiveSellRecoveryCandidate,
) -> bool:
    authorization_sha256 = getattr(
        candidate,
        "authorization_sha256",
        None,
    )

    execution_status = getattr(
        candidate,
        "execution_status",
        None,
    )

    authorization = getattr(
        candidate,
        "authorization",
        None,
    )

    claim = getattr(
        candidate,
        "claim",
        None,
    )

    execution = getattr(
        candidate,
        "execution",
        None,
    )

    if (
        not _valid_sha256(
            authorization_sha256
        )
        or execution_status
        not in (
            SIGNED,
            SUBMISSION_ARMED,
            SUBMITTED,
        )
        or authorization is None
        or claim is None
        or execution is None
    ):
        return False

    return (
        getattr(
            authorization,
            "authorization_sha256",
            None,
        )
        == authorization_sha256

        and getattr(
            claim,
            "authorization_sha256",
            None,
        )
        == authorization_sha256

        and getattr(
            execution,
            "authorization_sha256",
            None,
        )
        == authorization_sha256

        and getattr(
            execution,
            "status",
            None,
        )
        == execution_status
    )


async def recover_one_live_sell_once(
    *,
    db_path: Path = DB_PATH,
) -> LiveSellRecoveryExecutionResult:
    """
    Recover at most one durable execution-bearing
    live SELL after restart.

    This executor intentionally processes only the
    oldest validated recovery candidate from one
    discovery snapshot.

    It does NOT:
    - rebuild authorization;
    - rebuild account context;
    - rebuild an unsigned message;
    - run pre-sign validation;
    - sign directly;
    - submit directly;
    - reconcile directly;
    - loop over multiple candidates;
    - retry the lifecycle.

    The only consequential authority delegated by this
    component is exactly one call to
    advance_authorized_live_sell_once().
    """

    discovered_candidates = 0

    def finish(
        status: str,
        *reasons: str,
        authorization_sha256: (
            str | None
        ) = None,
        execution_status: (
            str | None
        ) = None,
        lifecycle_status: (
            str | None
        ) = None,
        lifecycle_stage: (
            str | None
        ) = None,
        transaction_signature: (
            str | None
        ) = None,
    ) -> LiveSellRecoveryExecutionResult:
        return LiveSellRecoveryExecutionResult(
            executor_version=(
                LIVE_SELL_RECOVERY_EXECUTOR_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            discovered_candidates=(
                discovered_candidates
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            execution_status=(
                execution_status
            ),
            lifecycle_status=(
                lifecycle_status
            ),
            lifecycle_stage=(
                lifecycle_stage
            ),
            transaction_signature=(
                transaction_signature
            ),
        )

    try:
        normalized_path = Path(
            db_path
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_DATABASE_PATH_INVALID",
        )

    try:
        discovery = (
            discover_live_sell_recovery_candidates(
                db_path=normalized_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_SELL_RECOVERY_DISCOVERY_EXCEPTION",
        )

    if (
        getattr(
            discovery,
            "resolver_version",
            None,
        )
        != LIVE_SELL_RECOVERY_DISCOVERY_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_RECOVERY_DISCOVERY_VERSION_MISMATCH",
        )

    if (
        getattr(
            discovery,
            "status",
            None,
        )
        != DISCOVERY_PASS
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_RECOVERY_DISCOVERY_FAILED",
            *tuple(
                getattr(
                    discovery,
                    "reasons",
                    (),
                )
            ),
        )

    candidates = getattr(
        discovery,
        "candidates",
        None,
    )

    if not isinstance(
        candidates,
        tuple,
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_RECOVERY_CANDIDATES_INVALID",
        )

    discovered_candidates = len(
        candidates
    )

    if not candidates:
        return finish(
            IDLE,
        )

    #
    # Discovery guarantees oldest signed artifact first.
    # Deliberately execute only one candidate.
    #
    candidate = candidates[0]

    if not _candidate_contract_valid(
        candidate
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_RECOVERY_CANDIDATE_INVALID",
        )

    authorization_sha256 = (
        candidate.authorization_sha256
    )

    execution_status = (
        candidate.execution_status
    )

    try:
        lifecycle = (
            await advance_authorized_live_sell_once(
                authorization=(
                    candidate.authorization
                ),
                db_path=normalized_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_SELL_RECOVERY_LIFECYCLE_EXCEPTION",
            authorization_sha256=(
                authorization_sha256
            ),
            execution_status=(
                execution_status
            ),
        )

    lifecycle_status = getattr(
        lifecycle,
        "status",
        None,
    )

    lifecycle_stage = getattr(
        lifecycle,
        "stage",
        None,
    )

    transaction_signature = getattr(
        lifecycle,
        "transaction_signature",
        None,
    )

    if (
        getattr(
            lifecycle,
            "executor_version",
            None,
        )
        != LIVE_SELL_LIFECYCLE_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_RECOVERY_LIFECYCLE_VERSION_MISMATCH",
            authorization_sha256=(
                authorization_sha256
            ),
            execution_status=(
                execution_status
            ),
            lifecycle_status=(
                lifecycle_status
            ),
            lifecycle_stage=(
                lifecycle_stage
            ),
            transaction_signature=(
                transaction_signature
            ),
        )

    if (
        getattr(
            lifecycle,
            "authorization_sha256",
            None,
        )
        != authorization_sha256
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_RECOVERY_LIFECYCLE_BINDING_MISMATCH",
            authorization_sha256=(
                authorization_sha256
            ),
            execution_status=(
                execution_status
            ),
            lifecycle_status=(
                lifecycle_status
            ),
            lifecycle_stage=(
                lifecycle_stage
            ),
            transaction_signature=(
                transaction_signature
            ),
        )

    reasons = tuple(
        getattr(
            lifecycle,
            "reasons",
            (),
        )
    )

    common = {
        "authorization_sha256": (
            authorization_sha256
        ),
        "execution_status": (
            execution_status
        ),
        "lifecycle_status": (
            lifecycle_status
        ),
        "lifecycle_stage": (
            lifecycle_stage
        ),
        "transaction_signature": (
            transaction_signature
        ),
    }

    if (
        lifecycle_status
        == LIFECYCLE_ADVANCED
    ):
        return finish(
            ADVANCED,
            *reasons,
            **common,
        )

    if (
        lifecycle_status
        == LIFECYCLE_RECONCILED
    ):
        return finish(
            RECONCILED,
            *reasons,
            **common,
        )

    if (
        lifecycle_status
        == LIFECYCLE_HOLD
    ):
        return finish(
            HOLD,
            *reasons,
            **common,
        )

    if (
        lifecycle_status
        == LIFECYCLE_BLOCK
    ):
        return finish(
            BLOCK,
            *reasons,
            **common,
        )

    if (
        lifecycle_status
        == LIFECYCLE_UNKNOWN
    ):
        return finish(
            UNKNOWN,
            *reasons,
            **common,
        )

    #
    # Discovery only selects ACTIVE claims with an
    # existing execution record. Such a candidate cannot
    # legitimately resolve through the lifecycle's
    # pre-sign ABORTED terminal.
    #
    if (
        lifecycle_status
        == LIFECYCLE_ABORTED
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_RECOVERY_ABORTED_EXECUTION_INCOHERENT",
            *reasons,
            **common,
        )

    return finish(
        UNKNOWN,
        "LIVE_SELL_RECOVERY_LIFECYCLE_STATUS_INVALID",
        *reasons,
        **common,
    )
