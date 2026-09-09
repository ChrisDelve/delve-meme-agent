from __future__ import annotations

import hashlib
from dataclasses import dataclass

from solders.compute_budget import (
    ID as COMPUTE_BUDGET_PROGRAM,
    set_compute_unit_limit,
    set_compute_unit_price,
)
from solders.hash import Hash
from solders.message import (
    MessageV0,
    to_bytes_versioned,
)
from solders.pubkey import Pubkey

from src.execution.live_blockhash_context import (
    LIVE_BLOCKHASH_CONTEXT_VERSION,
    LiveBlockhashContext,
)
from src.execution.live_pump_sell_authorization import (
    LIVE_PUMP_SELL_AUTHORIZATION_VERSION,
    LivePumpSellAuthorization,
)
from src.execution.pump_sell_v2_account_context import (
    PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION,
    PumpSellV2AccountContext,
)
from src.execution.pump_sell_v2_instruction import (
    PUMP_SELL_V2_INSTRUCTION_VERSION,
    build_pump_sell_v2_instruction,
)
from src.safety.token_safety_resolver import (
    COMMITMENT,
)


PUMP_SELL_V2_UNSIGNED_MESSAGE_VERSION = (
    "pump-sell-v2-unsigned-message-v1"
)

MICRO_LAMPORTS_PER_LAMPORT = 1_000_000

MAX_COMPUTE_UNIT_LIMIT = 1_400_000

SOLANA_PACKET_DATA_SIZE = 1_232

U64_MAX = (1 << 64) - 1


class PumpSellV2UnsignedMessageError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class PumpSellV2UnsignedMessagePlan:
    builder_version: str

    authorization_version: str
    authorization_sha256: str

    account_context_version: str
    instruction_builder_version: str

    payer: str

    authorization_fee_rpc_slot: int
    account_context_global_rpc_slot: int

    blockhash_context_version: str
    recent_blockhash: str
    last_valid_block_height: int
    blockhash_rpc_slot: int
    blockhash_min_context_slot: int

    compute_unit_limit: int
    compute_unit_price_micro_lamports: int

    authorized_base_network_fee_lamports: int
    authorized_priority_fee_lamports: int
    planned_priority_fee_lamports: int

    pump_instruction_sha256: str

    message_sha256: str

    estimated_signed_transaction_size_bytes: int

    message: MessageV0


def _required_pubkey(
    value: str | None,
    *,
    label: str,
) -> Pubkey:
    if not value:
        raise PumpSellV2UnsignedMessageError(
            f"{label} is missing."
        )

    try:
        result = Pubkey.from_string(
            value
        )

    except Exception as error:
        raise PumpSellV2UnsignedMessageError(
            f"{label} is not a valid "
            "Solana public key."
        ) from error

    if result == Pubkey.default():
        raise PumpSellV2UnsignedMessageError(
            f"{label} cannot be the "
            "default public key."
        )

    return result


def _required_sha256(
    value: object,
    *,
    label: str,
) -> str:
    if (
        not isinstance(
            value,
            str,
        )
        or len(value) != 64
    ):
        raise PumpSellV2UnsignedMessageError(
            f"{label} is invalid."
        )

    try:
        bytes.fromhex(
            value
        )

    except ValueError as error:
        raise PumpSellV2UnsignedMessageError(
            f"{label} is invalid."
        ) from error

    return value.lower()


def _required_nonnegative_u64(
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
        or value < 0
        or value > U64_MAX
    ):
        raise PumpSellV2UnsignedMessageError(
            f"{label} is outside the valid "
            "non-negative u64 range."
        )

    return value


def _validate_compute_unit_limit(
    value: object,
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
        or value > MAX_COMPUTE_UNIT_LIMIT
    ):
        raise PumpSellV2UnsignedMessageError(
            "Compute-unit limit must be "
            "between 1 and 1,400,000."
        )

    return value


def _priority_fee_lamports(
    *,
    compute_unit_limit: int,
    compute_unit_price_micro_lamports: int,
) -> int:
    numerator = (
        compute_unit_limit
        * compute_unit_price_micro_lamports
    )

    return (
        numerator
        + MICRO_LAMPORTS_PER_LAMPORT
        - 1
    ) // MICRO_LAMPORTS_PER_LAMPORT


