from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Type


LIVE_AUTHORITY_GATE_VERSION = (
    "live-authority-gate-v1"
)


class LiveAuthorityGate:
    """
    Process-local serialization primitive for live-capital authority.

    A single instance is intended to be shared by the production
    recovery heartbeat and production one-shot BUY/SELL composition.

    The gate owns no trading authority itself. It does not:
      - inspect candidates;
      - access SQLite;
      - load or retain a signer;
      - construct, sign, submit, or reconcile transactions;
      - start tasks or loops;
      - retry work.

    It is intentionally non-reentrant. Authority owners must acquire
    it only at their outer execution boundary.
    """

    __slots__ = (
        "__lock",
    )

    def __init__(
        self,
    ) -> None:
        self.__lock = asyncio.Lock()

    async def __aenter__(
        self,
    ) -> LiveAuthorityGate:
        await self.__lock.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: Type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.__lock.release()
        return False
