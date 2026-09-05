import asyncio
import hashlib
import json
import os
import ssl

import base58
import certifi
import websockets
from dotenv import load_dotenv

from src.data.transaction_decoder import fetch_transaction


load_dotenv()

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

if not HELIUS_API_KEY:
    raise RuntimeError("HELIUS_API_KEY is missing from .env")

WS_URL = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

SSL_CONTEXT = ssl.create_default_context(
    cafile=certifi.where()
)

BUY_V2_DISCRIMINATOR = hashlib.sha256(
    b"global:buy_v2"
).digest()[:8]

BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR = hashlib.sha256(
    b"global:buy_exact_quote_in_v2"
).digest()[:8]


def resolve_accounts(transaction, accounts):
    message = (
        transaction
        .get("transaction", {})
        .get("message", {})
    )

    account_keys = message.get("accountKeys", [])

    resolved = []

    for account in accounts:
        if isinstance(account, str):
            resolved.append(account)
            continue

        if isinstance(account, int) and account < len(account_keys):
            key = account_keys[account]

            if isinstance(key, dict):
                resolved.append(key.get("pubkey"))
            else:
                resolved.append(str(key))

    return resolved


def get_all_instructions(transaction):
    message = (
        transaction
        .get("transaction", {})
        .get("message", {})
    )

    for instruction in message.get("instructions", []):
        yield instruction

    meta = transaction.get("meta", {})

    for group in meta.get("innerInstructions", []) or []:
        for instruction in group.get("instructions", []):
            yield instruction


def decode_buy_instruction(transaction):
    for instruction in get_all_instructions(transaction):

        if instruction.get("programId") != PUMP_PROGRAM_ID:
            continue

        encoded_data = instruction.get("data")

        if not isinstance(encoded_data, str):
            continue

        try:
            raw = base58.b58decode(encoded_data)
        except Exception:
            continue

        if len(raw) < 24:
            continue

        discriminator = raw[:8]

        if discriminator not in (
            BUY_V2_DISCRIMINATOR,
            BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR,
        ):
            continue

        accounts = resolve_accounts(
            transaction,
            instruction.get("accounts", [])
        )

        # Pump buy_v2 account order:
        # [1] base mint
        # [2] quote mint
        # [13] user / buyer
        if len(accounts) < 14:
            continue

        mint = accounts[1]
        quote_mint = accounts[2]
        buyer = accounts[13]

        value_1 = int.from_bytes(
            raw[8:16],
            byteorder="little"
        )

        value_2 = int.from_bytes(
            raw[16:24],
            byteorder="little"
        )

        if discriminator == BUY_V2_DISCRIMINATOR:
            instruction_type = "BuyV2"

            base_amount = value_1
            quote_limit = value_2

        else:
            instruction_type = "BuyExactQuoteInV2"

            quote_limit = value_1
            base_amount = value_2

        return {
            "type": instruction_type,
            "mint": mint,
            "quote_mint": quote_mint,
            "buyer": buyer,
            "base_amount": base_amount,
            "quote_limit": quote_limit,
        }

    return None


async def run_probe():
    print("=" * 70)
    print("🦍 DELVE MEME AGENT — APE PROBE")
    print("👁️ Waiting for a Pump.fun V2 buy...")
    print("🔒 READ-ONLY MODE")
    print("=" * 70)

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
                    "mentions": [PUMP_PROGRAM_ID]
                },
                {
                    "commitment": "processed"
                }
            ]
        }

        await websocket.send(json.dumps(request))

        response = json.loads(
            await websocket.recv()
        )

        if "error" in response:
            raise RuntimeError(response["error"])

        print("✅ Connected")
        print("⏳ Hunting first V2 ape...")
        print("-" * 70)

        async for message in websocket:
            payload = json.loads(message)

            params = payload.get("params")

            if not params:
                continue

            value = (
                params
                .get("result", {})
                .get("value", {})
            )

            if value.get("err") is not None:
                continue

            logs = value.get("logs", [])

            is_buy = any(
                (
                    "Instruction: BuyV2" in log
                    or
                    "Instruction: BuyExactQuoteInV2" in log
                )
                for log in logs
            )

            if not is_buy:
                continue

            signature = value.get("signature")

            transaction = await fetch_transaction(
                signature
            )

            if transaction is None:
                continue

            decoded = decode_buy_instruction(
                transaction
            )

            if not decoded:
                continue

            print()
            print("🦍 APE DETECTED")
            print("=" * 70)
            print(f"Buyer:       {decoded['buyer']}")
            print(f"Mint:        {decoded['mint']}")
            print(f"Quote Mint:  {decoded['quote_mint']}")
            print(f"Instruction: {decoded['type']}")

            if decoded["type"] == "BuyExactQuoteInV2":
                print(f"SOL Budget:  {decoded['quote_limit']} lamports")
                print(f"Min Tokens:  {decoded['base_amount']} base units")
            else:
                print(f"Tokens Req:  {decoded['base_amount']} base units")
                print(f"Max Cost:    {decoded['quote_limit']} quote units")
            
            print(f"Signature:   {signature}")
            print("=" * 70)

            print()
            print("✅ First buyer successfully decoded.")
            print("🛑 Probe complete.")

            return


if __name__ == "__main__":
    try:
        asyncio.run(run_probe())
    except KeyboardInterrupt:
        print("\n🛑 Ape Probe stopped.")