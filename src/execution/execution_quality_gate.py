from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_DOWN

from src.execution.pump_execution_simulator import (
    LAMPORTS_PER_SOL,
    PumpBuySimulation,
    PumpCurveState,
    calculate_exact_input_buy,
)
from src.safety.token_safety_gate import (
    PASS as SAFETY_PASS,
    resolve_and_gate,
)
from src.safety.token_safety_resolver import (
    TokenSafetySnapshot,
)


GATE_VERSION = "execution-quality-gate-v1"

PASS = "PASS"
ABORT = "ABORT"
UNKNOWN = "UNKNOWN"


#
# Shadow-policy limits.
#
# These are execution protection limits,
# NOT authorization for live capital.
#
MAX_POSITIVE_MARKET_DRIFT_BPS = 500.0
MAX_NEGATIVE_MARKET_DRIFT_BPS = 500.0

MAX_PRICE_IMPACT_BPS = 200.0
MAX_ALL_IN_VS_SIGNAL_BPS = 650.0

MAX_SNAPSHOT_AGE_SECONDS = 3


@dataclass(frozen=True)
class ExecutionQualityResult:
    gate_version: str

    mint: str

    status: str
    reasons: tuple[str, ...]

    signal_spot_price_raw: float
    live_spot_price_raw: float | None

    market_drift_bps: float | None
    price_impact_bps: float | None
    all_in_vs_signal_bps: float | None

    spendable_quote_in: int

    protocol_fee_bps: int
    creator_fee_bps: int

    slippage_bps: int

    snapshot_age_seconds: float | None

    simulation: PumpBuySimulation | None

    evaluated_at: int

    @property
    def allows_order_build(self) -> bool:
        return self.status == PASS


def pct_change_bps(
    new_value: float,
    old_value: float,
) -> float:
    if old_value <= 0:
        raise ValueError(
            "Reference price must be positive."
        )

    return (
        (
            new_value
            / old_value
        )
        - 1.0
    ) * 10_000.0


def sol_to_lamports(
    value: str,
) -> int:
    amount = Decimal(value)

    if amount <= 0:
        raise ValueError(
            "SOL amount must be positive."
        )

    lamports = int(
        (
            amount
            * Decimal(
                LAMPORTS_PER_SOL
            )
        ).to_integral_value(
            rounding=ROUND_DOWN
        )
    )

    if lamports <= 0:
        raise ValueError(
            "SOL amount is below one lamport."
        )

    return lamports


def curve_state_from_snapshot(
    snapshot: TokenSafetySnapshot,
) -> PumpCurveState:
    curve = snapshot.bonding_curve

    return PumpCurveState(
        virtual_quote_reserves=(
            curve.virtual_quote_reserves
        ),
        virtual_token_reserves=(
            curve.virtual_token_reserves
        ),
        real_quote_reserves=(
            curve.real_quote_reserves
        ),
        real_token_reserves=(
            curve.real_token_reserves
        ),
    )


