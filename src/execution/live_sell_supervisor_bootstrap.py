from __future__ import annotations

import math
import os
from pathlib import Path

from dotenv import load_dotenv

from src.execution.live_sell_supervisor_config import (
    LIVE_SELL_SUPERVISOR_CONFIG_VERSION,
    LiveSellSupervisorConfig,
)


LIVE_SELL_SUPERVISOR_BOOTSTRAP_VERSION = (
    "live-sell-supervisor-bootstrap-v1"
)

SELL_SUPERVISOR_TAKE_PROFIT_RETURN_BPS_ENV = (
    "DELVE_LIVE_SELL_SUPERVISOR_TAKE_PROFIT_RETURN_BPS"
)

SELL_SUPERVISOR_STOP_LOSS_RETURN_BPS_ENV = (
    "DELVE_LIVE_SELL_SUPERVISOR_STOP_LOSS_RETURN_BPS"
)

SELL_SUPERVISOR_MAX_HOLD_SECONDS_ENV = (
    "DELVE_LIVE_SELL_SUPERVISOR_MAX_HOLD_SECONDS"
)

SELL_SUPERVISOR_EVALUATION_INTERVAL_SECONDS_ENV = (
    "DELVE_LIVE_SELL_SUPERVISOR_EVALUATION_INTERVAL_SECONDS"
)


class LiveSellSupervisorBootstrapError(
    RuntimeError
):
    pass


def _normalize_dotenv_path(
    value: Path | str | None,
) -> Path | None:
    if value is None:
        return None

    if isinstance(
        value,
        Path,
    ):
        text = str(
            value
        )

    elif isinstance(
        value,
        str,
    ):
        text = value

    else:
        raise ValueError(
            "dotenv_path is invalid"
        )

    if (
        not text
        or text != text.strip()
    ):
        raise ValueError(
            "dotenv_path is invalid"
        )

    return Path(
        text
    )


def _required_text(
    name: str,
) -> str:
    value = os.environ.get(
        name
    )

    if value is None:
        raise LiveSellSupervisorBootstrapError(
            f"SELL_SUPERVISOR_ENV_MISSING:{name}"
        )

    if (
        not isinstance(
            value,
            str,
        )
        or not value
        or value != value.strip()
    ):
        raise LiveSellSupervisorBootstrapError(
            f"SELL_SUPERVISOR_ENV_INVALID:{name}"
        )

    return value


def _required_int(
    name: str,
) -> int:
    text = _required_text(
        name
    )

    try:
        value = int(
            text,
            10,
        )

    except (
        TypeError,
        ValueError,
    ):
        raise LiveSellSupervisorBootstrapError(
            f"SELL_SUPERVISOR_ENV_INVALID:{name}"
        ) from None

    #
    # Reject integer-looking alternate representations such as
    # "+1", "01", or "-0" so the launch contract stays canonical.
    #
    if str(value) != text:
        raise LiveSellSupervisorBootstrapError(
            f"SELL_SUPERVISOR_ENV_INVALID:{name}"
        )

    return value


def _required_float(
    name: str,
) -> float:
    text = _required_text(
        name
    )

    try:
        value = float(
            text
        )

    except (
        TypeError,
        ValueError,
    ):
        raise LiveSellSupervisorBootstrapError(
            f"SELL_SUPERVISOR_ENV_INVALID:{name}"
        ) from None

    if not math.isfinite(
        value
    ):
        raise LiveSellSupervisorBootstrapError(
            f"SELL_SUPERVISOR_ENV_INVALID:{name}"
        )

    return value


def _required_optional_max_hold(
    name: str,
) -> int | None:
    text = _required_text(
        name
    )

    #
    # "none" is the one explicit production sentinel for disabling
    # the max-hold exit. Missing or blank remains fail-closed.
    #
    if text == "none":
        return None

    try:
        value = int(
            text,
            10,
        )

    except (
        TypeError,
        ValueError,
    ):
        raise LiveSellSupervisorBootstrapError(
            f"SELL_SUPERVISOR_ENV_INVALID:{name}"
        ) from None

    if str(value) != text:
        raise LiveSellSupervisorBootstrapError(
            f"SELL_SUPERVISOR_ENV_INVALID:{name}"
        )

    return value


def bootstrap_live_sell_supervisor_config(
    *,
    dotenv_path: Path | str | None = Path(
        ".env"
    ),
) -> LiveSellSupervisorConfig:
    """
    Construct explicit continuous SELL policy/cadence configuration.

    This bootstrap owns configuration parsing only. It must not:
      - load or inspect signer secrets;
      - open SQLite;
      - create RPC clients;
      - acquire process authority;
      - start BUY/SELL/recovery work.

    Process environment takes precedence over dotenv values.
    """
    if (
        LIVE_SELL_SUPERVISOR_CONFIG_VERSION
        != "live-sell-supervisor-config-v1"
    ):
        raise LiveSellSupervisorBootstrapError(
            "SELL_SUPERVISOR_BOOTSTRAP_"
            "COMPONENT_VERSION_MISMATCH"
        )

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

    try:
        return LiveSellSupervisorConfig(
            config_version=(
                LIVE_SELL_SUPERVISOR_CONFIG_VERSION
            ),
            take_profit_return_bps=(
                _required_int(
                    SELL_SUPERVISOR_TAKE_PROFIT_RETURN_BPS_ENV
                )
            ),
            stop_loss_return_bps=(
                _required_int(
                    SELL_SUPERVISOR_STOP_LOSS_RETURN_BPS_ENV
                )
            ),
            max_hold_seconds=(
                _required_optional_max_hold(
                    SELL_SUPERVISOR_MAX_HOLD_SECONDS_ENV
                )
            ),
            evaluation_interval_seconds=(
                _required_float(
                    SELL_SUPERVISOR_EVALUATION_INTERVAL_SECONDS_ENV
                )
            ),
        )

    except LiveSellSupervisorBootstrapError:
        raise

    except (
        TypeError,
        ValueError,
    ):
        raise LiveSellSupervisorBootstrapError(
            "SELL_SUPERVISOR_BOOTSTRAP_CONFIG_INVALID"
        ) from None
