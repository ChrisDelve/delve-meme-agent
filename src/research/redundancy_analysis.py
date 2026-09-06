import math
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path


DB_PATH = Path("logs/delve_meme.db")

ANALYSIS_VERSION = "redundancy-analysis-v1"
DIAGNOSTIC_VERSION = "feature-diagnostics-v1"
COHORT_VERSION = "research-cohort-v1"
FEATURE_VERSION = "derived-features-v1"

CANDIDATE_STATUS = "DEVELOPMENT_CANDIDATE"

# Locked before validation is opened.
REDUNDANCY_THRESHOLD = 0.85
REVIEW_THRESHOLD = 0.70

MIN_SHARED_MINTS = 300
MIN_SHARED_MINT_COVERAGE = 0.60


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_tables(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS feature_redundancy_pairs (
            analysis_version TEXT NOT NULL,

            feature_a TEXT NOT NULL,
            feature_b TEXT NOT NULL,

            family_a TEXT NOT NULL,
            family_b TEXT NOT NULL,

            shared_rows INTEGER NOT NULL,
            shared_mints INTEGER NOT NULL,
            shared_mint_coverage REAL NOT NULL,

            weighted_rank_correlation REAL,
            abs_correlation REAL,

            relation TEXT NOT NULL,

            built_at INTEGER NOT NULL,

            PRIMARY KEY (
                analysis_version,
                feature_a,
                feature_b
            )
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS feature_redundancy_clusters (
            analysis_version TEXT NOT NULL,

            cluster_id INTEGER NOT NULL,
            cluster_size INTEGER NOT NULL,

            feature_name TEXT NOT NULL,
            feature_family TEXT NOT NULL,

            effect_pp REAL,
            mint_coverage REAL,
            screening_score REAL,
            direction TEXT,

            is_representative INTEGER NOT NULL,

            built_at INTEGER NOT NULL,

            PRIMARY KEY (
                analysis_version,
                feature_name
            )
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_redundancy_pairs_relation
        ON feature_redundancy_pairs(
            analysis_version,
            relation
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_redundancy_clusters_cluster
        ON feature_redundancy_clusters(
            analysis_version,
            cluster_id
        )
        """
    )

    connection.commit()


def safe_identifier(name):
    if not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*",
        name,
    ):
        raise ValueError(
            f"Unsafe SQL identifier: {name!r}"
        )

    return name


def load_candidates(connection):
    rows = connection.execute(
        """
        SELECT
            feature_name,
            feature_family,
            effect_pp,
            mint_coverage,
            screening_score,
            direction,
            valid_mints

        FROM feature_diagnostic_summary

        WHERE
            diagnostic_version = ?
            AND status = ?

        ORDER BY
            screening_score DESC,
            ABS(effect_pp) DESC,
            feature_name ASC
        """,
        (
            DIAGNOSTIC_VERSION,
            CANDIDATE_STATUS,
        ),
    ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def verify_feature_columns(
    connection,
    feature_names,
):
    columns = connection.execute(
        """
        PRAGMA table_info(derived_features)
        """
    ).fetchall()

    known_columns = {
        row["name"]
        for row in columns
    }

    missing = [
        feature
        for feature in feature_names
        if feature not in known_columns
    ]

    if missing:
        raise RuntimeError(
            "Candidate feature columns missing from "
            f"derived_features: {missing}"
        )


def load_development_rows(
    connection,
    feature_names,
):
    safe_names = [
        safe_identifier(name)
        for name in feature_names
    ]

    feature_sql = ",\n".join(
        f'd."{name}" AS "{name}"'
        for name in safe_names
    )

    sql = f"""
        SELECT
            rc.entry_signature,
            rc.mint,
            {feature_sql}

        FROM research_cohort rc

        JOIN derived_features d
          ON d.entry_signature = rc.entry_signature
         AND d.derived_version = ?

        WHERE
            rc.cohort_version = ?
            AND rc.split = 'development'
            AND rc.eligible_15m = 1
    """

    return connection.execute(
        sql,
        (
            FEATURE_VERSION,
            COHORT_VERSION,
        ),
    ).fetchall()


def numeric_or_none(value):
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def weighted_rank_vector(
    raw_values,
    mints,
):
    """
    Build a tie-safe weighted percentile-rank vector.

    Each mint contributes one total unit of weight among rows where
    this feature is known. This prevents highly active mints from
    determining the rank transformation.

    Identical feature values always receive exactly the same rank.
    """
    valid_indices = []
    mint_counts = defaultdict(int)

    numeric_values = [
        numeric_or_none(value)
        for value in raw_values
    ]

    for index, value in enumerate(
        numeric_values
    ):
        if value is None:
            continue

        mint = mints[index]

        valid_indices.append(index)
        mint_counts[mint] += 1

    ranks = [
        None
        for _ in raw_values
    ]

    if not valid_indices:
        return ranks

    row_weights = {}

    for index in valid_indices:
        mint = mints[index]

        row_weights[index] = (
            1.0 / mint_counts[mint]
        )

    ordered = sorted(
        valid_indices,
        key=lambda index: numeric_values[index],
    )

    total_weight = sum(
        row_weights[index]
        for index in ordered
    )

    if total_weight <= 0:
        return ranks

    cumulative_weight = 0.0
    position = 0

    while position < len(ordered):
        start = position
        value = numeric_values[
            ordered[position]
        ]

        while (
            position < len(ordered)
            and numeric_values[
                ordered[position]
            ] == value
        ):
            position += 1

        tied_indices = ordered[
            start:position
        ]

        tied_weight = sum(
            row_weights[index]
            for index in tied_indices
        )

        weighted_midrank = (
            cumulative_weight
            + (tied_weight / 2.0)
        ) / total_weight

        for index in tied_indices:
            ranks[index] = weighted_midrank

        cumulative_weight += tied_weight

    return ranks


def weighted_correlation(
    xs,
    ys,
    weights,
):
    if not xs:
        return None

    if not (
        len(xs)
        == len(ys)
        == len(weights)
    ):
        raise ValueError(
            "Correlation input lengths differ"
        )

    total_weight = sum(weights)

    if total_weight <= 0:
        return None

    mean_x = sum(
        x * weight
        for x, weight in zip(
            xs,
            weights,
        )
    ) / total_weight

    mean_y = sum(
        y * weight
        for y, weight in zip(
            ys,
            weights,
        )
    ) / total_weight

    covariance = sum(
        weight
        * (x - mean_x)
        * (y - mean_y)
        for x, y, weight in zip(
            xs,
            ys,
            weights,
        )
    )

    variance_x = sum(
        weight
        * (x - mean_x) ** 2
        for x, weight in zip(
            xs,
            weights,
        )
    )

    variance_y = sum(
        weight
        * (y - mean_y) ** 2
        for y, weight in zip(
            ys,
            weights,
        )
    )

    denominator = math.sqrt(
        variance_x * variance_y
    )

    if denominator <= 0:
        return None

    return covariance / denominator


def analyze_pair(
    feature_a,
    feature_b,
    ranks_a,
    ranks_b,
    mints,
    development_mints,
):
    shared_indices = []

    mint_counts = defaultdict(int)

    for index in range(len(mints)):
        if (
            ranks_a[index] is None
            or ranks_b[index] is None
        ):
            continue

        shared_indices.append(index)
        mint_counts[mints[index]] += 1

    shared_rows = len(shared_indices)
    shared_mints = len(mint_counts)

    shared_mint_coverage = (
        shared_mints
        / development_mints
        if development_mints
        else 0.0
    )

    if not shared_indices:
        return {
            "shared_rows": 0,
            "shared_mints": 0,
            "shared_mint_coverage": 0.0,
            "correlation": None,
            "abs_correlation": None,
            "relation": "INSUFFICIENT_OVERLAP",
        }

    xs = []
    ys = []
    weights = []

    for index in shared_indices:
        mint = mints[index]

        # Rebalance again for the specific feature pair.
        # Every mint contributes one total unit to this pair.
        pair_weight = (
            1.0 / mint_counts[mint]
        )

        xs.append(ranks_a[index])
        ys.append(ranks_b[index])
        weights.append(pair_weight)

    correlation = weighted_correlation(
        xs,
        ys,
        weights,
    )

    abs_correlation = (
        abs(correlation)
        if correlation is not None
        else None
    )

    if (
        shared_mints < MIN_SHARED_MINTS
        or shared_mint_coverage
        < MIN_SHARED_MINT_COVERAGE
    ):
        relation = "INSUFFICIENT_OVERLAP"

    elif (
        abs_correlation is not None
        and abs_correlation
        >= REDUNDANCY_THRESHOLD
    ):
        relation = "REDUNDANT"

    elif (
        abs_correlation is not None
        and abs_correlation
        >= REVIEW_THRESHOLD
    ):
        relation = "REVIEW"

    else:
        relation = "DISTINCT"

    return {
        "shared_rows": shared_rows,
        "shared_mints": shared_mints,
        "shared_mint_coverage":
            shared_mint_coverage,
        "correlation": correlation,
        "abs_correlation":
            abs_correlation,
        "relation": relation,
    }


def pair_key(
    feature_a,
    feature_b,
):
    return tuple(
        sorted(
            (
                feature_a,
                feature_b,
            )
        )
    )


def build_complete_link_clusters(
    candidates,
    pair_lookup,
):
    """
    Conservative clustering.

    A feature may join an existing cluster only when it is classified
    REDUNDANT with every feature already inside that cluster.

    This prevents transitive chaining such as:
        A ~ B
        B ~ C
        but A !~ C

    from incorrectly turning A/B/C into one redundant block.
    """
    clusters = []

    for candidate in candidates:
        feature = candidate["feature_name"]

        placed = False

        for cluster in clusters:
            compatible = True

            for member in cluster:
                key = pair_key(
                    feature,
                    member["feature_name"],
                )

                pair = pair_lookup.get(key)

                if (
                    pair is None
                    or pair["relation"]
                    != "REDUNDANT"
                ):
                    compatible = False
                    break

            if compatible:
                cluster.append(candidate)
                placed = True
                break

        if not placed:
            clusters.append(
                [candidate]
            )

    return clusters


def rebuild_redundancy_analysis():
    started = time.time()
    built_at = int(time.time())

    with get_connection() as connection:
        init_tables(connection)

        print()
        print("=" * 78)
        print(
            "DELVE MEME AGENT — "
            "DEVELOPMENT REDUNDANCY ANALYSIS"
        )
        print("=" * 78)
        print(f"Version:          {ANALYSIS_VERSION}")
        print(f"Diagnostics:      {DIAGNOSTIC_VERSION}")
        print("Split:            DEVELOPMENT ONLY")
        print("Primary horizon:  15 minutes")
        print("Dependence:       mint-balanced weighted rank correlation")
        print(
            f"Redundant:        |rho| >= "
            f"{REDUNDANCY_THRESHOLD:.2f}"
        )
        print(
            f"Review:           |rho| >= "
            f"{REVIEW_THRESHOLD:.2f}"
        )
        print("VALIDATION DATA IS NOT READ")
        print("=" * 78)

        connection.execute(
            """
            DELETE FROM feature_redundancy_pairs
            WHERE analysis_version = ?
            """,
            (ANALYSIS_VERSION,),
        )

        connection.execute(
            """
            DELETE FROM feature_redundancy_clusters
            WHERE analysis_version = ?
            """,
            (ANALYSIS_VERSION,),
        )

        candidates = load_candidates(
            connection
        )

        feature_names = [
            candidate["feature_name"]
            for candidate in candidates
        ]

        if len(feature_names) < 2:
            raise RuntimeError(
                "Need at least two development "
                "candidates for redundancy analysis"
            )

        verify_feature_columns(
            connection,
            feature_names,
        )

        development_mints = connection.execute(
            """
            SELECT
                COUNT(DISTINCT mint) AS mints

            FROM research_cohort

            WHERE
                cohort_version = ?
                AND split = 'development'
                AND eligible_15m = 1
            """,
            (COHORT_VERSION,),
        ).fetchone()["mints"]

        rows = load_development_rows(
            connection,
            feature_names,
        )

        mints = [
            row["mint"]
            for row in rows
        ]

        print()
        print("INPUT")
        print("-" * 78)
        print(
            f"Development candidates:   "
            f"{len(candidates)}"
        )
        print(
            f"Development rows:         "
            f"{len(rows)}"
        )
        print(
            f"Development mints:        "
            f"{development_mints}"
        )

        print()
        print(
            "Building mint-balanced "
            "tie-safe rank vectors..."
        )

        rank_vectors = {}

        for index, feature in enumerate(
            feature_names,
            start=1,
        ):
            print(
                f"[{index:02d}/{len(feature_names):02d}] "
                f"{feature}"
            )

            raw_values = [
                row[feature]
                for row in rows
            ]

            rank_vectors[feature] = (
                weighted_rank_vector(
                    raw_values,
                    mints,
                )
            )

        candidate_by_name = {
            candidate["feature_name"]:
                candidate
            for candidate in candidates
        }

        pair_lookup = {}

        total_pairs = (
            len(feature_names)
            * (len(feature_names) - 1)
            // 2
        )

        print()
        print(
            f"Analyzing {total_pairs} "
            "candidate pairs..."
        )

        pair_number = 0

        for first_index in range(
            len(feature_names)
        ):
            feature_a = feature_names[
                first_index
            ]

            for second_index in range(
                first_index + 1,
                len(feature_names),
            ):
                feature_b = feature_names[
                    second_index
                ]

                pair_number += 1

                result = analyze_pair(
                    feature_a,
                    feature_b,
                    rank_vectors[feature_a],
                    rank_vectors[feature_b],
                    mints,
                    development_mints,
                )

                key = pair_key(
                    feature_a,
                    feature_b,
                )

                pair_lookup[key] = result

                candidate_a = (
                    candidate_by_name[
                        feature_a
                    ]
                )

                candidate_b = (
                    candidate_by_name[
                        feature_b
                    ]
                )

                connection.execute(
                    """
                    INSERT INTO feature_redundancy_pairs (
                        analysis_version,

                        feature_a,
                        feature_b,

                        family_a,
                        family_b,

                        shared_rows,
                        shared_mints,
                        shared_mint_coverage,

                        weighted_rank_correlation,
                        abs_correlation,

                        relation,

                        built_at
                    )
                    VALUES (
                        ?, ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?,
                        ?
                    )
                    """,
                    (
                        ANALYSIS_VERSION,

                        feature_a,
                        feature_b,

                        candidate_a[
                            "feature_family"
                        ],
                        candidate_b[
                            "feature_family"
                        ],

                        result["shared_rows"],
                        result["shared_mints"],
                        result[
                            "shared_mint_coverage"
                        ],

                        result["correlation"],
                        result[
                            "abs_correlation"
                        ],

                        result["relation"],

                        built_at,
                    ),
                )

        clusters = build_complete_link_clusters(
            candidates,
            pair_lookup,
        )

        for cluster_id, cluster in enumerate(
            clusters,
            start=1,
        ):
            # Candidates were loaded in descending
            # development screening order.
            representative = cluster[0][
                "feature_name"
            ]

            cluster_size = len(cluster)

            for candidate in cluster:
                connection.execute(
                    """
                    INSERT INTO feature_redundancy_clusters (
                        analysis_version,

                        cluster_id,
                        cluster_size,

                        feature_name,
                        feature_family,

                        effect_pp,
                        mint_coverage,
                        screening_score,
                        direction,

                        is_representative,

                        built_at
                    )
                    VALUES (
                        ?, ?, ?,
                        ?, ?,
                        ?, ?, ?, ?,
                        ?,
                        ?
                    )
                    """,
                    (
                        ANALYSIS_VERSION,

                        cluster_id,
                        cluster_size,

                        candidate[
                            "feature_name"
                        ],
                        candidate[
                            "feature_family"
                        ],

                        candidate["effect_pp"],
                        candidate[
                            "mint_coverage"
                        ],
                        candidate[
                            "screening_score"
                        ],
                        candidate["direction"],

                        int(
                            candidate[
                                "feature_name"
                            ] == representative
                        ),

                        built_at,
                    ),
                )

        connection.commit()

        relation_counts = (
            connection.execute(
                """
                SELECT
                    relation,
                    COUNT(*) AS pairs

                FROM feature_redundancy_pairs

                WHERE analysis_version = ?

                GROUP BY relation

                ORDER BY pairs DESC
                """,
                (ANALYSIS_VERSION,),
            ).fetchall()
        )

        top_pairs = connection.execute(
            """
            SELECT
                feature_a,
                feature_b,
                ROUND(
                    weighted_rank_correlation,
                    4
                ) AS rho,
                relation,
                shared_mints

            FROM feature_redundancy_pairs

            WHERE
                analysis_version = ?
                AND weighted_rank_correlation
                    IS NOT NULL

            ORDER BY
                abs_correlation DESC,
                feature_a,
                feature_b

            LIMIT 20
            """,
            (ANALYSIS_VERSION,),
        ).fetchall()

        multi_clusters = sum(
            1
            for cluster in clusters
            if len(cluster) > 1
        )

        singleton_clusters = sum(
            1
            for cluster in clusters
            if len(cluster) == 1
        )

        elapsed = time.time() - started

        print()
        print("=" * 78)
        print("PAIR RELATION COUNTS")
        print("=" * 78)

        for row in relation_counts:
            print(
                f"{row['relation']:<24}"
                f"{row['pairs']:>5}"
            )

        print()
        print("=" * 78)
        print("CLUSTER SUMMARY")
        print("=" * 78)
        print(
            f"Candidate features:       "
            f"{len(candidates)}"
        )
        print(
            f"Redundancy clusters:      "
            f"{len(clusters)}"
        )
        print(
            f"Multi-feature clusters:   "
            f"{multi_clusters}"
        )
        print(
            f"Singleton clusters:       "
            f"{singleton_clusters}"
        )

        print()
        print("=" * 78)
        print("REDUNDANCY CLUSTERS")
        print("=" * 78)

        for cluster_id, cluster in enumerate(
            clusters,
            start=1,
        ):
            representative = cluster[0][
                "feature_name"
            ]

            print()
            print(
                f"Cluster {cluster_id:02d} "
                f"(size={len(cluster)})"
            )
            print(
                f"  Representative: "
                f"{representative}"
            )

            for candidate in cluster:
                marker = (
                    "*"
                    if candidate[
                        "feature_name"
                    ] == representative
                    else "-"
                )

                print(
                    f"  {marker} "
                    f"{candidate['feature_name']:<34}"
                    f"{candidate['feature_family']}"
                )

        print()
        print("=" * 78)
        print("STRONGEST PAIRWISE DEPENDENCIES")
        print("=" * 78)

        for row in top_pairs:
            print(
                f"{row['feature_a']:<30}"
                f"{row['feature_b']:<30}"
                f"rho={row['rho']:>7.4f}  "
                f"{row['relation']:<12}"
                f"mints={row['shared_mints']}"
            )

        print()
        print(f"Runtime: {elapsed:.1f}s")
        print("=" * 78)


if __name__ == "__main__":
    rebuild_redundancy_analysis()