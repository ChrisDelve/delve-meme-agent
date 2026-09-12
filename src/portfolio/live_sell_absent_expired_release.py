from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from src.execution.live_pump_sell_authorization import (
    LivePumpSellAuthorization,
)
from src.portfolio.live_reservations import (
    DB_PATH,
    get_connection,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    BLOCK,
    CONSUMED,
    PASS,
    RELEASED,
    UNKNOWN,
    LiveSellInventoryClaim,
    _authorization_contract_valid,
    _claim_identity_matches_authorization,
    _load_claim,
    _strict_nonnegative_sqlite_int,
    _strict_timestamp,
    _valid_sha256,
    init_schema as init_claim_schema,
)
from src.portfolio.live_sell_execution_records import (
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED,
    LiveSellExecutionRecord,
    _record_contract_valid,
    _row_to_record,
    init_schema as init_execution_schema,
)


LIVE_SELL_ABSENT_EXPIRED_RELEASE_VERSION = (
    "live-sell-absent-expired-release-v1"
)

RECONCILED_ABSENT_EXPIRED_SELL_REASON = (
    "RECONCILED_ABSENT_EXPIRED_SELL"
)


@dataclass(frozen=True)
class LiveSellAbsentExpiredReleaseResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    claim: LiveSellInventoryClaim | None
    execution: LiveSellExecutionRecord | None

    changed: bool


def _execution_matches_authorization(
    *,
    execution: LiveSellExecutionRecord,
    authorization: LivePumpSellAuthorization,
) -> bool:
    return (
        _record_contract_valid(
            execution
        )
        and execution.authorization_version
        == authorization.authorization_version
        and execution.authorization_sha256
        == authorization.authorization_sha256
        and execution.wallet_pubkey
        == authorization.wallet_pubkey
        and execution.mint
        == authorization.mint
        and execution.tokens_to_sell
        == authorization.tokens_to_sell
        and execution.status
        in (
            SIGNED,
            SUBMISSION_ARMED,
            SUBMITTED,
        )
    )


def _execution_matches_expiry_evidence(
    *,
    execution: LiveSellExecutionRecord,
    transaction_signature: str,
    signed_transaction_sha256: str,
    last_valid_block_height: int,
    blockhash_rpc_slot: int,
) -> bool:
    return (
        execution.transaction_signature
        == transaction_signature
        and execution.signed_transaction_sha256
        == signed_transaction_sha256
        and execution.last_valid_block_height
        == last_valid_block_height
        and execution.blockhash_rpc_slot
        == blockhash_rpc_slot
    )


def _load_execution_locked(
    *,
    connection: sqlite3.Connection,
    authorization_sha256: str,
) -> LiveSellExecutionRecord | None:
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

    if row is None:
        return None

    try:
        return _row_to_record(
            row
        )

    except Exception:
        return None


