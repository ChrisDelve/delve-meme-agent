from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.execution.model_entry_candidate import (
    EXPECTED_MODEL_SHADOW_VERSION,
    ModelEntryCandidate,
    make_model_entry_candidate,
)


MODEL_ENTRY_CANDIDATE_ADAPTER_VERSION = (
    "model-entry-candidate-adapter-v1"
)


def _required(
    prediction: Mapping[str, Any],
    name: str,
) -> Any:
    if name not in prediction:
        raise ValueError(
            f"prediction field {name} is missing"
        )

    return prediction[name]


def _require_exact(
    *,
    name: str,
    prediction_value: Any,
    event_value: Any,
) -> None:
    if (
        type(prediction_value)
        is not type(event_value)
        or prediction_value != event_value
    ):
        raise ValueError(
            f"prediction {name} mismatch"
        )


def _eligible_prediction_value(
    value: Any,
) -> bool:
    #
    # SQLite returns INTEGER 1 for the production
    # eligible row. True is also accepted for pure
    # in-memory callers/tests.
    #
    # Deliberately reject truthy coercions such as
    # "1", nonzero floats, lists, etc.
    #
    return (
        value is True
        or (
            type(value) is int
            and value == 1
        )
    )


def adapt_model_entry_candidate(
    *,
    prediction: Mapping[str, Any],
    entry_signature: str,
    mint: str,
    event_user: str,
    quote_mint: str,
    slot: int | None,
    trade_timestamp: int,
    observed_at: int,
    signal_virtual_quote_reserves: int,
    signal_virtual_token_reserves: int,
) -> ModelEntryCandidate:
    """
    Bind one persisted model prediction to the exact causal Pump BUY
    event and produce the immutable ModelEntryCandidate contract.

    This adapter is pure. It performs no:
      - SQLite read/write;
      - RPC;
      - token-safety resolution;
      - strategy threshold evaluation;
      - wallet/risk sizing;
      - durable live-entry admission;
      - capital reservation;
      - signer access;
      - transaction work;
      - BUY/SELL invocation.

    The prediction row is not trusted merely because it came from the
    model-shadow table. Every event identity field used by the live
    candidate must agree exactly with the causal event supplied by the
    caller.
    """

    if not isinstance(
        prediction,
        Mapping,
    ):
        raise TypeError(
            "prediction must be a mapping"
        )

    prediction_signature = _required(
        prediction,
        "entry_signature",
    )
    prediction_mint = _required(
        prediction,
        "mint",
    )
    prediction_wallet = _required(
        prediction,
        "wallet",
    )
    prediction_quote_mint = _required(
        prediction,
        "quote_mint",
    )
    prediction_slot = _required(
        prediction,
        "slot",
    )
    prediction_trade_timestamp = _required(
        prediction,
        "trade_timestamp",
    )
    prediction_observed_at = _required(
        prediction,
        "observed_at",
    )

    _require_exact(
        name="entry_signature",
        prediction_value=prediction_signature,
        event_value=entry_signature,
    )
    _require_exact(
        name="mint",
        prediction_value=prediction_mint,
        event_value=mint,
    )
    _require_exact(
        name="wallet",
        prediction_value=prediction_wallet,
        event_value=event_user,
    )
    _require_exact(
        name="quote_mint",
        prediction_value=prediction_quote_mint,
        event_value=quote_mint,
    )
    _require_exact(
        name="slot",
        prediction_value=prediction_slot,
        event_value=slot,
    )
    _require_exact(
        name="trade_timestamp",
        prediction_value=(
            prediction_trade_timestamp
        ),
        event_value=trade_timestamp,
    )
    _require_exact(
        name="observed_at",
        prediction_value=(
            prediction_observed_at
        ),
        event_value=observed_at,
    )

    shadow_version = _required(
        prediction,
        "shadow_version",
    )

    if (
        shadow_version
        != EXPECTED_MODEL_SHADOW_VERSION
    ):
        raise ValueError(
            "prediction shadow_version mismatch"
        )

    model_eligible = _required(
        prediction,
        "model_eligible",
    )

    if not _eligible_prediction_value(
        model_eligible
    ):
        raise ValueError(
            "prediction is not exactly eligible"
        )

    artifact_version = _required(
        prediction,
        "artifact_version",
    )
    artifact_sha256 = _required(
        prediction,
        "artifact_sha256",
    )
    probability_2x_15m = _required(
        prediction,
        "probability_2x_15m",
    )
    predicted_at = _required(
        prediction,
        "predicted_at",
    )

    return make_model_entry_candidate(
        entry_signature=entry_signature,
        mint=mint,
        event_user=event_user,
        quote_mint=quote_mint,
        slot=slot,
        trade_timestamp=trade_timestamp,
        observed_at=observed_at,
        predicted_at=predicted_at,
        model_shadow_version=(
            shadow_version
        ),
        artifact_version=(
            artifact_version
        ),
        artifact_sha256=(
            artifact_sha256
        ),
        model_eligible=True,
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
