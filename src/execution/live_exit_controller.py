from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.execution.live_sell_initiator import (
    BLOCK as INITIATOR_BLOCK,
    CLAIMED as INITIATOR_CLAIMED,
    UNKNOWN as INITIATOR_UNKNOWN,
    LIVE_SELL_INITIATOR_VERSION,
    LiveSellInitiationResult,
    initiate_live_sell_once,
)
from src.portfolio.live_wallet_valuation import (
    RESOLVED as VALUATION_RESOLVED,
    LIVE_WALLET_VALUATION_VERSION,
    LiveWalletValuationResult,
    resolve_live_wallet_valuation,
)
from src.strategies.live_exit_policy import (
    FULL_EXIT,
    HOLD,
    UNKNOWN as POLICY_UNKNOWN,
    LIVE_EXIT_POLICY_VERSION,
    LiveExitDecision,
    LiveExitPolicy,
    evaluate_live_exit,
)


LIVE_EXIT_CONTROLLER_VERSION = (
    "live-exit-controller-v1"
)

IDLE = "IDLE"
CLAIMED = "CLAIMED"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"

U64_MAX = (1 << 64) - 1

_EXIT_REASON_PRIORITY = {
    "STOP_LOSS": 0,
    "TAKE_PROFIT": 1,
    "MAX_HOLD_TIME": 2,
}


@dataclass(frozen=True)
class LiveExitControllerResult:
    controller_version: str

    status: str
    reasons: tuple[str, ...]

    wallet_pubkey: str
    evaluated_at: int

    valuation: LiveWalletValuationResult | None

    decisions: tuple[
        LiveExitDecision,
        ...,
    ] | None

    selected_decision: LiveExitDecision | None

    initiation: LiveSellInitiationResult | None


def _strict_u64(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= U64_MAX
    )


def _strict_positive_u64(
    value: Any,
) -> bool:
    return (
        _strict_u64(value)
        and value > 0
    )


def _decision_binding_valid(
    *,
    decision: LiveExitDecision,
    valuation: Any,
) -> bool:
    if (
        not isinstance(
            decision,
            LiveExitDecision,
        )
        or decision.evaluator_version
        != LIVE_EXIT_POLICY_VERSION
        or decision.mint
        != valuation.mint
        or decision.tokens_held
        != valuation.tokens_held
    ):
        return False

    if decision.status == FULL_EXIT:
        return (
            decision.reason
            in _EXIT_REASON_PRIORITY
            and _strict_positive_u64(
                decision.tokens_to_sell
            )
            and decision.tokens_to_sell
            == decision.tokens_held
        )

    if decision.status in (
        HOLD,
        POLICY_UNKNOWN,
    ):
        return (
            decision.tokens_to_sell == 0
        )

    return False


def _initiation_binding_valid(
    *,
    initiation: LiveSellInitiationResult,
    wallet_pubkey: str,
    decision: LiveExitDecision,
) -> bool:
    if (
        not isinstance(
            initiation,
            LiveSellInitiationResult,
        )
        or initiation.initiator_version
        != LIVE_SELL_INITIATOR_VERSION
        or initiation.wallet_pubkey
        != wallet_pubkey
        or initiation.mint
        != decision.mint
        or initiation.tokens_to_sell
        != decision.tokens_to_sell
    ):
        return False

    if initiation.status == INITIATOR_CLAIMED:
        return (
            initiation.authorization is not None
            and initiation.claim is not None
        )

    return initiation.status in (
        INITIATOR_BLOCK,
        INITIATOR_UNKNOWN,
    )


