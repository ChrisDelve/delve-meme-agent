from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature

from src.execution.pump_buy_v2_signing import (
    MessageSigner as BuyMessageSigner,
)
from src.execution.pump_sell_v2_signing import (
    MessageSigner as SellMessageSigner,
)

from src.execution.solana_message_signer import (
    DEFAULT_SOLANA_KEYPAIR_ENV,
    SOLANA_MESSAGE_SIGNER_VERSION,
    SoldersMessageSigner,
    SolanaSignerConfigurationError,
    load_solana_message_signer_from_env,
)


def exercise_buy_signer_shape(
    signer: BuyMessageSigner,
) -> tuple[Pubkey, Signature]:
    return (
        signer.pubkey(),
        signer.sign_message(
            b"buy-shape-probe"
        ),
    )


def exercise_sell_signer_shape(
    signer: SellMessageSigner,
) -> tuple[Pubkey, Signature]:
    return (
        signer.pubkey(),
        signer.sign_message(
            b"sell-shape-probe"
        ),
    )


class SolanaMessageSignerTests(
    unittest.TestCase
):
    def test_public_version_is_locked(
        self,
    ):
        self.assertEqual(
            SOLANA_MESSAGE_SIGNER_VERSION,
            "solana-message-signer-v1",
        )

    def test_direct_keypair_adapter_preserves_pubkey(
        self,
    ):
        keypair = Keypair()

        signer = SoldersMessageSigner(
            keypair
        )

        self.assertIsInstance(
            signer.pubkey(),
            Pubkey,
        )
        self.assertEqual(
            signer.pubkey(),
            keypair.pubkey(),
        )

    def test_sign_message_matches_underlying_keypair(
        self,
    ):
        keypair = Keypair()

        signer = SoldersMessageSigner(
            keypair
        )

        message = (
            b"delve-production-signer-test"
        )

        expected = keypair.sign_message(
            message
        )

        actual = signer.sign_message(
            message
        )

        self.assertIsInstance(
            actual,
            Signature,
        )
        self.assertEqual(
            actual,
            expected,
        )

    def test_adapter_satisfies_buy_and_sell_call_shapes(
        self,
    ):
        keypair = Keypair()

        signer = SoldersMessageSigner(
            keypair
        )

        buy_pubkey, buy_signature = (
            exercise_buy_signer_shape(
                signer
            )
        )

        sell_pubkey, sell_signature = (
            exercise_sell_signer_shape(
                signer
            )
        )

        self.assertEqual(
            buy_pubkey,
            keypair.pubkey(),
        )
        self.assertEqual(
            sell_pubkey,
            keypair.pubkey(),
        )

        self.assertIsInstance(
            buy_signature,
            Signature,
        )
        self.assertIsInstance(
            sell_signature,
            Signature,
        )

    def test_sign_message_requires_exact_bytes(
        self,
    ):
        signer = SoldersMessageSigner(
            Keypair()
        )

        for value in (
            bytearray(b"abc"),
            memoryview(b"abc"),
            "abc",
            None,
        ):
            with self.subTest(
                type=type(value).__name__
            ):
                with self.assertRaises(
                    TypeError
                ):
                    signer.sign_message(
                        value
                    )

    def test_constructor_rejects_non_keypair(
        self,
    ):
        with self.assertRaises(
            TypeError
        ):
            SoldersMessageSigner(
                object()
            )

    def test_repr_exposes_pubkey_but_not_secret(
        self,
    ):
        keypair = Keypair()

        encoded = str(
            keypair
        )

        signer = SoldersMessageSigner(
            keypair
        )

        rendered = repr(
            signer
        )

        self.assertIn(
            str(
                keypair.pubkey()
            ),
            rendered,
        )

        self.assertNotIn(
            encoded,
            rendered,
        )

    def test_canonical_environment_variable_loads_signer(
        self,
    ):
        keypair = Keypair()

        encoded = str(
            keypair
        )

        with patch.dict(
            os.environ,
            {
                DEFAULT_SOLANA_KEYPAIR_ENV:
                encoded,
            },
            clear=True,
        ):
            signer = (
                load_solana_message_signer_from_env()
            )

        self.assertEqual(
            signer.pubkey(),
            keypair.pubkey(),
        )

    def test_missing_environment_variable_fails_closed(
        self,
    ):
        with patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            with self.assertRaises(
                SolanaSignerConfigurationError
            ) as context:
                load_solana_message_signer_from_env()

        message = str(
            context.exception
        )

        self.assertEqual(
            message,
            (
                "SOLANA_KEYPAIR_ENV_MISSING:"
                f"{DEFAULT_SOLANA_KEYPAIR_ENV}"
            ),
        )

    def test_invalid_environment_secret_is_sanitized(
        self,
    ):
        bad_secret = (
            "this-is-not-a-real-private-key"
        )

        with patch.dict(
            os.environ,
            {
                DEFAULT_SOLANA_KEYPAIR_ENV:
                bad_secret,
            },
            clear=True,
        ):
            with self.assertRaises(
                SolanaSignerConfigurationError
            ) as context:
                load_solana_message_signer_from_env()

        message = str(
            context.exception
        )

        self.assertEqual(
            message,
            (
                "SOLANA_KEYPAIR_ENV_INVALID:"
                f"{DEFAULT_SOLANA_KEYPAIR_ENV}"
            ),
        )

        self.assertNotIn(
            bad_secret,
            message,
        )

    def test_environment_secret_whitespace_is_rejected_not_trimmed(
        self,
    ):
        encoded = str(
            Keypair()
        )

        with patch.dict(
            os.environ,
            {
                DEFAULT_SOLANA_KEYPAIR_ENV:
                f" {encoded}",
            },
            clear=True,
        ):
            with self.assertRaises(
                SolanaSignerConfigurationError
            ):
                load_solana_message_signer_from_env()

    def test_adapter_has_no_instance_dictionary(
        self,
    ):
        signer = SoldersMessageSigner(
            Keypair()
        )

        self.assertFalse(
            hasattr(
                signer,
                "__dict__",
            )
        )

    def test_unrelated_environment_secret_is_never_used(
        self,
    ):
        with patch.dict(
            os.environ,
            {
                "SOME_OTHER_SOLANA_KEY":
                str(
                    Keypair()
                ),
            },
            clear=True,
        ):
            with self.assertRaises(
                SolanaSignerConfigurationError
            ) as context:
                load_solana_message_signer_from_env()

        self.assertIn(
            "SOLANA_KEYPAIR_ENV_MISSING",
            str(context.exception),
        )




if __name__ == "__main__":
    unittest.main()
