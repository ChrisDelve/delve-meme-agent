from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from src.features.live_signal_state import (
    build_live_signal_state,
    raw_model_features,
)
from src.models.validated_signal import ValidatedSignal


DB_PATH = Path("logs/delve_meme.db")

MODEL_SHADOW_VERSION = "model-shadow-v1"


_connection: sqlite3.Connection | None = None
_model: ValidatedSignal | None = None


def get_connection() -> sqlite3.Connection:
    global _connection

    if _connection is None:
        _connection = sqlite3.connect(
            DB_PATH,
            timeout=30.0,
        )
        _connection.row_factory = sqlite3.Row

        _connection.execute(
            "PRAGMA busy_timeout = 5000"
        )

    return _connection


def get_model() -> ValidatedSignal:
    global _model

    if _model is None:
        _model = ValidatedSignal()

    return _model


def init_model_shadow_table(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS model_shadow_predictions (
            entry_signature TEXT PRIMARY KEY,

            shadow_version TEXT NOT NULL,

            artifact_version TEXT NOT NULL,
            artifact_sha256 TEXT NOT NULL,

            mint TEXT NOT NULL,
            wallet TEXT NOT NULL,
            quote_mint TEXT NOT NULL,

            slot INTEGER,
            trade_timestamp INTEGER NOT NULL,
            observed_at INTEGER,

            mayhem_mode REAL,

            buys_15s INTEGER NOT NULL,
            buy_rate_15s REAL NOT NULL,

            model_eligible INTEGER NOT NULL,
            ineligible_reason TEXT,

            probability_2x_15m REAL,
            model_logit REAL,
            development_baseline_probability REAL,

            target_event TEXT,
            target_horizon_seconds INTEGER,

            transformed_features_json TEXT,

            predicted_at INTEGER NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_model_shadow_predictions_timestamp
        ON model_shadow_predictions (
            trade_timestamp
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_model_shadow_predictions_mint
        ON model_shadow_predictions (
            mint
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_model_shadow_predictions_artifact
        ON model_shadow_predictions (
            artifact_sha256
        )
        """
    )

    connection.commit()


def _existing_prediction(
    connection: sqlite3.Connection,
    entry_signature: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT *
        FROM model_shadow_predictions
        WHERE entry_signature = ?
        """,
        (entry_signature,),
    ).fetchone()

    if row is None:
        return None

    return dict(row)


def record_model_shadow_prediction(
    *,
    signature: str,
    slot: int | None,
    wallet: str,
    mint: str,
    quote_mint: str,
    trade_timestamp: int,
    mayhem_mode,
    observed_at: int | None = None,
) -> dict[str, Any]:
    """
    Build the production-equivalent causal signal state and record the
    validated model prediction.

    Important:
    - no outcome labels
    - no research tables
    - no model fitting
    - no validation-holdout access
    - no future trades
    - current trade_timestamp is strictly excluded from the 15s window
    - no order execution
    """

    if not signature:
        raise ValueError("signature is required")

    if not wallet:
        raise ValueError("wallet is required")

    if not mint:
        raise ValueError("mint is required")

    if not quote_mint:
        raise ValueError("quote_mint is required")

    if trade_timestamp is None:
        raise ValueError("trade_timestamp is required")

    connection = get_connection()

    init_model_shadow_table(
        connection
    )

    existing = _existing_prediction(
        connection,
        signature,
    )

    if existing is not None:
        return existing

    state = build_live_signal_state(
        connection,
        mint=mint,
        quote_mint=quote_mint,
        trade_timestamp=int(
            trade_timestamp
        ),
        mayhem_mode=mayhem_mode,
    )

    predicted_at = int(
        time.time()
    )

    if observed_at is None:
        observed_at = predicted_at

    model = get_model()

    if not state.eligible:
        connection.execute(
            """
            INSERT INTO model_shadow_predictions (
                entry_signature,
                shadow_version,

                artifact_version,
                artifact_sha256,

                mint,
                wallet,
                quote_mint,

                slot,
                trade_timestamp,
                observed_at,

                mayhem_mode,

                buys_15s,
                buy_rate_15s,

                model_eligible,
                ineligible_reason,

                probability_2x_15m,
                model_logit,
                development_baseline_probability,

                target_event,
                target_horizon_seconds,

                transformed_features_json,

                predicted_at
            )
            VALUES (
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?,
                ?, ?,
                ?, ?,
                NULL,
                NULL,
                ?,
                NULL,
                NULL,
                NULL,
                ?
            )
            """,
            (
                signature,
                MODEL_SHADOW_VERSION,

                model.payload[
                    "artifact_version"
                ],
                model.artifact_sha256,

                mint,
                wallet,
                quote_mint,

                slot,
                int(trade_timestamp),
                int(observed_at),

                state.mayhem_mode,

                int(state.buys_15s),
                float(
                    state.buy_rate_15s
                ),

                0,
                state.ineligible_reason,

                float(
                    model.baseline_probability
                ),

                predicted_at,
            ),
        )

        connection.commit()

        return (
            _existing_prediction(
                connection,
                signature,
            )
        )

    raw_features = (
        raw_model_features(
            state
        )
    )

    score = model.score(
        mayhem_mode=raw_features[
            "mayhem_mode"
        ],
        buy_rate_15s=raw_features[
            "buy_rate_15s"
        ],
    )

    target = score.get(
        "target",
        {},
    )

    transformed_features = (
        score.get(
            "transformed_features",
            {},
        )
    )

    connection.execute(
        """
        INSERT INTO model_shadow_predictions (
            entry_signature,
            shadow_version,

            artifact_version,
            artifact_sha256,

            mint,
            wallet,
            quote_mint,

            slot,
            trade_timestamp,
            observed_at,

            mayhem_mode,

            buys_15s,
            buy_rate_15s,

            model_eligible,
            ineligible_reason,

            probability_2x_15m,
            model_logit,
            development_baseline_probability,

            target_event,
            target_horizon_seconds,

            transformed_features_json,

            predicted_at
        )
        VALUES (
            ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?,
            ?, ?,
            1,
            NULL,
            ?, ?, ?,
            ?, ?,
            ?,
            ?
        )
        """,
        (
            signature,
            MODEL_SHADOW_VERSION,

            score[
                "artifact_version"
            ],
            score[
                "artifact_sha256"
            ],

            mint,
            wallet,
            quote_mint,

            slot,
            int(
                trade_timestamp
            ),
            int(
                observed_at
            ),

            state.mayhem_mode,

            int(
                state.buys_15s
            ),
            float(
                state.buy_rate_15s
            ),

            float(
                score["probability"]
            ),
            float(
                score["logit"]
            ),
            float(
                score[
                    "development_baseline_probability"
                ]
            ),

            target.get(
                "event"
            ),
            target.get(
                "horizon_seconds"
            ),

            json.dumps(
                transformed_features,
                sort_keys=True,
                separators=(
                    ",",
                    ":",
                ),
            ),

            predicted_at,
        ),
    )

    connection.commit()

    return (
        _existing_prediction(
            connection,
            signature,
        )
    )