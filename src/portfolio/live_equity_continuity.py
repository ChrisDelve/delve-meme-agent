from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import (
    date,
    datetime,
    timezone,
)
from pathlib import Path
from typing import Any

from solders.pubkey import Pubkey

from src.portfolio.live_reservations import (
    DB_PATH,
    SQLITE_INT_MAX,
    get_connection,
)
from src.portfolio.live_wallet_valuation import (
    LIVE_WALLET_VALUATION_VERSION,
)


LIVE_EQUITY_CONTINUITY_VERSION = (
    "live-equity-continuity-v1"
)

PASS = "PASS"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveEquityContinuityState:
    continuity_version: str

    wallet_pubkey: str

    day_key: str

    day_start_equity_lamports: int
    high_water_equity_lamports: int
    latest_equity_lamports: int

    latest_valuation_version: str
    latest_wallet_balance_rpc_slot: int

    latest_observed_at: float

    created_at: float
    updated_at: float


@dataclass(frozen=True)
class LiveEquityContinuityResult:
    status: str
    reasons: tuple[str, ...]

    state: LiveEquityContinuityState | None

    changed: bool


def _strict_nonnegative_int(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= SQLITE_INT_MAX
    )


def _strict_timestamp(
    value: Any,
) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def _nonempty_string(
    value: Any,
) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
    )


def _utc_day_key(
    timestamp: float,
) -> str:
    return (
        datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc,
        )
        .date()
        .isoformat()
    )


def _valid_day_key(
    value: Any,
) -> bool:
    if not _nonempty_string(
        value
    ):
        return False

    try:
        parsed = date.fromisoformat(
            value
        )

    except Exception:
        return False

    return (
        parsed.isoformat()
        == value
    )


