"""Pure parsing for Coinbase Advanced market_trades messages."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from unicodedata import category

from src.domain.instrument_id import InstrumentId
from src.venues.coinbase.instrument_identity import instrument_id_from_product_id


MARKET_TRADES_CHANNEL: Final[str] = "market_trades"
MARKET_TRADE_EVENT_TYPES: Final[frozenset[str]] = frozenset({"snapshot", "update"})
MAKER_SIDES: Final[frozenset[str]] = frozenset({"BUY", "SELL"})


class CoinbaseMarketTradesMessageError(ValueError):
    """A known market_trades message violates the Coinbase boundary contract."""


@dataclass(frozen=True, slots=True)
class CoinbaseMarketTrade:
    """One Coinbase trade with maker-side semantics and envelope provenance."""

    event_type: str
    envelope_timestamp: str
    sequence_num: int
    trade_id: str
    product_id: str
    instrument_id: InstrumentId
    price: Decimal
    size: Decimal
    maker_side: str
    trade_time: str

    def __post_init__(self) -> None:
        event_type = _exact_text(self.event_type, "event_type")
        if event_type not in MARKET_TRADE_EVENT_TYPES:
            raise CoinbaseMarketTradesMessageError(
                "event_type must be snapshot or update"
            )
        _timestamp_text(self.envelope_timestamp, "envelope_timestamp")
        if (
            not isinstance(self.sequence_num, int)
            or isinstance(self.sequence_num, bool)
            or self.sequence_num < 0
        ):
            raise CoinbaseMarketTradesMessageError(
                "sequence_num must be a nonnegative integer"
            )
        _exact_text(self.trade_id, "trade_id")
        product_id = _exact_text(self.product_id, "product_id")
        if not isinstance(self.instrument_id, InstrumentId):
            raise CoinbaseMarketTradesMessageError(
                "instrument_id must be an InstrumentId"
            )
        if self.instrument_id != instrument_id_from_product_id(product_id):
            raise CoinbaseMarketTradesMessageError(
                "instrument_id must match product_id"
            )
        _require_positive_decimal(self.price, "price")
        _require_positive_decimal(self.size, "size")
        maker_side = _exact_text(self.maker_side, "maker_side")
        if maker_side not in MAKER_SIDES:
            raise CoinbaseMarketTradesMessageError(
                "maker_side must be BUY or SELL"
            )
        _timestamp_text(self.trade_time, "trade_time")


def _required(mapping: Mapping[str, Any], field_name: str) -> Any:
    if field_name not in mapping:
        raise CoinbaseMarketTradesMessageError(f"{field_name} is missing")
    return mapping[field_name]


def _exact_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise CoinbaseMarketTradesMessageError(f"{field_name} must be a string")
    if not value or not value.strip():
        raise CoinbaseMarketTradesMessageError(f"{field_name} must not be empty")
    if value != value.strip():
        raise CoinbaseMarketTradesMessageError(
            f"{field_name} must not have leading or trailing whitespace"
        )
    if any(category(character) == "Cc" for character in value):
        raise CoinbaseMarketTradesMessageError(
            f"{field_name} must not contain control characters"
        )
    return value


def _positive_decimal(value: Any, field_name: str) -> Decimal:
    text = _exact_text(value, field_name)
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        raise CoinbaseMarketTradesMessageError(
            f"{field_name} must be a valid decimal string"
        ) from None
    _require_positive_decimal(parsed, field_name)
    return parsed


def _require_positive_decimal(value: Any, field_name: str) -> None:
    if type(value) is not Decimal:
        raise CoinbaseMarketTradesMessageError(
            f"{field_name} must be a Decimal"
        )
    if not value.is_finite() or value <= 0:
        raise CoinbaseMarketTradesMessageError(
            f"{field_name} must be a positive finite decimal"
        )


def _timestamp_text(value: Any, field_name: str) -> str:
    text = _exact_text(value, field_name)
    if "T" not in text:
        raise CoinbaseMarketTradesMessageError(
            f"{field_name} must be a timezone-aware timestamp"
        )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise CoinbaseMarketTradesMessageError(
            f"{field_name} must be a timezone-aware timestamp"
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CoinbaseMarketTradesMessageError(
            f"{field_name} must be a timezone-aware timestamp"
        )
    return text


def parse_market_trades_message(
    message: Mapping[str, Any],
) -> tuple[CoinbaseMarketTrade, ...]:
    """Parse all trades in received event/trade order without side effects."""
    if not isinstance(message, Mapping):
        raise TypeError("message must be a mapping")

    channel = _exact_text(_required(message, "channel"), "channel")
    if channel != MARKET_TRADES_CHANNEL:
        raise CoinbaseMarketTradesMessageError("channel must be market_trades")

    envelope_timestamp = _timestamp_text(
        _required(message, "timestamp"), "timestamp"
    )
    sequence_num = _required(message, "sequence_num")
    if (
        not isinstance(sequence_num, int)
        or isinstance(sequence_num, bool)
        or sequence_num < 0
    ):
        raise CoinbaseMarketTradesMessageError(
            "sequence_num must be a nonnegative integer"
        )

    events = _required(message, "events")
    if not isinstance(events, list) or not events:
        raise CoinbaseMarketTradesMessageError("events must be a nonempty list")

    parsed_trades: list[CoinbaseMarketTrade] = []
    for event in events:
        if not isinstance(event, Mapping):
            raise CoinbaseMarketTradesMessageError("event must be a mapping")
        event_type = _exact_text(_required(event, "type"), "event type")
        if event_type not in MARKET_TRADE_EVENT_TYPES:
            raise CoinbaseMarketTradesMessageError(
                "event type must be snapshot or update"
            )
        trades = _required(event, "trades")
        if not isinstance(trades, list) or not trades:
            raise CoinbaseMarketTradesMessageError(
                "event trades must be a nonempty list"
            )

        for trade in trades:
            if not isinstance(trade, Mapping):
                raise CoinbaseMarketTradesMessageError("trade must be a mapping")
            trade_id = _exact_text(_required(trade, "trade_id"), "trade_id")
            product_id = _exact_text(
                _required(trade, "product_id"), "product_id"
            )
            maker_side = _exact_text(_required(trade, "side"), "side")
            if maker_side not in MAKER_SIDES:
                raise CoinbaseMarketTradesMessageError("side must be BUY or SELL")
            trade_time = _timestamp_text(_required(trade, "time"), "time")

            parsed_trades.append(
                CoinbaseMarketTrade(
                    event_type=event_type,
                    envelope_timestamp=envelope_timestamp,
                    sequence_num=sequence_num,
                    trade_id=trade_id,
                    product_id=product_id,
                    instrument_id=instrument_id_from_product_id(product_id),
                    price=_positive_decimal(_required(trade, "price"), "price"),
                    size=_positive_decimal(_required(trade, "size"), "size"),
                    maker_side=maker_side,
                    trade_time=trade_time,
                )
            )

    return tuple(parsed_trades)
