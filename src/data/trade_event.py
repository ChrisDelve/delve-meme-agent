import base64

import base58


PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# Official Pump.fun TradeEvent discriminator
TRADE_EVENT_DISCRIMINATOR = bytes([
    189, 219, 127, 211, 78, 230, 97, 238
])


class BorshReader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def read_bytes(self, size: int) -> bytes:
        end = self.offset + size

        if end > len(self.data):
            raise ValueError("TradeEvent data ended unexpectedly")

        value = self.data[self.offset:end]
        self.offset = end

        return value

    def read_u64(self) -> int:
        return int.from_bytes(
            self.read_bytes(8),
            byteorder="little",
            signed=False,
        )

    def read_i64(self) -> int:
        return int.from_bytes(
            self.read_bytes(8),
            byteorder="little",
            signed=True,
        )

    def read_u32(self) -> int:
        return int.from_bytes(
            self.read_bytes(4),
            byteorder="little",
            signed=False,
        )

    def read_u16(self) -> int:
        return int.from_bytes(
            self.read_bytes(2),
            byteorder="little",
            signed=False,
        )

    def read_bool(self) -> bool:
        return bool(self.read_bytes(1)[0])

    def read_pubkey(self) -> str:
        return base58.b58encode(
            self.read_bytes(32)
        ).decode("utf-8")

    def read_string(self) -> str:
        length = self.read_u32()

        return self.read_bytes(length).decode(
            "utf-8",
            errors="replace",
        )


def decode_trade_event_bytes(raw: bytes):
    position = raw.find(TRADE_EVENT_DISCRIMINATOR)

    # Anchor event data should be near the beginning.
    if position < 0 or position > 16:
        return None

    event_data = raw[
        position + len(TRADE_EVENT_DISCRIMINATOR):
    ]

    reader = BorshReader(event_data)

    try:
        event = {
            "mint": reader.read_pubkey(),
            "sol_amount": reader.read_u64(),
            "token_amount": reader.read_u64(),
            "is_buy": reader.read_bool(),
            "user": reader.read_pubkey(),
            "timestamp": reader.read_i64(),
            "virtual_sol_reserves": reader.read_u64(),
            "virtual_token_reserves": reader.read_u64(),
            "real_sol_reserves": reader.read_u64(),
            "real_token_reserves": reader.read_u64(),
            "fee_recipient": reader.read_pubkey(),
            "fee_basis_points": reader.read_u64(),
            "fee": reader.read_u64(),
            "creator": reader.read_pubkey(),
            "creator_fee_basis_points": reader.read_u64(),
            "creator_fee": reader.read_u64(),
            "track_volume": reader.read_bool(),
            "total_unclaimed_tokens": reader.read_u64(),
            "total_claimed_tokens": reader.read_u64(),
            "current_sol_volume": reader.read_u64(),
            "last_update_timestamp": reader.read_i64(),
            "ix_name": reader.read_string(),
            "mayhem_mode": reader.read_bool(),
            "cashback_fee_basis_points": reader.read_u64(),
            "cashback": reader.read_u64(),
            "buyback_fee_basis_points": reader.read_u64(),
            "buyback_fee": reader.read_u64(),
        }

        shareholder_count = reader.read_u32()

        shareholders = []

        for _ in range(shareholder_count):
            shareholders.append({
                "address": reader.read_pubkey(),
                "share_bps": reader.read_u16(),
            })

        event["shareholders"] = shareholders
        event["quote_mint"] = reader.read_pubkey()
        event["quote_amount"] = reader.read_u64()
        event["virtual_quote_reserves"] = reader.read_u64()
        event["real_quote_reserves"] = reader.read_u64()

        return event

    except (ValueError, IndexError):
        return None


def extract_trade_event(transaction):
    meta = transaction.get("meta", {})

    # First look for Anchor "Program data" event logs.
    for log in meta.get("logMessages", []) or []:

        if not log.startswith("Program data: "):
            continue

        encoded = log.removeprefix("Program data: ")

        try:
            raw = base64.b64decode(encoded)
        except Exception:
            continue

        event = decode_trade_event_bytes(raw)

        if event:
            return event

    # Some Pump events are emitted through CPI.
    # Search inner Pump instructions as a fallback.
    for group in meta.get("innerInstructions", []) or []:

        for instruction in group.get("instructions", []):

            if instruction.get("programId") != PUMP_PROGRAM_ID:
                continue

            encoded = instruction.get("data")

            if not isinstance(encoded, str):
                continue

            try:
                raw = base58.b58decode(encoded)
            except Exception:
                continue

            event = decode_trade_event_bytes(raw)

            if event:
                return event

    return None

def extract_trade_event_from_logs(logs):
    for log in logs:

        if not log.startswith("Program data: "):
            continue

        encoded = log.removeprefix("Program data: ")

        try:
            raw = base64.b64decode(encoded)
        except Exception:
            continue

        event = decode_trade_event_bytes(raw)

        if event:
            return event

    return None