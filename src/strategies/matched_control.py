import bisect
import math
import time
from statistics import mean, median

from src.data.market_db import get_connection


ANALYSIS_VERSION = "matched-control-v1"


def init_matched_control_table():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS matched_control_pairs (
                analysis_version TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,

                treated_signature TEXT NOT NULL,
                treated_wallet TEXT NOT NULL,
                treated_mint TEXT NOT NULL,
                treated_alpha REAL NOT NULL,
                treated_rank INTEGER NOT NULL,
                treated_age_seconds INTEGER NOT NULL,
                treated_timestamp INTEGER NOT NULL,
                treated_quote_amount INTEGER NOT NULL,
                treated_mayhem_mode INTEGER NOT NULL,
                treated_peak_15m REAL,
                treated_hit_2x_15m INTEGER NOT NULL,
                treated_hit_5x_15m INTEGER NOT NULL,
                treated_hit_10x_15m INTEGER NOT NULL,

                control_signature TEXT NOT NULL,
                control_wallet TEXT NOT NULL,
                control_mint TEXT NOT NULL,
                control_rank INTEGER NOT NULL,
                control_age_seconds INTEGER NOT NULL,
                control_timestamp INTEGER NOT NULL,
                control_quote_amount INTEGER NOT NULL,
                control_mayhem_mode INTEGER NOT NULL,
                control_peak_15m REAL,
                control_hit_2x_15m INTEGER NOT NULL,
                control_hit_5x_15m INTEGER NOT NULL,
                control_hit_10x_15m INTEGER NOT NULL,

                rank_gap INTEGER NOT NULL,
                age_gap_seconds INTEGER NOT NULL,
                time_gap_seconds INTEGER NOT NULL,
                size_ratio REAL NOT NULL,
                match_tier INTEGER NOT NULL,

                created_at INTEGER NOT NULL,

                PRIMARY KEY (
                    analysis_version,
                    snapshot_id,
                    treated_signature
                ),

                UNIQUE (
                    analysis_version,
                    snapshot_id,
                    control_signature
                )
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_matched_control_snapshot
            ON matched_control_pairs(
                snapshot_id,
                analysis_version
            )
            """
        )

        connection.commit()


def hit_flags(peak):
    if peak is None:
        peak = 1.0

    return (
        int(peak >= 2.0),
        int(peak >= 5.0),
        int(peak >= 10.0),
    )


def match_limits(tier):
    if tier == 1:
        return {
            "time": 60,
            "rank": 10,
            "age": 15,
            "size_ratio": 2.0,
        }

    if tier == 2:
        return {
            "time": 180,
            "rank": 20,
            "age": 30,
            "size_ratio": 4.0,
        }

    return {
        "time": 300,
        "rank": 30,
        "age": 60,
        "size_ratio": 8.0,
    }


def calculate_match_metrics(treated, control):
    rank_gap = abs(
        treated["observed_rank"]
        - control["entry_rank"]
    )

    age_gap = abs(
        treated["entry_age_seconds"]
        - control["entry_age_seconds"]
    )

    time_gap = abs(
        treated["entry_trade_timestamp"]
        - control["entry_timestamp"]
    )

    treated_size = treated["quote_amount"]
    control_size = control["entry_quote_amount"]

    size_ratio = (
        max(treated_size, control_size)
        / min(treated_size, control_size)
    )

    cost = (
        (rank_gap / 10.0)
        + (age_gap / 30.0)
        + (time_gap / 60.0)
        + math.log(size_ratio, 2)
    )

    return (
        cost,
        rank_gap,
        age_gap,
        time_gap,
        size_ratio,
    )


def exact_mcnemar_pvalue(
    treated_only,
    control_only,
):
    discordant = treated_only + control_only

    if discordant == 0:
        return 1.0

    smaller = min(
        treated_only,
        control_only,
    )

    lower_tail = sum(
        math.comb(discordant, k)
        for k in range(smaller + 1)
    ) / (2 ** discordant)

    return min(
        1.0,
        2.0 * lower_tail,
    )


def rebuild_matched_controls():
    init_matched_control_table()

    with get_connection() as connection:
        active_snapshot = connection.execute(
            """
            SELECT snapshot_id
            FROM wallet_alpha_snapshot_runs
            WHERE active = 1
            LIMIT 1
            """
        ).fetchone()

        if active_snapshot is None:
            raise RuntimeError(
                "No active frozen wallet snapshot."
            )

        snapshot_id = active_snapshot["snapshot_id"]

        treated = connection.execute(
            """
            SELECT
                so.entry_signature,
                so.wallet,
                so.mint,
                so.frozen_alpha_score,
                so.observed_rank,
                so.entry_age_seconds,
                so.entry_trade_timestamp,
                so.peak_multiple_15m,
                so.hit_2x_15m,
                so.hit_5x_15m,
                so.hit_10x_15m,

                t.quote_mint,
                t.quote_amount,
                COALESCE(t.mayhem_mode, 0)
                    AS mayhem_mode

            FROM shadow_outcomes AS so

            JOIN trades AS t
              ON t.signature = so.entry_signature

            WHERE
                so.snapshot_id = ?
                AND so.eligible_15m = 1
                AND so.frozen_alpha_score >= 80
                AND so.observed_rank IS NOT NULL
                AND so.entry_age_seconds IS NOT NULL
                AND t.quote_amount > 0

            ORDER BY
                so.entry_trade_timestamp,
                so.entry_signature
            """,
            (snapshot_id,),
        ).fetchall()

        if not treated:
            raise RuntimeError(
                "No eligible high-alpha shadow outcomes."
            )

        start_ts = min(
            row["entry_trade_timestamp"]
            for row in treated
        )

        end_ts = max(
            row["entry_trade_timestamp"]
            for row in treated
        )

        controls = connection.execute(
            """
            WITH ordinary AS (
                SELECT
                    bo.entry_signature,
                    bo.buyer,
                    bo.mint,
                    bo.quote_mint,
                    bo.entry_timestamp,
                    bo.entry_rank,
                    bo.entry_age_seconds,
                    bo.entry_quote_amount,
                    bo.peak_multiple_15m,

                    COALESCE(t.mayhem_mode, 0)
                        AS mayhem_mode,

                    ROW_NUMBER() OVER (
                        PARTITION BY
                            bo.buyer,
                            bo.mint
                        ORDER BY
                            bo.entry_timestamp,
                            bo.entry_signature
                    ) AS rn

                FROM buy_outcomes AS bo

                JOIN trades AS t
                  ON t.signature = bo.entry_signature

                WHERE
                    bo.eligible_15m = 1

                    AND bo.entry_timestamp
                        BETWEEN ? AND ?

                    AND bo.entry_rank IS NOT NULL
                    AND bo.entry_age_seconds IS NOT NULL
                    AND bo.entry_quote_amount > 0
                    AND bo.entry_price_raw IS NOT NULL

                    AND NOT EXISTS (
                        SELECT 1
                        FROM frozen_wallet_scores AS f
                        WHERE
                            f.snapshot_id = ?
                            AND f.wallet = bo.buyer
                    )
            )

            SELECT *
            FROM ordinary
            WHERE rn = 1

            ORDER BY
                entry_timestamp,
                entry_signature
            """,
            (
                start_ts,
                end_ts,
                snapshot_id,
            ),
        ).fetchall()

        if not controls:
            raise RuntimeError(
                "No eligible same-period ordinary controls."
            )

        connection.execute(
            """
            DELETE FROM matched_control_pairs
            WHERE
                analysis_version = ?
                AND snapshot_id = ?
            """,
            (
                ANALYSIS_VERSION,
                snapshot_id,
            ),
        )

        control_times = [
            row["entry_timestamp"]
            for row in controls
        ]

        used_controls = set()
        matches = []

        for treated_row in treated:
            chosen = None

            for tier in (1, 2, 3):
                limits = match_limits(tier)

                treated_ts = treated_row[
                    "entry_trade_timestamp"
                ]

                left = bisect.bisect_left(
                    control_times,
                    treated_ts - limits["time"],
                )

                right = bisect.bisect_right(
                    control_times,
                    treated_ts + limits["time"],
                )

                best = None

                for control in controls[left:right]:
                    control_signature = control[
                        "entry_signature"
                    ]

                    if control_signature in used_controls:
                        continue

                    # Different mint intentionally:
                    # this tests total predictive value,
                    # not same-token timing.
                    if (
                        control["mint"]
                        == treated_row["mint"]
                    ):
                        continue

                    if (
                        control["quote_mint"]
                        != treated_row["quote_mint"]
                    ):
                        continue

                    if (
                        control["mayhem_mode"]
                        != treated_row["mayhem_mode"]
                    ):
                        continue

                    (
                        cost,
                        rank_gap,
                        age_gap,
                        time_gap,
                        size_ratio,
                    ) = calculate_match_metrics(
                        treated_row,
                        control,
                    )

                    if rank_gap > limits["rank"]:
                        continue

                    if age_gap > limits["age"]:
                        continue

                    if (
                        size_ratio
                        > limits["size_ratio"]
                    ):
                        continue

                    tie_break = (
                        cost,
                        control_signature,
                    )

                    if (
                        best is None
                        or tie_break < best[0]
                    ):
                        best = (
                            tie_break,
                            control,
                            rank_gap,
                            age_gap,
                            time_gap,
                            size_ratio,
                            tier,
                        )

                if best is not None:
                    chosen = best
                    break

            if chosen is None:
                continue

            (
                _,
                control,
                rank_gap,
                age_gap,
                time_gap,
                size_ratio,
                tier,
            ) = chosen

            used_controls.add(
                control["entry_signature"]
            )

            (
                control_2x,
                control_5x,
                control_10x,
            ) = hit_flags(
                control["peak_multiple_15m"]
            )

            matches.append(
                (
                    treated_row,
                    control,
                    control_2x,
                    control_5x,
                    control_10x,
                    rank_gap,
                    age_gap,
                    time_gap,
                    size_ratio,
                    tier,
                )
            )

        now = int(time.time())

        for (
            treated_row,
            control,
            control_2x,
            control_5x,
            control_10x,
            rank_gap,
            age_gap,
            time_gap,
            size_ratio,
            tier,
        ) in matches:

            connection.execute(
                """
                INSERT INTO matched_control_pairs (
                    analysis_version,
                    snapshot_id,

                    treated_signature,
                    treated_wallet,
                    treated_mint,
                    treated_alpha,
                    treated_rank,
                    treated_age_seconds,
                    treated_timestamp,
                    treated_quote_amount,
                    treated_mayhem_mode,
                    treated_peak_15m,
                    treated_hit_2x_15m,
                    treated_hit_5x_15m,
                    treated_hit_10x_15m,

                    control_signature,
                    control_wallet,
                    control_mint,
                    control_rank,
                    control_age_seconds,
                    control_timestamp,
                    control_quote_amount,
                    control_mayhem_mode,
                    control_peak_15m,
                    control_hit_2x_15m,
                    control_hit_5x_15m,
                    control_hit_10x_15m,

                    rank_gap,
                    age_gap_seconds,
                    time_gap_seconds,
                    size_ratio,
                    match_tier,

                    created_at
                )

                VALUES (
                    ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?
                )
                """,
                (
                    ANALYSIS_VERSION,
                    snapshot_id,

                    treated_row["entry_signature"],
                    treated_row["wallet"],
                    treated_row["mint"],
                    treated_row["frozen_alpha_score"],
                    treated_row["observed_rank"],
                    treated_row["entry_age_seconds"],
                    treated_row["entry_trade_timestamp"],
                    treated_row["quote_amount"],
                    treated_row["mayhem_mode"],
                    treated_row["peak_multiple_15m"],
                    treated_row["hit_2x_15m"],
                    treated_row["hit_5x_15m"],
                    treated_row["hit_10x_15m"],

                    control["entry_signature"],
                    control["buyer"],
                    control["mint"],
                    control["entry_rank"],
                    control["entry_age_seconds"],
                    control["entry_timestamp"],
                    control["entry_quote_amount"],
                    control["mayhem_mode"],
                    control["peak_multiple_15m"],
                    control_2x,
                    control_5x,
                    control_10x,

                    rank_gap,
                    age_gap,
                    time_gap,
                    size_ratio,
                    tier,

                    now,
                ),
            )

        connection.commit()

        rows = connection.execute(
            """
            SELECT *
            FROM matched_control_pairs
            WHERE
                analysis_version = ?
                AND snapshot_id = ?
            ORDER BY treated_timestamp
            """,
            (
                ANALYSIS_VERSION,
                snapshot_id,
            ),
        ).fetchall()

    if not rows:
        raise RuntimeError(
            "No matched control pairs were created."
        )

    tier_counts = {}

    for row in rows:
        tier = row["match_tier"]

        tier_counts[tier] = (
            tier_counts.get(tier, 0) + 1
        )

    print()
    print("=" * 72)
    print(
        "DELVE MEME AGENT — "
        "SAME-PERIOD MATCHED CONTROL"
    )
    print("=" * 72)

    print(f"Snapshot: {snapshot_id}")

    print(
        f"Eligible high-alpha entries: {len(treated)}"
    )

    print(
        f"Ordinary control pool:        {len(controls)}"
    )

    print(
        f"Matched pairs:                {len(rows)}"
    )

    print(
        "Match rate:                   "
        f"{100.0 * len(rows) / len(treated):.1f}%"
    )

    print(
        f"Tier counts:                  {tier_counts}"
    )

    print()

    print(
        "Median rank gap:              "
        f"{median(row['rank_gap'] for row in rows):.1f}"
    )

    print(
        "Median age gap:               "
        f"{median(row['age_gap_seconds'] for row in rows):.1f}s"
    )

    print(
        "Median time gap:              "
        f"{median(row['time_gap_seconds'] for row in rows):.1f}s"
    )

    print(
        "Median size ratio:            "
        f"{median(row['size_ratio'] for row in rows):.2f}x"
    )

    print()

    for threshold in ("2x", "5x", "10x"):
        treated_key = (
            f"treated_hit_{threshold}_15m"
        )

        control_key = (
            f"control_hit_{threshold}_15m"
        )

        treated_rate = mean(
            row[treated_key]
            for row in rows
        )

        control_rate = mean(
            row[control_key]
            for row in rows
        )

        treated_only = sum(
            1
            for row in rows
            if (
                row[treated_key] == 1
                and row[control_key] == 0
            )
        )

        control_only = sum(
            1
            for row in rows
            if (
                row[treated_key] == 0
                and row[control_key] == 1
            )
        )

        p_value = exact_mcnemar_pvalue(
            treated_only,
            control_only,
        )

        print(
            f"15m {threshold}: "
            f"treated={100.0 * treated_rate:.2f}%  "
            f"control={100.0 * control_rate:.2f}%  "
            f"delta="
            f"{100.0 * (treated_rate - control_rate):+.2f}pp"
        )

        print(
            "           discordant "
            f"T-only={treated_only} "
            f"C-only={control_only} "
            f"exact-p={p_value:.6g}"
        )

    print("=" * 72)


if __name__ == "__main__":
    rebuild_matched_controls()