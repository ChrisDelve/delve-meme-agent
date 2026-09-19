from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.execution.live_buy_execution_config import (
    LiveBuyExecutionConfig,
)
from src.execution.live_operating_config import (
    LiveOperatingConfig,
)
from src.execution.solana_message_signer import (
    LazyEnvironmentMessageSigner,
)
from src.portfolio.live_reservations import (
    get_connection,
)


LIVE_STARTUP_PREFLIGHT_VERSION = (
    "live-startup-preflight-v1"
)

SOLANA_MAINNET_GENESIS_HASH = (
    "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"
)

LIVE_STARTUP_PREFLIGHT_SIGNER_UNAVAILABLE = (
    "LIVE_STARTUP_PREFLIGHT_SIGNER_UNAVAILABLE"
)

LIVE_STARTUP_PREFLIGHT_SIGNER_WALLET_MISMATCH = (
    "LIVE_STARTUP_PREFLIGHT_SIGNER_WALLET_MISMATCH"
)

LIVE_STARTUP_PREFLIGHT_DATABASE_UNAVAILABLE = (
    "LIVE_STARTUP_PREFLIGHT_DATABASE_UNAVAILABLE"
)

LIVE_STARTUP_PREFLIGHT_DATABASE_INTEGRITY_FAILED = (
    "LIVE_STARTUP_PREFLIGHT_DATABASE_INTEGRITY_FAILED"
)

LIVE_STARTUP_PREFLIGHT_RPC_UNAVAILABLE = (
    "LIVE_STARTUP_PREFLIGHT_RPC_UNAVAILABLE"
)

LIVE_STARTUP_PREFLIGHT_RPC_CLUSTER_MISMATCH = (
    "LIVE_STARTUP_PREFLIGHT_RPC_CLUSTER_MISMATCH"
)

LIVE_STARTUP_PREFLIGHT_RPC_RESULT_INVALID = (
    "LIVE_STARTUP_PREFLIGHT_RPC_RESULT_INVALID"
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

    wallet_pubkey: str
    database_path: Path

    operational_kill: bool

    signer_checked: bool
    signer_pubkey: str | None

    rpc_genesis_hash: str
    wallet_balance_lamports: int
    rpc_slot: int


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
    """
    connection: sqlite3.Connection | None = None

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


async def _resolve_rpc_probe(
    *,
    wallet_pubkey: str,
) -> tuple[str, Any]:
    """
    Resolve network identity and one authoritative wallet balance.

    Imports are intentionally local. The existing Helius stack reads
    its RPC environment at module import time, so startup configuration
    must have populated the environment before this function is called.
    """
    from src.execution.live_wallet_balance import (
        resolve_live_wallet_balance,
    )
    from src.safety.token_safety_resolver import (
        HeliusRpcClient,
    )

    async with HeliusRpcClient() as rpc:
        genesis_hash = await rpc.call(
            "getGenesisHash",
            [],
        )

    balance_result = (
        await resolve_live_wallet_balance(
            wallet_pubkey=wallet_pubkey
        )
    )

    return (
        genesis_hash,
        balance_result,
    )


def _valid_nonnegative_int(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            int,
        )
        and not isinstance(
            value,
            bool,
        )
        and value >= 0
    )


async def run_live_startup_preflight(
    *,
    operating_config: LiveOperatingConfig,
    execution_config: LiveBuyExecutionConfig,
) -> LiveStartupPreflightResult:
    """
    Fail-closed startup proof before the unified live process begins.

    Authority deliberately NOT owned here:
      - no LiveProcessOwner;
      - no process lease acquisition;
      - no transaction construction;
      - no signing;
      - no submission;
      - no BUY/SELL/recovery authority;
      - no trading-schema initialization.

    Order:
        validated configs
            ↓
        signer identity when operational kill is OFF
            ↓
        create/open + integrity/write-lock proof of live DB
            ↓
        exact Solana mainnet genesis proof
            ↓
        exact configured-wallet balance RPC proof
            ↓
        return immutable startup evidence

    When operational kill is ON, signer configuration is intentionally
    untouched so a dry/shadow launch preserves lazy secret access.
    """
    if not isinstance(
        operating_config,
        LiveOperatingConfig,
    ):
        raise TypeError(
            "operating_config must be LiveOperatingConfig"
        )

    if not isinstance(
        execution_config,
        LiveBuyExecutionConfig,
    ):
        raise TypeError(
            "execution_config must be LiveBuyExecutionConfig"
        )

    wallet_pubkey = (
        execution_config.wallet_pubkey.strip()
    )

    signer_checked = False
    signer_pubkey: str | None = None

    if not operating_config.operational_kill:
        signer = (
            LazyEnvironmentMessageSigner()
        )

        try:
            signer_pubkey = str(
                signer.pubkey()
            )

        except Exception:
            raise LiveStartupPreflightError(
                LIVE_STARTUP_PREFLIGHT_SIGNER_UNAVAILABLE
            ) from None

        signer_checked = True

        if signer_pubkey != wallet_pubkey:
            raise LiveStartupPreflightError(
                LIVE_STARTUP_PREFLIGHT_SIGNER_WALLET_MISMATCH
            )

    database_path = (
        _probe_live_database(
            operating_config.db_path
        )
    )

    try:
        (
            genesis_hash,
            balance_result,
        ) = await _resolve_rpc_probe(
            wallet_pubkey=wallet_pubkey
        )

    except Exception:
        raise LiveStartupPreflightError(
            LIVE_STARTUP_PREFLIGHT_RPC_UNAVAILABLE
        ) from None

    if (
        not isinstance(
            genesis_hash,
            str,
        )
        or genesis_hash.strip()
        != SOLANA_MAINNET_GENESIS_HASH
    ):
        raise LiveStartupPreflightError(
            LIVE_STARTUP_PREFLIGHT_RPC_CLUSTER_MISMATCH
        )

    status = getattr(
        balance_result,
        "status",
        None,
    )

    reasons = getattr(
        balance_result,
        "reasons",
        None,
    )

    resolved_wallet = getattr(
        balance_result,
        "wallet_pubkey",
        None,
    )

    balance_lamports = getattr(
        balance_result,
        "balance_lamports",
        None,
    )

    rpc_slot = getattr(
        balance_result,
        "rpc_slot",
        None,
    )

    if (
        status != "RESOLVED"
        or reasons != ()
        or resolved_wallet != wallet_pubkey
        or not _valid_nonnegative_int(
            balance_lamports
        )
        or not _valid_nonnegative_int(
            rpc_slot
        )
    ):
        raise LiveStartupPreflightError(
            LIVE_STARTUP_PREFLIGHT_RPC_RESULT_INVALID
        )

    return LiveStartupPreflightResult(
        preflight_version=(
            LIVE_STARTUP_PREFLIGHT_VERSION
        ),
        wallet_pubkey=wallet_pubkey,
        database_path=database_path,
        operational_kill=(
            operating_config.operational_kill
        ),
        signer_checked=signer_checked,
        signer_pubkey=signer_pubkey,
        rpc_genesis_hash=(
            SOLANA_MAINNET_GENESIS_HASH
        ),
        wallet_balance_lamports=(
            balance_lamports
        ),
        rpc_slot=rpc_slot,
    )
