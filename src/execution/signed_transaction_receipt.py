import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solders.message import MessageV0
from solders.transaction import VersionedTransaction

from src.execution.signed_transaction_status import (
    KNOWN,
    UNKNOWN as STATUS_UNKNOWN,
    resolve_signed_transaction_status,
)
from src.portfolio.live_reservations import (
    DB_PATH,
    RESERVATION_VERSION,
    SIGNED,
    SUBMITTED,
    LiveCapitalReservation,
    load_capital_reservation_read_only,
)
from src.safety.token_safety_resolver import (
    HeliusRpcClient,
)


SIGNED_TRANSACTION_RECEIPT_VERSION = (
    "signed-transaction-receipt-v1"
)

RESOLVED = "RESOLVED"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SignedTransactionReceiptResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    reservation_id: str
    transaction_signature: str | None

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


def _strict_nonnegative_int(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    )


def _artifact_identity(
    reservation: LiveCapitalReservation,
) -> tuple:
    """
    Immutable signed-transaction identity carried
    by Reservation v6.
    """

    return (
        reservation.reservation_version,
        reservation.wallet_pubkey,
        reservation.signed_at,
        reservation.transaction_signature,
        reservation.signed_message_sha256,
        reservation.signed_transaction_sha256,
        reservation.signed_transaction_bytes,
        reservation.recent_blockhash,
        reservation.last_valid_block_height,
        reservation.blockhash_rpc_slot,
    )


def _resolve_fee_payer_pubkey(
    transaction_bytes: bytes,
) -> str:
    """
    Resolve the fee payer from the exact persisted
    signed transaction.

    Solana transaction account index 0 is the
    transaction fee payer.
    """

    try:
        transaction = (
            VersionedTransaction.from_bytes(
                transaction_bytes
            )
        )
    except Exception as error:
        raise ValueError(
            "SIGNED_TRANSACTION_FEE_PAYER_INVALID"
        ) from error

    message = transaction.message

    if not isinstance(
        message,
        MessageV0,
    ):
        raise ValueError(
            "SIGNED_TRANSACTION_FEE_PAYER_INVALID"
        )

    account_keys = (
        message.account_keys
    )

    if not account_keys:
        raise ValueError(
            "SIGNED_TRANSACTION_FEE_PAYER_INVALID"
        )

    fee_payer_pubkey = str(
        account_keys[0]
    )

    if not fee_payer_pubkey:
        raise ValueError(
            "SIGNED_TRANSACTION_FEE_PAYER_INVALID"
        )

    return fee_payer_pubkey


