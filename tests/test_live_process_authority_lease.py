from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.execution.live_process_authority_lease import (
    LIVE_PROCESS_AUTHORITY_ALREADY_HELD,
    LIVE_PROCESS_AUTHORITY_INVALID_LOCK_FILE,
    LIVE_PROCESS_AUTHORITY_LEASE_VERSION,
    LIVE_PROCESS_AUTHORITY_UNSUPPORTED,
    LiveProcessAuthorityLease,
    LiveProcessAuthorityLeaseError,
)


CHILD_TRY_ACQUIRE = r'''
import sys
from pathlib import Path

from src.execution.live_process_authority_lease import (
    LiveProcessAuthorityLease,
    LiveProcessAuthorityLeaseError,
)

path = Path(sys.argv[1])

try:
    lease = LiveProcessAuthorityLease(
        lock_path=path
    )
except LiveProcessAuthorityLeaseError as error:
    print(
        str(error),
        flush=True,
    )
    raise SystemExit(23)

print(
    "ACQUIRED",
    flush=True,
)

lease.release()
'''


CHILD_ACQUIRE_AND_EXIT_ABRUPTLY = r'''
import os
import sys
from pathlib import Path

from src.execution.live_process_authority_lease import (
    LiveProcessAuthorityLease,
)

lease = LiveProcessAuthorityLease(
    lock_path=Path(sys.argv[1])
)

print(
    "ACQUIRED",
    flush=True,
)

os._exit(17)
'''


