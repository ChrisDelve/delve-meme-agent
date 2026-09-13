from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from src.execution.live_process_authority_lease import (
    LIVE_PROCESS_AUTHORITY_ALREADY_HELD,
    LIVE_PROCESS_AUTHORITY_LEASE_VERSION,
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
            "live-process-authority-lease-v1",
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


if __name__ == "__main__":
    unittest.main()
