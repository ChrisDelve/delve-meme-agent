from __future__ import annotations

import unittest
from dataclasses import replace

from solders.pubkey import Pubkey

from src.execution.live_sell_transaction_receipt import (
    RESOLVED,
)
from src.execution.order_authorization import (
    WRAPPED_SOL_MINT,
)
from src.execution.successful_pump_sell_fill import (
    PROVEN,
    SUCCESSFUL_PUMP_SELL_FILL_VERSION,
    SuccessfulPumpSellFillResult,
)
from src.portfolio.live_positions import (
    CLOSED,
    OPEN,
)
from src.portfolio.live_reservations import (
    get_connection,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    CONSUMED,
    _load_claim,
)
from src.portfolio.live_sell_execution_records import (
    PASS as EXECUTION_PASS,
    load_live_sell_execution_record_read_only,
)
from src.portfolio.live_sell_transaction_journal import (
    load_live_sell_transaction_journal_entry_read_only,
)
from src.portfolio.live_sell_success_accounting import (
    PASS,
    RECONCILED_SUCCESSFUL_SELL_REASON,
    UNKNOWN,
    record_successful_sell_and_consume_claim,
)
from src.safety.token_safety_resolver import (
    TOKEN_PROGRAM,
    derive_associated_token_account,
)
from tests import (
    test_live_sell_execution_records
    as execution_record_tests,
)


