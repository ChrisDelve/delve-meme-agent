from __future__ import annotations

from dataclasses import dataclass

from src.execution.pump_execution_simulator import (
    PumpBuySimulation,
    PumpCurveState,
    calculate_exact_input_buy,
)


BPS_DENOMINATOR = 10_000

RISK_GOVERNOR_VERSION = "risk-governor-v1"

PASS = "PASS"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RiskPolicy:
    #
    # SHADOW POLICY — not live-capital authorization.
    #
    # 1% of current equity per new position.
    #
    max_trade_equity_bps: int = 100

    #
    # Maximum aggregate capital committed to
    # open memecoin positions.
    #
    max_total_exposure_bps: int = 500

    #
    # Account-level circuit breakers.
    #
    max_daily_loss_bps: int = 300
    max_drawdown_bps: int = 500

    max_open_positions: int = 5

    #
    # Avoid absurd dust trades.
    #
    min_trade_lamports: int = 1_000_000

    #
    # Independent of the execution gate:
    # sizing itself must not push curve impact
    # beyond this level.
    #
    max_size_price_impact_bps: float = 200.0


@dataclass(frozen=True)
class AccountRiskState:
    current_equity_lamports: int

    day_start_equity_lamports: int

    high_water_equity_lamports: int

    open_exposure_lamports: int

    open_positions: int


@dataclass(frozen=True)
class RiskGovernorResult:
    governor_version: str

    status: str
    reasons: tuple[str, ...]

    kill_switch: bool

    current_equity_lamports: int

    daily_loss_bps: float
    drawdown_bps: float

    per_trade_cap_lamports: int
    total_exposure_cap_lamports: int
    remaining_exposure_lamports: int

    liquidity_cap_lamports: int

    recommended_spend_lamports: int

    recommended_simulation: (
        PumpBuySimulation | None
    )

    @property
    def allows_new_position(self) -> bool:
        return (
            self.status == PASS
            and not self.kill_switch
            and self.recommended_spend_lamports > 0
        )


def loss_bps(
    *,
    reference: int,
    current: int,
) -> float:
    if reference <= 0:
        raise ValueError(
            "reference equity must be positive."
        )

    if current >= reference:
        return 0.0

    return (
        (
            reference
            - current
        )
        / reference
        * BPS_DENOMINATOR
    )


def validate_policy(
    policy: RiskPolicy,
) -> None:
    integer_bps_fields = (
        (
            "max_trade_equity_bps",
            policy.max_trade_equity_bps,
        ),
        (
            "max_total_exposure_bps",
            policy.max_total_exposure_bps,
        ),
        (
            "max_daily_loss_bps",
            policy.max_daily_loss_bps,
        ),
        (
            "max_drawdown_bps",
            policy.max_drawdown_bps,
        ),
    )

    for name, value in integer_bps_fields:
        if (
            value < 0
            or value > BPS_DENOMINATOR
        ):
            raise ValueError(
                f"{name} must be between "
                "0 and 10,000 bps."
            )

    if policy.max_open_positions <= 0:
        raise ValueError(
            "max_open_positions must be positive."
        )

    if policy.min_trade_lamports <= 0:
        raise ValueError(
            "min_trade_lamports must be positive."
        )

    if (
        policy.max_size_price_impact_bps
        < 0
    ):
        raise ValueError(
            "max_size_price_impact_bps "
            "cannot be negative."
        )


def validate_account_state(
    state: AccountRiskState,
) -> None:
    if state.current_equity_lamports <= 0:
        raise ValueError(
            "current_equity_lamports "
            "must be positive."
        )

    if (
        state.day_start_equity_lamports
        <= 0
    ):
        raise ValueError(
            "day_start_equity_lamports "
            "must be positive."
        )

    if (
        state.high_water_equity_lamports
        <= 0
    ):
        raise ValueError(
            "high_water_equity_lamports "
            "must be positive."
        )

    if state.open_exposure_lamports < 0:
        raise ValueError(
            "open_exposure_lamports "
            "cannot be negative."
        )

    if state.open_positions < 0:
        raise ValueError(
            "open_positions cannot be negative."
        )


