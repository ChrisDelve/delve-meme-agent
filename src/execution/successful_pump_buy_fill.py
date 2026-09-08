from __future__ import annotations

import base64
import hashlib
import struct

import base58
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.transaction import (
    VersionedTransaction,
)

from src.data.trade_event import (
    TRADE_EVENT_DISCRIMINATOR,
    decode_trade_event_bytes,
)
from src.execution.pump_buy_v2_account_context import (
    BUY_V2_ACCOUNT_NAMES,
)
from src.execution.pump_buy_v2_instruction import (
    BUY_V2_DISCRIMINATOR,
)
from src.execution.order_authorization import (
    WRAPPED_SOL_MINT,
)
from src.execution.signed_transaction_receipt import (
    BLOCK as RECEIPT_BLOCK,
    RESOLVED as RECEIPT_RESOLVED,
    UNKNOWN as RECEIPT_UNKNOWN,
    SIGNED_TRANSACTION_RECEIPT_VERSION,
    resolve_signed_transaction_receipt,
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
    ASSOCIATED_TOKEN_PROGRAM,
    PUMP_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    HeliusRpcClient,
)


SUCCESSFUL_PUMP_BUY_FILL_VERSION = (
    "successful-pump-buy-fill-v1"
)

PROVEN = "PROVEN"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"

U64_MAX = (1 << 64) - 1


@dataclass(frozen=True)
class SuccessfulPumpBuyFillResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    reservation_id: str
    transaction_signature: str | None

    receipt_status: str | None
    receipt_slot: int | None
    block_time: int | None

    mint: str | None
    wallet_pubkey: str | None

    base_token_program: str | None
    associated_base_user: str | None

    authorized_token_amount: int | None
    max_sol_cost: int | None

    trade_event_token_amount: int | None

    token_pre_amount: int | None
    token_post_amount: int | None
    token_delta: int | None

    fee_lamports: int | None
    wallet_pre_balance_lamports: int | None
    wallet_post_balance_lamports: int | None
    wallet_balance_delta_lamports: int | None
    wallet_cost_lamports: int | None

    trade_event_sol_amount: int | None
    protocol_fee_lamports: int | None
    creator_fee_lamports: int | None
    cashback_lamports: int | None
    buyback_fee_lamports: int | None

    quote_mint: str | None
    quote_amount: int | None

    persisted_transaction_sha256: str | None
    observed_transaction_sha256: str | None


