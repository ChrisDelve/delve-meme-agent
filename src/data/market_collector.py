import asyncio
import json
import os
import ssl
import time

import certifi
import websockets
from dotenv import load_dotenv

from src.data.market_db import (
    get_counts,
    init_db,
    save_buy,
    save_launch,
)
from src.data.trade_event import (
    extract_trade_event,
    extract_trade_event_from_logs,
)

from src.data.trade_recorder import save_trade_event
from src.data.transaction_decoder import (
    decode_create_v2_instruction,
    fetch_transaction,
)


load_dotenv()

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

if not HELIUS_API_KEY:
    raise RuntimeError("HELIUS_API_KEY is missing from .env")

WS_URL = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

SSL_CONTEXT = ssl.create_default_context(
    cafile=certifi.where()
)

# Prevent us from creating an unlimited number of
# simultaneous RPC requests during bursts.
PROCESSING_LIMIT = asyncio.Semaphore(10)


def find_create_v2(transaction):
    message = (
        transaction
        .get("transaction", {})
        .get("message", {})
    )

    for instruction in message.get("instructions", []):
        decoded = decode_create_v2_instruction(
            instruction
        )

        if decoded:
            return decoded

    return None


async def process_launch(
    signature,
    slot,
):
    async with PROCESSING_LIMIT:
        transaction = await fetch_transaction(
            signature
        )

        if transaction is None:
            return

        decoded = find_create_v2(
            transaction
        )

        if decoded is None:
            return

        block_time = transaction.get(
            "blockTime"
        )

        save_launch(
            mint=decoded["mint"],
            name=decoded["name"],
            symbol=decoded["symbol"],
            creator=decoded["creator"],
            mayhem_mode=decoded["mayhem_mode"],
            launch_slot=transaction.get(
                "slot",
                slot,
            ),
            launch_timestamp=block_time,
            signature=signature,
            metadata_uri=decoded["uri"],
            first_seen_at=int(time.time()),
        )

        counts = get_counts()

        print()
        print("🚀 LAUNCH SAVED")
        print("=" * 70)
        print(f"Name:       {decoded['name']}")
        print(f"Ticker:     ${decoded['symbol']}")
        print(f"Mint:       {decoded['mint']}")
        print(f"Creator:    {decoded['creator']}")
        print(f"Mayhem:     {decoded['mayhem_mode']}")
        print(f"Slot:       {transaction.get('slot', slot)}")
        print(f"Timestamp:  {block_time}")
        print()
        print(
            f"DB → {counts['launches']} launches | "
            f"{counts['buys']} buys | "
            f"{counts['wallets']} wallets"
        )
        print("=" * 70)


async def process_buy(
    signature,
    slot,
):
    async with PROCESSING_LIMIT:
        transaction = await fetch_transaction(
            signature
        )

        if transaction is None:
            return

        trade_event = extract_trade_event(
            transaction
        )

        if not trade_event:
            return

        if not trade_event["is_buy"]:
            return
        save_trade_event(
            signature,
            slot,
            trade_event,
        )
        result = save_buy(
            signature=signature,
            mint=trade_event["mint"],
            buyer=trade_event["user"],
            quote_mint=trade_event["quote_mint"],
            slot=transaction.get(
                "slot",
                slot,
            ),
            trade_timestamp=trade_event["timestamp"],
            sol_amount_lamports=trade_event["sol_amount"],
            quote_amount=trade_event["quote_amount"],
            token_amount=trade_event["token_amount"],
            protocol_fee_lamports=trade_event["fee"],
            creator_fee_lamports=trade_event["creator_fee"],
            ix_name=trade_event["ix_name"],
            mayhem_mode=trade_event["mayhem_mode"],
            observed_at=int(time.time()),
        )

        # Duplicate transaction already saved.
        if result is None:
            return

        actual_sol = (
            trade_event["sol_amount"]
            / 1_000_000_000
        )

        rank = result[
            "observed_rank"
        ]

        age = result[
            "entry_age_seconds"
        ]

        print()
        print("🦍 BUY SAVED")
        print("=" * 70)
        print(f"Wallet:     {trade_event['user']}")
        print(f"Mint:       {trade_event['mint']}")
        print(f"SOL:        {actual_sol:.6f}")
        print(f"Rank:       #{rank}")

        if age is None:
            print("Entry Age:  waiting for launch record")
        else:
            print(f"Entry Age:  {age} sec")

        print(f"Mayhem:     {trade_event['mayhem_mode']}")

        counts = get_counts()

        print()
        print(
            f"DB → {counts['launches']} launches | "
            f"{counts['buys']} buys | "
            f"{counts['wallets']} wallets"
        )
        print("=" * 70)

