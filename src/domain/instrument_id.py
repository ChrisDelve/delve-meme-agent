"""Venue- and environment-scoped instrument identity, without adapter semantics."""

from dataclasses import dataclass
from unicodedata import category


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """Exact identity text; control characters (Unicode Cc) are not permitted."""

    venue_id: str
    environment_id: str
    venue_instrument_id: str

    def __post_init__(self) -> None:
        for field_name in ("venue_id", "environment_id", "venue_instrument_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string")
            if not value or not value.strip():
                raise ValueError(f"{field_name} must not be empty or whitespace-only")
            if value != value.strip():
                raise ValueError(f"{field_name} must not have leading or trailing whitespace")
            if any(category(character) == "Cc" for character in value):
                raise ValueError(f"{field_name} must not contain control characters")
