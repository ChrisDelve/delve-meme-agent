from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from src.execution.live_sell_transaction_status import (
    ABSENT_EXPIRED,
    ABSENT_STILL_VALID,
    KNOWN,
    UNKNOWN as STATUS_UNKNOWN,
)
from src.execution.pump_sell_v2_submission import (
    BLOCK,
    PUMP_SELL_V2_SUBMISSION_VERSION,
    RECONCILIATION_REQUIRED,
    SUBMITTED,
    UNKNOWN,
    submit_pump_sell_v2_once,
)
from src.execution.solana_single_attempt_rpc import (
    SingleAttemptRpcWriteError,
)
from src.portfolio.live_sell_execution_records import (
    BLOCK as EXECUTION_BLOCK,
    PASS as EXECUTION_PASS,
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED as EXECUTION_SUBMITTED,
    load_live_sell_execution_record_read_only,
)
from tests import (
    test_live_sell_submission_boundary
    as submission_boundary_tests,
)


MODULE = (
    "src.execution.pump_sell_v2_submission"
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


class PumpSellV2SubmissionTests(
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
            EXECUTION_PASS,
        )

        self.assertIsNotNone(
            loaded.record
        )

        self.initial = loaded.record

        self.signature = (
            self.initial
            .transaction_signature
        )

        self.transaction_bytes = (
            self.initial
            .signed_transaction_bytes
        )

        self.blockhash_rpc_slot = (
            self.initial
            .blockhash_rpc_slot
        )

        self.last_valid_block_height = (
            self.initial
            .last_valid_block_height
        )

    def tearDown(self):
        self.helper.tearDown()

    def loader_result(
        self,
        record,
        *,
        status=EXECUTION_PASS,
        reasons=(),
    ):
        return SimpleNamespace(
            status=status,
            reasons=tuple(
                reasons
            ),
            record=record,
        )

    def status_result(
        self,
        state,
        *,
        reasons=(),
        authorization_sha256=None,
        signature=None,
        execution_status=SIGNED,
        last_valid_block_height=None,
        blockhash_rpc_slot=None,
    ):
        if authorization_sha256 is None:
            authorization_sha256 = (
                self.authorization
                .authorization_sha256
            )

        if signature is None:
            signature = self.signature

        if last_valid_block_height is None:
            last_valid_block_height = (
                self.last_valid_block_height
            )

        if blockhash_rpc_slot is None:
            blockhash_rpc_slot = (
                self.blockhash_rpc_slot
            )

        return SimpleNamespace(
            state=state,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            transaction_signature=(
                signature
            ),
            execution_status=(
                execution_status
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
        status=EXECUTION_PASS,
        reasons=(),
        record=None,
        changed=True,
    ):
        return SimpleNamespace(
            status=status,
            reasons=tuple(
                reasons
            ),
            record=record,
            changed=changed,
        )

    def armed_record(self):
        return replace(
            self.initial,
            status=SUBMISSION_ARMED,
            submission_started_at=20.0,
            submission_attempt_count=1,
            submitted_at=None,
            updated_at=20.0,
        )

    def submitted_record(self):
        return replace(
            self.initial,
            status=EXECUTION_SUBMITTED,
            submission_started_at=20.0,
            submission_attempt_count=1,
            submitted_at=30.0,
            updated_at=30.0,
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
                self.loader_result(
                    self.initial
                ),
                self.loader_result(
                    self.initial
                ),
            ]

        if observation is None:
            observation = (
                self.status_result(
                    ABSENT_STILL_VALID
                )
            )

        armed = self.armed_record()

        if arm is None:
            arm = self.transition(
                record=armed,
                changed=True,
            )

        submitted = (
            self.submitted_record()
        )

        if acknowledgment is None:
            acknowledgment = (
                self.transition(
                    record=submitted,
                    changed=True,
                )
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
                "load_live_sell_execution_record_read_only",
                load_mock,
            ),
            patch(
                f"{MODULE}."
                "resolve_live_sell_transaction_status",
                status_mock,
            ),
            patch(
                f"{MODULE}."
                "arm_live_sell_submission",
                arm_mock,
            ),
            patch(
                f"{MODULE}."
                "acknowledge_live_sell_submitted",
                ack_mock,
            ),
            patch(
                f"{MODULE}."
                "SingleAttemptSolanaRpcClient",
                return_value=writer,
            ),
        ):
            result = await (
                submit_pump_sell_v2_once(
                    authorization=(
                        self.authorization
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
            PUMP_SELL_V2_SUBMISSION_VERSION,
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
                    self.blockhash_rpc_slot,
                )
            ],
        )

        status_mock.assert_awaited_once()
        arm_mock.assert_called_once()

        ack_mock.assert_called_once_with(
            authorization=(
                self.authorization
            ),
            transaction_signature=(
                self.signature
            ),
            signed_transaction_sha256=(
                self.initial
                .signed_transaction_sha256
            ),
            db_path=self.db_path,
        )

    async def test_known_transaction_never_arms_or_writes(
        self,
    ):
        (
            result,
            writer,
            _,
            _,
            arm_mock,
            ack_mock,
        ) = await self.run_submit(
            snapshots=[
                self.loader_result(
                    self.initial
                ),
            ],
            observation=(
                self.status_result(
                    KNOWN
                )
            ),
        )

        self.assertEqual(
            result.status,
            RECONCILIATION_REQUIRED,
        )

        self.assertIn(
            "SELL_TRANSACTION_ALREADY_KNOWN",
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
        (
            result,
            writer,
            _,
            _,
            arm_mock,
            ack_mock,
        ) = await self.run_submit(
            snapshots=[
                self.loader_result(
                    self.initial
                ),
            ],
            observation=(
                self.status_result(
                    ABSENT_EXPIRED
                )
            ),
        )

        self.assertEqual(
            result.status,
            RECONCILIATION_REQUIRED,
        )

        self.assertIn(
            "SELL_TRANSACTION_ABSENT_EXPIRED",
            result.reasons,
        )

        self.assertEqual(
            writer.calls,
            [],
        )

        arm_mock.assert_not_called()
        ack_mock.assert_not_called()

    async def test_unknown_status_never_arms_or_writes(
        self,
    ):
        observation = (
            self.status_result(
                STATUS_UNKNOWN,
                reasons=(
                    "RPC_FAILED",
                ),
            )
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
                self.loader_result(
                    self.initial
                ),
            ],
            observation=observation,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_STATUS_UNKNOWN",
            result.reasons,
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
        ack_mock.assert_not_called()

    async def test_preexisting_armed_execution_never_checks_or_writes(
        self,
    ):
        armed = self.armed_record()

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
                self.loader_result(
                    armed
                ),
            ],
            writer=writer,
        )

        self.assertEqual(
            result.status,
            RECONCILIATION_REQUIRED,
        )

        self.assertIn(
            "SELL_SUBMISSION_ALREADY_ARMED",
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

    async def test_already_submitted_execution_never_checks_or_writes(
        self,
    ):
        submitted = (
            self.submitted_record()
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
                self.loader_result(
                    submitted
                ),
            ],
            writer=writer,
        )

        self.assertEqual(
            result.status,
            RECONCILIATION_REQUIRED,
        )

        self.assertIn(
            "SELL_ALREADY_SUBMITTED",
            result.reasons,
        )

        status_mock.assert_not_awaited()
        arm_mock.assert_not_called()
        ack_mock.assert_not_called()

        self.assertEqual(
            writer.calls,
            [],
        )

    async def test_status_binding_mismatch_never_arms(
        self,
    ):
        observation = (
            self.status_result(
                ABSENT_STILL_VALID,
                blockhash_rpc_slot=(
                    self.blockhash_rpc_slot
                    + 1
                ),
            )
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
                self.loader_result(
                    self.initial
                ),
            ],
            observation=observation,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_STATUS_OBSERVATION_BINDING_MISMATCH",
            result.reasons,
        )

        arm_mock.assert_not_called()
        ack_mock.assert_not_called()

        self.assertEqual(
            writer.calls,
            [],
        )

    async def test_artifact_change_during_status_check_never_arms(
        self,
    ):
        changed = replace(
            self.initial,
            signed_at=(
                self.initial.signed_at
                + 1.0
            ),
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
                self.loader_result(
                    self.initial
                ),
                self.loader_result(
                    changed
                ),
            ],
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_ARTIFACT_CHANGED_DURING_STATUS_CHECK",
            result.reasons,
        )

        arm_mock.assert_not_called()
        ack_mock.assert_not_called()

        self.assertEqual(
            writer.calls,
            [],
        )

    async def test_arm_pass_without_changed_never_writes(
        self,
    ):
        armed = self.armed_record()

        arm = self.transition(
            status=EXECUTION_PASS,
            record=armed,
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
            "SELL_SUBMISSION_ARM_NOT_ACQUIRED",
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
        armed = self.armed_record()

        arm = self.transition(
            status=EXECUTION_BLOCK,
            reasons=(
                "SELL_SUBMISSION_ALREADY_ARMED_"
                "REQUIRES_RECONCILIATION",
            ),
            record=armed,
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

    async def test_writer_context_failure_after_arm_requires_reconciliation(
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
            "SELL_UNEXPECTED_SEND_FAILURE",
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
            error=(
                SingleAttemptRpcWriteError(
                    "SEND_TRANSACTION_NETWORK_AMBIGUOUS"
                )
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
            "SELL_RPC_WRITE_OUTCOME_UNCERTAIN",
            result.reasons,
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
            "SELL_RETURNED_SIGNATURE_MISMATCH",
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
        acknowledgment = (
            self.transition(
                status="UNKNOWN",
                reasons=(
                    "DB_FAILURE",
                ),
                record=None,
                changed=False,
            )
        )

        (
            result,
            writer,
            _,
            _,
            _,
            _,
        ) = await self.run_submit(
            acknowledgment=(
                acknowledgment
            ),
        )

        self.assertEqual(
            result.status,
            RECONCILIATION_REQUIRED,
        )

        self.assertIn(
            "SELL_SUBMISSION_ACKNOWLEDGMENT_FAILED",
            result.reasons,
        )

        self.assertIn(
            "DB_FAILURE",
            result.reasons,
        )

        self.assertTrue(
            result.relay_invoked
        )

        self.assertEqual(
            len(writer.calls),
            1,
        )

    async def test_real_durable_arm_and_ack_success(
        self,
    ):
        observation = (
            self.status_result(
                ABSENT_STILL_VALID
            )
        )

        writer = FakeWriteClient(
            result=self.signature
        )

        status_mock = AsyncMock(
            return_value=observation
        )

        with (
            patch(
                f"{MODULE}."
                "resolve_live_sell_transaction_status",
                status_mock,
            ),
            patch(
                f"{MODULE}."
                "SingleAttemptSolanaRpcClient",
                return_value=writer,
            ),
        ):
            result = await (
                submit_pump_sell_v2_once(
                    authorization=(
                        self.authorization
                    ),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            SUBMITTED,
        )

        self.assertTrue(
            result.relay_invoked
        )

        self.assertEqual(
            writer.calls,
            [
                (
                    self.transaction_bytes,
                    self.blockhash_rpc_slot,
                )
            ],
        )

        persisted = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            persisted.status,
            EXECUTION_PASS,
        )

        self.assertEqual(
            persisted.record.status,
            EXECUTION_SUBMITTED,
        )

        self.assertEqual(
            persisted.record
            .submission_attempt_count,
            1,
        )

        self.assertIsNotNone(
            persisted.record
            .submission_started_at
        )

        self.assertIsNotNone(
            persisted.record
            .submitted_at
        )


if __name__ == "__main__":
    unittest.main()
