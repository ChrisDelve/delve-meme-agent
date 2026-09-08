from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from solders.message import to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from src.execution.live_blockhash_context import (
    LIVE_BLOCKHASH_CONTEXT_VERSION,
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
from src.execution.pump_buy_v2_execution_preflight import (
    APPROVE,
    PUMP_BUY_V2_EXECUTION_PREFLIGHT_VERSION,
    PumpBuyV2ExecutionPreflight,
)
from src.execution.pump_buy_v2_pre_sign_validation import (
    PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION,
    PumpBuyV2PreSignValidation,
)
from src.execution.pump_buy_v2_unsigned_message import (
    PUMP_BUY_V2_UNSIGNED_MESSAGE_VERSION,
    SOLANA_PACKET_DATA_SIZE,
    PumpBuyV2UnsignedMessagePlan,
)
from src.safety.token_safety_resolver import (
    COMMITMENT,
    HeliusRpcClient,
)
from src.portfolio.live_reservations import (
    ACTIVE,
    DB_PATH as LIVE_RESERVATION_DB_PATH,
    RESERVATION_VERSION,
    SIGNED,
    bind_reservation_signed_transaction,
    load_capital_reservation,
)


PUMP_BUY_V2_SIGNING_VERSION = (
    "pump-buy-v2-signing-v2"
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
class PumpBuyV2SigningResult:
    signer_version: str

    status: str
    reasons: tuple[str, ...]

    reservation_id: str

    signer_pubkey: str | None

    transaction_signature: str | None

    message_sha256: str | None
    signed_transaction_sha256: str | None
    signed_transaction_bytes: bytes | None

    reservation_changed: bool | None
    signed_at: float | None

    blockhash_context_version: str | None = None
    last_valid_block_height: int | None = None
    blockhash_rpc_slot: int | None = None

    @property
    def is_durably_signed(self) -> bool:
        return (
            self.status == PASS
            and self.transaction_signature is not None
            and self.message_sha256 is not None
            and self.signed_transaction_sha256 is not None
            and self.signed_transaction_bytes is not None
            and self.signed_at is not None
        )


async def sign_and_bind_pump_buy_v2(
    *,
    authorization: OrderAuthorization,
    context: PumpBuyV2AccountContext,
    message_plan: PumpBuyV2UnsignedMessagePlan,
    network_validation: PumpBuyV2PreSignValidation,
    execution_preflight: PumpBuyV2ExecutionPreflight,
    signer: MessageSigner,
    db_path: Path = LIVE_RESERVATION_DB_PATH,
) -> PumpBuyV2SigningResult:
    """
    Sign the already-authorized, already-preflighted
    Pump BUY message and durably bind that exact
    signed transaction to Reservation v5.

    This function does not submit a transaction.

    A signed transaction is exposed to the caller
    only after the live-capital ledger confirms the
    exact recoverable artifact is durably SIGNED.
    """

    signer_pubkey_string: str | None = None

    message_sha256: str | None = None

    persisted_transaction_signature: (
        str | None
    ) = None

    persisted_transaction_sha256: (
        str | None
    ) = None

    persisted_transaction_bytes: (
        bytes | None
    ) = None

    reservation_changed: bool | None = None
    signed_at: float | None = None

    blockhash_context_version: str | None = None
    last_valid_block_height: int | None = None
    blockhash_rpc_slot: int | None = None

    def finish(
        status: str,
        *reasons: str,
    ) -> PumpBuyV2SigningResult:

        return PumpBuyV2SigningResult(
            signer_version=(
                PUMP_BUY_V2_SIGNING_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            reservation_id=(
                authorization.reservation_id
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
            blockhash_context_version=(
                blockhash_context_version
            ),
            last_valid_block_height=(
                last_valid_block_height
            ),
            blockhash_rpc_slot=(
                blockhash_rpc_slot
            ),
            signed_transaction_sha256=(
                persisted_transaction_sha256
            ),
            signed_transaction_bytes=(
                persisted_transaction_bytes
            ),
            reservation_changed=(
                reservation_changed
            ),
            signed_at=signed_at,
        )

    #
    # Upstream authorization.
    #
    if (
        authorization.authorization_version
        != AUTHORIZATION_VERSION
    ):
        return finish(
            BLOCK,
            "AUTHORIZATION_VERSION_MISMATCH",
        )

    if (
        authorization.status != AUTHORIZE
        or authorization.side != BUY
    ):
        return finish(
            BLOCK,
            "AUTHORIZATION_INVALID",
        )

    try:
        authorization_valid = (
            authorization.is_valid()
        )

    except Exception:
        return finish(
            UNKNOWN,
            "AUTHORIZATION_VALIDATION_FAILED",
        )

    if not authorization_valid:
        return finish(
            BLOCK,
            "AUTHORIZATION_EXPIRED",
        )

    #
    # Artifact versions.
    #
    if (
        context.resolver_version
        != PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION
    ):
        return finish(
            BLOCK,
            "ACCOUNT_CONTEXT_VERSION_MISMATCH",
        )

    if (
        message_plan.builder_version
        != PUMP_BUY_V2_UNSIGNED_MESSAGE_VERSION
    ):
        return finish(
            BLOCK,
            "MESSAGE_PLAN_VERSION_MISMATCH",
        )

    if (
        network_validation.validator_version
        != PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION
        or network_validation.status != APPROVE
        or not network_validation.allows_signing
    ):
        return finish(
            BLOCK,
            "NETWORK_PRE_SIGN_NOT_APPROVED",
        )

    if (
        execution_preflight.validator_version
        != PUMP_BUY_V2_EXECUTION_PREFLIGHT_VERSION
        or execution_preflight.status != APPROVE
        or not execution_preflight.allows_signing
    ):
        return finish(
            BLOCK,
            "EXECUTION_PREFLIGHT_NOT_APPROVED",
        )

    #
    # Signing is authorized only for the exact
    # expiry-aware message contract approved by
    # both upstream validation stages.
    #
    plan_blockhash_context_version = getattr(
        message_plan,
        "blockhash_context_version",
        None,
    )

    plan_last_valid_block_height = getattr(
        message_plan,
        "last_valid_block_height",
        None,
    )

    plan_blockhash_rpc_slot = getattr(
        message_plan,
        "blockhash_rpc_slot",
        None,
    )

    if (
        plan_blockhash_context_version
        != LIVE_BLOCKHASH_CONTEXT_VERSION
        or not isinstance(
            plan_last_valid_block_height,
            int,
        )
        or isinstance(
            plan_last_valid_block_height,
            bool,
        )
        or plan_last_valid_block_height < 0
        or not isinstance(
            plan_blockhash_rpc_slot,
            int,
        )
        or isinstance(
            plan_blockhash_rpc_slot,
            bool,
        )
        or plan_blockhash_rpc_slot < 0
    ):
        return finish(
            BLOCK,
            "BLOCKHASH_EXPIRY_PROVENANCE_INVALID",
        )

    if (
        getattr(
            network_validation,
            "blockhash_context_version",
            None,
        )
        != plan_blockhash_context_version
        or getattr(
            network_validation,
            "last_valid_block_height",
            None,
        )
        != plan_last_valid_block_height
        or getattr(
            network_validation,
            "blockhash_rpc_slot",
            None,
        )
        != plan_blockhash_rpc_slot
        or getattr(
            execution_preflight,
            "blockhash_context_version",
            None,
        )
        != plan_blockhash_context_version
        or getattr(
            execution_preflight,
            "last_valid_block_height",
            None,
        )
        != plan_last_valid_block_height
        or getattr(
            execution_preflight,
            "blockhash_rpc_slot",
            None,
        )
        != plan_blockhash_rpc_slot
    ):
        return finish(
            BLOCK,
            "BLOCKHASH_EXPIRY_BINDING_MISMATCH",
        )

    blockhash_context_version = (
        plan_blockhash_context_version
    )
    last_valid_block_height = (
        plan_last_valid_block_height
    )
    blockhash_rpc_slot = (
        plan_blockhash_rpc_slot
    )

    #
    # Exact reservation chain.
    #
    reservation_id = (
        authorization.reservation_id
    )

    if (
        context.reservation_id
        != reservation_id
        or message_plan.reservation_id
        != reservation_id
        or network_validation.reservation_id
        != reservation_id
        or execution_preflight.reservation_id
        != reservation_id
    ):
        return finish(
            BLOCK,
            "RESERVATION_BINDING_MISMATCH",
        )

    #
    # Exact simulation / transaction identity.
    #
    if (
        context.authorization_version
        != authorization.authorization_version
        or message_plan.authorization_version
        != authorization.authorization_version
    ):
        return finish(
            BLOCK,
            "AUTHORIZATION_BINDING_MISMATCH",
        )

    if (
        context.simulation_sha256
        != authorization.simulation_sha256
        or message_plan.simulation_sha256
        != authorization.simulation_sha256
    ):
        return finish(
            BLOCK,
            "SIMULATION_BINDING_MISMATCH",
        )

    payer = authorization.wallet_pubkey

    if (
        not payer
        or context.user != payer
        or message_plan.payer != payer
        or network_validation.payer != payer
    ):
        return finish(
            BLOCK,
            "PAYER_BINDING_MISMATCH",
        )

    if (
        network_validation
        .authorized_wallet_liability_lamports
        != authorization.wallet_cost_lamports
        or execution_preflight
        .authorized_wallet_liability_lamports
        != authorization.wallet_cost_lamports
    ):
        return finish(
            BLOCK,
            "WALLET_LIABILITY_BINDING_MISMATCH",
        )

    #
    # Freeze and fingerprint the exact bytes that
    # the signer will sign.
    #
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

    message_sha256 = hashlib.sha256(
        message_bytes
    ).hexdigest()

    if (
        message_sha256
        != message_plan.message_sha256
        or message_sha256
        != network_validation.message_sha256
        or message_sha256
        != execution_preflight.message_sha256
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
            account_keys[
                0
            ]
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

    #
    # Authoritative Reservation v5 reread.
    #
    try:
        reservation = (
            load_capital_reservation(
                reservation_id=(
                    reservation_id
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "RESERVATION_LEDGER_READ_FAILED",
        )

    if reservation is None:
        return finish(
            BLOCK,
            "RESERVATION_NOT_FOUND",
        )

    if (
        reservation.reservation_version
        != RESERVATION_VERSION
        or authorization.reservation_version
        != RESERVATION_VERSION
    ):
        return finish(
            BLOCK,
            "RESERVATION_VERSION_MISMATCH",
        )

    if reservation.status != ACTIVE:
        return finish(
            BLOCK,
            "RESERVATION_NOT_ACTIVE",
        )

    if (
        reservation.signed_at is not None
        or reservation.transaction_signature
        is not None
        or reservation.signed_message_sha256
        is not None
        or reservation.signed_transaction_sha256
        is not None
        or reservation.signed_transaction_bytes
        is not None
    ):
        return finish(
            BLOCK,
            "ACTIVE_RESERVATION_HAS_SIGNED_ARTIFACT",
        )

    if (
        reservation.wallet_pubkey != payer
        or reservation.mint
        != authorization.mint
        or reservation.side
        != authorization.side
    ):
        return finish(
            BLOCK,
            "RESERVATION_IDENTITY_MISMATCH",
        )

    if (
        reservation.spend_lamports
        != authorization.spend_lamports
        or reservation.wallet_cost_lamports
        != authorization.wallet_cost_lamports
    ):
        return finish(
            BLOCK,
            "RESERVATION_ECONOMICS_MISMATCH",
        )

    if (
        reservation.risk_governor_version
        != authorization.risk_governor_version
        or reservation.risk_simulation_sha256
        != authorization.simulation_sha256
    ):
        return finish(
            BLOCK,
            "RESERVATION_RISK_BINDING_MISMATCH",
        )

    #
    # Only now touch the signer.
    #
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

    #
    # Final point-in-time blockhash validity check.
    #
    # This occurs after all deterministic validation
    # and immediately before the final authority
    # reread + cryptographic signature.
    #
    slot_candidates = (
        getattr(
            network_validation,
            "min_context_slot",
            None,
        ),
        getattr(
            network_validation,
            "blockhash_valid_slot",
            None,
        ),
        getattr(
            network_validation,
            "fee_rpc_slot",
            None,
        ),
        getattr(
            execution_preflight,
            "simulation_slot",
            None,
        ),
    )

    parsed_slots: list[int] = []

    for slot in slot_candidates:
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
            return finish(
                BLOCK,
                "FINAL_BLOCKHASH_CONTEXT_INVALID",
            )

        parsed_slots.append(
            slot
        )

    final_min_context_slot = max(
        parsed_slots
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
        blockhash_context.get(
            "slot"
        )
    )

    if (
        not isinstance(
            final_blockhash_slot,
            int,
        )
        or isinstance(
            final_blockhash_slot,
            bool,
        )
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

    #
    # The network call above can consume time.
    # Revalidate authorization and authoritative
    # reservation state again after it returns.
    #
    try:
        authorization_still_valid = (
            authorization.is_valid()
        )

    except Exception:
        return finish(
            UNKNOWN,
            "AUTHORIZATION_REVALIDATION_FAILED",
        )

    if not authorization_still_valid:
        return finish(
            BLOCK,
            "AUTHORIZATION_EXPIRED_BEFORE_SIGNING",
        )

    try:
        final_reservation = (
            load_capital_reservation(
                reservation_id=(
                    reservation_id
                ),
                db_path=db_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "FINAL_RESERVATION_LEDGER_READ_FAILED",
        )

    if final_reservation is None:
        return finish(
            BLOCK,
            "RESERVATION_NOT_FOUND",
        )

    if (
        final_reservation.reservation_version
        != RESERVATION_VERSION
    ):
        return finish(
            BLOCK,
            "RESERVATION_VERSION_MISMATCH",
        )

    if final_reservation.status != ACTIVE:
        return finish(
            BLOCK,
            "RESERVATION_NOT_ACTIVE",
        )

    if (
        final_reservation.signed_at
        is not None
        or final_reservation
        .transaction_signature
        is not None
        or final_reservation
        .signed_message_sha256
        is not None
        or final_reservation
        .signed_transaction_sha256
        is not None
        or final_reservation
        .signed_transaction_bytes
        is not None
    ):
        return finish(
            BLOCK,
            "ACTIVE_RESERVATION_HAS_SIGNED_ARTIFACT",
        )

    if (
        final_reservation.wallet_pubkey
        != payer
        or final_reservation.mint
        != authorization.mint
        or final_reservation.side
        != authorization.side
    ):
        return finish(
            BLOCK,
            "RESERVATION_IDENTITY_MISMATCH",
        )

    if (
        final_reservation.spend_lamports
        != authorization.spend_lamports
        or final_reservation
        .wallet_cost_lamports
        != authorization.wallet_cost_lamports
    ):
        return finish(
            BLOCK,
            "RESERVATION_ECONOMICS_MISMATCH",
        )

    if (
        final_reservation
        .risk_governor_version
        != authorization.risk_governor_version
        or final_reservation
        .risk_simulation_sha256
        != authorization.simulation_sha256
    ):
        return finish(
            BLOCK,
            "RESERVATION_RISK_BINDING_MISMATCH",
        )

    #
    # Produce one real signature over the exact
    # versioned message bytes.
    #
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
        signature_valid = signature.verify(
            signer_pubkey,
            message_bytes,
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

    #
    # Attach only that verified signature to the
    # exact already-frozen MessageV0.
    #
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

    #
    # The transaction now exists cryptographically,
    # but must not escape this function unless the
    # exact artifact is durably bound to Reservation
    # v5 first.
    #
    try:
        transition = (
            bind_reservation_signed_transaction(
                reservation_id=(
                    reservation_id
                ),
                transaction_signature=(
                    candidate_transaction_signature
                ),
                signed_message_sha256=(
                    message_sha256
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
            "RESERVATION_SIGN_BIND_FAILED",
        )

    if transition.status != PASS:
        transition_status = (
            BLOCK
            if transition.status == BLOCK
            else UNKNOWN
        )

        return finish(
            transition_status,
            "RESERVATION_SIGN_BIND_FAILED",
            *transition.reasons,
        )

    persisted = transition.reservation

    if persisted is None:
        return finish(
            UNKNOWN,
            "SIGNED_RESERVATION_MISSING",
        )

    if (
        persisted.reservation_version
        != RESERVATION_VERSION
        or persisted.status != SIGNED
        or persisted.transaction_signature
        != candidate_transaction_signature
        or persisted.signed_message_sha256
        != message_sha256
        or persisted.signed_transaction_sha256
        != candidate_transaction_sha256
        or persisted.signed_transaction_bytes
        != candidate_transaction_bytes
        or persisted.signed_at is None
    ):
        return finish(
            UNKNOWN,
            "PERSISTED_SIGNED_ARTIFACT_MISMATCH",
        )

    #
    # Only after durable ledger confirmation may
    # signed bytes become visible to downstream code.
    #
    persisted_transaction_signature = (
        candidate_transaction_signature
    )

    persisted_transaction_sha256 = (
        candidate_transaction_sha256
    )

    persisted_transaction_bytes = (
        candidate_transaction_bytes
    )

    reservation_changed = (
        transition.changed
    )

    signed_at = persisted.signed_at

    return finish(
        PASS,
    )
