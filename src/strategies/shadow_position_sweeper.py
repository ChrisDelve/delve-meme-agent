from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from src.execution.live_pump_fee_state import (
    resolve_live_pump_fee_state,
)

from src.execution.pump_execution_simulator import (
    PumpCurveState,
)

from src.portfolio.shadow_portfolio import (
    DB_PATH,
    close_shadow_position,
    get_shadow_exit_intent,
    list_open_shadow_mints,
    mark_open_position,
    partial_close_shadow_position,
    set_shadow_exit_intent,
    write_off_shadow_position,
)
from src.strategies.shadow_exit_engine import (
    ShadowExitDecision,
    evaluate_shadow_exit,
)
from src.strategies.shadow_exit_recovery import (
    plan_shadow_exit_recovery,
)

from src.strategies.shadow_position_runtime import (
    SHADOW_EXIT_BASE_NETWORK_FEE_LAMPORTS,
    SHADOW_EXIT_PRIORITY_FEE_LAMPORTS,
    SHADOW_EXIT_SLIPPAGE_BPS,
    get_shadow_position_lock,
    initialize_shadow_position_manager,
    unregister_open_shadow_mint,
)


SHADOW_SWEEPER_VERSION = (
    "shadow-position-sweeper-v2"
)

SHADOW_SWEEP_INTERVAL_SECONDS = 15


@dataclass(frozen=True)
class ShadowSweepResult:
    sweeper_version: str

    mint: str

    status: str
    reason: str | None

    protocol_fee_bps: int | None
    creator_fee_bps: int | None

    mark_value_lamports: int | None

    exit_decision: (
        ShadowExitDecision | None
    )

    realized_pnl_lamports: int | None


