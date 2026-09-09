from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import (
    asdict,
    dataclass,
)
from typing import Any

from solders.pubkey import Pubkey

from src.execution.exit_execution import (
    EXIT_EXECUTION_CONTRACT_VERSION,
    PUMP_BONDING_CURVE_VENUE,
    ExitExecution,
    normalize_pump_sell_execution,
)
from src.execution.live_pump_fee_state import (
    LIVE_PUMP_FEE_STATE_VERSION,
    resolve_live_pump_fee_state,
)
from src.execution.pump_execution_simulator import (
    PumpCurveState,
)
from src.execution.pump_sell_simulator import (
    calculate_exact_input_sell,
)
from src.portfolio.live_positions import (
    OPEN_LIVE_POSITIONS_VERSION,
    PASS as POSITIONS_PASS,
    OpenLivePositionsResult,
    load_open_live_positions_read_only,
)
from src.portfolio.live_sell_allocation import (
    BLOCK as ALLOCATION_BLOCK,
    LIVE_SELL_ALLOCATION_VERSION,
    PLANNED,
    UNKNOWN as ALLOCATION_UNKNOWN,
    LiveSellAllocationPlan,
    plan_live_sell_allocation,
)
from src.safety.token_safety_gate import (
    SOL_QUOTE_MINT,
)


LIVE_PUMP_SELL_AUTHORIZATION_VERSION = (
    "live-pump-sell-authorization-v1"
)

AUTHORIZED = "AUTHORIZED"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"

BPS_DENOMINATOR = 10_000
U64_MAX = (1 << 64) - 1


@dataclass(frozen=True)
class LivePumpSellAuthorization:
    authorization_version: str
    authorization_sha256: str

    wallet_pubkey: str
    mint: str

    tokens_to_sell: int

    allocation: LiveSellAllocationPlan

    fee_state_version: str
    fee_rpc_slot: int
    fee_fetched_at: float

    quote_mint: str

    protocol_fee_bps: int
    creator_fee_bps: int

    slippage_bps: int
    base_network_fee_lamports: int
    priority_fee_lamports: int

    exit_execution: ExitExecution

    authorized_at: float


@dataclass(frozen=True)
class LivePumpSellAuthorizationResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    wallet_pubkey: str
    mint: str

    allocation: LiveSellAllocationPlan | None
    exit_execution: ExitExecution | None

    authorization: (
        LivePumpSellAuthorization | None
    )


def _strict_u64(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= U64_MAX
    )


def _strict_positive_u64(
    value: Any,
) -> bool:
    return (
        _strict_u64(value)
        and value > 0
    )


def _strict_bps(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= BPS_DENOMINATOR
    )


