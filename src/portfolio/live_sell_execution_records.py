from __future__ import annotations

import hashlib
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solders.hash import Hash
from solders.message import to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from src.execution.live_blockhash_context import (
    LIVE_BLOCKHASH_CONTEXT_VERSION,
)
from src.execution.live_pump_sell_authorization import (
    LIVE_PUMP_SELL_AUTHORIZATION_VERSION,
    LivePumpSellAuthorization,
)
from src.execution.pump_sell_v2_unsigned_message import (
    PUMP_SELL_V2_UNSIGNED_MESSAGE_VERSION,
    SOLANA_PACKET_DATA_SIZE,
    PumpSellV2UnsignedMessagePlan,
)
from src.portfolio.live_reservations import (
    DB_PATH,
    SQLITE_INT_MAX,
    get_connection,
)
from src.portfolio.live_sell_claims import (
    ACTIVE as CLAIM_ACTIVE,
    BLOCK as CLAIM_BLOCK,
    PASS as CLAIM_PASS,
    UNKNOWN as CLAIM_UNKNOWN,
    load_active_live_sell_inventory_claim_read_only,
)


LIVE_SELL_EXECUTION_RECORD_VERSION = (
    "live-sell-execution-record-v1"
)

SIGNED = "SIGNED"

#
# These states are reserved now so the execution row
# does not require a CHECK-constraint table migration
# when the next submission-boundary slices are added.
#
SUBMISSION_ARMED = "SUBMISSION_ARMED"
SUBMITTED = "SUBMITTED"

PASS = "PASS"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveSellExecutionRecord:
    record_version: str

    authorization_version: str
    authorization_sha256: str

    wallet_pubkey: str
    mint: str
    tokens_to_sell: int

    status: str

    message_sha256: str

    transaction_signature: str
    signed_transaction_sha256: str
    signed_transaction_bytes: bytes

    blockhash_context_version: str
    recent_blockhash: str
    last_valid_block_height: int
    blockhash_rpc_slot: int

    signed_at: float
    updated_at: float


@dataclass(frozen=True)
class LiveSellExecutionRecordResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    record: LiveSellExecutionRecord | None

    changed: bool


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
        and float(value) >= 0.0
    )


def _valid_pubkey(
    value: Any,
) -> bool:
    if not isinstance(
        value,
        str,
    ):
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

    return (
        pubkey
        != Pubkey.default()
    )


def _valid_blockhash(
    value: Any,
) -> bool:
    if not isinstance(
        value,
        str,
    ):
        return False

    value = value.strip()

    if not value:
        return False

    try:
        blockhash = Hash.from_string(
            value
        )

    except Exception:
        return False

    return (
        blockhash
        != Hash.default()
    )


