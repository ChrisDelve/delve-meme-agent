from __future__ import annotations

import time
import unittest

from src.portfolio.live_reservations import (
    get_connection,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    CONSUMED,
    RELEASED,
    _load_claim,
)
from src.portfolio.live_sell_success_accounting import (
    PASS as ACCOUNTING_PASS,
    record_successful_sell_and_consume_claim,
)
from src.portfolio.live_sell_unexecuted_release import (
    BLOCK,
    PASS,
    UNKNOWN,
    RELEASED_UNEXECUTED_SELL_REASON,
    release_unexecuted_live_sell_claim,
)
from tests import (
    test_live_sell_success_accounting
    as accounting_tests,
)


class LiveSellUnexecutedReleaseTests(
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

    def tearDown(self):
        self.helper.tearDown()

    def delete_execution(self):
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

    def journal_count(self):
        connection = get_connection(
            self.db_path
        )

        try:
            table = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'live_sell_transaction_journal'
                """
            ).fetchone()

            if table is None:
                return 0

            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM live_sell_transaction_journal
                """
            ).fetchone()

            return int(
                row["count"]
            )

        finally:
            connection.close()

    def release(self):
        return (
            release_unexecuted_live_sell_claim(
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

    def test_active_claim_without_execution_is_released_without_accounting_mutation(
        self,
    ):
        positions_before = (
            self.position_snapshot()
        )
        journal_before = (
            self.journal_count()
        )

        self.delete_execution()

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
        self.assertEqual(
            result.claim.terminal_reason,
            RELEASED_UNEXECUTED_SELL_REASON,
        )
        self.assertIsNotNone(
            result.claim.terminal_at
        )

        self.assertEqual(
            self.position_snapshot(),
            positions_before,
        )
        self.assertEqual(
            self.journal_count(),
            journal_before,
        )

    def test_missing_execution_table_can_release_fresh_claim(
        self,
    ):
        self.delete_execution()

        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                DROP TABLE live_sell_execution_records
                """
            )
            connection.commit()

        finally:
            connection.close()

        result = self.release()

        self.assertEqual(
            result.status,
            PASS,
        )
        self.assertTrue(
            result.changed
        )

        claim = self.load_claim()

        self.assertEqual(
            claim.status,
            RELEASED,
        )
        self.assertEqual(
            claim.terminal_reason,
            RELEASED_UNEXECUTED_SELL_REASON,
        )

    def test_exact_retry_is_idempotent(
        self,
    ):
        self.delete_execution()

        first = self.release()
        retry = self.release()

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

    def test_any_durable_execution_record_blocks_release(
        self,
    ):
        result = self.release()

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertFalse(
            result.changed
        )
        self.assertIn(
            "SELL_EXECUTION_RECORD_EXISTS",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim().status,
            ACTIVE,
        )

    def test_other_terminal_reason_is_never_reclassified(
        self,
    ):
        self.delete_execution()

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
                    "OTHER_RELEASE_REASON",
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
            "SELL_UNEXECUTED_RELEASE_TERMINAL_REASON_MISMATCH",
            result.reasons,
        )

        claim = self.load_claim()

        self.assertEqual(
            claim.status,
            RELEASED,
        )
        self.assertEqual(
            claim.terminal_reason,
            "OTHER_RELEASE_REASON",
        )

    def test_consumed_claim_cannot_be_released_as_unexecuted(
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
            "SELL_CLAIM_NOT_UNEXECUTED_RELEASABLE",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim().status,
            CONSUMED,
        )

    def test_invalid_authorization_hash_is_unknown_without_mutation(
        self,
    ):
        result = (
            release_unexecuted_live_sell_claim(
                authorization_sha256="bad",
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertFalse(
            result.changed
        )

        self.assertEqual(
            self.load_claim().status,
            ACTIVE,
        )


if __name__ == "__main__":
    unittest.main()