def process_buy_event(
    signature,
    slot,
    trade_event,
):
    result = save_buy(
        signature=signature,
        mint=trade_event["mint"],
        buyer=trade_event["user"],
        quote_mint=trade_event["quote_mint"],
        slot=slot,
        trade_timestamp=trade_event["timestamp"],
        sol_amount_lamports=trade_event["sol_amount"],
        quote_amount=trade_event["quote_amount"],
        token_amount=trade_event["token_amount"],
        protocol_fee_lamports=trade_event["fee"],
        creator_fee_lamports=trade_event["creator_fee"],
        ix_name=trade_event["ix_name"],
        mayhem_mode=trade_event["mayhem_mode"],
        observed_at=int(time.time()),
    )

    if not result:
        return

    rank = result["observed_rank"]
    entry_age = result["entry_age_seconds"]

    print()
    print("🦍 BUY SAVED")
    print("=" * 70)
    print(f"Wallet:    {trade_event['user']}")
    print(f"Mint:      {trade_event['mint']}")
    print(
        f"SOL:       "
        f"{trade_event['sol_amount'] / 1_000_000_000:.6f}"
    )

    if rank is None:
        print("Rank:      waiting for launch record")
    else:
        print(f"Rank:      #{rank}")

    if entry_age is None:
        print("Entry Age: waiting for launch record")
    else:
        print(f"Entry Age: {entry_age} sec")

    print(f"Mayhem:    {trade_event['mayhem_mode']}")

    counts = get_counts()

    print()
    print(
        f"DB → {counts['launches']} launches | "
        f"{counts['buys']} buys | "
        f"{counts['wallets']} wallets"
    )
    print("=" * 70)
async def listen():
    init_db()

    counts = get_counts()

    print("=" * 70)
    print("🧠 DELVE MEME AGENT — MARKET COLLECTOR")
    print("👁️  Pump.fun launches + buyers")
    print("🔒 READ-ONLY MODE")
    print("=" * 70)

    print(
        f"Starting DB → "
        f"{counts['launches']} launches | "
        f"{counts['buys']} buys | "
        f"{counts['wallets']} wallets"
    )

    print("=" * 70)

    while True:
        try:
            async with websockets.connect(
                WS_URL,
                ssl=SSL_CONTEXT,
                ping_interval=20,
                ping_timeout=20,
            ) as websocket:

                request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {
                            "mentions": [
                                PUMP_PROGRAM_ID
                            ]
                        },
                        {
                            "commitment": "processed"
                        }
                    ],
                }

                await websocket.send(
                    json.dumps(request)
                )

                response = json.loads(
                    await websocket.recv()
                )

                if "error" in response:
                    raise RuntimeError(
                        response["error"]
                    )

                print()
                print("✅ Connected to Solana")
                print("✅ Collector is learning")
                print("-" * 70)

                async for message in websocket:
                    payload = json.loads(
                        message
                    )

                    params = payload.get(
                        "params"
                    )

                    if not params:
                        continue

                    result = params.get(
                        "result",
                        {},
                    )

                    value = result.get(
                        "value",
                        {},
                    )

                    if value.get("err") is not None:
                        continue

                    signature = value.get(
                        "signature"
                    )

                    logs = value.get(
                        "logs",
                        [],
                    )

                    slot = (
                        result
                        .get("context", {})
                        .get("slot")
                    )

                    is_create = any(
                        "Instruction: CreateV2"
                        in log
                        for log in logs
                    )
                    if is_create:
                        asyncio.create_task(
                            process_launch(
                                signature,
                                slot,
                            )
                        )
                    trade_event = extract_trade_event_from_logs(
                        logs
                    )

                    if trade_event:
                        save_trade_event(
                            signature,
                            slot,
                            trade_event,
                        )

                        if trade_event["is_buy"]:
                            process_buy_event(
                                signature,
                                slot,
                                trade_event,
                            )

        except asyncio.CancelledError:
            raise

        except Exception as error:
            print()
            print(
                f"⚠️ Collector error: {error}"
            )
            print(
                "🔄 Reconnecting in 2 seconds..."
            )

            await asyncio.sleep(2)


if __name__ == "__main__":
    try:
        asyncio.run(
            listen()
        )
    except KeyboardInterrupt:
        print()
        print("🛑 Market Collector stopped.")