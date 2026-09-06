from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_DOWN
from pathlib import Path


DB_PATH = Path("logs/delve_meme.db")

SOL_QUOTE_MINT = "11111111111111111111111111111111"

SIMULATOR_VERSION = "pump-execution-simulator-v1"

LAMPORTS_PER_SOL = 1_000_000_000
BPS_DENOMINATOR = 10_000


@dataclass(frozen=True)
class PumpCurveState:
    virtual_quote_reserves: int
    virtual_token_reserves: int

    real_quote_reserves: int
    real_token_reserves: int


@dataclass(frozen=True)
class PumpBuySimulation:
    simulator_version: str

    spendable_quote_in: int

    protocol_fee_bps: int
    creator_fee_bps: int
    total_fee_bps: int

    initial_net_quote: int
    curve_quote_in: int

    protocol_fee: int
    creator_fee: int
    pump_fees_total: int

    pump_spend_used: int
    unused_pump_budget: int

    tokens_out: int
    min_tokens_out: int

    slippage_bps: int

    pre_virtual_quote_reserves: int
    pre_virtual_token_reserves: int
    pre_real_quote_reserves: int
    pre_real_token_reserves: int

    post_virtual_quote_reserves: int
    post_virtual_token_reserves: int
    post_real_quote_reserves: int
    post_real_token_reserves: int

    real_token_reserve_sufficient: bool
    would_exhaust_real_token_reserves: bool

    pre_spot_price_raw: float
    average_curve_price_raw: float
    price_impact_bps: float

    base_network_fee_lamports: int
    priority_fee_lamports: int
    rent_lamports: int

    total_transaction_overhead_lamports: int
    total_wallet_cost_lamports: int

    all_in_entry_price_raw: float

    executable: bool
    ineligible_reason: str | None


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(
        DB_PATH,
        timeout=30.0,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA busy_timeout = 30000"
    )

    return connection


def ceil_div(
    numerator: int,
    denominator: int,
) -> int:
    if denominator <= 0:
        raise ValueError(
            "ceil_div denominator must be positive."
        )

    return (
        numerator
        + denominator
        - 1
    ) // denominator


def ceil_fee(
    quote_amount: int,
    fee_bps: int,
) -> int:
    if fee_bps == 0:
        return 0

    return ceil_div(
        quote_amount * fee_bps,
        BPS_DENOMINATOR,
    )


def sol_to_lamports(
    sol: str,
) -> int:
    value = Decimal(sol)

    if value <= 0:
        raise ValueError(
            "SOL amount must be positive."
        )

    lamports = (
        value
        * Decimal(LAMPORTS_PER_SOL)
    ).to_integral_value(
        rounding=ROUND_DOWN
    )

    result = int(lamports)

    if result <= 0:
        raise ValueError(
            "SOL amount is below one lamport."
        )

    return result


def lamports_to_sol(
    lamports: int,
) -> Decimal:
    return (
        Decimal(lamports)
        / Decimal(LAMPORTS_PER_SOL)
    )


def validate_bps(
    name: str,
    value: int,
) -> None:
    if value < 0:
        raise ValueError(
            f"{name} cannot be negative."
        )

    if value > BPS_DENOMINATOR:
        raise ValueError(
            f"{name} cannot exceed 10,000 bps."
        )


