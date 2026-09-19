from __future__ import annotations

import asyncio
import unittest
from unittest.mock import (
    Mock,
    patch,
)

import src.execution.live_entry_result_mailbox as mailbox_module

from src.execution.live_entry_candidate_pipeline import (
    LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION,
    LiveEntryCandidatePipelineResult,
)
from src.execution.live_entry_result_mailbox import (
    FAILED,
    IGNORED,
    LIVE_ENTRY_RESULT_MAILBOX_VERSION,
    MAILBOX_AT_CAPACITY,
    MAILBOX_RESULT_CONTRACT_INVALID,
    PUBLISHED,
    LiveEntryResultMailbox,
    LiveEntryResultMailboxError,
)


class LiveEntryResultMailboxTests(
    unittest.IsolatedAsyncioTestCase
):
    @staticmethod
    def result(
        *,
        evidence_ready: bool,
    ):
        value = Mock(
            spec=LiveEntryCandidatePipelineResult
        )

        value.pipeline_version = (
            LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION
        )

        value.evidence_ready = (
            evidence_ready
        )

        return value

    async def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_ENTRY_RESULT_MAILBOX_VERSION,
            "live-entry-result-mailbox-v1",
        )

    async def test_limit_must_be_strict_positive_int(
        self,
    ):
        for invalid in (
            0,
            -1,
            True,
            1.5,
            None,
        ):
            with self.subTest(
                invalid=invalid
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    (
                        "^max_pending_results "
                        "must be positive int$"
                    ),
                ):
                    LiveEntryResultMailbox(
                        max_pending_results=invalid
                    )

    async def test_non_ready_result_is_ignored_without_capacity_use(
        self,
    ):
        mailbox = LiveEntryResultMailbox(
            max_pending_results=1
        )

        result = self.result(
            evidence_ready=False
        )

        self.assertEqual(
            mailbox.publish(
                result
            ),
            IGNORED,
        )

        self.assertEqual(
            mailbox.pending_result_count,
            0,
        )

        self.assertFalse(
            mailbox.failed
        )

    async def test_ready_result_is_received_by_exact_identity(
        self,
    ):
        mailbox = LiveEntryResultMailbox(
            max_pending_results=1
        )

        result = self.result(
            evidence_ready=True
        )

        self.assertEqual(
            mailbox.publish(
                result
            ),
            PUBLISHED,
        )

        self.assertEqual(
            mailbox.pending_result_count,
            1,
        )

        received = await mailbox.receive()

        self.assertIs(
            received,
            result,
        )

        self.assertEqual(
            mailbox.pending_result_count,
            0,
        )

    async def test_overflow_fails_permanently_and_retains_exact_result(
        self,
    ):
        mailbox = LiveEntryResultMailbox(
            max_pending_results=1
        )

        first = self.result(
            evidence_ready=True
        )

        overflow = self.result(
            evidence_ready=True
        )

        later = self.result(
            evidence_ready=True
        )

        self.assertEqual(
            mailbox.publish(first),
            PUBLISHED,
        )

        self.assertEqual(
            mailbox.publish(overflow),
            FAILED,
        )

        self.assertTrue(
            mailbox.failed
        )

        self.assertEqual(
            mailbox.failure_reason,
            MAILBOX_AT_CAPACITY,
        )

        self.assertIs(
            mailbox.failure_item,
            overflow,
        )

        self.assertEqual(
            mailbox.publish(later),
            FAILED,
        )

        self.assertIs(
            mailbox.failure_item,
            overflow,
        )

        self.assertEqual(
            mailbox.pending_result_count,
            1,
        )

    async def test_invalid_result_contract_fails_mailbox(
        self,
    ):
        mailbox = LiveEntryResultMailbox(
            max_pending_results=1
        )

        invalid = object()

        self.assertEqual(
            mailbox.publish(
                invalid
            ),
            FAILED,
        )

        self.assertTrue(
            mailbox.failed
        )

        self.assertEqual(
            mailbox.failure_reason,
            MAILBOX_RESULT_CONTRACT_INVALID,
        )

        self.assertIs(
            mailbox.failure_item,
            invalid,
        )

    async def test_receive_after_failure_fails_closed(
        self,
    ):
        mailbox = LiveEntryResultMailbox(
            max_pending_results=1
        )

        mailbox.publish(
            self.result(
                evidence_ready=True
            )
        )

        mailbox.publish(
            self.result(
                evidence_ready=True
            )
        )

        with self.assertRaisesRegex(
            LiveEntryResultMailboxError,
            "^LIVE_ENTRY_RESULT_MAILBOX_AT_CAPACITY$",
        ):
            await mailbox.receive()

    async def test_wait_failed_unblocks_on_failure(
        self,
    ):
        mailbox = LiveEntryResultMailbox(
            max_pending_results=1
        )

        waiter = asyncio.create_task(
            mailbox.wait_failed()
        )

        await asyncio.sleep(0)

        mailbox.publish(
            self.result(
                evidence_ready=True
            )
        )

        overflow = self.result(
            evidence_ready=True
        )

        mailbox.publish(
            overflow
        )

        await waiter

        self.assertTrue(
            mailbox.failed
        )

        self.assertIs(
            mailbox.failure_item,
            overflow,
        )

    async def test_wrong_event_loop_is_rejected(
        self,
    ):
        mailbox = LiveEntryResultMailbox(
            max_pending_results=1
        )

        foreign_loop = object()

        with patch.object(
            mailbox_module.asyncio,
            "get_running_loop",
            return_value=foreign_loop,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^mailbox used from wrong event loop$",
            ):
                mailbox.publish(
                    self.result(
                        evidence_ready=True
                    )
                )


if __name__ == "__main__":
    unittest.main()
