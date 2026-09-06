import time

from src.data.market_db import get_connection


SIGNAL_VERSION = "wallet-shadow-v1"


def init_shadow_signals_table():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_signals (
                snapshot_id TEXT NOT NULL,
                signature TEXT NOT NULL,

                signal_version TEXT NOT NULL,
                created_at INTEGER NOT NULL,

                trade_timestamp INTEGER NOT NULL,
                slot INTEGER,

                wallet TEXT NOT NULL,
                mint TEXT NOT NULL,
                quote_mint TEXT,

                sol_amount_lamports INTEGER,
                token_amount INTEGER,

                observed_rank INTEGER,
                entry_age_seconds INTEGER,
                mayhem_mode INTEGER,

                frozen_alpha_score REAL,
                frozen_confidence REAL,
                frozen_status TEXT,

                frozen_eligible_5m INTEGER,
                frozen_eligible_15m INTEGER,
                frozen_eligible_1h INTEGER,

                PRIMARY KEY (
                    snapshot_id,
                    signature
                )
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_shadow_signals_wallet
            ON shadow_signals(
                snapshot_id,
                wallet
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_shadow_signals_mint
            ON shadow_signals(
                mint
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_shadow_signals_timestamp
            ON shadow_signals(
                trade_timestamp
            )
            """
        )

        connection.commit()


def record_shadow_signal(
    *,
    signature,
    slot,
    wallet,
    mint,
    quote_mint,
    trade_timestamp,
    sol_amount_lamports,
    token_amount,
    observed_rank,
    entry_age_seconds,
    mayhem_mode,
):
    """
    Record a forward shadow observation only when
    the buyer belongs to the currently active frozen cohort.

    Returns the recorded signal dict, or None when the wallet
    is not part of the frozen cohort.
    """

    init_shadow_signals_table()

    with get_connection() as connection:
        frozen = connection.execute(
            """
            SELECT
                runs.snapshot_id,
                scores.alpha_score,
                scores.confidence,
                scores.status,
                scores.eligible_5m,
                scores.eligible_15m,
                scores.eligible_1h
            FROM wallet_alpha_snapshot_runs AS runs
            JOIN frozen_wallet_scores AS scores
              ON scores.snapshot_id = runs.snapshot_id
            WHERE
                runs.active = 1
                AND scores.wallet = ?
            LIMIT 1
            """,
            (wallet,),
        ).fetchone()

        if frozen is None:
            return None

        created_at = int(time.time())

        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO shadow_signals (
                snapshot_id,
                signature,

                signal_version,
                created_at,

                trade_timestamp,
                slot,

                wallet,
                mint,
                quote_mint,

                sol_amount_lamports,
                token_amount,

                observed_rank,
                entry_age_seconds,
                mayhem_mode,

                frozen_alpha_score,
                frozen_confidence,
                frozen_status,

                frozen_eligible_5m,
                frozen_eligible_15m,
                frozen_eligible_1h
            )
            VALUES (
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?
            )
            """,
            (
                frozen["snapshot_id"],
                signature,

                SIGNAL_VERSION,
                created_at,

                trade_timestamp,
                slot,

                wallet,
                mint,
                quote_mint,

                sol_amount_lamports,
                token_amount,

                observed_rank,
                entry_age_seconds,
                int(bool(mayhem_mode)),

                frozen["alpha_score"],
                frozen["confidence"],
                frozen["status"],

                frozen["eligible_5m"],
                frozen["eligible_15m"],
                frozen["eligible_1h"],
            ),
        )

        connection.commit()

        # Duplicate transaction already captured.
        if cursor.rowcount == 0:
            return None

        return {
            "snapshot_id": frozen["snapshot_id"],
            "wallet": wallet,
            "mint": mint,
            "alpha_score": frozen["alpha_score"],
            "signature": signature,
        }