from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from src.execution.pump_execution_simulator import (
    LAMPORTS_PER_SOL,
    PumpBuySimulation,
    PumpCurveState,
)
from src.execution.pump_sell_simulator import (
    PumpSellSimulation,
    calculate_exact_input_sell,
)
from src.risk.risk_governor import (
    AccountRiskState,
    RiskGovernorResult,
    RiskPolicy,
    evaluate_risk,
)


DB_PATH = Path(
    "logs/delve_meme.db"
)

SHADOW_PORTFOLIO_VERSION = (
    "shadow-portfolio-v1"
)

STARTING_EQUITY_ENV = (
    "SHADOW_STARTING_EQUITY_SOL"
)


@dataclass(frozen=True)
class ShadowAccountSnapshot:
    starting_equity_lamports: int
    cash_balance_lamports: int
    current_equity_lamports: int

    day_start_equity_lamports: int
    high_water_equity_lamports: int

    open_exposure_lamports: int
    open_positions: int

    day_key: str

@dataclass(frozen=True)
class ShadowRiskPreview:
    status: str
    reasons: tuple[str, ...]

    account: (
        ShadowAccountSnapshot | None
    )

    risk_result: (
        RiskGovernorResult | None
    )

@dataclass(frozen=True)
class ShadowEntryResult:
    status: str
    reasons: tuple[str, ...]

    position_id: int | None

    account_before: (
        ShadowAccountSnapshot | None
    )

    account_after: (
        ShadowAccountSnapshot | None
    )

    risk_result: (
        RiskGovernorResult | None
    )

    simulation: (
        PumpBuySimulation | None
    )

    initial_exit_simulation: (
        PumpSellSimulation | None
    )


@dataclass(frozen=True)
class ShadowMarkResult:
    status: str
    reasons: tuple[str, ...]

    position_id: int | None
    mint: str

    tokens_held: int
    entry_timestamp: int | None
    entry_wallet_cost_lamports: int | None

    mark_value_lamports: int
    unrealized_pnl_lamports: int

    sell_simulation: (
        PumpSellSimulation | None
    )

    account: (
        ShadowAccountSnapshot | None
    )

@dataclass(frozen=True)
class ShadowCloseResult:
    status: str
    reasons: tuple[str, ...]

    position_id: int | None
    mint: str

    exit_reason: str | None

    tokens_sold: int

    gross_quote_lamports: int
    net_proceeds_lamports: int
    realized_pnl_lamports: int

    sell_simulation: (
        PumpSellSimulation | None
    )

    account_before: (
        ShadowAccountSnapshot | None
    )

    account_after: (
        ShadowAccountSnapshot | None
    )

def utc_day_key(
    timestamp: float | None = None,
) -> str:
    if timestamp is None:
        timestamp = time.time()

    return (
        datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc,
        )
        .date()
        .isoformat()
    )


def get_connection(
    db_path: Path = DB_PATH,
) -> sqlite3.Connection:
    connection = sqlite3.connect(
        db_path,
        timeout=30.0,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA busy_timeout = 30000"
    )

    return connection


