"""Translate native Pump mint text into a scoped identity, without runtime I/O."""

from typing import Final

from src.domain.instrument_id import InstrumentId


PUMP_VENUE_ID: Final[str] = "pump"
SOLANA_MAINNET_ENVIRONMENT_ID: Final[str] = "solana-mainnet"


def instrument_id_from_mint(mint: str) -> InstrumentId:
    """Scope exact mint text to Pump on production Solana mainnet.

    InstrumentId owns generic text validation. This translation does not
    validate native mint format or prove that the mint exists on the venue.
    """
    return InstrumentId(
        venue_id=PUMP_VENUE_ID,
        environment_id=SOLANA_MAINNET_ENVIRONMENT_ID,
        venue_instrument_id=mint,
    )
