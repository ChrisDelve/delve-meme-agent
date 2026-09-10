from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.signed_transaction_status import (
    ABSENT_EXPIRED,
    ABSENT_STILL_VALID,
    HISTORY,
    KNOWN,
    RECENT,
    UNKNOWN,
    _parse_signature_status_response,
    _strict_nonnegative_int,
)
from src.portfolio.live_reservations import (
    DB_PATH,
)
from src.portfolio.live_sell_execution_records import (
    PASS as EXECUTION_PASS,
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED,
    load_live_sell_execution_record_read_only,
)
from src.safety.token_safety_resolver import (
    COMMITMENT,
    HeliusRpcClient,
)


LIVE_SELL_TRANSACTION_STATUS_RESOLVER_VERSION = (
    "live-sell-transaction-status-resolver-v1"
)


@dataclass(frozen=True)
class LiveSellTransactionStatusResult:
    resolver_version: str

    state: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    transaction_signature: str | None
    execution_status: str | None

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


async def resolve_live_sell_transaction_status(
    *,
    authorization_sha256: str,
    db_path: Path = DB_PATH,
) -> LiveSellTransactionStatusResult:
    """
    Observe the chain state of one exact durable
    signed SELL transaction.

    This resolver is intentionally read-only.

    It has:
    - no signing authority
    - no send authority
    - no resend authority
    - no claim mutation authority
    - no position mutation authority
    - no execution-record mutation authority

    Artifact integrity is delegated to
    load_live_sell_execution_record_read_only(),
    which independently reparses and verifies the
    persisted VersionedTransaction.

    State semantics intentionally match the existing
    BUY signed-transaction status resolver:

        KNOWN
        ABSENT_STILL_VALID
        ABSENT_EXPIRED
        UNKNOWN
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

    status_source: str | None = None

    status_context_slot: (
        int | None
    ) = None

    transaction_slot: (
        int | None
    ) = None

    confirmations: (
        int | None
    ) = None

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
    ) -> LiveSellTransactionStatusResult:
        return LiveSellTransactionStatusResult(
            resolver_version=(
                LIVE_SELL_TRANSACTION_STATUS_RESOLVER_VERSION
            ),
            state=state,
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
            status_source=(
                status_source
            ),
            status_context_slot=(
                status_context_slot
            ),
            transaction_slot=(
                transaction_slot
            ),
            confirmations=(
                confirmations
            ),
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

    if not _valid_sha256(
        authorization_sha256
    ):
        return finish(
            UNKNOWN,
            "INVALID_AUTHORIZATION_SHA256",
        )

    #
    # The durable execution-record loader is the
    # artifact-integrity boundary for SELL.
    #
    try:
        execution_result = (
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
        execution_result.status
        != EXECUTION_PASS
        or execution_result.record
        is None
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_RECORD_UNAVAILABLE",
            *execution_result.reasons,
        )

    record = execution_result.record

    execution_status = (
        record.status
    )

    if record.status not in (
        SIGNED,
        SUBMISSION_ARMED,
        SUBMITTED,
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_STATUS_NOT_RESOLVABLE",
        )

    transaction_signature = (
        record.transaction_signature
    )

    last_valid_block_height = (
        record.last_valid_block_height
    )

    blockhash_rpc_slot = (
        record.blockhash_rpc_slot
    )

    #
    # The execution-record loader has already proven
    # the signed bytes, embedded signature, message
    # hash, transaction hash, payer, and blockhash.
    #
    if (
        not isinstance(
            transaction_signature,
            str,
        )
        or not transaction_signature
        or not _strict_nonnegative_int(
            last_valid_block_height
        )
        or not _strict_nonnegative_int(
            blockhash_rpc_slot
        )
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_STATUS_INPUT_INVALID",
        )

    try:
        async with HeliusRpcClient() as rpc:
            #
            # Recent validator/cache observation.
            #
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
                    recent_status[
                        "slot"
                    ]
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
                    recent_status[
                        "err"
                    ]
                )

                return finish(
                    KNOWN,
                )

            #
            # The recent cache does not know the
            # signature. Determine whether the signed
            # transaction is still blockhash-valid.
            #
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
            #
            if (
                current_block_height
                <= last_valid_block_height
            ):
                return finish(
                    ABSENT_STILL_VALID,
                )

            #
            # Recent-cache absence after expiry is not
            # enough to prove non-landing. Search
            # transaction history before declaring
            # ABSENT_EXPIRED.
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
                    history_status[
                        "slot"
                    ]
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
                    history_status[
                        "err"
                    ]
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