class LiveProcessAuthorityLeaseTests(
    unittest.TestCase
):
    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_PROCESS_AUTHORITY_LEASE_VERSION,
            "live-process-authority-lease-v2",
        )

    def test_invalid_lock_path_fails_closed(
        self,
    ):
        for invalid in (
            "",
            "   ",
            object(),
        ):
            with self.subTest(
                invalid=invalid
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "^lock_path is invalid$",
                ):
                    LiveProcessAuthorityLease(
                        lock_path=invalid
                    )

    def test_context_manager_releases_and_file_persists(
        self,
    ):
        with TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "authority.lock"
            )

            with LiveProcessAuthorityLease(
                lock_path=path
            ) as lease:
                self.assertEqual(
                    lease.lock_path,
                    path,
                )
                self.assertFalse(
                    lease.released
                )
                self.assertTrue(
                    path.exists()
                )

            self.assertTrue(
                lease.released
            )

            self.assertTrue(
                path.exists()
            )

            lease.release()

            self.assertTrue(
                lease.released
            )

    def test_second_process_fails_while_lease_is_held(
        self,
    ):
        with TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "authority.lock"
            )

            with LiveProcessAuthorityLease(
                lock_path=path
            ):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        CHILD_TRY_ACQUIRE,
                        str(path),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )

            self.assertEqual(
                result.returncode,
                23,
            )

            self.assertEqual(
                result.stdout.strip(),
                LIVE_PROCESS_AUTHORITY_ALREADY_HELD,
            )

    def test_second_process_acquires_after_release(
        self,
    ):
        with TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "authority.lock"
            )

            lease = LiveProcessAuthorityLease(
                lock_path=path
            )

            lease.release()

            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    CHILD_TRY_ACQUIRE,
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(
                result.returncode,
                0,
            )

            self.assertEqual(
                result.stdout.strip(),
                "ACQUIRED",
            )

    def test_abrupt_process_exit_releases_kernel_lock(
        self,
    ):
        with TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "authority.lock"
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    CHILD_ACQUIRE_AND_EXIT_ABRUPTLY,
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            self.assertEqual(
                result.returncode,
                17,
            )

            self.assertEqual(
                result.stdout.strip(),
                "ACQUIRED",
            )

            with LiveProcessAuthorityLease(
                lock_path=path
            ) as lease:
                self.assertFalse(
                    lease.released
                )

    def test_symlink_lock_path_is_rejected_without_touching_target(
        self,
    ):
        with TemporaryDirectory() as directory:
            root = Path(
                directory
            )

            target = (
                root
                / "target.txt"
            )

            target.write_text(
                "do-not-touch",
                encoding="utf-8",
            )

            lock_path = (
                root
                / "authority.lock"
            )

            lock_path.symlink_to(
                target
            )

            with self.assertRaisesRegex(
                LiveProcessAuthorityLeaseError,
                (
                    "^"
                    + LIVE_PROCESS_AUTHORITY_INVALID_LOCK_FILE
                    + "$"
                ),
            ):
                LiveProcessAuthorityLease(
                    lock_path=lock_path
                )

            self.assertEqual(
                target.read_text(
                    encoding="utf-8"
                ),
                "do-not-touch",
            )

    def test_directory_lock_path_is_rejected(
        self,
    ):
        with TemporaryDirectory() as directory:
            lock_path = (
                Path(directory)
                / "authority.lock"
            )

            lock_path.mkdir()

            with self.assertRaisesRegex(
                LiveProcessAuthorityLeaseError,
                (
                    "^"
                    + LIVE_PROCESS_AUTHORITY_INVALID_LOCK_FILE
                    + "$"
                ),
            ):
                LiveProcessAuthorityLease(
                    lock_path=lock_path
                )

    @unittest.skipUnless(
        hasattr(
            os,
            "mkfifo",
        ),
        "mkfifo unavailable",
    )
    def test_fifo_lock_path_is_rejected_before_open(
        self,
    ):
        with TemporaryDirectory() as directory:
            lock_path = (
                Path(directory)
                / "authority.lock"
            )

            os.mkfifo(
                lock_path,
                0o600,
            )

            with self.assertRaisesRegex(
                LiveProcessAuthorityLeaseError,
                (
                    "^"
                    + LIVE_PROCESS_AUTHORITY_INVALID_LOCK_FILE
                    + "$"
                ),
            ):
                LiveProcessAuthorityLease(
                    lock_path=lock_path
                )

    def test_hardlinked_lock_path_is_rejected(
        self,
    ):
        with TemporaryDirectory() as directory:
            root = Path(
                directory
            )

            original = (
                root
                / "original.lock"
            )

            original.write_text(
                "",
                encoding="utf-8",
            )

            lock_path = (
                root
                / "authority.lock"
            )

            os.link(
                original,
                lock_path,
            )

            self.assertGreater(
                lock_path.stat().st_nlink,
                1,
            )

            with self.assertRaisesRegex(
                LiveProcessAuthorityLeaseError,
                (
                    "^"
                    + LIVE_PROCESS_AUTHORITY_INVALID_LOCK_FILE
                    + "$"
                ),
            ):
                LiveProcessAuthorityLease(
                    lock_path=lock_path
                )

    def test_preexisting_regular_file_is_hardened_to_owner_only(
        self,
    ):
        with TemporaryDirectory() as directory:
            lock_path = (
                Path(directory)
                / "authority.lock"
            )

            lock_path.write_text(
                "",
                encoding="utf-8",
            )

            lock_path.chmod(
                0o666
            )

            with LiveProcessAuthorityLease(
                lock_path=lock_path
            ):
                mode = stat.S_IMODE(
                    lock_path.stat().st_mode
                )

                self.assertEqual(
                    mode,
                    0o600,
                )

    def test_missing_o_nofollow_fails_closed_before_file_creation(
        self,
    ):
        with TemporaryDirectory() as directory:
            lock_path = (
                Path(directory)
                / "authority.lock"
            )

            original = os.O_NOFOLLOW

            try:
                delattr(
                    os,
                    "O_NOFOLLOW",
                )

                with self.assertRaisesRegex(
                    LiveProcessAuthorityLeaseError,
                    (
                        "^"
                        + LIVE_PROCESS_AUTHORITY_UNSUPPORTED
                        + "$"
                    ),
                ):
                    LiveProcessAuthorityLease(
                        lock_path=lock_path
                    )
            finally:
                os.O_NOFOLLOW = original

            self.assertFalse(
                lock_path.exists()
            )

    def test_pathname_inode_mismatch_fails_closed_and_closes_fd(
        self,
    ):
        with TemporaryDirectory() as directory:
            lock_path = (
                Path(directory)
                / "authority.lock"
            )

            mismatched_path_stat = SimpleNamespace(
                st_mode=(
                    stat.S_IFREG
                    | 0o600
                ),
                st_nlink=1,
                st_uid=os.geteuid(),
                st_dev=-1,
                st_ino=-1,
            )

            with patch(
                (
                    "src.execution."
                    "live_process_authority_lease."
                    "os.lstat"
                ),
                side_effect=[
                    FileNotFoundError(),
                    mismatched_path_stat,
                ],
            ):
                with self.assertRaisesRegex(
                    LiveProcessAuthorityLeaseError,
                    (
                        "^"
                        + LIVE_PROCESS_AUTHORITY_INVALID_LOCK_FILE
                        + "$"
                    ),
                ):
                    LiveProcessAuthorityLease(
                        lock_path=lock_path
                    )

            with LiveProcessAuthorityLease(
                lock_path=lock_path
            ) as lease:
                self.assertFalse(
                    lease.released
                )


if __name__ == "__main__":
    unittest.main()
