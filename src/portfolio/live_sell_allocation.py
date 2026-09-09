from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from solders.pubkey import Pubkey

from src.portfolio.live_positions import (
    LIVE_POSITION_VERSION,
    OPEN,
    LivePosition,
)


LIVE_SELL_ALLOCATION_VERSION = (
    "live-sell-allocation-v1"
)

FIFO_ENTRY_SLOT = "FIFO_ENTRY_SLOT"

PLANNED = "PLANNED"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"

U64_MAX = (1 << 64) - 1

I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1


@dataclass(frozen=True)
class LiveSellLotAllocation:
    position_id: int
    position_version: str

    entry_slot: int

    tokens_before: int
    tokens_to_sell: int
    tokens_after: int

    exposure_before_lamports: int
    exposure_reduction_lamports: int
    exposure_after_lamports: int

    cost_basis_before_lamports: int
    cost_basis_reduction_lamports: int
    cost_basis_after_lamports: int

    cumulative_net_proceeds_before_lamports: int
    cumulative_realized_pnl_before_lamports: int


@dataclass(frozen=True)
class LiveSellAllocationPlan:
    allocation_version: str
    allocation_method: str

    status: str
    reasons: tuple[str, ...]

    wallet_pubkey: str
    mint: str

    requested_tokens: int

    total_tokens_before: int | None
    total_tokens_after: int | None

    total_exposure_before_lamports: int | None
    total_exposure_reduction_lamports: int | None
    total_exposure_after_lamports: int | None

    total_cost_basis_before_lamports: int | None
    total_cost_basis_reduction_lamports: int | None
    total_cost_basis_after_lamports: int | None

    allocations: tuple[
        LiveSellLotAllocation,
        ...,
    ] | None


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


def _strict_i64(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and I64_MIN <= value <= I64_MAX
    )


def _add_u64(
    left: int,
    right: int,
) -> int | None:
    value = left + right

    if value > U64_MAX:
        return None

    return value


def _proportional_reduction(
    *,
    value_before: int,
    tokens_to_sell: int,
    tokens_before: int,
) -> int:
    if tokens_to_sell == tokens_before:
        return value_before

    return (
        value_before
        * tokens_to_sell
        // tokens_before
    )


