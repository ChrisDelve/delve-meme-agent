from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


SOL_QUOTE_MINT = "11111111111111111111111111111111"
WINDOW_SECONDS = 15


@dataclass(frozen=True)
class LiveSignalState:
    mint: str
    quote_mint: str
    trade_timestamp: int

    mayhem_mode: Optional[float]
    buys_15s: int
    buy_rate_15s: float

    eligible: bool
    ineligible_reason: Optional[str]


def count_prior_buys_15s(
    connection: sqlite3.Connection,
    *,
    mint: str,
    quote_mint: str,
    trade_timestamp: int,
) -> int:
    """
    Reconstruct the exact historical 15-second BUY count used by
    candidate-features-v1.

    Important:
    - same mint
    - same quote mint
    - BUY side only
    - lower bound inclusive
    - entry timestamp strictly excluded
    """

    row = connection.execute(
        """
        SELECT COUNT(*) AS buys_15s
        FROM trades
        WHERE
            mint = ?
            AND quote_mint = ?
            AND side = 'BUY'
            AND trade_timestamp >= ?
            AND trade_timestamp < ?
        """,
        (
            mint,
            quote_mint,
            trade_timestamp - WINDOW_SECONDS,
            trade_timestamp,
        ),
    ).fetchone()

    return int(row[0])


def build_live_signal_state(
    connection: sqlite3.Connection,
    *,
    mint: str,
    quote_mint: str,
    trade_timestamp: int,
    mayhem_mode,
) -> LiveSignalState:
    """
    Build the two raw production features required by
    validated-signal-artifact-v1:

        mayhem_mode
        buy_rate_15s

    This function performs no research, label access, model fitting,
    or future-trade access.
    """

    if not mint:
        raise ValueError("mint is required")

    if not quote_mint:
        raise ValueError("quote_mint is required")

    if trade_timestamp is None:
        raise ValueError("trade_timestamp is required")

    trade_timestamp = int(trade_timestamp)

    # validated-signal-artifact-v1 was developed and validated on
    # the native-SOL quote universe.
    if quote_mint != SOL_QUOTE_MINT:
        return LiveSignalState(
            mint=mint,
            quote_mint=quote_mint,
            trade_timestamp=trade_timestamp,
            mayhem_mode=(
                None
                if mayhem_mode is None
                else float(mayhem_mode)
            ),
            buys_15s=0,
            buy_rate_15s=0.0,
            eligible=False,
            ineligible_reason="NON_SOL_QUOTE",
        )

    buys_15s = count_prior_buys_15s(
        connection,
        mint=mint,
        quote_mint=quote_mint,
        trade_timestamp=trade_timestamp,
    )

    buy_rate_15s = (
        float(buys_15s)
        / float(WINDOW_SECONDS)
    )

    normalized_mayhem = (
        None
        if mayhem_mode is None
        else float(mayhem_mode)
    )

    return LiveSignalState(
        mint=mint,
        quote_mint=quote_mint,
        trade_timestamp=trade_timestamp,
        mayhem_mode=normalized_mayhem,
        buys_15s=buys_15s,
        buy_rate_15s=buy_rate_15s,
        eligible=True,
        ineligible_reason=None,
    )


def raw_model_features(
    state: LiveSignalState,
) -> dict[str, Optional[float]]:
    """
    Return only the raw feature contract expected by the frozen
    validated inference artifact.
    """

    if not state.eligible:
        raise ValueError(
            f"Signal state is not model-eligible: "
            f"{state.ineligible_reason}"
        )

    return {
        "mayhem_mode": state.mayhem_mode,
        "buy_rate_15s": state.buy_rate_15s,
    }