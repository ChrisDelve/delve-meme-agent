from dataclasses import replace
import time
import unittest
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from src.execution.live_sell_failed_reconciliation import (
    BLOCK,
    HOLD,
    LIVE_SELL_FAILED_RECONCILIATION_VERSION,
    RECONCILED,
    UNKNOWN,
    reconcile_failed_live_sell,
)
from src.execution.live_sell_transaction_receipt import (
    BLOCK as RECEIPT_BLOCK,
    LIVE_SELL_TRANSACTION_RECEIPT_VERSION,
    RESOLVED as RECEIPT_RESOLVED,
    UNKNOWN as RECEIPT_UNKNOWN,
    LiveSellTransactionReceiptResult,
)
from src.execution.live_sell_transaction_status import (
    KNOWN,
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
    load_live_sell_execution_record_read_only,
)
from src.portfolio.live_sell_transaction_journal import (
    BLOCK as JOURNAL_BLOCK,
    FAILED,
    PASS as JOURNAL_PASS,
    RECONCILED_FAILED_SELL_REASON,
    UNKNOWN as JOURNAL_UNKNOWN,
    FailedSellLedgerResult,
    load_live_sell_transaction_journal_entry_read_only,
)
from tests import (
    test_live_sell_submission_boundary
    as submission_boundary_tests,
)


MODULE = (
    "src.execution."
    "live_sell_failed_reconciliation"
)


