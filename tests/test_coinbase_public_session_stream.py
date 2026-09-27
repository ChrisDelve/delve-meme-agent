import json
import unittest

from src.venues.coinbase.market_sequence import CoinbaseSequenceStatus
from src.venues.coinbase.market_trades import CoinbaseMarketTradesMessageError
from src.venues.coinbase.public_market_source import CoinbasePublicWireMessage
from src.venues.coinbase.public_message_router import CoinbaseIgnoredPublicFrame
from src.venues.coinbase.public_sequence_tracker import (
    CoinbaseConnectionSequenceIntegrity,
)
from src.venues.coinbase.public_session_stream import (
    stream_public_session_observations,
)


class TestSourceError(RuntimeError):
    pass


class TestCloseError(RuntimeError):
    pass


class FakeWireSource:
    def __init__(self, items, *, close_error=None):
        self._items = iter(items)
        self._close_error = close_error
        self.next_count = 0
        self.close_count = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.next_count += 1
        try:
            item = next(self._items)
        except StopIteration:
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        return item

    async def aclose(self):
        self.close_count += 1
        if self._close_error is not None:
            raise self._close_error


class FakeWireSourceFactory:
    def __init__(self, source_batches, *, close_error=None):
        self._source_batches = iter(source_batches)
        self._close_error = close_error
        self.calls = []
        self.sources = []

    def __call__(self, product_ids):
        self.calls.append(product_ids)
        source = FakeWireSource(
            next(self._source_batches),
            close_error=self._close_error,
        )
        self.sources.append(source)
        return source


