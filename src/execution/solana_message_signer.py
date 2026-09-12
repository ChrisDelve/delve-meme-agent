from __future__ import annotations

import os

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature


SOLANA_MESSAGE_SIGNER_VERSION = (
    "solana-message-signer-v1"
)

DEFAULT_SOLANA_KEYPAIR_ENV = (
    "DELVE_SOLANA_KEYPAIR_BASE58"
)


class SolanaSignerConfigurationError(
    RuntimeError
):
    """
    Fail-closed production signer configuration error.

    Messages from this exception must never include private
    key material.
    """


class SoldersMessageSigner:
    """
    Minimal in-process Solana message signer.

    Authority:
      - retain one already-loaded Solders Keypair;
      - expose its public key;
      - sign caller-supplied message bytes.

    This object deliberately does NOT:
      - know about Pump.fun;
      - authorize orders;
      - construct transactions;
      - access SQLite;
      - access RPC;
      - submit transactions;
      - retry;
      - reconcile fills;
      - expose secret bytes or secret strings.
    """

    __slots__ = (
        "__keypair",
    )

    def __init__(
        self,
        keypair: Keypair,
    ) -> None:
        if not isinstance(
            keypair,
            Keypair,
        ):
            raise TypeError(
                "keypair must be a solders Keypair"
            )

        self.__keypair = keypair

    def pubkey(
        self,
    ) -> Pubkey:
        return self.__keypair.pubkey()

    def sign_message(
        self,
        message: bytes,
    ) -> Signature:
        if not isinstance(
            message,
            bytes,
        ):
            raise TypeError(
                "message must be bytes"
            )

        return self.__keypair.sign_message(
            message
        )

    def __repr__(
        self,
    ) -> str:
        return (
            "SoldersMessageSigner("
            f"pubkey='{self.pubkey()}'"
            ")"
        )


def load_solana_message_signer_from_env(
) -> SoldersMessageSigner:
    """
    Load exactly one Solana Keypair from the canonical
    production process environment variable.

    This function intentionally does not call load_dotenv().
    Bootstrap configuration owns environment population.
    """

    encoded_keypair = os.environ.get(
        DEFAULT_SOLANA_KEYPAIR_ENV
    )

    if encoded_keypair is None:
        raise SolanaSignerConfigurationError(
            "SOLANA_KEYPAIR_ENV_MISSING:"
            f"{DEFAULT_SOLANA_KEYPAIR_ENV}"
        )

    if (
        not encoded_keypair
        or encoded_keypair
        != encoded_keypair.strip()
    ):
        raise SolanaSignerConfigurationError(
            "SOLANA_KEYPAIR_ENV_INVALID:"
            f"{DEFAULT_SOLANA_KEYPAIR_ENV}"
        )

    try:
        keypair = (
            Keypair.from_base58_string(
                encoded_keypair
            )
        )
    except Exception:
        # Never propagate parser details because an
        # implementation could echo offending input.
        raise SolanaSignerConfigurationError(
            "SOLANA_KEYPAIR_ENV_INVALID:"
            f"{DEFAULT_SOLANA_KEYPAIR_ENV}"
        ) from None

    return SoldersMessageSigner(
        keypair
    )
