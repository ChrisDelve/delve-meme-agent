from __future__ import annotations

import asyncio
from pathlib import Path

from src.portfolio.shadow_portfolio import (
    DB_PATH,
    list_open_shadow_mints,
)


#
# Shared provisional shadow exit execution
# assumptions.
#
SHADOW_EXIT_SLIPPAGE_BPS = 300
SHADOW_EXIT_BASE_NETWORK_FEE_LAMPORTS = 5_000
SHADOW_EXIT_PRIORITY_FEE_LAMPORTS = 0


_open_mints: set[str] | None = None
_registry_db_path: Path | None = None

_mint_locks: dict[
    str,
    asyncio.Lock,
] = {}


def initialize_shadow_position_manager(
    *,
    db_path: Path = DB_PATH,
) -> set[str]:
    global _open_mints
    global _registry_db_path

    normalized_path = Path(
        db_path
    )

    _open_mints = (
        list_open_shadow_mints(
            db_path=normalized_path
        )
    )

    _registry_db_path = (
        normalized_path
    )

    return set(
        _open_mints
    )


def _ensure_initialized(
    *,
    db_path: Path = DB_PATH,
) -> None:
    global _registry_db_path

    normalized_path = Path(
        db_path
    )

    if _open_mints is None:
        initialize_shadow_position_manager(
            db_path=normalized_path
        )
        return

    if (
        _registry_db_path
        != normalized_path
    ):
        raise RuntimeError(
            "Shadow position manager is "
            "already initialized for a "
            "different database path."
        )


def register_open_shadow_mint(
    mint: str,
    *,
    db_path: Path = DB_PATH,
) -> None:
    _ensure_initialized(
        db_path=db_path
    )

    assert _open_mints is not None

    _open_mints.add(
        mint
    )


def unregister_open_shadow_mint(
    mint: str,
    *,
    db_path: Path = DB_PATH,
) -> None:
    _ensure_initialized(
        db_path=db_path
    )

    assert _open_mints is not None

    _open_mints.discard(
        mint
    )


def is_open_shadow_mint_tracked(
    mint: str,
    *,
    db_path: Path = DB_PATH,
) -> bool:
    _ensure_initialized(
        db_path=db_path
    )

    assert _open_mints is not None

    return mint in _open_mints


def _get_mint_lock(
    mint: str,
) -> asyncio.Lock:
    lock = _mint_locks.get(
        mint
    )

    if lock is None:
        lock = asyncio.Lock()

        _mint_locks[
            mint
        ] = lock

    return lock


def get_shadow_position_lock(
    mint: str,
) -> asyncio.Lock:
    return _get_mint_lock(
        mint
    )
