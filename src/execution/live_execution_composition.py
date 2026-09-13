from __future__ import annotations

from typing import Any

from src.execution.live_buy_runtime import (
    LiveBuyRuntimeResult,
    run_live_buy_once,
)
from src.execution.live_curve_state import (
    LivePumpCurveState,
)
from src.execution.live_operating_config import (
    LIVE_OPERATING_CONFIG_VERSION,
    LiveOperatingConfig,
)
from src.execution.live_sell_runtime import (
    LiveSellRuntimeResult,
    run_live_sell_once,
)
from src.execution.solana_message_signer import (
    LazyEnvironmentMessageSigner,
)
from src.safety.token_safety_gate import (
    TokenSafetyGateResult,
)
from src.strategies.live_exit_policy import (
    LiveExitPolicy,
)


LIVE_EXECUTION_COMPOSITION_VERSION = (
    "live-execution-composition-v1"
)


def _valid_config(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            LiveOperatingConfig,
        )
        and LIVE_OPERATING_CONFIG_VERSION
        == "live-operating-config-v1"
    )


async def run_production_live_buy_once(
    *,
    config: LiveOperatingConfig,
    mint: str,
    wallet_pubkey: str,
    protected_cash_lamports: int,
    live_curve: LivePumpCurveState,
    safety: TokenSafetyGateResult,
    protocol_fee_bps: int,
    creator_fee_bps: int,
    buy_slippage_bps: int,
    buy_base_network_fee_lamports: int,
    buy_priority_fee_lamports: int,
    buy_rent_lamports: int,
    exit_slippage_bps: int,
    exit_base_network_fee_lamports: int,
    exit_priority_fee_lamports: int,
    reservation_ttl_seconds: float,
    max_authorization_age_seconds: float,
    compute_unit_limit: int | None,
    signal_virtual_quote_reserves: int | None = None,
    signal_virtual_token_reserves: int | None = None,
    min_context_slot: int | None = None,
) -> LiveBuyRuntimeResult:
    """
    Production one-shot BUY composition boundary.

    This layer owns no BUY authority. It only translates
    non-secret process policy into the existing live BUY
    runtime and provides an inert lazy production signer.

    The runtime remains the sole owner of:
      - recovery ordering;
      - operational-kill ordering;
      - signer preflight;
      - fresh-entry authorization;
      - reservation;
      - signing;
      - submission/reconciliation behavior.
    """
    if not _valid_config(
        config
    ):
        raise TypeError(
            "config must be LiveOperatingConfig"
        )

    signer = (
        LazyEnvironmentMessageSigner()
    )

    return await run_live_buy_once(
        kill_switch=config.operational_kill,
        mint=mint,
        wallet_pubkey=wallet_pubkey,
        protected_cash_lamports=(
            protected_cash_lamports
        ),
        live_curve=live_curve,
        safety=safety,
        signal_virtual_quote_reserves=(
            signal_virtual_quote_reserves
        ),
        signal_virtual_token_reserves=(
            signal_virtual_token_reserves
        ),
        protocol_fee_bps=protocol_fee_bps,
        creator_fee_bps=creator_fee_bps,
        buy_slippage_bps=buy_slippage_bps,
        buy_base_network_fee_lamports=(
            buy_base_network_fee_lamports
        ),
        buy_priority_fee_lamports=(
            buy_priority_fee_lamports
        ),
        buy_rent_lamports=buy_rent_lamports,
        exit_slippage_bps=exit_slippage_bps,
        exit_base_network_fee_lamports=(
            exit_base_network_fee_lamports
        ),
        exit_priority_fee_lamports=(
            exit_priority_fee_lamports
        ),
        reservation_ttl_seconds=(
            reservation_ttl_seconds
        ),
        max_authorization_age_seconds=(
            max_authorization_age_seconds
        ),
        compute_unit_limit=compute_unit_limit,
        signer=signer,
        policy=config.risk_policy,
        min_context_slot=min_context_slot,
        db_path=config.db_path,
    )


async def run_production_live_sell_once(
    *,
    config: LiveOperatingConfig,
    compute_unit_limit: int | None,
    wallet_pubkey: str,
    evaluated_at: int,
    policy: LiveExitPolicy,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    min_context_slot: int | None = None,
) -> LiveSellRuntimeResult:
    """
    Production one-shot SELL composition boundary.

    SELL exit policy remains explicit caller input. The
    LiveOperatingConfig RiskPolicy is BUY/account-risk policy
    and is deliberately not substituted for LiveExitPolicy.

    The signer is constructed inertly. The live SELL runtime
    remains responsible for deciding whether signer authority
    ever needs to be touched.
    """
    if not _valid_config(
        config
    ):
        raise TypeError(
            "config must be LiveOperatingConfig"
        )

    signer = (
        LazyEnvironmentMessageSigner()
    )

    return await run_live_sell_once(
        kill_switch=config.operational_kill,
        signer=signer,
        compute_unit_limit=compute_unit_limit,
        wallet_pubkey=wallet_pubkey,
        evaluated_at=evaluated_at,
        policy=policy,
        slippage_bps=slippage_bps,
        base_network_fee_lamports=(
            base_network_fee_lamports
        ),
        priority_fee_lamports=(
            priority_fee_lamports
        ),
        min_context_slot=min_context_slot,
        db_path=config.db_path,
    )
