from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from solders.hash import Hash
from solders.message import to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

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
)
from src.execution.pump_sell_v2_pre_sign_validation import (
    APPROVE,
    DENY,
    PUMP_SELL_V2_PRE_SIGN_VALIDATION_VERSION,
    UNKNOWN as PRE_SIGN_UNKNOWN,
    PumpSellV2PreSignValidation,
)
from src.execution.pump_sell_v2_unsigned_message import (
    PUMP_SELL_V2_UNSIGNED_MESSAGE_VERSION,
    SOLANA_PACKET_DATA_SIZE,
    PumpSellV2UnsignedMessagePlan,
)
from src.portfolio.live_reservations import (
    DB_PATH as LIVE_DB_PATH,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION,
    BLOCK as CLAIM_BLOCK,
    LIVE_SELL_INVENTORY_CLAIM_VERSION,
    PASS as CLAIM_PASS,
    UNKNOWN as CLAIM_UNKNOWN,
    LiveSellInventoryClaim,
    load_active_live_sell_inventory_claim_read_only,
)
from src.portfolio.live_sell_execution_records import (
    BLOCK as EXECUTION_BLOCK,
    LIVE_SELL_EXECUTION_RECORD_VERSION,
    PASS as EXECUTION_PASS,
    SIGNED as EXECUTION_SIGNED,
    UNKNOWN as EXECUTION_UNKNOWN,
    LiveSellExecutionRecord,
    bind_live_sell_signed_artifact,
    load_live_sell_execution_record_read_only,
)
from src.safety.token_safety_resolver import (
    COMMITMENT,
    HeliusRpcClient,
)


PUMP_SELL_V2_SIGNING_VERSION = (
    "pump-sell-v2-signing-v1"
)

PASS = "PASS"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


class MessageSigner(Protocol):
    def pubkey(self) -> Pubkey:
        ...

    def sign_message(
        self,
        message: bytes,
    ) -> Signature:
        ...


@dataclass(frozen=True)
class PumpSellV2SigningResult:
    signer_version: str

    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    signer_pubkey: str | None

    transaction_signature: str | None
    message_sha256: str | None
    signed_transaction_sha256: str | None
    signed_transaction_bytes: bytes | None

    execution_record_changed: bool | None
    signed_at: float | None

    blockhash_context_version: str | None
    last_valid_block_height: int | None
    blockhash_rpc_slot: int | None

    final_blockhash_slot: int | None

    @property
    def is_durably_signed(self) -> bool:
        return (
            self.status == PASS
            and self.transaction_signature
            is not None
            and self.message_sha256
            is not None
            and self.signed_transaction_sha256
            is not None
            and self.signed_transaction_bytes
            is not None
            and self.signed_at
            is not None
        )


def _nonnegative_int(
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
    ):
        return None

    return value


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


def _valid_blockhash(
    value: object,
) -> bool:
    if not isinstance(
        value,
        str,
    ):
        return False

    try:
        blockhash = Hash.from_string(
            value
        )

    except Exception:
        return False

    return (
        blockhash
        != Hash.default()
    )


def _claim_matches_authorization(
    *,
    claim: LiveSellInventoryClaim,
    authorization: LivePumpSellAuthorization,
) -> bool:
    return (
        claim.claim_version
        == LIVE_SELL_INVENTORY_CLAIM_VERSION
        and claim.authorization_version
        == authorization.authorization_version
        and claim.authorization_sha256
        == authorization.authorization_sha256
        and claim.wallet_pubkey
        == authorization.wallet_pubkey
        and claim.mint
        == authorization.mint
        and claim.tokens_to_sell
        == authorization.tokens_to_sell
        and claim.allocation
        == authorization.allocation
        and claim.status
        == ACTIVE
        and claim.terminal_at is None
        and claim.terminal_reason is None
    )


