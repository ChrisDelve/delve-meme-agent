from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path


DB_PATH = Path("logs/delve_meme.db")

SPEC_VERSION = "forward-confirmation-spec-v1"

FREEZE_EPOCH = 1788712703

SHADOW_VERSION = "model-shadow-v1"

GRADER_VERSION = "model-shadow-grader-v1"

ARTIFACT_VERSION = "validated-signal-artifact-v1"

ARTIFACT_SHA256 = (
    "22e40605a87d06bddc382edf877274c628e40a1845530ef00a043ea4a7c1177c"
)

SOL_QUOTE_MINT = "11111111111111111111111111111111"

TARGET_EVENT = "2x"
TARGET_HORIZON_SECONDS = 900

EXECUTION_PROXY = "first_subsequent_buy"
MAX_EXECUTION_DELAY_SECONDS = 10

MIN_DISTINCT_MINTS = 500

BOOTSTRAP_UNIT = "mint"
BOOTSTRAP_RESAMPLES = 10000

PRIMARY_METRICS = {
    "logloss_gain_minimum": 0.005,
    "logloss_ci_lower_minimum": 0.0,
    "brier_gain_minimum": 0.0,
    "auc_ci_lower_minimum": 0.50,
}

POLICY = {
    "model_refit": False,
    "feature_changes": False,
    "recalibration": False,
    "threshold_search": False,
    "strategy_threshold_selection": False,
    "session_3_reuse": False,
    "capital_authorization": False,
}


def build_spec() -> dict:
    return {
        "spec_version": SPEC_VERSION,
        "freeze_epoch_exclusive": FREEZE_EPOCH,
        "shadow_version": SHADOW_VERSION,
        "grader_version": GRADER_VERSION,
        "artifact_version": ARTIFACT_VERSION,
        "artifact_sha256": ARTIFACT_SHA256,
        "quote_mint": SOL_QUOTE_MINT,
        "target_event": TARGET_EVENT,
        "target_horizon_seconds": TARGET_HORIZON_SECONDS,
        "execution_proxy": EXECUTION_PROXY,
        "max_execution_delay_seconds": (
            MAX_EXECUTION_DELAY_SECONDS
        ),
        "minimum_distinct_mints": MIN_DISTINCT_MINTS,
        "bootstrap_unit": BOOTSTRAP_UNIT,
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "primary_metrics": PRIMARY_METRICS,
        "policy": POLICY,
    }


def canonical_json(spec: dict) -> str:
    return json.dumps(
        spec,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint(spec: dict) -> str:
    return hashlib.sha256(
        canonical_json(spec).encode("utf-8")
    ).hexdigest()


def init_table(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        forward_confirmation_specs (
            spec_version TEXT PRIMARY KEY,
            spec_sha256 TEXT NOT NULL,
            freeze_epoch_exclusive INTEGER NOT NULL,
            spec_json TEXT NOT NULL,
            frozen_at INTEGER NOT NULL
        )
        """
    )

    connection.commit()


def freeze() -> None:
    spec = build_spec()

    spec_json = canonical_json(spec)

    spec_sha256 = fingerprint(spec)

    frozen_at = int(time.time())

    with sqlite3.connect(DB_PATH) as connection:
        init_table(connection)

        existing = connection.execute(
            """
            SELECT
                spec_sha256,
                spec_json,
                freeze_epoch_exclusive
            FROM forward_confirmation_specs
            WHERE spec_version = ?
            """,
            (SPEC_VERSION,),
        ).fetchone()

        if existing is not None:
            if (
                existing[0] != spec_sha256
                or existing[1] != spec_json
                or existing[2] != FREEZE_EPOCH
            ):
                raise RuntimeError(
                    "Frozen confirmation spec already exists "
                    "with different contents."
                )

            print(
                "ALREADY FROZEN — IDENTICAL SPEC"
            )
        else:
            connection.execute(
                """
                INSERT INTO forward_confirmation_specs (
                    spec_version,
                    spec_sha256,
                    freeze_epoch_exclusive,
                    spec_json,
                    frozen_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    SPEC_VERSION,
                    spec_sha256,
                    FREEZE_EPOCH,
                    spec_json,
                    frozen_at,
                ),
            )

            connection.commit()

            print(
                "FROZEN — NEW CONFIRMATION SPEC"
            )

    print()
    print(
        f"Spec version:       {SPEC_VERSION}"
    )

    print(
        f"Spec SHA-256:       {spec_sha256}"
    )

    print(
        f"Freeze epoch:       > {FREEZE_EPOCH}"
    )

    print(
        f"Minimum mints:      {MIN_DISTINCT_MINTS}"
    )

    print(
        f"Bootstrap unit:     {BOOTSTRAP_UNIT}"
    )

    print(
        f"Bootstrap samples:  {BOOTSTRAP_RESAMPLES:,}"
    )

    print()
    print(
        "MODEL REFIT: NO"
    )

    print(
        "RECALIBRATION: NO"
    )

    print(
        "THRESHOLD SEARCH: NO"
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )


if __name__ == "__main__":
    freeze()