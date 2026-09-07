from __future__ import annotations

import math
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

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

RESERVATION_VERSION = "live-capital-reservation-v1"

ACTIVE = "ACTIVE"
SUBMITTED = "SUBMITTED"
RELEASED = "RELEASED"
EXPIRED = "EXPIRED"

BUY = "BUY"


@dataclass(frozen=True)
class LiveCapitalReservation:
    reservation_id: str
    reservation_version: str

    mint: str
    side: str

    spend_lamports: int
    wallet_cost_lamports: int
    status: str

    risk_governor_version: str

    base_available_cash_lamports: int
    base_open_exposure_lamports: int
    base_open_positions: int

    reserved_exposure_before_lamports: int
    reserved_cash_before_lamports: int
    active_reservations_before: int

    created_at: float
    expires_at: float


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

            spend_lamports INTEGER NOT NULL
                CHECK (spend_lamports > 0),

            wallet_cost_lamports INTEGER NOT NULL
                CHECK (wallet_cost_lamports > 0),

            status TEXT NOT NULL,

            risk_governor_version TEXT NOT NULL,

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

            terminal_at REAL,
            terminal_reason TEXT
        )
        """
    )

    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        live_capital_reservations_held_mint
        ON live_capital_reservations (mint)
        WHERE status IN (
            'ACTIVE',
            'SUBMITTED'
        )
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

        WHERE status IN (?, ?)
        """,
        (
            ACTIVE,
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
              AND status IN (?, ?)

            LIMIT 1
            """,
            (
                mint,
                ACTIVE,
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

        spend_lamports = int(
            risk.recommended_spend_lamports
        )

        wallet_cost_lamports = int(
            risk.recommended_simulation
            .total_wallet_cost_lamports
        )

        if wallet_cost_lamports <= 0:
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
            spend_lamports=spend_lamports,
            wallet_cost_lamports=(
                wallet_cost_lamports
            ),
            status=ACTIVE,
            risk_governor_version=(
                risk.governor_version
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
        )

        connection.execute(
            """
            INSERT INTO live_capital_reservations (
                reservation_id,
                reservation_version,
                mint,
                side,
                spend_lamports,
                wallet_cost_lamports,
                status,
                risk_governor_version,
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
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                reservation.reservation_id,
                reservation.reservation_version,
                reservation.mint,
                reservation.side,
                reservation.spend_lamports,
                reservation.wallet_cost_lamports,
                reservation.status,
                reservation.risk_governor_version,
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
