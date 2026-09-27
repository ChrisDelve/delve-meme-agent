"""Public Coinbase Advanced WebSocket subscription contracts."""

from collections.abc import Iterable
from dataclasses import dataclass
import unicodedata


COINBASE_PUBLIC_MARKET_WS_URL = "wss://advanced-trade-ws.coinbase.com"

MARKET_TRADES_CHANNEL = "market_trades"
HEARTBEATS_CHANNEL = "heartbeats"
_SUPPORTED_CHANNELS = frozenset((MARKET_TRADES_CHANNEL, HEARTBEATS_CHANNEL))


def _validate_exact_text(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be an exact str")
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if value != value.strip():
        raise ValueError(f"{field_name} must not have boundary whitespace")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value


@dataclass(frozen=True, slots=True)
class CoinbasePublicSubscription:
    """A validated public Coinbase Advanced channel subscription."""

    channel: str
    product_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.channel) is not str:
            raise TypeError("channel must be an exact str")
        if self.channel not in _SUPPORTED_CHANNELS:
            raise ValueError(f"unsupported public Coinbase channel: {self.channel!r}")
        if type(self.product_ids) is not tuple:
            raise TypeError("product_ids must be an exact tuple")

        seen_product_ids: set[str] = set()
        for index, product_id in enumerate(self.product_ids):
            _validate_exact_text(product_id, f"product_ids[{index}]")
            if product_id in seen_product_ids:
                raise ValueError(f"duplicate product ID: {product_id!r}")
            seen_product_ids.add(product_id)

        if self.channel == MARKET_TRADES_CHANNEL and not self.product_ids:
            raise ValueError("market_trades requires at least one product ID")
        if self.channel == HEARTBEATS_CHANNEL and self.product_ids:
            raise ValueError("heartbeats must not contain product IDs")

    def to_payload(self) -> dict[str, object]:
        """Return a new JSON-compatible Coinbase subscription payload."""

        payload: dict[str, object] = {
            "type": "subscribe",
            "channel": self.channel,
        }
        if self.channel == MARKET_TRADES_CHANNEL:
            payload["product_ids"] = list(self.product_ids)
        return payload


def market_trades_subscription(
    product_ids: Iterable[str],
) -> CoinbasePublicSubscription:
    """Create a public market-trades subscription in caller-provided order."""

    if isinstance(product_ids, (str, bytes)):
        raise TypeError("product_ids must be an iterable of product ID strings")
    try:
        exact_product_ids = tuple(product_ids)
    except TypeError as error:
        raise TypeError("product_ids must be an iterable of product ID strings") from error
    return CoinbasePublicSubscription(
        channel=MARKET_TRADES_CHANNEL,
        product_ids=exact_product_ids,
    )


def heartbeat_subscription() -> CoinbasePublicSubscription:
    """Create the public heartbeat subscription."""

    return CoinbasePublicSubscription(
        channel=HEARTBEATS_CHANNEL,
        product_ids=(),
    )
