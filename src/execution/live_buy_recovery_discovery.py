from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.portfolio.live_reservations import (
    BUY,
    DB_PATH,
    RESERVATION_VERSION,
    SIGNED,
    SUBMITTED as RESERVATION_SUBMITTED,
    LiveCapitalReservation,
    load_capital_reservation_read_only,
)


LIVE_BUY_RECOVERY_DISCOVERY_VERSION = (
    "live-buy-recovery-discovery-v1"
)

PASS = "PASS"
UNKNOWN = "UNKNOWN"

PRISTINE_SIGNED = "PRISTINE_SIGNED"
ARMED_SIGNED = "ARMED_SIGNED"
SUBMITTED = "SUBMITTED"


@dataclass(frozen=True)
class LiveBuyRecoveryCandidate:
    recovery_state: str
    reservation: LiveCapitalReservation


@dataclass(frozen=True)
class LiveBuyRecoveryDiscoveryResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    candidates: tuple[
        LiveBuyRecoveryCandidate,
        ...,
    ]

    scanned_reservation_rows: int
    recovery_reservation_rows: int

    failed_reservation_id: str | None


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


def _nonempty_string(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            str,
        )
        and bool(
            value.strip()
        )
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


def _strict_nonnegative_int(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            int,
        )
        and not isinstance(
            value,
            bool,
        )
        and value >= 0
    )


def _valid_timestamp(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            (
                int,
                float,
            ),
        )
        and not isinstance(
            value,
            bool,
        )
        and math.isfinite(
            float(value)
        )
        and float(value) >= 0.0
    )


def _signed_artifact_is_coherent(
    reservation: LiveCapitalReservation,
) -> bool:
    return (
        reservation.reservation_version
        == RESERVATION_VERSION
        and reservation.side == BUY
        and reservation.status
        in (
            SIGNED,
            RESERVATION_SUBMITTED,
        )
        and _valid_timestamp(
            reservation.signed_at
        )
        and _nonempty_string(
            reservation.transaction_signature
        )
        and _valid_sha256(
            reservation.signed_message_sha256
        )
        and _valid_sha256(
            reservation.signed_transaction_sha256
        )
        and isinstance(
            reservation.signed_transaction_bytes,
            bytes,
        )
        and bool(
            reservation.signed_transaction_bytes
        )
        and _nonempty_string(
            reservation.recent_blockhash
        )
        and _strict_nonnegative_int(
            reservation.last_valid_block_height
        )
        and _strict_nonnegative_int(
            reservation.blockhash_rpc_slot
        )
        and reservation.terminal_at is None
        and reservation.terminal_reason is None
    )


def _classify_recovery_state(
    reservation: LiveCapitalReservation,
) -> str | None:
    if not _signed_artifact_is_coherent(
        reservation
    ):
        return None

    attempt_count = (
        reservation.submission_attempt_count
    )

    if not _strict_nonnegative_int(
        attempt_count
    ):
        return None

    if reservation.status == SIGNED:
        if reservation.submitted_at is not None:
            return None

        if (
            reservation.submission_started_at
            is None
            and attempt_count == 0
        ):
            return PRISTINE_SIGNED

        if (
            _valid_timestamp(
                reservation.submission_started_at
            )
            and attempt_count >= 1
        ):
            return ARMED_SIGNED

        return None

    if (
        reservation.status
        == RESERVATION_SUBMITTED
    ):
        if (
            not _valid_timestamp(
                reservation.submission_started_at
            )
            or attempt_count < 1
            or not _valid_timestamp(
                reservation.submitted_at
            )
        ):
            return None

        return SUBMITTED

    return None


def _index_matches_reservation(
    *,
    row: sqlite3.Row,
    reservation: LiveCapitalReservation,
) -> bool:
    try:
        return (
            reservation.reservation_id
            == str(
                row["reservation_id"]
            )
            and reservation.reservation_version
            == str(
                row["reservation_version"]
            )
            and reservation.side
            == str(
                row["side"]
            )
            and reservation.status
            == str(
                row["status"]
            )
            and reservation.signed_at
            == (
                None
                if row["signed_at"] is None
                else float(
                    row["signed_at"]
                )
            )
            and reservation.submission_started_at
            == (
                None
                if (
                    row[
                        "submission_started_at"
                    ]
                    is None
                )
                else float(
                    row[
                        "submission_started_at"
                    ]
                )
            )
            and reservation.submission_attempt_count
            == int(
                row[
                    "submission_attempt_count"
                ]
            )
            and reservation.submitted_at
            == (
                None
                if row["submitted_at"] is None
                else float(
                    row["submitted_at"]
                )
            )
        )
    except Exception:
        return False