def init_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        live_sell_execution_records (
            authorization_sha256
                TEXT PRIMARY KEY
                REFERENCES
                    live_sell_inventory_claims(
                        authorization_sha256
                    )
                ON DELETE RESTRICT,

            record_version
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

            status
                TEXT NOT NULL
                CHECK (
                    status IN (
                        'SIGNED',
                        'SUBMISSION_ARMED',
                        'SUBMITTED'
                    )
                ),

            message_sha256
                TEXT NOT NULL,

            transaction_signature
                TEXT NOT NULL
                UNIQUE,

            signed_transaction_sha256
                TEXT NOT NULL
                UNIQUE,

            signed_transaction_bytes
                BLOB NOT NULL,

            blockhash_context_version
                TEXT NOT NULL,

            recent_blockhash
                TEXT NOT NULL,

            last_valid_block_height
                INTEGER NOT NULL
                CHECK (
                    last_valid_block_height >= 0
                ),

            blockhash_rpc_slot
                INTEGER NOT NULL
                CHECK (
                    blockhash_rpc_slot >= 0
                ),

            signed_at
                REAL NOT NULL,

            updated_at
                REAL NOT NULL
        )
        """
    )


def _row_to_record(
    row: sqlite3.Row,
) -> LiveSellExecutionRecord:
    return LiveSellExecutionRecord(
        record_version=str(
            row["record_version"]
        ),
        authorization_version=str(
            row["authorization_version"]
        ),
        authorization_sha256=str(
            row["authorization_sha256"]
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
        status=str(
            row["status"]
        ),
        message_sha256=str(
            row["message_sha256"]
        ),
        transaction_signature=str(
            row["transaction_signature"]
        ),
        signed_transaction_sha256=str(
            row[
                "signed_transaction_sha256"
            ]
        ),
        signed_transaction_bytes=bytes(
            row[
                "signed_transaction_bytes"
            ]
        ),
        blockhash_context_version=str(
            row[
                "blockhash_context_version"
            ]
        ),
        recent_blockhash=str(
            row["recent_blockhash"]
        ),
        last_valid_block_height=int(
            row[
                "last_valid_block_height"
            ]
        ),
        blockhash_rpc_slot=int(
            row["blockhash_rpc_slot"]
        ),
        signed_at=float(
            row["signed_at"]
        ),
        updated_at=float(
            row["updated_at"]
        ),
    )


def _record_contract_valid(
    record: LiveSellExecutionRecord,
) -> bool:
    if (
        not isinstance(
            record,
            LiveSellExecutionRecord,
        )
        or record.record_version
        != LIVE_SELL_EXECUTION_RECORD_VERSION
        or record.authorization_version
        != LIVE_PUMP_SELL_AUTHORIZATION_VERSION
        or not _valid_sha256(
            record.authorization_sha256
        )
        or not _valid_pubkey(
            record.wallet_pubkey
        )
        or not _valid_pubkey(
            record.mint
        )
        or not _strict_positive_sqlite_int(
            record.tokens_to_sell
        )
        or record.status
        not in (
            SIGNED,
            SUBMISSION_ARMED,
            SUBMITTED,
        )
        or not _valid_sha256(
            record.message_sha256
        )
        or not _valid_sha256(
            record.signed_transaction_sha256
        )
        or not isinstance(
            record.signed_transaction_bytes,
            bytes,
        )
        or not record.signed_transaction_bytes
        or len(
            record.signed_transaction_bytes
        )
        > SOLANA_PACKET_DATA_SIZE
        or record.blockhash_context_version
        != LIVE_BLOCKHASH_CONTEXT_VERSION
        or not _valid_blockhash(
            record.recent_blockhash
        )
        or not _strict_nonnegative_sqlite_int(
            record.last_valid_block_height
        )
        or not _strict_nonnegative_sqlite_int(
            record.blockhash_rpc_slot
        )
        or not _strict_timestamp(
            record.signed_at
        )
        or not _strict_timestamp(
            record.updated_at
        )
        or record.updated_at
        < record.signed_at
    ):
        return False

    try:
        expected_signature = (
            Signature.from_string(
                record.transaction_signature
            )
        )

    except Exception:
        return False

    if (
        expected_signature
        == Signature.default()
    ):
        return False

    try:
        transaction = (
            VersionedTransaction.from_bytes(
                record
                .signed_transaction_bytes
            )
        )

    except Exception:
        return False

    try:
        if (
            bytes(transaction)
            != record
            .signed_transaction_bytes
        ):
            return False

    except Exception:
        return False

    signatures = tuple(
        transaction.signatures
    )

    if signatures != (
        expected_signature,
    ):
        return False

    message = transaction.message

    if (
        message.header
        .num_required_signatures
        != 1
    ):
        return False

    account_keys = tuple(
        message.account_keys
    )

    if (
        not account_keys
        or str(
            account_keys[0]
        )
        != record.wallet_pubkey
    ):
        return False

    if (
        str(
            message.recent_blockhash
        )
        != record.recent_blockhash
    ):
        return False

    try:
        message_bytes = (
            to_bytes_versioned(
                message
            )
        )

    except Exception:
        return False

    actual_message_sha256 = (
        hashlib.sha256(
            message_bytes
        ).hexdigest()
    )

    if (
        actual_message_sha256
        != record.message_sha256
    ):
        return False

    actual_transaction_sha256 = (
        hashlib.sha256(
            record
            .signed_transaction_bytes
        ).hexdigest()
    )

    if (
        actual_transaction_sha256
        != record
        .signed_transaction_sha256
    ):
        return False

    try:
        signer_pubkey = (
            Pubkey.from_string(
                record.wallet_pubkey
            )
        )

        signature_valid = (
            expected_signature.verify(
                signer_pubkey,
                message_bytes,
            )
        )

    except Exception:
        return False

    if not signature_valid:
        return False

    try:
        verification_results = (
            transaction
            .verify_with_results()
        )

    except Exception:
        return False

    return (
        verification_results
        == [True]
    )


def _record_matches_candidate(
    *,
    record: LiveSellExecutionRecord,
    authorization: LivePumpSellAuthorization,
    message_sha256: str,
    transaction_signature: str,
    signed_transaction_sha256: str,
    signed_transaction_bytes: bytes,
    blockhash_context_version: str,
    recent_blockhash: str,
    last_valid_block_height: int,
    blockhash_rpc_slot: int,
) -> bool:
    return (
        _record_contract_valid(
            record
        )
        and record.authorization_version
        == authorization
        .authorization_version
        and record.authorization_sha256
        == authorization
        .authorization_sha256
        and record.wallet_pubkey
        == authorization.wallet_pubkey
        and record.mint
        == authorization.mint
        and record.tokens_to_sell
        == authorization.tokens_to_sell
        and record.message_sha256
        == message_sha256
        and record.transaction_signature
        == transaction_signature
        and record.signed_transaction_sha256
        == signed_transaction_sha256
        and record.signed_transaction_bytes
        == signed_transaction_bytes
        and record.blockhash_context_version
        == blockhash_context_version
        and record.recent_blockhash
        == recent_blockhash
        and record.last_valid_block_height
        == last_valid_block_height
        and record.blockhash_rpc_slot
        == blockhash_rpc_slot
    )


def _validate_candidate_signed_artifact(
    *,
    authorization: LivePumpSellAuthorization,
    message_plan: PumpSellV2UnsignedMessagePlan,
    transaction_signature: str,
    signed_transaction_bytes: bytes,
) -> tuple[
    str,
    tuple[str, ...],
    str | None,
]:
    if not isinstance(
        authorization,
        LivePumpSellAuthorization,
    ):
        return (
            UNKNOWN,
            (
                "INVALID_SELL_AUTHORIZATION",
            ),
            None,
        )

    if (
        authorization
        .authorization_version
        != LIVE_PUMP_SELL_AUTHORIZATION_VERSION
    ):
        return (
            BLOCK,
            (
                "AUTHORIZATION_VERSION_MISMATCH",
            ),
            None,
        )

    #
    # A signed artifact is not authoritative merely
    # because the wallet signed it. It must be the
    # exact immutable SELL MessageV0 already constructed
    # for this exact authorization.
    #
    if not isinstance(
        message_plan,
        PumpSellV2UnsignedMessagePlan,
    ):
        return (
            BLOCK,
            (
                "MESSAGE_PLAN_INVALID",
            ),
            None,
        )

    if (
        message_plan.builder_version
        != PUMP_SELL_V2_UNSIGNED_MESSAGE_VERSION
    ):
        return (
            BLOCK,
            (
                "MESSAGE_PLAN_VERSION_MISMATCH",
            ),
            None,
        )

    if (
        message_plan.authorization_version
        != authorization.authorization_version
        or message_plan.authorization_sha256
        != authorization.authorization_sha256
    ):
        return (
            BLOCK,
            (
                "MESSAGE_AUTHORIZATION_BINDING_MISMATCH",
            ),
            None,
        )

    if (
        message_plan.payer
        != authorization.wallet_pubkey
    ):
        return (
            BLOCK,
            (
                "MESSAGE_PAYER_BINDING_MISMATCH",
            ),
            None,
        )

    if (
        message_plan.blockhash_context_version
        != LIVE_BLOCKHASH_CONTEXT_VERSION
    ):
        return (
            BLOCK,
            (
                "BLOCKHASH_CONTEXT_VERSION_MISMATCH",
            ),
            None,
        )

    if not _valid_blockhash(
        message_plan.recent_blockhash
    ):
        return (
            BLOCK,
            (
                "RECENT_BLOCKHASH_INVALID",
            ),
            None,
        )

    if (
        not _strict_nonnegative_sqlite_int(
            message_plan.last_valid_block_height
        )
        or not _strict_nonnegative_sqlite_int(
            message_plan.blockhash_rpc_slot
        )
    ):
        return (
            BLOCK,
            (
                "BLOCKHASH_PROVENANCE_INVALID",
            ),
            None,
        )

    if not _valid_sha256(
        message_plan.message_sha256
    ):
        return (
            BLOCK,
            (
                "MESSAGE_SHA256_INVALID",
            ),
            None,
        )

    if (
        not _strict_positive_sqlite_int(
            message_plan
            .estimated_signed_transaction_size_bytes
        )
        or message_plan
        .estimated_signed_transaction_size_bytes
        > SOLANA_PACKET_DATA_SIZE
    ):
        return (
            BLOCK,
            (
                "MESSAGE_SIGNED_SIZE_INVALID",
            ),
            None,
        )

    try:
        plan_message_bytes = (
            to_bytes_versioned(
                message_plan.message
            )
        )

    except Exception:
        return (
            BLOCK,
            (
                "MESSAGE_SERIALIZATION_FAILED",
            ),
            None,
        )

    actual_plan_message_sha256 = (
        hashlib.sha256(
            plan_message_bytes
        ).hexdigest()
    )

    if (
        actual_plan_message_sha256
        != message_plan.message_sha256
    ):
        return (
            BLOCK,
            (
                "MESSAGE_FINGERPRINT_MISMATCH",
            ),
            None,
        )

    if (
        str(
            message_plan
            .message
            .recent_blockhash
        )
        != message_plan.recent_blockhash
    ):
        return (
            BLOCK,
            (
                "MESSAGE_BLOCKHASH_BINDING_MISMATCH",
            ),
            None,
        )

    if (
        message_plan
        .message
        .header
        .num_required_signatures
        != 1
    ):
        return (
            BLOCK,
            (
                "MESSAGE_SIGNER_COUNT_MISMATCH",
            ),
            None,
        )

    plan_account_keys = tuple(
        message_plan.message.account_keys
    )

    if (
        not plan_account_keys
        or str(
            plan_account_keys[0]
        )
        != authorization.wallet_pubkey
    ):
        return (
            BLOCK,
            (
                "MESSAGE_PAYER_BINDING_MISMATCH",
            ),
            None,
        )

    if (
        not isinstance(
            signed_transaction_bytes,
            bytes,
        )
        or not signed_transaction_bytes
    ):
        return (
            BLOCK,
            (
                "SIGNED_TRANSACTION_BYTES_INVALID",
            ),
            None,
        )

    if (
        len(
            signed_transaction_bytes
        )
        != message_plan
        .estimated_signed_transaction_size_bytes
    ):
        return (
            BLOCK,
            (
                "SIGNED_TRANSACTION_SIZE_MISMATCH",
            ),
            None,
        )

    if (
        len(
            signed_transaction_bytes
        )
        > SOLANA_PACKET_DATA_SIZE
    ):
        return (
            BLOCK,
            (
                "SIGNED_TRANSACTION_PACKET_TOO_LARGE",
            ),
            None,
        )

    try:
        expected_signature = (
            Signature.from_string(
                transaction_signature
            )
        )

    except Exception:
        return (
            BLOCK,
            (
                "TRANSACTION_SIGNATURE_INVALID",
            ),
            None,
        )

    if (
        expected_signature
        == Signature.default()
    ):
        return (
            BLOCK,
            (
                "DEFAULT_SIGNATURE_REJECTED",
            ),
            None,
        )

    try:
        transaction = (
            VersionedTransaction.from_bytes(
                signed_transaction_bytes
            )
        )

    except Exception:
        return (
            BLOCK,
            (
                "SIGNED_TRANSACTION_PARSE_FAILED",
            ),
            None,
        )

    try:
        round_trip_bytes = bytes(
            transaction
        )

    except Exception:
        return (
            BLOCK,
            (
                "SIGNED_TRANSACTION_SERIALIZATION_FAILED",
            ),
            None,
        )

    if (
        round_trip_bytes
        != signed_transaction_bytes
    ):
        return (
            BLOCK,
            (
                "SIGNED_TRANSACTION_ROUND_TRIP_MISMATCH",
            ),
            None,
        )

    signatures = tuple(
        transaction.signatures
    )

    if signatures != (
        expected_signature,
    ):
        return (
            BLOCK,
            (
                "SIGNED_TRANSACTION_SIGNATURE_MISMATCH",
            ),
            None,
        )

    #
    # Critical exact-artifact assertion.
    #
    try:
        signed_message_bytes = (
            to_bytes_versioned(
                transaction.message
            )
        )

    except Exception:
        return (
            BLOCK,
            (
                "SIGNED_MESSAGE_SERIALIZATION_FAILED",
            ),
            None,
        )

    if (
        signed_message_bytes
        != plan_message_bytes
    ):
        return (
            BLOCK,
            (
                "SIGNED_MESSAGE_MUTATED",
            ),
            None,
        )

    if (
        str(
            transaction
            .message
            .recent_blockhash
        )
        != message_plan.recent_blockhash
    ):
        return (
            BLOCK,
            (
                "SIGNED_TRANSACTION_BLOCKHASH_MISMATCH",
            ),
            None,
        )

    try:
        signer_pubkey = (
            Pubkey.from_string(
                authorization.wallet_pubkey
            )
        )

        signature_valid = (
            expected_signature.verify(
                signer_pubkey,
                plan_message_bytes,
            )
        )

    except Exception:
        return (
            UNKNOWN,
            (
                "SIGNATURE_VERIFICATION_FAILED",
            ),
            None,
        )

    if not signature_valid:
        return (
            BLOCK,
            (
                "SIGNATURE_VERIFICATION_FAILED",
            ),
            None,
        )

    try:
        verification_results = (
            transaction
            .verify_with_results()
        )

    except Exception:
        return (
            UNKNOWN,
            (
                "SIGNED_TRANSACTION_VERIFICATION_FAILED",
            ),
            None,
        )

    if (
        verification_results
        != [True]
    ):
        return (
            BLOCK,
            (
                "SIGNED_TRANSACTION_VERIFICATION_FAILED",
            ),
            None,
        )

    signed_transaction_sha256 = (
        hashlib.sha256(
            signed_transaction_bytes
        ).hexdigest()
    )

    return (
        PASS,
        (),
        signed_transaction_sha256,
    )


def load_live_sell_execution_record_read_only(
    *,
    authorization_sha256: str,
    db_path: Path = DB_PATH,
) -> LiveSellExecutionRecordResult:
    def finish(
        status: str,
        *reasons: str,
        record: (
            LiveSellExecutionRecord
            | None
        ) = None,
    ) -> LiveSellExecutionRecordResult:
        return LiveSellExecutionRecordResult(
            resolver_version=(
                LIVE_SELL_EXECUTION_RECORD_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
                if isinstance(
                    authorization_sha256,
                    str,
                )
                else ""
            ),
            record=record,
            changed=False,
        )

    if not _valid_sha256(
        authorization_sha256
    ):
        return finish(
            UNKNOWN,
            "AUTHORIZATION_SHA256_INVALID",
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
            "SELL_EXECUTION_RECORD_READ_FAILED",
        )

    connection.row_factory = (
        sqlite3.Row
    )

    try:
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

                FROM live_sell_execution_records

                WHERE authorization_sha256 = ?
                """,
                (
                    authorization_sha256,
                ),
            ).fetchone()

        except sqlite3.OperationalError as error:
            if (
                "no such table: "
                "live_sell_execution_records"
                in str(error)
            ):
                return finish(
                    UNKNOWN,
                    "LIVE_SELL_EXECUTION_TABLE_NOT_FOUND",
                )

            return finish(
                UNKNOWN,
                "SELL_EXECUTION_RECORD_READ_FAILED",
            )

        except sqlite3.Error:
            return finish(
                UNKNOWN,
                "SELL_EXECUTION_RECORD_READ_FAILED",
            )

        if row is None:
            return finish(
                BLOCK,
                "SELL_EXECUTION_RECORD_NOT_FOUND",
            )

        try:
            record = _row_to_record(
                row
            )

        except Exception:
            return finish(
                UNKNOWN,
                "SELL_EXECUTION_RECORD_ROW_INVALID",
            )

        if not _record_contract_valid(
            record
        ):
            return finish(
                UNKNOWN,
                "SELL_EXECUTION_RECORD_EVIDENCE_INVALID",
                record=record,
            )

        return finish(
            PASS,
            record=record,
        )

    finally:
        connection.close()


