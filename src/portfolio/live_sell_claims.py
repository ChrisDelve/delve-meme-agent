from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solders.pubkey import Pubkey

from src.execution.live_pump_sell_authorization import (
    LIVE_PUMP_SELL_AUTHORIZATION_VERSION,
    LivePumpSellAuthorization,
    _authorization_sha256,
)
from src.portfolio.live_positions import (
    OPEN,
    LivePosition,
    _row_to_position,
)
from src.portfolio.live_reservations import (
    DB_PATH,
    SQLITE_INT_MAX,
    get_connection,
)
from src.portfolio.live_sell_allocation import (
    FIFO_ENTRY_SLOT,
    LIVE_SELL_ALLOCATION_VERSION,
    PLANNED,
    LiveSellAllocationPlan,
    LiveSellLotAllocation,
    plan_live_sell_allocation,
)
from src.portfolio.live_sell_authorization_records import (
    PASS as AUTHORIZATION_RECORD_PASS,
    _load_live_sell_authorization_record_in_transaction,
    _persist_live_sell_authorization_record_in_transaction,
)


LIVE_SELL_INVENTORY_CLAIM_VERSION = (
    "live-sell-inventory-claim-v1"
)

ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION = (
    "active-live-sell-inventory-claim-loader-v1"
)

ACTIVE = "ACTIVE"
RELEASED = "RELEASED"
CONSUMED = "CONSUMED"

PASS = "PASS"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"

SQLITE_INT_MIN = -(1 << 63)


@dataclass(frozen=True)
class LiveSellInventoryClaim:
    claim_version: str

    authorization_version: str
    authorization_sha256: str

    wallet_pubkey: str
    mint: str
    tokens_to_sell: int

    allocation: LiveSellAllocationPlan

    status: str

    claimed_at: float
    terminal_at: float | None
    terminal_reason: str | None


@dataclass(frozen=True)
class LiveSellInventoryClaimResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    claim: LiveSellInventoryClaim | None

    changed: bool



@dataclass(frozen=True)
class ActiveLiveSellInventoryClaimResult:
    loader_version: str

    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    claim: LiveSellInventoryClaim | None



def _strict_nonnegative_sqlite_int(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= SQLITE_INT_MAX
    )


def _strict_positive_sqlite_int(
    value: Any,
) -> bool:
    return (
        _strict_nonnegative_sqlite_int(
            value
        )
        and value > 0
    )


