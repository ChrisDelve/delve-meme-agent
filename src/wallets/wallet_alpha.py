import math
import sqlite3
import statistics
import time
from collections import defaultdict
from pathlib import Path


DB_PATH = Path("logs/delve_meme.db")

# A wallet can be displayed before 10 mints,
# but it will NOT become strategy-eligible.
MIN_PROVISIONAL_MINTS = 10
MIN_ESTABLISHED_MINTS = 30

# Empirical-Bayes shrinkage.
# Roughly equivalent to 20 baseline observations
# before trusting a wallet's personal hit rate.
PRIOR_STRENGTH = 20.0


HORIZONS = {
    "5m": {
        "column": "peak_multiple_5m",
        "eligible_column": "eligible_5m",
    },
    "15m": {
        "column": "peak_multiple_15m",
        "eligible_column": "eligible_15m",
    },
    "1h": {
        "column": "peak_multiple_1h",
        "eligible_column": "eligible_1h",
    },
}


# Outcome importance.
#
# These are deliberately explicit rather than hidden
# in an opaque model. Historical backfill will later
# be used to tune them.
WEIGHTS = {
    ("5m", 2): 0.08,
    ("5m", 5): 0.12,
    ("5m", 10): 0.05,

    ("15m", 2): 0.12,
    ("15m", 5): 0.18,
    ("15m", 10): 0.10,

    ("1h", 2): 0.10,
    ("1h", 5): 0.15,
    ("1h", 10): 0.10,
}


def get_connection():
    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    return connection


def clamp(value, minimum, maximum):
    return max(
        minimum,
        min(maximum, value),
    )


def init_wallet_scores_table():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_scores (
                wallet TEXT PRIMARY KEY,

                distinct_mints INTEGER NOT NULL,

                median_entry_age_seconds REAL,
                median_entry_rank REAL,

                eligible_5m INTEGER,
                eligible_15m INTEGER,
                eligible_1h INTEGER,

                hit_2x_5m INTEGER,
                hit_5x_5m INTEGER,
                hit_10x_5m INTEGER,

                hit_2x_15m INTEGER,
                hit_5x_15m INTEGER,
                hit_10x_15m INTEGER,

                hit_2x_1h INTEGER,
                hit_5x_1h INTEGER,
                hit_10x_1h INTEGER,

                rate_2x_5m REAL,
                rate_5x_5m REAL,
                rate_10x_5m REAL,

                rate_2x_15m REAL,
                rate_5x_15m REAL,
                rate_10x_15m REAL,

                rate_2x_1h REAL,
                rate_5x_1h REAL,
                rate_10x_1h REAL,

                alpha_score REAL,
                confidence REAL,
                status TEXT,

                updated_at INTEGER
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_wallet_scores_alpha
            ON wallet_scores(alpha_score)
            """
        )


def load_first_entries():
    """
    Use exactly one observation per wallet + mint.

    If a wallet buys the same token five times,
    only its first validated entry counts as an
    independent reputation observation.
    """

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                entry_signature,
                buyer,
                mint,
                entry_timestamp,
                entry_rank,
                entry_age_seconds,

                eligible_5m,
                eligible_15m,
                eligible_1h,

                peak_multiple_5m,
                peak_multiple_15m,
                peak_multiple_1h

            FROM buy_outcomes

            WHERE
                entry_age_seconds IS NOT NULL
                AND entry_timestamp IS NOT NULL
                

            ORDER BY
                buyer ASC,
                mint ASC,
                entry_timestamp ASC,
                entry_signature ASC
            """
        ).fetchall()

    first_entries = []
    seen = set()

    for row in rows:
        key = (
            row["buyer"],
            row["mint"],
        )

        if key in seen:
            continue

        seen.add(key)
        first_entries.append(row)

    return first_entries


def is_horizon_eligible(
    entry,
    horizon_name,
):
    eligibility_column = HORIZONS[
        horizon_name
    ]["eligible_column"]

    return (
        entry[eligibility_column] == 1
    )


def get_peak(
    entry,
    horizon_name,
):
    column = HORIZONS[
        horizon_name
    ]["column"]

    return entry[column]


def calculate_baselines(entries):
    baseline = {}

    for horizon_name in HORIZONS:

        eligible = [
            entry
            for entry in entries
            if is_horizon_eligible(
                entry,
                horizon_name,
            )
        ]

        for target in (2, 5, 10):
            usable = [
                entry
                for entry in eligible
                if get_peak(
                    entry,
                    horizon_name,
                ) is not None
            ]

            hits = sum(
                1
                for entry in usable
                if get_peak(
                    entry,
                    horizon_name,
                ) >= target
            )

            n = len(usable)

            rate = (
                hits / n
                if n
                else 0.0
            )

            baseline[
                (horizon_name, target)
            ] = {
                "hits": hits,
                "n": n,
                "rate": rate,
            }

    return baseline


