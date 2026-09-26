"""Translate Coinbase Advanced product IDs into scoped instrument identities."""

from typing import Final

from src.domain.instrument_id import InstrumentId


COINBASE_ADVANCED_VENUE_ID: Final[str] = "coinbase-advanced"
COINBASE_PRODUCTION_ENVIRONMENT_ID: Final[str] = "production"


def instrument_id_from_product_id(product_id: str) -> InstrumentId:
    """Scope exact product ID text to Coinbase Advanced production."""
    return InstrumentId(
        venue_id=COINBASE_ADVANCED_VENUE_ID,
        environment_id=COINBASE_PRODUCTION_ENVIRONMENT_ID,
        venue_instrument_id=product_id,
    )
