from __future__ import annotations

from dataclasses import dataclass

from src.execution.pump_execution_simulator import (
    PumpCurveState,
)
from src.execution.pump_sell_simulator import (
    PumpSellSimulation,
    calculate_exact_input_sell,
    find_max_liquidity_feasible_sell,
)


SHADOW_EXIT_RECOVERY_VERSION = (
    "shadow-exit-recovery-v1"
)


@dataclass(frozen=True)
class ShadowExitRecoveryPlan:
    recovery_version: str

    action: str
    reason: str

    tokens_held: int
    tokens_to_sell: int
    tokens_after: int

    full_sell_simulation: PumpSellSimulation

    planned_sell_simulation: (
        PumpSellSimulation | None
    )


def plan_shadow_exit_recovery(
    *,
    tokens_held: int,
    curve_state: PumpCurveState,

    protocol_fee_bps: int,
    creator_fee_bps: int,

    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
) -> ShadowExitRecoveryPlan:
    """
    Plan how an already-mandated exit can be
    recovered through the currently supported
    Pump bonding-curve route.

    This function does not decide WHETHER a
    position should exit. The exit engine owns
    that decision.

    Actions:

    FINAL
        Full remaining position can execute.

    PARTIAL
        Full sell is blocked specifically by
        real quote liquidity, but the maximum
        liquidity-feasible partial sale has
        positive economic wallet recovery.

    WRITE_OFF_CANDIDATE
        Full sell is liquidity blocked and even
        the maximum liquidity-feasible partial
        has no positive wallet recovery.

    UNKNOWN
        Full sell failed for some other reason.
        Fail closed rather than inventing an
        alternate exit route.
    """

    tokens_held = int(
        tokens_held
    )

    if tokens_held <= 0:
        raise ValueError(
            "tokens_held must be positive."
        )

    full_simulation = (
        calculate_exact_input_sell(
            state=curve_state,

            tokens_in=tokens_held,

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

    if full_simulation.executable:
        return ShadowExitRecoveryPlan(
            recovery_version=(
                SHADOW_EXIT_RECOVERY_VERSION
            ),

            action="FINAL",
            reason="FULL_SELL_EXECUTABLE",

            tokens_held=tokens_held,
            tokens_to_sell=tokens_held,
            tokens_after=0,

            full_sell_simulation=(
                full_simulation
            ),

            planned_sell_simulation=(
                full_simulation
            ),
        )

    if (
        full_simulation.ineligible_reason
        != "INSUFFICIENT_REAL_QUOTE_RESERVES"
    ):
        return ShadowExitRecoveryPlan(
            recovery_version=(
                SHADOW_EXIT_RECOVERY_VERSION
            ),

            action="UNKNOWN",
            reason=(
                "FULL_SELL_FAILED:"
                f"{full_simulation.ineligible_reason}"
            ),

            tokens_held=tokens_held,
            tokens_to_sell=0,
            tokens_after=tokens_held,

            full_sell_simulation=(
                full_simulation
            ),

            planned_sell_simulation=None,
        )

    (
        partial_tokens,
        partial_simulation,
    ) = find_max_liquidity_feasible_sell(
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

    if (
        partial_tokens <= 0
        or partial_simulation is None
    ):
        return ShadowExitRecoveryPlan(
            recovery_version=(
                SHADOW_EXIT_RECOVERY_VERSION
            ),

            action="WRITE_OFF_CANDIDATE",
            reason=(
                "NO_LIQUIDITY_FEASIBLE_RECOVERY"
            ),

            tokens_held=tokens_held,
            tokens_to_sell=0,
            tokens_after=tokens_held,

            full_sell_simulation=(
                full_simulation
            ),

            planned_sell_simulation=None,
        )

    #
    # Full liquidation already failed for
    # insufficient real quote reserves. Therefore
    # a liquidity-bound partial result must leave
    # residual tokens. Anything else indicates an
    # inconsistent sizing result and fails closed.
    #
    if partial_tokens >= tokens_held:
        return ShadowExitRecoveryPlan(
            recovery_version=(
                SHADOW_EXIT_RECOVERY_VERSION
            ),

            action="UNKNOWN",
            reason=(
                "INVALID_PARTIAL_LIQUIDITY_BOUNDARY"
            ),

            tokens_held=tokens_held,
            tokens_to_sell=0,
            tokens_after=tokens_held,

            full_sell_simulation=(
                full_simulation
            ),

            planned_sell_simulation=(
                partial_simulation
            ),
        )

    if (
        partial_simulation.executable
        and int(
            partial_simulation
            .net_wallet_proceeds_lamports
        ) > 0
    ):
        return ShadowExitRecoveryPlan(
            recovery_version=(
                SHADOW_EXIT_RECOVERY_VERSION
            ),

            action="PARTIAL",
            reason=(
                "MAX_LIQUIDITY_FEASIBLE_RECOVERY"
            ),

            tokens_held=tokens_held,

            tokens_to_sell=(
                partial_tokens
            ),

            tokens_after=(
                tokens_held
                - partial_tokens
            ),

            full_sell_simulation=(
                full_simulation
            ),

            planned_sell_simulation=(
                partial_simulation
            ),
        )

    return ShadowExitRecoveryPlan(
        recovery_version=(
            SHADOW_EXIT_RECOVERY_VERSION
        ),

        action="WRITE_OFF_CANDIDATE",
        reason="NO_ECONOMIC_PARTIAL_RECOVERY",

        tokens_held=tokens_held,
        tokens_to_sell=0,
        tokens_after=tokens_held,

        full_sell_simulation=(
            full_simulation
        ),

        planned_sell_simulation=(
            partial_simulation
        ),
    )
