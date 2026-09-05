import sqlite3
from pathlib import Path

from src.data.transaction_decoder import fetch_transaction
from src.data.trade_event import extract_trade_event


DB_PATH = Path("logs/delve_meme.db")


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_trades_table():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                signature TEXT PRIMARY KEY,
                mint TEXT NOT NULL,
                wallet TEXT NOT NULL,
                side TEXT NOT NULL,
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
                virtual_sol_reserves INTEGER,
                virtual_token_reserves INTEGER,
                real_sol_reserves INTEGER,
                real_token_reserves INTEGER,
                virtual_quote_reserves INTEGER,
                real_quote_reserves INTEGER,
                observed_at INTEGER
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trades_mint
            ON trades(mint)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trades_wallet
            ON trades(wallet)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trades_timestamp
            ON trades(trade_timestamp)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trades_side
            ON trades(side)
            """
        )


def save_trade_event(
    signature,
    slot,
    trade_event,
):
    side = "BUY" if trade_event["is_buy"] else "SELL"

    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO trades (
                signature,
                mint,
                wallet,
                side,
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
                virtual_sol_reserves,
                virtual_token_reserves,
                real_sol_reserves,
                real_token_reserves,
                virtual_quote_reserves,
                real_quote_reserves,
                observed_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                signature,
                trade_event["mint"],
                trade_event["user"],
                side,
                trade_event["quote_mint"],
                slot,
                trade_event["timestamp"],
                trade_event["sol_amount"],
                trade_event["quote_amount"],
                trade_event["token_amount"],
                trade_event["fee"],
                trade_event["creator_fee"],
                trade_event["ix_name"],
                int(bool(trade_event["mayhem_mode"])),
                trade_event["virtual_sol_reserves"],
                trade_event["virtual_token_reserves"],
                trade_event["real_sol_reserves"],
                trade_event["real_token_reserves"],
                trade_event["virtual_quote_reserves"],
                trade_event["real_quote_reserves"],
                trade_event["timestamp"],
            ),
        )

    print()
    print(f"{'🟢' if side == 'BUY' else '🔴'} TRADE SAVED")
    print("=" * 70)
    print(f"Side:    {side}")
    print(f"Wallet:  {trade_event['user']}")
    print(f"Mint:    {trade_event['mint']}")
    print(
        f"SOL:     "
        f"{trade_event['sol_amount'] / 1_000_000_000:.6f}"
    )
    print(f"Tokens:  {trade_event['token_amount']}")
    print(f"Slot:    {slot}")
    print("=" * 70)


async def process_trade(
    signature,
    slot,
):
    transaction = await fetch_transaction(signature)

    if transaction is None:
        return

    trade_event = extract_trade_event(transaction)

    if not trade_event:
        return

    save_trade_event(
        signature,
        slot,
        trade_event,
    )


init_trades_table()