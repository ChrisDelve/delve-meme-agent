from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solders.pubkey import Pubkey

from src.execution.live_pump_sell_authorization import (
    LivePumpSellAuthorization,
)
from src.execution.live_sell_transaction_receipt import (
    LIVE_SELL_TRANSACTION_RECEIPT_VERSION,
    RESOLVED,
)
from src.execution.order_authorization import (
    WRAPPED_SOL_MINT,
)
from src.execution.successful_pump_sell_fill import (
    PROVEN,
    SUCCESSFUL_PUMP_SELL_FILL_VERSION,
    SuccessfulPumpSellFillResult,
)
from src.portfolio.live_positions import (
    CLOSED,
    LIVE_POSITION_VERSION,
    OPEN,
    init_schema as init_position_schema,
)
from src.portfolio.live_reservations import (
    DB_PATH,
    SQLITE_INT_MAX,
    get_connection,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    CONSUMED,
    RELEASED,
    _authorization_contract_valid,
    _claim_identity_matches_authorization,
    _claim_matches_authorization,
    _load_claim,
    init_schema as init_claim_schema,
)
from src.portfolio.live_sell_execution_records import (
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED,
    _record_contract_valid,
    _row_to_record,
    init_schema as init_execution_schema,
)
from src.portfolio.live_sell_transaction_journal import (
    LIVE_SELL_TRANSACTION_JOURNAL_VERSION,
    SUCCESS,
    LiveSellTransactionJournalEntry,
    _row_to_entry,
    init_schema as init_journal_schema,
)
from src.safety.token_safety_resolver import (
    TOKEN_PROGRAM,
    derive_associated_token_account,
)


LIVE_SELL_SUCCESS_ACCOUNTING_VERSION = (
    "live-sell-success-accounting-v1"
)

RECONCILED_SUCCESSFUL_SELL_REASON = (
    "RECONCILED_SUCCESSFUL_SELL"
)

PASS = "PASS"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SuccessfulSellLotAccounting:
    ordinal: int

    position_id: int
    position_version: str

    tokens_before: int
    tokens_sold: int
    tokens_after: int

    exposure_before_lamports: int
    exposure_reduction_lamports: int
    exposure_after_lamports: int

    cost_basis_before_lamports: int
    cost_basis_reduction_lamports: int
    cost_basis_after_lamports: int

    cumulative_net_proceeds_before_lamports: int
    allocated_net_proceeds_lamports: int
    cumulative_net_proceeds_after_lamports: int

    cumulative_realized_pnl_before_lamports: int
    leg_realized_pnl_lamports: int
    cumulative_realized_pnl_after_lamports: int


@dataclass(frozen=True)
class SuccessfulSellAccountingRecord:
    accounting_version: str
    authorization_sha256: str
    fill_resolver_version: str

    gross_quote_credit_lamports: int
    transaction_fee_lamports: int
    net_wallet_proceeds_lamports: int

    total_cost_basis_reduction_lamports: int
    total_realized_pnl_lamports: int

    recorded_at: float

    lots: tuple[
        SuccessfulSellLotAccounting,
        ...,
    ]


@dataclass(frozen=True)
class SuccessfulSellAccountingResult:
    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    journal: (
        LiveSellTransactionJournalEntry
        | None
    )

    accounting: (
        SuccessfulSellAccountingRecord
        | None
    )

    claim_status: str | None
    terminal_at: float | None
    terminal_reason: str | None

    changed: bool


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


def _strict_sqlite_int(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and -SQLITE_INT_MAX - 1
        <= value
        <= SQLITE_INT_MAX
    )


def _strict_timestamp(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            (int, float),
        )
        and not isinstance(
            value,
            bool,
        )
        and math.isfinite(
            float(value)
        )
        and float(value) > 0
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


