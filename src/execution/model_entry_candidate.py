from __future__ import annotations

import math
import string
from dataclasses import dataclass


MODEL_ENTRY_CANDIDATE_VERSION = (
    "model-entry-candidate-v1"
)

EXPECTED_MODEL_SHADOW_VERSION = (
    "model-shadow-v1"
)

EXPECTED_ARTIFACT_VERSION = (
    "validated-signal-artifact-v1"
)


def _require_text(
    *,
    name: str,
    value: object,
) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
    ):
        raise ValueError(
            f"{name} is invalid"
        )

    return value


def _require_nonnegative_int(
    *,
    name: str,
    value: object,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
    ):
        raise ValueError(
            f"{name} is invalid"
        )

    return value


def _require_positive_int(
    *,
    name: str,
    value: object,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValueError(
            f"{name} is invalid"
        )

    return value


def _require_probability(
    value: object,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(
            value,
            (int, float),
        )
    ):
        raise ValueError(
            "probability_2x_15m is invalid"
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
            "probability_2x_15m is invalid"
        )

    return normalized


def _require_sha256(
    value: object,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(
            character
            not in string.hexdigits
            for character in value
        )
    ):
        raise ValueError(
            "artifact_sha256 is invalid"
        )

    return value


@dataclass(
    frozen=True,
    slots=True,
)
class ModelEntryCandidate:
    """
    Immutable causal candidate evidence for a possible live entry.

    This object owns no live-capital authority.

    It deliberately does not:
      - resolve token safety;
      - perform RPC work;
      - read or write SQLite;
      - resolve Pump curve or fee state;
      - perform risk sizing;
      - evaluate execution quality;
      - reserve capital;
      - load or invoke a signer;
      - construct/sign/submit a transaction;
      - invoke BUY or SELL execution.

    Strategy thresholds are also deliberately absent. A candidate
    contract binds model provenance to one causal market event; later
    policy decides whether that candidate deserves fresh live
    evaluation.
    """

    candidate_version: str

    entry_signature: str
    mint: str
    event_user: str
    quote_mint: str

    slot: int | None
    trade_timestamp: int
    observed_at: int
    predicted_at: int

    model_shadow_version: str
    artifact_version: str
    artifact_sha256: str

    model_eligible: bool
    probability_2x_15m: float

    signal_virtual_quote_reserves: int
    signal_virtual_token_reserves: int

    def __post_init__(
        self,
    ) -> None:
        if (
            self.candidate_version
            != MODEL_ENTRY_CANDIDATE_VERSION
        ):
            raise ValueError(
                "candidate_version is invalid"
            )

        _require_text(
            name="entry_signature",
            value=self.entry_signature,
        )

        _require_text(
            name="mint",
            value=self.mint,
        )

        _require_text(
            name="event_user",
            value=self.event_user,
        )

        _require_text(
            name="quote_mint",
            value=self.quote_mint,
        )

        if self.slot is not None:
            _require_nonnegative_int(
                name="slot",
                value=self.slot,
            )

        _require_nonnegative_int(
            name="trade_timestamp",
            value=self.trade_timestamp,
        )

        _require_nonnegative_int(
            name="observed_at",
            value=self.observed_at,
        )

        _require_nonnegative_int(
            name="predicted_at",
            value=self.predicted_at,
        )

        if (
            self.model_shadow_version
            != EXPECTED_MODEL_SHADOW_VERSION
        ):
            raise ValueError(
                "model_shadow_version is invalid"
            )

        if (
            self.artifact_version
            != EXPECTED_ARTIFACT_VERSION
        ):
            raise ValueError(
                "artifact_version is invalid"
            )

        _require_sha256(
            self.artifact_sha256
        )

        if not isinstance(
            self.model_eligible,
            bool,
        ):
            raise ValueError(
                "model_eligible is invalid"
            )

        if not self.model_eligible:
            raise ValueError(
                "model candidate is not eligible"
            )

        probability = (
            _require_probability(
                self.probability_2x_15m
            )
        )

        object.__setattr__(
            self,
            "probability_2x_15m",
            probability,
        )

        _require_positive_int(
            name=(
                "signal_virtual_quote_reserves"
            ),
            value=(
                self.signal_virtual_quote_reserves
            ),
        )

        _require_positive_int(
            name=(
                "signal_virtual_token_reserves"
            ),
            value=(
                self.signal_virtual_token_reserves
            ),
        )


def make_model_entry_candidate(
    *,
    entry_signature: str,
    mint: str,
    event_user: str,
    quote_mint: str,
    slot: int | None,
    trade_timestamp: int,
    observed_at: int,
    predicted_at: int,
    model_shadow_version: str,
    artifact_version: str,
    artifact_sha256: str,
    model_eligible: bool,
    probability_2x_15m: float,
    signal_virtual_quote_reserves: int,
    signal_virtual_token_reserves: int,
) -> ModelEntryCandidate:
    return ModelEntryCandidate(
        candidate_version=(
            MODEL_ENTRY_CANDIDATE_VERSION
        ),
        entry_signature=entry_signature,
        mint=mint,
        event_user=event_user,
        quote_mint=quote_mint,
        slot=slot,
        trade_timestamp=trade_timestamp,
        observed_at=observed_at,
        predicted_at=predicted_at,
        model_shadow_version=(
            model_shadow_version
        ),
        artifact_version=(
            artifact_version
        ),
        artifact_sha256=(
            artifact_sha256
        ),
        model_eligible=(
            model_eligible
        ),
        probability_2x_15m=(
            probability_2x_15m
        ),
        signal_virtual_quote_reserves=(
            signal_virtual_quote_reserves
        ),
        signal_virtual_token_reserves=(
            signal_virtual_token_reserves
        ),
    )
