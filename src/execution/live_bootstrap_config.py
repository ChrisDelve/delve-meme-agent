from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.execution.live_operating_config import (
    LiveOperatingConfig,
)


LIVE_BOOTSTRAP_CONFIG_VERSION = (
    "live-bootstrap-config-v1"
)

LIVE_DB_PATH_ENV = (
    "DELVE_LIVE_DB_PATH"
)
LIVE_RECOVERY_INTERVAL_SECONDS_ENV = (
    "DELVE_LIVE_RECOVERY_INTERVAL_SECONDS"
)
LIVE_OPERATIONAL_KILL_ENV = (
    "DELVE_LIVE_OPERATIONAL_KILL"
)

LIVE_MAX_TRADE_EQUITY_BPS_ENV = (
    "DELVE_LIVE_MAX_TRADE_EQUITY_BPS"
)
LIVE_MAX_TOTAL_EXPOSURE_BPS_ENV = (
    "DELVE_LIVE_MAX_TOTAL_EXPOSURE_BPS"
)
LIVE_MAX_DAILY_LOSS_BPS_ENV = (
    "DELVE_LIVE_MAX_DAILY_LOSS_BPS"
)
LIVE_MAX_DRAWDOWN_BPS_ENV = (
    "DELVE_LIVE_MAX_DRAWDOWN_BPS"
)
LIVE_MAX_OPEN_POSITIONS_ENV = (
    "DELVE_LIVE_MAX_OPEN_POSITIONS"
)
LIVE_MIN_TRADE_LAMPORTS_ENV = (
    "DELVE_LIVE_MIN_TRADE_LAMPORTS"
)
LIVE_MAX_SIZE_PRICE_IMPACT_BPS_ENV = (
    "DELVE_LIVE_MAX_SIZE_PRICE_IMPACT_BPS"
)


class LiveBootstrapConfigurationError(
    ValueError
):
    pass


def _missing(
    name: str,
) -> LiveBootstrapConfigurationError:
    return LiveBootstrapConfigurationError(
        f"LIVE_ENV_MISSING:{name}"
    )


def _invalid(
    name: str,
) -> LiveBootstrapConfigurationError:
    return LiveBootstrapConfigurationError(
        f"LIVE_ENV_INVALID:{name}"
    )


def _required_text(
    name: str,
) -> str:
    value = os.environ.get(
        name
    )

    if value is None:
        raise _missing(
            name
        )

    if (
        not isinstance(
            value,
            str,
        )
        or not value
        or value != value.strip()
    ):
        raise _invalid(
            name
        )

    return value


def _required_int(
    name: str,
) -> int:
    value = _required_text(
        name
    )

    if (
        not value.isascii()
        or not value.isdigit()
    ):
        raise _invalid(
            name
        )

    try:
        return int(
            value,
            10,
        )
    except Exception:
        raise _invalid(
            name
        ) from None


def _required_float(
    name: str,
) -> float:
    value = _required_text(
        name
    )

    try:
        normalized = float(
            value
        )
    except Exception:
        raise _invalid(
            name
        ) from None

    if not math.isfinite(
        normalized
    ):
        raise _invalid(
            name
        )

    return normalized


def _required_bool(
    name: str,
) -> bool:
    value = _required_text(
        name
    )

    if value == "true":
        return True

    if value == "false":
        return False

    raise _invalid(
        name
    )


def _normalize_dotenv_path(
    value: Any,
) -> Path | None:
    if value is None:
        return None

    if (
        isinstance(
            value,
            str,
        )
        and not value.strip()
    ):
        raise ValueError(
            "dotenv_path is invalid"
        )

    try:
        return Path(
            value
        )
    except Exception:
        raise ValueError(
            "dotenv_path is invalid"
        ) from None


def bootstrap_live_operating_config(
    *,
    dotenv_path: Path | str | None = Path(
        ".env"
    ),
) -> LiveOperatingConfig:
    """
    Populate the production process environment and construct the
    canonical non-secret live operating policy.

    Process environment always takes precedence over dotenv values.

    This boundary deliberately does not:
      - read or parse the Solana keypair;
      - construct or retain a signer;
      - start recovery;
      - create asyncio tasks or loops;
      - invoke BUY or SELL execution;
      - access SQLite;
      - construct, sign, submit, or reconcile transactions.

    Signer authority remains lazy and is owned downstream by the
    existing live execution runtime path.
    """
    normalized_dotenv_path = (
        _normalize_dotenv_path(
            dotenv_path
        )
    )

    if normalized_dotenv_path is not None:
        load_dotenv(
            dotenv_path=normalized_dotenv_path,
            override=False,
        )

    return LiveOperatingConfig(
        db_path=Path(
            _required_text(
                LIVE_DB_PATH_ENV
            )
        ),
        recovery_interval_seconds=(
            _required_float(
                LIVE_RECOVERY_INTERVAL_SECONDS_ENV
            )
        ),
        operational_kill=_required_bool(
            LIVE_OPERATIONAL_KILL_ENV
        ),
        max_trade_equity_bps=_required_int(
            LIVE_MAX_TRADE_EQUITY_BPS_ENV
        ),
        max_total_exposure_bps=_required_int(
            LIVE_MAX_TOTAL_EXPOSURE_BPS_ENV
        ),
        max_daily_loss_bps=_required_int(
            LIVE_MAX_DAILY_LOSS_BPS_ENV
        ),
        max_drawdown_bps=_required_int(
            LIVE_MAX_DRAWDOWN_BPS_ENV
        ),
        max_open_positions=_required_int(
            LIVE_MAX_OPEN_POSITIONS_ENV
        ),
        min_trade_lamports=_required_int(
            LIVE_MIN_TRADE_LAMPORTS_ENV
        ),
        max_size_price_impact_bps=(
            _required_float(
                LIVE_MAX_SIZE_PRICE_IMPACT_BPS_ENV
            )
        ),
    )