def _strict_sqlite_int(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and SQLITE_INT_MIN
        <= value
        <= SQLITE_INT_MAX
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


def _valid_pubkey(
    value: Any,
) -> bool:
    if not isinstance(value, str):
        return False

    value = value.strip()

    if not value:
        return False

    try:
        pubkey = Pubkey.from_string(
            value
        )

    except Exception:
        return False

    return pubkey != Pubkey.default()


def _authorization_fingerprint_matches(
    authorization: LivePumpSellAuthorization,
) -> bool:
    try:
        expected_sha256 = (
            _authorization_sha256(
                wallet_pubkey=(
                    authorization.wallet_pubkey
                ),
                mint=authorization.mint,
                bonding_curve=(
                    authorization.bonding_curve
                ),
                base_token_program=(
                    authorization.base_token_program
                ),
                associated_base_user=(
                    authorization.associated_base_user
                ),
                creator=authorization.creator,
                mayhem_mode=(
                    authorization.mayhem_mode
                ),
                curve_quote_mint=(
                    authorization.curve_quote_mint
                ),
                quote_mint_for_instruction=(
                    authorization
                    .quote_mint_for_instruction
                ),
                tokens_to_sell=(
                    authorization.tokens_to_sell
                ),
                allocation=(
                    authorization.allocation
                ),
                fee_rpc_slot=(
                    authorization.fee_rpc_slot
                ),
                quote_mint=(
                    authorization.curve_quote_mint
                ),
                protocol_fee_bps=(
                    authorization.protocol_fee_bps
                ),
                creator_fee_bps=(
                    authorization.creator_fee_bps
                ),
                slippage_bps=(
                    authorization.slippage_bps
                ),
                base_network_fee_lamports=(
                    authorization
                    .base_network_fee_lamports
                ),
                priority_fee_lamports=(
                    authorization
                    .priority_fee_lamports
                ),
                exit_execution=(
                    authorization.exit_execution
                ),
            )
        )

    except Exception:
        return False

    return (
        expected_sha256
        == authorization.authorization_sha256
    )


def init_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        live_sell_inventory_claims (
            authorization_sha256
                TEXT PRIMARY KEY,

            claim_version
                TEXT NOT NULL,

            authorization_version
                TEXT NOT NULL,

            wallet_pubkey
                TEXT NOT NULL,

            mint
                TEXT NOT NULL,

            tokens_to_sell
                INTEGER NOT NULL
                CHECK (tokens_to_sell > 0),

            allocation_version
                TEXT NOT NULL,

            allocation_method
                TEXT NOT NULL,

            requested_tokens
                INTEGER NOT NULL
                CHECK (requested_tokens > 0),

            total_tokens_before
                INTEGER NOT NULL
                CHECK (total_tokens_before >= 0),

            total_tokens_after
                INTEGER NOT NULL
                CHECK (total_tokens_after >= 0),

            total_exposure_before_lamports
                INTEGER NOT NULL
                CHECK (
                    total_exposure_before_lamports
                    >= 0
                ),

            total_exposure_reduction_lamports
                INTEGER NOT NULL
                CHECK (
                    total_exposure_reduction_lamports
                    >= 0
                ),

            total_exposure_after_lamports
                INTEGER NOT NULL
                CHECK (
                    total_exposure_after_lamports
                    >= 0
                ),

            total_cost_basis_before_lamports
                INTEGER NOT NULL
                CHECK (
                    total_cost_basis_before_lamports
                    >= 0
                ),

            total_cost_basis_reduction_lamports
                INTEGER NOT NULL
                CHECK (
                    total_cost_basis_reduction_lamports
                    >= 0
                ),

            total_cost_basis_after_lamports
                INTEGER NOT NULL
                CHECK (
                    total_cost_basis_after_lamports
                    >= 0
                ),

            status
                TEXT NOT NULL
                CHECK (
                    status IN (
                        'ACTIVE',
                        'RELEASED',
                        'CONSUMED'
                    )
                ),

            claimed_at
                REAL NOT NULL,

            terminal_at
                REAL,

            terminal_reason
                TEXT
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        live_sell_inventory_claim_lots (
            authorization_sha256
                TEXT NOT NULL
                REFERENCES
                    live_sell_inventory_claims(
                        authorization_sha256
                    )
                ON DELETE RESTRICT,

            ordinal
                INTEGER NOT NULL
                CHECK (ordinal >= 0),

            position_id
                INTEGER NOT NULL
                CHECK (position_id > 0),

            position_version
                TEXT NOT NULL,

            entry_slot
                INTEGER NOT NULL
                CHECK (entry_slot >= 0),

            tokens_before
                INTEGER NOT NULL
                CHECK (tokens_before > 0),

            tokens_to_sell
                INTEGER NOT NULL
                CHECK (tokens_to_sell > 0),

            tokens_after
                INTEGER NOT NULL
                CHECK (tokens_after >= 0),

            exposure_before_lamports
                INTEGER NOT NULL
                CHECK (
                    exposure_before_lamports
                    >= 0
                ),

            exposure_reduction_lamports
                INTEGER NOT NULL
                CHECK (
                    exposure_reduction_lamports
                    >= 0
                ),

            exposure_after_lamports
                INTEGER NOT NULL
                CHECK (
                    exposure_after_lamports
                    >= 0
                ),

            cost_basis_before_lamports
                INTEGER NOT NULL
                CHECK (
                    cost_basis_before_lamports
                    >= 0
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
                    cost_basis_after_lamports
                    >= 0
                ),

            cumulative_net_proceeds_before_lamports
                INTEGER NOT NULL
                CHECK (
                    cumulative_net_proceeds_before_lamports
                    >= 0
                ),

            cumulative_realized_pnl_before_lamports
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

    #
    # This is the durable concurrency invariant.
    #
    # Multiple historical claims may exist for one
    # wallet/mint after terminal reconciliation, but
    # no more than one may have inventory authority
    # at any instant.
    #
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        live_sell_inventory_one_active_per_mint
        ON live_sell_inventory_claims (
            wallet_pubkey,
            mint
        )
        WHERE status = 'ACTIVE'
        """
    )


def _allocation_is_persistable(
    allocation: LiveSellAllocationPlan,
) -> bool:
    if (
        allocation.allocation_version
        != LIVE_SELL_ALLOCATION_VERSION
        or allocation.allocation_method
        != FIFO_ENTRY_SLOT
        or allocation.status
        != PLANNED
        or allocation.reasons
        != ()
        or not _strict_positive_sqlite_int(
            allocation.requested_tokens
        )
        or not _strict_nonnegative_sqlite_int(
            allocation.total_tokens_before
        )
        or not _strict_nonnegative_sqlite_int(
            allocation.total_tokens_after
        )
        or not _strict_nonnegative_sqlite_int(
            allocation
            .total_exposure_before_lamports
        )
        or not _strict_nonnegative_sqlite_int(
            allocation
            .total_exposure_reduction_lamports
        )
        or not _strict_nonnegative_sqlite_int(
            allocation
            .total_exposure_after_lamports
        )
        or not _strict_nonnegative_sqlite_int(
            allocation
            .total_cost_basis_before_lamports
        )
        or not _strict_nonnegative_sqlite_int(
            allocation
            .total_cost_basis_reduction_lamports
        )
        or not _strict_nonnegative_sqlite_int(
            allocation
            .total_cost_basis_after_lamports
        )
        or not isinstance(
            allocation.allocations,
            tuple,
        )
        or not allocation.allocations
    ):
        return False

    for item in allocation.allocations:
        if (
            not isinstance(
                item,
                LiveSellLotAllocation,
            )
            or not _strict_positive_sqlite_int(
                item.position_id
            )
            or not isinstance(
                item.position_version,
                str,
            )
            or not item.position_version.strip()
            or not _strict_nonnegative_sqlite_int(
                item.entry_slot
            )
            or not _strict_positive_sqlite_int(
                item.tokens_before
            )
            or not _strict_positive_sqlite_int(
                item.tokens_to_sell
            )
            or not _strict_nonnegative_sqlite_int(
                item.tokens_after
            )
            or not _strict_nonnegative_sqlite_int(
                item.exposure_before_lamports
            )
            or not _strict_nonnegative_sqlite_int(
                item.exposure_reduction_lamports
            )
            or not _strict_nonnegative_sqlite_int(
                item.exposure_after_lamports
            )
            or not _strict_nonnegative_sqlite_int(
                item.cost_basis_before_lamports
            )
            or not _strict_nonnegative_sqlite_int(
                item.cost_basis_reduction_lamports
            )
            or not _strict_nonnegative_sqlite_int(
                item.cost_basis_after_lamports
            )
            or not _strict_nonnegative_sqlite_int(
                item
                .cumulative_net_proceeds_before_lamports
            )
            or not _strict_sqlite_int(
                item
                .cumulative_realized_pnl_before_lamports
            )
        ):
            return False

    return True


def _authorization_contract_valid(
    authorization: LivePumpSellAuthorization,
) -> bool:
    return (
        authorization.authorization_version
        == LIVE_PUMP_SELL_AUTHORIZATION_VERSION
        and _valid_sha256(
            authorization.authorization_sha256
        )
        and _authorization_fingerprint_matches(
            authorization
        )
        and _valid_pubkey(
            authorization.wallet_pubkey
        )
        and _valid_pubkey(
            authorization.mint
        )
        and _strict_positive_sqlite_int(
            authorization.tokens_to_sell
        )
        and isinstance(
            authorization.allocation,
            LiveSellAllocationPlan,
        )
        and _allocation_is_persistable(
            authorization.allocation
        )
        and authorization.allocation.wallet_pubkey
        == authorization.wallet_pubkey
        and authorization.allocation.mint
        == authorization.mint
        and authorization.allocation.requested_tokens
        == authorization.tokens_to_sell
    )


def _load_open_positions_in_transaction(
    *,
    connection: sqlite3.Connection,
    wallet_pubkey: str,
) -> tuple[
    str,
    tuple[str, ...],
    tuple[LivePosition, ...] | None,
]:
    try:
        rows = connection.execute(
            """
            SELECT *

            FROM live_positions

            WHERE wallet_pubkey = ?
              AND status = ?

            ORDER BY position_id ASC
            """,
            (
                wallet_pubkey,
                OPEN,
            ),
        ).fetchall()

    except sqlite3.OperationalError as error:
        if (
            "no such table: live_positions"
            in str(error)
        ):
            return (
                UNKNOWN,
                (
                    "LIVE_POSITIONS_TABLE_NOT_FOUND",
                ),
                None,
            )

        return (
            UNKNOWN,
            (
                "LIVE_POSITION_READ_FAILED",
            ),
            None,
        )

    except sqlite3.Error:
        return (
            UNKNOWN,
            (
                "LIVE_POSITION_READ_FAILED",
            ),
            None,
        )

    positions: list[
        LivePosition
    ] = []

    for row in rows:
        try:
            positions.append(
                _row_to_position(
                    row
                )
            )

        except Exception:
            return (
                UNKNOWN,
                (
                    "LIVE_POSITION_ROW_INVALID",
                ),
                None,
            )

    return (
        PASS,
        (),
        tuple(positions),
    )


def _row_to_lot(
    row: sqlite3.Row,
) -> LiveSellLotAllocation:
    return LiveSellLotAllocation(
        position_id=int(
            row["position_id"]
        ),
        position_version=str(
            row["position_version"]
        ),
        entry_slot=int(
            row["entry_slot"]
        ),
        tokens_before=int(
            row["tokens_before"]
        ),
        tokens_to_sell=int(
            row["tokens_to_sell"]
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
        cumulative_realized_pnl_before_lamports=int(
            row[
                "cumulative_realized_pnl_before_lamports"
            ]
        ),
    )


def _load_claim(
    *,
    connection: sqlite3.Connection,
    authorization_sha256: str,
) -> LiveSellInventoryClaim | None:
    row = connection.execute(
        """
        SELECT *

        FROM live_sell_inventory_claims

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

        FROM live_sell_inventory_claim_lots

        WHERE authorization_sha256 = ?

        ORDER BY ordinal ASC
        """,
        (
            authorization_sha256,
        ),
    ).fetchall()

    allocations = tuple(
        _row_to_lot(
            lot_row
        )
        for lot_row in lot_rows
    )

    allocation = LiveSellAllocationPlan(
        allocation_version=str(
            row["allocation_version"]
        ),
        allocation_method=str(
            row["allocation_method"]
        ),
        status=PLANNED,
        reasons=(),
        wallet_pubkey=str(
            row["wallet_pubkey"]
        ),
        mint=str(
            row["mint"]
        ),
        requested_tokens=int(
            row["requested_tokens"]
        ),
        total_tokens_before=int(
            row["total_tokens_before"]
        ),
        total_tokens_after=int(
            row["total_tokens_after"]
        ),
        total_exposure_before_lamports=int(
            row[
                "total_exposure_before_lamports"
            ]
        ),
        total_exposure_reduction_lamports=int(
            row[
                "total_exposure_reduction_lamports"
            ]
        ),
        total_exposure_after_lamports=int(
            row[
                "total_exposure_after_lamports"
            ]
        ),
        total_cost_basis_before_lamports=int(
            row[
                "total_cost_basis_before_lamports"
            ]
        ),
        total_cost_basis_reduction_lamports=int(
            row[
                "total_cost_basis_reduction_lamports"
            ]
        ),
        total_cost_basis_after_lamports=int(
            row[
                "total_cost_basis_after_lamports"
            ]
        ),
        allocations=allocations,
    )

    terminal_at = (
        None
        if row["terminal_at"] is None
        else float(
            row["terminal_at"]
        )
    )

    terminal_reason = (
        None
        if row["terminal_reason"] is None
        else str(
            row["terminal_reason"]
        )
    )

    return LiveSellInventoryClaim(
        claim_version=str(
            row["claim_version"]
        ),
        authorization_version=str(
            row[
                "authorization_version"
            ]
        ),
        authorization_sha256=str(
            row[
                "authorization_sha256"
            ]
        ),
        wallet_pubkey=str(
            row["wallet_pubkey"]
        ),
        mint=str(
            row["mint"]
        ),
        tokens_to_sell=int(
            row["tokens_to_sell"]
        ),
        allocation=allocation,
        status=str(
            row["status"]
        ),
        claimed_at=float(
            row["claimed_at"]
        ),
        terminal_at=terminal_at,
        terminal_reason=terminal_reason,
    )


def _claim_identity_matches_authorization(
    *,
    claim: LiveSellInventoryClaim,
    authorization: LivePumpSellAuthorization,
) -> bool:
    return (
        claim.claim_version
        == LIVE_SELL_INVENTORY_CLAIM_VERSION
        and claim.authorization_version
        == LIVE_PUMP_SELL_AUTHORIZATION_VERSION
        and claim.authorization_sha256
        == authorization.authorization_sha256
        and claim.wallet_pubkey
        == authorization.wallet_pubkey
        and claim.mint
        == authorization.mint
        and claim.tokens_to_sell
        == authorization.tokens_to_sell
        and claim.allocation
        == authorization.allocation
    )


def _claim_matches_authorization(
    *,
    claim: LiveSellInventoryClaim,
    authorization: LivePumpSellAuthorization,
) -> bool:
    return (
        _claim_identity_matches_authorization(
            claim=claim,
            authorization=authorization,
        )
        and claim.status == ACTIVE
        and _strict_timestamp(
            claim.claimed_at
        )
        and claim.terminal_at is None
        and claim.terminal_reason is None
    )


def load_active_live_sell_inventory_claim_read_only(
    *,
    authorization: LivePumpSellAuthorization,
    db_path: Path = DB_PATH,
) -> ActiveLiveSellInventoryClaimResult:
    """
    Load and validate the exact durable ACTIVE
    inventory claim for one SELL authorization.

    This function is strictly read-only. It never
    initializes schema, acquires inventory, releases
    inventory, or mutates live-position state.
    """

    authorization_sha256 = ""

    def finish(
        status: str,
        *reasons: str,
        claim: (
            LiveSellInventoryClaim
            | None
        ) = None,
    ) -> ActiveLiveSellInventoryClaimResult:
        return ActiveLiveSellInventoryClaimResult(
            loader_version=(
                ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            authorization_sha256=(
                authorization_sha256
            ),
            claim=claim,
        )

    if not isinstance(
        authorization,
        LivePumpSellAuthorization,
    ):
        return finish(
            UNKNOWN,
            "INVALID_SELL_AUTHORIZATION",
        )

    authorization_sha256 = (
        authorization.authorization_sha256
    )

    if not _authorization_contract_valid(
        authorization
    ):
        return finish(
            UNKNOWN,
            "SELL_AUTHORIZATION_CONTRACT_INVALID",
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
            "SELL_CLAIM_READ_FAILED",
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
            claim = _load_claim(
                connection=connection,
                authorization_sha256=(
                    authorization_sha256
                ),
            )

            active_rows = (
                connection.execute(
                    """
                    SELECT authorization_sha256

                    FROM live_sell_inventory_claims

                    WHERE wallet_pubkey = ?
                      AND mint = ?
                      AND status = ?

                    ORDER BY authorization_sha256 ASC
                    """,
                    (
                        authorization.wallet_pubkey,
                        authorization.mint,
                        ACTIVE,
                    ),
                ).fetchall()
            )

        except sqlite3.OperationalError as error:
            error_text = str(
                error
            )

            if (
                "no such table: live_sell_inventory_claims"
                in error_text
                or
                "no such table: live_sell_inventory_claim_lots"
                in error_text
            ):
                return finish(
                    UNKNOWN,
                    "LIVE_SELL_CLAIM_TABLE_NOT_FOUND",
                )

            return finish(
                UNKNOWN,
                "SELL_CLAIM_READ_FAILED",
            )

        except sqlite3.Error:
            return finish(
                UNKNOWN,
                "SELL_CLAIM_READ_FAILED",
            )

        except Exception:
            return finish(
                UNKNOWN,
                "SELL_CLAIM_ROW_INVALID",
            )

        if len(active_rows) > 1:
            return finish(
                UNKNOWN,
                "MULTIPLE_ACTIVE_SELL_CLAIMS",
            )

        if claim is None:
            if active_rows:
                return finish(
                    BLOCK,
                    "ACTIVE_SELL_CLAIM_AUTHORIZATION_MISMATCH",
                )

            return finish(
                BLOCK,
                "ACTIVE_SELL_CLAIM_NOT_FOUND",
            )

        if not _claim_identity_matches_authorization(
            claim=claim,
            authorization=authorization,
        ):
            return finish(
                UNKNOWN,
                "SELL_CLAIM_EVIDENCE_MISMATCH",
                claim=claim,
            )

        if claim.status != ACTIVE:
            return finish(
                BLOCK,
                "SELL_CLAIM_NOT_ACTIVE",
                claim=claim,
            )

        if (
            not _strict_timestamp(
                claim.claimed_at
            )
            or claim.terminal_at is not None
            or claim.terminal_reason is not None
        ):
            return finish(
                UNKNOWN,
                "ACTIVE_SELL_CLAIM_STATE_INCOHERENT",
                claim=claim,
            )

        if len(active_rows) != 1:
            return finish(
                UNKNOWN,
                "ACTIVE_SELL_CLAIM_INDEX_INCOHERENT",
                claim=claim,
            )

        active_authorization_sha256 = str(
            active_rows[0][
                "authorization_sha256"
            ]
        )

        if (
            active_authorization_sha256
            != authorization_sha256
        ):
            return finish(
                UNKNOWN,
                "ACTIVE_SELL_CLAIM_INDEX_INCOHERENT",
                claim=claim,
            )

        return finish(
            PASS,
            claim=claim,
        )

    finally:
        connection.close()


def acquire_live_sell_inventory_claim(
    *,
    authorization: LivePumpSellAuthorization,
    db_path: Path = DB_PATH,
) -> LiveSellInventoryClaimResult:
    authorization_sha256 = ""

    def finish(
        status: str,
        *reasons: str,
        claim: (
            LiveSellInventoryClaim
            | None
        ) = None,
        changed: bool = False,
    ) -> LiveSellInventoryClaimResult:
        return LiveSellInventoryClaimResult(
            resolver_version=(
                LIVE_SELL_INVENTORY_CLAIM_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            authorization_sha256=(
                authorization_sha256
            ),
            claim=claim,
            changed=changed,
        )

    if not isinstance(
        authorization,
        LivePumpSellAuthorization,
    ):
        return finish(
            UNKNOWN,
            "INVALID_SELL_AUTHORIZATION",
        )

    authorization_sha256 = (
        authorization.authorization_sha256
    )

    if not _authorization_contract_valid(
        authorization
    ):
        return finish(
            UNKNOWN,
            "SELL_AUTHORIZATION_CONTRACT_INVALID",
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

    connection: (
        sqlite3.Connection | None
    ) = None

    try:
        connection = get_connection(
            db_path
        )

        connection.execute(
            "BEGIN IMMEDIATE"
        )

        load_status, load_reasons, positions = (
            _load_open_positions_in_transaction(
                connection=connection,
                wallet_pubkey=(
                    authorization.wallet_pubkey
                ),
            )
        )

        if (
            load_status != PASS
            or positions is None
        ):
            connection.commit()

            return finish(
                UNKNOWN,
                *load_reasons,
            )

        current_allocation = (
            plan_live_sell_allocation(
                wallet_pubkey=(
                    authorization.wallet_pubkey
                ),
                mint=authorization.mint,
                tokens_to_sell=(
                    authorization.tokens_to_sell
                ),
                positions=positions,
            )
        )

        if (
            current_allocation.status
            == BLOCK
        ):
            connection.commit()

            return finish(
                BLOCK,
                *current_allocation.reasons,
            )

        if (
            current_allocation.status
            == UNKNOWN
            or current_allocation.status
            != PLANNED
        ):
            connection.commit()

            return finish(
                UNKNOWN,
                "SELL_CLAIM_ALLOCATION_NOT_PLANNED",
                *current_allocation.reasons,
            )

        #
        # This is the CAS comparison:
        #
        # authorization's immutable FIFO snapshot
        # must still equal the authoritative
        # database snapshot while holding SQLite's
        # write reservation.
        #
        if (
            current_allocation
            != authorization.allocation
        ):
            connection.commit()

            return finish(
                BLOCK,
                "SELL_AUTHORIZATION_ALLOCATION_STALE",
            )

        #
        # Claim schema may be initialized only after
        # authoritative position state has been read
        # successfully and still matches the exact
        # authorization allocation.
        #
        init_schema(
            connection
        )

        existing = _load_claim(
            connection=connection,
            authorization_sha256=(
                authorization_sha256
            ),
        )

        if existing is not None:
            if not _claim_matches_authorization(
                claim=existing,
                authorization=authorization,
            ):
                connection.commit()

                return finish(
                    UNKNOWN,
                    "SELL_CLAIM_EVIDENCE_MISMATCH",
                    claim=existing,
                )

            authorization_record = (
                _persist_live_sell_authorization_record_in_transaction(
                    connection=connection,
                    authorization=authorization,
                )
            )

            if (
                authorization_record.status
                != AUTHORIZATION_RECORD_PASS
                or authorization_record.authorization
                is None
            ):
                connection.rollback()

                return finish(
                    UNKNOWN,
                    "SELL_AUTHORIZATION_RECORD_BIND_FAILED",
                    *authorization_record.reasons,
                    claim=existing,
                )

            connection.commit()

            return finish(
                PASS,
                claim=existing,
                changed=False,
            )

        active_rows = connection.execute(
            """
            SELECT authorization_sha256

            FROM live_sell_inventory_claims

            WHERE wallet_pubkey = ?
              AND mint = ?
              AND status = ?
            """,
            (
                authorization.wallet_pubkey,
                authorization.mint,
                ACTIVE,
            ),
        ).fetchall()

        if len(active_rows) > 1:
            connection.commit()

            return finish(
                UNKNOWN,
                "MULTIPLE_ACTIVE_SELL_CLAIMS",
            )

        if active_rows:
            connection.commit()

            return finish(
                BLOCK,
                "SELL_INVENTORY_ALREADY_CLAIMED",
            )

        claimed_at = time.time()

        if not _strict_timestamp(
            claimed_at
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                "SYSTEM_TIME_INVALID",
            )

        authorization_record = (
            _persist_live_sell_authorization_record_in_transaction(
                connection=connection,
                authorization=authorization,
            )
        )

        if (
            authorization_record.status
            != AUTHORIZATION_RECORD_PASS
            or authorization_record.authorization
            is None
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                "SELL_AUTHORIZATION_RECORD_BIND_FAILED",
                *authorization_record.reasons,
            )

        allocation = (
            authorization.allocation
        )

        connection.execute(
            """
            INSERT INTO
                live_sell_inventory_claims (
                    authorization_sha256,
                    claim_version,
                    authorization_version,
                    wallet_pubkey,
                    mint,
                    tokens_to_sell,
                    allocation_version,
                    allocation_method,
                    requested_tokens,
                    total_tokens_before,
                    total_tokens_after,
                    total_exposure_before_lamports,
                    total_exposure_reduction_lamports,
                    total_exposure_after_lamports,
                    total_cost_basis_before_lamports,
                    total_cost_basis_reduction_lamports,
                    total_cost_basis_after_lamports,
                    status,
                    claimed_at,
                    terminal_at,
                    terminal_reason
                )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                authorization_sha256,
                LIVE_SELL_INVENTORY_CLAIM_VERSION,
                LIVE_PUMP_SELL_AUTHORIZATION_VERSION,
                authorization.wallet_pubkey,
                authorization.mint,
                authorization.tokens_to_sell,
                allocation.allocation_version,
                allocation.allocation_method,
                allocation.requested_tokens,
                allocation.total_tokens_before,
                allocation.total_tokens_after,
                allocation
                .total_exposure_before_lamports,
                allocation
                .total_exposure_reduction_lamports,
                allocation
                .total_exposure_after_lamports,
                allocation
                .total_cost_basis_before_lamports,
                allocation
                .total_cost_basis_reduction_lamports,
                allocation
                .total_cost_basis_after_lamports,
                ACTIVE,
                claimed_at,
                None,
                None,
            ),
        )

        assert allocation.allocations is not None

        for ordinal, lot in enumerate(
            allocation.allocations
        ):
            connection.execute(
                """
                INSERT INTO
                    live_sell_inventory_claim_lots (
                        authorization_sha256,
                        ordinal,
                        position_id,
                        position_version,
                        entry_slot,
                        tokens_before,
                        tokens_to_sell,
                        tokens_after,
                        exposure_before_lamports,
                        exposure_reduction_lamports,
                        exposure_after_lamports,
                        cost_basis_before_lamports,
                        cost_basis_reduction_lamports,
                        cost_basis_after_lamports,
                        cumulative_net_proceeds_before_lamports,
                        cumulative_realized_pnl_before_lamports
                    )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    authorization_sha256,
                    ordinal,
                    lot.position_id,
                    lot.position_version,
                    lot.entry_slot,
                    lot.tokens_before,
                    lot.tokens_to_sell,
                    lot.tokens_after,
                    lot.exposure_before_lamports,
                    lot
                    .exposure_reduction_lamports,
                    lot.exposure_after_lamports,
                    lot.cost_basis_before_lamports,
                    lot
                    .cost_basis_reduction_lamports,
                    lot.cost_basis_after_lamports,
                    lot
                    .cumulative_net_proceeds_before_lamports,
                    lot
                    .cumulative_realized_pnl_before_lamports,
                ),
            )

        persisted = _load_claim(
            connection=connection,
            authorization_sha256=(
                authorization_sha256
            ),
        )

        if (
            persisted is None
            or not _claim_matches_authorization(
                claim=persisted,
                authorization=authorization,
            )
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                "SELL_CLAIM_ATOMIC_VERIFICATION_FAILED",
            )

        persisted_authorization = (
            _load_live_sell_authorization_record_in_transaction(
                connection=connection,
                authorization_sha256=(
                    authorization_sha256
                ),
            )
        )

        if (
            persisted_authorization.status
            != AUTHORIZATION_RECORD_PASS
            or persisted_authorization.authorization
            is None
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                "SELL_AUTHORIZATION_RECORD_ATOMIC_VERIFICATION_FAILED",
                *persisted_authorization.reasons,
            )

        connection.commit()

        return finish(
            PASS,
            claim=persisted,
            changed=True,
        )

    except sqlite3.IntegrityError:
        if connection is not None:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass

        #
        # Normal competing SELLs are detected
        # explicitly while BEGIN IMMEDIATE holds
        # the write reservation. An unexpected
        # integrity failure is therefore not
        # safely classifiable as a normal conflict.
        #
        return finish(
            UNKNOWN,
            "SELL_CLAIM_INTEGRITY_ERROR",
        )

    except sqlite3.Error:
        if connection is not None:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass

        return finish(
            UNKNOWN,
            "SELL_CLAIM_DATABASE_ERROR",
        )

    except Exception:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass

        return finish(
            UNKNOWN,
            "SELL_CLAIM_FAILED",
        )

    finally:
        if connection is not None:
            connection.close()
