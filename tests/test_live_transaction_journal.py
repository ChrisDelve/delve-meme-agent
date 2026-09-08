import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.portfolio.live_reservations import (
    ACTIVE,
    RELEASED,
    RESERVATION_VERSION,
    SIGNED,
    SUBMITTED,
    get_connection,
    held_reservation_totals,
    init_schema as init_reservation_schema,
)
from src.portfolio.live_transaction_journal import (
    BLOCK,
    FAILED,
    LIVE_TRANSACTION_JOURNAL_VERSION,
    PASS,
    RECONCILED_FAILED_TRANSACTION_REASON,
    UNKNOWN,
    init_schema as init_journal_schema,
    load_transaction_journal_entry_read_only,
    record_failed_transaction_and_release_reservation,
)


class LiveTransactionJournalTests(
    unittest.TestCase
):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()

        self.db_path = (
            Path(self.temp_dir.name)
            / "live.db"
        )

        self.reservation_id = (
            "failed-reconciliation-test"
        )

        self.signature = (
            "FailedTransactionSignature"
        )

        self.transaction_sha256 = (
            "22" * 32
        )

        self.wallet_pubkey = (
            "FailedWallet"
        )

        self.error = {
            "InstructionError": [
                2,
                {
                    "Custom": 6001,
                },
            ],
        }

        self.insert_reservation()

    def tearDown(self):
        self.temp_dir.cleanup()

    def insert_reservation(
        self,
        *,
        status=SIGNED,
        reservation_id=None,
        terminal_at=None,
        terminal_reason=None,
    ):
        if reservation_id is None:
            reservation_id = (
                self.reservation_id
            )

        submission_started_at = None
        submission_attempt_count = 0
        submitted_at = None

        if status == SUBMITTED:
            submission_started_at = 3.5
            submission_attempt_count = 1
            submitted_at = 4.0

        connection = get_connection(
            self.db_path
        )

        try:
            init_reservation_schema(
                connection
            )

            connection.execute(
                """
                INSERT INTO live_capital_reservations (
                    reservation_id,
                    reservation_version,
                    mint,
                    side,
                    wallet_pubkey,
                    spend_lamports,
                    wallet_cost_lamports,
                    status,
                    risk_governor_version,
                    risk_simulation_sha256,
                    base_available_cash_lamports,
                    base_open_exposure_lamports,
                    base_open_positions,
                    reserved_exposure_before_lamports,
                    reserved_cash_before_lamports,
                    active_reservations_before,
                    created_at,
                    expires_at,
                    signed_at,
                    transaction_signature,
                    signed_message_sha256,
                    signed_transaction_sha256,
                    signed_transaction_bytes,
                    recent_blockhash,
                    last_valid_block_height,
                    blockhash_rpc_slot,
                    submission_started_at,
                    submission_attempt_count,
                    submitted_at,
                    terminal_at,
                    terminal_reason
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    reservation_id,
                    RESERVATION_VERSION,
                    "MintFailed",
                    "BUY",
                    self.wallet_pubkey,
                    1_000_000,
                    1_100_000,
                    status,
                    "risk-test",
                    "11" * 32,
                    10_000_000,
                    0,
                    0,
                    0,
                    0,
                    0,
                    1.0,
                    100.0,
                    2.0,
                    self.signature,
                    "33" * 32,
                    self.transaction_sha256,
                    b"signed-transaction",
                    "BlockhashFailed",
                    350,
                    200,
                    submission_started_at,
                    submission_attempt_count,
                    submitted_at,
                    terminal_at,
                    terminal_reason,
                ),
            )

            connection.commit()

        finally:
            connection.close()

    def evidence(
        self,
        **overrides,
    ):
        values = {
            "reservation_id": (
                self.reservation_id
            ),
            "receipt_resolver_version": (
                "signed-transaction-receipt-v1"
            ),
            "transaction_signature": (
                self.signature
            ),
            "signed_transaction_sha256": (
                self.transaction_sha256
            ),
            "receipt_transaction_sha256": (
                self.transaction_sha256
            ),
            "receipt_slot": 230,
            "block_time": 1_800_000_000,
            "transaction_error": (
                self.error
            ),
            "fee_lamports": 9_000,
            "fee_payer_pubkey": (
                self.wallet_pubkey
            ),
            "fee_payer_pre_balance_lamports": (
                2_000_000
            ),
            "fee_payer_post_balance_lamports": (
                1_991_000
            ),
            "fee_payer_balance_delta_lamports": (
                -9_000
            ),
            "db_path": self.db_path,
        }

        values.update(
            overrides
        )

        return values

    def test_read_only_loader_returns_none_before_journal_entry(
        self,
    ):
        entry = (
            load_transaction_journal_entry_read_only(
                reservation_id=(
                    self.reservation_id
                ),
                db_path=self.db_path,
            )
        )

        self.assertIsNone(
            entry
        )

        connection = get_connection(
            self.db_path
        )

        try:
            row = connection.execute(
                """
                SELECT status
                FROM live_capital_reservations
                WHERE reservation_id = ?
                """,
                (
                    self.reservation_id,
                ),
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(
            row["status"],
            SIGNED,
        )

    def test_read_only_loader_returns_committed_failed_entry(
        self,
    ):
        transition = (
            record_failed_transaction_and_release_reservation(
                **self.evidence()
            )
        )

        self.assertEqual(
            transition.status,
            PASS,
        )

        entry = (
            load_transaction_journal_entry_read_only(
                reservation_id=(
                    self.reservation_id
                ),
                db_path=self.db_path,
            )
        )

        self.assertIsNotNone(
            entry
        )

        self.assertEqual(
            entry.reservation_id,
            self.reservation_id,
        )

        self.assertEqual(
            entry.transaction_signature,
            self.signature,
        )

        self.assertEqual(
            entry.outcome,
            FAILED,
        )

        self.assertEqual(
            entry.signed_transaction_sha256,
            self.transaction_sha256,
        )

        self.assertEqual(
            entry.receipt_transaction_sha256,
            self.transaction_sha256,
        )

        self.assertEqual(
            entry.wallet_pubkey,
            self.wallet_pubkey,
        )

        self.assertEqual(
            entry.fee_lamports,
            9_000,
        )

        self.assertEqual(
            entry.fee_payer_balance_delta_lamports,
            -9_000,
        )

    def test_signed_failed_transaction_journals_and_releases_atomically(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            before = held_reservation_totals(
                connection
            )
        finally:
            connection.close()

        self.assertEqual(
            before,
            (
                1_000_000,
                1_100_000,
                1,
            ),
        )

        result = (
            record_failed_transaction_and_release_reservation(
                **self.evidence()
            )
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertTrue(
            result.changed
        )

        self.assertEqual(
            result.reservation_status,
            RELEASED,
        )

        self.assertEqual(
            result.terminal_reason,
            RECONCILED_FAILED_TRANSACTION_REASON,
        )

        self.assertIsNotNone(
            result.entry
        )

        self.assertEqual(
            result.entry.journal_version,
            LIVE_TRANSACTION_JOURNAL_VERSION,
        )

        self.assertEqual(
            result.entry.outcome,
            FAILED,
        )

        self.assertEqual(
            result.entry.fee_lamports,
            9_000,
        )

        self.assertEqual(
            result.entry.fee_payer_balance_delta_lamports,
            -9_000,
        )

        connection = get_connection(
            self.db_path
        )

        try:
            after = held_reservation_totals(
                connection
            )

            journal_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM live_transaction_journal
                    """
                ).fetchone()[0]
            )
        finally:
            connection.close()

        self.assertEqual(
            after,
            (
                0,
                0,
                0,
            ),
        )

        self.assertEqual(
            journal_count,
            1,
        )

    def test_submitted_failed_transaction_releases_and_preserves_submission_metadata(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_capital_reservations
                SET
                    status = ?,
                    submission_started_at = ?,
                    submission_attempt_count = ?,
                    submitted_at = ?
                WHERE reservation_id = ?
                """,
                (
                    SUBMITTED,
                    3.5,
                    1,
                    4.0,
                    self.reservation_id,
                ),
            )

            connection.commit()
        finally:
            connection.close()

        result = (
            record_failed_transaction_and_release_reservation(
                **self.evidence()
            )
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        connection = get_connection(
            self.db_path
        )

        try:
            row = connection.execute(
                """
                SELECT *
                FROM live_capital_reservations
                WHERE reservation_id = ?
                """,
                (
                    self.reservation_id,
                ),
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(
            row["status"],
            RELEASED,
        )

        self.assertEqual(
            row["submission_started_at"],
            3.5,
        )

        self.assertEqual(
            row["submission_attempt_count"],
            1,
        )

        self.assertEqual(
            row["submitted_at"],
            4.0,
        )

    def test_exact_retry_is_idempotent(
        self,
    ):
        first = (
            record_failed_transaction_and_release_reservation(
                **self.evidence()
            )
        )

        second = (
            record_failed_transaction_and_release_reservation(
                **self.evidence()
            )
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

        connection = get_connection(
            self.db_path
        )

        try:
            count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM live_transaction_journal
                    """
                ).fetchone()[0]
            )
        finally:
            connection.close()

        self.assertEqual(
            count,
            1,
        )

    def test_idempotent_retry_blocks_terminal_timestamp_corruption(
        self,
    ):
        first = (
            record_failed_transaction_and_release_reservation(
                **self.evidence()
            )
        )

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
                UPDATE live_capital_reservations
                SET terminal_at = terminal_at + 1
                WHERE reservation_id = ?
                """,
                (
                    self.reservation_id,
                ),
            )

            connection.commit()
        finally:
            connection.close()

        result = (
            record_failed_transaction_and_release_reservation(
                **self.evidence()
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "FAILED_TRANSACTION_JOURNAL_EVIDENCE_MISMATCH",
            result.reasons,
        )

    def test_idempotent_retry_blocks_expiry_provenance_corruption(
        self,
    ):
        first = (
            record_failed_transaction_and_release_reservation(
                **self.evidence()
            )
        )

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
                UPDATE live_capital_reservations
                SET last_valid_block_height =
                    last_valid_block_height + 1
                WHERE reservation_id = ?
                """,
                (
                    self.reservation_id,
                ),
            )

            connection.commit()
        finally:
            connection.close()

        result = (
            record_failed_transaction_and_release_reservation(
                **self.evidence()
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "FAILED_TRANSACTION_JOURNAL_EVIDENCE_MISMATCH",
            result.reasons,
        )

    def test_idempotent_retry_is_evidence_bound(
        self,
    ):
        record_failed_transaction_and_release_reservation(
            **self.evidence()
        )

        result = (
            record_failed_transaction_and_release_reservation(
                **self.evidence(
                    receipt_slot=231,
                )
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "FAILED_TRANSACTION_JOURNAL_EVIDENCE_MISMATCH",
            result.reasons,
        )

    def test_active_reservation_cannot_be_failed_reconciled(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_capital_reservations
                SET
                    status = ?,
                    signed_at = NULL,
                    transaction_signature = NULL,
                    signed_message_sha256 = NULL,
                    signed_transaction_sha256 = NULL,
                    signed_transaction_bytes = NULL,
                    recent_blockhash = NULL,
                    last_valid_block_height = NULL,
                    blockhash_rpc_slot = NULL
                WHERE reservation_id = ?
                """,
                (
                    ACTIVE,
                    self.reservation_id,
                ),
            )

            connection.commit()
        finally:
            connection.close()

        result = (
            record_failed_transaction_and_release_reservation(
                **self.evidence()
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "RESERVATION_NOT_RECONCILABLE",
            result.reasons,
        )

    def test_success_receipt_cannot_use_failed_transition(
        self,
    ):
        result = (
            record_failed_transaction_and_release_reservation(
                **self.evidence(
                    transaction_error=None,
                )
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FAILED_TRANSACTION_ERROR_REQUIRED",
            result.reasons,
        )

    def test_failed_fee_delta_must_equal_actual_fee(
        self,
    ):
        result = (
            record_failed_transaction_and_release_reservation(
                **self.evidence(
                    fee_payer_post_balance_lamports=(
                        1_990_000
                    ),
                    fee_payer_balance_delta_lamports=(
                        -10_000
                    ),
                )
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FAILED_TRANSACTION_FEE_DELTA_MISMATCH",
            result.reasons,
        )

    def test_other_terminal_reason_is_not_reclassified(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_capital_reservations
                SET
                    status = ?,
                    terminal_at = ?,
                    terminal_reason = ?
                WHERE reservation_id = ?
                """,
                (
                    RELEASED,
                    5.0,
                    "PRE_SUBMIT_ABORT",
                    self.reservation_id,
                ),
            )

            connection.commit()
        finally:
            connection.close()

        result = (
            record_failed_transaction_and_release_reservation(
                **self.evidence()
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "FAILED_TRANSACTION_TERMINAL_REASON_MISMATCH",
            result.reasons,
        )

    def test_journal_insert_rolls_back_if_reservation_release_fails(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            init_journal_schema(
                connection
            )

            connection.execute(
                f"""
                CREATE TRIGGER block_failed_release
                BEFORE UPDATE OF status
                ON live_capital_reservations

                WHEN NEW.terminal_reason =
                    '{RECONCILED_FAILED_TRANSACTION_REASON}'

                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'blocked failed release'
                    );
                END
                """
            )

            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(
            sqlite3.IntegrityError
        ):
            record_failed_transaction_and_release_reservation(
                **self.evidence()
            )

        connection = get_connection(
            self.db_path
        )

        try:
            journal_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM live_transaction_journal
                    """
                ).fetchone()[0]
            )

            row = connection.execute(
                """
                SELECT status
                FROM live_capital_reservations
                WHERE reservation_id = ?
                """,
                (
                    self.reservation_id,
                ),
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(
            journal_count,
            0,
        )

        self.assertEqual(
            row["status"],
            SIGNED,
        )


if __name__ == "__main__":
    unittest.main()
