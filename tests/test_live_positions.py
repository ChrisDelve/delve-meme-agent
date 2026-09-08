import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.execution.order_authorization import (
    WRAPPED_SOL_MINT,
)
from src.execution.successful_pump_buy_fill import (
    SUCCESSFUL_PUMP_BUY_FILL_VERSION,
)
from src.portfolio.live_positions import (
    BLOCK,
    LIVE_POSITION_VERSION,
    OPEN,
    PASS,
    RECONCILED_SUCCESSFUL_BUY_REASON,
    UNKNOWN,
    init_schema as init_position_schema,
    load_live_position_read_only,
    record_successful_buy_and_open_position,
)
from src.portfolio.live_reservations import (
    ACTIVE,
    RELEASED,
    RESERVATION_VERSION,
    SIGNED,
    SUBMITTED,
    get_connection,
    init_schema as init_reservation_schema,
)
from src.portfolio.live_transaction_journal import (
    LIVE_TRANSACTION_JOURNAL_VERSION,
    SUCCESS,
    init_schema as init_journal_schema,
)


class LivePositionAccountingTests(
    unittest.TestCase
):
    def setUp(self):
        self.temp_dir = (
            TemporaryDirectory()
        )

        self.db_path = (
            Path(
                self.temp_dir.name
            )
            / "live.db"
        )

        self.reservation_id = (
            "successful-buy-test"
        )

        self.signature = (
            "SuccessfulBuySignature"
        )

        self.transaction_sha256 = (
            "22" * 32
        )

        self.wallet_pubkey = (
            "SuccessfulWallet"
        )

        self.mint = (
            "SuccessfulMint"
        )

        self.insert_reservation()

    def tearDown(self):
        self.temp_dir.cleanup()

    def insert_reservation(
        self,
        *,
        reservation_id=None,
        signature=None,
        status=SIGNED,
        mint=None,
        spend_lamports=1_000_000,
        wallet_cost_lamports=1_100_000,
        terminal_at=None,
        terminal_reason=None,
    ):
        if reservation_id is None:
            reservation_id = (
                self.reservation_id
            )

        if signature is None:
            signature = (
                self.signature
            )

        if mint is None:
            mint = self.mint

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
                    mint,
                    "BUY",
                    self.wallet_pubkey,
                    spend_lamports,
                    wallet_cost_lamports,
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
                    signature,
                    "33" * 32,
                    self.transaction_sha256,
                    b"signed-transaction",
                    "BlockhashSuccess",
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
            "reservation_id":
                self.reservation_id,

            "fill_resolver_version":
                SUCCESSFUL_PUMP_BUY_FILL_VERSION,

            "transaction_signature":
                self.signature,

            "signed_transaction_sha256":
                self.transaction_sha256,

            "observed_transaction_sha256":
                self.transaction_sha256,

            "entry_slot": 230,
            "block_time": 1_800_000_000,

            "mint": self.mint,

            "wallet_pubkey":
                self.wallet_pubkey,

            "base_token_program":
                "TokenProgram",

            "associated_base_user":
                "AssociatedBaseUser",

            "authorized_token_amount":
                123_456_789,

            "max_sol_cost":
                1_000_000,

            "trade_event_token_amount":
                123_456_789,

            "token_pre_amount":
                100,

            "token_post_amount":
                123_456_889,

            "token_delta":
                123_456_789,

            "fee_lamports": 5_000,

            "wallet_pre_balance_lamports":
                10_000_000,

            "wallet_post_balance_lamports":
                8_950_000,

            "wallet_balance_delta_lamports":
                -1_050_000,

            "wallet_cost_lamports":
                1_050_000,

            "trade_event_sol_amount":
                850_000,

            "protocol_fee_lamports":
                10_000,

            "creator_fee_lamports":
                5_000,

            "cashback_lamports": 0,
            "buyback_fee_lamports": 0,

            "quote_mint":
                str(
                    WRAPPED_SOL_MINT
                ),

            "quote_amount": 850_000,

            "db_path": self.db_path,
        }

        values.update(
            overrides
        )

        return values

    def held_totals(self):
        connection = get_connection(
            self.db_path
        )

        try:
            row = connection.execute(
                """
                SELECT
                    COALESCE(
                        SUM(spend_lamports),
                        0
                    ) AS exposure,

                    COALESCE(
                        SUM(wallet_cost_lamports),
                        0
                    ) AS cash,

                    COUNT(*) AS count

                FROM live_capital_reservations

                WHERE status IN (?, ?, ?)
                """,
                (
                    ACTIVE,
                    SIGNED,
                    SUBMITTED,
                ),
            ).fetchone()

            return (
                int(row["exposure"]),
                int(row["cash"]),
                int(row["count"]),
            )

        finally:
            connection.close()

    def rows(self):
        connection = get_connection(
            self.db_path
        )

        try:
            journal = None
            position = None

            try:
                journal = (
                    connection.execute(
                        """
                        SELECT *
                        FROM live_transaction_journal
                        WHERE reservation_id = ?
                        """,
                        (
                            self.reservation_id,
                        ),
                    ).fetchone()
                )
            except sqlite3.OperationalError:
                pass

            try:
                position = (
                    connection.execute(
                        """
                        SELECT *
                        FROM live_positions
                        WHERE reservation_id = ?
                        """,
                        (
                            self.reservation_id,
                        ),
                    ).fetchone()
                )
            except sqlite3.OperationalError:
                pass

            reservation = (
                connection.execute(
                    """
                    SELECT *
                    FROM live_capital_reservations
                    WHERE reservation_id = ?
                    """,
                    (
                        self.reservation_id,
                    ),
                ).fetchone()
            )

            return (
                journal,
                position,
                reservation,
            )

        finally:
            connection.close()

    def test_signed_success_atomically_journals_opens_and_releases(
        self,
    ):
        (
            exposure_before,
            cash_before,
            held_before,
        ) = self.held_totals()

        self.assertEqual(
            exposure_before,
            1_000_000,
        )

        self.assertEqual(
            cash_before,
            1_100_000,
        )

        self.assertEqual(
            held_before,
            1,
        )

        result = (
            record_successful_buy_and_open_position(
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

        self.assertIsNotNone(
            result.position
        )

        position = result.position

        self.assertEqual(
            position.position_version,
            LIVE_POSITION_VERSION,
        )

        self.assertEqual(
            position.status,
            OPEN,
        )

        self.assertEqual(
            position.token_pre_amount,
            100,
        )

        self.assertEqual(
            position.token_post_amount,
            123_456_889,
        )

        self.assertEqual(
            position.entry_tokens,
            123_456_789,
        )

        self.assertEqual(
            position.tokens_held,
            123_456_789,
        )

        self.assertEqual(
            position.entry_exposure_lamports,
            1_000_000,
        )

        self.assertEqual(
            position.remaining_exposure_lamports,
            1_000_000,
        )

        self.assertEqual(
            position.entry_wallet_cost_lamports,
            1_050_000,
        )

        self.assertEqual(
            position.remaining_cost_basis_lamports,
            1_050_000,
        )

        (
            exposure_after,
            cash_after,
            held_after,
        ) = self.held_totals()

        self.assertEqual(
            exposure_after,
            0,
        )

        self.assertEqual(
            cash_after,
            0,
        )

        self.assertEqual(
            held_after,
            0,
        )

        #
        # Exposure did not disappear. Ownership moved
        # from the reservation ledger to the live lot.
        #
        self.assertEqual(
            position.remaining_exposure_lamports,
            exposure_before,
        )

        journal, _, reservation = (
            self.rows()
        )

        self.assertIsNotNone(
            journal
        )

        self.assertEqual(
            journal["journal_version"],
            LIVE_TRANSACTION_JOURNAL_VERSION,
        )

        self.assertEqual(
            journal["outcome"],
            SUCCESS,
        )

        self.assertEqual(
            journal[
                "transaction_error_json"
            ],
            "null",
        )

        self.assertEqual(
            reservation["status"],
            RELEASED,
        )

        self.assertEqual(
            reservation[
                "terminal_reason"
            ],
            RECONCILED_SUCCESSFUL_BUY_REASON,
        )

        self.assertEqual(
            float(
                reservation[
                    "terminal_at"
                ]
            ),
            float(
                journal[
                    "recorded_at"
                ]
            ),
        )

        self.assertEqual(
            float(
                reservation[
                    "terminal_at"
                ]
            ),
            position.created_at,
        )

    def test_submitted_success_preserves_submission_metadata(
        self,
    ):
        self.temp_dir.cleanup()

        self.temp_dir = (
            TemporaryDirectory()
        )

        self.db_path = (
            Path(
                self.temp_dir.name
            )
            / "live.db"
        )

        self.insert_reservation(
            status=SUBMITTED,
        )

        result = (
            record_successful_buy_and_open_position(
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
            row[
                "submission_attempt_count"
            ],
            1,
        )

        self.assertEqual(
            float(
                row[
                    "submission_started_at"
                ]
            ),
            3.5,
        )

        self.assertEqual(
            float(
                row["submitted_at"]
            ),
            4.0,
        )

    def test_exact_retry_is_idempotent(
        self,
    ):
        first = (
            record_successful_buy_and_open_position(
                **self.evidence()
            )
        )

        second = (
            record_successful_buy_and_open_position(
                **self.evidence()
            )
        )

        self.assertEqual(
            first.status,
            PASS,
        )

        self.assertEqual(
            second.status,
            PASS,
        )

        self.assertTrue(
            first.changed
        )

        self.assertFalse(
            second.changed
        )

        connection = get_connection(
            self.db_path
        )

        try:
            journal_count = (
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM live_transaction_journal
                    """
                ).fetchone()[0]
            )

            position_count = (
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM live_positions
                    """
                ).fetchone()[0]
            )
        finally:
            connection.close()

        self.assertEqual(
            journal_count,
            1,
        )

        self.assertEqual(
            position_count,
            1,
        )

    def test_idempotent_retry_is_evidence_bound(
        self,
    ):
        first = (
            record_successful_buy_and_open_position(
                **self.evidence()
            )
        )

        self.assertEqual(
            first.status,
            PASS,
        )

        second = (
            record_successful_buy_and_open_position(
                **self.evidence(
                    entry_slot=231,
                )
            )
        )

        self.assertEqual(
            second.status,
            UNKNOWN,
        )

        self.assertIn(
            "SUCCESSFUL_BUY_TERMINAL_STATE_INCOHERENT",
            second.reasons,
        )

    def test_idempotent_retry_binds_absolute_token_balance_evidence(
        self,
    ):
        first = (
            record_successful_buy_and_open_position(
                **self.evidence()
            )
        )

        self.assertEqual(
            first.status,
            PASS,
        )

        #
        # Same token delta but different absolute
        # ATA balances cannot describe the exact
        # same historical fill.
        #
        second = (
            record_successful_buy_and_open_position(
                **self.evidence(
                    token_pre_amount=101,
                    token_post_amount=(
                        123_456_890
                    ),
                )
            )
        )

        self.assertEqual(
            second.status,
            UNKNOWN,
        )

        self.assertIn(
            "SUCCESSFUL_BUY_TERMINAL_STATE_INCOHERENT",
            second.reasons,
        )

    def test_active_reservation_cannot_open_live_position(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_capital_reservations
                SET status = ?
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
            record_successful_buy_and_open_position(
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

    def test_non_buy_reservation_cannot_open_live_position(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_capital_reservations
                SET side = 'SELL'
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
            record_successful_buy_and_open_position(
                **self.evidence()
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SUCCESSFUL_BUY_RESERVATION_EVIDENCE_MISMATCH",
            result.reasons,
        )

    def test_max_sol_cost_must_equal_reserved_exposure(
        self,
    ):
        result = (
            record_successful_buy_and_open_position(
                **self.evidence(
                    max_sol_cost=999_999,
                )
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SUCCESSFUL_BUY_RESERVATION_EVIDENCE_MISMATCH",
            result.reasons,
        )

    def test_actual_wallet_cost_cannot_exceed_reserved_liability(
        self,
    ):
        evidence = self.evidence(
            wallet_post_balance_lamports=(
                8_899_999
            ),
            wallet_balance_delta_lamports=(
                -1_100_001
            ),
            wallet_cost_lamports=(
                1_100_001
            ),
        )

        result = (
            record_successful_buy_and_open_position(
                **evidence
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SUCCESSFUL_BUY_RESERVATION_EVIDENCE_MISMATCH",
            result.reasons,
        )

    def test_token_evidence_must_agree(
        self,
    ):
        result = (
            record_successful_buy_and_open_position(
                **self.evidence(
                    token_delta=123_456_788,
                )
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SUCCESSFUL_BUY_TOKEN_EVIDENCE_MISMATCH",
            result.reasons,
        )

    def test_wallet_cost_evidence_must_agree(
        self,
    ):
        result = (
            record_successful_buy_and_open_position(
                **self.evidence(
                    wallet_cost_lamports=(
                        1_049_999
                    ),
                )
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SUCCESSFUL_BUY_WALLET_COST_MISMATCH",
            result.reasons,
        )

    def test_preexisting_accounting_row_blocks_atomic_transition(
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
                """
                INSERT INTO live_transaction_journal (
                    transaction_signature,
                    reservation_id,
                    journal_version,
                    wallet_pubkey,
                    mint,
                    side,
                    signed_transaction_sha256,
                    receipt_transaction_sha256,
                    receipt_resolver_version,
                    slot,
                    block_time,
                    outcome,
                    transaction_error_json,
                    fee_lamports,
                    fee_payer_pubkey,
                    fee_payer_pre_balance_lamports,
                    fee_payer_post_balance_lamports,
                    fee_payer_balance_delta_lamports,
                    last_valid_block_height,
                    blockhash_rpc_slot,
                    recorded_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    self.signature,
                    self.reservation_id,
                    LIVE_TRANSACTION_JOURNAL_VERSION,
                    self.wallet_pubkey,
                    self.mint,
                    "BUY",
                    self.transaction_sha256,
                    self.transaction_sha256,
                    "signed-transaction-receipt-v1",
                    230,
                    1_800_000_000,
                    SUCCESS,
                    "null",
                    5_000,
                    self.wallet_pubkey,
                    10_000_000,
                    8_950_000,
                    -1_050_000,
                    350,
                    200,
                    9.0,
                ),
            )

            connection.commit()
        finally:
            connection.close()

        result = (
            record_successful_buy_and_open_position(
                **self.evidence()
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SUCCESSFUL_BUY_ACCOUNTING_STATE_INCONSISTENT",
            result.reasons,
        )

    def test_atomic_rollback_if_position_insert_fails(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            init_journal_schema(
                connection
            )

            init_position_schema(
                connection
            )

            connection.execute(
                """
                CREATE TRIGGER block_live_position_insert
                BEFORE INSERT
                ON live_positions

                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'block-live-position-insert'
                    );
                END;
                """
            )

            connection.commit()

        finally:
            connection.close()

        result = (
            record_successful_buy_and_open_position(
                **self.evidence()
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SUCCESSFUL_BUY_DATABASE_ERROR",
            result.reasons,
        )

        #
        # Journal insert happens before position
        # insert. Therefore this proves the earlier
        # journal write rolls back when write #2
        # fails.
        #
        journal, position, reservation = (
            self.rows()
        )

        self.assertIsNone(
            journal
        )

        self.assertIsNone(
            position
        )

        self.assertEqual(
            reservation["status"],
            SIGNED,
        )

        self.assertIsNone(
            reservation["terminal_at"]
        )

        self.assertIsNone(
            reservation["terminal_reason"]
        )

        (
            exposure,
            cash,
            held,
        ) = self.held_totals()

        self.assertEqual(
            exposure,
            1_000_000,
        )

        self.assertEqual(
            cash,
            1_100_000,
        )

        self.assertEqual(
            held,
            1,
        )

    def test_atomic_rollback_if_reservation_release_fails(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            init_journal_schema(
                connection
            )

            init_position_schema(
                connection
            )

            connection.execute(
                """
                CREATE TRIGGER block_success_release
                BEFORE UPDATE
                ON live_capital_reservations

                WHEN
                    NEW.terminal_reason
                    = 'RECONCILED_SUCCESSFUL_BUY'

                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'block-success-release'
                    );
                END;
                """
            )

            connection.commit()
        finally:
            connection.close()

        result = (
            record_successful_buy_and_open_position(
                **self.evidence()
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SUCCESSFUL_BUY_DATABASE_ERROR",
            result.reasons,
        )

        journal, position, reservation = (
            self.rows()
        )

        self.assertIsNone(
            journal
        )

        self.assertIsNone(
            position
        )

        self.assertEqual(
            reservation["status"],
            SIGNED,
        )

    def test_same_mint_can_have_multiple_successful_entry_lots(
        self,
    ):
        first = (
            record_successful_buy_and_open_position(
                **self.evidence()
            )
        )

        self.assertEqual(
            first.status,
            PASS,
        )

        second_reservation = (
            "successful-buy-test-2"
        )

        second_signature = (
            "SuccessfulBuySignature2"
        )

        self.insert_reservation(
            reservation_id=(
                second_reservation
            ),
            signature=(
                second_signature
            ),
            mint=self.mint,
        )

        second_evidence = (
            self.evidence(
                reservation_id=(
                    second_reservation
                ),
                transaction_signature=(
                    second_signature
                ),
                token_pre_amount=(
                    123_456_889
                ),
                token_post_amount=(
                    246_913_678
                ),
            )
        )

        second = (
            record_successful_buy_and_open_position(
                **second_evidence
            )
        )

        self.assertEqual(
            second.status,
            PASS,
        )

        connection = get_connection(
            self.db_path
        )

        try:
            count = connection.execute(
                """
                SELECT COUNT(*)
                FROM live_positions
                WHERE mint = ?
                """,
                (
                    self.mint,
                ),
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(
            count,
            2,
        )

    def test_read_only_loader_returns_committed_position(
        self,
    ):
        transition = (
            record_successful_buy_and_open_position(
                **self.evidence()
            )
        )

        self.assertEqual(
            transition.status,
            PASS,
        )

        position = (
            load_live_position_read_only(
                reservation_id=(
                    self.reservation_id
                ),
                db_path=self.db_path,
            )
        )

        self.assertIsNotNone(
            position
        )

        self.assertEqual(
            position.entry_signature,
            self.signature,
        )

        self.assertEqual(
            position.status,
            OPEN,
        )


if __name__ == "__main__":
    unittest.main()
