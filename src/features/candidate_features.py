import sqlite3
import time
from pathlib import Path


DB_PATH = Path("logs/delve_meme.db")

SOL_QUOTE_MINT = "11111111111111111111111111111111"

FEATURE_VERSION = "candidate-features-v1"


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_feature_table(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_features (
            entry_signature TEXT PRIMARY KEY,

            feature_version TEXT NOT NULL,

            mint TEXT NOT NULL,
            wallet TEXT NOT NULL,
            quote_mint TEXT NOT NULL,

            entry_timestamp INTEGER NOT NULL,
            entry_rank INTEGER,
            entry_age_seconds INTEGER,
            entry_quote_amount INTEGER,
            entry_token_amount INTEGER,
            entry_price_raw REAL,
            mayhem_mode INTEGER,

            launch_known INTEGER NOT NULL,
            seconds_since_launch INTEGER,

            prior_trade_count_60s INTEGER NOT NULL,

            buys_5s INTEGER NOT NULL,
            sells_5s INTEGER NOT NULL,
            unique_buyers_5s INTEGER NOT NULL,
            unique_sellers_5s INTEGER NOT NULL,
            buy_quote_5s INTEGER NOT NULL,
            sell_quote_5s INTEGER NOT NULL,
            net_quote_5s INTEGER NOT NULL,

            buys_15s INTEGER NOT NULL,
            sells_15s INTEGER NOT NULL,
            unique_buyers_15s INTEGER NOT NULL,
            unique_sellers_15s INTEGER NOT NULL,
            buy_quote_15s INTEGER NOT NULL,
            sell_quote_15s INTEGER NOT NULL,
            net_quote_15s INTEGER NOT NULL,

            buys_30s INTEGER NOT NULL,
            sells_30s INTEGER NOT NULL,
            unique_buyers_30s INTEGER NOT NULL,
            unique_sellers_30s INTEGER NOT NULL,
            buy_quote_30s INTEGER NOT NULL,
            sell_quote_30s INTEGER NOT NULL,
            net_quote_30s INTEGER NOT NULL,

            buys_60s INTEGER NOT NULL,
            sells_60s INTEGER NOT NULL,
            unique_buyers_60s INTEGER NOT NULL,
            unique_sellers_60s INTEGER NOT NULL,
            buy_quote_60s INTEGER NOT NULL,
            sell_quote_60s INTEGER NOT NULL,
            net_quote_60s INTEGER NOT NULL,

            pre_price_raw REAL,

            virtual_sol_reserves_pre INTEGER,
            virtual_token_reserves_pre INTEGER,
            real_sol_reserves_pre INTEGER,
            real_token_reserves_pre INTEGER,

            prior_trade_timestamp INTEGER,

            built_at INTEGER NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_candidate_features_timestamp
        ON candidate_features(entry_timestamp)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_candidate_features_mint
        ON candidate_features(mint)
        """
    )

    connection.commit()


def init_supporting_indexes(connection):
    # Critical for historical rolling-window lookups.
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_trades_mint_timestamp
        ON trades(mint, trade_timestamp)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_buys_quote_timestamp
        ON buys(quote_mint, trade_timestamp)
        """
    )

    connection.commit()


def rebuild_candidate_features():
    started = time.time()

    with get_connection() as connection:
        init_feature_table(connection)
        init_supporting_indexes(connection)

        print()
        print("=" * 72)
        print("DELVE MEME AGENT — CAUSAL CANDIDATE FEATURE BUILDER")
        print("=" * 72)
        print(f"Version: {FEATURE_VERSION}")
        print("Quote universe: native SOL only")
        print("Causality: prior trades must have trade_timestamp < entry timestamp")
        print("=" * 72)

        connection.execute(
            """
            DELETE FROM candidate_features
            WHERE feature_version = ?
            """,
            (FEATURE_VERSION,),
        )

        connection.execute(
            """
            INSERT OR REPLACE INTO candidate_features (
                entry_signature,
                feature_version,

                mint,
                wallet,
                quote_mint,

                entry_timestamp,
                entry_rank,
                entry_age_seconds,
                entry_quote_amount,
                entry_token_amount,
                entry_price_raw,
                mayhem_mode,

                launch_known,
                seconds_since_launch,

                prior_trade_count_60s,

                buys_5s,
                sells_5s,
                unique_buyers_5s,
                unique_sellers_5s,
                buy_quote_5s,
                sell_quote_5s,
                net_quote_5s,

                buys_15s,
                sells_15s,
                unique_buyers_15s,
                unique_sellers_15s,
                buy_quote_15s,
                sell_quote_15s,
                net_quote_15s,

                buys_30s,
                sells_30s,
                unique_buyers_30s,
                unique_sellers_30s,
                buy_quote_30s,
                sell_quote_30s,
                net_quote_30s,

                buys_60s,
                sells_60s,
                unique_buyers_60s,
                unique_sellers_60s,
                buy_quote_60s,
                sell_quote_60s,
                net_quote_60s,

                pre_price_raw,

                virtual_sol_reserves_pre,
                virtual_token_reserves_pre,
                real_sol_reserves_pre,
                real_token_reserves_pre,

                prior_trade_timestamp,

                built_at
            )

            SELECT
                b.signature,
                ?,

                b.mint,
                b.buyer,
                b.quote_mint,

                b.trade_timestamp,
                b.observed_rank,
                b.entry_age_seconds,
                b.quote_amount,
                b.token_amount,

                CASE
                    WHEN b.quote_amount > 0
                     AND b.token_amount > 0
                    THEN 1.0 * b.quote_amount / b.token_amount
                END,

                b.mayhem_mode,

                CASE
                    WHEN l.mint IS NOT NULL
                     AND l.launch_timestamp IS NOT NULL
                     AND l.launch_timestamp <= b.trade_timestamp
                    THEN 1
                    ELSE 0
                END,

                CASE
                    WHEN l.mint IS NOT NULL
                     AND l.launch_timestamp IS NOT NULL
                     AND l.launch_timestamp <= b.trade_timestamp
                    THEN b.trade_timestamp - l.launch_timestamp
                END,

                COUNT(t.signature),

                SUM(
                    CASE
                        WHEN t.trade_timestamp >= b.trade_timestamp - 5
                         AND t.side = 'BUY'
                        THEN 1 ELSE 0
                    END
                ),

                SUM(
                    CASE
                        WHEN t.trade_timestamp >= b.trade_timestamp - 5
                         AND t.side = 'SELL'
                        THEN 1 ELSE 0
                    END
                ),

                COUNT(
                    DISTINCT CASE
                        WHEN t.trade_timestamp >= b.trade_timestamp - 5
                         AND t.side = 'BUY'
                        THEN t.wallet
                    END
                ),

                COUNT(
                    DISTINCT CASE
                        WHEN t.trade_timestamp >= b.trade_timestamp - 5
                         AND t.side = 'SELL'
                        THEN t.wallet
                    END
                ),

                COALESCE(
                    SUM(
                        CASE
                            WHEN t.trade_timestamp >= b.trade_timestamp - 5
                             AND t.side = 'BUY'
                            THEN t.quote_amount
                            ELSE 0
                        END
                    ),
                    0
                ),

                COALESCE(
                    SUM(
                        CASE
                            WHEN t.trade_timestamp >= b.trade_timestamp - 5
                             AND t.side = 'SELL'
                            THEN t.quote_amount
                            ELSE 0
                        END
                    ),
                    0
                ),

                COALESCE(
                    SUM(
                        CASE
                            WHEN t.trade_timestamp >= b.trade_timestamp - 5
                             AND t.side = 'BUY'
                            THEN t.quote_amount
                            WHEN t.trade_timestamp >= b.trade_timestamp - 5
                             AND t.side = 'SELL'
                            THEN -t.quote_amount
                            ELSE 0
                        END
                    ),
                    0
                ),

                SUM(
                    CASE
                        WHEN t.trade_timestamp >= b.trade_timestamp - 15
                         AND t.side = 'BUY'
                        THEN 1 ELSE 0
                    END
                ),

                SUM(
                    CASE
                        WHEN t.trade_timestamp >= b.trade_timestamp - 15
                         AND t.side = 'SELL'
                        THEN 1 ELSE 0
                    END
                ),

                COUNT(
                    DISTINCT CASE
                        WHEN t.trade_timestamp >= b.trade_timestamp - 15
                         AND t.side = 'BUY'
                        THEN t.wallet
                    END
                ),

                COUNT(
                    DISTINCT CASE
                        WHEN t.trade_timestamp >= b.trade_timestamp - 15
                         AND t.side = 'SELL'
                        THEN t.wallet
                    END
                ),

                COALESCE(
                    SUM(
                        CASE
                            WHEN t.trade_timestamp >= b.trade_timestamp - 15
                             AND t.side = 'BUY'
                            THEN t.quote_amount
                            ELSE 0
                        END
                    ),
                    0
                ),

                COALESCE(
                    SUM(
                        CASE
                            WHEN t.trade_timestamp >= b.trade_timestamp - 15
                             AND t.side = 'SELL'
                            THEN t.quote_amount
                            ELSE 0
                        END
                    ),
                    0
                ),

                COALESCE(
                    SUM(
                        CASE
                            WHEN t.trade_timestamp >= b.trade_timestamp - 15
                             AND t.side = 'BUY'
                            THEN t.quote_amount
                            WHEN t.trade_timestamp >= b.trade_timestamp - 15
                             AND t.side = 'SELL'
                            THEN -t.quote_amount
                            ELSE 0
                        END
                    ),
                    0
                ),

                SUM(
                    CASE
                        WHEN t.trade_timestamp >= b.trade_timestamp - 30
                         AND t.side = 'BUY'
                        THEN 1 ELSE 0
                    END
                ),

                SUM(
                    CASE
                        WHEN t.trade_timestamp >= b.trade_timestamp - 30
                         AND t.side = 'SELL'
                        THEN 1 ELSE 0
                    END
                ),

                COUNT(
                    DISTINCT CASE
                        WHEN t.trade_timestamp >= b.trade_timestamp - 30
                         AND t.side = 'BUY'
                        THEN t.wallet
                    END
                ),

                COUNT(
                    DISTINCT CASE
                        WHEN t.trade_timestamp >= b.trade_timestamp - 30
                         AND t.side = 'SELL'
                        THEN t.wallet
                    END
                ),

                COALESCE(
                    SUM(
                        CASE
                            WHEN t.trade_timestamp >= b.trade_timestamp - 30
                             AND t.side = 'BUY'
                            THEN t.quote_amount
                            ELSE 0
                        END
                    ),
                    0
                ),

                COALESCE(
                    SUM(
                        CASE
                            WHEN t.trade_timestamp >= b.trade_timestamp - 30
                             AND t.side = 'SELL'
                            THEN t.quote_amount
                            ELSE 0
                        END
                    ),
                    0
                ),

                COALESCE(
                    SUM(
                        CASE
                            WHEN t.trade_timestamp >= b.trade_timestamp - 30
                             AND t.side = 'BUY'
                            THEN t.quote_amount
                            WHEN t.trade_timestamp >= b.trade_timestamp - 30
                             AND t.side = 'SELL'
                            THEN -t.quote_amount
                            ELSE 0
                        END
                    ),
                    0
                ),

                SUM(CASE WHEN t.side = 'BUY' THEN 1 ELSE 0 END),
                SUM(CASE WHEN t.side = 'SELL' THEN 1 ELSE 0 END),

                COUNT(
                    DISTINCT CASE
                        WHEN t.side = 'BUY'
                        THEN t.wallet
                    END
                ),

                COUNT(
                    DISTINCT CASE
                        WHEN t.side = 'SELL'
                        THEN t.wallet
                    END
                ),

                COALESCE(
                    SUM(
                        CASE
                            WHEN t.side = 'BUY'
                            THEN t.quote_amount
                            ELSE 0
                        END
                    ),
                    0
                ),

                COALESCE(
                    SUM(
                        CASE
                            WHEN t.side = 'SELL'
                            THEN t.quote_amount
                            ELSE 0
                        END
                    ),
                    0
                ),

                COALESCE(
                    SUM(
                        CASE
                            WHEN t.side = 'BUY'
                            THEN t.quote_amount
                            WHEN t.side = 'SELL'
                            THEN -t.quote_amount
                            ELSE 0
                        END
                    ),
                    0
                ),

                (
                    SELECT
                        CASE
                            WHEN p.quote_amount > 0
                             AND p.token_amount > 0
                            THEN 1.0 * p.quote_amount / p.token_amount
                        END
                    FROM trades p
                    WHERE p.mint = b.mint
                      AND p.quote_mint = ?
                      AND p.trade_timestamp < b.trade_timestamp
                      AND p.quote_amount > 0
                      AND p.token_amount > 0
                    ORDER BY
                        p.trade_timestamp DESC,
                        p.slot DESC,
                        p.signature DESC
                    LIMIT 1
                ),

                (
                    SELECT p.virtual_sol_reserves
                    FROM trades p
                    WHERE p.mint = b.mint
                      AND p.quote_mint = ?
                      AND p.trade_timestamp < b.trade_timestamp
                    ORDER BY
                        p.trade_timestamp DESC,
                        p.slot DESC,
                        p.signature DESC
                    LIMIT 1
                ),

                (
                    SELECT p.virtual_token_reserves
                    FROM trades p
                    WHERE p.mint = b.mint
                      AND p.quote_mint = ?
                      AND p.trade_timestamp < b.trade_timestamp
                    ORDER BY
                        p.trade_timestamp DESC,
                        p.slot DESC,
                        p.signature DESC
                    LIMIT 1
                ),

                (
                    SELECT p.real_sol_reserves
                    FROM trades p
                    WHERE p.mint = b.mint
                      AND p.quote_mint = ?
                      AND p.trade_timestamp < b.trade_timestamp
                    ORDER BY
                        p.trade_timestamp DESC,
                        p.slot DESC,
                        p.signature DESC
                    LIMIT 1
                ),

                (
                    SELECT p.real_token_reserves
                    FROM trades p
                    WHERE p.mint = b.mint
                      AND p.quote_mint = ?
                      AND p.trade_timestamp < b.trade_timestamp
                    ORDER BY
                        p.trade_timestamp DESC,
                        p.slot DESC,
                        p.signature DESC
                    LIMIT 1
                ),

                MAX(t.trade_timestamp),

                CAST(strftime('%s','now') AS INTEGER)

            FROM buys b

            LEFT JOIN trades t
              ON t.mint = b.mint
             AND t.quote_mint = ?
             AND t.trade_timestamp < b.trade_timestamp
             AND t.trade_timestamp >= b.trade_timestamp - 60

            LEFT JOIN launches l
              ON l.mint = b.mint

            WHERE
                b.quote_mint = ?
                AND b.trade_timestamp IS NOT NULL
                AND b.quote_amount > 0
                AND b.token_amount > 0

            GROUP BY
                b.signature
            """,
            (
                FEATURE_VERSION,
                SOL_QUOTE_MINT,
                SOL_QUOTE_MINT,
                SOL_QUOTE_MINT,
                SOL_QUOTE_MINT,
                SOL_QUOTE_MINT,
                SOL_QUOTE_MINT,
                SOL_QUOTE_MINT,
            ),
        )

        connection.commit()

        summary = connection.execute(
            """
            SELECT
                COUNT(*) AS rows,
                COUNT(DISTINCT mint) AS mints,
                COUNT(DISTINCT wallet) AS wallets,

                SUM(
                    CASE
                        WHEN prior_trade_timestamp IS NOT NULL
                        THEN 1 ELSE 0
                    END
                ) AS entries_with_prior_trade,

                SUM(launch_known) AS launch_known,

                SUM(
                    CASE
                        WHEN pre_price_raw IS NOT NULL
                        THEN 1 ELSE 0
                    END
                ) AS pre_price_known

            FROM candidate_features
            WHERE feature_version = ?
            """,
            (FEATURE_VERSION,),
        ).fetchone()

        elapsed = time.time() - started

        print()
        print("FEATURE BUILD COMPLETE")
        print("=" * 72)
        print(f"Rows:               {summary['rows']}")
        print(f"Mints:              {summary['mints']}")
        print(f"Wallets:            {summary['wallets']}")
        print(
            "Prior trade known:  "
            f"{summary['entries_with_prior_trade']}"
        )
        print(
            "Pre-price known:    "
            f"{summary['pre_price_known']}"
        )
        print(
            "Launch known:       "
            f"{summary['launch_known']}"
        )
        print(f"Runtime:            {elapsed:.1f}s")
        print("=" * 72)


if __name__ == "__main__":
    rebuild_candidate_features()