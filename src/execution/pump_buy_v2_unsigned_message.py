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
from src.execution.order_authorization import (
    AUTHORIZATION_VERSION,
    AUTHORIZE,
    BUY,
    OrderAuthorization,
)
from src.execution.pump_buy_v2_account_context import (
    PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION,
    PumpBuyV2AccountContext,
)
from src.execution.pump_buy_v2_instruction import (
    PUMP_BUY_V2_INSTRUCTION_VERSION,
    build_pump_buy_v2_instruction,
)


PUMP_BUY_V2_UNSIGNED_MESSAGE_VERSION = (
    "pump-buy-v2-unsigned-message-v2"
)

MICRO_LAMPORTS_PER_LAMPORT = 1_000_000

MAX_COMPUTE_UNIT_LIMIT = 1_400_000

SOLANA_PACKET_DATA_SIZE = 1_232

U64_MAX = (1 << 64) - 1


class PumpBuyV2UnsignedMessageError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class PumpBuyV2UnsignedMessagePlan:
    builder_version: str

    authorization_version: str
    account_context_version: str
    instruction_builder_version: str

    reservation_id: str
    simulation_sha256: str

    payer: str

    blockhash_context_version: str
    recent_blockhash: str
    last_valid_block_height: int
    blockhash_rpc_slot: int

    compute_unit_limit: int
    compute_unit_price_micro_lamports: int

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
        raise PumpBuyV2UnsignedMessageError(
            f"{label} is missing."
        )

    try:
        result = Pubkey.from_string(
            value
        )

    except Exception as error:
        raise PumpBuyV2UnsignedMessageError(
            f"{label} is not a valid "
            "Solana public key."
        ) from error

    if result == Pubkey.default():
        raise PumpBuyV2UnsignedMessageError(
            f"{label} cannot be the "
            "default public key."
        )

    return result


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
        raise PumpBuyV2UnsignedMessageError(
            f"{label} is outside the "
            "valid non-negative u64 range."
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
        raise PumpBuyV2UnsignedMessageError(
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
        raise PumpBuyV2UnsignedMessageError(
            "Compute-unit limit cannot "
            "represent the exact authorized "
            "priority fee."
        )

    return (
        price,
        planned_fee,
    )


def build_unsigned_pump_buy_v2_message(
    *,
    authorization: OrderAuthorization,
    context: PumpBuyV2AccountContext,
    blockhash_context: LiveBlockhashContext,
    compute_unit_limit: int,
) -> PumpBuyV2UnsignedMessagePlan:

    #
    # Current authority boundary.
    #
    if (
        authorization.authorization_version
        != AUTHORIZATION_VERSION
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Authorization version "
            "is unsupported."
        )

    if authorization.status != AUTHORIZE:
        raise PumpBuyV2UnsignedMessageError(
            "Order is not authorized."
        )

    if authorization.side != BUY:
        raise PumpBuyV2UnsignedMessageError(
            "Only Pump BUY authorization "
            "is supported."
        )

    if not authorization.is_valid():
        raise PumpBuyV2UnsignedMessageError(
            "Order authorization "
            "is expired."
        )

    payer = _required_pubkey(
        authorization.wallet_pubkey,
        label="authorized payer",
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

    rent_budget = (
        _required_nonnegative_u64(
            authorization.rent_lamports,
            label="rent",
        )
    )

    #
    # These values are intentionally read here.
    # Actual live base-fee/rent validation belongs
    # in the later chain-aware pre-sign boundary.
    #
    if (
        base_network_fee
        + priority_fee_budget
        + rent_budget
        > U64_MAX
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Authorized transaction "
            "overhead overflows u64."
        )

    #
    # Context must be the exact downstream artifact
    # of this authorization.
    #
    if (
        context.resolver_version
        != PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Account-context version "
            "is unsupported."
        )

    if (
        context.authorization_version
        != authorization.authorization_version
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Account context is bound to "
            "a different authorization version."
        )

    if (
        context.reservation_id
        != authorization.reservation_id
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Account context reservation "
            "identity mismatch."
        )

    if (
        context.simulation_sha256
        != authorization.simulation_sha256
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Account context simulation "
            "fingerprint mismatch."
        )

    if (
        context.user
        != authorization.wallet_pubkey
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Account-context user does not "
            "match authorized payer."
        )

    if (
        context.base_mint
        != authorization.mint
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Account-context mint does not "
            "match authorization."
        )

    if (
        context.quote_mint
        != authorization.quote_mint_for_instruction
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Account-context quote mint does "
            "not match authorization."
        )

    if (
        context.base_token_program
        != authorization.base_token_program
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Account-context base token "
            "program does not match "
            "authorization."
        )

    if (
        context.bonding_curve
        != authorization.bonding_curve
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Account-context bonding curve "
            "does not match authorization."
        )

    if (
        context.associated_base_bonding_curve
        != authorization.associated_bonding_curve
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Account-context bonding-curve "
            "ATA does not match authorization."
        )

    if (
        context.amount
        != authorization.token_amount
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Account-context amount "
            "does not match authorization."
        )

    if (
        context.max_sol_cost
        != authorization.max_sol_cost
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Account-context max SOL cost "
            "does not match authorization."
        )

    #
    # Rebuild the exact instruction mechanically
    # from the authorized account context rather
    # than accepting an arbitrary Instruction.
    #
    instruction_plan = (
        build_pump_buy_v2_instruction(
            context=context
        )
    )

    if (
        instruction_plan.builder_version
        != PUMP_BUY_V2_INSTRUCTION_VERSION
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Pump instruction builder "
            "version is unsupported."
        )

    if (
        instruction_plan.reservation_id
        != authorization.reservation_id
        or instruction_plan.simulation_sha256
        != authorization.simulation_sha256
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Pump instruction authority "
            "binding mismatch."
        )

    pump_instruction = (
        instruction_plan.instruction
    )

    if (
        len(
            pump_instruction.accounts
        )
        != 27
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Pump instruction does not "
            "contain exactly 27 accounts."
        )

    #
    # Account 14 in Pump's 1-based IDL order is
    # user. It must be our sole authorized signer.
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
        raise PumpBuyV2UnsignedMessageError(
            "Pump instruction signer does "
            "not match authorized payer."
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
        raise PumpBuyV2UnsignedMessageError(
            "Pump instruction requires an "
            "unexpected signer set."
        )

    #
    # Execution policy supplies the CU limit.
    # Spending authority supplies the maximum
    # priority-fee budget.
    #
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

    #
    # The blockhash and its expiry height must come
    # from one authoritative getLatestBlockhash
    # artifact. They are never accepted separately.
    #
    if not isinstance(
        blockhash_context,
        LiveBlockhashContext,
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Live blockhash context is invalid."
        )

    if (
        blockhash_context.resolver_version
        != LIVE_BLOCKHASH_CONTEXT_VERSION
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Live blockhash context version "
            "is unsupported."
        )

    for label, value in (
        (
            "blockhash rpc slot",
            blockhash_context.rpc_slot,
        ),
        (
            "blockhash minimum context slot",
            blockhash_context.min_context_slot,
        ),
        (
            "last valid block height",
            blockhash_context
            .last_valid_block_height,
        ),
    ):
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
        ):
            raise PumpBuyV2UnsignedMessageError(
                f"{label} is invalid."
            )

    if (
        blockhash_context.rpc_slot
        < blockhash_context.min_context_slot
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Live blockhash RPC context predates "
            "its minimum context slot."
        )

    try:
        blockhash = Hash.from_string(
            blockhash_context.blockhash
        )

    except Exception as error:
        raise PumpBuyV2UnsignedMessageError(
            "Recent blockhash is invalid."
        ) from error

    if blockhash == Hash.default():
        raise PumpBuyV2UnsignedMessageError(
            "Default blockhash is not "
            "allowed for live construction."
        )

    #
    # Pure compilation. No Keypair and no
    # VersionedTransaction are involved.
    #
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
        raise PumpBuyV2UnsignedMessageError(
            "MessageV0 compilation failed."
        ) from error

    #
    # Structural post-compile proof.
    #
    if (
        message.header.num_required_signatures
        != 1
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Compiled message requires an "
            "unexpected number of signatures."
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
        raise PumpBuyV2UnsignedMessageError(
            "Compiled message fee payer "
            "is not the authorized wallet."
        )

    required_signers = account_keys[
        :message.header.num_required_signatures
    ]

    if required_signers != (
        payer,
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Compiled message signer set "
            "does not equal authorized payer."
        )

    if len(
        message.address_table_lookups
    ) != 0:
        raise PumpBuyV2UnsignedMessageError(
            "Address lookup tables are "
            "not authorized in v1."
        )

    if (
        message.recent_blockhash
        != blockhash
    ):
        raise PumpBuyV2UnsignedMessageError(
            "Compiled blockhash mismatch."
        )

    compiled_instructions = tuple(
        message.instructions
    )

    if len(
        compiled_instructions
    ) != 3:
        raise PumpBuyV2UnsignedMessageError(
            "Unsigned Pump message must "
            "contain exactly 3 instructions."
        )

    #
    # Verify both Compute Budget instructions
    # survived compilation byte-for-byte.
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
            raise PumpBuyV2UnsignedMessageError(
                "Compute Budget program "
                "identity mismatch."
            )

        if len(
            compiled.accounts
        ) != 0:
            raise PumpBuyV2UnsignedMessageError(
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
            raise PumpBuyV2UnsignedMessageError(
                "Compute Budget instruction "
                "data changed during compile."
            )

    #
    # Verify the Pump instruction survived
    # compilation exactly: program, data, and
    # every account identity in IDL order.
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
        raise PumpBuyV2UnsignedMessageError(
            "Compiled Pump program "
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
        raise PumpBuyV2UnsignedMessageError(
            "Compiled Pump instruction "
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
        raise PumpBuyV2UnsignedMessageError(
            "Compiled Pump account order "
            "or identity mismatch."
        )

    #
    # Fingerprint the actual versioned message.
    #
    message_bytes = (
        to_bytes_versioned(
            message
        )
    )

    message_sha256 = hashlib.sha256(
        message_bytes
    ).hexdigest()

    #
    # Exactly one signature is required, so the
    # future signed transaction adds:
    #
    #   1 byte shortvec signature count
    #   64 bytes Ed25519 signature
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
        raise PumpBuyV2UnsignedMessageError(
            "Compiled transaction would "
            "exceed Solana packet size."
        )

    return PumpBuyV2UnsignedMessagePlan(
        builder_version=(
            PUMP_BUY_V2_UNSIGNED_MESSAGE_VERSION
        ),
        authorization_version=(
            authorization.authorization_version
        ),
        account_context_version=(
            context.resolver_version
        ),
        instruction_builder_version=(
            instruction_plan.builder_version
        ),
        reservation_id=(
            authorization.reservation_id
        ),
        simulation_sha256=(
            authorization.simulation_sha256
        ),
        payer=str(
            payer
        ),
        blockhash_context_version=(
            blockhash_context
            .resolver_version
        ),
        recent_blockhash=str(
            blockhash
        ),
        last_valid_block_height=(
            blockhash_context
            .last_valid_block_height
        ),
        blockhash_rpc_slot=(
            blockhash_context.rpc_slot
        ),
        compute_unit_limit=(
            cu_limit
        ),
        compute_unit_price_micro_lamports=(
            cu_price
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
