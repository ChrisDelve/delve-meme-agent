from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.portfolio.live_reservations import (
    DB_PATH,
    get_connection,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    CONSUMED,
    RELEASED,
    LiveSellInventoryClaim,
    _load_claim,
    _strict_timestamp,
)


LIVE_SELL_UNEXECUTED_RELEASE_VERSION = (
    "live-sell-unexecuted-release-v1"
)

RELEASED_UNEXECUTED_SELL_REASON = (
    "RELEASED_UNEXECUTED_SELL"
)

PASS = "PASS"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveSellUnexecutedReleaseResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    claim: LiveSellInventoryClaim | None

    changed: bool


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


def release_unexecuted_live_sell_claim(
    *,
    authorization_sha256: str,
    db_path: Path = DB_PATH,
) -> LiveSellUnexecutedReleaseResult:
    """
    Atomically release one ACTIVE SELL inventory claim
    only when there is no durable SELL execution
    artifact for that authorization.

    This is a pre-submission abort/recovery boundary.

    It performs no:
    - signing
    - submission
    - RPC
    - retry
    - position mutation
    - PnL mutation
    - transaction-journal mutation

    BEGIN IMMEDIATE is required so a concurrent signer
    cannot bind an execution record between the
    no-execution proof and the claim release.

    A signer that has computed a signature in memory
    but has not durably bound it is harmless here:
    after this release, its later bind must fail because
    the inventory claim is no longer ACTIVE.
    """
    if isinstance(
        authorization_sha256,
        str,
    ):
        authorization_sha256 = (
            authorization_sha256.strip()
        )

    else:
        authorization_sha256 = ""

    def finish(
        status: str,
        *reasons: str,
        claim: LiveSellInventoryClaim | None = None,
        changed: bool = False,
    ) -> LiveSellUnexecutedReleaseResult:
        return LiveSellUnexecutedReleaseResult(
            resolver_version=(
                LIVE_SELL_UNEXECUTED_RELEASE_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            claim=claim,
            changed=changed,
        )

    if not _valid_sha256(
        authorization_sha256
    ):
        return finish(
            UNKNOWN,
            "INVALID_AUTHORIZATION_SHA256",
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
        connection = get_connection(
            normalized_path
        )

        connection.execute(
            "BEGIN IMMEDIATE"
        )

        #
        # This primitive never initializes schema.
        # Missing claim schema means there is no
        # authoritative claim we are allowed to mutate.
        #
        if not _table_exists(
            connection=connection,
            table_name=(
                "live_sell_inventory_claims"
            ),
        ):
            connection.commit()

            return finish(
                UNKNOWN,
                "LIVE_SELL_CLAIM_TABLE_NOT_FOUND",
            )

        claim = _load_claim(
            connection=connection,
            authorization_sha256=(
                authorization_sha256
            ),
        )

        if claim is None:
            connection.commit()

            return finish(
                BLOCK,
                "SELL_CLAIM_NOT_FOUND",
            )

        #
        # Exact idempotent replay.
        #
        if claim.status == RELEASED:
            if (
                claim.terminal_reason
                != RELEASED_UNEXECUTED_SELL_REASON
            ):
                connection.commit()

                return finish(
                    BLOCK,
                    "SELL_UNEXECUTED_RELEASE_TERMINAL_REASON_MISMATCH",
                    claim=claim,
                )

            if (
                claim.terminal_at is None
                or not _strict_timestamp(
                    claim.terminal_at
                )
            ):
                connection.commit()

                return finish(
                    UNKNOWN,
                    "SELL_UNEXECUTED_RELEASE_TERMINAL_STATE_INVALID",
                    claim=claim,
                )

            connection.commit()

            return finish(
                PASS,
                claim=claim,
                changed=False,
            )

        if claim.status == CONSUMED:
            connection.commit()

            return finish(
                BLOCK,
                "SELL_CLAIM_NOT_UNEXECUTED_RELEASABLE",
                claim=claim,
            )

        if (
            claim.status != ACTIVE
            or claim.terminal_at is not None
            or claim.terminal_reason is not None
        ):
            connection.commit()

            return finish(
                UNKNOWN,
                "ACTIVE_SELL_CLAIM_STATE_INVALID",
                claim=claim,
            )

        #
        # Critical authority proof:
        #
        # under the same SQLite write reservation,
        # there must be no durable execution record
        # for this authorization.
        #
        if _table_exists(
            connection=connection,
            table_name=(
                "live_sell_execution_records"
            ),
        ):
            execution_row = connection.execute(
                """
                SELECT authorization_sha256

                FROM live_sell_execution_records

                WHERE authorization_sha256 = ?

                LIMIT 1
                """,
                (
                    authorization_sha256,
                ),
            ).fetchone()

            if execution_row is not None:
                connection.commit()

                return finish(
                    BLOCK,
                    "SELL_EXECUTION_RECORD_EXISTS",
                    claim=claim,
                )

        terminal_at = time.time()

        if not _strict_timestamp(
            terminal_at
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                "SYSTEM_TIME_INVALID",
                claim=claim,
            )

        updated = connection.execute(
            """
            UPDATE live_sell_inventory_claims

            SET
                status = ?,
                terminal_at = ?,
                terminal_reason = ?

            WHERE authorization_sha256 = ?
              AND status = ?
              AND terminal_at IS NULL
              AND terminal_reason IS NULL
            """,
            (
                RELEASED,
                terminal_at,
                RELEASED_UNEXECUTED_SELL_REASON,

                authorization_sha256,
                ACTIVE,
            ),
        )

        if updated.rowcount != 1:
            connection.rollback()

            return finish(
                UNKNOWN,
                "SELL_UNEXECUTED_RELEASE_TRANSITION_FAILED",
            )

        persisted = _load_claim(
            connection=connection,
            authorization_sha256=(
                authorization_sha256
            ),
        )

        if (
            persisted is None
            or persisted.status
            != RELEASED
            or persisted.terminal_at
            is None
            or not _strict_timestamp(
                persisted.terminal_at
            )
            or persisted.terminal_reason
            != RELEASED_UNEXECUTED_SELL_REASON
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                "SELL_UNEXECUTED_RELEASE_VERIFICATION_FAILED",
            )

        #
        # Verify the no-execution invariant again while
        # we still hold the same write reservation.
        #
        if _table_exists(
            connection=connection,
            table_name=(
                "live_sell_execution_records"
            ),
        ):
            execution_row = connection.execute(
                """
                SELECT authorization_sha256

                FROM live_sell_execution_records

                WHERE authorization_sha256 = ?

                LIMIT 1
                """,
                (
                    authorization_sha256,
                ),
            ).fetchone()

            if execution_row is not None:
                connection.rollback()

                return finish(
                    UNKNOWN,
                    "SELL_EXECUTION_APPEARED_DURING_RELEASE",
                )

        connection.commit()

        return finish(
            PASS,
            claim=persisted,
            changed=True,
        )

    except sqlite3.Error:
        if connection is not None:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass

        return finish(
            UNKNOWN,
            "SELL_UNEXECUTED_RELEASE_DATABASE_ERROR",
        )

    except Exception:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass

        return finish(
            UNKNOWN,
            "SELL_UNEXECUTED_RELEASE_FAILED",
        )

    finally:
        if connection is not None:
            connection.close()
