from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from src.execution.signed_transaction_status import (
    ABSENT_EXPIRED,
    ABSENT_STILL_VALID,
    KNOWN,
    UNKNOWN as STATUS_UNKNOWN,
)
from src.execution.signed_transaction_submission import (
    BLOCK,
    RECONCILIATION_REQUIRED,
    SIGNED_TRANSACTION_SUBMISSION_VERSION,
    SUBMITTED,
    UNKNOWN,
    submit_signed_transaction_once,
)
from src.execution.solana_single_attempt_rpc import (
    SingleAttemptRpcWriteError,
)
from src.portfolio.live_reservations import (
    RESERVATION_VERSION,
    SIGNED,
    SUBMITTED as RESERVATION_SUBMITTED,
)


MODULE = (
    "src.execution."
    "signed_transaction_submission"
)


class FakeWriteClient:
    def __init__(
        self,
        *,
        result=None,
        error=None,
        enter_error=None,
    ):
        self.result = result
        self.error = error
        self.enter_error = enter_error
        self.calls = []

    async def __aenter__(
        self,
    ):
        if self.enter_error is not None:
            raise self.enter_error

        return self

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        return None

    async def send_transaction_once(
        self,
        *,
        signed_transaction_bytes,
        min_context_slot,
    ):
        self.calls.append(
            (
                signed_transaction_bytes,
                min_context_slot,
            )
        )

        if self.error is not None:
            raise self.error

        return self.result


class SignedTransactionSubmissionTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.db_path = Path(
            "/tmp/submission-test.db"
        )

        self.reservation_id = (
            "reservation-submit-test"
        )

        self.signature = (
            "PersistedSignature"
        )

        self.transaction_bytes = (
            b"exact-persisted-signed-bytes"
        )

        self.initial = self.make_reservation()

    def make_reservation(
        self,
        *,
        status=SIGNED,
        submission_started_at=None,
        submission_attempt_count=0,
        submitted_at=None,
        transaction_signature=None,
        signed_transaction_bytes=None,
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
                transaction_signature
                if transaction_signature
                is not None
                else self.signature
            ),
            signed_message_sha256=(
                "11" * 32
            ),
            signed_transaction_sha256=(
                "22" * 32
            ),
            signed_transaction_bytes=(
                signed_transaction_bytes
                if signed_transaction_bytes
                is not None
                else self.transaction_bytes
            ),
            recent_blockhash=(
                "BlockhashPersisted"
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
        )

    def status_result(
        self,
        state,
        *,
        reasons=(),
        signature=None,
        last_valid_block_height=350,
        blockhash_rpc_slot=200,
    ):
        return SimpleNamespace(
            state=state,
            reasons=tuple(reasons),
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

    async def run_submit(
        self,
        *,
        snapshots=None,
        observation=None,
        arm=None,
        writer=None,
        acknowledgment=None,
    ):
        if snapshots is None:
            snapshots = [
                self.initial,
                self.initial,
            ]

        if observation is None:
            observation = self.status_result(
                ABSENT_STILL_VALID
            )

        armed = self.make_reservation(
            submission_started_at=20.0,
            submission_attempt_count=1,
        )

        if arm is None:
            arm = self.transition(
                reservation=armed,
                changed=True,
            )

        submitted = self.make_reservation(
            status=RESERVATION_SUBMITTED,
            submission_started_at=20.0,
            submission_attempt_count=1,
            submitted_at=30.0,
        )

        if acknowledgment is None:
            acknowledgment = self.transition(
                reservation=submitted,
                changed=True,
            )

        if writer is None:
            writer = FakeWriteClient(
                result=self.signature
            )

        load_mock = MagicMock(
            side_effect=snapshots
        )

        status_mock = AsyncMock(
            return_value=observation
        )

        arm_mock = MagicMock(
            return_value=arm
        )

        ack_mock = MagicMock(
            return_value=acknowledgment
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
                "arm_reservation_submission",
                arm_mock,
            ),
            patch(
                f"{MODULE}."
                "acknowledge_reservation_submitted",
                ack_mock,
            ),
            patch(
                f"{MODULE}."
                "SingleAttemptSolanaRpcClient",
                return_value=writer,
            ),
        ):
            result = await (
                submit_signed_transaction_once(
                    reservation_id=(
                        self.reservation_id
                    ),
                    db_path=self.db_path,
                )
            )

        return (
            result,
            writer,
            load_mock,
            status_mock,
            arm_mock,
            ack_mock,
        )

    async def test_success_relays_exact_persisted_bytes_once(
        self,
    ):
        (
            result,
            writer,
            _,
            status_mock,
            arm_mock,
            ack_mock,
        ) = await self.run_submit()

        self.assertEqual(
            result.executor_version,
            SIGNED_TRANSACTION_SUBMISSION_VERSION,
        )

        self.assertEqual(
            result.status,
            SUBMITTED,
        )

        self.assertTrue(
            result.relay_invoked
        )

        self.assertEqual(
            result.returned_signature,
            self.signature,
        )

        self.assertEqual(
            writer.calls,
            [
                (
                    self.transaction_bytes,
                    200,
                )
            ],
        )

        status_mock.assert_awaited_once()
        arm_mock.assert_called_once()
        ack_mock.assert_called_once_with(
            reservation_id=(
                self.reservation_id
            ),
            transaction_signature=(
                self.signature
            ),
            db_path=self.db_path,
        )

    async def test_known_transaction_never_arms_or_writes(
        self,
    ):
        observation = self.status_result(
            KNOWN
        )

        (
            result,
            writer,
            _,
            _,
            arm_mock,
            ack_mock,
        ) = await self.run_submit(
            snapshots=[
                self.initial,
            ],
            observation=observation,
        )

        self.assertEqual(
            result.status,
            RECONCILIATION_REQUIRED,
        )

        self.assertIn(
            "TRANSACTION_ALREADY_KNOWN",
            result.reasons,
        )

        self.assertFalse(
            result.relay_invoked
        )

        self.assertEqual(
            writer.calls,
            [],
        )

        arm_mock.assert_not_called()
        ack_mock.assert_not_called()

    async def test_expired_transaction_never_arms_or_writes(
        self,
    ):
        observation = self.status_result(
            ABSENT_EXPIRED
        )

        (
            result,
            writer,
            _,
            _,
            arm_mock,
            _,
        ) = await self.run_submit(
            snapshots=[
                self.initial,
            ],
            observation=observation,
        )

        self.assertEqual(
            result.status,
            RECONCILIATION_REQUIRED,
        )

        self.assertIn(
            "TRANSACTION_EXPIRED_REQUIRES_RECONCILIATION",
            result.reasons,
        )

        self.assertEqual(
            writer.calls,
            [],
        )

        arm_mock.assert_not_called()

    async def test_unknown_status_never_arms_or_writes(
        self,
    ):
        observation = self.status_result(
            STATUS_UNKNOWN,
            reasons=(
                "RPC_FAILED",
            ),
        )

        (
            result,
            writer,
            _,
            _,
            arm_mock,
            _,
        ) = await self.run_submit(
            snapshots=[
                self.initial,
            ],
            observation=observation,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "RPC_FAILED",
            result.reasons,
        )

        self.assertEqual(
            writer.calls,
            [],
        )

        arm_mock.assert_not_called()

    async def test_preexisting_armed_reservation_never_checks_or_writes(
        self,
    ):
        armed = self.make_reservation(
            submission_started_at=20.0,
            submission_attempt_count=1,
        )

        writer = FakeWriteClient(
            result=self.signature
        )

        (
            result,
            writer,
            _,
            status_mock,
            arm_mock,
            _,
        ) = await self.run_submit(
            snapshots=[
                armed,
            ],
            writer=writer,
        )

        self.assertEqual(
            result.status,
            RECONCILIATION_REQUIRED,
        )

        self.assertIn(
            "SUBMISSION_ALREADY_ARMED_REQUIRES_RECONCILIATION",
            result.reasons,
        )

        status_mock.assert_not_awaited()
        arm_mock.assert_not_called()

        self.assertEqual(
            writer.calls,
            [],
        )

    async def test_already_submitted_reservation_never_checks_or_writes(
        self,
    ):
        submitted = self.make_reservation(
            status=RESERVATION_SUBMITTED,
            submission_started_at=20.0,
            submission_attempt_count=1,
            submitted_at=30.0,
        )

        writer = FakeWriteClient(
            result=self.signature
        )

        (
            result,
            writer,
            _,
            status_mock,
            arm_mock,
            ack_mock,
        ) = await self.run_submit(
            snapshots=[
                submitted,
            ],
            writer=writer,
        )

        self.assertEqual(
            result.status,
            RECONCILIATION_REQUIRED,
        )

        self.assertIn(
            "RESERVATION_ALREADY_SUBMITTED",
            result.reasons,
        )

        self.assertFalse(
            result.relay_invoked
        )

        status_mock.assert_not_awaited()
        arm_mock.assert_not_called()
        ack_mock.assert_not_called()

        self.assertEqual(
            writer.calls,
            [],
        )

    async def test_arm_pass_without_changed_never_writes(
        self,
    ):
        armed = self.make_reservation(
            submission_started_at=20.0,
            submission_attempt_count=1,
        )

        arm = self.transition(
            status="PASS",
            reasons=(),
            reservation=armed,
            changed=False,
        )

        (
            result,
            writer,
            _,
            _,
            _,
            ack_mock,
        ) = await self.run_submit(
            arm=arm,
        )

        self.assertEqual(
            result.status,
            RECONCILIATION_REQUIRED,
        )

        self.assertIn(
            "SUBMISSION_ARM_DID_NOT_ACQUIRE_SEND_AUTHORITY",
            result.reasons,
        )

        self.assertFalse(
            result.relay_invoked
        )

        self.assertEqual(
            writer.calls,
            [],
        )

        ack_mock.assert_not_called()

    async def test_concurrent_arm_loss_never_writes(
        self,
    ):
        arm = self.transition(
            status="BLOCK",
            reasons=(
                "SUBMISSION_ALREADY_ARMED_REQUIRES_RECONCILIATION",
            ),
            reservation=self.make_reservation(
                submission_started_at=20.0,
                submission_attempt_count=1,
            ),
            changed=False,
        )

        (
            result,
            writer,
            _,
            _,
            _,
            ack_mock,
        ) = await self.run_submit(
            arm=arm,
        )

        self.assertEqual(
            result.status,
            RECONCILIATION_REQUIRED,
        )

        self.assertFalse(
            result.relay_invoked
        )

        self.assertEqual(
            writer.calls,
            [],
        )

        ack_mock.assert_not_called()

    async def test_status_binding_mismatch_never_arms(
        self,
    ):
        observation = self.status_result(
            ABSENT_STILL_VALID,
            blockhash_rpc_slot=201,
        )

        (
            result,
            writer,
            _,
            _,
            arm_mock,
            _,
        ) = await self.run_submit(
            snapshots=[
                self.initial,
            ],
            observation=observation,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "STATUS_OBSERVATION_BINDING_MISMATCH",
            result.reasons,
        )

        arm_mock.assert_not_called()

        self.assertEqual(
            writer.calls,
            [],
        )

    async def test_reservation_change_during_status_check_never_arms(
        self,
    ):
        changed = self.make_reservation(
            signed_transaction_bytes=(
                b"different-signed-bytes"
            )
        )

        (
            result,
            writer,
            _,
            _,
            arm_mock,
            _,
        ) = await self.run_submit(
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

        arm_mock.assert_not_called()

        self.assertEqual(
            writer.calls,
            [],
        )

    async def test_rpc_context_failure_after_arm_does_not_claim_relay(
        self,
    ):
        writer = FakeWriteClient(
            enter_error=RuntimeError(
                "session setup failed"
            )
        )

        (
            result,
            writer,
            _,
            _,
            _,
            ack_mock,
        ) = await self.run_submit(
            writer=writer,
        )

        self.assertEqual(
            result.status,
            RECONCILIATION_REQUIRED,
        )

        self.assertIn(
            "SEND_OUTCOME_REQUIRES_RECONCILIATION",
            result.reasons,
        )

        self.assertIn(
            "UNEXPECTED_SEND_FAILURE",
            result.reasons,
        )

        self.assertFalse(
            result.relay_invoked
        )

        self.assertEqual(
            writer.calls,
            [],
        )

        ack_mock.assert_not_called()

    async def test_write_error_requires_reconciliation_and_never_acks(
        self,
    ):
        writer = FakeWriteClient(
            error=SingleAttemptRpcWriteError(
                "SEND_TRANSACTION_NETWORK_AMBIGUOUS"
            )
        )

        (
            result,
            writer,
            _,
            _,
            _,
            ack_mock,
        ) = await self.run_submit(
            writer=writer,
        )

        self.assertEqual(
            result.status,
            RECONCILIATION_REQUIRED,
        )

        self.assertTrue(
            result.relay_invoked
        )

        self.assertIn(
            "SEND_TRANSACTION_NETWORK_AMBIGUOUS",
            result.reasons,
        )

        self.assertEqual(
            len(writer.calls),
            1,
        )

        ack_mock.assert_not_called()

    async def test_returned_signature_mismatch_never_acks(
        self,
    ):
        writer = FakeWriteClient(
            result="DifferentSignature"
        )

        (
            result,
            writer,
            _,
            _,
            _,
            ack_mock,
        ) = await self.run_submit(
            writer=writer,
        )

        self.assertEqual(
            result.status,
            RECONCILIATION_REQUIRED,
        )

        self.assertIn(
            "RETURNED_SIGNATURE_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            len(writer.calls),
            1,
        )

        ack_mock.assert_not_called()

    async def test_acknowledgment_failure_requires_reconciliation(
        self,
    ):
        acknowledgment = self.transition(
            status="UNKNOWN",
            reasons=(
                "DB_FAILURE",
            ),
            reservation=None,
            changed=False,
        )

        (
            result,
            writer,
            _,
            _,
            _,
            ack_mock,
        ) = await self.run_submit(
            acknowledgment=acknowledgment,
        )

        self.assertEqual(
            result.status,
            RECONCILIATION_REQUIRED,
        )

        self.assertIn(
            "SUBMISSION_ACKNOWLEDGMENT_FAILED",
            result.reasons,
        )

        self.assertEqual(
            len(writer.calls),
            1,
        )

        ack_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