def bind_live_sell_signed_artifact(
    *,
    authorization: LivePumpSellAuthorization,
    message_plan: PumpSellV2UnsignedMessagePlan,
    transaction_signature: str,
    signed_transaction_bytes: bytes,
    db_path: Path = DB_PATH,
) -> LiveSellExecutionRecordResult:
    authorization_sha256 = (
        authorization.authorization_sha256
        if isinstance(
            authorization,
            LivePumpSellAuthorization,
        )
        else ""
    )

    def finish(
        status: str,
        *reasons: str,
        record: (
            LiveSellExecutionRecord
            | None
        ) = None,
        changed: bool = False,
    ) -> LiveSellExecutionRecordResult:
        return LiveSellExecutionRecordResult(
            resolver_version=(
                LIVE_SELL_EXECUTION_RECORD_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            record=record,
            changed=changed,
        )

    (
        candidate_status,
        candidate_reasons,
        signed_transaction_sha256,
    ) = _validate_candidate_signed_artifact(
        authorization=authorization,
        message_plan=message_plan,
        transaction_signature=(
            transaction_signature
        ),
        signed_transaction_bytes=(
            signed_transaction_bytes
        ),
    )

    if candidate_status != PASS:
        return finish(
            candidate_status,
            *candidate_reasons,
        )

    assert (
        signed_transaction_sha256
        is not None
    )

    message_sha256 = (
        message_plan.message_sha256
    )

    blockhash_context_version = (
        message_plan.blockhash_context_version
    )

    recent_blockhash = (
        message_plan.recent_blockhash
    )

    last_valid_block_height = (
        message_plan.last_valid_block_height
    )

    blockhash_rpc_slot = (
        message_plan.blockhash_rpc_slot
    )

    message_sha256 = (
        message_plan.message_sha256
    )

    blockhash_context_version = (
        message_plan.blockhash_context_version
    )

    recent_blockhash = (
        message_plan.recent_blockhash
    )

    last_valid_block_height = (
        message_plan.last_valid_block_height
    )

    blockhash_rpc_slot = (
        message_plan.blockhash_rpc_slot
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
        connection = get_connection(
            db_path
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_DATABASE_OPEN_FAILED",
        )

    committed_record: (
        LiveSellExecutionRecord
        | None
    ) = None

    changed = False

    try:
        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

        except sqlite3.Error:
            return finish(
                UNKNOWN,
                "SELL_EXECUTION_TRANSACTION_BEGIN_FAILED",
            )

        #
        # BEGIN IMMEDIATE is intentionally acquired
        # before the independent read-only claim
        # validation. While this transaction owns the
        # writer reservation, no competing writer can
        # release/consume/replace the claim between
        # validation and artifact insertion.
        #
        claim_result = (
            load_active_live_sell_inventory_claim_read_only(
                authorization=(
                    authorization
                ),
                db_path=db_path,
            )
        )

        if (
            claim_result.status
            != CLAIM_PASS
        ):
            try:
                connection.rollback()
            except Exception:
                pass

            if (
                claim_result.status
                == CLAIM_BLOCK
            ):
                mapped_status = BLOCK

            elif (
                claim_result.status
                == CLAIM_UNKNOWN
            ):
                mapped_status = UNKNOWN

            else:
                mapped_status = UNKNOWN

            return finish(
                mapped_status,
                "ACTIVE_SELL_CLAIM_VALIDATION_FAILED",
                *claim_result.reasons,
            )

        claim = claim_result.claim

        if claim is None:
            try:
                connection.rollback()
            except Exception:
                pass

            return finish(
                UNKNOWN,
                "ACTIVE_SELL_CLAIM_MISSING",
            )

        if (
            claim.status
            != CLAIM_ACTIVE
            or claim.authorization_sha256
            != authorization_sha256
            or claim.wallet_pubkey
            != authorization.wallet_pubkey
            or claim.mint
            != authorization.mint
            or claim.tokens_to_sell
            != authorization.tokens_to_sell
            or claim.allocation
            != authorization.allocation
        ):
            try:
                connection.rollback()
            except Exception:
                pass

            return finish(
                UNKNOWN,
                "ACTIVE_SELL_CLAIM_BINDING_MISMATCH",
            )

        try:
            init_schema(
                connection
            )

        except sqlite3.Error:
            try:
                connection.rollback()
            except Exception:
                pass

            return finish(
                UNKNOWN,
                "SELL_EXECUTION_SCHEMA_INIT_FAILED",
            )

        try:
            existing_row = (
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

        except sqlite3.Error:
            try:
                connection.rollback()
            except Exception:
                pass

            return finish(
                UNKNOWN,
                "SELL_EXECUTION_RECORD_READ_FAILED",
            )

        if (
            existing_row is not None
        ):
            try:
                existing = (
                    _row_to_record(
                        existing_row
                    )
                )

            except Exception:
                try:
                    connection.rollback()
                except Exception:
                    pass

                return finish(
                    UNKNOWN,
                    "SELL_EXECUTION_RECORD_ROW_INVALID",
                )

            if not _record_matches_candidate(
                record=existing,
                authorization=authorization,
                message_sha256=(
                    message_sha256
                ),
                transaction_signature=(
                    transaction_signature
                ),
                signed_transaction_sha256=(
                    signed_transaction_sha256
                ),
                signed_transaction_bytes=(
                    signed_transaction_bytes
                ),
                blockhash_context_version=(
                    blockhash_context_version
                ),
                recent_blockhash=(
                    recent_blockhash
                ),
                last_valid_block_height=(
                    last_valid_block_height
                ),
                blockhash_rpc_slot=(
                    blockhash_rpc_slot
                ),
            ):
                try:
                    connection.rollback()
                except Exception:
                    pass

                return finish(
                    UNKNOWN,
                    "SELL_EXECUTION_RECORD_EVIDENCE_MISMATCH",
                    record=existing,
                )

            if (
                existing.status
                != SIGNED
            ):
                try:
                    connection.rollback()
                except Exception:
                    pass

                return finish(
                    BLOCK,
                    "SELL_EXECUTION_ALREADY_ADVANCED",
                    record=existing,
                )

            try:
                connection.rollback()

            except Exception:
                return finish(
                    UNKNOWN,
                    "SELL_EXECUTION_IDEMPOTENT_ROLLBACK_FAILED",
                )

            return finish(
                PASS,
                record=existing,
                changed=False,
            )

        now = time.time()

        try:
            connection.execute(
                """
                INSERT INTO
                    live_sell_execution_records (
                        authorization_sha256,
                        record_version,
                        authorization_version,
                        wallet_pubkey,
                        mint,
                        tokens_to_sell,
                        status,
                        message_sha256,
                        transaction_signature,
                        signed_transaction_sha256,
                        signed_transaction_bytes,
                        blockhash_context_version,
                        recent_blockhash,
                        last_valid_block_height,
                        blockhash_rpc_slot,
                        signed_at,
                        updated_at
                    )

                VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?
                )
                """,
                (
                    authorization_sha256,
                    LIVE_SELL_EXECUTION_RECORD_VERSION,
                    authorization.authorization_version,
                    authorization.wallet_pubkey,
                    authorization.mint,
                    authorization.tokens_to_sell,
                    SIGNED,
                    message_sha256,
                    transaction_signature,
                    signed_transaction_sha256,
                    signed_transaction_bytes,
                    blockhash_context_version,
                    recent_blockhash,
                    last_valid_block_height,
                    blockhash_rpc_slot,
                    now,
                    now,
                ),
            )

            persisted_row = (
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

        except sqlite3.IntegrityError:
            try:
                connection.rollback()
            except Exception:
                pass

            return finish(
                UNKNOWN,
                "SELL_EXECUTION_RECORD_INTEGRITY_ERROR",
            )

        except sqlite3.Error:
            try:
                connection.rollback()
            except Exception:
                pass

            return finish(
                UNKNOWN,
                "SELL_EXECUTION_RECORD_WRITE_FAILED",
            )

        if persisted_row is None:
            try:
                connection.rollback()
            except Exception:
                pass

            return finish(
                UNKNOWN,
                "SIGNED_SELL_EXECUTION_RECORD_MISSING",
            )

        try:
            candidate_record = (
                _row_to_record(
                    persisted_row
                )
            )

        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass

            return finish(
                UNKNOWN,
                "SIGNED_SELL_EXECUTION_RECORD_INVALID",
            )

        if not _record_matches_candidate(
            record=candidate_record,
            authorization=authorization,
            message_sha256=(
                message_sha256
            ),
            transaction_signature=(
                transaction_signature
            ),
            signed_transaction_sha256=(
                signed_transaction_sha256
            ),
            signed_transaction_bytes=(
                signed_transaction_bytes
            ),
            blockhash_context_version=(
                blockhash_context_version
            ),
            recent_blockhash=(
                recent_blockhash
            ),
            last_valid_block_height=(
                last_valid_block_height
            ),
            blockhash_rpc_slot=(
                blockhash_rpc_slot
            ),
        ):
            try:
                connection.rollback()
            except Exception:
                pass

            return finish(
                UNKNOWN,
                "SIGNED_SELL_EXECUTION_RECORD_MISMATCH",
            )

        try:
            connection.commit()

        except sqlite3.Error:
            try:
                connection.rollback()
            except Exception:
                pass

            return finish(
                UNKNOWN,
                "SELL_EXECUTION_RECORD_COMMIT_FAILED",
            )

        committed_record = (
            candidate_record
        )
        changed = True

    finally:
        connection.close()

    #
    # Independent readback after COMMIT. Signed bytes
    # are not exposed downstream until the durable row
    # can be reconstructed and cryptographically
    # validated from a fresh read-only connection.
    #
    readback = (
        load_live_sell_execution_record_read_only(
            authorization_sha256=(
                authorization_sha256
            ),
            db_path=db_path,
        )
    )

    if (
        readback.status != PASS
        or readback.record is None
    ):
        return finish(
            UNKNOWN,
            "SIGNED_SELL_EXECUTION_READBACK_FAILED",
            *readback.reasons,
        )

    persisted = readback.record

    if (
        committed_record is None
        or persisted
        != committed_record
        or not _record_matches_candidate(
            record=persisted,
            authorization=authorization,
            message_sha256=(
                message_sha256
            ),
            transaction_signature=(
                transaction_signature
            ),
            signed_transaction_sha256=(
                signed_transaction_sha256
            ),
            signed_transaction_bytes=(
                signed_transaction_bytes
            ),
            blockhash_context_version=(
                blockhash_context_version
            ),
            recent_blockhash=(
                recent_blockhash
            ),
            last_valid_block_height=(
                last_valid_block_height
            ),
            blockhash_rpc_slot=(
                blockhash_rpc_slot
            ),
        )
    ):
        return finish(
            UNKNOWN,
            "PERSISTED_SIGNED_SELL_ARTIFACT_MISMATCH",
            record=persisted,
        )

    return finish(
        PASS,
        record=persisted,
        changed=changed,
    )
