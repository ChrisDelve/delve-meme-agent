import unittest
from unittest.mock import (
    MagicMock,
    patch,
)

from solders.pubkey import Pubkey

from src.execution.live_wallet_balance import (
    LIVE_WALLET_BALANCE_VERSION,
    RESOLVED,
    UNKNOWN,
    U64_MAX,
    resolve_live_wallet_balance,
)
from src.safety.token_safety_resolver import (
    COMMITMENT,
)


MODULE = (
    "src.execution.live_wallet_balance"
)


class FakeRpcClient:
    def __init__(
        self,
        *,
        response=None,
        error=None,
    ):
        self.response = response
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

        return self.response


class LiveWalletBalanceTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.wallet_pubkey = str(
            Pubkey.new_unique()
        )

    async def resolve(
        self,
        *,
        wallet_pubkey=None,
        min_context_slot=None,
        response=None,
        error=None,
    ):
        if wallet_pubkey is None:
            wallet_pubkey = (
                self.wallet_pubkey
            )

        if response is None:
            response = {
                "context": {
                    "slot": 321,
                },
                "value": 5_000_000,
            }

        client = FakeRpcClient(
            response=response,
            error=error,
        )

        factory = MagicMock(
            return_value=client
        )

        with patch(
            f"{MODULE}.HeliusRpcClient",
            factory,
        ):
            result = await (
                resolve_live_wallet_balance(
                    wallet_pubkey=(
                        wallet_pubkey
                    ),
                    min_context_slot=(
                        min_context_slot
                    ),
                )
            )

        return (
            result,
            client,
            factory,
        )

    async def test_invalid_wallet_does_not_call_rpc(
        self,
    ):
        (
            result,
            client,
            factory,
        ) = await self.resolve(
            wallet_pubkey=(
                "not-a-solana-pubkey"
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "INVALID_WALLET_PUBKEY",
            result.reasons,
        )

        self.assertEqual(
            client.calls,
            [],
        )

        factory.assert_not_called()

    async def test_default_wallet_does_not_call_rpc(
        self,
    ):
        (
            result,
            client,
            factory,
        ) = await self.resolve(
            wallet_pubkey=str(
                Pubkey.default()
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "INVALID_WALLET_PUBKEY",
            result.reasons,
        )

        self.assertEqual(
            client.calls,
            [],
        )

        factory.assert_not_called()

    async def test_invalid_min_context_slot_is_rejected(
        self,
    ):
        for invalid in (
            True,
            -1,
            U64_MAX + 1,
        ):
            with self.subTest(
                invalid=invalid
            ):
                (
                    result,
                    client,
                    factory,
                ) = await self.resolve(
                    min_context_slot=(
                        invalid
                    ),
                )

                self.assertEqual(
                    result.status,
                    UNKNOWN,
                )

                self.assertIn(
                    "INVALID_MIN_CONTEXT_SLOT",
                    result.reasons,
                )

                self.assertEqual(
                    client.calls,
                    [],
                )

                factory.assert_not_called()

    async def test_successful_positive_balance(
        self,
    ):
        (
            result,
            client,
            factory,
        ) = await self.resolve()

        self.assertEqual(
            result.resolver_version,
            LIVE_WALLET_BALANCE_VERSION,
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            result.wallet_pubkey,
            self.wallet_pubkey,
        )

        self.assertEqual(
            result.balance_lamports,
            5_000_000,
        )

        self.assertEqual(
            result.rpc_slot,
            321,
        )

        self.assertEqual(
            result.commitment,
            COMMITMENT,
        )

        self.assertIsNone(
            result.min_context_slot
        )

        factory.assert_called_once()

        self.assertEqual(
            client.calls,
            [
                (
                    "getBalance",
                    [
                        self.wallet_pubkey,
                        {
                            "commitment":
                                COMMITMENT,
                        },
                    ],
                ),
            ],
        )

    async def test_zero_balance_is_valid(
        self,
    ):
        result, _, _ = (
            await self.resolve(
                response={
                    "context": {
                        "slot": 321,
                    },
                    "value": 0,
                },
            )
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            result.balance_lamports,
            0,
        )

    async def test_min_context_slot_is_forwarded(
        self,
    ):
        (
            result,
            client,
            _,
        ) = await self.resolve(
            min_context_slot=300,
            response={
                "context": {
                    "slot": 325,
                },
                "value": 7_000_000,
            },
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            result.min_context_slot,
            300,
        )

        self.assertEqual(
            result.rpc_slot,
            325,
        )

        self.assertEqual(
            client.calls,
            [
                (
                    "getBalance",
                    [
                        self.wallet_pubkey,
                        {
                            "commitment":
                                COMMITMENT,
                            "minContextSlot":
                                300,
                        },
                    ],
                ),
            ],
        )

    async def test_rpc_failure_is_unknown(
        self,
    ):
        result, _, _ = (
            await self.resolve(
                error=RuntimeError(
                    "rpc failed"
                ),
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "BALANCE_RPC_FAILED",
            result.reasons,
        )

        self.assertIsNone(
            result.balance_lamports
        )

    async def test_non_dict_response_is_unknown(
        self,
    ):
        result, _, _ = (
            await self.resolve(
                response=[],
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "BALANCE_RESPONSE_INVALID",
            result.reasons,
        )

    async def test_missing_context_is_unknown(
        self,
    ):
        result, _, _ = (
            await self.resolve(
                response={
                    "value": 1,
                },
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "BALANCE_RESPONSE_INVALID",
            result.reasons,
        )

    async def test_invalid_context_slot_is_unknown(
        self,
    ):
        for invalid in (
            True,
            -1,
            U64_MAX + 1,
        ):
            with self.subTest(
                invalid=invalid
            ):
                result, _, _ = (
                    await self.resolve(
                        response={
                            "context": {
                                "slot":
                                    invalid,
                            },
                            "value": 1,
                        },
                    )
                )

                self.assertEqual(
                    result.status,
                    UNKNOWN,
                )

                self.assertIn(
                    "BALANCE_RESPONSE_INVALID",
                    result.reasons,
                )

    async def test_context_below_minimum_is_unknown(
        self,
    ):
        result, _, _ = (
            await self.resolve(
                min_context_slot=300,
                response={
                    "context": {
                        "slot": 299,
                    },
                    "value": 1,
                },
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "BALANCE_CONTEXT_SLOT_BELOW_MINIMUM",
            result.reasons,
        )

    async def test_invalid_balance_values_are_unknown(
        self,
    ):
        for invalid in (
            True,
            -1,
            U64_MAX + 1,
        ):
            with self.subTest(
                invalid=invalid
            ):
                result, _, _ = (
                    await self.resolve(
                        response={
                            "context": {
                                "slot": 321,
                            },
                            "value":
                                invalid,
                        },
                    )
                )

                self.assertEqual(
                    result.status,
                    UNKNOWN,
                )

                self.assertIn(
                    "BALANCE_RESPONSE_INVALID",
                    result.reasons,
                )


if __name__ == "__main__":
    unittest.main()