def evaluate_execution_quality(
    *,
    snapshot: TokenSafetySnapshot,
    signal_virtual_quote_reserves: int,
    signal_virtual_token_reserves: int,
    spendable_quote_in: int,
    protocol_fee_bps: int,
    creator_fee_bps: int,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    rent_lamports: int,
) -> ExecutionQualityResult:

    now = int(
        time.time()
    )

    unknown_reasons: list[str] = []
    abort_reasons: list[str] = []

    if (
        signal_virtual_quote_reserves
        <= 0
        or signal_virtual_token_reserves
        <= 0
    ):
        return ExecutionQualityResult(
            gate_version=GATE_VERSION,
            mint=snapshot.mint,
            status=UNKNOWN,
            reasons=(
                "INVALID_SIGNAL_RESERVES",
            ),
            signal_spot_price_raw=0.0,
            live_spot_price_raw=None,
            market_drift_bps=None,
            price_impact_bps=None,
            all_in_vs_signal_bps=None,
            spendable_quote_in=(
                spendable_quote_in
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            slippage_bps=(
                slippage_bps
            ),
            snapshot_age_seconds=None,
            simulation=None,
            evaluated_at=now,
        )

    signal_spot_price = (
        signal_virtual_quote_reserves
        / signal_virtual_token_reserves
    )

    snapshot_age = max(
        0.0,
        time.time()
        - snapshot.fetched_at,
    )

    if (
        snapshot_age
        > MAX_SNAPSHOT_AGE_SECONDS
    ):
        unknown_reasons.append(
            "LIVE_SNAPSHOT_STALE"
        )

    state = curve_state_from_snapshot(
        snapshot
    )

    if (
        state.virtual_quote_reserves
        <= 0
        or state.virtual_token_reserves
        <= 0
    ):
        unknown_reasons.append(
            "INVALID_LIVE_CURVE_STATE"
        )

        live_spot_price = None

    else:
        live_spot_price = (
            state.virtual_quote_reserves
            / state.virtual_token_reserves
        )

    if live_spot_price is None:
        market_drift_bps = None

    else:
        market_drift_bps = (
            pct_change_bps(
                live_spot_price,
                signal_spot_price,
            )
        )

        if (
            market_drift_bps
            > MAX_POSITIVE_MARKET_DRIFT_BPS
        ):
            abort_reasons.append(
                "MARKET_DRIFT_TOO_HIGH"
            )

        elif (
            market_drift_bps
            < -MAX_NEGATIVE_MARKET_DRIFT_BPS
        ):
            abort_reasons.append(
                "SIGNAL_STATE_INVALIDATED_DOWN"
            )

    simulation: (
        PumpBuySimulation | None
    ) = None

    try:
        simulation = (
            calculate_exact_input_buy(
                state=state,
                spendable_quote_in=(
                    spendable_quote_in
                ),
                protocol_fee_bps=(
                    protocol_fee_bps
                ),
                creator_fee_bps=(
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
                rent_lamports=(
                    rent_lamports
                ),
            )
        )

    except Exception as error:
        unknown_reasons.append(
            "BUY_SIMULATION_FAILED:"
            f"{type(error).__name__}"
        )

    price_impact_bps: (
        float | None
    ) = None

    all_in_vs_signal_bps: (
        float | None
    ) = None

    if simulation is not None:

        if not simulation.executable:
            abort_reasons.append(
                "BUY_NOT_EXECUTABLE:"
                f"{simulation.ineligible_reason}"
            )

        else:
            price_impact_bps = (
                simulation.price_impact_bps
            )

            if (
                price_impact_bps
                > MAX_PRICE_IMPACT_BPS
            ):
                abort_reasons.append(
                    "OWN_PRICE_IMPACT_TOO_HIGH"
                )

            if (
                simulation.all_in_entry_price_raw
                > 0
            ):
                all_in_vs_signal_bps = (
                    pct_change_bps(
                        simulation.all_in_entry_price_raw,
                        signal_spot_price,
                    )
                )

                if (
                    all_in_vs_signal_bps
                    > MAX_ALL_IN_VS_SIGNAL_BPS
                ):
                    abort_reasons.append(
                        "ALL_IN_ENTRY_TOO_EXPENSIVE"
                    )

            else:
                unknown_reasons.append(
                    "ALL_IN_ENTRY_PRICE_UNAVAILABLE"
                )

    #
    # Known bad execution quality takes
    # precedence. Otherwise uncertainty
    # must fail closed.
    #
    abort_reasons = list(
        dict.fromkeys(
            abort_reasons
        )
    )

    unknown_reasons = list(
        dict.fromkeys(
            unknown_reasons
        )
    )

    if abort_reasons:
        status = ABORT
        reasons = tuple(
            abort_reasons
        )

    elif unknown_reasons:
        status = UNKNOWN
        reasons = tuple(
            unknown_reasons
        )

    else:
        status = PASS
        reasons = ()

    return ExecutionQualityResult(
        gate_version=GATE_VERSION,

        mint=snapshot.mint,

        status=status,

        reasons=reasons,

        signal_spot_price_raw=(
            signal_spot_price
        ),

        live_spot_price_raw=(
            live_spot_price
        ),

        market_drift_bps=(
            market_drift_bps
        ),

        price_impact_bps=(
            price_impact_bps
        ),

        all_in_vs_signal_bps=(
            all_in_vs_signal_bps
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

        slippage_bps=(
            slippage_bps
        ),

        snapshot_age_seconds=(
            snapshot_age
        ),

        simulation=simulation,

        evaluated_at=now,
    )


async def resolve_and_evaluate(
    *,
    mint: str,
    signal_virtual_quote_reserves: int,
    signal_virtual_token_reserves: int,
    spendable_quote_in: int,
    protocol_fee_bps: int,
    creator_fee_bps: int,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    rent_lamports: int,
) -> ExecutionQualityResult:

    safety_result = await resolve_and_gate(
        mint
    )

    if (
        safety_result.status
        != SAFETY_PASS
        or safety_result.snapshot
        is None
    ):
        return ExecutionQualityResult(
            gate_version=GATE_VERSION,

            mint=mint,

            status=UNKNOWN,

            reasons=(
                "TOKEN_SAFETY_NOT_PASS",
                *safety_result.reasons,
            ),

            signal_spot_price_raw=(
                signal_virtual_quote_reserves
                / signal_virtual_token_reserves
                if (
                    signal_virtual_quote_reserves > 0
                    and signal_virtual_token_reserves > 0
                )
                else 0.0
            ),

            live_spot_price_raw=None,

            market_drift_bps=None,
            price_impact_bps=None,
            all_in_vs_signal_bps=None,

            spendable_quote_in=(
                spendable_quote_in
            ),

            protocol_fee_bps=(
                protocol_fee_bps
            ),

            creator_fee_bps=(
                creator_fee_bps
            ),

            slippage_bps=(
                slippage_bps
            ),

            snapshot_age_seconds=None,

            simulation=None,

            evaluated_at=int(
                time.time()
            ),
        )

    return evaluate_execution_quality(
        snapshot=safety_result.snapshot,

        signal_virtual_quote_reserves=(
            signal_virtual_quote_reserves
        ),

        signal_virtual_token_reserves=(
            signal_virtual_token_reserves
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

        slippage_bps=(
            slippage_bps
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
    )


def print_result(
    result: ExecutionQualityResult,
) -> None:
    print()
    print("=" * 82)

    print(
        "DELVE MEME AGENT — "
        "EXECUTION QUALITY GATE"
    )

    print("=" * 82)

    print(
        f"Gate:                    "
        f"{result.gate_version}"
    )

    print(
        f"Mint:                    "
        f"{result.mint}"
    )

    print(
        f"Decision:                "
        f"{result.status}"
    )

    print(
        f"Order build allowed:     "
        f"{result.allows_order_build}"
    )

    if result.reasons:
        print()
        print("REASONS")
        print("-" * 82)

        for reason in result.reasons:
            print(
                f"- {reason}"
            )

    print()
    print("ENTRY QUALITY")
    print("-" * 82)

    print(
        f"Market drift:            "
        f"{result.market_drift_bps:+.2f} bps"
        if result.market_drift_bps is not None
        else
        "Market drift:            UNKNOWN"
    )

    print(
        f"Own price impact:        "
        f"{result.price_impact_bps:+.2f} bps"
        if result.price_impact_bps is not None
        else
        "Own price impact:        UNKNOWN"
    )

    print(
        f"All-in vs signal:        "
        f"{result.all_in_vs_signal_bps:+.2f} bps"
        if result.all_in_vs_signal_bps is not None
        else
        "All-in vs signal:        UNKNOWN"
    )

    print(
        f"Snapshot age:            "
        f"{result.snapshot_age_seconds:.3f}s"
        if result.snapshot_age_seconds is not None
        else
        "Snapshot age:            UNKNOWN"
    )

    print()
    print("POLICY LIMITS")
    print("-" * 82)

    print(
        f"Max positive drift:      "
        f"+{MAX_POSITIVE_MARKET_DRIFT_BPS:.0f} bps"
    )

    print(
        f"Max negative drift:      "
        f"-{MAX_NEGATIVE_MARKET_DRIFT_BPS:.0f} bps"
    )

    print(
        f"Max own impact:          "
        f"{MAX_PRICE_IMPACT_BPS:.0f} bps"
    )

    print(
        f"Max all-in deterioration:"
        f" {MAX_ALL_IN_VS_SIGNAL_BPS:.0f} bps"
    )

    if result.simulation is not None:
        print()
        print("BUY SIMULATION")
        print("-" * 82)

        print(
            f"Executable:              "
            f"{result.simulation.executable}"
        )

        print(
            f"Tokens out raw:          "
            f"{result.simulation.tokens_out:,}"
        )

        print(
            f"Min tokens out raw:      "
            f"{result.simulation.min_tokens_out:,}"
        )

        print(
            f"Pump fee total:          "
            f"{result.simulation.total_fee_bps} bps"
        )

        print(
            f"Wallet cost lamports:    "
            f"{result.simulation.total_wallet_cost_lamports:,}"
        )

    print()
    print(
        "TRANSACTION SUBMISSION: NO"
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )

    print("=" * 82)


async def async_main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--mint",
        required=True,
    )

    parser.add_argument(
        "--signal-vquote",
        type=int,
        required=True,
    )

    parser.add_argument(
        "--signal-vtoken",
        type=int,
        required=True,
    )

    parser.add_argument(
        "--spend-sol",
        required=True,
    )

    parser.add_argument(
        "--protocol-fee-bps",
        type=int,
        required=True,
    )

    parser.add_argument(
        "--creator-fee-bps",
        type=int,
        required=True,
    )

    parser.add_argument(
        "--slippage-bps",
        type=int,
        default=300,
    )

    parser.add_argument(
        "--base-network-fee-lamports",
        type=int,
        default=5000,
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

    result = await resolve_and_evaluate(
        mint=args.mint,

        signal_virtual_quote_reserves=(
            args.signal_vquote
        ),

        signal_virtual_token_reserves=(
            args.signal_vtoken
        ),

        spendable_quote_in=(
            sol_to_lamports(
                args.spend_sol
            )
        ),

        protocol_fee_bps=(
            args.protocol_fee_bps
        ),

        creator_fee_bps=(
            args.creator_fee_bps
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
        output = asdict(
            result
        )

        output[
            "allows_order_build"
        ] = (
            result.allows_order_build
        )

        print(
            json.dumps(
                output,
                indent=2,
                sort_keys=True,
            )
        )

        return

    print_result(
        result
    )


def main() -> None:
    asyncio.run(
        async_main()
    )


if __name__ == "__main__":
    main()