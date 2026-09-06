import sqlite3
import time
from pathlib import Path


DB_PATH = Path("logs/delve_meme.db")

COHORT_VERSION = "research-cohort-v1"
FEATURE_VERSION = "derived-features-v1"
LABEL_VERSION = "fixed-horizon-labels-v1"

DEVELOPMENT_SESSION_ID = 2
VALIDATION_SESSION_ID = 3


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_cohort_table(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS research_cohort (
            entry_signature TEXT NOT NULL,
            cohort_version TEXT NOT NULL,

            mint TEXT NOT NULL,
            wallet TEXT NOT NULL,
            entry_timestamp INTEGER NOT NULL,

            session_id INTEGER NOT NULL,
            split TEXT NOT NULL,

            mint_seen_in_development INTEGER NOT NULL,
            primary_validation INTEGER NOT NULL,

            eligible_5m INTEGER NOT NULL,
            eligible_15m INTEGER NOT NULL,
            eligible_1h INTEGER NOT NULL,

            mint_rows_5m INTEGER,
            mint_rows_15m INTEGER,
            mint_rows_1h INTEGER,

            mint_weight_5m REAL,
            mint_weight_15m REAL,
            mint_weight_1h REAL,

            built_at INTEGER NOT NULL,

            PRIMARY KEY (
                entry_signature,
                cohort_version
            )
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_research_cohort_split
        ON research_cohort(
            cohort_version,
            split
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_research_cohort_mint
        ON research_cohort(
            cohort_version,
            mint
        )
        """
    )

    connection.commit()


def rebuild_research_cohort():
    started = time.time()
    built_at = int(time.time())

    with get_connection() as connection:
        init_cohort_table(connection)

        print()
        print("=" * 72)
        print("DELVE MEME AGENT — RESEARCH COHORT BUILDER")
        print("=" * 72)
        print(f"Version:     {COHORT_VERSION}")
        print(f"Development: collector session {DEVELOPMENT_SESSION_ID}")
        print(f"Validation:  collector session {VALIDATION_SESSION_ID}")
        print("Primary horizon: 15m")
        print("Primary validation requires unseen mint")
        print("Mint-balanced weighting")
        print("=" * 72)

        connection.execute(
            """
            DELETE FROM research_cohort
            WHERE cohort_version = ?
            """,
            (COHORT_VERSION,),
        )

        connection.execute(
            """
            INSERT INTO research_cohort (
                entry_signature,
                cohort_version,

                mint,
                wallet,
                entry_timestamp,

                session_id,
                split,

                mint_seen_in_development,
                primary_validation,

                eligible_5m,
                eligible_15m,
                eligible_1h,

                mint_rows_5m,
                mint_rows_15m,
                mint_rows_1h,

                mint_weight_5m,
                mint_weight_15m,
                mint_weight_1h,

                built_at
            )

            WITH base AS (
                SELECT
                    d.entry_signature,
                    d.mint,
                    d.wallet,
                    d.entry_timestamp,

                    c.id AS session_id,

                    CASE
                        WHEN c.id = ?
                        THEN 'development'

                        WHEN c.id = ?
                        THEN 'validation'
                    END AS split,

                    l.eligible_5m,
                    l.eligible_15m,
                    l.eligible_1h

                FROM derived_features d

                JOIN fixed_horizon_labels l
                  ON l.entry_signature = d.entry_signature
                 AND l.label_version = ?

                JOIN collector_coverage c
                  ON c.valid = 1
                 AND d.entry_timestamp >= c.started_at
                 AND d.entry_timestamp <= c.ended_at

                WHERE
                    d.derived_version = ?
                    AND c.id IN (?, ?)
            ),

            development_mints AS (
                SELECT DISTINCT mint
                FROM base
                WHERE
                    session_id = ?
                    AND eligible_15m = 1
            ),

            marked AS (
                SELECT
                    b.*,

                    CASE
                        WHEN EXISTS (
                            SELECT 1
                            FROM development_mints dm
                            WHERE dm.mint = b.mint
                        )
                        THEN 1
                        ELSE 0
                    END AS mint_seen_in_development

                FROM base b
            ),

            counted AS (
                SELECT
                    m.*,

                    SUM(
                        CASE
                            WHEN eligible_5m = 1
                            THEN 1 ELSE 0
                        END
                    ) OVER (
                        PARTITION BY session_id, mint
                    ) AS mint_rows_5m,

                    SUM(
                        CASE
                            WHEN eligible_15m = 1
                            THEN 1 ELSE 0
                        END
                    ) OVER (
                        PARTITION BY session_id, mint
                    ) AS mint_rows_15m,

                    SUM(
                        CASE
                            WHEN eligible_1h = 1
                            THEN 1 ELSE 0
                        END
                    ) OVER (
                        PARTITION BY session_id, mint
                    ) AS mint_rows_1h

                FROM marked m
            )

            SELECT
                entry_signature,
                ?,

                mint,
                wallet,
                entry_timestamp,

                session_id,
                split,

                mint_seen_in_development,

                CASE
                    WHEN
                        session_id = ?
                        AND eligible_15m = 1
                        AND mint_seen_in_development = 0
                    THEN 1
                    ELSE 0
                END,

                eligible_5m,
                eligible_15m,
                eligible_1h,

                mint_rows_5m,
                mint_rows_15m,
                mint_rows_1h,

                CASE
                    WHEN
                        eligible_5m = 1
                        AND mint_rows_5m > 0
                    THEN 1.0 / mint_rows_5m
                END,

                CASE
                    WHEN
                        eligible_15m = 1
                        AND mint_rows_15m > 0
                    THEN 1.0 / mint_rows_15m
                END,

                CASE
                    WHEN
                        eligible_1h = 1
                        AND mint_rows_1h > 0
                    THEN 1.0 / mint_rows_1h
                END,

                ?

            FROM counted
            """,
            (
                DEVELOPMENT_SESSION_ID,
                VALIDATION_SESSION_ID,
                LABEL_VERSION,
                FEATURE_VERSION,
                DEVELOPMENT_SESSION_ID,
                VALIDATION_SESSION_ID,
                DEVELOPMENT_SESSION_ID,
                COHORT_VERSION,
                VALIDATION_SESSION_ID,
                built_at,
            ),
        )

        connection.commit()

        summary = connection.execute(
            """
            SELECT
                split,

                COUNT(*) AS rows,
                COUNT(DISTINCT mint) AS mints,

                SUM(eligible_5m) AS eligible_5m,
                SUM(eligible_15m) AS eligible_15m,
                SUM(eligible_1h) AS eligible_1h,

                SUM(primary_validation) AS primary_validation_rows,

                COUNT(
                    DISTINCT CASE
                        WHEN primary_validation = 1
                        THEN mint
                    END
                ) AS primary_validation_mints,

                ROUND(
                    SUM(
                        CASE
                            WHEN eligible_15m = 1
                            THEN mint_weight_15m
                            ELSE 0
                        END
                    ),
                    6
                ) AS total_15m_mint_weight

            FROM research_cohort

            WHERE cohort_version = ?

            GROUP BY split

            ORDER BY
                CASE split
                    WHEN 'development' THEN 1
                    WHEN 'validation' THEN 2
                    ELSE 3
                END
            """,
            (COHORT_VERSION,),
        ).fetchall()

        elapsed = time.time() - started

        print()
        print("RESEARCH COHORT BUILD COMPLETE")
        print("=" * 72)

        for row in summary:
            print()
            print(f"Split:                    {row['split']}")
            print(f"Rows:                     {row['rows']}")
            print(f"Mints:                    {row['mints']}")
            print(f"Eligible 5m:              {row['eligible_5m']}")
            print(f"Eligible 15m:             {row['eligible_15m']}")
            print(f"Eligible 1h:              {row['eligible_1h']}")
            print(
                "Primary validation rows:  "
                f"{row['primary_validation_rows']}"
            )
            print(
                "Primary validation mints: "
                f"{row['primary_validation_mints']}"
            )
            print(
                "Total 15m mint weight:     "
                f"{row['total_15m_mint_weight']}"
            )

        print()
        print(f"Runtime: {elapsed:.1f}s")
        print("=" * 72)


if __name__ == "__main__":
    rebuild_research_cohort()