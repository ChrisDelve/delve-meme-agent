from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path
from typing import Any

from src.execution.live_authority_gate import (
    LIVE_AUTHORITY_GATE_VERSION,
    LiveAuthorityGate,
)
from src.execution.live_recovery_coordinator import (
    ADVANCED,
    BLOCK,
    HOLD,
    IDLE,
    RECONCILED,
    UNKNOWN,
    LIVE_RECOVERY_COORDINATOR_VERSION,
    recover_one_live_obligation_once,
)
from src.portfolio.live_reservations import (
    DB_PATH,
)


LIVE_RECOVERY_HEARTBEAT_VERSION = (
    "live-recovery-heartbeat-v2"
)

LIVE_RECOVERY_HEARTBEAT_INTERVAL_SECONDS = 5.0


def _valid_authority_gate(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            LiveAuthorityGate,
        )
        and LIVE_AUTHORITY_GATE_VERSION
        == "live-authority-gate-v1"
    )


def _positive_finite_interval(
    value: Any,
) -> float | None:
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
        return None

    normalized = float(
        value
    )

    if (
        not math.isfinite(
            normalized
        )
        or normalized <= 0.0
    ):
        return None

    return normalized


def _valid_reasons(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            tuple,
        )
        and all(
            isinstance(
                reason,
                str,
            )
            and bool(
                reason,
            )
            for reason in value
        )
    )


def _valid_coordinator_result(
    result: Any,
) -> bool:
    return (
        getattr(
            result,
            "coordinator_version",
            None,
        )
        == LIVE_RECOVERY_COORDINATOR_VERSION
        and getattr(
            result,
            "status",
            None,
        )
        in (
            IDLE,
            ADVANCED,
            RECONCILED,
            HOLD,
            BLOCK,
            UNKNOWN,
        )
        and isinstance(
            getattr(
                result,
                "stage",
                None,
            ),
            str,
        )
        and bool(
            getattr(
                result,
                "stage",
                "",
            )
        )
        and _valid_reasons(
            getattr(
                result,
                "reasons",
                None,
            )
        )
    )


def _print_result(
    result: Any,
) -> None:
    status = getattr(
        result,
        "status",
        UNKNOWN,
    )

    if status == IDLE:
        return

    side = (
        getattr(
            result,
            "selected_side",
            None,
        )
        or "-"
    )

    state = (
        getattr(
            result,
            "selected_state",
            None,
        )
        or "-"
    )

    reasons = tuple(
        getattr(
            result,
            "reasons",
            (),
        )
        or ()
    )

    reason_text = (
        ",".join(
            reasons
        )
        if reasons
        else "-"
    )

    print(
        "🔁 LIVE RECOVERY | "
        f"status={status} | "
        f"side={side} | "
        f"state={state} | "
        f"reasons={reason_text}"
    )


async def run_live_recovery_heartbeat(
    *,
    allow_submission: bool,
    authority_gate: LiveAuthorityGate,
    db_path: Path = DB_PATH,
    interval_seconds: float = (
        LIVE_RECOVERY_HEARTBEAT_INTERVAL_SECONDS
    ),
) -> None:
    """
    Periodically advance at most one durable live execution
    obligation per heartbeat.

    Each iteration delegates exactly once to
    recover_one_live_obligation_once(), which itself selects
    at most one BUY or SELL recovery path.

    This service has no:
      - signer loading;
      - transaction construction;
      - signing;
      - submission authority of its own;
      - reconciliation authority of its own;
      - direct database mutation;
      - fresh-entry authority;
      - multi-obligation drain loop inside one heartbeat.

    asyncio.CancelledError always propagates so process
    shutdown cannot be swallowed.

    Unexpected coordinator exceptions are made visible and
    isolated so one software/runtime failure does not silently
    terminate future recovery heartbeats.
    """
    if not isinstance(
        allow_submission,
        bool,
    ):
        raise TypeError(
            "allow_submission must be bool"
        )

    if not _valid_authority_gate(
        authority_gate
    ):
        raise TypeError(
            "authority_gate must be LiveAuthorityGate"
        )

    interval = _positive_finite_interval(
        interval_seconds
    )

    if interval is None:
        raise ValueError(
            "interval_seconds must be positive and finite"
        )

    try:
        normalized_path = Path(
            db_path
        )
    except Exception:
        raise ValueError(
            "db_path is invalid"
        ) from None

    while True:
        started_at = time.monotonic()

        try:
            async with authority_gate:
                result = (
                    await recover_one_live_obligation_once(
                        allow_submission=(
                            allow_submission
                        ),
                        db_path=normalized_path,
                    )
                )

            if not _valid_coordinator_result(
                result
            ):
                print(
                    "⚠️ LIVE RECOVERY HEARTBEAT "
                    "CONTRACT ERROR"
                )

            else:
                _print_result(
                    result
                )

        except asyncio.CancelledError:
            raise

        except Exception as error:
            print(
                "⚠️ LIVE RECOVERY HEARTBEAT ERROR | "
                f"{type(error).__name__}: "
                f"{error}"
            )

        elapsed = (
            time.monotonic()
            - started_at
        )

        delay = max(
            0.1,
            interval
            - elapsed,
        )

        await asyncio.sleep(
            delay
        )
