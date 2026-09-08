from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solders.message import to_bytes_versioned

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
from src.execution.pump_buy_v2_unsigned_message import (
    PUMP_BUY_V2_UNSIGNED_MESSAGE_VERSION,
    PumpBuyV2UnsignedMessagePlan,
)
from src.portfolio.live_reservations import (
    ACTIVE,
    DB_PATH as LIVE_RESERVATION_DB_PATH,
    RESERVATION_VERSION,
    load_capital_reservation,
)
from src.safety.token_safety_resolver import (
    COMMITMENT,
    HeliusRpcClient,
)


PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION = (
    "pump-buy-v2-pre-sign-validation-v1"
)

APPROVE = "APPROVE"
DENY = "DENY"
UNKNOWN = "UNKNOWN"

U64_MAX = (1 << 64) - 1


@dataclass(frozen=True)
class PumpBuyV2PreSignValidation:
    validator_version: str

    status: str
    reasons: tuple[str, ...]

    reservation_id: str
    message_sha256: str | None
    payer: str | None

    min_context_slot: int | None

    blockhash_valid_slot: int | None
    fee_rpc_slot: int | None

    rpc_total_fee_lamports: int | None
    actual_base_fee_lamports: int | None

    authorized_base_fee_lamports: int | None
    authorized_priority_fee_lamports: int | None
    authorized_rent_lamports: int | None

    authorized_wallet_liability_lamports: (
        int | None
    )

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


