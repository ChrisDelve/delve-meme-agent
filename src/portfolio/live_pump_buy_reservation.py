from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solders.pubkey import Pubkey

from src.execution.pump_execution_simulator import (
    PumpCurveState,
)
from src.portfolio.live_account_risk_state import (
    LIVE_ACCOUNT_RISK_STATE_VERSION,
    RESOLVED as ACCOUNT_STATE_RESOLVED,
    LiveAccountRiskStateResult,
    resolve_live_account_risk_state,
)
from src.portfolio.live_reservations import (
    DB_PATH,
    ACTIVE,
    RESERVATION_VERSION,
    SQLITE_INT_MAX,
    ReservationDecision,
    reserve_pump_buy_capital,
)
from src.portfolio.live_wallet_valuation import (
    LIVE_WALLET_VALUATION_VERSION,
    RESOLVED as WALLET_VALUATION_RESOLVED,
)
from src.risk.risk_governor import (
    RiskPolicy,
)


LIVE_PUMP_BUY_RESERVATION_VERSION = (
    "live-pump-buy-reservation-v1"
)

BPS_DENOMINATOR = 10_000
U64_MAX = (1 << 64) - 1

PASS = "PASS"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LivePumpBuyReservationResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    wallet_pubkey: str
    mint: str

    raw_wallet_balance_lamports: (
        int | None
    )

    protected_cash_lamports: (
        int | None
    )

    available_cash_lamports: (
        int | None
    )

    account_state: (
        LiveAccountRiskStateResult | None
    )

    reservation_decision: (
        ReservationDecision | None
    )


def _strict_nonnegative_int(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= SQLITE_INT_MAX
    )


def _strict_u64(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= U64_MAX
    )


def _strict_bps(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= BPS_DENOMINATOR
    )


