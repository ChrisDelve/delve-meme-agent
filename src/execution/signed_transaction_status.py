from dataclasses import dataclass
from pathlib import Path
from typing import Any
import hashlib

from solders.hash import Hash
from solders.signature import Signature

from src.portfolio.live_reservations import (
    DB_PATH,
    RESERVATION_VERSION,
    SIGNED,
    SQLITE_INT_MAX,
    LiveCapitalReservation,
    load_capital_reservation_read_only,
)
from src.safety.token_safety_resolver import (
    COMMITMENT,
    HeliusRpcClient,
)


SIGNED_TRANSACTION_STATUS_RESOLVER_VERSION = (
    "signed-transaction-status-resolver-v1"
)

KNOWN = "KNOWN"
ABSENT_STILL_VALID = "ABSENT_STILL_VALID"
ABSENT_EXPIRED = "ABSENT_EXPIRED"
UNKNOWN = "UNKNOWN"

RECENT = "RECENT"
HISTORY = "HISTORY"


@dataclass(frozen=True)
class SignedTransactionStatusResult:
    resolver_version: str
    state: str
    reasons: tuple[str, ...]

    reservation_id: str
    transaction_signature: str | None

    status_source: str | None
    status_context_slot: int | None
    transaction_slot: int | None

    confirmations: int | None
    confirmation_status: str | None
    transaction_error: Any

    current_block_height: int | None
    last_valid_block_height: int | None
    blockhash_rpc_slot: int | None

    history_searched: bool


