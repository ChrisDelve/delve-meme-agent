from __future__ import annotations

import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    patch,
)

from solders.pubkey import Pubkey

from src.execution.live_sell_successful_reconciliation import (
    BLOCK,
    HOLD,
    RECONCILED,
    UNKNOWN,
    reconcile_successful_live_sell,
)
from src.execution.successful_pump_sell_fill import (
    BLOCK as FILL_BLOCK,
    UNKNOWN as FILL_UNKNOWN,
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
from src.portfolio.live_sell_success_accounting import (
    BLOCK as ACCOUNTING_BLOCK,
    PASS as ACCOUNTING_PASS,
    RECONCILED_SUCCESSFUL_SELL_REASON,
    record_successful_sell_and_consume_claim,
)
from tests import (
    test_live_sell_success_accounting
    as accounting_tests,
)


MODULE = (
    "src.execution."
    "live_sell_successful_reconciliation"
)


class LiveSellSuccessfulReconciliationTests(
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
        self.fill = (
            self.helper.make_fill()
        )

    def tearDown(self):
        self.helper.tearDown()

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

    async def run_with_fill(
        self,
        fill,
    ):
        resolver = AsyncMock(
            return_value=fill
        )

        with patch(
            (
                f"{MODULE}."
                "resolve_successful_pump_sell_fill"
            ),
            new=resolver,
        ):
            result = await (
                reconcile_successful_live_sell(
                    authorization=(
                        self.authorization
                    ),
                    db_path=self.db_path,
                )
            )

        return result, resolver

    async def test_proven_successful_sell_reconciles(
        self,
    ):
        result, resolver = (
            await self.run_with_fill(
                self.fill
            )
        )

        self.assertEqual(
            result.status,
            RECONCILED,
        )
        self.assertTrue(
            result.changed
        )
        self.assertEqual(
            result.fill_status,
            self.fill.status,
        )
        self.assertEqual(
            result.claim_status,
            CONSUMED,
        )
        self.assertEqual(
            result.terminal_reason,
            RECONCILED_SUCCESSFUL_SELL_REASON,
        )
        self.assertEqual(
            result.journal_outcome,
            "SUCCESS",
        )
        self.assertIsNotNone(
            result.net_wallet_proceeds_lamports
        )
        self.assertIsNotNone(
            result.total_realized_pnl_lamports
        )

        resolver.assert_awaited_once()

    async def test_exact_retry_is_local_without_fill_rpc(
        self,
    ):
        first, _ = (
            await self.run_with_fill(
                self.fill
            )
        )

        self.assertEqual(
            first.status,
            RECONCILED,
        )
        self.assertTrue(
            first.changed
        )

        resolver = AsyncMock(
            side_effect=AssertionError(
                "fill RPC must not run"
            )
        )

        with patch(
            (
                f"{MODULE}."
                "resolve_successful_pump_sell_fill"
            ),
            new=resolver,
        ):
            second = await (
                reconcile_successful_live_sell(
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
        self.assertEqual(
            second.claim_status,
            CONSUMED,
        )
        self.assertEqual(
            second.journal_outcome,
            "SUCCESS",
        )

        resolver.assert_not_awaited()

    async def test_fill_unknown_never_mutates(
        self,
    ):
        fill = replace(
            self.fill,
            status=FILL_UNKNOWN,
            reasons=(
                "CHAIN_STATE_UNCERTAIN",
            ),
        )

        result, _ = (
            await self.run_with_fill(
                fill
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "SUCCESSFUL_SELL_FILL_UNKNOWN",
            result.reasons,
        )
        self.assertEqual(
            self.load_claim().status,
            ACTIVE,
        )

    async def test_fill_block_holds_without_mutation(
        self,
    ):
        fill = replace(
            self.fill,
            status=FILL_BLOCK,
            reasons=(
                "SUCCESS_PROOF_BLOCKED",
            ),
        )

        result, _ = (
            await self.run_with_fill(
                fill
            )
        )

        self.assertEqual(
            result.status,
            HOLD,
        )
        self.assertIn(
            "SUCCESSFUL_SELL_FILL_BLOCKED",
            result.reasons,
        )
        self.assertEqual(
            self.load_claim().status,
            ACTIVE,
        )

    async def test_fill_exception_is_unknown(
        self,
    ):
        resolver = AsyncMock(
            side_effect=RuntimeError(
                "rpc failed"
            )
        )

        with patch(
            (
                f"{MODULE}."
                "resolve_successful_pump_sell_fill"
            ),
            new=resolver,
        ):
            result = await (
                reconcile_successful_live_sell(
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
            "SUCCESSFUL_SELL_FILL_RESOLUTION_FAILED",
            result.reasons,
        )
        self.assertEqual(
            self.load_claim().status,
            ACTIVE,
        )

    async def test_proven_fill_binding_mismatch_never_mutates(
        self,
    ):
        fill = replace(
            self.fill,
            transaction_signature=str(
                Pubkey.new_unique()
            ),
        )

        result, _ = (
            await self.run_with_fill(
                fill
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "SUCCESSFUL_SELL_FILL_BINDING_MISMATCH",
            result.reasons,
        )
        self.assertEqual(
            self.load_claim().status,
            ACTIVE,
        )

    async def test_concurrent_exact_success_completion_is_idempotent(
        self,
    ):
        async def complete_then_return(
            *,
            authorization,
            db_path,
        ):
            accounting = (
                record_successful_sell_and_consume_claim(
                    authorization=authorization,
                    fill=self.fill,
                    db_path=db_path,
                )
            )

            self.assertEqual(
                accounting.status,
                ACCOUNTING_PASS,
            )
            self.assertTrue(
                accounting.changed
            )

            return self.fill

        with patch(
            (
                f"{MODULE}."
                "resolve_successful_pump_sell_fill"
            ),
            new=AsyncMock(
                side_effect=(
                    complete_then_return
                )
            ),
        ):
            result = await (
                reconcile_successful_live_sell(
                    authorization=(
                        self.authorization
                    ),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            RECONCILED,
        )
        self.assertFalse(
            result.changed
        )
        self.assertEqual(
            result.claim_status,
            CONSUMED,
        )
        self.assertEqual(
            result.journal_outcome,
            "SUCCESS",
        )

    async def test_released_claim_blocks_locally_without_fill_rpc(
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
                """,
                (
                    RELEASED,
                    time.time(),
                    "OTHER_TERMINAL_REASON",
                    self.authorization
                    .authorization_sha256,
                ),
            )
            connection.commit()

        finally:
            connection.close()

        resolver = AsyncMock(
            side_effect=AssertionError(
                "fill RPC must not run"
            )
        )

        with patch(
            (
                f"{MODULE}."
                "resolve_successful_pump_sell_fill"
            ),
            new=resolver,
        ):
            result = await (
                reconcile_successful_live_sell(
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
            "SELL_CLAIM_ALREADY_RELEASED",
            result.reasons,
        )

        resolver.assert_not_awaited()

    async def test_accounting_block_becomes_hold(
        self,
    ):
        fake_accounting_result = (
            SimpleNamespace(
                status=ACCOUNTING_BLOCK,
                reasons=(
                    "ACCOUNTING_BLOCKED",
                ),
                journal=None,
                accounting=None,
                claim_status=ACTIVE,
                terminal_at=None,
                terminal_reason=None,
                changed=False,
            )
        )

        with (
            patch(
                (
                    f"{MODULE}."
                    "resolve_successful_pump_sell_fill"
                ),
                new=AsyncMock(
                    return_value=self.fill
                ),
            ),
            patch(
                (
                    f"{MODULE}."
                    "record_successful_sell_and_consume_claim"
                ),
                return_value=(
                    fake_accounting_result
                ),
            ),
        ):
            result = await (
                reconcile_successful_live_sell(
                    authorization=(
                        self.authorization
                    ),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            HOLD,
        )
        self.assertIn(
            "SUCCESSFUL_SELL_ACCOUNTING_BLOCKED",
            result.reasons,
        )


if __name__ == "__main__":
    unittest.main()
