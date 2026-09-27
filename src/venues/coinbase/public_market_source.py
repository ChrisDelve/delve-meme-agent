"""Single-session public Coinbase Advanced WebSocket wire source."""

from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
import json
import time
from typing import Any

from websockets.asyncio.client import connect

from src.venues.coinbase.public_subscription import (
    COINBASE_PUBLIC_MARKET_WS_URL,
    heartbeat_subscription,
    market_trades_subscription,
)


@dataclass(frozen=True, slots=True)
class CoinbasePublicWireMessage:
    """One exact text frame and its local wall-clock receipt time."""

    received_at_unix_ns: int
    raw_text: str

    def __post_init__(self) -> None:
        if type(self.received_at_unix_ns) is not int:
            raise TypeError("received_at_unix_ns must be an exact int")
        if self.received_at_unix_ns < 0:
            raise ValueError("received_at_unix_ns must be nonnegative")
        if type(self.raw_text) is not str:
            raise TypeError("raw_text must be an exact str")
        if not self.raw_text:
            raise ValueError("raw_text must not be empty")


async def stream_public_market_messages(
    product_ids: Iterable[str],
    *,
    connect_factory: Callable[[str], Any] = connect,
    clock_ns: Callable[[], int] = time.time_ns,
) -> AsyncIterator[CoinbasePublicWireMessage]:
    """Yield exact text frames from one public Coinbase connection session."""

    market_subscription = market_trades_subscription(product_ids)
    heartbeat = heartbeat_subscription()

    async with connect_factory(COINBASE_PUBLIC_MARKET_WS_URL) as websocket:
        await websocket.send(json.dumps(market_subscription.to_payload()))
        await websocket.send(json.dumps(heartbeat.to_payload()))

        async for raw_frame in websocket:
            if type(raw_frame) is not str:
                raise TypeError("Coinbase public WebSocket frames must be text")
            received_at_unix_ns = clock_ns()
            yield CoinbasePublicWireMessage(
                received_at_unix_ns=received_at_unix_ns,
                raw_text=raw_frame,
            )
