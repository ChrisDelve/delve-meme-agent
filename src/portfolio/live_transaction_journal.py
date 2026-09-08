from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.portfolio.live_reservations import (
    ACTIVE,
    DB_PATH,
    RELEASED,
    RESERVATION_VERSION,
    SIGNED,
    SQLITE_INT_MAX,
    SUBMITTED,
    get_connection,
    init_schema as init_reservation_schema,
)


LIVE_TRANSACTION_JOURNAL_VERSION = (
    "live-transaction-journal-v1"
)

FAILED = "FAILED"

RECONCILED_FAILED_TRANSACTION_REASON = (
    "RECONCILED_FAILED_TRANSACTION"
)

PASS = "PASS"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveTransactionJournalEntry:
    journal_version: str

    transaction_signature: str
    reservation_id: str

    wallet_pubkey: str
    mint: str
    side: str

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
class FailedTransactionLedgerResult:
    status: str
    reasons: tuple[str, ...]

    entry: LiveTransactionJournalEntry | None

    reservation_id: str
    reservation_status: str | None

    terminal_at: float | None
    terminal_reason: str | None

    changed: bool


def init_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        live_transaction_journal (
            transaction_signature
                TEXT PRIMARY KEY,

            reservation_id
                TEXT NOT NULL UNIQUE
                REFERENCES live_capital_reservations(
                    reservation_id
                ),

            journal_version TEXT NOT NULL,

            wallet_pubkey TEXT NOT NULL,
            mint TEXT NOT NULL,
            side TEXT NOT NULL,

            signed_transaction_sha256
                TEXT NOT NULL,

            receipt_transaction_sha256
                TEXT NOT NULL,

            receipt_resolver_version
                TEXT NOT NULL,

            slot INTEGER NOT NULL
                CHECK (slot >= 0),

            block_time INTEGER,

            outcome TEXT NOT NULL,

            transaction_error_json
                TEXT,

            fee_lamports INTEGER NOT NULL
                CHECK (fee_lamports >= 0),

            fee_payer_pubkey TEXT NOT NULL,

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
                    blockhash_rpc_slot >= 0
                ),

            recorded_at REAL NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        live_transaction_journal_outcome_slot
        ON live_transaction_journal (
            outcome,
            slot
        )
        """
    )


def _strict_nonnegative_int(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        and value <= SQLITE_INT_MAX
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


def _canonical_error_json(
    transaction_error: Any,
) -> str:
    if transaction_error is None:
        raise ValueError(
            "FAILED_TRANSACTION_ERROR_REQUIRED"
        )

    try:
        encoded = json.dumps(
            transaction_error,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
    except (
        TypeError,
        ValueError,
    ) as error:
        raise ValueError(
            "FAILED_TRANSACTION_ERROR_INVALID"
        ) from error

    if not encoded:
        raise ValueError(
            "FAILED_TRANSACTION_ERROR_INVALID"
        )

    return encoded


def _submission_metadata_coherent(
    row: sqlite3.Row,
) -> bool:
    status = str(
        row["status"]
    )

    submission_started_at = (
        row["submission_started_at"]
    )

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


def _row_to_entry(
    row: sqlite3.Row,
) -> LiveTransactionJournalEntry:
    return LiveTransactionJournalEntry(
        journal_version=str(
            row["journal_version"]
        ),
        transaction_signature=str(
            row["transaction_signature"]
        ),
        reservation_id=str(
            row["reservation_id"]
        ),
        wallet_pubkey=str(
            row["wallet_pubkey"]
        ),
        mint=str(
            row["mint"]
        ),
        side=str(
            row["side"]
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
            if row["block_time"] is None
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
            row["blockhash_rpc_slot"]
        ),
        recorded_at=float(
            row["recorded_at"]
        ),
    )


def load_transaction_journal_entry_read_only(
    *,
    reservation_id: str,
    db_path: Path = DB_PATH,
) -> LiveTransactionJournalEntry | None:
    """
    Read one live transaction journal entry without
    mutating the authoritative live database.

    This loader intentionally performs:
    - no schema initialization
    - no schema migration
    - no reservation transition
    - no journal write
    - no write-capable database open

    It exists for observational reconciliation and
    crash/concurrency recovery paths.
    """

    if not isinstance(
        reservation_id,
        str,
    ):
        return None

    reservation_id = (
        reservation_id.strip()
    )

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
        try:
            row = connection.execute(
                """
                SELECT *
                FROM live_transaction_journal
                WHERE reservation_id = ?
                """,
                (
                    reservation_id,
                ),
            ).fetchone()

        except sqlite3.OperationalError as error:
            if (
                "no such table:"
                " live_transaction_journal"
                in str(error)
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


def record_failed_transaction_and_release_reservation(
    *,
    reservation_id: str,
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
) -> FailedTransactionLedgerResult:
    """
    Atomically journal one independently resolved
    failed on-chain transaction and release its held
    SIGNED/SUBMITTED capital reservation.

    This function performs no chain observation.

    It has no:
    - RPC authority
    - receipt-resolution authority
    - signing authority
    - send authority
    - position authority

    The transaction-journal insert and reservation
    release either commit together or roll back
    together.
    """

    string_values = {
        "reservation_id": reservation_id,
        "receipt_resolver_version": (
            receipt_resolver_version
        ),
        "transaction_signature": (
            transaction_signature
        ),
        "fee_payer_pubkey": (
            fee_payer_pubkey
        ),
    }

    normalized: dict[str, str] = {}

    for name, value in (
        string_values.items()
    ):
        if not isinstance(
            value,
            str,
        ):
            value = ""

        value = value.strip()
        normalized[name] = value

        if not value:
            return FailedTransactionLedgerResult(
                status=UNKNOWN,
                reasons=(
                    f"INVALID_{name.upper()}",
                ),
                entry=None,
                reservation_id=(
                    normalized.get(
                        "reservation_id",
                        "",
                    )
                ),
                reservation_status=None,
                terminal_at=None,
                terminal_reason=None,
                changed=False,
            )

    reservation_id = normalized[
        "reservation_id"
    ]

    receipt_resolver_version = (
        normalized[
            "receipt_resolver_version"
        ]
    )

    transaction_signature = normalized[
        "transaction_signature"
    ]

    fee_payer_pubkey = normalized[
        "fee_payer_pubkey"
    ]

    if not _valid_sha256(
        signed_transaction_sha256
    ):
        return FailedTransactionLedgerResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_SIGNED_TRANSACTION_SHA256",
            ),
            entry=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    if not _valid_sha256(
        receipt_transaction_sha256
    ):
        return FailedTransactionLedgerResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_RECEIPT_TRANSACTION_SHA256",
            ),
            entry=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    if (
        receipt_transaction_sha256
        != signed_transaction_sha256
    ):
        return FailedTransactionLedgerResult(
            status=UNKNOWN,
            reasons=(
                "RECEIPT_TRANSACTION_HASH_MISMATCH",
            ),
            entry=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
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

    for value, reason in integer_values:
        if not _strict_nonnegative_int(
            value
        ):
            return FailedTransactionLedgerResult(
                status=UNKNOWN,
                reasons=(reason,),
                entry=None,
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
        return FailedTransactionLedgerResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_BLOCK_TIME",
            ),
            entry=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    if (
        not isinstance(
            fee_payer_balance_delta_lamports,
            int,
        )
        or isinstance(
            fee_payer_balance_delta_lamports,
            bool,
        )
        or abs(
            fee_payer_balance_delta_lamports
        )
        > SQLITE_INT_MAX
    ):
        return FailedTransactionLedgerResult(
            status=UNKNOWN,
            reasons=(
                "INVALID_FEE_PAYER_BALANCE_DELTA",
            ),
            entry=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    if (
        fee_payer_post_balance_lamports
        - fee_payer_pre_balance_lamports
        != fee_payer_balance_delta_lamports
    ):
        return FailedTransactionLedgerResult(
            status=UNKNOWN,
            reasons=(
                "FEE_PAYER_BALANCE_DELTA_MISMATCH",
            ),
            entry=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    #
    # Failed transaction state changes are atomic;
    # only the charged transaction fee should remain
    # as a fee-payer lamport debit for this path.
    #
    if (
        fee_payer_balance_delta_lamports
        != -fee_lamports
    ):
        return FailedTransactionLedgerResult(
            status=UNKNOWN,
            reasons=(
                "FAILED_TRANSACTION_FEE_DELTA_MISMATCH",
            ),
            entry=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

    try:
        transaction_error_json = (
            _canonical_error_json(
                transaction_error
            )
        )
    except ValueError as error:
        return FailedTransactionLedgerResult(
            status=UNKNOWN,
            reasons=(
                str(error),
            ),
            entry=None,
            reservation_id=reservation_id,
            reservation_status=None,
            terminal_at=None,
            terminal_reason=None,
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

        init_reservation_schema(
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

            return FailedTransactionLedgerResult(
                status=BLOCK,
                reasons=(
                    "RESERVATION_NOT_FOUND",
                ),
                entry=None,
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
            ] is None
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
            ] is None
            else str(
                reservation_row[
                    "terminal_reason"
                ]
            )
        )

        if (
            str(
                reservation_row[
                    "reservation_version"
                ]
            )
            != RESERVATION_VERSION
        ):
            connection.commit()

            return FailedTransactionLedgerResult(
                status=BLOCK,
                reasons=(
                    "RESERVATION_VERSION_MISMATCH",
                ),
                entry=None,
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

        journal_rows = (
            connection.execute(
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
            ).fetchall()
        )

        if len(journal_rows) > 1:
            connection.commit()

            return FailedTransactionLedgerResult(
                status=BLOCK,
                reasons=(
                    "FAILED_TRANSACTION_JOURNAL_IDENTITY_COLLISION",
                ),
                entry=None,
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

        existing_entry = (
            None
            if not journal_rows
            else _row_to_entry(
                journal_rows[0]
            )
        )

        reservation_evidence_matches = (
            reservation_row[
                "transaction_signature"
            ]
            == transaction_signature
            and reservation_row[
                "signed_transaction_sha256"
            ]
            == signed_transaction_sha256
            and reservation_row[
                "wallet_pubkey"
            ]
            == fee_payer_pubkey
        )

        reservation_last_valid_block_height = (
            reservation_row[
                "last_valid_block_height"
            ]
        )

        reservation_blockhash_rpc_slot = (
            reservation_row[
                "blockhash_rpc_slot"
            ]
        )

        reservation_provenance_valid = (
            _strict_nonnegative_int(
                reservation_last_valid_block_height
            )
            and _strict_nonnegative_int(
                reservation_blockhash_rpc_slot
            )
        )

        def entry_matches(
            entry: (
                LiveTransactionJournalEntry
                | None
            ),
        ) -> bool:
            return (
                entry is not None
                and entry.journal_version
                == LIVE_TRANSACTION_JOURNAL_VERSION
                and entry.transaction_signature
                == transaction_signature
                and entry.reservation_id
                == reservation_id
                and entry.wallet_pubkey
                == fee_payer_pubkey
                and entry.mint
                == str(
                    reservation_row["mint"]
                )
                and entry.side
                == str(
                    reservation_row["side"]
                )
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
                and entry.fee_payer_pre_balance_lamports
                == fee_payer_pre_balance_lamports
                and entry.fee_payer_post_balance_lamports
                == fee_payer_post_balance_lamports
                and entry.fee_payer_balance_delta_lamports
                == fee_payer_balance_delta_lamports
                and reservation_provenance_valid
                and entry.last_valid_block_height
                == reservation_last_valid_block_height
                and entry.blockhash_rpc_slot
                == reservation_blockhash_rpc_slot
            )

        #
        # Exact idempotent retry after the full
        # atomic transition already committed.
        #
        if reservation_status == RELEASED:
            if (
                terminal_reason
                != RECONCILED_FAILED_TRANSACTION_REASON
            ):
                connection.commit()

                return FailedTransactionLedgerResult(
                    status=BLOCK,
                    reasons=(
                        "FAILED_TRANSACTION_TERMINAL_REASON_MISMATCH",
                    ),
                    entry=existing_entry,
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
                not reservation_evidence_matches
                or not entry_matches(
                    existing_entry
                )
                or terminal_at is None
                or existing_entry is None
                or terminal_at
                != existing_entry.recorded_at
            ):
                connection.commit()

                return FailedTransactionLedgerResult(
                    status=BLOCK,
                    reasons=(
                        "FAILED_TRANSACTION_JOURNAL_EVIDENCE_MISMATCH",
                    ),
                    entry=existing_entry,
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

            return FailedTransactionLedgerResult(
                status=PASS,
                reasons=(),
                entry=existing_entry,
                reservation_id=reservation_id,
                reservation_status=RELEASED,
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

            return FailedTransactionLedgerResult(
                status=BLOCK,
                reasons=(
                    "RESERVATION_NOT_RECONCILABLE",
                ),
                entry=existing_entry,
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

            return FailedTransactionLedgerResult(
                status=BLOCK,
                reasons=(
                    "SUBMISSION_METADATA_INCONSISTENT",
                ),
                entry=existing_entry,
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

        if not reservation_evidence_matches:
            connection.commit()

            return FailedTransactionLedgerResult(
                status=BLOCK,
                reasons=(
                    "FAILED_TRANSACTION_EVIDENCE_MISMATCH",
                ),
                entry=existing_entry,
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
        # A journal row cannot legitimately predate
        # this atomic release transition.
        #
        if existing_entry is not None:
            connection.commit()

            return FailedTransactionLedgerResult(
                status=UNKNOWN,
                reasons=(
                    "FAILED_TRANSACTION_JOURNAL_STATE_INCONSISTENT",
                ),
                entry=existing_entry,
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

        last_valid_block_height = (
            reservation_last_valid_block_height
        )

        blockhash_rpc_slot = (
            reservation_blockhash_rpc_slot
        )

        if not reservation_provenance_valid:
            connection.rollback()

            return FailedTransactionLedgerResult(
                status=UNKNOWN,
                reasons=(
                    "RESERVATION_EXPIRY_PROVENANCE_INVALID",
                ),
                entry=None,
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
                str(
                    reservation_row[
                        "wallet_pubkey"
                    ]
                ),
                str(
                    reservation_row[
                        "mint"
                    ]
                ),
                str(
                    reservation_row[
                        "side"
                    ]
                ),
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
                int(
                    last_valid_block_height
                ),
                int(
                    blockhash_rpc_slot
                ),
                now,
            ),
        )

        updated = connection.execute(
            """
            UPDATE live_capital_reservations

            SET
                status = ?,
                terminal_at = ?,
                terminal_reason = ?

            WHERE reservation_id = ?
              AND status IN (?, ?)
              AND transaction_signature = ?
              AND signed_transaction_sha256 = ?
              AND wallet_pubkey = ?
            """,
            (
                RELEASED,
                now,
                RECONCILED_FAILED_TRANSACTION_REASON,
                reservation_id,
                SIGNED,
                SUBMITTED,
                transaction_signature,
                signed_transaction_sha256,
                fee_payer_pubkey,
            ),
        )

        if updated.rowcount != 1:
            connection.rollback()

            return FailedTransactionLedgerResult(
                status=UNKNOWN,
                reasons=(
                    "FAILED_TRANSACTION_RELEASE_TRANSITION_FAILED",
                ),
                entry=None,
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

        persisted_journal_row = (
            connection.execute(
                """
                SELECT *
                FROM live_transaction_journal
                WHERE transaction_signature = ?
                """,
                (
                    transaction_signature,
                ),
            ).fetchone()
        )

        persisted_reservation_row = (
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
            persisted_journal_row is None
            or persisted_reservation_row
            is None
        ):
            connection.rollback()

            return FailedTransactionLedgerResult(
                status=UNKNOWN,
                reasons=(
                    "FAILED_TRANSACTION_ATOMIC_VERIFICATION_MISSING",
                ),
                entry=None,
                reservation_id=reservation_id,
                reservation_status=None,
                terminal_at=None,
                terminal_reason=None,
                changed=False,
            )

        persisted_entry = _row_to_entry(
            persisted_journal_row
        )

        persisted_terminal_at = (
            persisted_reservation_row[
                "terminal_at"
            ]
        )

        persisted_terminal_reason = (
            persisted_reservation_row[
                "terminal_reason"
            ]
        )

        if (
            not entry_matches(
                persisted_entry
            )
            or persisted_reservation_row[
                "status"
            ]
            != RELEASED
            or persisted_terminal_at
            is None
            or float(
                persisted_terminal_at
            )
            != persisted_entry.recorded_at
            or persisted_terminal_reason
            != RECONCILED_FAILED_TRANSACTION_REASON
            or persisted_reservation_row[
                "transaction_signature"
            ]
            != transaction_signature
            or persisted_reservation_row[
                "signed_transaction_sha256"
            ]
            != signed_transaction_sha256
        ):
            connection.rollback()

            return FailedTransactionLedgerResult(
                status=UNKNOWN,
                reasons=(
                    "FAILED_TRANSACTION_ATOMIC_VERIFICATION_FAILED",
                ),
                entry=None,
                reservation_id=reservation_id,
                reservation_status=None,
                terminal_at=None,
                terminal_reason=None,
                changed=False,
            )

        connection.commit()

        return FailedTransactionLedgerResult(
            status=PASS,
            reasons=(),
            entry=persisted_entry,
            reservation_id=reservation_id,
            reservation_status=RELEASED,
            terminal_at=float(
                persisted_terminal_at
            ),
            terminal_reason=str(
                persisted_terminal_reason
            ),
            changed=True,
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
