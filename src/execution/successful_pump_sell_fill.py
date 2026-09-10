from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from src.execution.live_pump_sell_authorization import (
    LivePumpSellAuthorization,
)
from src.execution.live_sell_transaction_receipt import (
    BLOCK as RECEIPT_BLOCK,
    LIVE_SELL_TRANSACTION_RECEIPT_VERSION,
    RESOLVED as RECEIPT_RESOLVED,
    UNKNOWN as RECEIPT_UNKNOWN,
    resolve_live_sell_transaction_receipt,
)
from src.execution.live_sell_transaction_status import (
    KNOWN,
)
from src.execution.order_authorization import (
    WRAPPED_SOL_MINT,
)
from src.execution.pump_sell_v2_account_context import (
    SELL_V2_ACCOUNT_NAMES,
)
from src.execution.pump_sell_v2_instruction import (
    SELL_V2_DISCRIMINATOR,
)
from src.execution.successful_pump_buy_fill import (
    _extract_single_trade_event,
    _parse_exact_success_response,
    _token_balance_for_account,
)
from src.portfolio.live_reservations import (
    DB_PATH,
    get_connection,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    LiveSellInventoryClaim,
    _authorization_contract_valid,
    _claim_matches_authorization,
    _load_claim,
)
from src.portfolio.live_sell_execution_records import (
    BLOCK as EXECUTION_BLOCK,
    PASS as EXECUTION_PASS,
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED,
    LiveSellExecutionRecord,
    _record_contract_valid,
    load_live_sell_execution_record_read_only,
)
from src.safety.token_safety_resolver import (
    PUMP_PROGRAM,
    TOKEN_PROGRAM,
    HeliusRpcClient,
    derive_associated_token_account,
)


SUCCESSFUL_PUMP_SELL_FILL_VERSION = (
    "successful-pump-sell-fill-v1"
)

PROVEN = "PROVEN"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"

U64_MAX = (1 << 64) - 1


@dataclass(frozen=True)
class SuccessfulPumpSellFillResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str
    transaction_signature: str | None

    receipt_status: str | None
    receipt_slot: int | None
    block_time: int | None

    mint: str | None
    wallet_pubkey: str | None

    base_token_program: str | None
    associated_base_user: str | None

    quote_mint: str | None
    quote_token_program: str | None
    associated_quote_user: str | None

    authorized_token_amount: int | None
    min_quote_out: int | None

    trade_event_token_amount: int | None

    base_token_pre_amount: int | None
    base_token_post_amount: int | None
    base_token_debit: int | None

    quote_token_pre_amount: int | None
    quote_token_post_amount: int | None
    quote_token_credit_lamports: int | None

    fee_lamports: int | None

    wallet_pre_balance_lamports: int | None
    wallet_post_balance_lamports: int | None
    wallet_balance_delta_lamports: int | None

    trade_event_sol_amount: int | None
    protocol_fee_lamports: int | None
    creator_fee_lamports: int | None
    cashback_lamports: int | None
    buyback_fee_lamports: int | None
    quote_amount: int | None

    persisted_transaction_sha256: str | None
    observed_transaction_sha256: str | None


def _strict_u64(
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
        and 0 <= value <= U64_MAX
    )


def _execution_status_reconcilable(
    status: str,
) -> bool:
    return status in (
        SIGNED,
        SUBMISSION_ARMED,
        SUBMITTED,
    )


def _artifact_identity(
    record: LiveSellExecutionRecord,
) -> tuple:
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


def _execution_matches_authorization(
    *,
    record: LiveSellExecutionRecord,
    authorization: LivePumpSellAuthorization,
) -> bool:
    return (
        _record_contract_valid(
            record
        )
        and record.authorization_version
        == authorization.authorization_version
        and record.authorization_sha256
        == authorization.authorization_sha256
        and record.wallet_pubkey
        == authorization.wallet_pubkey
        and record.mint
        == authorization.mint
        and record.tokens_to_sell
        == authorization.tokens_to_sell
        and _execution_status_reconcilable(
            record.status
        )
    )


