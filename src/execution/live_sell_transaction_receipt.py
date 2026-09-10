from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.live_sell_transaction_status import (
    KNOWN,
    UNKNOWN as STATUS_UNKNOWN,
    LIVE_SELL_TRANSACTION_STATUS_RESOLVER_VERSION,
    resolve_live_sell_transaction_status,
)
from src.execution.signed_transaction_receipt import (
    _parse_landed_receipt,
    _resolve_fee_payer_pubkey,
)
from src.portfolio.live_reservations import (
    DB_PATH,
)
from src.portfolio.live_sell_execution_records import (
    PASS as EXECUTION_PASS,
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED,
    LiveSellExecutionRecord,
    load_live_sell_execution_record_read_only,
)
from src.safety.token_safety_resolver import (
    COMMITMENT,
    HeliusRpcClient,
)


LIVE_SELL_TRANSACTION_RECEIPT_VERSION = (
    "live-sell-transaction-receipt-v1"
)

RESOLVED = "RESOLVED"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveSellTransactionReceiptResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    transaction_signature: str | None
    execution_status: str | None

    status_observation_state: str | None
    status_transaction_slot: int | None

    receipt_slot: int | None
    block_time: int | None

    transaction_error: Any

    fee_lamports: int | None

    fee_payer_pubkey: str | None

    fee_payer_pre_balance_lamports: int | None
    fee_payer_post_balance_lamports: int | None
    fee_payer_balance_delta_lamports: int | None

    persisted_transaction_sha256: str | None
    receipt_transaction_sha256: str | None

    last_valid_block_height: int | None
    blockhash_rpc_slot: int | None


