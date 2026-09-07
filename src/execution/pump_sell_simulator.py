from __future__ import annotations

from dataclasses import dataclass

from src.execution.pump_execution_simulator import (
    BPS_DENOMINATOR,
    PumpCurveState,
    ceil_div,
    ceil_fee,
    validate_bps,
)


SELL_SIMULATOR_VERSION = (
    "pump-sell-simulator-v1"
)


@dataclass(frozen=True)
class PumpSellSimulation:
    simulator_version: str

    tokens_in: int

    protocol_fee_bps: int
    creator_fee_bps: int
    total_fee_bps: int

    gross_quote_out: int

    protocol_fee: int
    creator_fee: int
    pump_fees_total: int

    net_quote_out: int
    min_quote_out: int

    slippage_bps: int

    pre_virtual_quote_reserves: int
    pre_virtual_token_reserves: int
    pre_real_quote_reserves: int
    pre_real_token_reserves: int

    post_virtual_quote_reserves: int
    post_virtual_token_reserves: int
    post_real_quote_reserves: int
    post_real_token_reserves: int

    real_quote_reserve_sufficient: bool
    would_exhaust_real_quote_reserves: bool

    pre_spot_price_raw: float
    average_curve_price_raw: float

    #
    # Positive means adverse sell impact.
    #
    price_impact_bps: float

    base_network_fee_lamports: int
    priority_fee_lamports: int

    total_transaction_overhead_lamports: int

    #
    # Pump proceeds after Pump fees but before
    # transaction overhead.
    #
    net_quote_out_after_pump_fees: int

    #
    # Economic wallet proceeds after also
    # accounting for transaction overhead.
    #
    net_wallet_proceeds_lamports: int

    all_in_exit_price_raw: float

    executable: bool
    ineligible_reason: str | None


def calculate_exact_input_sell(
    *,
    state: PumpCurveState,
    tokens_in: int,
    protocol_fee_bps: int,
    creator_fee_bps: int,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
) -> PumpSellSimulation:

    validate_bps(
        "protocol_fee_bps",
        protocol_fee_bps,
    )

    validate_bps(
        "creator_fee_bps",
        creator_fee_bps,
    )

    validate_bps(
        "slippage_bps",
        slippage_bps,
    )

    if tokens_in <= 0:
        raise ValueError(
            "tokens_in must be positive."
        )

    for name, value in (
        (
            "base_network_fee_lamports",
            base_network_fee_lamports,
        ),
        (
            "priority_fee_lamports",
            priority_fee_lamports,
        ),
    ):
        if value < 0:
            raise ValueError(
                f"{name} cannot be negative."
            )

    reserve_values = (
        state.virtual_quote_reserves,
        state.virtual_token_reserves,
        state.real_quote_reserves,
        state.real_token_reserves,
    )

    if any(
        value < 0
        for value in reserve_values
    ):
        raise ValueError(
            "Curve reserves cannot be negative."
        )

    if (
        state.virtual_quote_reserves <= 0
        or state.virtual_token_reserves <= 0
    ):
        raise ValueError(
            "Virtual reserves must be positive."
        )

    total_fee_bps = (
        protocol_fee_bps
        + creator_fee_bps
    )

    #
    # Pump constant-product SELL:
    #
    # k = vQuote * vToken
    #
    # post_vToken =
    #     pre_vToken + tokens_in
    #
    # post_vQuote =
    #     ceil(k / post_vToken)
    #
    # gross_quote_out =
    #     pre_vQuote - post_vQuote
    #
    invariant = (
        state.virtual_quote_reserves
        * state.virtual_token_reserves
    )

    post_virtual_token_candidate = (
        state.virtual_token_reserves
        + tokens_in
    )

    post_virtual_quote_candidate = (
        ceil_div(
            invariant,
            post_virtual_token_candidate,
        )
    )

    gross_quote_out = (
        state.virtual_quote_reserves
        - post_virtual_quote_candidate
    )

    if gross_quote_out < 0:
        gross_quote_out = 0

    protocol_fee = ceil_fee(
        gross_quote_out,
        protocol_fee_bps,
    )

    creator_fee = ceil_fee(
        gross_quote_out,
        creator_fee_bps,
    )

    pump_fees_total = (
        protocol_fee
        + creator_fee
    )

    net_quote_out = (
        gross_quote_out
        - pump_fees_total
    )

    if net_quote_out < 0:
        net_quote_out = 0

    #
    # Pump sell_v2 min_sol_output is the
    # minimum quote received AFTER protocol
    # and creator fees.
    #
    min_quote_out = (
        net_quote_out
        * (
            BPS_DENOMINATOR
            - slippage_bps
        )
        // BPS_DENOMINATOR
    )

    real_quote_reserve_sufficient = (
        gross_quote_out
        <= state.real_quote_reserves
    )

    would_exhaust_real_quote_reserves = (
        gross_quote_out
        == state.real_quote_reserves
        and gross_quote_out > 0
    )

    if gross_quote_out <= 0:
        executable = False
        reason = "ZERO_QUOTE_OUTPUT"

    elif pump_fees_total >= gross_quote_out:
        executable = False
        reason = (
            "FEES_EXHAUST_QUOTE_OUTPUT"
        )

    elif not real_quote_reserve_sufficient:
        executable = False
        reason = (
            "INSUFFICIENT_REAL_QUOTE_RESERVES"
        )

    else:
        executable = True
        reason = None

    if executable:
        post_virtual_quote = (
            state.virtual_quote_reserves
            - gross_quote_out
        )

        post_virtual_token = (
            state.virtual_token_reserves
            + tokens_in
        )

        post_real_quote = (
            state.real_quote_reserves
            - gross_quote_out
        )

        post_real_token = (
            state.real_token_reserves
            + tokens_in
        )

    else:
        post_virtual_quote = (
            state.virtual_quote_reserves
        )

        post_virtual_token = (
            state.virtual_token_reserves
        )

        post_real_quote = (
            state.real_quote_reserves
        )

        post_real_token = (
            state.real_token_reserves
        )

    pre_spot_price = (
        state.virtual_quote_reserves
        / state.virtual_token_reserves
    )

    if gross_quote_out > 0:
        average_curve_price = (
            gross_quote_out
            / tokens_in
        )

        #
        # Sell impact is reported as a positive
        # adverse cost:
        #
        # 100 bps means average gross sell price
        # is 1% below pre-trade spot.
        #
        price_impact_bps = (
            1.0
            - (
                average_curve_price
                / pre_spot_price
            )
        ) * BPS_DENOMINATOR

    else:
        average_curve_price = 0.0
        price_impact_bps = 0.0

    transaction_overhead = (
        base_network_fee_lamports
        + priority_fee_lamports
    )

    if executable:
        net_wallet_proceeds = max(
            0,
            net_quote_out
            - transaction_overhead,
        )

        all_in_exit_price = (
            net_wallet_proceeds
            / tokens_in
        )

    else:
        net_wallet_proceeds = 0
        all_in_exit_price = 0.0

    return PumpSellSimulation(
        simulator_version=(
            SELL_SIMULATOR_VERSION
        ),

        tokens_in=tokens_in,

        protocol_fee_bps=(
            protocol_fee_bps
        ),

        creator_fee_bps=(
            creator_fee_bps
        ),

        total_fee_bps=(
            total_fee_bps
        ),

        gross_quote_out=(
            gross_quote_out
        ),

        protocol_fee=(
            protocol_fee
        ),

        creator_fee=(
            creator_fee
        ),

        pump_fees_total=(
            pump_fees_total
        ),

        net_quote_out=(
            net_quote_out
        ),

        min_quote_out=(
            min_quote_out
        ),

        slippage_bps=(
            slippage_bps
        ),

        pre_virtual_quote_reserves=(
            state.virtual_quote_reserves
        ),

        pre_virtual_token_reserves=(
            state.virtual_token_reserves
        ),

        pre_real_quote_reserves=(
            state.real_quote_reserves
        ),

        pre_real_token_reserves=(
            state.real_token_reserves
        ),

        post_virtual_quote_reserves=(
            post_virtual_quote
        ),

        post_virtual_token_reserves=(
            post_virtual_token
        ),

        post_real_quote_reserves=(
            post_real_quote
        ),

        post_real_token_reserves=(
            post_real_token
        ),

        real_quote_reserve_sufficient=(
            real_quote_reserve_sufficient
        ),

        would_exhaust_real_quote_reserves=(
            would_exhaust_real_quote_reserves
        ),

        pre_spot_price_raw=(
            pre_spot_price
        ),

        average_curve_price_raw=(
            average_curve_price
        ),

        price_impact_bps=(
            price_impact_bps
        ),

        base_network_fee_lamports=(
            base_network_fee_lamports
        ),

        priority_fee_lamports=(
            priority_fee_lamports
        ),

        total_transaction_overhead_lamports=(
            transaction_overhead
        ),

        net_quote_out_after_pump_fees=(
            net_quote_out
        ),

        net_wallet_proceeds_lamports=(
            net_wallet_proceeds
        ),

        all_in_exit_price_raw=(
            all_in_exit_price
        ),

        executable=(
            executable
        ),

        ineligible_reason=(
            reason
        ),
    )

