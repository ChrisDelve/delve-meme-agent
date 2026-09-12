from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.live_pump_sell_authorization import (
    LivePumpSellAuthorization,
)
from src.portfolio.live_reservations import (
    DB_PATH,
)
from src.portfolio.live_sell_authorization_records import (
    PASS as AUTHORIZATION_PASS,
    load_live_sell_authorization_record_read_only,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    CONSUMED,
    PASS as CLAIM_PASS,
    RELEASED,
    LiveSellInventoryClaim,
    load_active_live_sell_inventory_claim_read_only,
)
from src.portfolio.live_sell_execution_records import (
    PASS as EXECUTION_PASS,
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED,
    LiveSellExecutionRecord,
    load_live_sell_execution_record_read_only,
)


LIVE_SELL_RECOVERY_DISCOVERY_VERSION = (
    "live-sell-recovery-discovery-v1"
)

PASS = "PASS"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveSellRecoveryCandidate:
    authorization_sha256: str

    execution_status: str
    signed_at: float

    authorization: LivePumpSellAuthorization
    claim: LiveSellInventoryClaim
    execution: LiveSellExecutionRecord


@dataclass(frozen=True)
class LiveSellRecoveryDiscoveryResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    candidates: tuple[
        LiveSellRecoveryCandidate,
        ...,
    ]

    scanned_execution_rows: int
    active_execution_rows: int

    failed_authorization_sha256: (
        str | None
    )


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


def _identity_coherent(
    *,
    authorization: LivePumpSellAuthorization,
    claim: LiveSellInventoryClaim,
    execution: LiveSellExecutionRecord,
) -> bool:
    return (
        claim.authorization_sha256
        == authorization.authorization_sha256
        == execution.authorization_sha256

        and claim.authorization_version
        == authorization.authorization_version
        == execution.authorization_version

        and claim.wallet_pubkey
        == authorization.wallet_pubkey
        == execution.wallet_pubkey

        and claim.mint
        == authorization.mint
        == execution.mint

        and claim.tokens_to_sell
        == authorization.tokens_to_sell
        == execution.tokens_to_sell

        and claim.allocation
        == authorization.allocation

        and claim.status == ACTIVE

        and execution.status
        in (
            SIGNED,
            SUBMISSION_ARMED,
            SUBMITTED,
        )
    )


