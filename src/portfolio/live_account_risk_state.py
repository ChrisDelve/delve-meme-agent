from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from solders.pubkey import Pubkey

from src.portfolio.live_equity_continuity import (
    LIVE_EQUITY_CONTINUITY_VERSION,
    PASS as CONTINUITY_PASS,
    LiveEquityContinuityState,
    record_live_equity_continuity,
)
from src.portfolio.live_positions import (
    LIVE_POSITION_RISK_TOTALS_VERSION,
    PASS as RISK_TOTALS_PASS,
    LivePositionRiskTotalsResult,
    load_live_position_risk_totals_read_only,
)
from src.portfolio.live_wallet_valuation import (
    LIVE_WALLET_VALUATION_VERSION,
    RESOLVED as VALUATION_RESOLVED,
    LiveWalletValuationResult,
    resolve_live_wallet_valuation,
)
from src.risk.risk_governor import (
    AccountRiskState,
)


LIVE_ACCOUNT_RISK_STATE_VERSION = (
    "live-account-risk-state-v1"
)

RESOLVED = "RESOLVED"
UNKNOWN = "UNKNOWN"

BPS_DENOMINATOR = 10_000
U64_MAX = (1 << 64) - 1


@dataclass(frozen=True)
class LiveAccountRiskStateResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    wallet_pubkey: str

    account: AccountRiskState | None

    wallet_valuation: (
        LiveWalletValuationResult | None
    )

    continuity: (
        LiveEquityContinuityState | None
    )

    risk_totals_before: (
        LivePositionRiskTotalsResult | None
    )

    risk_totals_after: (
        LivePositionRiskTotalsResult | None
    )


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


def _strict_bps(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= BPS_DENOMINATOR
    )


def _risk_totals_contract_reason(
    result: Any,
    *,
    wallet_pubkey: str,
) -> str | None:
    if (
        getattr(
            result,
            "loader_version",
            None,
        )
        != LIVE_POSITION_RISK_TOTALS_VERSION
    ):
        return (
            "LIVE_POSITION_RISK_TOTALS_VERSION_MISMATCH"
        )

    if (
        getattr(
            result,
            "status",
            None,
        )
        != RISK_TOTALS_PASS
    ):
        return (
            "LIVE_POSITION_RISK_TOTALS_NOT_PASS"
        )

    if (
        getattr(
            result,
            "wallet_pubkey",
            None,
        )
        != wallet_pubkey
    ):
        return (
            "LIVE_POSITION_RISK_TOTALS_WALLET_MISMATCH"
        )

    exposure = getattr(
        result,
        "open_exposure_lamports",
        None,
    )

    positions = getattr(
        result,
        "open_positions",
        None,
    )

    if (
        not _strict_u64(
            exposure
        )
        or not _strict_u64(
            positions
        )
    ):
        return (
            "LIVE_POSITION_RISK_TOTALS_INVALID"
        )

    return None


