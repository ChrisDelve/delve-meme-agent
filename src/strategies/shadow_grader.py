import time
from collections import Counter, defaultdict

from src.data.market_db import get_connection
from src.data.runner_tracker import (
    calculate_peak_multiple,
    price_raw,
)


HORIZONS = {
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
}


def init_shadow_outcomes_table():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_outcomes (
                snapshot_id TEXT NOT NULL,
                wallet TEXT NOT NULL,
                mint TEXT NOT NULL,

                entry_signature TEXT NOT NULL,
                entry_trade_timestamp INTEGER NOT NULL,
                entry_slot INTEGER,
                quote_mint TEXT,

                entry_price_raw REAL NOT NULL,

                frozen_alpha_score REAL,
                frozen_confidence REAL,

                observed_rank INTEGER,
                entry_age_seconds INTEGER,

                raw_signal_count INTEGER NOT NULL,
                convergence_at_entry INTEGER NOT NULL,

                eligible_5m INTEGER NOT NULL,
                eligible_15m INTEGER NOT NULL,
                eligible_1h INTEGER NOT NULL,

                peak_multiple_5m REAL,
                peak_multiple_15m REAL,
                peak_multiple_1h REAL,

                hit_2x_5m INTEGER,
                hit_5x_5m INTEGER,
                hit_10x_5m INTEGER,

                hit_2x_15m INTEGER,
                hit_5x_15m INTEGER,
                hit_10x_15m INTEGER,

                hit_2x_1h INTEGER,
                hit_5x_1h INTEGER,
                hit_10x_1h INTEGER,

                graded_at INTEGER NOT NULL,

                PRIMARY KEY (
                    snapshot_id,
                    wallet,
                    mint
                )
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_shadow_outcomes_alpha
            ON shadow_outcomes(
                snapshot_id,
                frozen_alpha_score
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_shadow_outcomes_mint
            ON shadow_outcomes(
                snapshot_id,
                mint
            )
            """
        )

        connection.commit()


def load_coverage_intervals(connection):
    rows = connection.execute(
        """
        SELECT
            started_at,
            ended_at
        FROM collector_coverage
        WHERE
            valid = 1
            AND ended_at IS NOT NULL
        ORDER BY started_at
        """
    ).fetchall()

    intervals = []

    for row in rows:
        start = row["started_at"]
        end = row["ended_at"]

        if start is None or end is None:
            continue

        if end < start:
            continue

        if not intervals:
            intervals.append([start, end])
            continue

        previous = intervals[-1]

        # Merge only genuinely continuous/overlapping
        # observation windows.
        if start <= previous[1] + 1:
            previous[1] = max(
                previous[1],
                end,
            )
        else:
            intervals.append([start, end])

    return intervals


def has_continuous_coverage(
    intervals,
    start_timestamp,
    end_timestamp,
):
    for start, end in intervals:
        if (
            start <= start_timestamp
            and end >= end_timestamp
        ):
            return True

    return False


def calculate_horizon_result(
    entry_price,
    trade_rows,
):
    peak = calculate_peak_multiple(
        entry_price,
        trade_rows,
    )

    # The entry itself establishes 1.00x.
    # No later trades must NOT cause the
    # observation to disappear from the sample.
    if peak is None:
        peak = 1.0

    return {
        "peak": peak,
        "hit_2x": int(peak >= 2.0),
        "hit_5x": int(peak >= 5.0),
        "hit_10x": int(peak >= 10.0),
    }


def rebuild_shadow_outcomes():
    init_shadow_outcomes_table()

    with get_connection() as connection:
        active_snapshot = connection.execute(
            """
            SELECT
                snapshot_id,
                frozen_at
            FROM wallet_alpha_snapshot_runs
            WHERE active = 1
            LIMIT 1
            """
        ).fetchone()

        if active_snapshot is None:
            raise RuntimeError(
                "No active frozen wallet snapshot."
            )

        snapshot_id = active_snapshot[
            "snapshot_id"
        ]

        signals = connection.execute(
            """
            SELECT
                rowid AS event_id,
                *
            FROM shadow_signals
            WHERE snapshot_id = ?
            ORDER BY
                trade_timestamp,
                event_id
            """,
            (snapshot_id,),
        ).fetchall()

        if not signals:
            raise RuntimeError(
                "No shadow signals for active snapshot."
            )

        coverage_intervals = (
            load_coverage_intervals(connection)
        )

        pair_counts = Counter(
            (
                row["wallet"],
                row["mint"],
            )
            for row in signals
        )

        seen_pairs = set()

        # This is updated chronologically.
        # It never sees future signals.
        wallets_seen_by_mint = defaultdict(set)

        first_entries = []

        for signal in signals:
            key = (
                signal["wallet"],
                signal["mint"],
            )

            if key in seen_pairs:
                continue

            seen_pairs.add(key)

            wallets_seen_by_mint[
                signal["mint"]
            ].add(
                signal["wallet"]
            )

            convergence = len(
                wallets_seen_by_mint[
                    signal["mint"]
                ]
            )

            first_entries.append(
                (
                    signal,
                    pair_counts[key],
                    convergence,
                )
            )

        connection.execute(
            """
            DELETE FROM shadow_outcomes
            WHERE snapshot_id = ?
            """,
            (snapshot_id,),
        )

        graded = 0
        missing_entry_trade = 0
        invalid_entry_price = 0

        for (
            signal,
            raw_signal_count,
            convergence,
        ) in first_entries:

            entry_trade = connection.execute(
                """
                SELECT *
                FROM trades
                WHERE signature = ?
                LIMIT 1
                """,
                (
                    signal["signature"],
                ),
            ).fetchone()

            if entry_trade is None:
                missing_entry_trade += 1
                continue

            entry_price = price_raw(
                entry_trade["quote_amount"],
                entry_trade["token_amount"],
            )

            if entry_price is None:
                invalid_entry_price += 1
                continue

            entry_timestamp = signal[
                "trade_timestamp"
            ]

            eligibility = {}

            for name, seconds in HORIZONS.items():
                eligibility[name] = (
                    has_continuous_coverage(
                        coverage_intervals,
                        entry_timestamp,
                        entry_timestamp + seconds,
                    )
                )

            eligible_cutoffs = [
                entry_timestamp + HORIZONS[name]
                for name in HORIZONS
                if eligibility[name]
            ]

            later_trades = []

            if eligible_cutoffs:
                max_cutoff = max(
                    eligible_cutoffs
                )

                later_trades = connection.execute(
                    """
                    SELECT
                        signature,
                        mint,
                        quote_mint,
                        slot,
                        trade_timestamp,
                        quote_amount,
                        token_amount
                    FROM trades
                    WHERE
                        mint = ?
                        AND (
                            quote_mint = ?
                            OR (
                                quote_mint IS NULL
                                AND ? IS NULL
                            )
                        )
                        AND trade_timestamp > ?
                        AND trade_timestamp <= ?
                    ORDER BY
                        trade_timestamp,
                        slot,
                        signature
                    """,
                    (
                        signal["mint"],
                        signal["quote_mint"],
                        signal["quote_mint"],
                        entry_timestamp,
                        max_cutoff,
                    ),
                ).fetchall()

            results = {}

            for name, seconds in HORIZONS.items():
                if not eligibility[name]:
                    results[name] = None
                    continue

                cutoff = (
                    entry_timestamp
                    + seconds
                )

                horizon_trades = [
                    trade
                    for trade in later_trades
                    if (
                        trade[
                            "trade_timestamp"
                        ]
                        <= cutoff
                    )
                ]

                results[name] = (
                    calculate_horizon_result(
                        entry_price,
                        horizon_trades,
                    )
                )

            now = int(time.time())

            connection.execute(
                """
                INSERT OR REPLACE INTO shadow_outcomes (
                    snapshot_id,
                    wallet,
                    mint,

                    entry_signature,
                    entry_trade_timestamp,
                    entry_slot,
                    quote_mint,

                    entry_price_raw,

                    frozen_alpha_score,
                    frozen_confidence,

                    observed_rank,
                    entry_age_seconds,

                    raw_signal_count,
                    convergence_at_entry,

                    eligible_5m,
                    eligible_15m,
                    eligible_1h,

                    peak_multiple_5m,
                    peak_multiple_15m,
                    peak_multiple_1h,

                    hit_2x_5m,
                    hit_5x_5m,
                    hit_10x_5m,

                    hit_2x_15m,
                    hit_5x_15m,
                    hit_10x_15m,

                    hit_2x_1h,
                    hit_5x_1h,
                    hit_10x_1h,

                    graded_at
                )
                VALUES (
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?
                )
                """,
                (
                    snapshot_id,
                    signal["wallet"],
                    signal["mint"],

                    signal["signature"],
                    entry_timestamp,
                    signal["slot"],
                    signal["quote_mint"],

                    entry_price,

                    signal[
                        "frozen_alpha_score"
                    ],
                    signal[
                        "frozen_confidence"
                    ],

                    signal["observed_rank"],
                    signal[
                        "entry_age_seconds"
                    ],

                    raw_signal_count,
                    convergence,

                    int(
                        eligibility["5m"]
                    ),
                    int(
                        eligibility["15m"]
                    ),
                    int(
                        eligibility["1h"]
                    ),

                    (
                        results["5m"]["peak"]
                        if results["5m"]
                        else None
                    ),
                    (
                        results["15m"]["peak"]
                        if results["15m"]
                        else None
                    ),
                    (
                        results["1h"]["peak"]
                        if results["1h"]
                        else None
                    ),

                    (
                        results["5m"]["hit_2x"]
                        if results["5m"]
                        else None
                    ),
                    (
                        results["5m"]["hit_5x"]
                        if results["5m"]
                        else None
                    ),
                    (
                        results["5m"]["hit_10x"]
                        if results["5m"]
                        else None
                    ),

                    (
                        results["15m"]["hit_2x"]
                        if results["15m"]
                        else None
                    ),
                    (
                        results["15m"]["hit_5x"]
                        if results["15m"]
                        else None
                    ),
                    (
                        results["15m"]["hit_10x"]
                        if results["15m"]
                        else None
                    ),

                    (
                        results["1h"]["hit_2x"]
                        if results["1h"]
                        else None
                    ),
                    (
                        results["1h"]["hit_5x"]
                        if results["1h"]
                        else None
                    ),
                    (
                        results["1h"]["hit_10x"]
                        if results["1h"]
                        else None
                    ),

                    now,
                ),
            )

            graded += 1

        connection.commit()

        summary = connection.execute(
            """
            SELECT
                COUNT(*) AS outcomes,
                SUM(eligible_5m) AS eligible_5m,
                SUM(eligible_15m) AS eligible_15m,
                SUM(eligible_1h) AS eligible_1h
            FROM shadow_outcomes
            WHERE snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()

    print()
    print("=" * 72)
    print(
        "DELVE MEME AGENT — "
        "FORWARD SHADOW OUTCOMES"
    )
    print("=" * 72)
    print(
        f"Snapshot: {snapshot_id}"
    )
    print(
        "Raw signals: "
        f"{len(signals)}"
    )
    print(
        "Independent wallet/mints: "
        f"{len(first_entries)}"
    )
    print(
        f"Rows graded: {graded}"
    )
    print(
        "Missing entry trades: "
        f"{missing_entry_trade}"
    )
    print(
        "Invalid entry prices: "
        f"{invalid_entry_price}"
    )
    print()
    print(
        "Certified 5m:  "
        f"{summary['eligible_5m'] or 0}"
    )
    print(
        "Certified 15m: "
        f"{summary['eligible_15m'] or 0}"
    )
    print(
        "Certified 1h:  "
        f"{summary['eligible_1h'] or 0}"
    )
    print("=" * 72)


if __name__ == "__main__":
    rebuild_shadow_outcomes()