def _price_for_authorized_priority_fee(
    *,
    authorized_priority_fee_lamports: int,
    compute_unit_limit: int,
) -> tuple[
    int,
    int,
]:
    if authorized_priority_fee_lamports == 0:
        return (
            0,
            0,
        )

    price = (
        authorized_priority_fee_lamports
        * MICRO_LAMPORTS_PER_LAMPORT
    ) // compute_unit_limit

    price = min(
        price,
        U64_MAX,
    )

    planned_fee = (
        _priority_fee_lamports(
            compute_unit_limit=(
                compute_unit_limit
            ),
            compute_unit_price_micro_lamports=(
                price
            ),
        )
    )

    if (
        planned_fee
        != authorized_priority_fee_lamports
    ):
        raise PumpSellV2UnsignedMessageError(
            "Compute-unit limit cannot "
            "represent the exact authorized "
            "priority fee."
        )

    return (
        price,
        planned_fee,
    )


def build_unsigned_pump_sell_v2_message(
    *,
    authorization: LivePumpSellAuthorization,
    context: PumpSellV2AccountContext,
    blockhash_context: LiveBlockhashContext,
    compute_unit_limit: int,
) -> PumpSellV2UnsignedMessagePlan:
    """
    Build one exact unsigned Pump sell_v2 MessageV0.

    The message contains exactly:
      1. Compute-unit limit
      2. Compute-unit price
      3. Pump sell_v2

    No RPC, simulation, signing, submission,
    database mutation, or position mutation occurs.
    """

    # --------------------------------------------------------
    # Authorized SELL authority.
    # --------------------------------------------------------

    if not isinstance(
        authorization,
        LivePumpSellAuthorization,
    ):
        raise PumpSellV2UnsignedMessageError(
            "SELL authorization type is invalid."
        )

    if (
        authorization.authorization_version
        != LIVE_PUMP_SELL_AUTHORIZATION_VERSION
    ):
        raise PumpSellV2UnsignedMessageError(
            "SELL authorization version "
            "is unsupported."
        )

    authorization_sha256 = (
        _required_sha256(
            authorization.authorization_sha256,
            label=(
                "SELL authorization fingerprint"
            ),
        )
    )

    payer = _required_pubkey(
        authorization.wallet_pubkey,
        label="authorized payer",
    )

    authorization_fee_rpc_slot = (
        _required_nonnegative_u64(
            authorization.fee_rpc_slot,
            label=(
                "authorization fee RPC slot"
            ),
        )
    )

    base_network_fee = (
        _required_nonnegative_u64(
            authorization
            .base_network_fee_lamports,
            label="base network fee",
        )
    )

    priority_fee_budget = (
        _required_nonnegative_u64(
            authorization
            .priority_fee_lamports,
            label="priority fee",
        )
    )

    if (
        base_network_fee
        + priority_fee_budget
        > U64_MAX
    ):
        raise PumpSellV2UnsignedMessageError(
            "Authorized network overhead "
            "overflows u64."
        )

    # --------------------------------------------------------
    # Context must be the exact downstream artifact
    # of this authorization.
    # --------------------------------------------------------

    if not isinstance(
        context,
        PumpSellV2AccountContext,
    ):
        raise PumpSellV2UnsignedMessageError(
            "SELL account context type is invalid."
        )

    if (
        context.resolver_version
        != PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION
    ):
        raise PumpSellV2UnsignedMessageError(
            "SELL account-context version "
            "is unsupported."
        )

    if (
        context.authorization_version
        != authorization.authorization_version
    ):
        raise PumpSellV2UnsignedMessageError(
            "Account-context authorization "
            "version mismatch."
        )

    context_authorization_sha256 = (
        _required_sha256(
            context.authorization_sha256,
            label=(
                "account-context authorization "
                "fingerprint"
            ),
        )
    )

    if (
        context_authorization_sha256
        != authorization_sha256
    ):
        raise PumpSellV2UnsignedMessageError(
            "Account-context authorization "
            "fingerprint mismatch."
        )

    if (
        context.authorization_fee_rpc_slot
        != authorization_fee_rpc_slot
    ):
        raise PumpSellV2UnsignedMessageError(
            "Account-context fee RPC slot "
            "does not match authorization."
        )

    context_global_rpc_slot = (
        _required_nonnegative_u64(
            context.global_rpc_slot,
            label=(
                "account-context Global RPC slot"
            ),
        )
    )

    if (
        context_global_rpc_slot
        < authorization_fee_rpc_slot
    ):
        raise PumpSellV2UnsignedMessageError(
            "Account-context Global state "
            "predates authorized SELL state."
        )

    if (
        context.user
        != authorization.wallet_pubkey
    ):
        raise PumpSellV2UnsignedMessageError(
            "Account-context user does not "
            "match authorized payer."
        )

    if (
        context.base_mint
        != authorization.mint
    ):
        raise PumpSellV2UnsignedMessageError(
            "Account-context mint does not "
            "match authorization."
        )

    if (
        context.quote_mint
        != authorization
        .quote_mint_for_instruction
    ):
        raise PumpSellV2UnsignedMessageError(
            "Account-context quote mint does "
            "not match authorization."
        )

    if (
        context.base_token_program
        != authorization.base_token_program
    ):
        raise PumpSellV2UnsignedMessageError(
            "Account-context base token "
            "program does not match "
            "authorization."
        )

    if (
        context.bonding_curve
        != authorization.bonding_curve
    ):
        raise PumpSellV2UnsignedMessageError(
            "Account-context bonding curve "
            "does not match authorization."
        )

    if (
        context.associated_base_user
        != authorization.associated_base_user
    ):
        raise PumpSellV2UnsignedMessageError(
            "Account-context user base ATA "
            "does not match authorization."
        )

    if (
        context.amount
        != authorization.tokens_to_sell
    ):
        raise PumpSellV2UnsignedMessageError(
            "Account-context SELL amount "
            "does not match authorization."
        )

    authorized_min_sol_output = getattr(
        authorization.exit_execution,
        "min_quote_out",
        None,
    )

    if (
        context.min_sol_output
        != authorized_min_sol_output
    ):
        raise PumpSellV2UnsignedMessageError(
            "Account-context minimum SOL "
            "output does not match "
            "authorization."
        )

    # --------------------------------------------------------
    # Rebuild exact Pump instruction mechanically.
    # Arbitrary caller-provided Instructions are never
    # accepted.
    # --------------------------------------------------------

    instruction_plan = (
        build_pump_sell_v2_instruction(
            context=context
        )
    )

    if (
        instruction_plan.builder_version
        != PUMP_SELL_V2_INSTRUCTION_VERSION
    ):
        raise PumpSellV2UnsignedMessageError(
            "Pump SELL instruction builder "
            "version is unsupported."
        )

    if (
        instruction_plan.context_version
        != context.resolver_version
        or instruction_plan.authorization_version
        != authorization.authorization_version
        or instruction_plan.authorization_sha256
        != authorization_sha256
    ):
        raise PumpSellV2UnsignedMessageError(
            "Pump SELL instruction authority "
            "binding mismatch."
        )

    pump_instruction = (
        instruction_plan.instruction
    )

    if (
        len(
            pump_instruction.accounts
        )
        != 26
    ):
        raise PumpSellV2UnsignedMessageError(
            "Pump SELL instruction does not "
            "contain exactly 26 accounts."
        )

    #
    # Pump IDL account 14 is user.
    # It must be the sole signer.
    #
    user_meta = (
        pump_instruction.accounts[
            13
        ]
    )

    if (
        user_meta.pubkey != payer
        or not user_meta.is_signer
        or not user_meta.is_writable
    ):
        raise PumpSellV2UnsignedMessageError(
            "Pump SELL instruction signer "
            "does not match authorized payer."
        )

    signer_pubkeys = tuple(
        meta.pubkey
        for meta
        in pump_instruction.accounts
        if meta.is_signer
    )

    if signer_pubkeys != (
        payer,
    ):
        raise PumpSellV2UnsignedMessageError(
            "Pump SELL instruction requires "
            "an unexpected signer set."
        )

    # --------------------------------------------------------
    # Compute-budget policy.
    # --------------------------------------------------------

    cu_limit = (
        _validate_compute_unit_limit(
            compute_unit_limit
        )
    )

    (
        cu_price,
        planned_priority_fee,
    ) = _price_for_authorized_priority_fee(
        authorized_priority_fee_lamports=(
            priority_fee_budget
        ),
        compute_unit_limit=cu_limit,
    )

    limit_instruction = (
        set_compute_unit_limit(
            cu_limit
        )
    )

    price_instruction = (
        set_compute_unit_price(
            cu_price
        )
    )

    # --------------------------------------------------------
    # Exact authoritative blockhash + expiry artifact.
    # --------------------------------------------------------

    if not isinstance(
        blockhash_context,
        LiveBlockhashContext,
    ):
        raise PumpSellV2UnsignedMessageError(
            "Live blockhash context is invalid."
        )

    if (
        blockhash_context.resolver_version
        != LIVE_BLOCKHASH_CONTEXT_VERSION
    ):
        raise PumpSellV2UnsignedMessageError(
            "Live blockhash context version "
            "is unsupported."
        )

    blockhash_rpc_slot = (
        _required_nonnegative_u64(
            blockhash_context.rpc_slot,
            label="blockhash RPC slot",
        )
    )

    blockhash_min_context_slot = (
        _required_nonnegative_u64(
            blockhash_context.min_context_slot,
            label=(
                "blockhash minimum context slot"
            ),
        )
    )

    last_valid_block_height = (
        _required_nonnegative_u64(
            blockhash_context
            .last_valid_block_height,
            label=(
                "last valid block height"
            ),
        )
    )

    if (
        blockhash_context.commitment
        != COMMITMENT
    ):
        raise PumpSellV2UnsignedMessageError(
            "Live blockhash commitment "
            "is unsupported."
        )

    if (
        blockhash_rpc_slot
        < blockhash_min_context_slot
    ):
        raise PumpSellV2UnsignedMessageError(
            "Live blockhash RPC context "
            "predates its minimum context slot."
        )

    #
    # SELL must not use a blockhash context whose
    # authority anchor predates any network state used
    # to construct the authorized transaction.
    #
    state_anchor_slot = max(
        authorization_fee_rpc_slot,
        context_global_rpc_slot,
    )

    if (
        blockhash_min_context_slot
        < state_anchor_slot
    ):
        raise PumpSellV2UnsignedMessageError(
            "Live blockhash minimum context "
            "slot predates SELL construction "
            "authority."
        )

    try:
        blockhash = Hash.from_string(
            blockhash_context.blockhash
        )

    except Exception as error:
        raise PumpSellV2UnsignedMessageError(
            "Recent blockhash is invalid."
        ) from error

    if blockhash == Hash.default():
        raise PumpSellV2UnsignedMessageError(
            "Default blockhash is not allowed "
            "for live SELL construction."
        )

    # --------------------------------------------------------
    # Pure MessageV0 compilation.
    # --------------------------------------------------------

    try:
        message = MessageV0.try_compile(
            payer,
            [
                limit_instruction,
                price_instruction,
                pump_instruction,
            ],
            [],
            blockhash,
        )

    except Exception as error:
        raise PumpSellV2UnsignedMessageError(
            "SELL MessageV0 compilation failed."
        ) from error

    # --------------------------------------------------------
    # Structural post-compile proof.
    # --------------------------------------------------------

    if (
        message.header.num_required_signatures
        != 1
    ):
        raise PumpSellV2UnsignedMessageError(
            "Compiled SELL message requires "
            "an unexpected number of "
            "signatures."
        )

    account_keys = tuple(
        message.account_keys
    )

    if (
        not account_keys
        or account_keys[
            0
        ] != payer
    ):
        raise PumpSellV2UnsignedMessageError(
            "Compiled SELL message fee payer "
            "is not the authorized wallet."
        )

    required_signers = account_keys[
        :message.header.num_required_signatures
    ]

    if required_signers != (
        payer,
    ):
        raise PumpSellV2UnsignedMessageError(
            "Compiled SELL message signer set "
            "does not equal authorized payer."
        )

    if len(
        message.address_table_lookups
    ) != 0:
        raise PumpSellV2UnsignedMessageError(
            "Address lookup tables are not "
            "authorized in SELL v1."
        )

    if (
        message.recent_blockhash
        != blockhash
    ):
        raise PumpSellV2UnsignedMessageError(
            "Compiled SELL blockhash mismatch."
        )

    compiled_instructions = tuple(
        message.instructions
    )

    if len(
        compiled_instructions
    ) != 3:
        raise PumpSellV2UnsignedMessageError(
            "Unsigned Pump SELL message must "
            "contain exactly 3 instructions."
        )

    #
    # Verify both Compute Budget instructions survived
    # compilation exactly.
    #
    for (
        compiled,
        expected,
    ) in zip(
        compiled_instructions[
            :2
        ],
        (
            limit_instruction,
            price_instruction,
        ),
        strict=True,
    ):
        program = account_keys[
            compiled.program_id_index
        ]

        if (
            program
            != COMPUTE_BUDGET_PROGRAM
        ):
            raise PumpSellV2UnsignedMessageError(
                "Compute Budget program "
                "identity mismatch."
            )

        if len(
            compiled.accounts
        ) != 0:
            raise PumpSellV2UnsignedMessageError(
                "Compute Budget instruction "
                "contains unexpected accounts."
            )

        if (
            bytes(
                compiled.data
            )
            != bytes(
                expected.data
            )
        ):
            raise PumpSellV2UnsignedMessageError(
                "Compute Budget instruction "
                "data changed during compile."
            )

    #
    # Verify exact Pump instruction survived compile:
    # program, data, and all 26 account identities.
    #
    compiled_pump = (
        compiled_instructions[
            2
        ]
    )

    if (
        account_keys[
            compiled_pump.program_id_index
        ]
        != pump_instruction.program_id
    ):
        raise PumpSellV2UnsignedMessageError(
            "Compiled Pump SELL program "
            "identity mismatch."
        )

    if (
        bytes(
            compiled_pump.data
        )
        != bytes(
            pump_instruction.data
        )
    ):
        raise PumpSellV2UnsignedMessageError(
            "Compiled Pump SELL instruction "
            "data mismatch."
        )

    compiled_pump_accounts = tuple(
        account_keys[
            index
        ]
        for index
        in compiled_pump.accounts
    )

    expected_pump_accounts = tuple(
        meta.pubkey
        for meta
        in pump_instruction.accounts
    )

    if (
        compiled_pump_accounts
        != expected_pump_accounts
    ):
        raise PumpSellV2UnsignedMessageError(
            "Compiled Pump SELL account order "
            "or identity mismatch."
        )

    # --------------------------------------------------------
    # Fingerprint actual serialized versioned message.
    # --------------------------------------------------------

    message_bytes = (
        to_bytes_versioned(
            message
        )
    )

    message_sha256 = hashlib.sha256(
        message_bytes
    ).hexdigest()

    #
    # Exactly one future Ed25519 signature:
    #   1 byte shortvec count + 64 bytes signature.
    #
    estimated_signed_size = (
        len(
            message_bytes
        )
        + 1
        + 64
    )

    if (
        estimated_signed_size
        > SOLANA_PACKET_DATA_SIZE
    ):
        raise PumpSellV2UnsignedMessageError(
            "Compiled SELL transaction would "
            "exceed Solana packet size."
        )

    return PumpSellV2UnsignedMessagePlan(
        builder_version=(
            PUMP_SELL_V2_UNSIGNED_MESSAGE_VERSION
        ),

        authorization_version=(
            authorization.authorization_version
        ),
        authorization_sha256=(
            authorization_sha256
        ),

        account_context_version=(
            context.resolver_version
        ),
        instruction_builder_version=(
            instruction_plan.builder_version
        ),

        payer=str(
            payer
        ),

        authorization_fee_rpc_slot=(
            authorization_fee_rpc_slot
        ),
        account_context_global_rpc_slot=(
            context_global_rpc_slot
        ),

        blockhash_context_version=(
            blockhash_context.resolver_version
        ),
        recent_blockhash=str(
            blockhash
        ),
        last_valid_block_height=(
            last_valid_block_height
        ),
        blockhash_rpc_slot=(
            blockhash_rpc_slot
        ),
        blockhash_min_context_slot=(
            blockhash_min_context_slot
        ),

        compute_unit_limit=(
            cu_limit
        ),
        compute_unit_price_micro_lamports=(
            cu_price
        ),

        authorized_base_network_fee_lamports=(
            base_network_fee
        ),
        authorized_priority_fee_lamports=(
            priority_fee_budget
        ),
        planned_priority_fee_lamports=(
            planned_priority_fee
        ),

        pump_instruction_sha256=(
            instruction_plan
            .instruction_sha256
        ),

        message_sha256=(
            message_sha256
        ),

        estimated_signed_transaction_size_bytes=(
            estimated_signed_size
        ),

        message=message,
    )