async def resolve_live_account_risk_state(
    *,
    wallet_pubkey: str,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    min_context_slot: int | None = None,
) -> LiveAccountRiskStateResult:
    """
    Resolve the authoritative live AccountRiskState
    consumed by the portfolio risk governor.

    Sources:
    - current equity:
        live wallet valuation
    - UTC day-start / lifetime high-water:
        live equity continuity ledger
    - OPEN exposure / position count:
        live position risk totals

    Position risk totals bracket the wallet valuation.
    If those totals change, this call fails closed.

    This function performs no:
    - transaction construction
    - signing
    - submission
    - reservation
    - risk decision
    - direct SQL

    Its only mutation authority is recording a proven
    resolved wallet-equity observation into the
    continuity ledger after the risk-total bracket
    has remained stable.
    """

    normalized_wallet = ""

    def finish(
        status: str,
        *reasons: str,
        account: (
            AccountRiskState | None
        ) = None,
        wallet_valuation: (
            LiveWalletValuationResult | None
        ) = None,
        continuity: (
            LiveEquityContinuityState | None
        ) = None,
        risk_totals_before: (
            LivePositionRiskTotalsResult | None
        ) = None,
        risk_totals_after: (
            LivePositionRiskTotalsResult | None
        ) = None,
    ) -> LiveAccountRiskStateResult:
        return LiveAccountRiskStateResult(
            resolver_version=(
                LIVE_ACCOUNT_RISK_STATE_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=(
                normalized_wallet
            ),
            account=account,
            wallet_valuation=(
                wallet_valuation
            ),
            continuity=continuity,
            risk_totals_before=(
                risk_totals_before
            ),
            risk_totals_after=(
                risk_totals_after
            ),
        )

    # --------------------------------------------------------
    # Input validation before any read/RPC/write authority.
    # --------------------------------------------------------

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

    if not _strict_bps(
        slippage_bps
    ):
        return finish(
            UNKNOWN,
            "INVALID_SLIPPAGE_BPS",
        )

    if not _strict_u64(
        base_network_fee_lamports
    ):
        return finish(
            UNKNOWN,
            "INVALID_BASE_NETWORK_FEE",
        )

    if not _strict_u64(
        priority_fee_lamports
    ):
        return finish(
            UNKNOWN,
            "INVALID_PRIORITY_FEE",
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
        )

    # --------------------------------------------------------
    # Risk totals BEFORE valuation.
    # --------------------------------------------------------

    try:
        risk_before = (
            load_live_position_risk_totals_read_only(
                wallet_pubkey=(
                    normalized_wallet
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_POSITION_RISK_TOTALS_LOAD_FAILED",
        )

    before_reason = (
        _risk_totals_contract_reason(
            risk_before,
            wallet_pubkey=(
                normalized_wallet
            ),
        )
    )

    if before_reason is not None:
        reasons = tuple(
            getattr(
                risk_before,
                "reasons",
                (),
            )
            or ()
        )

        return finish(
            UNKNOWN,
            before_reason,
            *(
                "LIVE_POSITION_RISK_TOTALS:"
                + str(reason)
                for reason in reasons
            ),
            risk_totals_before=(
                risk_before
            ),
        )

    # --------------------------------------------------------
    # Authoritative instantaneous wallet valuation.
    # --------------------------------------------------------

    try:
        valuation = await (
            resolve_live_wallet_valuation(
                wallet_pubkey=(
                    normalized_wallet
                ),
                slippage_bps=(
                    slippage_bps
                ),
                base_network_fee_lamports=(
                    base_network_fee_lamports
                ),
                priority_fee_lamports=(
                    priority_fee_lamports
                ),
                min_context_slot=(
                    min_context_slot
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_WALLET_VALUATION_FAILED",
            risk_totals_before=(
                risk_before
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
            wallet_valuation=(
                valuation
            ),
            risk_totals_before=(
                risk_before
            ),
        )

    if (
        getattr(
            valuation,
            "status",
            None,
        )
        != VALUATION_RESOLVED
    ):
        reasons = tuple(
            getattr(
                valuation,
                "reasons",
                (),
            )
            or ()
        )

        return finish(
            UNKNOWN,
            "LIVE_WALLET_VALUATION_NOT_RESOLVED",
            *(
                "LIVE_WALLET_VALUATION:"
                + str(reason)
                for reason in reasons
            ),
            wallet_valuation=(
                valuation
            ),
            risk_totals_before=(
                risk_before
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
            wallet_valuation=(
                valuation
            ),
            risk_totals_before=(
                risk_before
            ),
        )

    current_equity = getattr(
        valuation,
        "current_equity_lamports",
        None,
    )

    balance_rpc_slot = getattr(
        valuation,
        "wallet_balance_rpc_slot",
        None,
    )

    valuation_open_lots = getattr(
        valuation,
        "open_position_lots",
        None,
    )

    if not _strict_positive_u64(
        current_equity
    ):
        return finish(
            UNKNOWN,
            "LIVE_ACCOUNT_CURRENT_EQUITY_INVALID",
            wallet_valuation=(
                valuation
            ),
            risk_totals_before=(
                risk_before
            ),
        )

    if not _strict_u64(
        balance_rpc_slot
    ):
        return finish(
            UNKNOWN,
            "LIVE_ACCOUNT_WALLET_BALANCE_SLOT_INVALID",
            wallet_valuation=(
                valuation
            ),
            risk_totals_before=(
                risk_before
            ),
        )

    if not _strict_u64(
        valuation_open_lots
    ):
        return finish(
            UNKNOWN,
            "LIVE_ACCOUNT_OPEN_POSITION_COUNT_INVALID",
            wallet_valuation=(
                valuation
            ),
            risk_totals_before=(
                risk_before
            ),
        )

    # --------------------------------------------------------
    # Risk totals AFTER valuation.
    # --------------------------------------------------------

    try:
        risk_after = (
            load_live_position_risk_totals_read_only(
                wallet_pubkey=(
                    normalized_wallet
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "FINAL_LIVE_POSITION_RISK_TOTALS_LOAD_FAILED",
            wallet_valuation=(
                valuation
            ),
            risk_totals_before=(
                risk_before
            ),
        )

    after_reason = (
        _risk_totals_contract_reason(
            risk_after,
            wallet_pubkey=(
                normalized_wallet
            ),
        )
    )

    if after_reason is not None:
        reasons = tuple(
            getattr(
                risk_after,
                "reasons",
                (),
            )
            or ()
        )

        return finish(
            UNKNOWN,
            "FINAL_" + after_reason,
            *(
                "FINAL_LIVE_POSITION_RISK_TOTALS:"
                + str(reason)
                for reason in reasons
            ),
            wallet_valuation=(
                valuation
            ),
            risk_totals_before=(
                risk_before
            ),
            risk_totals_after=(
                risk_after
            ),
        )

    before_exposure = int(
        risk_before.open_exposure_lamports
    )

    before_positions = int(
        risk_before.open_positions
    )

    after_exposure = int(
        risk_after.open_exposure_lamports
    )

    after_positions = int(
        risk_after.open_positions
    )

    if (
        before_exposure
        != after_exposure
        or before_positions
        != after_positions
    ):
        return finish(
            UNKNOWN,
            "LIVE_POSITION_RISK_CHANGED_DURING_VALUATION",
            wallet_valuation=(
                valuation
            ),
            risk_totals_before=(
                risk_before
            ),
            risk_totals_after=(
                risk_after
            ),
        )

    if (
        valuation_open_lots
        != after_positions
    ):
        return finish(
            UNKNOWN,
            "VALUATION_POSITION_COUNT_MISMATCH",
            wallet_valuation=(
                valuation
            ),
            risk_totals_before=(
                risk_before
            ),
            risk_totals_after=(
                risk_after
            ),
        )

    # --------------------------------------------------------
    # Persist continuity only AFTER the live portfolio-risk
    # bracket has remained stable.
    # --------------------------------------------------------

    try:
        continuity_result = (
            record_live_equity_continuity(
                wallet_pubkey=(
                    normalized_wallet
                ),
                valuation_version=(
                    LIVE_WALLET_VALUATION_VERSION
                ),
                current_equity_lamports=(
                    current_equity
                ),
                wallet_balance_rpc_slot=(
                    balance_rpc_slot
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_EQUITY_CONTINUITY_FAILED",
            wallet_valuation=(
                valuation
            ),
            risk_totals_before=(
                risk_before
            ),
            risk_totals_after=(
                risk_after
            ),
        )

    if (
        getattr(
            continuity_result,
            "status",
            None,
        )
        != CONTINUITY_PASS
    ):
        reasons = tuple(
            getattr(
                continuity_result,
                "reasons",
                (),
            )
            or ()
        )

        return finish(
            UNKNOWN,
            "LIVE_EQUITY_CONTINUITY_NOT_PASS",
            *(
                "LIVE_EQUITY_CONTINUITY:"
                + str(reason)
                for reason in reasons
            ),
            wallet_valuation=(
                valuation
            ),
            continuity=getattr(
                continuity_result,
                "state",
                None,
            ),
            risk_totals_before=(
                risk_before
            ),
            risk_totals_after=(
                risk_after
            ),
        )

    continuity = getattr(
        continuity_result,
        "state",
        None,
    )

    if continuity is None:
        return finish(
            UNKNOWN,
            "LIVE_EQUITY_CONTINUITY_STATE_MISSING",
            wallet_valuation=(
                valuation
            ),
            risk_totals_before=(
                risk_before
            ),
            risk_totals_after=(
                risk_after
            ),
        )

    if (
        getattr(
            continuity,
            "continuity_version",
            None,
        )
        != LIVE_EQUITY_CONTINUITY_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIVE_EQUITY_CONTINUITY_VERSION_MISMATCH",
            wallet_valuation=(
                valuation
            ),
            continuity=continuity,
            risk_totals_before=(
                risk_before
            ),
            risk_totals_after=(
                risk_after
            ),
        )

    if (
        getattr(
            continuity,
            "wallet_pubkey",
            None,
        )
        != normalized_wallet
    ):
        return finish(
            UNKNOWN,
            "LIVE_EQUITY_CONTINUITY_WALLET_MISMATCH",
            wallet_valuation=(
                valuation
            ),
            continuity=continuity,
            risk_totals_before=(
                risk_before
            ),
            risk_totals_after=(
                risk_after
            ),
        )

    day_start_equity = getattr(
        continuity,
        "day_start_equity_lamports",
        None,
    )

    high_water_equity = getattr(
        continuity,
        "high_water_equity_lamports",
        None,
    )

    latest_equity = getattr(
        continuity,
        "latest_equity_lamports",
        None,
    )

    continuity_valuation_version = getattr(
        continuity,
        "latest_valuation_version",
        None,
    )

    continuity_balance_slot = getattr(
        continuity,
        "latest_wallet_balance_rpc_slot",
        None,
    )

    if (
        not _strict_positive_u64(
            day_start_equity
        )
        or not _strict_positive_u64(
            high_water_equity
        )
        or not _strict_positive_u64(
            latest_equity
        )
    ):
        return finish(
            UNKNOWN,
            "LIVE_EQUITY_CONTINUITY_EQUITY_INVALID",
            wallet_valuation=(
                valuation
            ),
            continuity=continuity,
            risk_totals_before=(
                risk_before
            ),
            risk_totals_after=(
                risk_after
            ),
        )

    if (
        latest_equity
        != current_equity
        or continuity_valuation_version
        != LIVE_WALLET_VALUATION_VERSION
        or continuity_balance_slot
        != balance_rpc_slot
    ):
        return finish(
            UNKNOWN,
            "LIVE_EQUITY_CONTINUITY_BINDING_MISMATCH",
            wallet_valuation=(
                valuation
            ),
            continuity=continuity,
            risk_totals_before=(
                risk_before
            ),
            risk_totals_after=(
                risk_after
            ),
        )

    if (
        high_water_equity
        < current_equity
        or high_water_equity
        < day_start_equity
    ):
        return finish(
            UNKNOWN,
            "LIVE_EQUITY_CONTINUITY_HIGH_WATER_INVALID",
            wallet_valuation=(
                valuation
            ),
            continuity=continuity,
            risk_totals_before=(
                risk_before
            ),
            risk_totals_after=(
                risk_after
            ),
        )

    # --------------------------------------------------------
    # Final authoritative risk state.
    #
    # Held ACTIVE/SIGNED/SUBMITTED reservations are NOT added
    # here. reserve_pump_buy_capital() already adds those
    # liabilities internally.
    # --------------------------------------------------------

    account = AccountRiskState(
        current_equity_lamports=int(
            current_equity
        ),
        day_start_equity_lamports=int(
            day_start_equity
        ),
        high_water_equity_lamports=int(
            high_water_equity
        ),
        open_exposure_lamports=int(
            after_exposure
        ),
        open_positions=int(
            after_positions
        ),
    )

    return finish(
        RESOLVED,
        account=account,
        wallet_valuation=(
            valuation
        ),
        continuity=continuity,
        risk_totals_before=(
            risk_before
        ),
        risk_totals_after=(
            risk_after
        ),
    )
