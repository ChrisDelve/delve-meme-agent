import json
import unittest
from dataclasses import FrozenInstanceError

from src.venues.coinbase.market_trades import (
    CoinbaseMarketTrade,
    CoinbaseMarketTradesMessageError,
    parse_market_trades_message,
)
from src.venues.coinbase.public_market_source import CoinbasePublicWireMessage
from src.venues.coinbase.public_message_router import (
    CoinbaseHeartbeatWireFrame,
    CoinbaseIgnoredPublicFrame,
    CoinbaseParsedMarketTradesFrame,
    CoinbasePublicMessageRoutingError,
    route_public_wire_message,
)


class CoinbasePublicMessageRouterTests(unittest.TestCase):
    def trade(self, trade_id, product_id="BTC-USD"):
        return {
            "trade_id": trade_id,
            "product_id": product_id,
            "price": "65000.125",
            "size": "0.00042",
            "side": "BUY",
            "time": "2026-09-27T12:34:56.123456Z",
        }

    def market_message(self, trades=None, **changes):
        message = {
            "channel": "market_trades",
            "timestamp": "2026-09-27T12:34:57Z",
            "sequence_num": 42,
            "events": [
                {
                    "type": "update",
                    "trades": [self.trade("1001")] if trades is None else trades,
                }
            ],
        }
        message.update(changes)
        return message

    def wire(self, value, received_at_unix_ns=123456789):
        raw_text = value if isinstance(value, str) else json.dumps(value)
        return CoinbasePublicWireMessage(received_at_unix_ns, raw_text)

    def parsed_trades(self, trades=None, **changes):
        return parse_market_trades_message(
            self.market_message(trades=trades, **changes)
        )

    def test_valid_market_frame_uses_existing_parser_and_preserves_receipt(self):
        message = self.market_message()
        expected_trades = parse_market_trades_message(message)

        result = route_public_wire_message(self.wire(message, 987654321))

        self.assertIsInstance(result, CoinbaseParsedMarketTradesFrame)
        self.assertEqual(result.received_at_unix_ns, 987654321)
        self.assertEqual(result.sequence_num, 42)
        self.assertEqual(result.trades, expected_trades)
        self.assertEqual(result.product_ids, ("BTC-USD",))

    def test_multiple_trades_preserve_order_and_ordered_unique_products(self):
        trades = [
            self.trade("3", "BTC-USD"),
            self.trade("1", "ETH-USD"),
            self.trade("2", "BTC-USD"),
            self.trade("4", "SOL-USD"),
        ]

        result = route_public_wire_message(self.wire(self.market_message(trades)))

        self.assertEqual(
            [trade.trade_id for trade in result.trades],
            ["3", "1", "2", "4"],
        )
        self.assertEqual(result.product_ids, ("BTC-USD", "ETH-USD", "SOL-USD"))

    def test_direct_market_frame_accepts_consistent_values(self):
        trades = self.parsed_trades(
            [self.trade("1", "ETH-USD"), self.trade("2", "BTC-USD")]
        )
        result = CoinbaseParsedMarketTradesFrame(
            received_at_unix_ns=0,
            sequence_num=42,
            trades=trades,
            product_ids=("ETH-USD", "BTC-USD"),
        )
        self.assertEqual(result.trades, trades)

    def test_direct_market_frame_rejects_contradictory_sequence(self):
        trades = self.parsed_trades()
        for sequence_num in (41, 43):
            with self.subTest(sequence_num=sequence_num), self.assertRaises(ValueError):
                CoinbaseParsedMarketTradesFrame(
                    1,
                    sequence_num,
                    trades,
                    ("BTC-USD",),
                )

        mixed_trades = trades + self.parsed_trades(sequence_num=43)
        with self.assertRaises(ValueError):
            CoinbaseParsedMarketTradesFrame(
                1,
                42,
                mixed_trades,
                ("BTC-USD",),
            )

    def test_direct_market_frame_rejects_invalid_sequence_values(self):
        trades = self.parsed_trades()
        for sequence_num in (True, False, -1, 1.0, "42", None):
            with self.subTest(sequence_num=sequence_num), self.assertRaises(
                (TypeError, ValueError)
            ):
                CoinbaseParsedMarketTradesFrame(
                    1,
                    sequence_num,
                    trades,
                    ("BTC-USD",),
                )

    def test_direct_market_frame_rejects_incorrect_product_ids(self):
        trades = self.parsed_trades(
            [self.trade("1", "ETH-USD"), self.trade("2", "BTC-USD")]
        )
        for product_ids in (
            ("BTC-USD", "ETH-USD"),
            ("ETH-USD",),
            ("ETH-USD", "BTC-USD", "ETH-USD"),
            ["ETH-USD", "BTC-USD"],
        ):
            with self.subTest(product_ids=product_ids), self.assertRaises(
                (TypeError, ValueError)
            ):
                CoinbaseParsedMarketTradesFrame(1, 42, trades, product_ids)

    def test_direct_market_frame_rejects_invalid_trade_tuple_or_items(self):
        valid_trade = self.parsed_trades()[0]
        for trades in ((), [valid_trade], (object(),), ("trade",)):
            with self.subTest(trades=trades), self.assertRaises(
                (TypeError, ValueError)
            ):
                CoinbaseParsedMarketTradesFrame(
                    1,
                    42,
                    trades,
                    ("BTC-USD",),
                )

    def test_heartbeat_routes_separately_and_preserves_exact_wire_values(self):
        raw_text = (
            '  {"channel":"heartbeats","sequence_num":77,'
            '"heartbeat_counter":"7"}\n'
        )
        result = route_public_wire_message(
            CoinbasePublicWireMessage(101, raw_text)
        )
        self.assertEqual(
            result,
            CoinbaseHeartbeatWireFrame(
                received_at_unix_ns=101,
                sequence_num=77,
                raw_text=raw_text,
            ),
        )
        self.assertEqual(result.received_at_unix_ns, 101)
        self.assertEqual(result.sequence_num, 77)
        self.assertEqual(result.raw_text, raw_text)

    def test_zero_is_a_valid_heartbeat_sequence(self):
        result = route_public_wire_message(
            self.wire({"channel": "heartbeats", "sequence_num": 0})
        )
        self.assertEqual(result.sequence_num, 0)

    def test_direct_heartbeat_rejects_invalid_sequence_values(self):
        for sequence_num in (True, False, -1, 1.0, "1", None):
            with self.subTest(sequence_num=sequence_num), self.assertRaises(
                (TypeError, ValueError)
            ):
                CoinbaseHeartbeatWireFrame(
                    received_at_unix_ns=1,
                    sequence_num=sequence_num,
                    raw_text='{"channel":"heartbeats"}',
                )

    def test_recognized_heartbeat_requires_sequence_num(self):
        with self.assertRaisesRegex(
            CoinbasePublicMessageRoutingError,
            "sequence_num is missing",
        ):
            route_public_wire_message(self.wire({"channel": "heartbeats"}))

    def test_recognized_heartbeat_rejects_invalid_sequence_num(self):
        for sequence_num in (True, False, -1, 1.0, "1", None):
            with self.subTest(sequence_num=sequence_num), self.assertRaises(
                CoinbasePublicMessageRoutingError
            ):
                route_public_wire_message(
                    self.wire(
                        {
                            "channel": "heartbeats",
                            "sequence_num": sequence_num,
                        }
                    )
                )

    def test_unsupported_and_control_messages_are_ignored(self):
        for message in (
            {"channel": "ticker", "value": 1},
            {"type": "subscriptions", "channels": []},
            {"future_message": True},
        ):
            raw_text = json.dumps(message, separators=(",", ":"))
            with self.subTest(message=message):
                result = route_public_wire_message(
                    CoinbasePublicWireMessage(202, raw_text)
                )
                self.assertEqual(result, CoinbaseIgnoredPublicFrame(202, raw_text))

    def test_malformed_json_is_rejected(self):
        with self.assertRaises(CoinbasePublicMessageRoutingError):
            route_public_wire_message(self.wire("{not valid JSON"))

    def test_non_object_top_level_json_is_rejected(self):
        for raw_text in ('[]', '"text"', "42", "null"):
            with self.subTest(raw_text=raw_text), self.assertRaises(
                CoinbasePublicMessageRoutingError
            ):
                route_public_wire_message(self.wire(raw_text))

    def test_malformed_recognized_market_message_propagates_parser_error(self):
        message = self.market_message()
        del message["events"]
        with self.assertRaises(CoinbaseMarketTradesMessageError):
            route_public_wire_message(self.wire(message))

    def test_wrong_router_input_type_is_rejected(self):
        for value in (None, {}, "wire", object()):
            with self.subTest(value=value), self.assertRaises(TypeError):
                route_public_wire_message(value)

    def test_result_objects_are_immutable(self):
        market = route_public_wire_message(self.wire(self.market_message()))
        heartbeat = route_public_wire_message(
            self.wire({"channel": "heartbeats", "sequence_num": 5})
        )
        ignored = route_public_wire_message(self.wire({"channel": "ticker"}))

        for result, field_name, replacement in (
            (market, "sequence_num", 43),
            (heartbeat, "raw_text", "changed"),
            (ignored, "received_at_unix_ns", 2),
        ):
            with self.subTest(result=result), self.assertRaises(FrozenInstanceError):
                setattr(result, field_name, replacement)

    def test_wire_result_direct_construction_validates_fields(self):
        for timestamp, raw_text in (
            (True, "{}"),
            (-1, "{}"),
            (1, b"{}"),
            (1, ""),
        ):
            with self.subTest(
                result_type=CoinbaseHeartbeatWireFrame,
                timestamp=timestamp,
                raw_text=raw_text,
            ), self.assertRaises((TypeError, ValueError)):
                CoinbaseHeartbeatWireFrame(timestamp, 1, raw_text)
            with self.subTest(
                result_type=CoinbaseIgnoredPublicFrame,
                timestamp=timestamp,
                raw_text=raw_text,
            ), self.assertRaises((TypeError, ValueError)):
                CoinbaseIgnoredPublicFrame(timestamp, raw_text)


if __name__ == "__main__":
    unittest.main()
