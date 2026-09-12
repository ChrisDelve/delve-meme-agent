from __future__ import annotations

import time
import unittest

from src.portfolio.live_reservations import (
    get_connection,
)
from src.portfolio.live_sell_absent_expired_release import (
    BLOCK,
    PASS,
    RECONCILED_ABSENT_EXPIRED_SELL_REASON,
    release_reconciled_absent_expired_sell_claim,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    CONSUMED,
    RELEASED,
    _load_claim,
)
from src.portfolio.live_sell_execution_records import (
    PASS as EXECUTION_PASS,
    load_live_sell_execution_record_read_only,
)
from src.portfolio.live_sell_success_accounting import (
    PASS as ACCOUNTING_PASS,
    record_successful_sell_and_consume_claim,
)
from tests import (
    test_live_sell_success_accounting
    as accounting_tests,
)


class LiveSellAbsentExpiredReleaseTests(
    unittest.TestCase
):
    def setUp(self):
        self.helper = (
            accounting_tests
            .LiveSellSuccessAccountingTests(
                methodName=(
                    "test_successful_sell_updates_positions_journal_and_claim_atomically"
                )
            )
        )
        self.helper.setUp()

        self.authorization = (
            self.helper.authorization
        )
        self.db_path = (
            self.helper.db_path
        )

        execution_result = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            execution_result.status,
            EXECUTION_PASS,
        )
        self.assertIsNotNone(
            execution_result.record
        )

        self.execution = (
            execution_result.record
        )

    def tearDown(self):
        self.helper.tearDown()

    def load_claim(self):
        connection = get_connection(
            self.db_path
        )

        try:
            return _load_claim(
                connection=connection,
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
            )

        finally:
            connection.close()

    def position_snapshot(self):
        connection = get_connection(
            self.db_path
        )

        try:
            rows = connection.execute(
                """
                SELECT *
                FROM live_positions
                ORDER BY position_id
                """
            ).fetchall()

            return tuple(
                tuple(row)
                for row in rows
            )

        finally:
            connection.close()

    def release(
        self,
        **overrides,
    ):
        kwargs = {
            "authorization": (
                self.authorization
            ),
            "transaction_signature": (
                self.execution
                .transaction_signature
            ),
            "signed_transaction_sha256": (
                self.execution
                .signed_transaction_sha256
            ),
            "last_valid_block_height": (
                self.execution
                .last_valid_block_height
            ),
            "blockhash_rpc_slot": (
                self.execution
                .blockhash_rpc_slot
            ),
            "db_path": self.db_path,
        }

        kwargs.update(
            overrides
        )

        return (
            release_reconciled_absent_expired_sell_claim(
                **kwargs
            )
        )

    def test_exact_expired_release_preserves_positions_and_execution(
        self,
    ):
        positions_before = (
            self.position_snapshot()
        )
        execution_before = (
            self.execution
        )

        result = self.release()

        self.assertEqual(
            result.status,
            PASS,
        )
        self.assertTrue(
            result.changed
        )
        self.assertIsNotNone(
            result.claim
        )
        self.assertEqual(
            result.claim.status,
            RELEASED,
        )
        self.assertIsNotNone(
            result.claim.terminal_at
        )
        self.assertEqual(
            result.claim.terminal_reason,
            RECONCILED_ABSENT_EXPIRED_SELL_REASON,
        )

        self.assertEqual(
            result.execution,
            execution_before,
        )

        self.assertEqual(
            self.position_snapshot(),
            positions_before,
        )

    def test_exact_retry_is_idempotent_but_evidence_bound(
        self,
    ):
        first = self.release()
        retry = self.release()

        mismatch = self.release(
            last_valid_block_height=(
                self.execution
                .last_valid_block_height
                + 1
            )
        )

        self.assertEqual(
            first.status,
            PASS,
        )
        self.assertTrue(
            first.changed
        )

        self.assertEqual(
            retry.status,
            PASS,
        )
        self.assertFalse(
            retry.changed
        )

        self.assertEqual(
            mismatch.status,
            BLOCK,
        )
        self.assertIn(
            "SELL_EXPIRY_EVIDENCE_MISMATCH",
            mismatch.reasons,
        )

    def test_expiry_release_does_not_reclassify_other_terminal_reason(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_sell_inventory_claims

                SET
                    status = ?,
                    terminal_at = ?,
                    terminal_reason = ?

                WHERE authorization_sha256 = ?
                  AND status = ?
                """,
                (
                    RELEASED,
                    time.time(),
                    "RECONCILED_FAILED_SELL",
                    self.authorization
                    .authorization_sha256,
                    ACTIVE,
                ),
            )
            connection.commit()

        finally:
            connection.close()

        result = self.release()

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertFalse(
            result.changed
        )
        self.assertIn(
            "SELL_EXPIRY_TERMINAL_REASON_MISMATCH",
            result.reasons,
        )

        claim = self.load_claim()

        self.assertEqual(
            claim.status,
            RELEASED,
        )
        self.assertEqual(
            claim.terminal_reason,
            "RECONCILED_FAILED_SELL",
        )

    def test_consumed_claim_cannot_be_released_as_expired(
        self,
    ):
        accounted = (
            record_successful_sell_and_consume_claim(
                authorization=(
                    self.authorization
                ),
                fill=(
                    self.helper.make_fill()
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            accounted.status,
            ACCOUNTING_PASS,
        )

        result = self.release()

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertFalse(
            result.changed
        )
        self.assertIn(
            "SELL_CLAIM_NOT_RECONCILABLE",
            result.reasons,
        )

        claim = self.load_claim()

        self.assertEqual(
            claim.status,
            CONSUMED,
        )

    def test_missing_execution_record_never_releases_claim(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                DELETE FROM live_sell_execution_records
                WHERE authorization_sha256 = ?
                """,
                (
                    self.authorization
                    .authorization_sha256,
                ),
            )
            connection.commit()

        finally:
            connection.close()

        result = self.release()

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertFalse(
            result.changed
        )
        self.assertIn(
            "SELL_EXECUTION_NOT_FOUND_OR_INVALID",
            result.reasons,
        )

        claim = self.load_claim()

        self.assertEqual(
            claim.status,
            ACTIVE,
        )

    def test_mismatched_signed_transaction_hash_never_releases_claim(
        self,
    ):
        result = self.release(
            signed_transaction_sha256=(
                "11" * 32
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertFalse(
            result.changed
        )
        self.assertIn(
            "SELL_EXPIRY_EVIDENCE_MISMATCH",
            result.reasons,
        )

        claim = self.load_claim()

        self.assertEqual(
            claim.status,
            ACTIVE,
        )


if __name__ == "__main__":
    unittest.main()