def reconstruct_pre_state_from_trade(
    row: sqlite3.Row,
) -> PumpCurveState:
    quote_amount = int(
        row["quote_amount"]
    )

    token_amount = int(
        row["token_amount"]
    )

    post_virtual_quote = int(
        row["virtual_sol_reserves"]
    )

    post_virtual_token = int(
        row["virtual_token_reserves"]
    )

    post_real_quote = int(
        row["real_sol_reserves"]
    )

    post_real_token = int(
        row["real_token_reserves"]
    )

    side = row["side"]

    if side == "BUY":
        pre_virtual_quote = (
            post_virtual_quote
            - quote_amount
        )

        pre_virtual_token = (
            post_virtual_token
            + token_amount
        )

        pre_real_quote = (
            post_real_quote
            - quote_amount
        )

        pre_real_token = (
            post_real_token
            + token_amount
        )

    elif side == "SELL":
        pre_virtual_quote = (
            post_virtual_quote
            + quote_amount
        )

        pre_virtual_token = (
            post_virtual_token
            - token_amount
        )

        pre_real_quote = (
            post_real_quote
            + quote_amount
        )

        pre_real_token = (
            post_real_token
            - token_amount
        )

    else:
        raise ValueError(
            f"Unsupported trade side: {side}"
        )

    values = (
        pre_virtual_quote,
        pre_virtual_token,
        pre_real_quote,
        pre_real_token,
    )

    if any(
        value < 0
        for value in values
    ):
        raise RuntimeError(
            "Reconstructed pre-state contains "
            "negative reserves."
        )

    if (
        pre_virtual_quote <= 0
        or pre_virtual_token <= 0
    ):
        raise RuntimeError(
            "Reconstructed virtual reserves "
            "are not positive."
        )

    return PumpCurveState(
        virtual_quote_reserves=(
            pre_virtual_quote
        ),
        virtual_token_reserves=(
            pre_virtual_token
        ),
        real_quote_reserves=(
            pre_real_quote
        ),
        real_token_reserves=(
            pre_real_token
        ),
    )


