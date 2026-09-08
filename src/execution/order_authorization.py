from __future__ import annotations

import math
import time
from pathlib import Path
from dataclasses import (
    dataclass,
)

from src.execution.execution_quality_gate import (
    ExecutionQualityResult,
)
from src.execution.live_curve_state import (
    LivePumpCurveState,
)
from src.execution.simulation_fingerprint import (
    simulation_fingerprint,
)

from src.portfolio.live_reservations import (
    ACTIVE,
    DB_PATH as LIVE_RESERVATION_DB_PATH,
    RESERVATION_VERSION,
    load_capital_reservation,
)
from src.safety.token_safety_gate import (
    SOL_QUOTE_MINT,
    TokenSafetyGateResult,
)


AUTHORIZATION_VERSION = "order-authorization-v4"

AUTHORIZE = "AUTHORIZE"
DENY = "DENY"

BUY = "BUY"

WRAPPED_SOL_MINT = (
    "So11111111111111111111111111111111111111112"
)

U64_MAX = (1 << 64) - 1


@dataclass(frozen=True)
class OrderAuthorization:
    """
    Immutable authority boundary between Delve's
    analytical pipeline and any future transaction
    builder.

    This object does NOT build, sign, or submit a
    transaction and does NOT itself enable live
    capital.
    """

    authorization_version: str

    status: str
    reasons: tuple[str, ...]

    mint: str
    side: str

    wallet_pubkey: str | None

    bonding_curve: str | None
    associated_bonding_curve: str | None
    base_token_program: str | None

    creator: str | None
    mayhem_mode: bool | None

    quote_mint_for_instruction: str | None

    token_amount: int | None
    max_sol_cost: int | None

    spend_lamports: int
    wallet_cost_lamports: int | None

    base_network_fee_lamports: int | None
    priority_fee_lamports: int | None
    rent_lamports: int | None

    reservation_id: str
    reservation_version: str | None
    reservation_expires_at: float | None

    curve_rpc_slot: int
    live_curve_fetched_at: float
    live_curve_age_seconds: float | None

    safety_gate_version: str
    execution_gate_version: str
    risk_governor_version: str | None

    simulation_sha256: str | None

    created_at: float
    expires_at: float

    def is_valid(
        self,
    ) -> bool:
        now = time.time()

        return (
            self.status == AUTHORIZE
            and now < self.expires_at
        )