def plan_live_sell_allocation(
    *,
    wallet_pubkey: str,
    mint: str,
    tokens_to_sell: int,
    positions: tuple[
        LivePosition,
        ...,
    ],
) -> LiveSellAllocationPlan:
    """
    Produce a deterministic accounting allocation for
    one aggregate wallet-level token SELL.

    Allocation method:
        FIFO by entry_slot, then position_id.

    This function performs no:
    - database read
    - database mutation
    - RPC
    - execution simulation
    - risk decision
    - transaction construction
    - signing
    - submission
    - realized-PnL calculation

    Actual proceeds are intentionally not allocated
    until a successful SELL fill has been proven.
    """

    normalized_wallet = ""
    normalized_mint = ""

    def finish(
        status: str,
        *reasons: str,
        total_tokens_before: int | None = None,
        total_tokens_after: int | None = None,
        total_exposure_before_lamports: int | None = None,
        total_exposure_reduction_lamports: int | None = None,
        total_exposure_after_lamports: int | None = None,
        total_cost_basis_before_lamports: int | None = None,
        total_cost_basis_reduction_lamports: int | None = None,
        total_cost_basis_after_lamports: int | None = None,
        allocations: tuple[
            LiveSellLotAllocation,
            ...,
        ] | None = None,
    ) -> LiveSellAllocationPlan:
        return LiveSellAllocationPlan(
            allocation_version=(
                LIVE_SELL_ALLOCATION_VERSION
            ),
            allocation_method=(
                FIFO_ENTRY_SLOT
            ),
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=normalized_wallet,
            mint=normalized_mint,
            requested_tokens=(
                tokens_to_sell
                if _strict_u64(
                    tokens_to_sell
                )
                else 0
            ),
            total_tokens_before=(
                total_tokens_before
            ),
            total_tokens_after=(
                total_tokens_after
            ),
            total_exposure_before_lamports=(
                total_exposure_before_lamports
            ),
            total_exposure_reduction_lamports=(
                total_exposure_reduction_lamports
            ),
            total_exposure_after_lamports=(
                total_exposure_after_lamports
            ),
            total_cost_basis_before_lamports=(
                total_cost_basis_before_lamports
            ),
            total_cost_basis_reduction_lamports=(
                total_cost_basis_reduction_lamports
            ),
            total_cost_basis_after_lamports=(
                total_cost_basis_after_lamports
            ),
            allocations=allocations,
        )

    # --------------------------------------------------------
    # Request contract
    # --------------------------------------------------------

    if not isinstance(
        wallet_pubkey,
        str,
    ):
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    wallet_pubkey = wallet_pubkey.strip()

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

    if not isinstance(
        mint,
        str,
    ):
        return finish(
            UNKNOWN,
            "INVALID_MINT",
        )

    mint = mint.strip()

    if not mint:
        return finish(
            UNKNOWN,
            "INVALID_MINT",
        )

    try:
        parsed_mint = Pubkey.from_string(
            mint
        )

    except Exception:
        return finish(
            UNKNOWN,
            "INVALID_MINT",
        )

    if parsed_mint == Pubkey.default():
        return finish(
            UNKNOWN,
            "INVALID_MINT",
        )

    normalized_mint = str(
        parsed_mint
    )

    if not _strict_positive_u64(
        tokens_to_sell
    ):
        return finish(
            UNKNOWN,
            "INVALID_TOKENS_TO_SELL",
        )

    if not isinstance(
        positions,
        tuple,
    ):
        return finish(
            UNKNOWN,
            "POSITIONS_NOT_TUPLE",
        )

    # --------------------------------------------------------
    # Validate and select exact OPEN lots.
    # --------------------------------------------------------

    eligible: list[LivePosition] = []

    total_tokens_before = 0
    total_exposure_before = 0
    total_cost_basis_before = 0

    seen_position_ids: set[int] = set()

    for position in positions:
        if not isinstance(
            position,
            LivePosition,
        ):
            return finish(
                UNKNOWN,
                "POSITION_TYPE_INVALID",
            )

        if (
            position.position_version
            != LIVE_POSITION_VERSION
        ):
            return finish(
                UNKNOWN,
                "POSITION_VERSION_MISMATCH",
            )

        if (
            position.wallet_pubkey
            != normalized_wallet
        ):
            return finish(
                UNKNOWN,
                "POSITION_WALLET_MISMATCH",
            )

        if position.status != OPEN:
            return finish(
                UNKNOWN,
                "POSITION_NOT_OPEN",
            )

        if not _strict_positive_u64(
            position.position_id
        ):
            return finish(
                UNKNOWN,
                "POSITION_ID_INVALID",
            )

        if (
            position.position_id
            in seen_position_ids
        ):
            return finish(
                UNKNOWN,
                "DUPLICATE_POSITION_ID",
            )

        seen_position_ids.add(
            position.position_id
        )

        if not _strict_u64(
            position.entry_slot
        ):
            return finish(
                UNKNOWN,
                "POSITION_ENTRY_SLOT_INVALID",
            )

        if not _strict_positive_u64(
            position.tokens_held
        ):
            return finish(
                UNKNOWN,
                "POSITION_TOKENS_INVALID",
            )

        if not _strict_u64(
            position.remaining_exposure_lamports
        ):
            return finish(
                UNKNOWN,
                "POSITION_EXPOSURE_INVALID",
            )

        if not _strict_u64(
            position.remaining_cost_basis_lamports
        ):
            return finish(
                UNKNOWN,
                "POSITION_COST_BASIS_INVALID",
            )

        if not _strict_u64(
            position.cumulative_net_proceeds_lamports
        ):
            return finish(
                UNKNOWN,
                "POSITION_PROCEEDS_INVALID",
            )

        if not _strict_i64(
            position.cumulative_realized_pnl_lamports
        ):
            return finish(
                UNKNOWN,
                "POSITION_REALIZED_PNL_INVALID",
            )

        if position.mint != normalized_mint:
            continue

        eligible.append(
            position
        )

        value = _add_u64(
            total_tokens_before,
            position.tokens_held,
        )

        if value is None:
            return finish(
                UNKNOWN,
                "TOTAL_TOKEN_INVENTORY_OVERFLOW",
            )

        total_tokens_before = value

        value = _add_u64(
            total_exposure_before,
            position.remaining_exposure_lamports,
        )

        if value is None:
            return finish(
                UNKNOWN,
                "TOTAL_EXPOSURE_OVERFLOW",
            )

        total_exposure_before = value

        value = _add_u64(
            total_cost_basis_before,
            position.remaining_cost_basis_lamports,
        )

        if value is None:
            return finish(
                UNKNOWN,
                "TOTAL_COST_BASIS_OVERFLOW",
            )

        total_cost_basis_before = value

    if not eligible:
        return finish(
            BLOCK,
            "NO_OPEN_POSITION_FOR_MINT",
            total_tokens_before=0,
            total_tokens_after=0,
            total_exposure_before_lamports=0,
            total_exposure_reduction_lamports=0,
            total_exposure_after_lamports=0,
            total_cost_basis_before_lamports=0,
            total_cost_basis_reduction_lamports=0,
            total_cost_basis_after_lamports=0,
            allocations=(),
        )

    if tokens_to_sell > total_tokens_before:
        return finish(
            BLOCK,
            "SELL_EXCEEDS_OPEN_INVENTORY",
            total_tokens_before=(
                total_tokens_before
            ),
            total_tokens_after=(
                total_tokens_before
            ),
            total_exposure_before_lamports=(
                total_exposure_before
            ),
            total_exposure_reduction_lamports=0,
            total_exposure_after_lamports=(
                total_exposure_before
            ),
            total_cost_basis_before_lamports=(
                total_cost_basis_before
            ),
            total_cost_basis_reduction_lamports=0,
            total_cost_basis_after_lamports=(
                total_cost_basis_before
            ),
            allocations=(),
        )

    # --------------------------------------------------------
    # FIFO allocation.
    # --------------------------------------------------------

    eligible.sort(
        key=lambda position: (
            position.entry_slot,
            position.position_id,
        )
    )

    remaining_to_sell = tokens_to_sell

    allocations: list[
        LiveSellLotAllocation
    ] = []

    total_exposure_reduction = 0
    total_cost_basis_reduction = 0

    for position in eligible:
        if remaining_to_sell == 0:
            break

        allocation_tokens = min(
            position.tokens_held,
            remaining_to_sell,
        )

        exposure_reduction = (
            _proportional_reduction(
                value_before=(
                    position
                    .remaining_exposure_lamports
                ),
                tokens_to_sell=(
                    allocation_tokens
                ),
                tokens_before=(
                    position.tokens_held
                ),
            )
        )

        cost_basis_reduction = (
            _proportional_reduction(
                value_before=(
                    position
                    .remaining_cost_basis_lamports
                ),
                tokens_to_sell=(
                    allocation_tokens
                ),
                tokens_before=(
                    position.tokens_held
                ),
            )
        )

        tokens_after = (
            position.tokens_held
            - allocation_tokens
        )

        exposure_after = (
            position
            .remaining_exposure_lamports
            - exposure_reduction
        )

        cost_basis_after = (
            position
            .remaining_cost_basis_lamports
            - cost_basis_reduction
        )

        allocations.append(
            LiveSellLotAllocation(
                position_id=(
                    position.position_id
                ),
                position_version=(
                    position.position_version
                ),
                entry_slot=int(
                    position.entry_slot
                ),
                tokens_before=int(
                    position.tokens_held
                ),
                tokens_to_sell=int(
                    allocation_tokens
                ),
                tokens_after=int(
                    tokens_after
                ),
                exposure_before_lamports=int(
                    position
                    .remaining_exposure_lamports
                ),
                exposure_reduction_lamports=int(
                    exposure_reduction
                ),
                exposure_after_lamports=int(
                    exposure_after
                ),
                cost_basis_before_lamports=int(
                    position
                    .remaining_cost_basis_lamports
                ),
                cost_basis_reduction_lamports=int(
                    cost_basis_reduction
                ),
                cost_basis_after_lamports=int(
                    cost_basis_after
                ),
                cumulative_net_proceeds_before_lamports=int(
                    position
                    .cumulative_net_proceeds_lamports
                ),
                cumulative_realized_pnl_before_lamports=int(
                    position
                    .cumulative_realized_pnl_lamports
                ),
            )
        )

        total_exposure_reduction += (
            exposure_reduction
        )

        total_cost_basis_reduction += (
            cost_basis_reduction
        )

        remaining_to_sell -= (
            allocation_tokens
        )

    if remaining_to_sell != 0:
        return finish(
            UNKNOWN,
            "SELL_ALLOCATION_INCOMPLETE",
        )

    total_tokens_after = (
        total_tokens_before
        - tokens_to_sell
    )

    total_exposure_after = (
        total_exposure_before
        - total_exposure_reduction
    )

    total_cost_basis_after = (
        total_cost_basis_before
        - total_cost_basis_reduction
    )

    if (
        sum(
            allocation.tokens_to_sell
            for allocation in allocations
        )
        != tokens_to_sell
    ):
        return finish(
            UNKNOWN,
            "SELL_ALLOCATION_TOKEN_SUM_MISMATCH",
        )

    return finish(
        PLANNED,
        total_tokens_before=(
            total_tokens_before
        ),
        total_tokens_after=(
            total_tokens_after
        ),
        total_exposure_before_lamports=(
            total_exposure_before
        ),
        total_exposure_reduction_lamports=(
            total_exposure_reduction
        ),
        total_exposure_after_lamports=(
            total_exposure_after
        ),
        total_cost_basis_before_lamports=(
            total_cost_basis_before
        ),
        total_cost_basis_reduction_lamports=(
            total_cost_basis_reduction
        ),
        total_cost_basis_after_lamports=(
            total_cost_basis_after
        ),
        allocations=tuple(
            allocations
        ),
    )
