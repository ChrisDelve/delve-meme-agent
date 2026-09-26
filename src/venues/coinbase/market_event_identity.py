"""Translate Coinbase Advanced trade identity into a generic event ID."""

from src.domain.market_event_id import MarketEventId
from src.venues.coinbase.market_trades import CoinbaseMarketTrade


def market_event_id_from_coinbase_trade(
    trade: CoinbaseMarketTrade,
) -> MarketEventId:
    """Use the Coinbase trade ID as its venue-native event ID."""
    if not isinstance(trade, CoinbaseMarketTrade):
        raise TypeError("trade must be a CoinbaseMarketTrade")
    return MarketEventId(
        instrument_id=trade.instrument_id,
        venue_event_id=trade.trade_id,
    )