def discover_live_buy_recovery_candidates(
    *,
    db_path: Path = DB_PATH,
) -> LiveBuyRecoveryDiscoveryResult:
    """
    Discover durable BUY reservations requiring execution
    recovery after process restart.

    Discovery is strictly read-only.

    Recovery classes:

    PRISTINE_SIGNED
        SIGNED and definitely before the durable submission
        boundary. It may later become eligible for exactly
        one relay after reconciliation/status inspection.

    ARMED_SIGNED
        SIGNED but the durable submission boundary has
        already been crossed. The transaction MAY have
        been relayed. It must never be blindly relayed
        again and requires reconciliation.

    SUBMITTED
        The exact persisted transaction signature was
        returned by RPC and acknowledged locally. It
        requires reconciliation.

    ACTIVE reservations are intentionally excluded because
    they have not yet acquired signed execution authority.

    Discovery is all-or-none fail closed:
    one malformed unresolved SIGNED/SUBMITTED reservation
    returns UNKNOWN and zero candidates.

    This resolver performs no:
      - schema migration;
      - TTL expiration;
      - reservation transition;
      - signing;
      - submission;
      - reconciliation;
      - release.
    """

    scanned_reservation_rows = 0
    recovery_reservation_rows = 0

    def finish(
        status: str,
        *reasons: str,
        candidates: tuple[
            LiveBuyRecoveryCandidate,
            ...,
        ] = (),
        failed_reservation_id: (
            str | None
        ) = None,
    ) -> LiveBuyRecoveryDiscoveryResult:
        return LiveBuyRecoveryDiscoveryResult(
            resolver_version=(
                LIVE_BUY_RECOVERY_DISCOVERY_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            candidates=candidates,
            scanned_reservation_rows=(
                scanned_reservation_rows
            ),
            recovery_reservation_rows=(
                recovery_reservation_rows
            ),
            failed_reservation_id=(
                failed_reservation_id
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

    connection: (
        sqlite3.Connection | None
    ) = None

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
                "live_capital_reservations"
            ),
        ):
            return finish(
                PASS,
            )

        rows = connection.execute(
            """
            SELECT
                reservation_id,
                reservation_version,
                side,
                status,
                signed_at,
                submission_started_at,
                submission_attempt_count,
                submitted_at

            FROM live_capital_reservations

            WHERE status IN (?, ?)

            ORDER BY
                signed_at ASC,
                reservation_id ASC
            """,
            (
                SIGNED,
                RESERVATION_SUBMITTED,
            ),
        ).fetchall()

        scanned_reservation_rows = len(
            rows
        )

    except sqlite3.Error:
        return finish(
            UNKNOWN,
            "LIVE_BUY_RECOVERY_DISCOVERY_DATABASE_ERROR",
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_BUY_RECOVERY_DISCOVERY_FAILED",
        )

    finally:
        if connection is not None:
            connection.close()

    candidates: list[
        LiveBuyRecoveryCandidate
    ] = []

    for row in rows:
        try:
            raw_reservation_id = (
                row["reservation_id"]
            )

            reservation_id = (
                raw_reservation_id.strip()
                if isinstance(
                    raw_reservation_id,
                    str,
                )
                else ""
            )

            raw_version = (
                row[
                    "reservation_version"
                ]
            )

            raw_side = row["side"]
            raw_status = row["status"]

        except Exception:
            return finish(
                UNKNOWN,
                "LIVE_BUY_RECOVERY_INDEX_ROW_INVALID",
            )

        if not reservation_id:
            return finish(
                UNKNOWN,
                "LIVE_BUY_RECOVERY_RESERVATION_ID_INVALID",
            )

        if (
            raw_version
            != RESERVATION_VERSION
        ):
            return finish(
                UNKNOWN,
                "LIVE_BUY_RECOVERY_RESERVATION_VERSION_MISMATCH",
                failed_reservation_id=(
                    reservation_id
                ),
            )

        if raw_side != BUY:
            return finish(
                UNKNOWN,
                "LIVE_BUY_RECOVERY_SIDE_INVALID",
                failed_reservation_id=(
                    reservation_id
                ),
            )

        if raw_status not in (
            SIGNED,
            RESERVATION_SUBMITTED,
        ):
            return finish(
                UNKNOWN,
                "LIVE_BUY_RECOVERY_STATUS_INVALID",
                failed_reservation_id=(
                    reservation_id
                ),
            )

        try:
            reservation = (
                load_capital_reservation_read_only(
                    reservation_id=(
                        reservation_id
                    ),
                    db_path=(
                        normalized_path
                    ),
                )
            )

        except Exception:
            return finish(
                UNKNOWN,
                "LIVE_BUY_RECOVERY_RESERVATION_LOAD_FAILED",
                failed_reservation_id=(
                    reservation_id
                ),
            )

        if not isinstance(
            reservation,
            LiveCapitalReservation,
        ):
            return finish(
                UNKNOWN,
                "LIVE_BUY_RECOVERY_RESERVATION_LOAD_FAILED",
                failed_reservation_id=(
                    reservation_id
                ),
            )

        if not _index_matches_reservation(
            row=row,
            reservation=reservation,
        ):
            return finish(
                UNKNOWN,
                "LIVE_BUY_RECOVERY_INDEX_CHANGED_DURING_DISCOVERY",
                failed_reservation_id=(
                    reservation_id
                ),
            )

        recovery_state = (
            _classify_recovery_state(
                reservation
            )
        )

        if recovery_state is None:
            return finish(
                UNKNOWN,
                "LIVE_BUY_RECOVERY_RESERVATION_INVALID",
                failed_reservation_id=(
                    reservation_id
                ),
            )

        recovery_reservation_rows += 1

        candidates.append(
            LiveBuyRecoveryCandidate(
                recovery_state=(
                    recovery_state
                ),
                reservation=(
                    reservation
                ),
            )
        )

    priority = {
        ARMED_SIGNED: 0,
        SUBMITTED: 1,
        PRISTINE_SIGNED: 2,
    }

    candidates.sort(
        key=lambda candidate: (
            priority[
                candidate.recovery_state
            ],
            float(
                candidate
                .reservation
                .signed_at
            ),
            candidate
            .reservation
            .reservation_id,
        )
    )

    return finish(
        PASS,
        candidates=tuple(
            candidates
        ),
    )
