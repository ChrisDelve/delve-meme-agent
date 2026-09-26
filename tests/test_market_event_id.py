"""Focused tests for generic and venue-adapted market-event identity."""

from dataclasses import FrozenInstanceError, fields
from decimal import Decimal
import unittest

from src.domain.instrument_id import InstrumentId
from src.domain.market_event_id import MarketEventId
from src.venues.coinbase.instrument_identity import instrument_id_from_product_id
from src.venues.coinbase.market_event_identity import (
    market_event_id_from_coinbase_trade,
)
from src.venues.coinbase.market_trades import CoinbaseMarketTrade
from src.venues.pump.instrument_identity import instrument_id_from_mint
from src.venues.pump.market_event_identity import market_event_id_from_pump_trade
from src.venues.pump.market_trades import PumpMarketTrade


class MarketEventIdTests(unittest.TestCase):
    def pump_trade(self):
        return PumpMarketTrade(
            signature="PumpSignatureExact",
            slot=123,
            observed_at=1_750_000_001,
            event_timestamp=1_750_000_000,
            mint="PumpMintExact",
            instrument_id=instrument_id_from_mint("PumpMintExact"),
            user="PumpUserExact",
            quote_mint="PumpQuoteMintExact",
            is_buy=True,
            sol_amount_lamports=1_000_000,
            quote_amount=1_010_000,
            token_amount=2_000_000,
            protocol_fee_lamports=10_000,
            creator_fee_lamports=2_500,
            protocol_fee_basis_points=100,
            creator_fee_basis_points=25,
            ix_name="buy",
            mayhem_mode=False,
            virtual_sol_reserves=30_000_000,
            virtual_token_reserves=40_000_000,
            real_sol_reserves=20_000_000,
            real_token_reserves=25_000_000,
            virtual_quote_reserves=31_000_000,
            real_quote_reserves=21_000_000,
        )

    def coinbase_trade(self):
        return CoinbaseMarketTrade(
            event_type="update",
            envelope_timestamp="2026-09-26T12:34:57Z",
            sequence_num=42,
            trade_id="CoinbaseTradeExact",
            product_id="BTC-USD",
            instrument_id=instrument_id_from_product_id("BTC-USD"),
            price=Decimal("65000.125"),
            size=Decimal("0.00042"),
            maker_side="SELL",
            trade_time="2026-09-26T12:34:56.123456Z",
        )

    def test_valid_construction_is_immutable_and_hashable(self):
        instrument = InstrumentId("venue", "production", "instrument")
        identity = MarketEventId(instrument, "Event.Exact-01")
        duplicate = MarketEventId(instrument, "Event.Exact-01")
        self.assertEqual(identity.venue_event_id, "Event.Exact-01")
        self.assertEqual(identity, duplicate)
        self.assertEqual(hash(identity), hash(duplicate))
        self.assertEqual({identity: "found"}[duplicate], "found")
        with self.assertRaises(FrozenInstanceError):
            identity.venue_event_id = "changed"

    def test_invalid_instrument_type_rejected(self):
        for value in (None, "instrument", 1, object()):
            with self.subTest(value=value), self.assertRaises(TypeError):
                MarketEventId(value, "event")

    def test_invalid_event_id_text_or_type_rejected(self):
        instrument = InstrumentId("venue", "production", "instrument")
        for value in (
            None,
            1,
            True,
            b"event",
            "",
            " ",
            " event",
            "event ",
            "event\x00id",
            "event\x7fid",
            "event\x85id",
        ):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                MarketEventId(instrument, value)

    def test_same_native_id_on_different_instruments_is_unequal(self):
        first = MarketEventId(
            InstrumentId("coinbase-advanced", "production", "BTC-USD"),
            "1001",
        )
        same = MarketEventId(
            InstrumentId("coinbase-advanced", "production", "BTC-USD"),
            "1001",
        )
        different_instrument = MarketEventId(
            InstrumentId("coinbase-advanced", "production", "ETH-USD"),
            "1001",
        )
        self.assertEqual(first, same)
        self.assertNotEqual(first, different_instrument)

    def test_pump_adapter_binds_exact_signature_and_instrument(self):
        trade = self.pump_trade()
        identity = market_event_id_from_pump_trade(trade)
        self.assertEqual(identity.instrument_id, trade.instrument_id)
        self.assertEqual(identity.venue_event_id, "PumpSignatureExact")

    def test_coinbase_adapter_binds_exact_trade_id_and_instrument(self):
        trade = self.coinbase_trade()
        identity = market_event_id_from_coinbase_trade(trade)
        self.assertEqual(identity.instrument_id, trade.instrument_id)
        self.assertEqual(identity.venue_event_id, "CoinbaseTradeExact")

    def test_adapters_reject_wrong_input_types(self):
        invalid = (None, "trade", {}, object())
        for value in invalid:
            with self.subTest(adapter="pump", value=value), self.assertRaises(TypeError):
                market_event_id_from_pump_trade(value)
            with self.subTest(
                adapter="coinbase", value=value
            ), self.assertRaises(TypeError):
                market_event_id_from_coinbase_trade(value)
        with self.assertRaises(TypeError):
            market_event_id_from_pump_trade(self.coinbase_trade())
        with self.assertRaises(TypeError):
            market_event_id_from_coinbase_trade(self.pump_trade())

    def test_generic_identity_contains_no_venue_semantic_data(self):
        self.assertEqual(
            [field.name for field in fields(MarketEventId)],
            ["instrument_id", "venue_event_id"],
        )
        identity = market_event_id_from_coinbase_trade(self.coinbase_trade())
        for absent in (
            "timestamp",
            "sequence_num",
            "slot",
            "side",
            "price",
            "size",
            "user",
            "strategy",
            "authority",
            "replay_key",
        ):
            with self.subTest(absent=absent):
                self.assertFalse(hasattr(identity, absent))


if __name__ == "__main__":
    unittest.main()