async def validate_pump_buy_v2_pre_sign(
    *,
    authorization: OrderAuthorization,
    context: PumpBuyV2AccountContext,
    message_plan: PumpBuyV2UnsignedMessagePlan,
    db_path: Path = LIVE_RESERVATION_DB_PATH,
) -> PumpBuyV2PreSignValidation:
    """
    Final ledger/message/network-fee validation before
    any signer is allowed to touch a Pump BUY message.

    This validator does NOT:
      - load a private key
      - sign a transaction
      - submit a transaction
      - validate CPI account-creation rent
      - execute/simulate the Pump program

    CPI/rent/program preflight is a separate boundary
    that must also pass before signing.
    """

    checked_at = time.time()

    message_sha256: str | None = None
    payer: str | None = None

    min_context_slot: int | None = None

    blockhash_valid_slot: int | None = None
    fee_rpc_slot: int | None = None

    rpc_total_fee_lamports: int | None = None
    actual_base_fee_lamports: int | None = None

    authorized_base_fee_lamports: (
        int | None
    ) = None

    authorized_priority_fee_lamports: (
        int | None
    ) = None

    authorized_rent_lamports: (
        int | None
    ) = None

    authorized_wallet_liability_lamports: (
        int | None
    ) = None

    def finish(
        status: str,
        *reasons: str,
    ) -> PumpBuyV2PreSignValidation:

        return PumpBuyV2PreSignValidation(
            validator_version=(
                PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            reservation_id=(
                authorization.reservation_id
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
            authorized_rent_lamports=(
                authorized_rent_lamports
            ),
            authorized_wallet_liability_lamports=(
                authorized_wallet_liability_lamports
            ),
            checked_at=checked_at,
        )

    #
    # Authorization contract.
    #
    if (
        authorization.authorization_version
        != AUTHORIZATION_VERSION
    ):
        return finish(
            DENY,
            "AUTHORIZATION_VERSION_MISMATCH",
        )

    if authorization.status != AUTHORIZE:
        return finish(
            DENY,
            "ORDER_NOT_AUTHORIZED",
        )

    if authorization.side != BUY:
        return finish(
            DENY,
            "SIDE_NOT_BUY",
        )

    if not authorization.is_valid():
        return finish(
            DENY,
            "AUTHORIZATION_EXPIRED",
        )

    #
    # Downstream artifact versions.
    #
    if (
        context.resolver_version
        != PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION
    ):
        return finish(
            DENY,
            "ACCOUNT_CONTEXT_VERSION_MISMATCH",
        )

    if (
        message_plan.builder_version
        != PUMP_BUY_V2_UNSIGNED_MESSAGE_VERSION
    ):
        return finish(
            DENY,
            "MESSAGE_PLAN_VERSION_MISMATCH",
        )

    #
    # Identity/fingerprint chain.
    #
    if (
        context.authorization_version
        != authorization.authorization_version
        or message_plan.authorization_version
        != authorization.authorization_version
    ):
        return finish(
            DENY,
            "AUTHORIZATION_BINDING_MISMATCH",
        )

    if (
        context.reservation_id
        != authorization.reservation_id
        or message_plan.reservation_id
        != authorization.reservation_id
    ):
        return finish(
            DENY,
            "RESERVATION_BINDING_MISMATCH",
        )

    if (
        context.simulation_sha256
        != authorization.simulation_sha256
        or message_plan.simulation_sha256
        != authorization.simulation_sha256
    ):
        return finish(
            DENY,
            "SIMULATION_BINDING_MISMATCH",
        )

    payer = (
        authorization.wallet_pubkey
    )

    if (
        not payer
        or context.user != payer
        or message_plan.payer != payer
    ):
        return finish(
            DENY,
            "PAYER_BINDING_MISMATCH",
        )

    #
    # Exact economics authorized for the message.
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

    authorized_rent_lamports = (
        _nonnegative_u64(
            authorization
            .rent_lamports
        )
    )

    max_sol_cost = _positive_u64(
        authorization.max_sol_cost
    )

    wallet_liability = _positive_u64(
        authorization.wallet_cost_lamports
    )

    if (
        authorized_base_fee_lamports is None
        or authorized_priority_fee_lamports is None
        or authorized_rent_lamports is None
        or max_sol_cost is None
        or wallet_liability is None
    ):
        return finish(
            DENY,
            "AUTHORIZED_ECONOMICS_INVALID",
        )

    authorized_wallet_liability_lamports = (
        max_sol_cost
        + authorized_base_fee_lamports
        + authorized_priority_fee_lamports
        + authorized_rent_lamports
    )

    if (
        authorized_wallet_liability_lamports
        > U64_MAX
    ):
        return finish(
            DENY,
            "AUTHORIZED_WALLET_LIABILITY_OVERFLOW",
        )

    if (
        wallet_liability
        != authorized_wallet_liability_lamports
    ):
        return finish(
            DENY,
            "AUTHORIZED_WALLET_LIABILITY_MISMATCH",
        )

    if (
        authorization.spend_lamports
        != max_sol_cost
    ):
        return finish(
            DENY,
            "AUTHORIZED_SPEND_CEILING_MISMATCH",
        )

    if (
        message_plan
        .authorized_priority_fee_lamports
        != authorized_priority_fee_lamports
        or message_plan
        .planned_priority_fee_lamports
        != authorized_priority_fee_lamports
    ):
        return finish(
            DENY,
            "MESSAGE_PRIORITY_FEE_MISMATCH",
        )

    #
    # Recompute the actual immutable message hash.
    #
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
        message_sha256
        != message_plan.message_sha256
    ):
        return finish(
            DENY,
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
            DENY,
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
            DENY,
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
            DENY,
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
            DENY,
            "MESSAGE_LOOKUP_TABLES_NOT_AUTHORIZED",
        )

    #
    # Authoritative ledger re-read.
    #
    try:
        reservation = (
            load_capital_reservation(
                reservation_id=(
                    authorization
                    .reservation_id
                ),
                db_path=db_path,
            )
        )

    except Exception as error:
        return finish(
            UNKNOWN,
            (
                "RESERVATION_LEDGER_READ_FAILED:"
                f"{type(error).__name__}"
            ),
        )

    if reservation is None:
        return finish(
            DENY,
            "RESERVATION_NOT_FOUND",
        )

    if (
        reservation.reservation_version
        != RESERVATION_VERSION
        or authorization.reservation_version
        != RESERVATION_VERSION
    ):
        return finish(
            DENY,
            "RESERVATION_VERSION_MISMATCH",
        )

    if reservation.status != ACTIVE:
        return finish(
            DENY,
            "RESERVATION_NOT_ACTIVE",
        )

    if (
        reservation.signed_at is not None
        or reservation.transaction_signature
    ):
        return finish(
            DENY,
            "RESERVATION_ALREADY_BOUND",
        )

    if (
        reservation.wallet_pubkey
        != payer
        or reservation.mint
        != authorization.mint
        or reservation.side
        != authorization.side
    ):
        return finish(
            DENY,
            "RESERVATION_IDENTITY_MISMATCH",
        )

    if (
        reservation.spend_lamports
        != authorization.spend_lamports
        or reservation.wallet_cost_lamports
        != wallet_liability
    ):
        return finish(
            DENY,
            "RESERVATION_ECONOMICS_MISMATCH",
        )

    if (
        reservation.risk_simulation_sha256
        != authorization.simulation_sha256
    ):
        return finish(
            DENY,
            "RESERVATION_SIMULATION_MISMATCH",
        )

    if (
        authorization.risk_governor_version
        != reservation.risk_governor_version
    ):
        return finish(
            DENY,
            "RESERVATION_RISK_VERSION_MISMATCH",
        )

    #
    # The RPC node may not answer from chain state
    # older than either transaction-critical state
    # snapshot.
    #
    curve_slot = _nonnegative_u64(
        authorization.curve_rpc_slot
    )

    global_slot = _nonnegative_u64(
        context.global_rpc_slot
    )

    if (
        curve_slot is None
        or global_slot is None
    ):
        return finish(
            DENY,
            "STATE_SLOT_INVALID",
        )

    min_context_slot = max(
        curve_slot,
        global_slot,
    )

    encoded_message = (
        base64.b64encode(
            message_bytes
        ).decode(
            "ascii"
        )
    )

    #
    # Live chain checks.
    #
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
    # null means the node cannot currently quote
    # this message at its embedded blockhash.
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

    if rpc_total_fee_lamports is None:
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
