from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from src.execution.live_fee_resolver import (
    resolve_live_event_fee_bps,
)

from src.execution.pump_execution_simulator import (
    PumpCurveState,
)

from src.portfolio.shadow_portfolio import (
    DB_PATH,
    ShadowCloseResult,
    ShadowMarkResult,
    close_shadow_position,
    mark_open_position,
)

from src.strategies.shadow_exit_engine import (
    ShadowExitDecision,
    evaluate_shadow_exit,
)

from src.strategies.shadow_position_runtime import (
    SHADOW_EXIT_BASE_NETWORK_FEE_LAMPORTS,
    SHADOW_EXIT_PRIORITY_FEE_LAMPORTS,
    SHADOW_EXIT_SLIPPAGE_BPS,
    get_shadow_position_lock,
    initialize_shadow_position_manager,
    is_open_shadow_mint_tracked,
    register_open_shadow_mint,
    unregister_open_shadow_mint,
)


SHADOW_POSITION_MANAGER_VERSION = (
    "shadow-position-manager-v1"
)


@dataclass(frozen=True)
class ShadowPositionEventResult:
    manager_version: str

    status: str
    reason: str | None

    mint: str

    fee_status: str | None
    protocol_fee_bps: int | None
    creator_fee_bps: int | None

    mark: ShadowMarkResult | None

    exit_decision: (
        ShadowExitDecision | None
    )

    close: ShadowCloseResult | None