async def sweep_shadow_position(
    *,
    mint: str,

    db_path: Path = DB_PATH,

    evaluated_at: int | None = None,
) -> ShadowSweepResult:

    if evaluated_at is None:
        evaluated_at = int(
            time.time()
        )

    lock = get_shadow_position_lock(
        mint
    )

    async with lock:
        try:
            fee_state = (
                await resolve_live_pump_fee_state(
                    mint=mint
                )
            )

        except Exception as error:
            return ShadowSweepResult(
                sweeper_version=(
                    SHADOW_SWEEPER_VERSION
                ),

                mint=mint,

                status="UNKNOWN",

                reason=(
                    "LIVE_FEE_STATE_FAILED:"
                    f"{type(error).__name__}"
                ),

                protocol_fee_bps=None,
                creator_fee_bps=None,

                mark_value_lamports=None,

                exit_decision=None,

                realized_pnl_lamports=None,
            )

        #
        # Our exact Pump sell simulator models
        # the bonding-curve route.
        #
        # Once the curve graduates, we must not
        # fabricate a Pump sell. A PumpSwap /
        # post-graduation route will be a
        # separate execution subsystem.
        #
        if fee_state.curve.complete:
            return ShadowSweepResult(
                sweeper_version=(
                    SHADOW_SWEEPER_VERSION
                ),

                mint=mint,

                status="UNKNOWN",

                reason=(
                    "GRADUATED_CURVE_EXIT_ROUTE_UNSUPPORTED"
                ),

                protocol_fee_bps=int(
                    fee_state.protocol_fee_bps
                ),

                creator_fee_bps=int(
                    fee_state.creator_fee_bps
                ),

                mark_value_lamports=None,

                exit_decision=None,

                realized_pnl_lamports=None,
            )

        curve_state = PumpCurveState(
            virtual_quote_reserves=int(
                fee_state.curve
                .virtual_quote_reserves
            ),

            virtual_token_reserves=int(
                fee_state.curve
                .virtual_token_reserves
            ),

            real_quote_reserves=int(
                fee_state.curve
                .real_quote_reserves
            ),

            real_token_reserves=int(
                fee_state.curve
                .real_token_reserves
            ),
        )

        mark = mark_open_position(
            mint=mint,

            curve_state=curve_state,

            protocol_fee_bps=int(
                fee_state.protocol_fee_bps
            ),

            creator_fee_bps=int(
                fee_state.creator_fee_bps
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
                evaluated_at
            ),

            db_path=db_path,
        )

        if mark.status == "NO_POSITION":
            unregister_open_shadow_mint(
                mint,
                db_path=db_path,
            )

            return ShadowSweepResult(
                sweeper_version=(
                    SHADOW_SWEEPER_VERSION
                ),

                mint=mint,

                status="NO_POSITION",
                reason=None,

                protocol_fee_bps=int(
                    fee_state.protocol_fee_bps
                ),

                creator_fee_bps=int(
                    fee_state.creator_fee_bps
                ),

                mark_value_lamports=None,

                exit_decision=None,

                realized_pnl_lamports=None,
            )


        if mark.status not in (
            "MARKED",
            "UNEXITABLE",
        ):
            return ShadowSweepResult(
                sweeper_version=(
                    SHADOW_SWEEPER_VERSION
                ),

                mint=mint,

                status="UNKNOWN",

                reason=(
                    "LIQUIDATION_MARK_FAILED:"
                    f"{mark.status}"
                ),

                protocol_fee_bps=int(
                    fee_state.protocol_fee_bps
                ),

                creator_fee_bps=int(
                    fee_state.creator_fee_bps
                ),

                mark_value_lamports=int(
                    mark.mark_value_lamports
                ),

                exit_decision=None,

                realized_pnl_lamports=None,
            )

        intent = get_shadow_exit_intent(
            mint=mint,
            db_path=db_path,
        )

        if intent.status == "NO_POSITION":
            unregister_open_shadow_mint(
                mint,
                db_path=db_path,
            )

            return ShadowSweepResult(
                sweeper_version=(
                    SHADOW_SWEEPER_VERSION
                ),

                mint=mint,

                status="NO_POSITION",
                reason=None,

                protocol_fee_bps=int(
                    fee_state.protocol_fee_bps
                ),

                creator_fee_bps=int(
                    fee_state.creator_fee_bps
                ),

                mark_value_lamports=None,
                exit_decision=None,
                realized_pnl_lamports=None,
            )

        if intent.status == "UNKNOWN":
            return ShadowSweepResult(
                sweeper_version=(
                    SHADOW_SWEEPER_VERSION
                ),

                mint=mint,

                status="UNKNOWN",

                reason=(
                    "EXIT_INTENT_FAILED:"
                    + (
                        intent.reasons[0]
                        if intent.reasons
                        else "UNKNOWN"
                    )
                ),

                protocol_fee_bps=int(
                    fee_state.protocol_fee_bps
                ),

                creator_fee_bps=int(
                    fee_state.creator_fee_bps
                ),

                mark_value_lamports=int(
                    mark.mark_value_lamports
                ),

                exit_decision=None,
                realized_pnl_lamports=None,
            )

        decision: (
            ShadowExitDecision | None
        ) = None

        if intent.status == "EXISTING":
            if (
                intent.exit_pending_reason is None
                or intent.exit_pending_since is None
            ):
                return ShadowSweepResult(
                    sweeper_version=(
                        SHADOW_SWEEPER_VERSION
                    ),

                    mint=mint,

                    status="UNKNOWN",
                    reason=(
                        "EXIT_INTENT_STATE_INCOMPLETE"
                    ),

                    protocol_fee_bps=int(
                        fee_state.protocol_fee_bps
                    ),

                    creator_fee_bps=int(
                        fee_state.creator_fee_bps
                    ),

                    mark_value_lamports=int(
                        mark.mark_value_lamports
                    ),

                    exit_decision=None,
                    realized_pnl_lamports=None,
                )

            #
            # A prior exit decision has already
            # transitioned this position into
            # liquidation mode.
            #
            # Never re-run HOLD / TP / SL policy
            # for the residual position.
            #
            exit_reason = str(
                intent.exit_pending_reason
            )

        elif intent.status == "NONE":
            if (
                mark.entry_timestamp is None
                or (
                    mark.entry_wallet_cost_lamports
                    is None
                )
            ):
                return ShadowSweepResult(
                    sweeper_version=(
                        SHADOW_SWEEPER_VERSION
                    ),

                    mint=mint,

                    status="UNKNOWN",

                    reason=(
                        "ENTRY_ECONOMICS_UNAVAILABLE"
                    ),

                    protocol_fee_bps=int(
                        fee_state.protocol_fee_bps
                    ),

                    creator_fee_bps=int(
                        fee_state.creator_fee_bps
                    ),

                    mark_value_lamports=int(
                        mark.mark_value_lamports
                    ),

                    exit_decision=None,
                    realized_pnl_lamports=None,
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
                    evaluated_at
                ),
            )

            if not decision.should_exit:
                return ShadowSweepResult(
                    sweeper_version=(
                        SHADOW_SWEEPER_VERSION
                    ),

                    mint=mint,

                    status=(
                        decision.status
                    ),

                    reason=(
                        decision.reason
                    ),

                    protocol_fee_bps=int(
                        fee_state.protocol_fee_bps
                    ),

                    creator_fee_bps=int(
                        fee_state.creator_fee_bps
                    ),

                    mark_value_lamports=int(
                        mark.mark_value_lamports
                    ),

                    exit_decision=(
                        decision
                    ),

                    realized_pnl_lamports=None,
                )

            if decision.reason is None:
                return ShadowSweepResult(
                    sweeper_version=(
                        SHADOW_SWEEPER_VERSION
                    ),

                    mint=mint,

                    status="UNKNOWN",

                    reason=(
                        "EXIT_REASON_UNAVAILABLE"
                    ),

                    protocol_fee_bps=int(
                        fee_state.protocol_fee_bps
                    ),

                    creator_fee_bps=int(
                        fee_state.creator_fee_bps
                    ),

                    mark_value_lamports=int(
                        mark.mark_value_lamports
                    ),

                    exit_decision=(
                        decision
                    ),

                    realized_pnl_lamports=None,
                )

            persisted = set_shadow_exit_intent(
                mint=mint,

                exit_reason=(
                    decision.reason
                ),

                exit_timestamp=int(
                    evaluated_at
                ),

                db_path=db_path,
            )

            if persisted.status == "NO_POSITION":
                unregister_open_shadow_mint(
                    mint,
                    db_path=db_path,
                )

                return ShadowSweepResult(
                    sweeper_version=(
                        SHADOW_SWEEPER_VERSION
                    ),

                    mint=mint,

                    status="NO_POSITION",
                    reason=None,

                    protocol_fee_bps=int(
                        fee_state.protocol_fee_bps
                    ),

                    creator_fee_bps=int(
                        fee_state.creator_fee_bps
                    ),

                    mark_value_lamports=None,

                    exit_decision=(
                        decision
                    ),

                    realized_pnl_lamports=None,
                )

            if persisted.status not in (
                "SET",
                "EXISTING",
            ):
                return ShadowSweepResult(
                    sweeper_version=(
                        SHADOW_SWEEPER_VERSION
                    ),

                    mint=mint,

                    status="UNKNOWN",

                    reason=(
                        "EXIT_INTENT_PERSIST_FAILED:"
                        f"{persisted.status}"
                    ),

                    protocol_fee_bps=int(
                        fee_state.protocol_fee_bps
                    ),

                    creator_fee_bps=int(
                        fee_state.creator_fee_bps
                    ),

                    mark_value_lamports=int(
                        mark.mark_value_lamports
                    ),

                    exit_decision=(
                        decision
                    ),

                    realized_pnl_lamports=None,
                )

            if (
                persisted.exit_pending_reason
                is None
            ):
                return ShadowSweepResult(
                    sweeper_version=(
                        SHADOW_SWEEPER_VERSION
                    ),

                    mint=mint,

                    status="UNKNOWN",

                    reason=(
                        "PERSISTED_EXIT_REASON_UNAVAILABLE"
                    ),

                    protocol_fee_bps=int(
                        fee_state.protocol_fee_bps
                    ),

                    creator_fee_bps=int(
                        fee_state.creator_fee_bps
                    ),

                    mark_value_lamports=int(
                        mark.mark_value_lamports
                    ),

                    exit_decision=(
                        decision
                    ),

                    realized_pnl_lamports=None,
                )

            #
            # First persisted intent wins even if
            # another writer had already stored it.
            #
            exit_reason = str(
                persisted.exit_pending_reason
            )

        else:
            return ShadowSweepResult(
                sweeper_version=(
                    SHADOW_SWEEPER_VERSION
                ),

                mint=mint,

                status="UNKNOWN",

                reason=(
                    "UNEXPECTED_EXIT_INTENT_STATUS:"
                    f"{intent.status}"
                ),

                protocol_fee_bps=int(
                    fee_state.protocol_fee_bps
                ),

                creator_fee_bps=int(
                    fee_state.creator_fee_bps
                ),

                mark_value_lamports=int(
                    mark.mark_value_lamports
                ),

                exit_decision=None,
                realized_pnl_lamports=None,
            )

        if int(mark.tokens_held) <= 0:
            return ShadowSweepResult(
                sweeper_version=(
                    SHADOW_SWEEPER_VERSION
                ),

                mint=mint,

                status="UNKNOWN",
                reason="OPEN_POSITION_HAS_NO_TOKENS",

                protocol_fee_bps=int(
                    fee_state.protocol_fee_bps
                ),

                creator_fee_bps=int(
                    fee_state.creator_fee_bps
                ),

                mark_value_lamports=int(
                    mark.mark_value_lamports
                ),

                exit_decision=(
                    decision
                ),

                realized_pnl_lamports=None,
            )

        try:
            recovery = (
                plan_shadow_exit_recovery(
                    tokens_held=int(
                        mark.tokens_held
                    ),

                    curve_state=curve_state,

                    protocol_fee_bps=int(
                        fee_state.protocol_fee_bps
                    ),

                    creator_fee_bps=int(
                        fee_state.creator_fee_bps
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
                )
            )

        except Exception as error:
            return ShadowSweepResult(
                sweeper_version=(
                    SHADOW_SWEEPER_VERSION
                ),

                mint=mint,

                status="UNKNOWN",

                reason=(
                    "EXIT_RECOVERY_PLAN_FAILED:"
                    f"{type(error).__name__}"
                ),

                protocol_fee_bps=int(
                    fee_state.protocol_fee_bps
                ),

                creator_fee_bps=int(
                    fee_state.creator_fee_bps
                ),

                mark_value_lamports=int(
                    mark.mark_value_lamports
                ),

                exit_decision=(
                    decision
                ),

                realized_pnl_lamports=None,
            )

        if recovery.action == "FINAL":
            close = close_shadow_position(
                mint=mint,
                exit_reason=exit_reason,

                curve_state=curve_state,

                protocol_fee_bps=int(
                    fee_state.protocol_fee_bps
                ),

                creator_fee_bps=int(
                    fee_state.creator_fee_bps
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
                    evaluated_at
                ),

                db_path=db_path,
            )

            if close.status == "CLOSED":
                unregister_open_shadow_mint(
                    mint,
                    db_path=db_path,
                )

                return ShadowSweepResult(
                    sweeper_version=(
                        SHADOW_SWEEPER_VERSION
                    ),

                    mint=mint,

                    status="CLOSED",
                    reason=exit_reason,

                    protocol_fee_bps=int(
                        fee_state.protocol_fee_bps
                    ),

                    creator_fee_bps=int(
                        fee_state.creator_fee_bps
                    ),

                    mark_value_lamports=int(
                        mark.mark_value_lamports
                    ),

                    exit_decision=(
                        decision
                    ),

                    realized_pnl_lamports=int(
                        close.realized_pnl_lamports
                    ),
                )

            if close.status == "NO_POSITION":
                unregister_open_shadow_mint(
                    mint,
                    db_path=db_path,
                )

                return ShadowSweepResult(
                    sweeper_version=(
                        SHADOW_SWEEPER_VERSION
                    ),

                    mint=mint,

                    status="NO_POSITION",
                    reason=None,

                    protocol_fee_bps=int(
                        fee_state.protocol_fee_bps
                    ),

                    creator_fee_bps=int(
                        fee_state.creator_fee_bps
                    ),

                    mark_value_lamports=None,

                    exit_decision=(
                        decision
                    ),

                    realized_pnl_lamports=None,
                )

            return ShadowSweepResult(
                sweeper_version=(
                    SHADOW_SWEEPER_VERSION
                ),

                mint=mint,

                status="UNKNOWN",

                reason=(
                    "POSITION_CLOSE_FAILED:"
                    f"{close.status}"
                ),

                protocol_fee_bps=int(
                    fee_state.protocol_fee_bps
                ),

                creator_fee_bps=int(
                    fee_state.creator_fee_bps
                ),

                mark_value_lamports=int(
                    mark.mark_value_lamports
                ),

                exit_decision=(
                    decision
                ),

                realized_pnl_lamports=None,
            )

        if recovery.action == "PARTIAL":
            partial = partial_close_shadow_position(
                mint=mint,
                exit_reason=exit_reason,

                tokens_to_sell=int(
                    recovery.tokens_to_sell
                ),

                curve_state=curve_state,

                protocol_fee_bps=int(
                    fee_state.protocol_fee_bps
                ),

                creator_fee_bps=int(
                    fee_state.creator_fee_bps
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
                    evaluated_at
                ),

                db_path=db_path,
            )

            if partial.status == "PARTIAL":
                residual_mark = 0

                if (
                    partial.residual_mark_simulation
                    is not None
                    and (
                        partial
                        .residual_mark_simulation
                        .executable
                    )
                ):
                    residual_mark = int(
                        partial
                        .residual_mark_simulation
                        .net_wallet_proceeds_lamports
                    )

                return ShadowSweepResult(
                    sweeper_version=(
                        SHADOW_SWEEPER_VERSION
                    ),

                    mint=mint,

                    status="PARTIAL",
                    reason=exit_reason,

                    protocol_fee_bps=int(
                        fee_state.protocol_fee_bps
                    ),

                    creator_fee_bps=int(
                        fee_state.creator_fee_bps
                    ),

                    mark_value_lamports=(
                        residual_mark
                    ),

                    exit_decision=(
                        decision
                    ),

                    realized_pnl_lamports=int(
                        partial
                        .cumulative_realized_pnl_lamports
                    ),
                )

            if partial.status == "NO_POSITION":
                unregister_open_shadow_mint(
                    mint,
                    db_path=db_path,
                )

                return ShadowSweepResult(
                    sweeper_version=(
                        SHADOW_SWEEPER_VERSION
                    ),

                    mint=mint,

                    status="NO_POSITION",
                    reason=None,

                    protocol_fee_bps=int(
                        fee_state.protocol_fee_bps
                    ),

                    creator_fee_bps=int(
                        fee_state.creator_fee_bps
                    ),

                    mark_value_lamports=None,

                    exit_decision=(
                        decision
                    ),

                    realized_pnl_lamports=None,
                )

            return ShadowSweepResult(
                sweeper_version=(
                    SHADOW_SWEEPER_VERSION
                ),

                mint=mint,

                status="UNKNOWN",

                reason=(
                    "PARTIAL_EXIT_FAILED:"
                    f"{partial.status}"
                ),

                protocol_fee_bps=int(
                    fee_state.protocol_fee_bps
                ),

                creator_fee_bps=int(
                    fee_state.creator_fee_bps
                ),

                mark_value_lamports=int(
                    mark.mark_value_lamports
                ),

                exit_decision=(
                    decision
                ),

                realized_pnl_lamports=None,
            )

        if (
            recovery.action
            == "WRITE_OFF_CANDIDATE"
        ):
            write_off = (
                write_off_shadow_position(
                    mint=mint,

                    #
                    # Preserve the original policy
                    # reason. exit_kind=WRITE_OFF
                    # records the terminal mechanic.
                    #
                    exit_reason=exit_reason,

                    exit_timestamp=int(
                        evaluated_at
                    ),

                    db_path=db_path,
                )
            )

            if write_off.status in (
                "CLOSED",
                "NO_POSITION",
            ):
                unregister_open_shadow_mint(
                    mint,
                    db_path=db_path,
                )

            if write_off.status == "CLOSED":
                return ShadowSweepResult(
                    sweeper_version=(
                        SHADOW_SWEEPER_VERSION
                    ),

                    mint=mint,

                    status="CLOSED",
                    reason=exit_reason,

                    protocol_fee_bps=int(
                        fee_state.protocol_fee_bps
                    ),

                    creator_fee_bps=int(
                        fee_state.creator_fee_bps
                    ),

                    mark_value_lamports=0,

                    exit_decision=(
                        decision
                    ),

                    realized_pnl_lamports=int(
                        write_off
                        .realized_pnl_lamports
                    ),
                )

            if write_off.status == "NO_POSITION":
                return ShadowSweepResult(
                    sweeper_version=(
                        SHADOW_SWEEPER_VERSION
                    ),

                    mint=mint,

                    status="NO_POSITION",
                    reason=None,

                    protocol_fee_bps=int(
                        fee_state.protocol_fee_bps
                    ),

                    creator_fee_bps=int(
                        fee_state.creator_fee_bps
                    ),

                    mark_value_lamports=None,

                    exit_decision=(
                        decision
                    ),

                    realized_pnl_lamports=None,
                )

            return ShadowSweepResult(
                sweeper_version=(
                    SHADOW_SWEEPER_VERSION
                ),

                mint=mint,

                status="UNKNOWN",

                reason=(
                    "POSITION_WRITE_OFF_FAILED:"
                    f"{write_off.status}"
                ),

                protocol_fee_bps=int(
                    fee_state.protocol_fee_bps
                ),

                creator_fee_bps=int(
                    fee_state.creator_fee_bps
                ),

                mark_value_lamports=0,

                exit_decision=(
                    decision
                ),

                realized_pnl_lamports=None,
            )

        return ShadowSweepResult(
            sweeper_version=(
                SHADOW_SWEEPER_VERSION
            ),

            mint=mint,

            status="UNKNOWN",

            reason=(
                "EXIT_RECOVERY_UNAVAILABLE:"
                f"{recovery.reason}"
            ),

            protocol_fee_bps=int(
                fee_state.protocol_fee_bps
            ),

            creator_fee_bps=int(
                fee_state.creator_fee_bps
            ),

            mark_value_lamports=int(
                mark.mark_value_lamports
            ),

            exit_decision=(
                decision
            ),

            realized_pnl_lamports=None,
        )


async def sweep_open_shadow_positions(
    *,
    db_path: Path = DB_PATH,

    evaluated_at: int | None = None,
) -> tuple[
    ShadowSweepResult,
    ...
]:

    mints = sorted(
        list_open_shadow_mints(
            db_path=db_path
        )
    )

    results: list[
        ShadowSweepResult
    ] = []

    for mint in mints:
        result = (
            await sweep_shadow_position(
                mint=mint,

                db_path=db_path,

                evaluated_at=(
                    evaluated_at
                ),
            )
        )

        results.append(
            result
        )

    return tuple(
        results
    )


async def run_shadow_position_sweeper(
    *,
    db_path: Path = DB_PATH,

    interval_seconds: int = (
        SHADOW_SWEEP_INTERVAL_SECONDS
    ),
) -> None:

    if interval_seconds <= 0:
        raise ValueError(
            "interval_seconds must be positive."
        )

    initialize_shadow_position_manager(
        db_path=db_path
    )

    while True:
        started_at = (
            time.monotonic()
        )

        try:
            results = (
                await sweep_open_shadow_positions(
                    db_path=db_path
                )
            )

            for result in results:
                if result.status == "CLOSED":
                    print(
                        "⏱️ SHADOW SWEEP CLOSED | "
                        f"mint={result.mint} | "
                        f"reason={result.reason}"
                    )

                elif result.status == "UNKNOWN":
                    print(
                        "⚠️ SHADOW SWEEP UNKNOWN | "
                        f"mint={result.mint} | "
                        f"reason={result.reason}"
                    )

        except asyncio.CancelledError:
            raise

        except Exception as error:
            print(
                "⚠️ SHADOW SWEEPER ERROR | "
                f"{type(error).__name__}: "
                f"{error}"
            )

        elapsed = (
            time.monotonic()
            - started_at
        )

        delay = max(
            0.1,
            float(
                interval_seconds
            )
            - elapsed,
        )

        await asyncio.sleep(
            delay
        )