import unittest
from dataclasses import FrozenInstanceError

from src.venues.coinbase.public_subscription import (
    COINBASE_PUBLIC_MARKET_WS_URL,
    CoinbasePublicSubscription,
    heartbeat_subscription,
    market_trades_subscription,
)


class CoinbasePublicSubscriptionTests(unittest.TestCase):
    def test_exact_public_endpoint(self):
        self.assertEqual(
            COINBASE_PUBLIC_MARKET_WS_URL,
            "wss://advanced-trade-ws.coinbase.com",
        )

    def test_valid_single_product_market_trades_subscription(self):
        subscription = market_trades_subscription(("BTC-USD",))
        self.assertEqual(subscription.channel, "market_trades")
        self.assertEqual(subscription.product_ids, ("BTC-USD",))

    def test_multi_product_order_is_preserved(self):
        product_ids = ["ETH-USD", "BTC-USD", "SOL-USD"]
        subscription = market_trades_subscription(product_ids)
        self.assertEqual(subscription.product_ids, tuple(product_ids))
        self.assertEqual(
            subscription.to_payload()["product_ids"],
            product_ids,
        )

    def test_heartbeat_subscription_has_no_products(self):
        subscription = heartbeat_subscription()
        self.assertEqual(subscription.channel, "heartbeats")
        self.assertEqual(subscription.product_ids, ())

    def test_subscription_is_immutable(self):
        subscription = market_trades_subscription(("BTC-USD",))
        with self.assertRaises(FrozenInstanceError):
            subscription.channel = "heartbeats"
        with self.assertRaises(FrozenInstanceError):
            subscription.product_ids = ()

    def test_invalid_or_unsupported_channels_are_rejected(self):
        for channel in (None, 1, b"market_trades"):
            with self.subTest(channel=channel):
                with self.assertRaises(TypeError):
                    CoinbasePublicSubscription(channel=channel, product_ids=())

        for channel in ("", "market_trades ", "ticker", "HEARTBEATS"):
            with self.subTest(channel=channel):
                with self.assertRaises(ValueError):
                    CoinbasePublicSubscription(channel=channel, product_ids=())

    def test_market_trades_requires_product_ids(self):
        with self.assertRaises(ValueError):
            CoinbasePublicSubscription(
                channel="market_trades",
                product_ids=(),
            )

    def test_heartbeat_rejects_product_ids(self):
        with self.assertRaises(ValueError):
            CoinbasePublicSubscription(
                channel="heartbeats",
                product_ids=("BTC-USD",),
            )

    def test_direct_construction_requires_tuple_product_ids(self):
        for product_ids in (["BTC-USD"], "BTC-USD", None):
            with self.subTest(product_ids=product_ids):
                with self.assertRaises(TypeError):
                    CoinbasePublicSubscription(
                        channel="market_trades",
                        product_ids=product_ids,
                    )

    def test_invalid_product_id_types_are_rejected(self):
        for product_id in (None, True, 1, 1.0, b"BTC-USD"):
            with self.subTest(product_id=product_id):
                with self.assertRaises(TypeError):
                    market_trades_subscription((product_id,))

    def test_invalid_product_id_text_is_rejected(self):
        for product_id in (
            "",
            "   ",
            " BTC-USD",
            "BTC-USD ",
            "BTC\nUSD",
            "BTC\x00USD",
        ):
            with self.subTest(product_id=product_id):
                with self.assertRaises(ValueError):
                    market_trades_subscription((product_id,))

    def test_duplicate_product_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            market_trades_subscription(("BTC-USD", "ETH-USD", "BTC-USD"))

    def test_market_trades_payload_is_exact(self):
        payload = market_trades_subscription(
            ("BTC-USD", "ETH-USD"),
        ).to_payload()
        self.assertEqual(
            payload,
            {
                "type": "subscribe",
                "channel": "market_trades",
                "product_ids": ["BTC-USD", "ETH-USD"],
            },
        )

    def test_heartbeat_payload_is_exact(self):
        self.assertEqual(
            heartbeat_subscription().to_payload(),
            {
                "type": "subscribe",
                "channel": "heartbeats",
            },
        )

    def test_payload_mutation_does_not_mutate_contract(self):
        subscription = market_trades_subscription(("BTC-USD", "ETH-USD"))
        first_payload = subscription.to_payload()
        first_payload["channel"] = "changed"
        first_payload["product_ids"].append("SOL-USD")

        self.assertEqual(subscription.channel, "market_trades")
        self.assertEqual(subscription.product_ids, ("BTC-USD", "ETH-USD"))
        self.assertEqual(
            subscription.to_payload(),
            {
                "type": "subscribe",
                "channel": "market_trades",
                "product_ids": ["BTC-USD", "ETH-USD"],
            },
        )

    def test_payloads_contain_no_authentication_fields(self):
        forbidden_fields = {
            "jwt",
            "api_key",
            "secret",
            "authentication",
            "timestamp",
        }
        for subscription in (
            market_trades_subscription(("BTC-USD",)),
            heartbeat_subscription(),
        ):
            with self.subTest(channel=subscription.channel):
                self.assertTrue(
                    forbidden_fields.isdisjoint(subscription.to_payload()),
                )


if __name__ == "__main__":
    unittest.main()