async def process_shadow_position_event(
    *,
    mint: str,
    event_user: str | None,

    quote_amount: int,

    protocol_fee_lamports: int | None,
    creator_fee_lamports: int | None,

    event_protocol_fee_bps: int | None,
    event_creator_fee_bps: int | None,

    virtual_quote_reserves: int,
    virtual_token_reserves: int,

    real_quote_reserves: int,
    real_token_reserves: int,

    observed_at: int | None = None,

    db_path: Path = DB_PATH,
) -> ShadowPositionEventResult:

    #
    # Fast path. Nearly every Pump event
    # should stop here without touching SQLite.
    #
    if not is_open_shadow_mint_tracked(
        mint,
        db_path=db_path,
    ):
        return ShadowPositionEventResult(
            manager_version=(
                SHADOW_POSITION_MANAGER_VERSION
            ),

            status="IGNORED",
            reason="NO_TRACKED_POSITION",

            mint=mint,

            fee_status=None,
            protocol_fee_bps=None,
            creator_fee_bps=None,

            mark=None,
            exit_decision=None,
            close=None,
        )

    if observed_at is None:
        observed_at = int(
            time.time()
        )

    lock = get_shadow_position_lock(
        mint
    )

    async with lock:
        #
        # Another queued event may already have
        # closed this position.
        #
        if not is_open_shadow_mint_tracked(
            mint,
            db_path=db_path,
        ):
            return ShadowPositionEventResult(
                manager_version=(
                    SHADOW_POSITION_MANAGER_VERSION
                ),

                status="IGNORED",
                reason="POSITION_ALREADY_CLOSED",

                mint=mint,

                fee_status=None,
                protocol_fee_bps=None,
                creator_fee_bps=None,

                mark=None,
                exit_decision=None,
                close=None,
            )

        (
            fee_status,
            protocol_fee_bps,
            creator_fee_bps,
        ) = resolve_live_event_fee_bps(
            quote_amount=int(
                quote_amount
            ),

            protocol_fee_lamports=(
                protocol_fee_lamports
            ),

            creator_fee_lamports=(
                creator_fee_lamports
            ),

            event_protocol_fee_bps=(
                event_protocol_fee_bps
            ),

            event_creator_fee_bps=(
                event_creator_fee_bps
            ),

            event_user=(
                event_user
            ),
        )

        #
        # Fail closed. This includes the
        # privileged Mayhem-agent fee regime,
        # which is not applicable to our wallet.
        #
        if (
            protocol_fee_bps is None
            or creator_fee_bps is None
        ):
            return ShadowPositionEventResult(
                manager_version=(
                    SHADOW_POSITION_MANAGER_VERSION
                ),

                status="SKIPPED",
                reason=(
                    "UNUSABLE_FEE_REGIME:"
                    f"{fee_status}"
                ),

                mint=mint,

                fee_status=(
                    fee_status
                ),

                protocol_fee_bps=None,
                creator_fee_bps=None,

                mark=None,
                exit_decision=None,
                close=None,
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

        mark = mark_open_position(
            mint=mint,

            curve_state=(
                curve_state
            ),

            protocol_fee_bps=int(
                protocol_fee_bps
            ),

            creator_fee_bps=int(
                creator_fee_bps
            ),

            slippage_bps=(
                SHADOW_EXIT_SLIPPAGE_BPS
            ),

            base_network_fee_lamports=(
                SHADOW_EXIT_BASE_NETWORK_FEE_LAMPORTS
            ),

            priority_fee_lamports=(
                SHADOW_EXIT_PRIORITY_FEE_LAMPORTS
            ),

            mark_timestamp=int(
                observed_at
            ),

            db_path=db_path,
        )

        if mark.status == "NO_POSITION":
            unregister_open_shadow_mint(
                mint,
                db_path=db_path,
            )

            return ShadowPositionEventResult(
                manager_version=(
                    SHADOW_POSITION_MANAGER_VERSION
                ),

                status="NO_POSITION",
                reason=None,

                mint=mint,

                fee_status=(
                    fee_status
                ),

                protocol_fee_bps=int(
                    protocol_fee_bps
                ),

                creator_fee_bps=int(
                    creator_fee_bps
                ),

                mark=mark,
                exit_decision=None,
                close=None,
            )

        if mark.status != "MARKED":
            return ShadowPositionEventResult(
                manager_version=(
                    SHADOW_POSITION_MANAGER_VERSION
                ),

                status="UNKNOWN",
                reason=(
                    "LIQUIDATION_MARK_FAILED:"
                    f"{mark.status}"
                ),

                mint=mint,

                fee_status=(
                    fee_status
                ),

                protocol_fee_bps=int(
                    protocol_fee_bps
                ),

                creator_fee_bps=int(
                    creator_fee_bps
                ),

                mark=mark,
                exit_decision=None,
                close=None,
            )

        if (
            mark.entry_timestamp is None
            or mark.entry_wallet_cost_lamports
            is None
        ):
            return ShadowPositionEventResult(
                manager_version=(
                    SHADOW_POSITION_MANAGER_VERSION
                ),

                status="UNKNOWN",
                reason=(
                    "ENTRY_ECONOMICS_UNAVAILABLE"
                ),

                mint=mint,

                fee_status=(
                    fee_status
                ),

                protocol_fee_bps=int(
                    protocol_fee_bps
                ),

                creator_fee_bps=int(
                    creator_fee_bps
                ),

                mark=mark,
                exit_decision=None,
                close=None,
            )

        decision = evaluate_shadow_exit(
            entry_timestamp=int(
                mark.entry_timestamp
            ),

            entry_wallet_cost_lamports=int(
                mark.entry_wallet_cost_lamports
            ),

            cumulative_net_proceeds_lamports=int(
                mark.cumulative_net_proceeds_lamports
            ),

            liquidation_value_lamports=int(
                mark.mark_value_lamports
            ),

            mark_status=(
                mark.status
            ),

            evaluated_at=int(
                observed_at
            ),
        )

        if not decision.should_exit:
            return ShadowPositionEventResult(
                manager_version=(
                    SHADOW_POSITION_MANAGER_VERSION
                ),

                status=(
                    decision.status
                ),

                reason=(
                    decision.reason
                ),

                mint=mint,

                fee_status=(
                    fee_status
                ),

                protocol_fee_bps=int(
                    protocol_fee_bps
                ),

                creator_fee_bps=int(
                    creator_fee_bps
                ),

                mark=mark,

                exit_decision=(
                    decision
                ),

                close=None,
            )

        if decision.reason is None:
            return ShadowPositionEventResult(
                manager_version=(
                    SHADOW_POSITION_MANAGER_VERSION
                ),

                status="UNKNOWN",
                reason=(
                    "EXIT_REASON_UNAVAILABLE"
                ),

                mint=mint,

                fee_status=(
                    fee_status
                ),

                protocol_fee_bps=int(
                    protocol_fee_bps
                ),

                creator_fee_bps=int(
                    creator_fee_bps
                ),

                mark=mark,

                exit_decision=(
                    decision
                ),

                close=None,
            )

        close = close_shadow_position(
            mint=mint,

            exit_reason=(
                decision.reason
            ),

            curve_state=(
                curve_state
            ),

            protocol_fee_bps=int(
                protocol_fee_bps
            ),

            creator_fee_bps=int(
                creator_fee_bps
            ),

            slippage_bps=(
                SHADOW_EXIT_SLIPPAGE_BPS
            ),

            base_network_fee_lamports=(
                SHADOW_EXIT_BASE_NETWORK_FEE_LAMPORTS
            ),

            priority_fee_lamports=(
                SHADOW_EXIT_PRIORITY_FEE_LAMPORTS
            ),

            exit_timestamp=int(
                observed_at
            ),

            db_path=db_path,
        )

        if close.status == "CLOSED":
            unregister_open_shadow_mint(
                mint,
                db_path=db_path,
            )

            return ShadowPositionEventResult(
                manager_version=(
                    SHADOW_POSITION_MANAGER_VERSION
                ),

                status="CLOSED",
                reason=(
                    decision.reason
                ),

                mint=mint,

                fee_status=(
                    fee_status
                ),

                protocol_fee_bps=int(
                    protocol_fee_bps
                ),

                creator_fee_bps=int(
                    creator_fee_bps
                ),

                mark=mark,

                exit_decision=(
                    decision
                ),

                close=close,
            )

        if close.status == "NO_POSITION":
            unregister_open_shadow_mint(
                mint,
                db_path=db_path,
            )

        return ShadowPositionEventResult(
            manager_version=(
                SHADOW_POSITION_MANAGER_VERSION
            ),

            status="UNKNOWN",
            reason=(
                "POSITION_CLOSE_FAILED:"
                f"{close.status}"
            ),

            mint=mint,

            fee_status=(
                fee_status
            ),

            protocol_fee_bps=int(
                protocol_fee_bps
            ),

            creator_fee_bps=int(
                creator_fee_bps
            ),

            mark=mark,

            exit_decision=(
                decision
            ),

            close=close,
        )