async def reserve_live_pump_buy(
    *,
    mint: str,
    wallet_pubkey: str,

    protected_cash_lamports: int,

    curve_state: PumpCurveState,
    protocol_fee_bps: int,
    creator_fee_bps: int,

    buy_slippage_bps: int,
    buy_base_network_fee_lamports: int,
    buy_priority_fee_lamports: int,
    buy_rent_lamports: int,

    exit_slippage_bps: int,
    exit_base_network_fee_lamports: int,
    exit_priority_fee_lamports: int,

    reservation_ttl_seconds: float,

    policy: RiskPolicy | None = None,
    min_context_slot: int | None = None,
    db_path: Path = DB_PATH,
) -> LivePumpBuyReservationResult:
    """
    Resolve authoritative live portfolio risk and
    atomically reserve capital for one Pump BUY.

    Cash contract:

        raw native SOL wallet balance
        - explicit protected cash
        = available_cash_lamports supplied to
          reserve_pump_buy_capital()

    ACTIVE/SIGNED/SUBMITTED reservation liabilities
    are deliberately NOT subtracted here.
    reserve_pump_buy_capital() subtracts them
    atomically inside its own reservation transaction.

    Exit-valuation assumptions are intentionally
    distinct from proposed-BUY execution assumptions.

    This orchestration boundary performs no:
    - transaction construction
    - signing
    - submission
    - direct SQL
    - direct wallet-balance RPC
    - manual held-reservation accounting
    """

    normalized_wallet = ""

    normalized_mint = ""

    def finish(
        status: str,
        *reasons: str,
        raw_wallet_balance_lamports: (
            int | None
        ) = None,
        protected_cash: (
            int | None
        ) = None,
        available_cash_lamports: (
            int | None
        ) = None,
        account_state: (
            LiveAccountRiskStateResult
            | None
        ) = None,
        reservation_decision: (
            ReservationDecision
            | None
        ) = None,
    ) -> LivePumpBuyReservationResult:
        return LivePumpBuyReservationResult(
            resolver_version=(
                LIVE_PUMP_BUY_RESERVATION_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=(
                normalized_wallet
            ),
            mint=normalized_mint,
            raw_wallet_balance_lamports=(
                raw_wallet_balance_lamports
            ),
            protected_cash_lamports=(
                protected_cash
            ),
            available_cash_lamports=(
                available_cash_lamports
            ),
            account_state=account_state,
            reservation_decision=(
                reservation_decision
            ),
        )

    # --------------------------------------------------------
    # Input contract before RPC / DB mutation.
    # --------------------------------------------------------

    if not isinstance(
        mint,
        str,
    ):
        return finish(
            UNKNOWN,
            "INVALID_MINT",
        )

    normalized_mint = (
        mint.strip()
    )

    if not normalized_mint:
        return finish(
            UNKNOWN,
            "INVALID_MINT",
        )

    if not isinstance(
        wallet_pubkey,
        str,
    ):
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    wallet_pubkey = (
        wallet_pubkey.strip()
    )

    if not wallet_pubkey:
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    try:
        parsed_wallet = (
            Pubkey.from_string(
                wallet_pubkey
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    if parsed_wallet == Pubkey.default():
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    normalized_wallet = str(
        parsed_wallet
    )

    if not _strict_nonnegative_int(
        protected_cash_lamports
    ):
        return finish(
            UNKNOWN,
            "INVALID_PROTECTED_CASH",
        )

    protected_cash_lamports = int(
        protected_cash_lamports
    )

    for value, reason in (
        (
            protocol_fee_bps,
            "INVALID_PROTOCOL_FEE_BPS",
        ),
        (
            creator_fee_bps,
            "INVALID_CREATOR_FEE_BPS",
        ),
        (
            buy_slippage_bps,
            "INVALID_BUY_SLIPPAGE_BPS",
        ),
        (
            exit_slippage_bps,
            "INVALID_EXIT_SLIPPAGE_BPS",
        ),
    ):
        if not _strict_bps(
            value
        ):
            return finish(
                UNKNOWN,
                reason,
                protected_cash=(
                    protected_cash_lamports
                ),
            )

    for value, reason in (
        (
            buy_base_network_fee_lamports,
            "INVALID_BUY_BASE_NETWORK_FEE",
        ),
        (
            buy_priority_fee_lamports,
            "INVALID_BUY_PRIORITY_FEE",
        ),
        (
            buy_rent_lamports,
            "INVALID_BUY_RENT",
        ),
        (
            exit_base_network_fee_lamports,
            "INVALID_EXIT_BASE_NETWORK_FEE",
        ),
        (
            exit_priority_fee_lamports,
            "INVALID_EXIT_PRIORITY_FEE",
        ),
    ):
        if not _strict_nonnegative_int(
            value
        ):
            return finish(
                UNKNOWN,
                reason,
                protected_cash=(
                    protected_cash_lamports
                ),
            )

    if (
        min_context_slot is not None
        and not _strict_u64(
            min_context_slot
        )
    ):
        return finish(
            UNKNOWN,
            "INVALID_MIN_CONTEXT_SLOT",
            protected_cash=(
                protected_cash_lamports
            ),
        )

    if (
        not isinstance(
            reservation_ttl_seconds,
            (int, float),
        )
        or isinstance(
            reservation_ttl_seconds,
            bool,
        )
        or not (
            float(
                reservation_ttl_seconds
            )
            > 0.0
        )
        or not (
            float(
                reservation_ttl_seconds
            )
            < float("inf")
        )
    ):
        return finish(
            UNKNOWN,
            "INVALID_RESERVATION_TTL",
            protected_cash=(
                protected_cash_lamports
            ),
        )

    try:
        normalized_path = Path(
            db_path
        )
    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_DATABASE_PATH_INVALID",
            protected_cash=(
                protected_cash_lamports
            ),
        )

    # --------------------------------------------------------
    # Authoritative account state.
    #
    # These are EXIT-mark assumptions, not proposed-BUY
    # execution assumptions.
    # --------------------------------------------------------

    try:
        account_result = await (
            resolve_live_account_risk_state(
                wallet_pubkey=(
                    normalized_wallet
                ),
                slippage_bps=(
                    exit_slippage_bps
                ),
                base_network_fee_lamports=(
                    exit_base_network_fee_lamports
                ),
                priority_fee_lamports=(
                    exit_priority_fee_lamports
                ),
                min_context_slot=(
                    min_context_slot
                ),
                db_path=normalized_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_ACCOUNT_RISK_STATE_FAILED",
            protected_cash=(
                protected_cash_lamports
            ),
        )

    if (
        getattr(
            account_result,
            "resolver_version",
            None,
        )
        != LIVE_ACCOUNT_RISK_STATE_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIVE_ACCOUNT_RISK_STATE_VERSION_MISMATCH",
            protected_cash=(
                protected_cash_lamports
            ),
            account_state=(
                account_result
            ),
        )

    if (
        getattr(
            account_result,
            "status",
            None,
        )
        != ACCOUNT_STATE_RESOLVED
    ):
        reasons = tuple(
            getattr(
                account_result,
                "reasons",
                (),
            )
            or ()
        )

        return finish(
            UNKNOWN,
            "LIVE_ACCOUNT_RISK_STATE_NOT_RESOLVED",
            *(
                "LIVE_ACCOUNT_RISK_STATE:"
                + str(reason)
                for reason in reasons
            ),
            protected_cash=(
                protected_cash_lamports
            ),
            account_state=(
                account_result
            ),
        )

    if (
        getattr(
            account_result,
            "wallet_pubkey",
            None,
        )
        != normalized_wallet
    ):
        return finish(
            UNKNOWN,
            "LIVE_ACCOUNT_RISK_STATE_WALLET_MISMATCH",
            protected_cash=(
                protected_cash_lamports
            ),
            account_state=(
                account_result
            ),
        )

    account = getattr(
        account_result,
        "account",
        None,
    )

    if account is None:
        return finish(
            UNKNOWN,
            "LIVE_ACCOUNT_RISK_STATE_MISSING",
            protected_cash=(
                protected_cash_lamports
            ),
            account_state=(
                account_result
            ),
        )

    # --------------------------------------------------------
    # Bind cash to the exact wallet valuation that produced
    # current equity. No separate wallet-balance RPC occurs
    # here.
    # --------------------------------------------------------

    valuation = getattr(
        account_result,
        "wallet_valuation",
        None,
    )

    if valuation is None:
        return finish(
            UNKNOWN,
            "LIVE_WALLET_VALUATION_MISSING",
            protected_cash=(
                protected_cash_lamports
            ),
            account_state=(
                account_result
            ),
        )

    if (
        getattr(
            valuation,
            "resolver_version",
            None,
        )
        != LIVE_WALLET_VALUATION_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIVE_WALLET_VALUATION_VERSION_MISMATCH",
            protected_cash=(
                protected_cash_lamports
            ),
            account_state=(
                account_result
            ),
        )

    if (
        getattr(
            valuation,
            "status",
            None,
        )
        != WALLET_VALUATION_RESOLVED
    ):
        return finish(
            UNKNOWN,
            "LIVE_WALLET_VALUATION_NOT_RESOLVED",
            protected_cash=(
                protected_cash_lamports
            ),
            account_state=(
                account_result
            ),
        )

    if (
        getattr(
            valuation,
            "wallet_pubkey",
            None,
        )
        != normalized_wallet
    ):
        return finish(
            UNKNOWN,
            "LIVE_WALLET_VALUATION_WALLET_MISMATCH",
            protected_cash=(
                protected_cash_lamports
            ),
            account_state=(
                account_result
            ),
        )

    raw_wallet_balance = getattr(
        valuation,
        "wallet_balance_lamports",
        None,
    )

    valuation_equity = getattr(
        valuation,
        "current_equity_lamports",
        None,
    )

    if not _strict_nonnegative_int(
        raw_wallet_balance
    ):
        return finish(
            UNKNOWN,
            "LIVE_WALLET_BALANCE_INVALID",
            protected_cash=(
                protected_cash_lamports
            ),
            account_state=(
                account_result
            ),
        )

    if (
        not _strict_nonnegative_int(
            valuation_equity
        )
        or getattr(
            account,
            "current_equity_lamports",
            None,
        )
        != valuation_equity
    ):
        return finish(
            UNKNOWN,
            "LIVE_ACCOUNT_EQUITY_BINDING_MISMATCH",
            raw_wallet_balance_lamports=(
                int(raw_wallet_balance)
            ),
            protected_cash=(
                protected_cash_lamports
            ),
            account_state=(
                account_result
            ),
        )

    raw_wallet_balance = int(
        raw_wallet_balance
    )

    if (
        protected_cash_lamports
        > raw_wallet_balance
    ):
        return finish(
            BLOCK,
            "PROTECTED_CASH_EXCEEDS_WALLET_BALANCE",
            raw_wallet_balance_lamports=(
                raw_wallet_balance
            ),
            protected_cash=(
                protected_cash_lamports
            ),
            available_cash_lamports=0,
            account_state=(
                account_result
            ),
        )

    available_cash = (
        raw_wallet_balance
        - protected_cash_lamports
    )

    if available_cash == 0:
        return finish(
            BLOCK,
            "NO_AVAILABLE_CASH_AFTER_PROTECTED_RESERVE",
            raw_wallet_balance_lamports=(
                raw_wallet_balance
            ),
            protected_cash=(
                protected_cash_lamports
            ),
            available_cash_lamports=0,
            account_state=(
                account_result
            ),
        )

    # --------------------------------------------------------
    # Reservation authority.
    #
    # IMPORTANT:
    # Do not subtract held reservations here.
    # reserve_pump_buy_capital() does that atomically.
    #
    # These are BUY execution assumptions, separate from the
    # EXIT-mark assumptions supplied above.
    # --------------------------------------------------------

    try:
        decision = (
            reserve_pump_buy_capital(
                mint=normalized_mint,
                wallet_pubkey=(
                    normalized_wallet
                ),
                available_cash_lamports=(
                    available_cash
                ),
                account=account,
                curve_state=curve_state,
                protocol_fee_bps=(
                    protocol_fee_bps
                ),
                creator_fee_bps=(
                    creator_fee_bps
                ),
                slippage_bps=(
                    buy_slippage_bps
                ),
                base_network_fee_lamports=(
                    buy_base_network_fee_lamports
                ),
                priority_fee_lamports=(
                    buy_priority_fee_lamports
                ),
                rent_lamports=(
                    buy_rent_lamports
                ),
                reservation_ttl_seconds=(
                    reservation_ttl_seconds
                ),
                policy=policy,
                db_path=normalized_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_CAPITAL_RESERVATION_FAILED",
            raw_wallet_balance_lamports=(
                raw_wallet_balance
            ),
            protected_cash=(
                protected_cash_lamports
            ),
            available_cash_lamports=(
                available_cash
            ),
            account_state=(
                account_result
            ),
        )

    decision_status = getattr(
        decision,
        "status",
        None,
    )

    decision_reasons = tuple(
        getattr(
            decision,
            "reasons",
            (),
        )
        or ()
    )

    if decision_status not in (
        PASS,
        BLOCK,
        UNKNOWN,
    ):
        return finish(
            UNKNOWN,
            "LIVE_CAPITAL_RESERVATION_STATUS_INVALID",
            raw_wallet_balance_lamports=(
                raw_wallet_balance
            ),
            protected_cash=(
                protected_cash_lamports
            ),
            available_cash_lamports=(
                available_cash
            ),
            account_state=(
                account_result
            ),
            reservation_decision=(
                decision
            ),
        )

    reservation = getattr(
        decision,
        "reservation",
        None,
    )

    if decision_status != PASS:
        if reservation is not None:
            return finish(
                UNKNOWN,
                "NON_PASS_RESERVATION_DECISION_HAS_RESERVATION",
                raw_wallet_balance_lamports=(
                    raw_wallet_balance
                ),
                protected_cash=(
                    protected_cash_lamports
                ),
                available_cash_lamports=(
                    available_cash
                ),
                account_state=(
                    account_result
                ),
                reservation_decision=(
                    decision
                ),
            )

        return finish(
            decision_status,
            *decision_reasons,
            raw_wallet_balance_lamports=(
                raw_wallet_balance
            ),
            protected_cash=(
                protected_cash_lamports
            ),
            available_cash_lamports=(
                available_cash
            ),
            account_state=(
                account_result
            ),
            reservation_decision=(
                decision
            ),
        )

    if reservation is None:
        return finish(
            UNKNOWN,
            "PASS_RESERVATION_DECISION_MISSING_RESERVATION",
            raw_wallet_balance_lamports=(
                raw_wallet_balance
            ),
            protected_cash=(
                protected_cash_lamports
            ),
            available_cash_lamports=(
                available_cash
            ),
            account_state=(
                account_result
            ),
            reservation_decision=(
                decision
            ),
        )

    if (
        getattr(
            reservation,
            "reservation_version",
            None,
        )
        != RESERVATION_VERSION
        or getattr(
            reservation,
            "status",
            None,
        )
        != ACTIVE
        or getattr(
            reservation,
            "wallet_pubkey",
            None,
        )
        != normalized_wallet
        or getattr(
            reservation,
            "mint",
            None,
        )
        != normalized_mint
        or getattr(
            reservation,
            "base_available_cash_lamports",
            None,
        )
        != available_cash
        or getattr(
            reservation,
            "base_open_exposure_lamports",
            None,
        )
        != account.open_exposure_lamports
        or getattr(
            reservation,
            "base_open_positions",
            None,
        )
        != account.open_positions
    ):
        return finish(
            UNKNOWN,
            "LIVE_CAPITAL_RESERVATION_BINDING_MISMATCH",
            raw_wallet_balance_lamports=(
                raw_wallet_balance
            ),
            protected_cash=(
                protected_cash_lamports
            ),
            available_cash_lamports=(
                available_cash
            ),
            account_state=(
                account_result
            ),
            reservation_decision=(
                decision
            ),
        )

    return finish(
        PASS,
        raw_wallet_balance_lamports=(
            raw_wallet_balance
        ),
        protected_cash=(
            protected_cash_lamports
        ),
        available_cash_lamports=(
            available_cash
        ),
        account_state=(
            account_result
        ),
        reservation_decision=(
            decision
        ),
    )
