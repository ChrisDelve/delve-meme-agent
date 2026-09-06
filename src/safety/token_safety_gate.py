from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass

from src.safety.token_safety_resolver import (
    SafetyResolutionError,
    TokenSafetySnapshot,
    resolve_token_safety,
)


GATE_VERSION = "token-safety-gate-v1"

PASS = "PASS"
REJECT = "REJECT"
UNKNOWN = "UNKNOWN"

#
# Pump SOL-paired bonding curves store
# Pubkey::default() as quote_mint.
#
SOL_QUOTE_MINT = (
    "11111111111111111111111111111111"
)

EXPECTED_PUMP_DECIMALS = 6

#
# We want the independently fetched RPC
# facts to remain tightly coherent.
#
MAX_RPC_SLOT_SPAN = 8

#
# Current Pump create_v2 Token-2022 mints
# use on-mint metadata.
#
REQUIRED_TOKEN_2022_EXTENSIONS = {
    "metadatapointer",
    "tokenmetadata",
}

#
# For v1, these are also the only
# Token-2022 mint extensions our executor
# explicitly supports.
#
ALLOWED_TOKEN_2022_EXTENSIONS = {
    "metadatapointer",
    "tokenmetadata",
}


@dataclass(frozen=True)
class TokenSafetyGateResult:
    gate_version: str

    mint: str

    status: str

    reasons: tuple[str, ...]

    resolver_warnings: tuple[str, ...]

    evaluated_at: int

    snapshot: TokenSafetySnapshot | None

    resolution_error: str | None

    @property
    def allows_trade(self) -> bool:
        return self.status == PASS


def normalize_extension(
    name: str,
) -> str:
    return "".join(
        character.lower()
        for character in name
        if character.isalnum()
    )