def _load_claim_read_only(
    *,
    authorization_sha256: str,
    db_path: Path,
) -> LiveSellInventoryClaim | None:
    connection = get_connection(
        db_path
    )

    try:
        return _load_claim(
            connection=connection,
            authorization_sha256=(
                authorization_sha256
            ),
        )

    finally:
        connection.close()


def _receipt_binding_matches(
    *,
    receipt,
    record: LiveSellExecutionRecord,
    authorization: LivePumpSellAuthorization,
) -> bool:
    if (
        receipt.resolver_version
        != LIVE_SELL_TRANSACTION_RECEIPT_VERSION
        or receipt.authorization_sha256
        != authorization.authorization_sha256
        or receipt.transaction_signature
        != record.transaction_signature
        or receipt.execution_status
        not in (
            SIGNED,
            SUBMISSION_ARMED,
            SUBMITTED,
        )
        or receipt.status_observation_state
        != KNOWN
        or receipt.persisted_transaction_sha256
        != record.signed_transaction_sha256
        or receipt.receipt_transaction_sha256
        != record.signed_transaction_sha256
        or receipt.fee_payer_pubkey
        != record.wallet_pubkey
        or receipt.last_valid_block_height
        != record.last_valid_block_height
        or receipt.blockhash_rpc_slot
        != record.blockhash_rpc_slot
        or not _strict_u64(
            receipt.status_transaction_slot
        )
        or not _strict_u64(
            receipt.receipt_slot
        )
        or receipt.status_transaction_slot
        != receipt.receipt_slot
        or not _strict_u64(
            receipt.fee_lamports
        )
        or not _strict_u64(
            receipt.fee_payer_pre_balance_lamports
        )
        or not _strict_u64(
            receipt.fee_payer_post_balance_lamports
        )
        or not isinstance(
            receipt.fee_payer_balance_delta_lamports,
            int,
        )
        or isinstance(
            receipt.fee_payer_balance_delta_lamports,
            bool,
        )
        or (
            receipt.fee_payer_post_balance_lamports
            - receipt.fee_payer_pre_balance_lamports
        )
        != receipt.fee_payer_balance_delta_lamports
    ):
        return False

    if (
        receipt.block_time is not None
        and not _strict_u64(
            receipt.block_time
        )
    ):
        return False

    return True


