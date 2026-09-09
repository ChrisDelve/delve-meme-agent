import hashlib
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor

from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

from src.portfolio.live_sell_claims import (
    RELEASED,
)
from src.portfolio.live_sell_execution_records import (
    BLOCK,
    PASS,
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED,
    UNKNOWN,
    acknowledge_live_sell_submitted,
    arm_live_sell_submission,
    bind_live_sell_signed_artifact,
    load_live_sell_execution_record_read_only,
)
from tests import (
    test_live_sell_execution_records
    as execution_record_tests,
)


class LiveSellSubmissionBoundaryTests(
    unittest.TestCase
):
    def setUp(self):
        helper = (
            execution_record_tests
            .LiveSellExecutionRecordTests(
                methodName=(
                    "test_valid_signed_artifact_"
                    "is_durable"
                )
            )
        )

        helper.setUp()

        self.helper = helper
        self.db_path = helper.db_path

        self.keypair = helper.keypair
        self.authorization = (
            helper.authorization
        )
        self.message_plan = (
            helper.message_plan
        )

        message_bytes = (
            to_bytes_versioned(
                self.message_plan.message
            )
        )

        signature = (
            self.keypair.sign_message(
                message_bytes
            )
        )

        transaction = (
            VersionedTransaction.populate(
                self.message_plan.message,
                [
                    signature
                ],
            )
        )

        self.transaction_signature = str(
            signature
        )

        self.signed_transaction_bytes = bytes(
            transaction
        )

        self.signed_transaction_sha256 = (
            hashlib.sha256(
                self.signed_transaction_bytes
            ).hexdigest()
        )

        bound = (
            bind_live_sell_signed_artifact(
                authorization=(
                    self.authorization
                ),
                message_plan=(
                    self.message_plan
                ),
                transaction_signature=(
                    self.transaction_signature
                ),
                signed_transaction_bytes=(
                    self.signed_transaction_bytes
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            bound.status,
            PASS,
        )

        self.assertIsNotNone(
            bound.record
        )

        self.assertEqual(
            bound.record.status,
            SIGNED,
        )

    def tearDown(self):
        self.helper.tearDown()

    def arm(self):
        return arm_live_sell_submission(
            authorization=(
                self.authorization
            ),
            transaction_signature=(
                self.transaction_signature
            ),
            signed_transaction_sha256=(
                self.signed_transaction_sha256
            ),
            db_path=self.db_path,
        )

    def acknowledge(self):
        return (
            acknowledge_live_sell_submitted(
                authorization=(
                    self.authorization
                ),
                transaction_signature=(
                    self.transaction_signature
                ),
                signed_transaction_sha256=(
                    self.signed_transaction_sha256
                ),
                db_path=self.db_path,
            )
        )

    def release_claim(self):
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            updated = connection.execute(
                """
                UPDATE
                    live_sell_inventory_claims

                SET
                    status = ?,
                    terminal_at = ?,
                    terminal_reason = ?

                WHERE authorization_sha256 = ?
                """,
                (
                    RELEASED,
                    500.0,
                    "TEST_RELEASE",
                    self.authorization
                    .authorization_sha256,
                ),
            )

            self.assertEqual(
                updated.rowcount,
                1,
            )

            connection.commit()

        finally:
            connection.close()

    def claim_snapshot(self):
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            return tuple(
                connection.execute(
                    """
                    SELECT
                        authorization_sha256,
                        wallet_pubkey,
                        mint,
                        tokens_to_sell,
                        status,
                        claimed_at,
                        terminal_at,
                        terminal_reason

                    FROM live_sell_inventory_claims

                    ORDER BY authorization_sha256
                    """
                ).fetchall()
            )

        finally:
            connection.close()

    def position_snapshot(self):
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            return tuple(
                connection.execute(
                    """
                    SELECT
                        position_id,
                        status,
                        tokens_held,
                        remaining_exposure_lamports,
                        remaining_cost_basis_lamports,
                        cumulative_net_proceeds_lamports,
                        cumulative_realized_pnl_lamports

                    FROM live_positions

                    ORDER BY position_id
                    """
                ).fetchall()
            )

        finally:
            connection.close()

    def test_signed_to_submission_armed_is_durable(
        self,
    ):
        armed = self.arm()

        self.assertEqual(
            armed.status,
            PASS,
        )

        self.assertTrue(
            armed.changed
        )

        self.assertIsNotNone(
            armed.record
        )

        self.assertEqual(
            armed.record.status,
            SUBMISSION_ARMED,
        )

        self.assertIsNotNone(
            armed.record
            .submission_started_at
        )

        self.assertEqual(
            armed.record
            .submission_attempt_count,
            1,
        )

        self.assertIsNone(
            armed.record.submitted_at
        )

        loaded = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            loaded.status,
            PASS,
        )

        self.assertEqual(
            loaded.record,
            armed.record,
        )

    def test_rearm_is_blocked_and_attempt_count_stays_one(
        self,
    ):
        first = self.arm()

        self.assertEqual(
            first.status,
            PASS,
        )

        started_at = (
            first.record
            .submission_started_at
        )

        second = self.arm()

        self.assertEqual(
            second.status,
            BLOCK,
        )

        self.assertIn(
            (
                "SELL_SUBMISSION_ALREADY_ARMED_"
                "REQUIRES_RECONCILIATION"
            ),
            second.reasons,
        )

        self.assertFalse(
            second.changed
        )

        self.assertEqual(
            second.record.status,
            SUBMISSION_ARMED,
        )

        self.assertEqual(
            second.record
            .submission_attempt_count,
            1,
        )

        self.assertEqual(
            second.record
            .submission_started_at,
            started_at,
        )

    def test_artifact_identity_mismatch_cannot_arm(
        self,
    ):
        wrong_hash = (
            "00" * 32
        )

        result = (
            arm_live_sell_submission(
                authorization=(
                    self.authorization
                ),
                transaction_signature=(
                    self.transaction_signature
                ),
                signed_transaction_sha256=(
                    wrong_hash
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SELL_SUBMISSION_ARTIFACT_MISMATCH",
            result.reasons,
        )

        loaded = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            loaded.status,
            PASS,
        )

        self.assertEqual(
            loaded.record.status,
            SIGNED,
        )

        self.assertEqual(
            loaded.record
            .submission_attempt_count,
            0,
        )

    def test_terminal_claim_cannot_arm(
        self,
    ):
        self.release_claim()

        result = self.arm()

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SELL_SUBMISSION_CLAIM_BLOCKED",
            result.reasons,
        )

        loaded = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            loaded.status,
            PASS,
        )

        self.assertEqual(
            loaded.record.status,
            SIGNED,
        )

    def test_concurrent_arm_only_one_caller_wins(
        self,
    ):
        def worker():
            return self.arm()

        with ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            results = tuple(
                executor.map(
                    lambda _: worker(),
                    range(2),
                )
            )

        statuses = sorted(
            result.status
            for result in results
        )

        self.assertEqual(
            statuses,
            [
                BLOCK,
                PASS,
            ],
        )

        winners = [
            result
            for result in results
            if result.status == PASS
        ]

        self.assertEqual(
            len(winners),
            1,
        )

        self.assertTrue(
            winners[0].changed
        )

        loaded = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            loaded.status,
            PASS,
        )

        self.assertEqual(
            loaded.record.status,
            SUBMISSION_ARMED,
        )

        self.assertEqual(
            loaded.record
            .submission_attempt_count,
            1,
        )

    def test_arm_does_not_mutate_claim_or_positions(
        self,
    ):
        claim_before = (
            self.claim_snapshot()
        )

        positions_before = (
            self.position_snapshot()
        )

        armed = self.arm()

        self.assertEqual(
            armed.status,
            PASS,
        )

        self.assertEqual(
            self.claim_snapshot(),
            claim_before,
        )

        self.assertEqual(
            self.position_snapshot(),
            positions_before,
        )

    def test_submit_acknowledgement_requires_arm(
        self,
    ):
        result = self.acknowledge()

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SELL_SUBMISSION_NOT_ARMED",
            result.reasons,
        )

        self.assertEqual(
            result.record.status,
            SIGNED,
        )

        self.assertFalse(
            result.changed
        )

    def test_submission_armed_to_submitted_is_durable(
        self,
    ):
        armed = self.arm()

        self.assertEqual(
            armed.status,
            PASS,
        )

        submitted = self.acknowledge()

        self.assertEqual(
            submitted.status,
            PASS,
        )

        self.assertTrue(
            submitted.changed
        )

        self.assertIsNotNone(
            submitted.record
        )

        self.assertEqual(
            submitted.record.status,
            SUBMITTED,
        )

        self.assertEqual(
            submitted.record
            .submission_attempt_count,
            1,
        )

        self.assertIsNotNone(
            submitted.record
            .submission_started_at
        )

        self.assertIsNotNone(
            submitted.record
            .submitted_at
        )

        self.assertGreaterEqual(
            submitted.record.submitted_at,
            submitted.record
            .submission_started_at,
        )

        loaded = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            loaded.status,
            PASS,
        )

        self.assertEqual(
            loaded.record,
            submitted.record,
        )

    def test_submitted_acknowledgement_exact_retry_is_idempotent(
        self,
    ):
        armed = self.arm()

        self.assertEqual(
            armed.status,
            PASS,
        )

        first = self.acknowledge()

        self.assertEqual(
            first.status,
            PASS,
        )

        self.assertTrue(
            first.changed
        )

        submitted_at = (
            first.record.submitted_at
        )

        second = self.acknowledge()

        self.assertEqual(
            second.status,
            PASS,
        )

        self.assertFalse(
            second.changed
        )

        self.assertEqual(
            second.record.status,
            SUBMITTED,
        )

        self.assertEqual(
            second.record.submitted_at,
            submitted_at,
        )

        self.assertEqual(
            second.record
            .submission_attempt_count,
            1,
        )

    def test_acknowledgement_survives_claim_change_after_arm(
        self,
    ):
        armed = self.arm()

        self.assertEqual(
            armed.status,
            PASS,
        )

        #
        # Once broadcast authority has been granted,
        # the transaction may already have left the
        # process. A later claim transition must not
        # prevent exact RPC acknowledgement from
        # being durably recorded.
        #
        self.release_claim()

        submitted = self.acknowledge()

        self.assertEqual(
            submitted.status,
            PASS,
        )

        self.assertTrue(
            submitted.changed
        )

        self.assertEqual(
            submitted.record.status,
            SUBMITTED,
        )

        self.assertEqual(
            submitted.record
            .submission_attempt_count,
            1,
        )

    def test_legacy_signed_row_is_readable_and_migrates_on_arm(
        self,
    ):
        #
        # Simulate a v1 execution database written
        # before submission metadata columns existed.
        #
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            connection.execute(
                """
                ALTER TABLE
                    live_sell_execution_records
                DROP COLUMN
                    submitted_at
                """
            )

            connection.execute(
                """
                ALTER TABLE
                    live_sell_execution_records
                DROP COLUMN
                    submission_attempt_count
                """
            )

            connection.execute(
                """
                ALTER TABLE
                    live_sell_execution_records
                DROP COLUMN
                    submission_started_at
                """
            )

            connection.commit()

        finally:
            connection.close()

        legacy = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            legacy.status,
            PASS,
        )

        self.assertEqual(
            legacy.record.status,
            SIGNED,
        )

        self.assertIsNone(
            legacy.record
            .submission_started_at
        )

        self.assertEqual(
            legacy.record
            .submission_attempt_count,
            0,
        )

        self.assertIsNone(
            legacy.record.submitted_at
        )

        armed = self.arm()

        self.assertEqual(
            armed.status,
            PASS,
        )

        self.assertEqual(
            armed.record.status,
            SUBMISSION_ARMED,
        )

        self.assertEqual(
            armed.record
            .submission_attempt_count,
            1,
        )

        connection = sqlite3.connect(
            self.db_path
        )

        try:
            columns = {
                row[1]
                for row in connection.execute(
                    """
                    PRAGMA table_info(
                        live_sell_execution_records
                    )
                    """
                ).fetchall()
            }

        finally:
            connection.close()

        self.assertIn(
            "submission_started_at",
            columns,
        )

        self.assertIn(
            "submission_attempt_count",
            columns,
        )

        self.assertIn(
            "submitted_at",
            columns,
        )

    def test_incoherent_submission_metadata_fails_closed(
        self,
    ):
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            updated = connection.execute(
                """
                UPDATE
                    live_sell_execution_records

                SET
                    submission_attempt_count = 1

                WHERE authorization_sha256 = ?
                """,
                (
                    self.authorization
                    .authorization_sha256,
                ),
            )

            self.assertEqual(
                updated.rowcount,
                1,
            )

            connection.commit()

        finally:
            connection.close()

        loaded = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            loaded.status,
            UNKNOWN,
        )

        armed = self.arm()

        self.assertEqual(
            armed.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_EXECUTION_RECORD_INVALID",
            armed.reasons,
        )
