from __future__ import annotations

import math
from dataclasses import dataclass

from solders.pubkey import Pubkey


LIVE_BUY_EXECUTION_CONFIG_VERSION = (
    "live-buy-execution-config-v1"
)

BPS_DENOMINATOR = 10_000
SQLITE_INT_MAX = (1 << 63) - 1
MAX_COMPUTE_UNIT_LIMIT = 1_400_000


def _wallet_pubkey(
    value: object,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
    ):
        raise ValueError(
            "wallet_pubkey is invalid"
        )

    try:
        parsed = Pubkey.from_string(
            value
        )
    except Exception:
        raise ValueError(
            "wallet_pubkey is invalid"
        ) from None

    if parsed == Pubkey.default():
        raise ValueError(
            "wallet_pubkey is invalid"
        )

    return str(parsed)


def _strict_bps(
    *,
    name: str,
    value: object,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > BPS_DENOMINATOR
    ):
        raise ValueError(
            f"{name} is invalid"
        )

    return value


def _strict_nonnegative_sqlite_int(
    *,
    name: str,
    value: object,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > SQLITE_INT_MAX
    ):
        raise ValueError(
            f"{name} is invalid"
        )

    return value


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
        or isinstance(value, bool)
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


def _compute_unit_limit(
    value: object,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
        or value > MAX_COMPUTE_UNIT_LIMIT
    ):
        raise ValueError(
            "compute_unit_limit is invalid"
        )

    return value


@dataclass(
    frozen=True,
    slots=True,
)
class LiveBuyExecutionConfig:
    """
    Explicit non-secret production assumptions for one live BUY path.

    This contract contains account identity and execution assumptions.
    It does not contain portfolio risk limits; those remain owned by
    LiveOperatingConfig / RiskPolicy.

    protected_cash_lamports is a cash floor:

        raw native SOL wallet balance
        - protected_cash_lamports
        = base available trading cash

    Authoritative account state and held-reservation accounting remain
    downstream in the existing reservation/risk path.

    This object deliberately has no authority to:
      - load environment variables;
      - load or invoke a signer/private key;
      - resolve wallet or market state by RPC;
      - access or mutate SQLite;
      - select a candidate;
      - resolve entry evidence;
      - size a trade;
      - reserve capital;
      - construct/sign/submit a transaction;
      - invoke BUY or SELL execution.
    """

    config_version: str

    wallet_pubkey: str
    protected_cash_lamports: int

    buy_slippage_bps: int
    buy_base_network_fee_lamports: int
    buy_priority_fee_lamports: int
    buy_rent_lamports: int

    exit_slippage_bps: int
    exit_base_network_fee_lamports: int
    exit_priority_fee_lamports: int

    reservation_ttl_seconds: float
    max_authorization_age_seconds: float

    compute_unit_limit: int

    def __post_init__(
        self,
    ) -> None:
        if (
            self.config_version
            != LIVE_BUY_EXECUTION_CONFIG_VERSION
        ):
            raise ValueError(
                "config_version is invalid"
            )

        normalized_wallet = (
            _wallet_pubkey(
                self.wallet_pubkey
            )
        )

        protected_cash = (
            _strict_nonnegative_sqlite_int(
                name=(
                    "protected_cash_lamports"
                ),
                value=(
                    self.protected_cash_lamports
                ),
            )
        )

        buy_slippage = _strict_bps(
            name="buy_slippage_bps",
            value=self.buy_slippage_bps,
        )

        exit_slippage = _strict_bps(
            name="exit_slippage_bps",
            value=self.exit_slippage_bps,
        )

        buy_base_network_fee = (
            _strict_nonnegative_sqlite_int(
                name=(
                    "buy_base_network_fee_lamports"
                ),
                value=(
                    self.buy_base_network_fee_lamports
                ),
            )
        )

        buy_priority_fee = (
            _strict_nonnegative_sqlite_int(
                name=(
                    "buy_priority_fee_lamports"
                ),
                value=(
                    self.buy_priority_fee_lamports
                ),
            )
        )

        buy_rent = (
            _strict_nonnegative_sqlite_int(
                name="buy_rent_lamports",
                value=self.buy_rent_lamports,
            )
        )

        exit_base_network_fee = (
            _strict_nonnegative_sqlite_int(
                name=(
                    "exit_base_network_fee_lamports"
                ),
                value=(
                    self.exit_base_network_fee_lamports
                ),
            )
        )

        exit_priority_fee = (
            _strict_nonnegative_sqlite_int(
                name=(
                    "exit_priority_fee_lamports"
                ),
                value=(
                    self.exit_priority_fee_lamports
                ),
            )
        )

        reservation_ttl = (
            _positive_finite_number(
                name=(
                    "reservation_ttl_seconds"
                ),
                value=(
                    self.reservation_ttl_seconds
                ),
            )
        )

        authorization_age = (
            _positive_finite_number(
                name=(
                    "max_authorization_age_seconds"
                ),
                value=(
                    self.max_authorization_age_seconds
                ),
            )
        )

        compute_limit = (
            _compute_unit_limit(
                self.compute_unit_limit
            )
        )

        object.__setattr__(
            self,
            "wallet_pubkey",
            normalized_wallet,
        )

        object.__setattr__(
            self,
            "protected_cash_lamports",
            protected_cash,
        )

        object.__setattr__(
            self,
            "buy_slippage_bps",
            buy_slippage,
        )

        object.__setattr__(
            self,
            "buy_base_network_fee_lamports",
            buy_base_network_fee,
        )

        object.__setattr__(
            self,
            "buy_priority_fee_lamports",
            buy_priority_fee,
        )

        object.__setattr__(
            self,
            "buy_rent_lamports",
            buy_rent,
        )

        object.__setattr__(
            self,
            "exit_slippage_bps",
            exit_slippage,
        )

        object.__setattr__(
            self,
            "exit_base_network_fee_lamports",
            exit_base_network_fee,
        )

        object.__setattr__(
            self,
            "exit_priority_fee_lamports",
            exit_priority_fee,
        )

        object.__setattr__(
            self,
            "reservation_ttl_seconds",
            reservation_ttl,
        )

        object.__setattr__(
            self,
            "max_authorization_age_seconds",
            authorization_age,
        )

        object.__setattr__(
            self,
            "compute_unit_limit",
            compute_limit,
        )
