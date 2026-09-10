from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.live_pump_sell_authorization import (
    LivePumpSellAuthorization,
)
from src.execution.live_sell_transaction_receipt import (
    LIVE_SELL_TRANSACTION_RECEIPT_VERSION,
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


LIVE_SELL_TRANSACTION_JOURNAL_VERSION = (
    "live-sell-transaction-journal-v1"
)

FAILED = "FAILED"
SUCCESS = "SUCCESS"

RECONCILED_FAILED_SELL_REASON = (
    "RECONCILED_FAILED_SELL"
)

PASS = "PASS"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveSellTransactionJournalEntry:
    journal_version: str

    authorization_sha256: str

    transaction_signature: str

    wallet_pubkey: str
    mint: str
    tokens_to_sell: int

    signed_transaction_sha256: str
    receipt_transaction_sha256: str
    receipt_resolver_version: str

    slot: int
    block_time: int | None

    outcome: str
    transaction_error_json: str

    fee_lamports: int

    fee_payer_pubkey: str
    fee_payer_pre_balance_lamports: int
    fee_payer_post_balance_lamports: int
    fee_payer_balance_delta_lamports: int

    last_valid_block_height: int
    blockhash_rpc_slot: int

    recorded_at: float


@dataclass(frozen=True)
class FailedSellLedgerResult:
    status: str
    reasons: tuple[str, ...]

    entry: (
        LiveSellTransactionJournalEntry
        | None
    )

    authorization_sha256: str

    claim_status: str | None
    terminal_at: float | None
    terminal_reason: str | None

    changed: bool


def _strict_nonnegative_sqlite_int(
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
        and 0 <= value <= SQLITE_INT_MAX
    )


def _strict_sqlite_int(
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


def _canonical_error_json(
    transaction_error: Any,
) -> str:
    if transaction_error is None:
        raise ValueError(
            "FAILED_SELL_TRANSACTION_ERROR_REQUIRED"
        )

    try:
        encoded = json.dumps(
            transaction_error,
            sort_keys=True,
            separators=(
                ",",
                ":",
            ),
            ensure_ascii=False,
            allow_nan=False,
        )

        decoded = json.loads(
            encoded
        )

    except (
        TypeError,
        ValueError,
    ) as error:
        raise ValueError(
            "FAILED_SELL_TRANSACTION_ERROR_INVALID"
        ) from error

    if decoded is None:
        raise ValueError(
            "FAILED_SELL_TRANSACTION_ERROR_REQUIRED"
        )

    return encoded


def init_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        live_sell_transaction_journal (
            authorization_sha256
                TEXT PRIMARY KEY
                REFERENCES
                    live_sell_inventory_claims(
                        authorization_sha256
                    )
                ON DELETE RESTRICT,

            journal_version
                TEXT NOT NULL,

            transaction_signature
                TEXT NOT NULL
                UNIQUE,

            wallet_pubkey
                TEXT NOT NULL,

            mint
                TEXT NOT NULL,

            tokens_to_sell
                INTEGER NOT NULL
                CHECK (tokens_to_sell > 0),

            signed_transaction_sha256
                TEXT NOT NULL
                UNIQUE,

            receipt_transaction_sha256
                TEXT NOT NULL,

            receipt_resolver_version
                TEXT NOT NULL,

            slot
                INTEGER NOT NULL
                CHECK (slot >= 0),

            block_time
                INTEGER
                CHECK (
                    block_time IS NULL
                    OR block_time >= 0
                ),

            outcome
                TEXT NOT NULL
                CHECK (
                    outcome IN (
                        'FAILED',
                        'SUCCESS'
                    )
                ),

            transaction_error_json
                TEXT NOT NULL,

            fee_lamports
                INTEGER NOT NULL
                CHECK (fee_lamports >= 0),

            fee_payer_pubkey
                TEXT NOT NULL,

            fee_payer_pre_balance_lamports
                INTEGER NOT NULL
                CHECK (
                    fee_payer_pre_balance_lamports
                    >= 0
                ),

            fee_payer_post_balance_lamports
                INTEGER NOT NULL
                CHECK (
                    fee_payer_post_balance_lamports
                    >= 0
                ),

            fee_payer_balance_delta_lamports
                INTEGER NOT NULL,

            last_valid_block_height
                INTEGER NOT NULL
                CHECK (
                    last_valid_block_height
                    >= 0
                ),

            blockhash_rpc_slot
                INTEGER NOT NULL
                CHECK (
                    blockhash_rpc_slot
                    >= 0
                ),

            recorded_at
                REAL NOT NULL
        )
        """
    )


def _row_to_entry(
    row: sqlite3.Row,
) -> LiveSellTransactionJournalEntry:
    return LiveSellTransactionJournalEntry(
        journal_version=str(
            row["journal_version"]
        ),
        authorization_sha256=str(
            row[
                "authorization_sha256"
            ]
        ),
        transaction_signature=str(
            row[
                "transaction_signature"
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
        signed_transaction_sha256=str(
            row[
                "signed_transaction_sha256"
            ]
        ),
        receipt_transaction_sha256=str(
            row[
                "receipt_transaction_sha256"
            ]
        ),
        receipt_resolver_version=str(
            row[
                "receipt_resolver_version"
            ]
        ),
        slot=int(
            row["slot"]
        ),
        block_time=(
            None
            if row["block_time"]
            is None
            else int(
                row["block_time"]
            )
        ),
        outcome=str(
            row["outcome"]
        ),
        transaction_error_json=str(
            row[
                "transaction_error_json"
            ]
        ),
        fee_lamports=int(
            row["fee_lamports"]
        ),
        fee_payer_pubkey=str(
            row["fee_payer_pubkey"]
        ),
        fee_payer_pre_balance_lamports=int(
            row[
                "fee_payer_pre_balance_lamports"
            ]
        ),
        fee_payer_post_balance_lamports=int(
            row[
                "fee_payer_post_balance_lamports"
            ]
        ),
        fee_payer_balance_delta_lamports=int(
            row[
                "fee_payer_balance_delta_lamports"
            ]
        ),
        last_valid_block_height=int(
            row[
                "last_valid_block_height"
            ]
        ),
        blockhash_rpc_slot=int(
            row[
                "blockhash_rpc_slot"
            ]
        ),
        recorded_at=float(
            row["recorded_at"]
        ),
    )


def load_live_sell_transaction_journal_entry_read_only(
    *,
    authorization_sha256: str,
    db_path: Path = DB_PATH,
) -> LiveSellTransactionJournalEntry | None:
    if not _valid_sha256(
        authorization_sha256
    ):
        return None

    connection = get_connection(
        db_path
    )

    try:
        try:
            row = connection.execute(
                """
                SELECT *

                FROM live_sell_transaction_journal

                WHERE authorization_sha256 = ?
                """,
                (
                    authorization_sha256,
                ),
            ).fetchone()

        except sqlite3.OperationalError as error:
            if (
                "no such table"
                in str(error).lower()
            ):
                return None

            raise

        if row is None:
            return None

        return _row_to_entry(
            row
        )

    finally:
        connection.close()


def record_failed_sell_and_release_claim(
    *,
    authorization: LivePumpSellAuthorization,

    receipt_resolver_version: str,

    transaction_signature: str,

    signed_transaction_sha256: str,
    receipt_transaction_sha256: str,

    receipt_slot: int,
    block_time: int | None,

    transaction_error: Any,

    fee_lamports: int,

    fee_payer_pubkey: str,
    fee_payer_pre_balance_lamports: int,
    fee_payer_post_balance_lamports: int,
    fee_payer_balance_delta_lamports: int,

    db_path: Path = DB_PATH,
) -> FailedSellLedgerResult:
    """
    Atomically record one exact failed landed SELL and
    release its durable inventory claim.

    This primitive has no:
    - RPC authority
    - signing authority
    - transaction-build authority
    - send authority
    - position mutation authority
    - PnL/accounting authority

    Claim allocation rows remain immutable historical
    evidence.

    Exact retries after a completed transition are
    idempotent.
    """

    authorization_sha256 = ""

    if isinstance(
        authorization,
        LivePumpSellAuthorization,
    ):
        authorization_sha256 = (
            authorization.authorization_sha256
        )

    claim_status: (
        str | None
    ) = None

    terminal_at: (
        float | None
    ) = None

    terminal_reason: (
        str | None
    ) = None

    def finish(
        status: str,
        *reasons: str,
        entry: (
            LiveSellTransactionJournalEntry
            | None
        ) = None,
        changed: bool = False,
    ) -> FailedSellLedgerResult:
        return FailedSellLedgerResult(
            status=status,
            reasons=tuple(
                reasons
            ),
            entry=entry,
            authorization_sha256=(
                authorization_sha256
            ),
            claim_status=claim_status,
            terminal_at=terminal_at,
            terminal_reason=terminal_reason,
            changed=changed,
        )

    #
    # Validate the complete signed SELL authorization
    # independently of the caller.
    #
    if (
        not isinstance(
            authorization,
            LivePumpSellAuthorization,
        )
        or not _authorization_contract_valid(
            authorization
        )
    ):
        return finish(
            UNKNOWN,
            "SELL_AUTHORIZATION_CONTRACT_INVALID",
        )

    authorization_sha256 = (
        authorization.authorization_sha256
    )

    if (
        not _nonempty_string(
            receipt_resolver_version
        )
        or receipt_resolver_version
        != LIVE_SELL_TRANSACTION_RECEIPT_VERSION
    ):
        return finish(
            UNKNOWN,
            "INVALID_RECEIPT_RESOLVER_VERSION",
        )

    if not _nonempty_string(
        transaction_signature
    ):
        return finish(
            UNKNOWN,
            "INVALID_TRANSACTION_SIGNATURE",
        )

    if not _valid_sha256(
        signed_transaction_sha256
    ):
        return finish(
            UNKNOWN,
            "INVALID_SIGNED_TRANSACTION_SHA256",
        )

    if not _valid_sha256(
        receipt_transaction_sha256
    ):
        return finish(
            UNKNOWN,
            "INVALID_RECEIPT_TRANSACTION_SHA256",
        )

    if (
        receipt_transaction_sha256
        != signed_transaction_sha256
    ):
        return finish(
            UNKNOWN,
            "RECEIPT_TRANSACTION_HASH_MISMATCH",
        )

    integer_values = (
        (
            receipt_slot,
            "INVALID_RECEIPT_SLOT",
        ),
        (
            fee_lamports,
            "INVALID_FEE_LAMPORTS",
        ),
        (
            fee_payer_pre_balance_lamports,
            "INVALID_FEE_PAYER_PRE_BALANCE",
        ),
        (
            fee_payer_post_balance_lamports,
            "INVALID_FEE_PAYER_POST_BALANCE",
        ),
    )

    for (
        value,
        reason,
    ) in integer_values:
        if not _strict_nonnegative_sqlite_int(
            value
        ):
            return finish(
                UNKNOWN,
                reason,
            )

    if (
        block_time is not None
        and not _strict_nonnegative_sqlite_int(
            block_time
        )
    ):
        return finish(
            UNKNOWN,
            "INVALID_BLOCK_TIME",
        )

    if not _strict_sqlite_int(
        fee_payer_balance_delta_lamports
    ):
        return finish(
            UNKNOWN,
            "INVALID_FEE_PAYER_BALANCE_DELTA",
        )

    if (
        fee_payer_post_balance_lamports
        - fee_payer_pre_balance_lamports
        != fee_payer_balance_delta_lamports
    ):
        return finish(
            UNKNOWN,
            "FEE_PAYER_BALANCE_DELTA_MISMATCH",
        )

    #
    # A failed Solana transaction must not be credited
    # with SELL proceeds. Only its charged network fee
    # may remain as a wallet lamport debit.
    #
    if (
        fee_payer_balance_delta_lamports
        != -fee_lamports
    ):
        return finish(
            UNKNOWN,
            "FAILED_SELL_FEE_DELTA_MISMATCH",
        )

    if not _nonempty_string(
        fee_payer_pubkey
    ):
        return finish(
            UNKNOWN,
            "INVALID_FEE_PAYER_PUBKEY",
        )

    try:
        transaction_error_json = (
            _canonical_error_json(
                transaction_error
            )
        )

    except ValueError as error:
        return finish(
            UNKNOWN,
            str(error),
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

        init_schema(
            connection
        )

        #
        # Claim and allocation are loaded under the same
        # write lock that will perform the terminal
        # transition.
        #
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

        claim_status = (
            claim.status
        )

        terminal_at = (
            claim.terminal_at
        )

        terminal_reason = (
            claim.terminal_reason
        )

        if (
            not _claim_identity_matches_authorization(
                claim=claim,
                authorization=authorization,
            )
            or not _strict_timestamp(
                claim.claimed_at
            )
        ):
            connection.commit()

            return finish(
                BLOCK,
                "SELL_CLAIM_IDENTITY_MISMATCH",
            )

        #
        # Decode and cryptographically validate the
        # execution row under this same transaction.
        #
        execution_row = (
            connection.execute(
                """
                SELECT *

                FROM live_sell_execution_records

                WHERE authorization_sha256 = ?
                """,
                (
                    authorization_sha256,
                ),
            ).fetchone()
        )

        if execution_row is None:
            connection.commit()

            return finish(
                BLOCK,
                "SELL_EXECUTION_RECORD_NOT_FOUND",
            )

        execution = (
            _row_to_record(
                execution_row
            )
        )

        if not _record_contract_valid(
            execution
        ):
            connection.commit()

            return finish(
                UNKNOWN,
                "SELL_EXECUTION_RECORD_INVALID",
            )

        if (
            execution.authorization_sha256
            != authorization_sha256
            or execution.authorization_version
            != authorization.authorization_version
            or execution.wallet_pubkey
            != authorization.wallet_pubkey
            or execution.mint
            != authorization.mint
            or execution.tokens_to_sell
            != authorization.tokens_to_sell
        ):
            connection.commit()

            return finish(
                BLOCK,
                "SELL_EXECUTION_AUTHORIZATION_MISMATCH",
            )

        if execution.status not in (
            SIGNED,
            SUBMISSION_ARMED,
            SUBMITTED,
        ):
            connection.commit()

            return finish(
                BLOCK,
                "SELL_EXECUTION_NOT_RECONCILABLE",
            )

        if (
            execution.transaction_signature
            != transaction_signature
            or execution.signed_transaction_sha256
            != signed_transaction_sha256
            or execution.wallet_pubkey
            != fee_payer_pubkey
        ):
            connection.commit()

            return finish(
                BLOCK,
                "FAILED_SELL_RECEIPT_EVIDENCE_MISMATCH",
            )

        #
        # Detect journal identity collisions across all
        # immutable transaction identifiers.
        #
        journal_rows = (
            connection.execute(
                """
                SELECT *

                FROM live_sell_transaction_journal

                WHERE authorization_sha256 = ?
                   OR transaction_signature = ?
                   OR signed_transaction_sha256 = ?
                """,
                (
                    authorization_sha256,
                    transaction_signature,
                    signed_transaction_sha256,
                ),
            ).fetchall()
        )

        if len(
            journal_rows
        ) > 1:
            connection.commit()

            return finish(
                BLOCK,
                "FAILED_SELL_JOURNAL_IDENTITY_COLLISION",
            )

        existing_entry = (
            None
            if not journal_rows
            else _row_to_entry(
                journal_rows[0]
            )
        )

        def entry_matches(
            entry: (
                LiveSellTransactionJournalEntry
                | None
            ),
        ) -> bool:
            return (
                entry is not None
                and entry.journal_version
                == LIVE_SELL_TRANSACTION_JOURNAL_VERSION
                and entry.authorization_sha256
                == authorization_sha256
                and entry.transaction_signature
                == transaction_signature
                and entry.wallet_pubkey
                == authorization.wallet_pubkey
                and entry.mint
                == authorization.mint
                and entry.tokens_to_sell
                == authorization.tokens_to_sell
                and entry.signed_transaction_sha256
                == signed_transaction_sha256
                and entry.receipt_transaction_sha256
                == receipt_transaction_sha256
                and entry.receipt_resolver_version
                == receipt_resolver_version
                and entry.slot
                == receipt_slot
                and entry.block_time
                == block_time
                and entry.outcome
                == FAILED
                and entry.transaction_error_json
                == transaction_error_json
                and entry.fee_lamports
                == fee_lamports
                and entry.fee_payer_pubkey
                == fee_payer_pubkey
                and entry
                .fee_payer_pre_balance_lamports
                == fee_payer_pre_balance_lamports
                and entry
                .fee_payer_post_balance_lamports
                == fee_payer_post_balance_lamports
                and entry
                .fee_payer_balance_delta_lamports
                == fee_payer_balance_delta_lamports
                and entry.last_valid_block_height
                == execution.last_valid_block_height
                and entry.blockhash_rpc_slot
                == execution.blockhash_rpc_slot
            )

        #
        # Exact idempotent retry after the atomic
        # release already committed.
        #
        if claim.status == RELEASED:
            if (
                claim.terminal_reason
                != RECONCILED_FAILED_SELL_REASON
            ):
                connection.commit()

                return finish(
                    BLOCK,
                    "FAILED_SELL_TERMINAL_REASON_MISMATCH",
                    entry=existing_entry,
                )

            if (
                claim.terminal_at is None
                or existing_entry is None
                or not entry_matches(
                    existing_entry
                )
                or claim.terminal_at
                != existing_entry.recorded_at
            ):
                connection.commit()

                return finish(
                    BLOCK,
                    "FAILED_SELL_JOURNAL_EVIDENCE_MISMATCH",
                    entry=existing_entry,
                )

            connection.commit()

            return finish(
                PASS,
                entry=existing_entry,
                changed=False,
            )

        if claim.status == CONSUMED:
            connection.commit()

            return finish(
                BLOCK,
                "SELL_CLAIM_ALREADY_CONSUMED",
                entry=existing_entry,
            )

        if claim.status != ACTIVE:
            connection.commit()

            return finish(
                BLOCK,
                "SELL_CLAIM_NOT_RECONCILABLE",
                entry=existing_entry,
            )

        if not _claim_matches_authorization(
            claim=claim,
            authorization=authorization,
        ):
            connection.commit()

            return finish(
                BLOCK,
                "ACTIVE_SELL_CLAIM_CONTRACT_MISMATCH",
                entry=existing_entry,
            )

        #
        # A journal row cannot legitimately predate the
        # atomic ACTIVE -> RELEASED transition.
        #
        if existing_entry is not None:
            connection.commit()

            return finish(
                UNKNOWN,
                "FAILED_SELL_JOURNAL_STATE_INCONSISTENT",
                entry=existing_entry,
            )

        now = time.time()

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
                transaction_signature,
                authorization.wallet_pubkey,
                authorization.mint,
                authorization.tokens_to_sell,
                signed_transaction_sha256,
                receipt_transaction_sha256,
                receipt_resolver_version,
                receipt_slot,
                block_time,
                FAILED,
                transaction_error_json,
                fee_lamports,
                fee_payer_pubkey,
                fee_payer_pre_balance_lamports,
                fee_payer_post_balance_lamports,
                fee_payer_balance_delta_lamports,
                execution.last_valid_block_height,
                execution.blockhash_rpc_slot,
                now,
            ),
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
              AND wallet_pubkey = ?
              AND mint = ?
              AND tokens_to_sell = ?
            """,
            (
                RELEASED,
                now,
                RECONCILED_FAILED_SELL_REASON,
                authorization_sha256,
                ACTIVE,
                authorization.wallet_pubkey,
                authorization.mint,
                authorization.tokens_to_sell,
            ),
        )

        if updated.rowcount != 1:
            connection.rollback()

            return finish(
                UNKNOWN,
                "FAILED_SELL_RELEASE_TRANSITION_FAILED",
                changed=False,
            )

        persisted_journal_row = (
            connection.execute(
                """
                SELECT *

                FROM live_sell_transaction_journal

                WHERE authorization_sha256 = ?
                """,
                (
                    authorization_sha256,
                ),
            ).fetchone()
        )

        persisted_claim = (
            _load_claim(
                connection=connection,
                authorization_sha256=(
                    authorization_sha256
                ),
            )
        )

        if (
            persisted_journal_row
            is None
            or persisted_claim
            is None
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                "FAILED_SELL_ATOMIC_VERIFICATION_MISSING",
                changed=False,
            )

        persisted_entry = (
            _row_to_entry(
                persisted_journal_row
            )
        )

        if (
            not entry_matches(
                persisted_entry
            )
            or persisted_claim.status
            != RELEASED
            or persisted_claim.terminal_at
            is None
            or persisted_claim.terminal_at
            != persisted_entry.recorded_at
            or persisted_claim.terminal_reason
            != RECONCILED_FAILED_SELL_REASON
            or not _claim_identity_matches_authorization(
                claim=persisted_claim,
                authorization=authorization,
            )
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                "FAILED_SELL_ATOMIC_VERIFICATION_FAILED",
                changed=False,
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
            entry=persisted_entry,
            changed=True,
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
