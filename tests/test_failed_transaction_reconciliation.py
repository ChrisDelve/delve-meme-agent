from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from src.execution.failed_transaction_reconciliation import (
    BLOCK,
    HOLD,
    RECONCILED,
    UNKNOWN,
    FAILED_TRANSACTION_RECONCILIATION_VERSION,
    reconcile_failed_transaction,
)
from src.execution.signed_transaction_receipt import (
    BLOCK as RECEIPT_BLOCK,
    RESOLVED as RECEIPT_RESOLVED,
    UNKNOWN as RECEIPT_UNKNOWN,
    SIGNED_TRANSACTION_RECEIPT_VERSION,
)
from src.portfolio.live_reservations import (
    RELEASED,
    RESERVATION_VERSION,
    SIGNED,
    SUBMITTED,
)
from src.portfolio.live_transaction_journal import (
    FAILED,
    LIVE_TRANSACTION_JOURNAL_VERSION,
    PASS,
    RECONCILED_FAILED_TRANSACTION_REASON,
)


MODULE = (
    "src.execution."
    "failed_transaction_reconciliation"
)


class FailedTransactionReconciliationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.db_path = Path(
            "/tmp/failed-reconciliation-test.db"
        )

        self.reservation_id = (
            "failed-executor-test"
        )

        self.signature = (
            "FailedExecutorSignature"
        )

        self.transaction_sha256 = (
            "22" * 32
        )

        self.wallet_pubkey = (
            "FailedExecutorWallet"
        )

        self.error = {
            "InstructionError": [
                2,
                {
                    "Custom": 6001,
                },
            ],
        }

        self.initial = self.make_reservation()

    def make_reservation(
        self,
        *,
        status=SIGNED,
        terminal_at=None,
        terminal_reason=None,
        signed_transaction_sha256=None,
    ):
        return SimpleNamespace(
            reservation_id=(
                self.reservation_id
            ),
            reservation_version=(
                RESERVATION_VERSION
            ),
            mint="MintFailedExecutor",
            side="BUY",
            wallet_pubkey=(
                self.wallet_pubkey
            ),
            status=status,
            signed_at=2.0,
            transaction_signature=(
                self.signature
            ),
            signed_message_sha256=(
                "11" * 32
            ),
            signed_transaction_sha256=(
                self.transaction_sha256
                if signed_transaction_sha256
                is None
                else signed_transaction_sha256
            ),
            signed_transaction_bytes=(
                b"failed-executor-signed-tx"
            ),
            recent_blockhash=(
                "FailedExecutorBlockhash"
            ),
            last_valid_block_height=350,
            blockhash_rpc_slot=200,
            terminal_at=terminal_at,
            terminal_reason=terminal_reason,
        )

    def receipt(
        self,
        *,
        status=RECEIPT_RESOLVED,
        reasons=(),
        transaction_error=None,
        signature=None,
        persisted_transaction_sha256=None,
        receipt_transaction_sha256=None,
        fee_payer_pubkey=None,
        fee_lamports=9_000,
        pre_balance=2_000_000,
        post_balance=1_991_000,
        balance_delta=-9_000,
    ):
        if transaction_error is None:
            transaction_error = (
                self.error
            )

        return SimpleNamespace(
            resolver_version=(
                SIGNED_TRANSACTION_RECEIPT_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            reservation_id=(
                self.reservation_id
            ),
            transaction_signature=(
                self.signature
                if signature is None
                else signature
            ),
            receipt_slot=230,
            block_time=1_800_000_000,
            transaction_error=(
                transaction_error
            ),
            fee_lamports=fee_lamports,
            fee_payer_pubkey=(
                self.wallet_pubkey
                if fee_payer_pubkey is None
                else fee_payer_pubkey
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
                self.transaction_sha256
                if persisted_transaction_sha256
                is None
                else persisted_transaction_sha256
            ),
            receipt_transaction_sha256=(
                self.transaction_sha256
                if receipt_transaction_sha256
                is None
                else receipt_transaction_sha256
            ),
        )

    def success_receipt(self):
        receipt = self.receipt()

        return SimpleNamespace(
            **{
                **receipt.__dict__,
                "transaction_error": None,
            }
        )

    def journal_entry(
        self,
        *,
        recorded_at=40.0,
    ):
        return SimpleNamespace(
            journal_version=(
                LIVE_TRANSACTION_JOURNAL_VERSION
            ),
            transaction_signature=(
                self.signature
            ),
            reservation_id=(
                self.reservation_id
            ),
            wallet_pubkey=(
                self.wallet_pubkey
            ),
            mint="MintFailedExecutor",
            side="BUY",
            signed_transaction_sha256=(
                self.transaction_sha256
            ),
            receipt_transaction_sha256=(
                self.transaction_sha256
            ),
            receipt_resolver_version=(
                SIGNED_TRANSACTION_RECEIPT_VERSION
            ),
            slot=230,
            block_time=1_800_000_000,
            outcome=FAILED,
            transaction_error_json=(
                json.dumps(
                    self.error,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                )
            ),
            fee_lamports=9_000,
            fee_payer_pubkey=(
                self.wallet_pubkey
            ),
            fee_payer_pre_balance_lamports=(
                2_000_000
            ),
            fee_payer_post_balance_lamports=(
                1_991_000
            ),
            fee_payer_balance_delta_lamports=(
                -9_000
            ),
            last_valid_block_height=350,
            blockhash_rpc_slot=200,
            recorded_at=recorded_at,
        )

    def transition(
        self,
        *,
        changed=True,
    ):
        return SimpleNamespace(
            status=PASS,
            reasons=(),
            changed=changed,
        )

    async def run_executor(
        self,
        *,
        snapshots=None,
        receipt=None,
        journal_entries=None,
        transition=None,
    ):
        if snapshots is None:
            released = self.make_reservation(
                status=RELEASED,
                terminal_at=40.0,
                terminal_reason=(
                    RECONCILED_FAILED_TRANSACTION_REASON
                ),
            )

            snapshots = [
                self.initial,
                self.initial,
                released,
            ]

        if receipt is None:
            receipt = self.receipt()

        if journal_entries is None:
            journal_entries = [
                self.journal_entry(),
            ]

        if transition is None:
            transition = self.transition()

        load_mock = MagicMock(
            side_effect=snapshots
        )

        receipt_mock = AsyncMock(
            return_value=receipt
        )

        journal_load_mock = MagicMock(
            side_effect=journal_entries
        )

        transition_mock = MagicMock(
            return_value=transition
        )

        with (
            patch(
                f"{MODULE}."
                "load_capital_reservation_read_only",
                load_mock,
            ),
            patch(
                f"{MODULE}."
                "resolve_signed_transaction_receipt",
                receipt_mock,
            ),
            patch(
                f"{MODULE}."
                "load_transaction_journal_entry_read_only",
                journal_load_mock,
            ),
            patch(
                f"{MODULE}."
                "record_failed_transaction_and_release_reservation",
                transition_mock,
            ),
        ):
            result = await (
                reconcile_failed_transaction(
                    reservation_id=(
                        self.reservation_id
                    ),
                    db_path=self.db_path,
                )
            )

        return (
            result,
            load_mock,
            receipt_mock,
            journal_load_mock,
            transition_mock,
        )

    async def test_failed_signed_transaction_reconciles(
        self,
    ):
        (
            result,
            load_mock,
            receipt_mock,
            journal_load_mock,
            transition_mock,
        ) = await self.run_executor()

        self.assertEqual(
            result.executor_version,
            FAILED_TRANSACTION_RECONCILIATION_VERSION,
        )

        self.assertEqual(
            result.status,
            RECONCILED,
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

        self.assertEqual(
            result.journal_outcome,
            FAILED,
        )

        self.assertEqual(
            load_mock.call_count,
            3,
        )

        receipt_mock.assert_awaited_once()
        transition_mock.assert_called_once()
        journal_load_mock.assert_called_once()

    async def test_failed_submitted_transaction_reconciles(
        self,
    ):
        submitted = self.make_reservation(
            status=SUBMITTED,
        )

        released = self.make_reservation(
            status=RELEASED,
            terminal_at=40.0,
            terminal_reason=(
                RECONCILED_FAILED_TRANSACTION_REASON
            ),
        )

        result, _, _, _, transition_mock = (
            await self.run_executor(
                snapshots=[
                    submitted,
                    submitted,
                    released,
                ],
            )
        )

        self.assertEqual(
            result.status,
            RECONCILED,
        )

        transition_mock.assert_called_once()

    async def test_successful_transaction_holds_without_mutation(
        self,
    ):
        receipt = self.success_receipt()

        (
            result,
            load_mock,
            receipt_mock,
            journal_load_mock,
            transition_mock,
        ) = await self.run_executor(
            snapshots=[
                self.initial,
            ],
            receipt=receipt,
            journal_entries=[],
        )

        self.assertEqual(
            result.status,
            HOLD,
        )

        self.assertIn(
            "SUCCESSFUL_TRANSACTION_REQUIRES_FILL_RECONCILIATION",
            result.reasons,
        )

        self.assertEqual(
            load_mock.call_count,
            1,
        )

        receipt_mock.assert_awaited_once()
        transition_mock.assert_not_called()
        journal_load_mock.assert_not_called()

    async def test_unknown_receipt_never_mutates(
        self,
    ):
        receipt = self.receipt(
            status=RECEIPT_UNKNOWN,
            reasons=(
                "RPC_UNCERTAIN",
            ),
        )

        result, _, _, _, transition_mock = (
            await self.run_executor(
                snapshots=[
                    self.initial,
                ],
                receipt=receipt,
                journal_entries=[],
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "RECEIPT_UNKNOWN",
            result.reasons,
        )

        transition_mock.assert_not_called()

    async def test_blocked_receipt_holds_without_mutation(
        self,
    ):
        receipt = self.receipt(
            status=RECEIPT_BLOCK,
            reasons=(
                "TRANSACTION_NOT_KNOWN",
            ),
        )

        result, _, _, _, transition_mock = (
            await self.run_executor(
                snapshots=[
                    self.initial,
                ],
                receipt=receipt,
                journal_entries=[],
            )
        )

        self.assertEqual(
            result.status,
            HOLD,
        )

        transition_mock.assert_not_called()

    async def test_receipt_binding_mismatch_never_mutates(
        self,
    ):
        receipt = self.receipt(
            fee_payer_pubkey=(
                "DifferentWallet"
            ),
        )

        result, _, _, _, transition_mock = (
            await self.run_executor(
                snapshots=[
                    self.initial,
                ],
                receipt=receipt,
                journal_entries=[],
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FAILED_RECEIPT_BINDING_MISMATCH",
            result.reasons,
        )

        transition_mock.assert_not_called()

    async def test_reservation_change_during_receipt_never_mutates(
        self,
    ):
        changed = self.make_reservation(
            signed_transaction_sha256=(
                "44" * 32
            ),
        )

        result, load_mock, _, _, transition_mock = (
            await self.run_executor(
                snapshots=[
                    self.initial,
                    changed,
                ],
                journal_entries=[],
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "RESERVATION_CHANGED_DURING_RECEIPT_CHECK",
            result.reasons,
        )

        self.assertEqual(
            load_mock.call_count,
            2,
        )

        transition_mock.assert_not_called()

    async def test_already_reconciled_is_local_idempotent_without_receipt_rpc(
        self,
    ):
        released = self.make_reservation(
            status=RELEASED,
            terminal_at=40.0,
            terminal_reason=(
                RECONCILED_FAILED_TRANSACTION_REASON
            ),
        )

        (
            result,
            load_mock,
            receipt_mock,
            journal_load_mock,
            transition_mock,
        ) = await self.run_executor(
            snapshots=[
                released,
            ],
            journal_entries=[
                self.journal_entry(),
            ],
        )

        self.assertEqual(
            result.status,
            RECONCILED,
        )

        self.assertFalse(
            result.changed
        )

        self.assertEqual(
            load_mock.call_count,
            1,
        )

        receipt_mock.assert_not_awaited()
        journal_load_mock.assert_called_once()
        transition_mock.assert_not_called()

    async def test_concurrent_reconciler_wins_before_mutation_is_idempotent(
        self,
    ):
        released = self.make_reservation(
            status=RELEASED,
            terminal_at=40.0,
            terminal_reason=(
                RECONCILED_FAILED_TRANSACTION_REASON
            ),
        )

        (
            result,
            load_mock,
            receipt_mock,
            journal_load_mock,
            transition_mock,
        ) = await self.run_executor(
            snapshots=[
                self.initial,
                released,
            ],
            journal_entries=[
                self.journal_entry(),
            ],
        )

        self.assertEqual(
            result.status,
            RECONCILED,
        )

        self.assertFalse(
            result.changed
        )

        self.assertEqual(
            load_mock.call_count,
            2,
        )

        receipt_mock.assert_awaited_once()
        journal_load_mock.assert_called_once()
        transition_mock.assert_not_called()

    async def test_concurrent_atomic_win_is_idempotent(
        self,
    ):
        released = self.make_reservation(
            status=RELEASED,
            terminal_at=40.0,
            terminal_reason=(
                RECONCILED_FAILED_TRANSACTION_REASON
            ),
        )

        (
            result,
            load_mock,
            receipt_mock,
            journal_load_mock,
            transition_mock,
        ) = await self.run_executor(
            snapshots=[
                self.initial,
                self.initial,
                released,
            ],
            journal_entries=[
                self.journal_entry(),
            ],
            transition=self.transition(
                changed=False,
            ),
        )

        self.assertEqual(
            result.status,
            RECONCILED,
        )

        self.assertFalse(
            result.changed
        )

        self.assertEqual(
            load_mock.call_count,
            3,
        )

        receipt_mock.assert_awaited_once()
        transition_mock.assert_called_once()
        journal_load_mock.assert_called_once()

    async def test_ledger_transition_failure_never_claims_reconciled(
        self,
    ):
        transition = SimpleNamespace(
            status="UNKNOWN",
            reasons=(
                "LEDGER_FAILURE",
            ),
            changed=False,
        )

        (
            result,
            load_mock,
            receipt_mock,
            journal_load_mock,
            transition_mock,
        ) = await self.run_executor(
            snapshots=[
                self.initial,
                self.initial,
            ],
            journal_entries=[],
            transition=transition,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FAILED_TRANSACTION_LEDGER_TRANSITION_REJECTED",
            result.reasons,
        )

        self.assertEqual(
            load_mock.call_count,
            2,
        )

        receipt_mock.assert_awaited_once()
        transition_mock.assert_called_once()
        journal_load_mock.assert_not_called()

    async def test_incoherent_existing_failed_journal_never_claims_success(
        self,
    ):
        released = self.make_reservation(
            status=RELEASED,
            terminal_at=40.0,
            terminal_reason=(
                RECONCILED_FAILED_TRANSACTION_REASON
            ),
        )

        bad_entry = self.journal_entry(
            recorded_at=41.0,
        )

        (
            result,
            _,
            receipt_mock,
            _,
            transition_mock,
        ) = await self.run_executor(
            snapshots=[
                released,
            ],
            journal_entries=[
                bad_entry,
            ],
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FAILED_RECONCILIATION_JOURNAL_INCOHERENT",
            result.reasons,
        )

        receipt_mock.assert_not_awaited()
        transition_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
