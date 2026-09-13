from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.model_entry_candidate import (
    MODEL_ENTRY_CANDIDATE_VERSION,
    ModelEntryCandidate,
)
from src.portfolio.live_reservations import (
    DB_PATH,
    SQLITE_INT_MAX,
    get_connection,
)


LIVE_ENTRY_ADMISSION_VERSION = (
    "live-entry-admission-v1"
)

PASS = "PASS"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(
    frozen=True,
    slots=True,
)
class LiveEntryAdmission:
    admission_version: str
    candidate_version: str
    candidate_sha256: str

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

    admitted_at: float


@dataclass(
    frozen=True,
    slots=True,
)
class LiveEntryAdmissionResult:
    resolver_version: str
    status: str
    reasons: tuple[str, ...]

    entry_signature: str
    candidate_sha256: str

    admission: LiveEntryAdmission | None
    changed: bool


def _strict_nonnegative_sqlite_int(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= SQLITE_INT_MAX
    )


def _strict_positive_sqlite_int(
    value: Any,
) -> bool:
    return (
        _strict_nonnegative_sqlite_int(
            value
        )
        and value > 0
    )


def _strict_timestamp(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            (int, float),
        )
        and not isinstance(value, bool)
        and math.isfinite(
            float(value)
        )
        and float(value) >= 0.0
    )


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


def _candidate_storage_valid(
    candidate: ModelEntryCandidate,
) -> bool:
    return (
        isinstance(
            candidate,
            ModelEntryCandidate,
        )
        and candidate.candidate_version
        == MODEL_ENTRY_CANDIDATE_VERSION
        and (
            candidate.slot is None
            or _strict_nonnegative_sqlite_int(
                candidate.slot
            )
        )
        and _strict_nonnegative_sqlite_int(
            candidate.trade_timestamp
        )
        and _strict_nonnegative_sqlite_int(
            candidate.observed_at
        )
        and _strict_nonnegative_sqlite_int(
            candidate.predicted_at
        )
        and _strict_positive_sqlite_int(
            candidate
            .signal_virtual_quote_reserves
        )
        and _strict_positive_sqlite_int(
            candidate
            .signal_virtual_token_reserves
        )
    )


def _candidate_payload(
    candidate: ModelEntryCandidate,
) -> dict[str, object]:
    return {
        "candidate_version": (
            candidate.candidate_version
        ),
        "entry_signature": (
            candidate.entry_signature
        ),
        "mint": candidate.mint,
        "event_user": candidate.event_user,
        "quote_mint": candidate.quote_mint,
        "slot": candidate.slot,
        "trade_timestamp": (
            candidate.trade_timestamp
        ),
        "observed_at": candidate.observed_at,
        "predicted_at": candidate.predicted_at,
        "model_shadow_version": (
            candidate.model_shadow_version
        ),
        "artifact_version": (
            candidate.artifact_version
        ),
        "artifact_sha256": (
            candidate.artifact_sha256
        ),
        "model_eligible": (
            candidate.model_eligible
        ),
        "probability_2x_15m": (
            candidate.probability_2x_15m
        ),
        "signal_virtual_quote_reserves": (
            candidate
            .signal_virtual_quote_reserves
        ),
        "signal_virtual_token_reserves": (
            candidate
            .signal_virtual_token_reserves
        ),
    }