def evaluate_token_safety(
    snapshot: TokenSafetySnapshot,
) -> TokenSafetyGateResult:
    reject_reasons: list[str] = []
    unknown_reasons: list[str] = []

    curve = snapshot.bonding_curve

    #
    # -------------------------------------------------
    # MINT CONTROL
    # -------------------------------------------------
    #

    if snapshot.mint_authority is not None:
        reject_reasons.append(
            "ACTIVE_MINT_AUTHORITY"
        )

    if snapshot.freeze_authority is not None:
        reject_reasons.append(
            "ACTIVE_FREEZE_AUTHORITY"
        )

    if (
        snapshot.decimals
        != EXPECTED_PUMP_DECIMALS
    ):
        reject_reasons.append(
            "UNEXPECTED_PUMP_DECIMALS"
        )

    if snapshot.supply_raw <= 0:
        reject_reasons.append(
            "NON_POSITIVE_TOKEN_SUPPLY"
        )

    #
    # -------------------------------------------------
    # TOKEN PROGRAM / TOKEN-2022 EXTENSIONS
    # -------------------------------------------------
    #

    if snapshot.token_standard == "TOKEN_2022":
        normalized_extensions = {
            normalize_extension(extension)
            for extension
            in snapshot.token_2022_extensions
        }

        if not normalized_extensions:
            unknown_reasons.append(
                "TOKEN_2022_EXTENSIONS_NOT_RESOLVED"
            )

        unsupported_extensions = (
            normalized_extensions
            - ALLOWED_TOKEN_2022_EXTENSIONS
        )

        if unsupported_extensions:
            reject_reasons.append(
                "UNSUPPORTED_TOKEN_2022_EXTENSIONS:"
                + ",".join(
                    sorted(
                        unsupported_extensions
                    )
                )
            )

        missing_extensions = (
            REQUIRED_TOKEN_2022_EXTENSIONS
            - normalized_extensions
        )

        if missing_extensions:
            unknown_reasons.append(
                "EXPECTED_TOKEN_2022_EXTENSIONS_MISSING:"
                + ",".join(
                    sorted(
                        missing_extensions
                    )
                )
            )

    elif snapshot.token_standard == "SPL_TOKEN":
        #
        # Pump's legacy create instruction
        # does not support Mayhem mode.
        #
        if curve.is_mayhem_mode:
            reject_reasons.append(
                "LEGACY_SPL_TOKEN_MARKED_MAYHEM"
            )

    else:
        reject_reasons.append(
            "UNSUPPORTED_TOKEN_STANDARD"
        )

    #
    # -------------------------------------------------
    # PUMP STRUCTURE
    # -------------------------------------------------
    #

    if not curve.owner_verified:
        reject_reasons.append(
            "INVALID_BONDING_CURVE_OWNER"
        )

    if not curve.discriminator_verified:
        reject_reasons.append(
            "INVALID_BONDING_CURVE_DISCRIMINATOR"
        )

    #
    # Our v1 execution path is bonding-curve only.
    # PumpSwap execution is deliberately not wired yet.
    #
    if curve.complete:
        reject_reasons.append(
            "BONDING_CURVE_COMPLETE"
        )

    if curve.real_token_reserves <= 0:
        reject_reasons.append(
            "NO_REAL_TOKEN_LIQUIDITY"
        )

    #
    # Agent v1 trades SOL-paired coins only.
    #
    if curve.quote_mint not in (
        None,
        SOL_QUOTE_MINT,
    ):
        reject_reasons.append(
            "NON_SOL_QUOTE_MINT"
        )

    #
    # -------------------------------------------------
    # RPC / DATA COHERENCE
    # -------------------------------------------------
    #

    if (
        snapshot.rpc_slot_span
        > MAX_RPC_SLOT_SPAN
    ):
        unknown_reasons.append(
            "RPC_SNAPSHOT_TOO_WIDE"
        )

    #
    # Resolver warnings are intentionally
    # interpreted explicitly.
    #
    for warning in snapshot.warnings:

        if warning == (
            "MAYHEM_CURVE_SUPPLY_DIFFERS_"
            "FROM_MINT_SUPPLY"
        ):
            #
            # Expected Mayhem supply structure.
            #
            continue

        if warning == "MINT_SUPPLY_RPC_MISMATCH":
            unknown_reasons.append(
                "MINT_SUPPLY_RPC_MISMATCH"
            )

            continue

        if warning == "CURVE_SUPPLY_MISMATCH":
            unknown_reasons.append(
                "CURVE_SUPPLY_MISMATCH"
            )

            continue

        if warning == (
            "TOKEN_2022_EXTENSIONS_NOT_REPORTED"
        ):
            unknown_reasons.append(
                "TOKEN_2022_EXTENSIONS_NOT_REPORTED"
            )

            continue

        if warning == "NO_EXTERNAL_SUPPLY":
            #
            # Legitimate for an extremely young
            # Pump/Mayhem coin. There is simply
            # no external-holder concentration
            # to evaluate yet.
            #
            continue

        if warning == "NON_DEFAULT_QUOTE_MINT":
            reject_reasons.append(
                "NON_SOL_QUOTE_MINT"
            )

            continue

        if warning == "RPC_SLOT_SPAN_GT_32":
            unknown_reasons.append(
                "RPC_SNAPSHOT_TOO_WIDE"
            )

            continue

        #
        # Important fail-closed behavior:
        #
        # If the resolver gains a new warning
        # in the future, the gate must not
        # silently start permitting it.
        #
        unknown_reasons.append(
            f"UNREVIEWED_RESOLVER_WARNING:{warning}"
        )

    #
    # Deduplicate while retaining order.
    #
    reject_reasons = list(
        dict.fromkeys(
            reject_reasons
        )
    )

    unknown_reasons = list(
        dict.fromkeys(
            unknown_reasons
        )
    )

    #
    # Known unsafe facts take precedence.
    #
    if reject_reasons:
        status = REJECT
        reasons = tuple(
            reject_reasons
        )

    elif unknown_reasons:
        status = UNKNOWN
        reasons = tuple(
            unknown_reasons
        )

    else:
        status = PASS
        reasons = ()

    return TokenSafetyGateResult(
        gate_version=GATE_VERSION,

        mint=snapshot.mint,

        status=status,

        reasons=reasons,

        resolver_warnings=(
            snapshot.warnings
        ),

        evaluated_at=int(
            time.time()
        ),

        snapshot=snapshot,

        resolution_error=None,
    )


