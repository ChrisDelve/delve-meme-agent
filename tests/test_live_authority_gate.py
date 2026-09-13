from __future__ import annotations

import asyncio
import unittest

from src.execution.live_authority_gate import (
    LIVE_AUTHORITY_GATE_VERSION,
    LiveAuthorityGate,
)


class LiveAuthorityGateTests(
    unittest.IsolatedAsyncioTestCase
):
    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_AUTHORITY_GATE_VERSION,
            "live-authority-gate-v1",
        )

    async def test_context_returns_same_gate(
        self,
    ):
        gate = LiveAuthorityGate()

        async with gate as acquired:
            self.assertIs(
                acquired,
                gate,
            )

    async def test_gate_serializes_two_authority_holders(
        self,
    ):
        gate = LiveAuthorityGate()

        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        second_entered = asyncio.Event()

        order = []

        async def first():
            async with gate:
                order.append(
                    "first-enter"
                )
                first_entered.set()

                await release_first.wait()

                order.append(
                    "first-exit"
                )

        async def second():
            await first_entered.wait()

            async with gate:
                order.append(
                    "second-enter"
                )
                second_entered.set()

        first_task = asyncio.create_task(
            first()
        )

        await first_entered.wait()

        second_task = asyncio.create_task(
            second()
        )

        await asyncio.sleep(
            0
        )

        self.assertFalse(
            second_entered.is_set()
        )

        release_first.set()

        await asyncio.gather(
            first_task,
            second_task,
        )

        self.assertTrue(
            second_entered.is_set()
        )

        self.assertEqual(
            order,
            [
                "first-enter",
                "first-exit",
                "second-enter",
            ],
        )

    async def test_exception_releases_gate(
        self,
    ):
        gate = LiveAuthorityGate()

        with self.assertRaisesRegex(
            RuntimeError,
            "^boom$",
        ):
            async with gate:
                raise RuntimeError(
                    "boom"
                )

        entered_again = False

        async with gate:
            entered_again = True

        self.assertTrue(
            entered_again
        )

    async def test_cancellation_inside_gate_releases_it(
        self,
    ):
        gate = LiveAuthorityGate()

        entered = asyncio.Event()

        async def holder():
            async with gate:
                entered.set()

                await asyncio.Event().wait()

        task = asyncio.create_task(
            holder()
        )

        await entered.wait()

        task.cancel()

        with self.assertRaises(
            asyncio.CancelledError
        ):
            await task

        entered_again = False

        async with gate:
            entered_again = True

        self.assertTrue(
            entered_again
        )


if __name__ == "__main__":
    unittest.main()
