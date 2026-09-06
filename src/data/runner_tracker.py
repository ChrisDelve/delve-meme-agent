import sqlite3
import time
from pathlib import Path

from src.data.coverage import (
    interval_is_covered,
    load_valid_coverage,
)

DB_PATH = Path("logs/delve_meme.db")


HORIZONS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
}


def get_connection():
    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_outcomes_table():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS buy_outcomes (
                entry_signature TEXT PRIMARY KEY,
                buyer TEXT NOT NULL,
                mint TEXT NOT NULL,
                quote_mint TEXT,
                entry_timestamp INTEGER NOT NULL,
                entry_rank INTEGER,
                entry_age_seconds INTEGER,

                entry_quote_amount INTEGER,
                entry_token_amount INTEGER,
                entry_price_raw REAL,

                peak_multiple_1m REAL,
                peak_multiple_5m REAL,
                peak_multiple_15m REAL,
                peak_multiple_1h REAL,
                peak_multiple_all REAL,

                trough_multiple_all REAL,

                time_to_2x_seconds INTEGER,
                time_to_5x_seconds INTEGER,
                time_to_10x_seconds INTEGER,

                trades_after_entry INTEGER,
                latest_trade_timestamp INTEGER,
                updated_at INTEGER
            )
            """
        )
        existing_columns = {
            row["name"]
            for row in connection.execute(
                """
                PRAGMA table_info(buy_outcomes)
                """
            ).fetchall()
        }

        for column in (
            "eligible_5m",
            "eligible_15m",
            "eligible_1h",
        ):
            if column not in existing_columns:
                connection.execute(
                    f"""
                    ALTER TABLE buy_outcomes
                    ADD COLUMN {column} INTEGER
                    """
                )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_outcomes_buyer
            ON buy_outcomes(buyer)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_outcomes_mint
            ON buy_outcomes(mint)
            """
        )


def price_raw(quote_amount, token_amount):
    if quote_amount is None or token_amount is None:
        return None

    if quote_amount <= 0 or token_amount <= 0:
        return None

    return quote_amount / token_amount


def calculate_peak_multiple(
    entry_price,
    trade_rows,
    cutoff_timestamp=None,
):
    prices = []

    for trade in trade_rows:
        timestamp = trade["trade_timestamp"]

        if (
            cutoff_timestamp is not None
            and timestamp > cutoff_timestamp
        ):
            continue

        trade_price = price_raw(
            trade["quote_amount"],
            trade["token_amount"],
        )

        if trade_price is not None:
            prices.append(trade_price)

    if not prices:
        return None

    return max(prices) / entry_price


def first_multiple_time(
    entry_timestamp,
    entry_price,
    trade_rows,
    target_multiple,
):
    target_price = entry_price * target_multiple

    for trade in trade_rows:
        trade_price = price_raw(
            trade["quote_amount"],
            trade["token_amount"],
        )

        if (
            trade_price is not None
            and trade_price >= target_price
        ):
            return (
                trade["trade_timestamp"]
                - entry_timestamp
            )

    return None


