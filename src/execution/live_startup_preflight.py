from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.execution.live_operating_config import (
    LIVE_OPERATING_CONFIG_VERSION,
    LiveOperatingConfig,
)
from src.portfolio.live_reservations import (
    get_connection,
)


LIVE_STARTUP_PREFLIGHT_VERSION = (
    "live-startup-preflight-v2"
)

LIVE_STARTUP_PREFLIGHT_DATABASE_UNAVAILABLE = (
    "LIVE_STARTUP_PREFLIGHT_DATABASE_UNAVAILABLE"
)

LIVE_STARTUP_PREFLIGHT_DATABASE_INTEGRITY_FAILED = (
    "LIVE_STARTUP_PREFLIGHT_DATABASE_INTEGRITY_FAILED"
)


class LiveStartupPreflightError(
    RuntimeError
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class LiveStartupPreflightResult:
    preflight_version: str
    database_path: Path


def _valid_operating_config(
    value: object,
) -> bool:
    return (
        isinstance(
            value,
            LiveOperatingConfig,
        )
        and LIVE_OPERATING_CONFIG_VERSION
        == "live-operating-config-v1"
    )


def _probe_live_database(
    db_path: Path,
) -> Path:
    """
    Prove that the canonical live SQLite location is usable.

    This function deliberately creates/opens only the database file.
    It does NOT initialize trading schemas.

    BEGIN IMMEDIATE proves SQLite write-lock authority without
    committing any durable trading state. PRAGMA quick_check proves
    basic database integrity. The transaction is always rolled back.

    This is the only startup prerequisite enforced before the unified
    live process is allowed to begin recovery.
    """

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

        rows = connection.execute(
            "PRAGMA quick_check"
        ).fetchall()

        if (
            len(rows) != 1
            or str(
                rows[0][0]
            ).strip().lower()
            != "ok"
        ):
            raise LiveStartupPreflightError(
                LIVE_STARTUP_PREFLIGHT_DATABASE_INTEGRITY_FAILED
            )

        connection.rollback()

    except LiveStartupPreflightError:
        raise

    except Exception:
        raise LiveStartupPreflightError(
            LIVE_STARTUP_PREFLIGHT_DATABASE_UNAVAILABLE
        ) from None

    finally:
        if connection is not None:
            try:
                if connection.in_transaction:
                    connection.rollback()

            finally:
                connection.close()

    try:
        normalized = Path(
            db_path
        ).resolve()

        if (
            not normalized.exists()
            or not normalized.is_file()
        ):
            raise LiveStartupPreflightError(
                LIVE_STARTUP_PREFLIGHT_DATABASE_UNAVAILABLE
            )

    except LiveStartupPreflightError:
        raise

    except Exception:
        raise LiveStartupPreflightError(
            LIVE_STARTUP_PREFLIGHT_DATABASE_UNAVAILABLE
        ) from None

    return normalized


async def run_live_startup_preflight(
    *,
    operating_config: LiveOperatingConfig,
) -> LiveStartupPreflightResult:
    """
    Prove only the structural startup prerequisite required for
    recovery to operate: a usable canonical live database.

    Recovery-first invariant:

        validate operating configuration
            ↓
        create/open + integrity/write-lock proof of live DB
            ↓
        unified process begins
            ↓
        durable BUY/SELL recovery receives first authority
            ↓
        fresh-capital signer/RPC/wallet requirements remain owned
        by their existing recovery-first runtime layers

    This boundary deliberately does NOT:
      - load or inspect signer/private-key configuration;
      - resolve wallet balances;
      - create an RPC client;
      - prove network identity;
      - acquire LiveProcessOwner or the process lease;
      - construct/sign/submit transactions;
      - exercise BUY/SELL/recovery authority;
      - initialize trading schemas.

    A signer, RPC provider, or fresh-capital prerequisite failure must
    never prevent the process from reaching durable recovery.
    """

    if not _valid_operating_config(
        operating_config
    ):
        raise TypeError(
            "operating_config must be "
            "LiveOperatingConfig"
        )

    database_path = (
        _probe_live_database(
            operating_config.db_path
        )
    )

    return LiveStartupPreflightResult(
        preflight_version=(
            LIVE_STARTUP_PREFLIGHT_VERSION
        ),
        database_path=database_path,
    )