def _parse_landed_receipt(
    response: Any,
    *,
    expected_transaction_bytes: bytes,
    expected_transaction_sha256: str,
    expected_transaction_slot: int,
    expected_transaction_error: Any,
) -> tuple[
    int,
    int | None,
    Any,
    int,
    int,
    int,
    int,
    str,
]:
    if not isinstance(
        response,
        dict,
    ):
        raise ValueError(
            "RECEIPT_RESPONSE_INVALID"
        )

    slot = response.get(
        "slot"
    )

    if not _strict_nonnegative_int(slot):
        raise ValueError(
            "RECEIPT_SLOT_INVALID"
        )

    if slot != expected_transaction_slot:
        raise ValueError(
            "RECEIPT_SLOT_MISMATCH"
        )

    block_time = response.get(
        "blockTime"
    )

    if (
        block_time is not None
        and not _strict_nonnegative_int(
            block_time
        )
    ):
        raise ValueError(
            "RECEIPT_BLOCK_TIME_INVALID"
        )

    transaction = response.get(
        "transaction"
    )

    if (
        not isinstance(
            transaction,
            (list, tuple),
        )
        or len(transaction) != 2
        or not isinstance(
            transaction[0],
            str,
        )
        or not transaction[0]
        or transaction[1] != "base64"
    ):
        raise ValueError(
            "RECEIPT_TRANSACTION_ENCODING_INVALID"
        )

    try:
        receipt_transaction_bytes = (
            base64.b64decode(
                transaction[0],
                validate=True,
            )
        )
    except Exception as error:
        raise ValueError(
            "RECEIPT_TRANSACTION_BASE64_INVALID"
        ) from error

    if not receipt_transaction_bytes:
        raise ValueError(
            "RECEIPT_TRANSACTION_BYTES_INVALID"
        )

    receipt_transaction_sha256 = (
        hashlib.sha256(
            receipt_transaction_bytes
        ).hexdigest()
    )

    if (
        receipt_transaction_sha256
        != expected_transaction_sha256
    ):
        raise ValueError(
            "RECEIPT_TRANSACTION_HASH_MISMATCH"
        )

    if (
        receipt_transaction_bytes
        != expected_transaction_bytes
    ):
        raise ValueError(
            "RECEIPT_TRANSACTION_BYTES_MISMATCH"
        )

    meta = response.get(
        "meta"
    )

    if not isinstance(
        meta,
        dict,
    ):
        raise ValueError(
            "RECEIPT_META_INVALID"
        )

    if "err" not in meta:
        raise ValueError(
            "RECEIPT_ERROR_FIELD_MISSING"
        )

    transaction_error = meta[
        "err"
    ]

    if (
        transaction_error
        != expected_transaction_error
    ):
        raise ValueError(
            "RECEIPT_ERROR_MISMATCH"
        )

    fee_lamports = meta.get(
        "fee"
    )

    if not _strict_nonnegative_int(
        fee_lamports
    ):
        raise ValueError(
            "RECEIPT_FEE_INVALID"
        )

    pre_balances = meta.get(
        "preBalances"
    )

    post_balances = meta.get(
        "postBalances"
    )

    if (
        not isinstance(
            pre_balances,
            list,
        )
        or not isinstance(
            post_balances,
            list,
        )
        or not pre_balances
        or len(pre_balances)
        != len(post_balances)
    ):
        raise ValueError(
            "RECEIPT_BALANCES_INVALID"
        )

    if (
        any(
            not _strict_nonnegative_int(
                value
            )
            for value in pre_balances
        )
        or any(
            not _strict_nonnegative_int(
                value
            )
            for value in post_balances
        )
    ):
        raise ValueError(
            "RECEIPT_BALANCES_INVALID"
        )

    fee_payer_pre_balance = (
        pre_balances[0]
    )

    fee_payer_post_balance = (
        post_balances[0]
    )

    fee_payer_balance_delta = (
        fee_payer_post_balance
        - fee_payer_pre_balance
    )

    return (
        slot,
        block_time,
        transaction_error,
        fee_lamports,
        fee_payer_pre_balance,
        fee_payer_post_balance,
        fee_payer_balance_delta,
        receipt_transaction_sha256,
    )


