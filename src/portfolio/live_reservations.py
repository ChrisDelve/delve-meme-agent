from __future__ import annotations

import hashlib

import math
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from solders.hash import Hash
from solders.pubkey import Pubkey

from src.execution.simulation_fingerprint import (
    simulation_fingerprint,
)

from src.execution.pump_execution_simulator import (
    PumpCurveState,
)
from src.risk.risk_governor import (
    AccountRiskState,
    RiskGovernorResult,
    RiskPolicy,
    UNKNOWN,
    evaluate_risk,
)


DB_PATH = Path("logs/delve_live.db")

RESERVATION_VERSION = "live-capital-reservation-v6"

ACTIVE = "ACTIVE"
SIGNED = "SIGNED"
SUBMITTED = "SUBMITTED"
RELEASED = "RELEASED"
EXPIRED = "EXPIRED"

BUY = "BUY"

SQLITE_INT_MAX = (1 << 63) - 1


@dataclass(frozen=True)
class LiveCapitalReservation:
    reservation_id: str
    reservation_version: str

    mint: str
    side: str
    wallet_pubkey: str

    spend_lamports: int
    wallet_cost_lamports: int
    status: str

    risk_governor_version: str
    risk_simulation_sha256: str

    base_available_cash_lamports: int
    base_open_exposure_lamports: int
    base_open_positions: int

    reserved_exposure_before_lamports: int
    reserved_cash_before_lamports: int
    active_reservations_before: int

    created_at: float
    expires_at: float

    signed_at: float | None
    transaction_signature: str | None

    signed_message_sha256: str | None = None
    signed_transaction_sha256: str | None = None
    signed_transaction_bytes: bytes | None = None

    recent_blockhash: str | None = None
    last_valid_block_height: int | None = None
    blockhash_rpc_slot: int | None = None

    # Durable pre-send boundary.
    #
    # submission_started_at means the transaction
    # MAY have been relayed. It does not prove that
    # an RPC call occurred or was accepted.
    submission_started_at: float | None = None
    submission_attempt_count: int = 0

    # Set only after the RPC returns the exact
    # already-persisted transaction signature.
    submitted_at: float | None = None


@dataclass(frozen=True)
class ReservationTransitionResult:
    status: str
    reasons: tuple[str, ...]

    reservation: LiveCapitalReservation | None
    changed: bool


@dataclass(frozen=True)
class ReservationDecision:
    status: str
    reasons: tuple[str, ...]

    reservation: LiveCapitalReservation | None
    risk_result: RiskGovernorResult | None


def get_connection(
    db_path: Path = DB_PATH,
) -> sqlite3.Connection:
    db_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        db_path,
        timeout=30.0,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA busy_timeout = 30000"
    )

    #
    # Live-capital authority state is intentionally
    # isolated from the research/shadow database.
    #
    # Journal mode is not changed here. Changing it
    # is database-wide and can race when concurrent
    # reservation connections open a fresh database.
    #
    # FULL synchronous favors durability over
    # throughput for capital-control state.
    #
    connection.execute(
        "PRAGMA synchronous = FULL"
    )

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    return connection


