from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from solders.instruction import (
    AccountMeta,
    Instruction,
)
from solders.pubkey import Pubkey

from src.execution.live_pump_fee_state import (
    PUMP_FEE_PROGRAM,
)
from src.execution.order_authorization import (
    WRAPPED_SOL_MINT,
)
from src.execution.pump_sell_v2_account_context import (
    PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION,
    SELL_V2_ACCOUNT_NAMES,
    PumpSellV2AccountContext,
)
from src.safety.token_safety_resolver import (
    ASSOCIATED_TOKEN_PROGRAM,
    PUMP_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
)


PUMP_SELL_V2_INSTRUCTION_VERSION = (
    "pump-sell-v2-instruction-v1"
)

SELL_V2_DISCRIMINATOR = bytes(
    [
        93,
        246,
        130,
        60,
        231,
        233,
        64,
        178,
    ]
)

U64_MAX = (1 << 64) - 1


#
# Exact current Pump sell_v2 AccountMeta contract
# from the official public IDL.
#
# Tuple fields:
#   account name
#   is_signer
#   is_writable
#
SELL_V2_ACCOUNT_META_SPEC = (
    ("global", False, False),
    ("base_mint", False, False),
    ("quote_mint", False, False),
    (
        "base_token_program",
        False,
        False,
    ),
    (
        "quote_token_program",
        False,
        False,
    ),
    (
        "associated_token_program",
        False,
        False,
    ),
    (
        "fee_recipient",
        False,
        True,
    ),
    (
        "associated_quote_fee_recipient",
        False,
        True,
    ),
    (
        "buyback_fee_recipient",
        False,
        True,
    ),
    (
        "associated_quote_buyback_fee_recipient",
        False,
        True,
    ),
    (
        "bonding_curve",
        False,
        True,
    ),
    (
        "associated_base_bonding_curve",
        False,
        True,
    ),
    (
        "associated_quote_bonding_curve",
        False,
        True,
    ),
    (
        "user",
        True,
        True,
    ),
    (
        "associated_base_user",
        False,
        True,
    ),
    (
        "associated_quote_user",
        False,
        True,
    ),
    (
        "creator_vault",
        False,
        True,
    ),
    (
        "associated_creator_vault",
        False,
        True,
    ),
    (
        "sharing_config",
        False,
        False,
    ),
    (
        "user_volume_accumulator",
        False,
        True,
    ),
    (
        "associated_user_volume_accumulator",
        False,
        True,
    ),
    (
        "fee_config",
        False,
        False,
    ),
    (
        "fee_program",
        False,
        False,
    ),
    (
        "system_program",
        False,
        False,
    ),
    (
        "event_authority",
        False,
        False,
    ),
    (
        "program",
        False,
        False,
    ),
)


class PumpSellV2InstructionError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class PumpSellV2Instruction:
    builder_version: str

    context_version: str
    authorization_version: str
    authorization_sha256: str

    instruction: Instruction

    instruction_data_hex: str
    instruction_sha256: str


def _strict_u64(
    value: object,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= U64_MAX
    )


def _strict_positive_u64(
    value: object,
) -> bool:
    return (
        _strict_u64(value)
        and value > 0
    )


def _required_pubkey(
    value: str,
    *,
    label: str,
    allow_default: bool = False,
) -> Pubkey:
    if not isinstance(
        value,
        str,
    ):
        raise PumpSellV2InstructionError(
            f"{label} is invalid."
        )

    try:
        pubkey = Pubkey.from_string(
            value
        )

    except Exception as error:
        raise PumpSellV2InstructionError(
            f"{label} is not a valid "
            "Solana public key."
        ) from error

    if (
        not allow_default
        and pubkey == Pubkey.default()
    ):
        raise PumpSellV2InstructionError(
            f"{label} cannot be the "
            "default public key."
        )

    return pubkey


def _fingerprint_instruction(
    *,
    program_id: Pubkey,
    accounts: tuple[
        AccountMeta,
        ...
    ],
    data: bytes,
) -> str:
    digest = hashlib.sha256()

    digest.update(
        PUMP_SELL_V2_INSTRUCTION_VERSION.encode(
            "utf-8"
        )
    )

    digest.update(
        b"\x00"
    )

    digest.update(
        bytes(
            program_id
        )
    )

    digest.update(
        len(
            accounts
        ).to_bytes(
            2,
            byteorder="little",
            signed=False,
        )
    )

    for meta in accounts:
        digest.update(
            bytes(
                meta.pubkey
            )
        )

        digest.update(
            bytes(
                (
                    1
                    if meta.is_signer
                    else 0,
                    1
                    if meta.is_writable
                    else 0,
                )
            )
        )

    digest.update(
        len(
            data
        ).to_bytes(
            4,
            byteorder="little",
            signed=False,
        )
    )

    digest.update(
        data
    )

    return digest.hexdigest()


