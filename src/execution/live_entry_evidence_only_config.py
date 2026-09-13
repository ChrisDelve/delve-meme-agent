from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from src.strategies.live_entry_policy import (
    LIVE_ENTRY_POLICY_VERSION,
    LiveEntryPolicy,
)


LIVE_ENTRY_EVIDENCE_ONLY_CONFIG_VERSION = (
    "live-entry-evidence-only-config-v1"
)


def _strict_probability(
    name: str,
    value: Any,
) -> float:
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            (int, float),
        )
    ):
        raise TypeError(
            f"{name} must be numeric"
        )

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
        raise ValueError(
            f"{name} must be between 0 and 1"
        )

    return normalized


def _strict_positive_int(
    name: str,
    value: Any,
) -> int:
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            int,
        )
    ):
        raise TypeError(
            f"{name} must be int"
        )

    if value <= 0:
        raise ValueError(
            f"{name} must be positive"
        )

    return value


@dataclass(
    frozen=True,
    slots=True,
)
class LiveEntryEvidenceOnlyConfig:
    """
    Explicit non-capital operating policy for live-entry evidence
    observation.

    There are deliberately no defaults. Every policy and scheduler
    limit must be chosen explicitly by the caller.

    This object owns no:
      - environment loading;
      - RPC client;
      - SQLite connection;
      - LiveProcessOwner;
      - wallet or capital state;
      - signer or private key;
      - reservation;
      - transaction construction;
      - BUY or SELL authority.
    """

    min_probability_2x_15m: float
    max_candidate_age_seconds: int

    max_concurrency: int
    max_pending_tasks: int

    def __post_init__(
        self,
    ) -> None:
        if (
            LIVE_ENTRY_POLICY_VERSION
            != "live-entry-policy-v1"
        ):
            raise RuntimeError(
                "LIVE_ENTRY_EVIDENCE_ONLY_CONFIG_"
                "POLICY_VERSION_MISMATCH"
            )

        probability = (
            _strict_probability(
                "min_probability_2x_15m",
                self.min_probability_2x_15m,
            )
        )

        candidate_age = (
            _strict_positive_int(
                "max_candidate_age_seconds",
                self.max_candidate_age_seconds,
            )
        )

        concurrency = (
            _strict_positive_int(
                "max_concurrency",
                self.max_concurrency,
            )
        )

        pending = (
            _strict_positive_int(
                "max_pending_tasks",
                self.max_pending_tasks,
            )
        )

        if pending < concurrency:
            raise ValueError(
                "max_pending_tasks must be "
                ">= max_concurrency"
            )

        object.__setattr__(
            self,
            "min_probability_2x_15m",
            probability,
        )

        object.__setattr__(
            self,
            "max_candidate_age_seconds",
            candidate_age,
        )

        object.__setattr__(
            self,
            "max_concurrency",
            concurrency,
        )

        object.__setattr__(
            self,
            "max_pending_tasks",
            pending,
        )

    @property
    def policy(
        self,
    ) -> LiveEntryPolicy:
        return LiveEntryPolicy(
            min_probability_2x_15m=(
                self.min_probability_2x_15m
            ),
            max_candidate_age_seconds=(
                self.max_candidate_age_seconds
            ),
        )
