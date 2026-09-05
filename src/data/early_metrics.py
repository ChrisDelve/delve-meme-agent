import sqlite3
from pathlib import Path


DB_PATH = Path("logs/delve_meme.db")


def get_connection():
    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    return connection


def rebuild_early_metrics():
    with get_connection() as connection:

        # First remove ranks from buys where we never observed
        # the token launch. Those ranks are not valid.
        connection.execute(
            """
            UPDATE buys
            SET
                observed_rank = NULL,
                entry_age_seconds = NULL
            WHERE mint NOT IN (
                SELECT mint
                FROM launches
            )
            """
        )

        launches = connection.execute(
            """
            SELECT
                mint,
                launch_timestamp
            FROM launches
            WHERE launch_timestamp IS NOT NULL
            """
        ).fetchall()

        validated_buys = 0

        for launch in launches:
            mint = launch["mint"]
            launch_timestamp = launch["launch_timestamp"]

            buys = connection.execute(
                """
                SELECT
                    signature,
                    trade_timestamp,
                    slot
                FROM buys
                WHERE
                    mint = ?
                    AND trade_timestamp IS NOT NULL
                    AND trade_timestamp >= ?
                ORDER BY
                    trade_timestamp ASC,
                    slot ASC,
                    signature ASC
                """,
                (
                    mint,
                    launch_timestamp,
                ),
            ).fetchall()

            for rank, buy in enumerate(
                buys,
                start=1,
            ):
                entry_age_seconds = (
                    buy["trade_timestamp"]
                    - launch_timestamp
                )

                connection.execute(
                    """
                    UPDATE buys
                    SET
                        observed_rank = ?,
                        entry_age_seconds = ?
                    WHERE signature = ?
                    """,
                    (
                        rank,
                        entry_age_seconds,
                        buy["signature"],
                    ),
                )

                validated_buys += 1

        connection.commit()

        counts = connection.execute(
            """
            SELECT
                COUNT(*) AS total_buys,

                SUM(
                    CASE
                        WHEN entry_age_seconds IS NOT NULL
                        THEN 1
                        ELSE 0
                    END
                ) AS validated_buys,

                SUM(
                    CASE
                        WHEN entry_age_seconds IS NULL
                        THEN 1
                        ELSE 0
                    END
                ) AS unknown_launch_buys

            FROM buys
            """
        ).fetchone()

    print()
    print("=" * 70)
    print("🧹 DELVE MEME AGENT — EARLY BUY METRICS REBUILT")
    print("=" * 70)
    print(f"Observed launches:     {len(launches)}")
    print(f"Total buys:            {counts['total_buys']}")
    print(f"Validated early buys:  {counts['validated_buys']}")
    print(f"Unknown-launch buys:   {counts['unknown_launch_buys']}")
    print(f"Rows recalculated:     {validated_buys}")
    print("=" * 70)
    print()
    print(
        "✅ Only buys tied to an observed launch now "
        "have an entry rank and entry age."
    )


if __name__ == "__main__":
    rebuild_early_metrics()