def _strict_nonnegative_int(
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
        and value >= 0
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


def _artifact_identity(
    record: LiveSellExecutionRecord,
) -> tuple:
    """
    Immutable identity of the exact signed SELL
    artifact.

    Submission lifecycle state/timestamps are
    intentionally excluded because they may advance
    while receipt observation is in flight without
    changing the transaction itself.
    """

    return (
        record.record_version,

        record.authorization_version,
        record.authorization_sha256,

        record.wallet_pubkey,
        record.mint,
        record.tokens_to_sell,

        record.message_sha256,

        record.transaction_signature,
        record.signed_transaction_sha256,
        record.signed_transaction_bytes,

        record.blockhash_context_version,
        record.recent_blockhash,
        record.last_valid_block_height,
        record.blockhash_rpc_slot,

        record.signed_at,
    )


def _receipt_resolvable_status(
    status: str,
) -> bool:
    return status in (
        SIGNED,
        SUBMISSION_ARMED,
        SUBMITTED,
    )


async def resolve_live_sell_transaction_receipt(
    *,
    authorization_sha256: str,
    db_path: Path = DB_PATH,
) -> LiveSellTransactionReceiptResult:
    """
    Resolve one exact landed SELL transaction receipt.

    This resolver is read-only.

    It has no:
    - signing authority
    - transaction-build authority
    - send/retry authority
    - claim mutation authority
    - position mutation authority
    - PnL/accounting authority
    - journal mutation authority

    SIGNED is intentionally receipt-resolvable because
    an exact durable signed artifact could have been
    relayed outside the local submission executor.

    SUBMISSION_ARMED is intentionally receipt-resolvable
    because a process may crash after the RPC side
    effect but before local SUBMITTED acknowledgment.
    """

    if isinstance(
        authorization_sha256,
        str,
    ):
        authorization_sha256 = (
            authorization_sha256.strip()
        )
    else:
        authorization_sha256 = ""

    transaction_signature: (
        str | None
    ) = None

    execution_status: (
        str | None
    ) = None

    status_observation_state: (
        str | None
    ) = None

    status_transaction_slot: (
        int | None
    ) = None

    receipt_slot: (
        int | None
    ) = None

    block_time: (
        int | None
    ) = None

    transaction_error: Any = None

    fee_lamports: (
        int | None
    ) = None

    fee_payer_pubkey: (
        str | None
    ) = None

    fee_payer_pre_balance_lamports: (
        int | None
    ) = None

    fee_payer_post_balance_lamports: (
        int | None
    ) = None

    fee_payer_balance_delta_lamports: (
        int | None
    ) = None

    persisted_transaction_sha256: (
        str | None
    ) = None

    receipt_transaction_sha256: (
        str | None
    ) = None

    last_valid_block_height: (
        int | None
    ) = None

    blockhash_rpc_slot: (
        int | None
    ) = None

    def finish(
        status: str,
        *reasons: str,
    ) -> LiveSellTransactionReceiptResult:
        return LiveSellTransactionReceiptResult(
            resolver_version=(
                LIVE_SELL_TRANSACTION_RECEIPT_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            transaction_signature=(
                transaction_signature
            ),
            execution_status=(
                execution_status
            ),
            status_observation_state=(
                status_observation_state
            ),
            status_transaction_slot=(
                status_transaction_slot
            ),
            receipt_slot=(
                receipt_slot
            ),
            block_time=(
                block_time
            ),
            transaction_error=(
                transaction_error
            ),
            fee_lamports=(
                fee_lamports
            ),
            fee_payer_pubkey=(
                fee_payer_pubkey
            ),
            fee_payer_pre_balance_lamports=(
                fee_payer_pre_balance_lamports
            ),
            fee_payer_post_balance_lamports=(
                fee_payer_post_balance_lamports
            ),
            fee_payer_balance_delta_lamports=(
                fee_payer_balance_delta_lamports
            ),
            persisted_transaction_sha256=(
                persisted_transaction_sha256
            ),
            receipt_transaction_sha256=(
                receipt_transaction_sha256
            ),
            last_valid_block_height=(
                last_valid_block_height
            ),
            blockhash_rpc_slot=(
                blockhash_rpc_slot
            ),
        )

    if not _valid_sha256(
        authorization_sha256
    ):
        return finish(
            UNKNOWN,
            "INVALID_AUTHORIZATION_SHA256",
        )

    #
    # Initial durable artifact snapshot.
    #
    try:
        initial_result = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_RECORD_READ_FAILED",
        )

    if (
        initial_result.status
        != EXECUTION_PASS
        or initial_result.record
        is None
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_RECORD_UNAVAILABLE",
            *initial_result.reasons,
        )

    initial = (
        initial_result.record
    )

    transaction_signature = (
        initial.transaction_signature
    )

    execution_status = (
        initial.status
    )

    persisted_transaction_sha256 = (
        initial.signed_transaction_sha256
    )

    last_valid_block_height = (
        initial.last_valid_block_height
    )

    blockhash_rpc_slot = (
        initial.blockhash_rpc_slot
    )

    if not _receipt_resolvable_status(
        initial.status
    ):
        return finish(
            BLOCK,
            "SELL_EXECUTION_NOT_RECEIPT_RESOLVABLE",
        )

    initial_identity = (
        _artifact_identity(
            initial
        )
    )

    #
    # A receipt RPC is permitted only after the chain
    # status layer proves this exact signature KNOWN.
    #
    try:
        observation = (
            await resolve_live_sell_transaction_status(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "STATUS_RESOLUTION_FAILED",
        )

    status_observation_state = (
        observation.state
    )

    status_transaction_slot = (
        observation.transaction_slot
    )

    if (
        observation.resolver_version
        != LIVE_SELL_TRANSACTION_STATUS_RESOLVER_VERSION
    ):
        return finish(
            UNKNOWN,
            "STATUS_RESOLVER_VERSION_MISMATCH",
        )

    if observation.state == STATUS_UNKNOWN:
        return finish(
            UNKNOWN,
            "STATUS_OBSERVATION_UNKNOWN",
            *observation.reasons,
        )

    if observation.state != KNOWN:
        return finish(
            BLOCK,
            "TRANSACTION_NOT_KNOWN",
            *observation.reasons,
        )

    #
    # Bind the chain observation to this exact durable
    # artifact. Lifecycle state itself is allowed to
    # advance concurrently and is therefore not part
    # of this binding.
    #
    if (
        observation.authorization_sha256
        != authorization_sha256
        or observation.transaction_signature
        != initial.transaction_signature
        or observation.last_valid_block_height
        != initial.last_valid_block_height
        or observation.blockhash_rpc_slot
        != initial.blockhash_rpc_slot
        or not _strict_nonnegative_int(
            observation.transaction_slot
        )
    ):
        return finish(
            UNKNOWN,
            "STATUS_OBSERVATION_BINDING_MISMATCH",
        )

    #
    # Re-read before getTransaction. An arm or submit
    # state transition is allowed; artifact mutation
    # is not.
    #
    try:
        current_result = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_REREAD_FAILED",
        )

    if (
        current_result.status
        != EXECUTION_PASS
        or current_result.record
        is None
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_REREAD_UNAVAILABLE",
            *current_result.reasons,
        )

    current = (
        current_result.record
    )

    execution_status = (
        current.status
    )

    if (
        not _receipt_resolvable_status(
            current.status
        )
        or _artifact_identity(
            current
        )
        != initial_identity
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_CHANGED_DURING_STATUS_CHECK",
        )

    signed_transaction_bytes = (
        current.signed_transaction_bytes
    )

    signed_transaction_sha256 = (
        current.signed_transaction_sha256
    )

    if (
        not isinstance(
            signed_transaction_bytes,
            bytes,
        )
        or not signed_transaction_bytes
        or not _valid_sha256(
            signed_transaction_sha256
        )
    ):
        return finish(
            UNKNOWN,
            "SIGNED_TRANSACTION_ARTIFACT_INVALID",
        )

    #
    # Account index zero of the exact serialized
    # VersionedTransaction must be the persisted wallet.
    #
    try:
        fee_payer_pubkey = (
            _resolve_fee_payer_pubkey(
                signed_transaction_bytes
            )
        )

    except ValueError as error:
        return finish(
            UNKNOWN,
            str(error),
        )

    if (
        not isinstance(
            current.wallet_pubkey,
            str,
        )
        or not current.wallet_pubkey.strip()
        or current.wallet_pubkey.strip()
        != fee_payer_pubkey
    ):
        return finish(
            UNKNOWN,
            "FEE_PAYER_WALLET_MISMATCH",
        )

    #
    # Exactly one landed-receipt RPC observation.
    #
    try:
        async with HeliusRpcClient() as rpc:
            response = await rpc.call(
                "getTransaction",
                [
                    current.transaction_signature,
                    {
                        "encoding": "base64",
                        "commitment": COMMITMENT,
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            )

    except Exception:
        return finish(
            UNKNOWN,
            "RECEIPT_RPC_FAILED",
        )

    if response is None:
        return finish(
            UNKNOWN,
            "KNOWN_TRANSACTION_RECEIPT_NOT_FOUND",
        )

    #
    # Reuse the existing hardened generic landed
    # Solana receipt parser.
    #
    try:
        (
            receipt_slot,
            block_time,
            transaction_error,
            fee_lamports,
            fee_payer_pre_balance_lamports,
            fee_payer_post_balance_lamports,
            fee_payer_balance_delta_lamports,
            receipt_transaction_sha256,
        ) = _parse_landed_receipt(
            response,
            expected_transaction_bytes=(
                signed_transaction_bytes
            ),
            expected_transaction_sha256=(
                signed_transaction_sha256
            ),
            expected_transaction_slot=(
                observation.transaction_slot
            ),
            expected_transaction_error=(
                observation.transaction_error
            ),
        )

    except ValueError as error:
        return finish(
            UNKNOWN,
            str(error),
        )

    #
    # Final durable identity check after receipt RPC.
    #
    try:
        final_result = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_FINAL_READ_FAILED",
        )

    if (
        final_result.status
        != EXECUTION_PASS
        or final_result.record
        is None
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_FINAL_READ_UNAVAILABLE",
            *final_result.reasons,
        )

    final = (
        final_result.record
    )

    execution_status = (
        final.status
    )

    if (
        not _receipt_resolvable_status(
            final.status
        )
        or _artifact_identity(
            final
        )
        != initial_identity
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_CHANGED_DURING_RECEIPT_CHECK",
        )

    return finish(
        RESOLVED,
    )