def _strict_nonnegative_int(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= SQLITE_INT_MAX
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


def _validate_signed_reservation(
    reservation: LiveCapitalReservation,
) -> tuple[str, ...]:
    reasons: list[str] = []

    if (
        reservation.reservation_version
        != RESERVATION_VERSION
    ):
        reasons.append(
            "RESERVATION_VERSION_MISMATCH"
        )

    if reservation.status != SIGNED:
        reasons.append(
            "RESERVATION_NOT_SIGNED"
        )

    signature_text = (
        reservation.transaction_signature
    )

    try:
        parsed_signature = (
            Signature.from_string(
                signature_text
            )
            if isinstance(
                signature_text,
                str,
            )
            and signature_text.strip()
            else None
        )
    except Exception:
        parsed_signature = None

    if (
        parsed_signature is None
        or parsed_signature
        == Signature.default()
    ):
        reasons.append(
            "INVALID_TRANSACTION_SIGNATURE"
        )

    if not _valid_sha256(
        reservation.signed_message_sha256
    ):
        reasons.append(
            "INVALID_SIGNED_MESSAGE_SHA256"
        )

    if not _valid_sha256(
        reservation.signed_transaction_sha256
    ):
        reasons.append(
            "INVALID_SIGNED_TRANSACTION_SHA256"
        )

    transaction_bytes = (
        reservation.signed_transaction_bytes
    )

    if (
        not isinstance(
            transaction_bytes,
            bytes,
        )
        or not transaction_bytes
    ):
        reasons.append(
            "INVALID_SIGNED_TRANSACTION_BYTES"
        )

    elif (
        hashlib.sha256(
            transaction_bytes
        ).hexdigest()
        != reservation.signed_transaction_sha256
    ):
        reasons.append(
            "SIGNED_TRANSACTION_HASH_MISMATCH"
        )

    recent_blockhash = (
        reservation.recent_blockhash
    )

    try:
        parsed_blockhash = (
            Hash.from_string(
                recent_blockhash
            )
            if isinstance(
                recent_blockhash,
                str,
            )
            and recent_blockhash.strip()
            else None
        )
    except Exception:
        parsed_blockhash = None

    if (
        parsed_blockhash is None
        or parsed_blockhash
        == Hash.default()
    ):
        reasons.append(
            "INVALID_RECENT_BLOCKHASH"
        )

    if not _strict_nonnegative_int(
        reservation.last_valid_block_height
    ):
        reasons.append(
            "INVALID_LAST_VALID_BLOCK_HEIGHT"
        )

    if not _strict_nonnegative_int(
        reservation.blockhash_rpc_slot
    ):
        reasons.append(
            "INVALID_BLOCKHASH_RPC_SLOT"
        )

    return tuple(reasons)


def _parse_signature_status_response(
    response: Any,
    *,
    min_context_slot: int,
) -> tuple[
    int,
    dict[str, Any] | None,
]:
    if not isinstance(
        response,
        dict,
    ):
        raise ValueError(
            "status response must be an object"
        )

    context = response.get(
        "context"
    )

    if not isinstance(
        context,
        dict,
    ):
        raise ValueError(
            "status context missing"
        )

    context_slot = context.get(
        "slot"
    )

    if (
        not _strict_nonnegative_int(
            context_slot
        )
        or context_slot
        < min_context_slot
    ):
        raise ValueError(
            "status context is stale"
        )

    values = response.get(
        "value"
    )

    if (
        not isinstance(
            values,
            list,
        )
        or len(values) != 1
    ):
        raise ValueError(
            "status value shape invalid"
        )

    status = values[0]

    if status is None:
        return (
            context_slot,
            None,
        )

    if not isinstance(
        status,
        dict,
    ):
        raise ValueError(
            "transaction status invalid"
        )

    transaction_slot = status.get(
        "slot"
    )

    if not _strict_nonnegative_int(
        transaction_slot
    ):
        raise ValueError(
            "transaction slot invalid"
        )

    confirmations = status.get(
        "confirmations"
    )

    if (
        confirmations is not None
        and not _strict_nonnegative_int(
            confirmations
        )
    ):
        raise ValueError(
            "confirmations invalid"
        )

    if "err" not in status:
        raise ValueError(
            "transaction error field missing"
        )

    confirmation_status = status.get(
        "confirmationStatus"
    )

    if (
        confirmation_status is not None
        and confirmation_status
        not in (
            "processed",
            "confirmed",
            "finalized",
        )
    ):
        raise ValueError(
            "confirmation status invalid"
        )

    return (
        context_slot,
        status,
    )


async def resolve_signed_transaction_status(
    *,
    reservation_id: str,
    db_path: Path = DB_PATH,
) -> SignedTransactionStatusResult:
    reservation_id = (
        reservation_id.strip()
    )

    transaction_signature: (
        str | None
    ) = None

    status_source: str | None = None
    status_context_slot: (
        int | None
    ) = None
    transaction_slot: int | None = None

    confirmations: int | None = None
    confirmation_status: (
        str | None
    ) = None
    transaction_error: Any = None

    current_block_height: (
        int | None
    ) = None
    last_valid_block_height: (
        int | None
    ) = None
    blockhash_rpc_slot: (
        int | None
    ) = None

    history_searched = False

    def finish(
        state: str,
        *reasons: str,
    ) -> SignedTransactionStatusResult:
        return SignedTransactionStatusResult(
            resolver_version=(
                SIGNED_TRANSACTION_STATUS_RESOLVER_VERSION
            ),
            state=state,
            reasons=tuple(reasons),
            reservation_id=(
                reservation_id
            ),
            transaction_signature=(
                transaction_signature
            ),
            status_source=status_source,
            status_context_slot=(
                status_context_slot
            ),
            transaction_slot=(
                transaction_slot
            ),
            confirmations=confirmations,
            confirmation_status=(
                confirmation_status
            ),
            transaction_error=(
                transaction_error
            ),
            current_block_height=(
                current_block_height
            ),
            last_valid_block_height=(
                last_valid_block_height
            ),
            blockhash_rpc_slot=(
                blockhash_rpc_slot
            ),
            history_searched=(
                history_searched
            ),
        )

    if not reservation_id:
        return finish(
            UNKNOWN,
            "INVALID_RESERVATION_ID",
        )

    try:
        reservation = (
            load_capital_reservation_read_only(
                reservation_id=(
                    reservation_id
                ),
                db_path=db_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            "RESERVATION_READ_FAILED",
        )

    if reservation is None:
        return finish(
            UNKNOWN,
            "RESERVATION_NOT_FOUND",
        )

    validation_reasons = (
        _validate_signed_reservation(
            reservation
        )
    )

    if validation_reasons:
        return finish(
            UNKNOWN,
            *validation_reasons,
        )

    transaction_signature = (
        reservation
        .transaction_signature
    )

    last_valid_block_height = (
        reservation
        .last_valid_block_height
    )

    blockhash_rpc_slot = (
        reservation
        .blockhash_rpc_slot
    )

    assert transaction_signature is not None
    assert last_valid_block_height is not None
    assert blockhash_rpc_slot is not None

    try:
        async with HeliusRpcClient() as rpc:
            try:
                recent_response = (
                    await rpc.call(
                        "getSignatureStatuses",
                        [
                            [
                                transaction_signature
                            ]
                        ],
                    )
                )
            except Exception:
                return finish(
                    UNKNOWN,
                    "RECENT_STATUS_RPC_FAILED",
                )

            try:
                (
                    status_context_slot,
                    recent_status,
                ) = (
                    _parse_signature_status_response(
                        recent_response,
                        min_context_slot=(
                            blockhash_rpc_slot
                        ),
                    )
                )
            except Exception:
                return finish(
                    UNKNOWN,
                    "RECENT_STATUS_RESPONSE_INVALID",
                )

            if recent_status is not None:
                status_source = RECENT
                transaction_slot = (
                    recent_status["slot"]
                )
                confirmations = (
                    recent_status.get(
                        "confirmations"
                    )
                )
                confirmation_status = (
                    recent_status.get(
                        "confirmationStatus"
                    )
                )
                transaction_error = (
                    recent_status["err"]
                )

                return finish(
                    KNOWN,
                )

            try:
                height_response = (
                    await rpc.call(
                        "getBlockHeight",
                        [
                            {
                                "commitment": (
                                    COMMITMENT
                                ),
                                "minContextSlot": (
                                    blockhash_rpc_slot
                                ),
                            }
                        ],
                    )
                )
            except Exception:
                return finish(
                    UNKNOWN,
                    "BLOCK_HEIGHT_RPC_FAILED",
                )

            if not _strict_nonnegative_int(
                height_response
            ):
                return finish(
                    UNKNOWN,
                    "BLOCK_HEIGHT_RESPONSE_INVALID",
                )

            current_block_height = (
                height_response
            )

            #
            # lastValidBlockHeight is inclusive.
            # Expiration is established only after
            # the current block height exceeds it.
            #
            if (
                current_block_height
                <= last_valid_block_height
            ):
                return finish(
                    ABSENT_STILL_VALID,
                )

            #
            # A null recent-cache result after expiry
            # is not strong enough to prove absence.
            # Search long-term transaction history
            # before classifying ABSENT_EXPIRED.
            #
            history_searched = True

            try:
                history_response = (
                    await rpc.call(
                        "getSignatureStatuses",
                        [
                            [
                                transaction_signature
                            ],
                            {
                                "searchTransactionHistory": (
                                    True
                                ),
                            },
                        ],
                    )
                )
            except Exception:
                return finish(
                    UNKNOWN,
                    "HISTORY_STATUS_RPC_FAILED",
                )

            try:
                (
                    status_context_slot,
                    history_status,
                ) = (
                    _parse_signature_status_response(
                        history_response,
                        min_context_slot=(
                            blockhash_rpc_slot
                        ),
                    )
                )
            except Exception:
                return finish(
                    UNKNOWN,
                    "HISTORY_STATUS_RESPONSE_INVALID",
                )

            if history_status is not None:
                status_source = HISTORY
                transaction_slot = (
                    history_status["slot"]
                )
                confirmations = (
                    history_status.get(
                        "confirmations"
                    )
                )
                confirmation_status = (
                    history_status.get(
                        "confirmationStatus"
                    )
                )
                transaction_error = (
                    history_status["err"]
                )

                return finish(
                    KNOWN,
                )

            return finish(
                ABSENT_EXPIRED,
            )

    except Exception:
        return finish(
            UNKNOWN,
            "STATUS_RESOLUTION_FAILED",
        )
