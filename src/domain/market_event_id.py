"""Venue-native market-event identity scoped by an instrument."""

from dataclasses import dataclass
from unicodedata import category

from src.domain.instrument_id import InstrumentId


@dataclass(frozen=True, slots=True)
class MarketEventId:
    """Opaque event identity with no strategy, replay, or capital authority."""

    instrument_id: InstrumentId
    venue_event_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, InstrumentId):
            raise TypeError("instrument_id must be an InstrumentId")
        if not isinstance(self.venue_event_id, str):
            raise TypeError("venue_event_id must be a string")
        if not self.venue_event_id or not self.venue_event_id.strip():
            raise ValueError("venue_event_id must not be empty or whitespace-only")
        if self.venue_event_id != self.venue_event_id.strip():
            raise ValueError(
                "venue_event_id must not have leading or trailing whitespace"
            )
        if any(category(character) == "Cc" for character in self.venue_event_id):
            raise ValueError("venue_event_id must not contain control characters")
