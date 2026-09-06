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
    list_open_shadow_mints,
    mark_open_position,
)

from src.strategies.shadow_exit_engine import (
    ShadowExitDecision,
    evaluate_shadow_exit,
)


SHADOW_POSITION_MANAGER_VERSION = (
    "shadow-position-manager-v1"
)

#
# Provisional shadow exit execution assumptions.
#
SHADOW_EXIT_SLIPPAGE_BPS = 300
SHADOW_EXIT_BASE_NETWORK_FEE_LAMPORTS = 5_000
SHADOW_EXIT_PRIORITY_FEE_LAMPORTS = 0


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


_open_mints: set[str] | None = None
_registry_db_path: Path | None = None

_mint_locks: dict[
    str,
    asyncio.Lock,
] = {}


def initialize_shadow_position_manager(
    *,
    db_path: Path = DB_PATH,
) -> set[str]:
    global _open_mints
    global _registry_db_path

    normalized_path = Path(
        db_path
    )

    _open_mints = (
        list_open_shadow_mints(
            db_path=normalized_path
        )
    )

    _registry_db_path = (
        normalized_path
    )

    return set(
        _open_mints
    )


def _ensure_initialized(
    *,
    db_path: Path = DB_PATH,
) -> None:
    global _registry_db_path

    normalized_path = Path(
        db_path
    )

    if _open_mints is None:
        initialize_shadow_position_manager(
            db_path=normalized_path
        )
        return

    if (
        _registry_db_path
        != normalized_path
    ):
        raise RuntimeError(
            "Shadow position manager is "
            "already initialized for a "
            "different database path."
        )


def register_open_shadow_mint(
    mint: str,
    *,
    db_path: Path = DB_PATH,
) -> None:
    _ensure_initialized(
        db_path=db_path
    )

    assert _open_mints is not None

    _open_mints.add(
        mint
    )


def unregister_open_shadow_mint(
    mint: str,
    *,
    db_path: Path = DB_PATH,
) -> None:
    _ensure_initialized(
        db_path=db_path
    )

    assert _open_mints is not None

    _open_mints.discard(
        mint
    )


def is_open_shadow_mint_tracked(
    mint: str,
    *,
    db_path: Path = DB_PATH,
) -> bool:
    _ensure_initialized(
        db_path=db_path
    )

    assert _open_mints is not None

    return mint in _open_mints


def _get_mint_lock(
    mint: str,
) -> asyncio.Lock:

    lock = _mint_locks.get(
        mint
    )

    if lock is None:
        lock = asyncio.Lock()

        _mint_locks[
            mint
        ] = lock

    return lock


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

    _ensure_initialized(
        db_path=db_path
    )

    assert _open_mints is not None

    #
    # Fast path. Nearly every Pump event
    # should stop here without touching SQLite.
    #
    if mint not in _open_mints:
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

    lock = _get_mint_lock(
        mint
    )

    async with lock:
        #
        # Another queued event may already have
        # closed this position.
        #
        if mint not in _open_mints:
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