def _strict_timestamp(
    value: Any,
) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def _authorization_sha256(
    *,
    wallet_pubkey: str,
    mint: str,
    tokens_to_sell: int,
    allocation: LiveSellAllocationPlan,
    fee_rpc_slot: int,
    quote_mint: str,
    protocol_fee_bps: int,
    creator_fee_bps: int,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    exit_execution: ExitExecution,
) -> str:
    evidence = getattr(
        exit_execution,
        "venue_evidence",
        None,
    )

    payload = {
        "authorization_version": (
            LIVE_PUMP_SELL_AUTHORIZATION_VERSION
        ),
        "wallet_pubkey": wallet_pubkey,
        "mint": mint,
        "tokens_to_sell": tokens_to_sell,

        "allocation": {
            "allocation_version": (
                allocation.allocation_version
            ),
            "allocation_method": (
                allocation.allocation_method
            ),
            "requested_tokens": (
                allocation.requested_tokens
            ),
            "total_tokens_before": (
                allocation.total_tokens_before
            ),
            "total_tokens_after": (
                allocation.total_tokens_after
            ),
            "total_exposure_before_lamports": (
                allocation
                .total_exposure_before_lamports
            ),
            "total_exposure_reduction_lamports": (
                allocation
                .total_exposure_reduction_lamports
            ),
            "total_exposure_after_lamports": (
                allocation
                .total_exposure_after_lamports
            ),
            "total_cost_basis_before_lamports": (
                allocation
                .total_cost_basis_before_lamports
            ),
            "total_cost_basis_reduction_lamports": (
                allocation
                .total_cost_basis_reduction_lamports
            ),
            "total_cost_basis_after_lamports": (
                allocation
                .total_cost_basis_after_lamports
            ),
            "allocations": [
                asdict(item)
                for item
                in (
                    allocation.allocations
                    or ()
                )
            ],
        },

        "fee": {
            "fee_state_version": (
                LIVE_PUMP_FEE_STATE_VERSION
            ),
            "fee_rpc_slot": fee_rpc_slot,
            "quote_mint": quote_mint,
            "protocol_fee_bps": (
                protocol_fee_bps
            ),
            "creator_fee_bps": (
                creator_fee_bps
            ),
        },

        "execution": {
            "contract_version": (
                exit_execution
                .contract_version
            ),
            "venue": (
                exit_execution.venue
            ),
            "simulator_version": (
                exit_execution
                .simulator_version
            ),
            "tokens_in": (
                exit_execution.tokens_in
            ),
            "protocol_fee_bps": (
                exit_execution
                .protocol_fee_bps
            ),
            "creator_fee_bps": (
                exit_execution
                .creator_fee_bps
            ),
            "slippage_bps": (
                exit_execution.slippage_bps
            ),
            "gross_quote_out": (
                exit_execution.gross_quote_out
            ),
            "protocol_fee": (
                exit_execution.protocol_fee
            ),
            "creator_fee": (
                exit_execution.creator_fee
            ),
            "net_quote_out_after_venue_fees": (
                exit_execution
                .net_quote_out_after_venue_fees
            ),
            "min_quote_out": (
                exit_execution.min_quote_out
            ),
            "base_network_fee_lamports": (
                exit_execution
                .base_network_fee_lamports
            ),
            "priority_fee_lamports": (
                exit_execution
                .priority_fee_lamports
            ),
            "total_transaction_overhead_lamports": (
                exit_execution
                .total_transaction_overhead_lamports
            ),
            "net_wallet_proceeds_lamports": (
                exit_execution
                .net_wallet_proceeds_lamports
            ),
            "executable": (
                exit_execution.executable
            ),
            "ineligible_reason": (
                exit_execution.ineligible_reason
            ),
            "venue_evidence": (
                asdict(evidence)
                if evidence is not None
                else None
            ),
        },
    }

    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(
        raw
    ).hexdigest()


