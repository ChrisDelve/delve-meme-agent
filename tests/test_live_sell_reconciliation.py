from __future__ import annotations

import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from src.execution.live_sell_failed_reconciliation import (
    RECONCILED as FAILED_RECONCILED,
)
from src.execution.live_sell_reconciliation import (
    BLOCK,
    EXPIRED,
    FAILED,
    HOLD,
    RECONCILED,
    SUCCESS,
    UNKNOWN,
    reconcile_live_sell,
)
from src.execution.live_sell_successful_reconciliation import (
    RECONCILED as SUCCESS_RECONCILED,
)
from src.execution.live_sell_transaction_status import (
    ABSENT_EXPIRED,
    ABSENT_STILL_VALID,
    KNOWN,
    UNKNOWN as STATUS_UNKNOWN,
    LIVE_SELL_TRANSACTION_STATUS_RESOLVER_VERSION,
)
from src.portfolio.live_reservations import (
    get_connection,
)
from src.portfolio.live_sell_absent_expired_release import (
    RECONCILED_ABSENT_EXPIRED_SELL_REASON,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    RELEASED,
    _load_claim,
)
from src.portfolio.live_sell_execution_records import (
    PASS as EXECUTION_PASS,
    load_live_sell_execution_record_read_only,
)
from src.portfolio.live_sell_success_accounting import (
    PASS as ACCOUNTING_PASS,
    RECONCILED_SUCCESSFUL_SELL_REASON,
    record_successful_sell_and_consume_claim,
)
from src.portfolio.live_sell_transaction_journal import (
    RECONCILED_FAILED_SELL_REASON,
)
from tests import (
    test_live_sell_success_accounting
    as accounting_tests,
)


MODULE = (
    "src.execution.live_sell_reconciliation"
)


class LiveSellReconciliationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.helper = (
            accounting_tests
            .LiveSellSuccessAccountingTests(
                methodName=(
                    "test_successful_sell_updates_positions_journal_and_claim_atomically"
                )
            )
        )
        self.helper.setUp()

        self.authorization = (
            self.helper.authorization
        )
        self.db_path = (
            self.helper.db_path
        )

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

    def tearDown(self):
        self.helper.tearDown()

    def status(
        self,
        state,
        *,
        transaction_error=None,
    ):
        current_height = None
        history_searched = False

        if state == ABSENT_STILL_VALID:
            current_height = (
                self.execution
                .last_valid_block_height
            )

        if state == ABSENT_EXPIRED:
            current_height = (
                self.execution
                .last_valid_block_height
                + 1
            )
            history_searched = True

        return SimpleNamespace(
            resolver_version=(
                LIVE_SELL_TRANSACTION_STATUS_RESOLVER_VERSION
            ),
            state=state,
            reasons=(),
            authorization_sha256=(
                self.authorization
                .authorization_sha256
            ),
            transaction_signature=(
                self.execution
                .transaction_signature
            ),
            execution_status=(
                self.execution.status
            ),
            transaction_error=(
                transaction_error
            ),
            current_block_height=(
                current_height
            ),
            last_valid_block_height=(
                self.execution
                .last_valid_block_height
            ),
            blockhash_rpc_slot=(
                self.execution
                .blockhash_rpc_slot
            ),
            history_searched=(
                history_searched
            ),
        )

    def load_claim(self):
        connection = get_connection(
            self.db_path
        )

        try:
            return _load_claim(
                connection=connection,
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
            )

        finally:
            connection.close()

    async def test_known_success_routes_only_successful_reconciler(
        self,
    ):
        successful = AsyncMock(
            return_value=SimpleNamespace(
                status=SUCCESS_RECONCILED,
                reasons=(),
                changed=True,
            )
        )
        failed = AsyncMock()
        expired = MagicMock()

        with (
            patch(
                f"{MODULE}.resolve_live_sell_transaction_status",
                new=AsyncMock(
                    return_value=self.status(
                        KNOWN
                    )
                ),
            ),
            patch(
                f"{MODULE}.reconcile_successful_live_sell",
                new=successful,
            ),
            patch(
                f"{MODULE}.reconcile_failed_live_sell",
                new=failed,
            ),
            patch(
                f"{MODULE}.release_reconciled_absent_expired_sell_claim",
                new=expired,
            ),
        ):
            result = await reconcile_live_sell(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            RECONCILED,
        )
        self.assertEqual(
            result.route,
            SUCCESS,
        )
        self.assertTrue(
            result.changed
        )

        successful.assert_awaited_once()
        failed.assert_not_awaited()
        expired.assert_not_called()

    async def test_known_failure_routes_only_failed_reconciler(
        self,
    ):
        successful = AsyncMock()
        failed = AsyncMock(
            return_value=SimpleNamespace(
                status=FAILED_RECONCILED,
                reasons=(),
                changed=True,
            )
        )
        expired = MagicMock()

        with (
            patch(
                f"{MODULE}.resolve_live_sell_transaction_status",
                new=AsyncMock(
                    return_value=self.status(
                        KNOWN,
                        transaction_error={
                            "InstructionError": [
                                1,
                                "Custom",
                            ],
                        },
                    )
                ),
            ),
            patch(
                f"{MODULE}.reconcile_successful_live_sell",
                new=successful,
            ),
            patch(
                f"{MODULE}.reconcile_failed_live_sell",
                new=failed,
            ),
            patch(
                f"{MODULE}.release_reconciled_absent_expired_sell_claim",
                new=expired,
            ),
        ):
            result = await reconcile_live_sell(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            RECONCILED,
        )
        self.assertEqual(
            result.route,
            FAILED,
        )

        successful.assert_not_awaited()
        failed.assert_awaited_once()
        expired.assert_not_called()

    async def test_absent_still_valid_holds_without_child(
        self,
    ):
        successful = AsyncMock()
        failed = AsyncMock()
        expired = MagicMock()

        with (
            patch(
                f"{MODULE}.resolve_live_sell_transaction_status",
                new=AsyncMock(
                    return_value=self.status(
                        ABSENT_STILL_VALID
                    )
                ),
            ),
            patch(
                f"{MODULE}.reconcile_successful_live_sell",
                new=successful,
            ),
            patch(
                f"{MODULE}.reconcile_failed_live_sell",
                new=failed,
            ),
            patch(
                f"{MODULE}.release_reconciled_absent_expired_sell_claim",
                new=expired,
            ),
        ):
            result = await reconcile_live_sell(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            HOLD,
        )
        self.assertIsNone(
            result.route
        )

        successful.assert_not_awaited()
        failed.assert_not_awaited()
        expired.assert_not_called()

    async def test_status_unknown_preserves_claim(
        self,
    ):
        with patch(
            f"{MODULE}.resolve_live_sell_transaction_status",
            new=AsyncMock(
                return_value=self.status(
                    STATUS_UNKNOWN
                )
            ),
        ):
            result = await reconcile_live_sell(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            self.load_claim().status,
            ACTIVE,
        )

    async def test_absent_expired_releases_claim(
        self,
    ):
        with patch(
            f"{MODULE}.resolve_live_sell_transaction_status",
            new=AsyncMock(
                return_value=self.status(
                    ABSENT_EXPIRED
                )
            ),
        ):
            result = await reconcile_live_sell(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            RECONCILED,
        )
        self.assertEqual(
            result.route,
            EXPIRED,
        )
        self.assertTrue(
            result.changed
        )

        claim = self.load_claim()

        self.assertEqual(
            claim.status,
            RELEASED,
        )
        self.assertEqual(
            claim.terminal_reason,
            RECONCILED_ABSENT_EXPIRED_SELL_REASON,
        )

    async def test_expired_retry_is_local_without_status_rpc(
        self,
    ):
        with patch(
            f"{MODULE}.resolve_live_sell_transaction_status",
            new=AsyncMock(
                return_value=self.status(
                    ABSENT_EXPIRED
                )
            ),
        ):
            first = await reconcile_live_sell(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )

        self.assertEqual(
            first.status,
            RECONCILED,
        )
        self.assertTrue(
            first.changed
        )

        status = AsyncMock(
            side_effect=AssertionError(
                "status RPC must not run"
            )
        )

        with patch(
            f"{MODULE}.resolve_live_sell_transaction_status",
            new=status,
        ):
            retry = await reconcile_live_sell(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )

        self.assertEqual(
            retry.status,
            RECONCILED,
        )
        self.assertEqual(
            retry.route,
            EXPIRED,
        )
        self.assertFalse(
            retry.changed
        )

        status.assert_not_awaited()

    async def test_success_terminal_routes_locally_without_status_rpc(
        self,
    ):
        accounted = (
            record_successful_sell_and_consume_claim(
                authorization=(
                    self.authorization
                ),
                fill=(
                    self.helper.make_fill()
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            accounted.status,
            ACCOUNTING_PASS,
        )

        status = AsyncMock(
            side_effect=AssertionError(
                "status RPC must not run"
            )
        )

        successful = AsyncMock(
            return_value=SimpleNamespace(
                status=SUCCESS_RECONCILED,
                reasons=(),
                changed=False,
            )
        )

        with (
            patch(
                f"{MODULE}.resolve_live_sell_transaction_status",
                new=status,
            ),
            patch(
                f"{MODULE}.reconcile_successful_live_sell",
                new=successful,
            ),
        ):
            result = await reconcile_live_sell(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            RECONCILED,
        )
        self.assertEqual(
            result.route,
            SUCCESS,
        )
        self.assertFalse(
            result.changed
        )

        status.assert_not_awaited()
        successful.assert_awaited_once()

    async def test_failed_terminal_routes_locally_without_status_rpc(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_sell_inventory_claims

                SET
                    status = ?,
                    terminal_at = ?,
                    terminal_reason = ?

                WHERE authorization_sha256 = ?
                  AND status = ?
                """,
                (
                    RELEASED,
                    time.time(),
                    RECONCILED_FAILED_SELL_REASON,
                    self.authorization
                    .authorization_sha256,
                    ACTIVE,
                ),
            )
            connection.commit()

        finally:
            connection.close()

        status = AsyncMock(
            side_effect=AssertionError(
                "status RPC must not run"
            )
        )

        failed = AsyncMock(
            return_value=SimpleNamespace(
                status=FAILED_RECONCILED,
                reasons=(),
                changed=False,
            )
        )

        with (
            patch(
                f"{MODULE}.resolve_live_sell_transaction_status",
                new=status,
            ),
            patch(
                f"{MODULE}.reconcile_failed_live_sell",
                new=failed,
            ),
        ):
            result = await reconcile_live_sell(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            RECONCILED,
        )
        self.assertEqual(
            result.route,
            FAILED,
        )

        status.assert_not_awaited()
        failed.assert_awaited_once()

    async def test_other_terminal_reason_blocks_locally(
        self,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_sell_inventory_claims

                SET
                    status = ?,
                    terminal_at = ?,
                    terminal_reason = ?

                WHERE authorization_sha256 = ?
                  AND status = ?
                """,
                (
                    RELEASED,
                    time.time(),
                    "OTHER_RELEASE_REASON",
                    self.authorization
                    .authorization_sha256,
                    ACTIVE,
                ),
            )
            connection.commit()

        finally:
            connection.close()

        status = AsyncMock(
            side_effect=AssertionError(
                "status RPC must not run"
            )
        )

        with patch(
            f"{MODULE}.resolve_live_sell_transaction_status",
            new=status,
        ):
            result = await reconcile_live_sell(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertIn(
            "SELL_CLAIM_RELEASED_OTHER_REASON",
            result.reasons,
        )

        status.assert_not_awaited()

    async def test_invalid_expired_proof_never_releases_claim(
        self,
    ):
        invalid = self.status(
            ABSENT_EXPIRED
        )

        invalid = SimpleNamespace(
            **{
                **invalid.__dict__,
                "history_searched": False,
            }
        )

        with patch(
            f"{MODULE}.resolve_live_sell_transaction_status",
            new=AsyncMock(
                return_value=invalid
            ),
        ):
            result = await reconcile_live_sell(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "SELL_ABSENT_EXPIRED_PROOF_INVALID",
            result.reasons,
        )
        self.assertEqual(
            self.load_claim().status,
            ACTIVE,
        )


    async def test_execution_artifact_change_during_status_check_never_dispatches(
        self,
    ):
        changed_execution = replace(
            self.execution,
            signed_at=(
                self.execution.signed_at
                / 2.0
            ),
        )

        execution_reads = MagicMock(
            side_effect=[
                SimpleNamespace(
                    status=EXECUTION_PASS,
                    reasons=(),
                    record=self.execution,
                ),
                SimpleNamespace(
                    status=EXECUTION_PASS,
                    reasons=(),
                    record=changed_execution,
                ),
            ]
        )

        successful = AsyncMock()
        failed = AsyncMock()
        expired = MagicMock()

        with (
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only",
                new=execution_reads,
            ),
            patch(
                f"{MODULE}.resolve_live_sell_transaction_status",
                new=AsyncMock(
                    return_value=self.status(
                        KNOWN
                    )
                ),
            ),
            patch(
                f"{MODULE}.reconcile_successful_live_sell",
                new=successful,
            ),
            patch(
                f"{MODULE}.reconcile_failed_live_sell",
                new=failed,
            ),
            patch(
                f"{MODULE}.release_reconciled_absent_expired_sell_claim",
                new=expired,
            ),
        ):
            result = await reconcile_live_sell(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "SELL_EXECUTION_CHANGED_DURING_STATUS_CHECK",
            result.reasons,
        )

        self.assertEqual(
            execution_reads.call_count,
            2,
        )
        successful.assert_not_awaited()
        failed.assert_not_awaited()
        expired.assert_not_called()

    async def test_claim_change_during_status_check_never_dispatches(
        self,
    ):
        initial_claim = self.load_claim()

        changed_claim = replace(
            initial_claim,
            terminal_at=time.time(),
        )

        claim_reads = MagicMock(
            side_effect=[
                initial_claim,
                changed_claim,
            ]
        )

        successful = AsyncMock()
        failed = AsyncMock()
        expired = MagicMock()

        with (
            patch(
                f"{MODULE}._load_claim_read_only",
                new=claim_reads,
            ),
            patch(
                f"{MODULE}.resolve_live_sell_transaction_status",
                new=AsyncMock(
                    return_value=self.status(
                        KNOWN
                    )
                ),
            ),
            patch(
                f"{MODULE}.reconcile_successful_live_sell",
                new=successful,
            ),
            patch(
                f"{MODULE}.reconcile_failed_live_sell",
                new=failed,
            ),
            patch(
                f"{MODULE}.release_reconciled_absent_expired_sell_claim",
                new=expired,
            ),
        ):
            result = await reconcile_live_sell(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "SELL_CLAIM_CHANGED_DURING_STATUS_CHECK",
            result.reasons,
        )

        self.assertEqual(
            claim_reads.call_count,
            2,
        )
        successful.assert_not_awaited()
        failed.assert_not_awaited()
        expired.assert_not_called()


if __name__ == "__main__":
    unittest.main()
