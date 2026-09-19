import unittest

from src.data.transaction_decoder import (
    MAX_SUPPORTED_TRANSACTION_VERSION,
    build_get_transaction_payload,
)


class TransactionDecoderRpcContractTests(
    unittest.TestCase
):
    def test_supported_transaction_version_is_one(
        self,
    ):
        self.assertEqual(
            MAX_SUPPORTED_TRANSACTION_VERSION,
            1,
        )

    def test_get_transaction_payload_supports_v1(
        self,
    ):
        signature = "test-signature"

        payload = build_get_transaction_payload(
            signature
        )

        self.assertEqual(
            payload,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    signature,
                    {
                        "encoding": "jsonParsed",
                        "commitment": "confirmed",
                        "maxSupportedTransactionVersion": 1,
                    },
                ],
            },
        )


if __name__ == "__main__":
    unittest.main()