class LiveSellFailedReconciliationTests(
    unittest.IsolatedAsyncioTestCase
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

        self.fee_lamports = 5_000
        self.pre_balance = 2_000_000
        self.post_balance = (
            self.pre_balance
            - self.fee_lamports
        )

    def tearDown(self):
        self.helper.tearDown()

    def receipt(
        self,
        *,
        status=RECEIPT_RESOLVED,
        reasons=(),
        resolver_version=(
            LIVE_SELL_TRANSACTION_RECEIPT_VERSION
        ),
        authorization_sha256=None,
        transaction_signature=None,
        execution_status=None,
        status_observation_state=KNOWN,
        status_transaction_slot=230,
        receipt_slot=230,
        transaction_error="DEFAULT",
        fee_lamports=None,
        fee_payer_pubkey=None,
        pre_balance=None,
        post_balance=None,
        balance_delta=None,
        persisted_transaction_sha256=None,
        receipt_transaction_sha256=None,
        last_valid_block_height=None,
        blockhash_rpc_slot=None,
    ):
        if authorization_sha256 is None:
            authorization_sha256 = (
                self.authorization_sha256
            )

        if transaction_signature is None:
            transaction_signature = (
                self.execution
                .transaction_signature
            )

        if execution_status is None:
            execution_status = (
                self.execution.status
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
                self.execution.wallet_pubkey
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
                post_balance
                - pre_balance
            )

        if persisted_transaction_sha256 is None:
            persisted_transaction_sha256 = (
                self.execution
                .signed_transaction_sha256
            )

        if receipt_transaction_sha256 is None:
            receipt_transaction_sha256 = (
                persisted_transaction_sha256
            )

        if last_valid_block_height is None:
            last_valid_block_height = (
                self.execution
                .last_valid_block_height
            )

        if blockhash_rpc_slot is None:
            blockhash_rpc_slot = (
                self.execution
                .blockhash_rpc_slot
            )

        return LiveSellTransactionReceiptResult(
            resolver_version=(
                resolver_version
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            transaction_signature=(
                transaction_signature
            ),
            execution_status=(
                execution_status
            ),
            status_observation_state=(
                status_observation_state
            ),
            status_transaction_slot=(
                status_transaction_slot
            ),
            receipt_slot=(
                receipt_slot
            ),
            block_time=1_800_000_000,
            transaction_error=(
                transaction_error
            ),
            fee_lamports=(
                fee_lamports
            ),
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
            persisted_transaction_sha256=(
                persisted_transaction_sha256
            ),
            receipt_transaction_sha256=(
                receipt_transaction_sha256
            ),
            last_valid_block_height=(
                last_valid_block_height
            ),
            blockhash_rpc_slot=(
                blockhash_rpc_slot
            ),
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

    async def run_with_receipt(
        self,
        receipt,
    ):
        with patch(
            f"{MODULE}."
            "resolve_live_sell_transaction_receipt",
            AsyncMock(
                return_value=receipt
            ),
        ) as receipt_mock:
            result = await (
                reconcile_failed_live_sell(
                    authorization=(
                        self.authorization
                    ),
                    db_path=self.db_path,
                )
            )

        return (
            result,
            receipt_mock,
        )

    async def test_failed_sell_reconciles_end_to_end_without_position_mutation(
        self,
    ):
        positions_before = (
            self.helper.position_snapshot()
        )

        result, receipt_mock = (
            await self.run_with_receipt(
                self.receipt()
            )
        )

        self.assertEqual(
            result.executor_version,
            LIVE_SELL_FAILED_RECONCILIATION_VERSION,
        )

        self.assertEqual(
            result.status,
            RECONCILED,
        )

        self.assertTrue(
            result.changed
        )

        self.assertEqual(
            result.journal_outcome,
            FAILED,
        )

        self.assertEqual(
            result.claim_status,
            RELEASED,
        )

        self.assertEqual(
            result.terminal_reason,
            RECONCILED_FAILED_SELL_REASON,
        )

        receipt_mock.assert_awaited_once()

        entry = (
            load_live_sell_transaction_journal_entry_read_only(
                authorization_sha256=(
                    self.authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertIsNotNone(
            entry
        )

        self.assertEqual(
            entry.outcome,
            FAILED,
        )

        self.assertEqual(
            self.helper.position_snapshot(),
            positions_before,
        )

    async def test_exact_retry_recovers_locally_without_receipt_rpc(
        self,
    ):
        first, _ = (
            await self.run_with_receipt(
                self.receipt()
            )
        )

        self.assertEqual(
            first.status,
            RECONCILED,
        )

        with patch(
            f"{MODULE}."
            "resolve_live_sell_transaction_receipt",
            AsyncMock(
                side_effect=AssertionError(
                    "receipt resolver must not run"
                )
            ),
        ) as receipt_mock:
            second = await (
                reconcile_failed_live_sell(
                    authorization=(
                        self.authorization
                    ),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            second.status,
            RECONCILED,
        )

        self.assertFalse(
            second.changed
        )

        receipt_mock.assert_not_awaited()

    async def test_receipt_block_holds_without_mutation(
        self,
    ):
        result, _ = (
            await self.run_with_receipt(
                self.receipt(
                    status=RECEIPT_BLOCK,
                    reasons=(
                        "TRANSACTION_NOT_KNOWN",
                    ),
                    receipt_slot=None,
                )
            )
        )

        self.assertEqual(
            result.status,
            HOLD,
        )

        self.assertEqual(
            self.load_claim_any().status,
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

    async def test_receipt_unknown_propagates_without_mutation(
        self,
    ):
        result, _ = (
            await self.run_with_receipt(
                self.receipt(
                    status=RECEIPT_UNKNOWN,
                    reasons=(
                        "RPC_UNCERTAIN",
                    ),
                    receipt_slot=None,
                )
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_RECEIPT_UNKNOWN",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    async def test_successful_sell_is_held_for_fill_reconciliation(
        self,
    ):
        result, _ = (
            await self.run_with_receipt(
                self.receipt(
                    transaction_error=None
                )
            )
        )

        self.assertEqual(
            result.status,
            HOLD,
        )

        self.assertIn(
            "SUCCESSFUL_SELL_REQUIRES_FILL_RECONCILIATION",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    async def test_receipt_resolver_version_mismatch_is_unknown(
        self,
    ):
        result, _ = (
            await self.run_with_receipt(
                self.receipt(
                    resolver_version=(
                        "wrong-receipt-version"
                    )
                )
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FAILED_SELL_RECEIPT_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    async def test_receipt_signature_binding_mismatch_is_unknown(
        self,
    ):
        result, _ = (
            await self.run_with_receipt(
                self.receipt(
                    transaction_signature=(
                        "DifferentSignature"
                    )
                )
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FAILED_SELL_RECEIPT_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    async def test_failed_receipt_fee_delta_mismatch_is_unknown(
        self,
    ):
        result, _ = (
            await self.run_with_receipt(
                self.receipt(
                    post_balance=(
                        self.post_balance
                        - 100
                    ),
                    balance_delta=(
                        -self.fee_lamports
                        - 100
                    ),
                )
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FAILED_SELL_RECEIPT_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    async def test_execution_artifact_change_after_receipt_is_unknown(
        self,
    ):
        async def mutate_during_receipt(
            **kwargs,
        ):
            connection = get_connection(
                self.db_path
            )

            try:
                connection.execute(
                    """
                    UPDATE live_sell_execution_records

                    SET signed_at = signed_at - 0.5

                    WHERE authorization_sha256 = ?
                    """,
                    (
                        self.authorization_sha256,
                    ),
                )

                connection.commit()

            finally:
                connection.close()

            return self.receipt()

        with patch(
            f"{MODULE}."
            "resolve_live_sell_transaction_receipt",
            AsyncMock(
                side_effect=(
                    mutate_during_receipt
                )
            ),
        ):
            result = await (
                reconcile_failed_live_sell(
                    authorization=(
                        self.authorization
                    ),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_EXECUTION_CHANGED_DURING_RECEIPT_CHECK",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    async def test_consumed_claim_blocks_before_receipt_resolution(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            now = time.time()

            connection.execute(
                """
                UPDATE live_sell_inventory_claims

                SET
                    status = ?,
                    terminal_at = ?,
                    terminal_reason = ?

                WHERE authorization_sha256 = ?
                """,
                (
                    CONSUMED,
                    now,
                    "TEST_CONSUMED",
                    self.authorization_sha256,
                ),
            )

            connection.commit()

        finally:
            connection.close()

        with patch(
            f"{MODULE}."
            "resolve_live_sell_transaction_receipt",
            AsyncMock(),
        ) as receipt_mock:
            result = await (
                reconcile_failed_live_sell(
                    authorization=(
                        self.authorization
                    ),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SELL_CLAIM_ALREADY_CONSUMED",
            result.reasons,
        )

        receipt_mock.assert_not_awaited()

    async def test_released_wrong_terminal_reason_blocks_locally(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            now = time.time()

            connection.execute(
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
                    "WRONG_REASON",
                    self.authorization_sha256,
                ),
            )

            connection.commit()

        finally:
            connection.close()

        with patch(
            f"{MODULE}."
            "resolve_live_sell_transaction_receipt",
            AsyncMock(),
        ) as receipt_mock:
            result = await (
                reconcile_failed_live_sell(
                    authorization=(
                        self.authorization
                    ),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "FAILED_SELL_TERMINAL_REASON_MISMATCH",
            result.reasons,
        )

        receipt_mock.assert_not_awaited()

    async def test_tampered_terminal_journal_is_unknown_on_retry(
        self,
    ):
        first, _ = (
            await self.run_with_receipt(
                self.receipt()
            )
        )

        self.assertEqual(
            first.status,
            RECONCILED,
        )

        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_sell_transaction_journal

                SET fee_lamports = fee_lamports + 1

                WHERE authorization_sha256 = ?
                """,
                (
                    self.authorization_sha256,
                ),
            )

            connection.commit()

        finally:
            connection.close()

        with patch(
            f"{MODULE}."
            "resolve_live_sell_transaction_receipt",
            AsyncMock(),
        ) as receipt_mock:
            second = await (
                reconcile_failed_live_sell(
                    authorization=(
                        self.authorization
                    ),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            second.status,
            UNKNOWN,
        )

        self.assertIn(
            "FAILED_SELL_TERMINAL_INCOHERENT",
            second.reasons,
        )

        receipt_mock.assert_not_awaited()

    async def test_receipt_resolution_exception_is_unknown(
        self,
    ):
        with patch(
            f"{MODULE}."
            "resolve_live_sell_transaction_receipt",
            AsyncMock(
                side_effect=RuntimeError(
                    "rpc failure"
                )
            ),
        ):
            result = await (
                reconcile_failed_live_sell(
                    authorization=(
                        self.authorization
                    ),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_RECEIPT_RESOLUTION_FAILED",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    async def test_ledger_block_propagates_without_claim_release(
        self,
    ):
        fake_ledger = FailedSellLedgerResult(
            status=JOURNAL_BLOCK,
            reasons=(
                "TEST_BLOCK",
            ),
            entry=None,
            authorization_sha256=(
                self.authorization_sha256
            ),
            claim_status=ACTIVE,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

        with (
            patch(
                f"{MODULE}."
                "resolve_live_sell_transaction_receipt",
                AsyncMock(
                    return_value=(
                        self.receipt()
                    )
                ),
            ),
            patch(
                f"{MODULE}."
                "record_failed_sell_and_release_claim",
                MagicMock(
                    return_value=fake_ledger
                ),
            ),
        ):
            result = await (
                reconcile_failed_live_sell(
                    authorization=(
                        self.authorization
                    ),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "FAILED_SELL_LEDGER_BLOCKED",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    async def test_ledger_unknown_propagates_without_claim_release(
        self,
    ):
        fake_ledger = FailedSellLedgerResult(
            status=JOURNAL_UNKNOWN,
            reasons=(
                "TEST_UNKNOWN",
            ),
            entry=None,
            authorization_sha256=(
                self.authorization_sha256
            ),
            claim_status=ACTIVE,
            terminal_at=None,
            terminal_reason=None,
            changed=False,
        )

        with (
            patch(
                f"{MODULE}."
                "resolve_live_sell_transaction_receipt",
                AsyncMock(
                    return_value=(
                        self.receipt()
                    )
                ),
            ),
            patch(
                f"{MODULE}."
                "record_failed_sell_and_release_claim",
                MagicMock(
                    return_value=fake_ledger
                ),
            ),
        ):
            result = await (
                reconcile_failed_live_sell(
                    authorization=(
                        self.authorization
                    ),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FAILED_SELL_LEDGER_UNKNOWN",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )


if __name__ == "__main__":
    unittest.main()