def init_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        live_sell_success_accounting (
            authorization_sha256
                TEXT PRIMARY KEY
                REFERENCES
                    live_sell_transaction_journal(
                        authorization_sha256
                    )
                ON DELETE RESTRICT,

            accounting_version
                TEXT NOT NULL,

            fill_resolver_version
                TEXT NOT NULL,

            gross_quote_credit_lamports
                INTEGER NOT NULL
                CHECK (
                    gross_quote_credit_lamports > 0
                ),

            transaction_fee_lamports
                INTEGER NOT NULL
                CHECK (
                    transaction_fee_lamports >= 0
                ),

            net_wallet_proceeds_lamports
                INTEGER NOT NULL
                CHECK (
                    net_wallet_proceeds_lamports >= 0
                ),

            total_cost_basis_reduction_lamports
                INTEGER NOT NULL
                CHECK (
                    total_cost_basis_reduction_lamports
                    >= 0
                ),

            total_realized_pnl_lamports
                INTEGER NOT NULL,

            recorded_at
                REAL NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        live_sell_success_accounting_lots (
            authorization_sha256
                TEXT NOT NULL
                REFERENCES
                    live_sell_success_accounting(
                        authorization_sha256
                    )
                ON DELETE RESTRICT,

            ordinal
                INTEGER NOT NULL
                CHECK (ordinal >= 0),

            position_id
                INTEGER NOT NULL,

            position_version
                TEXT NOT NULL,

            tokens_before
                INTEGER NOT NULL
                CHECK (tokens_before > 0),

            tokens_sold
                INTEGER NOT NULL
                CHECK (tokens_sold > 0),

            tokens_after
                INTEGER NOT NULL
                CHECK (tokens_after >= 0),

            exposure_before_lamports
                INTEGER NOT NULL
                CHECK (
                    exposure_before_lamports >= 0
                ),

            exposure_reduction_lamports
                INTEGER NOT NULL
                CHECK (
                    exposure_reduction_lamports >= 0
                ),

            exposure_after_lamports
                INTEGER NOT NULL
                CHECK (
                    exposure_after_lamports >= 0
                ),

            cost_basis_before_lamports
                INTEGER NOT NULL
                CHECK (
                    cost_basis_before_lamports >= 0
                ),

            cost_basis_reduction_lamports
                INTEGER NOT NULL
                CHECK (
                    cost_basis_reduction_lamports
                    >= 0
                ),

            cost_basis_after_lamports
                INTEGER NOT NULL
                CHECK (
                    cost_basis_after_lamports >= 0
                ),

            cumulative_net_proceeds_before_lamports
                INTEGER NOT NULL
                CHECK (
                    cumulative_net_proceeds_before_lamports
                    >= 0
                ),

            allocated_net_proceeds_lamports
                INTEGER NOT NULL
                CHECK (
                    allocated_net_proceeds_lamports
                    >= 0
                ),

            cumulative_net_proceeds_after_lamports
                INTEGER NOT NULL
                CHECK (
                    cumulative_net_proceeds_after_lamports
                    >= 0
                ),

            cumulative_realized_pnl_before_lamports
                INTEGER NOT NULL,

            leg_realized_pnl_lamports
                INTEGER NOT NULL,

            cumulative_realized_pnl_after_lamports
                INTEGER NOT NULL,

            PRIMARY KEY (
                authorization_sha256,
                ordinal
            ),

            UNIQUE (
                authorization_sha256,
                position_id
            )
        )
        """
    )


def _row_to_lot(
    row: sqlite3.Row,
) -> SuccessfulSellLotAccounting:
    return SuccessfulSellLotAccounting(
        ordinal=int(
            row["ordinal"]
        ),
        position_id=int(
            row["position_id"]
        ),
        position_version=str(
            row["position_version"]
        ),
        tokens_before=int(
            row["tokens_before"]
        ),
        tokens_sold=int(
            row["tokens_sold"]
        ),
        tokens_after=int(
            row["tokens_after"]
        ),
        exposure_before_lamports=int(
            row[
                "exposure_before_lamports"
            ]
        ),
        exposure_reduction_lamports=int(
            row[
                "exposure_reduction_lamports"
            ]
        ),
        exposure_after_lamports=int(
            row[
                "exposure_after_lamports"
            ]
        ),
        cost_basis_before_lamports=int(
            row[
                "cost_basis_before_lamports"
            ]
        ),
        cost_basis_reduction_lamports=int(
            row[
                "cost_basis_reduction_lamports"
            ]
        ),
        cost_basis_after_lamports=int(
            row[
                "cost_basis_after_lamports"
            ]
        ),
        cumulative_net_proceeds_before_lamports=int(
            row[
                "cumulative_net_proceeds_before_lamports"
            ]
        ),
        allocated_net_proceeds_lamports=int(
            row[
                "allocated_net_proceeds_lamports"
            ]
        ),
        cumulative_net_proceeds_after_lamports=int(
            row[
                "cumulative_net_proceeds_after_lamports"
            ]
        ),
        cumulative_realized_pnl_before_lamports=int(
            row[
                "cumulative_realized_pnl_before_lamports"
            ]
        ),
        leg_realized_pnl_lamports=int(
            row[
                "leg_realized_pnl_lamports"
            ]
        ),
        cumulative_realized_pnl_after_lamports=int(
            row[
                "cumulative_realized_pnl_after_lamports"
            ]
        ),
    )


def _load_success_accounting(
    *,
    connection: sqlite3.Connection,
    authorization_sha256: str,
) -> SuccessfulSellAccountingRecord | None:
    row = connection.execute(
        """
        SELECT *
        FROM live_sell_success_accounting
        WHERE authorization_sha256 = ?
        """,
        (
            authorization_sha256,
        ),
    ).fetchone()

    if row is None:
        return None

    lot_rows = connection.execute(
        """
        SELECT *
        FROM live_sell_success_accounting_lots
        WHERE authorization_sha256 = ?
        ORDER BY ordinal ASC
        """,
        (
            authorization_sha256,
        ),
    ).fetchall()

    lots = tuple(
        _row_to_lot(
            lot_row
        )
        for lot_row in lot_rows
    )

    return SuccessfulSellAccountingRecord(
        accounting_version=str(
            row["accounting_version"]
        ),
        authorization_sha256=str(
            row[
                "authorization_sha256"
            ]
        ),
        fill_resolver_version=str(
            row[
                "fill_resolver_version"
            ]
        ),
        gross_quote_credit_lamports=int(
            row[
                "gross_quote_credit_lamports"
            ]
        ),
        transaction_fee_lamports=int(
            row[
                "transaction_fee_lamports"
            ]
        ),
        net_wallet_proceeds_lamports=int(
            row[
                "net_wallet_proceeds_lamports"
            ]
        ),
        total_cost_basis_reduction_lamports=int(
            row[
                "total_cost_basis_reduction_lamports"
            ]
        ),
        total_realized_pnl_lamports=int(
            row[
                "total_realized_pnl_lamports"
            ]
        ),
        recorded_at=float(
            row["recorded_at"]
        ),
        lots=lots,
    )


def _accounting_record_valid(
    record: SuccessfulSellAccountingRecord,
) -> bool:
    if (
        record.accounting_version
        != LIVE_SELL_SUCCESS_ACCOUNTING_VERSION
        or not _valid_sha256(
            record.authorization_sha256
        )
        or record.fill_resolver_version
        != SUCCESSFUL_PUMP_SELL_FILL_VERSION
        or not _strict_positive_int(
            record.gross_quote_credit_lamports
        )
        or not _strict_nonnegative_int(
            record.transaction_fee_lamports
        )
        or not _strict_nonnegative_int(
            record.net_wallet_proceeds_lamports
        )
        or not _strict_nonnegative_int(
            record.total_cost_basis_reduction_lamports
        )
        or not _strict_sqlite_int(
            record.total_realized_pnl_lamports
        )
        or not _strict_timestamp(
            record.recorded_at
        )
        or not record.lots
    ):
        return False

    expected_net = max(
        0,
        record.gross_quote_credit_lamports
        - record.transaction_fee_lamports,
    )

    if (
        record.net_wallet_proceeds_lamports
        != expected_net
    ):
        return False

    total_allocated = 0
    total_cost = 0
    total_realized = 0

    for ordinal, lot in enumerate(
        record.lots
    ):
        if (
            lot.ordinal != ordinal
            or lot.position_version
            != LIVE_POSITION_VERSION
            or not _strict_positive_int(
                lot.position_id
            )
            or not _strict_positive_int(
                lot.tokens_before
            )
            or not _strict_positive_int(
                lot.tokens_sold
            )
            or not _strict_nonnegative_int(
                lot.tokens_after
            )
            or lot.tokens_before
            - lot.tokens_sold
            != lot.tokens_after
            or not _strict_nonnegative_int(
                lot.exposure_before_lamports
            )
            or not _strict_nonnegative_int(
                lot.exposure_reduction_lamports
            )
            or not _strict_nonnegative_int(
                lot.exposure_after_lamports
            )
            or lot.exposure_before_lamports
            - lot.exposure_reduction_lamports
            != lot.exposure_after_lamports
            or not _strict_nonnegative_int(
                lot.cost_basis_before_lamports
            )
            or not _strict_nonnegative_int(
                lot.cost_basis_reduction_lamports
            )
            or not _strict_nonnegative_int(
                lot.cost_basis_after_lamports
            )
            or lot.cost_basis_before_lamports
            - lot.cost_basis_reduction_lamports
            != lot.cost_basis_after_lamports
            or not _strict_nonnegative_int(
                lot.cumulative_net_proceeds_before_lamports
            )
            or not _strict_nonnegative_int(
                lot.allocated_net_proceeds_lamports
            )
            or not _strict_nonnegative_int(
                lot.cumulative_net_proceeds_after_lamports
            )
            or lot.cumulative_net_proceeds_before_lamports
            + lot.allocated_net_proceeds_lamports
            != lot.cumulative_net_proceeds_after_lamports
            or not _strict_sqlite_int(
                lot.cumulative_realized_pnl_before_lamports
            )
            or not _strict_sqlite_int(
                lot.leg_realized_pnl_lamports
            )
            or not _strict_sqlite_int(
                lot.cumulative_realized_pnl_after_lamports
            )
            or lot.allocated_net_proceeds_lamports
            - lot.cost_basis_reduction_lamports
            != lot.leg_realized_pnl_lamports
            or lot.cumulative_realized_pnl_before_lamports
            + lot.leg_realized_pnl_lamports
            != lot.cumulative_realized_pnl_after_lamports
        ):
            return False

        total_allocated += (
            lot.allocated_net_proceeds_lamports
        )
        total_cost += (
            lot.cost_basis_reduction_lamports
        )
        total_realized += (
            lot.leg_realized_pnl_lamports
        )

    return (
        total_allocated
        == record.net_wallet_proceeds_lamports
        and total_cost
        == record.total_cost_basis_reduction_lamports
        and total_realized
        == record.total_realized_pnl_lamports
    )


def _fill_contract_valid(
    *,
    authorization: LivePumpSellAuthorization,
    fill: SuccessfulPumpSellFillResult,
) -> bool:
    if (
        not isinstance(
            authorization,
            LivePumpSellAuthorization,
        )
        or not _authorization_contract_valid(
            authorization
        )
        or not isinstance(
            fill,
            SuccessfulPumpSellFillResult,
        )
        or fill.resolver_version
        != SUCCESSFUL_PUMP_SELL_FILL_VERSION
        or fill.status != PROVEN
        or fill.reasons != ()
        or fill.authorization_sha256
        != authorization.authorization_sha256
        or fill.mint
        != authorization.mint
        or fill.wallet_pubkey
        != authorization.wallet_pubkey
        or fill.base_token_program
        != authorization.base_token_program
        or fill.associated_base_user
        != authorization.associated_base_user
        or fill.authorized_token_amount
        != authorization.tokens_to_sell
        or fill.trade_event_token_amount
        != authorization.tokens_to_sell
        or fill.base_token_debit
        != authorization.tokens_to_sell
        or fill.min_quote_out
        != authorization.exit_execution.min_quote_out
        or fill.receipt_status != RESOLVED
        or not _nonempty_string(
            fill.transaction_signature
        )
        or not _strict_nonnegative_int(
            fill.receipt_slot
        )
        or (
            fill.block_time is not None
            and not _strict_nonnegative_int(
                fill.block_time
            )
        )
        or not _strict_positive_int(
            fill.quote_token_credit_lamports
        )
        or fill.quote_token_credit_lamports
        < authorization.exit_execution.min_quote_out
        or not _strict_nonnegative_int(
            fill.fee_lamports
        )
        or not _strict_nonnegative_int(
            fill.wallet_pre_balance_lamports
        )
        or not _strict_nonnegative_int(
            fill.wallet_post_balance_lamports
        )
        or not _strict_sqlite_int(
            fill.wallet_balance_delta_lamports
        )
        or fill.wallet_post_balance_lamports
        - fill.wallet_pre_balance_lamports
        != fill.wallet_balance_delta_lamports
        or not _strict_nonnegative_int(
            fill.base_token_pre_amount
        )
        or not _strict_nonnegative_int(
            fill.base_token_post_amount
        )
        or fill.base_token_pre_amount
        - fill.base_token_post_amount
        != fill.base_token_debit
        or not _strict_nonnegative_int(
            fill.quote_token_pre_amount
        )
        or not _strict_nonnegative_int(
            fill.quote_token_post_amount
        )
        or fill.quote_token_post_amount
        - fill.quote_token_pre_amount
        != fill.quote_token_credit_lamports
        or fill.quote_mint
        != str(
            WRAPPED_SOL_MINT
        )
        or fill.quote_token_program
        != str(
            TOKEN_PROGRAM
        )
        or not _valid_sha256(
            fill.persisted_transaction_sha256
        )
        or not _valid_sha256(
            fill.observed_transaction_sha256
        )
        or fill.persisted_transaction_sha256
        != fill.observed_transaction_sha256
    ):
        return False

    try:
        canonical_quote_user = (
            derive_associated_token_account(
                owner=Pubkey.from_string(
                    authorization.wallet_pubkey
                ),
                mint=Pubkey.from_string(
                    str(WRAPPED_SOL_MINT)
                ),
                token_program=TOKEN_PROGRAM,
            )
        )
    except Exception:
        return False

    return (
        fill.associated_quote_user
        == str(
            canonical_quote_user
        )
    )


def _journal_matches(
    *,
    entry: LiveSellTransactionJournalEntry,
    authorization: LivePumpSellAuthorization,
    fill: SuccessfulPumpSellFillResult,
    execution,
) -> bool:
    return (
        entry.journal_version
        == LIVE_SELL_TRANSACTION_JOURNAL_VERSION
        and entry.authorization_sha256
        == authorization.authorization_sha256
        and entry.transaction_signature
        == fill.transaction_signature
        and entry.wallet_pubkey
        == authorization.wallet_pubkey
        and entry.mint
        == authorization.mint
        and entry.tokens_to_sell
        == authorization.tokens_to_sell
        and entry.signed_transaction_sha256
        == fill.persisted_transaction_sha256
        and entry.receipt_transaction_sha256
        == fill.observed_transaction_sha256
        and entry.receipt_resolver_version
        == LIVE_SELL_TRANSACTION_RECEIPT_VERSION
        and entry.slot
        == fill.receipt_slot
        and entry.block_time
        == fill.block_time
        and entry.outcome == SUCCESS
        and entry.transaction_error_json
        == "null"
        and entry.fee_lamports
        == fill.fee_lamports
        and entry.fee_payer_pubkey
        == authorization.wallet_pubkey
        and entry.fee_payer_pre_balance_lamports
        == fill.wallet_pre_balance_lamports
        and entry.fee_payer_post_balance_lamports
        == fill.wallet_post_balance_lamports
        and entry.fee_payer_balance_delta_lamports
        == fill.wallet_balance_delta_lamports
        and entry.last_valid_block_height
        == execution.last_valid_block_height
        and entry.blockhash_rpc_slot
        == execution.blockhash_rpc_slot
        and _strict_timestamp(
            entry.recorded_at
        )
    )


def _success_record_matches(
    *,
    record: SuccessfulSellAccountingRecord,
    authorization: LivePumpSellAuthorization,
    fill: SuccessfulPumpSellFillResult,
) -> bool:
    if (
        not _accounting_record_valid(
            record
        )
        or record.authorization_sha256
        != authorization.authorization_sha256
        or record.gross_quote_credit_lamports
        != fill.quote_token_credit_lamports
        or record.transaction_fee_lamports
        != fill.fee_lamports
        or len(record.lots)
        != len(
            authorization.allocation.allocations
        )
    ):
        return False

    for claim_lot, ledger_lot in zip(
        authorization.allocation.allocations,
        record.lots,
        strict=True,
    ):
        if (
            ledger_lot.position_id
            != claim_lot.position_id
            or ledger_lot.position_version
            != claim_lot.position_version
            or ledger_lot.tokens_before
            != claim_lot.tokens_before
            or ledger_lot.tokens_sold
            != claim_lot.tokens_to_sell
            or ledger_lot.tokens_after
            != claim_lot.tokens_after
            or ledger_lot.exposure_before_lamports
            != claim_lot.exposure_before_lamports
            or ledger_lot.exposure_reduction_lamports
            != claim_lot.exposure_reduction_lamports
            or ledger_lot.exposure_after_lamports
            != claim_lot.exposure_after_lamports
            or ledger_lot.cost_basis_before_lamports
            != claim_lot.cost_basis_before_lamports
            or ledger_lot.cost_basis_reduction_lamports
            != claim_lot.cost_basis_reduction_lamports
            or ledger_lot.cost_basis_after_lamports
            != claim_lot.cost_basis_after_lamports
            or ledger_lot.cumulative_net_proceeds_before_lamports
            != claim_lot.cumulative_net_proceeds_before_lamports
            or ledger_lot.cumulative_realized_pnl_before_lamports
            != claim_lot.cumulative_realized_pnl_before_lamports
        ):
            return False

    return True


def record_successful_sell_and_consume_claim(
    *,
    authorization: LivePumpSellAuthorization,
    fill: SuccessfulPumpSellFillResult,
    db_path: Path = DB_PATH,
) -> SuccessfulSellAccountingResult:
    authorization_sha256 = (
        authorization.authorization_sha256
        if isinstance(
            authorization,
            LivePumpSellAuthorization,
        )
        else ""
    )

    claim_status: str | None = None
    terminal_at: float | None = None
    terminal_reason: str | None = None

    def finish(
        status: str,
        *reasons: str,
        journal: (
            LiveSellTransactionJournalEntry
            | None
        ) = None,
        accounting: (
            SuccessfulSellAccountingRecord
            | None
        ) = None,
        changed: bool = False,
    ) -> SuccessfulSellAccountingResult:
        return SuccessfulSellAccountingResult(
            status=status,
            reasons=tuple(reasons),
            authorization_sha256=(
                authorization_sha256
            ),
            journal=journal,
            accounting=accounting,
            claim_status=claim_status,
            terminal_at=terminal_at,
            terminal_reason=terminal_reason,
            changed=changed,
        )

    if not _fill_contract_valid(
        authorization=authorization,
        fill=fill,
    ):
        return finish(
            UNKNOWN,
            "SUCCESSFUL_SELL_FILL_CONTRACT_INVALID",
        )

    authorization_sha256 = (
        authorization.authorization_sha256
    )

    connection = get_connection(
        db_path
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        init_claim_schema(
            connection
        )
        init_execution_schema(
            connection
        )
        init_journal_schema(
            connection
        )
        init_position_schema(
            connection
        )
        init_schema(
            connection
        )

        claim = _load_claim(
            connection=connection,
            authorization_sha256=(
                authorization_sha256
            ),
        )

        if claim is None:
            connection.rollback()
            return finish(
                UNKNOWN,
                "SUCCESSFUL_SELL_CLAIM_NOT_FOUND",
            )

        claim_status = claim.status
        terminal_at = claim.terminal_at
        terminal_reason = claim.terminal_reason

        execution_row = connection.execute(
            """
            SELECT *
            FROM live_sell_execution_records
            WHERE authorization_sha256 = ?
            """,
            (
                authorization_sha256,
            ),
        ).fetchone()

        if execution_row is None:
            connection.rollback()
            return finish(
                UNKNOWN,
                "SUCCESSFUL_SELL_EXECUTION_NOT_FOUND",
            )

        execution = _row_to_record(
            execution_row
        )

        if (
            not _record_contract_valid(
                execution
            )
            or execution.authorization_sha256
            != authorization_sha256
            or execution.authorization_version
            != authorization.authorization_version
            or execution.wallet_pubkey
            != authorization.wallet_pubkey
            or execution.mint
            != authorization.mint
            or execution.tokens_to_sell
            != authorization.tokens_to_sell
            or execution.transaction_signature
            != fill.transaction_signature
            or execution.signed_transaction_sha256
            != fill.persisted_transaction_sha256
            or execution.status
            not in (
                SIGNED,
                SUBMISSION_ARMED,
                SUBMITTED,
            )
        ):
            connection.rollback()
            return finish(
                UNKNOWN,
                "SUCCESSFUL_SELL_EXECUTION_CONTRACT_INVALID",
            )

        journal_rows = connection.execute(
            """
            SELECT *
            FROM live_sell_transaction_journal
            WHERE authorization_sha256 = ?
               OR transaction_signature = ?
            """,
            (
                authorization_sha256,
                fill.transaction_signature,
            ),
        ).fetchall()

        existing_entry = (
            None
            if not journal_rows
            else _row_to_entry(
                journal_rows[0]
            )
        )

        existing_accounting = (
            _load_success_accounting(
                connection=connection,
                authorization_sha256=(
                    authorization_sha256
                ),
            )
        )

        if claim.status == CONSUMED:
            if (
                claim.terminal_reason
                != RECONCILED_SUCCESSFUL_SELL_REASON
                or claim.terminal_at is None
                or len(journal_rows) != 1
                or existing_entry is None
                or existing_accounting is None
                or not _claim_identity_matches_authorization(
                    claim=claim,
                    authorization=authorization,
                )
                or not _journal_matches(
                    entry=existing_entry,
                    authorization=authorization,
                    fill=fill,
                    execution=execution,
                )
                or not _success_record_matches(
                    record=existing_accounting,
                    authorization=authorization,
                    fill=fill,
                )
            ):
                connection.rollback()
                return finish(
                    UNKNOWN,
                    "SUCCESSFUL_SELL_TERMINAL_STATE_INCOHERENT",
                    journal=existing_entry,
                    accounting=existing_accounting,
                )

            connection.commit()

            return finish(
                PASS,
                journal=existing_entry,
                accounting=existing_accounting,
                changed=False,
            )

        if claim.status == RELEASED:
            connection.rollback()
            return finish(
                BLOCK,
                "SUCCESSFUL_SELL_CLAIM_ALREADY_RELEASED",
                journal=existing_entry,
                accounting=existing_accounting,
            )

        if (
            claim.status != ACTIVE
            or claim.terminal_at is not None
            or claim.terminal_reason is not None
            or not _claim_matches_authorization(
                claim=claim,
                authorization=authorization,
            )
        ):
            connection.rollback()
            return finish(
                BLOCK,
                "ACTIVE_SELL_CLAIM_CONTRACT_MISMATCH",
                journal=existing_entry,
                accounting=existing_accounting,
            )

        if (
            journal_rows
            or existing_accounting is not None
        ):
            connection.rollback()
            return finish(
                UNKNOWN,
                "SUCCESSFUL_SELL_LEDGER_STATE_INCONSISTENT",
                journal=existing_entry,
                accounting=existing_accounting,
            )

        claim_lots = tuple(
            claim.allocation.allocations
        )

        if (
            not claim_lots
            or sum(
                lot.tokens_to_sell
                for lot in claim_lots
            )
            != authorization.tokens_to_sell
        ):
            connection.rollback()
            return finish(
                UNKNOWN,
                "SUCCESSFUL_SELL_ALLOCATION_INVALID",
            )

        gross_quote_credit = (
            fill.quote_token_credit_lamports
        )
        fee_lamports = fill.fee_lamports

        net_wallet_proceeds = max(
            0,
            gross_quote_credit
            - fee_lamports,
        )

        total_cost_basis_reduction = sum(
            lot.cost_basis_reduction_lamports
            for lot in claim_lots
        )

        if not _strict_nonnegative_int(
            total_cost_basis_reduction
        ):
            connection.rollback()
            return finish(
                UNKNOWN,
                "SUCCESSFUL_SELL_COST_BASIS_TOTAL_INVALID",
            )

        lot_accounting: list[
            SuccessfulSellLotAccounting
        ] = []

        allocated_so_far = 0
        total_tokens = (
            authorization.tokens_to_sell
        )

        for ordinal, lot in enumerate(
            claim_lots
        ):
            if ordinal == len(
                claim_lots
            ) - 1:
                allocated_proceeds = (
                    net_wallet_proceeds
                    - allocated_so_far
                )
            else:
                allocated_proceeds = (
                    net_wallet_proceeds
                    * lot.tokens_to_sell
                    // total_tokens
                )

            allocated_so_far += (
                allocated_proceeds
            )

            cumulative_net_after = (
                lot.cumulative_net_proceeds_before_lamports
                + allocated_proceeds
            )

            leg_realized_pnl = (
                allocated_proceeds
                - lot.cost_basis_reduction_lamports
            )

            cumulative_realized_after = (
                lot.cumulative_realized_pnl_before_lamports
                + leg_realized_pnl
            )

            if (
                not _strict_nonnegative_int(
                    cumulative_net_after
                )
                or not _strict_sqlite_int(
                    leg_realized_pnl
                )
                or not _strict_sqlite_int(
                    cumulative_realized_after
                )
            ):
                connection.rollback()
                return finish(
                    UNKNOWN,
                    "SUCCESSFUL_SELL_ACCOUNTING_OVERFLOW",
                )

            lot_accounting.append(
                SuccessfulSellLotAccounting(
                    ordinal=ordinal,
                    position_id=(
                        lot.position_id
                    ),
                    position_version=(
                        lot.position_version
                    ),
                    tokens_before=(
                        lot.tokens_before
                    ),
                    tokens_sold=(
                        lot.tokens_to_sell
                    ),
                    tokens_after=(
                        lot.tokens_after
                    ),
                    exposure_before_lamports=(
                        lot.exposure_before_lamports
                    ),
                    exposure_reduction_lamports=(
                        lot.exposure_reduction_lamports
                    ),
                    exposure_after_lamports=(
                        lot.exposure_after_lamports
                    ),
                    cost_basis_before_lamports=(
                        lot.cost_basis_before_lamports
                    ),
                    cost_basis_reduction_lamports=(
                        lot.cost_basis_reduction_lamports
                    ),
                    cost_basis_after_lamports=(
                        lot.cost_basis_after_lamports
                    ),
                    cumulative_net_proceeds_before_lamports=(
                        lot.cumulative_net_proceeds_before_lamports
                    ),
                    allocated_net_proceeds_lamports=(
                        allocated_proceeds
                    ),
                    cumulative_net_proceeds_after_lamports=(
                        cumulative_net_after
                    ),
                    cumulative_realized_pnl_before_lamports=(
                        lot.cumulative_realized_pnl_before_lamports
                    ),
                    leg_realized_pnl_lamports=(
                        leg_realized_pnl
                    ),
                    cumulative_realized_pnl_after_lamports=(
                        cumulative_realized_after
                    ),
                )
            )

        if allocated_so_far != net_wallet_proceeds:
            connection.rollback()
            return finish(
                UNKNOWN,
                "SUCCESSFUL_SELL_PROCEEDS_ALLOCATION_MISMATCH",
            )

        total_realized_pnl = sum(
            lot.leg_realized_pnl_lamports
            for lot in lot_accounting
        )

        if (
            not _strict_sqlite_int(
                total_realized_pnl
            )
            or total_realized_pnl
            != (
                net_wallet_proceeds
                - total_cost_basis_reduction
            )
        ):
            connection.rollback()
            return finish(
                UNKNOWN,
                "SUCCESSFUL_SELL_REALIZED_PNL_TOTAL_INVALID",
            )

        #
        # Validate every before-state first.
        # No position is mutated until every claimed lot is
        # proven to still match its authorization-time state.
        #
        for lot in claim_lots:
            row = connection.execute(
                """
                SELECT *
                FROM live_positions
                WHERE position_id = ?
                """,
                (
                    lot.position_id,
                ),
            ).fetchone()

            if (
                row is None
                or str(
                    row["position_version"]
                )
                != LIVE_POSITION_VERSION
                or lot.position_version
                != LIVE_POSITION_VERSION
                or str(
                    row["wallet_pubkey"]
                )
                != authorization.wallet_pubkey
                or str(
                    row["mint"]
                )
                != authorization.mint
                or str(
                    row["status"]
                )
                != OPEN
                or int(
                    row["entry_slot"]
                )
                != lot.entry_slot
                or int(
                    row["tokens_held"]
                )
                != lot.tokens_before
                or int(
                    row[
                        "remaining_exposure_lamports"
                    ]
                )
                != lot.exposure_before_lamports
                or int(
                    row[
                        "remaining_cost_basis_lamports"
                    ]
                )
                != lot.cost_basis_before_lamports
                or int(
                    row[
                        "cumulative_net_proceeds_lamports"
                    ]
                )
                != lot.cumulative_net_proceeds_before_lamports
                or int(
                    row[
                        "cumulative_realized_pnl_lamports"
                    ]
                )
                != lot.cumulative_realized_pnl_before_lamports
            ):
                connection.rollback()
                return finish(
                    UNKNOWN,
                    "SUCCESSFUL_SELL_POSITION_STATE_MISMATCH",
                )

        now = time.time()

        #
        # Parent immutable transaction outcome.
        #
        connection.execute(
            """
            INSERT INTO live_sell_transaction_journal (
                authorization_sha256,
                journal_version,
                transaction_signature,
                wallet_pubkey,
                mint,
                tokens_to_sell,
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
                authorization_sha256,
                LIVE_SELL_TRANSACTION_JOURNAL_VERSION,
                fill.transaction_signature,
                authorization.wallet_pubkey,
                authorization.mint,
                authorization.tokens_to_sell,
                fill.persisted_transaction_sha256,
                fill.observed_transaction_sha256,
                LIVE_SELL_TRANSACTION_RECEIPT_VERSION,
                fill.receipt_slot,
                fill.block_time,
                SUCCESS,
                "null",
                fee_lamports,
                authorization.wallet_pubkey,
                fill.wallet_pre_balance_lamports,
                fill.wallet_post_balance_lamports,
                fill.wallet_balance_delta_lamports,
                execution.last_valid_block_height,
                execution.blockhash_rpc_slot,
                now,
            ),
        )

        #
        # Aggregate immutable successful-SELL accounting.
        #
        connection.execute(
            """
            INSERT INTO live_sell_success_accounting (
                authorization_sha256,
                accounting_version,
                fill_resolver_version,
                gross_quote_credit_lamports,
                transaction_fee_lamports,
                net_wallet_proceeds_lamports,
                total_cost_basis_reduction_lamports,
                total_realized_pnl_lamports,
                recorded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                authorization_sha256,
                LIVE_SELL_SUCCESS_ACCOUNTING_VERSION,
                fill.resolver_version,
                gross_quote_credit,
                fee_lamports,
                net_wallet_proceeds,
                total_cost_basis_reduction,
                total_realized_pnl,
                now,
            ),
        )

        for lot in lot_accounting:
            connection.execute(
                """
                INSERT INTO
                    live_sell_success_accounting_lots (
                        authorization_sha256,
                        ordinal,
                        position_id,
                        position_version,
                        tokens_before,
                        tokens_sold,
                        tokens_after,
                        exposure_before_lamports,
                        exposure_reduction_lamports,
                        exposure_after_lamports,
                        cost_basis_before_lamports,
                        cost_basis_reduction_lamports,
                        cost_basis_after_lamports,
                        cumulative_net_proceeds_before_lamports,
                        allocated_net_proceeds_lamports,
                        cumulative_net_proceeds_after_lamports,
                        cumulative_realized_pnl_before_lamports,
                        leg_realized_pnl_lamports,
                        cumulative_realized_pnl_after_lamports
                    )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    authorization_sha256,
                    lot.ordinal,
                    lot.position_id,
                    lot.position_version,
                    lot.tokens_before,
                    lot.tokens_sold,
                    lot.tokens_after,
                    lot.exposure_before_lamports,
                    lot.exposure_reduction_lamports,
                    lot.exposure_after_lamports,
                    lot.cost_basis_before_lamports,
                    lot.cost_basis_reduction_lamports,
                    lot.cost_basis_after_lamports,
                    lot.cumulative_net_proceeds_before_lamports,
                    lot.allocated_net_proceeds_lamports,
                    lot.cumulative_net_proceeds_after_lamports,
                    lot.cumulative_realized_pnl_before_lamports,
                    lot.leg_realized_pnl_lamports,
                    lot.cumulative_realized_pnl_after_lamports,
                ),
            )

        #
        # Strong compare-and-swap mutation.
        #
        for lot in lot_accounting:
            next_status = (
                CLOSED
                if lot.tokens_after == 0
                else OPEN
            )

            updated = connection.execute(
                """
                UPDATE live_positions

                SET
                    status = ?,
                    tokens_held = ?,
                    remaining_exposure_lamports = ?,
                    remaining_cost_basis_lamports = ?,
                    cumulative_net_proceeds_lamports = ?,
                    cumulative_realized_pnl_lamports = ?,
                    updated_at = ?

                WHERE position_id = ?
                  AND position_version = ?
                  AND wallet_pubkey = ?
                  AND mint = ?
                  AND status = ?
                  AND tokens_held = ?
                  AND remaining_exposure_lamports = ?
                  AND remaining_cost_basis_lamports = ?
                  AND cumulative_net_proceeds_lamports = ?
                  AND cumulative_realized_pnl_lamports = ?
                """,
                (
                    next_status,
                    lot.tokens_after,
                    lot.exposure_after_lamports,
                    lot.cost_basis_after_lamports,
                    lot.cumulative_net_proceeds_after_lamports,
                    lot.cumulative_realized_pnl_after_lamports,
                    now,
                    lot.position_id,
                    lot.position_version,
                    authorization.wallet_pubkey,
                    authorization.mint,
                    OPEN,
                    lot.tokens_before,
                    lot.exposure_before_lamports,
                    lot.cost_basis_before_lamports,
                    lot.cumulative_net_proceeds_before_lamports,
                    lot.cumulative_realized_pnl_before_lamports,
                ),
            )

            if updated.rowcount != 1:
                connection.rollback()
                return finish(
                    UNKNOWN,
                    "SUCCESSFUL_SELL_POSITION_CAS_FAILED",
                )

        #
        # Inventory authority is consumed only after every
        # journal/accounting/position write has succeeded.
        #
        updated_claim = connection.execute(
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
              AND wallet_pubkey = ?
              AND mint = ?
              AND tokens_to_sell = ?
            """,
            (
                CONSUMED,
                now,
                RECONCILED_SUCCESSFUL_SELL_REASON,
                authorization_sha256,
                ACTIVE,
                authorization.wallet_pubkey,
                authorization.mint,
                authorization.tokens_to_sell,
            ),
        )

        if updated_claim.rowcount != 1:
            connection.rollback()
            return finish(
                UNKNOWN,
                "SUCCESSFUL_SELL_CONSUME_TRANSITION_FAILED",
            )

        persisted_claim = _load_claim(
            connection=connection,
            authorization_sha256=(
                authorization_sha256
            ),
        )

        persisted_journal_row = connection.execute(
            """
            SELECT *
            FROM live_sell_transaction_journal
            WHERE authorization_sha256 = ?
            """,
            (
                authorization_sha256,
            ),
        ).fetchone()

        persisted_accounting = (
            _load_success_accounting(
                connection=connection,
                authorization_sha256=(
                    authorization_sha256
                ),
            )
        )

        if (
            persisted_claim is None
            or persisted_journal_row is None
            or persisted_accounting is None
        ):
            connection.rollback()
            return finish(
                UNKNOWN,
                "SUCCESSFUL_SELL_ATOMIC_VERIFICATION_MISSING",
            )

        persisted_journal = (
            _row_to_entry(
                persisted_journal_row
            )
        )

        if (
            persisted_claim.status
            != CONSUMED
            or persisted_claim.terminal_at
            is None
            or persisted_claim.terminal_reason
            != RECONCILED_SUCCESSFUL_SELL_REASON
            or not _claim_identity_matches_authorization(
                claim=persisted_claim,
                authorization=authorization,
            )
            or not _journal_matches(
                entry=persisted_journal,
                authorization=authorization,
                fill=fill,
                execution=execution,
            )
            or not _success_record_matches(
                record=persisted_accounting,
                authorization=authorization,
                fill=fill,
            )
        ):
            connection.rollback()
            return finish(
                UNKNOWN,
                "SUCCESSFUL_SELL_ATOMIC_VERIFICATION_FAILED",
            )

        connection.commit()

        claim_status = (
            persisted_claim.status
        )
        terminal_at = (
            persisted_claim.terminal_at
        )
        terminal_reason = (
            persisted_claim.terminal_reason
        )

        return finish(
            PASS,
            journal=persisted_journal,
            accounting=persisted_accounting,
            changed=True,
        )

    except sqlite3.Error:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass

        return finish(
            UNKNOWN,
            "SUCCESSFUL_SELL_DATABASE_ERROR",
        )

    finally:
        connection.close()