def _decode_authorized_sell(
    *,
    record: LiveSellExecutionRecord,
    authorization: LivePumpSellAuthorization,
) -> dict[str, Any]:
    if not _execution_matches_authorization(
        record=record,
        authorization=authorization,
    ):
        raise ValueError(
            "SELL_EXECUTION_AUTHORIZATION_MISMATCH"
        )

    try:
        transaction = (
            VersionedTransaction.from_bytes(
                record.signed_transaction_bytes
            )
        )
    except Exception as error:
        raise ValueError(
            "SELL_TRANSACTION_DECODE_FAILED"
        ) from error

    message = transaction.message

    if not isinstance(
        message,
        MessageV0,
    ):
        raise ValueError(
            "SELL_TRANSACTION_MESSAGE_NOT_V0"
        )

    if len(
        message.address_table_lookups
    ) != 0:
        raise ValueError(
            "SELL_ADDRESS_LOOKUPS_NOT_AUTHORIZED"
        )

    account_keys = tuple(
        message.account_keys
    )

    account_keys_text = tuple(
        str(value)
        for value in account_keys
    )

    if (
        not account_keys
        or account_keys_text[0]
        != authorization.wallet_pubkey
    ):
        raise ValueError(
            "SELL_FEE_PAYER_MISMATCH"
        )

    instructions = tuple(
        message.instructions
    )

    if len(
        instructions
    ) != 3:
        raise ValueError(
            "SELL_INSTRUCTION_COUNT_MISMATCH"
        )

    compiled = instructions[2]

    try:
        program_id = account_keys[
            compiled.program_id_index
        ]
    except Exception as error:
        raise ValueError(
            "SELL_PROGRAM_INDEX_INVALID"
        ) from error

    if program_id != PUMP_PROGRAM:
        raise ValueError(
            "SELL_PROGRAM_ID_MISMATCH"
        )

    instruction_data = bytes(
        compiled.data
    )

    if len(
        instruction_data
    ) != 24:
        raise ValueError(
            "SELL_INSTRUCTION_DATA_LENGTH_INVALID"
        )

    if (
        instruction_data[:8]
        != SELL_V2_DISCRIMINATOR
    ):
        raise ValueError(
            "SELL_V2_DISCRIMINATOR_MISMATCH"
        )

    amount, min_quote_out = (
        struct.unpack(
            "<QQ",
            instruction_data[8:],
        )
    )

    if (
        amount
        != authorization.tokens_to_sell
    ):
        raise ValueError(
            "SELL_AUTHORIZED_AMOUNT_MISMATCH"
        )

    expected_min_quote_out = getattr(
        authorization.exit_execution,
        "min_quote_out",
        None,
    )

    if (
        not _strict_u64(
            expected_min_quote_out
        )
        or expected_min_quote_out <= 0
        or min_quote_out
        != expected_min_quote_out
    ):
        raise ValueError(
            "SELL_MIN_QUOTE_OUT_MISMATCH"
        )

    account_indices = tuple(
        int(value)
        for value
        in compiled.accounts
    )

    if len(
        account_indices
    ) != len(
        SELL_V2_ACCOUNT_NAMES
    ):
        raise ValueError(
            "SELL_ACCOUNT_COUNT_MISMATCH"
        )

    if any(
        index < 0
        or index >= len(
            account_keys
        )
        for index in account_indices
    ):
        raise ValueError(
            "SELL_ACCOUNT_INDEX_INVALID"
        )

    accounts = {
        name: account_keys_text[
            index
        ]
        for name, index in zip(
            SELL_V2_ACCOUNT_NAMES,
            account_indices,
            strict=True,
        )
    }

    quote_mint = str(
        WRAPPED_SOL_MINT
    )

    quote_token_program = str(
        TOKEN_PROGRAM
    )

    required = {
        "base_mint":
            authorization.mint,

        "quote_mint":
            quote_mint,

        "base_token_program":
            authorization.base_token_program,

        "quote_token_program":
            quote_token_program,

        "bonding_curve":
            authorization.bonding_curve,

        "user":
            authorization.wallet_pubkey,

        "associated_base_user":
            authorization.associated_base_user,

        "program":
            str(
                PUMP_PROGRAM
            ),
    }

    for name, expected in required.items():
        if accounts.get(
            name
        ) != expected:
            raise ValueError(
                f"SELL_ACCOUNT_{name.upper()}_MISMATCH"
            )

    try:
        canonical_quote_user = (
            derive_associated_token_account(
                owner=Pubkey.from_string(
                    authorization.wallet_pubkey
                ),
                mint=Pubkey.from_string(
                    quote_mint
                ),
                token_program=TOKEN_PROGRAM,
            )
        )
    except Exception as error:
        raise ValueError(
            "SELL_QUOTE_USER_DERIVATION_FAILED"
        ) from error

    if (
        accounts[
            "associated_quote_user"
        ]
        != str(
            canonical_quote_user
        )
    ):
        raise ValueError(
            "SELL_ASSOCIATED_QUOTE_USER_MISMATCH"
        )

    try:
        base_account_index = (
            account_keys_text.index(
                authorization.associated_base_user
            )
        )

        quote_account_index = (
            account_keys_text.index(
                str(
                    canonical_quote_user
                )
            )
        )

    except ValueError as error:
        raise ValueError(
            "SELL_USER_TOKEN_ACCOUNT_INDEX_MISSING"
        ) from error

    return {
        "transaction_bytes":
            record.signed_transaction_bytes,

        "account_keys":
            account_keys_text,

        "base_token_program":
            authorization.base_token_program,

        "associated_base_user":
            authorization.associated_base_user,

        "quote_mint":
            quote_mint,

        "quote_token_program":
            quote_token_program,

        "associated_quote_user":
            str(
                canonical_quote_user
            ),

        "base_account_index":
            base_account_index,

        "quote_account_index":
            quote_account_index,

        "amount":
            amount,

        "min_quote_out":
            min_quote_out,
    }


