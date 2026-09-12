from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.live_pump_sell_authorization import (
    AUTHORIZED as AUTHORIZATION_AUTHORIZED,
    BLOCK as AUTHORIZATION_BLOCK,
    UNKNOWN as AUTHORIZATION_UNKNOWN,
    LIVE_PUMP_SELL_AUTHORIZATION_VERSION,
    LivePumpSellAuthorization,
    authorize_live_pump_sell,
)
from src.portfolio.live_reservations import (
    DB_PATH,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    BLOCK as CLAIM_BLOCK,
    PASS as CLAIM_PASS,
    UNKNOWN as CLAIM_UNKNOWN,
    LIVE_SELL_INVENTORY_CLAIM_VERSION,
    LiveSellInventoryClaim,
    acquire_live_sell_inventory_claim,
)


LIVE_SELL_INITIATOR_VERSION = (
    "live-sell-initiator-v1"
)

CLAIMED = "CLAIMED"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveSellInitiationResult:
    initiator_version: str

    status: str
    reasons: tuple[str, ...]

    wallet_pubkey: str
    mint: str
    tokens_to_sell: int

    authorization_sha256: str | None

    authorization_status: str | None
    claim_status: str | None

    claim_changed: bool | None

    authorization: LivePumpSellAuthorization | None
    claim: LiveSellInventoryClaim | None


def _valid_sha256(
    value: Any,
) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(
            character
            in "0123456789abcdef"
            for character in value
        )
    )


def _binding_matches(
    *,
    authorization: LivePumpSellAuthorization,
    claim: LiveSellInventoryClaim,
) -> bool:
    return (
        authorization.authorization_version
        == LIVE_PUMP_SELL_AUTHORIZATION_VERSION

        and claim.claim_version
        == LIVE_SELL_INVENTORY_CLAIM_VERSION

        and claim.status == ACTIVE

        and claim.authorization_version
        == authorization.authorization_version

        and claim.authorization_sha256
        == authorization.authorization_sha256

        and claim.wallet_pubkey
        == authorization.wallet_pubkey

        and claim.mint
        == authorization.mint

        and claim.tokens_to_sell
        == authorization.tokens_to_sell

        and claim.allocation
        == authorization.allocation
    )


