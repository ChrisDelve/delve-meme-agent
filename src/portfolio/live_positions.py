from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solders.pubkey import Pubkey

from src.execution.order_authorization import (
    WRAPPED_SOL_MINT,
)
from src.execution.signed_transaction_receipt import (
    SIGNED_TRANSACTION_RECEIPT_VERSION,
)
from src.execution.successful_pump_buy_fill import (
    SUCCESSFUL_PUMP_BUY_FILL_VERSION,
)
from src.portfolio.live_reservations import (
    ACTIVE,
    BUY,
    DB_PATH,
    RELEASED,
    RESERVATION_VERSION,
    SIGNED,
    SQLITE_INT_MAX,
    SUBMITTED,
    get_connection,
    init_schema as init_reservation_schema,
)
from src.portfolio.live_transaction_journal import (
    LIVE_TRANSACTION_JOURNAL_VERSION,
    SUCCESS,
    init_schema as init_journal_schema,
)


LIVE_POSITION_VERSION = (
    "live-position-v1"
)

LIVE_POSITION_RISK_TOTALS_VERSION = (
    "live-position-risk-totals-v1"
)

OPEN = "OPEN"

RECONCILED_SUCCESSFUL_BUY_REASON = (
    "RECONCILED_SUCCESSFUL_BUY"
)

PASS = "PASS"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LivePosition:
    position_id: int
    position_version: str

    reservation_id: str
    entry_signature: str

    wallet_pubkey: str
    mint: str
    status: str

    fill_resolver_version: str

    signed_transaction_sha256: str
    observed_transaction_sha256: str

    entry_slot: int
    entry_block_time: int | None

    base_token_program: str
    associated_base_user: str
    quote_mint: str

    authorized_token_amount: int
    trade_event_token_amount: int

    token_pre_amount: int
    token_post_amount: int

    entry_tokens: int
    tokens_held: int

    authorized_max_sol_cost_lamports: int

    entry_exposure_lamports: int
    remaining_exposure_lamports: int

    entry_wallet_cost_lamports: int
    remaining_cost_basis_lamports: int

    cumulative_net_proceeds_lamports: int
    cumulative_realized_pnl_lamports: int

    network_fee_lamports: int

    wallet_pre_balance_lamports: int
    wallet_post_balance_lamports: int
    wallet_balance_delta_lamports: int

    trade_event_sol_amount: int
    protocol_fee_lamports: int
    creator_fee_lamports: int
    cashback_lamports: int
    buyback_fee_lamports: int
    quote_amount: int

    created_at: float
    updated_at: float


@dataclass(frozen=True)
class SuccessfulBuyAccountingResult:
    status: str
    reasons: tuple[str, ...]

    position: LivePosition | None

    reservation_id: str
    reservation_status: str | None

    terminal_at: float | None
    terminal_reason: str | None

    changed: bool


@dataclass(frozen=True)
class LivePositionRiskTotalsResult:
    loader_version: str

    status: str
    reasons: tuple[str, ...]

    wallet_pubkey: str

    open_exposure_lamports: int | None
    open_positions: int | None


def _strict_nonnegative_int(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= SQLITE_INT_MAX
    )


def _strict_positive_int(
    value: Any,
) -> bool:
    return (
        _strict_nonnegative_int(value)
        and value > 0
    )


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


def _nonempty_string(
    value: Any,
) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
    )


def _submission_metadata_coherent(
    row: sqlite3.Row,
) -> bool:
    status = str(
        row["status"]
    )

    submission_started_at = row[
        "submission_started_at"
    ]

    submission_attempt_count = int(
        row["submission_attempt_count"]
    )

    submitted_at = row[
        "submitted_at"
    ]

    if status == SIGNED:
        if submitted_at is not None:
            return False

        if submission_started_at is None:
            return (
                submission_attempt_count == 0
            )

        return (
            submission_attempt_count >= 1
        )

    if status == SUBMITTED:
        return (
            submission_started_at
            is not None
            and submission_attempt_count
            >= 1
            and submitted_at is not None
        )

    return True


