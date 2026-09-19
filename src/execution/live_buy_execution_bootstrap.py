from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from src.execution.live_buy_execution_config import (
    LIVE_BUY_EXECUTION_CONFIG_VERSION,
    LiveBuyExecutionConfig,
)


LIVE_BUY_EXECUTION_BOOTSTRAP_VERSION = (
    "live-buy-execution-bootstrap-v1"
)

BUY_EXECUTION_WALLET_PUBKEY_ENV = (
    "DELVE_LIVE_BUY_EXECUTION_WALLET_PUBKEY"
)

BUY_EXECUTION_PROTECTED_CASH_LAMPORTS_ENV = (
    "DELVE_LIVE_BUY_EXECUTION_PROTECTED_CASH_LAMPORTS"
)

BUY_EXECUTION_BUY_SLIPPAGE_BPS_ENV = (
    "DELVE_LIVE_BUY_EXECUTION_BUY_SLIPPAGE_BPS"
)

BUY_EXECUTION_BUY_BASE_NETWORK_FEE_LAMPORTS_ENV = (
    "DELVE_LIVE_BUY_EXECUTION_BUY_BASE_NETWORK_FEE_LAMPORTS"
)

BUY_EXECUTION_BUY_PRIORITY_FEE_LAMPORTS_ENV = (
    "DELVE_LIVE_BUY_EXECUTION_BUY_PRIORITY_FEE_LAMPORTS"
)

BUY_EXECUTION_BUY_RENT_LAMPORTS_ENV = (
    "DELVE_LIVE_BUY_EXECUTION_BUY_RENT_LAMPORTS"
)

BUY_EXECUTION_EXIT_SLIPPAGE_BPS_ENV = (
    "DELVE_LIVE_BUY_EXECUTION_EXIT_SLIPPAGE_BPS"
)

BUY_EXECUTION_EXIT_BASE_NETWORK_FEE_LAMPORTS_ENV = (
    "DELVE_LIVE_BUY_EXECUTION_EXIT_BASE_NETWORK_FEE_LAMPORTS"
)

BUY_EXECUTION_EXIT_PRIORITY_FEE_LAMPORTS_ENV = (
    "DELVE_LIVE_BUY_EXECUTION_EXIT_PRIORITY_FEE_LAMPORTS"
)

BUY_EXECUTION_RESERVATION_TTL_SECONDS_ENV = (
    "DELVE_LIVE_BUY_EXECUTION_RESERVATION_TTL_SECONDS"
)

BUY_EXECUTION_MAX_AUTHORIZATION_AGE_SECONDS_ENV = (
    "DELVE_LIVE_BUY_EXECUTION_MAX_AUTHORIZATION_AGE_SECONDS"
)

BUY_EXECUTION_COMPUTE_UNIT_LIMIT_ENV = (
    "DELVE_LIVE_BUY_EXECUTION_COMPUTE_UNIT_LIMIT"
)


class LiveBuyExecutionBootstrapError(
    RuntimeError
):
    pass


def _components_are_compatible(
) -> bool:
    return (
        LIVE_BUY_EXECUTION_CONFIG_VERSION
        == "live-buy-execution-config-v1"
    )