def release_reconciled_absent_expired_sell_claim(
    *,
    authorization: LivePumpSellAuthorization,
    transaction_signature: str,
    signed_transaction_sha256: str,
    last_valid_block_height: int,
    blockhash_rpc_slot: int,
    db_path: Path = DB_PATH,
) -> LiveSellAbsentExpiredReleaseResult:
    """
    Atomically release an ACTIVE SELL inventory claim
    after an external reconciliation authority has
    proven the exact signed transaction ABSENT_EXPIRED.

    This function performs no chain observation,
    signing, sending, retrying, position mutation,
    PnL mutation, or transaction-journal mutation.

    Exact persisted execution identity is required so
    stale or mismatched expiry evidence cannot release
    unrelated inventory authority.
    """
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
        claim: LiveSellInventoryClaim | None = None,
        execution: LiveSellExecutionRecord | None = None,
        changed: bool = False,
    ) -> LiveSellAbsentExpiredReleaseResult:
        return LiveSellAbsentExpiredReleaseResult(
            resolver_version=(
                LIVE_SELL_ABSENT_EXPIRED_RELEASE_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            authorization_sha256=(
                authorization_sha256
            ),
            claim=claim,
            execution=execution,
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

    if not isinstance(
        transaction_signature,
        str,
    ):
        transaction_signature = ""

    transaction_signature = (
        transaction_signature.strip()
    )

    if not transaction_signature:
        return finish(
            UNKNOWN,
            "INVALID_TRANSACTION_SIGNATURE",
        )

    if not isinstance(
        signed_transaction_sha256,
        str,
    ):
        signed_transaction_sha256 = ""

    signed_transaction_sha256 = (
        signed_transaction_sha256.strip()
    )

    if not _valid_sha256(
        signed_transaction_sha256
    ):
        return finish(
            UNKNOWN,
            "INVALID_SIGNED_TRANSACTION_SHA256",
        )

    if not _strict_nonnegative_sqlite_int(
        last_valid_block_height
    ):
        return finish(
            UNKNOWN,
            "INVALID_LAST_VALID_BLOCK_HEIGHT",
        )

    if not _strict_nonnegative_sqlite_int(
        blockhash_rpc_slot
    ):
        return finish(
            UNKNOWN,
            "INVALID_BLOCKHASH_RPC_SLOT",
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

        if not _claim_identity_matches_authorization(
            claim=claim,
            authorization=authorization,
        ):
            connection.commit()

            return finish(
                BLOCK,
                "SELL_CLAIM_AUTHORIZATION_MISMATCH",
                claim=claim,
            )

        execution = _load_execution_locked(
            connection=connection,
            authorization_sha256=(
                authorization_sha256
            ),
        )

        if execution is None:
            connection.commit()

            return finish(
                BLOCK,
                "SELL_EXECUTION_NOT_FOUND_OR_INVALID",
                claim=claim,
            )

        if not _execution_matches_authorization(
            execution=execution,
            authorization=authorization,
        ):
            connection.commit()

            return finish(
                BLOCK,
                "SELL_EXECUTION_AUTHORIZATION_MISMATCH",
                claim=claim,
                execution=execution,
            )

        evidence_matches = (
            _execution_matches_expiry_evidence(
                execution=execution,
                transaction_signature=(
                    transaction_signature
                ),
                signed_transaction_sha256=(
                    signed_transaction_sha256
                ),
                last_valid_block_height=(
                    last_valid_block_height
                ),
                blockhash_rpc_slot=(
                    blockhash_rpc_slot
                ),
            )
        )

        #
        # Exact terminal retry.
        #
        if claim.status == RELEASED:
            if (
                claim.terminal_reason
                != RECONCILED_ABSENT_EXPIRED_SELL_REASON
            ):
                connection.commit()

                return finish(
                    BLOCK,
                    "SELL_EXPIRY_TERMINAL_REASON_MISMATCH",
                    claim=claim,
                    execution=execution,
                )

            if not evidence_matches:
                connection.commit()

                return finish(
                    BLOCK,
                    "SELL_EXPIRY_EVIDENCE_MISMATCH",
                    claim=claim,
                    execution=execution,
                )

            connection.commit()

            return finish(
                PASS,
                claim=claim,
                execution=execution,
                changed=False,
            )

        if claim.status == CONSUMED:
            connection.commit()

            return finish(
                BLOCK,
                "SELL_CLAIM_NOT_RECONCILABLE",
                claim=claim,
                execution=execution,
            )

        if claim.status != ACTIVE:
            connection.commit()

            return finish(
                BLOCK,
                "SELL_CLAIM_NOT_RECONCILABLE",
                claim=claim,
                execution=execution,
            )

        if not evidence_matches:
            connection.commit()

            return finish(
                BLOCK,
                "SELL_EXPIRY_EVIDENCE_MISMATCH",
                claim=claim,
                execution=execution,
            )

        terminal_at = time.time()

        if not _strict_timestamp(
            terminal_at
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                "SYSTEM_TIME_INVALID",
                claim=claim,
                execution=execution,
            )

        updated = connection.execute(
            """
            UPDATE live_sell_inventory_claims

            SET
                status = ?,
                terminal_at = ?,
                terminal_reason = ?

            WHERE authorization_sha256 = ?
              AND claim_version = ?
              AND authorization_version = ?
              AND wallet_pubkey = ?
              AND mint = ?
              AND tokens_to_sell = ?
              AND status = ?
              AND terminal_at IS NULL
              AND terminal_reason IS NULL
            """,
            (
                RELEASED,
                terminal_at,
                RECONCILED_ABSENT_EXPIRED_SELL_REASON,

                authorization_sha256,
                claim.claim_version,
                claim.authorization_version,
                claim.wallet_pubkey,
                claim.mint,
                claim.tokens_to_sell,
                ACTIVE,
            ),
        )

        if updated.rowcount != 1:
            connection.rollback()

            return finish(
                UNKNOWN,
                "SELL_EXPIRED_RELEASE_TRANSITION_FAILED",
            )

        persisted_claim = _load_claim(
            connection=connection,
            authorization_sha256=(
                authorization_sha256
            ),
        )

        persisted_execution = (
            _load_execution_locked(
                connection=connection,
                authorization_sha256=(
                    authorization_sha256
                ),
            )
        )

        if (
            persisted_claim is None
            or persisted_execution is None
            or persisted_claim.status
            != RELEASED
            or persisted_claim.terminal_at
            is None
            or not _strict_timestamp(
                persisted_claim.terminal_at
            )
            or persisted_claim.terminal_reason
            != RECONCILED_ABSENT_EXPIRED_SELL_REASON
            or not _claim_identity_matches_authorization(
                claim=persisted_claim,
                authorization=authorization,
            )
            or not _execution_matches_authorization(
                execution=persisted_execution,
                authorization=authorization,
            )
            or not _execution_matches_expiry_evidence(
                execution=persisted_execution,
                transaction_signature=(
                    transaction_signature
                ),
                signed_transaction_sha256=(
                    signed_transaction_sha256
                ),
                last_valid_block_height=(
                    last_valid_block_height
                ),
                blockhash_rpc_slot=(
                    blockhash_rpc_slot
                ),
            )
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                "SELL_EXPIRED_RELEASE_VERIFICATION_FAILED",
            )

        connection.commit()

        return finish(
            PASS,
            claim=persisted_claim,
            execution=persisted_execution,
            changed=True,
        )

    except sqlite3.Error:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass

        return finish(
            UNKNOWN,
            "SELL_EXPIRED_RELEASE_DATABASE_ERROR",
        )

    finally:
        connection.close()