def _record_matches_exact_plan(
    *,
    record: LiveSellExecutionRecord,
    authorization: LivePumpSellAuthorization,
    message_plan: PumpSellV2UnsignedMessagePlan,
) -> bool:
    return (
        record.record_version
        == LIVE_SELL_EXECUTION_RECORD_VERSION
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
        and record.message_sha256
        == message_plan.message_sha256
        and record.blockhash_context_version
        == message_plan.blockhash_context_version
        and record.recent_blockhash
        == message_plan.recent_blockhash
        and record.last_valid_block_height
        == message_plan.last_valid_block_height
        and record.blockhash_rpc_slot
        == message_plan.blockhash_rpc_slot
        and record.signed_at is not None
    )


async def sign_and_bind_pump_sell_v2(
    *,
    authorization: LivePumpSellAuthorization,
    context: PumpSellV2AccountContext,
    message_plan: PumpSellV2UnsignedMessagePlan,
    network_validation: PumpSellV2PreSignValidation,
    signer: MessageSigner,
    db_path: Path = LIVE_DB_PATH,
) -> PumpSellV2SigningResult:
    """
    Sign one exact already-authorized Pump SELL
    MessageV0 and durably bind that signed artifact
    to the exclusive live SELL inventory claim.

    No transaction submission occurs here.

    Signed transaction bytes are exposed only after
    exact durable execution-record verification.
    """

    authorization_sha256 = (
        authorization.authorization_sha256
        if isinstance(
            authorization,
            LivePumpSellAuthorization,
        )
        else ""
    )

    signer_pubkey_string: (
        str | None
    ) = None

    persisted_transaction_signature: (
        str | None
    ) = None

    message_sha256: (
        str | None
    ) = None

    persisted_transaction_sha256: (
        str | None
    ) = None

    persisted_transaction_bytes: (
        bytes | None
    ) = None

    execution_record_changed: (
        bool | None
    ) = None

    signed_at: (
        float | None
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

    final_blockhash_slot: (
        int | None
    ) = None

    def finish(
        status: str,
        *reasons: str,
    ) -> PumpSellV2SigningResult:
        return PumpSellV2SigningResult(
            signer_version=(
                PUMP_SELL_V2_SIGNING_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            signer_pubkey=(
                signer_pubkey_string
            ),
            transaction_signature=(
                persisted_transaction_signature
            ),
            message_sha256=(
                message_sha256
            ),
            signed_transaction_sha256=(
                persisted_transaction_sha256
            ),
            signed_transaction_bytes=(
                persisted_transaction_bytes
            ),
            execution_record_changed=(
                execution_record_changed
            ),
            signed_at=signed_at,
            blockhash_context_version=(
                blockhash_context_version
            ),
            last_valid_block_height=(
                last_valid_block_height
            ),
            blockhash_rpc_slot=(
                blockhash_rpc_slot
            ),
            final_blockhash_slot=(
                final_blockhash_slot
            ),
        )

    # -----------------------------------------------------
    # Exact contract types.
    # -----------------------------------------------------

    if not isinstance(
        authorization,
        LivePumpSellAuthorization,
    ):
        return finish(
            BLOCK,
            "SELL_AUTHORIZATION_INVALID",
        )

    if not isinstance(
        context,
        PumpSellV2AccountContext,
    ):
        return finish(
            BLOCK,
            "SELL_ACCOUNT_CONTEXT_INVALID",
        )

    if not isinstance(
        message_plan,
        PumpSellV2UnsignedMessagePlan,
    ):
        return finish(
            BLOCK,
            "SELL_MESSAGE_PLAN_INVALID",
        )

    if not isinstance(
        network_validation,
        PumpSellV2PreSignValidation,
    ):
        return finish(
            BLOCK,
            "SELL_PRE_SIGN_VALIDATION_INVALID",
        )

    if (
        authorization.authorization_version
        != LIVE_PUMP_SELL_AUTHORIZATION_VERSION
    ):
        return finish(
            BLOCK,
            "SELL_AUTHORIZATION_VERSION_MISMATCH",
        )

    if not _valid_sha256(
        authorization.authorization_sha256
    ):
        return finish(
            BLOCK,
            "SELL_AUTHORIZATION_FINGERPRINT_INVALID",
        )

    # -----------------------------------------------------
    # Account-context authority binding.
    # -----------------------------------------------------

    if (
        context.resolver_version
        != PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION
    ):
        return finish(
            BLOCK,
            "ACCOUNT_CONTEXT_VERSION_MISMATCH",
        )

    if (
        context.authorization_version
        != authorization.authorization_version
        or context.authorization_sha256
        != authorization.authorization_sha256
    ):
        return finish(
            BLOCK,
            "ACCOUNT_CONTEXT_AUTHORIZATION_MISMATCH",
        )

    if (
        context.authorization_fee_rpc_slot
        != authorization.fee_rpc_slot
    ):
        return finish(
            BLOCK,
            "ACCOUNT_CONTEXT_FEE_SLOT_MISMATCH",
        )

    if (
        _nonnegative_int(
            context.global_rpc_slot
        )
        is None
    ):
        return finish(
            BLOCK,
            "ACCOUNT_CONTEXT_GLOBAL_SLOT_INVALID",
        )

    if (
        getattr(
            context,
            "user",
            None,
        )
        != authorization.wallet_pubkey
    ):
        return finish(
            BLOCK,
            "ACCOUNT_CONTEXT_WALLET_MISMATCH",
        )

    if (
        getattr(
            context,
            "base_mint",
            None,
        )
        != authorization.mint
        or getattr(
            context,
            "bonding_curve",
            None,
        )
        != authorization.bonding_curve
        or getattr(
            context,
            "base_token_program",
            None,
        )
        != authorization.base_token_program
        or getattr(
            context,
            "associated_base_user",
            None,
        )
        != authorization.associated_base_user
    ):
        return finish(
            BLOCK,
            "ACCOUNT_CONTEXT_CONSTRUCTION_IDENTITY_MISMATCH",
        )

    if (
        getattr(
            context,
            "amount",
            None,
        )
        != authorization.tokens_to_sell
    ):
        return finish(
            BLOCK,
            "ACCOUNT_CONTEXT_SELL_AMOUNT_MISMATCH",
        )

    if (
        getattr(
            context,
            "min_sol_output",
            None,
        )
        != authorization.exit_execution.min_quote_out
    ):
        return finish(
            BLOCK,
            "ACCOUNT_CONTEXT_MIN_OUTPUT_MISMATCH",
        )

    # -----------------------------------------------------
    # Exact unsigned-message bindings.
    # -----------------------------------------------------

    if (
        message_plan.builder_version
        != PUMP_SELL_V2_UNSIGNED_MESSAGE_VERSION
    ):
        return finish(
            BLOCK,
            "MESSAGE_PLAN_VERSION_MISMATCH",
        )

    if (
        message_plan.authorization_version
        != authorization.authorization_version
        or message_plan.authorization_sha256
        != authorization.authorization_sha256
    ):
        return finish(
            BLOCK,
            "MESSAGE_AUTHORIZATION_BINDING_MISMATCH",
        )

    if (
        message_plan.account_context_version
        != context.resolver_version
    ):
        return finish(
            BLOCK,
            "MESSAGE_CONTEXT_VERSION_MISMATCH",
        )

    if (
        message_plan.instruction_builder_version
        != PUMP_SELL_V2_INSTRUCTION_VERSION
    ):
        return finish(
            BLOCK,
            "MESSAGE_INSTRUCTION_VERSION_MISMATCH",
        )

    payer = authorization.wallet_pubkey

    if (
        message_plan.payer
        != payer
    ):
        return finish(
            BLOCK,
            "MESSAGE_PAYER_MISMATCH",
        )

    if (
        message_plan.authorization_fee_rpc_slot
        != authorization.fee_rpc_slot
        or message_plan.authorization_fee_rpc_slot
        != context.authorization_fee_rpc_slot
    ):
        return finish(
            BLOCK,
            "MESSAGE_AUTHORIZATION_FEE_SLOT_MISMATCH",
        )

    if (
        message_plan.account_context_global_rpc_slot
        != context.global_rpc_slot
    ):
        return finish(
            BLOCK,
            "MESSAGE_GLOBAL_SLOT_MISMATCH",
        )

    if (
        message_plan.blockhash_context_version
        != LIVE_BLOCKHASH_CONTEXT_VERSION
    ):
        return finish(
            BLOCK,
            "MESSAGE_BLOCKHASH_CONTEXT_VERSION_MISMATCH",
        )

    if (
        not _valid_blockhash(
            message_plan.recent_blockhash
        )
        or _nonnegative_int(
            message_plan.last_valid_block_height
        )
        is None
        or _nonnegative_int(
            message_plan.blockhash_rpc_slot
        )
        is None
        or _nonnegative_int(
            message_plan.blockhash_min_context_slot
        )
        is None
    ):
        return finish(
            BLOCK,
            "MESSAGE_BLOCKHASH_PROVENANCE_INVALID",
        )

    if (
        message_plan.authorized_base_network_fee_lamports
        != authorization.base_network_fee_lamports
        or message_plan
        .authorized_priority_fee_lamports
        != authorization.priority_fee_lamports
        or message_plan
        .planned_priority_fee_lamports
        != authorization.priority_fee_lamports
    ):
        return finish(
            BLOCK,
            "MESSAGE_FEE_AUTHORITY_MISMATCH",
        )

    # -----------------------------------------------------
    # Pre-sign approval must bind this exact chain.
    # -----------------------------------------------------

    if (
        network_validation.validator_version
        != PUMP_SELL_V2_PRE_SIGN_VALIDATION_VERSION
    ):
        return finish(
            BLOCK,
            "PRE_SIGN_VERSION_MISMATCH",
        )

    if (
        network_validation.status
        != APPROVE
        or not network_validation.allows_signing
    ):
        if (
            network_validation.status
            == PRE_SIGN_UNKNOWN
        ):
            return finish(
                UNKNOWN,
                "PRE_SIGN_VALIDATION_UNKNOWN",
                *network_validation.reasons,
            )

        if (
            network_validation.status
            == DENY
        ):
            return finish(
                BLOCK,
                "PRE_SIGN_VALIDATION_DENIED",
                *network_validation.reasons,
            )

        return finish(
            BLOCK,
            "PRE_SIGN_VALIDATION_NOT_APPROVED",
        )

    if (
        network_validation.authorization_sha256
        != authorization.authorization_sha256
    ):
        return finish(
            BLOCK,
            "PRE_SIGN_AUTHORIZATION_MISMATCH",
        )

    if (
        network_validation.claim_loader_version
        != ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION
        or network_validation.claim_version
        != LIVE_SELL_INVENTORY_CLAIM_VERSION
        or network_validation.claim_authorization_sha256
        != authorization.authorization_sha256
    ):
        return finish(
            BLOCK,
            "PRE_SIGN_CLAIM_BINDING_MISMATCH",
        )

    if (
        network_validation.message_sha256
        != message_plan.message_sha256
        or network_validation.payer
        != payer
    ):
        return finish(
            BLOCK,
            "PRE_SIGN_MESSAGE_BINDING_MISMATCH",
        )

    if (
        network_validation.blockhash_context_version
        != message_plan.blockhash_context_version
        or network_validation.last_valid_block_height
        != message_plan.last_valid_block_height
        or network_validation.blockhash_rpc_slot
        != message_plan.blockhash_rpc_slot
    ):
        return finish(
            BLOCK,
            "PRE_SIGN_BLOCKHASH_BINDING_MISMATCH",
        )

    if (
        network_validation.authorized_base_fee_lamports
        != authorization.base_network_fee_lamports
        or network_validation
        .authorized_priority_fee_lamports
        != authorization.priority_fee_lamports
    ):
        return finish(
            BLOCK,
            "PRE_SIGN_FEE_AUTHORITY_MISMATCH",
        )

    pre_sign_min_context_slot = (
        _nonnegative_int(
            network_validation.min_context_slot
        )
    )

    pre_sign_blockhash_valid_slot = (
        _nonnegative_int(
            network_validation.blockhash_valid_slot
        )
    )

    pre_sign_fee_rpc_slot = (
        _nonnegative_int(
            network_validation.fee_rpc_slot
        )
    )

    if (
        pre_sign_min_context_slot
        is None
        or pre_sign_blockhash_valid_slot
        is None
        or pre_sign_fee_rpc_slot
        is None
    ):
        return finish(
            BLOCK,
            "PRE_SIGN_SLOT_PROVENANCE_INVALID",
        )

    expected_pre_sign_min_slot = max(
        authorization.fee_rpc_slot,
        context.global_rpc_slot,
        message_plan.blockhash_rpc_slot,
    )

    if (
        pre_sign_min_context_slot
        != expected_pre_sign_min_slot
        or pre_sign_blockhash_valid_slot
        < pre_sign_min_context_slot
        or pre_sign_fee_rpc_slot
        < max(
            pre_sign_min_context_slot,
            pre_sign_blockhash_valid_slot,
        )
    ):
        return finish(
            BLOCK,
            "PRE_SIGN_SLOT_PROVENANCE_MISMATCH",
        )

    # -----------------------------------------------------
    # Freeze exact bytes that may be signed.
    # -----------------------------------------------------

    try:
        message_bytes = (
            to_bytes_versioned(
                message_plan.message
            )
        )

    except Exception:
        return finish(
            BLOCK,
            "MESSAGE_SERIALIZATION_FAILED",
        )

    message_sha256 = (
        hashlib.sha256(
            message_bytes
        ).hexdigest()
    )

    if (
        message_sha256
        != message_plan.message_sha256
        or message_sha256
        != network_validation.message_sha256
    ):
        return finish(
            BLOCK,
            "MESSAGE_FINGERPRINT_MISMATCH",
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
            BLOCK,
            "MESSAGE_BLOCKHASH_BINDING_MISMATCH",
        )

    if (
        message_plan
        .message
        .header
        .num_required_signatures
        != 1
    ):
        return finish(
            BLOCK,
            "MESSAGE_SIGNER_COUNT_MISMATCH",
        )

    account_keys = tuple(
        message_plan.message.account_keys
    )

    if (
        not account_keys
        or str(
            account_keys[0]
        )
        != payer
    ):
        return finish(
            BLOCK,
            "MESSAGE_PAYER_MISMATCH",
        )

    if (
        len(
            message_plan
            .message
            .address_table_lookups
        )
        != 0
    ):
        return finish(
            BLOCK,
            "MESSAGE_LOOKUP_TABLES_NOT_AUTHORIZED",
        )

    # -----------------------------------------------------
    # If this exact SELL was already durably signed,
    # return the persisted artifact without touching
    # either RPC or signer again.
    # -----------------------------------------------------

    try:
        existing_execution = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    authorization.authorization_sha256
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_LEDGER_READ_FAILED",
        )

    if (
        existing_execution.status
        == EXECUTION_PASS
    ):
        existing_record = (
            existing_execution.record
        )

        if existing_record is None:
            return finish(
                UNKNOWN,
                "SIGNED_SELL_EXECUTION_RECORD_MISSING",
            )

        if (
            not _record_matches_exact_plan(
                record=existing_record,
                authorization=authorization,
                message_plan=message_plan,
            )
        ):
            return finish(
                UNKNOWN,
                "PERSISTED_SIGNED_ARTIFACT_MISMATCH",
            )

        if (
            existing_record.status
            != EXECUTION_SIGNED
        ):
            return finish(
                BLOCK,
                "SELL_EXECUTION_ALREADY_ADVANCED",
            )

        persisted_transaction_signature = (
            existing_record.transaction_signature
        )

        persisted_transaction_sha256 = (
            existing_record
            .signed_transaction_sha256
        )

        persisted_transaction_bytes = (
            existing_record
            .signed_transaction_bytes
        )

        execution_record_changed = False
        signed_at = existing_record.signed_at

        blockhash_context_version = (
            existing_record
            .blockhash_context_version
        )

        last_valid_block_height = (
            existing_record
            .last_valid_block_height
        )

        blockhash_rpc_slot = (
            existing_record
            .blockhash_rpc_slot
        )

        return finish(
            PASS,
        )

    if (
        existing_execution.status
        == EXECUTION_UNKNOWN
    ):
        if (
            tuple(
                existing_execution.reasons
            )
            != (
                "LIVE_SELL_EXECUTION_TABLE_NOT_FOUND",
            )
        ):
            return finish(
                UNKNOWN,
                "SELL_EXECUTION_LEDGER_UNKNOWN",
                *existing_execution.reasons,
            )

    elif (
        existing_execution.status
        == EXECUTION_BLOCK
    ):
        if (
            "SELL_EXECUTION_RECORD_NOT_FOUND"
            not in existing_execution.reasons
        ):
            return finish(
                BLOCK,
                "SELL_EXECUTION_LEDGER_BLOCKED",
                *existing_execution.reasons,
            )

    else:
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_LEDGER_STATUS_INVALID",
        )

    # -----------------------------------------------------
    # Authoritative ACTIVE claim immediately before
    # touching signer/network authority.
    # -----------------------------------------------------

    try:
        claim_result = (
            load_active_live_sell_inventory_claim_read_only(
                authorization=authorization,
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_CLAIM_LEDGER_READ_FAILED",
        )

    if (
        claim_result.status
        == CLAIM_UNKNOWN
    ):
        return finish(
            UNKNOWN,
            "SELL_CLAIM_VALIDATION_UNKNOWN",
            *claim_result.reasons,
        )

    if (
        claim_result.status
        == CLAIM_BLOCK
    ):
        return finish(
            BLOCK,
            "SELL_CLAIM_VALIDATION_BLOCKED",
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
            "SELL_CLAIM_VALIDATION_INVALID",
        )

    if not _claim_matches_authorization(
        claim=claim_result.claim,
        authorization=authorization,
    ):
        return finish(
            UNKNOWN,
            "SELL_CLAIM_AUTHORITY_MISMATCH",
        )

    # -----------------------------------------------------
    # Only now touch the signer identity.
    # -----------------------------------------------------

    try:
        signer_pubkey = signer.pubkey()

    except Exception:
        return finish(
            UNKNOWN,
            "SIGNER_PUBKEY_FAILED",
        )

    if not isinstance(
        signer_pubkey,
        Pubkey,
    ):
        return finish(
            BLOCK,
            "SIGNER_PUBKEY_INVALID",
        )

    signer_pubkey_string = str(
        signer_pubkey
    )

    if signer_pubkey_string != payer:
        return finish(
            BLOCK,
            "SIGNER_PUBKEY_MISMATCH",
        )

    # -----------------------------------------------------
    # Final point-in-time blockhash check.
    #
    # The answer may not come from chain state older
    # than any upstream approved transaction-critical
    # state snapshot.
    # -----------------------------------------------------

    final_min_context_slot = max(
        authorization.fee_rpc_slot,
        context.global_rpc_slot,
        message_plan.blockhash_rpc_slot,
        pre_sign_min_context_slot,
        pre_sign_blockhash_valid_slot,
        pre_sign_fee_rpc_slot,
    )

    try:
        async with HeliusRpcClient() as rpc:
            blockhash_result = await rpc.call(
                "isBlockhashValid",
                [
                    message_plan.recent_blockhash,
                    {
                        "commitment": (
                            COMMITMENT
                        ),
                        "minContextSlot": (
                            final_min_context_slot
                        ),
                    },
                ],
            )

    except Exception as error:
        return finish(
            UNKNOWN,
            (
                "FINAL_BLOCKHASH_RPC_FAILED:"
                f"{type(error).__name__}"
            ),
        )

    if not isinstance(
        blockhash_result,
        dict,
    ):
        return finish(
            UNKNOWN,
            "FINAL_BLOCKHASH_RESULT_INVALID",
        )

    blockhash_context = (
        blockhash_result.get(
            "context"
        )
    )

    if not isinstance(
        blockhash_context,
        dict,
    ):
        return finish(
            UNKNOWN,
            "FINAL_BLOCKHASH_CONTEXT_INVALID",
        )

    final_blockhash_slot = (
        _nonnegative_int(
            blockhash_context.get(
                "slot"
            )
        )
    )

    if (
        final_blockhash_slot
        is None
        or final_blockhash_slot
        < final_min_context_slot
    ):
        return finish(
            UNKNOWN,
            "FINAL_BLOCKHASH_CONTEXT_INVALID",
        )

    blockhash_valid = (
        blockhash_result.get(
            "value"
        )
    )

    if not isinstance(
        blockhash_valid,
        bool,
    ):
        return finish(
            UNKNOWN,
            "FINAL_BLOCKHASH_VALUE_INVALID",
        )

    if not blockhash_valid:
        return finish(
            BLOCK,
            "BLOCKHASH_EXPIRED_BEFORE_SIGNING",
        )

    # -----------------------------------------------------
    # RPC consumed time. Re-read exact ACTIVE claim
    # after the network boundary and before signing.
    #
    # This loader also revalidates the authorization
    # fingerprint and full FIFO allocation.
    # -----------------------------------------------------

    try:
        final_claim_result = (
            load_active_live_sell_inventory_claim_read_only(
                authorization=authorization,
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "FINAL_SELL_CLAIM_LEDGER_READ_FAILED",
        )

    if (
        final_claim_result.status
        == CLAIM_UNKNOWN
    ):
        return finish(
            UNKNOWN,
            "FINAL_SELL_CLAIM_VALIDATION_UNKNOWN",
            *final_claim_result.reasons,
        )

    if (
        final_claim_result.status
        == CLAIM_BLOCK
    ):
        return finish(
            BLOCK,
            "FINAL_SELL_CLAIM_VALIDATION_BLOCKED",
            *final_claim_result.reasons,
        )

    if (
        final_claim_result.status
        != CLAIM_PASS
        or final_claim_result.claim
        is None
    ):
        return finish(
            UNKNOWN,
            "FINAL_SELL_CLAIM_VALIDATION_INVALID",
        )

    if not _claim_matches_authorization(
        claim=final_claim_result.claim,
        authorization=authorization,
    ):
        return finish(
            UNKNOWN,
            "FINAL_SELL_CLAIM_AUTHORITY_MISMATCH",
        )

    # -----------------------------------------------------
    # Produce exactly one real Ed25519 signature over
    # the already-frozen MessageV0 bytes.
    # -----------------------------------------------------

    try:
        signature = signer.sign_message(
            message_bytes
        )

    except Exception:
        return finish(
            UNKNOWN,
            "MESSAGE_SIGNING_FAILED",
        )

    if not isinstance(
        signature,
        Signature,
    ):
        return finish(
            BLOCK,
            "SIGNATURE_TYPE_INVALID",
        )

    if signature == Signature.default():
        return finish(
            BLOCK,
            "DEFAULT_SIGNATURE_REJECTED",
        )

    try:
        signature_valid = (
            signature.verify(
                signer_pubkey,
                message_bytes,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SIGNATURE_VERIFICATION_FAILED",
        )

    if not signature_valid:
        return finish(
            BLOCK,
            "SIGNATURE_VERIFICATION_FAILED",
        )

    try:
        signed_transaction = (
            VersionedTransaction.populate(
                message_plan.message,
                [
                    signature
                ],
            )
        )

    except Exception:
        return finish(
            BLOCK,
            "SIGNED_TRANSACTION_BUILD_FAILED",
        )

    if tuple(
        signed_transaction.signatures
    ) != (
        signature,
    ):
        return finish(
            BLOCK,
            "SIGNED_TRANSACTION_SIGNATURE_MISMATCH",
        )

    try:
        verification_results = (
            signed_transaction
            .verify_with_results()
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SIGNED_TRANSACTION_VERIFICATION_FAILED",
        )

    if verification_results != [
        True
    ]:
        return finish(
            BLOCK,
            "SIGNED_TRANSACTION_VERIFICATION_FAILED",
        )

    try:
        signed_message_bytes = (
            to_bytes_versioned(
                signed_transaction.message
            )
        )

    except Exception:
        return finish(
            BLOCK,
            "SIGNED_MESSAGE_SERIALIZATION_FAILED",
        )

    if signed_message_bytes != message_bytes:
        return finish(
            BLOCK,
            "SIGNED_MESSAGE_MUTATED",
        )

    try:
        candidate_transaction_bytes = bytes(
            signed_transaction
        )

    except Exception:
        return finish(
            BLOCK,
            "SIGNED_TRANSACTION_SERIALIZATION_FAILED",
        )

    if (
        len(
            candidate_transaction_bytes
        )
        != message_plan
        .estimated_signed_transaction_size_bytes
    ):
        return finish(
            BLOCK,
            "SIGNED_TRANSACTION_SIZE_MISMATCH",
        )

    if (
        len(
            candidate_transaction_bytes
        )
        > SOLANA_PACKET_DATA_SIZE
    ):
        return finish(
            BLOCK,
            "SIGNED_TRANSACTION_PACKET_TOO_LARGE",
        )

    candidate_transaction_signature = str(
        signature
    )

    candidate_transaction_sha256 = (
        hashlib.sha256(
            candidate_transaction_bytes
        ).hexdigest()
    )

    # -----------------------------------------------------
    # Cryptographic bytes now exist, but they MUST NOT
    # escape unless the durable SELL execution ledger
    # accepts the exact artifact.
    #
    # The binder takes BEGIN IMMEDIATE and revalidates
    # the exact ACTIVE claim under the write reservation,
    # closing the final claim-terminalization race.
    # -----------------------------------------------------

    try:
        transition = (
            bind_live_sell_signed_artifact(
                authorization=authorization,
                message_plan=message_plan,
                transaction_signature=(
                    candidate_transaction_signature
                ),
                signed_transaction_bytes=(
                    candidate_transaction_bytes
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_EXECUTION_SIGN_BIND_FAILED",
        )

    if transition.status != EXECUTION_PASS:
        transition_status = (
            BLOCK
            if transition.status
            == EXECUTION_BLOCK
            else UNKNOWN
        )

        return finish(
            transition_status,
            "SELL_EXECUTION_SIGN_BIND_FAILED",
            *transition.reasons,
        )

    persisted = transition.record

    if persisted is None:
        return finish(
            UNKNOWN,
            "SIGNED_SELL_EXECUTION_RECORD_MISSING",
        )

    if (
        not _record_matches_exact_plan(
            record=persisted,
            authorization=authorization,
            message_plan=message_plan,
        )
        or persisted.status
        != EXECUTION_SIGNED
        or persisted.transaction_signature
        != candidate_transaction_signature
        or persisted.signed_transaction_sha256
        != candidate_transaction_sha256
        or persisted.signed_transaction_bytes
        != candidate_transaction_bytes
    ):
        return finish(
            UNKNOWN,
            "PERSISTED_SIGNED_ARTIFACT_MISMATCH",
        )

    # -----------------------------------------------------
    # Only after durable exact readback may the signed
    # bytes become visible downstream.
    # -----------------------------------------------------

    persisted_transaction_signature = (
        persisted.transaction_signature
    )

    persisted_transaction_sha256 = (
        persisted.signed_transaction_sha256
    )

    persisted_transaction_bytes = (
        persisted.signed_transaction_bytes
    )

    execution_record_changed = (
        transition.changed
    )

    signed_at = persisted.signed_at

    blockhash_context_version = (
        persisted.blockhash_context_version
    )

    last_valid_block_height = (
        persisted.last_valid_block_height
    )

    blockhash_rpc_slot = (
        persisted.blockhash_rpc_slot
    )

    return finish(
        PASS,
    )