def build_pump_sell_v2_instruction(
    *,
    context: PumpSellV2AccountContext,
) -> PumpSellV2Instruction:
    """
    Build the exact current Pump sell_v2
    instruction from an already-authorized
    account context.

    No RPC, signing, submission, database
    mutation, or position mutation occurs here.
    """

    if not isinstance(
        context,
        PumpSellV2AccountContext,
    ):
        raise PumpSellV2InstructionError(
            "SELL account context type is invalid."
        )

    if (
        context.resolver_version
        != PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION
    ):
        raise PumpSellV2InstructionError(
            "SELL account context version "
            "is unsupported."
        )

    if (
        len(
            SELL_V2_ACCOUNT_NAMES
        )
        != 26
        or len(
            SELL_V2_ACCOUNT_META_SPEC
        )
        != 26
    ):
        raise PumpSellV2InstructionError(
            "SELL v2 account contract "
            "must contain exactly 26 accounts."
        )

    meta_names = tuple(
        name
        for (
            name,
            _,
            _,
        )
        in SELL_V2_ACCOUNT_META_SPEC
    )

    if (
        meta_names
        != SELL_V2_ACCOUNT_NAMES
    ):
        raise PumpSellV2InstructionError(
            "SELL v2 AccountMeta contract "
            "does not match context order."
        )

    if not _strict_positive_u64(
        context.amount
    ):
        raise PumpSellV2InstructionError(
            "SELL amount is invalid."
        )

    if not _strict_u64(
        context.min_sol_output
    ):
        raise PumpSellV2InstructionError(
            "SELL minimum SOL output "
            "is invalid."
        )

    if (
        context.quote_mint
        != WRAPPED_SOL_MINT
    ):
        raise PumpSellV2InstructionError(
            "SELL quote mint is not "
            "wrapped SOL."
        )

    if (
        context.base_token_program
        not in (
            str(
                TOKEN_PROGRAM
            ),
            str(
                TOKEN_2022_PROGRAM
            ),
        )
    ):
        raise PumpSellV2InstructionError(
            "SELL base token program "
            "is unsupported."
        )

    if (
        context.quote_token_program
        != str(
            TOKEN_PROGRAM
        )
    ):
        raise PumpSellV2InstructionError(
            "SELL quote token program "
            "is invalid."
        )

    if (
        context.associated_token_program
        != str(
            ASSOCIATED_TOKEN_PROGRAM
        )
    ):
        raise PumpSellV2InstructionError(
            "SELL associated token program "
            "is invalid."
        )

    if (
        context.fee_program
        != str(
            PUMP_FEE_PROGRAM
        )
    ):
        raise PumpSellV2InstructionError(
            "SELL fee program is invalid."
        )

    if (
        context.system_program
        != str(
            Pubkey.default()
        )
    ):
        raise PumpSellV2InstructionError(
            "SELL system program is invalid."
        )

    if (
        context.program
        != str(
            PUMP_PROGRAM
        )
    ):
        raise PumpSellV2InstructionError(
            "SELL Pump program is invalid."
        )

    ordered_accounts = (
        context.ordered_accounts()
    )

    if len(
        ordered_accounts
    ) != 26:
        raise PumpSellV2InstructionError(
            "SELL account context does "
            "not contain 26 accounts."
        )

    account_metas = []

    for (
        account_value,
        (
            account_name,
            is_signer,
            is_writable,
        ),
    ) in zip(
        ordered_accounts,
        SELL_V2_ACCOUNT_META_SPEC,
        strict=True,
    ):
        allow_default = (
            account_name
            == "system_program"
        )

        pubkey = _required_pubkey(
            account_value,
            label=account_name,
            allow_default=(
                allow_default
            ),
        )

        account_metas.append(
            AccountMeta(
                pubkey=pubkey,
                is_signer=is_signer,
                is_writable=is_writable,
            )
        )

    account_metas_tuple = tuple(
        account_metas
    )

    instruction_data = (
        SELL_V2_DISCRIMINATOR
        + struct.pack(
            "<QQ",
            context.amount,
            context.min_sol_output,
        )
    )

    if len(
        instruction_data
    ) != 24:
        raise PumpSellV2InstructionError(
            "SELL v2 instruction data "
            "must be exactly 24 bytes."
        )

    instruction = Instruction(
        program_id=PUMP_PROGRAM,
        data=instruction_data,
        accounts=list(
            account_metas_tuple
        ),
    )

    instruction_sha256 = (
        _fingerprint_instruction(
            program_id=PUMP_PROGRAM,
            accounts=(
                account_metas_tuple
            ),
            data=instruction_data,
        )
    )

    return PumpSellV2Instruction(
        builder_version=(
            PUMP_SELL_V2_INSTRUCTION_VERSION
        ),
        context_version=(
            context.resolver_version
        ),
        authorization_version=(
            context.authorization_version
        ),
        authorization_sha256=(
            context.authorization_sha256
        ),
        instruction=instruction,
        instruction_data_hex=(
            instruction_data.hex()
        ),
        instruction_sha256=(
            instruction_sha256
        ),
    )