def init_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        live_positions (
            position_id INTEGER PRIMARY KEY
                AUTOINCREMENT,

            position_version TEXT NOT NULL,

            reservation_id TEXT NOT NULL UNIQUE
                REFERENCES live_capital_reservations(
                    reservation_id
                ),

            entry_signature TEXT NOT NULL UNIQUE,

            wallet_pubkey TEXT NOT NULL,
            mint TEXT NOT NULL,

            status TEXT NOT NULL,

            fill_resolver_version TEXT NOT NULL,

            signed_transaction_sha256
                TEXT NOT NULL,

            observed_transaction_sha256
                TEXT NOT NULL,

            entry_slot INTEGER NOT NULL
                CHECK (entry_slot >= 0),

            entry_block_time INTEGER,

            base_token_program TEXT NOT NULL,
            associated_base_user TEXT NOT NULL,
            quote_mint TEXT NOT NULL,

            authorized_token_amount
                INTEGER NOT NULL
                CHECK (
                    authorized_token_amount > 0
                ),

            trade_event_token_amount
                INTEGER NOT NULL
                CHECK (
                    trade_event_token_amount > 0
                ),

            token_pre_amount
                INTEGER NOT NULL
                CHECK (
                    token_pre_amount >= 0
                ),

            token_post_amount
                INTEGER NOT NULL
                CHECK (
                    token_post_amount > 0
                ),

            entry_tokens INTEGER NOT NULL
                CHECK (entry_tokens > 0),

            tokens_held INTEGER NOT NULL
                CHECK (tokens_held >= 0),

            authorized_max_sol_cost_lamports
                INTEGER NOT NULL
                CHECK (
                    authorized_max_sol_cost_lamports
                    > 0
                ),

            entry_exposure_lamports
                INTEGER NOT NULL
                CHECK (
                    entry_exposure_lamports > 0
                ),

            remaining_exposure_lamports
                INTEGER NOT NULL
                CHECK (
                    remaining_exposure_lamports
                    >= 0
                ),

            entry_wallet_cost_lamports
                INTEGER NOT NULL
                CHECK (
                    entry_wallet_cost_lamports > 0
                ),

            remaining_cost_basis_lamports
                INTEGER NOT NULL
                CHECK (
                    remaining_cost_basis_lamports
                    >= 0
                ),

            cumulative_net_proceeds_lamports
                INTEGER NOT NULL
                CHECK (
                    cumulative_net_proceeds_lamports
                    >= 0
                ),

            cumulative_realized_pnl_lamports
                INTEGER NOT NULL,

            network_fee_lamports
                INTEGER NOT NULL
                CHECK (
                    network_fee_lamports >= 0
                ),

            wallet_pre_balance_lamports
                INTEGER NOT NULL
                CHECK (
                    wallet_pre_balance_lamports
                    >= 0
                ),

            wallet_post_balance_lamports
                INTEGER NOT NULL
                CHECK (
                    wallet_post_balance_lamports
                    >= 0
                ),

            wallet_balance_delta_lamports
                INTEGER NOT NULL,

            trade_event_sol_amount
                INTEGER NOT NULL
                CHECK (
                    trade_event_sol_amount
                    >= 0
                ),

            protocol_fee_lamports
                INTEGER NOT NULL
                CHECK (
                    protocol_fee_lamports
                    >= 0
                ),

            creator_fee_lamports
                INTEGER NOT NULL
                CHECK (
                    creator_fee_lamports
                    >= 0
                ),

            cashback_lamports
                INTEGER NOT NULL
                CHECK (
                    cashback_lamports
                    >= 0
                ),

            buyback_fee_lamports
                INTEGER NOT NULL
                CHECK (
                    buyback_fee_lamports
                    >= 0
                ),

            quote_amount
                INTEGER NOT NULL
                CHECK (
                    quote_amount >= 0
                ),

            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        live_positions_status_mint
        ON live_positions (
            status,
            mint
        )
        """
    )


def _row_to_position(
    row: sqlite3.Row,
) -> LivePosition:
    return LivePosition(
        position_id=int(
            row["position_id"]
        ),
        position_version=str(
            row["position_version"]
        ),
        reservation_id=str(
            row["reservation_id"]
        ),
        entry_signature=str(
            row["entry_signature"]
        ),
        wallet_pubkey=str(
            row["wallet_pubkey"]
        ),
        mint=str(
            row["mint"]
        ),
        status=str(
            row["status"]
        ),
        fill_resolver_version=str(
            row["fill_resolver_version"]
        ),
        signed_transaction_sha256=str(
            row[
                "signed_transaction_sha256"
            ]
        ),
        observed_transaction_sha256=str(
            row[
                "observed_transaction_sha256"
            ]
        ),
        entry_slot=int(
            row["entry_slot"]
        ),
        entry_block_time=(
            None
            if row["entry_block_time"]
            is None
            else int(
                row["entry_block_time"]
            )
        ),
        base_token_program=str(
            row["base_token_program"]
        ),
        associated_base_user=str(
            row[
                "associated_base_user"
            ]
        ),
        quote_mint=str(
            row["quote_mint"]
        ),
        authorized_token_amount=int(
            row[
                "authorized_token_amount"
            ]
        ),
        trade_event_token_amount=int(
            row[
                "trade_event_token_amount"
            ]
        ),
        token_pre_amount=int(
            row[
                "token_pre_amount"
            ]
        ),
        token_post_amount=int(
            row[
                "token_post_amount"
            ]
        ),
        entry_tokens=int(
            row["entry_tokens"]
        ),
        tokens_held=int(
            row["tokens_held"]
        ),
        authorized_max_sol_cost_lamports=int(
            row[
                "authorized_max_sol_cost_lamports"
            ]
        ),
        entry_exposure_lamports=int(
            row[
                "entry_exposure_lamports"
            ]
        ),
        remaining_exposure_lamports=int(
            row[
                "remaining_exposure_lamports"
            ]
        ),
        entry_wallet_cost_lamports=int(
            row[
                "entry_wallet_cost_lamports"
            ]
        ),
        remaining_cost_basis_lamports=int(
            row[
                "remaining_cost_basis_lamports"
            ]
        ),
        cumulative_net_proceeds_lamports=int(
            row[
                "cumulative_net_proceeds_lamports"
            ]
        ),
        cumulative_realized_pnl_lamports=int(
            row[
                "cumulative_realized_pnl_lamports"
            ]
        ),
        network_fee_lamports=int(
            row["network_fee_lamports"]
        ),
        wallet_pre_balance_lamports=int(
            row[
                "wallet_pre_balance_lamports"
            ]
        ),
        wallet_post_balance_lamports=int(
            row[
                "wallet_post_balance_lamports"
            ]
        ),
        wallet_balance_delta_lamports=int(
            row[
                "wallet_balance_delta_lamports"
            ]
        ),
        trade_event_sol_amount=int(
            row[
                "trade_event_sol_amount"
            ]
        ),
        protocol_fee_lamports=int(
            row[
                "protocol_fee_lamports"
            ]
        ),
        creator_fee_lamports=int(
            row[
                "creator_fee_lamports"
            ]
        ),
        cashback_lamports=int(
            row["cashback_lamports"]
        ),
        buyback_fee_lamports=int(
            row[
                "buyback_fee_lamports"
            ]
        ),
        quote_amount=int(
            row["quote_amount"]
        ),
        created_at=float(
            row["created_at"]
        ),
        updated_at=float(
            row["updated_at"]
        ),
    )


def load_live_position_read_only(
    *,
    reservation_id: str,
    db_path: Path = DB_PATH,
) -> LivePosition | None:
    if not _nonempty_string(
        reservation_id
    ):
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
        try:
            row = connection.execute(
                """
                SELECT *
                FROM live_positions
                WHERE reservation_id = ?
                """,
                (
                    reservation_id.strip(),
                ),
            ).fetchone()

        except sqlite3.OperationalError as error:
            if (
                "no such table: live_positions"
                in str(error)
            ):
                return None

            raise

        if row is None:
            return None

        return _row_to_position(
            row
        )

    finally:
        connection.close()


def load_live_position_risk_totals_read_only(
    *,
    wallet_pubkey: str,
    db_path: Path = DB_PATH,
) -> LivePositionRiskTotalsResult:
    """
    Resolve authoritative OPEN live-position risk
    for one exact wallet without mutating local state.

    Exposure is the SUM of each OPEN lot's current
    remaining_exposure_lamports.

    Missing/unreadable state is UNKNOWN rather than
    being interpreted as zero exposure.
    """

    normalized_wallet = ""

    def finish(
        status: str,
        *reasons: str,
        open_exposure_lamports: int | None = None,
        open_positions: int | None = None,
    ) -> LivePositionRiskTotalsResult:
        return LivePositionRiskTotalsResult(
            loader_version=(
                LIVE_POSITION_RISK_TOTALS_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=normalized_wallet,
            open_exposure_lamports=(
                open_exposure_lamports
            ),
            open_positions=open_positions,
        )

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

    try:
        database_exists = (
            db_path.exists()
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_DATABASE_PATH_INVALID",
        )

    if not database_exists:
        return finish(
            UNKNOWN,
            "LIVE_DATABASE_NOT_FOUND",
        )

    try:
        database_uri = (
            db_path.resolve().as_uri()
            + "?mode=ro"
        )

        connection = sqlite3.connect(
            database_uri,
            uri=True,
            timeout=30.0,
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_POSITION_RISK_READ_FAILED",
        )

    connection.row_factory = sqlite3.Row

    try:
        connection.execute(
            "PRAGMA query_only = ON"
        )

        connection.execute(
            "PRAGMA busy_timeout = 30000"
        )

        try:
            rows = connection.execute(
                """
                SELECT
                    position_version,
                    remaining_exposure_lamports

                FROM live_positions

                WHERE wallet_pubkey = ?
                  AND status = ?
                """,
                (
                    normalized_wallet,
                    OPEN,
                ),
            ).fetchall()

        except sqlite3.OperationalError as error:
            if (
                "no such table: live_positions"
                in str(error)
            ):
                return finish(
                    UNKNOWN,
                    "LIVE_POSITIONS_TABLE_NOT_FOUND",
                )

            return finish(
                UNKNOWN,
                "LIVE_POSITION_RISK_READ_FAILED",
            )

        except sqlite3.Error:
            return finish(
                UNKNOWN,
                "LIVE_POSITION_RISK_READ_FAILED",
            )

        total_exposure = 0
        open_position_count = 0

        for row in rows:
            if (
                str(
                    row["position_version"]
                )
                != LIVE_POSITION_VERSION
            ):
                return finish(
                    UNKNOWN,
                    "LIVE_POSITION_VERSION_MISMATCH",
                )

            exposure = row[
                "remaining_exposure_lamports"
            ]

            if not _strict_nonnegative_int(
                exposure
            ):
                return finish(
                    UNKNOWN,
                    "LIVE_POSITION_EXPOSURE_INVALID",
                )

            total_exposure += int(
                exposure
            )

            if (
                total_exposure
                > SQLITE_INT_MAX
            ):
                return finish(
                    UNKNOWN,
                    "LIVE_POSITION_EXPOSURE_OVERFLOW",
                )

            open_position_count += 1

            if (
                open_position_count
                > SQLITE_INT_MAX
            ):
                return finish(
                    UNKNOWN,
                    "LIVE_POSITION_COUNT_OVERFLOW",
                )

        return finish(
            PASS,
            open_exposure_lamports=(
                total_exposure
            ),
            open_positions=(
                open_position_count
            ),
        )

    finally:
        connection.close()


def _position_entry_matches(
    *,
    row: sqlite3.Row,
    reservation_row: sqlite3.Row,
    fill_resolver_version: str,
    transaction_signature: str,
    signed_transaction_sha256: str,
    observed_transaction_sha256: str,
    entry_slot: int,
    block_time: int | None,
    base_token_program: str,
    associated_base_user: str,
    quote_mint: str,
    authorized_token_amount: int,
    trade_event_token_amount: int,
    token_pre_amount: int,
    token_post_amount: int,
    token_delta: int,
    max_sol_cost: int,
    wallet_cost_lamports: int,
    fee_lamports: int,
    wallet_pre_balance_lamports: int,
    wallet_post_balance_lamports: int,
    wallet_balance_delta_lamports: int,
    trade_event_sol_amount: int,
    protocol_fee_lamports: int,
    creator_fee_lamports: int,
    cashback_lamports: int,
    buyback_fee_lamports: int,
    quote_amount: int,
) -> bool:
    return (
        str(
            row["position_version"]
        )
        == LIVE_POSITION_VERSION

        and str(
            row["reservation_id"]
        )
        == str(
            reservation_row[
                "reservation_id"
            ]
        )

        and str(
            row["entry_signature"]
        )
        == transaction_signature

        and str(
            row["wallet_pubkey"]
        )
        == str(
            reservation_row[
                "wallet_pubkey"
            ]
        )

        and str(
            row["mint"]
        )
        == str(
            reservation_row["mint"]
        )

        and str(
            row["fill_resolver_version"]
        )
        == fill_resolver_version

        and str(
            row[
                "signed_transaction_sha256"
            ]
        )
        == signed_transaction_sha256

        and str(
            row[
                "observed_transaction_sha256"
            ]
        )
        == observed_transaction_sha256

        and int(
            row["entry_slot"]
        )
        == entry_slot

        and (
            (
                row["entry_block_time"]
                is None
                and block_time is None
            )
            or (
                row["entry_block_time"]
                is not None
                and block_time is not None
                and int(
                    row[
                        "entry_block_time"
                    ]
                )
                == block_time
            )
        )

        and str(
            row["base_token_program"]
        )
        == base_token_program

        and str(
            row[
                "associated_base_user"
            ]
        )
        == associated_base_user

        and str(
            row["quote_mint"]
        )
        == quote_mint

        and int(
            row[
                "authorized_token_amount"
            ]
        )
        == authorized_token_amount

        and int(
            row[
                "trade_event_token_amount"
            ]
        )
        == trade_event_token_amount

        and int(
            row[
                "token_pre_amount"
            ]
        )
        == token_pre_amount

        and int(
            row[
                "token_post_amount"
            ]
        )
        == token_post_amount

        and int(
            row["entry_tokens"]
        )
        == token_delta

        and int(
            row[
                "authorized_max_sol_cost_lamports"
            ]
        )
        == max_sol_cost

        and int(
            row[
                "entry_exposure_lamports"
            ]
        )
        == int(
            reservation_row[
                "spend_lamports"
            ]
        )

        and int(
            row[
                "entry_wallet_cost_lamports"
            ]
        )
        == wallet_cost_lamports

        and int(
            row["network_fee_lamports"]
        )
        == fee_lamports

        and int(
            row[
                "wallet_pre_balance_lamports"
            ]
        )
        == wallet_pre_balance_lamports

        and int(
            row[
                "wallet_post_balance_lamports"
            ]
        )
        == wallet_post_balance_lamports

        and int(
            row[
                "wallet_balance_delta_lamports"
            ]
        )
        == wallet_balance_delta_lamports

        and int(
            row[
                "trade_event_sol_amount"
            ]
        )
        == trade_event_sol_amount

        and int(
            row[
                "protocol_fee_lamports"
            ]
        )
        == protocol_fee_lamports

        and int(
            row[
                "creator_fee_lamports"
            ]
        )
        == creator_fee_lamports

        and int(
            row["cashback_lamports"]
        )
        == cashback_lamports

        and int(
            row[
                "buyback_fee_lamports"
            ]
        )
        == buyback_fee_lamports

        and int(
            row["quote_amount"]
        )
        == quote_amount
    )


def _journal_success_matches(
    *,
    row: sqlite3.Row,
    reservation_row: sqlite3.Row,
    transaction_signature: str,
    signed_transaction_sha256: str,
    observed_transaction_sha256: str,
    entry_slot: int,
    block_time: int | None,
    fee_lamports: int,
    wallet_pre_balance_lamports: int,
    wallet_post_balance_lamports: int,
    wallet_balance_delta_lamports: int,
) -> bool:
    return (
        str(
            row["journal_version"]
        )
        == LIVE_TRANSACTION_JOURNAL_VERSION

        and str(
            row["transaction_signature"]
        )
        == transaction_signature

        and str(
            row["reservation_id"]
        )
        == str(
            reservation_row[
                "reservation_id"
            ]
        )

        and str(
            row["wallet_pubkey"]
        )
        == str(
            reservation_row[
                "wallet_pubkey"
            ]
        )

        and str(
            row["mint"]
        )
        == str(
            reservation_row["mint"]
        )

        and str(
            row["side"]
        )
        == BUY

        and str(
            row[
                "signed_transaction_sha256"
            ]
        )
        == signed_transaction_sha256

        and str(
            row[
                "receipt_transaction_sha256"
            ]
        )
        == observed_transaction_sha256

        and str(
            row[
                "receipt_resolver_version"
            ]
        )
        == SIGNED_TRANSACTION_RECEIPT_VERSION

        and int(
            row["slot"]
        )
        == entry_slot

        and (
            (
                row["block_time"] is None
                and block_time is None
            )
            or (
                row["block_time"] is not None
                and block_time is not None
                and int(
                    row["block_time"]
                )
                == block_time
            )
        )

        and str(
            row["outcome"]
        )
        == SUCCESS

        and str(
            row[
                "transaction_error_json"
            ]
        )
        == "null"

        and int(
            row["fee_lamports"]
        )
        == fee_lamports

        and str(
            row["fee_payer_pubkey"]
        )
        == str(
            reservation_row[
                "wallet_pubkey"
            ]
        )

        and int(
            row[
                "fee_payer_pre_balance_lamports"
            ]
        )
        == wallet_pre_balance_lamports

        and int(
            row[
                "fee_payer_post_balance_lamports"
            ]
        )
        == wallet_post_balance_lamports

        and int(
            row[
                "fee_payer_balance_delta_lamports"
            ]
        )
        == wallet_balance_delta_lamports

        and int(
            row[
                "last_valid_block_height"
            ]
        )
        == int(
            reservation_row[
                "last_valid_block_height"
            ]
        )

        and int(
            row["blockhash_rpc_slot"]
        )
        == int(
            reservation_row[
                "blockhash_rpc_slot"
            ]
        )
    )


def record_successful_buy_and_open_position(
    *,
    reservation_id: str,

    fill_resolver_version: str,

    transaction_signature: str,

    signed_transaction_sha256: str,
    observed_transaction_sha256: str,

    entry_slot: int,
    block_time: int | None,

    mint: str,
    wallet_pubkey: str,

    base_token_program: str,
    associated_base_user: str,

    authorized_token_amount: int,
    max_sol_cost: int,

    trade_event_token_amount: int,

    token_pre_amount: int,
    token_post_amount: int,
    token_delta: int,

    fee_lamports: int,

    wallet_pre_balance_lamports: int,
    wallet_post_balance_lamports: int,
    wallet_balance_delta_lamports: int,
    wallet_cost_lamports: int,

    trade_event_sol_amount: int,
    protocol_fee_lamports: int,
    creator_fee_lamports: int,
    cashback_lamports: int,
    buyback_fee_lamports: int,

    quote_mint: str,
    quote_amount: int,

    db_path: Path = DB_PATH,
) -> SuccessfulBuyAccountingResult:
    """
    Atomically convert one proven successful Pump
    BUY reservation into durable live exposure.

    One successful commit performs all three:

    1. immutable SUCCESS transaction journal row
    2. OPEN live-position entry lot
    3. SIGNED/SUBMITTED reservation -> RELEASED

    No RPC, signing, submission, strategy or marking
    authority exists here.
    """

    if not _nonempty_string(
        reservation_id
    ):
        return SuccessfulBuyAccountingResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_RESERVATION_ID",
            ),
            position=None,
            reservation_id="",
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    reservation_id = (
        reservation_id.strip()
    )

    string_fields = {
        "fill_resolver_version":
            fill_resolver_version,

        "transaction_signature":
            transaction_signature,

        "mint":
            mint,

        "wallet_pubkey":
            wallet_pubkey,

        "base_token_program":
            base_token_program,

        "associated_base_user":
            associated_base_user,

        "quote_mint":
            quote_mint,
    }

    if any(
        not _nonempty_string(value)
        for value in string_fields.values()
    ):
        return SuccessfulBuyAccountingResult(
            status=UNKNOWN,
            reasons=(
                "SUCCESSFUL_BUY_EVIDENCE_STRING_INVALID",
            ),
            position=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    if (
        fill_resolver_version
        != SUCCESSFUL_PUMP_BUY_FILL_VERSION
    ):
        return SuccessfulBuyAccountingResult(
            status=BLOCK,
            reasons=(
                "SUCCESSFUL_BUY_FILL_VERSION_MISMATCH",
            ),
            position=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    if (
        not _valid_sha256(
            signed_transaction_sha256
        )
        or not _valid_sha256(
            observed_transaction_sha256
        )
        or observed_transaction_sha256
        != signed_transaction_sha256
    ):
        return SuccessfulBuyAccountingResult(
            status=UNKNOWN,
            reasons=(
                "SUCCESSFUL_BUY_TRANSACTION_IDENTITY_INVALID",
            ),
            position=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    if not _strict_nonnegative_int(
        entry_slot
    ):
        return SuccessfulBuyAccountingResult(
            status=UNKNOWN,
            reasons=(
                "SUCCESSFUL_BUY_SLOT_INVALID",
            ),
            position=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    if (
        block_time is not None
        and not _strict_nonnegative_int(
            block_time
        )
    ):
        return SuccessfulBuyAccountingResult(
            status=UNKNOWN,
            reasons=(
                "SUCCESSFUL_BUY_BLOCK_TIME_INVALID",
            ),
            position=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    positive_fields = (
        authorized_token_amount,
        max_sol_cost,
        trade_event_token_amount,
        token_post_amount,
        token_delta,
        wallet_cost_lamports,
    )

    if any(
        not _strict_positive_int(value)
        for value in positive_fields
    ):
        return SuccessfulBuyAccountingResult(
            status=UNKNOWN,
            reasons=(
                "SUCCESSFUL_BUY_POSITIVE_AMOUNT_INVALID",
            ),
            position=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    nonnegative_fields = (
        token_pre_amount,
        fee_lamports,
        wallet_pre_balance_lamports,
        wallet_post_balance_lamports,
        trade_event_sol_amount,
        protocol_fee_lamports,
        creator_fee_lamports,
        cashback_lamports,
        buyback_fee_lamports,
        quote_amount,
    )

    if any(
        not _strict_nonnegative_int(value)
        for value in nonnegative_fields
    ):
        return SuccessfulBuyAccountingResult(
            status=UNKNOWN,
            reasons=(
                "SUCCESSFUL_BUY_NONNEGATIVE_AMOUNT_INVALID",
            ),
            position=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    if (
        not isinstance(
            wallet_balance_delta_lamports,
            int,
        )
        or isinstance(
            wallet_balance_delta_lamports,
            bool,
        )
        or abs(
            wallet_balance_delta_lamports
        )
        > SQLITE_INT_MAX
    ):
        return SuccessfulBuyAccountingResult(
            status=UNKNOWN,
            reasons=(
                "SUCCESSFUL_BUY_WALLET_DELTA_INVALID",
            ),
            position=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    if (
        wallet_post_balance_lamports
        - wallet_pre_balance_lamports
        != wallet_balance_delta_lamports
        or wallet_balance_delta_lamports
        >= 0
        or -wallet_balance_delta_lamports
        != wallet_cost_lamports
    ):
        return SuccessfulBuyAccountingResult(
            status=UNKNOWN,
            reasons=(
                "SUCCESSFUL_BUY_WALLET_COST_MISMATCH",
            ),
            position=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    if fee_lamports > wallet_cost_lamports:
        return SuccessfulBuyAccountingResult(
            status=UNKNOWN,
            reasons=(
                "SUCCESSFUL_BUY_FEE_EXCEEDS_WALLET_COST",
            ),
            position=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    if (
        authorized_token_amount
        != trade_event_token_amount
        or authorized_token_amount
        != token_delta
        or (
            token_post_amount
            - token_pre_amount
        )
        != token_delta
    ):
        return SuccessfulBuyAccountingResult(
            status=UNKNOWN,
            reasons=(
                "SUCCESSFUL_BUY_TOKEN_EVIDENCE_MISMATCH",
            ),
            position=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    if (
        quote_mint.strip()
        != str(
            WRAPPED_SOL_MINT
        )
    ):
        return SuccessfulBuyAccountingResult(
            status=BLOCK,
            reasons=(
                "SUCCESSFUL_BUY_NON_SOL_QUOTE_MINT",
            ),
            position=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    connection = get_connection(
        db_path
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        init_reservation_schema(
            connection
        )

        init_journal_schema(
            connection
        )

        init_schema(
            connection
        )

        reservation_row = (
            connection.execute(
                """
                SELECT *
                FROM live_capital_reservations
                WHERE reservation_id = ?
                """,
                (
                    reservation_id,
                ),
            ).fetchone()
        )

        if reservation_row is None:
            connection.commit()

            return SuccessfulBuyAccountingResult(
                status=UNKNOWN,
                reasons=(
                    "RESERVATION_NOT_FOUND",
                ),
                position=None,
                reservation_id=reservation_id,
                reservation_status=None,
                terminal_at=None,
                terminal_reason=None,
                changed=False,
            )

        reservation_status = str(
            reservation_row["status"]
        )

        terminal_at = (
            None
            if reservation_row[
                "terminal_at"
            ]
            is None
            else float(
                reservation_row[
                    "terminal_at"
                ]
            )
        )

        terminal_reason = (
            None
            if reservation_row[
                "terminal_reason"
            ]
            is None
            else str(
                reservation_row[
                    "terminal_reason"
                ]
            )
        )

        journal_row = connection.execute(
            """
            SELECT *
            FROM live_transaction_journal
            WHERE reservation_id = ?
               OR transaction_signature = ?
            """,
            (
                reservation_id,
                transaction_signature,
            ),
        ).fetchone()

        position_row = connection.execute(
            """
            SELECT *
            FROM live_positions
            WHERE reservation_id = ?
               OR entry_signature = ?
            """,
            (
                reservation_id,
                transaction_signature,
            ),
        ).fetchone()

        #
        # Exact already-completed recovery.
        #
        if reservation_status == RELEASED:
            if (
                terminal_reason
                != RECONCILED_SUCCESSFUL_BUY_REASON
            ):
                connection.commit()

                return SuccessfulBuyAccountingResult(
                    status=BLOCK,
                    reasons=(
                        "SUCCESSFUL_BUY_TERMINAL_REASON_MISMATCH",
                    ),
                    position=(
                        None
                        if position_row is None
                        else _row_to_position(
                            position_row
                        )
                    ),
                    reservation_id=reservation_id,
                    reservation_status=(
                        reservation_status
                    ),
                    terminal_at=terminal_at,
                    terminal_reason=(
                        terminal_reason
                    ),
                    changed=False,
                )

            coherent = (
                terminal_at is not None

                and journal_row is not None

                and position_row is not None

                and _journal_success_matches(
                    row=journal_row,
                    reservation_row=(
                        reservation_row
                    ),
                    transaction_signature=(
                        transaction_signature
                    ),
                    signed_transaction_sha256=(
                        signed_transaction_sha256
                    ),
                    observed_transaction_sha256=(
                        observed_transaction_sha256
                    ),
                    entry_slot=entry_slot,
                    block_time=block_time,
                    fee_lamports=(
                        fee_lamports
                    ),
                    wallet_pre_balance_lamports=(
                        wallet_pre_balance_lamports
                    ),
                    wallet_post_balance_lamports=(
                        wallet_post_balance_lamports
                    ),
                    wallet_balance_delta_lamports=(
                        wallet_balance_delta_lamports
                    ),
                )

                and _position_entry_matches(
                    row=position_row,
                    reservation_row=(
                        reservation_row
                    ),
                    fill_resolver_version=(
                        fill_resolver_version
                    ),
                    transaction_signature=(
                        transaction_signature
                    ),
                    signed_transaction_sha256=(
                        signed_transaction_sha256
                    ),
                    observed_transaction_sha256=(
                        observed_transaction_sha256
                    ),
                    entry_slot=entry_slot,
                    block_time=block_time,
                    base_token_program=(
                        base_token_program
                    ),
                    associated_base_user=(
                        associated_base_user
                    ),
                    quote_mint=quote_mint,
                    authorized_token_amount=(
                        authorized_token_amount
                    ),
                    trade_event_token_amount=(
                        trade_event_token_amount
                    ),
                    token_pre_amount=(
                        token_pre_amount
                    ),
                    token_post_amount=(
                        token_post_amount
                    ),
                    token_delta=token_delta,
                    max_sol_cost=max_sol_cost,
                    wallet_cost_lamports=(
                        wallet_cost_lamports
                    ),
                    fee_lamports=(
                        fee_lamports
                    ),
                    wallet_pre_balance_lamports=(
                        wallet_pre_balance_lamports
                    ),
                    wallet_post_balance_lamports=(
                        wallet_post_balance_lamports
                    ),
                    wallet_balance_delta_lamports=(
                        wallet_balance_delta_lamports
                    ),
                    trade_event_sol_amount=(
                        trade_event_sol_amount
                    ),
                    protocol_fee_lamports=(
                        protocol_fee_lamports
                    ),
                    creator_fee_lamports=(
                        creator_fee_lamports
                    ),
                    cashback_lamports=(
                        cashback_lamports
                    ),
                    buyback_fee_lamports=(
                        buyback_fee_lamports
                    ),
                    quote_amount=quote_amount,
                )

                and float(
                    journal_row[
                        "recorded_at"
                    ]
                )
                == terminal_at

                and float(
                    position_row["created_at"]
                )
                == terminal_at
            )

            if not coherent:
                connection.commit()

                return SuccessfulBuyAccountingResult(
                    status=UNKNOWN,
                    reasons=(
                        "SUCCESSFUL_BUY_TERMINAL_STATE_INCOHERENT",
                    ),
                    position=(
                        None
                        if position_row is None
                        else _row_to_position(
                            position_row
                        )
                    ),
                    reservation_id=reservation_id,
                    reservation_status=(
                        reservation_status
                    ),
                    terminal_at=terminal_at,
                    terminal_reason=(
                        terminal_reason
                    ),
                    changed=False,
                )

            connection.commit()

            return SuccessfulBuyAccountingResult(
                status=PASS,
                reasons=(),
                position=_row_to_position(
                    position_row
                ),
                reservation_id=reservation_id,
                reservation_status=(
                    reservation_status
                ),
                terminal_at=terminal_at,
                terminal_reason=(
                    terminal_reason
                ),
                changed=False,
            )

        if reservation_status not in (
            SIGNED,
            SUBMITTED,
        ):
            connection.commit()

            return SuccessfulBuyAccountingResult(
                status=BLOCK,
                reasons=(
                    "RESERVATION_NOT_RECONCILABLE",
                ),
                position=(
                    None
                    if position_row is None
                    else _row_to_position(
                        position_row
                    )
                ),
                reservation_id=reservation_id,
                reservation_status=(
                    reservation_status
                ),
                terminal_at=terminal_at,
                terminal_reason=(
                    terminal_reason
                ),
                changed=False,
            )

        if not _submission_metadata_coherent(
            reservation_row
        ):
            connection.commit()

            return SuccessfulBuyAccountingResult(
                status=BLOCK,
                reasons=(
                    "SUBMISSION_METADATA_INCONSISTENT",
                ),
                position=None,
                reservation_id=reservation_id,
                reservation_status=(
                    reservation_status
                ),
                terminal_at=terminal_at,
                terminal_reason=(
                    terminal_reason
                ),
                changed=False,
            )

        reservation_evidence_matches = (
            str(
                reservation_row[
                    "reservation_version"
                ]
            )
            == RESERVATION_VERSION

            and str(
                reservation_row["side"]
            )
            == BUY

            and str(
                reservation_row["mint"]
            )
            == mint

            and str(
                reservation_row[
                    "wallet_pubkey"
                ]
            )
            == wallet_pubkey

            and str(
                reservation_row[
                    "transaction_signature"
                ]
            )
            == transaction_signature

            and str(
                reservation_row[
                    "signed_transaction_sha256"
                ]
            )
            == signed_transaction_sha256

            and _strict_positive_int(
                reservation_row[
                    "spend_lamports"
                ]
            )

            and int(
                reservation_row[
                    "spend_lamports"
                ]
            )
            == max_sol_cost

            and _strict_positive_int(
                reservation_row[
                    "wallet_cost_lamports"
                ]
            )

            and wallet_cost_lamports
            <= int(
                reservation_row[
                    "wallet_cost_lamports"
                ]
            )

            and _strict_nonnegative_int(
                reservation_row[
                    "last_valid_block_height"
                ]
            )

            and _strict_nonnegative_int(
                reservation_row[
                    "blockhash_rpc_slot"
                ]
            )
        )

        if not reservation_evidence_matches:
            connection.commit()

            return SuccessfulBuyAccountingResult(
                status=BLOCK,
                reasons=(
                    "SUCCESSFUL_BUY_RESERVATION_EVIDENCE_MISMATCH",
                ),
                position=None,
                reservation_id=reservation_id,
                reservation_status=(
                    reservation_status
                ),
                terminal_at=terminal_at,
                terminal_reason=(
                    terminal_reason
                ),
                changed=False,
            )

        #
        # Neither record may predate the atomic
        # successful transition.
        #
        if (
            journal_row is not None
            or position_row is not None
        ):
            connection.commit()

            return SuccessfulBuyAccountingResult(
                status=UNKNOWN,
                reasons=(
                    "SUCCESSFUL_BUY_ACCOUNTING_STATE_INCONSISTENT",
                ),
                position=(
                    None
                    if position_row is None
                    else _row_to_position(
                        position_row
                    )
                ),
                reservation_id=reservation_id,
                reservation_status=(
                    reservation_status
                ),
                terminal_at=terminal_at,
                terminal_reason=(
                    terminal_reason
                ),
                changed=False,
            )

        now = time.time()

        #
        # Immutable chain outcome journal.
        #
        connection.execute(
            """
            INSERT INTO live_transaction_journal (
                transaction_signature,
                reservation_id,
                journal_version,
                wallet_pubkey,
                mint,
                side,
                signed_transaction_sha256,
                receipt_transaction_sha256,
                receipt_resolver_version,
                slot,
                block_time,
                outcome,
                transaction_error_json,
                fee_lamports,
                fee_payer_pubkey,
                fee_payer_pre_balance_lamports,
                fee_payer_post_balance_lamports,
                fee_payer_balance_delta_lamports,
                last_valid_block_height,
                blockhash_rpc_slot,
                recorded_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                transaction_signature,
                reservation_id,
                LIVE_TRANSACTION_JOURNAL_VERSION,
                wallet_pubkey,
                mint,
                BUY,
                signed_transaction_sha256,
                observed_transaction_sha256,
                SIGNED_TRANSACTION_RECEIPT_VERSION,
                entry_slot,
                block_time,
                SUCCESS,
                "null",
                fee_lamports,
                wallet_pubkey,
                wallet_pre_balance_lamports,
                wallet_post_balance_lamports,
                wallet_balance_delta_lamports,
                int(
                    reservation_row[
                        "last_valid_block_height"
                    ]
                ),
                int(
                    reservation_row[
                        "blockhash_rpc_slot"
                    ]
                ),
                now,
            ),
        )

        #
        # Durable live entry lot.
        #
        cursor = connection.execute(
            """
            INSERT INTO live_positions (
                position_version,
                reservation_id,
                entry_signature,
                wallet_pubkey,
                mint,
                status,
                fill_resolver_version,
                signed_transaction_sha256,
                observed_transaction_sha256,
                entry_slot,
                entry_block_time,
                base_token_program,
                associated_base_user,
                quote_mint,
                authorized_token_amount,
                trade_event_token_amount,
                token_pre_amount,
                token_post_amount,
                entry_tokens,
                tokens_held,
                authorized_max_sol_cost_lamports,
                entry_exposure_lamports,
                remaining_exposure_lamports,
                entry_wallet_cost_lamports,
                remaining_cost_basis_lamports,
                cumulative_net_proceeds_lamports,
                cumulative_realized_pnl_lamports,
                network_fee_lamports,
                wallet_pre_balance_lamports,
                wallet_post_balance_lamports,
                wallet_balance_delta_lamports,
                trade_event_sol_amount,
                protocol_fee_lamports,
                creator_fee_lamports,
                cashback_lamports,
                buyback_fee_lamports,
                quote_amount,
                created_at,
                updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
            """,
            (
                LIVE_POSITION_VERSION,
                reservation_id,
                transaction_signature,
                wallet_pubkey,
                mint,
                OPEN,
                fill_resolver_version,
                signed_transaction_sha256,
                observed_transaction_sha256,
                entry_slot,
                block_time,
                base_token_program,
                associated_base_user,
                quote_mint,
                authorized_token_amount,
                trade_event_token_amount,
                token_pre_amount,
                token_post_amount,
                token_delta,
                token_delta,
                max_sol_cost,
                int(
                    reservation_row[
                        "spend_lamports"
                    ]
                ),
                int(
                    reservation_row[
                        "spend_lamports"
                    ]
                ),
                wallet_cost_lamports,
                wallet_cost_lamports,
                0,
                0,
                fee_lamports,
                wallet_pre_balance_lamports,
                wallet_post_balance_lamports,
                wallet_balance_delta_lamports,
                trade_event_sol_amount,
                protocol_fee_lamports,
                creator_fee_lamports,
                cashback_lamports,
                buyback_fee_lamports,
                quote_amount,
                now,
                now,
            ),
        )

        position_id = int(
            cursor.lastrowid
        )

        #
        # Reservation stops holding capital only after
        # both durable accounting records exist in
        # this same transaction.
        #
        updated = connection.execute(
            """
            UPDATE live_capital_reservations

            SET
                status = ?,
                terminal_at = ?,
                terminal_reason = ?

            WHERE reservation_id = ?
              AND status IN (?, ?)
              AND side = ?
              AND transaction_signature = ?
              AND signed_transaction_sha256 = ?
              AND wallet_pubkey = ?
              AND mint = ?
              AND spend_lamports = ?
            """,
            (
                RELEASED,
                now,
                RECONCILED_SUCCESSFUL_BUY_REASON,
                reservation_id,
                SIGNED,
                SUBMITTED,
                BUY,
                transaction_signature,
                signed_transaction_sha256,
                wallet_pubkey,
                mint,
                max_sol_cost,
            ),
        )

        if updated.rowcount != 1:
            connection.rollback()

            return SuccessfulBuyAccountingResult(
                status=UNKNOWN,
                reasons=(
                    "SUCCESSFUL_BUY_RELEASE_TRANSITION_FAILED",
                ),
                position=None,
                reservation_id=reservation_id,
                reservation_status=(
                    reservation_status
                ),
                terminal_at=terminal_at,
                terminal_reason=(
                    terminal_reason
                ),
                changed=False,
            )

        persisted_journal = (
            connection.execute(
                """
                SELECT *
                FROM live_transaction_journal
                WHERE reservation_id = ?
                """,
                (
                    reservation_id,
                ),
            ).fetchone()
        )

        persisted_position = (
            connection.execute(
                """
                SELECT *
                FROM live_positions
                WHERE position_id = ?
                """,
                (
                    position_id,
                ),
            ).fetchone()
        )

        persisted_reservation = (
            connection.execute(
                """
                SELECT *
                FROM live_capital_reservations
                WHERE reservation_id = ?
                """,
                (
                    reservation_id,
                ),
            ).fetchone()
        )

        if (
            persisted_journal is None
            or persisted_position is None
            or persisted_reservation is None
        ):
            connection.rollback()

            return SuccessfulBuyAccountingResult(
                status=UNKNOWN,
                reasons=(
                    "SUCCESSFUL_BUY_ATOMIC_VERIFICATION_MISSING",
                ),
                position=None,
                reservation_id=reservation_id,
                reservation_status=(
                    reservation_status
                ),
                terminal_at=terminal_at,
                terminal_reason=(
                    terminal_reason
                ),
                changed=False,
            )

        if (
            str(
                persisted_reservation[
                    "status"
                ]
            )
            != RELEASED

            or str(
                persisted_reservation[
                    "terminal_reason"
                ]
            )
            != RECONCILED_SUCCESSFUL_BUY_REASON

            or float(
                persisted_reservation[
                    "terminal_at"
                ]
            )
            != now

            or not _journal_success_matches(
                row=persisted_journal,
                reservation_row=(
                    persisted_reservation
                ),
                transaction_signature=(
                    transaction_signature
                ),
                signed_transaction_sha256=(
                    signed_transaction_sha256
                ),
                observed_transaction_sha256=(
                    observed_transaction_sha256
                ),
                entry_slot=entry_slot,
                block_time=block_time,
                fee_lamports=(
                    fee_lamports
                ),
                wallet_pre_balance_lamports=(
                    wallet_pre_balance_lamports
                ),
                wallet_post_balance_lamports=(
                    wallet_post_balance_lamports
                ),
                wallet_balance_delta_lamports=(
                    wallet_balance_delta_lamports
                ),
            )

            or not _position_entry_matches(
                row=persisted_position,
                reservation_row=(
                    persisted_reservation
                ),
                fill_resolver_version=(
                    fill_resolver_version
                ),
                transaction_signature=(
                    transaction_signature
                ),
                signed_transaction_sha256=(
                    signed_transaction_sha256
                ),
                observed_transaction_sha256=(
                    observed_transaction_sha256
                ),
                entry_slot=entry_slot,
                block_time=block_time,
                base_token_program=(
                    base_token_program
                ),
                associated_base_user=(
                    associated_base_user
                ),
                quote_mint=quote_mint,
                authorized_token_amount=(
                    authorized_token_amount
                ),
                trade_event_token_amount=(
                    trade_event_token_amount
                ),
                token_pre_amount=(
                    token_pre_amount
                ),
                token_post_amount=(
                    token_post_amount
                ),
                token_delta=token_delta,
                max_sol_cost=max_sol_cost,
                wallet_cost_lamports=(
                    wallet_cost_lamports
                ),
                fee_lamports=(
                    fee_lamports
                ),
                wallet_pre_balance_lamports=(
                    wallet_pre_balance_lamports
                ),
                wallet_post_balance_lamports=(
                    wallet_post_balance_lamports
                ),
                wallet_balance_delta_lamports=(
                    wallet_balance_delta_lamports
                ),
                trade_event_sol_amount=(
                    trade_event_sol_amount
                ),
                protocol_fee_lamports=(
                    protocol_fee_lamports
                ),
                creator_fee_lamports=(
                    creator_fee_lamports
                ),
                cashback_lamports=(
                    cashback_lamports
                ),
                buyback_fee_lamports=(
                    buyback_fee_lamports
                ),
                quote_amount=quote_amount,
            )

            or str(
                persisted_position[
                    "status"
                ]
            )
            != OPEN

            or int(
                persisted_position[
                    "tokens_held"
                ]
            )
            != token_delta

            or int(
                persisted_position[
                    "remaining_exposure_lamports"
                ]
            )
            != max_sol_cost

            or int(
                persisted_position[
                    "remaining_cost_basis_lamports"
                ]
            )
            != wallet_cost_lamports

            or float(
                persisted_journal[
                    "recorded_at"
                ]
            )
            != now

            or float(
                persisted_position[
                    "created_at"
                ]
            )
            != now

            or float(
                persisted_position[
                    "updated_at"
                ]
            )
            != now
        ):
            connection.rollback()

            return SuccessfulBuyAccountingResult(
                status=UNKNOWN,
                reasons=(
                    "SUCCESSFUL_BUY_ATOMIC_VERIFICATION_FAILED",
                ),
                position=None,
                reservation_id=reservation_id,
                reservation_status=(
                    reservation_status
                ),
                terminal_at=terminal_at,
                terminal_reason=(
                    terminal_reason
                ),
                changed=False,
            )

        connection.commit()

        return SuccessfulBuyAccountingResult(
            status=PASS,
            reasons=(),
            position=_row_to_position(
                persisted_position
            ),
            reservation_id=reservation_id,
            reservation_status=RELEASED,
            terminal_at=now,
            terminal_reason=(
                RECONCILED_SUCCESSFUL_BUY_REASON
            ),
            changed=True,
        )

    except sqlite3.Error:
        connection.rollback()

        return SuccessfulBuyAccountingResult(
            status=UNKNOWN,
            reasons=(
                "SUCCESSFUL_BUY_DATABASE_ERROR",
            ),
            position=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    finally:
        connection.close()
