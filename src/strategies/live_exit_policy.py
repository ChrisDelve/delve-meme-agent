from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.execution.live_pump_liquidation_value import (
    LIVE_PUMP_LIQUIDATION_VALUE_VERSION,
    RESOLVED as LIQUIDATION_RESOLVED,
)
from src.portfolio.live_wallet_valuation import (
    LiveMintInventoryValuation,
)


LIVE_EXIT_POLICY_VERSION = (
    "live-exit-policy-v1"
)

HOLD = "HOLD"
FULL_EXIT = "FULL_EXIT"
UNKNOWN = "UNKNOWN"

U64_MAX = (1 << 64) - 1
I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1


@dataclass(frozen=True)
class LiveExitPolicy:
    """
    Explicit real-capital exit thresholds.

    There are deliberately no live defaults. A runtime
    must explicitly choose every enabled threshold.

    max_hold_seconds=None disables time-based exits.
    """

    take_profit_return_bps: int
    stop_loss_return_bps: int
    max_hold_seconds: int | None


@dataclass(frozen=True)
class LiveExitDecision:
    evaluator_version: str

    status: str
    reason: str | None

    mint: str

    tokens_held: int
    tokens_to_sell: int

    evaluated_at: int
    age_seconds: int | None

    total_entry_wallet_cost_lamports: int
    cumulative_net_proceeds_lamports: int
    liquidation_value_lamports: int

    return_bps: float | None

    policy: LiveExitPolicy | None

    @property
    def should_exit(self) -> bool:
        return self.status == FULL_EXIT


def _strict_u64(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= U64_MAX
    )


def _strict_positive_u64(
    value: Any,
) -> bool:
    return (
        _strict_u64(value)
        and value > 0
    )


def _strict_i64(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and I64_MIN <= value <= I64_MAX
    )


def _policy_error(
    policy: Any,
) -> str | None:
    if not isinstance(
        policy,
        LiveExitPolicy,
    ):
        return "INVALID_LIVE_EXIT_POLICY"

    if (
        not isinstance(
            policy.take_profit_return_bps,
            int,
        )
        or isinstance(
            policy.take_profit_return_bps,
            bool,
        )
        or policy.take_profit_return_bps
        <= 0
    ):
        return "INVALID_TAKE_PROFIT_RETURN_BPS"

    if (
        not isinstance(
            policy.stop_loss_return_bps,
            int,
        )
        or isinstance(
            policy.stop_loss_return_bps,
            bool,
        )
        or policy.stop_loss_return_bps
        >= 0
        or policy.stop_loss_return_bps
        <= -10_000
    ):
        return "INVALID_STOP_LOSS_RETURN_BPS"

    if policy.max_hold_seconds is not None:
        if not _strict_positive_u64(
            policy.max_hold_seconds
        ):
            return "INVALID_MAX_HOLD_SECONDS"

    return None


