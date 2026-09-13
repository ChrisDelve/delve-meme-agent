from __future__ import annotations

from typing import Any

from src.execution.live_operating_config import (
    LIVE_OPERATING_CONFIG_VERSION,
    LiveOperatingConfig,
)
from src.execution.live_recovery_heartbeat import (
    run_live_recovery_heartbeat,
)


LIVE_RECOVERY_SERVICE_VERSION = (
    "live-recovery-service-v1"
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

    await run_live_recovery_heartbeat(
        allow_submission=(
            config.recovery_allow_submission
        ),
        db_path=config.db_path,
        interval_seconds=(
            config.recovery_interval_seconds
        ),
    )
