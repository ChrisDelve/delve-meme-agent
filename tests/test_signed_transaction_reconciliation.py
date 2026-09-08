from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from src.execution.signed_transaction_reconciliation import (
    BLOCK,
    HOLD,
    RECONCILED,
    SIGNED_TRANSACTION_RECONCILIATION_VERSION,
    UNKNOWN,
    reconcile_signed_transaction,
)
from src.execution.signed_transaction_status import (
    ABSENT_EXPIRED,
    ABSENT_STILL_VALID,
    KNOWN,
    UNKNOWN as STATUS_UNKNOWN,
)
from src.portfolio.live_reservations import (
    RECONCILED_ABSENT_EXPIRED_REASON,
    RELEASED,
    RESERVATION_VERSION,
    SIGNED,
    SUBMITTED as RESERVATION_SUBMITTED,
)


MODULE = (
    "src.execution."
    "signed_transaction_reconciliation"
)


class SignedTransactionReconciliationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.db_path = Path(
            "/tmp/reconciliation-test.db"
        )

        self.reservation_id = (
            "reservation-reconciliation-test"
        )

        self.signature = (
            "PersistedReconciliationSignature"
        )

        self.transaction_bytes = (
            b"exact-reconciliation-transaction"
        )

        self.initial = (
            self.make_reservation()
        )

    def make_reservation(
        self,
        *,
        status=SIGNED,
        signed_transaction_sha256=None,
        terminal_at=None,
        terminal_reason=None,
        submission_started_at=None,
        submission_attempt_count=0,
        submitted_at=None,
    ):
        return SimpleNamespace(
            reservation_id=(
                self.reservation_id
            ),
            reservation_version=(
                RESERVATION_VERSION
            ),
            status=status,
            signed_at=10.0,
            transaction_signature=(
                self.signature
            ),
            signed_message_sha256=(
                "11" * 32
            ),
            signed_transaction_sha256=(
                signed_transaction_sha256
                if signed_transaction_sha256
                is not None
                else "22" * 32
            ),
            signed_transaction_bytes=(
                self.transaction_bytes
            ),
            recent_blockhash=(
                "BlockhashReconciliation"
            ),
            last_valid_block_height=350,
            blockhash_rpc_slot=200,
            submission_started_at=(
                submission_started_at
            ),
            submission_attempt_count=(
                submission_attempt_count
            ),
            submitted_at=submitted_at,
            terminal_at=terminal_at,
            terminal_reason=terminal_reason,
        )

    def status_result(
        self,
        state,
        *,
        reasons=(),
        reservation_id=None,
        signature=None,
        last_valid_block_height=350,
        blockhash_rpc_slot=200,
    ):
        return SimpleNamespace(
            state=state,
            reasons=tuple(reasons),
            reservation_id=(
                self.reservation_id
                if reservation_id is None
                else reservation_id
            ),
            transaction_signature=(
                self.signature
                if signature is None
                else signature
            ),
            last_valid_block_height=(
                last_valid_block_height
            ),
            blockhash_rpc_slot=(
                blockhash_rpc_slot
            ),
        )

    def transition(
        self,
        *,
        status="PASS",
        reasons=(),
        reservation=None,
        changed=True,
    ):
        return SimpleNamespace(
            status=status,
            reasons=tuple(reasons),
            reservation=reservation,
            changed=changed,
        )

    async def run_reconcile(
        self,
        *,
        snapshots=None,
        observation=None,
        transition=None,
    ):
        if snapshots is None:
            snapshots = [
                self.initial,
                self.initial,
            ]

        if observation is None:
            observation = (
                self.status_result(
                    ABSENT_EXPIRED
                )
            )

        released = self.make_reservation(
            status=RELEASED,
            terminal_at=40.0,
            terminal_reason=(
                RECONCILED_ABSENT_EXPIRED_REASON
            ),
        )

        if transition is None:
            transition = self.transition(
                reservation=released,
                changed=True,
            )

        load_mock = MagicMock(
            side_effect=snapshots
        )

        status_mock = AsyncMock(
            return_value=observation
        )

        release_mock = MagicMock(
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
                "resolve_signed_transaction_status",
                status_mock,
            ),
            patch(
                f"{MODULE}."
                "release_reconciled_absent_expired_reservation",
                release_mock,
            ),
        ):
            result = await (
                reconcile_signed_transaction(
                    reservation_id=(
                        self.reservation_id
                    ),
                    db_path=self.db_path,
                )
            )

        return (
            result,
            load_mock,
            status_mock,
            release_mock,
        )

    async def test_absent_expired_signed_releases(
        self,
    ):
        (
            result,
            load_mock,
            status_mock,
            release_mock,
        ) = await self.run_reconcile()

        self.assertEqual(
            result.executor_version,
            SIGNED_TRANSACTION_RECONCILIATION_VERSION,
        )

        self.assertEqual(
            result.status,
            RECONCILED,
        )

        self.assertTrue(
            result.changed
        )

        self.assertEqual(
            result.status_observation_state,
            ABSENT_EXPIRED,
        )

        self.assertEqual(
            result.reservation_status,
            RELEASED,
        )

        self.assertEqual(
            result.terminal_reason,
            RECONCILED_ABSENT_EXPIRED_REASON,
        )

        self.assertEqual(
            load_mock.call_count,
            2,
        )

        status_mock.assert_awaited_once()

        release_mock.assert_called_once_with(
            reservation_id=(
                self.reservation_id
            ),
            transaction_signature=(
                self.signature
            ),
            signed_transaction_sha256=(
                "22" * 32
            ),
            last_valid_block_height=350,
            blockhash_rpc_slot=200,
            db_path=self.db_path,
        )

    async def test_absent_expired_submitted_releases(
        self,
    ):
        submitted = self.make_reservation(
            status=RESERVATION_SUBMITTED,
            submission_started_at=20.0,
            submission_attempt_count=1,
            submitted_at=30.0,
        )

        released = self.make_reservation(
            status=RELEASED,
            terminal_at=40.0,
            terminal_reason=(
                RECONCILED_ABSENT_EXPIRED_REASON
            ),
            submission_started_at=20.0,
            submission_attempt_count=1,
            submitted_at=30.0,
        )

        (
            result,
            _,
            status_mock,
            release_mock,
        ) = await self.run_reconcile(
            snapshots=[
                submitted,
                submitted,
            ],
            transition=self.transition(
                reservation=released,
                changed=True,
            ),
        )

        self.assertEqual(
            result.status,
            RECONCILED,
        )

        self.assertTrue(
            result.changed
        )

        status_mock.assert_awaited_once()
        release_mock.assert_called_once()

    async def test_absent_still_valid_holds_without_mutation(
        self,
    ):
        (
            result,
            load_mock,
            status_mock,
            release_mock,
        ) = await self.run_reconcile(
            snapshots=[
                self.initial,
            ],
            observation=(
                self.status_result(
                    ABSENT_STILL_VALID
                )
            ),
        )

        self.assertEqual(
            result.status,
            HOLD,
        )

        self.assertIn(
            "TRANSACTION_STILL_VALID",
            result.reasons,
        )

        self.assertFalse(
            result.changed
        )

        self.assertEqual(
            load_mock.call_count,
            1,
        )

        status_mock.assert_awaited_once()
        release_mock.assert_not_called()

    async def test_known_transaction_holds_without_mutation(
        self,
    ):
        (
            result,
            _,
            status_mock,
            release_mock,
        ) = await self.run_reconcile(
            snapshots=[
                self.initial,
            ],
            observation=(
                self.status_result(
                    KNOWN
                )
            ),
        )

        self.assertEqual(
            result.status,
            HOLD,
        )

        self.assertIn(
            "TRANSACTION_KNOWN_REQUIRES_FURTHER_RECONCILIATION",
            result.reasons,
        )

        self.assertFalse(
            result.changed
        )

        status_mock.assert_awaited_once()
        release_mock.assert_not_called()

    async def test_unknown_status_never_mutates(
        self,
    ):
        (
            result,
            _,
            status_mock,
            release_mock,
        ) = await self.run_reconcile(
            snapshots=[
                self.initial,
            ],
            observation=(
                self.status_result(
                    STATUS_UNKNOWN,
                    reasons=(
                        "RPC_UNCERTAIN",
                    ),
                )
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "STATUS_OBSERVATION_UNKNOWN",
            result.reasons,
        )

        self.assertIn(
            "RPC_UNCERTAIN",
            result.reasons,
        )

        status_mock.assert_awaited_once()
        release_mock.assert_not_called()

    async def test_status_binding_mismatch_never_mutates(
        self,
    ):
        (
            result,
            load_mock,
            status_mock,
            release_mock,
        ) = await self.run_reconcile(
            snapshots=[
                self.initial,
            ],
            observation=(
                self.status_result(
                    ABSENT_EXPIRED,
                    signature=(
                        "DifferentSignature"
                    ),
                )
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "STATUS_OBSERVATION_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            load_mock.call_count,
            1,
        )

        status_mock.assert_awaited_once()
        release_mock.assert_not_called()

    async def test_reservation_mutation_during_status_check_never_mutates(
        self,
    ):
        changed = self.make_reservation(
            signed_transaction_sha256=(
                "33" * 32
            ),
        )

        (
            result,
            load_mock,
            status_mock,
            release_mock,
        ) = await self.run_reconcile(
            snapshots=[
                self.initial,
                changed,
            ],
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "RESERVATION_CHANGED_DURING_STATUS_CHECK",
            result.reasons,
        )

        self.assertEqual(
            load_mock.call_count,
            2,
        )

        status_mock.assert_awaited_once()
        release_mock.assert_not_called()

    async def test_already_reconciled_is_idempotent_without_status_rpc(
        self,
    ):
        released = self.make_reservation(
            status=RELEASED,
            terminal_at=40.0,
            terminal_reason=(
                RECONCILED_ABSENT_EXPIRED_REASON
            ),
        )

        (
            result,
            load_mock,
            status_mock,
            release_mock,
        ) = await self.run_reconcile(
            snapshots=[
                released,
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
            result.reservation_status,
            RELEASED,
        )

        self.assertEqual(
            load_mock.call_count,
            1,
        )

        status_mock.assert_not_awaited()
        release_mock.assert_not_called()

    async def test_concurrent_reconciler_wins_before_reread_is_idempotent(
        self,
    ):
        released = self.make_reservation(
            status=RELEASED,
            terminal_at=40.0,
            terminal_reason=(
                RECONCILED_ABSENT_EXPIRED_REASON
            ),
        )

        (
            result,
            load_mock,
            status_mock,
            release_mock,
        ) = await self.run_reconcile(
            snapshots=[
                self.initial,
                released,
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
            result.reservation_status,
            RELEASED,
        )

        self.assertEqual(
            load_mock.call_count,
            2,
        )

        status_mock.assert_awaited_once()
        release_mock.assert_not_called()

    async def test_concurrent_reconciler_wins_atomic_release_is_idempotent(
        self,
    ):
        released = self.make_reservation(
            status=RELEASED,
            terminal_at=40.0,
            terminal_reason=(
                RECONCILED_ABSENT_EXPIRED_REASON
            ),
        )

        concurrent_win = self.transition(
            status="PASS",
            reasons=(),
            reservation=released,
            changed=False,
        )

        (
            result,
            load_mock,
            status_mock,
            release_mock,
        ) = await self.run_reconcile(
            transition=concurrent_win,
        )

        self.assertEqual(
            result.status,
            RECONCILED,
        )

        self.assertFalse(
            result.changed
        )

        self.assertEqual(
            result.reservation_status,
            RELEASED,
        )

        self.assertEqual(
            result.terminal_reason,
            RECONCILED_ABSENT_EXPIRED_REASON,
        )

        self.assertEqual(
            load_mock.call_count,
            2,
        )

        status_mock.assert_awaited_once()
        release_mock.assert_called_once()

    async def test_ledger_release_failure_never_claims_success(
        self,
    ):
        failed = self.transition(
            status="BLOCK",
            reasons=(
                "RECONCILIATION_EVIDENCE_MISMATCH",
            ),
            reservation=self.initial,
            changed=False,
        )

        (
            result,
            _,
            status_mock,
            release_mock,
        ) = await self.run_reconcile(
            transition=failed,
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertFalse(
            result.changed
        )

        self.assertIn(
            "RECONCILIATION_RELEASE_FAILED",
            result.reasons,
        )

        self.assertIn(
            "RECONCILIATION_EVIDENCE_MISMATCH",
            result.reasons,
        )

        status_mock.assert_awaited_once()
        release_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
