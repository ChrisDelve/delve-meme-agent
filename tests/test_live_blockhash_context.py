import asyncio
import unittest
from unittest.mock import patch

from solders.hash import Hash

from src.execution.live_blockhash_context import (
    LIVE_BLOCKHASH_CONTEXT_VERSION,
    LiveBlockhashContextError,
    resolve_live_blockhash_context,
)
from src.safety.token_safety_resolver import (
    COMMITMENT,
)


MODULE = (
    "src.execution.live_blockhash_context"
)


class FakeRpc:
    def __init__(
        self,
        *,
        result=None,
        error=None,
    ):
        self.result = result
        self.error = error
        self.calls = []

    async def __aenter__(
        self,
    ):
        return self

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        return None

    async def call(
        self,
        method,
        params,
    ):
        self.calls.append(
            (
                method,
                params,
            )
        )

        if self.error is not None:
            raise self.error

        return self.result


class LiveBlockhashContextTests(
    unittest.TestCase
):
    def setUp(self):
        self.blockhash = (
            Hash.new_unique()
        )

    def valid_result(
        self,
        *,
        slot=200,
        blockhash=None,
        last_valid_block_height=350,
    ):
        return {
            "context": {
                "slot": slot,
            },
            "value": {
                "blockhash": str(
                    self.blockhash
                    if blockhash is None
                    else blockhash
                ),
                "lastValidBlockHeight": (
                    last_valid_block_height
                ),
            },
        }

    def resolve(
        self,
        *,
        min_context_slot=100,
        rpc=None,
    ):
        if rpc is None:
            rpc = FakeRpc(
                result=(
                    self.valid_result()
                )
            )

        with patch(
            f"{MODULE}.HeliusRpcClient",
            return_value=rpc,
        ):
            result = asyncio.run(
                resolve_live_blockhash_context(
                    min_context_slot=(
                        min_context_slot
                    )
                )
            )

        return result, rpc

    def test_valid_response_preserves_blockhash_pair(
        self,
    ):
        result, rpc = self.resolve()

        self.assertEqual(
            result.resolver_version,
            LIVE_BLOCKHASH_CONTEXT_VERSION,
        )

        self.assertEqual(
            result.blockhash,
            str(
                self.blockhash
            ),
        )

        self.assertEqual(
            result.last_valid_block_height,
            350,
        )

        self.assertEqual(
            result.rpc_slot,
            200,
        )

        self.assertEqual(
            result.min_context_slot,
            100,
        )

        self.assertEqual(
            result.commitment,
            COMMITMENT,
        )

        self.assertGreater(
            result.fetched_at,
            0,
        )

        self.assertEqual(
            rpc.calls,
            [
                (
                    "getLatestBlockhash",
                    [
                        {
                            "commitment": (
                                COMMITMENT
                            ),
                            "minContextSlot": 100,
                        }
                    ],
                )
            ],
        )

    def test_context_slot_before_minimum_rejected(
        self,
    ):
        rpc = FakeRpc(
            result=self.valid_result(
                slot=99,
            )
        )

        with patch(
            f"{MODULE}.HeliusRpcClient",
            return_value=rpc,
        ):
            with self.assertRaisesRegex(
                LiveBlockhashContextError,
                "predates",
            ):
                asyncio.run(
                    resolve_live_blockhash_context(
                        min_context_slot=100
                    )
                )

    def test_invalid_min_context_slot_rejected_before_rpc(
        self,
    ):
        with patch(
            f"{MODULE}.HeliusRpcClient"
        ) as rpc_class:
            with self.assertRaises(
                LiveBlockhashContextError
            ):
                asyncio.run(
                    resolve_live_blockhash_context(
                        min_context_slot=-1
                    )
                )

        rpc_class.assert_not_called()

    def test_missing_context_rejected(
        self,
    ):
        rpc = FakeRpc(
            result={
                "value": {
                    "blockhash": str(
                        self.blockhash
                    ),
                    "lastValidBlockHeight": 350,
                }
            }
        )

        with patch(
            f"{MODULE}.HeliusRpcClient",
            return_value=rpc,
        ):
            with self.assertRaises(
                LiveBlockhashContextError
            ):
                asyncio.run(
                    resolve_live_blockhash_context(
                        min_context_slot=100
                    )
                )

    def test_invalid_blockhash_rejected(
        self,
    ):
        rpc = FakeRpc(
            result=self.valid_result(
                blockhash=(
                    "not-a-solana-blockhash"
                )
            )
        )

        with patch(
            f"{MODULE}.HeliusRpcClient",
            return_value=rpc,
        ):
            with self.assertRaisesRegex(
                LiveBlockhashContextError,
                "Blockhash",
            ):
                asyncio.run(
                    resolve_live_blockhash_context(
                        min_context_slot=100
                    )
                )

    def test_default_blockhash_rejected(
        self,
    ):
        rpc = FakeRpc(
            result=self.valid_result(
                blockhash=Hash.default()
            )
        )

        with patch(
            f"{MODULE}.HeliusRpcClient",
            return_value=rpc,
        ):
            with self.assertRaisesRegex(
                LiveBlockhashContextError,
                "Default blockhash",
            ):
                asyncio.run(
                    resolve_live_blockhash_context(
                        min_context_slot=100
                    )
                )

    def test_invalid_last_valid_block_height_rejected(
        self,
    ):
        rpc = FakeRpc(
            result=self.valid_result(
                last_valid_block_height=True,
            )
        )

        with patch(
            f"{MODULE}.HeliusRpcClient",
            return_value=rpc,
        ):
            with self.assertRaisesRegex(
                LiveBlockhashContextError,
                "last_valid_block_height",
            ):
                asyncio.run(
                    resolve_live_blockhash_context(
                        min_context_slot=100
                    )
                )

    def test_rpc_failure_fails_closed(
        self,
    ):
        rpc = FakeRpc(
            error=RuntimeError(
                "network unavailable"
            )
        )

        with patch(
            f"{MODULE}.HeliusRpcClient",
            return_value=rpc,
        ):
            with self.assertRaisesRegex(
                LiveBlockhashContextError,
                "RPC failed",
            ):
                asyncio.run(
                    resolve_live_blockhash_context(
                        min_context_slot=100
                    )
                )


if __name__ == "__main__":
    unittest.main()