async def resolve_signed_transaction_receipt(
    *,
    reservation_id: str,
    db_path: Path = DB_PATH,
) -> SignedTransactionReceiptResult:
    """
    Resolve exact landed Solana receipt evidence
    for one persisted signed transaction.

    This resolver is observational only.

    It has:
    - no ledger mutation authority
    - no position authority
    - no signing authority
    - no send authority
    - no resend authority
    - no submission retry authority

    A receipt is RESOLVED only when:

    1. Reservation v6 contains a coherent held
       signed transaction.
    2. Status Resolver v3 independently reports
       that exact transaction as KNOWN.
    3. The reservation artifact remains unchanged.
    4. getTransaction returns the same slot/error
       observed by Status Resolver.
    5. The base64 transaction bytes returned by
       getTransaction exactly equal the bytes
       durably persisted before submission.
    6. The reservation artifact remains unchanged
       after the receipt observation.
    """

    transaction_signature: (
        str | None
    ) = None

    status_observation_state: (
        str | None
    ) = None

    status_transaction_slot: (
        int | None
    ) = None

    receipt_slot: int | None = None
    block_time: int | None = None

    transaction_error: Any = None

    fee_lamports: int | None = None

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

    if not isinstance(
        reservation_id,
        str,
    ):
        reservation_id = ""

    reservation_id = (
        reservation_id.strip()
    )

    def finish(
        status: str,
        *reasons: str,
    ) -> SignedTransactionReceiptResult:
        return SignedTransactionReceiptResult(
            resolver_version=(
                SIGNED_TRANSACTION_RECEIPT_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            reservation_id=reservation_id,
            transaction_signature=(
                transaction_signature
            ),
            status_observation_state=(
                status_observation_state
            ),
            status_transaction_slot=(
                status_transaction_slot
            ),
            receipt_slot=receipt_slot,
            block_time=block_time,
            transaction_error=(
                transaction_error
            ),
            fee_lamports=fee_lamports,
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
        )

    if not reservation_id:
        return finish(
            UNKNOWN,
            "INVALID_RESERVATION_ID",
        )

    #
    # Snapshot exact local signed authority.
    #
    try:
        initial = (
            load_capital_reservation_read_only(
                reservation_id=reservation_id,
                db_path=db_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            "RESERVATION_READ_FAILED",
        )

    if initial is None:
        return finish(
            UNKNOWN,
            "RESERVATION_NOT_FOUND",
        )

    transaction_signature = (
        initial.transaction_signature
    )

    persisted_transaction_sha256 = (
        initial.signed_transaction_sha256
    )

    if (
        initial.reservation_version
        != RESERVATION_VERSION
    ):
        return finish(
            BLOCK,
            "RESERVATION_VERSION_MISMATCH",
        )

    if initial.status not in (
        SIGNED,
        SUBMITTED,
    ):
        return finish(
            BLOCK,
            "RESERVATION_NOT_SIGNED_OR_SUBMITTED",
        )

    initial_identity = (
        _artifact_identity(
            initial
        )
    )

    #
    # Require independent proof that the exact
    # transaction is known on chain.
    #
    try:
        observation = (
            await resolve_signed_transaction_status(
                reservation_id=reservation_id,
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
        observation.state
        == STATUS_UNKNOWN
    ):
        return finish(
            UNKNOWN,
            "STATUS_OBSERVATION_UNKNOWN",
            *observation.reasons,
        )

    if observation.state != KNOWN:
        return finish(
            BLOCK,
            "TRANSACTION_NOT_KNOWN",
        )

    if (
        observation.reservation_id
        != reservation_id
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
    # Re-read before receipt RPC so stale status
    # evidence cannot authorize observation against
    # a changed local transaction artifact.
    #
    try:
        current = (
            load_capital_reservation_read_only(
                reservation_id=reservation_id,
                db_path=db_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            "RESERVATION_REREAD_FAILED",
        )

    if current is None:
        return finish(
            UNKNOWN,
            "RESERVATION_DISAPPEARED",
        )

    if (
        current.reservation_version
        != RESERVATION_VERSION
        or current.status
        not in (
            SIGNED,
            SUBMITTED,
        )
        or _artifact_identity(current)
        != initial_identity
    ):
        return finish(
            UNKNOWN,
            "RESERVATION_CHANGED_DURING_STATUS_CHECK",
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
        or not isinstance(
            signed_transaction_sha256,
            str,
        )
        or len(
            signed_transaction_sha256
        )
        != 64
    ):
        return finish(
            UNKNOWN,
            "SIGNED_TRANSACTION_ARTIFACT_INVALID",
        )

    #
    # Bind balance index zero to the exact wallet
    # that owns this reservation before using it as
    # fee-payer financial evidence.
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

    wallet_pubkey = (
        current.wallet_pubkey
    )

    if (
        not isinstance(
            wallet_pubkey,
            str,
        )
        or not wallet_pubkey.strip()
        or wallet_pubkey.strip()
        != fee_payer_pubkey
    ):
        return finish(
            UNKNOWN,
            "FEE_PAYER_WALLET_MISMATCH",
        )

    #
    # Exactly one receipt observation.
    #
    try:
        async with HeliusRpcClient() as rpc:
            response = await rpc.call(
                "getTransaction",
                [
                    current.transaction_signature,
                    {
                        "encoding": "base64",
                        "commitment": "confirmed",
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
    # Final local identity check. Receipt resolution
    # never mutates the reservation itself.
    #
    try:
        final = (
            load_capital_reservation_read_only(
                reservation_id=reservation_id,
                db_path=db_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            "RESERVATION_FINAL_READ_FAILED",
        )

    if final is None:
        return finish(
            UNKNOWN,
            "RESERVATION_DISAPPEARED_AFTER_RECEIPT",
        )

    if (
        final.reservation_version
        != RESERVATION_VERSION
        or final.status
        not in (
            SIGNED,
            SUBMITTED,
        )
        or _artifact_identity(final)
        != initial_identity
    ):
        return finish(
            UNKNOWN,
            "RESERVATION_CHANGED_DURING_RECEIPT_CHECK",
        )

    return finish(
        RESOLVED,
    )
