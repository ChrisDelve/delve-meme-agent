from __future__ import annotations

import time
from dataclasses import dataclass


EXIT_ENGINE_VERSION = (
    "shadow-exit-engine-v1"
)

HOLD = "HOLD"
EXIT = "EXIT"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ShadowExitPolicy:
    #
    # Shadow-only provisional policy.
    #
    # +10,000 bps return = 2.0x liquidation value
    # relative to total entry wallet cost.
    #
    take_profit_return_bps: int = 10_000

    #
    # Provisional -40% liquidation-value stop.
    # This is NOT claimed to be optimized.
    #
    stop_loss_return_bps: int = -4_000

    #
    # Model target horizon is 15 minutes.
    #
    max_hold_seconds: int = 15 * 60


@dataclass(frozen=True)
class ShadowExitDecision:
    engine_version: str

    status: str
    reason: str | None

    entry_timestamp: int
    evaluated_at: int

    age_seconds: int

    entry_wallet_cost_lamports: int
    cumulative_net_proceeds_lamports: int
    liquidation_value_lamports: int

    return_bps: float | None

    policy: ShadowExitPolicy

    @property
    def should_exit(self) -> bool:
        return (
            self.status == EXIT
        )


def validate_policy(
    policy: ShadowExitPolicy,
) -> None:

    if (
        policy.take_profit_return_bps
        <= 0
    ):
        raise ValueError(
            "take_profit_return_bps "
            "must be positive."
        )

    if (
        policy.stop_loss_return_bps
        >= 0
    ):
        raise ValueError(
            "stop_loss_return_bps "
            "must be negative."
        )

    if (
        policy.stop_loss_return_bps
        <= -10_000
    ):
        raise ValueError(
            "stop_loss_return_bps "
            "must be greater than -10000."
        )

    if (
        policy.max_hold_seconds
        <= 0
    ):
        raise ValueError(
            "max_hold_seconds "
            "must be positive."
        )


