import sqlite3
import time
from pathlib import Path


DB_PATH = Path("logs/delve_meme.db")

LABEL_VERSION = "fixed-horizon-labels-v1"
SOURCE_VERSION = "causal-runner-outcomes-v1"


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_label_table(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS fixed_horizon_labels (
            entry_signature TEXT NOT NULL,
            label_version TEXT NOT NULL,
            source_version TEXT NOT NULL,

            mint TEXT NOT NULL,
            entry_timestamp INTEGER NOT NULL,

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

            source_updated_at INTEGER,
            built_at INTEGER NOT NULL,

            PRIMARY KEY (
                entry_signature,
                label_version
            )
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_fixed_labels_version_timestamp
        ON fixed_horizon_labels(
            label_version,
            entry_timestamp
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_fixed_labels_version_mint
        ON fixed_horizon_labels(
            label_version,
            mint
        )
        """
    )

    connection.commit()


def rebuild_fixed_horizon_labels():
    started = time.time()
    built_at = int(time.time())

    with get_connection() as connection:
        init_label_table(connection)

        print()
        print("=" * 72)
        print("DELVE MEME AGENT — FIXED-HORIZON LABEL BUILDER")
        print("=" * 72)
        print(f"Version: {LABEL_VERSION}")
        print(f"Source:  {SOURCE_VERSION}")
        print("Horizons: 5m / 15m / 1h")
        print("No all-time outcomes")
        print("No unrestricted time-to-runner labels")
        print("=" * 72)

        connection.execute(
            """
            DELETE FROM fixed_horizon_labels
            WHERE label_version = ?
            """,
            (LABEL_VERSION,),
        )

        connection.execute(
            """
            INSERT INTO fixed_horizon_labels (
                entry_signature,
                label_version,
                source_version,

                mint,
                entry_timestamp,

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

                source_updated_at,
                built_at
            )

            SELECT
                entry_signature,
                ?,
                ?,

                mint,
                entry_timestamp,

                eligible_5m,
                eligible_15m,
                eligible_1h,

                peak_multiple_5m,
                peak_multiple_15m,
                peak_multiple_1h,

                CASE
                    WHEN eligible_5m = 1
                    THEN CASE
                        WHEN peak_multiple_5m >= 2.0
                        THEN 1 ELSE 0
                    END
                END,

                CASE
                    WHEN eligible_5m = 1
                    THEN CASE
                        WHEN peak_multiple_5m >= 5.0
                        THEN 1 ELSE 0
                    END
                END,

                CASE
                    WHEN eligible_5m = 1
                    THEN CASE
                        WHEN peak_multiple_5m >= 10.0
                        THEN 1 ELSE 0
                    END
                END,

                CASE
                    WHEN eligible_15m = 1
                    THEN CASE
                        WHEN peak_multiple_15m >= 2.0
                        THEN 1 ELSE 0
                    END
                END,

                CASE
                    WHEN eligible_15m = 1
                    THEN CASE
                        WHEN peak_multiple_15m >= 5.0
                        THEN 1 ELSE 0
                    END
                END,

                CASE
                    WHEN eligible_15m = 1
                    THEN CASE
                        WHEN peak_multiple_15m >= 10.0
                        THEN 1 ELSE 0
                    END
                END,

                CASE
                    WHEN eligible_1h = 1
                    THEN CASE
                        WHEN peak_multiple_1h >= 2.0
                        THEN 1 ELSE 0
                    END
                END,

                CASE
                    WHEN eligible_1h = 1
                    THEN CASE
                        WHEN peak_multiple_1h >= 5.0
                        THEN 1 ELSE 0
                    END
                END,

                CASE
                    WHEN eligible_1h = 1
                    THEN CASE
                        WHEN peak_multiple_1h >= 10.0
                        THEN 1 ELSE 0
                    END
                END,

                updated_at,
                ?

            FROM buy_outcomes
            """,
            (
                LABEL_VERSION,
                SOURCE_VERSION,
                built_at,
            ),
        )

        connection.commit()

        summary = connection.execute(
            """
            SELECT
                COUNT(*) AS rows,
                COUNT(DISTINCT entry_signature) AS signatures,
                COUNT(DISTINCT mint) AS mints,

                SUM(eligible_5m) AS eligible_5m,
                SUM(eligible_15m) AS eligible_15m,
                SUM(eligible_1h) AS eligible_1h,

                SUM(hit_2x_5m) AS hit_2x_5m,
                SUM(hit_2x_15m) AS hit_2x_15m,
                SUM(hit_2x_1h) AS hit_2x_1h

            FROM fixed_horizon_labels

            WHERE label_version = ?
            """,
            (LABEL_VERSION,),
        ).fetchone()

        elapsed = time.time() - started

        print()
        print("LABEL BUILD COMPLETE")
        print("=" * 72)
        print(f"Rows:             {summary['rows']}")
        print(f"Signatures:       {summary['signatures']}")
        print(f"Mints:            {summary['mints']}")
        print(f"Eligible 5m:      {summary['eligible_5m']}")
        print(f"Eligible 15m:     {summary['eligible_15m']}")
        print(f"Eligible 1h:      {summary['eligible_1h']}")
        print(f"2x within 5m:     {summary['hit_2x_5m']}")
        print(f"2x within 15m:    {summary['hit_2x_15m']}")
        print(f"2x within 1h:     {summary['hit_2x_1h']}")
        print(f"Runtime:          {elapsed:.1f}s")
        print("=" * 72)


if __name__ == "__main__":
    rebuild_fixed_horizon_labels()