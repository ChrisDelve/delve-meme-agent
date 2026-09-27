import json
import unittest
from dataclasses import FrozenInstanceError

from src.venues.coinbase.public_market_source import (
    CoinbasePublicWireMessage,
    stream_public_market_messages,
)
from src.venues.coinbase.public_subscription import (
    COINBASE_PUBLIC_MARKET_WS_URL,
)


class TestConnectionError(RuntimeError):
    pass


class TestSendError(RuntimeError):
    pass


class TestReceiveError(RuntimeError):
    pass


class FakeWebSocket:
    def __init__(self, incoming=(), *, send_error_at=None):
        self._incoming = iter(incoming)
        self._send_error_at = send_error_at
        self.sent = []

    async def send(self, message):
        send_number = len(self.sent) + 1
        if self._send_error_at == send_number:
            raise TestSendError(f"send {send_number} failed")
        self.sent.append(message)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            item = next(self._incoming)
        except StopIteration:
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        return item


class FakeConnection:
    def __init__(self, websocket, *, enter_error=None):
        self.websocket = websocket
        self.enter_error = enter_error
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        if self.enter_error is not None:
            raise self.enter_error
        return self.websocket

    async def __aexit__(self, exc_type, exc, traceback):
        self.exited = True


class FakeConnectFactory:
    def __init__(self, connection):
        self.connection = connection
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return self.connection


class CountingClock:
    def __init__(self, timestamps):
        self._timestamps = iter(timestamps)
        self.call_count = 0

    def __call__(self):
        self.call_count += 1
        return next(self._timestamps)


async def collect_messages(product_ids, connect_factory, clock_ns):
    return [
        message
        async for message in stream_public_market_messages(
            product_ids,
            connect_factory=connect_factory,
            clock_ns=clock_ns,
        )
    ]


class CoinbasePublicWireMessageTests(unittest.TestCase):
    def test_valid_direct_construction_preserves_exact_text(self):
        raw_text = ' {"channel":"market_trades"}\n'
        message = CoinbasePublicWireMessage(
            received_at_unix_ns=0,
            raw_text=raw_text,
        )
        self.assertEqual(message.received_at_unix_ns, 0)
        self.assertEqual(message.raw_text, raw_text)

    def test_direct_construction_rejects_invalid_timestamps(self):
        for timestamp in (True, False, 1.0, "1", None, -1):
            with self.subTest(timestamp=timestamp):
                with self.assertRaises((TypeError, ValueError)):
                    CoinbasePublicWireMessage(timestamp, "message")

    def test_direct_construction_rejects_invalid_raw_text(self):
        for raw_text in (None, b"message", 1, ""):
            with self.subTest(raw_text=raw_text):
                with self.assertRaises((TypeError, ValueError)):
                    CoinbasePublicWireMessage(1, raw_text)

    def test_wire_message_is_immutable(self):
        message = CoinbasePublicWireMessage(1, "message")
        with self.assertRaises(FrozenInstanceError):
            message.received_at_unix_ns = 2
        with self.assertRaises(FrozenInstanceError):
            message.raw_text = "changed"


class CoinbasePublicMarketSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_connects_to_exact_endpoint_and_sends_subscriptions_in_order(self):
        websocket = FakeWebSocket()
        connection = FakeConnection(websocket)
        factory = FakeConnectFactory(connection)

        messages = await collect_messages(("BTC-USD", "ETH-USD"), factory, time_ns)

        self.assertEqual(messages, [])
        self.assertEqual(factory.urls, [COINBASE_PUBLIC_MARKET_WS_URL])
        self.assertEqual(len(websocket.sent), 2)
        self.assertEqual(
            json.loads(websocket.sent[0]),
            {
                "type": "subscribe",
                "channel": "market_trades",
                "product_ids": ["BTC-USD", "ETH-USD"],
            },
        )
        self.assertEqual(
            json.loads(websocket.sent[1]),
            {
                "type": "subscribe",
                "channel": "heartbeats",
            },
        )
        self.assertTrue(connection.exited)

    async def test_subscription_payloads_have_no_authentication_fields(self):
        websocket = FakeWebSocket()
        factory = FakeConnectFactory(FakeConnection(websocket))

        await collect_messages(("BTC-USD",), factory, time_ns)

        forbidden = {"jwt", "api_key", "secret", "authentication"}
        for sent_message in websocket.sent:
            self.assertTrue(forbidden.isdisjoint(json.loads(sent_message)))

    async def test_invalid_products_fail_before_connecting(self):
        factory = FakeConnectFactory(FakeConnection(FakeWebSocket()))

        with self.assertRaises(ValueError):
            await collect_messages((), factory, time_ns)

        self.assertEqual(factory.urls, [])

    async def test_text_frames_preserve_order_text_and_receipt_times(self):
        raw_messages = (
            '{"first": 1}',
            '  {"second":2}\n',
            '{"third":"exact"}',
        )
        websocket = FakeWebSocket(raw_messages)
        factory = FakeConnectFactory(FakeConnection(websocket))
        clock = CountingClock((101, 202, 303))

        messages = await collect_messages(("BTC-USD",), factory, clock)

        self.assertEqual([message.raw_text for message in messages], list(raw_messages))
        self.assertEqual(
            [message.received_at_unix_ns for message in messages],
            [101, 202, 303],
        )
        self.assertEqual(clock.call_count, 3)

    async def test_explicit_early_close_exits_connection_without_reconnect(self):
        websocket = FakeWebSocket(("first frame", "second frame"))
        connection = FakeConnection(websocket)
        factory = FakeConnectFactory(connection)
        clock = CountingClock((101, 202))
        generator = stream_public_market_messages(
            ("BTC-USD",),
            connect_factory=factory,
            clock_ns=clock,
        )

        first_message = await anext(generator)
        await generator.aclose()

        self.assertEqual(
            first_message,
            CoinbasePublicWireMessage(
                received_at_unix_ns=101,
                raw_text="first frame",
            ),
        )
        self.assertTrue(connection.exited)
        self.assertEqual(factory.urls, [COINBASE_PUBLIC_MARKET_WS_URL])
        self.assertEqual(clock.call_count, 1)

    async def test_binary_frame_is_rejected_without_decoding(self):
        websocket = FakeWebSocket((b'{"binary":true}',))
        connection = FakeConnection(websocket)
        factory = FakeConnectFactory(connection)
        clock = CountingClock((101,))

        with self.assertRaises(TypeError):
            await collect_messages(("BTC-USD",), factory, clock)

        self.assertEqual(clock.call_count, 0)
        self.assertTrue(connection.exited)

    async def test_empty_text_frame_is_rejected(self):
        websocket = FakeWebSocket(("",))
        connection = FakeConnection(websocket)
        factory = FakeConnectFactory(connection)
        clock = CountingClock((101,))

        with self.assertRaises(ValueError):
            await collect_messages(("BTC-USD",), factory, clock)

        self.assertEqual(clock.call_count, 1)
        self.assertTrue(connection.exited)

    async def test_connection_error_propagates_without_retry(self):
        connection = FakeConnection(
            FakeWebSocket(),
            enter_error=TestConnectionError("connect failed"),
        )
        factory = FakeConnectFactory(connection)

        with self.assertRaisesRegex(TestConnectionError, "connect failed"):
            await collect_messages(("BTC-USD",), factory, time_ns)

        self.assertEqual(factory.urls, [COINBASE_PUBLIC_MARKET_WS_URL])

    async def test_send_error_propagates_without_retry(self):
        websocket = FakeWebSocket(send_error_at=2)
        connection = FakeConnection(websocket)
        factory = FakeConnectFactory(connection)

        with self.assertRaisesRegex(TestSendError, "send 2 failed"):
            await collect_messages(("BTC-USD",), factory, time_ns)

        self.assertEqual(factory.urls, [COINBASE_PUBLIC_MARKET_WS_URL])
        self.assertEqual(len(websocket.sent), 1)
        self.assertTrue(connection.exited)

    async def test_receive_error_propagates_without_retry(self):
        websocket = FakeWebSocket((TestReceiveError("receive failed"),))
        connection = FakeConnection(websocket)
        factory = FakeConnectFactory(connection)

        with self.assertRaisesRegex(TestReceiveError, "receive failed"):
            await collect_messages(("BTC-USD",), factory, time_ns)

        self.assertEqual(factory.urls, [COINBASE_PUBLIC_MARKET_WS_URL])
        self.assertTrue(connection.exited)


def time_ns():
    return 1


if __name__ == "__main__":
    unittest.main()
