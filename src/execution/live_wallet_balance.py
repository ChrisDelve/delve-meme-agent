from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from solders.pubkey import Pubkey

from src.safety.token_safety_resolver import (
    COMMITMENT,
    HeliusRpcClient,
)


LIVE_WALLET_BALANCE_VERSION = (
    "live-wallet-balance-v1"
)

RESOLVED = "RESOLVED"
UNKNOWN = "UNKNOWN"

U64_MAX = (1 << 64) - 1


@dataclass(frozen=True)
class LiveWalletBalanceResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    wallet_pubkey: str

    balance_lamports: int | None
    rpc_slot: int | None

    commitment: str
    min_context_slot: int | None


def _strict_u64(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= U64_MAX
    )


async def resolve_live_wallet_balance(
    *,
    wallet_pubkey: str,
    min_context_slot: int | None = None,
) -> LiveWalletBalanceResult:
    """
    Resolve the authoritative confirmed native-SOL
    balance for one exact wallet.

    This resolver has no:
    - database authority
    - transaction-build authority
    - signing authority
    - send authority
    - risk/sizing authority

    The returned balance is raw wallet SOL.
    It is NOT yet "available trading cash".
    """

    normalized_wallet = ""

    def finish(
        status: str,
        *reasons: str,
        balance_lamports: int | None = None,
        rpc_slot: int | None = None,
    ) -> LiveWalletBalanceResult:
        return LiveWalletBalanceResult(
            resolver_version=(
                LIVE_WALLET_BALANCE_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=normalized_wallet,
            balance_lamports=(
                balance_lamports
            ),
            rpc_slot=rpc_slot,
            commitment=COMMITMENT,
            min_context_slot=(
                min_context_slot
            ),
        )

    if not isinstance(
        wallet_pubkey,
        str,
    ):
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    wallet_pubkey = (
        wallet_pubkey.strip()
    )

    if not wallet_pubkey:
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    try:
        parsed_wallet = (
            Pubkey.from_string(
                wallet_pubkey
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    if parsed_wallet == Pubkey.default():
        return finish(
            UNKNOWN,
            "INVALID_WALLET_PUBKEY",
        )

    normalized_wallet = str(
        parsed_wallet
    )

    if (
        min_context_slot is not None
        and not _strict_u64(
            min_context_slot
        )
    ):
        return finish(
            UNKNOWN,
            "INVALID_MIN_CONTEXT_SLOT",
        )

    config: dict[str, Any] = {
        "commitment": COMMITMENT,
    }

    if min_context_slot is not None:
        config["minContextSlot"] = (
            min_context_slot
        )

    try:
        async with HeliusRpcClient() as rpc:
            response = await rpc.call(
                "getBalance",
                [
                    normalized_wallet,
                    config,
                ],
            )

    except Exception:
        return finish(
            UNKNOWN,
            "BALANCE_RPC_FAILED",
        )

    if not isinstance(
        response,
        dict,
    ):
        return finish(
            UNKNOWN,
            "BALANCE_RESPONSE_INVALID",
        )

    context = response.get(
        "context"
    )

    if not isinstance(
        context,
        dict,
    ):
        return finish(
            UNKNOWN,
            "BALANCE_RESPONSE_INVALID",
        )

    rpc_slot = context.get(
        "slot"
    )

    if not _strict_u64(
        rpc_slot
    ):
        return finish(
            UNKNOWN,
            "BALANCE_RESPONSE_INVALID",
        )

    if (
        min_context_slot is not None
        and rpc_slot < min_context_slot
    ):
        return finish(
            UNKNOWN,
            "BALANCE_CONTEXT_SLOT_BELOW_MINIMUM",
        )

    balance = response.get(
        "value"
    )

    if not _strict_u64(
        balance
    ):
        return finish(
            UNKNOWN,
            "BALANCE_RESPONSE_INVALID",
        )

    return finish(
        RESOLVED,
        balance_lamports=balance,
        rpc_slot=rpc_slot,
    )
