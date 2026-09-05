import sqlite3
from pathlib import Path


DB_PATH = Path("logs/delve_meme.db")


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    return connection


def init_db():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS launches (
                mint TEXT PRIMARY KEY,
                name TEXT,
                symbol TEXT,
                creator TEXT,
                mayhem_mode INTEGER,
                launch_slot INTEGER,
                launch_timestamp INTEGER,
                signature TEXT,
                metadata_uri TEXT,
                first_seen_at INTEGER
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS buys (
                signature TEXT PRIMARY KEY,
                mint TEXT NOT NULL,
                buyer TEXT NOT NULL,
                quote_mint TEXT,
                slot INTEGER,
                trade_timestamp INTEGER,

                sol_amount_lamports INTEGER,
                quote_amount INTEGER,
                token_amount INTEGER,

                protocol_fee_lamports INTEGER,
                creator_fee_lamports INTEGER,

                ix_name TEXT,
                mayhem_mode INTEGER,

                observed_rank INTEGER,
                entry_age_seconds INTEGER,

                observed_at INTEGER
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_buys_mint
            ON buys(mint)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_buys_buyer
            ON buys(buyer)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_buys_timestamp
            ON buys(trade_timestamp)
            """
        )


def save_launch(
    *,
    mint,
    name,
    symbol,
    creator,
    mayhem_mode,
    launch_slot,
    launch_timestamp,
    signature,
    metadata_uri,
    first_seen_at,
):
    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO launches (
                mint,
                name,
                symbol,
                creator,
                mayhem_mode,
                launch_slot,
                launch_timestamp,
                signature,
                metadata_uri,
                first_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mint,
                name,
                symbol,
                creator,
                int(bool(mayhem_mode)),
                launch_slot,
                launch_timestamp,
                signature,
                metadata_uri,
                first_seen_at,
            ),
        )

        # Some buys can arrive before the launch transaction
        # finishes decoding. Fill their launch age afterward.
        if launch_timestamp is not None:
            connection.execute(
                """
                UPDATE buys
                SET entry_age_seconds =
                    trade_timestamp - ?
                WHERE mint = ?
                  AND entry_age_seconds IS NULL
                  AND trade_timestamp IS NOT NULL
                """,
                (
                    launch_timestamp,
                    mint,
                ),
            )


def save_buy(
    *,
    signature,
    mint,
    buyer,
    quote_mint,
    slot,
    trade_timestamp,
    sol_amount_lamports,
    quote_amount,
    token_amount,
    protocol_fee_lamports,
    creator_fee_lamports,
    ix_name,
    mayhem_mode,
    observed_at,
):
    with get_connection() as connection:

        existing = connection.execute(
            """
            SELECT 1
            FROM buys
            WHERE signature = ?
            """,
            (signature,),
        ).fetchone()

        if existing:
            return None

        rank_row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM buys
            WHERE mint = ?
            """,
            (mint,),
        ).fetchone()

        observed_rank = rank_row["count"] + 1

        launch = connection.execute(
            """
            SELECT launch_timestamp
            FROM launches
            WHERE mint = ?
            """,
            (mint,),
        ).fetchone()

        entry_age_seconds = None

        if (
            launch
            and launch["launch_timestamp"] is not None
            and trade_timestamp is not None
        ):
            entry_age_seconds = (
                trade_timestamp
                - launch["launch_timestamp"]
            )

        connection.execute(
            """
            INSERT INTO buys (
                signature,
                mint,
                buyer,
                quote_mint,
                slot,
                trade_timestamp,
                sol_amount_lamports,
                quote_amount,
                token_amount,
                protocol_fee_lamports,
                creator_fee_lamports,
                ix_name,
                mayhem_mode,
                observed_rank,
                entry_age_seconds,
                observed_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?
            )
            """,
            (
                signature,
                mint,
                buyer,
                quote_mint,
                slot,
                trade_timestamp,
                sol_amount_lamports,
                quote_amount,
                token_amount,
                protocol_fee_lamports,
                creator_fee_lamports,
                ix_name,
                int(bool(mayhem_mode)),
                observed_rank,
                entry_age_seconds,
                observed_at,
            ),
        )

        return {
            "observed_rank": observed_rank,
            "entry_age_seconds": entry_age_seconds,
        }


def get_counts():
    with get_connection() as connection:
        launches = connection.execute(
            "SELECT COUNT(*) AS count FROM launches"
        ).fetchone()["count"]

        buys = connection.execute(
            "SELECT COUNT(*) AS count FROM buys"
        ).fetchone()["count"]

        wallets = connection.execute(
            """
            SELECT COUNT(DISTINCT buyer) AS count
            FROM buys
            """
        ).fetchone()["count"]

        return {
            "launches": launches,
            "buys": buys,
            "wallets": wallets,
        }


if __name__ == "__main__":
    init_db()

    counts = get_counts()

    print("=" * 60)
    print("🧠 DELVE MEME AGENT — MARKET DATABASE")
    print("=" * 60)
    print(f"Launches: {counts['launches']}")
    print(f"Buys:     {counts['buys']}")
    print(f"Wallets:  {counts['wallets']}")
    print("=" * 60)
    print("✅ Database ready.")