async def initiate_live_sell_once(
    *,
    wallet_pubkey: str,
    mint: str,
    tokens_to_sell: int,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    db_path: Path = DB_PATH,
) -> LiveSellInitiationResult:
    """
    Authorize and atomically claim inventory for one
    explicit live SELL request.

    This component is policy-neutral. The caller has
    already decided that a SELL should occur and exactly
    how many tokens should be sold.

    Consequential authority is deliberately limited to:

        authorize_live_pump_sell(...)
        acquire_live_sell_inventory_claim(...)

    A successful invocation ends with an ACTIVE durable
    claim plus its exact durable authorization snapshot.

    It does NOT:
    - choose whether to exit;
    - choose tokens_to_sell;
    - sign;
    - build a transaction;
    - submit;
    - reconcile;
    - invoke the live SELL lifecycle;
    - process multiple requests;
    - retry.
    """

    normalized_wallet = (
        wallet_pubkey.strip()
        if isinstance(
            wallet_pubkey,
            str,
        )
        else ""
    )

    normalized_mint = (
        mint.strip()
        if isinstance(
            mint,
            str,
        )
        else ""
    )

    authorization_sha256: str | None = None
    authorization_status: str | None = None
    claim_status: str | None = None
    claim_changed: bool | None = None

    authorization: (
        LivePumpSellAuthorization | None
    ) = None

    claim: (
        LiveSellInventoryClaim | None
    ) = None

    def finish(
        status: str,
        *reasons: str,
    ) -> LiveSellInitiationResult:
        return LiveSellInitiationResult(
            initiator_version=(
                LIVE_SELL_INITIATOR_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            wallet_pubkey=(
                normalized_wallet
            ),
            mint=normalized_mint,
            tokens_to_sell=(
                tokens_to_sell
                if isinstance(
                    tokens_to_sell,
                    int,
                )
                and not isinstance(
                    tokens_to_sell,
                    bool,
                )
                else 0
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            authorization_status=(
                authorization_status
            ),
            claim_status=claim_status,
            claim_changed=claim_changed,
            authorization=authorization,
            claim=claim,
        )

    try:
        normalized_path = Path(
            db_path
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_DATABASE_PATH_INVALID",
        )

    #
    # Do not duplicate the authorizer's validation or
    # economic policy here. It owns wallet/mint parsing,
    # position/allocation resolution, fee state,
    # simulation, and final authorization fingerprint.
    #
    try:
        authorization_result = (
            await authorize_live_pump_sell(
                wallet_pubkey=(
                    wallet_pubkey
                ),
                mint=mint,
                tokens_to_sell=(
                    tokens_to_sell
                ),
                slippage_bps=(
                    slippage_bps
                ),
                base_network_fee_lamports=(
                    base_network_fee_lamports
                ),
                priority_fee_lamports=(
                    priority_fee_lamports
                ),
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_SELL_AUTHORIZATION_EXCEPTION",
        )

    if (
        getattr(
            authorization_result,
            "resolver_version",
            None,
        )
        != LIVE_PUMP_SELL_AUTHORIZATION_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_AUTHORIZATION_VERSION_MISMATCH",
        )

    authorization_status = getattr(
        authorization_result,
        "status",
        None,
    )

    if (
        authorization_status
        == AUTHORIZATION_BLOCK
    ):
        return finish(
            BLOCK,
            *tuple(
                getattr(
                    authorization_result,
                    "reasons",
                    (),
                )
            ),
        )

    if (
        authorization_status
        == AUTHORIZATION_UNKNOWN
    ):
        return finish(
            UNKNOWN,
            *tuple(
                getattr(
                    authorization_result,
                    "reasons",
                    (),
                )
            ),
        )

    if (
        authorization_status
        != AUTHORIZATION_AUTHORIZED
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_AUTHORIZATION_STATUS_INVALID",
        )

    candidate_authorization = getattr(
        authorization_result,
        "authorization",
        None,
    )

    if not isinstance(
        candidate_authorization,
        LivePumpSellAuthorization,
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_AUTHORIZATION_MISSING",
        )

    authorization = (
        candidate_authorization
    )

    authorization_sha256 = (
        authorization.authorization_sha256
    )

    if (
        authorization.authorization_version
        != LIVE_PUMP_SELL_AUTHORIZATION_VERSION
        or not _valid_sha256(
            authorization_sha256
        )
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_AUTHORIZATION_CONTRACT_INVALID",
        )

    #
    # Ensure the returned authority is bound to the
    # normalized request identity before attempting the
    # durable claim.
    #
    result_wallet = getattr(
        authorization_result,
        "wallet_pubkey",
        None,
    )

    result_mint = getattr(
        authorization_result,
        "mint",
        None,
    )

    if (
        authorization.wallet_pubkey
        != normalized_wallet
        or authorization.mint
        != normalized_mint
        or result_wallet
        != normalized_wallet
        or result_mint
        != normalized_mint
        or result_wallet
        != authorization.wallet_pubkey
        or result_mint
        != authorization.mint
        or authorization.tokens_to_sell
        != tokens_to_sell
        or getattr(
            authorization_result,
            "allocation",
            None,
        )
        != authorization.allocation
        or getattr(
            authorization_result,
            "exit_execution",
            None,
        )
        != authorization.exit_execution
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_AUTHORIZATION_BINDING_MISMATCH",
        )

    #
    # Atomic durable boundary:
    #
    # - re-read authoritative positions
    # - re-plan FIFO allocation under BEGIN IMMEDIATE
    # - compare against immutable authorization
    # - enforce one ACTIVE claim per wallet/mint
    # - persist exact authorization snapshot + claim
    #
    try:
        claim_result = (
            acquire_live_sell_inventory_claim(
                authorization=(
                    authorization
                ),
                db_path=normalized_path,
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_SELL_CLAIM_EXCEPTION",
        )

    if (
        getattr(
            claim_result,
            "resolver_version",
            None,
        )
        != LIVE_SELL_INVENTORY_CLAIM_VERSION
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_CLAIM_VERSION_MISMATCH",
        )

    claim_status = getattr(
        claim_result,
        "status",
        None,
    )

    claim_changed = getattr(
        claim_result,
        "changed",
        None,
    )

    if claim_status == CLAIM_BLOCK:
        return finish(
            BLOCK,
            *tuple(
                getattr(
                    claim_result,
                    "reasons",
                    (),
                )
            ),
        )

    if claim_status == CLAIM_UNKNOWN:
        return finish(
            UNKNOWN,
            *tuple(
                getattr(
                    claim_result,
                    "reasons",
                    (),
                )
            ),
        )

    if claim_status != CLAIM_PASS:
        return finish(
            UNKNOWN,
            "LIVE_SELL_CLAIM_STATUS_INVALID",
        )

    candidate_claim = getattr(
        claim_result,
        "claim",
        None,
    )

    if not isinstance(
        candidate_claim,
        LiveSellInventoryClaim,
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_CLAIM_MISSING",
        )

    claim = candidate_claim

    if (
        claim_result.authorization_sha256
        != authorization_sha256
        or not _binding_matches(
            authorization=authorization,
            claim=claim,
        )
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_CLAIM_BINDING_MISMATCH",
        )

    if not isinstance(
        claim_changed,
        bool,
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_CLAIM_CHANGED_INVALID",
        )

    return finish(
        CLAIMED,
    )
