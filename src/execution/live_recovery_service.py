from __future__ import annotations

from typing import Any

from src.execution.live_authority_gate import (
    LIVE_AUTHORITY_GATE_VERSION,
    LiveAuthorityGate,
)
from src.execution.live_operating_config import (
    LIVE_OPERATING_CONFIG_VERSION,
    LiveOperatingConfig,
)
from src.execution.live_recovery_heartbeat import (
    run_live_recovery_heartbeat,
)


LIVE_RECOVERY_SERVICE_VERSION = (
    "live-recovery-service-v2"
)


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


def _valid_config(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            LiveOperatingConfig,
        )
        and LIVE_OPERATING_CONFIG_VERSION
        == "live-operating-config-v1"
    )


async def run_live_recovery_service(
    *,
    config: LiveOperatingConfig,
    authority_gate: LiveAuthorityGate,
) -> None:
    """
    Canonical long-running recovery-service composition boundary.

    Authority is inherited exclusively from LiveOperatingConfig:

        operational_kill=False
            -> recovery_allow_submission=True

        operational_kill=True
            -> recovery_allow_submission=False

    This service deliberately does not:
      - load environment variables;
      - load or retain a signer;
      - know private-key configuration;
      - construct/sign/submit transactions directly;
      - access SQLite directly;
      - create background tasks;
      - run a second scheduling loop;
      - own fresh BUY/SELL entry authority.

    The heartbeat remains the sole periodic loop.
    """
    if not _valid_config(
        config
    ):
        raise TypeError(
            "config must be LiveOperatingConfig"
        )

    if not _valid_authority_gate(
        authority_gate
    ):
        raise TypeError(
            "authority_gate must be LiveAuthorityGate"
        )

    await run_live_recovery_heartbeat(
        allow_submission=(
            config.recovery_allow_submission
        ),
        authority_gate=authority_gate,
        db_path=config.db_path,
        interval_seconds=(
            config.recovery_interval_seconds
        ),
    )
