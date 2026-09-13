from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.risk.risk_governor import (
    RiskPolicy,
    validate_policy,
)


LIVE_OPERATING_CONFIG_VERSION = (
    "live-operating-config-v1"
)


def _strict_int(
    name: str,
    value: Any,
) -> int:
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            int,
        )
    ):
        raise TypeError(
            f"{name} must be int"
        )

    return value


def _positive_finite_number(
    name: str,
    value: Any,
) -> float:
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            (int, float),
        )
    ):
        raise TypeError(
            f"{name} must be numeric"
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
            f"{name} must be positive and finite"
        )

    return normalized


def _nonnegative_finite_number(
    name: str,
    value: Any,
) -> float:
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            (int, float),
        )
    ):
        raise TypeError(
            f"{name} must be numeric"
        )

    normalized = float(
        value
    )

    if (
        not math.isfinite(
            normalized
        )
        or normalized < 0.0
    ):
        raise ValueError(
            f"{name} must be nonnegative and finite"
        )

    return normalized


@dataclass(frozen=True)
class LiveOperatingConfig:
    """
    Non-secret process policy for live execution.

    All live risk limits are required explicitly. This class
    deliberately does not inherit RiskPolicy() defaults because
    those defaults are documented as shadow policy rather than
    live-capital authorization.

    Operational kill is the single process-level authority switch.
    Recovery relay authority is derived from it:

        kill=False -> recovery may relay a pristine signed artifact
        kill=True  -> recovery is reconciliation-only

    This object deliberately contains no:
      - private key;
      - signer;
      - signer loader;
      - RPC client;
      - transaction;
      - candidate/strategy state;
      - environment loading;
      - database mutation.
    """

    db_path: Path
    recovery_interval_seconds: float
    operational_kill: bool

    max_trade_equity_bps: int
    max_total_exposure_bps: int
    max_daily_loss_bps: int
    max_drawdown_bps: int

    max_open_positions: int
    min_trade_lamports: int

    max_size_price_impact_bps: float

    def __post_init__(
        self,
    ) -> None:
        if not isinstance(
            self.operational_kill,
            bool,
        ):
            raise TypeError(
                "operational_kill must be bool"
            )

        try:
            normalized_path = Path(
                self.db_path
            )
        except Exception:
            raise ValueError(
                "db_path is invalid"
            ) from None

        if (
            isinstance(
                self.db_path,
                str,
            )
            and not self.db_path.strip()
        ):
            raise ValueError(
                "db_path is invalid"
            )

        interval = _positive_finite_number(
            "recovery_interval_seconds",
            self.recovery_interval_seconds,
        )

        max_trade_equity_bps = _strict_int(
            "max_trade_equity_bps",
            self.max_trade_equity_bps,
        )

        max_total_exposure_bps = _strict_int(
            "max_total_exposure_bps",
            self.max_total_exposure_bps,
        )

        max_daily_loss_bps = _strict_int(
            "max_daily_loss_bps",
            self.max_daily_loss_bps,
        )

        max_drawdown_bps = _strict_int(
            "max_drawdown_bps",
            self.max_drawdown_bps,
        )

        max_open_positions = _strict_int(
            "max_open_positions",
            self.max_open_positions,
        )

        min_trade_lamports = _strict_int(
            "min_trade_lamports",
            self.min_trade_lamports,
        )

        max_size_price_impact_bps = (
            _nonnegative_finite_number(
                "max_size_price_impact_bps",
                self.max_size_price_impact_bps,
            )
        )

        policy = RiskPolicy(
            max_trade_equity_bps=(
                max_trade_equity_bps
            ),
            max_total_exposure_bps=(
                max_total_exposure_bps
            ),
            max_daily_loss_bps=(
                max_daily_loss_bps
            ),
            max_drawdown_bps=(
                max_drawdown_bps
            ),
            max_open_positions=(
                max_open_positions
            ),
            min_trade_lamports=(
                min_trade_lamports
            ),
            max_size_price_impact_bps=(
                max_size_price_impact_bps
            ),
        )

        validate_policy(
            policy
        )

        object.__setattr__(
            self,
            "db_path",
            normalized_path,
        )

        object.__setattr__(
            self,
            "recovery_interval_seconds",
            interval,
        )

        object.__setattr__(
            self,
            "max_size_price_impact_bps",
            max_size_price_impact_bps,
        )

    @property
    def recovery_allow_submission(
        self,
    ) -> bool:
        return not self.operational_kill

    @property
    def risk_policy(
        self,
    ) -> RiskPolicy:
        return RiskPolicy(
            max_trade_equity_bps=(
                self.max_trade_equity_bps
            ),
            max_total_exposure_bps=(
                self.max_total_exposure_bps
            ),
            max_daily_loss_bps=(
                self.max_daily_loss_bps
            ),
            max_drawdown_bps=(
                self.max_drawdown_bps
            ),
            max_open_positions=(
                self.max_open_positions
            ),
            min_trade_lamports=(
                self.min_trade_lamports
            ),
            max_size_price_impact_bps=(
                self.max_size_price_impact_bps
            ),
        )
