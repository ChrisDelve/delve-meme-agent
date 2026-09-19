from __future__ import annotations

import math
from dataclasses import dataclass

from src.strategies.live_exit_policy import (
    LIVE_EXIT_POLICY_VERSION,
    LiveExitPolicy,
)


LIVE_SELL_SUPERVISOR_CONFIG_VERSION = (
    "live-sell-supervisor-config-v1"
)

U64_MAX = (1 << 64) - 1


def _strict_positive_int(
    *,
    name: str,
    value: object,
) -> int:
    if (
        not isinstance(
            value,
            int,
        )
        or isinstance(
            value,
            bool,
        )
        or value <= 0
    ):
        raise ValueError(
            f"{name} is invalid"
        )

    return value


def _strict_stop_loss_bps(
    value: object,
) -> int:
    if (
        not isinstance(
            value,
            int,
        )
        or isinstance(
            value,
            bool,
        )
        or value >= 0
        or value <= -10_000
    ):
        raise ValueError(
            "stop_loss_return_bps is invalid"
        )

    return value


def _optional_positive_u64(
    *,
    name: str,
    value: object,
) -> int | None:
    if value is None:
        return None

    normalized = _strict_positive_int(
        name=name,
        value=value,
    )

    if normalized > U64_MAX:
        raise ValueError(
            f"{name} is invalid"
        )

    return normalized


def _positive_finite_number(
    *,
    name: str,
    value: object,
) -> float:
    if (
        not isinstance(
            value,
            (int, float),
        )
        or isinstance(
            value,
            bool,
        )
    ):
        raise ValueError(
            f"{name} is invalid"
        )

    normalized = float(
        value
    )

    if (
        not math.isfinite(
            normalized
        )
        or normalized <= 0.0
    ):
        raise ValueError(
            f"{name} is invalid"
        )

    return normalized


@dataclass(
    frozen=True,
    slots=True,
)
class LiveSellSupervisorConfig:
    """
    Explicit non-secret policy and cadence for continuous live SELL
    supervision.

    This config owns only:
      - take-profit threshold;
      - stop-loss threshold;
      - optional maximum holding period;
      - periodic SELL evaluation interval.

    It deliberately owns no:
      - environment/bootstrap behavior;
      - LiveProcessOwner;
      - signer/private key;
      - RPC;
      - SQLite;
      - recovery;
      - SELL execution;
      - transaction construction/signing/submission;
      - retry classification.

    No live defaults exist.

    Runtime execution assumptions such as wallet identity, compute-unit
    limit, exit slippage, and exit network fees remain owned by
    LiveBuyExecutionConfig.
    """

    config_version: str

    take_profit_return_bps: int
    stop_loss_return_bps: int
    max_hold_seconds: int | None

    evaluation_interval_seconds: float

    def __post_init__(
        self,
    ) -> None:
        if (
            self.config_version
            != LIVE_SELL_SUPERVISOR_CONFIG_VERSION
        ):
            raise ValueError(
                "config_version is invalid"
            )

        if (
            LIVE_EXIT_POLICY_VERSION
            != "live-exit-policy-v1"
        ):
            raise RuntimeError(
                "LIVE_SELL_SUPERVISOR_CONFIG_"
                "COMPONENT_VERSION_MISMATCH"
            )

        take_profit = (
            _strict_positive_int(
                name=(
                    "take_profit_return_bps"
                ),
                value=(
                    self.take_profit_return_bps
                ),
            )
        )

        stop_loss = (
            _strict_stop_loss_bps(
                self.stop_loss_return_bps
            )
        )

        max_hold = (
            _optional_positive_u64(
                name="max_hold_seconds",
                value=self.max_hold_seconds,
            )
        )

        interval = (
            _positive_finite_number(
                name=(
                    "evaluation_interval_seconds"
                ),
                value=(
                    self.evaluation_interval_seconds
                ),
            )
        )

        object.__setattr__(
            self,
            "take_profit_return_bps",
            take_profit,
        )

        object.__setattr__(
            self,
            "stop_loss_return_bps",
            stop_loss,
        )

        object.__setattr__(
            self,
            "max_hold_seconds",
            max_hold,
        )

        object.__setattr__(
            self,
            "evaluation_interval_seconds",
            interval,
        )

    @property
    def policy(
        self,
    ) -> LiveExitPolicy:
        """
        Return the canonical immutable exit policy represented by this
        supervisor configuration.
        """

        return LiveExitPolicy(
            take_profit_return_bps=(
                self.take_profit_return_bps
            ),
            stop_loss_return_bps=(
                self.stop_loss_return_bps
            ),
            max_hold_seconds=(
                self.max_hold_seconds
            ),
        )
