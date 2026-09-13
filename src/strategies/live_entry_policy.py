from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from src.execution.model_entry_candidate import (
    MODEL_ENTRY_CANDIDATE_VERSION,
    ModelEntryCandidate,
)


LIVE_ENTRY_POLICY_VERSION = (
    "live-entry-policy-v1"
)

PASS = "PASS"
REJECT = "REJECT"
UNKNOWN = "UNKNOWN"

#
# Pump trade events represent native SOL with the system-program
# pubkey. Transaction construction later converts that economic
# quote asset to wrapped SOL where the token instruction requires it.
#
SOL_QUOTE_MINT = (
    "11111111111111111111111111111111"
)


@dataclass(
    frozen=True,
    slots=True,
)
class LiveEntryPolicy:
    """
    Explicit strategy-level requirements for deciding whether an
    immutable ModelEntryCandidate deserves fresh live evidence.

    There are deliberately no live defaults. A caller must explicitly
    choose both strategy parameters.

    This policy owns no:
      - RPC;
      - token-safety resolution;
      - Pump curve or fee resolution;
      - SQLite access;
      - durable live-entry admission;
      - wallet/risk sizing;
      - capital reservation;
      - signer access;
      - transaction construction/signing/submission;
      - BUY or SELL invocation.
    """

    min_probability_2x_15m: float
    max_candidate_age_seconds: int


@dataclass(
    frozen=True,
    slots=True,
)
class LiveEntryPolicyDecision:
    evaluator_version: str

    status: str
    reasons: tuple[str, ...]

    entry_signature: str
    mint: str

    probability_2x_15m: float | None

    evaluated_at: int
    candidate_age_seconds: int | None

    policy: LiveEntryPolicy | None

    @property
    def should_resolve_evidence(
        self,
    ) -> bool:
        return self.status == PASS


def _strict_positive_int(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            int,
        )
        and not isinstance(
            value,
            bool,
        )
        and value > 0
    )


def _strict_probability(
    value: Any,
) -> float | None:
    if (
        not isinstance(
            value,
            (int, float),
        )
        or isinstance(
            value,
            bool,
        )
    ):
        return None

    normalized = float(
        value
    )

    if (
        not math.isfinite(
            normalized
        )
        or normalized < 0.0
        or normalized > 1.0
    ):
        return None

    return normalized


def _policy_error(
    policy: Any,
) -> str | None:
    if not isinstance(
        policy,
        LiveEntryPolicy,
    ):
        return (
            "INVALID_LIVE_ENTRY_POLICY"
        )

    if (
        _strict_probability(
            policy.min_probability_2x_15m
        )
        is None
    ):
        return (
            "INVALID_MIN_PROBABILITY_2X_15M"
        )

    if not _strict_positive_int(
        policy.max_candidate_age_seconds
    ):
        return (
            "INVALID_MAX_CANDIDATE_AGE_SECONDS"
        )

    return None


def evaluate_live_entry_candidate(
    *,
    candidate: ModelEntryCandidate,
    evaluated_at: int,
    policy: LiveEntryPolicy,
) -> LiveEntryPolicyDecision:
    """
    Pure strategy-level live-entry candidate evaluation.

    PASS means only:
        this immutable model candidate deserves fresh live evidence.

    PASS does NOT authorize capital.

    Evaluation ordering is deliberate:

        valid explicit policy
            ↓
        valid evaluation time
            ↓
        valid immutable candidate contract
            ↓
        supported native-SOL quote asset
            ↓
        valid causal timestamps
            ↓
        minimum model probability
            ↓
        maximum prediction age
            ↓
        PASS

    REJECT represents a valid candidate that does not satisfy live
    strategy policy.

    UNKNOWN represents malformed or causally inconsistent evidence
    where the system cannot safely make a strategy decision.
    """

    entry_signature = ""
    mint = ""
    probability: float | None = None
    candidate_age_seconds: int | None = None

    valid_policy = (
        policy
        if isinstance(
            policy,
            LiveEntryPolicy,
        )
        else None
    )

    def finish(
        status: str,
        *reasons: str,
    ) -> LiveEntryPolicyDecision:
        return LiveEntryPolicyDecision(
            evaluator_version=(
                LIVE_ENTRY_POLICY_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            entry_signature=(
                entry_signature
            ),
            mint=mint,
            probability_2x_15m=(
                probability
            ),
            evaluated_at=(
                evaluated_at
                if _strict_positive_int(
                    evaluated_at
                )
                else 0
            ),
            candidate_age_seconds=(
                candidate_age_seconds
            ),
            policy=valid_policy,
        )

    policy_error = _policy_error(
        policy
    )

    if policy_error is not None:
        return finish(
            UNKNOWN,
            policy_error,
        )

    if not _strict_positive_int(
        evaluated_at
    ):
        return finish(
            UNKNOWN,
            "INVALID_EVALUATED_AT",
        )

    if (
        not isinstance(
            candidate,
            ModelEntryCandidate,
        )
        or candidate.candidate_version
        != MODEL_ENTRY_CANDIDATE_VERSION
    ):
        return finish(
            UNKNOWN,
            "INVALID_MODEL_ENTRY_CANDIDATE",
        )

    entry_signature = (
        candidate.entry_signature
    )
    mint = candidate.mint

    if candidate.model_eligible is not True:
        return finish(
            UNKNOWN,
            "INVALID_MODEL_ELIGIBILITY",
        )

    if (
        candidate.quote_mint
        != SOL_QUOTE_MINT
    ):
        return finish(
            REJECT,
            "UNSUPPORTED_QUOTE_MINT",
        )

    probability = _strict_probability(
        candidate.probability_2x_15m
    )

    if probability is None:
        return finish(
            UNKNOWN,
            "INVALID_CANDIDATE_PROBABILITY",
        )

    timestamps = (
        candidate.trade_timestamp,
        candidate.observed_at,
        candidate.predicted_at,
    )

    if not all(
        _strict_positive_int(
            value
        )
        for value in timestamps
    ):
        return finish(
            UNKNOWN,
            "INVALID_CANDIDATE_TIMESTAMPS",
        )

    if (
        candidate.trade_timestamp
        > candidate.observed_at
    ):
        return finish(
            UNKNOWN,
            "TRADE_AFTER_OBSERVATION",
        )

    if (
        candidate.observed_at
        > candidate.predicted_at
    ):
        return finish(
            UNKNOWN,
            "OBSERVATION_AFTER_PREDICTION",
        )

    if (
        candidate.predicted_at
        > evaluated_at
    ):
        return finish(
            UNKNOWN,
            "PREDICTION_AFTER_EVALUATION",
        )

    candidate_age_seconds = (
        evaluated_at
        - candidate.predicted_at
    )

    threshold = float(
        policy.min_probability_2x_15m
    )

    if probability < threshold:
        return finish(
            REJECT,
            "PROBABILITY_BELOW_THRESHOLD",
        )

    if (
        candidate_age_seconds
        > policy.max_candidate_age_seconds
    ):
        return finish(
            REJECT,
            "CANDIDATE_STALE",
        )

    return finish(
        PASS
    )
