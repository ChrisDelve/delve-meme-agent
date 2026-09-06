import math
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from bisect import bisect_left

DB_PATH = Path("logs/delve_meme.db")

DIAGNOSTIC_VERSION = "feature-diagnostics-v1"
COHORT_VERSION = "research-cohort-v1"
FEATURE_VERSION = "derived-features-v1"
LABEL_VERSION = "fixed-horizon-labels-v1"

PRIMARY_HORIZON = "15m"
PRIMARY_TARGET = "hit_2x_15m"

# These are SCREENING thresholds, fixed before looking at validation.
MIN_MINT_COVERAGE = 0.60
MIN_VALID_MINTS = 300

CANDIDATE_EFFECT_PP = 3.0
CANDIDATE_MONOTONICITY = 0.45

WEAK_EFFECT_PP = 1.5
WEAK_MONOTONICITY = 0.25


# Intentionally representative rather than exhaustive.
# We are testing concepts, not stuffing every correlated window
# into the research process.
FEATURE_SPECS = [
    # ---------------------------------------------------------
    # ENTRY / TOKEN CONTEXT
    # ---------------------------------------------------------
    ("entry_rank", "entry_context"),
    ("entry_age_seconds", "entry_context"),
    ("entry_size_sol", "entry_context"),
    ("entry_price_return_from_pre", "entry_context"),
    ("prior_trade_gap_seconds", "entry_context"),
    ("launch_known", "entry_context"),
    ("seconds_since_launch", "entry_context"),
    ("mayhem_mode", "entry_context"),

    # ---------------------------------------------------------
    # ACTIVITY / VELOCITY
    # ---------------------------------------------------------
    ("buy_rate_5s", "activity"),
    ("buy_rate_15s", "activity"),
    ("buy_rate_60s", "activity"),

    ("sell_rate_5s", "activity"),
    ("sell_rate_15s", "activity"),
    ("sell_rate_60s", "activity"),

    ("trade_rate_5s", "activity"),
    ("trade_rate_15s", "activity"),
    ("trade_rate_60s", "activity"),

    # ---------------------------------------------------------
    # CAPITAL FLOW
    # ---------------------------------------------------------
    ("gross_sol_5s", "flow"),
    ("gross_sol_15s", "flow"),
    ("gross_sol_60s", "flow"),

    ("net_sol_rate_5s", "flow"),
    ("net_sol_rate_15s", "flow"),
    ("net_sol_rate_60s", "flow"),

    # ---------------------------------------------------------
    # IMBALANCE
    # ---------------------------------------------------------
    ("trade_imbalance_5s", "imbalance"),
    ("trade_imbalance_15s", "imbalance"),
    ("trade_imbalance_60s", "imbalance"),

    ("sol_imbalance_5s", "imbalance"),
    ("sol_imbalance_15s", "imbalance"),
    ("sol_imbalance_60s", "imbalance"),

    # ---------------------------------------------------------
    # PARTICIPATION
    # ---------------------------------------------------------
    ("unique_buyers_5s", "participation"),
    ("unique_buyers_15s", "participation"),
    ("unique_buyers_60s", "participation"),

    ("unique_sellers_5s", "participation"),
    ("unique_sellers_15s", "participation"),
    ("unique_sellers_60s", "participation"),

    ("buys_per_unique_buyer_15s", "participation"),
    ("sells_per_unique_seller_15s", "participation"),

    # ---------------------------------------------------------
    # ACCELERATION
    # ---------------------------------------------------------
    ("buy_rate_accel_5v10", "acceleration"),
    ("sell_rate_accel_5v10", "acceleration"),
    ("trade_rate_accel_5v10", "acceleration"),
    ("net_sol_accel_5v10", "acceleration"),

    ("buy_rate_ratio_5v10", "acceleration"),
    ("trade_rate_ratio_5v10", "acceleration"),

    ("buy_rate_accel_15v45", "acceleration"),
    ("sell_rate_accel_15v45", "acceleration"),
    ("trade_rate_accel_15v45", "acceleration"),
    ("net_sol_accel_15v45", "acceleration"),

    ("buy_rate_ratio_15v45", "acceleration"),
    ("trade_rate_ratio_15v45", "acceleration"),

    # ---------------------------------------------------------
    # RESERVE / CURVE STATE
    # Neutral state variables — not yet interpreted as
    # "bonding curve progress."
    # ---------------------------------------------------------
    ("virtual_sol_reserves_pre_sol", "reserve_state"),
    ("real_sol_reserves_pre_sol", "reserve_state"),
    ("real_to_virtual_sol_ratio_pre", "reserve_state"),
    ("real_to_virtual_token_ratio_pre", "reserve_state"),
]


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_tables(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS feature_diagnostic_summary (
            diagnostic_version TEXT NOT NULL,
            cohort_version TEXT NOT NULL,
            feature_version TEXT NOT NULL,
            label_version TEXT NOT NULL,

            feature_name TEXT NOT NULL,
            feature_family TEXT NOT NULL,

            development_rows INTEGER NOT NULL,
            development_mints INTEGER NOT NULL,

            valid_rows INTEGER NOT NULL,
            valid_mints INTEGER NOT NULL,

            row_coverage REAL NOT NULL,
            mint_coverage REAL NOT NULL,

            unique_values INTEGER NOT NULL,
            diagnostic_kind TEXT NOT NULL,

            global_baseline_rate REAL NOT NULL,
            known_subset_rate REAL,

            q10 REAL,
            q50 REAL,
            q90 REAL,

            low_bin_rate REAL,
            high_bin_rate REAL,

            effect_pp REAL,
            monotonicity REAL,
            direction TEXT,

            low_bin_mints INTEGER,
            high_bin_mints INTEGER,

            low_bin_max_mint_share REAL,
            high_bin_max_mint_share REAL,

            screening_score REAL,
            status TEXT NOT NULL,

            built_at INTEGER NOT NULL,

            PRIMARY KEY (
                diagnostic_version,
                feature_name
            )
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS feature_diagnostic_bins (
            diagnostic_version TEXT NOT NULL,
            feature_name TEXT NOT NULL,

            bin_number INTEGER NOT NULL,

            lower_value REAL,
            upper_value REAL,

            rows INTEGER NOT NULL,
            mints INTEGER NOT NULL,

            total_weight REAL NOT NULL,
            weighted_hit_rate REAL,

            max_mint_weight_share REAL,
            top10_mint_weight_share REAL,

            built_at INTEGER NOT NULL,

            PRIMARY KEY (
                diagnostic_version,
                feature_name,
                bin_number
            )
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_feature_diagnostic_summary_status
        ON feature_diagnostic_summary(
            diagnostic_version,
            status
        )
        """
    )

    connection.commit()


def weighted_mean(values):
    numerator = 0.0
    denominator = 0.0

    for value, weight in values:
        numerator += value * weight
        denominator += weight

    if denominator <= 0:
        return None

    return numerator / denominator


def weighted_quantile(sorted_rows, quantile):
    if not sorted_rows:
        return None

    total_weight = sum(row["weight"] for row in sorted_rows)

    if total_weight <= 0:
        return None

    target = total_weight * quantile
    cumulative = 0.0

    for row in sorted_rows:
        cumulative += row["weight"]

        if cumulative >= target:
            return row["value"]

    return sorted_rows[-1]["value"]


def correlation(xs, ys):
    if len(xs) != len(ys) or len(xs) < 2:
        return None

    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)

    numerator = sum(
        (x - mean_x) * (y - mean_y)
        for x, y in zip(xs, ys)
    )

    denominator_x = sum(
        (x - mean_x) ** 2
        for x in xs
    )

    denominator_y = sum(
        (y - mean_y) ** 2
        for y in ys
    )

    denominator = math.sqrt(
        denominator_x * denominator_y
    )

    if denominator == 0:
        return None

    return numerator / denominator


def summarize_bin(rows):
    if not rows:
        return None

    total_weight = sum(row["weight"] for row in rows)

    if total_weight <= 0:
        return None

    weighted_hits = sum(
        row["weight"] * row["target"]
        for row in rows
    )

    mint_weights = defaultdict(float)

    for row in rows:
        mint_weights[row["mint"]] += row["weight"]

    shares = sorted(
        (
            weight / total_weight
            for weight in mint_weights.values()
        ),
        reverse=True,
    )

    return {
        "rows": len(rows),
        "mints": len(mint_weights),
        "total_weight": total_weight,
        "hit_rate": weighted_hits / total_weight,
        "max_mint_share": shares[0] if shares else None,
        "top10_mint_share": sum(shares[:10]) if shares else None,
        "lower_value": min(row["value"] for row in rows),
        "upper_value": max(row["value"] for row in rows),
    }


def build_continuous_bins(rows, bin_count=10):
    """
    Build approximately weighted-quantile bins without ever splitting
    identical feature values across different bins.

    Fewer than bin_count bins may result when the feature has large
    point masses (for example many zero-valued observations). That is
    preferable to manufacturing artificial distinctions between equal
    values.
    """
    if not rows:
        return []

    sorted_rows = sorted(
        rows,
        key=lambda row: row["value"],
    )

    total_weight = sum(
        row["weight"]
        for row in sorted_rows
    )

    if total_weight <= 0:
        return []

    cutpoints = []

    for index in range(1, bin_count):
        quantile = index / bin_count

        cutpoint = weighted_quantile(
            sorted_rows,
            quantile,
        )

        if cutpoint is not None:
            cutpoints.append(cutpoint)

    # Repeated weighted quantiles are common for discrete/heaped
    # market features. Keep only unique boundaries.
    cutpoints = sorted(set(cutpoints))

    bins = [
        []
        for _ in range(len(cutpoints) + 1)
    ]

    for row in sorted_rows:
        # bisect_left guarantees values equal to a cutpoint remain
        # together on the same side of that boundary.
        bin_index = bisect_left(
            cutpoints,
            row["value"],
        )

        bins[bin_index].append(row)

    # Empty buckets can occur after duplicate boundaries collapse.
    # Remove them rather than presenting artificial empty deciles.
    return [
        bin_rows
        for bin_rows in bins
        if bin_rows
    ]


def build_discrete_bins(rows):
    grouped = defaultdict(list)

    for row in rows:
        grouped[row["value"]].append(row)

    return [
        grouped[value]
        for value in sorted(grouped.keys())
    ]


def screening_status(
    diagnostic_kind,
    mint_coverage,
    valid_mints,
    effect_pp,
    monotonicity,
):
    if (
        mint_coverage < MIN_MINT_COVERAGE
        or valid_mints < MIN_VALID_MINTS
    ):
        return "INSUFFICIENT_COVERAGE"

    if effect_pp is None:
        return "NO_CLEAR_SIGNAL"

    absolute_effect = abs(effect_pp)

    if diagnostic_kind == "binary":
        if absolute_effect >= CANDIDATE_EFFECT_PP:
            return "DEVELOPMENT_CANDIDATE"

        if absolute_effect >= WEAK_EFFECT_PP:
            return "WEAK_CANDIDATE"

        return "NO_CLEAR_SIGNAL"

    if monotonicity is None:
        return "NO_CLEAR_SIGNAL"

    absolute_monotonicity = abs(monotonicity)

    if (
        absolute_effect >= CANDIDATE_EFFECT_PP
        and absolute_monotonicity
        >= CANDIDATE_MONOTONICITY
    ):
        return "DEVELOPMENT_CANDIDATE"

    if (
        absolute_effect >= CANDIDATE_EFFECT_PP
        and absolute_monotonicity
        < CANDIDATE_MONOTONICITY
    ):
        return "NON_MONOTONIC"

    if (
        absolute_effect >= WEAK_EFFECT_PP
        and absolute_monotonicity
        >= WEAK_MONOTONICITY
    ):
        return "WEAK_CANDIDATE"

    return "NO_CLEAR_SIGNAL"


def load_development_baseline(connection):
    row = connection.execute(
        """
        SELECT
            COUNT(*) AS rows,
            COUNT(DISTINCT rc.mint) AS mints,
            SUM(rc.mint_weight_15m) AS total_weight,
            SUM(
                rc.mint_weight_15m
                * labels.hit_2x_15m
            ) AS weighted_hits

        FROM research_cohort rc

        JOIN fixed_horizon_labels labels
          ON labels.entry_signature
           = rc.entry_signature
         AND labels.label_version = ?

        WHERE
            rc.cohort_version = ?
            AND rc.split = 'development'
            AND rc.eligible_15m = 1
        """,
        (
            LABEL_VERSION,
            COHORT_VERSION,
        ),
    ).fetchone()

    baseline_rate = (
        row["weighted_hits"] / row["total_weight"]
        if row["total_weight"]
        else None
    )

    return {
        "rows": row["rows"],
        "mints": row["mints"],
        "total_weight": row["total_weight"],
        "rate": baseline_rate,
    }


def load_feature_rows(
    connection,
    feature_name,
):
    sql = f"""
        SELECT
            rc.mint AS mint,
            rc.mint_weight_15m AS row_weight,
            labels.hit_2x_15m AS target,
            d."{feature_name}" AS feature_value

        FROM research_cohort rc

        JOIN derived_features d
          ON d.entry_signature = rc.entry_signature
         AND d.derived_version = ?

        JOIN fixed_horizon_labels labels
          ON labels.entry_signature
           = rc.entry_signature
         AND labels.label_version = ?

        WHERE
            rc.cohort_version = ?
            AND rc.split = 'development'
            AND rc.eligible_15m = 1
    """

    raw_rows = connection.execute(
        sql,
        (
            FEATURE_VERSION,
            LABEL_VERSION,
            COHORT_VERSION,
        ),
    ).fetchall()

    valid_rows = []

    for row in raw_rows:
        value = row["feature_value"]
        weight = row["row_weight"]
        target = row["target"]

        if value is None:
            continue

        if weight is None or weight <= 0:
            continue

        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue

        if not math.isfinite(numeric_value):
            continue

        valid_rows.append(
            {
                "mint": row["mint"],
                "weight": float(weight),
                "target": int(target),
                "value": numeric_value,
            }
        )

    return raw_rows, valid_rows


def analyze_feature(
    connection,
    feature_name,
    feature_family,
    baseline,
    built_at,
):
    raw_rows, valid_rows = load_feature_rows(
        connection,
        feature_name,
    )

    valid_mints = {
        row["mint"]
        for row in valid_rows
    }

    valid_weight = sum(
        row["weight"]
        for row in valid_rows
    )

    row_coverage = (
        len(valid_rows) / baseline["rows"]
        if baseline["rows"]
        else 0.0
    )

    mint_coverage = (
        len(valid_mints) / baseline["mints"]
        if baseline["mints"]
        else 0.0
    )

    known_subset_rate = (
        sum(
            row["weight"] * row["target"]
            for row in valid_rows
        ) / valid_weight
        if valid_weight > 0
        else None
    )

    unique_values = sorted(
        {
            row["value"]
            for row in valid_rows
        }
    )

    unique_count = len(unique_values)

    if unique_count <= 1:
        diagnostic_kind = "constant"
        bins = build_discrete_bins(valid_rows)

    elif unique_count == 2:
        diagnostic_kind = "binary"
        bins = build_discrete_bins(valid_rows)

    elif unique_count <= 10:
        diagnostic_kind = "discrete"
        bins = build_discrete_bins(valid_rows)

    else:
        diagnostic_kind = "continuous"
        bins = build_continuous_bins(
            valid_rows,
            bin_count=10,
        )

    sorted_valid = sorted(
        valid_rows,
        key=lambda row: row["value"],
    )

    q10 = weighted_quantile(
        sorted_valid,
        0.10,
    )
    q50 = weighted_quantile(
        sorted_valid,
        0.50,
    )
    q90 = weighted_quantile(
        sorted_valid,
        0.90,
    )

    bin_summaries = []

    for bin_index, bin_rows in enumerate(
        bins,
        start=1,
    ):
        summary = summarize_bin(bin_rows)

        if summary is None:
            continue

        summary["bin_number"] = bin_index
        bin_summaries.append(summary)

        connection.execute(
            """
            INSERT INTO feature_diagnostic_bins (
                diagnostic_version,
                feature_name,

                bin_number,

                lower_value,
                upper_value,

                rows,
                mints,

                total_weight,
                weighted_hit_rate,

                max_mint_weight_share,
                top10_mint_weight_share,

                built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                DIAGNOSTIC_VERSION,
                feature_name,

                bin_index,

                summary["lower_value"],
                summary["upper_value"],

                summary["rows"],
                summary["mints"],

                summary["total_weight"],
                summary["hit_rate"],

                summary["max_mint_share"],
                summary["top10_mint_share"],

                built_at,
            ),
        )

    low_bin = (
        bin_summaries[0]
        if bin_summaries
        else None
    )

    high_bin = (
        bin_summaries[-1]
        if bin_summaries
        else None
    )

    low_rate = (
        low_bin["hit_rate"]
        if low_bin
        else None
    )

    high_rate = (
        high_bin["hit_rate"]
        if high_bin
        else None
    )

    effect_pp = (
        100.0 * (high_rate - low_rate)
        if (
            low_rate is not None
            and high_rate is not None
        )
        else None
    )

    monotonicity = None

    if diagnostic_kind not in (
        "binary",
        "constant",
    ):
        x_values = [
            summary["bin_number"]
            for summary in bin_summaries
        ]

        y_values = [
            summary["hit_rate"]
            for summary in bin_summaries
        ]

        monotonicity = correlation(
            x_values,
            y_values,
        )

    direction = "NONE"

    if effect_pp is not None:
        if effect_pp > 0:
            direction = "HIGHER_IS_BETTER"

        elif effect_pp < 0:
            direction = "LOWER_IS_BETTER"

    status = screening_status(
        diagnostic_kind,
        mint_coverage,
        len(valid_mints),
        effect_pp,
        monotonicity,
    )

    if diagnostic_kind == "binary":
        monotonic_component = 1.0

    elif monotonicity is None:
        monotonic_component = 0.0

    else:
        monotonic_component = abs(monotonicity)

    screening_score = (
        abs(effect_pp or 0.0)
        * monotonic_component
        * math.sqrt(max(mint_coverage, 0.0))
    )

    connection.execute(
        """
        INSERT INTO feature_diagnostic_summary (
            diagnostic_version,
            cohort_version,
            feature_version,
            label_version,

            feature_name,
            feature_family,

            development_rows,
            development_mints,

            valid_rows,
            valid_mints,

            row_coverage,
            mint_coverage,

            unique_values,
            diagnostic_kind,

            global_baseline_rate,
            known_subset_rate,

            q10,
            q50,
            q90,

            low_bin_rate,
            high_bin_rate,

            effect_pp,
            monotonicity,
            direction,

            low_bin_mints,
            high_bin_mints,

            low_bin_max_mint_share,
            high_bin_max_mint_share,

            screening_score,
            status,

            built_at
        )

        VALUES (
            ?, ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?
        )
        """,
        (
            DIAGNOSTIC_VERSION,
            COHORT_VERSION,
            FEATURE_VERSION,
            LABEL_VERSION,

            feature_name,
            feature_family,

            baseline["rows"],
            baseline["mints"],

            len(valid_rows),
            len(valid_mints),

            row_coverage,
            mint_coverage,

            unique_count,
            diagnostic_kind,

            baseline["rate"],
            known_subset_rate,

            q10,
            q50,
            q90,

            low_rate,
            high_rate,

            effect_pp,
            monotonicity,
            direction,

            low_bin["mints"] if low_bin else None,
            high_bin["mints"] if high_bin else None,

            (
                low_bin["max_mint_share"]
                if low_bin
                else None
            ),
            (
                high_bin["max_mint_share"]
                if high_bin
                else None
            ),

            screening_score,
            status,

            built_at,
        ),
    )


def rebuild_feature_diagnostics():
    started = time.time()
    built_at = int(time.time())

    with get_connection() as connection:
        init_tables(connection)

        print()
        print("=" * 78)
        print("DELVE MEME AGENT — DEVELOPMENT FEATURE DIAGNOSTICS")
        print("=" * 78)
        print(f"Version:          {DIAGNOSTIC_VERSION}")
        print(f"Cohort:           {COHORT_VERSION}")
        print("Split:            DEVELOPMENT ONLY")
        print("Primary horizon:  15 minutes")
        print("Primary target:   2x within 15 minutes")
        print("Weighting:        mint-balanced")
        print("VALIDATION DATA IS NOT READ")
        print("=" * 78)

        connection.execute(
            """
            DELETE FROM feature_diagnostic_bins
            WHERE diagnostic_version = ?
            """,
            (DIAGNOSTIC_VERSION,),
        )

        connection.execute(
            """
            DELETE FROM feature_diagnostic_summary
            WHERE diagnostic_version = ?
            """,
            (DIAGNOSTIC_VERSION,),
        )

        baseline = load_development_baseline(
            connection
        )

        print()
        print("DEVELOPMENT BASELINE")
        print("-" * 78)
        print(
            f"Rows:                     "
            f"{baseline['rows']}"
        )
        print(
            f"Mints:                    "
            f"{baseline['mints']}"
        )
        print(
            f"Total mint weight:        "
            f"{baseline['total_weight']:.3f}"
        )
        print(
            f"Mint-balanced 15m 2x:     "
            f"{100.0 * baseline['rate']:.2f}%"
        )

        print()
        print(
            f"Analyzing {len(FEATURE_SPECS)} "
            "pre-entry features..."
        )

        for index, (
            feature_name,
            feature_family,
        ) in enumerate(
            FEATURE_SPECS,
            start=1,
        ):
            print(
                f"[{index:02d}/{len(FEATURE_SPECS):02d}] "
                f"{feature_name}"
            )

            analyze_feature(
                connection,
                feature_name,
                feature_family,
                baseline,
                built_at,
            )

        connection.commit()

        top_rows = connection.execute(
            """
            SELECT
                feature_name,
                feature_family,
                status,
                ROUND(mint_coverage * 100.0, 1)
                    AS mint_coverage_pct,
                ROUND(effect_pp, 2)
                    AS effect_pp,
                ROUND(monotonicity, 3)
                    AS monotonicity,
                direction,
                ROUND(screening_score, 3)
                    AS screening_score

            FROM feature_diagnostic_summary

            WHERE diagnostic_version = ?

            ORDER BY
                screening_score DESC,
                ABS(effect_pp) DESC

            LIMIT 20
            """,
            (DIAGNOSTIC_VERSION,),
        ).fetchall()

        status_rows = connection.execute(
            """
            SELECT
                status,
                COUNT(*) AS features

            FROM feature_diagnostic_summary

            WHERE diagnostic_version = ?

            GROUP BY status

            ORDER BY features DESC
            """,
            (DIAGNOSTIC_VERSION,),
        ).fetchall()

        elapsed = time.time() - started

        print()
        print("=" * 78)
        print("STATUS COUNTS")
        print("=" * 78)

        for row in status_rows:
            print(
                f"{row['status']:<24}"
                f"{row['features']:>4}"
            )

        print()
        print("=" * 78)
        print("TOP DEVELOPMENT SCREENING RESULTS")
        print("=" * 78)

        for row in top_rows:
            monotonicity = row["monotonicity"]

            monotonic_text = (
                f"{monotonicity:.3f}"
                if monotonicity is not None
                else "N/A"
            )

            print(
                f"{row['feature_name']:<34}"
                f"{row['status']:<24}"
                f"effect={row['effect_pp']:>7.2f}pp  "
                f"mono={monotonic_text:>6}  "
                f"coverage={row['mint_coverage_pct']:>5.1f}%"
            )

        print()
        print(f"Runtime: {elapsed:.1f}s")
        print("=" * 78)


if __name__ == "__main__":
    rebuild_feature_diagnostics()