def simulate_size(
    *,
    state: PumpCurveState,
    spend_lamports: int,
    protocol_fee_bps: int,
    creator_fee_bps: int,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    rent_lamports: int,
) -> PumpBuySimulation:
    return calculate_exact_input_buy(
        state=state,

        spendable_quote_in=(
            spend_lamports
        ),

        protocol_fee_bps=(
            protocol_fee_bps
        ),

        creator_fee_bps=(
            creator_fee_bps
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

        rent_lamports=(
            rent_lamports
        ),
    )


def find_liquidity_cap(
    *,
    state: PumpCurveState,
    maximum_spend_lamports: int,
    minimum_spend_lamports: int,
    max_price_impact_bps: float,
    protocol_fee_bps: int,
    creator_fee_bps: int,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    rent_lamports: int,
) -> tuple[
    int,
    PumpBuySimulation | None,
]:
    """
    Find the largest integer-lamport order below
    maximum_spend_lamports that remains executable
    and below the sizing impact limit.

    Pump buy impact is monotonic enough for this
    bounded binary search under a fixed curve state.
    """

    if (
        maximum_spend_lamports
        < minimum_spend_lamports
    ):
        return (
            0,
            None,
        )

    low = minimum_spend_lamports
    high = maximum_spend_lamports

    best_spend = 0
    best_simulation = None

    while low <= high:
        midpoint = (
            low
            + high
        ) // 2

        simulation = simulate_size(
            state=state,

            spend_lamports=(
                midpoint
            ),

            protocol_fee_bps=(
                protocol_fee_bps
            ),

            creator_fee_bps=(
                creator_fee_bps
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

            rent_lamports=(
                rent_lamports
            ),
        )

        acceptable = (
            simulation.executable
            and simulation.price_impact_bps
            <= max_price_impact_bps
        )

        if acceptable:
            best_spend = midpoint
            best_simulation = simulation

            low = midpoint + 1

        else:
            high = midpoint - 1

    return (
        best_spend,
        best_simulation,
    )


def evaluate_risk(
    *,
    account: AccountRiskState,
    curve_state: PumpCurveState,
    protocol_fee_bps: int,
    creator_fee_bps: int,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    rent_lamports: int,
    policy: RiskPolicy | None = None,
) -> RiskGovernorResult:

    if policy is None:
        policy = RiskPolicy()

    try:
        validate_policy(
            policy
        )

        validate_account_state(
            account
        )

    except ValueError as error:
        return RiskGovernorResult(
            governor_version=(
                RISK_GOVERNOR_VERSION
            ),

            status=UNKNOWN,

            reasons=(
                f"INVALID_RISK_STATE:{error}",
            ),

            kill_switch=True,

            current_equity_lamports=(
                account.current_equity_lamports
            ),

            daily_loss_bps=0.0,
            drawdown_bps=0.0,

            per_trade_cap_lamports=0,
            total_exposure_cap_lamports=0,
            remaining_exposure_lamports=0,

            liquidity_cap_lamports=0,

            recommended_spend_lamports=0,

            recommended_simulation=None,
        )

    daily_loss = loss_bps(
        reference=(
            account.day_start_equity_lamports
        ),
        current=(
            account.current_equity_lamports
        ),
    )

    drawdown = loss_bps(
        reference=(
            account.high_water_equity_lamports
        ),
        current=(
            account.current_equity_lamports
        ),
    )

    block_reasons: list[str] = []

    kill_switch = False

    if (
        daily_loss
        >= policy.max_daily_loss_bps
    ):
        block_reasons.append(
            "DAILY_LOSS_LIMIT_REACHED"
        )

        kill_switch = True

    if (
        drawdown
        >= policy.max_drawdown_bps
    ):
        block_reasons.append(
            "DRAWDOWN_LIMIT_REACHED"
        )

        kill_switch = True

    if (
        account.open_positions
        >= policy.max_open_positions
    ):
        block_reasons.append(
            "MAX_OPEN_POSITIONS_REACHED"
        )

    per_trade_cap = (
        account.current_equity_lamports
        * policy.max_trade_equity_bps
        // BPS_DENOMINATOR
    )

    total_exposure_cap = (
        account.current_equity_lamports
        * policy.max_total_exposure_bps
        // BPS_DENOMINATOR
    )

    remaining_exposure = max(
        0,
        total_exposure_cap
        - account.open_exposure_lamports,
    )

    if remaining_exposure <= 0:
        block_reasons.append(
            "TOTAL_EXPOSURE_LIMIT_REACHED"
        )

    raw_trade_cap = min(
        per_trade_cap,
        remaining_exposure,
    )

    if (
        raw_trade_cap
        < policy.min_trade_lamports
    ):
        block_reasons.append(
            "RISK_BUDGET_BELOW_MINIMUM_ORDER"
        )

    if kill_switch:
        liquidity_cap = 0
        recommended_spend = 0
        recommended_simulation = None

    elif block_reasons:
        liquidity_cap = 0
        recommended_spend = 0
        recommended_simulation = None

    else:
        try:
            (
                liquidity_cap,
                recommended_simulation,
            ) = find_liquidity_cap(
                state=curve_state,

                maximum_spend_lamports=(
                    raw_trade_cap
                ),

                minimum_spend_lamports=(
                    policy.min_trade_lamports
                ),

                max_price_impact_bps=(
                    policy.max_size_price_impact_bps
                ),

                protocol_fee_bps=(
                    protocol_fee_bps
                ),

                creator_fee_bps=(
                    creator_fee_bps
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

                rent_lamports=(
                    rent_lamports
                ),
            )

        except Exception as error:
            return RiskGovernorResult(
                governor_version=(
                    RISK_GOVERNOR_VERSION
                ),

                status=UNKNOWN,

                reasons=(
                    "LIQUIDITY_SIZING_FAILED:"
                    f"{type(error).__name__}",
                ),

                kill_switch=False,

                current_equity_lamports=(
                    account.current_equity_lamports
                ),

                daily_loss_bps=(
                    daily_loss
                ),

                drawdown_bps=(
                    drawdown
                ),

                per_trade_cap_lamports=(
                    per_trade_cap
                ),

                total_exposure_cap_lamports=(
                    total_exposure_cap
                ),

                remaining_exposure_lamports=(
                    remaining_exposure
                ),

                liquidity_cap_lamports=0,

                recommended_spend_lamports=0,

                recommended_simulation=None,
            )

        recommended_spend = (
            liquidity_cap
        )

        if (
            recommended_spend
            < policy.min_trade_lamports
        ):
            block_reasons.append(
                "LIQUIDITY_CAP_BELOW_MINIMUM_ORDER"
            )

            recommended_spend = 0
            recommended_simulation = None

    block_reasons = list(
        dict.fromkeys(
            block_reasons
        )
    )

    if block_reasons:
        status = BLOCK

    else:
        status = PASS

    return RiskGovernorResult(
        governor_version=(
            RISK_GOVERNOR_VERSION
        ),

        status=status,

        reasons=tuple(
            block_reasons
        ),

        kill_switch=(
            kill_switch
        ),

        current_equity_lamports=(
            account.current_equity_lamports
        ),

        daily_loss_bps=(
            daily_loss
        ),

        drawdown_bps=(
            drawdown
        ),

        per_trade_cap_lamports=(
            per_trade_cap
        ),

        total_exposure_cap_lamports=(
            total_exposure_cap
        ),

        remaining_exposure_lamports=(
            remaining_exposure
        ),

        liquidity_cap_lamports=(
            liquidity_cap
        ),

        recommended_spend_lamports=(
            recommended_spend
        ),

        recommended_simulation=(
            recommended_simulation
        ),
    )