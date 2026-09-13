from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from types import TracebackType
from typing import Any, Type

try:
    import fcntl
except ImportError:
    fcntl = None


LIVE_PROCESS_AUTHORITY_LEASE_VERSION = (
    "live-process-authority-lease-v2"
)

LIVE_PROCESS_AUTHORITY_ALREADY_HELD = (
    "LIVE_PROCESS_AUTHORITY_ALREADY_HELD"
)

LIVE_PROCESS_AUTHORITY_UNSUPPORTED = (
    "LIVE_PROCESS_AUTHORITY_UNSUPPORTED_PLATFORM"
)

LIVE_PROCESS_AUTHORITY_OPEN_FAILED = (
    "LIVE_PROCESS_AUTHORITY_OPEN_FAILED"
)

LIVE_PROCESS_AUTHORITY_LOCK_FAILED = (
    "LIVE_PROCESS_AUTHORITY_LOCK_FAILED"
)

LIVE_PROCESS_AUTHORITY_INVALID_LOCK_FILE = (
    "LIVE_PROCESS_AUTHORITY_INVALID_LOCK_FILE"
)


class LiveProcessAuthorityLeaseError(
    RuntimeError
):
    pass


def _normalize_lock_path(
    value: Any,
) -> Path:
    if (
        isinstance(
            value,
            str,
        )
        and (
            not value
            or value != value.strip()
        )
    ):
        raise ValueError(
            "lock_path is invalid"
        )

    try:
        path = Path(
            value
        )
    except Exception:
        raise ValueError(
            "lock_path is invalid"
        ) from None

    if str(
        path
    ) in (
        "",
        ".",
    ):
        raise ValueError(
            "lock_path is invalid"
        )

    return path


class LiveProcessAuthorityLease:
    """
    Same-host process-lifetime exclusivity for live authority.

    The lease uses a nonblocking kernel advisory file lock. The
    file itself is not the authority; the held kernel lock is.

    The lock file is deliberately NOT unlinked during release.
    Replacing the pathname with a new inode could allow two
    processes to believe they hold the same logical lock.

    This primitive owns no:
      - asyncio task or loop;
      - database authority;
      - signer or private-key authority;
      - recovery authority;
      - BUY or SELL authority;
      - transaction construction, signing, or submission.

    It protects only cooperating processes on the same host.
    """

    __slots__ = (
        "__fd",
        "__lock_path",
        "__released",
    )

    def __init__(
        self,
        *,
        lock_path: Path | str,
    ) -> None:
        path = _normalize_lock_path(
            lock_path
        )

        if (
            fcntl is None
            or not hasattr(
                fcntl,
                "flock",
            )
            or not hasattr(
                fcntl,
                "LOCK_EX",
            )
            or not hasattr(
                fcntl,
                "LOCK_NB",
            )
            or not hasattr(
                os,
                "O_NOFOLLOW",
            )
        ):
            raise LiveProcessAuthorityLeaseError(
                LIVE_PROCESS_AUTHORITY_UNSUPPORTED
            )

        try:
            existing = os.lstat(
                path
            )
        except FileNotFoundError:
            existing = None
        except OSError:
            raise LiveProcessAuthorityLeaseError(
                LIVE_PROCESS_AUTHORITY_OPEN_FAILED
            ) from None

        if existing is not None:
            if (
                stat.S_ISLNK(
                    existing.st_mode
                )
                or not stat.S_ISREG(
                    existing.st_mode
                )
                or existing.st_nlink != 1
                or existing.st_uid
                != os.geteuid()
            ):
                raise LiveProcessAuthorityLeaseError(
                    LIVE_PROCESS_AUTHORITY_INVALID_LOCK_FILE
                )

        flags = (
            os.O_RDWR
            | os.O_CREAT
            | os.O_NOFOLLOW
        )

        if hasattr(
            os,
            "O_CLOEXEC",
        ):
            flags |= os.O_CLOEXEC

        try:
            fd = os.open(
                path,
                flags,
                0o600,
            )
        except OSError as error:
            if error.errno in (
                errno.ELOOP,
                errno.EISDIR,
                errno.ENOTDIR,
            ):
                raise LiveProcessAuthorityLeaseError(
                    LIVE_PROCESS_AUTHORITY_INVALID_LOCK_FILE
                ) from None

            raise LiveProcessAuthorityLeaseError(
                LIVE_PROCESS_AUTHORITY_OPEN_FAILED
            ) from None

        try:
            os.set_inheritable(
                fd,
                False,
            )

            try:
                opened = os.fstat(
                    fd
                )
            except OSError:
                raise LiveProcessAuthorityLeaseError(
                    LIVE_PROCESS_AUTHORITY_INVALID_LOCK_FILE
                ) from None

            if (
                not stat.S_ISREG(
                    opened.st_mode
                )
                or opened.st_nlink != 1
                or opened.st_uid
                != os.geteuid()
            ):
                raise LiveProcessAuthorityLeaseError(
                    LIVE_PROCESS_AUTHORITY_INVALID_LOCK_FILE
                )

            try:
                current = os.lstat(
                    path
                )
            except OSError:
                raise LiveProcessAuthorityLeaseError(
                    LIVE_PROCESS_AUTHORITY_INVALID_LOCK_FILE
                ) from None

            if (
                stat.S_ISLNK(
                    current.st_mode
                )
                or not stat.S_ISREG(
                    current.st_mode
                )
                or current.st_nlink != 1
                or current.st_uid
                != os.geteuid()
                or current.st_dev
                != opened.st_dev
                or current.st_ino
                != opened.st_ino
            ):
                raise LiveProcessAuthorityLeaseError(
                    LIVE_PROCESS_AUTHORITY_INVALID_LOCK_FILE
                )

            try:
                os.fchmod(
                    fd,
                    0o600,
                )
            except OSError:
                raise LiveProcessAuthorityLeaseError(
                    LIVE_PROCESS_AUTHORITY_INVALID_LOCK_FILE
                ) from None

            try:
                fcntl.flock(
                    fd,
                    (
                        fcntl.LOCK_EX
                        | fcntl.LOCK_NB
                    ),
                )
            except OSError as error:
                if error.errno in (
                    errno.EACCES,
                    errno.EAGAIN,
                ):
                    raise LiveProcessAuthorityLeaseError(
                        LIVE_PROCESS_AUTHORITY_ALREADY_HELD
                    ) from None

                raise LiveProcessAuthorityLeaseError(
                    LIVE_PROCESS_AUTHORITY_LOCK_FAILED
                ) from None

        except BaseException:
            os.close(
                fd
            )
            raise

        self.__fd = fd
        self.__lock_path = path
        self.__released = False

    @property
    def lock_path(
        self,
    ) -> Path:
        return self.__lock_path

    @property
    def released(
        self,
    ) -> bool:
        return self.__released

    def release(
        self,
    ) -> None:
        if self.__released:
            return

        fd = self.__fd

        self.__released = True

        try:
            fcntl.flock(
                fd,
                fcntl.LOCK_UN,
            )
        finally:
            os.close(
                fd
            )

    def __enter__(
        self,
    ) -> LiveProcessAuthorityLease:
        if self.__released:
            raise LiveProcessAuthorityLeaseError(
                "LIVE_PROCESS_AUTHORITY_LEASE_RELEASED"
            )

        return self

    def __exit__(
        self,
        exc_type: Type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.release()
        return False