async def resolve_and_gate(
    mint: str,
) -> TokenSafetyGateResult:
    try:
        snapshot = await resolve_token_safety(
            mint
        )

    except SafetyResolutionError as error:
        return TokenSafetyGateResult(
            gate_version=GATE_VERSION,

            mint=mint,

            status=UNKNOWN,

            reasons=(
                "TOKEN_SAFETY_RESOLUTION_FAILED",
            ),

            resolver_warnings=(),

            evaluated_at=int(
                time.time()
            ),

            snapshot=None,

            resolution_error=str(
                error
            ),
        )

    except Exception as error:
        #
        # Production fail-closed behavior.
        # A software/runtime failure must
        # never become permission to trade.
        #
        return TokenSafetyGateResult(
            gate_version=GATE_VERSION,

            mint=mint,

            status=UNKNOWN,

            reasons=(
                "TOKEN_SAFETY_UNEXPECTED_ERROR",
            ),

            resolver_warnings=(),

            evaluated_at=int(
                time.time()
            ),

            snapshot=None,

            resolution_error=(
                f"{type(error).__name__}: "
                f"{error}"
            ),
        )

    return evaluate_token_safety(
        snapshot
    )


def print_result(
    result: TokenSafetyGateResult,
) -> None:
    print()
    print("=" * 82)

    print(
        "DELVE MEME AGENT — "
        "TOKEN SAFETY GATE"
    )

    print("=" * 82)

    print(
        f"Gate:                    "
        f"{result.gate_version}"
    )

    print(
        f"Mint:                    "
        f"{result.mint}"
    )

    print(
        f"Decision:                "
        f"{result.status}"
    )

    print(
        f"Trade allowed:           "
        f"{result.allows_trade}"
    )

    if result.reasons:
        print()
        print("REASONS")
        print("-" * 82)

        for reason in result.reasons:
            print(
                f"- {reason}"
            )

    if result.resolver_warnings:
        print()
        print("RESOLVER WARNINGS")
        print("-" * 82)

        for warning in (
            result.resolver_warnings
        ):
            print(
                f"- {warning}"
            )

    if result.snapshot is not None:
        snapshot = result.snapshot

        print()
        print("STRUCTURAL FACTS")
        print("-" * 82)

        print(
            f"Token standard:          "
            f"{snapshot.token_standard}"
        )

        print(
            f"Mint authority:          "
            f"{snapshot.mint_authority or 'NONE'}"
        )

        print(
            f"Freeze authority:        "
            f"{snapshot.freeze_authority or 'NONE'}"
        )

        print(
            f"Curve complete:          "
            f"{snapshot.bonding_curve.complete}"
        )

        print(
            f"Real token reserves:     "
            f"{snapshot.bonding_curve.real_token_reserves:,}"
        )

        print(
            f"External % supply:       "
            f"{snapshot.external_supply_pct_total_supply:.3f}%"
        )

        print(
            f"Top holder % total:      "
            f"{snapshot.top_external_holder_pct_total_supply:.3f}%"
            if (
                snapshot.top_external_holder_pct_total_supply
                is not None
            )
            else
            "Top holder % total:      UNKNOWN"
        )

        print(
            f"Creator % total:         "
            f"{snapshot.creator_pct_total_supply:.3f}%"
            if (
                snapshot.creator_pct_total_supply
                is not None
            )
            else
            "Creator % total:         UNKNOWN"
        )

        print(
            f"RPC slot span:           "
            f"{snapshot.rpc_slot_span}"
        )

    if result.resolution_error:
        print()
        print("RESOLUTION ERROR")
        print("-" * 82)

        print(
            result.resolution_error
        )

    print()

    print(
        "TRANSACTION SUBMISSION: NO"
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )

    print("=" * 82)


async def async_main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--mint",
        required=True,
    )

    parser.add_argument(
        "--json",
        action="store_true",
    )

    args = parser.parse_args()

    result = await resolve_and_gate(
        args.mint
    )

    if args.json:
        output = asdict(
            result
        )

        output[
            "allows_trade"
        ] = result.allows_trade

        print(
            json.dumps(
                output,
                indent=2,
                sort_keys=True,
            )
        )

        return

    print_result(
        result
    )


def main() -> None:
    asyncio.run(
        async_main()
    )


if __name__ == "__main__":
    main()