def find_max_liquidity_feasible_sell(
    *,
    state: PumpCurveState,
    maximum_tokens_to_sell: int,

    protocol_fee_bps: int,
    creator_fee_bps: int,

    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
) -> tuple[
    int,
    PumpSellSimulation | None,
]:
    """
    Find the largest integer token amount whose
    modeled gross quote output can be paid by the
    current real quote reserves.

    This function solves only the Pump liquidity
    boundary. It deliberately does not use
    simulation.executable as the binary-search
    predicate because executability also includes
    zero-output and fee-economics constraints.

    The caller must separately evaluate the
    returned simulation for economic recovery.
    """

    maximum_tokens_to_sell = int(
        maximum_tokens_to_sell
    )

    if maximum_tokens_to_sell <= 0:
        return (
            0,
            None,
        )

    low = 1
    high = maximum_tokens_to_sell

    best_tokens = 0
    best_simulation = None

    while low <= high:
        midpoint = (
            low
            + high
        ) // 2

        simulation = (
            calculate_exact_input_sell(
                state=state,

                tokens_in=midpoint,

                protocol_fee_bps=int(
                    protocol_fee_bps
                ),

                creator_fee_bps=int(
                    creator_fee_bps
                ),

                slippage_bps=int(
                    slippage_bps
                ),

                base_network_fee_lamports=int(
                    base_network_fee_lamports
                ),

                priority_fee_lamports=int(
                    priority_fee_lamports
                ),
            )
        )

        liquidity_feasible = (
            int(
                simulation.gross_quote_out
            )
            <= int(
                state.real_quote_reserves
            )
        )

        if liquidity_feasible:
            best_tokens = midpoint
            best_simulation = simulation

            low = midpoint + 1

        else:
            high = midpoint - 1

    return (
        best_tokens,
        best_simulation,
    )