def discover_live_sell_recovery_candidates(
    *,
    db_path: Path = DB_PATH,
) -> LiveSellRecoveryDiscoveryResult:
    """
    Discover durable live SELLs that can be resumed
    after process restart without rebuilding or
    re-signing their transaction artifact.

    This resolver is strictly read-only.

    Recovery candidates require:

    - an execution row in SIGNED, SUBMISSION_ARMED,
      or SUBMITTED;
    - an exact ACTIVE inventory claim;
    - an exact durable recoverable authorization;
    - exact authorization / claim / execution
      identity coherence.

    Terminal claims are intentionally excluded.
    They represent reconciliation outcomes rather
    than unresolved execution authority.

    An ACTIVE claim with no execution row is also
    intentionally excluded. Such a claim still needs
    the signing front half and is a separate recovery
    class.

    Discovery is fail-closed and all-or-none:
    one incoherent ACTIVE execution row returns
    UNKNOWN and zero candidates.
    """

    scanned_execution_rows = 0
    active_execution_rows = 0

    def finish(
        status: str,
        *reasons: str,
        candidates: tuple[
            LiveSellRecoveryCandidate,
            ...,
        ] = (),
        failed_authorization_sha256: (
            str | None
        ) = None,
    ) -> LiveSellRecoveryDiscoveryResult:
        return LiveSellRecoveryDiscoveryResult(
            resolver_version=(
                LIVE_SELL_RECOVERY_DISCOVERY_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            candidates=candidates,
            scanned_execution_rows=(
                scanned_execution_rows
            ),
            active_execution_rows=(
                active_execution_rows
            ),
            failed_authorization_sha256=(
                failed_authorization_sha256
            ),
        )

    try:
        normalized_path = Path(
            db_path
        )

        if not normalized_path.exists():
            return finish(
                UNKNOWN,
                "LIVE_DATABASE_NOT_FOUND",
            )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_DATABASE_PATH_INVALID",
        )

    connection: sqlite3.Connection | None = None

    try:
        database_uri = (
            normalized_path.resolve().as_uri()
            + "?mode=ro"
        )

        connection = sqlite3.connect(
            database_uri,
            uri=True,
            timeout=30.0,
        )

        connection.row_factory = (
            sqlite3.Row
        )

        connection.execute(
            "PRAGMA query_only = ON"
        )

        connection.execute(
            "PRAGMA busy_timeout = 30000"
        )

        if not _table_exists(
            connection=connection,
            table_name=(
                "live_sell_execution_records"
            ),
        ):
            return finish(
                PASS,
            )

        if not _table_exists(
            connection=connection,
            table_name=(
                "live_sell_inventory_claims"
            ),
        ):
            return finish(
                UNKNOWN,
                "LIVE_SELL_CLAIM_TABLE_MISSING_WITH_EXECUTION_LEDGER",
            )

        rows = connection.execute(
            """
            SELECT
                execution.authorization_sha256
                    AS authorization_sha256,

                execution.status
                    AS execution_status,

                execution.signed_at
                    AS signed_at,

                claim.status
                    AS claim_status

            FROM live_sell_execution_records
                AS execution

            LEFT JOIN live_sell_inventory_claims
                AS claim
              ON claim.authorization_sha256
                 =
                 execution.authorization_sha256

            ORDER BY
                execution.signed_at ASC,
                execution.authorization_sha256 ASC
            """
        ).fetchall()

        scanned_execution_rows = len(
            rows
        )

    except sqlite3.Error:
        return finish(
            UNKNOWN,
            "LIVE_SELL_RECOVERY_DISCOVERY_DATABASE_ERROR",
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_SELL_RECOVERY_DISCOVERY_FAILED",
        )

    finally:
        if connection is not None:
            connection.close()

    candidates: list[
        LiveSellRecoveryCandidate
    ] = []

    for row in rows:
        try:
            authorization_sha256 = str(
                row[
                    "authorization_sha256"
                ]
            )

            raw_execution_status = str(
                row[
                    "execution_status"
                ]
            )

            raw_claim_status = (
                None
                if row[
                    "claim_status"
                ]
                is None
                else str(
                    row[
                        "claim_status"
                    ]
                )
            )

            raw_signed_at = float(
                row[
                    "signed_at"
                ]
            )

        except Exception:
            return finish(
                UNKNOWN,
                "LIVE_SELL_RECOVERY_INDEX_ROW_INVALID",
            )

        if not _valid_sha256(
            authorization_sha256
        ):
            return finish(
                UNKNOWN,
                "LIVE_SELL_RECOVERY_AUTHORIZATION_SHA_INVALID",
                failed_authorization_sha256=(
                    authorization_sha256
                ),
            )

        if raw_claim_status is None:
            return finish(
                UNKNOWN,
                "LIVE_SELL_RECOVERY_EXECUTION_ORPHANED",
                failed_authorization_sha256=(
                    authorization_sha256
                ),
            )

        #
        # Terminal claims are completed outcomes,
        # not restart execution work.
        #
        if raw_claim_status in (
            RELEASED,
            CONSUMED,
        ):
            continue

        if raw_claim_status != ACTIVE:
            return finish(
                UNKNOWN,
                "LIVE_SELL_RECOVERY_CLAIM_STATUS_INVALID",
                failed_authorization_sha256=(
                    authorization_sha256
                ),
            )

        active_execution_rows += 1

        if raw_execution_status not in (
            SIGNED,
            SUBMISSION_ARMED,
            SUBMITTED,
        ):
            return finish(
                UNKNOWN,
                "LIVE_SELL_RECOVERY_EXECUTION_STATUS_INVALID",
                failed_authorization_sha256=(
                    authorization_sha256
                ),
            )

        authorization_result = (
            load_live_sell_authorization_record_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=normalized_path,
            )
        )

        if (
            authorization_result.status
            != AUTHORIZATION_PASS
            or authorization_result.authorization
            is None
        ):
            return finish(
                UNKNOWN,
                "LIVE_SELL_RECOVERY_AUTHORIZATION_LOAD_FAILED",
                *tuple(
                    authorization_result.reasons
                ),
                failed_authorization_sha256=(
                    authorization_sha256
                ),
            )

        authorization = (
            authorization_result
            .authorization
        )

        claim_result = (
            load_active_live_sell_inventory_claim_read_only(
                authorization=authorization,
                db_path=normalized_path,
            )
        )

        if (
            claim_result.status
            != CLAIM_PASS
            or claim_result.claim is None
        ):
            return finish(
                UNKNOWN,
                "LIVE_SELL_RECOVERY_ACTIVE_CLAIM_LOAD_FAILED",
                *tuple(
                    claim_result.reasons
                ),
                failed_authorization_sha256=(
                    authorization_sha256
                ),
            )

        claim = claim_result.claim

        execution_result = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=normalized_path,
            )
        )

        if (
            execution_result.status
            != EXECUTION_PASS
            or execution_result.record
            is None
        ):
            return finish(
                UNKNOWN,
                "LIVE_SELL_RECOVERY_EXECUTION_LOAD_FAILED",
                *tuple(
                    execution_result.reasons
                ),
                failed_authorization_sha256=(
                    authorization_sha256
                ),
            )

        execution = (
            execution_result.record
        )

        if not _identity_coherent(
            authorization=authorization,
            claim=claim,
            execution=execution,
        ):
            return finish(
                UNKNOWN,
                "LIVE_SELL_RECOVERY_IDENTITY_MISMATCH",
                failed_authorization_sha256=(
                    authorization_sha256
                ),
            )

        if (
            execution.status
            != raw_execution_status
            or execution.signed_at
            != raw_signed_at
        ):
            return finish(
                UNKNOWN,
                "LIVE_SELL_RECOVERY_INDEX_CHANGED_DURING_DISCOVERY",
                failed_authorization_sha256=(
                    authorization_sha256
                ),
            )

        candidates.append(
            LiveSellRecoveryCandidate(
                authorization_sha256=(
                    authorization_sha256
                ),
                execution_status=(
                    execution.status
                ),
                signed_at=(
                    execution.signed_at
                ),
                authorization=(
                    authorization
                ),
                claim=claim,
                execution=execution,
            )
        )

    return finish(
        PASS,
        candidates=tuple(
            candidates
        ),
    )
