import sqlite3
import time
from pathlib import Path


DB_PATH = Path("logs/delve_meme.db")


def get_connection():
    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_coverage_db():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            collector_coverage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                started_at INTEGER NOT NULL,
                ended_at INTEGER,

                valid INTEGER NOT NULL DEFAULT 1,

                close_reason TEXT,

                created_at INTEGER NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_coverage_time

            ON collector_coverage(
                started_at,
                ended_at
            )
            """
        )


def invalidate_stale_intervals():
    """
    If the previous process died without closing its
    coverage interval, we cannot prove when coverage
    actually stopped.

    Fail closed: invalidate that interval instead of
    pretending it was continuous.
    """

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE collector_coverage

            SET
                valid = 0,
                close_reason =
                    'unclean_previous_shutdown'

            WHERE ended_at IS NULL
            """
        )


def start_coverage_interval():
    now = int(time.time())

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO collector_coverage (
                started_at,
                ended_at,
                valid,
                close_reason,
                created_at
            )

            VALUES (?, NULL, 1, NULL, ?)
            """,
            (
                now,
                now,
            ),
        )

        interval_id = cursor.lastrowid

    print()
    print(
        f"📡 Coverage interval started "
        f"#{interval_id}"
    )

    return interval_id


def close_coverage_interval(
    interval_id,
    reason,
    ended_at=None,
):
    if interval_id is None:
        return

    if ended_at is None:
        ended_at = int(time.time())

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE collector_coverage

            SET
                ended_at = ?,
                close_reason = ?

            WHERE
                id = ?
                AND ended_at IS NULL
            """,
            (
                ended_at,
                reason,
                interval_id,
            ),
        )

    print(
        f"📡 Coverage interval closed "
        f"#{interval_id}: {reason}"
    )


def load_valid_coverage():
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                started_at,
                ended_at

            FROM collector_coverage

            WHERE
                valid = 1
                AND ended_at IS NOT NULL
                AND ended_at > started_at

            ORDER BY started_at ASC
            """
        ).fetchall()

    return rows


def interval_is_covered(
    start_timestamp,
    end_timestamp,
    coverage_rows=None,
):
    """
    True only when one uninterrupted valid collector
    interval completely contains the requested period.
    """

    if coverage_rows is None:
        coverage_rows = load_valid_coverage()

    for coverage in coverage_rows:

        if (
            coverage["started_at"]
            <= start_timestamp
            and
            coverage["ended_at"]
            >= end_timestamp
        ):
            return True

    return False


init_coverage_db()