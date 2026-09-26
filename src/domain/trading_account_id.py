"""Venue- and environment-scoped trading account identity, without authority semantics."""

from dataclasses import dataclass
from unicodedata import category


@dataclass(frozen=True, slots=True)
class TradingAccountId:
    """Opaque venue-scoped account for trading activity and account evidence.

    This identity implies no custody, wallet ownership, signing capability,
    signer identity, fee-payer identity, settlement destination, token account,
    independent capital pool, trading authorization, process authority, or
    database authority. Accepted text is exact; Unicode Cc is rejected.
    """

    venue_id: str
    environment_id: str
    venue_account_id: str

    def __post_init__(self) -> None:
        for field_name in ("venue_id", "environment_id", "venue_account_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string")
            if not value or not value.strip():
                raise ValueError(f"{field_name} must not be empty or whitespace-only")
            if value != value.strip():
                raise ValueError(f"{field_name} must not have leading or trailing whitespace")
            if any(category(character) == "Cc" for character in value):
                raise ValueError(f"{field_name} must not contain control characters")
