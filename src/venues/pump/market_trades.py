"""Pure Pump trade-event translation without runtime integration or I/O."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from unicodedata import category

from src.domain.instrument_id import InstrumentId
from src.venues.pump.instrument_identity import instrument_id_from_mint


U64_MAX = (1 << 64) - 1


class PumpMarketTradeError(ValueError):
    """Pump trade data violates the isolated market-trade contract."""


@dataclass(frozen=True, slots=True)
class PumpMarketTrade:
    """Material Pump trade fields preserved with native Pump semantics."""

    signature: str
    slot: int | None
    observed_at: int
    event_timestamp: int
    mint: str
    instrument_id: InstrumentId
    user: str
    quote_mint: str
    is_buy: bool
    sol_amount_lamports: int
    quote_amount: int
    token_amount: int
    protocol_fee_lamports: int
    creator_fee_lamports: int
    protocol_fee_basis_points: int
    creator_fee_basis_points: int
    ix_name: str
    mayhem_mode: bool
    virtual_sol_reserves: int
    virtual_token_reserves: int
    real_sol_reserves: int
    real_token_reserves: int
    virtual_quote_reserves: int
    real_quote_reserves: int

    def __post_init__(self) -> None:
        for field_name in (
            "signature",
            "mint",
            "user",
            "quote_mint",
            "ix_name",
        ):
            _require_exact_text(getattr(self, field_name), field_name)

        if self.slot is not None:
            _require_nonnegative_int(self.slot, "slot")
        _require_nonnegative_int(self.observed_at, "observed_at")
        _require_nonnegative_int(self.event_timestamp, "event_timestamp")

        if not isinstance(self.instrument_id, InstrumentId):
            raise PumpMarketTradeError("instrument_id must be an InstrumentId")
        if self.instrument_id != instrument_id_from_mint(self.mint):
            raise PumpMarketTradeError("instrument_id must match mint")

        for field_name in ("is_buy", "mayhem_mode"):
            if type(getattr(self, field_name)) is not bool:
                raise PumpMarketTradeError(f"{field_name} must be bool")

        for field_name in (
            "sol_amount_lamports",
            "quote_amount",
            "token_amount",
            "protocol_fee_lamports",
            "creator_fee_lamports",
            "protocol_fee_basis_points",
            "creator_fee_basis_points",
            "virtual_sol_reserves",
            "virtual_token_reserves",
            "real_sol_reserves",
            "real_token_reserves",
            "virtual_quote_reserves",
            "real_quote_reserves",
        ):
            _require_u64(getattr(self, field_name), field_name)


def _require_exact_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise PumpMarketTradeError(f"{field_name} must be a string")
    if not value or not value.strip():
        raise PumpMarketTradeError(f"{field_name} must not be empty")
    if value != value.strip():
        raise PumpMarketTradeError(
            f"{field_name} must not have leading or trailing whitespace"
        )
    if any(category(character) == "Cc" for character in value):
        raise PumpMarketTradeError(
            f"{field_name} must not contain control characters"
        )
    return value


def _require_nonnegative_int(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise PumpMarketTradeError(
            f"{field_name} must be a nonnegative integer"
        )
    return value


def _require_u64(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 0 or value > U64_MAX:
        raise PumpMarketTradeError(f"{field_name} must be a u64 integer")
    return value


def _required(event: Mapping[str, Any], field_name: str) -> Any:
    if field_name not in event:
        raise PumpMarketTradeError(f"{field_name} is missing")
    return event[field_name]


def pump_market_trade_from_event(
    *,
    signature: str,
    slot: int | None,
    observed_at: int,
    event: Mapping[str, Any],
) -> PumpMarketTrade:
    """Translate one legacy Pump trade_event mapping without mutation."""
    if not isinstance(event, Mapping):
        raise TypeError("event must be a mapping")

    mint = _require_exact_text(_required(event, "mint"), "mint")
    return PumpMarketTrade(
        signature=signature,
        slot=slot,
        observed_at=observed_at,
        event_timestamp=_required(event, "timestamp"),
        mint=mint,
        instrument_id=instrument_id_from_mint(mint),
        user=_required(event, "user"),
        quote_mint=_required(event, "quote_mint"),
        is_buy=_required(event, "is_buy"),
        sol_amount_lamports=_required(event, "sol_amount"),
        quote_amount=_required(event, "quote_amount"),
        token_amount=_required(event, "token_amount"),
        protocol_fee_lamports=_required(event, "fee"),
        creator_fee_lamports=_required(event, "creator_fee"),
        protocol_fee_basis_points=_required(event, "fee_basis_points"),
        creator_fee_basis_points=_required(event, "creator_fee_basis_points"),
        ix_name=_required(event, "ix_name"),
        mayhem_mode=_required(event, "mayhem_mode"),
        virtual_sol_reserves=_required(event, "virtual_sol_reserves"),
        virtual_token_reserves=_required(event, "virtual_token_reserves"),
        real_sol_reserves=_required(event, "real_sol_reserves"),
        real_token_reserves=_required(event, "real_token_reserves"),
        virtual_quote_reserves=_required(event, "virtual_quote_reserves"),
        real_quote_reserves=_required(event, "real_quote_reserves"),
    )
