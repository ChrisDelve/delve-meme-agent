from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from src.execution.signed_transaction_receipt import (
    RESOLVED as RECEIPT_RESOLVED,
    SIGNED_TRANSACTION_RECEIPT_VERSION,
)
from src.execution.successful_buy_reconciliation import (
    BLOCK,
    HOLD,
    RECONCILED,
    UNKNOWN,
    SUCCESSFUL_BUY_RECONCILIATION_VERSION,
    reconcile_successful_buy,
)
from src.execution.successful_pump_buy_fill import (
    BLOCK as FILL_BLOCK,
    PROVEN as FILL_PROVEN,
    UNKNOWN as FILL_UNKNOWN,
    SUCCESSFUL_PUMP_BUY_FILL_VERSION,
    SuccessfulPumpBuyFillResult,
)
from src.portfolio.live_positions import (
    BLOCK as ACCOUNTING_BLOCK,
    OPEN,
    PASS as ACCOUNTING_PASS,
    UNKNOWN as ACCOUNTING_UNKNOWN,
    LIVE_POSITION_VERSION,
    RECONCILED_SUCCESSFUL_BUY_REASON,
)
from src.portfolio.live_reservations import (
    RELEASED,
    RESERVATION_VERSION,
    SIGNED,
    SUBMITTED,
)
from src.portfolio.live_transaction_journal import (
    LIVE_TRANSACTION_JOURNAL_VERSION,
    SUCCESS,
)


MODULE = (
    "src.execution."
    "successful_buy_reconciliation"
)


class SuccessfulBuyReconciliationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.db_path = Path(
            "/tmp/successful-buy-reconciliation-test.db"
        )

        self.reservation_id = (
            "successful-buy-executor-test"
        )

        self.signature = (
            "SuccessfulBuyExecutorSignature"
        )

        self.transaction_sha256 = (
            "22" * 32
        )

        self.wallet_pubkey = (
            "SuccessfulBuyExecutorWallet"
        )

        self.mint = (
            "SuccessfulBuyExecutorMint"
        )

        self.initial = (
            self.make_reservation()
        )

    def make_reservation(
        self,
        *,
        status=SIGNED,
        terminal_at=None,
        terminal_reason=None,
        spend_lamports=1_000_000,
        wallet_cost_lamports=1_100_000,
        signed_transaction_sha256=None,
    ):
        return SimpleNamespace(
            reservation_id=(
                self.reservation_id
            ),
            reservation_version=(
                RESERVATION_VERSION
            ),
            mint=self.mint,
            side="BUY",
            wallet_pubkey=(
                self.wallet_pubkey
            ),
            spend_lamports=(
                spend_lamports
            ),
            wallet_cost_lamports=(
                wallet_cost_lamports
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
                b"successful-buy-executor-signed-tx"
            ),
            recent_blockhash=(
                "SuccessfulExecutorBlockhash"
            ),
            last_valid_block_height=350,
            blockhash_rpc_slot=200,
            terminal_at=terminal_at,
            terminal_reason=terminal_reason,
        )

    def fill(
        self,
        *,
        status=FILL_PROVEN,
        reasons=(),
        signature=None,
        mint=None,
        wallet_pubkey=None,
        persisted_transaction_sha256=None,
        observed_transaction_sha256=None,
    ):
        return SimpleNamespace(
            resolver_version=(
                SUCCESSFUL_PUMP_BUY_FILL_VERSION
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
            receipt_status=(
                RECEIPT_RESOLVED
            ),
            receipt_slot=230,
            block_time=1_800_000_000,
            mint=(
                self.mint
                if mint is None
                else mint
            ),
            wallet_pubkey=(
                self.wallet_pubkey
                if wallet_pubkey is None
                else wallet_pubkey
            ),
            base_token_program=(
                "TokenProgram"
            ),
            associated_base_user=(
                "AssociatedBaseUser"
            ),
            authorized_token_amount=(
                123_456_789
            ),
            max_sol_cost=1_000_000,
            trade_event_token_amount=(
                123_456_789
            ),
            token_pre_amount=100,
            token_post_amount=(
                123_456_889
            ),
            token_delta=123_456_789,
            fee_lamports=5_000,
            wallet_pre_balance_lamports=(
                10_000_000
            ),
            wallet_post_balance_lamports=(
                8_950_000
            ),
            wallet_balance_delta_lamports=(
                -1_050_000
            ),
            wallet_cost_lamports=(
                1_050_000
            ),
            trade_event_sol_amount=(
                850_000
            ),
            protocol_fee_lamports=(
                10_000
            ),
            creator_fee_lamports=5_000,
            cashback_lamports=0,
            buyback_fee_lamports=0,
            quote_mint=(
                "So11111111111111111111111111111111111111112"
            ),
            quote_amount=850_000,
            persisted_transaction_sha256=(
                self.transaction_sha256
                if persisted_transaction_sha256
                is None
                else persisted_transaction_sha256
            ),
            observed_transaction_sha256=(
                self.transaction_sha256
                if observed_transaction_sha256
                is None
                else observed_transaction_sha256
            ),
        )

    def released_reservation(self):
        return self.make_reservation(
            status=RELEASED,
            terminal_at=40.0,
            terminal_reason=(
                RECONCILED_SUCCESSFUL_BUY_REASON
            ),
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
            mint=self.mint,
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
            outcome=SUCCESS,
            transaction_error_json="null",
            fee_lamports=5_000,
            fee_payer_pubkey=(
                self.wallet_pubkey
            ),
            fee_payer_pre_balance_lamports=(
                10_000_000
            ),
            fee_payer_post_balance_lamports=(
                8_950_000
            ),
            fee_payer_balance_delta_lamports=(
                -1_050_000
            ),
            last_valid_block_height=350,
            blockhash_rpc_slot=200,
            recorded_at=recorded_at,
        )

    def position(
        self,
        *,
        entry_signature=None,
        status=OPEN,
    ):
        return SimpleNamespace(
            position_id=7,
            position_version=(
                LIVE_POSITION_VERSION
            ),
            reservation_id=(
                self.reservation_id
            ),
            entry_signature=(
                self.signature
                if entry_signature is None
                else entry_signature
            ),
            wallet_pubkey=(
                self.wallet_pubkey
            ),
            mint=self.mint,
            status=status,
            fill_resolver_version=(
                SUCCESSFUL_PUMP_BUY_FILL_VERSION
            ),
            signed_transaction_sha256=(
                self.transaction_sha256
            ),
            observed_transaction_sha256=(
                self.transaction_sha256
            ),
            entry_slot=230,
            entry_block_time=(
                1_800_000_000
            ),
            base_token_program=(
                "TokenProgram"
            ),
            associated_base_user=(
                "AssociatedBaseUser"
            ),
            quote_mint=(
                "So11111111111111111111111111111111111111112"
            ),
            authorized_token_amount=(
                123_456_789
            ),
            trade_event_token_amount=(
                123_456_789
            ),
            token_pre_amount=100,
            token_post_amount=(
                123_456_889
            ),
            entry_tokens=123_456_789,
            tokens_held=123_456_789,
            authorized_max_sol_cost_lamports=(
                1_000_000
            ),
            entry_exposure_lamports=(
                1_000_000
            ),
            remaining_exposure_lamports=(
                1_000_000
            ),
            entry_wallet_cost_lamports=(
                1_050_000
            ),
            remaining_cost_basis_lamports=(
                1_050_000
            ),
            cumulative_net_proceeds_lamports=0,
            cumulative_realized_pnl_lamports=0,
            network_fee_lamports=5_000,
            wallet_pre_balance_lamports=(
                10_000_000
            ),
            wallet_post_balance_lamports=(
                8_950_000
            ),
            wallet_balance_delta_lamports=(
                -1_050_000
            ),
            trade_event_sol_amount=(
                850_000
            ),
            protocol_fee_lamports=(
                10_000
            ),
            creator_fee_lamports=5_000,
            cashback_lamports=0,
            buyback_fee_lamports=0,
            quote_amount=850_000,
            created_at=40.0,
            updated_at=40.0,
        )

    def accounting_transition(
        self,
        *,
        status=ACCOUNTING_PASS,
        reasons=(),
        changed=True,
    ):
        return SimpleNamespace(
            status=status,
            reasons=tuple(reasons),
            changed=changed,
        )

    async def run_executor(
        self,
        *,
        snapshots=None,
        fill=None,
        fill_error=None,
        journals=None,
        positions=None,
        transition=None,
    ):
        if snapshots is None:
            snapshots = [
                self.initial,
                self.initial,
                self.released_reservation(),
            ]

        if fill is None:
            fill = self.fill()

        if journals is None:
            journals = [
                self.journal_entry(),
            ]

        if positions is None:
            positions = [
                self.position(),
            ]

        if transition is None:
            transition = (
                self.accounting_transition()
            )

        reservation_mock = MagicMock(
            side_effect=snapshots
        )

        if fill_error is None:
            fill_mock = AsyncMock(
                return_value=fill
            )
        else:
            fill_mock = AsyncMock(
                side_effect=fill_error
            )

        journal_mock = MagicMock(
            side_effect=journals
        )

        position_mock = MagicMock(
            side_effect=positions
        )

        accounting_mock = MagicMock(
            return_value=transition
        )

        with (
            patch(
                f"{MODULE}."
                "load_capital_reservation_read_only",
                reservation_mock,
            ),
            patch(
                f"{MODULE}."
                "resolve_successful_pump_buy_fill",
                fill_mock,
            ),
            patch(
                f"{MODULE}."
                "load_transaction_journal_entry_read_only",
                journal_mock,
            ),
            patch(
                f"{MODULE}."
                "load_live_position_read_only",
                position_mock,
            ),
            patch(
                f"{MODULE}."
                "record_successful_buy_and_open_position",
                accounting_mock,
            ),
        ):
            result = await (
                reconcile_successful_buy(
                    reservation_id=(
                        self.reservation_id
                    ),
                    db_path=self.db_path,
                )
            )

        return (
            result,
            reservation_mock,
            fill_mock,
            journal_mock,
            position_mock,
            accounting_mock,
        )

    def test_real_successful_fill_result_exposes_executor_contract(
        self,
    ):
        names = {
            field.name
            for field in fields(
                SuccessfulPumpBuyFillResult
            )
        }

        required = {
            "resolver_version",
            "status",
            "reservation_id",
            "transaction_signature",
            "receipt_status",
            "receipt_slot",
            "block_time",
            "mint",
            "wallet_pubkey",
            "base_token_program",
            "associated_base_user",
            "authorized_token_amount",
            "max_sol_cost",
            "trade_event_token_amount",
            "token_pre_amount",
            "token_post_amount",
            "token_delta",
            "fee_lamports",
            "wallet_pre_balance_lamports",
            "wallet_post_balance_lamports",
            "wallet_balance_delta_lamports",
            "wallet_cost_lamports",
            "trade_event_sol_amount",
            "protocol_fee_lamports",
            "creator_fee_lamports",
            "cashback_lamports",
            "buyback_fee_lamports",
            "quote_mint",
            "quote_amount",
            "persisted_transaction_sha256",
            "observed_transaction_sha256",
        }

        self.assertTrue(
            required.issubset(names)
        )

    async def test_signed_proven_buy_reconciles(
        self,
    ):
        (
            result,
            reservation_mock,
            fill_mock,
            journal_mock,
            position_mock,
            accounting_mock,
        ) = await self.run_executor()

        self.assertEqual(
            result.executor_version,
            SUCCESSFUL_BUY_RECONCILIATION_VERSION,
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
            RECONCILED_SUCCESSFUL_BUY_REASON,
        )

        self.assertEqual(
            result.journal_outcome,
            SUCCESS,
        )

        self.assertEqual(
            result.position_id,
            7,
        )

        self.assertEqual(
            reservation_mock.call_count,
            3,
        )

        fill_mock.assert_awaited_once()
        accounting_mock.assert_called_once()
        journal_mock.assert_called_once()
        position_mock.assert_called_once()

        kwargs = (
            accounting_mock.call_args.kwargs
        )

        self.assertEqual(
            kwargs[
                "authorized_token_amount"
            ],
            123_456_789,
        )

        self.assertEqual(
            kwargs["token_pre_amount"],
            100,
        )

        self.assertEqual(
            kwargs["token_post_amount"],
            123_456_889,
        )

        self.assertEqual(
            kwargs["wallet_cost_lamports"],
            1_050_000,
        )

    async def test_submitted_proven_buy_reconciles(
        self,
    ):
        submitted = self.make_reservation(
            status=SUBMITTED,
        )

        result, _, _, _, _, mutation = (
            await self.run_executor(
                snapshots=[
                    submitted,
                    submitted,
                    self.released_reservation(),
                ],
            )
        )

        self.assertEqual(
            result.status,
            RECONCILED,
        )

        mutation.assert_called_once()

    async def test_already_reconciled_is_local_and_idempotent(
        self,
    ):
        released = (
            self.released_reservation()
        )

        (
            result,
            reservation_mock,
            fill_mock,
            journal_mock,
            position_mock,
            accounting_mock,
        ) = await self.run_executor(
            snapshots=[
                released,
            ],
            journals=[
                self.journal_entry(),
            ],
            positions=[
                self.position(),
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
            reservation_mock.call_count,
            1,
        )

        fill_mock.assert_not_awaited()
        accounting_mock.assert_not_called()
        journal_mock.assert_called_once()
        position_mock.assert_called_once()

    async def test_other_terminal_reason_blocks_without_fill_rpc(
        self,
    ):
        released = self.make_reservation(
            status=RELEASED,
            terminal_at=40.0,
            terminal_reason=(
                "RECONCILED_FAILED_TRANSACTION"
            ),
        )

        (
            result,
            _,
            fill_mock,
            journal_mock,
            position_mock,
            accounting_mock,
        ) = await self.run_executor(
            snapshots=[released],
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        fill_mock.assert_not_awaited()
        accounting_mock.assert_not_called()
        journal_mock.assert_not_called()
        position_mock.assert_not_called()

    async def test_fill_unknown_stays_unknown(
        self,
    ):
        fill = self.fill(
            status=FILL_UNKNOWN,
            reasons=("RPC_UNCERTAIN",),
        )

        result, _, _, _, _, mutation = (
            await self.run_executor(
                snapshots=[
                    self.initial,
                ],
                fill=fill,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "RPC_UNCERTAIN",
            result.reasons,
        )

        mutation.assert_not_called()

    async def test_fill_block_holds_reservation(
        self,
    ):
        fill = self.fill(
            status=FILL_BLOCK,
            reasons=("FILL_PROOF_BLOCKED",),
        )

        result, _, _, _, _, mutation = (
            await self.run_executor(
                snapshots=[
                    self.initial,
                ],
                fill=fill,
            )
        )

        self.assertEqual(
            result.status,
            HOLD,
        )

        mutation.assert_not_called()

    async def test_fill_exception_is_unknown(
        self,
    ):
        result, _, _, _, _, mutation = (
            await self.run_executor(
                snapshots=[
                    self.initial,
                ],
                fill_error=RuntimeError(
                    "rpc failure"
                ),
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SUCCESSFUL_FILL_RESOLUTION_FAILED",
            result.reasons,
        )

        mutation.assert_not_called()

    async def test_fill_binding_mismatch_never_mutates(
        self,
    ):
        fill = self.fill(
            observed_transaction_sha256=(
                "44" * 32
            ),
        )

        result, _, _, _, _, mutation = (
            await self.run_executor(
                snapshots=[
                    self.initial,
                ],
                fill=fill,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SUCCESSFUL_FILL_BINDING_MISMATCH",
            result.reasons,
        )

        mutation.assert_not_called()

    async def test_reservation_change_during_fill_never_mutates(
        self,
    ):
        changed = (
            self.make_reservation(
                wallet_cost_lamports=(
                    1_100_001
                ),
            )
        )

        result, _, _, _, _, mutation = (
            await self.run_executor(
                snapshots=[
                    self.initial,
                    changed,
                ],
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "RESERVATION_CHANGED_DURING_SUCCESS_RECONCILIATION",
            result.reasons,
        )

        mutation.assert_not_called()

    async def test_concurrent_success_completion_is_idempotent(
        self,
    ):
        released = (
            self.released_reservation()
        )

        (
            result,
            _,
            fill_mock,
            journal_mock,
            position_mock,
            mutation,
        ) = await self.run_executor(
            snapshots=[
                self.initial,
                released,
            ],
            journals=[
                self.journal_entry(),
            ],
            positions=[
                self.position(),
            ],
        )

        self.assertEqual(
            result.status,
            RECONCILED,
        )

        self.assertFalse(
            result.changed
        )

        fill_mock.assert_awaited_once()
        mutation.assert_not_called()
        journal_mock.assert_called_once()
        position_mock.assert_called_once()

    async def test_accounting_block_returns_hold(
        self,
    ):
        transition = (
            self.accounting_transition(
                status=ACCOUNTING_BLOCK,
                reasons=(
                    "ACCOUNTING_BLOCKED",
                ),
                changed=False,
            )
        )

        result, _, _, _, _, mutation = (
            await self.run_executor(
                snapshots=[
                    self.initial,
                    self.initial,
                ],
                transition=transition,
            )
        )

        self.assertEqual(
            result.status,
            HOLD,
        )

        self.assertIn(
            "ACCOUNTING_BLOCKED",
            result.reasons,
        )

        mutation.assert_called_once()

    async def test_accounting_unknown_returns_unknown(
        self,
    ):
        transition = (
            self.accounting_transition(
                status=ACCOUNTING_UNKNOWN,
                reasons=(
                    "ACCOUNTING_UNCERTAIN",
                ),
                changed=False,
            )
        )

        result, _, _, _, _, mutation = (
            await self.run_executor(
                snapshots=[
                    self.initial,
                    self.initial,
                ],
                transition=transition,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "ACCOUNTING_UNCERTAIN",
            result.reasons,
        )

        mutation.assert_called_once()

    async def test_final_terminal_incoherence_is_unknown(
        self,
    ):
        bad_position = self.position(
            entry_signature=(
                "WrongEntrySignature"
            ),
        )

        result, _, _, _, _, mutation = (
            await self.run_executor(
                positions=[
                    bad_position,
                ],
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FINAL_SUCCESS_STATE_INCOHERENT",
            result.reasons,
        )

        mutation.assert_called_once()


if __name__ == "__main__":
    unittest.main()
