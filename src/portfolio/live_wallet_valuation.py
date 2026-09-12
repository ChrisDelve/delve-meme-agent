from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solders.pubkey import Pubkey

from src.execution.live_pump_liquidation_value import (
    LIVE_PUMP_LIQUIDATION_VALUE_VERSION,
    RESOLVED as LIQUIDATION_RESOLVED,
    LivePumpLiquidationValueResult,
    resolve_live_pump_inventory_liquidation_value,
)
from src.execution.live_wallet_balance import (
    LIVE_WALLET_BALANCE_VERSION,
    RESOLVED as BALANCE_RESOLVED,
    resolve_live_wallet_balance,
)
from src.portfolio.live_positions import (
    OPEN_LIVE_POSITIONS_VERSION,
    PASS as POSITIONS_PASS,
    LivePosition,
    load_open_live_positions_read_only,
)
from src.portfolio.live_reservations import DB_PATH


LIVE_WALLET_VALUATION_VERSION = (
    "live-wallet-valuation-v1"
)

RESOLVED = "RESOLVED"
UNKNOWN = "UNKNOWN"

BPS_DENOMINATOR = 10_000
U64_MAX = (1 << 64) - 1

I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1


@dataclass(frozen=True)
class LiveMintInventoryValuation:
    mint: str

    open_lots: int
    tokens_held: int

    total_entry_wallet_cost_lamports: int
    remaining_cost_basis_lamports: int

    cumulative_net_proceeds_lamports: int
    cumulative_realized_pnl_lamports: int

    oldest_entry_slot: int
    newest_entry_slot: int

    oldest_entry_block_time: int | None
    newest_entry_block_time: int | None

    liquidation_value_lamports: int

    valuation: LivePumpLiquidationValueResult


