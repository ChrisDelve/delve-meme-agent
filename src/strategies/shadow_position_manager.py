from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from src.portfolio.shadow_portfolio import (
    DB_PATH,
)
from src.strategies.shadow_position_runtime import (
    initialize_shadow_position_manager,
    is_open_shadow_mint_tracked,
    register_open_shadow_mint,
)
from src.strategies.shadow_position_sweeper import (
    ShadowSweepResult,
    sweep_shadow_position,
)


SHADOW_POSITION_MANAGER_VERSION = (
    "shadow-position-manager-v2"
)

#
# Event activity is only a trigger for the
# authoritative position-management path.
#
# Event-provided reserves and fee fields must
# never price our hypothetical liquidation.
#
# At most one event-driven evaluation is active
# per mint. Additional events mark the mint dirty
# and return immediately.
#
# The active evaluator may perform one trailing
# authoritative evaluation after its first pass.
#
# This bounds a single event burst to two sweeps:
# initial + one trailing dirty pass.
#
_event_evaluations_in_flight: set[str] = set()
_event_dirty_mints: set[str] = set()


@dataclass(frozen=True)
class ShadowPositionEventResult:
    manager_version: str

    status: str
    reason: str | None

    mint: str

    sweep_result: (
        ShadowSweepResult | None
    )

    trailing_evaluation: bool


def _ignored_event_result(
    *,
    mint: str,
    reason: str,
) -> ShadowPositionEventResult:
    return ShadowPositionEventResult(
        manager_version=(
            SHADOW_POSITION_MANAGER_VERSION
        ),

        status="IGNORED",
        reason=reason,

        mint=mint,

        sweep_result=None,
        trailing_evaluation=False,
    )


def _coalesced_event_result(
    *,
    mint: str,
) -> ShadowPositionEventResult:
    return ShadowPositionEventResult(
        manager_version=(
            SHADOW_POSITION_MANAGER_VERSION
        ),

        status="COALESCED",
        reason=(
            "EVENT_EVALUATION_ALREADY_RUNNING"
        ),

        mint=mint,

        sweep_result=None,
        trailing_evaluation=False,
    )


def _event_result_from_sweep(
    *,
    result: ShadowSweepResult,
    trailing_evaluation: bool,
) -> ShadowPositionEventResult:
    return ShadowPositionEventResult(
        manager_version=(
            SHADOW_POSITION_MANAGER_VERSION
        ),

        status=result.status,
        reason=result.reason,

        mint=result.mint,

        sweep_result=result,

        trailing_evaluation=(
            trailing_evaluation
        ),
    )


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
    # Preserve the collector-facing event contract,
    # but deliberately do not use event economics
    # as execution authority.
    #
    _ = (
        event_user,
        quote_amount,
        protocol_fee_lamports,
        creator_fee_lamports,
        event_protocol_fee_bps,
        event_creator_fee_bps,
        virtual_quote_reserves,
        virtual_token_reserves,
        real_quote_reserves,
        real_token_reserves,
    )

    if not is_open_shadow_mint_tracked(
        mint,
        db_path=db_path,
    ):
        return _ignored_event_result(
            mint=mint,
            reason="NO_TRACKED_POSITION",
        )

    #
    # No await occurs between checking and adding
    # this flag. Within the collector event loop,
    # this is the coalescing ownership boundary.
    #
    if mint in _event_evaluations_in_flight:
        _event_dirty_mints.add(
            mint
        )

        return _coalesced_event_result(
            mint=mint
        )

    _event_evaluations_in_flight.add(
        mint
    )

    #
    # A stale dirty marker must never force an
    # unnecessary trailing evaluation for a new
    # burst.
    #
    _event_dirty_mints.discard(
        mint
    )

    if observed_at is None:
        observed_at = int(
            time.time()
        )

    trailing_evaluation = False

    try:
        #
        # sweep_shadow_position owns the shared
        # per-mint position lock. The manager must
        # not acquire that lock itself.
        #
        result = await sweep_shadow_position(
            mint=mint,
            db_path=db_path,
            evaluated_at=int(
                observed_at
            ),
        )

        #
        # If market activity arrived while the
        # first authoritative evaluation was in
        # progress, perform exactly one trailing
        # pass if the position remains tracked.
        #
        if (
            mint in _event_dirty_mints
            and result.status
            not in (
                "CLOSED",
                "NO_POSITION",
            )
            and is_open_shadow_mint_tracked(
                mint,
                db_path=db_path,
            )
        ):
            _event_dirty_mints.discard(
                mint
            )

            trailing_evaluation = True

            result = await sweep_shadow_position(
                mint=mint,
                db_path=db_path,
                evaluated_at=int(
                    time.time()
                ),
            )

        return _event_result_from_sweep(
            result=result,
            trailing_evaluation=(
                trailing_evaluation
            ),
        )

    finally:
        #
        # Events arriving during the trailing pass
        # may mark the mint dirty again. We
        # intentionally clear that marker here:
        # this invocation is capped at two
        # authoritative evaluations.
        #
        # A later event can start a fresh burst,
        # and the 15-second periodic sweeper
        # remains the independent fallback.
        #
        _event_evaluations_in_flight.discard(
            mint
        )

        _event_dirty_mints.discard(
            mint
        )
