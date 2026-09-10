from dataclasses import replace
import time
import unittest

from solders.keypair import Keypair

from src.execution.live_sell_transaction_receipt import (
    LIVE_SELL_TRANSACTION_RECEIPT_VERSION,
)
from src.portfolio.live_reservations import (
    get_connection,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    CONSUMED,
    RELEASED,
    _load_claim,
)
from src.portfolio.live_sell_execution_records import (
    PASS as EXECUTION_PASS,
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED,
    load_live_sell_execution_record_read_only,
)
from src.portfolio.live_sell_transaction_journal import (
    BLOCK,
    FAILED,
    LIVE_SELL_TRANSACTION_JOURNAL_VERSION,
    PASS,
    RECONCILED_FAILED_SELL_REASON,
    UNKNOWN,
    load_live_sell_transaction_journal_entry_read_only,
    record_failed_sell_and_release_claim,
)
from tests import (
    test_live_sell_submission_boundary
    as submission_boundary_tests,
)


class LiveSellTransactionJournalTests(
    unittest.TestCase
):
    def setUp(self):
        helper = (
            submission_boundary_tests
            .LiveSellSubmissionBoundaryTests(
                methodName=(
                    "test_signed_to_"
                    "submission_armed_is_durable"
                )
            )
        )

        helper.setUp()

        self.helper = helper
        self.db_path = helper.db_path

        self.authorization = (
            helper.authorization
        )

        self.authorization_sha256 = (
            self.authorization
            .authorization_sha256
        )

        loaded = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    self.authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            loaded.status,
            EXECUTION_PASS,
        )

        self.assertIsNotNone(
            loaded.record
        )

        self.execution = (
            loaded.record
        )

        self.error = {
            "InstructionError": [
                2,
                {
                    "Custom": 6001,
                },
            ]
        }

        self.receipt_slot = 230
        self.block_time = 1_800_000_000
        self.fee_lamports = 5_000

        self.pre_balance = (
            2_000_000
        )

        self.post_balance = (
            self.pre_balance
            - self.fee_lamports
        )

        self.balance_delta = (
            -self.fee_lamports
        )

    def tearDown(self):
        self.helper.tearDown()

    def call_ledger(
        self,
        *,
        authorization=None,
        receipt_resolver_version=(
            LIVE_SELL_TRANSACTION_RECEIPT_VERSION
        ),
        transaction_signature=None,
        signed_transaction_sha256=None,
        receipt_transaction_sha256=None,
        receipt_slot=None,
        block_time=None,
        transaction_error="DEFAULT",
        fee_lamports=None,
        fee_payer_pubkey=None,
        pre_balance=None,
        post_balance=None,
        balance_delta=None,
    ):
        if authorization is None:
            authorization = (
                self.authorization
            )

        if transaction_signature is None:
            transaction_signature = (
                self.execution
                .transaction_signature
            )

        if signed_transaction_sha256 is None:
            signed_transaction_sha256 = (
                self.execution
                .signed_transaction_sha256
            )

        if receipt_transaction_sha256 is None:
            receipt_transaction_sha256 = (
                signed_transaction_sha256
            )

        if receipt_slot is None:
            receipt_slot = (
                self.receipt_slot
            )

        if block_time is None:
            block_time = (
                self.block_time
            )

        if transaction_error == "DEFAULT":
            transaction_error = (
                self.error
            )

        if fee_lamports is None:
            fee_lamports = (
                self.fee_lamports
            )

        if fee_payer_pubkey is None:
            fee_payer_pubkey = (
                self.execution
                .wallet_pubkey
            )

        if pre_balance is None:
            pre_balance = (
                self.pre_balance
            )

        if post_balance is None:
            post_balance = (
                self.post_balance
            )

        if balance_delta is None:
            balance_delta = (
                self.balance_delta
            )

        return (
            record_failed_sell_and_release_claim(
                authorization=authorization,
                receipt_resolver_version=(
                    receipt_resolver_version
                ),
                transaction_signature=(
                    transaction_signature
                ),
                signed_transaction_sha256=(
                    signed_transaction_sha256
                ),
                receipt_transaction_sha256=(
                    receipt_transaction_sha256
                ),
                receipt_slot=receipt_slot,
                block_time=block_time,
                transaction_error=(
                    transaction_error
                ),
                fee_lamports=fee_lamports,
                fee_payer_pubkey=(
                    fee_payer_pubkey
                ),
                fee_payer_pre_balance_lamports=(
                    pre_balance
                ),
                fee_payer_post_balance_lamports=(
                    post_balance
                ),
                fee_payer_balance_delta_lamports=(
                    balance_delta
                ),
                db_path=self.db_path,
            )
        )

    def load_claim_any(self):
        connection = get_connection(
            self.db_path
        )

        try:
            return _load_claim(
                connection=connection,
                authorization_sha256=(
                    self.authorization_sha256
                ),
            )

        finally:
            connection.close()

    def execution_row_snapshot(self):
        connection = get_connection(
            self.db_path
        )

        try:
            row = connection.execute(
                """
                SELECT *

                FROM live_sell_execution_records

                WHERE authorization_sha256 = ?
                """,
                (
                    self.authorization_sha256,
                ),
            ).fetchone()

            self.assertIsNotNone(
                row
            )

            return dict(
                row
            )

        finally:
            connection.close()

    def claim_lot_snapshot(self):
        connection = get_connection(
            self.db_path
        )

        try:
            rows = connection.execute(
                """
                SELECT *

                FROM live_sell_inventory_claim_lots

                WHERE authorization_sha256 = ?

                ORDER BY ordinal ASC
                """,
                (
                    self.authorization_sha256,
                ),
            ).fetchall()

            return tuple(
                tuple(
                    row
                )
                for row in rows
            )

        finally:
            connection.close()

    def set_execution_status(
        self,
        status,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            signed_at = (
                self.execution.signed_at
            )

            if status == SIGNED:
                values = (
                    SIGNED,
                    None,
                    0,
                    None,
                    signed_at,
                )

            elif status == SUBMISSION_ARMED:
                started = (
                    signed_at + 1.0
                )

                values = (
                    SUBMISSION_ARMED,
                    started,
                    1,
                    None,
                    started,
                )

            elif status == SUBMITTED:
                started = (
                    signed_at + 1.0
                )

                submitted = (
                    signed_at + 2.0
                )

                values = (
                    SUBMITTED,
                    started,
                    1,
                    submitted,
                    submitted,
                )

            else:
                raise AssertionError(
                    status
                )

            updated = connection.execute(
                """
                UPDATE live_sell_execution_records

                SET
                    status = ?,
                    submission_started_at = ?,
                    submission_attempt_count = ?,
                    submitted_at = ?,
                    updated_at = ?

                WHERE authorization_sha256 = ?
                """,
                (
                    *values,
                    self.authorization_sha256,
                ),
            )

            self.assertEqual(
                updated.rowcount,
                1,
            )

            connection.commit()

        finally:
            connection.close()

    def test_failed_sell_is_atomically_journaled_and_claim_released(
        self,
    ):
        positions_before = (
            self.helper.position_snapshot()
        )

        lots_before = (
            self.claim_lot_snapshot()
        )

        execution_before = (
            self.execution_row_snapshot()
        )

        result = self.call_ledger()

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertTrue(
            result.changed
        )

        self.assertIsNotNone(
            result.entry
        )

        self.assertEqual(
            result.entry.journal_version,
            LIVE_SELL_TRANSACTION_JOURNAL_VERSION,
        )

        self.assertEqual(
            result.entry.outcome,
            FAILED,
        )

        self.assertEqual(
            result.entry.authorization_sha256,
            self.authorization_sha256,
        )

        self.assertEqual(
            result.entry.transaction_signature,
            self.execution.transaction_signature,
        )

        self.assertEqual(
            result.entry.signed_transaction_sha256,
            self.execution.signed_transaction_sha256,
        )

        self.assertEqual(
            result.entry.receipt_transaction_sha256,
            self.execution.signed_transaction_sha256,
        )

        self.assertEqual(
            result.entry.fee_lamports,
            self.fee_lamports,
        )

        self.assertEqual(
            result.entry.fee_payer_balance_delta_lamports,
            -self.fee_lamports,
        )

        claim = self.load_claim_any()

        self.assertIsNotNone(
            claim
        )

        self.assertEqual(
            claim.status,
            RELEASED,
        )

        self.assertEqual(
            claim.terminal_reason,
            RECONCILED_FAILED_SELL_REASON,
        )

        self.assertEqual(
            claim.terminal_at,
            result.entry.recorded_at,
        )

        persisted = (
            load_live_sell_transaction_journal_entry_read_only(
                authorization_sha256=(
                    self.authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            persisted,
            result.entry,
        )

        self.assertEqual(
            self.helper.position_snapshot(),
            positions_before,
        )

        self.assertEqual(
            self.claim_lot_snapshot(),
            lots_before,
        )

        self.assertEqual(
            self.execution_row_snapshot(),
            execution_before,
        )

    def test_exact_retry_is_idempotent(
        self,
    ):
        first = self.call_ledger()
        second = self.call_ledger()

        self.assertEqual(
            first.status,
            PASS,
        )

        self.assertTrue(
            first.changed
        )

        self.assertEqual(
            second.status,
            PASS,
        )

        self.assertFalse(
            second.changed
        )

        self.assertEqual(
            first.entry,
            second.entry,
        )

        claim = self.load_claim_any()

        self.assertEqual(
            claim.status,
            RELEASED,
        )

        self.assertEqual(
            claim.terminal_at,
            first.entry.recorded_at,
        )

    def test_failed_transaction_error_is_required(
        self,
    ):
        positions_before = (
            self.helper.position_snapshot()
        )

        result = self.call_ledger(
            transaction_error=None,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FAILED_SELL_TRANSACTION_ERROR_REQUIRED",
            result.reasons,
        )

        claim = self.load_claim_any()

        self.assertEqual(
            claim.status,
            ACTIVE,
        )

        self.assertIsNone(
            load_live_sell_transaction_journal_entry_read_only(
                authorization_sha256=(
                    self.authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            self.helper.position_snapshot(),
            positions_before,
        )

    def test_failed_sell_fee_delta_mismatch_is_rejected(
        self,
    ):
        result = self.call_ledger(
            post_balance=(
                self.pre_balance
                - self.fee_lamports
                - 100
            ),
            balance_delta=(
                -self.fee_lamports
                - 100
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FAILED_SELL_FEE_DELTA_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    def test_balance_arithmetic_mismatch_is_rejected(
        self,
    ):
        result = self.call_ledger(
            balance_delta=(
                -self.fee_lamports
                - 1
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FEE_PAYER_BALANCE_DELTA_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    def test_invalid_authorization_fingerprint_is_rejected(
        self,
    ):
        invalid = replace(
            self.authorization,
            authorization_sha256=(
                "00" * 32
            ),
        )

        result = self.call_ledger(
            authorization=invalid,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_AUTHORIZATION_CONTRACT_INVALID",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    def test_transaction_signature_mismatch_is_rejected(
        self,
    ):
        result = self.call_ledger(
            transaction_signature=(
                "DifferentSignature"
            ),
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "FAILED_SELL_RECEIPT_EVIDENCE_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    def test_signed_transaction_hash_mismatch_is_rejected(
        self,
    ):
        wrong_hash = (
            "11" * 32
        )

        result = self.call_ledger(
            signed_transaction_sha256=(
                wrong_hash
            ),
            receipt_transaction_sha256=(
                wrong_hash
            ),
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "FAILED_SELL_RECEIPT_EVIDENCE_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    def test_receipt_transaction_hash_mismatch_is_rejected(
        self,
    ):
        result = self.call_ledger(
            receipt_transaction_sha256=(
                "22" * 32
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "RECEIPT_TRANSACTION_HASH_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    def test_fee_payer_mismatch_is_rejected(
        self,
    ):
        result = self.call_ledger(
            fee_payer_pubkey=(
                str(
                    Keypair().pubkey()
                )
            ),
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "FAILED_SELL_RECEIPT_EVIDENCE_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    def test_submission_armed_execution_is_reconcilable(
        self,
    ):
        self.set_execution_status(
            SUBMISSION_ARMED
        )

        result = self.call_ledger()

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertTrue(
            result.changed
        )

        self.assertEqual(
            self.load_claim_any().status,
            RELEASED,
        )

    def test_submitted_execution_is_reconcilable(
        self,
    ):
        self.set_execution_status(
            SUBMITTED
        )

        result = self.call_ledger()

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertTrue(
            result.changed
        )

        self.assertEqual(
            self.load_claim_any().status,
            RELEASED,
        )

    def test_terminal_reason_mismatch_blocks_retry(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            now = time.time()

            updated = connection.execute(
                """
                UPDATE live_sell_inventory_claims

                SET
                    status = ?,
                    terminal_at = ?,
                    terminal_reason = ?

                WHERE authorization_sha256 = ?
                """,
                (
                    RELEASED,
                    now,
                    "DIFFERENT_TERMINAL_REASON",
                    self.authorization_sha256,
                ),
            )

            self.assertEqual(
                updated.rowcount,
                1,
            )

            connection.commit()

        finally:
            connection.close()

        result = self.call_ledger()

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "FAILED_SELL_TERMINAL_REASON_MISMATCH",
            result.reasons,
        )

        self.assertIsNone(
            load_live_sell_transaction_journal_entry_read_only(
                authorization_sha256=(
                    self.authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

    def test_tampered_terminal_journal_blocks_idempotent_retry(
        self,
    ):
        first = self.call_ledger()

        self.assertEqual(
            first.status,
            PASS,
        )

        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_sell_transaction_journal

                SET fee_lamports = ?

                WHERE authorization_sha256 = ?
                """,
                (
                    self.fee_lamports
                    + 1,
                    self.authorization_sha256,
                ),
            )

            connection.commit()

        finally:
            connection.close()

        second = self.call_ledger()

        self.assertEqual(
            second.status,
            BLOCK,
        )

        self.assertIn(
            "FAILED_SELL_JOURNAL_EVIDENCE_MISMATCH",
            second.reasons,
        )


if __name__ == "__main__":
    unittest.main()