@dataclass(frozen=True)
class LiveWalletValuationResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    wallet_pubkey: str

    positions_loader_version: str | None

    wallet_balance_version: str | None
    wallet_balance_lamports: int | None
    wallet_balance_rpc_slot: int | None

    open_position_lots: int | None
    unique_open_mints: int | None

    total_liquidation_value_lamports: int | None
    current_equity_lamports: int | None

    min_context_slot: int | None

    mint_valuations: tuple[
        LiveMintInventoryValuation,
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


def _strict_i64(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and I64_MIN <= value <= I64_MAX
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


def _position_book_fingerprint(
    positions: tuple[
        LivePosition,
        ...,
    ],
) -> tuple[
    tuple[Any, ...],
    ...,
]:
    return tuple(
        (
            position.position_id,
            position.position_version,
            position.reservation_id,
            position.wallet_pubkey,
            position.mint,
            position.status,

            position.entry_slot,
            position.entry_block_time,

            position.tokens_held,

            position.entry_wallet_cost_lamports,
            position.remaining_exposure_lamports,
            position.remaining_cost_basis_lamports,

            position.cumulative_net_proceeds_lamports,
            position.cumulative_realized_pnl_lamports,

            position.updated_at,
        )
        for position in positions
    )


async def resolve_live_wallet_valuation(
    *,
    wallet_pubkey: str,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    min_context_slot: int | None = None,
    db_path: Path = DB_PATH,
) -> LiveWalletValuationResult:
    """
    Resolve one conservative instantaneous wallet
    valuation snapshot.

    current_equity_lamports =
        native SOL wallet balance
        + conservative immediate liquidation value
          of all OPEN token inventory.

    Multiple OPEN lots of the same mint are aggregated
    BEFORE liquidation valuation so current curve
    liquidity cannot be counted more than once.

    The local OPEN-position book is reread after chain
    valuation. Any concurrent change makes the result
    UNKNOWN rather than mixing two portfolio states.

    This resolver has no:
    - database mutation authority
    - transaction build authority
    - signing authority
    - send authority
    - risk sizing/authorization authority
    - day-start/high-water persistence authority
    """

    normalized_wallet = ""

    def finish(
        status: str,
        *reasons: str,
        positions_loader_version: (
            str | None
        ) = None,
        wallet_balance_version: (
            str | None
        ) = None,
        wallet_balance_lamports: (
            int | None
        ) = None,
        wallet_balance_rpc_slot: (
            int | None
        ) = None,
        open_position_lots: (
            int | None
        ) = None,
        unique_open_mints: (
            int | None
        ) = None,
        total_liquidation_value_lamports: (
            int | None
        ) = None,
        current_equity_lamports: (
            int | None
        ) = None,
        mint_valuations: (
            tuple[
                LiveMintInventoryValuation,
                ...,
            ]
            | None
        ) = None,
    ) -> LiveWalletValuationResult:
        return LiveWalletValuationResult(
            resolver_version=(
                LIVE_WALLET_VALUATION_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=normalized_wallet,
            positions_loader_version=(
                positions_loader_version
            ),
            wallet_balance_version=(
                wallet_balance_version
            ),
            wallet_balance_lamports=(
                wallet_balance_lamports
            ),
            wallet_balance_rpc_slot=(
                wallet_balance_rpc_slot
            ),
            open_position_lots=(
                open_position_lots
            ),
            unique_open_mints=(
                unique_open_mints
            ),
            total_liquidation_value_lamports=(
                total_liquidation_value_lamports
            ),
            current_equity_lamports=(
                current_equity_lamports
            ),
            min_context_slot=(
                min_context_slot
            ),
            mint_valuations=(
                mint_valuations
            ),
        )

    # --------------------------------------------------------
    # Input contract
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

    try:
        normalized_path = Path(
            db_path
        )
    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_DATABASE_PATH_INVALID",
        )

    # --------------------------------------------------------
    # First authoritative local inventory snapshot
    # --------------------------------------------------------

    try:
        first_positions = (
            load_open_live_positions_read_only(
                wallet_pubkey=(
                    normalized_wallet
                ),
                db_path=normalized_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "OPEN_POSITION_LOAD_FAILED",
        )

    if (
        getattr(
            first_positions,
            "loader_version",
            None,
        )
        != OPEN_LIVE_POSITIONS_VERSION
    ):
        return finish(
            UNKNOWN,
            "OPEN_POSITION_LOADER_VERSION_MISMATCH",
        )

    if (
        getattr(
            first_positions,
            "status",
            None,
        )
        != POSITIONS_PASS
    ):
        reasons = tuple(
            getattr(
                first_positions,
                "reasons",
                (),
            )
            or ()
        )

        return finish(
            UNKNOWN,
            *(
                (
                    "OPEN_POSITION_LOAD_UNKNOWN:"
                    + str(reason)
                )
                for reason in reasons
            ),
            positions_loader_version=(
                OPEN_LIVE_POSITIONS_VERSION
            ),
        )

    positions = getattr(
        first_positions,
        "positions",
        None,
    )

    if not isinstance(
        positions,
        tuple,
    ):
        return finish(
            UNKNOWN,
            "OPEN_POSITION_RESULT_INVALID",
            positions_loader_version=(
                OPEN_LIVE_POSITIONS_VERSION
            ),
        )

    first_fingerprint = (
        _position_book_fingerprint(
            positions
        )
    )

    # --------------------------------------------------------
    # Aggregate inventory by mint.
    #
    # One mint gets ONE liquidation valuation regardless of
    # how many entry lots produced its current inventory.
    # --------------------------------------------------------

    inventory: dict[
        str,
        dict[str, Any],
    ] = {}

    for position in positions:
        mint = getattr(
            position,
            "mint",
            None,
        )

        tokens_held = getattr(
            position,
            "tokens_held",
            None,
        )

        entry_wallet_cost = getattr(
            position,
            "entry_wallet_cost_lamports",
            None,
        )

        remaining_cost_basis = getattr(
            position,
            "remaining_cost_basis_lamports",
            None,
        )

        cumulative_net_proceeds = getattr(
            position,
            "cumulative_net_proceeds_lamports",
            None,
        )

        cumulative_realized_pnl = getattr(
            position,
            "cumulative_realized_pnl_lamports",
            None,
        )

        entry_slot = getattr(
            position,
            "entry_slot",
            None,
        )

        entry_block_time = getattr(
            position,
            "entry_block_time",
            None,
        )

        if (
            not isinstance(
                mint,
                str,
            )
            or not mint
            or not _strict_positive_u64(
                tokens_held
            )
        ):
            return finish(
                UNKNOWN,
                "OPEN_POSITION_INVENTORY_INVALID",
                positions_loader_version=(
                    OPEN_LIVE_POSITIONS_VERSION
                ),
            )

        if (
            not _strict_positive_u64(
                entry_wallet_cost
            )
            or not _strict_u64(
                remaining_cost_basis
            )
            or not _strict_u64(
                cumulative_net_proceeds
            )
            or not _strict_i64(
                cumulative_realized_pnl
            )
            or not _strict_u64(
                entry_slot
            )
            or (
                entry_block_time is not None
                and not _strict_u64(
                    entry_block_time
                )
            )
        ):
            return finish(
                UNKNOWN,
                "OPEN_POSITION_ACCOUNTING_INVALID",
                positions_loader_version=(
                    OPEN_LIVE_POSITIONS_VERSION
                ),
            )

        current = inventory.setdefault(
            mint,
            {
                "open_lots": 0,
                "tokens_held": 0,

                "total_entry_wallet_cost_lamports": 0,
                "remaining_cost_basis_lamports": 0,

                "cumulative_net_proceeds_lamports": 0,
                "cumulative_realized_pnl_lamports": 0,

                "oldest_entry_slot": None,
                "newest_entry_slot": None,

                "entry_block_times_complete": True,
                "oldest_entry_block_time": None,
                "newest_entry_block_time": None,
            },
        )

        next_lots = (
            current["open_lots"]
            + 1
        )

        next_tokens = (
            current["tokens_held"]
            + tokens_held
        )

        next_entry_wallet_cost = (
            current[
                "total_entry_wallet_cost_lamports"
            ]
            + entry_wallet_cost
        )

        next_remaining_cost_basis = (
            current[
                "remaining_cost_basis_lamports"
            ]
            + remaining_cost_basis
        )

        next_cumulative_net_proceeds = (
            current[
                "cumulative_net_proceeds_lamports"
            ]
            + cumulative_net_proceeds
        )

        next_cumulative_realized_pnl = (
            current[
                "cumulative_realized_pnl_lamports"
            ]
            + cumulative_realized_pnl
        )

        if (
            next_lots > U64_MAX
            or next_tokens > U64_MAX
        ):
            return finish(
                UNKNOWN,
                "OPEN_POSITION_INVENTORY_OVERFLOW",
                positions_loader_version=(
                    OPEN_LIVE_POSITIONS_VERSION
                ),
            )

        if (
            next_entry_wallet_cost > U64_MAX
            or next_remaining_cost_basis > U64_MAX
            or next_cumulative_net_proceeds > U64_MAX
        ):
            return finish(
                UNKNOWN,
                "OPEN_POSITION_ACCOUNTING_OVERFLOW",
                positions_loader_version=(
                    OPEN_LIVE_POSITIONS_VERSION
                ),
            )

        if not (
            I64_MIN
            <= next_cumulative_realized_pnl
            <= I64_MAX
        ):
            return finish(
                UNKNOWN,
                "OPEN_POSITION_REALIZED_PNL_OVERFLOW",
                positions_loader_version=(
                    OPEN_LIVE_POSITIONS_VERSION
                ),
            )

        current[
            "open_lots"
        ] = next_lots

        current[
            "tokens_held"
        ] = next_tokens

        current[
            "total_entry_wallet_cost_lamports"
        ] = next_entry_wallet_cost

        current[
            "remaining_cost_basis_lamports"
        ] = next_remaining_cost_basis

        current[
            "cumulative_net_proceeds_lamports"
        ] = next_cumulative_net_proceeds

        current[
            "cumulative_realized_pnl_lamports"
        ] = next_cumulative_realized_pnl

        if (
            current["oldest_entry_slot"]
            is None
        ):
            current[
                "oldest_entry_slot"
            ] = entry_slot

            current[
                "newest_entry_slot"
            ] = entry_slot

        else:
            current[
                "oldest_entry_slot"
            ] = min(
                current[
                    "oldest_entry_slot"
                ],
                entry_slot,
            )

            current[
                "newest_entry_slot"
            ] = max(
                current[
                    "newest_entry_slot"
                ],
                entry_slot,
            )

        if entry_block_time is None:
            current[
                "entry_block_times_complete"
            ] = False

        else:
            if (
                current[
                    "oldest_entry_block_time"
                ]
                is None
            ):
                current[
                    "oldest_entry_block_time"
                ] = entry_block_time

                current[
                    "newest_entry_block_time"
                ] = entry_block_time

            else:
                current[
                    "oldest_entry_block_time"
                ] = min(
                    current[
                        "oldest_entry_block_time"
                    ],
                    entry_block_time,
                )

                current[
                    "newest_entry_block_time"
                ] = max(
                    current[
                        "newest_entry_block_time"
                    ],
                    entry_block_time,
                )

    # --------------------------------------------------------
    # Native SOL cash authority
    # --------------------------------------------------------

    try:
        wallet_balance = (
            await resolve_live_wallet_balance(
                wallet_pubkey=(
                    normalized_wallet
                ),
                min_context_slot=(
                    min_context_slot
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "WALLET_BALANCE_RESOLUTION_FAILED",
            positions_loader_version=(
                OPEN_LIVE_POSITIONS_VERSION
            ),
        )

    if (
        getattr(
            wallet_balance,
            "resolver_version",
            None,
        )
        != LIVE_WALLET_BALANCE_VERSION
    ):
        return finish(
            UNKNOWN,
            "WALLET_BALANCE_VERSION_MISMATCH",
            positions_loader_version=(
                OPEN_LIVE_POSITIONS_VERSION
            ),
        )

    if (
        getattr(
            wallet_balance,
            "status",
            None,
        )
        != BALANCE_RESOLVED
    ):
        reasons = tuple(
            getattr(
                wallet_balance,
                "reasons",
                (),
            )
            or ()
        )

        return finish(
            UNKNOWN,
            *(
                (
                    "WALLET_BALANCE_UNKNOWN:"
                    + str(reason)
                )
                for reason in reasons
            ),
            positions_loader_version=(
                OPEN_LIVE_POSITIONS_VERSION
            ),
            wallet_balance_version=(
                LIVE_WALLET_BALANCE_VERSION
            ),
        )

    if (
        getattr(
            wallet_balance,
            "wallet_pubkey",
            None,
        )
        != normalized_wallet
    ):
        return finish(
            UNKNOWN,
            "WALLET_BALANCE_WALLET_MISMATCH",
            positions_loader_version=(
                OPEN_LIVE_POSITIONS_VERSION
            ),
            wallet_balance_version=(
                LIVE_WALLET_BALANCE_VERSION
            ),
        )

    native_balance = getattr(
        wallet_balance,
        "balance_lamports",
        None,
    )

    balance_rpc_slot = getattr(
        wallet_balance,
        "rpc_slot",
        None,
    )

    if (
        not _strict_u64(
            native_balance
        )
        or not _strict_u64(
            balance_rpc_slot
        )
    ):
        return finish(
            UNKNOWN,
            "WALLET_BALANCE_RESULT_INVALID",
            positions_loader_version=(
                OPEN_LIVE_POSITIONS_VERSION
            ),
            wallet_balance_version=(
                LIVE_WALLET_BALANCE_VERSION
            ),
        )

    valuation_min_context_slot = (
        balance_rpc_slot
    )

    if (
        min_context_slot is not None
        and min_context_slot
        > valuation_min_context_slot
    ):
        valuation_min_context_slot = (
            min_context_slot
        )

    # --------------------------------------------------------
    # One valuation per aggregate mint inventory.
    # Deterministic mint order keeps audit output stable.
    # --------------------------------------------------------

    mint_valuations: list[
        LiveMintInventoryValuation
    ] = []

    total_liquidation_value = 0

    for mint in sorted(
        inventory
    ):
        mint_inventory = inventory[
            mint
        ]

        aggregate_tokens = (
            mint_inventory[
                "tokens_held"
            ]
        )

        try:
            valuation = await (
                resolve_live_pump_inventory_liquidation_value(
                    mint=mint,
                    tokens_held=(
                        aggregate_tokens
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
                        valuation_min_context_slot
                    ),
                )
            )

        except Exception:
            return finish(
                UNKNOWN,
                (
                    "MINT_LIQUIDATION_RESOLUTION_FAILED:"
                    + mint
                ),
                positions_loader_version=(
                    OPEN_LIVE_POSITIONS_VERSION
                ),
                wallet_balance_version=(
                    LIVE_WALLET_BALANCE_VERSION
                ),
                wallet_balance_lamports=(
                    native_balance
                ),
                wallet_balance_rpc_slot=(
                    balance_rpc_slot
                ),
                open_position_lots=(
                    len(positions)
                ),
                unique_open_mints=(
                    len(inventory)
                ),
            )

        if (
            getattr(
                valuation,
                "resolver_version",
                None,
            )
            != LIVE_PUMP_LIQUIDATION_VALUE_VERSION
        ):
            return finish(
                UNKNOWN,
                (
                    "MINT_LIQUIDATION_VERSION_MISMATCH:"
                    + mint
                ),
                positions_loader_version=(
                    OPEN_LIVE_POSITIONS_VERSION
                ),
                wallet_balance_version=(
                    LIVE_WALLET_BALANCE_VERSION
                ),
                wallet_balance_lamports=(
                    native_balance
                ),
                wallet_balance_rpc_slot=(
                    balance_rpc_slot
                ),
                open_position_lots=(
                    len(positions)
                ),
                unique_open_mints=(
                    len(inventory)
                ),
            )

        if (
            getattr(
                valuation,
                "status",
                None,
            )
            != LIQUIDATION_RESOLVED
        ):
            reasons = tuple(
                getattr(
                    valuation,
                    "reasons",
                    (),
                )
                or ()
            )

            prefixed = tuple(
                (
                    "MINT_LIQUIDATION_UNKNOWN:"
                    + mint
                    + ":"
                    + str(reason)
                )
                for reason in reasons
            )

            if not prefixed:
                prefixed = (
                    "MINT_LIQUIDATION_UNKNOWN:"
                    + mint,
                )

            return finish(
                UNKNOWN,
                *prefixed,
                positions_loader_version=(
                    OPEN_LIVE_POSITIONS_VERSION
                ),
                wallet_balance_version=(
                    LIVE_WALLET_BALANCE_VERSION
                ),
                wallet_balance_lamports=(
                    native_balance
                ),
                wallet_balance_rpc_slot=(
                    balance_rpc_slot
                ),
                open_position_lots=(
                    len(positions)
                ),
                unique_open_mints=(
                    len(inventory)
                ),
            )

        if (
            getattr(
                valuation,
                "mint",
                None,
            )
            != mint
            or getattr(
                valuation,
                "tokens_held",
                None,
            )
            != aggregate_tokens
        ):
            return finish(
                UNKNOWN,
                (
                    "MINT_LIQUIDATION_IDENTITY_MISMATCH:"
                    + mint
                ),
                positions_loader_version=(
                    OPEN_LIVE_POSITIONS_VERSION
                ),
                wallet_balance_version=(
                    LIVE_WALLET_BALANCE_VERSION
                ),
                wallet_balance_lamports=(
                    native_balance
                ),
                wallet_balance_rpc_slot=(
                    balance_rpc_slot
                ),
                open_position_lots=(
                    len(positions)
                ),
                unique_open_mints=(
                    len(inventory)
                ),
            )

        value = getattr(
            valuation,
            "liquidation_value_lamports",
            None,
        )

        if not _strict_u64(
            value
        ):
            return finish(
                UNKNOWN,
                (
                    "MINT_LIQUIDATION_VALUE_INVALID:"
                    + mint
                ),
                positions_loader_version=(
                    OPEN_LIVE_POSITIONS_VERSION
                ),
                wallet_balance_version=(
                    LIVE_WALLET_BALANCE_VERSION
                ),
                wallet_balance_lamports=(
                    native_balance
                ),
                wallet_balance_rpc_slot=(
                    balance_rpc_slot
                ),
                open_position_lots=(
                    len(positions)
                ),
                unique_open_mints=(
                    len(inventory)
                ),
            )

        next_total = (
            total_liquidation_value
            + value
        )

        if next_total > U64_MAX:
            return finish(
                UNKNOWN,
                "TOTAL_LIQUIDATION_VALUE_OVERFLOW",
                positions_loader_version=(
                    OPEN_LIVE_POSITIONS_VERSION
                ),
                wallet_balance_version=(
                    LIVE_WALLET_BALANCE_VERSION
                ),
                wallet_balance_lamports=(
                    native_balance
                ),
                wallet_balance_rpc_slot=(
                    balance_rpc_slot
                ),
                open_position_lots=(
                    len(positions)
                ),
                unique_open_mints=(
                    len(inventory)
                ),
            )

        total_liquidation_value = (
            next_total
        )

        mint_valuations.append(
            LiveMintInventoryValuation(
                mint=mint,
                open_lots=(
                    mint_inventory[
                        "open_lots"
                    ]
                ),
                tokens_held=(
                    aggregate_tokens
                ),

                total_entry_wallet_cost_lamports=(
                    mint_inventory[
                        "total_entry_wallet_cost_lamports"
                    ]
                ),

                remaining_cost_basis_lamports=(
                    mint_inventory[
                        "remaining_cost_basis_lamports"
                    ]
                ),

                cumulative_net_proceeds_lamports=(
                    mint_inventory[
                        "cumulative_net_proceeds_lamports"
                    ]
                ),

                cumulative_realized_pnl_lamports=(
                    mint_inventory[
                        "cumulative_realized_pnl_lamports"
                    ]
                ),

                oldest_entry_slot=(
                    mint_inventory[
                        "oldest_entry_slot"
                    ]
                ),

                newest_entry_slot=(
                    mint_inventory[
                        "newest_entry_slot"
                    ]
                ),

                oldest_entry_block_time=(
                    mint_inventory[
                        "oldest_entry_block_time"
                    ]
                    if mint_inventory[
                        "entry_block_times_complete"
                    ]
                    else None
                ),

                newest_entry_block_time=(
                    mint_inventory[
                        "newest_entry_block_time"
                    ]
                    if mint_inventory[
                        "entry_block_times_complete"
                    ]
                    else None
                ),

                liquidation_value_lamports=(
                    value
                ),
                valuation=valuation,
            )
        )

    # --------------------------------------------------------
    # Re-read local inventory.
    #
    # A concurrent accounting transition means the chain
    # values above no longer describe the same portfolio book.
    # --------------------------------------------------------

    try:
        final_positions = (
            load_open_live_positions_read_only(
                wallet_pubkey=(
                    normalized_wallet
                ),
                db_path=normalized_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "FINAL_OPEN_POSITION_LOAD_FAILED",
            positions_loader_version=(
                OPEN_LIVE_POSITIONS_VERSION
            ),
            wallet_balance_version=(
                LIVE_WALLET_BALANCE_VERSION
            ),
            wallet_balance_lamports=(
                native_balance
            ),
            wallet_balance_rpc_slot=(
                balance_rpc_slot
            ),
            open_position_lots=(
                len(positions)
            ),
            unique_open_mints=(
                len(inventory)
            ),
        )

    if (
        getattr(
            final_positions,
            "loader_version",
            None,
        )
        != OPEN_LIVE_POSITIONS_VERSION
        or getattr(
            final_positions,
            "status",
            None,
        )
        != POSITIONS_PASS
        or not isinstance(
            getattr(
                final_positions,
                "positions",
                None,
            ),
            tuple,
        )
    ):
        return finish(
            UNKNOWN,
            "FINAL_OPEN_POSITION_RESULT_INVALID",
            positions_loader_version=(
                OPEN_LIVE_POSITIONS_VERSION
            ),
            wallet_balance_version=(
                LIVE_WALLET_BALANCE_VERSION
            ),
            wallet_balance_lamports=(
                native_balance
            ),
            wallet_balance_rpc_slot=(
                balance_rpc_slot
            ),
            open_position_lots=(
                len(positions)
            ),
            unique_open_mints=(
                len(inventory)
            ),
        )

    final_fingerprint = (
        _position_book_fingerprint(
            final_positions.positions
        )
    )

    if (
        final_fingerprint
        != first_fingerprint
    ):
        return finish(
            UNKNOWN,
            "OPEN_POSITION_BOOK_CHANGED_DURING_VALUATION",
            positions_loader_version=(
                OPEN_LIVE_POSITIONS_VERSION
            ),
            wallet_balance_version=(
                LIVE_WALLET_BALANCE_VERSION
            ),
            wallet_balance_lamports=(
                native_balance
            ),
            wallet_balance_rpc_slot=(
                balance_rpc_slot
            ),
            open_position_lots=(
                len(positions)
            ),
            unique_open_mints=(
                len(inventory)
            ),
        )

    current_equity = (
        native_balance
        + total_liquidation_value
    )

    if current_equity > U64_MAX:
        return finish(
            UNKNOWN,
            "CURRENT_EQUITY_OVERFLOW",
            positions_loader_version=(
                OPEN_LIVE_POSITIONS_VERSION
            ),
            wallet_balance_version=(
                LIVE_WALLET_BALANCE_VERSION
            ),
            wallet_balance_lamports=(
                native_balance
            ),
            wallet_balance_rpc_slot=(
                balance_rpc_slot
            ),
            open_position_lots=(
                len(positions)
            ),
            unique_open_mints=(
                len(inventory)
            ),
            total_liquidation_value_lamports=(
                total_liquidation_value
            ),
        )

    return finish(
        RESOLVED,
        positions_loader_version=(
            OPEN_LIVE_POSITIONS_VERSION
        ),
        wallet_balance_version=(
            LIVE_WALLET_BALANCE_VERSION
        ),
        wallet_balance_lamports=(
            native_balance
        ),
        wallet_balance_rpc_slot=(
            balance_rpc_slot
        ),
        open_position_lots=(
            len(positions)
        ),
        unique_open_mints=(
            len(inventory)
        ),
        total_liquidation_value_lamports=(
            total_liquidation_value
        ),
        current_equity_lamports=(
            current_equity
        ),
        mint_valuations=tuple(
            mint_valuations
        ),
    )
