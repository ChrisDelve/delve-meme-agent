from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from src.execution.failed_transaction_reconciliation import (
    RECONCILED as FAILED_RECONCILED,
)
from src.execution.live_buy_recovery_discovery import (
    ARMED_SIGNED,
    PASS as DISCOVERY_PASS,
    PRISTINE_SIGNED,
    SUBMITTED as RECOVERY_SUBMITTED,
    UNKNOWN as DISCOVERY_UNKNOWN,
)
from src.execution.live_buy_recovery_executor import (
    ADVANCED,
    BLOCK,
    HOLD,
    IDLE,
    RECONCILED,
    UNKNOWN,
    RECONCILE_ABSENT,
    RECONCILE_FAILURE,
    RECONCILE_REQUIRED,
    RECONCILE_SUCCESS,
    STATUS,
    SUBMIT,
    recover_one_live_buy_once,
)
from src.execution.signed_transaction_reconciliation import (
    RECONCILED as ABSENT_RECONCILED,
)
from src.execution.signed_transaction_status import (
    ABSENT_EXPIRED,
    ABSENT_STILL_VALID,
    KNOWN,
    UNKNOWN as STATUS_UNKNOWN,
)
from src.execution.signed_transaction_submission import (
    BLOCK as SUBMISSION_BLOCK,
    RECONCILIATION_REQUIRED,
    SUBMITTED as SUBMISSION_SUBMITTED,
)
from src.execution.successful_buy_reconciliation import (
    HOLD as SUCCESS_HOLD,
    RECONCILED as SUCCESS_RECONCILED,
)


MODULE = (
    "src.execution.live_buy_recovery_executor"
)


class LiveBuyRecoveryExecutorTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.db_path = Path(
            "/tmp/live-buy-recovery-test.db"
        )

    def candidate(
        self,
        *,
        state=PRISTINE_SIGNED,
        reservation_id="reservation-a",
    ):
        return SimpleNamespace(
            recovery_state=state,
            reservation=SimpleNamespace(
                reservation_id=reservation_id,
                transaction_signature=(
                    f"signature-{reservation_id}"
                ),
            ),
        )

    def discovery(
        self,
        *candidates,
        status=DISCOVERY_PASS,
        reasons=(),
    ):
        return SimpleNamespace(
            status=status,
            reasons=reasons,
            candidates=tuple(candidates),
        )

    def observation(
        self,
        *,
        state,
        transaction_error=None,
        reasons=(),
    ):
        return SimpleNamespace(
            resolver_version=(
                "signed-transaction-status-resolver-v3"
            ),
            state=state,
            reasons=reasons,
            transaction_error=transaction_error,
        )

    def child(
        self,
        *,
        status,
        reasons=(),
        version="child-v1",
    ):
        return SimpleNamespace(
            executor_version=version,
            status=status,
            reasons=reasons,
        )

    async def test_invalid_allow_submission_fails_before_discovery(
        self,
    ):
        discovery = patch(
            f"{MODULE}.discover_live_buy_recovery_candidates"
        )

        with discovery as mocked:
            result = await recover_one_live_buy_once(
                allow_submission=1,
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        mocked.assert_not_called()

    async def test_empty_discovery_is_idle(
        self,
    ):
        with patch(
            f"{MODULE}.discover_live_buy_recovery_candidates",
            return_value=self.discovery(),
        ):
            result = await recover_one_live_buy_once(
                db_path=self.db_path
            )

        self.assertEqual(
            result.status,
            IDLE,
        )

    async def test_discovery_unknown_propagates(
        self,
    ):
        with patch(
            f"{MODULE}.discover_live_buy_recovery_candidates",
            return_value=self.discovery(
                status=DISCOVERY_UNKNOWN,
                reasons=("DISCOVERY_BAD",),
            ),
        ):
            result = await recover_one_live_buy_once(
                db_path=self.db_path
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.reasons,
            ("DISCOVERY_BAD",),
        )

    async def test_known_success_routes_success_reconciliation(
        self,
    ):
        successful = AsyncMock(
            return_value=self.child(
                status=SUCCESS_RECONCILED,
            )
        )
        failed = AsyncMock()
        submission = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.discovery(
                    self.candidate()
                ),
            ),
            patch(
                f"{MODULE}.resolve_signed_transaction_status",
                new=AsyncMock(
                    return_value=self.observation(
                        state=KNOWN,
                        transaction_error=None,
                    )
                ),
            ),
            patch(
                f"{MODULE}.reconcile_successful_buy",
                new=successful,
            ),
            patch(
                f"{MODULE}.reconcile_failed_transaction",
                new=failed,
            ),
            patch(
                f"{MODULE}.submit_signed_transaction_once",
                new=submission,
            ),
        ):
            result = await recover_one_live_buy_once(
                db_path=self.db_path
            )

        self.assertEqual(
            result.status,
            RECONCILED,
        )
        self.assertEqual(
            result.stage,
            RECONCILE_SUCCESS,
        )
        successful.assert_awaited_once()
        failed.assert_not_awaited()
        submission.assert_not_awaited()

    async def test_known_failure_routes_failed_reconciliation(
        self,
    ):
        failed = AsyncMock(
            return_value=self.child(
                status=FAILED_RECONCILED,
            )
        )
        successful = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.discovery(
                    self.candidate()
                ),
            ),
            patch(
                f"{MODULE}.resolve_signed_transaction_status",
                new=AsyncMock(
                    return_value=self.observation(
                        state=KNOWN,
                        transaction_error={
                            "InstructionError": 1
                        },
                    )
                ),
            ),
            patch(
                f"{MODULE}.reconcile_failed_transaction",
                new=failed,
            ),
            patch(
                f"{MODULE}.reconcile_successful_buy",
                new=successful,
            ),
        ):
            result = await recover_one_live_buy_once(
                db_path=self.db_path
            )

        self.assertEqual(
            result.status,
            RECONCILED,
        )
        self.assertEqual(
            result.stage,
            RECONCILE_FAILURE,
        )
        failed.assert_awaited_once()
        successful.assert_not_awaited()

    async def test_absent_expired_routes_exact_release_reconciliation(
        self,
    ):
        reconciliation = AsyncMock(
            return_value=self.child(
                status=ABSENT_RECONCILED,
            )
        )

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.discovery(
                    self.candidate()
                ),
            ),
            patch(
                f"{MODULE}.resolve_signed_transaction_status",
                new=AsyncMock(
                    return_value=self.observation(
                        state=ABSENT_EXPIRED
                    )
                ),
            ),
            patch(
                f"{MODULE}.reconcile_signed_transaction",
                new=reconciliation,
            ),
        ):
            result = await recover_one_live_buy_once(
                db_path=self.db_path
            )

        self.assertEqual(
            result.status,
            RECONCILED,
        )
        self.assertEqual(
            result.stage,
            RECONCILE_ABSENT,
        )
        reconciliation.assert_awaited_once()

    async def test_pristine_absent_valid_submits_once(
        self,
    ):
        submission = AsyncMock(
            return_value=SimpleNamespace(
                executor_version=(
                    "signed-transaction-submission-v1"
                ),
                status=SUBMISSION_SUBMITTED,
                reasons=(),
                relay_invoked=True,
                status_observation_state=(
                    ABSENT_STILL_VALID
                ),
            )
        )

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.discovery(
                    self.candidate(
                        state=PRISTINE_SIGNED
                    )
                ),
            ),
            patch(
                f"{MODULE}.resolve_signed_transaction_status",
                new=AsyncMock(
                    return_value=self.observation(
                        state=ABSENT_STILL_VALID
                    )
                ),
            ),
            patch(
                f"{MODULE}.submit_signed_transaction_once",
                new=submission,
            ),
        ):
            result = await recover_one_live_buy_once(
                db_path=self.db_path
            )

        self.assertEqual(
            result.status,
            ADVANCED,
        )
        self.assertEqual(
            result.stage,
            SUBMIT,
        )
        self.assertTrue(
            result.relay_invoked
        )
        submission.assert_awaited_once()

    async def test_kill_mode_never_submits_pristine_signed(
        self,
    ):
        submission = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.discovery(
                    self.candidate(
                        state=PRISTINE_SIGNED
                    )
                ),
            ),
            patch(
                f"{MODULE}.resolve_signed_transaction_status",
                new=AsyncMock(
                    return_value=self.observation(
                        state=ABSENT_STILL_VALID
                    )
                ),
            ),
            patch(
                f"{MODULE}.submit_signed_transaction_once",
                new=submission,
            ),
        ):
            result = await recover_one_live_buy_once(
                allow_submission=False,
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            HOLD,
        )
        self.assertEqual(
            result.stage,
            STATUS,
        )
        self.assertIn(
            "LIVE_BUY_SUBMISSION_DISABLED",
            result.reasons,
        )
        submission.assert_not_awaited()

    async def test_armed_signed_never_regains_relay_authority(
        self,
    ):
        submission = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.discovery(
                    self.candidate(
                        state=ARMED_SIGNED
                    )
                ),
            ),
            patch(
                f"{MODULE}.resolve_signed_transaction_status",
                new=AsyncMock(
                    return_value=self.observation(
                        state=ABSENT_STILL_VALID
                    )
                ),
            ),
            patch(
                f"{MODULE}.submit_signed_transaction_once",
                new=submission,
            ),
        ):
            result = await recover_one_live_buy_once(
                db_path=self.db_path
            )

        self.assertEqual(
            result.status,
            HOLD,
        )
        self.assertIn(
            "LIVE_BUY_RELAY_AUTHORITY_GONE",
            result.reasons,
        )
        submission.assert_not_awaited()

    async def test_submitted_never_regains_relay_authority(
        self,
    ):
        submission = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.discovery(
                    self.candidate(
                        state=RECOVERY_SUBMITTED
                    )
                ),
            ),
            patch(
                f"{MODULE}.resolve_signed_transaction_status",
                new=AsyncMock(
                    return_value=self.observation(
                        state=ABSENT_STILL_VALID
                    )
                ),
            ),
            patch(
                f"{MODULE}.submit_signed_transaction_once",
                new=submission,
            ),
        ):
            result = await recover_one_live_buy_once(
                db_path=self.db_path
            )

        self.assertEqual(
            result.status,
            HOLD,
        )
        submission.assert_not_awaited()

    async def test_unknown_status_never_submits_or_reconciles(
        self,
    ):
        submission = AsyncMock()
        successful = AsyncMock()
        failed = AsyncMock()
        absent = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.discovery(
                    self.candidate()
                ),
            ),
            patch(
                f"{MODULE}.resolve_signed_transaction_status",
                new=AsyncMock(
                    return_value=self.observation(
                        state=STATUS_UNKNOWN,
                        reasons=("RPC_UNKNOWN",),
                    )
                ),
            ),
            patch(
                f"{MODULE}.submit_signed_transaction_once",
                new=submission,
            ),
            patch(
                f"{MODULE}.reconcile_successful_buy",
                new=successful,
            ),
            patch(
                f"{MODULE}.reconcile_failed_transaction",
                new=failed,
            ),
            patch(
                f"{MODULE}.reconcile_signed_transaction",
                new=absent,
            ),
        ):
            result = await recover_one_live_buy_once(
                db_path=self.db_path
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.reasons,
            ("RPC_UNKNOWN",),
        )
        submission.assert_not_awaited()
        successful.assert_not_awaited()
        failed.assert_not_awaited()
        absent.assert_not_awaited()

    async def test_submission_uncertainty_requires_reconciliation(
        self,
    ):
        submission = AsyncMock(
            return_value=SimpleNamespace(
                executor_version=(
                    "signed-transaction-submission-v1"
                ),
                status=(
                    RECONCILIATION_REQUIRED
                ),
                reasons=(
                    "RPC_WRITE_UNCERTAIN",
                ),
                relay_invoked=True,
                status_observation_state=(
                    ABSENT_STILL_VALID
                ),
            )
        )

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.discovery(
                    self.candidate()
                ),
            ),
            patch(
                f"{MODULE}.resolve_signed_transaction_status",
                new=AsyncMock(
                    return_value=self.observation(
                        state=ABSENT_STILL_VALID
                    )
                ),
            ),
            patch(
                f"{MODULE}.submit_signed_transaction_once",
                new=submission,
            ),
        ):
            result = await recover_one_live_buy_once(
                db_path=self.db_path
            )

        self.assertEqual(
            result.status,
            HOLD,
        )
        self.assertEqual(
            result.stage,
            RECONCILE_REQUIRED,
        )
        self.assertTrue(
            result.relay_invoked
        )
        self.assertIn(
            "LIVE_BUY_SUBMISSION_RECONCILIATION_REQUIRED",
            result.reasons,
        )

    async def test_submission_block_before_relay_propagates_block(
        self,
    ):
        submission = AsyncMock(
            return_value=SimpleNamespace(
                executor_version=(
                    "signed-transaction-submission-v1"
                ),
                status=SUBMISSION_BLOCK,
                reasons=(
                    "SUBMISSION_ARM_FAILED",
                ),
                relay_invoked=False,
                status_observation_state=(
                    ABSENT_STILL_VALID
                ),
            )
        )

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.discovery(
                    self.candidate()
                ),
            ),
            patch(
                f"{MODULE}.resolve_signed_transaction_status",
                new=AsyncMock(
                    return_value=self.observation(
                        state=ABSENT_STILL_VALID
                    )
                ),
            ),
            patch(
                f"{MODULE}.submit_signed_transaction_once",
                new=submission,
            ),
        ):
            result = await recover_one_live_buy_once(
                db_path=self.db_path
            )

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertEqual(
            result.stage,
            SUBMIT,
        )
        self.assertFalse(
            result.relay_invoked
        )
        self.assertEqual(
            result.reasons,
            (
                "SUBMISSION_ARM_FAILED",
            ),
        )

    async def test_submission_block_after_relay_is_unknown(
        self,
    ):
        submission = AsyncMock(
            return_value=SimpleNamespace(
                executor_version=(
                    "signed-transaction-submission-v1"
                ),
                status=SUBMISSION_BLOCK,
                reasons=("ACK_FAILED",),
                relay_invoked=True,
                status_observation_state=(
                    ABSENT_STILL_VALID
                ),
            )
        )

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.discovery(
                    self.candidate()
                ),
            ),
            patch(
                f"{MODULE}.resolve_signed_transaction_status",
                new=AsyncMock(
                    return_value=self.observation(
                        state=ABSENT_STILL_VALID
                    )
                ),
            ),
            patch(
                f"{MODULE}.submit_signed_transaction_once",
                new=submission,
            ),
        ):
            result = await recover_one_live_buy_once(
                db_path=self.db_path
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            RECONCILE_REQUIRED,
        )
        self.assertTrue(
            result.relay_invoked
        )
        self.assertIn(
            "LIVE_BUY_SUBMISSION_BLOCK_AFTER_RELAY",
            result.reasons,
        )

    async def test_reconciliation_hold_propagates(
        self,
    ):
        successful = AsyncMock(
            return_value=self.child(
                status=SUCCESS_HOLD,
                reasons=("FILL_NOT_READY",),
            )
        )

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.discovery(
                    self.candidate()
                ),
            ),
            patch(
                f"{MODULE}.resolve_signed_transaction_status",
                new=AsyncMock(
                    return_value=self.observation(
                        state=KNOWN
                    )
                ),
            ),
            patch(
                f"{MODULE}.reconcile_successful_buy",
                new=successful,
            ),
        ):
            result = await recover_one_live_buy_once(
                db_path=self.db_path
            )

        self.assertEqual(
            result.status,
            HOLD,
        )
        self.assertEqual(
            result.reasons,
            ("FILL_NOT_READY",),
        )

    async def test_only_first_discovered_candidate_is_processed(
        self,
    ):
        status = AsyncMock(
            return_value=self.observation(
                state=ABSENT_STILL_VALID
            )
        )

        submission = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.discovery(
                    self.candidate(
                        state=ARMED_SIGNED,
                        reservation_id="first",
                    ),
                    self.candidate(
                        state=PRISTINE_SIGNED,
                        reservation_id="second",
                    ),
                ),
            ),
            patch(
                f"{MODULE}.resolve_signed_transaction_status",
                new=status,
            ),
            patch(
                f"{MODULE}.submit_signed_transaction_once",
                new=submission,
            ),
        ):
            result = await recover_one_live_buy_once(
                db_path=self.db_path
            )

        self.assertEqual(
            result.reservation_id,
            "first",
        )

        status.assert_awaited_once_with(
            reservation_id="first",
            db_path=self.db_path,
        )

        submission.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
