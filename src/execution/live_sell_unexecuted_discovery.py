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
    LIVE_SELL_AUTHORIZATION_RECORD_LOADER_VERSION,
    PASS as AUTHORIZATION_PASS,
    load_live_sell_authorization_record_read_only,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION,
    PASS as CLAIM_PASS,
    LiveSellInventoryClaim,
    load_active_live_sell_inventory_claim_read_only,
)


LIVE_SELL_UNEXECUTED_DISCOVERY_VERSION = (
    "live-sell-unexecuted-discovery-v1"
)

PASS = "PASS"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveSellUnexecutedCandidate:
    authorization_sha256: str
    claimed_at: float

    authorization: LivePumpSellAuthorization
    claim: LiveSellInventoryClaim


@dataclass(frozen=True)
class LiveSellUnexecutedDiscoveryResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    candidates: tuple[
        LiveSellUnexecutedCandidate,
        ...,
    ]

    scanned_active_claim_rows: int
    unexecuted_rows: int

    failed_authorization_sha256: str | None


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


def _execution_exists_read_only(
    *,
    db_path: Path,
    authorization_sha256: str,
) -> bool:
    database_uri = (
        db_path.resolve().as_uri()
        + "?mode=ro"
    )

    connection = sqlite3.connect(
        database_uri,
        uri=True,
        timeout=30.0,
    )

    try:
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
            return False

        row = connection.execute(
            """
            SELECT 1

            FROM live_sell_execution_records

            WHERE authorization_sha256 = ?

            LIMIT 1
            """,
            (
                authorization_sha256,
            ),
        ).fetchone()

        return row is not None

    finally:
        connection.close()


def _identity_coherent(
    *,
    authorization: LivePumpSellAuthorization,
    claim: LiveSellInventoryClaim,
) -> bool:
    return (
        authorization.authorization_sha256
        == claim.authorization_sha256

        and authorization.authorization_version
        == claim.authorization_version

        and authorization.wallet_pubkey
        == claim.wallet_pubkey

        and authorization.mint
        == claim.mint

        and authorization.tokens_to_sell
        == claim.tokens_to_sell

        and authorization.allocation
        == claim.allocation

        and claim.status == ACTIVE
    )


