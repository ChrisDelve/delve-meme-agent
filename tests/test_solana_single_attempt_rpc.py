import base64
import unittest
from unittest.mock import patch

import aiohttp

from solders.keypair import Keypair

from src.execution.solana_single_attempt_rpc import (
    SOLANA_MAINNET_RPC_PREFIX,
    SOLANA_SINGLE_ATTEMPT_RPC_VERSION,
    SingleAttemptRpcWriteError,
    SingleAttemptSolanaRpcClient,
)
from src.safety.token_safety_resolver import (
    COMMITMENT,
    RPC_URL,
)


class FakeResponse:
    def __init__(
        self,
        *,
        status=200,
        json_body=None,
        text_body="",
        json_error=None,
    ):
        self.status = status
        self.json_body = json_body
        self.text_body = text_body
        self.json_error = json_error

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

    async def json(
        self,
    ):
        if self.json_error is not None:
            raise self.json_error

        return self.json_body

    async def text(
        self,
    ):
        return self.text_body


class FakeSession:
    def __init__(
        self,
        *,
        responses=None,
        post_error=None,
    ):
        self.responses = list(
            responses or []
        )
        self.post_error = post_error

        self.calls = []

    def post(
        self,
        url,
        *,
        json,
    ):
        self.calls.append(
            (
                url,
                json,
            )
        )

        if self.post_error is not None:
            raise self.post_error

        if not self.responses:
            raise AssertionError(
                "Unexpected extra HTTP POST"
            )

        return self.responses.pop(0)


class SingleAttemptSolanaRpcTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.transaction_bytes = (
            b"exact-signed-transaction"
        )

        self.signature = str(
            Keypair().sign_message(
                b"single-attempt-rpc-test"
            )
        )

        self.min_context_slot = 200

    def client_with_session(
        self,
        session,
    ):
        client = (
            SingleAttemptSolanaRpcClient()
        )

        client.session = session

        return client

    async def test_send_transaction_request_contract(
        self,
    ):
        session = FakeSession(
            responses=[
                FakeResponse(
                    json_body={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": self.signature,
                    }
                )
            ]
        )

        client = self.client_with_session(
            session
        )

        result = await (
            client.send_transaction_once(
                signed_transaction_bytes=(
                    self.transaction_bytes
                ),
                min_context_slot=(
                    self.min_context_slot
                ),
            )
        )

        self.assertEqual(
            SOLANA_SINGLE_ATTEMPT_RPC_VERSION,
            "solana-single-attempt-rpc-v2",
        )

        self.assertEqual(
            result,
            self.signature,
        )

        self.assertEqual(
            len(session.calls),
            1,
        )

        url, payload = session.calls[0]

        self.assertEqual(
            url,
            RPC_URL,
        )

        self.assertEqual(
            payload["method"],
            "sendTransaction",
        )

        self.assertEqual(
            payload["params"][0],
            base64.b64encode(
                self.transaction_bytes
            ).decode(
                "ascii"
            ),
        )

        self.assertEqual(
            payload["params"][1],
            {
                "encoding": "base64",
                "skipPreflight": False,
                "preflightCommitment": (
                    COMMITMENT
                ),
                "maxRetries": 0,
                "minContextSlot": 200,
            },
        )

    def test_canonical_mainnet_prefix_is_locked(
        self,
    ):
        self.assertEqual(
            SOLANA_MAINNET_RPC_PREFIX,
            (
                "https://mainnet.helius-rpc.com/"
                "?api-key="
            ),
        )

        self.assertTrue(
            RPC_URL.startswith(
                SOLANA_MAINNET_RPC_PREFIX
            )
        )

        self.assertGreater(
            len(
                RPC_URL
            ),
            len(
                SOLANA_MAINNET_RPC_PREFIX
            ),
        )

    async def test_non_mainnet_endpoint_fails_before_post(
        self,
    ):
        session = FakeSession(
            responses=[
                FakeResponse(
                    json_body={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": self.signature,
                    }
                )
            ]
        )

        client = self.client_with_session(
            session
        )

        with patch(
            (
                "src.execution."
                "solana_single_attempt_rpc."
                "RPC_URL"
            ),
            (
                "https://devnet.helius-rpc.com/"
                "?api-key=test"
            ),
        ):
            with self.assertRaises(
                SingleAttemptRpcWriteError
            ) as raised:
                await client.send_transaction_once(
                    signed_transaction_bytes=(
                        self.transaction_bytes
                    ),
                    min_context_slot=(
                        self.min_context_slot
                    ),
                )

        self.assertEqual(
            raised.exception.reason,
            "RPC_ENDPOINT_NOT_CANONICAL_MAINNET",
        )

        self.assertEqual(
            session.calls,
            [],
        )

    async def test_empty_mainnet_api_key_fails_before_post(
        self,
    ):
        session = FakeSession(
            responses=[
                FakeResponse(
                    json_body={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": self.signature,
                    }
                )
            ]
        )

        client = self.client_with_session(
            session
        )

        with patch(
            (
                "src.execution."
                "solana_single_attempt_rpc."
                "RPC_URL"
            ),
            SOLANA_MAINNET_RPC_PREFIX,
        ):
            with self.assertRaises(
                SingleAttemptRpcWriteError
            ) as raised:
                await client.send_transaction_once(
                    signed_transaction_bytes=(
                        self.transaction_bytes
                    ),
                    min_context_slot=(
                        self.min_context_slot
                    ),
                )

        self.assertEqual(
            raised.exception.reason,
            "RPC_ENDPOINT_NOT_CANONICAL_MAINNET",
        )

        self.assertEqual(
            session.calls,
            [],
        )

    async def test_429_is_not_retried(
        self,
    ):
        session = FakeSession(
            responses=[
                FakeResponse(
                    status=429,
                    text_body="rate limited",
                ),
                FakeResponse(
                    json_body={
                        "result": self.signature,
                    }
                ),
            ]
        )

        client = self.client_with_session(
            session
        )

        with self.assertRaises(
            SingleAttemptRpcWriteError
        ) as raised:
            await client.send_transaction_once(
                signed_transaction_bytes=(
                    self.transaction_bytes
                ),
                min_context_slot=200,
            )

        self.assertEqual(
            raised.exception.reason,
            "SEND_TRANSACTION_HTTP_ERROR",
        )

        self.assertEqual(
            len(session.calls),
            1,
        )

    async def test_500_is_not_retried(
        self,
    ):
        session = FakeSession(
            responses=[
                FakeResponse(
                    status=500,
                    text_body="server error",
                ),
                FakeResponse(
                    json_body={
                        "result": self.signature,
                    }
                ),
            ]
        )

        client = self.client_with_session(
            session
        )

        with self.assertRaises(
            SingleAttemptRpcWriteError
        ):
            await client.send_transaction_once(
                signed_transaction_bytes=(
                    self.transaction_bytes
                ),
                min_context_slot=200,
            )

        self.assertEqual(
            len(session.calls),
            1,
        )

    async def test_network_error_is_not_retried(
        self,
    ):
        session = FakeSession(
            post_error=(
                aiohttp.ClientConnectionError(
                    "network lost"
                )
            )
        )

        client = self.client_with_session(
            session
        )

        with self.assertRaises(
            SingleAttemptRpcWriteError
        ) as raised:
            await client.send_transaction_once(
                signed_transaction_bytes=(
                    self.transaction_bytes
                ),
                min_context_slot=200,
            )

        self.assertEqual(
            raised.exception.reason,
            "SEND_TRANSACTION_NETWORK_AMBIGUOUS",
        )

        self.assertEqual(
            len(session.calls),
            1,
        )

    async def test_rpc_error_is_not_retried(
        self,
    ):
        session = FakeSession(
            responses=[
                FakeResponse(
                    json_body={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {
                            "code": -32002,
                            "message": (
                                "Transaction "
                                "simulation failed"
                            ),
                        },
                    }
                ),
                FakeResponse(
                    json_body={
                        "result": self.signature,
                    }
                ),
            ]
        )

        client = self.client_with_session(
            session
        )

        with self.assertRaises(
            SingleAttemptRpcWriteError
        ) as raised:
            await client.send_transaction_once(
                signed_transaction_bytes=(
                    self.transaction_bytes
                ),
                min_context_slot=200,
            )

        self.assertEqual(
            raised.exception.reason,
            "SEND_TRANSACTION_RPC_ERROR",
        )

        self.assertEqual(
            len(session.calls),
            1,
        )

    async def test_wrong_response_id_is_not_accepted(
        self,
    ):
        session = FakeSession(
            responses=[
                FakeResponse(
                    json_body={
                        "jsonrpc": "2.0",
                        "id": 999,
                        "result": self.signature,
                    }
                )
            ]
        )

        client = self.client_with_session(
            session
        )

        with self.assertRaises(
            SingleAttemptRpcWriteError
        ) as raised:
            await client.send_transaction_once(
                signed_transaction_bytes=(
                    self.transaction_bytes
                ),
                min_context_slot=200,
            )

        self.assertEqual(
            raised.exception.reason,
            "SEND_TRANSACTION_RESPONSE_IDENTITY_INVALID",
        )

        self.assertEqual(
            len(session.calls),
            1,
        )

    async def test_wrong_jsonrpc_version_is_not_accepted(
        self,
    ):
        session = FakeSession(
            responses=[
                FakeResponse(
                    json_body={
                        "jsonrpc": "1.0",
                        "id": 1,
                        "result": self.signature,
                    }
                )
            ]
        )

        client = self.client_with_session(
            session
        )

        with self.assertRaises(
            SingleAttemptRpcWriteError
        ) as raised:
            await client.send_transaction_once(
                signed_transaction_bytes=(
                    self.transaction_bytes
                ),
                min_context_slot=200,
            )

        self.assertEqual(
            raised.exception.reason,
            "SEND_TRANSACTION_RESPONSE_IDENTITY_INVALID",
        )

        self.assertEqual(
            len(session.calls),
            1,
        )

    async def test_invalid_result_is_not_retried(
        self,
    ):
        session = FakeSession(
            responses=[
                FakeResponse(
                    json_body={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": 123,
                    }
                ),
                FakeResponse(
                    json_body={
                        "result": self.signature,
                    }
                ),
            ]
        )

        client = self.client_with_session(
            session
        )

        with self.assertRaises(
            SingleAttemptRpcWriteError
        ) as raised:
            await client.send_transaction_once(
                signed_transaction_bytes=(
                    self.transaction_bytes
                ),
                min_context_slot=200,
            )

        self.assertEqual(
            raised.exception.reason,
            "SEND_TRANSACTION_RESULT_INVALID",
        )

        self.assertEqual(
            len(session.calls),
            1,
        )

    async def test_invalid_inputs_stop_before_post(
        self,
    ):
        cases = (
            (
                b"",
                200,
                "INVALID_SIGNED_TRANSACTION_BYTES",
            ),
            (
                self.transaction_bytes,
                True,
                "INVALID_MIN_CONTEXT_SLOT",
            ),
            (
                self.transaction_bytes,
                -1,
                "INVALID_MIN_CONTEXT_SLOT",
            ),
        )

        for (
            transaction_bytes,
            slot,
            expected_reason,
        ) in cases:
            with self.subTest(
                expected_reason=expected_reason,
                slot=slot,
            ):
                session = FakeSession()

                client = (
                    self.client_with_session(
                        session
                    )
                )

                with self.assertRaises(
                    SingleAttemptRpcWriteError
                ) as raised:
                    await (
                        client
                        .send_transaction_once(
                            signed_transaction_bytes=(
                                transaction_bytes
                            ),
                            min_context_slot=slot,
                        )
                    )

                self.assertEqual(
                    raised.exception.reason,
                    expected_reason,
                )

                self.assertEqual(
                    session.calls,
                    [],
                )


if __name__ == "__main__":
    unittest.main()