def _strict_u64(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= U64_MAX
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


def _artifact_identity(
    reservation: LiveCapitalReservation,
) -> tuple:
    return (
        reservation.reservation_version,
        reservation.wallet_pubkey,
        reservation.mint,
        reservation.side,
        reservation.spend_lamports,
        reservation.wallet_cost_lamports,
        reservation.signed_at,
        reservation.transaction_signature,
        reservation.signed_message_sha256,
        reservation.signed_transaction_sha256,
        reservation.signed_transaction_bytes,
        reservation.recent_blockhash,
        reservation.last_valid_block_height,
        reservation.blockhash_rpc_slot,
    )


def _derive_ata(
    *,
    owner: Pubkey,
    mint: Pubkey,
    token_program: Pubkey,
) -> Pubkey:
    address, _ = (
        Pubkey.find_program_address(
            [
                bytes(owner),
                bytes(token_program),
                bytes(mint),
            ],
            ASSOCIATED_TOKEN_PROGRAM,
        )
    )

    return address


def _decode_authorized_buy(
    reservation: LiveCapitalReservation,
) -> dict[str, Any]:
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
        raise ValueError(
            "SIGNED_TRANSACTION_BYTES_INVALID"
        )

    try:
        transaction = (
            VersionedTransaction.from_bytes(
                transaction_bytes
            )
        )
    except Exception as error:
        raise ValueError(
            "SIGNED_TRANSACTION_DESERIALIZATION_FAILED"
        ) from error

    message = transaction.message

    if not isinstance(
        message,
        MessageV0,
    ):
        raise ValueError(
            "SIGNED_TRANSACTION_MESSAGE_TYPE_INVALID"
        )

    if len(
        message.address_table_lookups
    ) != 0:
        raise ValueError(
            "SIGNED_TRANSACTION_ADDRESS_LOOKUP_UNSUPPORTED"
        )

    account_keys = tuple(
        message.account_keys
    )

    instructions = tuple(
        message.instructions
    )

    if len(instructions) != 3:
        raise ValueError(
            "SIGNED_TRANSACTION_INSTRUCTION_COUNT_INVALID"
        )

    compiled = instructions[2]

    program_index = (
        compiled.program_id_index
    )

    if (
        not isinstance(
            program_index,
            int,
        )
        or isinstance(
            program_index,
            bool,
        )
        or program_index < 0
        or program_index
        >= len(account_keys)
    ):
        raise ValueError(
            "BUY_V2_PROGRAM_INDEX_INVALID"
        )

    if (
        account_keys[
            program_index
        ]
        != PUMP_PROGRAM
    ):
        raise ValueError(
            "BUY_V2_PROGRAM_MISMATCH"
        )

    compiled_account_indexes = tuple(
        compiled.accounts
    )

    if (
        len(
            compiled_account_indexes
        )
        != len(
            BUY_V2_ACCOUNT_NAMES
        )
    ):
        raise ValueError(
            "BUY_V2_ACCOUNT_COUNT_MISMATCH"
        )

    try:
        compiled_accounts = tuple(
            account_keys[index]
            for index
            in compiled_account_indexes
        )
    except (
        IndexError,
        TypeError,
    ) as error:
        raise ValueError(
            "BUY_V2_ACCOUNT_INDEX_INVALID"
        ) from error

    accounts = dict(
        zip(
            BUY_V2_ACCOUNT_NAMES,
            compiled_accounts,
            strict=True,
        )
    )

    instruction_data = bytes(
        compiled.data
    )

    if len(instruction_data) != 24:
        raise ValueError(
            "BUY_V2_DATA_LENGTH_INVALID"
        )

    if (
        instruction_data[:8]
        != BUY_V2_DISCRIMINATOR
    ):
        raise ValueError(
            "BUY_V2_DISCRIMINATOR_MISMATCH"
        )

    amount, max_sol_cost = (
        struct.unpack(
            "<QQ",
            instruction_data[8:],
        )
    )

    if amount <= 0:
        raise ValueError(
            "BUY_V2_AMOUNT_INVALID"
        )

    if max_sol_cost <= 0:
        raise ValueError(
            "BUY_V2_MAX_SOL_COST_INVALID"
        )

    base_mint = accounts[
        "base_mint"
    ]

    quote_mint = accounts[
        "quote_mint"
    ]

    base_token_program = accounts[
        "base_token_program"
    ]

    quote_token_program = accounts[
        "quote_token_program"
    ]

    associated_token_program = accounts[
        "associated_token_program"
    ]

    program_account = accounts[
        "program"
    ]

    user = accounts[
        "user"
    ]

    associated_base_user = accounts[
        "associated_base_user"
    ]

    if (
        str(base_mint)
        != reservation.mint
    ):
        raise ValueError(
            "BUY_V2_MINT_MISMATCH"
        )

    if (
        str(user)
        != reservation.wallet_pubkey
    ):
        raise ValueError(
            "BUY_V2_USER_MISMATCH"
        )

    if base_token_program not in (
        TOKEN_PROGRAM,
        TOKEN_2022_PROGRAM,
    ):
        raise ValueError(
            "BUY_V2_BASE_TOKEN_PROGRAM_INVALID"
        )

    try:
        wrapped_sol_mint = (
            Pubkey.from_string(
                str(
                    WRAPPED_SOL_MINT
                )
            )
        )
    except Exception as error:
        raise ValueError(
            "WRAPPED_SOL_MINT_INVALID"
        ) from error

    if quote_mint != wrapped_sol_mint:
        raise ValueError(
            "BUY_V2_NON_SOL_QUOTE_MINT"
        )

    if (
        quote_token_program
        != TOKEN_PROGRAM
    ):
        raise ValueError(
            "BUY_V2_SOL_QUOTE_TOKEN_PROGRAM_MISMATCH"
        )

    if (
        associated_token_program
        != ASSOCIATED_TOKEN_PROGRAM
    ):
        raise ValueError(
            "BUY_V2_ASSOCIATED_TOKEN_PROGRAM_MISMATCH"
        )

    if program_account != PUMP_PROGRAM:
        raise ValueError(
            "BUY_V2_PROGRAM_ACCOUNT_MISMATCH"
        )

    reservation_spend = getattr(
        reservation,
        "spend_lamports",
        None,
    )

    if (
        not _strict_u64(
            reservation_spend
        )
        or reservation_spend <= 0
        or max_sol_cost
        != reservation_spend
    ):
        raise ValueError(
            "BUY_V2_MAX_SOL_COST_RESERVATION_MISMATCH"
        )

    derived_ata = _derive_ata(
        owner=user,
        mint=base_mint,
        token_program=base_token_program,
    )

    if (
        associated_base_user
        != derived_ata
    ):
        raise ValueError(
            "BUY_V2_ASSOCIATED_BASE_USER_MISMATCH"
        )

    try:
        token_account_index = (
            account_keys.index(
                associated_base_user
            )
        )
    except ValueError as error:
        raise ValueError(
            "BUY_V2_ASSOCIATED_BASE_USER_NOT_IN_MESSAGE"
        ) from error

    return {
        "transaction_bytes":
            transaction_bytes,

        "account_keys":
            account_keys,

        "base_mint":
            str(base_mint),

        "base_token_program":
            str(
                base_token_program
            ),

        "quote_mint":
            str(
                quote_mint
            ),

        "user":
            str(user),

        "associated_base_user":
            str(
                associated_base_user
            ),

        "token_account_index":
            token_account_index,

        "amount":
            amount,

        "max_sol_cost":
            max_sol_cost,
    }


def _parse_token_amount(
    value: Any,
) -> int:
    if (
        not isinstance(
            value,
            str,
        )
        or not value
        or not value.isdigit()
    ):
        raise ValueError(
            "TOKEN_BALANCE_AMOUNT_INVALID"
        )

    amount = int(value)

    if not _strict_u64(
        amount
    ):
        raise ValueError(
            "TOKEN_BALANCE_AMOUNT_INVALID"
        )

    return amount


def _token_balance_for_account(
    entries: Any,
    *,
    account_index: int,
    mint: str,
    owner: str,
    token_program: str,
) -> int | None:
    if entries is None:
        entries = []

    if not isinstance(
        entries,
        list,
    ):
        raise ValueError(
            "TOKEN_BALANCE_LIST_INVALID"
        )

    matching = []

    for entry in entries:
        if not isinstance(
            entry,
            dict,
        ):
            raise ValueError(
                "TOKEN_BALANCE_ENTRY_INVALID"
            )

        if (
            entry.get(
                "accountIndex"
            )
            == account_index
        ):
            matching.append(
                entry
            )

    if len(matching) > 1:
        raise ValueError(
            "TOKEN_BALANCE_ACCOUNT_AMBIGUOUS"
        )

    if not matching:
        return None

    entry = matching[0]

    if (
        entry.get("mint")
        != mint
    ):
        raise ValueError(
            "TOKEN_BALANCE_MINT_MISMATCH"
        )

    observed_owner = entry.get(
        "owner"
    )

    if (
        observed_owner is not None
        and observed_owner != owner
    ):
        raise ValueError(
            "TOKEN_BALANCE_OWNER_MISMATCH"
        )

    observed_program = entry.get(
        "programId"
    )

    if (
        observed_program is not None
        and observed_program
        != token_program
    ):
        raise ValueError(
            "TOKEN_BALANCE_PROGRAM_MISMATCH"
        )

    ui_token_amount = entry.get(
        "uiTokenAmount"
    )

    if not isinstance(
        ui_token_amount,
        dict,
    ):
        raise ValueError(
            "TOKEN_BALANCE_UI_AMOUNT_INVALID"
        )

    return _parse_token_amount(
        ui_token_amount.get(
            "amount"
        )
    )


def _extract_single_trade_event(
    *,
    meta: dict[str, Any],
    account_keys: tuple,
) -> dict[str, Any]:
    """
    Extract exactly one Pump TradeEvent while
    authenticating its emitting program.

    Preferred authority:
    - Pump CPI instruction from innerInstructions.

    Fallback authority:
    - Program data emitted while the Solana log
      invocation stack identifies Pump as the
      currently executing program.

    Bare unauthenticated Program data is never
    accepted as live fill evidence.
    """

    pump_program_text = str(
        PUMP_PROGRAM
    )

    #
    # Prefer CPI event evidence because the
    # instruction itself carries program identity.
    #
    inner_groups = meta.get(
        "innerInstructions"
    )

    if inner_groups is None:
        inner_groups = []

    if not isinstance(
        inner_groups,
        list,
    ):
        raise ValueError(
            "TRADE_EVENT_INNER_INSTRUCTIONS_INVALID"
        )

    inner_events: list[
        dict[str, Any]
    ] = []

    for group in inner_groups:
        if not isinstance(
            group,
            dict,
        ):
            raise ValueError(
                "TRADE_EVENT_INNER_GROUP_INVALID"
            )

        instructions = group.get(
            "instructions"
        )

        if not isinstance(
            instructions,
            list,
        ):
            raise ValueError(
                "TRADE_EVENT_INNER_INSTRUCTIONS_INVALID"
            )

        for instruction in instructions:
            if not isinstance(
                instruction,
                dict,
            ):
                raise ValueError(
                    "TRADE_EVENT_INNER_INSTRUCTION_INVALID"
                )

            program_matches = False

            program_id = instruction.get(
                "programId"
            )

            if isinstance(
                program_id,
                str,
            ):
                program_matches = (
                    program_id
                    == pump_program_text
                )

            elif (
                "programIdIndex"
                in instruction
            ):
                program_index = (
                    instruction.get(
                        "programIdIndex"
                    )
                )

                if (
                    not isinstance(
                        program_index,
                        int,
                    )
                    or isinstance(
                        program_index,
                        bool,
                    )
                    or program_index < 0
                    or program_index
                    >= len(account_keys)
                ):
                    raise ValueError(
                        "TRADE_EVENT_PROGRAM_INDEX_INVALID"
                    )

                program_matches = (
                    str(
                        account_keys[
                            program_index
                        ]
                    )
                    == pump_program_text
                )

            if not program_matches:
                continue

            encoded = instruction.get(
                "data"
            )

            if not isinstance(
                encoded,
                str,
            ):
                continue

            try:
                raw = base58.b58decode(
                    encoded
                )
            except Exception:
                continue

            event = (
                decode_trade_event_bytes(
                    raw
                )
            )

            if event is not None:
                inner_events.append(
                    event
                )

    if inner_events:
        if len(inner_events) != 1:
            raise ValueError(
                "TRADE_EVENT_AMBIGUOUS"
            )

        return inner_events[0]

    #
    # Fall back to log evidence, but only while
    # Pump is provably at the top of the invocation
    # stack.
    #
    logs = meta.get(
        "logMessages"
    )

    if not isinstance(
        logs,
        list,
    ):
        raise ValueError(
            "TRADE_EVENT_LOGS_INVALID"
        )

    active_programs: list[str] = []

    log_events: list[
        dict[str, Any]
    ] = []

    for log in logs:
        if not isinstance(
            log,
            str,
        ):
            raise ValueError(
                "TRADE_EVENT_LOG_ENTRY_INVALID"
            )

        if (
            log.startswith(
                "Program "
            )
            and " invoke ["
            in log
        ):
            parts = log.split()

            if len(parts) < 4:
                raise ValueError(
                    "TRADE_EVENT_LOG_STACK_INVALID"
                )

            program_id = parts[1]

            depth_text = parts[-1]

            try:
                depth = int(
                    depth_text
                    .removeprefix("[")
                    .removesuffix("]")
                )
            except ValueError as error:
                raise ValueError(
                    "TRADE_EVENT_LOG_STACK_INVALID"
                ) from error

            if depth <= 0:
                raise ValueError(
                    "TRADE_EVENT_LOG_STACK_INVALID"
                )

            active_programs = (
                active_programs[
                    :depth - 1
                ]
            )

            active_programs.append(
                program_id
            )

            continue

        if log.startswith(
            "Program data: "
        ):
            if (
                not active_programs
                or active_programs[-1]
                != pump_program_text
            ):
                continue

            encoded = log.removeprefix(
                "Program data: "
            )

            try:
                raw = base64.b64decode(
                    encoded,
                    validate=True,
                )
            except Exception:
                continue

            if not raw.startswith(
                TRADE_EVENT_DISCRIMINATOR
            ):
                continue

            event = (
                decode_trade_event_bytes(
                    raw
                )
            )

            if event is None:
                raise ValueError(
                    "TRADE_EVENT_DECODE_FAILED"
                )

            log_events.append(
                event
            )

            continue

        if log.startswith(
            "Program "
        ):
            parts = log.split()

            if len(parts) >= 3:
                program_id = parts[1]

                is_terminal = (
                    log.endswith(
                        " success"
                    )
                    or " failed:"
                    in log
                )

                if (
                    is_terminal
                    and active_programs
                    and active_programs[-1]
                    == program_id
                ):
                    active_programs.pop()

    if len(log_events) == 0:
        raise ValueError(
            "TRADE_EVENT_MISSING"
        )

    if len(log_events) != 1:
        raise ValueError(
            "TRADE_EVENT_AMBIGUOUS"
        )

    return log_events[0]


def _validate_trade_event(
    event: dict[str, Any],
    *,
    mint: str,
    wallet_pubkey: str,
    quote_mint: str,
    amount: int,
) -> None:
    if (
        event.get("mint")
        != mint
    ):
        raise ValueError(
            "TRADE_EVENT_MINT_MISMATCH"
        )

    if (
        event.get("user")
        != wallet_pubkey
    ):
        raise ValueError(
            "TRADE_EVENT_USER_MISMATCH"
        )

    if (
        event.get("is_buy")
        is not True
    ):
        raise ValueError(
            "TRADE_EVENT_SIDE_MISMATCH"
        )

    if (
        event.get(
            "token_amount"
        )
        != amount
    ):
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
            event.get(field)
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

    if (
        event["quote_mint"]
        != quote_mint
    ):
        raise ValueError(
            "TRADE_EVENT_QUOTE_MINT_MISMATCH"
        )


def _parse_exact_success_response(
    response: Any,
    *,
    expected_transaction_bytes: bytes,
    expected_transaction_sha256: str,
    expected_slot: int,
    expected_block_time: int | None,
    expected_fee_lamports: int,
    expected_payer_pre_balance: int,
    expected_payer_post_balance: int,
    expected_payer_delta: int,
    expected_account_count: int,
) -> tuple[
    dict[str, Any],
    str,
]:
    if not isinstance(
        response,
        dict,
    ):
        raise ValueError(
            "FILL_RESPONSE_INVALID"
        )

    slot = response.get(
        "slot"
    )

    if (
        not _strict_u64(slot)
        or slot != expected_slot
    ):
        raise ValueError(
            "FILL_RECEIPT_SLOT_MISMATCH"
        )

    block_time = response.get(
        "blockTime"
    )

    if (
        block_time
        != expected_block_time
    ):
        raise ValueError(
            "FILL_BLOCK_TIME_MISMATCH"
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
        or transaction[1]
        != "base64"
    ):
        raise ValueError(
            "FILL_TRANSACTION_ENCODING_INVALID"
        )

    try:
        observed_bytes = (
            base64.b64decode(
                transaction[0],
                validate=True,
            )
        )
    except Exception as error:
        raise ValueError(
            "FILL_TRANSACTION_BASE64_INVALID"
        ) from error

    observed_sha256 = (
        hashlib.sha256(
            observed_bytes
        ).hexdigest()
    )

    if (
        observed_sha256
        != expected_transaction_sha256
    ):
        raise ValueError(
            "FILL_TRANSACTION_HASH_MISMATCH"
        )

    if (
        observed_bytes
        != expected_transaction_bytes
    ):
        raise ValueError(
            "FILL_TRANSACTION_BYTES_MISMATCH"
        )

    meta = response.get(
        "meta"
    )

    if not isinstance(
        meta,
        dict,
    ):
        raise ValueError(
            "FILL_META_INVALID"
        )

    if (
        "err" not in meta
        or meta["err"] is not None
    ):
        raise ValueError(
            "FILL_TRANSACTION_NOT_SUCCESSFUL"
        )

    fee = meta.get(
        "fee"
    )

    if (
        not _strict_u64(fee)
        or fee
        != expected_fee_lamports
    ):
        raise ValueError(
            "FILL_FEE_MISMATCH"
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
        or len(pre_balances)
        != expected_account_count
        or len(post_balances)
        != expected_account_count
    ):
        raise ValueError(
            "FILL_LAMPORT_BALANCES_INVALID"
        )

    if (
        any(
            not _strict_u64(value)
            for value
            in pre_balances
        )
        or any(
            not _strict_u64(value)
            for value
            in post_balances
        )
    ):
        raise ValueError(
            "FILL_LAMPORT_BALANCES_INVALID"
        )

    if (
        pre_balances[0]
        != expected_payer_pre_balance
        or post_balances[0]
        != expected_payer_post_balance
        or (
            post_balances[0]
            - pre_balances[0]
        )
        != expected_payer_delta
    ):
        raise ValueError(
            "FILL_PAYER_BALANCE_BINDING_MISMATCH"
        )

    return (
        meta,
        observed_sha256,
    )


async def resolve_successful_pump_buy_fill(
    *,
    reservation_id: str,
    db_path: Path = DB_PATH,
) -> SuccessfulPumpBuyFillResult:
    """
    Prove the exact token fill produced by one
    successful persisted Pump buy_v2 transaction.

    This resolver is observational only.

    It has:
    - no database mutation authority
    - no reservation release authority
    - no position authority
    - no signing authority
    - no send authority

    PROVEN requires agreement between:
    - the exact persisted signed transaction,
    - Receipt Resolver v1,
    - the compiled Pump buy_v2 instruction,
    - the Pump TradeEvent,
    - the exact user base-token balance delta.
    """

    transaction_signature: (
        str | None
    ) = None

    receipt_status: str | None = None
    receipt_slot: int | None = None
    block_time: int | None = None

    mint: str | None = None
    wallet_pubkey: str | None = None

    base_token_program: (
        str | None
    ) = None

    associated_base_user: (
        str | None
    ) = None

    authorized_token_amount: (
        int | None
    ) = None

    max_sol_cost: int | None = None

    trade_event_token_amount: (
        int | None
    ) = None

    token_pre_amount: int | None = None
    token_post_amount: int | None = None
    token_delta: int | None = None

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

    wallet_cost_lamports: (
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

    quote_mint: str | None = None
    quote_amount: int | None = None

    persisted_transaction_sha256: (
        str | None
    ) = None

    observed_transaction_sha256: (
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
    ) -> SuccessfulPumpBuyFillResult:
        return SuccessfulPumpBuyFillResult(
            resolver_version=(
                SUCCESSFUL_PUMP_BUY_FILL_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            reservation_id=reservation_id,
            transaction_signature=(
                transaction_signature
            ),
            receipt_status=receipt_status,
            receipt_slot=receipt_slot,
            block_time=block_time,
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
            authorized_token_amount=(
                authorized_token_amount
            ),
            max_sol_cost=(
                max_sol_cost
            ),
            trade_event_token_amount=(
                trade_event_token_amount
            ),
            token_pre_amount=(
                token_pre_amount
            ),
            token_post_amount=(
                token_post_amount
            ),
            token_delta=token_delta,
            fee_lamports=fee_lamports,
            wallet_pre_balance_lamports=(
                wallet_pre_balance_lamports
            ),
            wallet_post_balance_lamports=(
                wallet_post_balance_lamports
            ),
            wallet_balance_delta_lamports=(
                wallet_balance_delta_lamports
            ),
            wallet_cost_lamports=(
                wallet_cost_lamports
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
            quote_mint=quote_mint,
            quote_amount=quote_amount,
            persisted_transaction_sha256=(
                persisted_transaction_sha256
            ),
            observed_transaction_sha256=(
                observed_transaction_sha256
            ),
        )

    if not reservation_id:
        return finish(
            UNKNOWN,
            "INVALID_RESERVATION_ID",
        )

    #
    # Snapshot local signed authority.
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

    mint = initial.mint
    wallet_pubkey = (
        initial.wallet_pubkey
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

    if initial.side != "BUY":
        return finish(
            BLOCK,
            "RESERVATION_NOT_BUY",
        )

    if (
        not _strict_u64(
            initial.spend_lamports
        )
        or initial.spend_lamports <= 0
    ):
        return finish(
            UNKNOWN,
            "RESERVATION_SPEND_INVALID",
        )

    if (
        not _strict_u64(
            initial.wallet_cost_lamports
        )
        or initial.wallet_cost_lamports
        <= 0
    ):
        return finish(
            UNKNOWN,
            "RESERVATION_WALLET_COST_INVALID",
        )

    initial_identity = (
        _artifact_identity(
            initial
        )
    )

    #
    # First prove that this exact transaction landed
    # successfully.
    #
    try:
        receipt = (
            await resolve_signed_transaction_receipt(
                reservation_id=reservation_id,
                db_path=db_path,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            "RECEIPT_RESOLUTION_FAILED",
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
            "RECEIPT_UNKNOWN",
            *receipt.reasons,
        )

    if (
        receipt.status
        == RECEIPT_BLOCK
    ):
        return finish(
            BLOCK,
            "TRANSACTION_NOT_READY_FOR_FILL_RESOLUTION",
            *receipt.reasons,
        )

    if (
        receipt.status
        != RECEIPT_RESOLVED
    ):
        return finish(
            UNKNOWN,
            "UNEXPECTED_RECEIPT_STATUS",
        )

    if (
        receipt.resolver_version
        != SIGNED_TRANSACTION_RECEIPT_VERSION
    ):
        return finish(
            UNKNOWN,
            "RECEIPT_VERSION_MISMATCH",
        )

    if (
        receipt.transaction_error
        is not None
    ):
        return finish(
            BLOCK,
            "TRANSACTION_FAILED_ON_CHAIN",
        )

    if (
        receipt.reservation_id
        != reservation_id
        or receipt.transaction_signature
        != initial.transaction_signature
        or receipt.persisted_transaction_sha256
        != initial.signed_transaction_sha256
        or receipt.receipt_transaction_sha256
        != initial.signed_transaction_sha256
        or receipt.fee_payer_pubkey
        != initial.wallet_pubkey
        or not _strict_u64(
            receipt.receipt_slot
        )
        or not _strict_u64(
            receipt.fee_lamports
        )
        or not _strict_u64(
            receipt
            .fee_payer_pre_balance_lamports
        )
        or not _strict_u64(
            receipt
            .fee_payer_post_balance_lamports
        )
        or not isinstance(
            receipt
            .fee_payer_balance_delta_lamports,
            int,
        )
        or isinstance(
            receipt
            .fee_payer_balance_delta_lamports,
            bool,
        )
        or (
            receipt
            .fee_payer_post_balance_lamports
            - receipt
            .fee_payer_pre_balance_lamports
        )
        != receipt
        .fee_payer_balance_delta_lamports
    ):
        return finish(
            UNKNOWN,
            "SUCCESS_RECEIPT_BINDING_MISMATCH",
        )

    if (
        receipt
        .fee_payer_balance_delta_lamports
        >= 0
    ):
        return finish(
            UNKNOWN,
            "SUCCESS_WALLET_COST_INVALID",
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

    wallet_cost_lamports = (
        -wallet_balance_delta_lamports
    )

    reserved_wallet_cost = getattr(
        initial,
        "wallet_cost_lamports",
        None,
    )

    if (
        not _strict_u64(
            reserved_wallet_cost
        )
        or reserved_wallet_cost <= 0
    ):
        return finish(
            UNKNOWN,
            "RESERVATION_WALLET_COST_INVALID",
        )

    if (
        wallet_cost_lamports
        > reserved_wallet_cost
    ):
        return finish(
            UNKNOWN,
            "SUCCESS_WALLET_COST_EXCEEDS_RESERVATION",
        )

    #
    # Re-read before interpreting the persisted
    # transaction as fill authority.
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
        current.status
        not in (
            SIGNED,
            SUBMITTED,
        )
        or _artifact_identity(
            current
        )
        != initial_identity
    ):
        return finish(
            UNKNOWN,
            "RESERVATION_CHANGED_DURING_RECEIPT_CHECK",
        )

    try:
        authorized = (
            _decode_authorized_buy(
                current
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

    authorized_token_amount = (
        authorized["amount"]
    )

    max_sol_cost = (
        authorized[
            "max_sol_cost"
        ]
    )

    #
    # One additional read-only transaction fetch is
    # used to obtain logs and token-balance evidence.
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
            "FILL_RPC_FAILED",
        )

    if response is None:
        return finish(
            UNKNOWN,
            "SUCCESSFUL_TRANSACTION_METADATA_NOT_FOUND",
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
                current
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

        _validate_trade_event(
            event,
            mint=current.mint,
            wallet_pubkey=(
                current.wallet_pubkey
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

        account_index = (
            authorized[
                "token_account_index"
            ]
        )

        pre_balances = meta[
            "preBalances"
        ]

        pre_token_amount = (
            _token_balance_for_account(
                meta.get(
                    "preTokenBalances"
                ),
                account_index=(
                    account_index
                ),
                mint=current.mint,
                owner=(
                    current.wallet_pubkey
                ),
                token_program=(
                    authorized[
                        "base_token_program"
                    ]
                ),
            )
        )

        post_token_amount = (
            _token_balance_for_account(
                meta.get(
                    "postTokenBalances"
                ),
                account_index=(
                    account_index
                ),
                mint=current.mint,
                owner=(
                    current.wallet_pubkey
                ),
                token_program=(
                    authorized[
                        "base_token_program"
                    ]
                ),
            )
        )

        if post_token_amount is None:
            raise ValueError(
                "POST_TOKEN_BALANCE_MISSING"
            )

        if pre_token_amount is None:
            #
            # A missing preTokenBalances entry is
            # authoritative zero only when the exact
            # ATA had zero pre-transaction lamports,
            # proving it did not yet exist.
            #
            if (
                pre_balances[
                    account_index
                ]
                != 0
            ):
                raise ValueError(
                    "PRE_TOKEN_BALANCE_MISSING_FOR_EXISTING_ACCOUNT"
                )

            pre_token_amount = 0

        token_change = (
            post_token_amount
            - pre_token_amount
        )

        if token_change <= 0:
            raise ValueError(
                "TOKEN_FILL_DELTA_INVALID"
            )

        if (
            token_change
            != authorized[
                "amount"
            ]
        ):
            raise ValueError(
                "TOKEN_FILL_AMOUNT_MISMATCH"
            )

    except ValueError as error:
        return finish(
            UNKNOWN,
            str(error),
        )

    trade_event_token_amount = (
        event["token_amount"]
    )

    token_pre_amount = (
        pre_token_amount
    )

    token_post_amount = (
        post_token_amount
    )

    token_delta = (
        token_change
    )

    trade_event_sol_amount = (
        event["sol_amount"]
    )

    protocol_fee_lamports = (
        event["fee"]
    )

    creator_fee_lamports = (
        event["creator_fee"]
    )

    cashback_lamports = (
        event["cashback"]
    )

    buyback_fee_lamports = (
        event["buyback_fee"]
    )

    quote_mint = (
        event["quote_mint"]
    )

    quote_amount = (
        event["quote_amount"]
    )

    #
    # Final local identity check. This resolver
    # never mutates the reservation.
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
            "RESERVATION_DISAPPEARED_AFTER_FILL_CHECK",
        )

    if (
        final.status
        not in (
            SIGNED,
            SUBMITTED,
        )
        or _artifact_identity(
            final
        )
        != initial_identity
    ):
        return finish(
            UNKNOWN,
            "RESERVATION_CHANGED_DURING_FILL_CHECK",
        )

    return finish(
        PROVEN,
    )