def _validate_sell_trade_event(
    event: dict[str, Any],
    *,
    mint: str,
    wallet_pubkey: str,
    quote_mint: str,
    amount: int,
) -> None:
    if not isinstance(
        event,
        dict,
    ):
        raise ValueError(
            "TRADE_EVENT_INVALID"
        )

    if event.get(
        "mint"
    ) != mint:
        raise ValueError(
            "TRADE_EVENT_MINT_MISMATCH"
        )

    if event.get(
        "user"
    ) != wallet_pubkey:
        raise ValueError(
            "TRADE_EVENT_USER_MISMATCH"
        )

    if event.get(
        "is_buy"
    ) is not False:
        raise ValueError(
            "TRADE_EVENT_SIDE_MISMATCH"
        )

    if event.get(
        "token_amount"
    ) != amount:
        raise ValueError(
            "TRADE_EVENT_TOKEN_AMOUNT_MISMATCH"
        )

    for field in (
        "sol_amount",
        "token_amount",
        "fee",
        "creator_fee",
        "cashback",
        "buyback_fee",
        "quote_amount",
    ):
        if not _strict_u64(
            event.get(
                field
            )
        ):
            raise ValueError(
                f"TRADE_EVENT_{field.upper()}_INVALID"
            )

    if (
        not isinstance(
            event.get(
                "quote_mint"
            ),
            str,
        )
        or not event[
            "quote_mint"
        ]
    ):
        raise ValueError(
            "TRADE_EVENT_QUOTE_MINT_INVALID"
        )

    if event[
        "quote_mint"
    ] != quote_mint:
        raise ValueError(
            "TRADE_EVENT_QUOTE_MINT_MISMATCH"
        )