def authorize_pump_buy(
    *,
    mint: str,
    requested_spend_lamports: int,
    reservation_id: str,
    live_curve: LivePumpCurveState,
    safety: TokenSafetyGateResult,
    execution: ExecutionQualityResult,
    max_authorization_age_seconds: float,
    reservation_db_path: Path = (
        LIVE_RESERVATION_DB_PATH
    ),
) -> OrderAuthorization:
    """
    Combine already-produced safety, execution,
    and risk evidence into one immutable proposed-
    order authorization.

    This function intentionally does not repeat
    those components' internal policies.
    """

    now = time.time()

    reasons: list[str] = []

    reservation_id = reservation_id.strip()

    reservation = None

    if not reservation_id:
        reasons.append(
            "INVALID_RESERVATION_ID"
        )

    else:
        reservation_load_failed = False

        try:
            reservation = (
                load_capital_reservation(
                    reservation_id=(
                        reservation_id
                    ),
                    now=now,
                    db_path=(
                        reservation_db_path
                    ),
                )
            )

        except Exception:
            reservation_load_failed = True

            reasons.append(
                "RESERVATION_LOAD_FAILED"
            )

        if (
            reservation is None
            and not reservation_load_failed
        ):
            reasons.append(
                "RESERVATION_NOT_FOUND"
            )

    if not mint:
        reasons.append(
            "INVALID_MINT"
        )

    if requested_spend_lamports <= 0:
        reasons.append(
            "INVALID_SPEND"
        )

    if (
        not math.isfinite(
            max_authorization_age_seconds
        )
        or max_authorization_age_seconds <= 0
    ):
        reasons.append(
            "INVALID_AUTHORIZATION_AGE"
        )

    #
    # Bind authorization to the actual live curve
    # state rather than trusting a caller-supplied
    # slot or previously calculated age.
    #
    if live_curve.mint != mint:
        reasons.append(
            "LIVE_CURVE_MINT_MISMATCH"
        )

    if live_curve.rpc_slot <= 0:
        reasons.append(
            "INVALID_CURVE_RPC_SLOT"
        )

    live_curve_age: float | None = None

    if (
        not math.isfinite(
            live_curve.fetched_at
        )
        or live_curve.fetched_at <= 0
    ):
        reasons.append(
            "INVALID_LIVE_CURVE_FETCH_TIME"
        )

    elif live_curve.fetched_at > now:
        reasons.append(
            "LIVE_CURVE_FETCH_TIME_IN_FUTURE"
        )

    else:
        live_curve_age = (
            now
            - live_curve.fetched_at
        )

        if (
            live_curve_age
            > max_authorization_age_seconds
        ):
            reasons.append(
                "LIVE_CURVE_STATE_TOO_OLD"
            )

    #
    # Capital reservation authority.
    #
    if reservation is not None:
        if (
            reservation.reservation_version
            != RESERVATION_VERSION
        ):
            reasons.append(
                "RESERVATION_VERSION_MISMATCH"
            )

        if not reservation.wallet_pubkey:
            reasons.append(
                "RESERVATION_WALLET_MISSING"
            )

        if reservation.status != ACTIVE:
            reasons.append(
                "RESERVATION_NOT_ACTIVE"
            )

        if reservation.mint != mint:
            reasons.append(
                "RESERVATION_MINT_MISMATCH"
            )

        if reservation.side != BUY:
            reasons.append(
                "RESERVATION_SIDE_MISMATCH"
            )

        if (
            reservation.spend_lamports
            != requested_spend_lamports
        ):
            reasons.append(
                "RESERVATION_SPEND_MISMATCH"
            )

        if (
            reservation.wallet_cost_lamports
            <= 0
        ):
            reasons.append(
                "INVALID_RESERVATION_WALLET_COST"
            )

        if reservation.created_at > now:
            reasons.append(
                "RESERVATION_CREATED_IN_FUTURE"
            )

        if reservation.expires_at <= now:
            reasons.append(
                "RESERVATION_EXPIRED"
            )

        if (
            reservation.signed_at is not None
            or reservation.transaction_signature
            is not None
        ):
            reasons.append(
                "RESERVATION_ALREADY_BOUND"
            )

    wallet_pubkey = (
        None
        if reservation is None
        else reservation.wallet_pubkey
    )

    bonding_curve: str | None = None
    associated_bonding_curve: str | None = None
    base_token_program: str | None = None

    creator: str | None = None
    mayhem_mode: bool | None = None

    quote_mint_for_instruction: (
        str | None
    ) = None

    #
    # Safety authority.
    #
    if not safety.allows_trade:
        reasons.append(
            "TOKEN_SAFETY_NOT_PASS"
        )

    if safety.snapshot is None:
        reasons.append(
            "TOKEN_SAFETY_SNAPSHOT_MISSING"
        )

    if safety.mint != mint:
        reasons.append(
            "SAFETY_MINT_MISMATCH"
        )

    if safety.snapshot is not None:
        if safety.snapshot.mint != mint:
            reasons.append(
                "SAFETY_SNAPSHOT_MINT_MISMATCH"
            )

        if (
            live_curve.rpc_slot
            < safety.snapshot.rpc_max_slot
        ):
            reasons.append(
                "CURVE_STATE_PREDATES_SAFETY"
            )

        snapshot_curve = getattr(
            safety.snapshot,
            "bonding_curve",
            None,
        )

        associated_bonding_curve = getattr(
            safety.snapshot,
            "associated_bonding_curve",
            None,
        )

        base_token_program = getattr(
            safety.snapshot,
            "token_program",
            None,
        )

        if not associated_bonding_curve:
            reasons.append(
                "ASSOCIATED_BONDING_CURVE_MISSING"
            )

        if not base_token_program:
            reasons.append(
                "BASE_TOKEN_PROGRAM_MISSING"
            )

        if snapshot_curve is None:
            reasons.append(
                "SAFETY_BONDING_CURVE_MISSING"
            )

        else:
            safety_curve_address = getattr(
                snapshot_curve,
                "address",
                None,
            )

            live_curve_address = getattr(
                live_curve.curve,
                "address",
                None,
            )

            if not safety_curve_address:
                reasons.append(
                    "SAFETY_BONDING_CURVE_ADDRESS_MISSING"
                )

            if not live_curve_address:
                reasons.append(
                    "LIVE_BONDING_CURVE_ADDRESS_MISSING"
                )

            if (
                safety_curve_address
                and live_curve_address
                and safety_curve_address
                != live_curve_address
            ):
                reasons.append(
                    "SAFETY_LIVE_CURVE_ADDRESS_MISMATCH"
                )

            if (
                safety_curve_address
                and live_curve_address
                and safety_curve_address
                == live_curve_address
            ):
                bonding_curve = str(
                    live_curve_address
                )

            safety_creator = getattr(
                snapshot_curve,
                "creator",
                None,
            )

            live_creator = getattr(
                live_curve.curve,
                "creator",
                None,
            )

            if not live_creator:
                reasons.append(
                    "LIVE_CURVE_CREATOR_MISSING"
                )

            if safety_creator != live_creator:
                reasons.append(
                    "SAFETY_LIVE_CURVE_CREATOR_MISMATCH"
                )

            if live_creator:
                creator = str(
                    live_creator
                )

            safety_mayhem = getattr(
                snapshot_curve,
                "is_mayhem_mode",
                None,
            )

            live_mayhem = getattr(
                live_curve.curve,
                "is_mayhem_mode",
                None,
            )

            if (
                not isinstance(
                    safety_mayhem,
                    bool,
                )
                or not isinstance(
                    live_mayhem,
                    bool,
                )
            ):
                reasons.append(
                    "INVALID_MAYHEM_MODE"
                )

            elif safety_mayhem != live_mayhem:
                reasons.append(
                    "SAFETY_LIVE_CURVE_MAYHEM_MISMATCH"
                )

            else:
                mayhem_mode = live_mayhem

            safety_quote_mint = getattr(
                snapshot_curve,
                "quote_mint",
                None,
            )

            live_quote_mint = getattr(
                live_curve.curve,
                "quote_mint",
                None,
            )

            if (
                safety_quote_mint
                not in (
                    None,
                    SOL_QUOTE_MINT,
                )
                or live_quote_mint
                not in (
                    None,
                    SOL_QUOTE_MINT,
                )
            ):
                reasons.append(
                    "NON_SOL_AUTHORIZATION_QUOTE_MINT"
                )

            else:
                quote_mint_for_instruction = (
                    WRAPPED_SOL_MINT
                )

    #
    # Execution-quality authority.
    #
    if not execution.allows_order_build:
        reasons.append(
            "EXECUTION_QUALITY_NOT_PASS"
        )

    if execution.mint != mint:
        reasons.append(
            "EXECUTION_MINT_MISMATCH"
        )

    if (
        execution.spendable_quote_in
        != requested_spend_lamports
    ):
        reasons.append(
            "EXECUTION_SPEND_MISMATCH"
        )

    simulation = execution.simulation

    if simulation is None:
        reasons.append(
            "EXECUTION_SIMULATION_MISSING"
        )

    #
    # Bind execution simulation to the exact live
    # curve snapshot.
    #
    simulation_sha256: str | None = None

    token_amount: int | None = None
    max_sol_cost: int | None = None

    base_network_fee_lamports: (
        int | None
    ) = None

    priority_fee_lamports: (
        int | None
    ) = None

    rent_lamports: (
        int | None
    ) = None

    if simulation is not None:
        if not simulation.executable:
            reasons.append(
                "SIMULATION_NOT_EXECUTABLE"
            )

        raw_token_amount = getattr(
            simulation,
            "tokens_out",
            None,
        )

        if (
            not isinstance(
                raw_token_amount,
                int,
            )
            or isinstance(
                raw_token_amount,
                bool,
            )
            or raw_token_amount <= 0
            or raw_token_amount > U64_MAX
        ):
            reasons.append(
                "AUTHORIZED_TOKEN_AMOUNT_OUT_OF_RANGE"
            )

        else:
            token_amount = raw_token_amount

        raw_max_sol_cost = getattr(
            simulation,
            "spendable_quote_in",
            None,
        )

        if (
            not isinstance(
                raw_max_sol_cost,
                int,
            )
            or isinstance(
                raw_max_sol_cost,
                bool,
            )
            or raw_max_sol_cost <= 0
            or raw_max_sol_cost > U64_MAX
        ):
            reasons.append(
                "MAX_SOL_COST_OUT_OF_RANGE"
            )

        else:
            max_sol_cost = raw_max_sol_cost

        raw_base_network_fee = getattr(
            simulation,
            "base_network_fee_lamports",
            None,
        )

        raw_priority_fee = getattr(
            simulation,
            "priority_fee_lamports",
            None,
        )

        raw_rent = getattr(
            simulation,
            "rent_lamports",
            None,
        )

        overhead_components = (
            (
                "BASE_NETWORK_FEE_OUT_OF_RANGE",
                raw_base_network_fee,
            ),
            (
                "PRIORITY_FEE_OUT_OF_RANGE",
                raw_priority_fee,
            ),
            (
                "RENT_LAMPORTS_OUT_OF_RANGE",
                raw_rent,
            ),
        )

        overhead_values: list[int] = []

        for (
            reason,
            value,
        ) in overhead_components:
            if (
                not isinstance(
                    value,
                    int,
                )
                or isinstance(
                    value,
                    bool,
                )
                or value < 0
                or value > U64_MAX
            ):
                reasons.append(
                    reason
                )

            else:
                overhead_values.append(
                    value
                )

        if len(
            overhead_values
        ) == 3:
            base_network_fee_lamports = (
                raw_base_network_fee
            )

            priority_fee_lamports = (
                raw_priority_fee
            )

            rent_lamports = (
                raw_rent
            )

            raw_total_overhead = getattr(
                simulation,
                (
                    "total_transaction_"
                    "overhead_lamports"
                ),
                None,
            )

            if (
                not isinstance(
                    raw_total_overhead,
                    int,
                )
                or isinstance(
                    raw_total_overhead,
                    bool,
                )
                or raw_total_overhead < 0
                or raw_total_overhead > U64_MAX
            ):
                reasons.append(
                    "TOTAL_TRANSACTION_OVERHEAD_OUT_OF_RANGE"
                )

            elif (
                (
                    base_network_fee_lamports
                    + priority_fee_lamports
                    + rent_lamports
                )
                != raw_total_overhead
            ):
                reasons.append(
                    "TRANSACTION_OVERHEAD_COMPONENT_SUM_MISMATCH"
                )

        if (
            simulation.spendable_quote_in
            != requested_spend_lamports
        ):
            reasons.append(
                "SIMULATION_SPEND_MISMATCH"
            )

        if (
            reservation is not None
            and simulation.total_wallet_cost_lamports
            != reservation.wallet_cost_lamports
        ):
            reasons.append(
                "SIMULATION_WALLET_COST_MISMATCH"
            )

        curve = live_curve.curve

        if (
            simulation.pre_virtual_quote_reserves
            != curve.virtual_quote_reserves
            or simulation.pre_virtual_token_reserves
            != curve.virtual_token_reserves
            or simulation.pre_real_quote_reserves
            != curve.real_quote_reserves
            or simulation.pre_real_token_reserves
            != curve.real_token_reserves
        ):
            reasons.append(
                "SIMULATION_LIVE_CURVE_MISMATCH"
            )

        try:
            simulation_sha256 = (
                simulation_fingerprint(
                    simulation
                )
            )

        except Exception:
            reasons.append(
                "SIMULATION_FINGERPRINT_FAILED"
            )

    #
    # Bind current execution economics to the exact
    # risk-approved simulation persisted when this
    # capital reservation was created.
    #
    if (
        reservation is not None
        and simulation_sha256 is not None
        and simulation_sha256
        != reservation.risk_simulation_sha256
    ):
        reasons.append(
            "RESERVATION_EXECUTION_SIMULATION_MISMATCH"
        )

    reasons = list(
        dict.fromkeys(
            reasons
        )
    )

    if reasons:
        status = DENY
        expires_at = now

    else:
        status = AUTHORIZE

        assert reservation is not None

        #
        # Expiry is anchored to when the underlying
        # curve state was fetched — not when this
        # authorization object happened to be made.
        #
        expires_at = min(
            (
                live_curve.fetched_at
                + max_authorization_age_seconds
            ),
            reservation.expires_at,
        )

    return OrderAuthorization(
        authorization_version=(
            AUTHORIZATION_VERSION
        ),
        status=status,
        reasons=tuple(
            reasons
        ),
        mint=mint,
        side=BUY,
        wallet_pubkey=wallet_pubkey,
        bonding_curve=bonding_curve,
        associated_bonding_curve=(
            associated_bonding_curve
        ),
        base_token_program=(
            base_token_program
        ),
        creator=creator,
        mayhem_mode=mayhem_mode,
        quote_mint_for_instruction=(
            quote_mint_for_instruction
        ),
        token_amount=token_amount,
        max_sol_cost=max_sol_cost,
        spend_lamports=(
            requested_spend_lamports
        ),
        wallet_cost_lamports=(
            None
            if reservation is None
            else reservation.wallet_cost_lamports
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
        reservation_id=reservation_id,
        reservation_version=(
            None
            if reservation is None
            else reservation.reservation_version
        ),
        reservation_expires_at=(
            None
            if reservation is None
            else reservation.expires_at
        ),
        curve_rpc_slot=int(
            live_curve.rpc_slot
        ),
        live_curve_fetched_at=float(
            live_curve.fetched_at
        ),
        live_curve_age_seconds=(
            live_curve_age
        ),
        safety_gate_version=(
            safety.gate_version
        ),
        execution_gate_version=(
            execution.gate_version
        ),
        risk_governor_version=(
            None
            if reservation is None
            else reservation.risk_governor_version
        ),
        simulation_sha256=(
            simulation_sha256
        ),
        created_at=now,
        expires_at=expires_at,
    )
