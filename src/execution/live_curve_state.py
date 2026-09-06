from __future__ import annotations

import time
from dataclasses import dataclass

from solders.pubkey import Pubkey

from src.safety.token_safety_resolver import (
    BondingCurveSnapshot,
    COMMITMENT,
    HeliusRpcClient,
    SafetyResolutionError,
    context_slot,
    decode_bonding_curve,
    derive_bonding_curve,
)


@dataclass(frozen=True)
class LivePumpCurveState:
    mint: str

    curve: BondingCurveSnapshot

    rpc_slot: int

    fetched_at: float


async def resolve_live_pump_curve_state(
    *,
    mint: str,
    min_context_slot: int | None = None,
) -> LivePumpCurveState:
    try:
        mint_pubkey = Pubkey.from_string(
            mint
        )
    except Exception as error:
        raise SafetyResolutionError(
            "Invalid mint public key."
        ) from error

    bonding_curve = derive_bonding_curve(
        mint_pubkey
    )

    config = {
        "encoding": "base64",
        "commitment": COMMITMENT,
    }

    if min_context_slot is not None:
        config[
            "minContextSlot"
        ] = int(
            min_context_slot
        )

    async with HeliusRpcClient() as rpc:
        result = await rpc.call(
            "getAccountInfo",
            [
                str(
                    bonding_curve
                ),
                config,
            ],
        )

        #
        # Timestamp immediately after the
        # curve RPC completes. This is the
        # state whose age matters for an
        # intended order.
        #
        fetched_at = time.time()

    if (
        not isinstance(
            result,
            dict,
        )
        or result.get(
            "value"
        )
        is None
    ):
        raise SafetyResolutionError(
            "Live Pump bonding curve "
            "does not exist."
        )

    slot = context_slot(
        result
    )

    if slot is None:
        raise SafetyResolutionError(
            "Live curve RPC context slot "
            "is missing."
        )

    curve = decode_bonding_curve(
        address=str(
            bonding_curve
        ),
        account=result[
            "value"
        ],
    )

    return LivePumpCurveState(
        mint=mint,
        curve=curve,
        rpc_slot=int(
            slot
        ),
        fetched_at=fetched_at,
    )