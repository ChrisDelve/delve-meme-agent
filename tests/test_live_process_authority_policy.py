from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.execution.live_process_authority_policy import (
    LIVE_PROCESS_AUTHORITY_LOCK_PATH,
    LIVE_PROCESS_AUTHORITY_POLICY_VERSION,
    live_process_authority_lock_path,
)


class LiveProcessAuthorityPolicyTests(
    unittest.TestCase
):
    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_PROCESS_AUTHORITY_POLICY_VERSION,
            "live-process-authority-policy-v1",
        )

    def test_lock_path_is_exact_and_absolute(
        self,
    ):
        self.assertEqual(
            LIVE_PROCESS_AUTHORITY_LOCK_PATH,
            Path(
                "/tmp/"
                "delve-meme-agent-live-authority.lock"
            ),
        )

        self.assertTrue(
            LIVE_PROCESS_AUTHORITY_LOCK_PATH.is_absolute()
        )

    def test_function_returns_canonical_path(
        self,
    ):
        self.assertEqual(
            live_process_authority_lock_path(),
            LIVE_PROCESS_AUTHORITY_LOCK_PATH,
        )

    def test_working_directory_cannot_change_authority_domain(
        self,
    ):
        original = Path.cwd()

        with TemporaryDirectory() as directory:
            try:
                os.chdir(
                    directory
                )

                observed = (
                    live_process_authority_lock_path()
                )
            finally:
                os.chdir(
                    original
                )

        self.assertEqual(
            observed,
            LIVE_PROCESS_AUTHORITY_LOCK_PATH,
        )

    def test_process_environment_cannot_change_authority_domain(
        self,
    ):
        key = (
            "DELVE_LIVE_PROCESS_AUTHORITY_LOCK_PATH"
        )

        original = os.environ.get(
            key
        )

        try:
            os.environ[
                key
            ] = "/tmp/alternate-authority.lock"

            observed = (
                live_process_authority_lock_path()
            )
        finally:
            if original is None:
                os.environ.pop(
                    key,
                    None,
                )
            else:
                os.environ[
                    key
                ] = original

        self.assertEqual(
            observed,
            LIVE_PROCESS_AUTHORITY_LOCK_PATH,
        )


if __name__ == "__main__":
    unittest.main()
