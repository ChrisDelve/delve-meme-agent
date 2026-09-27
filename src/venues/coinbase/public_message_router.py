"""Pure routing for received public Coinbase WebSocket text frames."""

from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import Any

from src.venues.coinbase.market_trades import (
    CoinbaseMarketTrade,
    parse_market_trades_message,
)
from src.venues.coinbase.public_market_source import CoinbasePublicWireMessage


class CoinbasePublicMessageRoutingError(ValueError):
    """A public Coinbase wire frame cannot be routed safely."""


def _validate_wire_fields(received_at_unix_ns: object, raw_text: object) -> None:
    if type(received_at_unix_ns) is not int:
        raise TypeError("received_at_unix_ns must be an exact int")
    if received_at_unix_ns < 0:
        raise ValueError("received_at_unix_ns must be nonnegative")
    if type(raw_text) is not str:
        raise TypeError("raw_text must be an exact str")
    if not raw_text:
        raise ValueError("raw_text must not be empty")


def _ordered_unique_product_ids(
    trades: tuple[CoinbaseMarketTrade, ...],
) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for trade in trades:
        if trade.product_id not in seen:
            seen.add(trade.product_id)
            ordered.append(trade.product_id)
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class CoinbaseParsedMarketTradesFrame:
    """Validated parsed trades from one received market-trades frame."""

    received_at_unix_ns: int
    sequence_num: int
    trades: tuple[CoinbaseMarketTrade, ...]
    product_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.received_at_unix_ns) is not int:
            raise TypeError("received_at_unix_ns must be an exact int")
        if self.received_at_unix_ns < 0:
            raise ValueError("received_at_unix_ns must be nonnegative")
        if type(self.sequence_num) is not int:
            raise TypeError("sequence_num must be an exact int")
        if self.sequence_num < 0:
            raise ValueError("sequence_num must be nonnegative")
        if type(self.trades) is not tuple:
            raise TypeError("trades must be an exact tuple")
        if not self.trades:
            raise ValueError("trades must not be empty")
        for trade in self.trades:
            if not isinstance(trade, CoinbaseMarketTrade):
                raise TypeError("every trade must be a CoinbaseMarketTrade")
            if trade.sequence_num != self.sequence_num:
                raise ValueError("every trade must match sequence_num")
        if type(self.product_ids) is not tuple:
            raise TypeError("product_ids must be an exact tuple")
        if self.product_ids != _ordered_unique_product_ids(self.trades):
            raise ValueError(
                "product_ids must match ordered unique trade product IDs"
            )


@dataclass(frozen=True, slots=True)
class CoinbaseHeartbeatWireFrame:
    """A recognized heartbeat frame without heartbeat semantic parsing."""

    received_at_unix_ns: int
    sequence_num: int
    raw_text: str

    def __post_init__(self) -> None:
        _validate_wire_fields(self.received_at_unix_ns, self.raw_text)
        if type(self.sequence_num) is not int:
            raise TypeError("sequence_num must be an exact int")
        if self.sequence_num < 0:
            raise ValueError("sequence_num must be nonnegative")


@dataclass(frozen=True, slots=True)
class CoinbaseIgnoredPublicFrame:
    """A valid JSON object outside the currently supported public channels."""

    received_at_unix_ns: int
    raw_text: str

    def __post_init__(self) -> None:
        _validate_wire_fields(self.received_at_unix_ns, self.raw_text)


def route_public_wire_message(
    wire_message: CoinbasePublicWireMessage,
) -> (
    CoinbaseParsedMarketTradesFrame
    | CoinbaseHeartbeatWireFrame
    | CoinbaseIgnoredPublicFrame
):
    """Decode and route one Coinbase public text frame without retaining state."""

    if not isinstance(wire_message, CoinbasePublicWireMessage):
        raise TypeError("wire_message must be a CoinbasePublicWireMessage")
    try:
        message: Any = json.loads(wire_message.raw_text)
    except json.JSONDecodeError as error:
        raise CoinbasePublicMessageRoutingError("raw_text must be valid JSON") from error
    if not isinstance(message, Mapping):
        raise CoinbasePublicMessageRoutingError(
            "top-level Coinbase public JSON must be an object"
        )

    channel = message.get("channel")
    if channel == "market_trades":
        trades = parse_market_trades_message(message)
        sequence_num = trades[0].sequence_num
        return CoinbaseParsedMarketTradesFrame(
            received_at_unix_ns=wire_message.received_at_unix_ns,
            sequence_num=sequence_num,
            trades=trades,
            product_ids=_ordered_unique_product_ids(trades),
        )
    if channel == "heartbeats":
        if "sequence_num" not in message:
            raise CoinbasePublicMessageRoutingError(
                "recognized heartbeat sequence_num is missing"
            )
        heartbeat_sequence_num = message["sequence_num"]
        if type(heartbeat_sequence_num) is not int or heartbeat_sequence_num < 0:
            raise CoinbasePublicMessageRoutingError(
                "recognized heartbeat sequence_num must be a nonnegative exact int"
            )
        return CoinbaseHeartbeatWireFrame(
            received_at_unix_ns=wire_message.received_at_unix_ns,
            sequence_num=heartbeat_sequence_num,
            raw_text=wire_message.raw_text,
        )
    return CoinbaseIgnoredPublicFrame(
        received_at_unix_ns=wire_message.received_at_unix_ns,
        raw_text=wire_message.raw_text,
    )
