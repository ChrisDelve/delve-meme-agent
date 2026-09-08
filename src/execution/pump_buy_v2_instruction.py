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
from src.execution.pump_buy_v2_account_context import (
    BUY_V2_ACCOUNT_NAMES,
    PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION,
    PumpBuyV2AccountContext,
)
from src.safety.token_safety_resolver import (
    ASSOCIATED_TOKEN_PROGRAM,
    PUMP_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
)


PUMP_BUY_V2_INSTRUCTION_VERSION = (
    "pump-buy-v2-instruction-v1"
)

BUY_V2_DISCRIMINATOR = bytes(
    [
        184,
        23,
        238,
        97,
        103,
        197,
        211,
        61,
    ]
)

U64_MAX = (1 << 64) - 1


#
# Exact current Pump buy_v2 AccountMeta contract.
#
# Tuple fields:
#   account name
#   is_signer
#   is_writable
#
BUY_V2_ACCOUNT_META_SPEC = (
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
        "global_volume_accumulator",
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


class PumpBuyV2InstructionError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class PumpBuyV2InstructionPlan:
    builder_version: str

    account_context_version: str

    reservation_id: str
    simulation_sha256: str

    amount: int
    max_sol_cost: int

    instruction_data_hex: str
    instruction_sha256: str

    instruction: Instruction


def _required_pubkey(
    value: str,
    *,
    label: str,
) -> Pubkey:

    try:
        return Pubkey.from_string(
            value
        )

    except Exception as error:
        raise PumpBuyV2InstructionError(
            f"{label} is not a valid "
            "Solana public key."
        ) from error


def _validate_u64(
    value: object,
    *,
    label: str,
) -> int:

    if (
        not isinstance(
            value,
            int,
        )
        or isinstance(
            value,
            bool,
        )
        or value <= 0
        or value > U64_MAX
    ):
        raise PumpBuyV2InstructionError(
            f"{label} is outside the "
            "valid positive u64 range."
        )

    return value


def _instruction_fingerprint(
    *,
    program_id: Pubkey,
    accounts: tuple[
        AccountMeta,
        ...
    ],
    data: bytes,
) -> str:

    payload = bytearray()

    payload.extend(
        bytes(
            program_id
        )
    )

    payload.extend(
        data
    )

    for account in accounts:
        payload.extend(
            bytes(
                account.pubkey
            )
        )

        payload.extend(
            bytes(
                [
                    1
                    if account.is_signer
                    else 0,
                    1
                    if account.is_writable
                    else 0,
                ]
            )
        )

    return hashlib.sha256(
        bytes(
            payload
        )
    ).hexdigest()


def build_pump_buy_v2_instruction(
    *,
    context: PumpBuyV2AccountContext,
) -> PumpBuyV2InstructionPlan:

    #
    # Contract version.
    #
    if (
        context.resolver_version
        != PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION
    ):
        raise PumpBuyV2InstructionError(
            "Pump buy_v2 account-context "
            "version is unsupported."
        )

    if not context.reservation_id:
        raise PumpBuyV2InstructionError(
            "Reservation identity is missing."
        )

    if (
        not isinstance(
            context.simulation_sha256,
            str,
        )
        or len(
            context.simulation_sha256
        ) != 64
    ):
        raise PumpBuyV2InstructionError(
            "Simulation fingerprint "
            "is malformed."
        )

    try:
        bytes.fromhex(
            context.simulation_sha256
        )

    except ValueError as error:
        raise PumpBuyV2InstructionError(
            "Simulation fingerprint "
            "is not hexadecimal."
        ) from error

    amount = _validate_u64(
        context.amount,
        label="amount",
    )

    max_sol_cost = _validate_u64(
        context.max_sol_cost,
        label="max_sol_cost",
    )

    #
    # Static protocol invariants.
    #
    if context.program != str(
        PUMP_PROGRAM
    ):
        raise PumpBuyV2InstructionError(
            "Pump program identity mismatch."
        )

    if context.system_program != str(
        Pubkey.default()
    ):
        raise PumpBuyV2InstructionError(
            "System program identity mismatch."
        )

    if (
        context.associated_token_program
        != str(
            ASSOCIATED_TOKEN_PROGRAM
        )
    ):
        raise PumpBuyV2InstructionError(
            "Associated Token Program "
            "identity mismatch."
        )

    if context.fee_program != str(
        PUMP_FEE_PROGRAM
    ):
        raise PumpBuyV2InstructionError(
            "Pump Fee Program "
            "identity mismatch."
        )

    if context.quote_mint != (
        WRAPPED_SOL_MINT
    ):
        raise PumpBuyV2InstructionError(
            "Only the authorized SOL quote "
            "mint is supported."
        )

    if (
        context.quote_token_program
        != str(
            TOKEN_PROGRAM
        )
    ):
        raise PumpBuyV2InstructionError(
            "SOL quote token-program "
            "identity mismatch."
        )

    if context.base_token_program not in (
        str(
            TOKEN_PROGRAM
        ),
        str(
            TOKEN_2022_PROGRAM
        ),
    ):
        raise PumpBuyV2InstructionError(
            "Base token program "
            "is unsupported."
        )

    #
    # Account order must remain exactly aligned with
    # the current Pump IDL.
    #
    named_accounts = (
        context.ordered_named_accounts()
    )

    if len(
        named_accounts
    ) != 27:
        raise PumpBuyV2InstructionError(
            "Pump buy_v2 requires "
            "exactly 27 accounts."
        )

    context_names = tuple(
        name
        for name, _
        in named_accounts
    )

    spec_names = tuple(
        name
        for name, _, _
        in BUY_V2_ACCOUNT_META_SPEC
    )

    if (
        context_names
        != BUY_V2_ACCOUNT_NAMES
        or context_names
        != spec_names
    ):
        raise PumpBuyV2InstructionError(
            "Pump buy_v2 account order "
            "does not match the IDL contract."
        )

    account_metas: list[
        AccountMeta
    ] = []

    for (
        (
            context_name,
            address,
        ),
        (
            spec_name,
            is_signer,
            is_writable,
        ),
    ) in zip(
        named_accounts,
        BUY_V2_ACCOUNT_META_SPEC,
        strict=True,
    ):
        if context_name != spec_name:
            raise PumpBuyV2InstructionError(
                "Pump buy_v2 account-name "
                "alignment failed."
            )

        pubkey = _required_pubkey(
            address,
            label=context_name,
        )

        account_metas.append(
            AccountMeta(
                pubkey,
                is_signer,
                is_writable,
            )
        )

    accounts = tuple(
        account_metas
    )

    #
    # Anchor instruction payload:
    #
    #   discriminator   [8]
    #   amount          u64 LE
    #   max_sol_cost    u64 LE
    #
    data = (
        BUY_V2_DISCRIMINATOR
        + struct.pack(
            "<QQ",
            amount,
            max_sol_cost,
        )
    )

    if len(data) != 24:
        raise PumpBuyV2InstructionError(
            "Pump buy_v2 instruction "
            "payload length is invalid."
        )

    program_id = Pubkey.from_string(
        context.program
    )

    instruction = Instruction(
        program_id,
        data,
        list(
            accounts
        ),
    )

    fingerprint = (
        _instruction_fingerprint(
            program_id=program_id,
            accounts=accounts,
            data=data,
        )
    )

    return PumpBuyV2InstructionPlan(
        builder_version=(
            PUMP_BUY_V2_INSTRUCTION_VERSION
        ),
        account_context_version=(
            context.resolver_version
        ),
        reservation_id=(
            context.reservation_id
        ),
        simulation_sha256=(
            context.simulation_sha256
        ),
        amount=amount,
        max_sol_cost=max_sol_cost,
        instruction_data_hex=(
            data.hex()
        ),
        instruction_sha256=(
            fingerprint
        ),
        instruction=instruction,
    )