def load_trade(
    connection: sqlite3.Connection,
    signature: str,
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT
            signature,
            mint,
            wallet,
            side,
            quote_mint,

            slot,
            trade_timestamp,
            observed_at,

            quote_amount,
            token_amount,

            protocol_fee_lamports,
            creator_fee_lamports,

            ix_name,
            mayhem_mode,

            virtual_sol_reserves,
            virtual_token_reserves,

            real_sol_reserves,
            real_token_reserves

        FROM trades

        WHERE signature = ?

        LIMIT 1
        """,
        (signature,),
    ).fetchone()

    if row is None:
        raise RuntimeError(
            f"Trade not found: {signature}"
        )

    if (
        row["quote_mint"]
        != SOL_QUOTE_MINT
    ):
        raise RuntimeError(
            "Simulator v1 supports only "
            "native-SOL Pump trades."
        )

    required = (
        "quote_amount",
        "token_amount",
        "virtual_sol_reserves",
        "virtual_token_reserves",
        "real_sol_reserves",
        "real_token_reserves",
    )

    for field in required:
        if row[field] is None:
            raise RuntimeError(
                f"Trade is missing required "
                f"field: {field}"
            )

    return row


def calculate_exact_input_buy(
    *,
    state: PumpCurveState,
    spendable_quote_in: int,
    protocol_fee_bps: int,
    creator_fee_bps: int,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    rent_lamports: int,
) -> PumpBuySimulation:
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

    if spendable_quote_in <= 0:
        raise ValueError(
            "spendable_quote_in must be positive."
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
        (
            "rent_lamports",
            rent_lamports,
        ),
    ):
        if value < 0:
            raise ValueError(
                f"{name} cannot be negative."
            )

    total_fee_bps = (
        protocol_fee_bps
        + creator_fee_bps
    )

    #
    # Pump exact-input quote contract.
    #
    # Step 1:
    #
    # net_quote =
    # floor(
    #   spendable_quote_in * 10_000
    #   /
    #   (10_000 + total_fee_bps)
    # )
    #
    initial_net_quote = (
        spendable_quote_in
        * BPS_DENOMINATOR
        // (
            BPS_DENOMINATOR
            + total_fee_bps
        )
    )

    protocol_fee = ceil_fee(
        initial_net_quote,
        protocol_fee_bps,
    )

    creator_fee = ceil_fee(
        initial_net_quote,
        creator_fee_bps,
    )

    pump_fees_total = (
        protocol_fee
        + creator_fee
    )

    curve_quote_in = (
        initial_net_quote
    )

    #
    # Official Pump correction:
    #
    # if net + fees > spendable,
    # reduce net by the excess.
    #
    excess = (
        curve_quote_in
        + pump_fees_total
        - spendable_quote_in
    )

    if excess > 0:
        curve_quote_in -= excess

    if curve_quote_in <= 1:
        return PumpBuySimulation(
            simulator_version=(
                SIMULATOR_VERSION
            ),

            spendable_quote_in=(
                spendable_quote_in
            ),

            protocol_fee_bps=(
                protocol_fee_bps
            ),

            creator_fee_bps=(
                creator_fee_bps
            ),

            total_fee_bps=(
                total_fee_bps
            ),

            initial_net_quote=(
                initial_net_quote
            ),

            curve_quote_in=max(
                0,
                curve_quote_in,
            ),

            protocol_fee=protocol_fee,
            creator_fee=creator_fee,

            pump_fees_total=(
                pump_fees_total
            ),

            pump_spend_used=(
                max(
                    0,
                    curve_quote_in,
                )
                + pump_fees_total
            ),

            unused_pump_budget=0,

            tokens_out=0,
            min_tokens_out=0,

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
                state.virtual_quote_reserves
            ),

            post_virtual_token_reserves=(
                state.virtual_token_reserves
            ),

            post_real_quote_reserves=(
                state.real_quote_reserves
            ),

            post_real_token_reserves=(
                state.real_token_reserves
            ),

            real_token_reserve_sufficient=False,

            would_exhaust_real_token_reserves=False,

            pre_spot_price_raw=(
                state.virtual_quote_reserves
                / state.virtual_token_reserves
            ),

            average_curve_price_raw=0.0,

            price_impact_bps=0.0,

            base_network_fee_lamports=(
                base_network_fee_lamports
            ),

            priority_fee_lamports=(
                priority_fee_lamports
            ),

            rent_lamports=(
                rent_lamports
            ),

            total_transaction_overhead_lamports=(
                base_network_fee_lamports
                + priority_fee_lamports
                + rent_lamports
            ),

            total_wallet_cost_lamports=(
                base_network_fee_lamports
                + priority_fee_lamports
                + rent_lamports
            ),

            all_in_entry_price_raw=0.0,

            executable=False,

            ineligible_reason=(
                "INSUFFICIENT_NET_QUOTE"
            ),
        )

    #
    # Official Pump exact-input curve quote:
    #
    # tokens_out =
    # floor(
    #   (net_quote - 1)
    #   * virtual_token_reserves
    #   /
    #   (
    #       virtual_quote_reserves
    #       + net_quote
    #       - 1
    #   )
    # )
    #
    curve_input_for_formula = (
        curve_quote_in
        - 1
    )

    tokens_out = (
        curve_input_for_formula
        * state.virtual_token_reserves
        // (
            state.virtual_quote_reserves
            + curve_input_for_formula
        )
    )

    min_tokens_out = (
        tokens_out
        * (
            BPS_DENOMINATOR
            - slippage_bps
        )
        // BPS_DENOMINATOR
    )

    reserve_sufficient = (
        tokens_out
        <= state.real_token_reserves
    )

    would_exhaust = (
        tokens_out
        == state.real_token_reserves
        and tokens_out > 0
    )

    if tokens_out <= 0:
        executable = False
        reason = "ZERO_TOKEN_OUTPUT"

    elif not reserve_sufficient:
        executable = False
        reason = (
            "INSUFFICIENT_REAL_TOKEN_RESERVES"
        )

    else:
        executable = True
        reason = None

    if executable:
        post_virtual_quote = (
            state.virtual_quote_reserves
            + curve_quote_in
        )

        post_virtual_token = (
            state.virtual_token_reserves
            - tokens_out
        )

        post_real_quote = (
            state.real_quote_reserves
            + curve_quote_in
        )

        post_real_token = (
            state.real_token_reserves
            - tokens_out
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

    pump_spend_used = (
        curve_quote_in
        + pump_fees_total
    )

    unused_budget = max(
        0,
        spendable_quote_in
        - pump_spend_used
    )

    transaction_overhead = (
        base_network_fee_lamports
        + priority_fee_lamports
        + rent_lamports
    )

    if executable:
        total_wallet_cost = (
            pump_spend_used
            + transaction_overhead
        )
    else:
        #
        # Failed/declined simulation does not
        # assume an on-chain transaction was
        # submitted.
        #
        total_wallet_cost = 0

    pre_spot_price = (
        state.virtual_quote_reserves
        / state.virtual_token_reserves
    )

    if tokens_out > 0:
        average_curve_price = (
            curve_quote_in
            / tokens_out
        )

        price_impact_bps = (
            (
                average_curve_price
                / pre_spot_price
            )
            - 1.0
        ) * BPS_DENOMINATOR

        all_in_entry_price = (
            total_wallet_cost
            / tokens_out
            if executable
            else 0.0
        )

    else:
        average_curve_price = 0.0
        price_impact_bps = 0.0
        all_in_entry_price = 0.0

    return PumpBuySimulation(
        simulator_version=(
            SIMULATOR_VERSION
        ),

        spendable_quote_in=(
            spendable_quote_in
        ),

        protocol_fee_bps=(
            protocol_fee_bps
        ),

        creator_fee_bps=(
            creator_fee_bps
        ),

        total_fee_bps=(
            total_fee_bps
        ),

        initial_net_quote=(
            initial_net_quote
        ),

        curve_quote_in=(
            curve_quote_in
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

        pump_spend_used=(
            pump_spend_used
        ),

        unused_pump_budget=(
            unused_budget
        ),

        tokens_out=(
            tokens_out
        ),

        min_tokens_out=(
            min_tokens_out
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

        real_token_reserve_sufficient=(
            reserve_sufficient
        ),

        would_exhaust_real_token_reserves=(
            would_exhaust
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

        rent_lamports=(
            rent_lamports
        ),

        total_transaction_overhead_lamports=(
            transaction_overhead
        ),

        total_wallet_cost_lamports=(
            total_wallet_cost
        ),

        all_in_entry_price_raw=(
            all_in_entry_price
        ),

        executable=(
            executable
        ),

        ineligible_reason=(
            reason
        ),
    )


def print_simulation(
    *,
    row: sqlite3.Row,
    state: PumpCurveState,
    result: PumpBuySimulation,
) -> None:
    print()
    print("=" * 78)

    print(
        "DELVE MEME AGENT — "
        "PUMP EXECUTION SIMULATOR"
    )

    print("=" * 78)

    print(
        f"Simulator version:       "
        f"{SIMULATOR_VERSION}"
    )

    print(
        f"State anchor signature:  "
        f"{row['signature']}"
    )

    print(
        f"Mint:                    "
        f"{row['mint']}"
    )

    print(
        f"Anchor side / ix:        "
        f"{row['side']} / "
        f"{row['ix_name']}"
    )

    print(
        f"Anchor slot:             "
        f"{row['slot']}"
    )

    print()

    print("PRE-TRADE CURVE STATE")
    print("-" * 78)

    print(
        f"Virtual SOL:             "
        f"{state.virtual_quote_reserves:,}"
    )

    print(
        f"Virtual tokens:          "
        f"{state.virtual_token_reserves:,}"
    )

    print(
        f"Real SOL:                "
        f"{state.real_quote_reserves:,}"
    )

    print(
        f"Real tokens:             "
        f"{state.real_token_reserves:,}"
    )

    print()

    print("HYPOTHETICAL DELVE ORDER")
    print("-" * 78)

    print(
        f"Pump spend budget:       "
        f"{result.spendable_quote_in:,} "
        f"lamports "
        f"("
        f"{lamports_to_sol(result.spendable_quote_in)} SOL"
        f")"
    )

    print(
        f"Protocol fee rate:       "
        f"{result.protocol_fee_bps} bps"
    )

    print(
        f"Creator fee rate:        "
        f"{result.creator_fee_bps} bps"
    )

    print(
        f"Total Pump fee rate:     "
        f"{result.total_fee_bps} bps"
    )

    print(
        f"Curve SOL input:         "
        f"{result.curve_quote_in:,}"
    )

    print(
        f"Protocol fee:            "
        f"{result.protocol_fee:,}"
    )

    print(
        f"Creator fee:             "
        f"{result.creator_fee:,}"
    )

    print(
        f"Pump spend actually used:"
        f" {result.pump_spend_used:,}"
    )

    print(
        f"Unused Pump budget:      "
        f"{result.unused_pump_budget:,}"
    )

    print()

    print(
        f"Expected tokens out:     "
        f"{result.tokens_out:,}"
    )

    print(
        f"Slippage tolerance:      "
        f"{result.slippage_bps} bps"
    )

    print(
        f"min_tokens_out:          "
        f"{result.min_tokens_out:,}"
    )

    print()

    print("EXECUTION ECONOMICS")
    print("-" * 78)

    print(
        f"Curve price impact:      "
        f"{result.price_impact_bps:+.2f} bps"
    )

    print(
        f"Base network fee:        "
        f"{result.base_network_fee_lamports:,}"
    )

    print(
        f"Priority fee:            "
        f"{result.priority_fee_lamports:,}"
    )

    print(
        f"Rent / account overhead: "
        f"{result.rent_lamports:,}"
    )

    print(
        f"Total wallet cost:       "
        f"{result.total_wallet_cost_lamports:,} "
        f"lamports"
    )

    if result.executable:
        print(
            f"Total wallet cost SOL:   "
            f"{lamports_to_sol(result.total_wallet_cost_lamports)}"
        )

        fee_and_overhead_bps = (
            (
                result.all_in_entry_price_raw
                / result.pre_spot_price_raw
            )
            - 1.0
        ) * BPS_DENOMINATOR

        print(
            f"All-in vs pre-spot:      "
            f"{fee_and_overhead_bps:+.2f} bps"
        )

    print()

    print("POST-TRADE CURVE STATE")
    print("-" * 78)

    print(
        f"Virtual SOL:             "
        f"{result.post_virtual_quote_reserves:,}"
    )

    print(
        f"Virtual tokens:          "
        f"{result.post_virtual_token_reserves:,}"
    )

    print(
        f"Real SOL:                "
        f"{result.post_real_quote_reserves:,}"
    )

    print(
        f"Real tokens:             "
        f"{result.post_real_token_reserves:,}"
    )

    print()

    print("EXECUTION DECISION")
    print("-" * 78)

    print(
        f"Real-token liquidity OK: "
        f"{'YES' if result.real_token_reserve_sufficient else 'NO'}"
    )

    print(
        f"Would exhaust curve:     "
        f"{'YES' if result.would_exhaust_real_token_reserves else 'NO'}"
    )

    print(
        f"Executable:              "
        f"{'YES' if result.executable else 'NO'}"
    )

    print(
        f"Reason:                  "
        f"{result.ineligible_reason or 'NONE'}"
    )

    print()
    print(
        "FEE SOURCE: EXPLICIT INPUT — "
        "NOT YET LIVE FEE RESOLVER"
    )

    print(
        "TRANSACTION SUBMISSION: NO"
    )

    print(
        "WALLET ACCESS: NO"
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )

    print("=" * 78)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--signature",
        required=True,
        help=(
            "Historical trade signature whose "
            "own post-state will reconstruct the "
            "curve state immediately before it."
        ),
    )

    parser.add_argument(
        "--spend-sol",
        required=True,
        help=(
            "Pump spend budget in SOL, before "
            "network/priority/rent overhead."
        ),
    )

    parser.add_argument(
        "--protocol-bps",
        required=True,
        type=int,
    )

    parser.add_argument(
        "--creator-bps",
        required=True,
        type=int,
    )

    parser.add_argument(
        "--slippage-bps",
        required=True,
        type=int,
    )

    parser.add_argument(
        "--base-network-fee-lamports",
        type=int,
        default=0,
    )

    parser.add_argument(
        "--priority-fee-lamports",
        type=int,
        default=0,
    )

    parser.add_argument(
        "--rent-lamports",
        type=int,
        default=0,
    )

    parser.add_argument(
        "--json",
        action="store_true",
    )

    args = parser.parse_args()

    spend_lamports = sol_to_lamports(
        args.spend_sol
    )

    with get_connection() as connection:
        row = load_trade(
            connection,
            args.signature,
        )

    state = reconstruct_pre_state_from_trade(
        row
    )

    result = calculate_exact_input_buy(
        state=state,

        spendable_quote_in=(
            spend_lamports
        ),

        protocol_fee_bps=(
            args.protocol_bps
        ),

        creator_fee_bps=(
            args.creator_bps
        ),

        slippage_bps=(
            args.slippage_bps
        ),

        base_network_fee_lamports=(
            args.base_network_fee_lamports
        ),

        priority_fee_lamports=(
            args.priority_fee_lamports
        ),

        rent_lamports=(
            args.rent_lamports
        ),
    )

    if args.json:
        print(
            json.dumps(
                asdict(result),
                indent=2,
                sort_keys=True,
            )
        )

        return

    print_simulation(
        row=row,
        state=state,
        result=result,
    )


if __name__ == "__main__":
    main()