def init_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        shadow_account (
            id INTEGER PRIMARY KEY
                CHECK (id = 1),

            portfolio_version TEXT NOT NULL,

            starting_equity_lamports
                INTEGER NOT NULL,

            cash_balance_lamports
                INTEGER NOT NULL,

            current_equity_lamports
                INTEGER NOT NULL,

            day_key TEXT NOT NULL,

            day_start_equity_lamports
                INTEGER NOT NULL,

            high_water_equity_lamports
                INTEGER NOT NULL,

            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        shadow_positions (
            id INTEGER PRIMARY KEY
                AUTOINCREMENT,

            portfolio_version TEXT NOT NULL,

            entry_signature TEXT
                NOT NULL UNIQUE,

            mint TEXT NOT NULL,

            status TEXT NOT NULL,

            probability_2x_15m REAL,

            entry_slot INTEGER,
            entry_timestamp INTEGER NOT NULL,

            entry_spend_lamports
                INTEGER NOT NULL,

            entry_wallet_cost_lamports
                INTEGER NOT NULL,

            tokens_held INTEGER NOT NULL,

            entry_protocol_fee_bps
                INTEGER NOT NULL,

            entry_creator_fee_bps
                INTEGER NOT NULL,

            entry_all_in_price_raw
                REAL NOT NULL,

            entry_virtual_quote_reserves
                INTEGER NOT NULL,

            entry_virtual_token_reserves
                INTEGER NOT NULL,

            entry_real_quote_reserves
                INTEGER NOT NULL,

            entry_real_token_reserves
                INTEGER NOT NULL,

            initial_mark_value_lamports
                INTEGER NOT NULL,

            latest_mark_value_lamports
                INTEGER NOT NULL,

            unrealized_pnl_lamports
                INTEGER NOT NULL,

            latest_virtual_quote_reserves
                INTEGER NOT NULL,

            latest_virtual_token_reserves
                INTEGER NOT NULL,

            latest_real_quote_reserves
                INTEGER NOT NULL,

            latest_real_token_reserves
                INTEGER NOT NULL,

            latest_mark_timestamp
                INTEGER NOT NULL,

            exit_timestamp INTEGER,
            exit_reason TEXT,

            exit_protocol_fee_bps INTEGER,
            exit_creator_fee_bps INTEGER,

            exit_gross_quote_lamports INTEGER,
            exit_net_proceeds_lamports INTEGER,

            realized_pnl_lamports INTEGER,

            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_shadow_open_mint
        ON shadow_positions(mint)
        WHERE status = 'OPEN'
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_shadow_positions_status
        ON shadow_positions(status)
        """
    )


def parse_starting_equity() -> int:
    raw = os.getenv(
        STARTING_EQUITY_ENV
    )

    if raw is None:
        raise RuntimeError(
            f"{STARTING_EQUITY_ENV} "
            "must be explicitly configured "
            "before a shadow account can be "
            "created."
        )

    try:
        sol = Decimal(
            raw.strip()
        )
    except InvalidOperation as error:
        raise RuntimeError(
            f"{STARTING_EQUITY_ENV} "
            "must be a valid SOL amount."
        ) from error

    if sol <= 0:
        raise RuntimeError(
            f"{STARTING_EQUITY_ENV} "
            "must be positive."
        )

    lamports = int(
        sol
        * Decimal(
            LAMPORTS_PER_SOL
        )
    )

    if lamports <= 0:
        raise RuntimeError(
            "Configured shadow equity is "
            "below one lamport."
        )

    return lamports


def ensure_account(
    connection: sqlite3.Connection,
) -> None:
    init_schema(
        connection
    )

    existing = connection.execute(
        """
        SELECT id
        FROM shadow_account
        WHERE id = 1
        """
    ).fetchone()

    if existing is not None:
        return

    starting_equity = (
        parse_starting_equity()
    )

    now = int(
        time.time()
    )

    day_key = utc_day_key(
        now
    )

    connection.execute(
        """
        INSERT INTO shadow_account (
            id,
            portfolio_version,
            starting_equity_lamports,
            cash_balance_lamports,
            current_equity_lamports,
            day_key,
            day_start_equity_lamports,
            high_water_equity_lamports,
            created_at,
            updated_at
        )
        VALUES (
            1,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?
        )
        """,
        (
            SHADOW_PORTFOLIO_VERSION,
            starting_equity,
            starting_equity,
            starting_equity,
            day_key,
            starting_equity,
            starting_equity,
            now,
            now,
        ),
    )


def refresh_account(
    connection: sqlite3.Connection,
) -> ShadowAccountSnapshot:
    ensure_account(
        connection
    )

    account = connection.execute(
        """
        SELECT *
        FROM shadow_account
        WHERE id = 1
        """
    ).fetchone()

    if account is None:
        raise RuntimeError(
            "Shadow account disappeared."
        )

    open_row = connection.execute(
        """
        SELECT
            COUNT(*) AS open_positions,

            COALESCE(
                SUM(entry_spend_lamports),
                0
            ) AS open_exposure,

            COALESCE(
                SUM(latest_mark_value_lamports),
                0
            ) AS open_mark_value

        FROM shadow_positions

        WHERE status = 'OPEN'
        """
    ).fetchone()

    cash = int(
        account[
            "cash_balance_lamports"
        ]
    )

    open_mark_value = int(
        open_row[
            "open_mark_value"
        ]
    )

    equity = (
        cash
        + open_mark_value
    )

    now = int(
        time.time()
    )

    current_day_key = utc_day_key(
        now
    )

    stored_day_key = str(
        account["day_key"]
    )

    day_start_equity = int(
        account[
            "day_start_equity_lamports"
        ]
    )

    if current_day_key != stored_day_key:
        stored_day_key = (
            current_day_key
        )

        day_start_equity = equity

    high_water = max(
        int(
            account[
                "high_water_equity_lamports"
            ]
        ),
        equity,
    )

    connection.execute(
        """
        UPDATE shadow_account

        SET
            current_equity_lamports = ?,
            day_key = ?,
            day_start_equity_lamports = ?,
            high_water_equity_lamports = ?,
            updated_at = ?

        WHERE id = 1
        """,
        (
            equity,
            stored_day_key,
            day_start_equity,
            high_water,
            now,
        ),
    )

    return ShadowAccountSnapshot(
        starting_equity_lamports=int(
            account[
                "starting_equity_lamports"
            ]
        ),

        cash_balance_lamports=cash,

        current_equity_lamports=(
            equity
        ),

        day_start_equity_lamports=(
            day_start_equity
        ),

        high_water_equity_lamports=(
            high_water
        ),

        open_exposure_lamports=int(
            open_row[
                "open_exposure"
            ]
        ),

        open_positions=int(
            open_row[
                "open_positions"
            ]
        ),

        day_key=stored_day_key,
    )


def initialize_shadow_account(
    *,
    db_path: Path = DB_PATH,
) -> ShadowAccountSnapshot:

    connection = get_connection(
        db_path
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        account = refresh_account(
            connection
        )

        connection.commit()

        return account

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def to_risk_state(
    account: ShadowAccountSnapshot,
) -> AccountRiskState:
    return AccountRiskState(
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
        ),

        open_positions=(
            account.open_positions
        ),
    )

def preview_shadow_entry_risk(
    *,
    mint: str,

    curve_state: PumpCurveState,

    protocol_fee_bps: int,
    creator_fee_bps: int,

    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    rent_lamports: int,

    policy: RiskPolicy | None = None,

    db_path: Path = DB_PATH,
) -> ShadowRiskPreview:

    connection = get_connection(
        db_path
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        ensure_account(
            connection
        )

        account = refresh_account(
            connection
        )

        duplicate = connection.execute(
            """
            SELECT id
            FROM shadow_positions
            WHERE mint = ?
              AND status = 'OPEN'
            """,
            (
                mint,
            ),
        ).fetchone()

        if duplicate is not None:
            connection.commit()

            return ShadowRiskPreview(
                status="BLOCK",
                reasons=(
                    "POSITION_ALREADY_OPEN_FOR_MINT",
                ),
                account=account,
                risk_result=None,
            )

        risk = evaluate_risk(
            account=to_risk_state(
                account
            ),

            curve_state=curve_state,

            protocol_fee_bps=int(
                protocol_fee_bps
            ),

            creator_fee_bps=int(
                creator_fee_bps
            ),

            slippage_bps=int(
                slippage_bps
            ),

            base_network_fee_lamports=int(
                base_network_fee_lamports
            ),

            priority_fee_lamports=int(
                priority_fee_lamports
            ),

            rent_lamports=int(
                rent_lamports
            ),

            policy=policy,
        )

        connection.commit()

        return ShadowRiskPreview(
            status=risk.status,
            reasons=tuple(
                risk.reasons
            ),
            account=account,
            risk_result=risk,
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

def open_shadow_position(
    *,
    entry_signature: str,
    mint: str,
    probability_2x_15m: float | None,
    entry_slot: int | None,
    entry_timestamp: int,

    curve_state: PumpCurveState,

    approved_simulation: (
        PumpBuySimulation | None
    ) = None,

    protocol_fee_bps: int,
    creator_fee_bps: int,

    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    rent_lamports: int,

    policy: RiskPolicy | None = None,

    db_path: Path = DB_PATH,
) -> ShadowEntryResult:

    connection = get_connection(
        db_path
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        ensure_account(
            connection
        )

        duplicate = connection.execute(
            """
            SELECT id
            FROM shadow_positions
            WHERE mint = ?
              AND status = 'OPEN'
            """,
            (
                mint,
            ),
        ).fetchone()

        account_before = (
            refresh_account(
                connection
            )
        )

        if duplicate is not None:
            connection.commit()

            return ShadowEntryResult(
                status="BLOCK",
                reasons=(
                    "POSITION_ALREADY_OPEN_FOR_MINT",
                ),
                position_id=int(
                    duplicate["id"]
                ),
                account_before=(
                    account_before
                ),
                account_after=(
                    account_before
                ),
                risk_result=None,
                simulation=None,
                initial_exit_simulation=None,
            )

        risk = evaluate_risk(
            account=to_risk_state(
                account_before
            ),

            curve_state=curve_state,

            protocol_fee_bps=int(
                protocol_fee_bps
            ),

            creator_fee_bps=int(
                creator_fee_bps
            ),

            slippage_bps=int(
                slippage_bps
            ),

            base_network_fee_lamports=int(
                base_network_fee_lamports
            ),

            priority_fee_lamports=int(
                priority_fee_lamports
            ),

            rent_lamports=int(
                rent_lamports
            ),

            policy=policy,
        )

        if not risk.allows_new_position:
            connection.commit()

            return ShadowEntryResult(
                status=risk.status,
                reasons=tuple(
                    risk.reasons
                ),
                position_id=None,
                account_before=(
                    account_before
                ),
                account_after=(
                    account_before
                ),
                risk_result=risk,
                simulation=None,
                initial_exit_simulation=None,
            )

        if approved_simulation is None:
            #
            # Convenience path used by isolated tests.
            # The autonomous pipeline will supply the
            # exact execution-approved simulation.
            #
            simulation = (
                risk.recommended_simulation
            )

        else:
            simulation = (
                approved_simulation
            )

            #
            # Re-check account risk at commit time.
            # If account state changed enough that the
            # approved size is no longer permitted,
            # fail closed rather than resize silently.
            #
            if (
                int(
                    simulation.spendable_quote_in
                )
                > int(
                    risk.recommended_spend_lamports
                )
            ):
                connection.commit()

                return ShadowEntryResult(
                    status="BLOCK",
                    reasons=(
                        "APPROVED_SIZE_EXCEEDS_CURRENT_RISK_BUDGET",
                    ),
                    position_id=None,
                    account_before=(
                        account_before
                    ),
                    account_after=(
                        account_before
                    ),
                    risk_result=risk,
                    simulation=simulation,
                    initial_exit_simulation=None,
                )

            #
            # The approved simulation must describe
            # this exact curve snapshot.
            #
            if (
                int(
                    simulation.pre_virtual_quote_reserves
                )
                != int(
                    curve_state.virtual_quote_reserves
                )
                or int(
                    simulation.pre_virtual_token_reserves
                )
                != int(
                    curve_state.virtual_token_reserves
                )
                or int(
                    simulation.pre_real_quote_reserves
                )
                != int(
                    curve_state.real_quote_reserves
                )
                or int(
                    simulation.pre_real_token_reserves
                )
                != int(
                    curve_state.real_token_reserves
                )
            ):
                connection.rollback()

                return ShadowEntryResult(
                    status="UNKNOWN",
                    reasons=(
                        "APPROVED_SIMULATION_CURVE_MISMATCH",
                    ),
                    position_id=None,
                    account_before=(
                        account_before
                    ),
                    account_after=None,
                    risk_result=risk,
                    simulation=simulation,
                    initial_exit_simulation=None,
                )

            #
            # Execution assumptions must also match.
            #
            if (
                int(
                    simulation.protocol_fee_bps
                )
                != int(
                    protocol_fee_bps
                )
                or int(
                    simulation.creator_fee_bps
                )
                != int(
                    creator_fee_bps
                )
                or int(
                    simulation.slippage_bps
                )
                != int(
                    slippage_bps
                )
                or int(
                    simulation.base_network_fee_lamports
                )
                != int(
                    base_network_fee_lamports
                )
                or int(
                    simulation.priority_fee_lamports
                )
                != int(
                    priority_fee_lamports
                )
                or int(
                    simulation.rent_lamports
                )
                != int(
                    rent_lamports
                )
            ):
                connection.rollback()

                return ShadowEntryResult(
                    status="UNKNOWN",
                    reasons=(
                        "APPROVED_SIMULATION_ASSUMPTION_MISMATCH",
                    ),
                    position_id=None,
                    account_before=(
                        account_before
                    ),
                    account_after=None,
                    risk_result=risk,
                    simulation=simulation,
                    initial_exit_simulation=None,
                )

        if simulation is None:
            connection.rollback()

            return ShadowEntryResult(
                status="UNKNOWN",
                reasons=(
                    "RISK_PASS_WITHOUT_SIMULATION",
                ),
                position_id=None,
                account_before=(
                    account_before
                ),
                account_after=None,
                risk_result=risk,
                simulation=None,
                initial_exit_simulation=None,
            )

        if not simulation.executable:
            connection.rollback()

            return ShadowEntryResult(
                status="UNKNOWN",
                reasons=(
                    "RISK_SIMULATION_NOT_EXECUTABLE",
                ),
                position_id=None,
                account_before=(
                    account_before
                ),
                account_after=None,
                risk_result=risk,
                simulation=simulation,
                initial_exit_simulation=None,
            )

        wallet_cost = int(
            simulation.total_wallet_cost_lamports
        )

        if (
            wallet_cost
            > account_before.cash_balance_lamports
        ):
            connection.commit()

            return ShadowEntryResult(
                status="BLOCK",
                reasons=(
                    "INSUFFICIENT_SHADOW_CASH",
                ),
                position_id=None,
                account_before=(
                    account_before
                ),
                account_after=(
                    account_before
                ),
                risk_result=risk,
                simulation=simulation,
                initial_exit_simulation=None,
            )

        post_buy_state = PumpCurveState(
            virtual_quote_reserves=int(
                simulation.post_virtual_quote_reserves
            ),

            virtual_token_reserves=int(
                simulation.post_virtual_token_reserves
            ),

            real_quote_reserves=int(
                simulation.post_real_quote_reserves
            ),

            real_token_reserves=int(
                simulation.post_real_token_reserves
            ),
        )

        initial_exit = (
            calculate_exact_input_sell(
                state=post_buy_state,

                tokens_in=int(
                    simulation.tokens_out
                ),

                protocol_fee_bps=int(
                    protocol_fee_bps
                ),

                creator_fee_bps=int(
                    creator_fee_bps
                ),

                slippage_bps=int(
                    slippage_bps
                ),

                base_network_fee_lamports=int(
                    base_network_fee_lamports
                ),

                priority_fee_lamports=int(
                    priority_fee_lamports
                ),
            )
        )

        if initial_exit.executable:
            initial_mark_value = int(
                initial_exit.net_wallet_proceeds_lamports
            )
        else:
            #
            # Conservative accounting:
            # if we cannot model a realizable exit,
            # mark the position at zero.
            #
            initial_mark_value = 0

        unrealized_pnl = (
            initial_mark_value
            - wallet_cost
        )

        now = int(
            time.time()
        )

        cursor = connection.execute(
            """
            INSERT INTO shadow_positions (
                portfolio_version,
                entry_signature,
                mint,
                status,
                probability_2x_15m,
                entry_slot,
                entry_timestamp,
                entry_spend_lamports,
                entry_wallet_cost_lamports,
                tokens_held,
                entry_protocol_fee_bps,
                entry_creator_fee_bps,
                entry_all_in_price_raw,
                entry_virtual_quote_reserves,
                entry_virtual_token_reserves,
                entry_real_quote_reserves,
                entry_real_token_reserves,
                initial_mark_value_lamports,
                latest_mark_value_lamports,
                unrealized_pnl_lamports,
                latest_virtual_quote_reserves,
                latest_virtual_token_reserves,
                latest_real_quote_reserves,
                latest_real_token_reserves,
                latest_mark_timestamp,
                created_at,
                updated_at
            )
            VALUES (
                ?, ?, ?, 'OPEN', ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?
            )
            """,
            (
                SHADOW_PORTFOLIO_VERSION,
                entry_signature,
                mint,
                probability_2x_15m,
                entry_slot,
                int(
                    entry_timestamp
                ),

                int(
                    simulation.spendable_quote_in
                ),

                wallet_cost,

                int(
                    simulation.tokens_out
                ),

                int(
                    protocol_fee_bps
                ),

                int(
                    creator_fee_bps
                ),

                float(
                    simulation.all_in_entry_price_raw
                ),

                int(
                    simulation.post_virtual_quote_reserves
                ),

                int(
                    simulation.post_virtual_token_reserves
                ),

                int(
                    simulation.post_real_quote_reserves
                ),

                int(
                    simulation.post_real_token_reserves
                ),

                initial_mark_value,
                initial_mark_value,
                unrealized_pnl,

                int(
                    simulation.post_virtual_quote_reserves
                ),

                int(
                    simulation.post_virtual_token_reserves
                ),

                int(
                    simulation.post_real_quote_reserves
                ),

                int(
                    simulation.post_real_token_reserves
                ),

                now,
                now,
                now,
            ),
        )

        position_id = int(
            cursor.lastrowid
        )

        new_cash = (
            account_before.cash_balance_lamports
            - wallet_cost
        )

        connection.execute(
            """
            UPDATE shadow_account

            SET
                cash_balance_lamports = ?,
                updated_at = ?

            WHERE id = 1
            """,
            (
                new_cash,
                now,
            ),
        )

        account_after = (
            refresh_account(
                connection
            )
        )

        connection.commit()

        return ShadowEntryResult(
            status="PASS",
            reasons=(),
            position_id=position_id,
            account_before=(
                account_before
            ),
            account_after=(
                account_after
            ),
            risk_result=risk,
            simulation=simulation,
            initial_exit_simulation=(
                initial_exit
            ),
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

def close_shadow_position(
    *,
    mint: str,
    exit_reason: str,

    curve_state: PumpCurveState,

    protocol_fee_bps: int,
    creator_fee_bps: int,

    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,

    exit_timestamp: int | None = None,

    db_path: Path = DB_PATH,
) -> ShadowCloseResult:

    if not exit_reason.strip():
        raise ValueError(
            "exit_reason must not be empty."
        )

    connection = get_connection(
        db_path
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        ensure_account(
            connection
        )

        account_before = (
            refresh_account(
                connection
            )
        )

        position = connection.execute(
            """
            SELECT *
            FROM shadow_positions
            WHERE mint = ?
              AND status = 'OPEN'
            LIMIT 1
            """,
            (
                mint,
            ),
        ).fetchone()

        if position is None:
            connection.commit()

            return ShadowCloseResult(
                status="NO_POSITION",
                reasons=(),

                position_id=None,
                mint=mint,

                exit_reason=None,

                tokens_sold=0,

                gross_quote_lamports=0,
                net_proceeds_lamports=0,
                realized_pnl_lamports=0,

                sell_simulation=None,

                account_before=(
                    account_before
                ),

                account_after=(
                    account_before
                ),
            )

        tokens_held = int(
            position[
                "tokens_held"
            ]
        )

        if tokens_held <= 0:
            connection.rollback()

            return ShadowCloseResult(
                status="UNKNOWN",
                reasons=(
                    "OPEN_POSITION_HAS_NO_TOKENS",
                ),

                position_id=int(
                    position["id"]
                ),

                mint=mint,

                exit_reason=(
                    exit_reason
                ),

                tokens_sold=0,

                gross_quote_lamports=0,
                net_proceeds_lamports=0,
                realized_pnl_lamports=0,

                sell_simulation=None,

                account_before=(
                    account_before
                ),

                account_after=None,
            )

        simulation = (
            calculate_exact_input_sell(
                state=curve_state,

                tokens_in=(
                    tokens_held
                ),

                protocol_fee_bps=int(
                    protocol_fee_bps
                ),

                creator_fee_bps=int(
                    creator_fee_bps
                ),

                slippage_bps=int(
                    slippage_bps
                ),

                base_network_fee_lamports=int(
                    base_network_fee_lamports
                ),

                priority_fee_lamports=int(
                    priority_fee_lamports
                ),
            )
        )

        gross_quote = int(
            simulation.gross_quote_out
        )

        net_proceeds = int(
            simulation.net_wallet_proceeds_lamports
        )

        if not simulation.executable:
            connection.commit()

            return ShadowCloseResult(
                status="UNEXITABLE",
                reasons=(
                    "SELL_NOT_EXECUTABLE:"
                    f"{simulation.ineligible_reason}",
                ),

                position_id=int(
                    position["id"]
                ),

                mint=mint,

                exit_reason=(
                    exit_reason
                ),

                tokens_sold=(
                    tokens_held
                ),

                gross_quote_lamports=(
                    gross_quote
                ),

                net_proceeds_lamports=(
                    net_proceeds
                ),

                realized_pnl_lamports=0,

                sell_simulation=(
                    simulation
                ),

                account_before=(
                    account_before
                ),

                account_after=(
                    account_before
                ),
            )

        #
        # A real wallet must be able to fund
        # transaction overhead before the sell
        # can land. Fail closed if shadow cash
        # cannot support that requirement.
        #
        if (
            account_before.cash_balance_lamports
            < int(
                simulation.total_transaction_overhead_lamports
            )
        ):
            connection.commit()

            return ShadowCloseResult(
                status="BLOCK",
                reasons=(
                    "INSUFFICIENT_CASH_FOR_EXIT_OVERHEAD",
                ),

                position_id=int(
                    position["id"]
                ),

                mint=mint,

                exit_reason=(
                    exit_reason
                ),

                tokens_sold=(
                    tokens_held
                ),

                gross_quote_lamports=(
                    gross_quote
                ),

                net_proceeds_lamports=(
                    net_proceeds
                ),

                realized_pnl_lamports=0,

                sell_simulation=(
                    simulation
                ),

                account_before=(
                    account_before
                ),

                account_after=(
                    account_before
                ),
            )

        if exit_timestamp is None:
            exit_timestamp = int(
                time.time()
            )

        wallet_cost = int(
            position[
                "entry_wallet_cost_lamports"
            ]
        )

        realized_pnl = (
            net_proceeds
            - wallet_cost
        )

        now = int(
            time.time()
        )

        updated = connection.execute(
            """
            UPDATE shadow_positions

            SET
                status = 'CLOSED',

                latest_mark_value_lamports = ?,
                unrealized_pnl_lamports = 0,

                latest_virtual_quote_reserves = ?,
                latest_virtual_token_reserves = ?,
                latest_real_quote_reserves = ?,
                latest_real_token_reserves = ?,

                latest_mark_timestamp = ?,

                exit_timestamp = ?,
                exit_reason = ?,

                exit_protocol_fee_bps = ?,
                exit_creator_fee_bps = ?,

                exit_gross_quote_lamports = ?,
                exit_net_proceeds_lamports = ?,

                realized_pnl_lamports = ?,

                updated_at = ?

            WHERE id = ?
              AND status = 'OPEN'
            """,
            (
                net_proceeds,

                int(
                    curve_state.virtual_quote_reserves
                ),

                int(
                    curve_state.virtual_token_reserves
                ),

                int(
                    curve_state.real_quote_reserves
                ),

                int(
                    curve_state.real_token_reserves
                ),

                int(
                    exit_timestamp
                ),

                int(
                    exit_timestamp
                ),

                exit_reason.strip(),

                int(
                    protocol_fee_bps
                ),

                int(
                    creator_fee_bps
                ),

                gross_quote,

                net_proceeds,

                realized_pnl,

                now,

                int(
                    position["id"]
                ),
            ),
        )

        if updated.rowcount != 1:
            connection.rollback()

            return ShadowCloseResult(
                status="UNKNOWN",
                reasons=(
                    "POSITION_CLOSE_STATE_CHANGED",
                ),

                position_id=int(
                    position["id"]
                ),

                mint=mint,

                exit_reason=(
                    exit_reason
                ),

                tokens_sold=(
                    tokens_held
                ),

                gross_quote_lamports=(
                    gross_quote
                ),

                net_proceeds_lamports=(
                    net_proceeds
                ),

                realized_pnl_lamports=0,

                sell_simulation=(
                    simulation
                ),

                account_before=(
                    account_before
                ),

                account_after=None,
            )

        cash_updated = connection.execute(
            """
            UPDATE shadow_account

            SET
                cash_balance_lamports =
                    cash_balance_lamports + ?,
                updated_at = ?

            WHERE id = 1
            """,
            (
                net_proceeds,
                now,
            ),
        )

        if cash_updated.rowcount != 1:
            connection.rollback()

            return ShadowCloseResult(
                status="UNKNOWN",
                reasons=(
                    "SHADOW_ACCOUNT_UPDATE_FAILED",
                ),

                position_id=int(
                    position["id"]
                ),

                mint=mint,

                exit_reason=(
                    exit_reason
                ),

                tokens_sold=(
                    tokens_held
                ),

                gross_quote_lamports=(
                    gross_quote
                ),

                net_proceeds_lamports=(
                    net_proceeds
                ),

                realized_pnl_lamports=0,

                sell_simulation=(
                    simulation
                ),

                account_before=(
                    account_before
                ),

                account_after=None,
            )

        account_after = (
            refresh_account(
                connection
            )
        )

        connection.commit()

        return ShadowCloseResult(
            status="CLOSED",
            reasons=(),

            position_id=int(
                position["id"]
            ),

            mint=mint,

            exit_reason=(
                exit_reason.strip()
            ),

            tokens_sold=(
                tokens_held
            ),

            gross_quote_lamports=(
                gross_quote
            ),

            net_proceeds_lamports=(
                net_proceeds
            ),

            realized_pnl_lamports=(
                realized_pnl
            ),

            sell_simulation=(
                simulation
            ),

            account_before=(
                account_before
            ),

            account_after=(
                account_after
            ),
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

def list_open_shadow_mints(
    *,
    db_path: Path = DB_PATH,
) -> set[str]:

    connection = get_connection(
        db_path
    )

    try:
        init_schema(
            connection
        )

        rows = connection.execute(
            """
            SELECT mint
            FROM shadow_positions
            WHERE status = 'OPEN'
            """
        ).fetchall()

        return {
            str(
                row["mint"]
            )
            for row in rows
        }

    finally:
        connection.close()

def mark_open_position(
    *,
    mint: str,
    curve_state: PumpCurveState,

    protocol_fee_bps: int,
    creator_fee_bps: int,

    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,

    mark_timestamp: int | None = None,

    db_path: Path = DB_PATH,
) -> ShadowMarkResult:

    connection = get_connection(
        db_path
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        ensure_account(
            connection
        )

        position = connection.execute(
            """
            SELECT *
            FROM shadow_positions
            WHERE mint = ?
              AND status = 'OPEN'
            """,
            (
                mint,
            ),
        ).fetchone()

        if position is None:
            account = refresh_account(
                connection
            )

            connection.commit()

            return ShadowMarkResult(
                status="NO_POSITION",
                reasons=(),
                position_id=None,
                mint=mint,
                tokens_held=0,

                entry_timestamp=None,
                entry_wallet_cost_lamports=None,

                mark_value_lamports=0,
                unrealized_pnl_lamports=0,
                sell_simulation=None,
                account=account,
            )

        tokens_held = int(
            position[
                "tokens_held"
            ]
        )

        simulation = (
            calculate_exact_input_sell(
                state=curve_state,

                tokens_in=(
                    tokens_held
                ),

                protocol_fee_bps=int(
                    protocol_fee_bps
                ),

                creator_fee_bps=int(
                    creator_fee_bps
                ),

                slippage_bps=int(
                    slippage_bps
                ),

                base_network_fee_lamports=int(
                    base_network_fee_lamports
                ),

                priority_fee_lamports=int(
                    priority_fee_lamports
                ),
            )
        )

        reasons: tuple[str, ...]

        if simulation.executable:
            status = "MARKED"

            reasons = ()

            mark_value = int(
                simulation.net_wallet_proceeds_lamports
            )

        else:
            status = "UNEXITABLE"

            reasons = (
                "SELL_NOT_EXECUTABLE:"
                f"{simulation.ineligible_reason}",
            )

            #
            # Fail-conservative valuation.
            #
            mark_value = 0

        wallet_cost = int(
            position[
                "entry_wallet_cost_lamports"
            ]
        )

        unrealized_pnl = (
            mark_value
            - wallet_cost
        )

        if mark_timestamp is None:
            mark_timestamp = int(
                time.time()
            )

        connection.execute(
            """
            UPDATE shadow_positions

            SET
                latest_mark_value_lamports = ?,
                unrealized_pnl_lamports = ?,

                latest_virtual_quote_reserves = ?,
                latest_virtual_token_reserves = ?,
                latest_real_quote_reserves = ?,
                latest_real_token_reserves = ?,

                latest_mark_timestamp = ?,
                updated_at = ?

            WHERE id = ?
            """,
            (
                mark_value,
                unrealized_pnl,

                int(
                    curve_state.virtual_quote_reserves
                ),

                int(
                    curve_state.virtual_token_reserves
                ),

                int(
                    curve_state.real_quote_reserves
                ),

                int(
                    curve_state.real_token_reserves
                ),

                int(
                    mark_timestamp
                ),

                int(
                    time.time()
                ),

                int(
                    position["id"]
                ),
            ),
        )

        account = refresh_account(
            connection
        )

        connection.commit()

        return ShadowMarkResult(
            status=status,
            reasons=reasons,

            position_id=int(
                position["id"]
            ),

            mint=mint,

            tokens_held=(
                tokens_held
            ),

            entry_timestamp=int(
                position[
                    "entry_timestamp"
                ]
            ),

            entry_wallet_cost_lamports=int(
                position[
                    "entry_wallet_cost_lamports"
                ]
            ),

            mark_value_lamports=(
                mark_value
            ),

            unrealized_pnl_lamports=(
                unrealized_pnl
            ),

            sell_simulation=(
                simulation
            ),

            account=account,
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
