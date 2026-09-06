from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from src.research.forward_confirmation_spec import (
    ARTIFACT_SHA256,
    ARTIFACT_VERSION,
    FREEZE_EPOCH,
    MAX_EXECUTION_DELAY_SECONDS,
    MIN_DISTINCT_MINTS,
    SHADOW_VERSION,
    SOL_QUOTE_MINT,
    SPEC_VERSION,
    TARGET_HORIZON_SECONDS,
    build_spec,
    fingerprint,
)


DB_PATH = Path("logs/delve_meme.db")


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(
        DB_PATH,
        timeout=30.0,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA busy_timeout = 30000"
    )

    return connection


def verify_frozen_spec(
    connection: sqlite3.Connection,
) -> str:
    expected_sha256 = fingerprint(
        build_spec()
    )

    row = connection.execute(
        """
        SELECT
            spec_sha256,
            freeze_epoch_exclusive

        FROM forward_confirmation_specs

        WHERE spec_version = ?
        """,
        (SPEC_VERSION,),
    ).fetchone()

    if row is None:
        raise RuntimeError(
            "Frozen forward confirmation spec "
            "was not found in the database."
        )

    if (
        row["spec_sha256"]
        != expected_sha256
    ):
        raise RuntimeError(
            "Frozen confirmation spec hash "
            "does not match source contract."
        )

    if (
        int(row["freeze_epoch_exclusive"])
        != FREEZE_EPOCH
    ):
        raise RuntimeError(
            "Frozen confirmation cutoff "
            "does not match source contract."
        )

    return expected_sha256


def get_readiness(
    connection: sqlite3.Connection,
) -> sqlite3.Row:
    now = int(time.time())

    settle_seconds = (
        MAX_EXECUTION_DELAY_SECONDS
        + TARGET_HORIZON_SECONDS
    )

    return connection.execute(
        """
        WITH eligible AS (
            SELECT
                p.entry_signature,
                p.mint,
                p.predicted_at

            FROM model_shadow_predictions p

            WHERE
                p.predicted_at > ?

                AND p.model_eligible = 1

                AND p.shadow_version = ?

                AND p.artifact_version = ?

                AND p.artifact_sha256 = ?

                AND p.quote_mint = ?
        ),

        matured AS (
            SELECT
                *

            FROM eligible

            WHERE
                predicted_at <= ? - ?
        ),

        execution_candidates AS (
            SELECT
                m.*,

                (
                    SELECT
                        MIN(t.observed_at)

                    FROM trades t

                    WHERE
                        t.mint = m.mint

                        AND t.quote_mint = ?

                        AND t.side = 'BUY'

                        AND t.signature
                            != m.entry_signature

                        AND t.quote_amount > 0

                        AND t.token_amount > 0

                        AND t.observed_at
                            IS NOT NULL

                        AND t.observed_at
                            > m.predicted_at

                        AND t.observed_at
                            <= (
                                m.predicted_at
                                + ?
                            )
                ) AS execution_observed_at

            FROM matured m
        ),

        executable AS (
            SELECT
                *

            FROM execution_candidates

            WHERE
                execution_observed_at
                    IS NOT NULL
        ),

        certified AS (
            SELECT
                e.*

            FROM executable e

            WHERE EXISTS (
                SELECT 1

                FROM collector_coverage c

                WHERE
                    c.valid = 1

                    AND c.started_at
                        <= e.predicted_at

                    AND COALESCE(
                            c.ended_at,
                            ?
                        )
                        >= (
                            e.execution_observed_at
                            + ?
                        )
            )
        )

        SELECT
            (
                SELECT COUNT(*)
                FROM eligible
            ) AS eligible_predictions,

            (
                SELECT COUNT(DISTINCT mint)
                FROM eligible
            ) AS eligible_mints,

            (
                SELECT COUNT(*)
                FROM matured
            ) AS matured_predictions,

            (
                SELECT COUNT(DISTINCT mint)
                FROM matured
            ) AS matured_mints,

            (
                SELECT COUNT(*)
                FROM executable
            ) AS executable_predictions,

            (
                SELECT COUNT(DISTINCT mint)
                FROM executable
            ) AS executable_mints,

            (
                SELECT COUNT(*)
                FROM certified
            ) AS certified_predictions,

            (
                SELECT COUNT(DISTINCT mint)
                FROM certified
            ) AS certified_mints
        """,
        (
            FREEZE_EPOCH,
            SHADOW_VERSION,
            ARTIFACT_VERSION,
            ARTIFACT_SHA256,
            SOL_QUOTE_MINT,

            now,
            settle_seconds,

            SOL_QUOTE_MINT,
            MAX_EXECUTION_DELAY_SECONDS,

            now,
            TARGET_HORIZON_SECONDS,
        ),
    ).fetchone()


def main() -> None:
    with get_connection() as connection:
        spec_sha256 = verify_frozen_spec(
            connection
        )

        row = get_readiness(
            connection
        )

    ready_mints = int(
        row["certified_mints"]
    )

    remaining = max(
        0,
        MIN_DISTINCT_MINTS
        - ready_mints,
    )

    percentage = min(
        100.0,
        100.0
        * ready_mints
        / MIN_DISTINCT_MINTS,
    )

    print()
    print("=" * 78)

    print(
        "DELVE MEME AGENT — "
        "BLIND FORWARD CONFIRMATION READINESS"
    )

    print("=" * 78)

    print(
        f"Spec version:          "
        f"{SPEC_VERSION}"
    )

    print(
        f"Spec SHA-256:          "
        f"{spec_sha256}"
    )

    print(
        f"Post-freeze rule:      "
        f"predicted_at > {FREEZE_EPOCH}"
    )

    print(
        f"Required distinct mints: "
        f"{MIN_DISTINCT_MINTS:,}"
    )

    print()

    print("COHORT ACCUMULATION")
    print("-" * 78)

    print(
        f"Eligible predictions:  "
        f"{row['eligible_predictions']:,}"
    )

    print(
        f"Eligible mints:        "
        f"{row['eligible_mints']:,}"
    )

    print()

    print(
        f"Matured predictions:   "
        f"{row['matured_predictions']:,}"
    )

    print(
        f"Matured mints:         "
        f"{row['matured_mints']:,}"
    )

    print()

    print(
        f"Executable predictions:"
        f" {row['executable_predictions']:,}"
    )

    print(
        f"Executable mints:      "
        f"{row['executable_mints']:,}"
    )

    print()

    print(
        f"Certified predictions: "
        f"{row['certified_predictions']:,}"
    )

    print(
        f"Certified mints:       "
        f"{ready_mints:,}"
    )

    print()

    print("CONFIRMATION READINESS")
    print("-" * 78)

    print(
        f"Progress:              "
        f"{ready_mints:,}/"
        f"{MIN_DISTINCT_MINTS:,} "
        f"({percentage:.1f}%)"
    )

    print(
        f"Mints remaining:       "
        f"{remaining:,}"
    )

    print()

    if ready_mints >= MIN_DISTINCT_MINTS:
        print(
            "STATUS: READY FOR LOCKED "
            "CONFIRMATORY EVALUATION"
        )
    else:
        print(
            "STATUS: WAIT — CONFIRMATORY "
            "COHORT STILL ACCUMULATING"
        )

    print()
    print(
        "OUTCOME LABELS READ: NO"
    )

    print(
        "MODEL PERFORMANCE READ: NO"
    )

    print(
        "THRESHOLD SEARCH: NO"
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )

    print("=" * 78)


if __name__ == "__main__":
    main()