"""Translate current legacy Pump trade identity into a generic event ID."""

from src.domain.market_event_id import MarketEventId
from src.venues.pump.market_trades import PumpMarketTrade


def market_event_id_from_pump_trade(trade: PumpMarketTrade) -> MarketEventId:
    """Use the current extracted trade signature as its venue-native ID."""
    if not isinstance(trade, PumpMarketTrade):
        raise TypeError("trade must be a PumpMarketTrade")
    return MarketEventId(
        instrument_id=trade.instrument_id,
        venue_event_id=trade.signature,
    )
