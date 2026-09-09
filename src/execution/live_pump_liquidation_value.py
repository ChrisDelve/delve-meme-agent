from __future__ import annotations

import math
from dataclasses import dataclass
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
    find_max_liquidity_feasible_sell,
)
from src.safety.token_safety_gate import (
    SOL_QUOTE_MINT,
)


LIVE_PUMP_LIQUIDATION_VALUE_VERSION = (
    "live-pump-liquidation-value-v1"
)

RESOLVED = "RESOLVED"
UNKNOWN = "UNKNOWN"

BPS_DENOMINATOR = 10_000
U64_MAX = (1 << 64) - 1


@dataclass(frozen=True)
class LivePumpLiquidationValueResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    mint: str

    tokens_held: int | None
    liquidatable_tokens: int | None
    unliquidatable_tokens: int | None

    liquidation_value_lamports: int | None

    fee_state_version: str | None
    fee_rpc_slot: int | None
    fee_fetched_at: float | None

    protocol_fee_bps: int | None
    creator_fee_bps: int | None

    quote_mint: str | None

    slippage_bps: int | None

    base_network_fee_lamports: int | None
    priority_fee_lamports: int | None

    exit_execution: ExitExecution | None


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


async def resolve_live_pump_inventory_liquidation_value(
    *,
    mint: str,
    tokens_held: int,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    min_context_slot: int | None = None,
) -> LivePumpLiquidationValueResult:
    """
    Resolve conservative immediate liquidation value
    for aggregate OPEN inventory of one Pump mint.

    IMPORTANT:
    tokens_held must represent the aggregate inventory
    for this mint across all OPEN position lots.

    This avoids valuing multiple lots independently
    against the same bonding-curve liquidity.

    Known current economic unexitability is valued at
    zero. Uncertain chain state or an unsupported
    post-graduation route is UNKNOWN.

    This resolver has no:
    - database mutation authority
    - portfolio accounting authority
    - risk/sizing authority
    - transaction build authority
    - signing authority
    - send authority
    """

    normalized_mint = ""

    def finish(
        status: str,
        *reasons: str,
        liquidatable_tokens: int | None = None,
        unliquidatable_tokens: int | None = None,
        liquidation_value_lamports: int | None = None,
        fee_state_version: str | None = None,
        fee_rpc_slot: int | None = None,
        fee_fetched_at: float | None = None,
        protocol_fee_bps: int | None = None,
        creator_fee_bps: int | None = None,
        quote_mint: str | None = None,
        exit_execution: ExitExecution | None = None,
    ) -> LivePumpLiquidationValueResult:
        return LivePumpLiquidationValueResult(
            resolver_version=(
                LIVE_PUMP_LIQUIDATION_VALUE_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            mint=normalized_mint,
            tokens_held=(
                tokens_held
                if _strict_positive_u64(
                    tokens_held
                )
                else None
            ),
            liquidatable_tokens=(
                liquidatable_tokens
            ),
            unliquidatable_tokens=(
                unliquidatable_tokens
            ),
            liquidation_value_lamports=(
                liquidation_value_lamports
            ),
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=fee_fetched_at,
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=quote_mint,
            slippage_bps=(
                slippage_bps
                if _strict_bps(
                    slippage_bps
                )
                else None
            ),
            base_network_fee_lamports=(
                base_network_fee_lamports
                if _strict_u64(
                    base_network_fee_lamports
                )
                else None
            ),
            priority_fee_lamports=(
                priority_fee_lamports
                if _strict_u64(
                    priority_fee_lamports
                )
                else None
            ),
            exit_execution=(
                exit_execution
            ),
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

    if not mint:
        return finish(
            UNKNOWN,
            "INVALID_MINT",
        )

    try:
        mint_pubkey = (
            Pubkey.from_string(
                mint
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "INVALID_MINT",
        )

    if mint_pubkey == Pubkey.default():
        return finish(
            UNKNOWN,
            "INVALID_MINT",
        )

    normalized_mint = str(
        mint_pubkey
    )

    if not _strict_positive_u64(
        tokens_held
    ):
        return finish(
            UNKNOWN,
            "INVALID_TOKEN_INVENTORY",
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

    try:
        fee_state = (
            await resolve_live_pump_fee_state(
                mint=normalized_mint,
                min_context_slot=(
                    min_context_slot
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_FEE_STATE_FAILED",
        )

    fee_state_version = getattr(
        fee_state,
        "resolver_version",
        None,
    )

    fee_state_mint = getattr(
        fee_state,
        "mint",
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

    curve = getattr(
        fee_state,
        "curve",
        None,
    )

    quote_mint = None

    if (
        fee_state_version
        != LIVE_PUMP_FEE_STATE_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIVE_FEE_STATE_VERSION_MISMATCH",
            fee_state_version=(
                str(fee_state_version)
                if fee_state_version
                is not None
                else None
            ),
        )

    if fee_state_mint != normalized_mint:
        return finish(
            UNKNOWN,
            "LIVE_FEE_STATE_MINT_MISMATCH",
            fee_state_version=(
                fee_state_version
            ),
        )

    if not _strict_u64(
        fee_rpc_slot
    ):
        return finish(
            UNKNOWN,
            "LIVE_FEE_STATE_SLOT_INVALID",
            fee_state_version=(
                fee_state_version
            ),
        )

    if (
        min_context_slot is not None
        and fee_rpc_slot
        < min_context_slot
    ):
        return finish(
            UNKNOWN,
            "LIVE_FEE_STATE_SLOT_BELOW_MINIMUM",
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
        )

    if not _strict_timestamp(
        fee_fetched_at
    ):
        return finish(
            UNKNOWN,
            "LIVE_FEE_STATE_TIMESTAMP_INVALID",
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
        )

    if (
        not _strict_bps(
            protocol_fee_bps
        )
        or not _strict_bps(
            creator_fee_bps
        )
    ):
        return finish(
            UNKNOWN,
            "LIVE_FEE_BPS_INVALID",
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
        )

    if curve is None:
        return finish(
            UNKNOWN,
            "LIVE_CURVE_STATE_MISSING",
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=quote_mint,
        )

    quote_mint = getattr(
        curve,
        "quote_mint",
        None,
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
            "LIVE_CURVE_COMPLETE_FLAG_INVALID",
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=quote_mint,
        )

    if complete:
        return finish(
            UNKNOWN,
            "GRADUATED_CURVE_EXIT_ROUTE_UNSUPPORTED",
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=quote_mint,
        )

    if quote_mint not in (
        None,
        SOL_QUOTE_MINT,
    ):
        return finish(
            UNKNOWN,
            "NON_SOL_QUOTE_MINT_UNSUPPORTED",
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=str(
                quote_mint
            ),
        )

    reserve_values = {
        "virtual_quote_reserves":
            getattr(
                curve,
                "virtual_quote_reserves",
                None,
            ),
        "virtual_token_reserves":
            getattr(
                curve,
                "virtual_token_reserves",
                None,
            ),
        "real_quote_reserves":
            getattr(
                curve,
                "real_quote_reserves",
                None,
            ),
        "real_token_reserves":
            getattr(
                curve,
                "real_token_reserves",
                None,
            ),
    }

    if any(
        not _strict_u64(value)
        for value
        in reserve_values.values()
    ):
        return finish(
            UNKNOWN,
            "LIVE_CURVE_RESERVES_INVALID",
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=quote_mint,
        )

    if (
        reserve_values[
            "virtual_quote_reserves"
        ]
        <= 0
        or reserve_values[
            "virtual_token_reserves"
        ]
        <= 0
    ):
        return finish(
            UNKNOWN,
            "LIVE_CURVE_VIRTUAL_RESERVES_INVALID",
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=quote_mint,
        )

    curve_state = PumpCurveState(
        virtual_quote_reserves=int(
            reserve_values[
                "virtual_quote_reserves"
            ]
        ),
        virtual_token_reserves=int(
            reserve_values[
                "virtual_token_reserves"
            ]
        ),
        real_quote_reserves=int(
            reserve_values[
                "real_quote_reserves"
            ]
        ),
        real_token_reserves=int(
            reserve_values[
                "real_token_reserves"
            ]
        ),
    )

    try:
        (
            liquidatable_tokens,
            simulation,
        ) = (
            find_max_liquidity_feasible_sell(
                state=curve_state,
                maximum_tokens_to_sell=(
                    tokens_held
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
            "LIQUIDATION_SIMULATION_FAILED",
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=quote_mint,
        )

    if (
        not _strict_u64(
            liquidatable_tokens
        )
        or liquidatable_tokens
        > tokens_held
    ):
        return finish(
            UNKNOWN,
            "LIQUIDATABLE_TOKEN_RESULT_INVALID",
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=quote_mint,
        )

    unliquidatable_tokens = (
        tokens_held
        - liquidatable_tokens
    )

    if liquidatable_tokens == 0:
        if simulation is not None:
            return finish(
                UNKNOWN,
                "LIQUIDATION_SIMULATION_INCOHERENT",
                fee_state_version=(
                    fee_state_version
                ),
                fee_rpc_slot=fee_rpc_slot,
                fee_fetched_at=float(
                    fee_fetched_at
                ),
                protocol_fee_bps=(
                    protocol_fee_bps
                ),
                creator_fee_bps=(
                    creator_fee_bps
                ),
            )

        return finish(
            RESOLVED,
            "NO_LIQUIDITY_FEASIBLE_TOKENS",
            liquidatable_tokens=0,
            unliquidatable_tokens=(
                tokens_held
            ),
            liquidation_value_lamports=0,
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=quote_mint,
        )

    if simulation is None:
        return finish(
            UNKNOWN,
            "LIQUIDATION_SIMULATION_MISSING",
            liquidatable_tokens=(
                liquidatable_tokens
            ),
            unliquidatable_tokens=(
                unliquidatable_tokens
            ),
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=quote_mint,
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
            liquidatable_tokens=(
                liquidatable_tokens
            ),
            unliquidatable_tokens=(
                unliquidatable_tokens
            ),
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=quote_mint,
        )

    if (
        getattr(
            exit_execution,
            "contract_version",
            None,
        )
        != EXIT_EXECUTION_CONTRACT_VERSION

        or getattr(
            exit_execution,
            "venue",
            None,
        )
        != PUMP_BONDING_CURVE_VENUE

        or getattr(
            exit_execution,
            "tokens_in",
            None,
        )
        != liquidatable_tokens

        or getattr(
            exit_execution,
            "protocol_fee_bps",
            None,
        )
        != protocol_fee_bps

        or getattr(
            exit_execution,
            "creator_fee_bps",
            None,
        )
        != creator_fee_bps

        or getattr(
            exit_execution,
            "slippage_bps",
            None,
        )
        != slippage_bps

        or getattr(
            exit_execution,
            "base_network_fee_lamports",
            None,
        )
        != base_network_fee_lamports

        or getattr(
            exit_execution,
            "priority_fee_lamports",
            None,
        )
        != priority_fee_lamports
    ):
        return finish(
            UNKNOWN,
            "EXIT_EXECUTION_BINDING_MISMATCH",
            liquidatable_tokens=(
                liquidatable_tokens
            ),
            unliquidatable_tokens=(
                unliquidatable_tokens
            ),
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=quote_mint,
        )

    executable = getattr(
        exit_execution,
        "executable",
        None,
    )

    if not isinstance(
        executable,
        bool,
    ):
        return finish(
            UNKNOWN,
            "EXIT_EXECUTION_STATUS_INVALID",
            liquidatable_tokens=(
                liquidatable_tokens
            ),
            unliquidatable_tokens=(
                unliquidatable_tokens
            ),
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=quote_mint,
        )

    net_wallet_proceeds = getattr(
        exit_execution,
        "net_wallet_proceeds_lamports",
        None,
    )

    if not _strict_u64(
        net_wallet_proceeds
    ):
        return finish(
            UNKNOWN,
            "EXIT_PROCEEDS_INVALID",
            liquidatable_tokens=(
                liquidatable_tokens
            ),
            unliquidatable_tokens=(
                unliquidatable_tokens
            ),
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=quote_mint,
        )

    if not executable:
        reason = getattr(
            exit_execution,
            "ineligible_reason",
            None,
        )

        if (
            reason is not None
            and not isinstance(
                reason,
                str,
            )
        ):
            return finish(
                UNKNOWN,
                "EXIT_INELIGIBILITY_REASON_INVALID",
                liquidatable_tokens=(
                    liquidatable_tokens
                ),
                unliquidatable_tokens=(
                    unliquidatable_tokens
                ),
                fee_state_version=(
                    fee_state_version
                ),
                fee_rpc_slot=fee_rpc_slot,
                fee_fetched_at=float(
                    fee_fetched_at
                ),
                protocol_fee_bps=(
                    protocol_fee_bps
                ),
                creator_fee_bps=(
                    creator_fee_bps
                ),
                exit_execution=(
                    exit_execution
                ),
            )

        return finish(
            RESOLVED,
            (
                "ECONOMICALLY_UNEXITABLE"
                if not reason
                else
                "ECONOMICALLY_UNEXITABLE:"
                + reason
            ),
            liquidatable_tokens=(
                liquidatable_tokens
            ),
            unliquidatable_tokens=(
                unliquidatable_tokens
            ),
            liquidation_value_lamports=0,
            fee_state_version=(
                fee_state_version
            ),
            fee_rpc_slot=fee_rpc_slot,
            fee_fetched_at=float(
                fee_fetched_at
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            quote_mint=quote_mint,
            exit_execution=(
                exit_execution
            ),
        )

    return finish(
        RESOLVED,
        liquidatable_tokens=(
            liquidatable_tokens
        ),
        unliquidatable_tokens=(
            unliquidatable_tokens
        ),
        liquidation_value_lamports=int(
            net_wallet_proceeds
        ),
        fee_state_version=(
            fee_state_version
        ),
        fee_rpc_slot=fee_rpc_slot,
        fee_fetched_at=float(
            fee_fetched_at
        ),
        protocol_fee_bps=(
            protocol_fee_bps
        ),
        creator_fee_bps=(
            creator_fee_bps
        ),
        quote_mint=quote_mint,
        exit_execution=(
            exit_execution
        ),
    )
