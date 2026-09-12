from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from src.execution.live_sell_lifecycle import (
    ABORT,
    ABORTED,
    ADVANCED,
    CLAIM,
    HOLD,
    RECONCILE,
    RECONCILED,
    SIGN,
    SUBMIT,
    advance_authorized_live_sell_once,
)
from src.execution.live_sell_reconciliation import (
    HOLD as RECONCILIATION_HOLD,
    RECONCILED as RECONCILIATION_RECONCILED,
)
from src.execution.live_sell_transaction_status import (
    ABSENT_STILL_VALID,
    KNOWN,
)
from src.execution.pump_sell_v2_signing import (
    PASS as SIGNING_PASS,
)
from src.execution.pump_sell_v2_submission import (
    RECONCILIATION_REQUIRED,
    SUBMITTED as SUBMISSION_SUBMITTED,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    PASS as CLAIM_PASS,
    RELEASED,
)
from src.portfolio.live_sell_execution_records import (
    PASS as EXECUTION_PASS,
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED as EXECUTION_SUBMITTED,
    UNKNOWN as EXECUTION_UNKNOWN,
)
from src.portfolio.live_sell_unexecuted_release import (
    PASS as RELEASE_PASS,
    RELEASED_UNEXECUTED_SELL_REASON,
)


MODULE = (
    "src.execution.live_sell_lifecycle"
)


class LiveSellLifecycleTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(
            delete=False
        )
        handle.close()

        self.db_path = handle.name

        self.authorization = SimpleNamespace(
            authorization_version=(
                "live-pump-sell-authorization-v2"
            ),
            authorization_sha256=(
                "ab" * 32
            ),
            wallet_pubkey="wallet",
            mint="mint",
            tokens_to_sell=123,
        )

        self.active_claim = SimpleNamespace(
            status=ACTIVE,
            terminal_at=None,
            terminal_reason=None,
        )

        self.aborted_claim = SimpleNamespace(
            status=RELEASED,
            terminal_at=100.0,
            terminal_reason=(
                RELEASED_UNEXECUTED_SELL_REASON
            ),
        )

        self.signed_execution = (
            self.execution(
                SIGNED
            )
        )

        self.armed_execution = (
            self.execution(
                SUBMISSION_ARMED
            )
        )

        self.submitted_execution = (
            self.execution(
                EXECUTION_SUBMITTED
            )
        )

    def tearDown(self):
        try:
            os.unlink(
                self.db_path
            )
        except FileNotFoundError:
            pass

    def execution(
        self,
        status,
    ):
        return SimpleNamespace(
            authorization_version=(
                self.authorization
                .authorization_version
            ),
            authorization_sha256=(
                self.authorization
                .authorization_sha256
            ),
            wallet_pubkey=(
                self.authorization.wallet_pubkey
            ),
            mint=self.authorization.mint,
            tokens_to_sell=(
                self.authorization.tokens_to_sell
            ),
            status=status,
            transaction_signature="signature",
        )

    def absent_execution_result(self):
        return SimpleNamespace(
            status=EXECUTION_UNKNOWN,
            reasons=(
                "LIVE_SELL_EXECUTION_TABLE_NOT_FOUND",
            ),
            record=None,
        )

    def execution_result(
        self,
        execution,
    ):
        return SimpleNamespace(
            status=EXECUTION_PASS,
            reasons=(),
            record=execution,
        )

    async def run_lifecycle(
        self,
        **kwargs,
    ):
        return (
            await advance_authorized_live_sell_once(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
                **kwargs,
            )
        )

    def common_contract_patches(self):
        return (
            patch(
                f"{MODULE}._authorization_contract_valid",
                return_value=True,
            ),
            patch(
                f"{MODULE}._claim_identity_matches_authorization",
                return_value=True,
            ),
            patch(
                f"{MODULE}._execution_matches_authorization",
                return_value=True,
            ),
        )

    async def test_missing_claim_acquires_only_without_signing(
        self,
    ):
        claim_result = SimpleNamespace(
            status=CLAIM_PASS,
            reasons=(),
            claim=self.active_claim,
            changed=True,
        )

        signing = AsyncMock()
        submission = AsyncMock()
        reconciliation = AsyncMock()

        contract_patches = (
            self.common_contract_patches()
        )

        with (
            contract_patches[0],
            contract_patches[1],
            contract_patches[2],
            patch(
                f"{MODULE}._load_claim_read_only",
                return_value=None,
            ),
            patch(
                f"{MODULE}.acquire_live_sell_inventory_claim",
                return_value=claim_result,
            ) as acquire,
            patch(
                f"{MODULE}.sign_and_bind_pump_sell_v2",
                new=signing,
            ),
            patch(
                f"{MODULE}.submit_pump_sell_v2_once",
                new=submission,
            ),
            patch(
                f"{MODULE}.reconcile_live_sell",
                new=reconciliation,
            ),
        ):
            result = await self.run_lifecycle()

        self.assertEqual(
            result.status,
            ADVANCED,
        )
        self.assertEqual(
            result.stage,
            CLAIM,
        )

        acquire.assert_called_once()
        signing.assert_not_awaited()
        submission.assert_not_awaited()
        reconciliation.assert_not_awaited()

    async def test_active_claim_without_execution_signs_only(
        self,
    ):
        signing_result = SimpleNamespace(
            status=SIGNING_PASS,
            reasons=(),
            transaction_signature="signature",
            is_durably_signed=True,
        )

        signing = AsyncMock(
            return_value=signing_result
        )
        submission = AsyncMock()
        reconciliation = AsyncMock()

        contract_patches = (
            self.common_contract_patches()
        )

        with (
            contract_patches[0],
            contract_patches[1],
            contract_patches[2],
            patch(
                f"{MODULE}._load_claim_read_only",
                return_value=self.active_claim,
            ),
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only",
                return_value=(
                    self.absent_execution_result()
                ),
            ),
            patch(
                f"{MODULE}.sign_and_bind_pump_sell_v2",
                new=signing,
            ),
            patch(
                f"{MODULE}.submit_pump_sell_v2_once",
                new=submission,
            ),
            patch(
                f"{MODULE}.reconcile_live_sell",
                new=reconciliation,
            ),
        ):
            result = await self.run_lifecycle(
                context=object(),
                message_plan=object(),
                network_validation=object(),
                signer=object(),
            )

        self.assertEqual(
            result.status,
            ADVANCED,
        )
        self.assertEqual(
            result.stage,
            SIGN,
        )

        signing.assert_awaited_once()
        submission.assert_not_awaited()
        reconciliation.assert_not_awaited()

    async def test_active_claim_without_signing_inputs_holds(
        self,
    ):
        signing = AsyncMock()

        contract_patches = (
            self.common_contract_patches()
        )

        with (
            contract_patches[0],
            contract_patches[1],
            contract_patches[2],
            patch(
                f"{MODULE}._load_claim_read_only",
                return_value=self.active_claim,
            ),
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only",
                return_value=(
                    self.absent_execution_result()
                ),
            ),
            patch(
                f"{MODULE}.sign_and_bind_pump_sell_v2",
                new=signing,
            ),
        ):
            result = await self.run_lifecycle()

        self.assertEqual(
            result.status,
            HOLD,
        )
        self.assertEqual(
            result.stage,
            SIGN,
        )
        self.assertIn(
            "SELL_SIGNING_INPUTS_REQUIRED",
            result.reasons,
        )

        signing.assert_not_awaited()

    async def test_explicit_abort_releases_only_unexecuted_claim(
        self,
    ):
        release = MagicMock(
            return_value=SimpleNamespace(
                status=RELEASE_PASS,
                reasons=(),
                claim=self.aborted_claim,
            )
        )

        signing = AsyncMock()

        contract_patches = (
            self.common_contract_patches()
        )

        with (
            contract_patches[0],
            contract_patches[1],
            contract_patches[2],
            patch(
                f"{MODULE}._load_claim_read_only",
                return_value=self.active_claim,
            ),
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only",
                return_value=(
                    self.absent_execution_result()
                ),
            ),
            patch(
                f"{MODULE}.release_unexecuted_live_sell_claim",
                new=release,
            ),
            patch(
                f"{MODULE}.sign_and_bind_pump_sell_v2",
                new=signing,
            ),
        ):
            result = await self.run_lifecycle(
                abort_unexecuted=True
            )

        self.assertEqual(
            result.status,
            ABORTED,
        )
        self.assertEqual(
            result.stage,
            ABORT,
        )

        release.assert_called_once()
        signing.assert_not_awaited()

    async def test_released_unexecuted_is_terminal_without_reconciliation(
        self,
    ):
        reconciliation = AsyncMock()
        submission = AsyncMock()

        contract_patches = (
            self.common_contract_patches()
        )

        with (
            contract_patches[0],
            contract_patches[1],
            contract_patches[2],
            patch(
                f"{MODULE}._load_claim_read_only",
                return_value=self.aborted_claim,
            ),
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only",
                return_value=(
                    self.absent_execution_result()
                ),
            ),
            patch(
                f"{MODULE}.reconcile_live_sell",
                new=reconciliation,
            ),
            patch(
                f"{MODULE}.submit_pump_sell_v2_once",
                new=submission,
            ),
        ):
            result = await self.run_lifecycle()

        self.assertEqual(
            result.status,
            ABORTED,
        )
        self.assertEqual(
            result.stage,
            ABORT,
        )

        reconciliation.assert_not_awaited()
        submission.assert_not_awaited()

    async def test_signed_absent_still_valid_reconciles_then_submits_once(
        self,
    ):
        reconciliation = AsyncMock(
            return_value=SimpleNamespace(
                status=RECONCILIATION_HOLD,
                reasons=(
                    "SELL_TRANSACTION_ABSENT_STILL_VALID",
                ),
                status_observation_state=(
                    ABSENT_STILL_VALID
                ),
                transaction_signature="signature",
            )
        )

        submission = AsyncMock(
            return_value=SimpleNamespace(
                status=SUBMISSION_SUBMITTED,
                reasons=(),
                transaction_signature="signature",
                status_observation_state=(
                    ABSENT_STILL_VALID
                ),
                relay_invoked=True,
                execution_status=(
                    EXECUTION_SUBMITTED
                ),
            )
        )

        contract_patches = (
            self.common_contract_patches()
        )

        with (
            contract_patches[0],
            contract_patches[1],
            contract_patches[2],
            patch(
                f"{MODULE}._load_claim_read_only",
                return_value=self.active_claim,
            ),
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only",
                return_value=self.execution_result(
                    self.signed_execution
                ),
            ),
            patch(
                f"{MODULE}.reconcile_live_sell",
                new=reconciliation,
            ),
            patch(
                f"{MODULE}.submit_pump_sell_v2_once",
                new=submission,
            ),
        ):
            result = await self.run_lifecycle()

        self.assertEqual(
            result.status,
            ADVANCED,
        )
        self.assertEqual(
            result.stage,
            SUBMIT,
        )

        reconciliation.assert_awaited_once()
        submission.assert_awaited_once()

    async def test_signed_absent_still_valid_with_submission_disabled_reconciles_without_relay(
        self,
    ):
        reconciliation = AsyncMock(
            return_value=SimpleNamespace(
                status=RECONCILIATION_HOLD,
                reasons=(
                    "SELL_TRANSACTION_ABSENT_STILL_VALID",
                ),
                status_observation_state=(
                    ABSENT_STILL_VALID
                ),
                transaction_signature="signature",
            )
        )

        submission = AsyncMock()

        contract_patches = (
            self.common_contract_patches()
        )

        with (
            contract_patches[0],
            contract_patches[1],
            contract_patches[2],
            patch(
                f"{MODULE}._load_claim_read_only",
                return_value=self.active_claim,
            ),
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only",
                return_value=self.execution_result(
                    self.signed_execution
                ),
            ),
            patch(
                f"{MODULE}.reconcile_live_sell",
                new=reconciliation,
            ),
            patch(
                f"{MODULE}.submit_pump_sell_v2_once",
                new=submission,
            ),
        ):
            result = await self.run_lifecycle(
                allow_submission=False,
            )

        self.assertEqual(
            result.status,
            HOLD,
        )

        self.assertEqual(
            result.stage,
            RECONCILE,
        )

        self.assertIn(
            "SELL_SUBMISSION_DISABLED",
            result.reasons,
        )

        self.assertIn(
            "SELL_TRANSACTION_ABSENT_STILL_VALID",
            result.reasons,
        )

        reconciliation.assert_awaited_once()
        submission.assert_not_awaited()

    async def test_signed_known_transaction_reconciles_without_submission(
        self,
    ):
        reconciliation = AsyncMock(
            return_value=SimpleNamespace(
                status=RECONCILIATION_RECONCILED,
                reasons=(),
                status_observation_state=KNOWN,
                transaction_signature="signature",
            )
        )

        submission = AsyncMock()

        contract_patches = (
            self.common_contract_patches()
        )

        with (
            contract_patches[0],
            contract_patches[1],
            contract_patches[2],
            patch(
                f"{MODULE}._load_claim_read_only",
                return_value=self.active_claim,
            ),
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only",
                return_value=self.execution_result(
                    self.signed_execution
                ),
            ),
            patch(
                f"{MODULE}.reconcile_live_sell",
                new=reconciliation,
            ),
            patch(
                f"{MODULE}.submit_pump_sell_v2_once",
                new=submission,
            ),
        ):
            result = await self.run_lifecycle()

        self.assertEqual(
            result.status,
            RECONCILED,
        )

        reconciliation.assert_awaited_once()
        submission.assert_not_awaited()

    async def test_submission_armed_never_reenters_submission(
        self,
    ):
        reconciliation = AsyncMock(
            return_value=SimpleNamespace(
                status=RECONCILIATION_HOLD,
                reasons=(
                    "SELL_TRANSACTION_ABSENT_STILL_VALID",
                ),
                status_observation_state=(
                    ABSENT_STILL_VALID
                ),
                transaction_signature="signature",
            )
        )

        submission = AsyncMock()

        contract_patches = (
            self.common_contract_patches()
        )

        with (
            contract_patches[0],
            contract_patches[1],
            contract_patches[2],
            patch(
                f"{MODULE}._load_claim_read_only",
                return_value=self.active_claim,
            ),
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only",
                return_value=self.execution_result(
                    self.armed_execution
                ),
            ),
            patch(
                f"{MODULE}.reconcile_live_sell",
                new=reconciliation,
            ),
            patch(
                f"{MODULE}.submit_pump_sell_v2_once",
                new=submission,
            ),
        ):
            result = await self.run_lifecycle()

        self.assertEqual(
            result.status,
            HOLD,
        )
        self.assertEqual(
            result.stage,
            RECONCILE,
        )

        reconciliation.assert_awaited_once()
        submission.assert_not_awaited()

    async def test_submitted_execution_reconciles_only(
        self,
    ):
        reconciliation = AsyncMock(
            return_value=SimpleNamespace(
                status=RECONCILIATION_RECONCILED,
                reasons=(),
                status_observation_state=KNOWN,
                transaction_signature="signature",
            )
        )

        submission = AsyncMock()
        signing = AsyncMock()

        contract_patches = (
            self.common_contract_patches()
        )

        with (
            contract_patches[0],
            contract_patches[1],
            contract_patches[2],
            patch(
                f"{MODULE}._load_claim_read_only",
                return_value=self.active_claim,
            ),
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only",
                return_value=self.execution_result(
                    self.submitted_execution
                ),
            ),
            patch(
                f"{MODULE}.reconcile_live_sell",
                new=reconciliation,
            ),
            patch(
                f"{MODULE}.submit_pump_sell_v2_once",
                new=submission,
            ),
            patch(
                f"{MODULE}.sign_and_bind_pump_sell_v2",
                new=signing,
            ),
        ):
            result = await self.run_lifecycle()

        self.assertEqual(
            result.status,
            RECONCILED,
        )

        reconciliation.assert_awaited_once()
        submission.assert_not_awaited()
        signing.assert_not_awaited()

    async def test_submission_uncertainty_stops_for_next_reconciliation_call(
        self,
    ):
        reconciliation = AsyncMock(
            return_value=SimpleNamespace(
                status=RECONCILIATION_HOLD,
                reasons=(
                    "SELL_TRANSACTION_ABSENT_STILL_VALID",
                ),
                status_observation_state=(
                    ABSENT_STILL_VALID
                ),
                transaction_signature="signature",
            )
        )

        submission = AsyncMock(
            return_value=SimpleNamespace(
                status=RECONCILIATION_REQUIRED,
                reasons=(
                    "SELL_WRITE_OUTCOME_UNCERTAIN",
                ),
                transaction_signature="signature",
                status_observation_state=(
                    ABSENT_STILL_VALID
                ),
                relay_invoked=True,
                execution_status=(
                    SUBMISSION_ARMED
                ),
            )
        )

        contract_patches = (
            self.common_contract_patches()
        )

        with (
            contract_patches[0],
            contract_patches[1],
            contract_patches[2],
            patch(
                f"{MODULE}._load_claim_read_only",
                return_value=self.active_claim,
            ),
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only",
                return_value=self.execution_result(
                    self.signed_execution
                ),
            ),
            patch(
                f"{MODULE}.reconcile_live_sell",
                new=reconciliation,
            ),
            patch(
                f"{MODULE}.submit_pump_sell_v2_once",
                new=submission,
            ),
        ):
            result = await self.run_lifecycle()

        self.assertEqual(
            result.status,
            HOLD,
        )
        self.assertEqual(
            result.stage,
            RECONCILE,
        )
        self.assertIn(
            "SELL_SUBMISSION_RECONCILIATION_REQUIRED",
            result.reasons,
        )

        #
        # Exactly one reconciliation pre-check occurred.
        # The controller does not immediately perform a
        # second reconciliation after an uncertain write.
        #
        self.assertEqual(
            reconciliation.await_count,
            1,
        )
        submission.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