def posterior_rate(
    hits,
    n,
    baseline_rate,
):
    """
    Empirical-Bayes shrinkage.

    A tiny wallet sample remains close to the
    global population rate until enough evidence
    accumulates.
    """

    if n <= 0:
        return baseline_rate

    prior_hits = (
        baseline_rate
        * PRIOR_STRENGTH
    )

    return (
        hits + prior_hits
    ) / (
        n + PRIOR_STRENGTH
    )


def lift_to_score(
    posterior,
    baseline,
):
    """
    Baseline performance maps to 50.

    2x the population success rate maps near 75.
    4x maps near 100.

    Underperformance falls below 50.
    """

    if baseline <= 0:
        return 50.0

    if posterior <= 0:
        return 0.0

    lift = posterior / baseline

    score = (
        50.0
        + 25.0 * math.log2(lift)
    )

    return clamp(
        score,
        0.0,
        100.0,
    )


def evaluate_wallet(
    wallet,
    entries,
    baseline,
):
    ages = [
        entry["entry_age_seconds"]
        for entry in entries
        if entry["entry_age_seconds"]
        is not None
    ]

    ranks = [
        entry["entry_rank"]
        for entry in entries
        if entry["entry_rank"]
        is not None
    ]

    median_age = (
        statistics.median(ages)
        if ages
        else None
    )

    median_rank = (
        statistics.median(ranks)
        if ranks
        else None
    )

    metrics = {}

    weighted_score_sum = 0.0
    usable_weight_sum = 0.0

    for horizon_name in HORIZONS:

        eligible = [
            entry
            for entry in entries
            if (
                is_horizon_eligible(
                    entry,
                    horizon_name,
                )
                and get_peak(
                    entry,
                    horizon_name,
                ) is not None
            )
        ]

        n = len(eligible)

        metrics[
            f"eligible_{horizon_name}"
        ] = n

        for target in (2, 5, 10):

            hits = sum(
                1
                for entry in eligible
                if get_peak(
                    entry,
                    horizon_name,
                ) >= target
            )

            raw_rate = (
                hits / n
                if n
                else None
            )

            metrics[
                f"hit_{target}x_"
                f"{horizon_name}"
            ] = hits

            metrics[
                f"rate_{target}x_"
                f"{horizon_name}"
            ] = raw_rate

            baseline_rate = baseline[
                (
                    horizon_name,
                    target,
                )
            ]["rate"]

            posterior = posterior_rate(
                hits,
                n,
                baseline_rate,
            )

            component_score = (
                lift_to_score(
                    posterior,
                    baseline_rate,
                )
            )

            weight = WEIGHTS[
                (
                    horizon_name,
                    target,
                )
            ]

            # No eligible observations means
            # this component contributes nothing.
            if n > 0:
                weighted_score_sum += (
                    component_score
                    * weight
                )

                usable_weight_sum += weight

    if usable_weight_sum > 0:
        raw_alpha = (
            weighted_score_sum
            / usable_weight_sum
        )
    else:
        raw_alpha = 50.0

    distinct_mints = len(entries)

    eligible_15m = metrics[
        "eligible_15m"
    ]

    confidence = clamp(
        eligible_15m
        / MIN_ESTABLISHED_MINTS,
        0.0,
        1.0,
    )

    # Small samples are pulled toward neutral.
    alpha_score = (
        50.0
        + (
            raw_alpha - 50.0
        ) * confidence
    )

    if eligible_15m < MIN_PROVISIONAL_MINTS:
        status = "DISCOVERY"

    elif eligible_15m < MIN_ESTABLISHED_MINTS:
        status = "PROVISIONAL"

    else:
        status = "ESTABLISHED"

    return {
        "wallet": wallet,
        "distinct_mints": distinct_mints,
        "median_entry_age_seconds":
            median_age,
        "median_entry_rank":
            median_rank,
        "alpha_score":
            alpha_score,
        "confidence":
            confidence,
        "status":
            status,
        **metrics,
    }