def rebuild_outcomes():
    init_outcomes_table()
    coverage_rows = load_valid_coverage()
    with get_connection() as connection:

        validated_buys = connection.execute(
            """
            SELECT
                signature,
                mint,
                buyer,
                quote_mint,
                trade_timestamp,
                quote_amount,
                token_amount,
                observed_rank,
                entry_age_seconds
            FROM buys
            WHERE
                entry_age_seconds IS NOT NULL
                AND trade_timestamp IS NOT NULL
                AND quote_amount > 0
                AND token_amount > 0
            ORDER BY trade_timestamp ASC
            """
        ).fetchall()

        updated = 0

        for entry in validated_buys:

            entry_price = price_raw(
                entry["quote_amount"],
                entry["token_amount"],
            )

            if entry_price is None:
                continue
            eligible_5m = interval_is_covered(
                entry["trade_timestamp"],
                entry["trade_timestamp"] + 300,
                coverage_rows,
            )

            eligible_15m = interval_is_covered(
                entry["trade_timestamp"],
                entry["trade_timestamp"] + 900,
                coverage_rows,
            )

            eligible_1h = interval_is_covered(
                entry["trade_timestamp"],
                entry["trade_timestamp"] + 3600,
                coverage_rows,
            )
            later_trades = connection.execute(
                """
                SELECT
                    signature,
                    side,
                    trade_timestamp,
                    quote_amount,
                    token_amount
                FROM trades
                WHERE
                    mint = ?
                    AND quote_mint = ?
                    AND trade_timestamp >= ?
                    AND quote_amount > 0
                    AND token_amount > 0
                ORDER BY
                    trade_timestamp ASC,
                    slot ASC,
                    signature ASC
                """,
                (
                    entry["mint"],
                    entry["quote_mint"],
                    entry["trade_timestamp"],
                ),
            ).fetchall()

            all_prices = [entry_price]

            all_prices.extend(
                value
                for value in (
                    price_raw(
                        trade["quote_amount"],
                        trade["token_amount"],
                    )
                    for trade in later_trades
                )
                if value is not None
            )

            peak_all = max(all_prices) / entry_price
            trough_all = min(all_prices) / entry_price

            peak_1m = calculate_peak_multiple(
                entry_price,
                later_trades,
                entry["trade_timestamp"] + HORIZONS["1m"],
            )

            peak_5m = (
                calculate_peak_multiple(
                    entry_price,
                    later_trades,
                    entry["trade_timestamp"] + HORIZONS["5m"],
                )
                if eligible_5m
                else None
            )

            if eligible_5m and peak_5m is None:
                peak_5m = 1.0
            peak_15m = (
                calculate_peak_multiple(
                    entry_price,
                    later_trades,
                    entry["trade_timestamp"] + HORIZONS["15m"],
                )
                if eligible_15m
                else None
            )

            if eligible_5m and peak_5m is None:
                peak_5m = 1.0
            peak_1h = (
                calculate_peak_multiple(
                    entry_price,
                    later_trades,
                    entry["trade_timestamp"] + HORIZONS["1h"],
                )
                if eligible_1h
                else None
            )

            if eligible_1h and peak_1h is None:
                peak_1h = 1.0
            time_to_2x = first_multiple_time(
                entry["trade_timestamp"],
                entry_price,
                later_trades,
                2.0,
            )

            time_to_5x = first_multiple_time(
                entry["trade_timestamp"],
                entry_price,
                later_trades,
                5.0,
            )

            time_to_10x = first_multiple_time(
                entry["trade_timestamp"],
                entry_price,
                later_trades,
                10.0,
            )

            latest_timestamp = (
                max(
                    trade["trade_timestamp"]
                    for trade in later_trades
                )
                if later_trades
                else entry["trade_timestamp"]
            )

            connection.execute(
                """
                INSERT OR REPLACE INTO buy_outcomes (
                    entry_signature,
                    buyer,
                    mint,
                    quote_mint,
                    entry_timestamp,
                    entry_rank,
                    entry_age_seconds,
                    entry_quote_amount,
                    entry_token_amount,
                    entry_price_raw,
                    eligible_5m,
                    eligible_15m,
                    eligible_1h,
                    peak_multiple_1m,
                    peak_multiple_5m,
                    peak_multiple_15m,
                    peak_multiple_1h,
                    peak_multiple_all,
                    trough_multiple_all,
                    time_to_2x_seconds,
                    time_to_5x_seconds,
                    time_to_10x_seconds,
                    trades_after_entry,
                    latest_trade_timestamp,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    entry["signature"],
                    entry["buyer"],
                    entry["mint"],
                    entry["quote_mint"],
                    entry["trade_timestamp"],
                    entry["observed_rank"],
                    entry["entry_age_seconds"],
                    entry["quote_amount"],
                    entry["token_amount"],
                    entry_price,
                    int(eligible_5m),
                    int(eligible_15m),
                    int(eligible_1h),
                    peak_1m,
                    peak_5m,
                    peak_15m,
                    peak_1h,
                    peak_all,
                    trough_all,
                    time_to_2x,
                    time_to_5x,
                    time_to_10x,
                    len(later_trades),
                    latest_timestamp,
                    int(time.time()),
                ),
            )

            updated += 1

        connection.commit()

        summary = connection.execute(
            """
            SELECT
                COUNT(*) AS outcomes,

                SUM(
                    CASE
                        WHEN peak_multiple_all >= 2
                        THEN 1
                        ELSE 0
                    END
                ) AS hit_2x,

                SUM(
                    CASE
                        WHEN peak_multiple_all >= 5
                        THEN 1
                        ELSE 0
                    END
                ) AS hit_5x,

                SUM(
                    CASE
                        WHEN peak_multiple_all >= 10
                        THEN 1
                        ELSE 0
                    END
                ) AS hit_10x,

                MAX(peak_multiple_all) AS best_runner

            FROM buy_outcomes
            """
        ).fetchone()

    print()
    print("=" * 72)
    print("🏁 DELVE MEME AGENT — RUNNER OUTCOMES")
    print("=" * 72)
    print(f"Validated entries analyzed: {updated}")
    print(f"Outcome rows:               {summary['outcomes']}")
    print(f"Reached 2x+:                {summary['hit_2x']}")
    print(f"Reached 5x+:                {summary['hit_5x']}")
    print(f"Reached 10x+:               {summary['hit_10x']}")

    if summary["best_runner"] is not None:
        print(
            f"Best observed runner:       "
            f"{summary['best_runner']:.2f}x"
        )

    print("=" * 72)


if __name__ == "__main__":
    rebuild_outcomes()