def init_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        live_equity_continuity (
            wallet_pubkey TEXT PRIMARY KEY,

            continuity_version
                TEXT NOT NULL,

            day_key
                TEXT NOT NULL,

            day_start_equity_lamports
                INTEGER NOT NULL
                CHECK (
                    day_start_equity_lamports
                    >= 0
                ),

            high_water_equity_lamports
                INTEGER NOT NULL
                CHECK (
                    high_water_equity_lamports
                    >= 0
                ),

            latest_equity_lamports
                INTEGER NOT NULL
                CHECK (
                    latest_equity_lamports
                    >= 0
                ),

            latest_valuation_version
                TEXT NOT NULL,

            latest_wallet_balance_rpc_slot
                INTEGER NOT NULL
                CHECK (
                    latest_wallet_balance_rpc_slot
                    >= 0
                ),

            latest_observed_at
                REAL NOT NULL,

            created_at
                REAL NOT NULL,

            updated_at
                REAL NOT NULL,

            CHECK (
                high_water_equity_lamports
                >= latest_equity_lamports
            ),

            CHECK (
                high_water_equity_lamports
                >= day_start_equity_lamports
            )
        )
        """
    )


def _row_to_state(
    row: sqlite3.Row,
) -> LiveEquityContinuityState:
    continuity_version = str(
        row["continuity_version"]
    )

    wallet_pubkey = str(
        row["wallet_pubkey"]
    )

    day_key = str(
        row["day_key"]
    )

    day_start_equity = row[
        "day_start_equity_lamports"
    ]

    high_water_equity = row[
        "high_water_equity_lamports"
    ]

    latest_equity = row[
        "latest_equity_lamports"
    ]

    latest_valuation_version = str(
        row["latest_valuation_version"]
    )

    latest_rpc_slot = row[
        "latest_wallet_balance_rpc_slot"
    ]

    latest_observed_at = row[
        "latest_observed_at"
    ]

    created_at = row[
        "created_at"
    ]

    updated_at = row[
        "updated_at"
    ]

    if (
        continuity_version
        != LIVE_EQUITY_CONTINUITY_VERSION
    ):
        raise ValueError(
            "LIVE_EQUITY_CONTINUITY_VERSION_MISMATCH"
        )

    if not _nonempty_string(
        wallet_pubkey
    ):
        raise ValueError(
            "LIVE_EQUITY_WALLET_INVALID"
        )

    try:
        parsed_wallet = (
            Pubkey.from_string(
                wallet_pubkey
            )
        )

    except Exception as error:
        raise ValueError(
            "LIVE_EQUITY_WALLET_INVALID"
        ) from error

    if parsed_wallet == Pubkey.default():
        raise ValueError(
            "LIVE_EQUITY_WALLET_INVALID"
        )

    if not _valid_day_key(
        day_key
    ):
        raise ValueError(
            "LIVE_EQUITY_DAY_KEY_INVALID"
        )

    for name, value in (
        (
            "day_start_equity",
            day_start_equity,
        ),
        (
            "high_water_equity",
            high_water_equity,
        ),
        (
            "latest_equity",
            latest_equity,
        ),
        (
            "latest_rpc_slot",
            latest_rpc_slot,
        ),
    ):
        if not _strict_nonnegative_int(
            value
        ):
            raise ValueError(
                "LIVE_EQUITY_INTEGER_INVALID:"
                + name
            )

    if (
        high_water_equity
        < latest_equity
        or high_water_equity
        < day_start_equity
    ):
        raise ValueError(
            "LIVE_EQUITY_HIGH_WATER_INVALID"
        )

    if not _nonempty_string(
        latest_valuation_version
    ):
        raise ValueError(
            "LIVE_EQUITY_VALUATION_VERSION_INVALID"
        )

    for name, value in (
        (
            "latest_observed_at",
            latest_observed_at,
        ),
        (
            "created_at",
            created_at,
        ),
        (
            "updated_at",
            updated_at,
        ),
    ):
        if not _strict_timestamp(
            value
        ):
            raise ValueError(
                "LIVE_EQUITY_TIMESTAMP_INVALID:"
                + name
            )

    return LiveEquityContinuityState(
        continuity_version=(
            continuity_version
        ),
        wallet_pubkey=wallet_pubkey,
        day_key=day_key,
        day_start_equity_lamports=int(
            day_start_equity
        ),
        high_water_equity_lamports=int(
            high_water_equity
        ),
        latest_equity_lamports=int(
            latest_equity
        ),
        latest_valuation_version=(
            latest_valuation_version
        ),
        latest_wallet_balance_rpc_slot=int(
            latest_rpc_slot
        ),
        latest_observed_at=float(
            latest_observed_at
        ),
        created_at=float(
            created_at
        ),
        updated_at=float(
            updated_at
        ),
    )


def record_live_equity_continuity(
    *,
    wallet_pubkey: str,
    valuation_version: str,
    current_equity_lamports: int,
    wallet_balance_rpc_slot: int,
    observed_at: float | None = None,
    db_path: Path = DB_PATH,
) -> LiveEquityContinuityResult:
    """
    Atomically record one proven live-wallet equity
    observation and maintain UTC day-start and lifetime
    high-water continuity.

    This function performs no:
    - wallet valuation
    - RPC
    - transaction construction
    - signing
    - transaction submission
    - position accounting
    - reservation accounting
    - risk-governor decision

    The caller must supply a successfully resolved
    live-wallet valuation.
    """

    def finish(
        status: str,
        *reasons: str,
        state: (
            LiveEquityContinuityState
            | None
        ) = None,
        changed: bool = False,
    ) -> LiveEquityContinuityResult:
        return LiveEquityContinuityResult(
            status=status,
            reasons=tuple(reasons),
            state=state,
            changed=changed,
        )

    # --------------------------------------------------------
    # Input contract before opening/creating the database.
    # --------------------------------------------------------

    if not isinstance(
        wallet_pubkey,
        str,
    ):
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    wallet_pubkey = (
        wallet_pubkey.strip()
    )

    if not wallet_pubkey:
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    try:
        parsed_wallet = (
            Pubkey.from_string(
                wallet_pubkey
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    if parsed_wallet == Pubkey.default():
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    normalized_wallet = str(
        parsed_wallet
    )

    if (
        valuation_version
        != LIVE_WALLET_VALUATION_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIVE_WALLET_VALUATION_VERSION_MISMATCH",
        )

    if not _strict_nonnegative_int(
        current_equity_lamports
    ):
        return finish(
            UNKNOWN,
            "INVALID_CURRENT_EQUITY",
        )

    if not _strict_nonnegative_int(
        wallet_balance_rpc_slot
    ):
        return finish(
            UNKNOWN,
            "INVALID_WALLET_BALANCE_RPC_SLOT",
        )

    if observed_at is None:
        observed_at = time.time()

    if not _strict_timestamp(
        observed_at
    ):
        return finish(
            UNKNOWN,
            "INVALID_OBSERVED_AT",
        )

    observed_at = float(
        observed_at
    )

    try:
        current_day_key = (
            _utc_day_key(
                observed_at
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "INVALID_OBSERVED_AT",
        )

    write_time = time.time()

    if not _strict_timestamp(
        write_time
    ):
        return finish(
            UNKNOWN,
            "SYSTEM_TIME_INVALID",
        )

    # --------------------------------------------------------
    # Atomic continuity transition.
    # --------------------------------------------------------

    try:
        connection = get_connection(
            db_path
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_EQUITY_DATABASE_OPEN_FAILED",
        )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        init_schema(
            connection
        )

        row = connection.execute(
            """
            SELECT *
            FROM live_equity_continuity
            WHERE wallet_pubkey = ?
            """,
            (
                normalized_wallet,
            ),
        ).fetchone()

        if row is None:
            connection.execute(
                """
                INSERT INTO live_equity_continuity (
                    wallet_pubkey,
                    continuity_version,
                    day_key,
                    day_start_equity_lamports,
                    high_water_equity_lamports,
                    latest_equity_lamports,
                    latest_valuation_version,
                    latest_wallet_balance_rpc_slot,
                    latest_observed_at,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
                """,
                (
                    normalized_wallet,
                    LIVE_EQUITY_CONTINUITY_VERSION,
                    current_day_key,
                    current_equity_lamports,
                    current_equity_lamports,
                    current_equity_lamports,
                    valuation_version,
                    wallet_balance_rpc_slot,
                    observed_at,
                    write_time,
                    write_time,
                ),
            )

            created_row = connection.execute(
                """
                SELECT *
                FROM live_equity_continuity
                WHERE wallet_pubkey = ?
                """,
                (
                    normalized_wallet,
                ),
            ).fetchone()

            if created_row is None:
                connection.rollback()

                return finish(
                    UNKNOWN,
                    "LIVE_EQUITY_INSERT_VERIFY_FAILED",
                )

            try:
                state = _row_to_state(
                    created_row
                )

            except Exception:
                connection.rollback()

                return finish(
                    UNKNOWN,
                    "LIVE_EQUITY_INSERT_VERIFY_FAILED",
                )

            connection.commit()

            return finish(
                PASS,
                state=state,
                changed=True,
            )

        try:
            previous = _row_to_state(
                row
            )

        except ValueError as error:
            connection.rollback()

            return finish(
                UNKNOWN,
                str(error),
            )

        if (
            previous.wallet_pubkey
            != normalized_wallet
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                "LIVE_EQUITY_WALLET_MISMATCH",
            )

        # ----------------------------------------------------
        # Reject stale observations.
        #
        # A stale observation must never roll account
        # continuity backward.
        # ----------------------------------------------------

        if (
            observed_at
            < previous.latest_observed_at
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                "STALE_EQUITY_OBSERVATION_TIME",
                state=previous,
            )

        if (
            wallet_balance_rpc_slot
            < previous.latest_wallet_balance_rpc_slot
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                "STALE_WALLET_BALANCE_RPC_SLOT",
                state=previous,
            )

        new_day_start = (
            previous.day_start_equity_lamports
        )

        if (
            current_day_key
            != previous.day_key
        ):
            new_day_start = (
                current_equity_lamports
            )

        new_high_water = max(
            previous.high_water_equity_lamports,
            current_equity_lamports,
        )

        exact_replay = (
            current_day_key
            == previous.day_key
            and current_equity_lamports
            == previous.latest_equity_lamports
            and valuation_version
            == previous.latest_valuation_version
            and wallet_balance_rpc_slot
            == previous.latest_wallet_balance_rpc_slot
            and observed_at
            == previous.latest_observed_at
            and new_day_start
            == previous.day_start_equity_lamports
            and new_high_water
            == previous.high_water_equity_lamports
        )

        if exact_replay:
            connection.commit()

            return finish(
                PASS,
                state=previous,
                changed=False,
            )

        connection.execute(
            """
            UPDATE live_equity_continuity

            SET
                day_key = ?,
                day_start_equity_lamports = ?,
                high_water_equity_lamports = ?,
                latest_equity_lamports = ?,
                latest_valuation_version = ?,
                latest_wallet_balance_rpc_slot = ?,
                latest_observed_at = ?,
                updated_at = ?

            WHERE wallet_pubkey = ?
            """,
            (
                current_day_key,
                new_day_start,
                new_high_water,
                current_equity_lamports,
                valuation_version,
                wallet_balance_rpc_slot,
                observed_at,
                write_time,
                normalized_wallet,
            ),
        )

        updated_row = connection.execute(
            """
            SELECT *
            FROM live_equity_continuity
            WHERE wallet_pubkey = ?
            """,
            (
                normalized_wallet,
            ),
        ).fetchone()

        if updated_row is None:
            connection.rollback()

            return finish(
                UNKNOWN,
                "LIVE_EQUITY_UPDATE_VERIFY_FAILED",
            )

        try:
            state = _row_to_state(
                updated_row
            )

        except Exception:
            connection.rollback()

            return finish(
                UNKNOWN,
                "LIVE_EQUITY_UPDATE_VERIFY_FAILED",
            )

        if (
            state.day_key
            != current_day_key
            or state.day_start_equity_lamports
            != new_day_start
            or state.high_water_equity_lamports
            != new_high_water
            or state.latest_equity_lamports
            != current_equity_lamports
            or state.latest_valuation_version
            != valuation_version
            or state.latest_wallet_balance_rpc_slot
            != wallet_balance_rpc_slot
            or state.latest_observed_at
            != observed_at
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                "LIVE_EQUITY_UPDATE_VERIFY_FAILED",
            )

        connection.commit()

        return finish(
            PASS,
            state=state,
            changed=True,
        )

    except sqlite3.Error:
        connection.rollback()

        return finish(
            UNKNOWN,
            "LIVE_EQUITY_DATABASE_WRITE_FAILED",
        )

    except Exception:
        connection.rollback()

        return finish(
            UNKNOWN,
            "LIVE_EQUITY_CONTINUITY_FAILED",
        )

    finally:
        connection.close()