class LiveSellSuccessAccountingTests(
    unittest.TestCase
):
    def setUp(self):
        self.helper = (
            execution_record_tests
            .LiveSellExecutionRecordTests(
                methodName=(
                    "test_valid_signed_artifact_is_durable"
                )
            )
        )
        self.helper.setUp()

        bind = self.helper.bind()
        self.assertEqual(
            bind.status,
            EXECUTION_PASS,
        )

        self.authorization = (
            self.helper.authorization
        )
        self.db_path = self.helper.db_path

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

        connection = get_connection(
            self.db_path
        )
        try:
            self.claim = _load_claim(
                connection=connection,
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
            )
        finally:
            connection.close()

        self.assertIsNotNone(
            self.claim
        )
        self.assertEqual(
            self.claim.status,
            ACTIVE,
        )

    def tearDown(self):
        self.helper.tearDown()

    def make_fill(
        self,
        *,
        quote_credit: int | None = None,
        fee_lamports: int = 5_000,
    ):
        minimum = (
            self.authorization
            .exit_execution
            .min_quote_out
        )

        if quote_credit is None:
            quote_credit = (
                max(
                    minimum,
                    100_000,
                )
                + 100_003
            )

        quote_pre = 20_000
        quote_post = (
            quote_pre
            + quote_credit
        )

        base_post = 17
        base_pre = (
            base_post
            + self.authorization.tokens_to_sell
        )

        wallet_pre = max(
            fee_lamports + 1_000_000,
            10_000_000,
        )
        wallet_post = (
            wallet_pre
            - fee_lamports
        )

        associated_quote_user = (
            derive_associated_token_account(
                owner=Pubkey.from_string(
                    self.authorization
                    .wallet_pubkey
                ),
                mint=Pubkey.from_string(
                    str(WRAPPED_SOL_MINT)
                ),
                token_program=TOKEN_PROGRAM,
            )
        )

        return SuccessfulPumpSellFillResult(
            resolver_version=(
                SUCCESSFUL_PUMP_SELL_FILL_VERSION
            ),
            status=PROVEN,
            reasons=(),
            authorization_sha256=(
                self.authorization
                .authorization_sha256
            ),
            transaction_signature=(
                self.execution
                .transaction_signature
            ),
            receipt_status=RESOLVED,
            receipt_slot=(
                self.execution
                .blockhash_rpc_slot
            ),
            block_time=1_700_000_000,
            mint=self.authorization.mint,
            wallet_pubkey=(
                self.authorization
                .wallet_pubkey
            ),
            base_token_program=(
                self.authorization
                .base_token_program
            ),
            associated_base_user=(
                self.authorization
                .associated_base_user
            ),
            quote_mint=str(
                WRAPPED_SOL_MINT
            ),
            quote_token_program=str(
                TOKEN_PROGRAM
            ),
            associated_quote_user=str(
                associated_quote_user
            ),
            authorized_token_amount=(
                self.authorization
                .tokens_to_sell
            ),
            min_quote_out=minimum,
            trade_event_token_amount=(
                self.authorization
                .tokens_to_sell
            ),
            base_token_pre_amount=(
                base_pre
            ),
            base_token_post_amount=(
                base_post
            ),
            base_token_debit=(
                self.authorization
                .tokens_to_sell
            ),
            quote_token_pre_amount=(
                quote_pre
            ),
            quote_token_post_amount=(
                quote_post
            ),
            quote_token_credit_lamports=(
                quote_credit
            ),
            fee_lamports=fee_lamports,
            wallet_pre_balance_lamports=(
                wallet_pre
            ),
            wallet_post_balance_lamports=(
                wallet_post
            ),
            wallet_balance_delta_lamports=(
                -fee_lamports
            ),
            trade_event_sol_amount=(
                quote_credit
            ),
            protocol_fee_lamports=0,
            creator_fee_lamports=0,
            cashback_lamports=0,
            buyback_fee_lamports=0,
            quote_amount=quote_credit,
            persisted_transaction_sha256=(
                self.execution
                .signed_transaction_sha256
            ),
            observed_transaction_sha256=(
                self.execution
                .signed_transaction_sha256
            ),
        )

    def query(
        self,
        sql,
        parameters=(),
    ):
        connection = get_connection(
            self.db_path
        )
        try:
            return connection.execute(
                sql,
                parameters,
            ).fetchall()
        finally:
            connection.close()

    def test_successful_sell_updates_positions_journal_and_claim_atomically(
        self,
    ):
        fill = self.make_fill()

        result = (
            record_successful_sell_and_consume_claim(
                authorization=(
                    self.authorization
                ),
                fill=fill,
                db_path=self.db_path,
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
            result.claim_status,
            CONSUMED,
        )
        self.assertEqual(
            result.terminal_reason,
            RECONCILED_SUCCESSFUL_SELL_REASON,
        )

        self.assertIsNotNone(
            result.accounting
        )

        accounting = result.accounting

        self.assertEqual(
            accounting.net_wallet_proceeds_lamports,
            max(
                0,
                fill.quote_token_credit_lamports
                - fill.fee_lamports,
            ),
        )

        self.assertEqual(
            sum(
                lot.allocated_net_proceeds_lamports
                for lot in accounting.lots
            ),
            accounting.net_wallet_proceeds_lamports,
        )

        for claim_lot, ledger_lot in zip(
            self.claim.allocation.allocations,
            accounting.lots,
            strict=True,
        ):
            rows = self.query(
                """
                SELECT *
                FROM live_positions
                WHERE position_id = ?
                """,
                (
                    claim_lot.position_id,
                ),
            )

            self.assertEqual(
                len(rows),
                1,
            )

            row = rows[0]

            self.assertEqual(
                int(row["tokens_held"]),
                claim_lot.tokens_after,
            )
            self.assertEqual(
                int(
                    row[
                        "remaining_exposure_lamports"
                    ]
                ),
                claim_lot.exposure_after_lamports,
            )
            self.assertEqual(
                int(
                    row[
                        "remaining_cost_basis_lamports"
                    ]
                ),
                claim_lot.cost_basis_after_lamports,
            )
            self.assertEqual(
                int(
                    row[
                        "cumulative_net_proceeds_lamports"
                    ]
                ),
                ledger_lot
                .cumulative_net_proceeds_after_lamports,
            )
            self.assertEqual(
                int(
                    row[
                        "cumulative_realized_pnl_lamports"
                    ]
                ),
                ledger_lot
                .cumulative_realized_pnl_after_lamports,
            )

            expected_status = (
                CLOSED
                if claim_lot.tokens_after == 0
                else OPEN
            )

            self.assertEqual(
                str(row["status"]),
                expected_status,
            )

        journal = self.query(
            """
            SELECT outcome,
                   transaction_error_json
            FROM live_sell_transaction_journal
            WHERE authorization_sha256 = ?
            """,
            (
                self.authorization
                .authorization_sha256,
            ),
        )

        self.assertEqual(
            len(journal),
            1,
        )
        self.assertEqual(
            str(journal[0]["outcome"]),
            "SUCCESS",
        )
        self.assertEqual(
            str(
                journal[0][
                    "transaction_error_json"
                ]
            ),
            "null",
        )

    def test_exact_retry_is_idempotent(
        self,
    ):
        fill = self.make_fill()

        first = (
            record_successful_sell_and_consume_claim(
                authorization=(
                    self.authorization
                ),
                fill=fill,
                db_path=self.db_path,
            )
        )

        second = (
            record_successful_sell_and_consume_claim(
                authorization=(
                    self.authorization
                ),
                fill=fill,
                db_path=self.db_path,
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
            second.status,
            PASS,
        )
        self.assertFalse(
            second.changed
        )

        self.assertEqual(
            first.accounting,
            second.accounting,
        )

    def test_stale_position_state_rolls_back_entire_transition(
        self,
    ):
        target = (
            self.claim
            .allocation
            .allocations[0]
        )

        connection = get_connection(
            self.db_path
        )
        try:
            connection.execute(
                """
                UPDATE live_positions
                SET cumulative_net_proceeds_lamports =
                    cumulative_net_proceeds_lamports + 1
                WHERE position_id = ?
                """,
                (
                    target.position_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        result = (
            record_successful_sell_and_consume_claim(
                authorization=(
                    self.authorization
                ),
                fill=self.make_fill(),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "SUCCESSFUL_SELL_POSITION_STATE_MISMATCH",
            result.reasons,
        )
        self.assertFalse(
            result.changed
        )

        self.assertIsNone(
            load_live_sell_transaction_journal_entry_read_only(
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            self.query(
                """
                SELECT status
                FROM live_sell_inventory_claims
                WHERE authorization_sha256 = ?
                """,
                (
                    self.authorization
                    .authorization_sha256,
                ),
            )[0]["status"],
            ACTIVE,
        )

    def test_fee_larger_than_credit_records_zero_net_and_realized_loss(
        self,
    ):
        credit = max(
            self.authorization
            .exit_execution
            .min_quote_out,
            1,
        )

        fill = self.make_fill(
            quote_credit=credit,
            fee_lamports=(
                credit + 1
            ),
        )

        result = (
            record_successful_sell_and_consume_claim(
                authorization=(
                    self.authorization
                ),
                fill=fill,
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        accounting = result.accounting

        self.assertEqual(
            accounting.net_wallet_proceeds_lamports,
            0,
        )

        self.assertEqual(
            accounting.total_realized_pnl_lamports,
            -accounting
            .total_cost_basis_reduction_lamports,
        )

        self.assertTrue(
            all(
                lot.allocated_net_proceeds_lamports
                == 0
                for lot in accounting.lots
            )
        )

    def test_proceeds_remainder_is_conserved_exactly(
        self,
    ):
        self.assertGreaterEqual(
            len(
                self.claim
                .allocation
                .allocations
            ),
            2,
        )

        fill = self.make_fill(
            quote_credit=(
                max(
                    self.authorization
                    .exit_execution
                    .min_quote_out,
                    1_000_000,
                )
                + 7
            ),
            fee_lamports=5_003,
        )

        result = (
            record_successful_sell_and_consume_claim(
                authorization=(
                    self.authorization
                ),
                fill=fill,
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        accounting = result.accounting

        self.assertEqual(
            sum(
                lot.allocated_net_proceeds_lamports
                for lot in accounting.lots
            ),
            accounting.net_wallet_proceeds_lamports,
        )

        self.assertEqual(
            accounting.total_realized_pnl_lamports,
            accounting.net_wallet_proceeds_lamports
            - accounting
            .total_cost_basis_reduction_lamports,
        )

    def test_fill_identity_mismatch_never_mutates(
        self,
    ):
        fill = replace(
            self.make_fill(),
            transaction_signature=str(
                Pubkey.new_unique()
            ),
        )

        result = (
            record_successful_sell_and_consume_claim(
                authorization=(
                    self.authorization
                ),
                fill=fill,
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

        claim = self.query(
            """
            SELECT status
            FROM live_sell_inventory_claims
            WHERE authorization_sha256 = ?
            """,
            (
                self.authorization
                .authorization_sha256,
            ),
        )[0]

        self.assertEqual(
            claim["status"],
            ACTIVE,
        )


if __name__ == "__main__":
    unittest.main()
