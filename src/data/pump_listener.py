import asyncio
import json
import os
import ssl

import certifi
import websockets
from dotenv import load_dotenv


# Load API credentials from .env
load_dotenv()

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

# Pump.fun bonding-curve program
PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

if not HELIUS_API_KEY:
    raise RuntimeError("HELIUS_API_KEY is missing from .env")

WS_URL = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


async def listen_to_pump():
    print("=" * 60)
    print("🏎️  DELVE MEME AGENT")
    print("👁️  Pump.fun listener starting...")
    print("🔒 READ-ONLY MODE")
    print("=" * 60)

    while True:
        try:
            async with websockets.connect(
                WS_URL,
                ssl=SSL_CONTEXT,
                ping_interval=20,
                ping_timeout=20,
            ) as websocket:

                subscribe_request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {
                            "mentions": [PUMP_PROGRAM_ID]
                        },
                        {
                            "commitment": "processed"
                        }
                    ]
                }

                await websocket.send(json.dumps(subscribe_request))

                response = json.loads(await websocket.recv())

                if "error" in response:
                    raise RuntimeError(
                        f"Subscription failed: {response['error']}"
                    )

                print("✅ Connected to Solana")
                print("✅ Watching Pump.fun")
                print("✅ No trading wallet connected")
                print("-" * 60)

                async for message in websocket:
                    payload = json.loads(message)

                    params = payload.get("params")
                    if not params:
                        continue

                    result = params.get("result", {})
                    value = result.get("value", {})

                    # Ignore failed transactions
                    if value.get("err") is not None:
                        continue

                    signature = value.get("signature")
                    logs = value.get("logs", [])
                    slot = result.get("context", {}).get("slot")

                    creation_logs = [
                        log
                        for log in logs
                        if "Instruction: Create" in log
                    ]

                    if not creation_logs:
                        continue

                    print()
                    print("🚨 POSSIBLE NEW PUMP.FUN TOKEN")
                    print(f"Slot:      {slot}")
                    print(f"Signature: {signature}")
                    print(f"Event:     {creation_logs[0]}")
                    print("-" * 60)

        except asyncio.CancelledError:
            raise

        except Exception as error:
            print()
            print(f"⚠️ Connection error: {error}")
            print("🔄 Reconnecting in 2 seconds...")
            await asyncio.sleep(2)


if __name__ == "__main__":
    try:
        asyncio.run(listen_to_pump())
    except KeyboardInterrupt:
        print("\n🛑 Delve Meme Agent stopped.")