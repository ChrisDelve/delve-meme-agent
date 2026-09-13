from __future__ import annotations

from typing import Any

from src.execution.live_authority_gate import (
    LIVE_AUTHORITY_GATE_VERSION,
    LiveAuthorityGate,
)
from src.execution.live_execution_composition import (
    run_production_live_buy_once,
    run_production_live_sell_once,
)
from src.execution.live_operating_config import (
    LIVE_OPERATING_CONFIG_VERSION,
    LiveOperatingConfig,
)
from src.execution.live_process_authority_lease import (
    LIVE_PROCESS_AUTHORITY_LEASE_VERSION,
    LiveProcessAuthorityLease,
)
from src.execution.live_process_authority_policy import (
    LIVE_PROCESS_AUTHORITY_POLICY_VERSION,
    live_process_authority_lock_path,
)
from src.execution.live_recovery_service import (
    run_live_recovery_service,
)


LIVE_PROCESS_OWNER_VERSION = (
    "live-process-owner-v1"
)

LIVE_PROCESS_OWNER_CLOSED = (
    "LIVE_PROCESS_OWNER_CLOSED"
)

LIVE_PROCESS_OWNER_BUSY = (
    "LIVE_PROCESS_OWNER_BUSY"
)


class LiveProcessOwnerError(
    RuntimeError
):
    pass


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


def _components_are_compatible(
) -> bool:
    return (
        LIVE_AUTHORITY_GATE_VERSION
        == "live-authority-gate-v1"
        and LIVE_PROCESS_AUTHORITY_LEASE_VERSION
        == "live-process-authority-lease-v2"
        and LIVE_PROCESS_AUTHORITY_POLICY_VERSION
        == "live-process-authority-policy-v1"
    )


class LiveProcessOwner:
    """
    Process-lifetime live-authority composition boundary.

    Construction ordering is deliberate:

        validate non-secret operating config
            ↓
        resolve canonical same-host authority domain
            ↓
        acquire LiveProcessAuthorityLease
            ↓
        construct exactly one LiveAuthorityGate
            ↓
        permit recovery / BUY / SELL composition

    The process lease is held for the entire usable lifetime of
    this owner.

    close() fails closed while an authority call is active. The
    caller must cancel/await long-running recovery before closing
    the owner. This prevents the host-global lease from being
    released while live authority is still executing.

    This owner deliberately does not:
      - load environment variables;
      - load or retain a signer;
      - create asyncio tasks;
      - own another scheduling loop;
      - access SQLite directly;
      - construct/sign/submit transactions directly;
      - duplicate recovery, BUY, or SELL policy.
    """

    __slots__ = (
        "__active_calls",
        "__authority_gate",
        "__closed",
        "__config",
        "__lease",
    )

    def __init__(
        self,
        *,
        config: LiveOperatingConfig,
    ) -> None:
        if not _valid_config(
            config
        ):
            raise TypeError(
                "config must be LiveOperatingConfig"
            )

        if not _components_are_compatible():
            raise LiveProcessOwnerError(
                "LIVE_PROCESS_OWNER_COMPONENT_VERSION_MISMATCH"
            )

        lock_path = (
            live_process_authority_lock_path()
        )

        lease = LiveProcessAuthorityLease(
            lock_path=lock_path
        )

        try:
            authority_gate = LiveAuthorityGate()
        except BaseException:
            lease.release()
            raise

        self.__config = config
        self.__lease = lease
        self.__authority_gate = authority_gate
        self.__active_calls = 0
        self.__closed = False

    @property
    def closed(
        self,
    ) -> bool:
        return self.__closed

    def _begin_authority_call(
        self,
    ) -> None:
        if self.__closed:
            raise LiveProcessOwnerError(
                LIVE_PROCESS_OWNER_CLOSED
            )

        self.__active_calls += 1

    def _end_authority_call(
        self,
    ) -> None:
        self.__active_calls -= 1

        if self.__active_calls < 0:
            self.__active_calls = 0
            self.__closed = True

            raise LiveProcessOwnerError(
                "LIVE_PROCESS_OWNER_ACTIVE_CALL_UNDERFLOW"
            )

    async def run_recovery_service(
        self,
    ) -> None:
        self._begin_authority_call()

        try:
            await run_live_recovery_service(
                config=self.__config,
                authority_gate=(
                    self.__authority_gate
                ),
            )
        finally:
            self._end_authority_call()

    async def run_buy_once(
        self,
        **runtime_inputs: Any,
    ) -> Any:
        self._begin_authority_call()

        try:
            return await run_production_live_buy_once(
                config=self.__config,
                authority_gate=(
                    self.__authority_gate
                ),
                **runtime_inputs,
            )
        finally:
            self._end_authority_call()

    async def run_sell_once(
        self,
        **runtime_inputs: Any,
    ) -> Any:
        self._begin_authority_call()

        try:
            return await run_production_live_sell_once(
                config=self.__config,
                authority_gate=(
                    self.__authority_gate
                ),
                **runtime_inputs,
            )
        finally:
            self._end_authority_call()

    def close(
        self,
    ) -> None:
        if self.__closed:
            return

        if self.__active_calls != 0:
            raise LiveProcessOwnerError(
                LIVE_PROCESS_OWNER_BUSY
            )

        # Mark closed before releasing the lease. Even if lease
        # release itself raises, this owner must never become
        # usable again without process authority.
        self.__closed = True

        self.__lease.release()

    def __enter__(
        self,
    ) -> LiveProcessOwner:
        if self.__closed:
            raise LiveProcessOwnerError(
                LIVE_PROCESS_OWNER_CLOSED
            )

        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> bool:
        self.close()
        return False