def _candidate_sha256(
    candidate: ModelEntryCandidate,
) -> str:
    payload = json.dumps(
        _candidate_payload(
            candidate
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode(
        "utf-8"
    )

    return hashlib.sha256(
        payload
    ).hexdigest()


def init_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
        live_entry_admissions (
            entry_signature
                TEXT PRIMARY KEY,

            admission_version
                TEXT NOT NULL,

            candidate_version
                TEXT NOT NULL,

            candidate_sha256
                TEXT NOT NULL
                CHECK (
                    length(candidate_sha256) = 64
                ),

            mint
                TEXT NOT NULL,

            event_user
                TEXT NOT NULL,

            quote_mint
                TEXT NOT NULL,

            slot
                INTEGER
                CHECK (
                    slot IS NULL
                    OR slot >= 0
                ),

            trade_timestamp
                INTEGER NOT NULL
                CHECK (trade_timestamp >= 0),

            observed_at
                INTEGER NOT NULL
                CHECK (observed_at >= 0),

            predicted_at
                INTEGER NOT NULL
                CHECK (predicted_at >= 0),

            model_shadow_version
                TEXT NOT NULL,

            artifact_version
                TEXT NOT NULL,

            artifact_sha256
                TEXT NOT NULL
                CHECK (
                    length(artifact_sha256) = 64
                ),

            model_eligible
                INTEGER NOT NULL
                CHECK (
                    model_eligible IN (0, 1)
                ),

            probability_2x_15m
                REAL NOT NULL
                CHECK (
                    probability_2x_15m >= 0.0
                    AND probability_2x_15m <= 1.0
                ),

            signal_virtual_quote_reserves
                INTEGER NOT NULL
                CHECK (
                    signal_virtual_quote_reserves
                    > 0
                ),

            signal_virtual_token_reserves
                INTEGER NOT NULL
                CHECK (
                    signal_virtual_token_reserves
                    > 0
                ),

            admitted_at
                REAL NOT NULL
                CHECK (admitted_at >= 0.0)
        )
        """
    )


def _row_to_admission(
    row: sqlite3.Row,
) -> LiveEntryAdmission | None:
    try:
        admission = LiveEntryAdmission(
            admission_version=str(
                row["admission_version"]
            ),
            candidate_version=str(
                row["candidate_version"]
            ),
            candidate_sha256=str(
                row["candidate_sha256"]
            ),
            entry_signature=str(
                row["entry_signature"]
            ),
            mint=str(
                row["mint"]
            ),
            event_user=str(
                row["event_user"]
            ),
            quote_mint=str(
                row["quote_mint"]
            ),
            slot=(
                None
                if row["slot"] is None
                else int(
                    row["slot"]
                )
            ),
            trade_timestamp=int(
                row["trade_timestamp"]
            ),
            observed_at=int(
                row["observed_at"]
            ),
            predicted_at=int(
                row["predicted_at"]
            ),
            model_shadow_version=str(
                row["model_shadow_version"]
            ),
            artifact_version=str(
                row["artifact_version"]
            ),
            artifact_sha256=str(
                row["artifact_sha256"]
            ),
            model_eligible=(
                int(
                    row["model_eligible"]
                )
                == 1
            ),
            probability_2x_15m=float(
                row["probability_2x_15m"]
            ),
            signal_virtual_quote_reserves=int(
                row[
                    "signal_virtual_quote_reserves"
                ]
            ),
            signal_virtual_token_reserves=int(
                row[
                    "signal_virtual_token_reserves"
                ]
            ),
            admitted_at=float(
                row["admitted_at"]
            ),
        )
    except Exception:
        return None

    if (
        admission.admission_version
        != LIVE_ENTRY_ADMISSION_VERSION
        or admission.candidate_version
        != MODEL_ENTRY_CANDIDATE_VERSION
        or not _valid_sha256(
            admission.candidate_sha256
        )
        or not admission.entry_signature
        or not admission.mint
        or not admission.event_user
        or not admission.quote_mint
        or (
            admission.slot is not None
            and not _strict_nonnegative_sqlite_int(
                admission.slot
            )
        )
        or not _strict_nonnegative_sqlite_int(
            admission.trade_timestamp
        )
        or not _strict_nonnegative_sqlite_int(
            admission.observed_at
        )
        or not _strict_nonnegative_sqlite_int(
            admission.predicted_at
        )
        or not admission.model_shadow_version
        or not admission.artifact_version
        or not _valid_sha256(
            admission.artifact_sha256
        )
        or not admission.model_eligible
        or not math.isfinite(
            admission.probability_2x_15m
        )
        or not (
            0.0
            <= admission.probability_2x_15m
            <= 1.0
        )
        or not _strict_positive_sqlite_int(
            admission
            .signal_virtual_quote_reserves
        )
        or not _strict_positive_sqlite_int(
            admission
            .signal_virtual_token_reserves
        )
        or not _strict_timestamp(
            admission.admitted_at
        )
    ):
        return None

    return admission


def _load_admission(
    *,
    connection: sqlite3.Connection,
    entry_signature: str,
) -> LiveEntryAdmission | None:
    row = connection.execute(
        """
        SELECT *
        FROM live_entry_admissions
        WHERE entry_signature = ?
        """,
        (
            entry_signature,
        ),
    ).fetchone()

    if row is None:
        return None

    return _row_to_admission(
        row
    )


def _admission_matches_candidate(
    *,
    admission: LiveEntryAdmission,
    candidate: ModelEntryCandidate,
    candidate_sha256: str,
) -> bool:
    return (
        admission.admission_version
        == LIVE_ENTRY_ADMISSION_VERSION
        and admission.candidate_version
        == candidate.candidate_version
        and admission.candidate_sha256
        == candidate_sha256
        and admission.entry_signature
        == candidate.entry_signature
        and admission.mint
        == candidate.mint
        and admission.event_user
        == candidate.event_user
        and admission.quote_mint
        == candidate.quote_mint
        and admission.slot
        == candidate.slot
        and admission.trade_timestamp
        == candidate.trade_timestamp
        and admission.observed_at
        == candidate.observed_at
        and admission.predicted_at
        == candidate.predicted_at
        and admission.model_shadow_version
        == candidate.model_shadow_version
        and admission.artifact_version
        == candidate.artifact_version
        and admission.artifact_sha256
        == candidate.artifact_sha256
        and admission.model_eligible
        is candidate.model_eligible
        and admission.probability_2x_15m
        == candidate.probability_2x_15m
        and admission.signal_virtual_quote_reserves
        == candidate.signal_virtual_quote_reserves
        and admission.signal_virtual_token_reserves
        == candidate.signal_virtual_token_reserves
    )


def acquire_live_entry_admission(
    *,
    candidate: ModelEntryCandidate,
    db_path: Path = DB_PATH,
) -> LiveEntryAdmissionResult:
    """
    Permanently admit one exact model entry candidate to cross toward
    live BUY authority.

    Identity is entry_signature plus the exact immutable candidate
    snapshot. Admission has no TTL in v1.

    First exact candidate:
        PASS, changed=True

    Exact replay:
        BLOCK, changed=False

    Same entry_signature with contradictory candidate identity:
        UNKNOWN, changed=False

    This primitive deliberately performs no:
      - candidate selection or strategy thresholding;
      - safety / fee / curve resolution;
      - RPC;
      - wallet balance resolution;
      - risk sizing;
      - capital reservation;
      - signer/private-key handling;
      - transaction construction/signing/submission;
      - BUY or SELL invocation;
      - retry or scheduling.
    """
    entry_signature = ""
    candidate_sha256 = ""

    def finish(
        status: str,
        *reasons: str,
        admission: (
            LiveEntryAdmission | None
        ) = None,
        changed: bool = False,
    ) -> LiveEntryAdmissionResult:
        return LiveEntryAdmissionResult(
            resolver_version=(
                LIVE_ENTRY_ADMISSION_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            entry_signature=(
                entry_signature
            ),
            candidate_sha256=(
                candidate_sha256
            ),
            admission=admission,
            changed=changed,
        )

    if not isinstance(
        candidate,
        ModelEntryCandidate,
    ):
        return finish(
            UNKNOWN,
            "INVALID_MODEL_ENTRY_CANDIDATE",
        )

    entry_signature = (
        candidate.entry_signature
    )

    if not _candidate_storage_valid(
        candidate
    ):
        return finish(
            UNKNOWN,
            "MODEL_ENTRY_CANDIDATE_STORAGE_INVALID",
        )

    try:
        candidate_sha256 = (
            _candidate_sha256(
                candidate
            )
        )
    except Exception:
        return finish(
            UNKNOWN,
            "MODEL_ENTRY_CANDIDATE_HASH_FAILED",
        )

    if not _valid_sha256(
        candidate_sha256
    ):
        return finish(
            UNKNOWN,
            "MODEL_ENTRY_CANDIDATE_HASH_INVALID",
        )

    connection: (
        sqlite3.Connection | None
    ) = None

    try:
        connection = get_connection(
            db_path
        )

        connection.execute(
            "BEGIN IMMEDIATE"
        )

        init_schema(
            connection
        )

        existing = _load_admission(
            connection=connection,
            entry_signature=(
                entry_signature
            ),
        )

        if existing is not None:
            if not _admission_matches_candidate(
                admission=existing,
                candidate=candidate,
                candidate_sha256=(
                    candidate_sha256
                ),
            ):
                connection.commit()

                return finish(
                    UNKNOWN,
                    (
                        "LIVE_ENTRY_ADMISSION_"
                        "IDENTITY_MISMATCH"
                    ),
                    admission=existing,
                )

            connection.commit()

            return finish(
                BLOCK,
                "LIVE_ENTRY_ALREADY_ADMITTED",
                admission=existing,
                changed=False,
            )

        admitted_at = time.time()

        if not _strict_timestamp(
            admitted_at
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                "SYSTEM_TIME_INVALID",
            )

        connection.execute(
            """
            INSERT INTO live_entry_admissions (
                entry_signature,
                admission_version,
                candidate_version,
                candidate_sha256,
                mint,
                event_user,
                quote_mint,
                slot,
                trade_timestamp,
                observed_at,
                predicted_at,
                model_shadow_version,
                artifact_version,
                artifact_sha256,
                model_eligible,
                probability_2x_15m,
                signal_virtual_quote_reserves,
                signal_virtual_token_reserves,
                admitted_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                candidate.entry_signature,
                LIVE_ENTRY_ADMISSION_VERSION,
                candidate.candidate_version,
                candidate_sha256,
                candidate.mint,
                candidate.event_user,
                candidate.quote_mint,
                candidate.slot,
                candidate.trade_timestamp,
                candidate.observed_at,
                candidate.predicted_at,
                candidate.model_shadow_version,
                candidate.artifact_version,
                candidate.artifact_sha256,
                (
                    1
                    if candidate.model_eligible
                    else 0
                ),
                candidate.probability_2x_15m,
                (
                    candidate
                    .signal_virtual_quote_reserves
                ),
                (
                    candidate
                    .signal_virtual_token_reserves
                ),
                admitted_at,
            ),
        )

        persisted = _load_admission(
            connection=connection,
            entry_signature=(
                entry_signature
            ),
        )

        if (
            persisted is None
            or not _admission_matches_candidate(
                admission=persisted,
                candidate=candidate,
                candidate_sha256=(
                    candidate_sha256
                ),
            )
            or persisted.admitted_at
            != admitted_at
        ):
            connection.rollback()

            return finish(
                UNKNOWN,
                (
                    "LIVE_ENTRY_ADMISSION_"
                    "ATOMIC_VERIFICATION_FAILED"
                ),
            )

        connection.commit()

        return finish(
            PASS,
            admission=persisted,
            changed=True,
        )

    except sqlite3.IntegrityError:
        if connection is not None:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass

        return finish(
            UNKNOWN,
            "LIVE_ENTRY_ADMISSION_INTEGRITY_ERROR",
        )

    except sqlite3.Error:
        if connection is not None:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass

        return finish(
            UNKNOWN,
            "LIVE_ENTRY_ADMISSION_DATABASE_ERROR",
        )

    except Exception:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass

        return finish(
            UNKNOWN,
            "LIVE_ENTRY_ADMISSION_EXCEPTION",
        )

    finally:
        if connection is not None:
            connection.close()
