from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solders.compute_budget import (
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
from src.execution.pump_sell_v2_unsigned_message import (
    PUMP_SELL_V2_UNSIGNED_MESSAGE_VERSION,
    PumpSellV2UnsignedMessagePlan,
)
from src.portfolio.live_reservations import (
    DB_PATH as LIVE_DB_PATH,
)
from src.portfolio.live_sell_claims import (
    ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION,
    BLOCK as CLAIM_BLOCK,
    LIVE_SELL_INVENTORY_CLAIM_VERSION,
    PASS as CLAIM_PASS,
    UNKNOWN as CLAIM_UNKNOWN,
    load_active_live_sell_inventory_claim_read_only,
)
from src.safety.token_safety_resolver import (
    COMMITMENT,
    HeliusRpcClient,
)


PUMP_SELL_V2_PRE_SIGN_VALIDATION_VERSION = (
    "pump-sell-v2-pre-sign-validation-v1"
)

APPROVE = "APPROVE"
DENY = "DENY"
UNKNOWN = "UNKNOWN"

U64_MAX = (1 << 64) - 1

MICRO_LAMPORTS_PER_LAMPORT = 1_000_000
SOLANA_PACKET_DATA_SIZE = 1_232


@dataclass(frozen=True)
class PumpSellV2PreSignValidation:
    validator_version: str

    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    claim_loader_version: str | None
    claim_version: str | None
    claim_authorization_sha256: str | None

    message_sha256: str | None
    payer: str | None

    min_context_slot: int | None

    blockhash_valid_slot: int | None
    fee_rpc_slot: int | None

    rpc_total_fee_lamports: int | None
    actual_base_fee_lamports: int | None

    authorized_base_fee_lamports: int | None
    authorized_priority_fee_lamports: int | None

    blockhash_context_version: str | None
    last_valid_block_height: int | None
    blockhash_rpc_slot: int | None

    checked_at: float

    @property
    def allows_signing(self) -> bool:
        return self.status == APPROVE


def _nonnegative_u64(
    value: object,
) -> int | None:
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
        return None

    return value


def _positive_u64(
    value: object,
) -> int | None:
    result = _nonnegative_u64(
        value
    )

    if (
        result is None
        or result <= 0
    ):
        return None

    return result


def _valid_sha256(
    value: object,
) -> bool:
    if (
        not isinstance(
            value,
            str,
        )
        or len(value) != 64
    ):
        return False

    try:
        bytes.fromhex(
            value
        )

    except ValueError:
        return False

    return True


def _valid_pubkey(
    value: object,
) -> bool:
    if (
        not isinstance(
            value,
            str,
        )
        or not value
    ):
        return False

    try:
        pubkey = Pubkey.from_string(
            value
        )

    except Exception:
        return False

    return (
        pubkey
        != Pubkey.default()
    )


def _rpc_context_slot(
    result: object,
) -> int | None:
    if not isinstance(
        result,
        dict,
    ):
        return None

    context = result.get(
        "context"
    )

    if not isinstance(
        context,
        dict,
    ):
        return None

    slot = context.get(
        "slot"
    )

    if (
        not isinstance(
            slot,
            int,
        )
        or isinstance(
            slot,
            bool,
        )
        or slot < 0
    ):
        return None

    return slot


def _compiled_instruction_matches(
    *,
    message: MessageV0,
    compiled_instruction: Any,
    expected_instruction: Any,
) -> bool:
    try:
        account_keys = tuple(
            message.account_keys
        )

        program_index = int(
            compiled_instruction.program_id_index
        )

        if (
            program_index < 0
            or program_index
            >= len(account_keys)
        ):
            return False

        if (
            account_keys[
                program_index
            ]
            != expected_instruction.program_id
        ):
            return False

        if (
            bytes(
                compiled_instruction.data
            )
            != bytes(
                expected_instruction.data
            )
        ):
            return False

        compiled_accounts = tuple(
            account_keys[
                int(index)
            ]
            for index in
            compiled_instruction.accounts
        )

        expected_accounts = tuple(
            meta.pubkey
            for meta in
            expected_instruction.accounts
        )

        if (
            compiled_accounts
            != expected_accounts
        ):
            return False

    except Exception:
        return False

    return True


async def validate_pump_sell_v2_pre_sign(
    *,
    authorization: LivePumpSellAuthorization,
    context: PumpSellV2AccountContext,
    message_plan: PumpSellV2UnsignedMessagePlan,
    db_path: Path = LIVE_DB_PATH,
) -> PumpSellV2PreSignValidation:
    """
    Final durable-inventory / message / network-fee
    validation before any signer is allowed to touch
    a Pump SELL message.

    This validator does NOT:
      - load or access a private key;
      - sign a transaction;
      - submit a transaction;
      - acquire, release, or consume a SELL claim;
      - mutate live positions;
      - initialize live database schema.
    """

    authorization_sha256 = ""

    claim_loader_version: (
        str | None
    ) = None

    claim_version: (
        str | None
    ) = None

    claim_authorization_sha256: (
        str | None
    ) = None

    message_sha256: (
        str | None
    ) = None

    payer: (
        str | None
    ) = None

    min_context_slot: (
        int | None
    ) = None

    blockhash_valid_slot: (
        int | None
    ) = None

    fee_rpc_slot: (
        int | None
    ) = None

    rpc_total_fee_lamports: (
        int | None
    ) = None

    actual_base_fee_lamports: (
        int | None
    ) = None

    authorized_base_fee_lamports: (
        int | None
    ) = None

    authorized_priority_fee_lamports: (
        int | None
    ) = None

    blockhash_context_version: (
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
    ) -> PumpSellV2PreSignValidation:
        return PumpSellV2PreSignValidation(
            validator_version=(
                PUMP_SELL_V2_PRE_SIGN_VALIDATION_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            claim_loader_version=(
                claim_loader_version
            ),
            claim_version=claim_version,
            claim_authorization_sha256=(
                claim_authorization_sha256
            ),
            message_sha256=(
                message_sha256
            ),
            payer=payer,
            min_context_slot=(
                min_context_slot
            ),
            blockhash_valid_slot=(
                blockhash_valid_slot
            ),
            fee_rpc_slot=(
                fee_rpc_slot
            ),
            rpc_total_fee_lamports=(
                rpc_total_fee_lamports
            ),
            actual_base_fee_lamports=(
                actual_base_fee_lamports
            ),
            authorized_base_fee_lamports=(
                authorized_base_fee_lamports
            ),
            authorized_priority_fee_lamports=(
                authorized_priority_fee_lamports
            ),
            blockhash_context_version=(
                blockhash_context_version
            ),
            last_valid_block_height=(
                last_valid_block_height
            ),
            blockhash_rpc_slot=(
                blockhash_rpc_slot
            ),
            checked_at=time.time(),
        )

    #
    # Exact artifact types.
    #
    if not isinstance(
        authorization,
        LivePumpSellAuthorization,
    ):
        return finish(
            DENY,
            "INVALID_SELL_AUTHORIZATION",
        )

    authorization_sha256 = (
        authorization.authorization_sha256
    )

    if not isinstance(
        context,
        PumpSellV2AccountContext,
    ):
        return finish(
            DENY,
            "INVALID_SELL_ACCOUNT_CONTEXT",
        )

    if not isinstance(
        message_plan,
        PumpSellV2UnsignedMessagePlan,
    ):
        return finish(
            DENY,
            "INVALID_SELL_MESSAGE_PLAN",
        )

    #
    # Durable inventory authority.
    #
    claim_result = (
        load_active_live_sell_inventory_claim_read_only(
            authorization=authorization,
            db_path=db_path,
        )
    )

    claim_loader_version = (
        claim_result.loader_version
    )

    if (
        claim_loader_version
        != ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION
    ):
        return finish(
            UNKNOWN,
            "SELL_CLAIM_LOADER_VERSION_MISMATCH",
        )

    if (
        claim_result.status
        == CLAIM_UNKNOWN
    ):
        return finish(
            UNKNOWN,
            *claim_result.reasons,
        )

    if (
        claim_result.status
        == CLAIM_BLOCK
    ):
        return finish(
            DENY,
            *claim_result.reasons,
        )

    if (
        claim_result.status
        != CLAIM_PASS
        or claim_result.claim
        is None
    ):
        return finish(
            UNKNOWN,
            "SELL_CLAIM_RESULT_INVALID",
        )

    claim = claim_result.claim

    claim_version = (
        claim.claim_version
    )

    claim_authorization_sha256 = (
        claim.authorization_sha256
    )

    if (
        claim_version
        != LIVE_SELL_INVENTORY_CLAIM_VERSION
        or claim_authorization_sha256
        != authorization_sha256
        or claim.wallet_pubkey
        != authorization.wallet_pubkey
        or claim.mint
        != authorization.mint
        or claim.tokens_to_sell
        != authorization.tokens_to_sell
        or claim.allocation
        != authorization.allocation
    ):
        return finish(
            UNKNOWN,
            "SELL_CLAIM_BINDING_MISMATCH",
        )

    #
    # Artifact versions / identity chain.
    #
    if (
        authorization.authorization_version
        != LIVE_PUMP_SELL_AUTHORIZATION_VERSION
        or not _valid_sha256(
            authorization_sha256
        )
    ):
        return finish(
            DENY,
            "SELL_AUTHORIZATION_VERSION_OR_HASH_INVALID",
        )

    if (
        context.resolver_version
        != PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION
    ):
        return finish(
            DENY,
            "ACCOUNT_CONTEXT_VERSION_MISMATCH",
        )

    if (
        message_plan.account_context_version
        != context.resolver_version
    ):
        return finish(
            DENY,
            "ACCOUNT_CONTEXT_BINDING_MISMATCH",
        )

    if (
        message_plan.builder_version
        != PUMP_SELL_V2_UNSIGNED_MESSAGE_VERSION
    ):
        return finish(
            DENY,
            "MESSAGE_PLAN_VERSION_MISMATCH",
        )

    if (
        message_plan.instruction_builder_version
        != PUMP_SELL_V2_INSTRUCTION_VERSION
    ):
        return finish(
            DENY,
            "INSTRUCTION_VERSION_MISMATCH",
        )

    if (
        getattr(
            context,
            "authorization_version",
            None,
        )
        != authorization.authorization_version
        or message_plan.authorization_version
        != authorization.authorization_version
    ):
        return finish(
            DENY,
            "AUTHORIZATION_VERSION_BINDING_MISMATCH",
        )

    if (
        getattr(
            context,
            "authorization_sha256",
            None,
        )
        != authorization_sha256
        or message_plan.authorization_sha256
        != authorization_sha256
    ):
        return finish(
            DENY,
            "AUTHORIZATION_HASH_BINDING_MISMATCH",
        )

    #
    # Payer / signer identity.
    #
    payer = (
        authorization.wallet_pubkey
    )

    if (
        not _valid_pubkey(
            payer
        )
        or getattr(
            context,
            "user",
            None,
        )
        != payer
        or message_plan.payer
        != payer
    ):
        return finish(
            DENY,
            "PAYER_BINDING_MISMATCH",
        )

    #
    # Transaction-critical state slots.
    #
    authorization_fee_rpc_slot = (
        _nonnegative_u64(
            authorization.fee_rpc_slot
        )
    )

    context_authorization_fee_rpc_slot = (
        _nonnegative_u64(
            getattr(
                context,
                "authorization_fee_rpc_slot",
                None,
            )
        )
    )

    context_global_rpc_slot = (
        _nonnegative_u64(
            getattr(
                context,
                "global_rpc_slot",
                None,
            )
        )
    )

    if (
        authorization_fee_rpc_slot
        is None
        or context_authorization_fee_rpc_slot
        is None
        or context_global_rpc_slot
        is None
    ):
        return finish(
            DENY,
            "STATE_SLOT_INVALID",
        )

    if (
        context_authorization_fee_rpc_slot
        != authorization_fee_rpc_slot
        or message_plan.authorization_fee_rpc_slot
        != authorization_fee_rpc_slot
        or message_plan.account_context_global_rpc_slot
        != context_global_rpc_slot
    ):
        return finish(
            DENY,
            "STATE_SLOT_BINDING_MISMATCH",
        )

    if (
        context_global_rpc_slot
        < authorization_fee_rpc_slot
    ):
        return finish(
            DENY,
            "ACCOUNT_CONTEXT_PREDATES_AUTHORIZATION",
        )

    #
    # Blockhash / expiry provenance.
    #
    blockhash_context_version = (
        message_plan.blockhash_context_version
    )

    last_valid_block_height = (
        _nonnegative_u64(
            message_plan.last_valid_block_height
        )
    )

    blockhash_rpc_slot = (
        _nonnegative_u64(
            message_plan.blockhash_rpc_slot
        )
    )

    blockhash_min_context_slot = (
        _nonnegative_u64(
            message_plan.blockhash_min_context_slot
        )
    )

    if (
        blockhash_context_version
        != LIVE_BLOCKHASH_CONTEXT_VERSION
        or last_valid_block_height
        is None
        or blockhash_rpc_slot
        is None
        or blockhash_min_context_slot
        is None
    ):
        return finish(
            DENY,
            "BLOCKHASH_EXPIRY_PROVENANCE_INVALID",
        )

    expected_blockhash_min_context_slot = max(
        authorization_fee_rpc_slot,
        context_global_rpc_slot,
    )

    if (
        blockhash_min_context_slot
        != expected_blockhash_min_context_slot
        or blockhash_rpc_slot
        < blockhash_min_context_slot
    ):
        return finish(
            DENY,
            "BLOCKHASH_CONTEXT_BINDING_MISMATCH",
        )

    try:
        blockhash = Hash.from_string(
            message_plan.recent_blockhash
        )

    except Exception:
        return finish(
            DENY,
            "RECENT_BLOCKHASH_INVALID",
        )

    if (
        blockhash
        == Hash.default()
    ):
        return finish(
            DENY,
            "RECENT_BLOCKHASH_INVALID",
        )

    #
    # Authorized network-fee economics.
    #
    authorized_base_fee_lamports = (
        _nonnegative_u64(
            authorization
            .base_network_fee_lamports
        )
    )

    authorized_priority_fee_lamports = (
        _nonnegative_u64(
            authorization
            .priority_fee_lamports
        )
    )

    if (
        authorized_base_fee_lamports
        is None
        or authorized_priority_fee_lamports
        is None
    ):
        return finish(
            DENY,
            "AUTHORIZED_NETWORK_FEES_INVALID",
        )

    if (
        message_plan
        .authorized_base_network_fee_lamports
        != authorized_base_fee_lamports
        or message_plan
        .authorized_priority_fee_lamports
        != authorized_priority_fee_lamports
    ):
        return finish(
            DENY,
            "MESSAGE_NETWORK_FEE_BINDING_MISMATCH",
        )

    compute_unit_limit = (
        _positive_u64(
            message_plan.compute_unit_limit
        )
    )

    compute_unit_price_micro_lamports = (
        _nonnegative_u64(
            message_plan
            .compute_unit_price_micro_lamports
        )
    )

    if (
        compute_unit_limit is None
        or compute_unit_price_micro_lamports
        is None
    ):
        return finish(
            DENY,
            "COMPUTE_BUDGET_INVALID",
        )

    planned_priority_fee_lamports = (
        (
            compute_unit_limit
            * compute_unit_price_micro_lamports
            + MICRO_LAMPORTS_PER_LAMPORT
            - 1
        )
        // MICRO_LAMPORTS_PER_LAMPORT
    )

    if (
        planned_priority_fee_lamports
        > U64_MAX
        or message_plan
        .planned_priority_fee_lamports
        != planned_priority_fee_lamports
        or planned_priority_fee_lamports
        != authorized_priority_fee_lamports
    ):
        return finish(
            DENY,
            "MESSAGE_PRIORITY_FEE_MISMATCH",
        )

    #
    # Rebuild the expected SELL instruction directly
    # from the authorization + account context.
    #
    try:
        instruction_plan = (
            build_pump_sell_v2_instruction(
                context=context,
            )
        )

    except Exception:
        return finish(
            DENY,
            "SELL_INSTRUCTION_REBUILD_FAILED",
        )

    if (
        getattr(
            instruction_plan,
            "builder_version",
            None,
        )
        != PUMP_SELL_V2_INSTRUCTION_VERSION
        or getattr(
            instruction_plan,
            "instruction_sha256",
            None,
        )
        != message_plan.pump_instruction_sha256
    ):
        return finish(
            DENY,
            "SELL_INSTRUCTION_BINDING_MISMATCH",
        )

    pump_instruction = getattr(
        instruction_plan,
        "instruction",
        None,
    )

    if pump_instruction is None:
        return finish(
            DENY,
            "SELL_INSTRUCTION_REBUILD_FAILED",
        )

    #
    # Recompute actual immutable MessageV0 evidence.
    #
    if not isinstance(
        message_plan.message,
        MessageV0,
    ):
        return finish(
            DENY,
            "MESSAGE_TYPE_INVALID",
        )

    try:
        message_bytes = (
            to_bytes_versioned(
                message_plan.message
            )
        )

    except Exception:
        return finish(
            DENY,
            "MESSAGE_SERIALIZATION_FAILED",
        )

    message_sha256 = hashlib.sha256(
        message_bytes
    ).hexdigest()

    if (
        not _valid_sha256(
            message_plan.message_sha256
        )
        or message_sha256
        != message_plan.message_sha256
    ):
        return finish(
            DENY,
            "MESSAGE_FINGERPRINT_MISMATCH",
        )

    estimated_signed_size = (
        len(
            message_bytes
        )
        + 1
        + 64
    )

    if (
        estimated_signed_size
        != message_plan
        .estimated_signed_transaction_size_bytes
        or estimated_signed_size
        > SOLANA_PACKET_DATA_SIZE
    ):
        return finish(
            DENY,
            "SIGNED_TRANSACTION_SIZE_MISMATCH",
        )

    #
    # The transaction must require exactly one future
    # Ed25519 signature, and that signer is the wallet.
    #
    try:
        header = (
            message_plan.message.header
        )

        account_keys = tuple(
            message_plan.message.account_keys
        )

        if (
            header.num_required_signatures
            != 1
            or not account_keys
            or account_keys[0]
            != Pubkey.from_string(
                payer
            )
        ):
            return finish(
                DENY,
                "MESSAGE_SIGNER_BINDING_MISMATCH",
            )

        if (
            str(
                message_plan
                .message
                .recent_blockhash
            )
            != message_plan.recent_blockhash
        ):
            return finish(
                DENY,
                "MESSAGE_BLOCKHASH_BINDING_MISMATCH",
            )

    except Exception:
        return finish(
            DENY,
            "MESSAGE_HEADER_INVALID",
        )

    #
    # Verify every compiled instruction, rather than
    # trusting metadata carried beside the message.
    #
    compiled_instructions = tuple(
        message_plan.message.instructions
    )

    if len(
        compiled_instructions
    ) != 3:
        return finish(
            DENY,
            "MESSAGE_INSTRUCTION_COUNT_MISMATCH",
        )

    expected_compute_limit = (
        set_compute_unit_limit(
            compute_unit_limit
        )
    )

    expected_compute_price = (
        set_compute_unit_price(
            compute_unit_price_micro_lamports
        )
    )

    if not _compiled_instruction_matches(
        message=message_plan.message,
        compiled_instruction=(
            compiled_instructions[0]
        ),
        expected_instruction=(
            expected_compute_limit
        ),
    ):
        return finish(
            DENY,
            "COMPUTE_LIMIT_INSTRUCTION_MISMATCH",
        )

    if not _compiled_instruction_matches(
        message=message_plan.message,
        compiled_instruction=(
            compiled_instructions[1]
        ),
        expected_instruction=(
            expected_compute_price
        ),
    ):
        return finish(
            DENY,
            "COMPUTE_PRICE_INSTRUCTION_MISMATCH",
        )

    if not _compiled_instruction_matches(
        message=message_plan.message,
        compiled_instruction=(
            compiled_instructions[2]
        ),
        expected_instruction=(
            pump_instruction
        ),
    ):
        return finish(
            DENY,
            "PUMP_SELL_INSTRUCTION_MISMATCH",
        )

    #
    # Current network checks.
    #
    # We preserve last_valid_block_height as provenance
    # from getLatestBlockhash, but current validity is
    # proven with isBlockhashValid just as BUY does.
    #
    min_context_slot = max(
        authorization_fee_rpc_slot,
        context_global_rpc_slot,
        blockhash_rpc_slot,
    )

    encoded_message = (
        base64.b64encode(
            message_bytes
        ).decode(
            "ascii"
        )
    )

    try:
        async with HeliusRpcClient() as rpc:
            blockhash_result = (
                await rpc.call(
                    "isBlockhashValid",
                    [
                        message_plan
                        .recent_blockhash,
                        {
                            "commitment": (
                                COMMITMENT
                            ),
                            "minContextSlot": (
                                min_context_slot
                            ),
                        },
                    ],
                )
            )

            blockhash_valid_slot = (
                _rpc_context_slot(
                    blockhash_result
                )
            )

            if (
                blockhash_valid_slot
                is None
                or blockhash_valid_slot
                < min_context_slot
            ):
                return finish(
                    UNKNOWN,
                    "BLOCKHASH_RPC_CONTEXT_INVALID",
                )

            blockhash_value = (
                blockhash_result.get(
                    "value"
                )
                if isinstance(
                    blockhash_result,
                    dict,
                )
                else None
            )

            if not isinstance(
                blockhash_value,
                bool,
            ):
                return finish(
                    UNKNOWN,
                    "BLOCKHASH_RPC_VALUE_INVALID",
                )

            if not blockhash_value:
                return finish(
                    DENY,
                    "BLOCKHASH_NOT_VALID",
                )

            fee_min_context_slot = max(
                min_context_slot,
                blockhash_valid_slot,
            )

            fee_result = await rpc.call(
                "getFeeForMessage",
                [
                    encoded_message,
                    {
                        "commitment": (
                            COMMITMENT
                        ),
                        "minContextSlot": (
                            fee_min_context_slot
                        ),
                    },
                ],
            )

    except Exception as error:
        return finish(
            UNKNOWN,
            (
                "PRE_SIGN_RPC_FAILED:"
                f"{type(error).__name__}"
            ),
        )

    fee_rpc_slot = _rpc_context_slot(
        fee_result
    )

    if (
        fee_rpc_slot is None
        or fee_rpc_slot
        < max(
            min_context_slot,
            blockhash_valid_slot,
        )
    ):
        return finish(
            UNKNOWN,
            "FEE_RPC_CONTEXT_INVALID",
        )

    fee_value = (
        fee_result.get(
            "value"
        )
        if isinstance(
            fee_result,
            dict,
        )
        else None
    )

    #
    # null means the node cannot currently quote this
    # exact message at its embedded blockhash.
    #
    if fee_value is None:
        return finish(
            DENY,
            "FEE_UNAVAILABLE_FOR_BLOCKHASH",
        )

    rpc_total_fee_lamports = (
        _nonnegative_u64(
            fee_value
        )
    )

    if (
        rpc_total_fee_lamports
        is None
    ):
        return finish(
            UNKNOWN,
            "FEE_RPC_VALUE_INVALID",
        )

    if (
        rpc_total_fee_lamports
        < authorized_priority_fee_lamports
    ):
        return finish(
            UNKNOWN,
            "RPC_FEE_BELOW_PRIORITY_FEE",
        )

    actual_base_fee_lamports = (
        rpc_total_fee_lamports
        - authorized_priority_fee_lamports
    )

    if (
        actual_base_fee_lamports
        > authorized_base_fee_lamports
    ):
        return finish(
            DENY,
            "BASE_NETWORK_FEE_EXCEEDS_AUTHORIZATION",
        )

    return finish(
        APPROVE,
    )