def _normalize_dotenv_path(
    value: Path | str | None,
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


def _required_text(
    name: str,
) -> str:
    value = os.environ.get(
        name
    )

    if value is None:
        raise LiveBuyExecutionBootstrapError(
            f"BUY_EXECUTION_ENV_MISSING:{name}"
        )

    if (
        not isinstance(
            value,
            str,
        )
        or not value
        or value != value.strip()
    ):
        raise LiveBuyExecutionBootstrapError(
            f"BUY_EXECUTION_ENV_INVALID:{name}"
        )

    return value


def _int_from_env(
    name: str,
) -> int:
    text = _required_text(
        name
    )

    try:
        return int(
            text,
            10,
        )
    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        raise LiveBuyExecutionBootstrapError(
            f"BUY_EXECUTION_ENV_INVALID:{name}"
        ) from None


def _float_from_env(
    name: str,
) -> float:
    text = _required_text(
        name
    )

    try:
        return float(
            text
        )
    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        raise LiveBuyExecutionBootstrapError(
            f"BUY_EXECUTION_ENV_INVALID:{name}"
        ) from None


def bootstrap_live_buy_execution_config(
    *,
    dotenv_path: Path | str | None = Path(
        ".env"
    ),
) -> LiveBuyExecutionConfig:
    """
    Load explicit non-secret production assumptions for live BUY
    execution.

    Exactly twelve environment values are required. There are no
    production defaults.

    Process environment takes precedence over dotenv values.

    This bootstrap deliberately does not:
      - read signer/private-key environment variables;
      - construct or invoke a signer;
      - construct LiveProcessOwner;
      - start recovery;
      - start candidate scheduling;
      - resolve candidate policy or evidence;
      - access wallet or market state by RPC;
      - access or mutate SQLite;
      - size or reserve capital;
      - invoke BUY or SELL execution;
      - construct/sign/submit transactions.

    Parsing belongs here. Semantic bounds and account-identity validity
    remain authoritative in LiveBuyExecutionConfig.
    """

    if not _components_are_compatible():
        raise LiveBuyExecutionBootstrapError(
            "BUY_EXECUTION_BOOTSTRAP_"
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

    wallet_pubkey = _required_text(
        BUY_EXECUTION_WALLET_PUBKEY_ENV
    )

    protected_cash_lamports = (
        _int_from_env(
            BUY_EXECUTION_PROTECTED_CASH_LAMPORTS_ENV
        )
    )

    buy_slippage_bps = _int_from_env(
        BUY_EXECUTION_BUY_SLIPPAGE_BPS_ENV
    )

    buy_base_network_fee_lamports = (
        _int_from_env(
            BUY_EXECUTION_BUY_BASE_NETWORK_FEE_LAMPORTS_ENV
        )
    )

    buy_priority_fee_lamports = (
        _int_from_env(
            BUY_EXECUTION_BUY_PRIORITY_FEE_LAMPORTS_ENV
        )
    )

    buy_rent_lamports = _int_from_env(
        BUY_EXECUTION_BUY_RENT_LAMPORTS_ENV
    )

    exit_slippage_bps = _int_from_env(
        BUY_EXECUTION_EXIT_SLIPPAGE_BPS_ENV
    )

    exit_base_network_fee_lamports = (
        _int_from_env(
            BUY_EXECUTION_EXIT_BASE_NETWORK_FEE_LAMPORTS_ENV
        )
    )

    exit_priority_fee_lamports = (
        _int_from_env(
            BUY_EXECUTION_EXIT_PRIORITY_FEE_LAMPORTS_ENV
        )
    )

    reservation_ttl_seconds = (
        _float_from_env(
            BUY_EXECUTION_RESERVATION_TTL_SECONDS_ENV
        )
    )

    max_authorization_age_seconds = (
        _float_from_env(
            BUY_EXECUTION_MAX_AUTHORIZATION_AGE_SECONDS_ENV
        )
    )

    compute_unit_limit = _int_from_env(
        BUY_EXECUTION_COMPUTE_UNIT_LIMIT_ENV
    )

    try:
        return LiveBuyExecutionConfig(
            config_version=(
                LIVE_BUY_EXECUTION_CONFIG_VERSION
            ),
            wallet_pubkey=wallet_pubkey,
            protected_cash_lamports=(
                protected_cash_lamports
            ),
            buy_slippage_bps=(
                buy_slippage_bps
            ),
            buy_base_network_fee_lamports=(
                buy_base_network_fee_lamports
            ),
            buy_priority_fee_lamports=(
                buy_priority_fee_lamports
            ),
            buy_rent_lamports=(
                buy_rent_lamports
            ),
            exit_slippage_bps=(
                exit_slippage_bps
            ),
            exit_base_network_fee_lamports=(
                exit_base_network_fee_lamports
            ),
            exit_priority_fee_lamports=(
                exit_priority_fee_lamports
            ),
            reservation_ttl_seconds=(
                reservation_ttl_seconds
            ),
            max_authorization_age_seconds=(
                max_authorization_age_seconds
            ),
            compute_unit_limit=(
                compute_unit_limit
            ),
        )

    except Exception:
        raise LiveBuyExecutionBootstrapError(
            "BUY_EXECUTION_BOOTSTRAP_CONFIG_INVALID"
        ) from None
