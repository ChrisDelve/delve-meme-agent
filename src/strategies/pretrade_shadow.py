from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from src.execution.execution_quality_gate import (
    evaluate_execution_quality,
)
from src.execution.live_curve_state import (
    resolve_live_pump_curve_state,
)

from src.execution.live_fee_resolver import (
    resolve_live_event_fee_bps,
)

from src.safety.token_safety_gate import (
    PASS as SAFETY_PASS,
    resolve_and_gate,
)


DB_PATH = Path(
    "logs/delve_meme.db"
)

PRETRADE_SHADOW_VERSION = (
    "pretrade-shadow-v1"
)

SOL_QUOTE_MINT = (
    "11111111111111111111111111111111"
)


#
# -------------------------------------------------
# OPERATIONAL SHADOW POLICY
# -------------------------------------------------
#
# 0.30 is NOT a newly optimized model threshold.
#
# It is an operational trigger chosen to reduce
# redundant RPC load while we exercise the real
# pre-trade pipeline.
#
CANDIDATE_PROBABILITY_THRESHOLD = 0.30

#
# The validated prediction target is 15 minutes.
# Once a mint is evaluated successfully/definitively,
# do not repeatedly evaluate every BUY event.
#
MINT_COOLDOWN_SECONDS = 15 * 60

#
# Transient UNKNOWN outcomes can retry sooner.
#
UNKNOWN_RETRY_SECONDS = 10.0

#
# Prevent RPC bursts from swamping Helius or the
# collector.
#
MAX_CONCURRENT_EVALUATIONS = 8

#
# A candidate waiting this long merely for capacity
# is stale enough that we do not want to chase it.
#
MAX_QUEUE_DELAY_SECONDS = 1.0


#
# -------------------------------------------------
# SHADOW ORDER PROBE
# -------------------------------------------------
#
# These are NOT final live-capital parameters.
#
SHADOW_SPENDABLE_QUOTE_IN = 10_000_000
SHADOW_SLIPPAGE_BPS = 300

SHADOW_BASE_NETWORK_FEE_LAMPORTS = 5_000
SHADOW_PRIORITY_FEE_LAMPORTS = 0
SHADOW_RENT_LAMPORTS = 0


@dataclass(frozen=True)
class PretradeShadowEvaluation:
    entry_signature: str

    shadow_version: str

    mint: str
    slot: int | None

    trade_timestamp: int
    predicted_at: int

    probability_2x_15m: float

    signal_virtual_quote_reserves: int
    signal_virtual_token_reserves: int

    signal_real_quote_reserves: int
    signal_real_token_reserves: int

    quote_amount: int

    protocol_fee_lamports: int | None
    creator_fee_lamports: int | None

    fee_resolution_status: str

    protocol_fee_bps: int | None
    creator_fee_bps: int | None

    safety_status: str
    safety_reasons: tuple[str, ...]

    execution_status: str
    execution_reasons: tuple[str, ...]

    market_drift_bps: float | None
    price_impact_bps: float | None
    all_in_vs_signal_bps: float | None

    live_curve_age_seconds: float | None

    final_status: str
    final_reasons: tuple[str, ...]

    scheduled_at: float
    started_at: float
    completed_at: float

    elapsed_ms: float


_tasks: set[
    asyncio.Task
] = set()

_mint_next_allowed_at: dict[
    str,
    float,
] = {}

_semaphore: (
    asyncio.Semaphore | None
) = None


def get_semaphore() -> asyncio.Semaphore:
    global _semaphore

    if _semaphore is None:
        _semaphore = asyncio.Semaphore(
            MAX_CONCURRENT_EVALUATIONS
        )

    return _semaphore


