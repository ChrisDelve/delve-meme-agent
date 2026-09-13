from __future__ import annotations

import os

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature


SOLANA_MESSAGE_SIGNER_VERSION = (
    "solana-message-signer-v1"
)

LAZY_SOLANA_MESSAGE_SIGNER_VERSION = (
    "lazy-solana-message-signer-v1"
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


class LazyEnvironmentMessageSigner:
    """
    Lazily load the canonical environment-backed Solana signer
    only when signing authority is actually touched.

    Construction and repr() perform no environment/key access.

    A successful load is cached for the lifetime of this object.
    A failed load is also cached and never retried by the same
    object. Operators must construct a new proxy after repairing
    signer configuration.

    Unexpected loader failures are converted to a generic,
    sanitized SolanaSignerConfigurationError so arbitrary
    implementation details cannot leak secret material.
    """

    __slots__ = (
        "__signer",
        "__load_attempted",
        "__load_error_message",
    )

    def __init__(
        self,
    ) -> None:
        self.__signer: (
            SoldersMessageSigner | None
        ) = None

        self.__load_attempted = False

        self.__load_error_message: (
            str | None
        ) = None

    def __resolve_signer(
        self,
    ) -> SoldersMessageSigner:
        if self.__signer is not None:
            return self.__signer

        if self.__load_attempted:
            raise SolanaSignerConfigurationError(
                self.__load_error_message
                or "SOLANA_SIGNER_LOAD_FAILED"
            )

        self.__load_attempted = True

        try:
            signer = (
                load_solana_message_signer_from_env()
            )

        except SolanaSignerConfigurationError as error:
            message = str(
                error
            )

            self.__load_error_message = (
                message
                or "SOLANA_SIGNER_LOAD_FAILED"
            )

            raise SolanaSignerConfigurationError(
                self.__load_error_message
            ) from None

        except Exception:
            self.__load_error_message = (
                "SOLANA_SIGNER_LOAD_FAILED"
            )

            raise SolanaSignerConfigurationError(
                self.__load_error_message
            ) from None

        if not isinstance(
            signer,
            SoldersMessageSigner,
        ):
            self.__load_error_message = (
                "SOLANA_SIGNER_LOAD_INVALID"
            )

            raise SolanaSignerConfigurationError(
                self.__load_error_message
            )

        self.__signer = signer

        return signer

    def pubkey(
        self,
    ) -> Pubkey:
        return (
            self.__resolve_signer()
            .pubkey()
        )

    def sign_message(
        self,
        message: bytes,
    ) -> Signature:
        #
        # Reject malformed caller input before touching
        # environment-backed signing authority.
        #
        if not isinstance(
            message,
            bytes,
        ):
            raise TypeError(
                "message must be bytes"
            )

        return (
            self.__resolve_signer()
            .sign_message(
                message
            )
        )

    def __repr__(
        self,
    ) -> str:
        if self.__signer is not None:
            state = "loaded"
        elif self.__load_attempted:
            state = "failed"
        else:
            state = "unloaded"

        return (
            "LazyEnvironmentMessageSigner("
            f"state='{state}'"
            ")"
        )
