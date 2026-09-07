from __future__ import annotations

from dataclasses import dataclass

from src.execution.pump_sell_simulator import (
    PumpSellSimulation,
)


EXIT_EXECUTION_CONTRACT_VERSION = (
    "exit-execution-v1"
)

PUMP_BONDING_CURVE_VENUE = (
    "PUMP_BONDING_CURVE"
)


@dataclass(frozen=True)
class PumpBondingCurveExitEvidence:
    """
    Venue-specific audit evidence for a Pump
    bonding-curve sell.

    These fields are deliberately NOT part of
    the generic execution economics contract.
    """

    pre_virtual_quote_reserves: int
    pre_virtual_token_reserves: int
    pre_real_quote_reserves: int
    pre_real_token_reserves: int

    post_virtual_quote_reserves: int
    post_virtual_token_reserves: int
    post_real_quote_reserves: int
    post_real_token_reserves: int


@dataclass(frozen=True)
class ExitExecution:
    """
    Venue-neutral economic result of attempting
    to sell tokens.

    Portfolio/accounting code should eventually
    consume this object rather than knowing how
    a particular venue prices an exit.
    """

    contract_version: str
    venue: str
    simulator_version: str

    tokens_in: int

    protocol_fee_bps: int
    creator_fee_bps: int
    slippage_bps: int

    gross_quote_out: int

    protocol_fee: int
    creator_fee: int

    net_quote_out_after_venue_fees: int
    min_quote_out: int

    price_impact_bps: float

    base_network_fee_lamports: int
    priority_fee_lamports: int
    total_transaction_overhead_lamports: int

    net_wallet_proceeds_lamports: int
    all_in_exit_price_raw: float

    executable: bool
    ineligible_reason: str | None

    venue_evidence: (
        PumpBondingCurveExitEvidence | None
    )


def normalize_pump_sell_execution(
    simulation: PumpSellSimulation,
) -> ExitExecution:
    """
    Convert the already-validated Pump bonding
    curve simulator result into Delve's generic
    exit-execution contract.

    This function performs no new economics.
    It is intentionally a lossless mapping of
    the fields Delve needs for accounting,
    execution evaluation, and audit evidence.
    """

    evidence = PumpBondingCurveExitEvidence(
        pre_virtual_quote_reserves=int(
            simulation.pre_virtual_quote_reserves
        ),
        pre_virtual_token_reserves=int(
            simulation.pre_virtual_token_reserves
        ),
        pre_real_quote_reserves=int(
            simulation.pre_real_quote_reserves
        ),
        pre_real_token_reserves=int(
            simulation.pre_real_token_reserves
        ),
        post_virtual_quote_reserves=int(
            simulation.post_virtual_quote_reserves
        ),
        post_virtual_token_reserves=int(
            simulation.post_virtual_token_reserves
        ),
        post_real_quote_reserves=int(
            simulation.post_real_quote_reserves
        ),
        post_real_token_reserves=int(
            simulation.post_real_token_reserves
        ),
    )

    return ExitExecution(
        contract_version=(
            EXIT_EXECUTION_CONTRACT_VERSION
        ),
        venue=PUMP_BONDING_CURVE_VENUE,
        simulator_version=str(
            simulation.simulator_version
        ),
        tokens_in=int(
            simulation.tokens_in
        ),
        protocol_fee_bps=int(
            simulation.protocol_fee_bps
        ),
        creator_fee_bps=int(
            simulation.creator_fee_bps
        ),
        slippage_bps=int(
            simulation.slippage_bps
        ),
        gross_quote_out=int(
            simulation.gross_quote_out
        ),
        protocol_fee=int(
            simulation.protocol_fee
        ),
        creator_fee=int(
            simulation.creator_fee
        ),
        net_quote_out_after_venue_fees=int(
            simulation.net_quote_out_after_pump_fees
        ),
        min_quote_out=int(
            simulation.min_quote_out
        ),
        price_impact_bps=float(
            simulation.price_impact_bps
        ),
        base_network_fee_lamports=int(
            simulation.base_network_fee_lamports
        ),
        priority_fee_lamports=int(
            simulation.priority_fee_lamports
        ),
        total_transaction_overhead_lamports=int(
            simulation
            .total_transaction_overhead_lamports
        ),
        net_wallet_proceeds_lamports=int(
            simulation.net_wallet_proceeds_lamports
        ),
        all_in_exit_price_raw=float(
            simulation.all_in_exit_price_raw
        ),
        executable=bool(
            simulation.executable
        ),
        ineligible_reason=(
            simulation.ineligible_reason
        ),
        venue_evidence=evidence,
    )
