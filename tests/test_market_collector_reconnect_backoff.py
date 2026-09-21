import asyncio
import unittest
from unittest.mock import Mock, patch

from src.data import market_collector


class MarketCollectorReconnectBackoffTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_repeated_connection_failures_back_off_and_cap(
        self,
    ):
        connect = Mock(
            side_effect=RuntimeError(
                "server rejected WebSocket connection: HTTP 429"
            )
        )

        observed_delays = []

        async def controlled_sleep(delay):
            observed_delays.append(
                float(delay)
            )

            if len(observed_delays) >= 7:
                raise asyncio.CancelledError()

        with (
            patch.object(
                market_collector,
                "init_db",
            ),
            patch.object(
                market_collector,
                "invalidate_stale_intervals",
            ),
            patch.object(
                market_collector,
                "get_counts",
                return_value={
                    "launches": 0,
                    "buys": 0,
                    "wallets": 0,
                },
            ),
            patch.object(
                market_collector.websockets,
                "connect",
                connect,
            ),
            patch.object(
                market_collector.asyncio,
                "sleep",
                new=controlled_sleep,
            ),
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await market_collector.listen(
                    shadow_portfolio_enabled=False
                )

        self.assertEqual(
            observed_delays,
            [
                2.0,
                4.0,
                8.0,
                16.0,
                32.0,
                60.0,
                60.0,
            ],
        )

        self.assertEqual(
            connect.call_count,
            7,
        )


if __name__ == "__main__":
    unittest.main()
