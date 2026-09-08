from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass

from solders.message import (
    to_bytes_versioned,
)
from solders.signature import Signature
from solders.transaction import (
    VersionedTransaction,
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
from src.execution.pump_buy_v2_pre_sign_validation import (
    APPROVE,
    PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION,
    PumpBuyV2PreSignValidation,
)
from src.execution.pump_buy_v2_unsigned_message import (
    PUMP_BUY_V2_UNSIGNED_MESSAGE_VERSION,
    PumpBuyV2UnsignedMessagePlan,
)
from src.safety.token_safety_resolver import (
    COMMITMENT,
    HeliusRpcClient,
)


PUMP_BUY_V2_EXECUTION_PREFLIGHT_VERSION = (
    "pump-buy-v2-execution-preflight-v1"
)

DENY = "DENY"
UNKNOWN = "UNKNOWN"

U64_MAX = (1 << 64) - 1


@dataclass(frozen=True)
class PumpBuyV2ExecutionPreflight:
    validator_version: str

    status: str
    reasons: tuple[str, ...]

    reservation_id: str

    message_sha256: str | None

    simulation_slot: int | None

    units_consumed: int | None
    compute_unit_limit: int | None

    simulated_fee_lamports: int | None

    payer_pre_balance_lamports: int | None
    payer_post_balance_lamports: int | None
    payer_debit_lamports: int | None

    non_fee_payer_debit_lamports: int | None

    authorized_wallet_liability_lamports: (
        int | None
    )

    authorized_non_fee_liability_lamports: (
        int | None
    )

    simulation_error: str | None
    logs: tuple[str, ...]

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

    parsed = _nonnegative_u64(
        value
    )

    if (
        parsed is None
        or parsed <= 0
    ):
        return None

    return parsed


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

    return _nonnegative_u64(
        slot
    )


def _balance_vector(
    value: object,
    *,
    expected_length: int,
) -> tuple[int, ...] | None:

    if (
        not isinstance(
            value,
            list,
        )
        or len(
            value
        ) != expected_length
    ):
        return None

    parsed: list[int] = []

    for item in value:
        amount = _nonnegative_u64(
            item
        )

        if amount is None:
            return None

        parsed.append(
            amount
        )

    return tuple(
        parsed
    )


async def preflight_pump_buy_v2_execution(
    *,
    authorization: OrderAuthorization,
    context: PumpBuyV2AccountContext,
    message_plan: PumpBuyV2UnsignedMessagePlan,
    network_validation: PumpBuyV2PreSignValidation,
) -> PumpBuyV2ExecutionPreflight:
    """
    Simulate the exact Pump BUY transaction envelope
    before any valid signature exists.

    A default zero signature is inserted solely to
    produce the wire transaction required by the RPC.

    `sigVerify` is explicitly false.

    No private key, Keypair, signing operation, or
    transaction submission occurs here.
    """

    checked_at = time.time()

    message_sha256: str | None = None

    simulation_slot: int | None = None

    units_consumed: int | None = None

    compute_unit_limit: int | None = None

    simulated_fee_lamports: int | None = None

    payer_pre_balance_lamports: int | None = None

    payer_post_balance_lamports: int | None = None

    payer_debit_lamports: int | None = None

    non_fee_payer_debit_lamports: (
        int | None
    ) = None

    authorized_wallet_liability_lamports: (
        int | None
    ) = None

    authorized_non_fee_liability_lamports: (
        int | None
    ) = None

    simulation_error: str | None = None

    logs: tuple[str, ...] = ()

    def finish(
        status: str,
        *reasons: str,
    ) -> PumpBuyV2ExecutionPreflight:

        return PumpBuyV2ExecutionPreflight(
            validator_version=(
                PUMP_BUY_V2_EXECUTION_PREFLIGHT_VERSION
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
            simulation_slot=(
                simulation_slot
            ),
            units_consumed=(
                units_consumed
            ),
            compute_unit_limit=(
                compute_unit_limit
            ),
            simulated_fee_lamports=(
                simulated_fee_lamports
            ),
            payer_pre_balance_lamports=(
                payer_pre_balance_lamports
            ),
            payer_post_balance_lamports=(
                payer_post_balance_lamports
            ),
            payer_debit_lamports=(
                payer_debit_lamports
            ),
            non_fee_payer_debit_lamports=(
                non_fee_payer_debit_lamports
            ),
            authorized_wallet_liability_lamports=(
                authorized_wallet_liability_lamports
            ),
            authorized_non_fee_liability_lamports=(
                authorized_non_fee_liability_lamports
            ),
            simulation_error=(
                simulation_error
            ),
            logs=logs,
            checked_at=checked_at,
        )

    #
    # Upstream authority contracts.
    #
    if (
        authorization.authorization_version
        != AUTHORIZATION_VERSION
        or authorization.status != AUTHORIZE
        or authorization.side != BUY
        or not authorization.is_valid()
    ):
        return finish(
            DENY,
            "AUTHORIZATION_INVALID",
        )

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

    if (
        network_validation.validator_version
        != PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION
        or network_validation.status
        != APPROVE
        or not network_validation.allows_signing
    ):
        return finish(
            DENY,
            "NETWORK_PRE_SIGN_NOT_APPROVED",
        )

    #
    # Exact artifact chain.
    #
    if (
        context.reservation_id
        != authorization.reservation_id
        or message_plan.reservation_id
        != authorization.reservation_id
        or network_validation.reservation_id
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

    if (
        context.user
        != authorization.wallet_pubkey
        or message_plan.payer
        != authorization.wallet_pubkey
        or network_validation.payer
        != authorization.wallet_pubkey
    ):
        return finish(
            DENY,
            "PAYER_BINDING_MISMATCH",
        )

    #
    # Recompute the exact message identity again.
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
        or message_sha256
        != network_validation.message_sha256
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
        != authorization.wallet_pubkey
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
    # Economics.
    #
    max_sol_cost = _positive_u64(
        authorization.max_sol_cost
    )

    authorized_rent = _nonnegative_u64(
        authorization.rent_lamports
    )

    wallet_liability = _positive_u64(
        authorization.wallet_cost_lamports
    )

    priority_fee = _nonnegative_u64(
        authorization.priority_fee_lamports
    )

    base_fee = _nonnegative_u64(
        authorization.base_network_fee_lamports
    )

    compute_unit_limit = _positive_u64(
        message_plan.compute_unit_limit
    )

    if (
        max_sol_cost is None
        or authorized_rent is None
        or wallet_liability is None
        or priority_fee is None
        or base_fee is None
        or compute_unit_limit is None
    ):
        return finish(
            DENY,
            "AUTHORIZED_ECONOMICS_INVALID",
        )

    authorized_wallet_liability_lamports = (
        wallet_liability
    )

    authorized_non_fee_liability_lamports = (
        max_sol_cost
        + authorized_rent
    )

    if (
        authorized_non_fee_liability_lamports
        > U64_MAX
    ):
        return finish(
            DENY,
            "NON_FEE_LIABILITY_OVERFLOW",
        )

    if (
        wallet_liability
        != (
            max_sol_cost
            + authorized_rent
            + priority_fee
            + base_fee
        )
    ):
        return finish(
            DENY,
            "AUTHORIZED_WALLET_LIABILITY_MISMATCH",
        )

    if (
        network_validation
        .authorized_wallet_liability_lamports
        != wallet_liability
        or network_validation
        .authorized_priority_fee_lamports
        != priority_fee
        or network_validation
        .authorized_base_fee_lamports
        != base_fee
        or network_validation
        .authorized_rent_lamports
        != authorized_rent
    ):
        return finish(
            DENY,
            "NETWORK_VALIDATION_ECONOMICS_MISMATCH",
        )

    expected_rpc_fee = _nonnegative_u64(
        network_validation
        .rpc_total_fee_lamports
    )

    if expected_rpc_fee is None:
        return finish(
            DENY,
            "NETWORK_VALIDATION_FEE_INVALID",
        )

    #
    # Build an intentionally INVALID-signature
    # transaction envelope.
    #
    # This is not signing. Signature.default() is a
    # fixed all-zero placeholder.
    #
    placeholder_signature = (
        Signature.default()
    )

    try:
        placeholder_transaction = (
            VersionedTransaction.populate(
                message_plan.message,
                [
                    placeholder_signature
                ],
            )
        )

    except Exception:
        return finish(
            DENY,
            "PLACEHOLDER_TRANSACTION_BUILD_FAILED",
        )

    if tuple(
        placeholder_transaction.signatures
    ) != (
        placeholder_signature,
    ):
        return finish(
            DENY,
            "PLACEHOLDER_SIGNATURE_MISMATCH",
        )

    try:
        transaction_bytes = bytes(
            placeholder_transaction
        )

    except Exception:
        return finish(
            DENY,
            "PLACEHOLDER_TRANSACTION_SERIALIZATION_FAILED",
        )

    if (
        len(
            transaction_bytes
        )
        != message_plan
        .estimated_signed_transaction_size_bytes
    ):
        return finish(
            DENY,
            "TRANSACTION_SIZE_BINDING_MISMATCH",
        )

    encoded_transaction = (
        base64.b64encode(
            transaction_bytes
        ).decode(
            "ascii"
        )
    )

    #
    # Simulation must occur at least as late as all
    # network-validation observations.
    #
    slot_candidates = (
        network_validation.min_context_slot,
        network_validation.blockhash_valid_slot,
        network_validation.fee_rpc_slot,
    )

    parsed_slots: list[int] = []

    for slot in slot_candidates:
        parsed = _nonnegative_u64(
            slot
        )

        if parsed is None:
            return finish(
                DENY,
                "NETWORK_VALIDATION_SLOT_INVALID",
            )

        parsed_slots.append(
            parsed
        )

    min_context_slot = max(
        parsed_slots
    )

    try:
        async with HeliusRpcClient() as rpc:
            result = await rpc.call(
                "simulateTransaction",
                [
                    encoded_transaction,
                    {
                        "commitment": (
                            COMMITMENT
                        ),
                        "encoding": "base64",
                        "sigVerify": False,
                        "replaceRecentBlockhash": (
                            False
                        ),
                        "minContextSlot": (
                            min_context_slot
                        ),
                        "innerInstructions": True,
                    },
                ],
            )

    except Exception as error:
        return finish(
            UNKNOWN,
            (
                "EXECUTION_SIMULATION_RPC_FAILED:"
                f"{type(error).__name__}"
            ),
        )

    simulation_slot = _rpc_context_slot(
        result
    )

    if (
        simulation_slot is None
        or simulation_slot
        < min_context_slot
    ):
        return finish(
            UNKNOWN,
            "SIMULATION_RPC_CONTEXT_INVALID",
        )

    if not isinstance(
        result,
        dict,
    ):
        return finish(
            UNKNOWN,
            "SIMULATION_RPC_RESULT_INVALID",
        )

    value = result.get(
        "value"
    )

    if not isinstance(
        value,
        dict,
    ):
        return finish(
            UNKNOWN,
            "SIMULATION_RPC_VALUE_INVALID",
        )

    raw_logs = value.get(
        "logs"
    )

    if raw_logs is None:
        logs = ()

    elif (
        isinstance(
            raw_logs,
            list,
        )
        and all(
            isinstance(
                item,
                str,
            )
            for item in raw_logs
        )
    ):
        logs = tuple(
            raw_logs
        )

    else:
        return finish(
            UNKNOWN,
            "SIMULATION_LOGS_INVALID",
        )

    error_value = value.get(
        "err"
    )

    if error_value is not None:
        simulation_error = repr(
            error_value
        )

        return finish(
            DENY,
            "EXECUTION_SIMULATION_FAILED",
        )

    #
    # We explicitly prohibited blockhash replacement.
    #
    if (
        value.get(
            "replacementBlockhash"
        )
        is not None
    ):
        return finish(
            UNKNOWN,
            "UNEXPECTED_REPLACEMENT_BLOCKHASH",
        )

    units_consumed = _nonnegative_u64(
        value.get(
            "unitsConsumed"
        )
    )

    if units_consumed is None:
        return finish(
            UNKNOWN,
            "SIMULATION_COMPUTE_UNITS_INVALID",
        )

    if (
        units_consumed
        > compute_unit_limit
    ):
        return finish(
            DENY,
            "SIMULATION_COMPUTE_LIMIT_EXCEEDED",
        )

    simulated_fee_lamports = (
        _nonnegative_u64(
            value.get(
                "fee"
            )
        )
    )

    if simulated_fee_lamports is None:
        return finish(
            UNKNOWN,
            "SIMULATION_FEE_INVALID",
        )

    #
    # Same exact message + blockhash should produce
    # the same fee already quoted by getFeeForMessage.
    #
    if (
        simulated_fee_lamports
        != expected_rpc_fee
    ):
        return finish(
            UNKNOWN,
            "SIMULATED_FEE_MISMATCH",
        )

    expected_account_count = len(
        account_keys
    )

    pre_balances = _balance_vector(
        value.get(
            "preBalances"
        ),
        expected_length=(
            expected_account_count
        ),
    )

    post_balances = _balance_vector(
        value.get(
            "postBalances"
        ),
        expected_length=(
            expected_account_count
        ),
    )

    if (
        pre_balances is None
        or post_balances is None
    ):
        return finish(
            UNKNOWN,
            "SIMULATION_BALANCES_INVALID",
        )

    #
    # Payer is static account key zero.
    #
    payer_pre_balance_lamports = (
        pre_balances[
            0
        ]
    )

    payer_post_balance_lamports = (
        post_balances[
            0
        ]
    )

    if (
        payer_post_balance_lamports
        > payer_pre_balance_lamports
    ):
        return finish(
            UNKNOWN,
            "SIMULATION_PAYER_BALANCE_INCREASED",
        )

    payer_debit_lamports = (
        payer_pre_balance_lamports
        - payer_post_balance_lamports
    )

    if (
        payer_debit_lamports
        > wallet_liability
    ):
        return finish(
            DENY,
            "SIMULATED_WALLET_LIABILITY_EXCEEDED",
        )

    if (
        payer_debit_lamports
        < simulated_fee_lamports
    ):
        return finish(
            UNKNOWN,
            "SIMULATED_DEBIT_BELOW_TRANSACTION_FEE",
        )

    non_fee_payer_debit_lamports = (
        payer_debit_lamports
        - simulated_fee_lamports
    )

    #
    # The Pump instruction itself caps Pump-side
    # spend at max_sol_cost. This additional bound
    # reserves only max_sol_cost + authorized rent
    # for all non-network-fee payer outflow.
    #
    if (
        non_fee_payer_debit_lamports
        > authorized_non_fee_liability_lamports
    ):
        return finish(
            DENY,
            "SIMULATED_NON_FEE_LIABILITY_EXCEEDED",
        )

    return finish(
        APPROVE,
    )