async def authorize_live_pump_sell(
    *,
    wallet_pubkey: str,
    mint: str,
    tokens_to_sell: int,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    min_context_slot: int | None = None,
) -> LivePumpSellAuthorizationResult:
    """
    Authorize one exact Pump bonding-curve SELL.

    Authority chain:

        authoritative OPEN inventory
        -> deterministic FIFO allocation
        -> authoritative live Pump curve/fees
        -> exact-input SELL simulation
        -> normalized ExitExecution
        -> immutable authorization

    Requested tokens are never silently resized.

    This resolver performs no:
    - DB mutation
    - position mutation
    - transaction construction
    - signing
    - submission
    - reservation
    - risk decision
    """

    normalized_wallet = ""
    normalized_mint = ""

    def finish(
        status: str,
        *reasons: str,
        allocation: (
            LiveSellAllocationPlan | None
        ) = None,
        exit_execution: (
            ExitExecution | None
        ) = None,
        authorization: (
            LivePumpSellAuthorization | None
        ) = None,
    ) -> LivePumpSellAuthorizationResult:
        return LivePumpSellAuthorizationResult(
            resolver_version=(
                LIVE_PUMP_SELL_AUTHORIZATION_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=normalized_wallet,
            mint=normalized_mint,
            allocation=allocation,
            exit_execution=exit_execution,
            authorization=authorization,
        )

    # --------------------------------------------------------
    # Request validation before any live read/RPC.
    # --------------------------------------------------------

    if not isinstance(
        wallet_pubkey,
        str,
    ):
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    wallet_pubkey = (
        wallet_pubkey.strip()
    )

    try:
        parsed_wallet = (
            Pubkey.from_string(
                wallet_pubkey
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    if parsed_wallet == Pubkey.default():
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    normalized_wallet = str(
        parsed_wallet
    )

    if not isinstance(
        mint,
        str,
    ):
        return finish(
            UNKNOWN,
            "INVALID_MINT",
        )

    mint = mint.strip()

    try:
        parsed_mint = Pubkey.from_string(
            mint
        )

    except Exception:
        return finish(
            UNKNOWN,
            "INVALID_MINT",
        )

    if parsed_mint == Pubkey.default():
        return finish(
            UNKNOWN,
            "INVALID_MINT",
        )

    normalized_mint = str(
        parsed_mint
    )

    if not _strict_positive_u64(
        tokens_to_sell
    ):
        return finish(
            UNKNOWN,
            "INVALID_TOKENS_TO_SELL",
        )

    if not _strict_bps(
        slippage_bps
    ):
        return finish(
            UNKNOWN,
            "INVALID_SLIPPAGE_BPS",
        )

    if not _strict_u64(
        base_network_fee_lamports
    ):
        return finish(
            UNKNOWN,
            "INVALID_BASE_NETWORK_FEE",
        )

    if not _strict_u64(
        priority_fee_lamports
    ):
        return finish(
            UNKNOWN,
            "INVALID_PRIORITY_FEE",
        )

    if (
        min_context_slot is not None
        and not _strict_u64(
            min_context_slot
        )
    ):
        return finish(
            UNKNOWN,
            "INVALID_MIN_CONTEXT_SLOT",
        )

    # --------------------------------------------------------
    # First authoritative OPEN-position read.
    # --------------------------------------------------------

    try:
        first_positions = (
            load_open_live_positions_read_only(
                wallet_pubkey=(
                    normalized_wallet
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "OPEN_POSITION_LOAD_FAILED",
        )

    if (
        getattr(
            first_positions,
            "loader_version",
            None,
        )
        != OPEN_LIVE_POSITIONS_VERSION
    ):
        return finish(
            UNKNOWN,
            "OPEN_POSITION_LOADER_VERSION_MISMATCH",
        )

    if (
        getattr(
            first_positions,
            "status",
            None,
        )
        != POSITIONS_PASS
    ):
        reasons = tuple(
            getattr(
                first_positions,
                "reasons",
                (),
            )
            or ()
        )

        return finish(
            UNKNOWN,
            "OPEN_POSITION_LOAD_NOT_PASS",
            *(
                "OPEN_POSITION:"
                + str(reason)
                for reason in reasons
            ),
        )

    positions = getattr(
        first_positions,
        "positions",
        None,
    )

    if not isinstance(
        positions,
        tuple,
    ):
        return finish(
            UNKNOWN,
            "OPEN_POSITION_RESULT_INVALID",
        )

    # --------------------------------------------------------
    # Deterministic allocation.
    # --------------------------------------------------------

    allocation = (
        plan_live_sell_allocation(
            wallet_pubkey=(
                normalized_wallet
            ),
            mint=normalized_mint,
            tokens_to_sell=(
                tokens_to_sell
            ),
            positions=positions,
        )
    )

    if (
        allocation.allocation_version
        != LIVE_SELL_ALLOCATION_VERSION
    ):
        return finish(
            UNKNOWN,
            "SELL_ALLOCATION_VERSION_MISMATCH",
            allocation=allocation,
        )

    if allocation.status == ALLOCATION_BLOCK:
        return finish(
            BLOCK,
            *allocation.reasons,
            allocation=allocation,
        )

    if (
        allocation.status
        == ALLOCATION_UNKNOWN
        or allocation.status
        != PLANNED
    ):
        return finish(
            UNKNOWN,
            "SELL_ALLOCATION_NOT_PLANNED",
            *allocation.reasons,
            allocation=allocation,
        )

    if (
        allocation.wallet_pubkey
        != normalized_wallet
        or allocation.mint
        != normalized_mint
        or allocation.requested_tokens
        != tokens_to_sell
    ):
        return finish(
            UNKNOWN,
            "SELL_ALLOCATION_BINDING_MISMATCH",
            allocation=allocation,
        )

    # --------------------------------------------------------
    # Live Pump curve + fee authority.
    # --------------------------------------------------------

    try:
        fee_state = await (
            resolve_live_pump_fee_state(
                mint=normalized_mint,
                min_context_slot=(
                    min_context_slot
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_PUMP_FEE_STATE_FAILED",
            allocation=allocation,
        )

    if (
        getattr(
            fee_state,
            "resolver_version",
            None,
        )
        != LIVE_PUMP_FEE_STATE_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIVE_PUMP_FEE_STATE_VERSION_MISMATCH",
            allocation=allocation,
        )

    if (
        getattr(
            fee_state,
            "mint",
            None,
        )
        != normalized_mint
    ):
        return finish(
            UNKNOWN,
            "LIVE_PUMP_FEE_STATE_MINT_MISMATCH",
            allocation=allocation,
        )

    curve = getattr(
        fee_state,
        "curve",
        None,
    )

    if curve is None:
        return finish(
            UNKNOWN,
            "LIVE_CURVE_STATE_MISSING",
            allocation=allocation,
        )

    complete = getattr(
        curve,
        "complete",
        None,
    )

    if not isinstance(
        complete,
        bool,
    ):
        return finish(
            UNKNOWN,
            "LIVE_CURVE_COMPLETE_INVALID",
            allocation=allocation,
        )

    if complete:
        return finish(
            UNKNOWN,
            "GRADUATED_CURVE_EXIT_ROUTE_UNSUPPORTED",
            allocation=allocation,
        )

    quote_mint = getattr(
        curve,
        "quote_mint",
        None,
    )

    if quote_mint not in (
        None,
        SOL_QUOTE_MINT,
    ):
        return finish(
            UNKNOWN,
            "NON_SOL_QUOTE_MINT_UNSUPPORTED",
            allocation=allocation,
        )

    normalized_quote_mint = (
        SOL_QUOTE_MINT
        if quote_mint is None
        else str(quote_mint)
    )

    protocol_fee_bps = getattr(
        fee_state,
        "protocol_fee_bps",
        None,
    )

    creator_fee_bps = getattr(
        fee_state,
        "creator_fee_bps",
        None,
    )

    fee_rpc_slot = getattr(
        fee_state,
        "rpc_slot",
        None,
    )

    fee_fetched_at = getattr(
        fee_state,
        "fetched_at",
        None,
    )

    if not _strict_bps(
        protocol_fee_bps
    ):
        return finish(
            UNKNOWN,
            "LIVE_PROTOCOL_FEE_BPS_INVALID",
            allocation=allocation,
        )

    if not _strict_bps(
        creator_fee_bps
    ):
        return finish(
            UNKNOWN,
            "LIVE_CREATOR_FEE_BPS_INVALID",
            allocation=allocation,
        )

    if not _strict_u64(
        fee_rpc_slot
    ):
        return finish(
            UNKNOWN,
            "LIVE_FEE_RPC_SLOT_INVALID",
            allocation=allocation,
        )

    if (
        min_context_slot is not None
        and fee_rpc_slot
        < min_context_slot
    ):
        return finish(
            UNKNOWN,
            "LIVE_FEE_RPC_SLOT_BELOW_MINIMUM",
            allocation=allocation,
        )

    if not _strict_timestamp(
        fee_fetched_at
    ):
        return finish(
            UNKNOWN,
            "LIVE_FEE_FETCHED_AT_INVALID",
            allocation=allocation,
        )

    reserve_values = (
        getattr(
            curve,
            "virtual_quote_reserves",
            None,
        ),
        getattr(
            curve,
            "virtual_token_reserves",
            None,
        ),
        getattr(
            curve,
            "real_quote_reserves",
            None,
        ),
        getattr(
            curve,
            "real_token_reserves",
            None,
        ),
    )

    if not all(
        _strict_u64(value)
        for value
        in reserve_values
    ):
        return finish(
            UNKNOWN,
            "LIVE_CURVE_RESERVES_INVALID",
            allocation=allocation,
        )

    (
        virtual_quote_reserves,
        virtual_token_reserves,
        real_quote_reserves,
        real_token_reserves,
    ) = reserve_values

    if (
        virtual_quote_reserves <= 0
        or virtual_token_reserves <= 0
    ):
        return finish(
            UNKNOWN,
            "LIVE_CURVE_VIRTUAL_RESERVES_INVALID",
            allocation=allocation,
        )

    curve_state = PumpCurveState(
        virtual_quote_reserves=int(
            virtual_quote_reserves
        ),
        virtual_token_reserves=int(
            virtual_token_reserves
        ),
        real_quote_reserves=int(
            real_quote_reserves
        ),
        real_token_reserves=int(
            real_token_reserves
        ),
    )

    # --------------------------------------------------------
    # Exact requested SELL economics.
    #
    # No liquidity-based downsizing is allowed here.
    # --------------------------------------------------------

    try:
        simulation = (
            calculate_exact_input_sell(
                state=curve_state,
                tokens_in=(
                    tokens_to_sell
                ),
                protocol_fee_bps=int(
                    protocol_fee_bps
                ),
                creator_fee_bps=int(
                    creator_fee_bps
                ),
                slippage_bps=(
                    slippage_bps
                ),
                base_network_fee_lamports=(
                    base_network_fee_lamports
                ),
                priority_fee_lamports=(
                    priority_fee_lamports
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "PUMP_SELL_SIMULATION_FAILED",
            allocation=allocation,
        )

    try:
        exit_execution = (
            normalize_pump_sell_execution(
                simulation
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "EXIT_EXECUTION_NORMALIZATION_FAILED",
            allocation=allocation,
        )

    if (
        exit_execution.contract_version
        != EXIT_EXECUTION_CONTRACT_VERSION
        or exit_execution.venue
        != PUMP_BONDING_CURVE_VENUE
        or exit_execution.tokens_in
        != tokens_to_sell
        or exit_execution.protocol_fee_bps
        != protocol_fee_bps
        or exit_execution.creator_fee_bps
        != creator_fee_bps
        or exit_execution.slippage_bps
        != slippage_bps
        or exit_execution.base_network_fee_lamports
        != base_network_fee_lamports
        or exit_execution.priority_fee_lamports
        != priority_fee_lamports
    ):
        return finish(
            UNKNOWN,
            "EXIT_EXECUTION_BINDING_MISMATCH",
            allocation=allocation,
            exit_execution=(
                exit_execution
            ),
        )

    if not exit_execution.executable:
        reason = (
            exit_execution.ineligible_reason
            or "UNSPECIFIED"
        )

        return finish(
            BLOCK,
            "SELL_NOT_EXECUTABLE:"
            + str(reason),
            allocation=allocation,
            exit_execution=(
                exit_execution
            ),
        )

    if not _strict_u64(
        exit_execution.min_quote_out
    ):
        return finish(
            UNKNOWN,
            "MIN_QUOTE_OUT_INVALID",
            allocation=allocation,
            exit_execution=(
                exit_execution
            ),
        )

    # --------------------------------------------------------
    # Re-read positions after chain-state/economic resolution.
    # The target allocation must still be identical.
    # --------------------------------------------------------

    try:
        final_positions = (
            load_open_live_positions_read_only(
                wallet_pubkey=(
                    normalized_wallet
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "FINAL_OPEN_POSITION_LOAD_FAILED",
            allocation=allocation,
            exit_execution=(
                exit_execution
            ),
        )

    if (
        getattr(
            final_positions,
            "loader_version",
            None,
        )
        != OPEN_LIVE_POSITIONS_VERSION
        or getattr(
            final_positions,
            "status",
            None,
        )
        != POSITIONS_PASS
        or not isinstance(
            getattr(
                final_positions,
                "positions",
                None,
            ),
            tuple,
        )
    ):
        return finish(
            UNKNOWN,
            "FINAL_OPEN_POSITION_RESULT_INVALID",
            allocation=allocation,
            exit_execution=(
                exit_execution
            ),
        )

    final_allocation = (
        plan_live_sell_allocation(
            wallet_pubkey=(
                normalized_wallet
            ),
            mint=normalized_mint,
            tokens_to_sell=(
                tokens_to_sell
            ),
            positions=(
                final_positions.positions
            ),
        )
    )

    if final_allocation != allocation:
        return finish(
            UNKNOWN,
            "OPEN_POSITION_ALLOCATION_CHANGED_DURING_AUTHORIZATION",
            allocation=allocation,
            exit_execution=(
                exit_execution
            ),
        )

    authorized_at = time.time()

    if not _strict_timestamp(
        authorized_at
    ):
        return finish(
            UNKNOWN,
            "SYSTEM_TIME_INVALID",
            allocation=allocation,
            exit_execution=(
                exit_execution
            ),
        )

    try:
        authorization_sha256 = (
            _authorization_sha256(
                wallet_pubkey=(
                    normalized_wallet
                ),
                mint=normalized_mint,
                tokens_to_sell=(
                    tokens_to_sell
                ),
                allocation=allocation,
                fee_rpc_slot=int(
                    fee_rpc_slot
                ),
                quote_mint=(
                    normalized_quote_mint
                ),
                protocol_fee_bps=int(
                    protocol_fee_bps
                ),
                creator_fee_bps=int(
                    creator_fee_bps
                ),
                slippage_bps=(
                    slippage_bps
                ),
                base_network_fee_lamports=(
                    base_network_fee_lamports
                ),
                priority_fee_lamports=(
                    priority_fee_lamports
                ),
                exit_execution=(
                    exit_execution
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_AUTHORIZATION_FINGERPRINT_FAILED",
            allocation=allocation,
            exit_execution=(
                exit_execution
            ),
        )

    authorization = (
        LivePumpSellAuthorization(
            authorization_version=(
                LIVE_PUMP_SELL_AUTHORIZATION_VERSION
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            wallet_pubkey=(
                normalized_wallet
            ),
            mint=normalized_mint,
            tokens_to_sell=(
                tokens_to_sell
            ),
            allocation=allocation,
            fee_state_version=(
                LIVE_PUMP_FEE_STATE_VERSION
            ),
            fee_rpc_slot=int(
                fee_rpc_slot
            ),
            fee_fetched_at=float(
                fee_fetched_at
            ),
            quote_mint=(
                normalized_quote_mint
            ),
            protocol_fee_bps=int(
                protocol_fee_bps
            ),
            creator_fee_bps=int(
                creator_fee_bps
            ),
            slippage_bps=(
                slippage_bps
            ),
            base_network_fee_lamports=(
                base_network_fee_lamports
            ),
            priority_fee_lamports=(
                priority_fee_lamports
            ),
            exit_execution=(
                exit_execution
            ),
            authorized_at=float(
                authorized_at
            ),
        )
    )

    return finish(
        AUTHORIZED,
        allocation=allocation,
        exit_execution=exit_execution,
        authorization=authorization,
    )