def discover_live_sell_unexecuted_candidates(
    *,
    db_path: Path = DB_PATH,
) -> LiveSellUnexecutedDiscoveryResult:
    """
    Discover ACTIVE live SELL claims which have not
    crossed the durable signing boundary.

    This resolver is strictly read-only.

    A candidate must have:
    - an ACTIVE inventory claim;
    - no durable execution record;
    - an exact recoverable authorization;
    - an exact active-claim reload;
    - coherent authorization / claim identity.

    Execution-bearing SELLs belong to
    live_sell_recovery_discovery instead.

    Discovery is fail-closed and all-or-none.
    """

    scanned_active_claim_rows = 0
    unexecuted_rows = 0

    def finish(
        status: str,
        *reasons: str,
        candidates: tuple[
            LiveSellUnexecutedCandidate,
            ...,
        ] = (),
        failed_authorization_sha256: (
            str | None
        ) = None,
    ) -> LiveSellUnexecutedDiscoveryResult:
        return LiveSellUnexecutedDiscoveryResult(
            resolver_version=(
                LIVE_SELL_UNEXECUTED_DISCOVERY_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            candidates=candidates,
            scanned_active_claim_rows=(
                scanned_active_claim_rows
            ),
            unexecuted_rows=(
                unexecuted_rows
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

        connection.row_factory = sqlite3.Row

        connection.execute(
            "PRAGMA query_only = ON"
        )

        connection.execute(
            "PRAGMA busy_timeout = 30000"
        )

        if not _table_exists(
            connection=connection,
            table_name=(
                "live_sell_inventory_claims"
            ),
        ):
            return finish(
                PASS,
            )

        execution_table_exists = (
            _table_exists(
                connection=connection,
                table_name=(
                    "live_sell_execution_records"
                ),
            )
        )

        if execution_table_exists:
            rows = connection.execute(
                """
                SELECT
                    claim.authorization_sha256
                        AS authorization_sha256,

                    claim.claimed_at
                        AS claimed_at,

                    execution.authorization_sha256
                        AS execution_authorization_sha256

                FROM live_sell_inventory_claims
                    AS claim

                LEFT JOIN live_sell_execution_records
                    AS execution
                  ON execution.authorization_sha256
                     =
                     claim.authorization_sha256

                WHERE claim.status = ?

                ORDER BY
                    claim.claimed_at ASC,
                    claim.authorization_sha256 ASC
                """,
                (
                    ACTIVE,
                ),
            ).fetchall()

        else:
            rows = connection.execute(
                """
                SELECT
                    authorization_sha256,
                    claimed_at,
                    NULL
                        AS execution_authorization_sha256

                FROM live_sell_inventory_claims

                WHERE status = ?

                ORDER BY
                    claimed_at ASC,
                    authorization_sha256 ASC
                """,
                (
                    ACTIVE,
                ),
            ).fetchall()

        scanned_active_claim_rows = len(
            rows
        )

    except sqlite3.Error:
        return finish(
            UNKNOWN,
            "LIVE_SELL_UNEXECUTED_DISCOVERY_DATABASE_ERROR",
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_SELL_UNEXECUTED_DISCOVERY_FAILED",
        )

    finally:
        if connection is not None:
            connection.close()

    candidates: list[
        LiveSellUnexecutedCandidate
    ] = []

    for row in rows:
        try:
            authorization_sha256 = str(
                row[
                    "authorization_sha256"
                ]
            )

            claimed_at = float(
                row[
                    "claimed_at"
                ]
            )

            execution_authorization_sha256 = (
                row[
                    "execution_authorization_sha256"
                ]
            )

        except Exception:
            return finish(
                UNKNOWN,
                "LIVE_SELL_UNEXECUTED_INDEX_ROW_INVALID",
            )

        #
        # Already-signed authority belongs to the
        # execution-bearing recovery path.
        #
        if (
            execution_authorization_sha256
            is not None
        ):
            continue

        unexecuted_rows += 1

        if not _valid_sha256(
            authorization_sha256
        ):
            return finish(
                UNKNOWN,
                "LIVE_SELL_UNEXECUTED_AUTHORIZATION_SHA_INVALID",
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
            getattr(
                authorization_result,
                "resolver_version",
                None,
            )
            != LIVE_SELL_AUTHORIZATION_RECORD_LOADER_VERSION
            or authorization_result.status
            != AUTHORIZATION_PASS
            or authorization_result.authorization
            is None
        ):
            return finish(
                UNKNOWN,
                "LIVE_SELL_UNEXECUTED_AUTHORIZATION_LOAD_FAILED",
                *tuple(
                    getattr(
                        authorization_result,
                        "reasons",
                        (),
                    )
                ),
                failed_authorization_sha256=(
                    authorization_sha256
                ),
            )

        authorization = (
            authorization_result.authorization
        )

        claim_result = (
            load_active_live_sell_inventory_claim_read_only(
                authorization=authorization,
                db_path=normalized_path,
            )
        )

        if (
            getattr(
                claim_result,
                "loader_version",
                None,
            )
            != ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION
            or claim_result.status
            != CLAIM_PASS
            or claim_result.claim is None
        ):
            return finish(
                UNKNOWN,
                "LIVE_SELL_UNEXECUTED_ACTIVE_CLAIM_LOAD_FAILED",
                *tuple(
                    getattr(
                        claim_result,
                        "reasons",
                        (),
                    )
                ),
                failed_authorization_sha256=(
                    authorization_sha256
                ),
            )

        claim = claim_result.claim

        if not _identity_coherent(
            authorization=authorization,
            claim=claim,
        ):
            return finish(
                UNKNOWN,
                "LIVE_SELL_UNEXECUTED_IDENTITY_MISMATCH",
                failed_authorization_sha256=(
                    authorization_sha256
                ),
            )

        if claim.claimed_at != claimed_at:
            return finish(
                UNKNOWN,
                "LIVE_SELL_UNEXECUTED_INDEX_CHANGED_DURING_DISCOVERY",
                failed_authorization_sha256=(
                    authorization_sha256
                ),
            )

        #
        # Recheck the consequential boundary after all
        # other durable evidence has been reloaded.
        #
        try:
            execution_now_exists = (
                _execution_exists_read_only(
                    db_path=normalized_path,
                    authorization_sha256=(
                        authorization_sha256
                    ),
                )
            )

        except Exception:
            return finish(
                UNKNOWN,
                "LIVE_SELL_UNEXECUTED_EXECUTION_RECHECK_FAILED",
                failed_authorization_sha256=(
                    authorization_sha256
                ),
            )

        if execution_now_exists:
            return finish(
                UNKNOWN,
                "LIVE_SELL_UNEXECUTED_EXECUTION_APPEARED_DURING_DISCOVERY",
                failed_authorization_sha256=(
                    authorization_sha256
                ),
            )

        candidates.append(
            LiveSellUnexecutedCandidate(
                authorization_sha256=(
                    authorization_sha256
                ),
                claimed_at=claimed_at,
                authorization=authorization,
                claim=claim,
            )
        )

    return finish(
        PASS,
        candidates=tuple(
            candidates
        ),
    )
