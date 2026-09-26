"""Focused tests for Coinbase Advanced identity and market-trade parsing."""

from dataclasses import FrozenInstanceError
from decimal import Decimal
import unittest

from src.domain.instrument_id import InstrumentId
from src.venues.coinbase.instrument_identity import (
    COINBASE_ADVANCED_VENUE_ID,
    COINBASE_PRODUCTION_ENVIRONMENT_ID,
    instrument_id_from_product_id,
)
from src.venues.coinbase.market_trades import (
    CoinbaseMarketTrade,
    CoinbaseMarketTradesMessageError,
    parse_market_trades_message,
)


class CoinbaseMarketTradesTests(unittest.TestCase):
    def direct_trade(self, **changes):
        value = {
            "event_type": "snapshot",
            "envelope_timestamp": "2026-09-26T12:34:57Z",
            "sequence_num": 42,
            "trade_id": "1001",
            "product_id": "BTC-USD",
            "instrument_id": instrument_id_from_product_id("BTC-USD"),
            "price": Decimal("65000.125"),
            "size": Decimal("0.00042"),
            "maker_side": "BUY",
            "trade_time": "2026-09-26T12:34:56.123456Z",
        }
        value.update(changes)
        return CoinbaseMarketTrade(**value)

    def trade(self, **changes):
        value = {
            "trade_id": "1001",
            "product_id": "BTC-USD",
            "price": "65000.125",
            "size": "0.00042",
            "side": "BUY",
            "time": "2026-09-26T12:34:56.123456Z",
        }
        value.update(changes)
        return value

    def message(self, *, event_type="snapshot", trades=None, **changes):
        value = {
            "channel": "market_trades",
            "timestamp": "2026-09-26T12:34:57Z",
            "sequence_num": 42,
            "events": [
                {
                    "type": event_type,
                    "trades": [self.trade()] if trades is None else trades,
                }
            ],
        }
        value.update(changes)
        return value

    def test_product_identity_translation_and_exact_text(self):
        product_id = "BtC-Usd.custom"
        self.assertEqual(COINBASE_ADVANCED_VENUE_ID, "coinbase-advanced")
        self.assertEqual(COINBASE_PRODUCTION_ENVIRONMENT_ID, "production")
        self.assertEqual(
            instrument_id_from_product_id(product_id),
            InstrumentId("coinbase-advanced", "production", product_id),
        )

    def test_valid_snapshot_is_immutable_and_preserves_provenance(self):
        parsed = parse_market_trades_message(self.message())
        self.assertEqual(len(parsed), 1)
        trade = parsed[0]
        self.assertEqual(trade.event_type, "snapshot")
        self.assertEqual(trade.envelope_timestamp, "2026-09-26T12:34:57Z")
        self.assertEqual(trade.sequence_num, 42)
        self.assertEqual(trade.trade_id, "1001")
        self.assertEqual(trade.product_id, "BTC-USD")
        self.assertEqual(trade.instrument_id.venue_instrument_id, "BTC-USD")
        self.assertEqual(trade.trade_time, "2026-09-26T12:34:56.123456Z")
        with self.assertRaises(FrozenInstanceError):
            trade.trade_id = "changed"

    def test_direct_valid_construction_is_immutable(self):
        trade = self.direct_trade()
        self.assertEqual(trade.instrument_id.venue_instrument_id, "BTC-USD")
        self.assertEqual(trade.price, Decimal("65000.125"))
        with self.assertRaises(FrozenInstanceError):
            trade.trade_id = "changed"

    def test_direct_construction_rejects_invalid_event_type(self):
        for value in ("other", " snapshot", "", 1, None):
            with self.subTest(value=value), self.assertRaises(
                CoinbaseMarketTradesMessageError
            ):
                self.direct_trade(event_type=value)

    def test_direct_construction_rejects_invalid_sequence_number(self):
        for value in (True, False, -1, 1.0, "42", None):
            with self.subTest(value=value), self.assertRaises(
                CoinbaseMarketTradesMessageError
            ):
                self.direct_trade(sequence_num=value)

    def test_direct_construction_rejects_invalid_identifiers(self):
        for field in ("trade_id", "product_id"):
            for value in ("", " ", " value", "value ", "a\x00b", 1, None):
                changes = {field: value}
                with self.subTest(field=field, value=value), self.assertRaises(
                    CoinbaseMarketTradesMessageError
                ):
                    self.direct_trade(**changes)

    def test_direct_construction_rejects_wrong_or_mismatched_instrument(self):
        for value in (
            "BTC-USD",
            None,
            InstrumentId("coinbase-advanced", "production", "ETH-USD"),
            InstrumentId("other-venue", "production", "BTC-USD"),
        ):
            with self.subTest(value=value), self.assertRaises(
                CoinbaseMarketTradesMessageError
            ):
                self.direct_trade(instrument_id=value)

    def test_direct_construction_requires_positive_finite_decimals(self):
        wrong_types = (1.0, 1, "1")
        invalid_decimals = (
            Decimal("0"),
            Decimal("-1"),
            Decimal("NaN"),
            Decimal("Infinity"),
            Decimal("-Infinity"),
        )
        for field in ("price", "size"):
            for value in (*wrong_types, *invalid_decimals):
                with self.subTest(field=field, value=value), self.assertRaises(
                    CoinbaseMarketTradesMessageError
                ):
                    self.direct_trade(**{field: value})

    def test_direct_construction_rejects_invalid_maker_side(self):
        for value in ("buy", "TAKER", " BUY", "", 1, None):
            with self.subTest(value=value), self.assertRaises(
                CoinbaseMarketTradesMessageError
            ):
                self.direct_trade(maker_side=value)

    def test_direct_construction_rejects_invalid_timestamps(self):
        invalid = (
            "", "not-a-time", "2026-09-26T12:34:57",
            " 2026-09-26T12:34:57Z", 1, None,
        )
        for field in ("envelope_timestamp", "trade_time"):
            for value in invalid:
                with self.subTest(field=field, value=value), self.assertRaises(
                    CoinbaseMarketTradesMessageError
                ):
                    self.direct_trade(**{field: value})

    def test_valid_update_uses_decimal_and_preserves_maker_side(self):
        for side in ("BUY", "SELL"):
            with self.subTest(side=side):
                trade = parse_market_trades_message(
                    self.message(event_type="update", trades=[self.trade(side=side)])
                )[0]
                self.assertEqual(trade.event_type, "update")
                self.assertEqual(trade.price, Decimal("65000.125"))
                self.assertEqual(trade.size, Decimal("0.00042"))
                self.assertIs(type(trade.price), Decimal)
                self.assertIs(type(trade.size), Decimal)
                self.assertEqual(trade.maker_side, side)

    def test_multiple_trades_preserve_received_order(self):
        trades = [
            self.trade(trade_id="3", product_id="BTC-USD"),
            self.trade(trade_id="1", product_id="ETH-USD"),
            self.trade(trade_id="2", product_id="SOL-USD"),
        ]
        parsed = parse_market_trades_message(self.message(trades=trades))
        self.assertEqual([trade.trade_id for trade in parsed], ["3", "1", "2"])

    def test_multiple_events_preserve_received_order(self):
        message = self.message()
        message["events"] = [
            {"type": "snapshot", "trades": [self.trade(trade_id="first")]},
            {"type": "update", "trades": [self.trade(trade_id="second")]},
        ]
        parsed = parse_market_trades_message(message)
        self.assertEqual(
            [(trade.event_type, trade.trade_id) for trade in parsed],
            [("snapshot", "first"), ("update", "second")],
        )

    def test_invalid_channel_rejected(self):
        for value in ("ticker", " market_trades", 1, None):
            with self.subTest(value=value), self.assertRaises(
                CoinbaseMarketTradesMessageError
            ):
                parse_market_trades_message(self.message(channel=value))

    def test_invalid_or_missing_sequence_number_rejected(self):
        for value in (None, True, -1, 1.0, "42"):
            with self.subTest(value=value), self.assertRaises(
                CoinbaseMarketTradesMessageError
            ):
                parse_market_trades_message(self.message(sequence_num=value))
        message = self.message()
        del message["sequence_num"]
        with self.assertRaises(CoinbaseMarketTradesMessageError):
            parse_market_trades_message(message)

    def test_missing_or_malformed_events_rejected(self):
        malformed = (None, {}, [], [None], [{"type": "update"}], [
            {"type": "other", "trades": [self.trade()]}
        ], [{"type": "update", "trades": []}])
        for events in malformed:
            with self.subTest(events=events), self.assertRaises(
                CoinbaseMarketTradesMessageError
            ):
                parse_market_trades_message(self.message(events=events))
        message = self.message()
        del message["events"]
        with self.assertRaises(CoinbaseMarketTradesMessageError):
            parse_market_trades_message(message)

    def test_malformed_trades_and_missing_fields_rejected(self):
        for trade in (None, [], "trade"):
            with self.subTest(trade=trade), self.assertRaises(
                CoinbaseMarketTradesMessageError
            ):
                parse_market_trades_message(self.message(trades=[trade]))
        for field in ("trade_id", "product_id", "price", "size", "side", "time"):
            trade = self.trade()
            del trade[field]
            with self.subTest(field=field), self.assertRaises(
                CoinbaseMarketTradesMessageError
            ):
                parse_market_trades_message(self.message(trades=[trade]))

    def test_nonpositive_or_invalid_price_and_size_rejected(self):
        invalid = ("0", "-1", "NaN", "Infinity", "abc", " 1", 1, None)
        for field in ("price", "size"):
            for value in invalid:
                with self.subTest(field=field, value=value), self.assertRaises(
                    CoinbaseMarketTradesMessageError
                ):
                    parse_market_trades_message(
                        self.message(trades=[self.trade(**{field: value})])
                    )

    def test_missing_or_invalid_identifier_and_time_text_rejected(self):
        for field in ("trade_id", "product_id"):
            for value in ("", " ", " value", "value ", "a\x00b", 1, None):
                with self.subTest(field=field, value=value), self.assertRaises(
                    (CoinbaseMarketTradesMessageError, ValueError)
                ):
                    parse_market_trades_message(
                        self.message(trades=[self.trade(**{field: value})])
                    )

        for value in (
            "", " ", " value", "value ", "a\x00b", 1, None,
            "not-a-time", "2026-09-26T12:34:56",
        ):
            with self.subTest(field="time", value=value), self.assertRaises(
                CoinbaseMarketTradesMessageError
            ):
                parse_market_trades_message(
                    self.message(trades=[self.trade(time=value)])
                )

    def test_missing_or_invalid_envelope_timestamp_rejected(self):
        for value in (None, 1, "", "not-a-time", "2026-09-26T12:34:57"):
            with self.subTest(value=value), self.assertRaises(
                CoinbaseMarketTradesMessageError
            ):
                parse_market_trades_message(self.message(timestamp=value))
        message = self.message()
        del message["timestamp"]
        with self.assertRaises(CoinbaseMarketTradesMessageError):
            parse_market_trades_message(message)

    def test_no_silent_string_coercion(self):
        for field in ("trade_id", "product_id", "price", "size", "side", "time"):
            with self.subTest(field=field), self.assertRaises(
                (CoinbaseMarketTradesMessageError, TypeError)
            ):
                parse_market_trades_message(
                    self.message(trades=[self.trade(**{field: 123})])
                )


if __name__ == "__main__":
    unittest.main()