def init_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        live_capital_reservations (
            reservation_id TEXT PRIMARY KEY,
            reservation_version TEXT NOT NULL,

            mint TEXT NOT NULL,
            side TEXT NOT NULL,
            wallet_pubkey TEXT NOT NULL,

            spend_lamports INTEGER NOT NULL
                CHECK (spend_lamports > 0),

            wallet_cost_lamports INTEGER NOT NULL
                CHECK (wallet_cost_lamports > 0),

            status TEXT NOT NULL,

            risk_governor_version TEXT NOT NULL,

            risk_simulation_sha256 TEXT NOT NULL,

            base_available_cash_lamports
                INTEGER NOT NULL,

            base_open_exposure_lamports
                INTEGER NOT NULL,

            base_open_positions
                INTEGER NOT NULL,

            reserved_exposure_before_lamports
                INTEGER NOT NULL,

            reserved_cash_before_lamports
                INTEGER NOT NULL,

            active_reservations_before
                INTEGER NOT NULL,

            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,

            signed_at REAL,
            transaction_signature TEXT,
            signed_message_sha256 TEXT,
            signed_transaction_sha256 TEXT,
            signed_transaction_bytes BLOB,

            recent_blockhash TEXT,
            last_valid_block_height INTEGER,
            blockhash_rpc_slot INTEGER,

            submission_started_at REAL,
            submission_attempt_count
                INTEGER NOT NULL DEFAULT 0,
            submitted_at REAL,

            terminal_at REAL,
            terminal_reason TEXT
        )
        """
    )

    reservation_columns = {
        str(row["name"])
        for row in connection.execute(
            """
            PRAGMA table_info(
                live_capital_reservations
            )
            """
        ).fetchall()
    }

    if (
        "wallet_pubkey"
        not in reservation_columns
    ):
        connection.execute(
            """
            ALTER TABLE live_capital_reservations
            ADD COLUMN wallet_pubkey TEXT
            """
        )

    if (
        "risk_simulation_sha256"
        not in reservation_columns
    ):
        connection.execute(
            """
            ALTER TABLE live_capital_reservations
            ADD COLUMN risk_simulation_sha256 TEXT
            """
        )

    if "signed_at" not in reservation_columns:
        connection.execute(
            """
            ALTER TABLE live_capital_reservations
            ADD COLUMN signed_at REAL
            """
        )

    if (
        "transaction_signature"
        not in reservation_columns
    ):
        connection.execute(
            """
            ALTER TABLE live_capital_reservations
            ADD COLUMN transaction_signature TEXT
            """
        )

    if (
        "signed_message_sha256"
        not in reservation_columns
    ):
        connection.execute(
            """
            ALTER TABLE live_capital_reservations
            ADD COLUMN signed_message_sha256 TEXT
            """
        )

    if (
        "signed_transaction_sha256"
        not in reservation_columns
    ):
        connection.execute(
            """
            ALTER TABLE live_capital_reservations
            ADD COLUMN signed_transaction_sha256 TEXT
            """
        )

    if (
        "signed_transaction_bytes"
        not in reservation_columns
    ):
        connection.execute(
            """
            ALTER TABLE live_capital_reservations
            ADD COLUMN signed_transaction_bytes BLOB
            """
        )

    if (
        "recent_blockhash"
        not in reservation_columns
    ):
        connection.execute(
            """
            ALTER TABLE live_capital_reservations
            ADD COLUMN recent_blockhash TEXT
            """
        )

    if (
        "last_valid_block_height"
        not in reservation_columns
    ):
        connection.execute(
            """
            ALTER TABLE live_capital_reservations
            ADD COLUMN last_valid_block_height INTEGER
            """
        )

    if (
        "blockhash_rpc_slot"
        not in reservation_columns
    ):
        connection.execute(
            """
            ALTER TABLE live_capital_reservations
            ADD COLUMN blockhash_rpc_slot INTEGER
            """
        )

    if (
        "submission_started_at"
        not in reservation_columns
    ):
        connection.execute(
            """
            ALTER TABLE live_capital_reservations
            ADD COLUMN submission_started_at REAL
            """
        )

    if (
        "submission_attempt_count"
        not in reservation_columns
    ):
        connection.execute(
            """
            ALTER TABLE live_capital_reservations
            ADD COLUMN submission_attempt_count
                INTEGER NOT NULL DEFAULT 0
            """
        )

    if (
        "submitted_at"
        not in reservation_columns
    ):
        connection.execute(
            """
            ALTER TABLE live_capital_reservations
            ADD COLUMN submitted_at REAL
            """
        )

    connection.execute(
        """
        DROP INDEX IF EXISTS
        live_capital_reservations_held_mint
        """
    )

    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        live_capital_reservations_held_mint_v2

        ON live_capital_reservations (mint)

        WHERE status IN (
            'ACTIVE',
            'SIGNED',
            'SUBMITTED'
        )
        """
    )

    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        live_capital_reservations_transaction_signature

        ON live_capital_reservations (
            transaction_signature
        )

        WHERE transaction_signature IS NOT NULL
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        live_capital_reservations_status_expiry
        ON live_capital_reservations (
            status,
            expires_at
        )
        """
    )


def expire_active_reservations(
    connection: sqlite3.Connection,
    *,
    now: float,
) -> int:
    updated = connection.execute(
        """
        UPDATE live_capital_reservations

        SET
            status = ?,
            terminal_at = ?,
            terminal_reason = 'TTL_EXPIRED'

        WHERE status = ?
          AND expires_at <= ?
        """,
        (
            EXPIRED,
            now,
            ACTIVE,
            now,
        ),
    )

    return int(
        updated.rowcount
    )


def held_reservation_totals(
    connection: sqlite3.Connection,
) -> tuple[int, int, int]:
    row = connection.execute(
        """
        SELECT
            COALESCE(
                SUM(spend_lamports),
                0
            ) AS reserved_exposure,

            COALESCE(
                SUM(wallet_cost_lamports),
                0
            ) AS reserved_cash,

            COUNT(*) AS reservation_count

        FROM live_capital_reservations

        WHERE status IN (?, ?, ?)
        """,
        (
            ACTIVE,
            SIGNED,
            SUBMITTED,
        ),
    ).fetchone()

    return (
        int(row["reserved_exposure"]),
        int(row["reserved_cash"]),
        int(row["reservation_count"]),
    )


def reserve_pump_buy_capital(
    *,
    mint: str,
    wallet_pubkey: str,
    available_cash_lamports: int,
    account: AccountRiskState,
    curve_state: PumpCurveState,
    protocol_fee_bps: int,
    creator_fee_bps: int,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    rent_lamports: int,
    reservation_ttl_seconds: float,
    policy: RiskPolicy | None = None,
    db_path: Path = DB_PATH,
) -> ReservationDecision:
    """
    Atomically reserve capital for one proposed
    live Pump bonding-curve BUY.

    `account` must represent live account/position
    state BEFORE local ACTIVE reservations. This
    function adds those reservations into exposure
    before rerunning the risk governor.

    No wallet access, transaction construction,
    signing, or submission occurs here.
    """

    mint = mint.strip()

    if not mint:
        return ReservationDecision(
            status=UNKNOWN,
            reasons=("INVALID_MINT",),
            reservation=None,
            risk_result=None,
        )

    wallet_pubkey = wallet_pubkey.strip()

    if not wallet_pubkey:
        return ReservationDecision(
            status=UNKNOWN,
            reasons=("INVALID_WALLET_PUBKEY",),
            reservation=None,
            risk_result=None,
        )

    try:
        parsed_wallet_pubkey = (
            Pubkey.from_string(
                wallet_pubkey
            )
        )

    except Exception:
        return ReservationDecision(
            status=UNKNOWN,
            reasons=("INVALID_WALLET_PUBKEY",),
            reservation=None,
            risk_result=None,
        )

    if parsed_wallet_pubkey == Pubkey.default():
        return ReservationDecision(
            status=UNKNOWN,
            reasons=("INVALID_WALLET_PUBKEY",),
            reservation=None,
            risk_result=None,
        )

    wallet_pubkey = str(
        parsed_wallet_pubkey
    )

    if available_cash_lamports < 0:
        return ReservationDecision(
            status=UNKNOWN,
            reasons=(
                "INVALID_AVAILABLE_CASH",
            ),
            reservation=None,
            risk_result=None,
        )

    if (
        not math.isfinite(
            reservation_ttl_seconds
        )
        or reservation_ttl_seconds <= 0
    ):
        return ReservationDecision(
            status=UNKNOWN,
            reasons=(
                "INVALID_RESERVATION_TTL",
            ),
            reservation=None,
            risk_result=None,
        )

    now = time.time()

    connection = get_connection(
        db_path
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        init_schema(
            connection
        )

        expire_active_reservations(
            connection,
            now=now,
        )

        duplicate = connection.execute(
            """
            SELECT reservation_id

            FROM live_capital_reservations

            WHERE mint = ?
              AND status IN (?, ?, ?)

            LIMIT 1
            """,
            (
                mint,
                ACTIVE,
                SIGNED,
                SUBMITTED,
            ),
        ).fetchone()

        if duplicate is not None:
            connection.commit()

            return ReservationDecision(
                status="BLOCK",
                reasons=(
                    "ACTIVE_RESERVATION_ALREADY_EXISTS_FOR_MINT",
                ),
                reservation=None,
                risk_result=None,
            )

        (
            reserved_exposure,
            reserved_cash,
            reservation_count,
        ) = held_reservation_totals(
            connection
        )

        effective_account = AccountRiskState(
            current_equity_lamports=(
                account.current_equity_lamports
            ),
            day_start_equity_lamports=(
                account.day_start_equity_lamports
            ),
            high_water_equity_lamports=(
                account.high_water_equity_lamports
            ),
            open_exposure_lamports=(
                account.open_exposure_lamports
                + reserved_exposure
            ),
            open_positions=(
                account.open_positions
                + reservation_count
            ),
        )

        risk = evaluate_risk(
            account=effective_account,
            curve_state=curve_state,
            protocol_fee_bps=protocol_fee_bps,
            creator_fee_bps=creator_fee_bps,
            slippage_bps=slippage_bps,
            base_network_fee_lamports=(
                base_network_fee_lamports
            ),
            priority_fee_lamports=(
                priority_fee_lamports
            ),
            rent_lamports=rent_lamports,
            policy=policy,
        )

        if not risk.allows_new_position:
            connection.commit()

            return ReservationDecision(
                status=risk.status,
                reasons=tuple(
                    risk.reasons
                ),
                reservation=None,
                risk_result=risk,
            )

        if risk.recommended_simulation is None:
            connection.rollback()

            return ReservationDecision(
                status=UNKNOWN,
                reasons=(
                    "RISK_SIMULATION_MISSING",
                ),
                reservation=None,
                risk_result=risk,
            )

        try:
            risk_simulation_sha256 = (
                simulation_fingerprint(
                    risk.recommended_simulation
                )
            )

        except Exception:
            connection.rollback()

            return ReservationDecision(
                status=UNKNOWN,
                reasons=(
                    "RISK_SIMULATION_FINGERPRINT_FAILED",
                ),
                reservation=None,
                risk_result=risk,
            )

        spend_lamports = int(
            risk.recommended_spend_lamports
        )

        transaction_overhead_lamports = int(
            risk.recommended_simulation
            .total_transaction_overhead_lamports
        )

        wallet_cost_lamports = (
            spend_lamports
            + transaction_overhead_lamports
        )

        if (
            spend_lamports <= 0
            or transaction_overhead_lamports < 0
            or wallet_cost_lamports <= 0
        ):
            connection.rollback()

            return ReservationDecision(
                status=UNKNOWN,
                reasons=(
                    "INVALID_WALLET_COST",
                ),
                reservation=None,
                risk_result=risk,
            )

        unreserved_cash = max(
            0,
            (
                available_cash_lamports
                - reserved_cash
            ),
        )

        if (
            wallet_cost_lamports
            > unreserved_cash
        ):
            connection.commit()

            return ReservationDecision(
                status="BLOCK",
                reasons=(
                    "INSUFFICIENT_UNRESERVED_CASH",
                ),
                reservation=None,
                risk_result=risk,
            )

        reservation = LiveCapitalReservation(
            reservation_id=uuid.uuid4().hex,
            reservation_version=(
                RESERVATION_VERSION
            ),
            mint=mint,
            side=BUY,
            wallet_pubkey=wallet_pubkey,
            spend_lamports=spend_lamports,
            wallet_cost_lamports=(
                wallet_cost_lamports
            ),
            status=ACTIVE,
            risk_governor_version=(
                risk.governor_version
            ),
            risk_simulation_sha256=(
                risk_simulation_sha256
            ),
            base_available_cash_lamports=(
                available_cash_lamports
            ),
            base_open_exposure_lamports=(
                account.open_exposure_lamports
            ),
            base_open_positions=(
                account.open_positions
            ),
            reserved_exposure_before_lamports=(
                reserved_exposure
            ),
            reserved_cash_before_lamports=(
                reserved_cash
            ),
            active_reservations_before=(
                reservation_count
            ),
            created_at=now,
            expires_at=(
                now
                + reservation_ttl_seconds
            ),
            signed_at=None,
            transaction_signature=None,
            signed_message_sha256=None,
            signed_transaction_sha256=None,
            signed_transaction_bytes=None,
            recent_blockhash=None,
            last_valid_block_height=None,
            blockhash_rpc_slot=None,
            submission_started_at=None,
            submission_attempt_count=0,
            submitted_at=None,
        )

        connection.execute(
            """
            INSERT INTO live_capital_reservations (
                reservation_id,
                reservation_version,
                mint,
                side,
                wallet_pubkey,
                spend_lamports,
                wallet_cost_lamports,
                status,
                risk_governor_version,
                risk_simulation_sha256,
                base_available_cash_lamports,
                base_open_exposure_lamports,
                base_open_positions,
                reserved_exposure_before_lamports,
                reserved_cash_before_lamports,
                active_reservations_before,
                created_at,
                expires_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                reservation.reservation_id,
                reservation.reservation_version,
                reservation.mint,
                reservation.side,
                reservation.wallet_pubkey,
                reservation.spend_lamports,
                reservation.wallet_cost_lamports,
                reservation.status,
                reservation.risk_governor_version,
                reservation.risk_simulation_sha256,
                reservation.base_available_cash_lamports,
                reservation.base_open_exposure_lamports,
                reservation.base_open_positions,
                reservation.reserved_exposure_before_lamports,
                reservation.reserved_cash_before_lamports,
                reservation.active_reservations_before,
                reservation.created_at,
                reservation.expires_at,
            ),
        )

        connection.commit()

        return ReservationDecision(
            status="PASS",
            reasons=(),
            reservation=reservation,
            risk_result=risk,
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def _row_to_reservation(
    row: sqlite3.Row,
) -> LiveCapitalReservation:
    return LiveCapitalReservation(
        reservation_id=str(
            row["reservation_id"]
        ),
        reservation_version=str(
            row["reservation_version"]
        ),
        mint=str(row["mint"]),
        side=str(row["side"]),
        wallet_pubkey=(
            ""
            if row["wallet_pubkey"] is None
            else str(row["wallet_pubkey"])
        ),
        spend_lamports=int(
            row["spend_lamports"]
        ),
        wallet_cost_lamports=int(
            row["wallet_cost_lamports"]
        ),
        status=str(row["status"]),
        risk_governor_version=str(
            row["risk_governor_version"]
        ),
        risk_simulation_sha256=str(
            row["risk_simulation_sha256"]
        ),
        base_available_cash_lamports=int(
            row[
                "base_available_cash_lamports"
            ]
        ),
        base_open_exposure_lamports=int(
            row[
                "base_open_exposure_lamports"
            ]
        ),
        base_open_positions=int(
            row["base_open_positions"]
        ),
        reserved_exposure_before_lamports=int(
            row[
                "reserved_exposure_before_lamports"
            ]
        ),
        reserved_cash_before_lamports=int(
            row[
                "reserved_cash_before_lamports"
            ]
        ),
        active_reservations_before=int(
            row["active_reservations_before"]
        ),
        created_at=float(
            row["created_at"]
        ),
        expires_at=float(
            row["expires_at"]
        ),
        signed_at=(
            None
            if row["signed_at"] is None
            else float(row["signed_at"])
        ),
        transaction_signature=(
            None
            if row["transaction_signature"] is None
            else str(
                row["transaction_signature"]
            )
        ),
        signed_message_sha256=(
            None
            if row["signed_message_sha256"] is None
            else str(
                row["signed_message_sha256"]
            )
        ),
        signed_transaction_sha256=(
            None
            if row["signed_transaction_sha256"] is None
            else str(
                row["signed_transaction_sha256"]
            )
        ),
        signed_transaction_bytes=(
            None
            if row["signed_transaction_bytes"] is None
            else bytes(
                row["signed_transaction_bytes"]
            )
        ),
        recent_blockhash=(
            None
            if row["recent_blockhash"] is None
            else str(
                row["recent_blockhash"]
            )
        ),
        last_valid_block_height=(
            None
            if row["last_valid_block_height"] is None
            else int(
                row["last_valid_block_height"]
            )
        ),
        blockhash_rpc_slot=(
            None
            if row["blockhash_rpc_slot"] is None
            else int(
                row["blockhash_rpc_slot"]
            )
        ),
        submission_started_at=(
            None
            if (
                "submission_started_at"
                not in row.keys()
                or row[
                    "submission_started_at"
                ]
                is None
            )
            else float(
                row[
                    "submission_started_at"
                ]
            )
        ),
        submission_attempt_count=(
            0
            if (
                "submission_attempt_count"
                not in row.keys()
                or row[
                    "submission_attempt_count"
                ]
                is None
            )
            else int(
                row[
                    "submission_attempt_count"
                ]
            )
        ),
        submitted_at=(
            None
            if (
                "submitted_at"
                not in row.keys()
                or row["submitted_at"]
                is None
            )
            else float(
                row["submitted_at"]
            )
        ),
    )


def load_capital_reservation(
    *,
    reservation_id: str,
    now: float | None = None,
    db_path: Path = DB_PATH,
) -> LiveCapitalReservation | None:
    """
    Return the current authoritative reservation
    state from the live-capital ledger.

    ACTIVE reservations whose TTL has elapsed are
    expired atomically before the row is returned.
    """

    reservation_id = reservation_id.strip()

    if not reservation_id:
        return None

    if now is None:
        now = time.time()

    connection = get_connection(
        db_path
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        init_schema(
            connection
        )

        expire_active_reservations(
            connection,
            now=now,
        )

        row = connection.execute(
            """
            SELECT *

            FROM live_capital_reservations

            WHERE reservation_id = ?
            """,
            (
                reservation_id,
            ),
        ).fetchone()

        connection.commit()

        if row is None:
            return None

        return _row_to_reservation(
            row
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def load_capital_reservation_read_only(
    *,
    reservation_id: str,
    db_path: Path = DB_PATH,
) -> LiveCapitalReservation | None:
    """
    Read one reservation without mutating the
    live-capital ledger.

    This loader intentionally performs:
    - no schema migration
    - no TTL expiration
    - no reservation transition
    - no write-capable database open

    It is for observational recovery/status paths,
    not capital-authority decisions.
    """

    reservation_id = reservation_id.strip()

    if not reservation_id:
        return None

    if not db_path.exists():
        return None

    database_uri = (
        db_path.resolve().as_uri()
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

    try:
        row = connection.execute(
            """
            SELECT *
            FROM live_capital_reservations
            WHERE reservation_id = ?
            """,
            (
                reservation_id,
            ),
        ).fetchone()

        if row is None:
            return None

        return _row_to_reservation(
            row
        )

    finally:
        connection.close()


def bind_reservation_signed_transaction(
    *,
    reservation_id: str,
    transaction_signature: str,
    signed_message_sha256: str,
    signed_transaction_bytes: bytes,
    recent_blockhash: str,
    last_valid_block_height: int,
    blockhash_rpc_slot: int,
    db_path: Path = DB_PATH,
) -> ReservationTransitionResult:
    """
    Atomically bind an ACTIVE reservation to a
    signed transaction BEFORE network submission.

    Repeating the same transition with the exact
    same signed artifact is idempotent.

    SIGNED means the exact transaction required for
    crash recovery has been durably persisted.

    SIGNED reservations remain held and are not
    automatically expired by reservation TTL.
    """

    reservation_id = reservation_id.strip()
    transaction_signature = (
        transaction_signature.strip()
    )

    if not reservation_id:
        return ReservationTransitionResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_RESERVATION_ID",
            ),
            reservation=None,
            changed=False,
        )

    if not transaction_signature:
        return ReservationTransitionResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_TRANSACTION_SIGNATURE",
            ),
            reservation=None,
            changed=False,
        )

    if not isinstance(
        signed_message_sha256,
        str,
    ):
        return ReservationTransitionResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_SIGNED_MESSAGE_SHA256",
            ),
            reservation=None,
            changed=False,
        )

    signed_message_sha256 = (
        signed_message_sha256
        .strip()
        .lower()
    )

    if (
        len(signed_message_sha256) != 64
        or any(
            character
            not in "0123456789abcdef"
            for character
            in signed_message_sha256
        )
    ):
        return ReservationTransitionResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_SIGNED_MESSAGE_SHA256",
            ),
            reservation=None,
            changed=False,
        )

    if (
        not isinstance(
            signed_transaction_bytes,
            bytes,
        )
        or not signed_transaction_bytes
    ):
        return ReservationTransitionResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_SIGNED_TRANSACTION_BYTES",
            ),
            reservation=None,
            changed=False,
        )

    if not isinstance(
        recent_blockhash,
        str,
    ):
        return ReservationTransitionResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_RECENT_BLOCKHASH",
            ),
            reservation=None,
            changed=False,
        )

    recent_blockhash = (
        recent_blockhash.strip()
    )

    try:
        parsed_recent_blockhash = (
            Hash.from_string(
                recent_blockhash
            )
        )
    except Exception:
        return ReservationTransitionResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_RECENT_BLOCKHASH",
            ),
            reservation=None,
            changed=False,
        )

    if (
        not recent_blockhash
        or parsed_recent_blockhash
        == Hash.default()
    ):
        return ReservationTransitionResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_RECENT_BLOCKHASH",
            ),
            reservation=None,
            changed=False,
        )

    recent_blockhash = str(
        parsed_recent_blockhash
    )

    if (
        not isinstance(
            last_valid_block_height,
            int,
        )
        or isinstance(
            last_valid_block_height,
            bool,
        )
        or last_valid_block_height < 0
        or last_valid_block_height
        > SQLITE_INT_MAX
    ):
        return ReservationTransitionResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_LAST_VALID_BLOCK_HEIGHT",
            ),
            reservation=None,
            changed=False,
        )

    if (
        not isinstance(
            blockhash_rpc_slot,
            int,
        )
        or isinstance(
            blockhash_rpc_slot,
            bool,
        )
        or blockhash_rpc_slot < 0
        or blockhash_rpc_slot
        > SQLITE_INT_MAX
    ):
        return ReservationTransitionResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_BLOCKHASH_RPC_SLOT",
            ),
            reservation=None,
            changed=False,
        )

    signed_transaction_sha256 = (
        hashlib.sha256(
            signed_transaction_bytes
        ).hexdigest()
    )

    now = time.time()
    connection = get_connection(db_path)

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        init_schema(connection)

        expire_active_reservations(
            connection,
            now=now,
        )

        row = connection.execute(
            """
            SELECT *
            FROM live_capital_reservations
            WHERE reservation_id = ?
            """,
            (
                reservation_id,
            ),
        ).fetchone()

        if row is None:
            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "RESERVATION_NOT_FOUND",
                ),
                reservation=None,
                changed=False,
            )

        reservation = _row_to_reservation(
            row
        )

        if (
            reservation.reservation_version
            != RESERVATION_VERSION
        ):
            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "RESERVATION_VERSION_MISMATCH",
                ),
                reservation=reservation,
                changed=False,
            )

        signature_owner = connection.execute(
            """
            SELECT reservation_id

            FROM live_capital_reservations

            WHERE transaction_signature = ?

            LIMIT 1
            """,
            (
                transaction_signature,
            ),
        ).fetchone()

        if (
            signature_owner is not None
            and str(
                signature_owner[
                    "reservation_id"
                ]
            )
            != reservation_id
        ):
            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "TRANSACTION_SIGNATURE_ALREADY_BOUND",
                ),
                reservation=reservation,
                changed=False,
            )

        if reservation.status == SIGNED:
            if (
                reservation.transaction_signature
                == transaction_signature
            ):
                if (
                    reservation.signed_message_sha256
                    == signed_message_sha256
                    and
                    reservation.signed_transaction_sha256
                    == signed_transaction_sha256
                    and
                    reservation.signed_transaction_bytes
                    == signed_transaction_bytes
                    and
                    reservation.recent_blockhash
                    == recent_blockhash
                    and
                    reservation.last_valid_block_height
                    == last_valid_block_height
                    and
                    reservation.blockhash_rpc_slot
                    == blockhash_rpc_slot
                ):
                    connection.commit()

                    return (
                        ReservationTransitionResult(
                            status="PASS",
                            reasons=(),
                            reservation=reservation,
                            changed=False,
                        )
                    )

                connection.commit()

                return ReservationTransitionResult(
                    status="BLOCK",
                    reasons=(
                        "SIGNED_TRANSACTION_ARTIFACT_MISMATCH",
                    ),
                    reservation=reservation,
                    changed=False,
                )

            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "RESERVATION_SIGNATURE_MISMATCH",
                ),
                reservation=reservation,
                changed=False,
            )

        if reservation.status != ACTIVE:
            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "RESERVATION_NOT_ACTIVE",
                ),
                reservation=reservation,
                changed=False,
            )

        updated = connection.execute(
            """
            UPDATE live_capital_reservations

            SET
                status = ?,
                signed_at = ?,
                transaction_signature = ?,
                signed_message_sha256 = ?,
                signed_transaction_sha256 = ?,
                signed_transaction_bytes = ?,
                recent_blockhash = ?,
                last_valid_block_height = ?,
                blockhash_rpc_slot = ?

            WHERE reservation_id = ?
              AND status = ?
            """,
            (
                SIGNED,
                now,
                transaction_signature,
                signed_message_sha256,
                signed_transaction_sha256,
                signed_transaction_bytes,
                recent_blockhash,
                last_valid_block_height,
                blockhash_rpc_slot,
                reservation_id,
                ACTIVE,
            ),
        )

        if updated.rowcount != 1:
            connection.rollback()

            return ReservationTransitionResult(
                status=UNKNOWN,
                reasons=(
                    "RESERVATION_SIGN_TRANSITION_FAILED",
                ),
                reservation=None,
                changed=False,
            )

        row = connection.execute(
            """
            SELECT *
            FROM live_capital_reservations
            WHERE reservation_id = ?
            """,
            (
                reservation_id,
            ),
        ).fetchone()

        connection.commit()

        return ReservationTransitionResult(
            status="PASS",
            reasons=(),
            reservation=_row_to_reservation(
                row
            ),
            changed=True,
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def arm_reservation_submission(
    *,
    reservation_id: str,
    db_path: Path = DB_PATH,
) -> ReservationTransitionResult:
    """
    Durably cross the pre-send boundary for one
    already-SIGNED transaction.

    Once this succeeds, downstream recovery must
    conservatively assume that the exact persisted
    signed transaction MAY have been relayed.

    This function does not perform network I/O.

    An already-armed reservation does not acquire
    another send boundary. It is blocked and must
    be reconciled before any further relay decision.

    Re-arming is non-mutating and never increments
    the attempt count.
    """

    reservation_id = reservation_id.strip()

    if not reservation_id:
        return ReservationTransitionResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_RESERVATION_ID",
            ),
            reservation=None,
            changed=False,
        )

    now = time.time()

    connection = get_connection(
        db_path
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
            FROM live_capital_reservations
            WHERE reservation_id = ?
            """,
            (
                reservation_id,
            ),
        ).fetchone()

        if row is None:
            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "RESERVATION_NOT_FOUND",
                ),
                reservation=None,
                changed=False,
            )

        reservation = _row_to_reservation(
            row
        )

        if reservation.status == SUBMITTED:
            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "RESERVATION_ALREADY_SUBMITTED",
                ),
                reservation=reservation,
                changed=False,
            )

        if reservation.status != SIGNED:
            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "RESERVATION_NOT_SIGNED",
                ),
                reservation=reservation,
                changed=False,
            )

        if (
            reservation.signed_at is None
            or reservation.transaction_signature
            is None
            or reservation.signed_message_sha256
            is None
            or reservation.signed_transaction_sha256
            is None
            or reservation.signed_transaction_bytes
            is None
            or reservation.recent_blockhash
            is None
            or reservation.last_valid_block_height
            is None
            or reservation.blockhash_rpc_slot
            is None
        ):
            connection.commit()

            return ReservationTransitionResult(
                status=UNKNOWN,
                reasons=(
                    "SIGNED_ARTIFACT_INCOMPLETE",
                ),
                reservation=reservation,
                changed=False,
            )

        if reservation.submitted_at is not None:
            connection.commit()

            return ReservationTransitionResult(
                status=UNKNOWN,
                reasons=(
                    "SUBMISSION_METADATA_INCONSISTENT",
                ),
                reservation=reservation,
                changed=False,
            )

        if (
            reservation.submission_started_at
            is not None
        ):
            if (
                reservation
                .submission_attempt_count
                != 1
            ):
                connection.commit()

                return (
                    ReservationTransitionResult(
                        status=UNKNOWN,
                        reasons=(
                            "SUBMISSION_METADATA_INCONSISTENT",
                        ),
                        reservation=reservation,
                        changed=False,
                    )
                )

            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "SUBMISSION_ALREADY_ARMED_REQUIRES_RECONCILIATION",
                ),
                reservation=reservation,
                changed=False,
            )

        if (
            reservation.submission_attempt_count
            != 0
        ):
            connection.commit()

            return ReservationTransitionResult(
                status=UNKNOWN,
                reasons=(
                    "SUBMISSION_METADATA_INCONSISTENT",
                ),
                reservation=reservation,
                changed=False,
            )

        updated = connection.execute(
            """
            UPDATE live_capital_reservations

            SET
                submission_started_at = ?,
                submission_attempt_count = 1

            WHERE reservation_id = ?
              AND status = ?
              AND submission_started_at IS NULL
              AND submission_attempt_count = 0
              AND submitted_at IS NULL
            """,
            (
                now,
                reservation_id,
                SIGNED,
            ),
        )

        if updated.rowcount != 1:
            connection.rollback()

            return ReservationTransitionResult(
                status=UNKNOWN,
                reasons=(
                    "SUBMISSION_ARM_TRANSITION_FAILED",
                ),
                reservation=None,
                changed=False,
            )

        row = connection.execute(
            """
            SELECT *
            FROM live_capital_reservations
            WHERE reservation_id = ?
            """,
            (
                reservation_id,
            ),
        ).fetchone()

        connection.commit()

        if row is None:
            return ReservationTransitionResult(
                status=UNKNOWN,
                reasons=(
                    "SUBMISSION_ARMED_RESERVATION_MISSING",
                ),
                reservation=None,
                changed=False,
            )

        return ReservationTransitionResult(
            status="PASS",
            reasons=(),
            reservation=_row_to_reservation(
                row
            ),
            changed=True,
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def acknowledge_reservation_submitted(
    *,
    reservation_id: str,
    transaction_signature: str,
    db_path: Path = DB_PATH,
) -> ReservationTransitionResult:
    """
    Atomically mark an armed SIGNED reservation as
    SUBMITTED only after an RPC caller has received
    the exact already-persisted transaction
    signature.

    This function does not perform network I/O.
    """

    reservation_id = reservation_id.strip()
    transaction_signature = (
        transaction_signature.strip()
    )

    if not reservation_id:
        return ReservationTransitionResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_RESERVATION_ID",
            ),
            reservation=None,
            changed=False,
        )

    if not transaction_signature:
        return ReservationTransitionResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_TRANSACTION_SIGNATURE",
            ),
            reservation=None,
            changed=False,
        )

    now = time.time()

    connection = get_connection(
        db_path
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
            FROM live_capital_reservations
            WHERE reservation_id = ?
            """,
            (
                reservation_id,
            ),
        ).fetchone()

        if row is None:
            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "RESERVATION_NOT_FOUND",
                ),
                reservation=None,
                changed=False,
            )

        reservation = _row_to_reservation(
            row
        )

        if (
            reservation.transaction_signature
            != transaction_signature
        ):
            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "RESERVATION_SIGNATURE_MISMATCH",
                ),
                reservation=reservation,
                changed=False,
            )

        if reservation.status == SUBMITTED:
            if (
                reservation.submission_started_at
                is None
                or reservation
                .submission_attempt_count
                < 1
                or reservation.submitted_at
                is None
            ):
                connection.commit()

                return (
                    ReservationTransitionResult(
                        status=UNKNOWN,
                        reasons=(
                            "SUBMISSION_METADATA_INCONSISTENT",
                        ),
                        reservation=reservation,
                        changed=False,
                    )
                )

            connection.commit()

            return ReservationTransitionResult(
                status="PASS",
                reasons=(),
                reservation=reservation,
                changed=False,
            )

        if reservation.status != SIGNED:
            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "RESERVATION_NOT_SIGNED",
                ),
                reservation=reservation,
                changed=False,
            )

        if reservation.submitted_at is not None:
            connection.commit()

            return ReservationTransitionResult(
                status=UNKNOWN,
                reasons=(
                    "SUBMISSION_METADATA_INCONSISTENT",
                ),
                reservation=reservation,
                changed=False,
            )

        if (
            reservation.submission_started_at
            is None
            or reservation
            .submission_attempt_count
            < 1
        ):
            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "SUBMISSION_NOT_ARMED",
                ),
                reservation=reservation,
                changed=False,
            )

        updated = connection.execute(
            """
            UPDATE live_capital_reservations

            SET
                status = ?,
                submitted_at = ?

            WHERE reservation_id = ?
              AND status = ?
              AND transaction_signature = ?
              AND submission_started_at IS NOT NULL
              AND submission_attempt_count >= 1
              AND submitted_at IS NULL
            """,
            (
                SUBMITTED,
                now,
                reservation_id,
                SIGNED,
                transaction_signature,
            ),
        )

        if updated.rowcount != 1:
            connection.rollback()

            return ReservationTransitionResult(
                status=UNKNOWN,
                reasons=(
                    "SUBMITTED_TRANSITION_FAILED",
                ),
                reservation=None,
                changed=False,
            )

        row = connection.execute(
            """
            SELECT *
            FROM live_capital_reservations
            WHERE reservation_id = ?
            """,
            (
                reservation_id,
            ),
        ).fetchone()

        connection.commit()

        if row is None:
            return ReservationTransitionResult(
                status=UNKNOWN,
                reasons=(
                    "SUBMITTED_RESERVATION_MISSING",
                ),
                reservation=None,
                changed=False,
            )

        return ReservationTransitionResult(
            status="PASS",
            reasons=(),
            reservation=_row_to_reservation(
                row
            ),
            changed=True,
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def release_active_reservation(
    *,
    reservation_id: str,
    reason: str,
    db_path: Path = DB_PATH,
) -> ReservationTransitionResult:
    """
    Release only a pre-submit ACTIVE reservation.

    A SUBMITTED reservation cannot be released by
    this function. It requires future transaction
    reconciliation because the transaction may
    still land on-chain.
    """

    reservation_id = reservation_id.strip()
    reason = reason.strip()

    if not reservation_id:
        return ReservationTransitionResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_RESERVATION_ID",
            ),
            reservation=None,
            changed=False,
        )

    if not reason:
        return ReservationTransitionResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_RELEASE_REASON",
            ),
            reservation=None,
            changed=False,
        )

    now = time.time()
    connection = get_connection(db_path)

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        init_schema(connection)

        expire_active_reservations(
            connection,
            now=now,
        )

        row = connection.execute(
            """
            SELECT *
            FROM live_capital_reservations
            WHERE reservation_id = ?
            """,
            (
                reservation_id,
            ),
        ).fetchone()

        if row is None:
            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "RESERVATION_NOT_FOUND",
                ),
                reservation=None,
                changed=False,
            )

        reservation = _row_to_reservation(
            row
        )

        if reservation.status == RELEASED:
            stored_reason = (
                None
                if row["terminal_reason"] is None
                else str(
                    row["terminal_reason"]
                )
            )

            if stored_reason == reason:
                connection.commit()

                return ReservationTransitionResult(
                    status="PASS",
                    reasons=(),
                    reservation=reservation,
                    changed=False,
                )

            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "RELEASE_REASON_MISMATCH",
                ),
                reservation=reservation,
                changed=False,
            )

        if reservation.status in (
            SIGNED,
            SUBMITTED,
        ):
            reason_code = (
                "SIGNED_RESERVATION_REQUIRES_RECONCILIATION"
                if reservation.status == SIGNED
                else
                "SUBMITTED_RESERVATION_REQUIRES_RECONCILIATION"
            )

            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    reason_code,
                ),
                reservation=reservation,
                changed=False,
            )

        if reservation.status != ACTIVE:
            connection.commit()

            return ReservationTransitionResult(
                status="BLOCK",
                reasons=(
                    "RESERVATION_NOT_ACTIVE",
                ),
                reservation=reservation,
                changed=False,
            )

        updated = connection.execute(
            """
            UPDATE live_capital_reservations

            SET
                status = ?,
                terminal_at = ?,
                terminal_reason = ?

            WHERE reservation_id = ?
              AND status = ?
            """,
            (
                RELEASED,
                now,
                reason,
                reservation_id,
                ACTIVE,
            ),
        )

        if updated.rowcount != 1:
            connection.rollback()

            return ReservationTransitionResult(
                status=UNKNOWN,
                reasons=(
                    "RESERVATION_RELEASE_TRANSITION_FAILED",
                ),
                reservation=None,
                changed=False,
            )

        row = connection.execute(
            """
            SELECT *
            FROM live_capital_reservations
            WHERE reservation_id = ?
            """,
            (
                reservation_id,
            ),
        ).fetchone()

        connection.commit()

        return ReservationTransitionResult(
            status="PASS",
            reasons=(),
            reservation=_row_to_reservation(
                row
            ),
            changed=True,
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