def evaluate_shadow_exit(
    *,
    entry_timestamp: int,

    entry_wallet_cost_lamports: int,
    cumulative_net_proceeds_lamports: int,
    liquidation_value_lamports: int,

    mark_status: str,

    evaluated_at: int | None = None,

    policy: ShadowExitPolicy | None = None,
) -> ShadowExitDecision:

    if policy is None:
        policy = ShadowExitPolicy()

    validate_policy(
        policy
    )

    if evaluated_at is None:
        evaluated_at = int(
            time.time()
        )

    if entry_timestamp <= 0:
        return ShadowExitDecision(
            engine_version=(
                EXIT_ENGINE_VERSION
            ),

            status=UNKNOWN,
            reason=(
                "INVALID_ENTRY_TIMESTAMP"
            ),

            entry_timestamp=int(
                entry_timestamp
            ),

            evaluated_at=int(
                evaluated_at
            ),

            age_seconds=0,

            entry_wallet_cost_lamports=int(
                entry_wallet_cost_lamports
            ),

            cumulative_net_proceeds_lamports=int(
                cumulative_net_proceeds_lamports
            ),

            liquidation_value_lamports=int(
                liquidation_value_lamports
            ),

            return_bps=None,

            policy=policy,
        )

    age_seconds = (
        int(
            evaluated_at
        )
        - int(
            entry_timestamp
        )
    )

    if age_seconds < 0:
        return ShadowExitDecision(
            engine_version=(
                EXIT_ENGINE_VERSION
            ),

            status=UNKNOWN,
            reason=(
                "NEGATIVE_POSITION_AGE"
            ),

            entry_timestamp=int(
                entry_timestamp
            ),

            evaluated_at=int(
                evaluated_at
            ),

            age_seconds=(
                age_seconds
            ),

            entry_wallet_cost_lamports=int(
                entry_wallet_cost_lamports
            ),

            cumulative_net_proceeds_lamports=int(
                cumulative_net_proceeds_lamports
            ),

            liquidation_value_lamports=int(
                liquidation_value_lamports
            ),

            return_bps=None,

            policy=policy,
        )

    if (
        entry_wallet_cost_lamports
        <= 0
    ):
        return ShadowExitDecision(
            engine_version=(
                EXIT_ENGINE_VERSION
            ),

            status=UNKNOWN,
            reason=(
                "INVALID_ENTRY_WALLET_COST"
            ),

            entry_timestamp=int(
                entry_timestamp
            ),

            evaluated_at=int(
                evaluated_at
            ),

            age_seconds=(
                age_seconds
            ),

            entry_wallet_cost_lamports=int(
                entry_wallet_cost_lamports
            ),

            cumulative_net_proceeds_lamports=int(
                cumulative_net_proceeds_lamports
            ),

            liquidation_value_lamports=int(
                liquidation_value_lamports
            ),

            return_bps=None,

            policy=policy,
        )

    if (
        cumulative_net_proceeds_lamports
        < 0
    ):
        return ShadowExitDecision(
            engine_version=(
                EXIT_ENGINE_VERSION
            ),

            status=UNKNOWN,
            reason=(
                "INVALID_CUMULATIVE_NET_PROCEEDS"
            ),

            entry_timestamp=int(
                entry_timestamp
            ),

            evaluated_at=int(
                evaluated_at
            ),

            age_seconds=(
                age_seconds
            ),

            entry_wallet_cost_lamports=int(
                entry_wallet_cost_lamports
            ),

            cumulative_net_proceeds_lamports=int(
                cumulative_net_proceeds_lamports
            ),

            liquidation_value_lamports=int(
                liquidation_value_lamports
            ),

            return_bps=None,

            policy=policy,
    )

    #
    # Price-based TP / SL decisions require an
    # executable liquidation mark.
    #
    # MAX_HOLD_TIME is different: it is a
    # time-based mandate and remains objectively
    # due even when the current liquidation mark
    # is unavailable.
    #
    if mark_status != "MARKED":
        if (
            age_seconds
            >= policy.max_hold_seconds
        ):
            return ShadowExitDecision(
                engine_version=(
                    EXIT_ENGINE_VERSION
                ),

                status=EXIT,
                reason="MAX_HOLD_TIME",

                entry_timestamp=int(
                    entry_timestamp
                ),

                evaluated_at=int(
                    evaluated_at
                ),

                age_seconds=(
                    age_seconds
                ),

                entry_wallet_cost_lamports=int(
                    entry_wallet_cost_lamports
                ),

                cumulative_net_proceeds_lamports=int(
                    cumulative_net_proceeds_lamports
                ),

                liquidation_value_lamports=int(
                    liquidation_value_lamports
                ),

                return_bps=None,

                policy=policy,
            )

        return ShadowExitDecision(
            engine_version=(
                EXIT_ENGINE_VERSION
            ),

            status=UNKNOWN,
            reason=(
                "LIQUIDATION_MARK_UNAVAILABLE:"
                f"{mark_status}"
            ),

            entry_timestamp=int(
                entry_timestamp
            ),

            evaluated_at=int(
                evaluated_at
            ),

            age_seconds=(
                age_seconds
            ),

            entry_wallet_cost_lamports=int(
                entry_wallet_cost_lamports
            ),

            cumulative_net_proceeds_lamports=int(
                cumulative_net_proceeds_lamports
            ),

            liquidation_value_lamports=int(
                liquidation_value_lamports
            ),

            return_bps=None,

            policy=policy,
        )

    hypothetical_total_recovery = (
        int(
            cumulative_net_proceeds_lamports
        )
        + int(
            liquidation_value_lamports
        )
    )

    pnl_lamports = (
        hypothetical_total_recovery
        - int(
            entry_wallet_cost_lamports
        )
    )

    return_bps = (
        pnl_lamports
        * 10_000
        / int(
            entry_wallet_cost_lamports
        )
    )

    #
    # Cross-multiplication avoids making the
    # actual decision from rounded return_bps.
    #
    scaled_pnl = (
        pnl_lamports
        * 10_000
    )

    take_profit_threshold = (
        int(
            policy.take_profit_return_bps
        )
        * int(
            entry_wallet_cost_lamports
        )
    )

    stop_loss_threshold = (
        int(
            policy.stop_loss_return_bps
        )
        * int(
            entry_wallet_cost_lamports
        )
    )

    if (
        scaled_pnl
        >= take_profit_threshold
    ):
        status = EXIT
        reason = "TAKE_PROFIT"

    elif (
        scaled_pnl
        <= stop_loss_threshold
    ):
        status = EXIT
        reason = "STOP_LOSS"

    elif (
        age_seconds
        >= policy.max_hold_seconds
    ):
        status = EXIT
        reason = "MAX_HOLD_TIME"

    else:
        status = HOLD
        reason = None

    return ShadowExitDecision(
        engine_version=(
            EXIT_ENGINE_VERSION
        ),

        status=status,
        reason=reason,

        entry_timestamp=int(
            entry_timestamp
        ),

        evaluated_at=int(
            evaluated_at
        ),

        age_seconds=(
            age_seconds
        ),

        entry_wallet_cost_lamports=int(
            entry_wallet_cost_lamports
        ),

        cumulative_net_proceeds_lamports=int(
            cumulative_net_proceeds_lamports
        ),

        liquidation_value_lamports=int(
            liquidation_value_lamports
        ),

        return_bps=float(
            return_bps
        ),

        policy=policy,
    )
