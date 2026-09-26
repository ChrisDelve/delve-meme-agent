"""Focused tests for the isolated Pump market-trade boundary."""

from dataclasses import FrozenInstanceError
import unittest

from src.domain.instrument_id import InstrumentId
from src.venues.pump.instrument_identity import instrument_id_from_mint
from src.venues.pump.market_trades import (
    PumpMarketTrade,
    PumpMarketTradeError,
    U64_MAX,
    pump_market_trade_from_event,
)


class PumpMarketTradeTests(unittest.TestCase):
    def event(self, **changes):
        value = {
            "mint": "MintExact",
            "sol_amount": 1_000_000,
            "token_amount": 2_000_000,
            "is_buy": True,
            "user": "UserExact",
            "timestamp": 1_750_000_000,
            "virtual_sol_reserves": 30_000_000,
            "virtual_token_reserves": 40_000_000,
            "real_sol_reserves": 20_000_000,
            "real_token_reserves": 25_000_000,
            "fee_basis_points": 100,
            "fee": 10_000,
            "creator_fee_basis_points": 25,
            "creator_fee": 2_500,
            "ix_name": "buy",
            "mayhem_mode": False,
            "quote_mint": "QuoteMintExact",
            "quote_amount": 1_010_000,
            "virtual_quote_reserves": 31_000_000,
            "real_quote_reserves": 21_000_000,
        }
        value.update(changes)
        return value

    def direct_trade(self, **changes):
        value = {
            "signature": "SignatureExact",
            "slot": 123,
            "observed_at": 1_750_000_001,
            "event_timestamp": 1_750_000_000,
            "mint": "MintExact",
            "instrument_id": instrument_id_from_mint("MintExact"),
            "user": "UserExact",
            "quote_mint": "QuoteMintExact",
            "is_buy": True,
            "sol_amount_lamports": 1_000_000,
            "quote_amount": 1_010_000,
            "token_amount": 2_000_000,
            "protocol_fee_lamports": 10_000,
            "creator_fee_lamports": 2_500,
            "protocol_fee_basis_points": 100,
            "creator_fee_basis_points": 25,
            "ix_name": "buy",
            "mayhem_mode": False,
            "virtual_sol_reserves": 30_000_000,
            "virtual_token_reserves": 40_000_000,
            "real_sol_reserves": 20_000_000,
            "real_token_reserves": 25_000_000,
            "virtual_quote_reserves": 31_000_000,
            "real_quote_reserves": 21_000_000,
        }
        value.update(changes)
        return PumpMarketTrade(**value)

    def test_valid_direct_construction_is_immutable(self):
        trade = self.direct_trade()
        self.assertEqual(trade.signature, "SignatureExact")
        self.assertEqual(trade.instrument_id, instrument_id_from_mint("MintExact"))
        with self.assertRaises(FrozenInstanceError):
            trade.signature = "changed"

    def test_adapter_translates_legacy_fields_exactly(self):
        trade = pump_market_trade_from_event(
            signature="SignatureExact",
            slot=123,
            observed_at=1_750_000_001,
            event=self.event(),
        )
        self.assertEqual(trade.event_timestamp, 1_750_000_000)
        self.assertEqual(trade.sol_amount_lamports, 1_000_000)
        self.assertEqual(trade.protocol_fee_lamports, 10_000)
        self.assertEqual(trade.creator_fee_lamports, 2_500)
        self.assertEqual(trade.protocol_fee_basis_points, 100)
        self.assertEqual(trade.creator_fee_basis_points, 25)
        self.assertEqual(trade.signature, "SignatureExact")
        self.assertEqual(trade.mint, "MintExact")
        self.assertEqual(trade.user, "UserExact")
        self.assertEqual(trade.quote_mint, "QuoteMintExact")
        self.assertEqual(trade.instrument_id, instrument_id_from_mint("MintExact"))

    def test_adapter_preserves_native_booleans(self):
        for is_buy in (True, False):
            for mayhem_mode in (True, False):
                with self.subTest(is_buy=is_buy, mayhem_mode=mayhem_mode):
                    trade = pump_market_trade_from_event(
                        signature="SignatureExact",
                        slot=None,
                        observed_at=1,
                        event=self.event(is_buy=is_buy, mayhem_mode=mayhem_mode),
                    )
                    self.assertIs(trade.is_buy, is_buy)
                    self.assertIs(trade.mayhem_mode, mayhem_mode)

    def test_slot_accepts_none_and_nonnegative_exact_int(self):
        for slot in (None, 0, 123):
            with self.subTest(slot=slot):
                self.assertEqual(self.direct_trade(slot=slot).slot, slot)

    def test_adapter_rejects_missing_or_malformed_mapping(self):
        for event in (None, [], "event"):
            with self.subTest(event=event), self.assertRaises(TypeError):
                pump_market_trade_from_event(
                    signature="SignatureExact", slot=1, observed_at=2, event=event
                )

        required_fields = tuple(self.event())
        for field_name in required_fields:
            event = self.event()
            del event[field_name]
            with self.subTest(field=field_name), self.assertRaises(
                PumpMarketTradeError
            ):
                pump_market_trade_from_event(
                    signature="SignatureExact", slot=1, observed_at=2, event=event
                )

    def test_invalid_exact_text_rejected(self):
        for field_name in ("signature", "mint", "user", "quote_mint", "ix_name"):
            for value in ("", " ", " value", "value ", "a\x00b", 1, None):
                with self.subTest(field=field_name, value=value), self.assertRaises(
                    PumpMarketTradeError
                ):
                    self.direct_trade(**{field_name: value})

    def test_bool_float_and_string_rejected_where_integer_required(self):
        fields = (
            "slot",
            "observed_at",
            "event_timestamp",
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
        )
        for field_name in fields:
            for value in (True, False, 1.0, "1"):
                with self.subTest(field=field_name, value=value), self.assertRaises(
                    PumpMarketTradeError
                ):
                    self.direct_trade(**{field_name: value})

    def test_negative_integer_rejected(self):
        fields = (
            "slot",
            "observed_at",
            "event_timestamp",
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
        )
        for field_name in fields:
            with self.subTest(field=field_name), self.assertRaises(
                PumpMarketTradeError
            ):
                self.direct_trade(**{field_name: -1})

    def test_u64_backed_fields_accept_maximum_and_reject_overflow(self):
        fields = (
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
        )
        for field_name in fields:
            with self.subTest(field=field_name, boundary="maximum"):
                self.assertEqual(
                    getattr(self.direct_trade(**{field_name: U64_MAX}), field_name),
                    U64_MAX,
                )
            with self.subTest(field=field_name, boundary="overflow"):
                with self.assertRaises(PumpMarketTradeError):
                    self.direct_trade(**{field_name: U64_MAX + 1})

    def test_slot_and_timestamps_do_not_use_u64_limit(self):
        for field_name in ("slot", "observed_at", "event_timestamp"):
            with self.subTest(field=field_name):
                value = U64_MAX + 1
                self.assertEqual(
                    getattr(self.direct_trade(**{field_name: value}), field_name),
                    value,
                )

    def test_exact_bool_required_for_native_boolean_fields(self):
        for field_name in ("is_buy", "mayhem_mode"):
            for value in (0, 1, "true", None):
                with self.subTest(field=field_name, value=value), self.assertRaises(
                    PumpMarketTradeError
                ):
                    self.direct_trade(**{field_name: value})

    def test_wrong_or_mismatched_instrument_rejected(self):
        for instrument_id in (
            "MintExact",
            None,
            instrument_id_from_mint("OtherMint"),
            InstrumentId("other-venue", "solana-mainnet", "MintExact"),
        ):
            with self.subTest(instrument_id=instrument_id), self.assertRaises(
                PumpMarketTradeError
            ):
                self.direct_trade(instrument_id=instrument_id)

    def test_adapter_does_not_mutate_event_mapping(self):
        event = self.event()
        original = dict(event)
        pump_market_trade_from_event(
            signature="SignatureExact", slot=123, observed_at=1, event=event
        )
        self.assertEqual(event, original)


if __name__ == "__main__":
    unittest.main()
