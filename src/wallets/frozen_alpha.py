import time

from src.data.market_db import get_connection


SNAPSHOT_VERSION = "wallet-alpha-v1"


def init_snapshot_tables():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_alpha_snapshot_runs (
                snapshot_id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                frozen_at INTEGER NOT NULL,
                wallet_count INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS frozen_wallet_scores (
                snapshot_id TEXT NOT NULL,
                wallet TEXT NOT NULL,

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
                source_updated_at INTEGER,

                PRIMARY KEY (
                    snapshot_id,
                    wallet
                )
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_frozen_wallet_snapshot
            ON frozen_wallet_scores(
                snapshot_id,
                wallet
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_frozen_wallet_alpha
            ON frozen_wallet_scores(
                snapshot_id,
                alpha_score
            )
            """
        )

        connection.commit()


def freeze_current_wallet_scores():
    init_snapshot_tables()

    frozen_at = int(time.time())
    snapshot_id = (
        f"{SNAPSHOT_VERSION}-{frozen_at}"
    )

    with get_connection() as connection:
        established_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM wallet_scores
            WHERE status = 'ESTABLISHED'
            """
        ).fetchone()[0]

        if established_count == 0:
            raise RuntimeError(
                "No ESTABLISHED wallet scores found."
            )

        # Only the pointer is mutable.
        # The frozen wallet rows themselves are never
        # updated or replaced.
        connection.execute(
            """
            UPDATE wallet_alpha_snapshot_runs
            SET active = 0
            WHERE active = 1
            """
        )

        connection.execute(
            """
            INSERT INTO wallet_alpha_snapshot_runs (
                snapshot_id,
                version,
                frozen_at,
                wallet_count,
                active
            )
            VALUES (?, ?, ?, ?, 1)
            """,
            (
                snapshot_id,
                SNAPSHOT_VERSION,
                frozen_at,
                established_count,
            ),
        )

        connection.execute(
            """
            INSERT INTO frozen_wallet_scores (
                snapshot_id,
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
                source_updated_at
            )
            SELECT
                ?,
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
            FROM wallet_scores
            WHERE status = 'ESTABLISHED'
            """,
            (snapshot_id,),
        )

        frozen_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM frozen_wallet_scores
            WHERE snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()[0]

        connection.commit()

    print()
    print("=" * 72)
    print(
        "FREEZE — DELVE MEME AGENT "
        "WALLET ALPHA"
    )
    print("=" * 72)
    print(f"Snapshot: {snapshot_id}")
    print(f"Frozen at: {frozen_at}")
    print(f"Wallets:   {frozen_count}")
    print("Status:    IMMUTABLE FORWARD COHORT")
    print("=" * 72)


if __name__ == "__main__":
    freeze_current_wallet_scores()