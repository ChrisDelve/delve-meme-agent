import base64
from typing import Any

import aiohttp

from solders.signature import Signature

from src.safety.token_safety_resolver import (
    COMMITMENT,
    RPC_URL,
    SSL_CONTEXT,
)


SOLANA_SINGLE_ATTEMPT_RPC_VERSION = (
    "solana-single-attempt-rpc-v2"
)

SOLANA_MAINNET_RPC_PREFIX = (
    "https://mainnet.helius-rpc.com/?api-key="
)


def _rpc_url_is_canonical_mainnet(
    value: object,
) -> bool:
    """
    Deterministic production network identity check.

    This performs no RPC call and therefore cannot consume relay
    attempts or block recovery reads.

    The production writer is intentionally bound to Helius Solana
    mainnet. A future configurable provider/cluster must introduce a
    new explicit network-identity contract rather than weakening this
    check.
    """
    return (
        isinstance(
            value,
            str,
        )
        and value.startswith(
            SOLANA_MAINNET_RPC_PREFIX
        )
        and len(
            value
        )
        > len(
            SOLANA_MAINNET_RPC_PREFIX
        )
    )


class SingleAttemptRpcWriteError(
    RuntimeError
):
    def __init__(
        self,
        reason: str,
        detail: str | None = None,
    ) -> None:
        self.reason = reason
        self.detail = detail

        message = reason

        if detail:
            message = (
                f"{reason}: {detail}"
            )

        super().__init__(
            message
        )


class SingleAttemptSolanaRpcClient:
    """
    Narrow Solana write-RPC capability.

    This client deliberately performs exactly one
    HTTP POST per send_transaction_once() call.

    It contains:
    - canonical Helius Solana mainnet endpoint enforcement
    - no HTTP retry loop
    - no 429 retry
    - no 5xx retry
    - no transport retry
    - no transaction rebuild
    - no signing capability

    Any ambiguous network outcome must be handled
    by transaction-status reconciliation before a
    caller considers another relay.
    """

    def __init__(self) -> None:
        self.session: (
            aiohttp.ClientSession | None
        ) = None

    async def __aenter__(
        self,
    ) -> "SingleAttemptSolanaRpcClient":
        connector = aiohttp.TCPConnector(
            ssl=SSL_CONTEXT
        )

        self.session = (
            aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(
                    total=10.0
                ),
            )
        )

        return self

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        if self.session is not None:
            await self.session.close()

    async def send_transaction_once(
        self,
        *,
        signed_transaction_bytes: bytes,
        min_context_slot: int,
    ) -> str:
        if self.session is None:
            raise SingleAttemptRpcWriteError(
                "RPC_SESSION_NOT_OPEN"
            )

        if not _rpc_url_is_canonical_mainnet(
            RPC_URL
        ):
            raise SingleAttemptRpcWriteError(
                "RPC_ENDPOINT_NOT_CANONICAL_MAINNET"
            )

        if (
            not isinstance(
                signed_transaction_bytes,
                bytes,
            )
            or not signed_transaction_bytes
        ):
            raise SingleAttemptRpcWriteError(
                "INVALID_SIGNED_TRANSACTION_BYTES"
            )

        if (
            not isinstance(
                min_context_slot,
                int,
            )
            or isinstance(
                min_context_slot,
                bool,
            )
            or min_context_slot < 0
        ):
            raise SingleAttemptRpcWriteError(
                "INVALID_MIN_CONTEXT_SLOT"
            )

        encoded_transaction = (
            base64.b64encode(
                signed_transaction_bytes
            ).decode(
                "ascii"
            )
        )

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                encoded_transaction,
                {
                    "encoding": "base64",
                    "skipPreflight": False,
                    "preflightCommitment": (
                        COMMITMENT
                    ),
                    "maxRetries": 0,
                    "minContextSlot": (
                        min_context_slot
                    ),
                },
            ],
        }

        #
        # Intentionally ONE post.
        #
        # Do not wrap this in MAX_RPC_ATTEMPTS,
        # backoff, or any other implicit retry
        # mechanism.
        #
        try:
            async with self.session.post(
                RPC_URL,
                json=payload,
            ) as response:

                if response.status != 200:
                    try:
                        body = (
                            await response.text()
                        )
                    except Exception:
                        body = ""

                    raise SingleAttemptRpcWriteError(
                        "SEND_TRANSACTION_HTTP_ERROR",
                        (
                            f"status="
                            f"{response.status}; "
                            f"body={body[:200]}"
                        ),
                    )

                try:
                    response_json: Any = (
                        await response.json()
                    )
                except Exception as error:
                    raise (
                        SingleAttemptRpcWriteError(
                            "SEND_TRANSACTION_JSON_INVALID",
                            type(error).__name__,
                        )
                    ) from error

        except SingleAttemptRpcWriteError:
            raise

        except (
            aiohttp.ClientError,
            TimeoutError,
        ) as error:
            raise SingleAttemptRpcWriteError(
                "SEND_TRANSACTION_NETWORK_AMBIGUOUS",
                type(error).__name__,
            ) from error

        if not isinstance(
            response_json,
            dict,
        ):
            raise SingleAttemptRpcWriteError(
                "SEND_TRANSACTION_RESPONSE_INVALID"
            )

        if (
            response_json.get("jsonrpc")
            != "2.0"
            or response_json.get("id")
            != 1
        ):
            raise SingleAttemptRpcWriteError(
                "SEND_TRANSACTION_RESPONSE_IDENTITY_INVALID"
            )

        if "error" in response_json:
            rpc_error = response_json[
                "error"
            ]

            detail = (
                str(rpc_error)[:300]
            )

            raise SingleAttemptRpcWriteError(
                "SEND_TRANSACTION_RPC_ERROR",
                detail,
            )

        result = response_json.get(
            "result"
        )

        if (
            not isinstance(
                result,
                str,
            )
            or not result.strip()
        ):
            raise SingleAttemptRpcWriteError(
                "SEND_TRANSACTION_RESULT_INVALID"
            )

        try:
            parsed_signature = (
                Signature.from_string(
                    result
                )
            )
        except Exception as error:
            raise (
                SingleAttemptRpcWriteError(
                    "SEND_TRANSACTION_RESULT_INVALID"
                )
            ) from error

        if (
            parsed_signature
            == Signature.default()
        ):
            raise SingleAttemptRpcWriteError(
                "SEND_TRANSACTION_RESULT_INVALID"
            )

        return str(
            parsed_signature
        )
