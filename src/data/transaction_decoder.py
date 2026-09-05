import asyncio
import hashlib
import os
import ssl
import sys
import time

import aiohttp
import base58
import certifi
from dotenv import load_dotenv


load_dotenv()

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

if not HELIUS_API_KEY:
    raise RuntimeError("HELIUS_API_KEY is missing from .env")

RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
RPC_MIN_INTERVAL_SECONDS = 0.25

_rpc_lock = asyncio.Lock()
_last_rpc_request = 0.0


async def wait_for_rpc_slot():
    global _last_rpc_request

    async with _rpc_lock:
        now = time.monotonic()

        wait_time = (
            RPC_MIN_INTERVAL_SECONDS
            - (now - _last_rpc_request)
        )

        if wait_time > 0:
            await asyncio.sleep(wait_time)

        _last_rpc_request = time.monotonic()

# Anchor instruction discriminator:
# first 8 bytes of sha256("global:create_v2")
CREATE_V2_DISCRIMINATOR = hashlib.sha256(
    b"global:create_v2"
).digest()[:8]


def read_borsh_string(data: bytes, offset: int):
    if offset + 4 > len(data):
        raise ValueError("Not enough bytes for string length")

    length = int.from_bytes(
        data[offset:offset + 4],
        byteorder="little"
    )

    offset += 4
    end = offset + length

    if end > len(data):
        raise ValueError("String extends beyond instruction data")

    value = data[offset:end].decode("utf-8", errors="replace")

    return value, end


def decode_create_v2_instruction(instruction):
    if instruction.get("programId") != PUMP_PROGRAM_ID:
        return None

    encoded_data = instruction.get("data")

    if not isinstance(encoded_data, str):
        return None

    try:
        raw = base58.b58decode(encoded_data)
    except Exception:
        return None

    if len(raw) < 8:
        return None

    if raw[:8] != CREATE_V2_DISCRIMINATOR:
        return None

    accounts = instruction.get("accounts", [])

    if not accounts:
        raise ValueError("CreateV2 instruction has no accounts")

    offset = 8

    name, offset = read_borsh_string(raw, offset)
    symbol, offset = read_borsh_string(raw, offset)
    uri, offset = read_borsh_string(raw, offset)

    if offset + 32 > len(raw):
        raise ValueError("Creator pubkey missing")

    creator_bytes = raw[offset:offset + 32]
    creator = base58.b58encode(creator_bytes).decode("utf-8")

    offset += 32

    mayhem_mode = None

    if offset < len(raw):
        mayhem_mode = bool(raw[offset])

    return {
        "mint": accounts[0],
        "name": name,
        "symbol": symbol,
        "uri": uri,
        "creator": creator,
        "mayhem_mode": mayhem_mode,
    }


async def fetch_transaction(signature: str):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {
                "encoding": "jsonParsed",
                "commitment": "confirmed",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    }

    connector = aiohttp.TCPConnector(
        ssl=SSL_CONTEXT
    )

    backoff_seconds = 1.0

    async with aiohttp.ClientSession(
        connector=connector
    ) as session:

        for attempt in range(10):

            await wait_for_rpc_slot()

            try:
                async with session.post(
                    RPC_URL,
                    json=payload
                ) as response:

                    if response.status == 429:
                        retry_after = response.headers.get(
                            "Retry-After"
                        )

                        try:
                            delay = float(retry_after)
                        except (TypeError, ValueError):
                            delay = backoff_seconds

                        delay = min(
                            max(delay, 1.0),
                            8.0,
                        )

                        print(
                            f"⏳ Helius busy — "
                            f"backing off {delay:.1f}s"
                        )

                        await asyncio.sleep(delay)

                        backoff_seconds = min(
                            backoff_seconds * 2,
                            8.0,
                        )

                        continue

                    if response.status >= 500:
                        await asyncio.sleep(
                            backoff_seconds
                        )

                        backoff_seconds = min(
                            backoff_seconds * 2,
                            8.0,
                        )

                        continue

                    if response.status != 200:
                        body = await response.text()

                        raise RuntimeError(
                            f"Helius HTTP "
                            f"{response.status}: "
                            f"{body[:200]}"
                        )

                    result = await response.json()

            except aiohttp.ClientError as error:

                if attempt == 9:
                    raise RuntimeError(
                        "Helius network request failed: "
                        f"{type(error).__name__}"
                    ) from error

                await asyncio.sleep(
                    backoff_seconds
                )

                backoff_seconds = min(
                    backoff_seconds * 2,
                    8.0,
                )

                continue

            if "error" in result:
                raise RuntimeError(
                    f"Helius RPC error: "
                    f"{result['error']}"
                )

            transaction = result.get("result")

            if transaction is not None:
                return transaction

            # Newly observed transactions can take a moment
            # to reach confirmed commitment.
            await asyncio.sleep(0.5)

    return None


async def decode_transaction(signature: str):
    transaction = await fetch_transaction(signature)

    if transaction is None:
        print("❌ Transaction was not found after retries.")
        return

    message = transaction.get(
        "transaction", {}
    ).get(
        "message", {}
    )

    instructions = message.get("instructions", [])

    decoded = None

    for instruction in instructions:
        decoded = decode_create_v2_instruction(instruction)

        if decoded:
            break

    if decoded is None:
        print("❌ No Pump.fun CreateV2 instruction found.")
        return

    print()
    print("=" * 70)
    print("🚀 DELVE MEME AGENT — PUMP.FUN LAUNCH DECODED")
    print("=" * 70)
    print(f"Name:       {decoded['name']}")
    print(f"Ticker:     ${decoded['symbol']}")
    print(f"Mint:       {decoded['mint']}")
    print(f"Creator:    {decoded['creator']}")
    print(f"Mayhem:     {decoded['mayhem_mode']}")
    print(f"Slot:       {transaction.get('slot')}")
    print(f"Block Time: {transaction.get('blockTime')}")
    print(f"Signature:  {signature}")
    print(f"Metadata:   {decoded['uri']}")
    print("=" * 70)


async def main():
    if len(sys.argv) != 2:
        print()
        print("Usage:")
        print(
            "python -m src.data.transaction_decoder "
            "<TRANSACTION_SIGNATURE>"
        )
        return

    signature = sys.argv[1]

    await decode_transaction(signature)


if __name__ == "__main__":
    asyncio.run(main())