class CoinbasePublicSessionStreamTests(unittest.IsolatedAsyncioTestCase):
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

    async def collect(self, product_ids, factory):
        return [
            observation
            async for observation in stream_public_session_observations(
                product_ids,
                wire_source_factory=factory,
            )
        ]

    def statuses(self, observations):
        return [
            observation.sequence_observation.assessment.status
            for observation in observations
            if observation.sequence_observation is not None
        ]

    def integrities(self, observations):
        return [
            observation.sequence_observation.integrity
            for observation in observations
            if observation.sequence_observation is not None
        ]

    async def test_observations_preserve_source_order_and_factory_is_called_once(self):
        product_ids = ["BTC-USD"]
        wires = [
            self.wire(self.market_message(10), 1),
            self.wire(self.heartbeat_message(11), 2),
            self.wire({"type": "subscriptions"}, 3),
        ]
        factory = FakeWireSourceFactory((wires,))

        observations = await self.collect(product_ids, factory)

        self.assertEqual(
            [observation.frame.received_at_unix_ns for observation in observations],
            [1, 2, 3],
        )
        self.assertEqual(factory.calls, [product_ids])
        self.assertIs(factory.calls[0], product_ids)
        self.assertEqual(len(factory.sources), 1)

    async def test_alternating_channels_share_one_sequence_state(self):
        wires = [
            self.wire(self.market_message(100)),
            self.wire(self.heartbeat_message(101)),
            self.wire(self.market_message(102)),
            self.wire(self.heartbeat_message(103)),
        ]
        observations = await self.collect(
            ("BTC-USD",),
            FakeWireSourceFactory((wires,)),
        )
        self.assertEqual(
            self.statuses(observations),
            [
                CoinbaseSequenceStatus.INITIAL,
                CoinbaseSequenceStatus.CONTIGUOUS,
                CoinbaseSequenceStatus.CONTIGUOUS,
                CoinbaseSequenceStatus.CONTIGUOUS,
            ],
        )
        self.assertEqual(
            self.integrities(observations),
            [CoinbaseConnectionSequenceIntegrity.INTACT] * 4,
        )

    async def test_cross_channel_gap_is_sticky_within_stream(self):
        wires = [
            self.wire(self.market_message(100)),
            self.wire(self.heartbeat_message(102)),
            self.wire(self.market_message(103)),
        ]
        observations = await self.collect(
            ("BTC-USD",),
            FakeWireSourceFactory((wires,)),
        )
        self.assertEqual(
            self.statuses(observations),
            [
                CoinbaseSequenceStatus.INITIAL,
                CoinbaseSequenceStatus.GAP,
                CoinbaseSequenceStatus.CONTIGUOUS,
            ],
        )
        self.assertEqual(
            self.integrities(observations),
            [
                CoinbaseConnectionSequenceIntegrity.INTACT,
                CoinbaseConnectionSequenceIntegrity.COMPROMISED,
                CoinbaseConnectionSequenceIntegrity.COMPROMISED,
            ],
        )

    async def test_ignored_frame_is_yielded_without_breaking_continuity(self):
        wires = [
            self.wire(self.market_message(100)),
            self.wire({"type": "subscriptions", "sequence_num": 999}),
            self.wire(self.heartbeat_message(101)),
        ]
        observations = await self.collect(
            ("BTC-USD",),
            FakeWireSourceFactory((wires,)),
        )
        self.assertEqual(len(observations), 3)
        self.assertIsInstance(observations[1].frame, CoinbaseIgnoredPublicFrame)
        self.assertIsNone(observations[1].sequence_observation)
        self.assertIs(
            observations[2].sequence_observation.assessment.status,
            CoinbaseSequenceStatus.CONTIGUOUS,
        )

    async def test_source_completion_ends_stream_and_closes_source(self):
        factory = FakeWireSourceFactory(
            ((self.wire(self.market_message(1)),),)
        )
        observations = await self.collect(("BTC-USD",), factory)
        self.assertEqual(len(observations), 1)
        self.assertEqual(factory.sources[0].close_count, 1)

    async def test_source_error_propagates_without_recreation(self):
        error = TestSourceError("source failed")
        factory = FakeWireSourceFactory(
            ((self.wire(self.market_message(1)), error),)
        )
        stream = stream_public_session_observations(
            ("BTC-USD",),
            wire_source_factory=factory,
        )

        first = await anext(stream)
        self.assertIs(
            first.sequence_observation.assessment.status,
            CoinbaseSequenceStatus.INITIAL,
        )
        with self.assertRaisesRegex(TestSourceError, "source failed"):
            await anext(stream)

        self.assertEqual(len(factory.calls), 1)
        self.assertEqual(factory.sources[0].close_count, 1)

    async def test_source_failure_stays_primary_when_close_also_fails(self):
        source_error = TestSourceError("source failed")
        close_error = TestCloseError("close failed")
        factory = FakeWireSourceFactory(
            ((self.wire(self.market_message(1)), source_error),),
            close_error=close_error,
        )
        stream = stream_public_session_observations(
            ("BTC-USD",),
            wire_source_factory=factory,
        )

        await anext(stream)
        with self.assertRaises(TestSourceError) as caught:
            await anext(stream)

        self.assertIs(caught.exception, source_error)
        self.assertIs(caught.exception.__cause__, close_error)
        self.assertEqual(factory.sources[0].close_count, 1)
        self.assertEqual(len(factory.calls), 1)

    async def test_malformed_frame_stops_before_later_source_frames(self):
        malformed = self.market_message(101)
        del malformed["events"]
        wires = (
            self.wire(self.market_message(100)),
            self.wire(malformed),
            self.wire(self.heartbeat_message(102)),
        )
        factory = FakeWireSourceFactory((wires,))
        stream = stream_public_session_observations(
            ("BTC-USD",),
            wire_source_factory=factory,
        )

        await anext(stream)
        with self.assertRaises(CoinbaseMarketTradesMessageError):
            await anext(stream)

        self.assertEqual(factory.sources[0].next_count, 2)
        self.assertEqual(factory.sources[0].close_count, 1)
        self.assertEqual(len(factory.calls), 1)

    async def test_processing_failure_stays_primary_when_close_also_fails(self):
        malformed = self.market_message(101)
        del malformed["events"]
        close_error = TestCloseError("close failed")
        wires = (
            self.wire(self.market_message(100)),
            self.wire(malformed),
            self.wire(self.heartbeat_message(102)),
        )
        factory = FakeWireSourceFactory(
            (wires,),
            close_error=close_error,
        )
        stream = stream_public_session_observations(
            ("BTC-USD",),
            wire_source_factory=factory,
        )

        await anext(stream)
        with self.assertRaises(CoinbaseMarketTradesMessageError) as caught:
            await anext(stream)

        self.assertIs(caught.exception.__cause__, close_error)
        self.assertEqual(factory.sources[0].next_count, 2)
        self.assertEqual(factory.sources[0].close_count, 1)
        self.assertEqual(len(factory.calls), 1)

    async def test_independent_invocations_start_with_fresh_integrity(self):
        first_batch = (
            self.wire(self.market_message(10)),
            self.wire(self.heartbeat_message(12)),
        )
        second_batch = (self.wire(self.market_message(10)),)
        factory = FakeWireSourceFactory((first_batch, second_batch))

        first = await self.collect(("BTC-USD",), factory)
        second = await self.collect(("BTC-USD",), factory)

        self.assertIs(
            first[-1].sequence_observation.integrity,
            CoinbaseConnectionSequenceIntegrity.COMPROMISED,
        )
        self.assertIs(
            second[0].sequence_observation.assessment.status,
            CoinbaseSequenceStatus.INITIAL,
        )
        self.assertIs(
            second[0].sequence_observation.integrity,
            CoinbaseConnectionSequenceIntegrity.INTACT,
        )
        self.assertEqual(len(factory.calls), 2)

    async def test_early_close_closes_upstream_without_recreation(self):
        factory = FakeWireSourceFactory(
            ((
                self.wire(self.market_message(1)),
                self.wire(self.heartbeat_message(2)),
            ),)
        )
        stream = stream_public_session_observations(
            ("BTC-USD",),
            wire_source_factory=factory,
        )

        first = await anext(stream)
        await stream.aclose()

        self.assertIs(
            first.sequence_observation.assessment.status,
            CoinbaseSequenceStatus.INITIAL,
        )
        self.assertEqual(factory.sources[0].next_count, 1)
        self.assertEqual(factory.sources[0].close_count, 1)
        self.assertEqual(len(factory.calls), 1)

    async def test_upstream_close_failure_is_not_suppressed(self):
        factory = FakeWireSourceFactory(
            ((self.wire(self.market_message(1)),),),
            close_error=TestCloseError("close failed"),
        )
        stream = stream_public_session_observations(
            ("BTC-USD",),
            wire_source_factory=factory,
        )
        await anext(stream)
        with self.assertRaisesRegex(TestCloseError, "close failed"):
            await stream.aclose()


if __name__ == "__main__":
    unittest.main()