def rebuild_wallet_scores():
    init_wallet_scores_table()

    first_entries = (
        load_first_entries()
    )

    if not first_entries:
        print(
            "❌ No eligible wallet outcomes "
            "found."
        )
        return

    baseline = calculate_baselines(
        first_entries
    )

    grouped = defaultdict(list)

    for entry in first_entries:
        grouped[
            entry["buyer"]
        ].append(entry)

    scores = []

    for wallet, entries in grouped.items():
        scores.append(
            evaluate_wallet(
                wallet,
                entries,
                baseline,
            )
        )

    now = int(time.time())

    with get_connection() as connection:

        connection.execute(
            """
            DELETE FROM wallet_scores
            """
        )

        for score in scores:

            connection.execute(
                """
                INSERT INTO wallet_scores (
                    wallet,

                    distinct_mints,

                    median_entry_age_seconds,
                    median_entry_rank,

                    eligible_5m,
                    eligible_15m,
                    eligible_1h,

                    hit_2x_5m,
                    hit_5x_5m,
                    hit_10x_5m,

                    hit_2x_15m,
                    hit_5x_15m,
                    hit_10x_15m,

                    hit_2x_1h,
                    hit_5x_1h,
                    hit_10x_1h,

                    rate_2x_5m,
                    rate_5x_5m,
                    rate_10x_5m,

                    rate_2x_15m,
                    rate_5x_15m,
                    rate_10x_15m,

                    rate_2x_1h,
                    rate_5x_1h,
                    rate_10x_1h,

                    alpha_score,
                    confidence,
                    status,

                    updated_at
                )

                VALUES (
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?
                )
                """,
                (
                    score["wallet"],

                    score[
                        "distinct_mints"
                    ],

                    score[
                        "median_entry_age_seconds"
                    ],

                    score[
                        "median_entry_rank"
                    ],

                    score[
                        "eligible_5m"
                    ],

                    score[
                        "eligible_15m"
                    ],

                    score[
                        "eligible_1h"
                    ],

                    score[
                        "hit_2x_5m"
                    ],

                    score[
                        "hit_5x_5m"
                    ],

                    score[
                        "hit_10x_5m"
                    ],

                    score[
                        "hit_2x_15m"
                    ],

                    score[
                        "hit_5x_15m"
                    ],

                    score[
                        "hit_10x_15m"
                    ],

                    score[
                        "hit_2x_1h"
                    ],

                    score[
                        "hit_5x_1h"
                    ],

                    score[
                        "hit_10x_1h"
                    ],

                    score[
                        "rate_2x_5m"
                    ],

                    score[
                        "rate_5x_5m"
                    ],

                    score[
                        "rate_10x_5m"
                    ],

                    score[
                        "rate_2x_15m"
                    ],

                    score[
                        "rate_5x_15m"
                    ],

                    score[
                        "rate_10x_15m"
                    ],

                    score[
                        "rate_2x_1h"
                    ],

                    score[
                        "rate_5x_1h"
                    ],

                    score[
                        "rate_10x_1h"
                    ],

                    score[
                        "alpha_score"
                    ],

                    score[
                        "confidence"
                    ],

                    score[
                        "status"
                    ],

                    now,
                ),
            )

        connection.commit()

    print()
    print("=" * 78)
    print(
        "🧠 DELVE MEME AGENT — "
        "WALLET ALPHA ENGINE"
    )
    print("=" * 78)

    print(
        f"Independent wallet/mint "
        f"entries: {len(first_entries)}"
    )

    print(
        f"Wallets evaluated:       "
        f"{len(scores)}"
    )

    print()

    print("POPULATION BASELINES")

    for horizon_name in HORIZONS:
        parts = []

        for target in (2, 5, 10):
            data = baseline[
                (
                    horizon_name,
                    target,
                )
            ]

            parts.append(
                f"{target}x="
                f"{data['rate'] * 100:.2f}% "
                f"(n={data['n']})"
            )

        print(
            f"{horizon_name:>3}: "
            + " | ".join(parts)
        )

    print()
    print("TOP ESTABLISHED WALLETS")
    print("-" * 78)

    established = [
        score
        for score in scores
        if score["status"]
        == "ESTABLISHED"
    ]

    established.sort(
        key=lambda item:
            item["alpha_score"],
        reverse=True,
    )

    if not established:
        print(
            "No wallets have reached "
            f"{MIN_ESTABLISHED_MINTS} "
            "independent mints yet."
        )

    else:
        for index, score in enumerate(
            established[:20],
            start=1,
        ):
            print()
            print(
                f"#{index} "
                f"{score['wallet']}"
            )

            print(
                f"Alpha:      "
                f"{score['alpha_score']:.2f}"
            )

            print(
                f"Mints:      "
                f"{score['distinct_mints']}"
            )

            print(
                f"Confidence: "
                f"{score['confidence']:.2f}"
            )

            print(
                f"Median Age: "
                f"{score['median_entry_age_seconds']}"
            )

            print(
                f"Median Rank:"
                f" {score['median_entry_rank']}"
            )

            print(
                "15m:        "
                f"2x "
                f"{(score['rate_2x_15m'] or 0) * 100:.1f}% | "
                f"5x "
                f"{(score['rate_5x_15m'] or 0) * 100:.1f}% | "
                f"10x "
                f"{(score['rate_10x_15m'] or 0) * 100:.1f}%"
            )

            print(
                "1h:         "
                f"2x "
                f"{(score['rate_2x_1h'] or 0) * 100:.1f}% | "
                f"5x "
                f"{(score['rate_5x_1h'] or 0) * 100:.1f}% | "
                f"10x "
                f"{(score['rate_10x_1h'] or 0) * 100:.1f}%"
            )

    print()
    print("=" * 78)


if __name__ == "__main__":
    rebuild_wallet_scores()