async def resolve_successful_pump_sell_fill(
    *,
    authorization: LivePumpSellAuthorization,
    db_path: Path = DB_PATH,
) -> SuccessfulPumpSellFillResult:
    """
    Prove one exact successful Pump sell_v2 fill.

    Observational only.

    PROVEN requires agreement between:
    - exact SELL authorization,
    - exact ACTIVE inventory claim,
    - cryptographically valid persisted signed artifact,
    - successful landed receipt,
    - exact fresh transaction bytes,
    - compiled Pump sell_v2 instruction,
    - authenticated Pump TradeEvent,
    - exact user base-token debit,
    - exact user WSOL quote-token credit.

    This resolver does not:
    - mutate the claim,
    - mutate positions,
    - calculate realized PnL,
    - sign,
    - rebuild,
    - submit,
    - retry a transaction.
    """

    authorization_sha256 = ""

    transaction_signature: (
        str | None
    ) = None

    receipt_status: (
        str | None
    ) = None

    receipt_slot: (
        int | None
    ) = None

    block_time: (
        int | None
    ) = None

    mint: str | None = None
    wallet_pubkey: str | None = None

    base_token_program: (
        str | None
    ) = None

    associated_base_user: (
        str | None
    ) = None

    quote_mint: str | None = None

    quote_token_program: (
        str | None
    ) = None

    associated_quote_user: (
        str | None
    ) = None

    authorized_token_amount: (
        int | None
    ) = None

    min_quote_out: (
        int | None
    ) = None

    trade_event_token_amount: (
        int | None
    ) = None

    base_token_pre_amount: (
        int | None
    ) = None

    base_token_post_amount: (
        int | None
    ) = None

    base_token_debit: (
        int | None
    ) = None

    quote_token_pre_amount: (
        int | None
    ) = None

    quote_token_post_amount: (
        int | None
    ) = None

    quote_token_credit_lamports: (
        int | None
    ) = None

    fee_lamports: int | None = None

    wallet_pre_balance_lamports: (
        int | None
    ) = None

    wallet_post_balance_lamports: (
        int | None
    ) = None

    wallet_balance_delta_lamports: (
        int | None
    ) = None

    trade_event_sol_amount: (
        int | None
    ) = None

    protocol_fee_lamports: (
        int | None
    ) = None

    creator_fee_lamports: (
        int | None
    ) = None

    cashback_lamports: (
        int | None
    ) = None

    buyback_fee_lamports: (
        int | None
    ) = None

    quote_amount: int | None = None

    persisted_transaction_sha256: (
        str | None
    ) = None

    observed_transaction_sha256: (
        str | None
    ) = None

    def finish(
        status: str,
        *reasons: str,
    ) -> SuccessfulPumpSellFillResult:
        return SuccessfulPumpSellFillResult(
            resolver_version=(
                SUCCESSFUL_PUMP_SELL_FILL_VERSION
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
            receipt_status=(
                receipt_status
            ),
            receipt_slot=(
                receipt_slot
            ),
            block_time=(
                block_time
            ),
            mint=mint,
            wallet_pubkey=(
                wallet_pubkey
            ),
            base_token_program=(
                base_token_program
            ),
            associated_base_user=(
                associated_base_user
            ),
            quote_mint=quote_mint,
            quote_token_program=(
                quote_token_program
            ),
            associated_quote_user=(
                associated_quote_user
            ),
            authorized_token_amount=(
                authorized_token_amount
            ),
            min_quote_out=(
                min_quote_out
            ),
            trade_event_token_amount=(
                trade_event_token_amount
            ),
            base_token_pre_amount=(
                base_token_pre_amount
            ),
            base_token_post_amount=(
                base_token_post_amount
            ),
            base_token_debit=(
                base_token_debit
            ),
            quote_token_pre_amount=(
                quote_token_pre_amount
            ),
            quote_token_post_amount=(
                quote_token_post_amount
            ),
            quote_token_credit_lamports=(
                quote_token_credit_lamports
            ),
            fee_lamports=(
                fee_lamports
            ),
            wallet_pre_balance_lamports=(
                wallet_pre_balance_lamports
            ),
            wallet_post_balance_lamports=(
                wallet_post_balance_lamports
            ),
            wallet_balance_delta_lamports=(
                wallet_balance_delta_lamports
            ),
            trade_event_sol_amount=(
                trade_event_sol_amount
            ),
            protocol_fee_lamports=(
                protocol_fee_lamports
            ),
            creator_fee_lamports=(
                creator_fee_lamports
            ),
            cashback_lamports=(
                cashback_lamports
            ),
            buyback_fee_lamports=(
                buyback_fee_lamports
            ),
            quote_amount=(
                quote_amount
            ),
            persisted_transaction_sha256=(
                persisted_transaction_sha256
            ),
            observed_transaction_sha256=(
                observed_transaction_sha256
            ),
        )

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

    mint = authorization.mint

    wallet_pubkey = (
        authorization.wallet_pubkey
    )

    try:
        initial_claim = (
            _load_claim_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_CLAIM_READ_FAILED",
        )

    if initial_claim is None:
        return finish(
            UNKNOWN,
            "SELL_CLAIM_NOT_FOUND",
        )

    if (
        initial_claim.status
        != ACTIVE
        or not _claim_matches_authorization(
            claim=initial_claim,
            authorization=authorization,
        )
    ):
        return finish(
            BLOCK,
            "SELL_CLAIM_NOT_ACTIVE",
        )

    try:
        initial_execution_result = (
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
            "SELL_EXECUTION_READ_FAILED",
        )

    if (
        initial_execution_result.status
        != EXECUTION_PASS
        or initial_execution_result.record
        is None
    ):
        mapped_status = (
            BLOCK
            if initial_execution_result.status
            == EXECUTION_BLOCK
            else UNKNOWN
        )

        return finish(
            mapped_status,
            "SELL_EXECUTION_UNAVAILABLE",
            *initial_execution_result.reasons,
        )

    initial_execution = (
        initial_execution_result.record
    )

    transaction_signature = (
        initial_execution.transaction_signature
    )

    persisted_transaction_sha256 = (
        initial_execution
        .signed_transaction_sha256
    )

    if not _execution_matches_authorization(
        record=initial_execution,
        authorization=authorization,
    ):
        return finish(
            BLOCK,
            "SELL_EXECUTION_AUTHORIZATION_MISMATCH",
        )

    initial_identity = (
        _artifact_identity(
            initial_execution
        )
    )

    try:
        receipt = (
            await resolve_live_sell_transaction_receipt(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_RECEIPT_RESOLUTION_FAILED",
        )

    receipt_status = (
        receipt.status
    )

    receipt_slot = (
        receipt.receipt_slot
    )

    block_time = (
        receipt.block_time
    )

    if (
        receipt.status
        == RECEIPT_UNKNOWN
    ):
        return finish(
            UNKNOWN,
            "SELL_RECEIPT_UNKNOWN",
            *receipt.reasons,
        )

    if (
        receipt.status
        == RECEIPT_BLOCK
    ):
        return finish(
            BLOCK,
            "TRANSACTION_NOT_READY_FOR_SELL_FILL_RESOLUTION",
            *receipt.reasons,
        )

    if (
        receipt.status
        != RECEIPT_RESOLVED
    ):
        return finish(
            UNKNOWN,
            "UNEXPECTED_SELL_RECEIPT_STATUS",
        )

    if (
        receipt.transaction_error
        is not None
    ):
        return finish(
            BLOCK,
            "TRANSACTION_FAILED_ON_CHAIN",
        )

    if not _receipt_binding_matches(
        receipt=receipt,
        record=initial_execution,
        authorization=authorization,
    ):
        return finish(
            UNKNOWN,
            "SUCCESSFUL_SELL_RECEIPT_BINDING_MISMATCH",
        )

    fee_lamports = (
        receipt.fee_lamports
    )

    wallet_pre_balance_lamports = (
        receipt
        .fee_payer_pre_balance_lamports
    )

    wallet_post_balance_lamports = (
        receipt
        .fee_payer_post_balance_lamports
    )

    wallet_balance_delta_lamports = (
        receipt
        .fee_payer_balance_delta_lamports
    )

    #
    # Re-read local authority after receipt observation.
    #
    try:
        current_claim = (
            _load_claim_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

        current_execution_result = (
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
            "SELL_LOCAL_STATE_REREAD_FAILED",
        )

    if (
        current_claim is None
        or current_claim.status
        != ACTIVE
        or not _claim_matches_authorization(
            claim=current_claim,
            authorization=authorization,
        )
    ):
        return finish(
            UNKNOWN,
            "SELL_CLAIM_CHANGED_DURING_RECEIPT_CHECK",
        )

    if (
        current_execution_result.status
        != EXECUTION_PASS
        or current_execution_result.record
        is None
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_REREAD_UNAVAILABLE",
            *current_execution_result.reasons,
        )

    current_execution = (
        current_execution_result.record
    )

    if (
        not _execution_matches_authorization(
            record=current_execution,
            authorization=authorization,
        )
        or _artifact_identity(
            current_execution
        )
        != initial_identity
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_CHANGED_DURING_RECEIPT_CHECK",
        )

    try:
        authorized = (
            _decode_authorized_sell(
                record=current_execution,
                authorization=authorization,
            )
        )

    except ValueError as error:
        return finish(
            UNKNOWN,
            str(error),
        )

    base_token_program = (
        authorized[
            "base_token_program"
        ]
    )

    associated_base_user = (
        authorized[
            "associated_base_user"
        ]
    )

    quote_mint = (
        authorized[
            "quote_mint"
        ]
    )

    quote_token_program = (
        authorized[
            "quote_token_program"
        ]
    )

    associated_quote_user = (
        authorized[
            "associated_quote_user"
        ]
    )

    authorized_token_amount = (
        authorized["amount"]
    )

    min_quote_out = (
        authorized[
            "min_quote_out"
        ]
    )

    #
    # Fresh read-only fetch for logs and token balances.
    #
    try:
        async with HeliusRpcClient() as rpc:
            response = await rpc.call(
                "getTransaction",
                [
                    current_execution
                    .transaction_signature,
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
            "SELL_FILL_RPC_FAILED",
        )

    if response is None:
        return finish(
            UNKNOWN,
            "SUCCESSFUL_SELL_TRANSACTION_METADATA_NOT_FOUND",
        )

    try:
        (
            meta,
            observed_transaction_sha256,
        ) = _parse_exact_success_response(
            response,
            expected_transaction_bytes=(
                authorized[
                    "transaction_bytes"
                ]
            ),
            expected_transaction_sha256=(
                current_execution
                .signed_transaction_sha256
            ),
            expected_slot=(
                receipt.receipt_slot
            ),
            expected_block_time=(
                receipt.block_time
            ),
            expected_fee_lamports=(
                receipt.fee_lamports
            ),
            expected_payer_pre_balance=(
                receipt
                .fee_payer_pre_balance_lamports
            ),
            expected_payer_post_balance=(
                receipt
                .fee_payer_post_balance_lamports
            ),
            expected_payer_delta=(
                receipt
                .fee_payer_balance_delta_lamports
            ),
            expected_account_count=len(
                authorized[
                    "account_keys"
                ]
            ),
        )

        event = (
            _extract_single_trade_event(
                meta=meta,
                account_keys=(
                    authorized[
                        "account_keys"
                    ]
                ),
            )
        )

        _validate_sell_trade_event(
            event,
            mint=authorization.mint,
            wallet_pubkey=(
                authorization.wallet_pubkey
            ),
            quote_mint=(
                authorized[
                    "quote_mint"
                ]
            ),
            amount=(
                authorized[
                    "amount"
                ]
            ),
        )

        pre_balances = meta[
            "preBalances"
        ]

        base_account_index = (
            authorized[
                "base_account_index"
            ]
        )

        quote_account_index = (
            authorized[
                "quote_account_index"
            ]
        )

        base_pre = (
            _token_balance_for_account(
                meta.get(
                    "preTokenBalances"
                ),
                account_index=(
                    base_account_index
                ),
                mint=authorization.mint,
                owner=(
                    authorization.wallet_pubkey
                ),
                token_program=(
                    authorized[
                        "base_token_program"
                    ]
                ),
            )
        )

        base_post = (
            _token_balance_for_account(
                meta.get(
                    "postTokenBalances"
                ),
                account_index=(
                    base_account_index
                ),
                mint=authorization.mint,
                owner=(
                    authorization.wallet_pubkey
                ),
                token_program=(
                    authorized[
                        "base_token_program"
                    ]
                ),
            )
        )

        if base_pre is None:
            raise ValueError(
                "SELL_PRE_BASE_TOKEN_BALANCE_MISSING"
            )

        if base_post is None:
            raise ValueError(
                "SELL_POST_BASE_TOKEN_BALANCE_MISSING"
            )

        base_debit = (
            base_pre
            - base_post
        )

        if base_debit <= 0:
            raise ValueError(
                "SELL_BASE_TOKEN_DEBIT_INVALID"
            )

        if (
            base_debit
            != authorization.tokens_to_sell
        ):
            raise ValueError(
                "SELL_BASE_TOKEN_DEBIT_MISMATCH"
            )

        quote_pre = (
            _token_balance_for_account(
                meta.get(
                    "preTokenBalances"
                ),
                account_index=(
                    quote_account_index
                ),
                mint=(
                    authorized[
                        "quote_mint"
                    ]
                ),
                owner=(
                    authorization.wallet_pubkey
                ),
                token_program=(
                    authorized[
                        "quote_token_program"
                    ]
                ),
            )
        )

        quote_post = (
            _token_balance_for_account(
                meta.get(
                    "postTokenBalances"
                ),
                account_index=(
                    quote_account_index
                ),
                mint=(
                    authorized[
                        "quote_mint"
                    ]
                ),
                owner=(
                    authorization.wallet_pubkey
                ),
                token_program=(
                    authorized[
                        "quote_token_program"
                    ]
                ),
            )
        )

        if quote_post is None:
            raise ValueError(
                "SELL_POST_QUOTE_TOKEN_BALANCE_MISSING"
            )

        if quote_pre is None:
            #
            # Missing preTokenBalances is authoritative
            # zero only if the exact quote ATA had zero
            # pre-transaction lamports.
            #
            if (
                pre_balances[
                    quote_account_index
                ]
                != 0
            ):
                raise ValueError(
                    "SELL_PRE_QUOTE_BALANCE_MISSING_FOR_EXISTING_ACCOUNT"
                )

            quote_pre = 0

        quote_credit = (
            quote_post
            - quote_pre
        )

        if quote_credit <= 0:
            raise ValueError(
                "SELL_QUOTE_TOKEN_CREDIT_INVALID"
            )

        if (
            quote_credit
            < authorized[
                "min_quote_out"
            ]
        ):
            raise ValueError(
                "SELL_QUOTE_CREDIT_BELOW_AUTHORIZED_MINIMUM"
            )

    except (
        ValueError,
        IndexError,
        KeyError,
    ) as error:
        return finish(
            UNKNOWN,
            str(error),
        )

    trade_event_token_amount = (
        event[
            "token_amount"
        ]
    )

    base_token_pre_amount = (
        base_pre
    )

    base_token_post_amount = (
        base_post
    )

    base_token_debit = (
        base_debit
    )

    quote_token_pre_amount = (
        quote_pre
    )

    quote_token_post_amount = (
        quote_post
    )

    quote_token_credit_lamports = (
        quote_credit
    )

    trade_event_sol_amount = (
        event[
            "sol_amount"
        ]
    )

    protocol_fee_lamports = (
        event["fee"]
    )

    creator_fee_lamports = (
        event[
            "creator_fee"
        ]
    )

    cashback_lamports = (
        event[
            "cashback"
        ]
    )

    buyback_fee_lamports = (
        event[
            "buyback_fee"
        ]
    )

    quote_amount = (
        event[
            "quote_amount"
        ]
    )

    #
    # Final local identity check after all chain reads.
    #
    try:
        final_claim = (
            _load_claim_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=db_path,
            )
        )

        final_execution_result = (
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
            "SELL_FINAL_LOCAL_READ_FAILED",
        )

    if (
        final_claim is None
        or final_claim.status
        != ACTIVE
        or not _claim_matches_authorization(
            claim=final_claim,
            authorization=authorization,
        )
    ):
        return finish(
            UNKNOWN,
            "SELL_CLAIM_CHANGED_DURING_FILL_CHECK",
        )

    if (
        final_execution_result.status
        != EXECUTION_PASS
        or final_execution_result.record
        is None
        or not _execution_matches_authorization(
            record=(
                final_execution_result.record
            ),
            authorization=authorization,
        )
        or _artifact_identity(
            final_execution_result.record
        )
        != initial_identity
    ):
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_CHANGED_DURING_FILL_CHECK",
        )

    return finish(
        PROVEN,
    )
