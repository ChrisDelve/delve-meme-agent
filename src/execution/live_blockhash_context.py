from __future__ import annotations

import time
from dataclasses import dataclass

from solders.hash import Hash

from src.safety.token_safety_resolver import (
    COMMITMENT,
    HeliusRpcClient,
)


LIVE_BLOCKHASH_CONTEXT_VERSION = (
    "live-blockhash-context-v1"
)


class LiveBlockhashContextError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class LiveBlockhashContext:
    resolver_version: str

    blockhash: str
    last_valid_block_height: int

    rpc_slot: int
    min_context_slot: int

    commitment: str
    fetched_at: float


def _require_nonnegative_int(
    value: object,
    *,
    label: str,
) -> int:

    if (
        not isinstance(
            value,
            int,
        )
        or isinstance(
            value,
            bool,
        )
        or value < 0
    ):
        raise LiveBlockhashContextError(
            f"{label} is invalid."
        )

    return value


async def resolve_live_blockhash_context(
    *,
    min_context_slot: int,
) -> LiveBlockhashContext:
    """
    Resolve one authoritative Solana blockhash
    together with the exact last-valid block height
    returned in the same RPC response.

    No signing or transaction submission occurs.
    """

    min_context_slot = (
        _require_nonnegative_int(
            min_context_slot,
            label="min_context_slot",
        )
    )

    try:
        async with HeliusRpcClient() as rpc:
            result = await rpc.call(
                "getLatestBlockhash",
                [
                    {
                        "commitment": (
                            COMMITMENT
                        ),
                        "minContextSlot": (
                            min_context_slot
                        ),
                    }
                ],
            )

    except LiveBlockhashContextError:
        raise

    except Exception as error:
        raise LiveBlockhashContextError(
            "getLatestBlockhash RPC failed: "
            f"{type(error).__name__}"
        ) from error

    if not isinstance(
        result,
        dict,
    ):
        raise LiveBlockhashContextError(
            "Malformed getLatestBlockhash result."
        )

    context = result.get(
        "context"
    )

    if not isinstance(
        context,
        dict,
    ):
        raise LiveBlockhashContextError(
            "Missing blockhash RPC context."
        )

    rpc_slot = _require_nonnegative_int(
        context.get(
            "slot"
        ),
        label="rpc_slot",
    )

    if rpc_slot < min_context_slot:
        raise LiveBlockhashContextError(
            "Blockhash RPC context predates "
            "the required minimum slot."
        )

    value = result.get(
        "value"
    )

    if not isinstance(
        value,
        dict,
    ):
        raise LiveBlockhashContextError(
            "Missing blockhash RPC value."
        )

    raw_blockhash = value.get(
        "blockhash"
    )

    if (
        not isinstance(
            raw_blockhash,
            str,
        )
        or not raw_blockhash.strip()
    ):
        raise LiveBlockhashContextError(
            "Blockhash is invalid."
        )

    try:
        blockhash = Hash.from_string(
            raw_blockhash.strip()
        )

    except Exception as error:
        raise LiveBlockhashContextError(
            "Blockhash is invalid."
        ) from error

    if blockhash == Hash.default():
        raise LiveBlockhashContextError(
            "Default blockhash is invalid."
        )

    last_valid_block_height = (
        _require_nonnegative_int(
            value.get(
                "lastValidBlockHeight"
            ),
            label=(
                "last_valid_block_height"
            ),
        )
    )

    return LiveBlockhashContext(
        resolver_version=(
            LIVE_BLOCKHASH_CONTEXT_VERSION
        ),
        blockhash=str(
            blockhash
        ),
        last_valid_block_height=(
            last_valid_block_height
        ),
        rpc_slot=rpc_slot,
        min_context_slot=(
            min_context_slot
        ),
        commitment=COMMITMENT,
        fetched_at=time.time(),
    )