def evaluate_live_exit(
    *,
    valuation: LiveMintInventoryValuation,
    evaluated_at: int,
    policy: LiveExitPolicy,
) -> LiveExitDecision:
    """
    Pure real-capital exit-policy evaluation.

    This function performs no:
    - database read or mutation;
    - RPC;
    - SELL authorization;
    - inventory claim;
    - transaction construction;
    - signing;
    - submission;
    - reconciliation;
    - retry.

    v1 policy emits only:
        HOLD
        FULL_EXIT
        UNKNOWN

    FULL_EXIT always means the complete current aggregate
    inventory for this mint.

    Time semantics:
    max-hold age is measured from newest_entry_block_time.
    Therefore every currently-open lot must have aged at
    least max_hold_seconds before MAX_HOLD_TIME can trigger.

    A full executable liquidation mark is required before
    any HOLD or FULL_EXIT decision. Partial liquidity is not
    converted into strategy-level partial profit taking.
    """

    normalized_mint = ""

    tokens_held = 0
    entry_cost = 0
    cumulative_proceeds = 0
    liquidation_value = 0

    age_seconds: int | None = None
    return_bps: float | None = None

    valid_policy = (
        policy
        if isinstance(
            policy,
            LiveExitPolicy,
        )
        else None
    )

    def finish(
        status: str,
        reason: str | None,
        *,
        tokens_to_sell: int = 0,
    ) -> LiveExitDecision:
        return LiveExitDecision(
            evaluator_version=(
                LIVE_EXIT_POLICY_VERSION
            ),
            status=status,
            reason=reason,
            mint=normalized_mint,
            tokens_held=tokens_held,
            tokens_to_sell=(
                tokens_to_sell
            ),
            evaluated_at=(
                evaluated_at
                if _strict_u64(
                    evaluated_at
                )
                else 0
            ),
            age_seconds=age_seconds,
            total_entry_wallet_cost_lamports=(
                entry_cost
            ),
            cumulative_net_proceeds_lamports=(
                cumulative_proceeds
            ),
            liquidation_value_lamports=(
                liquidation_value
            ),
            return_bps=return_bps,
            policy=valid_policy,
        )

    policy_error = _policy_error(
        policy
    )

    if policy_error is not None:
        return finish(
            UNKNOWN,
            policy_error,
        )

    if not _strict_positive_u64(
        evaluated_at
    ):
        return finish(
            UNKNOWN,
            "INVALID_EVALUATED_AT",
        )

    if not isinstance(
        valuation,
        LiveMintInventoryValuation,
    ):
        return finish(
            UNKNOWN,
            "INVALID_MINT_VALUATION",
        )

    normalized_mint = (
        valuation.mint.strip()
        if isinstance(
            valuation.mint,
            str,
        )
        else ""
    )

    if not normalized_mint:
        return finish(
            UNKNOWN,
            "INVALID_MINT",
        )

    tokens_held = (
        valuation.tokens_held
        if _strict_u64(
            valuation.tokens_held
        )
        else 0
    )

    entry_cost = (
        valuation
        .total_entry_wallet_cost_lamports
        if _strict_u64(
            valuation
            .total_entry_wallet_cost_lamports
        )
        else 0
    )

    cumulative_proceeds = (
        valuation
        .cumulative_net_proceeds_lamports
        if _strict_u64(
            valuation
            .cumulative_net_proceeds_lamports
        )
        else 0
    )

    liquidation_value = (
        valuation
        .liquidation_value_lamports
        if _strict_u64(
            valuation
            .liquidation_value_lamports
        )
        else 0
    )

    if not _strict_positive_u64(
        valuation.open_lots
    ):
        return finish(
            UNKNOWN,
            "INVALID_OPEN_LOTS",
        )

    if not _strict_positive_u64(
        valuation.tokens_held
    ):
        return finish(
            UNKNOWN,
            "INVALID_TOKENS_HELD",
        )

    if not _strict_positive_u64(
        valuation
        .total_entry_wallet_cost_lamports
    ):
        return finish(
            UNKNOWN,
            "INVALID_TOTAL_ENTRY_WALLET_COST",
        )

    if not _strict_u64(
        valuation
        .remaining_cost_basis_lamports
    ):
        return finish(
            UNKNOWN,
            "INVALID_REMAINING_COST_BASIS",
        )

    if not _strict_u64(
        valuation
        .cumulative_net_proceeds_lamports
    ):
        return finish(
            UNKNOWN,
            "INVALID_CUMULATIVE_NET_PROCEEDS",
        )

    if not _strict_i64(
        valuation
        .cumulative_realized_pnl_lamports
    ):
        return finish(
            UNKNOWN,
            "INVALID_CUMULATIVE_REALIZED_PNL",
        )

    if (
        not _strict_u64(
            valuation.oldest_entry_slot
        )
        or not _strict_u64(
            valuation.newest_entry_slot
        )
        or valuation.oldest_entry_slot
        > valuation.newest_entry_slot
    ):
        return finish(
            UNKNOWN,
            "INVALID_ENTRY_SLOT_BOUNDS",
        )

    oldest_time = (
        valuation.oldest_entry_block_time
    )
    newest_time = (
        valuation.newest_entry_block_time
    )

    if (
        oldest_time is None
    ) != (
        newest_time is None
    ):
        return finish(
            UNKNOWN,
            "INCOMPLETE_ENTRY_TIME_BOUNDS",
        )

    if oldest_time is not None:
        if (
            not _strict_positive_u64(
                oldest_time
            )
            or not _strict_positive_u64(
                newest_time
            )
            or oldest_time > newest_time
        ):
            return finish(
                UNKNOWN,
                "INVALID_ENTRY_TIME_BOUNDS",
            )

        if newest_time > evaluated_at:
            return finish(
                UNKNOWN,
                "NEGATIVE_POSITION_AGE",
            )

        age_seconds = (
            evaluated_at
            - newest_time
        )

    liquidation = (
        valuation.valuation
    )

    if (
        getattr(
            liquidation,
            "resolver_version",
            None,
        )
        != LIVE_PUMP_LIQUIDATION_VALUE_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIQUIDATION_VERSION_MISMATCH",
        )

    if (
        getattr(
            liquidation,
            "status",
            None,
        )
        != LIQUIDATION_RESOLVED
    ):
        return finish(
            UNKNOWN,
            "LIQUIDATION_NOT_RESOLVED",
        )

    if (
        getattr(
            liquidation,
            "mint",
            None,
        )
        != normalized_mint
        or getattr(
            liquidation,
            "tokens_held",
            None,
        )
        != tokens_held
        or getattr(
            liquidation,
            "liquidation_value_lamports",
            None,
        )
        != liquidation_value
    ):
        return finish(
            UNKNOWN,
            "LIQUIDATION_BINDING_MISMATCH",
        )

    liquidatable_tokens = getattr(
        liquidation,
        "liquidatable_tokens",
        None,
    )

    unliquidatable_tokens = getattr(
        liquidation,
        "unliquidatable_tokens",
        None,
    )

    if (
        not _strict_u64(
            liquidatable_tokens
        )
        or not _strict_u64(
            unliquidatable_tokens
        )
        or (
            liquidatable_tokens
            + unliquidatable_tokens
            != tokens_held
        )
    ):
        return finish(
            UNKNOWN,
            "LIQUIDATION_INVENTORY_INVALID",
        )

    if (
        liquidatable_tokens
        != tokens_held
        or unliquidatable_tokens != 0
    ):
        return finish(
            UNKNOWN,
            "FULL_LIQUIDATION_UNAVAILABLE",
        )

    hypothetical_total_recovery = (
        cumulative_proceeds
        + liquidation_value
    )

    pnl_lamports = (
        hypothetical_total_recovery
        - entry_cost
    )

    return_bps = (
        pnl_lamports
        * 10_000
        / entry_cost
    )

    #
    # Exact decisions use integer cross-multiplication,
    # not rounded floating-point return_bps.
    #
    scaled_pnl = (
        pnl_lamports
        * 10_000
    )

    take_profit_threshold = (
        policy.take_profit_return_bps
        * entry_cost
    )

    stop_loss_threshold = (
        policy.stop_loss_return_bps
        * entry_cost
    )

    if (
        scaled_pnl
        >= take_profit_threshold
    ):
        return finish(
            FULL_EXIT,
            "TAKE_PROFIT",
            tokens_to_sell=tokens_held,
        )

    if (
        scaled_pnl
        <= stop_loss_threshold
    ):
        return finish(
            FULL_EXIT,
            "STOP_LOSS",
            tokens_to_sell=tokens_held,
        )

    if policy.max_hold_seconds is not None:
        if age_seconds is None:
            return finish(
                UNKNOWN,
                "MAX_HOLD_AGE_UNAVAILABLE",
            )

        if (
            age_seconds
            >= policy.max_hold_seconds
        ):
            return finish(
                FULL_EXIT,
                "MAX_HOLD_TIME",
                tokens_to_sell=tokens_held,
            )

    return finish(
        HOLD,
        None,
    )