async def control_live_exit_once(
    *,
    wallet_pubkey: str,
    evaluated_at: int,
    policy: LiveExitPolicy,
    slippage_bps: int,
    base_network_fee_lamports: int,
    priority_fee_lamports: int,
    min_context_slot: int | None = None,
) -> LiveExitControllerResult:
    """
    Resolve, evaluate, and if warranted initiate at most
    one live wallet exit.

    Authority is deliberately bounded to:
      1. read-only live wallet valuation;
      2. pure exit-policy evaluation;
      3. one call to initiate_live_sell_once(...).

    The initiator itself may authorize and atomically claim
    inventory. This controller does NOT:
    - sign;
    - construct a transaction;
    - submit;
    - reconcile;
    - invoke the SELL lifecycle;
    - retry;
    - loop continuously;
    - initiate more than one SELL per invocation.

    All mint decisions are evaluated before any claim is
    attempted.

    Multiple actionable exits are prioritized:
      STOP_LOSS
      TAKE_PROFIT
      MAX_HOLD_TIME
      mint lexical order as tie-breaker.
    """

    normalized_wallet = (
        wallet_pubkey.strip()
        if isinstance(
            wallet_pubkey,
            str,
        )
        else ""
    )

    valuation_result: (
        LiveWalletValuationResult | None
    ) = None

    decisions: (
        tuple[
            LiveExitDecision,
            ...,
        ]
        | None
    ) = None

    selected_decision: (
        LiveExitDecision | None
    ) = None

    initiation: (
        LiveSellInitiationResult | None
    ) = None

    def finish(
        status: str,
        *reasons: str,
    ) -> LiveExitControllerResult:
        return LiveExitControllerResult(
            controller_version=(
                LIVE_EXIT_CONTROLLER_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=normalized_wallet,
            evaluated_at=(
                evaluated_at
                if _strict_u64(
                    evaluated_at
                )
                else 0
            ),
            valuation=valuation_result,
            decisions=decisions,
            selected_decision=(
                selected_decision
            ),
            initiation=initiation,
        )

    if not _strict_positive_u64(
        evaluated_at
    ):
        return finish(
            UNKNOWN,
            "INVALID_EVALUATED_AT",
        )

    try:
        valuation_result = (
            await resolve_live_wallet_valuation(
                wallet_pubkey=wallet_pubkey,
                slippage_bps=slippage_bps,
                base_network_fee_lamports=(
                    base_network_fee_lamports
                ),
                priority_fee_lamports=(
                    priority_fee_lamports
                ),
                min_context_slot=min_context_slot,
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            "WALLET_VALUATION_EXCEPTION",
        )

    if not isinstance(
        valuation_result,
        LiveWalletValuationResult,
    ):
        return finish(
            UNKNOWN,
            "INVALID_WALLET_VALUATION_RESULT",
        )

    if (
        valuation_result.resolver_version
        != LIVE_WALLET_VALUATION_VERSION
    ):
        return finish(
            UNKNOWN,
            "WALLET_VALUATION_VERSION_MISMATCH",
        )

    if (
        valuation_result.wallet_pubkey
        != normalized_wallet
    ):
        return finish(
            UNKNOWN,
            "WALLET_VALUATION_IDENTITY_MISMATCH",
        )

    if (
        valuation_result.status
        != VALUATION_RESOLVED
    ):
        return finish(
            UNKNOWN,
            "WALLET_VALUATION_UNKNOWN",
            *valuation_result.reasons,
        )

    mint_valuations = (
        valuation_result.mint_valuations
    )

    if mint_valuations is None:
        return finish(
            UNKNOWN,
            "MINT_VALUATIONS_MISSING",
        )

    if (
        valuation_result.unique_open_mints
        != len(mint_valuations)
    ):
        return finish(
            UNKNOWN,
            "MINT_VALUATION_COUNT_MISMATCH",
        )

    seen_mints: set[str] = set()

    ordered_valuations = []

    for mint_valuation in mint_valuations:
        mint = getattr(
            mint_valuation,
            "mint",
            None,
        )

        tokens_held = getattr(
            mint_valuation,
            "tokens_held",
            None,
        )

        if (
            not isinstance(
                mint,
                str,
            )
            or not mint.strip()
            or mint != mint.strip()
            or not _strict_positive_u64(
                tokens_held
            )
        ):
            return finish(
                UNKNOWN,
                "INVALID_MINT_VALUATION_BINDING",
            )

        if mint in seen_mints:
            return finish(
                UNKNOWN,
                "DUPLICATE_MINT_VALUATION",
            )

        seen_mints.add(mint)
        ordered_valuations.append(
            mint_valuation
        )

    ordered_valuations.sort(
        key=lambda item: item.mint
    )

    evaluated_decisions = []

    for mint_valuation in ordered_valuations:
        try:
            decision = evaluate_live_exit(
                valuation=mint_valuation,
                evaluated_at=evaluated_at,
                policy=policy,
            )
        except Exception:
            return finish(
                UNKNOWN,
                "EXIT_POLICY_EXCEPTION",
            )

        if not _decision_binding_valid(
            decision=decision,
            valuation=mint_valuation,
        ):
            return finish(
                UNKNOWN,
                "EXIT_DECISION_BINDING_MISMATCH",
            )

        evaluated_decisions.append(
            decision
        )

    decisions = tuple(
        evaluated_decisions
    )

    actionable = [
        decision
        for decision in decisions
        if decision.status == FULL_EXIT
    ]

    if not actionable:
        unknown_decisions = [
            decision
            for decision in decisions
            if decision.status
            == POLICY_UNKNOWN
        ]

        if unknown_decisions:
            return finish(
                UNKNOWN,
                *(
                    (
                        "EXIT_POLICY_UNKNOWN:"
                        f"{decision.mint}:"
                        f"{decision.reason}"
                    )
                    for decision
                    in unknown_decisions
                ),
            )

        return finish(
            IDLE,
            "NO_LIVE_EXIT_DUE",
        )

    actionable.sort(
        key=lambda decision: (
            _EXIT_REASON_PRIORITY[
                decision.reason
            ],
            decision.mint,
        )
    )

    selected_decision = actionable[0]

    try:
        initiation = (
            await initiate_live_sell_once(
                wallet_pubkey=(
                    normalized_wallet
                ),
                mint=(
                    selected_decision.mint
                ),
                tokens_to_sell=(
                    selected_decision
                    .tokens_to_sell
                ),
                slippage_bps=slippage_bps,
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
            "LIVE_SELL_INITIATION_EXCEPTION",
        )

    if not _initiation_binding_valid(
        initiation=initiation,
        wallet_pubkey=normalized_wallet,
        decision=selected_decision,
    ):
        return finish(
            UNKNOWN,
            "LIVE_SELL_INITIATION_BINDING_MISMATCH",
        )

    if (
        initiation.status
        == INITIATOR_CLAIMED
    ):
        return finish(
            CLAIMED,
            "LIVE_EXIT_CLAIMED",
        )

    if (
        initiation.status
        == INITIATOR_BLOCK
    ):
        return finish(
            BLOCK,
            *initiation.reasons,
        )

    if (
        initiation.status
        == INITIATOR_UNKNOWN
    ):
        return finish(
            UNKNOWN,
            *initiation.reasons,
        )

    return finish(
        UNKNOWN,
        "UNRECOGNIZED_INITIATOR_STATUS",
    )
