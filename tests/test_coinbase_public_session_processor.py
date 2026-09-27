import json
import unittest
from dataclasses import FrozenInstanceError

from src.venues.coinbase.market_sequence import CoinbaseSequenceStatus
from src.venues.coinbase.market_trades import CoinbaseMarketTradesMessageError
from src.venues.coinbase.public_market_source import CoinbasePublicWireMessage
from src.venues.coinbase.public_message_router import (
    CoinbaseHeartbeatWireFrame,
    CoinbaseIgnoredPublicFrame,
    CoinbaseParsedMarketTradesFrame,
    CoinbasePublicMessageRoutingError,
    route_public_wire_message,
)
from src.venues.coinbase.public_sequence_tracker import (
    CoinbaseConnectionSequenceIntegrity,
    CoinbaseConnectionSequenceTracker,
)
from src.venues.coinbase.public_session_processor import (
    CoinbasePublicSessionObservation,
    CoinbasePublicSessionProcessor,
)


class CoinbasePublicSessionProcessorTests(unittest.TestCase):
    def market_message(self, sequence_num):
        return {
            "channel": "market_trades",
            "timestamp": "2026-09-27T12:34:57Z",
            "sequence_num": sequence_num,
            "events": [
                {
                    "type": "update",
                    "trades": [
                        {
                            "trade_id": f"trade-{sequence_num}",
                            "product_id": "BTC-USD",
                            "price": "65000.125",
                            "size": "0.00042",
                            "side": "BUY",
                            "time": "2026-09-27T12:34:56.123456Z",
                        }
                    ],
                }
            ],
        }

    def heartbeat_message(self, sequence_num):
        return {
            "channel": "heartbeats",
            "timestamp": "2026-09-27T12:34:57Z",
            "sequence_num": sequence_num,
            "events": [],
        }

    def wire(self, message, received_at_unix_ns=1000):
        raw_text = message if isinstance(message, str) else json.dumps(message)
        return CoinbasePublicWireMessage(received_at_unix_ns, raw_text)

    def assert_sequence(self, result, status, integrity, current):
        self.assertIs(result.sequence_observation.assessment.status, status)
        self.assertIs(result.sequence_observation.integrity, integrity)
        self.assertEqual(
            result.sequence_observation.assessment.current_sequence_num,
            current,
        )

    def test_first_market_frame_is_initial_and_intact(self):
        processor = CoinbasePublicSessionProcessor()
        result = processor.process(self.wire(self.market_message(100)))
        self.assertIsInstance(result.frame, CoinbaseParsedMarketTradesFrame)
        self.assert_sequence(
            result,
            CoinbaseSequenceStatus.INITIAL,
            CoinbaseConnectionSequenceIntegrity.INTACT,
            100,
        )

    def test_heartbeat_after_market_is_contiguous(self):
        processor = CoinbasePublicSessionProcessor()
        processor.process(self.wire(self.market_message(100)))
        result = processor.process(self.wire(self.heartbeat_message(101)))
        self.assertIsInstance(result.frame, CoinbaseHeartbeatWireFrame)
        self.assert_sequence(
            result,
            CoinbaseSequenceStatus.CONTIGUOUS,
            CoinbaseConnectionSequenceIntegrity.INTACT,
            101,
        )

    def test_market_after_heartbeat_is_contiguous(self):
        processor = CoinbasePublicSessionProcessor()
        processor.process(self.wire(self.heartbeat_message(100)))
        result = processor.process(self.wire(self.market_message(101)))
        self.assertIsInstance(result.frame, CoinbaseParsedMarketTradesFrame)
        self.assert_sequence(
            result,
            CoinbaseSequenceStatus.CONTIGUOUS,
            CoinbaseConnectionSequenceIntegrity.INTACT,
            101,
        )

    def test_alternating_channels_share_one_intact_sequence(self):
        processor = CoinbasePublicSessionProcessor()
        observations = (
            processor.process(self.wire(self.market_message(100))),
            processor.process(self.wire(self.heartbeat_message(101))),
            processor.process(self.wire(self.market_message(102))),
            processor.process(self.wire(self.heartbeat_message(103))),
        )
        self.assertIs(
            observations[0].sequence_observation.assessment.status,
            CoinbaseSequenceStatus.INITIAL,
        )
        for observation in observations[1:]:
            self.assertIs(
                observation.sequence_observation.assessment.status,
                CoinbaseSequenceStatus.CONTIGUOUS,
            )
            self.assertIs(
                observation.sequence_observation.integrity,
                CoinbaseConnectionSequenceIntegrity.INTACT,
            )
        self.assertEqual(processor.previous_sequence_num, 103)

    def test_cross_channel_gap_compromises_integrity(self):
        processor = CoinbasePublicSessionProcessor()
        processor.process(self.wire(self.market_message(100)))
        result = processor.process(self.wire(self.heartbeat_message(102)))
        self.assert_sequence(
            result,
            CoinbaseSequenceStatus.GAP,
            CoinbaseConnectionSequenceIntegrity.COMPROMISED,
            102,
        )

    def test_cross_channel_repeat_compromises_integrity(self):
        processor = CoinbasePublicSessionProcessor()
        processor.process(self.wire(self.market_message(100)))
        result = processor.process(self.wire(self.heartbeat_message(100)))
        self.assert_sequence(
            result,
            CoinbaseSequenceStatus.REPEATED,
            CoinbaseConnectionSequenceIntegrity.COMPROMISED,
            100,
        )

    def test_cross_channel_out_of_order_compromises_integrity(self):
        processor = CoinbasePublicSessionProcessor()
        processor.process(self.wire(self.heartbeat_message(100)))
        result = processor.process(self.wire(self.market_message(99)))
        self.assert_sequence(
            result,
            CoinbaseSequenceStatus.OUT_OF_ORDER,
            CoinbaseConnectionSequenceIntegrity.COMPROMISED,
            99,
        )

    def test_contiguous_after_compromise_remains_compromised(self):
        processor = CoinbasePublicSessionProcessor()
        processor.process(self.wire(self.market_message(100)))
        processor.process(self.wire(self.heartbeat_message(102)))
        result = processor.process(self.wire(self.market_message(103)))
        self.assert_sequence(
            result,
            CoinbaseSequenceStatus.CONTIGUOUS,
            CoinbaseConnectionSequenceIntegrity.COMPROMISED,
            103,
        )

    def test_previous_sequence_tracks_latest_accepted_sequenced_frame(self):
        processor = CoinbasePublicSessionProcessor()
        self.assertIsNone(processor.previous_sequence_num)
        processor.process(self.wire(self.market_message(7)))
        self.assertEqual(processor.previous_sequence_num, 7)
        processor.process(self.wire(self.heartbeat_message(8)))
        self.assertEqual(processor.previous_sequence_num, 8)

    def test_ignored_frame_returns_none_and_does_not_change_state(self):
        processor = CoinbasePublicSessionProcessor()
        processor.process(self.wire(self.market_message(10)))
        processor.process(self.wire(self.heartbeat_message(12)))
        previous = processor.previous_sequence_num
        integrity = processor.integrity

        result = processor.process(
            self.wire({"type": "subscriptions", "sequence_num": 999})
        )

        self.assertIsInstance(result.frame, CoinbaseIgnoredPublicFrame)
        self.assertIsNone(result.sequence_observation)
        self.assertEqual(processor.previous_sequence_num, previous)
        self.assertIs(processor.integrity, integrity)

    def assert_failure_preserves_state(self, processor, wire_message, error_type):
        previous = processor.previous_sequence_num
        integrity = processor.integrity
        with self.assertRaises(error_type):
            processor.process(wire_message)
        self.assertEqual(processor.previous_sequence_num, previous)
        self.assertIs(processor.integrity, integrity)

    def test_malformed_market_frame_does_not_mutate_state(self):
        processor = CoinbasePublicSessionProcessor()
        processor.process(self.wire(self.market_message(10)))
        malformed = self.market_message(11)
        del malformed["events"]
        self.assert_failure_preserves_state(
            processor,
            self.wire(malformed),
            CoinbaseMarketTradesMessageError,
        )

    def test_malformed_heartbeat_does_not_mutate_state(self):
        processor = CoinbasePublicSessionProcessor()
        processor.process(self.wire(self.market_message(10)))
        self.assert_failure_preserves_state(
            processor,
            self.wire({"channel": "heartbeats"}),
            CoinbasePublicMessageRoutingError,
        )

    def test_malformed_json_does_not_mutate_state(self):
        processor = CoinbasePublicSessionProcessor()
        processor.process(self.wire(self.market_message(10)))
        self.assert_failure_preserves_state(
            processor,
            self.wire("{not JSON"),
            CoinbasePublicMessageRoutingError,
        )

    def test_wrong_input_type_does_not_mutate_state(self):
        processor = CoinbasePublicSessionProcessor()
        processor.process(self.wire(self.market_message(10)))
        self.assert_failure_preserves_state(processor, object(), TypeError)

    def test_new_processor_starts_fresh_after_compromise(self):
        compromised = CoinbasePublicSessionProcessor()
        compromised.process(self.wire(self.market_message(1)))
        compromised.process(self.wire(self.heartbeat_message(3)))
        self.assertIs(
            compromised.integrity,
            CoinbaseConnectionSequenceIntegrity.COMPROMISED,
        )

        fresh = CoinbasePublicSessionProcessor()
        result = fresh.process(self.wire(self.heartbeat_message(500)))
        self.assert_sequence(
            result,
            CoinbaseSequenceStatus.INITIAL,
            CoinbaseConnectionSequenceIntegrity.INTACT,
            500,
        )

    def test_direct_observation_rejects_recognized_frame_without_sequence(self):
        market_frame = route_public_wire_message(
            self.wire(self.market_message(10))
        )
        heartbeat_frame = route_public_wire_message(
            self.wire(self.heartbeat_message(11))
        )
        for frame in (market_frame, heartbeat_frame):
            with self.subTest(frame=frame), self.assertRaises(TypeError):
                CoinbasePublicSessionObservation(frame, None)

    def test_direct_observation_rejects_ignored_frame_with_sequence(self):
        ignored = route_public_wire_message(self.wire({"channel": "ticker"}))
        sequence = CoinbaseConnectionSequenceTracker().observe(1)
        with self.assertRaises(ValueError):
            CoinbasePublicSessionObservation(ignored, sequence)

    def test_direct_observation_rejects_mismatched_sequence(self):
        frame = route_public_wire_message(self.wire(self.heartbeat_message(10)))
        mismatched = CoinbaseConnectionSequenceTracker().observe(11)
        with self.assertRaises(ValueError):
            CoinbasePublicSessionObservation(frame, mismatched)

    def test_direct_observation_rejects_unsupported_frame_type(self):
        with self.assertRaises(TypeError):
            CoinbasePublicSessionObservation(object(), None)

    def test_result_is_immutable(self):
        result = CoinbasePublicSessionProcessor().process(
            self.wire(self.market_message(1))
        )
        with self.assertRaises(FrozenInstanceError):
            result.frame = route_public_wire_message(
                self.wire(self.heartbeat_message(2))
            )


if __name__ == "__main__":
    unittest.main()
