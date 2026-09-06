import sqlite3
import time
from pathlib import Path


DB_PATH = Path("logs/delve_meme.db")

SOURCE_FEATURE_VERSION = "candidate-features-v1"
DERIVED_VERSION = "derived-features-v1"

LAMPORTS_PER_SOL = 1_000_000_000


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_derived_table(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS derived_features (
            entry_signature TEXT PRIMARY KEY,

            derived_version TEXT NOT NULL,
            source_feature_version TEXT NOT NULL,

            mint TEXT NOT NULL,
            wallet TEXT NOT NULL,
            entry_timestamp INTEGER NOT NULL,

            entry_rank INTEGER,
            entry_age_seconds INTEGER,
            entry_size_sol REAL,
            entry_price_raw REAL,
            pre_price_raw REAL,
            entry_price_return_from_pre REAL,
            prior_trade_gap_seconds INTEGER,

            launch_known INTEGER NOT NULL,
            seconds_since_launch INTEGER,
            mayhem_mode INTEGER,

            has_prior_trade INTEGER NOT NULL,
            has_pre_price INTEGER NOT NULL,
            has_reserve_state INTEGER NOT NULL,

            trades_5s INTEGER NOT NULL,
            trades_15s INTEGER NOT NULL,
            trades_30s INTEGER NOT NULL,
            trades_60s INTEGER NOT NULL,

            buy_rate_5s REAL NOT NULL,
            buy_rate_15s REAL NOT NULL,
            buy_rate_30s REAL NOT NULL,
            buy_rate_60s REAL NOT NULL,

            sell_rate_5s REAL NOT NULL,
            sell_rate_15s REAL NOT NULL,
            sell_rate_30s REAL NOT NULL,
            sell_rate_60s REAL NOT NULL,

            trade_rate_5s REAL NOT NULL,
            trade_rate_15s REAL NOT NULL,
            trade_rate_30s REAL NOT NULL,
            trade_rate_60s REAL NOT NULL,

            buy_sol_5s REAL NOT NULL,
            buy_sol_15s REAL NOT NULL,
            buy_sol_30s REAL NOT NULL,
            buy_sol_60s REAL NOT NULL,

            sell_sol_5s REAL NOT NULL,
            sell_sol_15s REAL NOT NULL,
            sell_sol_30s REAL NOT NULL,
            sell_sol_60s REAL NOT NULL,

            net_sol_5s REAL NOT NULL,
            net_sol_15s REAL NOT NULL,
            net_sol_30s REAL NOT NULL,
            net_sol_60s REAL NOT NULL,

            gross_sol_5s REAL NOT NULL,
            gross_sol_15s REAL NOT NULL,
            gross_sol_30s REAL NOT NULL,
            gross_sol_60s REAL NOT NULL,

            net_sol_rate_5s REAL NOT NULL,
            net_sol_rate_15s REAL NOT NULL,
            net_sol_rate_30s REAL NOT NULL,
            net_sol_rate_60s REAL NOT NULL,

            trade_imbalance_5s REAL,
            trade_imbalance_15s REAL,
            trade_imbalance_30s REAL,
            trade_imbalance_60s REAL,

            sol_imbalance_5s REAL,
            sol_imbalance_15s REAL,
            sol_imbalance_30s REAL,
            sol_imbalance_60s REAL,

            unique_buyers_5s INTEGER NOT NULL,
            unique_buyers_15s INTEGER NOT NULL,
            unique_buyers_30s INTEGER NOT NULL,
            unique_buyers_60s INTEGER NOT NULL,

            unique_sellers_5s INTEGER NOT NULL,
            unique_sellers_15s INTEGER NOT NULL,
            unique_sellers_30s INTEGER NOT NULL,
            unique_sellers_60s INTEGER NOT NULL,

            buys_per_unique_buyer_5s REAL,
            buys_per_unique_buyer_15s REAL,
            buys_per_unique_buyer_30s REAL,
            buys_per_unique_buyer_60s REAL,

            sells_per_unique_seller_5s REAL,
            sells_per_unique_seller_15s REAL,
            sells_per_unique_seller_30s REAL,
            sells_per_unique_seller_60s REAL,

            prev_10s_buy_rate REAL NOT NULL,
            prev_10s_sell_rate REAL NOT NULL,
            prev_10s_trade_rate REAL NOT NULL,
            prev_10s_net_sol_rate REAL NOT NULL,

            buy_rate_accel_5v10 REAL NOT NULL,
            sell_rate_accel_5v10 REAL NOT NULL,
            trade_rate_accel_5v10 REAL NOT NULL,
            net_sol_accel_5v10 REAL NOT NULL,

            buy_rate_ratio_5v10 REAL,
            sell_rate_ratio_5v10 REAL,
            trade_rate_ratio_5v10 REAL,

            prev_45s_buy_rate REAL NOT NULL,
            prev_45s_sell_rate REAL NOT NULL,
            prev_45s_trade_rate REAL NOT NULL,
            prev_45s_net_sol_rate REAL NOT NULL,

            buy_rate_accel_15v45 REAL NOT NULL,
            sell_rate_accel_15v45 REAL NOT NULL,
            trade_rate_accel_15v45 REAL NOT NULL,
            net_sol_accel_15v45 REAL NOT NULL,

            buy_rate_ratio_15v45 REAL,
            sell_rate_ratio_15v45 REAL,
            trade_rate_ratio_15v45 REAL,

            virtual_sol_reserves_pre_sol REAL,
            real_sol_reserves_pre_sol REAL,
            real_to_virtual_sol_ratio_pre REAL,
            real_to_virtual_token_ratio_pre REAL,

            built_at INTEGER NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_derived_features_timestamp
        ON derived_features(entry_timestamp)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_derived_features_mint
        ON derived_features(mint)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_derived_features_wallet
        ON derived_features(wallet)
        """
    )

    connection.commit()


def rebuild_derived_features():
    started = time.time()

    with get_connection() as connection:
        init_derived_table(connection)

        print()
        print("=" * 72)
        print("DELVE MEME AGENT — DERIVED MARKET FEATURES")
        print("=" * 72)
        print(f"Source:  {SOURCE_FEATURE_VERSION}")
        print(f"Version: {DERIVED_VERSION}")
        print("NO OUTCOMES")
        print("NO WALLET ALPHA")
        print("NO FUTURE TRADES")
        print("=" * 72)

        connection.execute(
            """
            DELETE FROM derived_features
            WHERE derived_version = ?
            """,
            (DERIVED_VERSION,),
        )

        connection.execute(
            """
            INSERT OR REPLACE INTO derived_features

            WITH base AS (
                SELECT
                    c.*,

                    1.0 * c.entry_quote_amount
                        / ? AS entry_size_sol_calc,

                    1.0 * c.buy_quote_5s
                        / ? AS buy_sol_5s_calc,

                    1.0 * c.buy_quote_15s
                        / ? AS buy_sol_15s_calc,

                    1.0 * c.buy_quote_30s
                        / ? AS buy_sol_30s_calc,

                    1.0 * c.buy_quote_60s
                        / ? AS buy_sol_60s_calc,

                    1.0 * c.sell_quote_5s
                        / ? AS sell_sol_5s_calc,

                    1.0 * c.sell_quote_15s
                        / ? AS sell_sol_15s_calc,

                    1.0 * c.sell_quote_30s
                        / ? AS sell_sol_30s_calc,

                    1.0 * c.sell_quote_60s
                        / ? AS sell_sol_60s_calc,

                    1.0 * c.net_quote_5s
                        / ? AS net_sol_5s_calc,

                    1.0 * c.net_quote_15s
                        / ? AS net_sol_15s_calc,

                    1.0 * c.net_quote_30s
                        / ? AS net_sol_30s_calc,

                    1.0 * c.net_quote_60s
                        / ? AS net_sol_60s_calc

                FROM candidate_features c

                WHERE
                    c.feature_version = ?
            ),

            rates AS (
                SELECT
                    b.*,

                    b.buys_5s + b.sells_5s
                        AS trades_5s_calc,

                    b.buys_15s + b.sells_15s
                        AS trades_15s_calc,

                    b.buys_30s + b.sells_30s
                        AS trades_30s_calc,

                    b.buys_60s + b.sells_60s
                        AS trades_60s_calc,

                    1.0 * b.buys_5s / 5.0
                        AS buy_rate_5s_calc,

                    1.0 * b.buys_15s / 15.0
                        AS buy_rate_15s_calc,

                    1.0 * b.buys_30s / 30.0
                        AS buy_rate_30s_calc,

                    1.0 * b.buys_60s / 60.0
                        AS buy_rate_60s_calc,

                    1.0 * b.sells_5s / 5.0
                        AS sell_rate_5s_calc,

                    1.0 * b.sells_15s / 15.0
                        AS sell_rate_15s_calc,

                    1.0 * b.sells_30s / 30.0
                        AS sell_rate_30s_calc,

                    1.0 * b.sells_60s / 60.0
                        AS sell_rate_60s_calc,

                    1.0 * (b.buys_5s + b.sells_5s)
                        / 5.0 AS trade_rate_5s_calc,

                    1.0 * (b.buys_15s + b.sells_15s)
                        / 15.0 AS trade_rate_15s_calc,

                    1.0 * (b.buys_30s + b.sells_30s)
                        / 30.0 AS trade_rate_30s_calc,

                    1.0 * (b.buys_60s + b.sells_60s)
                        / 60.0 AS trade_rate_60s_calc,

                    (
                        b.buy_sol_5s_calc
                        + b.sell_sol_5s_calc
                    ) AS gross_sol_5s_calc,

                    (
                        b.buy_sol_15s_calc
                        + b.sell_sol_15s_calc
                    ) AS gross_sol_15s_calc,

                    (
                        b.buy_sol_30s_calc
                        + b.sell_sol_30s_calc
                    ) AS gross_sol_30s_calc,

                    (
                        b.buy_sol_60s_calc
                        + b.sell_sol_60s_calc
                    ) AS gross_sol_60s_calc,

                    b.net_sol_5s_calc / 5.0
                        AS net_sol_rate_5s_calc,

                    b.net_sol_15s_calc / 15.0
                        AS net_sol_rate_15s_calc,

                    b.net_sol_30s_calc / 30.0
                        AS net_sol_rate_30s_calc,

                    b.net_sol_60s_calc / 60.0
                        AS net_sol_rate_60s_calc

                FROM base b
            ),

            acceleration AS (
                SELECT
                    r.*,

                    1.0 * (r.buys_15s - r.buys_5s)
                        / 10.0 AS prev_10s_buy_rate_calc,

                    1.0 * (r.sells_15s - r.sells_5s)
                        / 10.0 AS prev_10s_sell_rate_calc,

                    1.0 * (
                        (r.buys_15s + r.sells_15s)
                        - (r.buys_5s + r.sells_5s)
                    ) / 10.0 AS prev_10s_trade_rate_calc,

                    (
                        r.net_sol_15s_calc
                        - r.net_sol_5s_calc
                    ) / 10.0 AS prev_10s_net_sol_rate_calc,

                    1.0 * (r.buys_60s - r.buys_15s)
                        / 45.0 AS prev_45s_buy_rate_calc,

                    1.0 * (r.sells_60s - r.sells_15s)
                        / 45.0 AS prev_45s_sell_rate_calc,

                    1.0 * (
                        (r.buys_60s + r.sells_60s)
                        - (r.buys_15s + r.sells_15s)
                    ) / 45.0 AS prev_45s_trade_rate_calc,

                    (
                        r.net_sol_60s_calc
                        - r.net_sol_15s_calc
                    ) / 45.0 AS prev_45s_net_sol_rate_calc

                FROM rates r
            )

            SELECT
                a.entry_signature,

                ?,
                ?,

                a.mint,
                a.wallet,
                a.entry_timestamp,

                a.entry_rank,
                a.entry_age_seconds,
                a.entry_size_sol_calc,
                a.entry_price_raw,
                a.pre_price_raw,

                CASE
                    WHEN a.pre_price_raw IS NOT NULL
                     AND a.pre_price_raw > 0
                     AND a.entry_price_raw IS NOT NULL
                    THEN
                        (a.entry_price_raw / a.pre_price_raw) - 1.0
                END,

                CASE
                    WHEN a.prior_trade_timestamp IS NOT NULL
                    THEN
                        a.entry_timestamp
                        - a.prior_trade_timestamp
                END,

                a.launch_known,
                a.seconds_since_launch,
                a.mayhem_mode,

                CASE
                    WHEN a.prior_trade_timestamp IS NOT NULL
                    THEN 1 ELSE 0
                END,

                CASE
                    WHEN a.pre_price_raw IS NOT NULL
                    THEN 1 ELSE 0
                END,

                CASE
                    WHEN a.virtual_sol_reserves_pre IS NOT NULL
                     AND a.virtual_token_reserves_pre IS NOT NULL
                     AND a.real_sol_reserves_pre IS NOT NULL
                     AND a.real_token_reserves_pre IS NOT NULL
                    THEN 1 ELSE 0
                END,

                a.trades_5s_calc,
                a.trades_15s_calc,
                a.trades_30s_calc,
                a.trades_60s_calc,

                a.buy_rate_5s_calc,
                a.buy_rate_15s_calc,
                a.buy_rate_30s_calc,
                a.buy_rate_60s_calc,

                a.sell_rate_5s_calc,
                a.sell_rate_15s_calc,
                a.sell_rate_30s_calc,
                a.sell_rate_60s_calc,

                a.trade_rate_5s_calc,
                a.trade_rate_15s_calc,
                a.trade_rate_30s_calc,
                a.trade_rate_60s_calc,

                a.buy_sol_5s_calc,
                a.buy_sol_15s_calc,
                a.buy_sol_30s_calc,
                a.buy_sol_60s_calc,

                a.sell_sol_5s_calc,
                a.sell_sol_15s_calc,
                a.sell_sol_30s_calc,
                a.sell_sol_60s_calc,

                a.net_sol_5s_calc,
                a.net_sol_15s_calc,
                a.net_sol_30s_calc,
                a.net_sol_60s_calc,

                a.gross_sol_5s_calc,
                a.gross_sol_15s_calc,
                a.gross_sol_30s_calc,
                a.gross_sol_60s_calc,

                a.net_sol_rate_5s_calc,
                a.net_sol_rate_15s_calc,
                a.net_sol_rate_30s_calc,
                a.net_sol_rate_60s_calc,

                CASE
                    WHEN a.trades_5s_calc > 0
                    THEN
                        1.0 * (a.buys_5s - a.sells_5s)
                        / a.trades_5s_calc
                END,

                CASE
                    WHEN a.trades_15s_calc > 0
                    THEN
                        1.0 * (a.buys_15s - a.sells_15s)
                        / a.trades_15s_calc
                END,

                CASE
                    WHEN a.trades_30s_calc > 0
                    THEN
                        1.0 * (a.buys_30s - a.sells_30s)
                        / a.trades_30s_calc
                END,

                CASE
                    WHEN a.trades_60s_calc > 0
                    THEN
                        1.0 * (a.buys_60s - a.sells_60s)
                        / a.trades_60s_calc
                END,

                CASE
                    WHEN a.gross_sol_5s_calc > 0
                    THEN
                        a.net_sol_5s_calc
                        / a.gross_sol_5s_calc
                END,

                CASE
                    WHEN a.gross_sol_15s_calc > 0
                    THEN
                        a.net_sol_15s_calc
                        / a.gross_sol_15s_calc
                END,

                CASE
                    WHEN a.gross_sol_30s_calc > 0
                    THEN
                        a.net_sol_30s_calc
                        / a.gross_sol_30s_calc
                END,

                CASE
                    WHEN a.gross_sol_60s_calc > 0
                    THEN
                        a.net_sol_60s_calc
                        / a.gross_sol_60s_calc
                END,

                a.unique_buyers_5s,
                a.unique_buyers_15s,
                a.unique_buyers_30s,
                a.unique_buyers_60s,

                a.unique_sellers_5s,
                a.unique_sellers_15s,
                a.unique_sellers_30s,
                a.unique_sellers_60s,

                CASE
                    WHEN a.unique_buyers_5s > 0
                    THEN
                        1.0 * a.buys_5s
                        / a.unique_buyers_5s
                END,

                CASE
                    WHEN a.unique_buyers_15s > 0
                    THEN
                        1.0 * a.buys_15s
                        / a.unique_buyers_15s
                END,

                CASE
                    WHEN a.unique_buyers_30s > 0
                    THEN
                        1.0 * a.buys_30s
                        / a.unique_buyers_30s
                END,

                CASE
                    WHEN a.unique_buyers_60s > 0
                    THEN
                        1.0 * a.buys_60s
                        / a.unique_buyers_60s
                END,

                CASE
                    WHEN a.unique_sellers_5s > 0
                    THEN
                        1.0 * a.sells_5s
                        / a.unique_sellers_5s
                END,

                CASE
                    WHEN a.unique_sellers_15s > 0
                    THEN
                        1.0 * a.sells_15s
                        / a.unique_sellers_15s
                END,

                CASE
                    WHEN a.unique_sellers_30s > 0
                    THEN
                        1.0 * a.sells_30s
                        / a.unique_sellers_30s
                END,

                CASE
                    WHEN a.unique_sellers_60s > 0
                    THEN
                        1.0 * a.sells_60s
                        / a.unique_sellers_60s
                END,

                a.prev_10s_buy_rate_calc,
                a.prev_10s_sell_rate_calc,
                a.prev_10s_trade_rate_calc,
                a.prev_10s_net_sol_rate_calc,

                a.buy_rate_5s_calc
                    - a.prev_10s_buy_rate_calc,

                a.sell_rate_5s_calc
                    - a.prev_10s_sell_rate_calc,

                a.trade_rate_5s_calc
                    - a.prev_10s_trade_rate_calc,

                a.net_sol_rate_5s_calc
                    - a.prev_10s_net_sol_rate_calc,

                CASE
                    WHEN a.prev_10s_buy_rate_calc > 0
                    THEN
                        a.buy_rate_5s_calc
                        / a.prev_10s_buy_rate_calc
                END,

                CASE
                    WHEN a.prev_10s_sell_rate_calc > 0
                    THEN
                        a.sell_rate_5s_calc
                        / a.prev_10s_sell_rate_calc
                END,

                CASE
                    WHEN a.prev_10s_trade_rate_calc > 0
                    THEN
                        a.trade_rate_5s_calc
                        / a.prev_10s_trade_rate_calc
                END,

                a.prev_45s_buy_rate_calc,
                a.prev_45s_sell_rate_calc,
                a.prev_45s_trade_rate_calc,
                a.prev_45s_net_sol_rate_calc,

                a.buy_rate_15s_calc
                    - a.prev_45s_buy_rate_calc,

                a.sell_rate_15s_calc
                    - a.prev_45s_sell_rate_calc,

                a.trade_rate_15s_calc
                    - a.prev_45s_trade_rate_calc,

                a.net_sol_rate_15s_calc
                    - a.prev_45s_net_sol_rate_calc,

                CASE
                    WHEN a.prev_45s_buy_rate_calc > 0
                    THEN
                        a.buy_rate_15s_calc
                        / a.prev_45s_buy_rate_calc
                END,

                CASE
                    WHEN a.prev_45s_sell_rate_calc > 0
                    THEN
                        a.sell_rate_15s_calc
                        / a.prev_45s_sell_rate_calc
                END,

                CASE
                    WHEN a.prev_45s_trade_rate_calc > 0
                    THEN
                        a.trade_rate_15s_calc
                        / a.prev_45s_trade_rate_calc
                END,

                CASE
                    WHEN a.virtual_sol_reserves_pre IS NOT NULL
                    THEN
                        1.0 * a.virtual_sol_reserves_pre / ?
                END,

                CASE
                    WHEN a.real_sol_reserves_pre IS NOT NULL
                    THEN
                        1.0 * a.real_sol_reserves_pre / ?
                END,

                CASE
                    WHEN a.virtual_sol_reserves_pre > 0
                     AND a.real_sol_reserves_pre IS NOT NULL
                    THEN
                        1.0 * a.real_sol_reserves_pre
                        / a.virtual_sol_reserves_pre
                END,

                CASE
                    WHEN a.virtual_token_reserves_pre > 0
                     AND a.real_token_reserves_pre IS NOT NULL
                    THEN
                        1.0 * a.real_token_reserves_pre
                        / a.virtual_token_reserves_pre
                END,

                CAST(strftime('%s','now') AS INTEGER)

            FROM acceleration a
            """,
            (
                LAMPORTS_PER_SOL,
                LAMPORTS_PER_SOL,
                LAMPORTS_PER_SOL,
                LAMPORTS_PER_SOL,
                LAMPORTS_PER_SOL,
                LAMPORTS_PER_SOL,
                LAMPORTS_PER_SOL,
                LAMPORTS_PER_SOL,
                LAMPORTS_PER_SOL,
                LAMPORTS_PER_SOL,
                LAMPORTS_PER_SOL,
                LAMPORTS_PER_SOL,
                LAMPORTS_PER_SOL,
                SOURCE_FEATURE_VERSION,
                DERIVED_VERSION,
                SOURCE_FEATURE_VERSION,
                LAMPORTS_PER_SOL,
                LAMPORTS_PER_SOL,
            ),
        )

        connection.commit()

        summary = connection.execute(
            """
            SELECT
                COUNT(*) AS rows,
                COUNT(DISTINCT entry_signature) AS signatures,
                COUNT(DISTINCT mint) AS mints,

                SUM(has_prior_trade) AS prior_trade,
                SUM(has_pre_price) AS pre_price,
                SUM(has_reserve_state) AS reserve_state,

                SUM(
                    CASE
                        WHEN trade_imbalance_5s IS NOT NULL
                        THEN 1 ELSE 0
                    END
                ) AS imbalance_5s,

                SUM(
                    CASE
                        WHEN buy_rate_ratio_5v10 IS NOT NULL
                        THEN 1 ELSE 0
                    END
                ) AS accel_ratio_5v10

            FROM derived_features
            WHERE derived_version = ?
            """,
            (DERIVED_VERSION,),
        ).fetchone()

        elapsed = time.time() - started

        print()
        print("DERIVED FEATURE BUILD COMPLETE")
        print("=" * 72)
        print(f"Rows:                  {summary['rows']}")
        print(f"Unique signatures:     {summary['signatures']}")
        print(f"Mints:                 {summary['mints']}")
        print(f"Prior trade state:     {summary['prior_trade']}")
        print(f"Pre-price state:       {summary['pre_price']}")
        print(f"Reserve state:         {summary['reserve_state']}")
        print(f"5s imbalance known:    {summary['imbalance_5s']}")
        print(f"5v10 ratio available:  {summary['accel_ratio_5v10']}")
        print(f"Runtime:               {elapsed:.1f}s")
        print("=" * 72)


if __name__ == "__main__":
    rebuild_derived_features()