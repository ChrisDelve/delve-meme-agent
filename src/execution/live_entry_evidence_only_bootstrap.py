from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.execution.live_entry_evidence_only_config import (
    LIVE_ENTRY_EVIDENCE_ONLY_CONFIG_VERSION,
    LiveEntryEvidenceOnlyConfig,
)


LIVE_ENTRY_EVIDENCE_ONLY_BOOTSTRAP_VERSION = (
    "live-entry-evidence-only-bootstrap-v1"
)

EVIDENCE_MIN_PROBABILITY_ENV = (
    "DELVE_LIVE_ENTRY_EVIDENCE_MIN_PROBABILITY_2X_15M"
)
EVIDENCE_MAX_CANDIDATE_AGE_ENV = (
    "DELVE_LIVE_ENTRY_EVIDENCE_MAX_CANDIDATE_AGE_SECONDS"
)
EVIDENCE_MAX_CONCURRENCY_ENV = (
    "DELVE_LIVE_ENTRY_EVIDENCE_MAX_CONCURRENCY"
)
EVIDENCE_MAX_PENDING_TASKS_ENV = (
    "DELVE_LIVE_ENTRY_EVIDENCE_MAX_PENDING_TASKS"
)


class LiveEntryEvidenceOnlyBootstrapError(
    RuntimeError
):
    pass


def _components_are_compatible(
) -> bool:
    return (
        LIVE_ENTRY_EVIDENCE_ONLY_CONFIG_VERSION
        == "live-entry-evidence-only-config-v1"
    )


def _normalize_dotenv_path(
    value: Path | str | None,
) -> Path | None:
    if value is None:
        return None

    if (
        isinstance(
            value,
            str,
        )
        and not value.strip()
    ):
        raise ValueError(
            "dotenv_path is invalid"
        )

    try:
        return Path(
            value
        )
    except Exception:
        raise ValueError(
            "dotenv_path is invalid"
        ) from None


def _required_text(
    name: str,
) -> str:
    value = os.environ.get(
        name
    )

    if value is None:
        raise (
            LiveEntryEvidenceOnlyBootstrapError(
                f"EVIDENCE_ENV_MISSING:{name}"
            )
        )

    if not isinstance(
        value,
        str,
    ):
        raise (
            LiveEntryEvidenceOnlyBootstrapError(
                f"EVIDENCE_ENV_INVALID:{name}"
            )
        )

    normalized = value.strip()

    if not normalized:
        raise (
            LiveEntryEvidenceOnlyBootstrapError(
                f"EVIDENCE_ENV_BLANK:{name}"
            )
        )

    return normalized


def _probability_from_env(
    name: str,
) -> float:
    text = _required_text(
        name
    )

    try:
        value = float(
            text
        )
    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        raise (
            LiveEntryEvidenceOnlyBootstrapError(
                f"EVIDENCE_ENV_INVALID:{name}"
            )
        ) from None

    if (
        not math.isfinite(
            value
        )
        or value < 0.0
        or value > 1.0
    ):
        raise (
            LiveEntryEvidenceOnlyBootstrapError(
                f"EVIDENCE_ENV_INVALID:{name}"
            )
        )

    return value


def _positive_int_from_env(
    name: str,
) -> int:
    text = _required_text(
        name
    )

    try:
        value = int(
            text,
            10,
        )
    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        raise (
            LiveEntryEvidenceOnlyBootstrapError(
                f"EVIDENCE_ENV_INVALID:{name}"
            )
        ) from None

    if value <= 0:
        raise (
            LiveEntryEvidenceOnlyBootstrapError(
                f"EVIDENCE_ENV_INVALID:{name}"
            )
        )

    return value


def bootstrap_live_entry_evidence_only_config(
    *,
    dotenv_path: Path | str | None = Path(
        ".env"
    ),
) -> LiveEntryEvidenceOnlyConfig:
    """
    Load required non-secret evidence-only observation policy.

    Exactly four environment values are consumed. There are no
    production defaults.

    This bootstrap deliberately does not read:
      - signer/private-key environment variables;
      - live-capital operating configuration;
      - wallet state;
      - database state;
      - RPC state.
    """

    if not _components_are_compatible():
        raise (
            LiveEntryEvidenceOnlyBootstrapError(
                "EVIDENCE_BOOTSTRAP_"
                "COMPONENT_VERSION_MISMATCH"
            )
        )

    normalized_dotenv_path = (
        _normalize_dotenv_path(
            dotenv_path
        )
    )

    if normalized_dotenv_path is not None:
        load_dotenv(
            dotenv_path=(
                normalized_dotenv_path
            ),
            override=False,
        )

    min_probability = (
        _probability_from_env(
            EVIDENCE_MIN_PROBABILITY_ENV
        )
    )

    max_candidate_age = (
        _positive_int_from_env(
            EVIDENCE_MAX_CANDIDATE_AGE_ENV
        )
    )

    max_concurrency = (
        _positive_int_from_env(
            EVIDENCE_MAX_CONCURRENCY_ENV
        )
    )

    max_pending_tasks = (
        _positive_int_from_env(
            EVIDENCE_MAX_PENDING_TASKS_ENV
        )
    )

    if (
        max_pending_tasks
        < max_concurrency
    ):
        raise (
            LiveEntryEvidenceOnlyBootstrapError(
                "EVIDENCE_ENV_INVALID:"
                f"{EVIDENCE_MAX_PENDING_TASKS_ENV}"
            )
        )

    try:
        return (
            LiveEntryEvidenceOnlyConfig(
                min_probability_2x_15m=(
                    min_probability
                ),
                max_candidate_age_seconds=(
                    max_candidate_age
                ),
                max_concurrency=(
                    max_concurrency
                ),
                max_pending_tasks=(
                    max_pending_tasks
                ),
            )
        )

    except Exception:
        #
        # Parsed values have already passed the bootstrap contract.
        # Any remaining constructor failure is a component-contract
        # failure and must not leak implementation-specific details.
        #
        raise (
            LiveEntryEvidenceOnlyBootstrapError(
                "EVIDENCE_BOOTSTRAP_CONFIG_INVALID"
            )
        ) from None