def init_table(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        pretrade_shadow_evaluations (
            entry_signature TEXT PRIMARY KEY,

            shadow_version TEXT NOT NULL,

            mint TEXT NOT NULL,
            slot INTEGER,

            trade_timestamp INTEGER NOT NULL,
            predicted_at INTEGER NOT NULL,

            probability_2x_15m REAL NOT NULL,

            signal_virtual_quote_reserves INTEGER NOT NULL,
            signal_virtual_token_reserves INTEGER NOT NULL,

            signal_real_quote_reserves INTEGER NOT NULL,
            signal_real_token_reserves INTEGER NOT NULL,

            quote_amount INTEGER NOT NULL,

            protocol_fee_lamports INTEGER,
            creator_fee_lamports INTEGER,

            fee_resolution_status TEXT NOT NULL,

            protocol_fee_bps INTEGER,
            creator_fee_bps INTEGER,

            safety_status TEXT NOT NULL,
            safety_reasons_json TEXT NOT NULL,

            execution_status TEXT NOT NULL,
            execution_reasons_json TEXT NOT NULL,

            market_drift_bps REAL,
            price_impact_bps REAL,
            all_in_vs_signal_bps REAL,

            live_curve_age_seconds REAL,

            final_status TEXT NOT NULL,
            final_reasons_json TEXT NOT NULL,

            scheduled_at REAL NOT NULL,
            started_at REAL NOT NULL,
            completed_at REAL NOT NULL,

            elapsed_ms REAL NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_pretrade_shadow_mint
        ON pretrade_shadow_evaluations (
            mint
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_pretrade_shadow_completed
        ON pretrade_shadow_evaluations (
            completed_at
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_pretrade_shadow_status
        ON pretrade_shadow_evaluations (
            final_status
        )
        """
    )


def write_evaluation_sync(
    record: PretradeShadowEvaluation,
) -> None:
    connection = sqlite3.connect(
        DB_PATH,
        timeout=30.0,
    )

    connection.execute(
        "PRAGMA busy_timeout = 30000"
    )

    try:
        init_table(
            connection
        )

        connection.execute(
            """
            INSERT OR REPLACE INTO
            pretrade_shadow_evaluations (
                entry_signature,

                shadow_version,

                mint,
                slot,

                trade_timestamp,
                predicted_at,

                probability_2x_15m,

                signal_virtual_quote_reserves,
                signal_virtual_token_reserves,

                signal_real_quote_reserves,
                signal_real_token_reserves,

                quote_amount,

                protocol_fee_lamports,
                creator_fee_lamports,

                fee_resolution_status,

                protocol_fee_bps,
                creator_fee_bps,

                safety_status,
                safety_reasons_json,

                execution_status,
                execution_reasons_json,

                market_drift_bps,
                price_impact_bps,
                all_in_vs_signal_bps,

                live_curve_age_seconds,

                final_status,
                final_reasons_json,

                scheduled_at,
                started_at,
                completed_at,

                elapsed_ms
            )
            VALUES (
                ?,
                ?,
                ?, ?,
                ?, ?,
                ?,
                ?, ?,
                ?, ?,
                ?,
                ?, ?,
                ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?,
                ?, ?,
                ?, ?, ?,
                ?
            )
            """,
            (
                record.entry_signature,

                record.shadow_version,

                record.mint,
                record.slot,

                record.trade_timestamp,
                record.predicted_at,

                record.probability_2x_15m,

                record.signal_virtual_quote_reserves,
                record.signal_virtual_token_reserves,

                record.signal_real_quote_reserves,
                record.signal_real_token_reserves,

                record.quote_amount,

                record.protocol_fee_lamports,
                record.creator_fee_lamports,

                record.fee_resolution_status,

                record.protocol_fee_bps,
                record.creator_fee_bps,

                record.safety_status,
                json.dumps(
                    record.safety_reasons
                ),

                record.execution_status,
                json.dumps(
                    record.execution_reasons
                ),

                record.market_drift_bps,
                record.price_impact_bps,
                record.all_in_vs_signal_bps,

                record.live_curve_age_seconds,

                record.final_status,
                json.dumps(
                    record.final_reasons
                ),

                record.scheduled_at,
                record.started_at,
                record.completed_at,

                record.elapsed_ms,
            ),
        )

        connection.commit()

    finally:
        connection.close()


async def persist(
    record: PretradeShadowEvaluation,
) -> None:
    await asyncio.to_thread(
        write_evaluation_sync,
        record,
    )


def make_record(
    *,
    entry_signature: str,
    mint: str,
    slot: int | None,
    trade_timestamp: int,
    predicted_at: int,
    probability_2x_15m: float,
    signal_virtual_quote_reserves: int,
    signal_virtual_token_reserves: int,
    signal_real_quote_reserves: int,
    signal_real_token_reserves: int,
    quote_amount: int,
    protocol_fee_lamports: int | None,
    creator_fee_lamports: int | None,
    fee_resolution_status: str,
    protocol_fee_bps: int | None,
    creator_fee_bps: int | None,
    safety_status: str,
    safety_reasons: tuple[str, ...],
    execution_status: str,
    execution_reasons: tuple[str, ...],
    market_drift_bps: float | None,
    price_impact_bps: float | None,
    all_in_vs_signal_bps: float | None,
    live_curve_age_seconds: float | None,
    final_status: str,
    final_reasons: tuple[str, ...],
    scheduled_at: float,
    started_at: float,
) -> PretradeShadowEvaluation:

    completed_at = time.time()

    return PretradeShadowEvaluation(
        entry_signature=(
            entry_signature
        ),

        shadow_version=(
            PRETRADE_SHADOW_VERSION
        ),

        mint=mint,
        slot=slot,

        trade_timestamp=int(
            trade_timestamp
        ),

        predicted_at=int(
            predicted_at
        ),

        probability_2x_15m=float(
            probability_2x_15m
        ),

        signal_virtual_quote_reserves=int(
            signal_virtual_quote_reserves
        ),

        signal_virtual_token_reserves=int(
            signal_virtual_token_reserves
        ),

        signal_real_quote_reserves=int(
            signal_real_quote_reserves
        ),

        signal_real_token_reserves=int(
            signal_real_token_reserves
        ),

        quote_amount=int(
            quote_amount
        ),

        protocol_fee_lamports=(
            int(
                protocol_fee_lamports
            )
            if protocol_fee_lamports
            is not None
            else None
        ),

        creator_fee_lamports=(
            int(
                creator_fee_lamports
            )
            if creator_fee_lamports
            is not None
            else None
        ),

        fee_resolution_status=(
            fee_resolution_status
        ),

        protocol_fee_bps=(
            protocol_fee_bps
        ),

        creator_fee_bps=(
            creator_fee_bps
        ),

        safety_status=(
            safety_status
        ),

        safety_reasons=(
            safety_reasons
        ),

        execution_status=(
            execution_status
        ),

        execution_reasons=(
            execution_reasons
        ),

        market_drift_bps=(
            market_drift_bps
        ),

        price_impact_bps=(
            price_impact_bps
        ),

        all_in_vs_signal_bps=(
            all_in_vs_signal_bps
        ),

        live_curve_age_seconds=(
            live_curve_age_seconds
        ),

        final_status=(
            final_status
        ),

        final_reasons=(
            final_reasons
        ),

        scheduled_at=(
            scheduled_at
        ),

        started_at=(
            started_at
        ),

        completed_at=(
            completed_at
        ),

        elapsed_ms=(
            (
                completed_at
                - scheduled_at
            )
            * 1000.0
        ),
    )


async def run_candidate(
    *,
    entry_signature: str,
    mint: str,
    event_user: str,
    slot: int | None,
    trade_timestamp: int,
    predicted_at: int,
    probability_2x_15m: float,
    signal_virtual_quote_reserves: int,
    signal_virtual_token_reserves: int,
    signal_real_quote_reserves: int,
    signal_real_token_reserves: int,
    quote_amount: int,
    protocol_fee_lamports: int | None,
    creator_fee_lamports: int | None,

    event_protocol_fee_bps: int | None,
    event_creator_fee_bps: int | None,

    scheduled_at: float,
) -> None:

    semaphore = get_semaphore()

    try:
        await asyncio.wait_for(
            semaphore.acquire(),
            timeout=(
                MAX_QUEUE_DELAY_SECONDS
            ),
        )

    except TimeoutError:
        started_at = time.time()

        record = make_record(
            entry_signature=(
                entry_signature
            ),
            mint=mint,
            slot=slot,
            trade_timestamp=(
                trade_timestamp
            ),
            predicted_at=(
                predicted_at
            ),
            probability_2x_15m=(
                probability_2x_15m
            ),
            signal_virtual_quote_reserves=(
                signal_virtual_quote_reserves
            ),
            signal_virtual_token_reserves=(
                signal_virtual_token_reserves
            ),
            signal_real_quote_reserves=(
                signal_real_quote_reserves
            ),
            signal_real_token_reserves=(
                signal_real_token_reserves
            ),
            quote_amount=(
                quote_amount
            ),
            protocol_fee_lamports=(
                protocol_fee_lamports
            ),
            creator_fee_lamports=(
                creator_fee_lamports
            ),
            fee_resolution_status=(
                "NOT_RUN"
            ),
            protocol_fee_bps=None,
            creator_fee_bps=None,
            safety_status="NOT_RUN",
            safety_reasons=(),
            execution_status="NOT_RUN",
            execution_reasons=(),
            market_drift_bps=None,
            price_impact_bps=None,
            all_in_vs_signal_bps=None,
            live_curve_age_seconds=None,
            final_status="UNKNOWN",
            final_reasons=(
                "EVALUATION_CAPACITY_TIMEOUT",
            ),
            scheduled_at=(
                scheduled_at
            ),
            started_at=(
                started_at
            ),
        )

        await persist(
            record
        )

        _mint_next_allowed_at[
            mint
        ] = (
            time.monotonic()
            + UNKNOWN_RETRY_SECONDS
        )

        return

    started_at = time.time()

    try:
        #
        # Exact triggering trade fee regime.
        #
        (
            fee_status,
            protocol_fee_bps,
            creator_fee_bps,
        ) = resolve_live_event_fee_bps(
            quote_amount=int(
                quote_amount
            ),

            protocol_fee_lamports=(
                protocol_fee_lamports
            ),

            creator_fee_lamports=(
                creator_fee_lamports
            ),

            event_protocol_fee_bps=(
                event_protocol_fee_bps
            ),

            event_creator_fee_bps=(
                event_creator_fee_bps
            ),
        )

        if not fee_status.startswith(
            "RESOLVED"
        ):
            record = make_record(
                entry_signature=(
                    entry_signature
                ),
                mint=mint,
                slot=slot,
                trade_timestamp=(
                    trade_timestamp
                ),
                predicted_at=(
                    predicted_at
                ),
                probability_2x_15m=(
                    probability_2x_15m
                ),
                signal_virtual_quote_reserves=(
                    signal_virtual_quote_reserves
                ),
                signal_virtual_token_reserves=(
                    signal_virtual_token_reserves
                ),
                signal_real_quote_reserves=(
                    signal_real_quote_reserves
                ),
                signal_real_token_reserves=(
                    signal_real_token_reserves
                ),
                quote_amount=(
                    quote_amount
                ),
                protocol_fee_lamports=(
                    protocol_fee_lamports
                ),
                creator_fee_lamports=(
                    creator_fee_lamports
                ),
                fee_resolution_status=(
                    fee_status
                ),
                protocol_fee_bps=None,
                creator_fee_bps=None,
                safety_status="NOT_RUN",
                safety_reasons=(),
                execution_status="NOT_RUN",
                execution_reasons=(),
                market_drift_bps=None,
                price_impact_bps=None,
                all_in_vs_signal_bps=None,
                live_curve_age_seconds=None,
                final_status="UNKNOWN",
                final_reasons=(
                    f"FEE_REGIME_UNRESOLVED:{fee_status}",
                ),
                scheduled_at=(
                    scheduled_at
                ),
                started_at=(
                    started_at
                ),
            )

            await persist(
                record
            )

            _mint_next_allowed_at[
                mint
            ] = (
                time.monotonic()
                + UNKNOWN_RETRY_SECONDS
            )

            return

        #
        # Structural token safety.
        #
        safety = await resolve_and_gate(
            mint
        )

        if (
            safety.status
            != SAFETY_PASS
            or safety.snapshot is None
        ):
            final_status = (
                "REJECT"
                if safety.status
                == "REJECT"
                else "UNKNOWN"
            )

            record = make_record(
                entry_signature=(
                    entry_signature
                ),
                mint=mint,
                slot=slot,
                trade_timestamp=(
                    trade_timestamp
                ),
                predicted_at=(
                    predicted_at
                ),
                probability_2x_15m=(
                    probability_2x_15m
                ),
                signal_virtual_quote_reserves=(
                    signal_virtual_quote_reserves
                ),
                signal_virtual_token_reserves=(
                    signal_virtual_token_reserves
                ),
                signal_real_quote_reserves=(
                    signal_real_quote_reserves
                ),
                signal_real_token_reserves=(
                    signal_real_token_reserves
                ),
                quote_amount=(
                    quote_amount
                ),
                protocol_fee_lamports=(
                    protocol_fee_lamports
                ),
                creator_fee_lamports=(
                    creator_fee_lamports
                ),
                fee_resolution_status=(
                    fee_status
                ),
                protocol_fee_bps=(
                    protocol_fee_bps
                ),
                creator_fee_bps=(
                    creator_fee_bps
                ),
                safety_status=(
                    safety.status
                ),
                safety_reasons=tuple(
                    safety.reasons
                ),
                execution_status="NOT_RUN",
                execution_reasons=(),
                market_drift_bps=None,
                price_impact_bps=None,
                all_in_vs_signal_bps=None,
                live_curve_age_seconds=None,
                final_status=(
                    final_status
                ),
                final_reasons=tuple(
                    f"SAFETY:{reason}"
                    for reason
                    in safety.reasons
                )
                or (
                    f"SAFETY:{safety.status}",
                ),
                scheduled_at=(
                    scheduled_at
                ),
                started_at=(
                    started_at
                ),
            )

            await persist(
                record
            )

            cooldown = (
                MINT_COOLDOWN_SECONDS
                if final_status
                == "REJECT"
                else UNKNOWN_RETRY_SECONDS
            )

            _mint_next_allowed_at[
                mint
            ] = (
                time.monotonic()
                + cooldown
            )

            return

        #
        # Fresh curve immediately before the
        # hypothetical order.
        #
        live_curve = (
            await resolve_live_pump_curve_state(
                mint=mint,
                min_context_slot=(
                    safety.snapshot.rpc_max_slot
                ),
            )
        )

        execution = (
            evaluate_execution_quality(
                snapshot=(
                    safety.snapshot
                ),
                live_curve=(
                    live_curve
                ),

                signal_virtual_quote_reserves=int(
                    signal_virtual_quote_reserves
                ),

                signal_virtual_token_reserves=int(
                    signal_virtual_token_reserves
                ),

                spendable_quote_in=(
                    SHADOW_SPENDABLE_QUOTE_IN
                ),

                protocol_fee_bps=int(
                    protocol_fee_bps
                ),

                creator_fee_bps=int(
                    creator_fee_bps
                ),

                slippage_bps=(
                    SHADOW_SLIPPAGE_BPS
                ),

                base_network_fee_lamports=(
                    SHADOW_BASE_NETWORK_FEE_LAMPORTS
                ),

                priority_fee_lamports=(
                    SHADOW_PRIORITY_FEE_LAMPORTS
                ),

                rent_lamports=(
                    SHADOW_RENT_LAMPORTS
                ),
            )
        )

        record = make_record(
            entry_signature=(
                entry_signature
            ),
            mint=mint,
            slot=slot,
            trade_timestamp=(
                trade_timestamp
            ),
            predicted_at=(
                predicted_at
            ),
            probability_2x_15m=(
                probability_2x_15m
            ),
            signal_virtual_quote_reserves=(
                signal_virtual_quote_reserves
            ),
            signal_virtual_token_reserves=(
                signal_virtual_token_reserves
            ),
            signal_real_quote_reserves=(
                signal_real_quote_reserves
            ),
            signal_real_token_reserves=(
                signal_real_token_reserves
            ),
            quote_amount=(
                quote_amount
            ),
            protocol_fee_lamports=(
                protocol_fee_lamports
            ),
            creator_fee_lamports=(
                creator_fee_lamports
            ),
            fee_resolution_status=(
                fee_status
            ),
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            safety_status=(
                safety.status
            ),
            safety_reasons=tuple(
                safety.reasons
            ),
            execution_status=(
                execution.status
            ),
            execution_reasons=tuple(
                execution.reasons
            ),
            market_drift_bps=(
                execution.market_drift_bps
            ),
            price_impact_bps=(
                execution.price_impact_bps
            ),
            all_in_vs_signal_bps=(
                execution.all_in_vs_signal_bps
            ),
            live_curve_age_seconds=(
                execution.snapshot_age_seconds
            ),
            final_status=(
                execution.status
            ),
            final_reasons=tuple(
                execution.reasons
            ),
            scheduled_at=(
                scheduled_at
            ),
            started_at=(
                started_at
            ),
        )

        await persist(
            record
        )

        cooldown = (
            UNKNOWN_RETRY_SECONDS
            if execution.status
            == "UNKNOWN"
            else MINT_COOLDOWN_SECONDS
        )

        _mint_next_allowed_at[
            mint
        ] = (
            time.monotonic()
            + cooldown
        )

        drift = (
            f"{execution.market_drift_bps:+.0f}bps"
            if execution.market_drift_bps
            is not None
            else "?"
        )

        print(
            "🧪 PRETRADE SHADOW | "
            f"p={probability_2x_15m:.3f} | "
            f"{mint[:8]}… | "
            f"safety={safety.status} | "
            f"execution={execution.status} | "
            f"drift={drift}"
        )

    except Exception as error:
        record = make_record(
            entry_signature=(
                entry_signature
            ),
            mint=mint,
            slot=slot,
            trade_timestamp=(
                trade_timestamp
            ),
            predicted_at=(
                predicted_at
            ),
            probability_2x_15m=(
                probability_2x_15m
            ),
            signal_virtual_quote_reserves=(
                signal_virtual_quote_reserves
            ),
            signal_virtual_token_reserves=(
                signal_virtual_token_reserves
            ),
            signal_real_quote_reserves=(
                signal_real_quote_reserves
            ),
            signal_real_token_reserves=(
                signal_real_token_reserves
            ),
            quote_amount=(
                quote_amount
            ),
            protocol_fee_lamports=(
                protocol_fee_lamports
            ),
            creator_fee_lamports=(
                creator_fee_lamports
            ),
            fee_resolution_status="ERROR",
            protocol_fee_bps=None,
            creator_fee_bps=None,
            safety_status="UNKNOWN",
            safety_reasons=(),
            execution_status="UNKNOWN",
            execution_reasons=(),
            market_drift_bps=None,
            price_impact_bps=None,
            all_in_vs_signal_bps=None,
            live_curve_age_seconds=None,
            final_status="UNKNOWN",
            final_reasons=(
                "PRETRADE_SHADOW_ERROR:"
                f"{type(error).__name__}",
            ),
            scheduled_at=(
                scheduled_at
            ),
            started_at=(
                started_at
            ),
        )

        try:
            await persist(
                record
            )
        except Exception as persist_error:
            print(
                "⚠️ PRETRADE SHADOW "
                "PERSIST ERROR | "
                f"{type(persist_error).__name__}: "
                f"{persist_error}"
            )

        _mint_next_allowed_at[
            mint
        ] = (
            time.monotonic()
            + UNKNOWN_RETRY_SECONDS
        )

        print(
            "⚠️ PRETRADE SHADOW ERROR | "
            f"{type(error).__name__}: "
            f"{error}"
        )

    finally:
        semaphore.release()


def task_done(
    task: asyncio.Task,
) -> None:
    _tasks.discard(
        task
    )

    try:
        task.result()

    except asyncio.CancelledError:
        pass

    except Exception as error:
        print(
            "⚠️ PRETRADE TASK ERROR | "
            f"{type(error).__name__}: "
            f"{error}"
        )


def schedule_pretrade_shadow_candidate(
    *,
    entry_signature: str,
    mint: str,
    event_user: str,
    quote_mint: str,
    slot: int | None,
    trade_timestamp: int,
    predicted_at: int,
    model_eligible: bool,
    probability_2x_15m: float | None,
    signal_virtual_quote_reserves: int,
    signal_virtual_token_reserves: int,
    signal_real_quote_reserves: int,
    signal_real_token_reserves: int,
    quote_amount: int,
    protocol_fee_lamports: int | None,
    creator_fee_lamports: int | None,
    event_protocol_fee_bps: int | None,
    event_creator_fee_bps: int | None,
) -> bool:
    """
    Very fast synchronous admission function.

    It performs no RPC work and no SQLite work.
    The expensive evaluation is scheduled on the
    existing asyncio event loop.
    """

    if not model_eligible:
        return False

    if probability_2x_15m is None:
        return False

    probability = float(
        probability_2x_15m
    )

    if (
        probability
        < CANDIDATE_PROBABILITY_THRESHOLD
    ):
        return False

    if quote_mint != SOL_QUOTE_MINT:
        return False

    now_monotonic = time.monotonic()

    next_allowed = (
        _mint_next_allowed_at.get(
            mint,
            0.0,
        )
    )

    if now_monotonic < next_allowed:
        return False

    #
    # Temporary admission lock prevents a burst
    # of same-mint BUYs from scheduling duplicate
    # evaluations before the first finishes.
    #
    _mint_next_allowed_at[
        mint
    ] = (
        now_monotonic
        + UNKNOWN_RETRY_SECONDS
    )

    scheduled_at = time.time()

    loop = asyncio.get_running_loop()

    task = loop.create_task(
        run_candidate(
            entry_signature=(
                entry_signature
            ),
            mint=mint,
            slot=slot,
            trade_timestamp=int(
                trade_timestamp
            ),
            predicted_at=int(
                predicted_at
            ),
            probability_2x_15m=(
                probability
            ),
            signal_virtual_quote_reserves=int(
                signal_virtual_quote_reserves
            ),
            signal_virtual_token_reserves=int(
                signal_virtual_token_reserves
            ),
            signal_real_quote_reserves=int(
                signal_real_quote_reserves
            ),
            signal_real_token_reserves=int(
                signal_real_token_reserves
            ),
            quote_amount=int(
                quote_amount
            ),
            protocol_fee_lamports=(
                protocol_fee_lamports
            ),
            creator_fee_lamports=(
                creator_fee_lamports
            ),

            event_protocol_fee_bps=(
                event_protocol_fee_bps
            ),

            event_creator_fee_bps=(
                event_creator_fee_bps
            ),
            scheduled_at=(
                scheduled_at
            ),
        )
    )

    _tasks.add(
        task
    )

    task.add_done_callback(
        task_done
    )

    return True