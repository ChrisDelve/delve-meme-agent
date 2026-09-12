from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    patch,
)

from src.execution.live_sell_lifecycle import (
    ABORTED as LIFECYCLE_ABORTED,
    ADVANCED as LIFECYCLE_ADVANCED,
    BLOCK as LIFECYCLE_BLOCK,
    HOLD as LIFECYCLE_HOLD,
    RECONCILED as LIFECYCLE_RECONCILED,
    UNKNOWN as LIFECYCLE_UNKNOWN,
    LIVE_SELL_LIFECYCLE_VERSION,
)
from src.execution.live_sell_recovery_discovery import (
    LIVE_SELL_RECOVERY_DISCOVERY_VERSION,
)
from src.execution.live_sell_recovery_executor import (
    ADVANCED,
    BLOCK,
    HOLD,
    IDLE,
    RECONCILED,
    UNKNOWN,
    recover_one_live_sell_once,
)
from src.portfolio.live_sell_execution_records import (
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED,
)


MODULE = (
    "src.execution.live_sell_recovery_executor"
)


class LiveSellRecoveryExecutorTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.db_path = Path(
            "/tmp/live-sell-recovery-test.db"
        )

        self.sha_a = "aa" * 32
        self.sha_b = "bb" * 32

    def candidate(
        self,
        *,
        authorization_sha256,
        execution_status=SIGNED,
    ):
        authorization = SimpleNamespace(
            authorization_sha256=(
                authorization_sha256
            )
        )

        claim = SimpleNamespace(
            authorization_sha256=(
                authorization_sha256
            )
        )

        execution = SimpleNamespace(
            authorization_sha256=(
                authorization_sha256
            ),
            status=execution_status,
        )

        return SimpleNamespace(
            authorization_sha256=(
                authorization_sha256
            ),
            execution_status=(
                execution_status
            ),
            signed_at=1.0,
            authorization=authorization,
            claim=claim,
            execution=execution,
        )

    def discovery(
        self,
        *,
        candidates=(),
        status="PASS",
        reasons=(),
        resolver_version=(
            LIVE_SELL_RECOVERY_DISCOVERY_VERSION
        ),
    ):
        return SimpleNamespace(
            resolver_version=(
                resolver_version
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            candidates=tuple(
                candidates
            ),
            failed_authorization_sha256=None,
        )

    def lifecycle(
        self,
        *,
        authorization_sha256,
        status,
        reasons=(),
        stage="RECONCILE",
        executor_version=(
            LIVE_SELL_LIFECYCLE_VERSION
        ),
    ):
        return SimpleNamespace(
            executor_version=(
                executor_version
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            stage=stage,
            transaction_signature="signature",
        )

    async def test_discovery_failure_never_invokes_lifecycle(
        self,
    ):
        discovery = self.discovery(
            status="UNKNOWN",
            reasons=(
                "DISCOVERY_FAILED",
            ),
        )

        lifecycle = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=discovery,
            ),
            patch(
                f"{MODULE}.advance_authorized_live_sell_once",
                lifecycle,
            ),
        ):
            result = (
                await recover_one_live_sell_once(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "LIVE_SELL_RECOVERY_DISCOVERY_FAILED",
            result.reasons,
        )

        lifecycle.assert_not_awaited()

    async def test_discovery_exception_never_invokes_lifecycle(
        self,
    ):
        lifecycle = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                side_effect=RuntimeError(
                    "discovery boom"
                ),
            ),
            patch(
                f"{MODULE}.advance_authorized_live_sell_once",
                lifecycle,
            ),
        ):
            result = (
                await recover_one_live_sell_once(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "LIVE_SELL_RECOVERY_DISCOVERY_EXCEPTION",
            result.reasons,
        )

        lifecycle.assert_not_awaited()

    async def test_discovery_version_mismatch_never_invokes_lifecycle(
        self,
    ):
        discovery = self.discovery(
            resolver_version=(
                "wrong-discovery-version"
            ),
        )

        lifecycle = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=discovery,
            ),
            patch(
                f"{MODULE}.advance_authorized_live_sell_once",
                lifecycle,
            ),
        ):
            result = (
                await recover_one_live_sell_once(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "LIVE_SELL_RECOVERY_DISCOVERY_VERSION_MISMATCH",
            result.reasons,
        )

        lifecycle.assert_not_awaited()

    async def test_empty_discovery_is_idle_without_lifecycle(
        self,
    ):
        lifecycle = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=(
                    self.discovery()
                ),
            ),
            patch(
                f"{MODULE}.advance_authorized_live_sell_once",
                lifecycle,
            ),
        ):
            result = (
                await recover_one_live_sell_once(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            IDLE,
        )
        self.assertEqual(
            result.discovered_candidates,
            0,
        )

        lifecycle.assert_not_awaited()

    async def test_multiple_candidates_advances_only_oldest_once(
        self,
    ):
        first = self.candidate(
            authorization_sha256=self.sha_a,
            execution_status=SIGNED,
        )

        second = self.candidate(
            authorization_sha256=self.sha_b,
            execution_status=SUBMITTED,
        )

        lifecycle = AsyncMock(
            return_value=(
                self.lifecycle(
                    authorization_sha256=(
                        self.sha_a
                    ),
                    status=(
                        LIFECYCLE_ADVANCED
                    ),
                    stage="SUBMIT",
                )
            )
        )

        with (
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=(
                    self.discovery(
                        candidates=(
                            first,
                            second,
                        )
                    )
                ),
            ),
            patch(
                f"{MODULE}.advance_authorized_live_sell_once",
                lifecycle,
            ),
        ):
            result = (
                await recover_one_live_sell_once(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            ADVANCED,
        )
        self.assertEqual(
            result.discovered_candidates,
            2,
        )
        self.assertEqual(
            result.authorization_sha256,
            self.sha_a,
        )

        lifecycle.assert_awaited_once_with(
            authorization=(
                first.authorization
            ),
            db_path=self.db_path,
        )

    async def test_reconcile_only_prefers_submitted_over_older_signed(
        self,
    ):
        older_signed = self.candidate(
            authorization_sha256=self.sha_a,
            execution_status=SIGNED,
        )

        newer_submitted = self.candidate(
            authorization_sha256=self.sha_b,
            execution_status=SUBMITTED,
        )

        lifecycle = AsyncMock(
            return_value=(
                self.lifecycle(
                    authorization_sha256=(
                        self.sha_b
                    ),
                    status=(
                        LIFECYCLE_RECONCILED
                    ),
                    stage="TERMINAL",
                )
            )
        )

        with (
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=(
                    self.discovery(
                        candidates=(
                            older_signed,
                            newer_submitted,
                        )
                    )
                ),
            ),
            patch(
                f"{MODULE}.advance_authorized_live_sell_once",
                lifecycle,
            ),
        ):
            result = (
                await recover_one_live_sell_once(
                    allow_submission=False,
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            RECONCILED,
        )

        self.assertEqual(
            result.discovered_candidates,
            2,
        )

        self.assertEqual(
            result.authorization_sha256,
            self.sha_b,
        )

        self.assertEqual(
            result.execution_status,
            SUBMITTED,
        )

        lifecycle.assert_awaited_once_with(
            authorization=(
                newer_submitted.authorization
            ),
            allow_submission=False,
            db_path=self.db_path,
        )

    async def test_reconciled_lifecycle_is_propagated(
        self,
    ):
        candidate = self.candidate(
            authorization_sha256=self.sha_a,
            execution_status=SUBMITTED,
        )

        with (
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=(
                    self.discovery(
                        candidates=(
                            candidate,
                        )
                    )
                ),
            ),
            patch(
                f"{MODULE}.advance_authorized_live_sell_once",
                new=AsyncMock(
                    return_value=(
                        self.lifecycle(
                            authorization_sha256=(
                                self.sha_a
                            ),
                            status=(
                                LIFECYCLE_RECONCILED
                            ),
                        )
                    )
                ),
            ),
        ):
            result = (
                await recover_one_live_sell_once(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            RECONCILED,
        )

    async def test_hold_lifecycle_is_propagated(
        self,
    ):
        candidate = self.candidate(
            authorization_sha256=self.sha_a,
            execution_status=(
                SUBMISSION_ARMED
            ),
        )

        with (
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=(
                    self.discovery(
                        candidates=(
                            candidate,
                        )
                    )
                ),
            ),
            patch(
                f"{MODULE}.advance_authorized_live_sell_once",
                new=AsyncMock(
                    return_value=(
                        self.lifecycle(
                            authorization_sha256=(
                                self.sha_a
                            ),
                            status=(
                                LIFECYCLE_HOLD
                            ),
                            reasons=(
                                "WAIT_FOR_CHAIN",
                            ),
                        )
                    )
                ),
            ),
        ):
            result = (
                await recover_one_live_sell_once(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            HOLD,
        )
        self.assertIn(
            "WAIT_FOR_CHAIN",
            result.reasons,
        )

    async def test_block_lifecycle_is_propagated(
        self,
    ):
        candidate = self.candidate(
            authorization_sha256=self.sha_a,
        )

        with (
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=(
                    self.discovery(
                        candidates=(
                            candidate,
                        )
                    )
                ),
            ),
            patch(
                f"{MODULE}.advance_authorized_live_sell_once",
                new=AsyncMock(
                    return_value=(
                        self.lifecycle(
                            authorization_sha256=(
                                self.sha_a
                            ),
                            status=(
                                LIFECYCLE_BLOCK
                            ),
                        )
                    )
                ),
            ),
        ):
            result = (
                await recover_one_live_sell_once(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            BLOCK,
        )

    async def test_unknown_lifecycle_is_propagated(
        self,
    ):
        candidate = self.candidate(
            authorization_sha256=self.sha_a,
        )

        with (
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=(
                    self.discovery(
                        candidates=(
                            candidate,
                        )
                    )
                ),
            ),
            patch(
                f"{MODULE}.advance_authorized_live_sell_once",
                new=AsyncMock(
                    return_value=(
                        self.lifecycle(
                            authorization_sha256=(
                                self.sha_a
                            ),
                            status=(
                                LIFECYCLE_UNKNOWN
                            ),
                        )
                    )
                ),
            ),
        ):
            result = (
                await recover_one_live_sell_once(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

    async def test_aborted_execution_candidate_is_incoherent(
        self,
    ):
        candidate = self.candidate(
            authorization_sha256=self.sha_a,
        )

        with (
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=(
                    self.discovery(
                        candidates=(
                            candidate,
                        )
                    )
                ),
            ),
            patch(
                f"{MODULE}.advance_authorized_live_sell_once",
                new=AsyncMock(
                    return_value=(
                        self.lifecycle(
                            authorization_sha256=(
                                self.sha_a
                            ),
                            status=(
                                LIFECYCLE_ABORTED
                            ),
                        )
                    )
                ),
            ),
        ):
            result = (
                await recover_one_live_sell_once(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "LIVE_SELL_RECOVERY_ABORTED_EXECUTION_INCOHERENT",
            result.reasons,
        )

    async def test_lifecycle_exception_fails_closed(
        self,
    ):
        candidate = self.candidate(
            authorization_sha256=self.sha_a,
        )

        with (
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=(
                    self.discovery(
                        candidates=(
                            candidate,
                        )
                    )
                ),
            ),
            patch(
                f"{MODULE}.advance_authorized_live_sell_once",
                new=AsyncMock(
                    side_effect=RuntimeError(
                        "lifecycle boom"
                    )
                ),
            ),
        ):
            result = (
                await recover_one_live_sell_once(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "LIVE_SELL_RECOVERY_LIFECYCLE_EXCEPTION",
            result.reasons,
        )
        self.assertEqual(
            result.authorization_sha256,
            self.sha_a,
        )

    async def test_lifecycle_version_mismatch_fails_closed(
        self,
    ):
        candidate = self.candidate(
            authorization_sha256=self.sha_a,
        )

        with (
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=(
                    self.discovery(
                        candidates=(
                            candidate,
                        )
                    )
                ),
            ),
            patch(
                f"{MODULE}.advance_authorized_live_sell_once",
                new=AsyncMock(
                    return_value=(
                        self.lifecycle(
                            authorization_sha256=(
                                self.sha_a
                            ),
                            status=(
                                LIFECYCLE_ADVANCED
                            ),
                            executor_version=(
                                "wrong-lifecycle-version"
                            ),
                        )
                    )
                ),
            ),
        ):
            result = (
                await recover_one_live_sell_once(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "LIVE_SELL_RECOVERY_LIFECYCLE_VERSION_MISMATCH",
            result.reasons,
        )

    async def test_lifecycle_binding_mismatch_fails_closed(
        self,
    ):
        candidate = self.candidate(
            authorization_sha256=self.sha_a,
        )

        with (
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=(
                    self.discovery(
                        candidates=(
                            candidate,
                        )
                    )
                ),
            ),
            patch(
                f"{MODULE}.advance_authorized_live_sell_once",
                new=AsyncMock(
                    return_value=(
                        self.lifecycle(
                            authorization_sha256=(
                                self.sha_b
                            ),
                            status=(
                                LIFECYCLE_ADVANCED
                            ),
                        )
                    )
                ),
            ),
        ):
            result = (
                await recover_one_live_sell_once(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "LIVE_SELL_RECOVERY_LIFECYCLE_BINDING_MISMATCH",
            result.reasons,
        )

    async def test_invalid_candidate_never_invokes_lifecycle(
        self,
    ):
        candidate = self.candidate(
            authorization_sha256=self.sha_a,
        )

        candidate.execution.status = (
            "INVALID"
        )

        lifecycle = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=(
                    self.discovery(
                        candidates=(
                            candidate,
                        )
                    )
                ),
            ),
            patch(
                f"{MODULE}.advance_authorized_live_sell_once",
                lifecycle,
            ),
        ):
            result = (
                await recover_one_live_sell_once(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "LIVE_SELL_RECOVERY_CANDIDATE_INVALID",
            result.reasons,
        )

        lifecycle.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
