from __future__ import annotations

from pathlib import Path
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
)
import unittest

from solders.pubkey import Pubkey

from src.execution.live_buy_entry_executor import (
    BLOCK as ENTRY_BLOCK,
    SIGNED as ENTRY_SIGNED,
    UNKNOWN as ENTRY_UNKNOWN,
    LIVE_BUY_ENTRY_EXECUTOR_VERSION,
    LiveBuyEntryExecutionResult,
)
from src.execution.live_buy_recovery_executor import (
    ADVANCED as RECOVERY_ADVANCED,
    BLOCK as RECOVERY_BLOCK,
    HOLD as RECOVERY_HOLD,
    IDLE as RECOVERY_IDLE,
    RECONCILED as RECOVERY_RECONCILED,
    UNKNOWN as RECOVERY_UNKNOWN,
    LIVE_BUY_RECOVERY_EXECUTOR_VERSION,
    LiveBuyRecoveryExecutionResult,
)
from src.execution.live_buy_runtime import (
    ADVANCED,
    BLOCK,
    ENTRY,
    HALTED,
    HOLD,
    INIT,
    KILL,
    LIVE_BUY_RUNTIME_VERSION,
    RECOVERY,
    RECONCILED,
    SIGNED,
    UNKNOWN,
    run_live_buy_once,
)


from src.risk.risk_governor import (
    RiskPolicy,
)

MODULE = (
    "src.execution.live_buy_runtime"
)


class LiveBuyRuntimeTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.db_path = Path(
            "/tmp/live-buy-runtime-test.db"
        )

        self.wallet = (
            "11111111111111111111111111111112"
        )
        self.mint = "mint"

        self.signer = Mock()
        self.signer.pubkey.return_value = (
            Pubkey.from_string(
                self.wallet
            )
        )

        self.live_curve = object()
        self.safety = object()

        self.signal_virtual_quote_reserves = (
            50_000_000_000
        )
        self.signal_virtual_token_reserves = (
            100_000_000_000
        )

        self.policy = RiskPolicy()

    def recovery(
        self,
        *,
        status=RECOVERY_IDLE,
        reasons=(),
        version=(
            LIVE_BUY_RECOVERY_EXECUTOR_VERSION
        ),
    ):
        return LiveBuyRecoveryExecutionResult(
            executor_version=version,
            status=status,
            stage="COMPLETE",
            reasons=tuple(
                reasons
            ),
            reservation_id=None,
            recovery_state=None,
            transaction_signature=None,
            status_observation_state=None,
            child_version=None,
            child_status=None,
            relay_invoked=False,
        )

    def entry(
        self,
        *,
        status=ENTRY_SIGNED,
        reasons=(),
        version=(
            LIVE_BUY_ENTRY_EXECUTOR_VERSION
        ),
    ):
        return LiveBuyEntryExecutionResult(
            executor_version=version,
            status=status,
            stage="COMPLETE",
            reasons=tuple(
                reasons
            ),
            wallet_pubkey=self.wallet,
            mint=self.mint,
            reservation_id="reservation",
            authorization_version=(
                "order-authorization-v1"
            ),
            global_rpc_slot=100,
            blockhash_rpc_slot=110,
            message_sha256=(
                "11" * 32
            ),
            signing_version=(
                "pump-buy-v2-signing-v3"
            ),
            signing_status="PASS",
            transaction_signature="signature",
            signed_transaction_sha256=(
                "22" * 32
            ),
            signed_at=123.0,
        )

    async def invoke(
        self,
        *,
        kill_switch=False,
        signer=None,
        compute_unit_limit=250_000,
        db_path=None,
    ):
        if signer is None:
            signer = self.signer

        if db_path is None:
            db_path = self.db_path

        return await run_live_buy_once(
            kill_switch=kill_switch,
            mint=self.mint,
            wallet_pubkey=self.wallet,
            protected_cash_lamports=0,
            live_curve=self.live_curve,
            safety=self.safety,
            signal_virtual_quote_reserves=(
                self.signal_virtual_quote_reserves
            ),
            signal_virtual_token_reserves=(
                self.signal_virtual_token_reserves
            ),
            protocol_fee_bps=100,
            creator_fee_bps=50,
            buy_slippage_bps=500,
            buy_base_network_fee_lamports=5_000,
            buy_priority_fee_lamports=7_000,
            buy_rent_lamports=2_000,
            exit_slippage_bps=500,
            exit_base_network_fee_lamports=5_000,
            exit_priority_fee_lamports=7_000,
            reservation_ttl_seconds=30.0,
            max_authorization_age_seconds=30.0,
            compute_unit_limit=(
                compute_unit_limit
            ),
            signer=signer,
            policy=self.policy,
            min_context_slot=90,
            db_path=db_path,
        )

    async def test_non_idle_recovery_never_touches_signer(
        self,
    ):
        recovery = AsyncMock(
            return_value=self.recovery(
                status=RECOVERY_HOLD,
                reasons=("RECOVERY_HOLD",),
            )
        )

        entry = AsyncMock()

        signer = Mock()
        signer.pubkey.side_effect = AssertionError(
            "signer must not be touched"
        )

        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=recovery,
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=entry,
            ),
        ):
            result = await self.invoke(
                signer=signer
            )

        self.assertEqual(
            result.status,
            HOLD,
        )
        self.assertEqual(
            result.stage,
            RECOVERY,
        )

        signer.pubkey.assert_not_called()
        entry.assert_not_awaited()

    async def test_kill_never_touches_signer(
        self,
    ):
        recovery = AsyncMock(
            return_value=self.recovery()
        )

        entry = AsyncMock()

        signer = Mock()
        signer.pubkey.side_effect = AssertionError(
            "signer must not be touched"
        )

        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=recovery,
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=entry,
            ),
        ):
            result = await self.invoke(
                kill_switch=True,
                signer=signer,
            )

        self.assertEqual(
            result.status,
            HALTED,
        )
        self.assertEqual(
            result.stage,
            KILL,
        )

        signer.pubkey.assert_not_called()
        entry.assert_not_awaited()

    async def test_signer_load_failure_stops_before_entry(
        self,
    ):
        recovery = AsyncMock(
            return_value=self.recovery()
        )

        entry = AsyncMock()

        signer = Mock()
        signer.pubkey.side_effect = RuntimeError(
            "signer unavailable"
        )

        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=recovery,
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=entry,
            ),
        ):
            result = await self.invoke(
                signer=signer
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            ENTRY,
        )
        self.assertIn(
            "LIVE_BUY_RUNTIME_SIGNER_PUBKEY_FAILED",
            result.reasons,
        )

        signer.pubkey.assert_called_once_with()
        entry.assert_not_awaited()

    async def test_signer_identity_mismatch_stops_before_entry(
        self,
    ):
        recovery = AsyncMock(
            return_value=self.recovery()
        )

        entry = AsyncMock()

        signer = Mock()
        signer.pubkey.return_value = (
            Pubkey.from_string(
                "11111111111111111111111111111111"
            )
        )

        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=recovery,
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=entry,
            ),
        ):
            result = await self.invoke(
                signer=signer
            )

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertEqual(
            result.stage,
            ENTRY,
        )
        self.assertIn(
            "LIVE_BUY_RUNTIME_SIGNER_PUBKEY_MISMATCH",
            result.reasons,
        )

        signer.pubkey.assert_called_once_with()
        entry.assert_not_awaited()

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_BUY_RUNTIME_VERSION,
            "live-buy-runtime-v3",
        )

    async def test_invalid_kill_switch_fails_before_recovery(
        self,
    ):
        recovery = AsyncMock()

        with patch(
            f"{MODULE}.recover_one_live_buy_once",
            new=recovery,
        ):
            result = await self.invoke(
                kill_switch="yes"
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            INIT,
        )
        recovery.assert_not_awaited()

    async def test_recovery_runs_before_bad_entry_configuration(
        self,
    ):
        recovery = AsyncMock(
            return_value=self.recovery(
                status=RECOVERY_HOLD,
                reasons=("RECOVERY_HOLD",),
            )
        )

        entry = AsyncMock()

        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=recovery,
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=entry,
            ),
        ):
            result = await self.invoke(
                signer=None,
                compute_unit_limit=0,
            )

        self.assertEqual(
            result.status,
            HOLD,
        )
        self.assertEqual(
            result.stage,
            RECOVERY,
        )

        recovery.assert_awaited_once_with(
            allow_submission=True,
            db_path=self.db_path,
        )

        entry.assert_not_awaited()

    async def test_recovery_runs_when_new_v2_inputs_are_omitted(
        self,
    ):
        recovery = AsyncMock(
            return_value=self.recovery(
                status=RECOVERY_HOLD,
                reasons=("RECOVERY_HOLD",),
            )
        )

        entry = AsyncMock()

        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=recovery,
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=entry,
            ),
        ):
            result = await run_live_buy_once(
                kill_switch=False,
                mint=self.mint,
                wallet_pubkey=self.wallet,
                protected_cash_lamports=0,
                live_curve=self.live_curve,
                safety=self.safety,
                protocol_fee_bps=100,
                creator_fee_bps=50,
                buy_slippage_bps=500,
                buy_base_network_fee_lamports=5_000,
                buy_priority_fee_lamports=7_000,
                buy_rent_lamports=2_000,
                exit_slippage_bps=500,
                exit_base_network_fee_lamports=5_000,
                exit_priority_fee_lamports=7_000,
                reservation_ttl_seconds=30.0,
                max_authorization_age_seconds=30.0,
                compute_unit_limit=0,
                signer=None,
                min_context_slot=90,
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            HOLD,
        )
        self.assertEqual(
            result.stage,
            RECOVERY,
        )

        recovery.assert_awaited_once_with(
            allow_submission=True,
            db_path=self.db_path,
        )

        entry.assert_not_awaited()

    async def test_recovery_advanced_stops_before_entry(
        self,
    ):
        recovery = AsyncMock(
            return_value=self.recovery(
                status=RECOVERY_ADVANCED,
                reasons=("SUBMITTED",),
            )
        )

        entry = AsyncMock()

        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=recovery,
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=entry,
            ),
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            ADVANCED,
        )
        self.assertEqual(
            result.stage,
            RECOVERY,
        )
        entry.assert_not_awaited()

    async def test_recovery_reconciled_stops_before_entry(
        self,
    ):
        recovery = AsyncMock(
            return_value=self.recovery(
                status=RECOVERY_RECONCILED
            )
        )

        entry = AsyncMock()

        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=recovery,
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=entry,
            ),
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            RECONCILED,
        )
        entry.assert_not_awaited()

    async def test_recovery_block_propagates(
        self,
    ):
        with patch(
            f"{MODULE}.recover_one_live_buy_once",
            new=AsyncMock(
                return_value=self.recovery(
                    status=RECOVERY_BLOCK,
                    reasons=("BLOCKED",),
                )
            ),
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertEqual(
            result.stage,
            RECOVERY,
        )

    async def test_recovery_unknown_propagates(
        self,
    ):
        with patch(
            f"{MODULE}.recover_one_live_buy_once",
            new=AsyncMock(
                return_value=self.recovery(
                    status=RECOVERY_UNKNOWN,
                    reasons=("UNKNOWN_CHAIN",),
                )
            ),
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            RECOVERY,
        )

    async def test_recovery_exception_is_unknown(
        self,
    ):
        with patch(
            f"{MODULE}.recover_one_live_buy_once",
            new=AsyncMock(
                side_effect=RuntimeError(
                    "boom"
                )
            ),
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            RECOVERY,
        )
        self.assertIn(
            "LIVE_BUY_RUNTIME_RECOVERY_EXCEPTION",
            result.reasons,
        )

    async def test_invalid_recovery_contract_blocks_entry(
        self,
    ):
        entry = AsyncMock()

        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=AsyncMock(
                    return_value=object()
                ),
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=entry,
            ),
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            RECOVERY,
        )
        entry.assert_not_awaited()

    async def test_kill_mode_recovery_is_reconciliation_only(
        self,
    ):
        recovery = AsyncMock(
            return_value=self.recovery()
        )

        entry = AsyncMock()

        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=recovery,
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=entry,
            ),
        ):
            result = await self.invoke(
                kill_switch=True
            )

        recovery.assert_awaited_once_with(
            allow_submission=False,
            db_path=self.db_path,
        )

        self.assertEqual(
            result.status,
            HALTED,
        )
        self.assertEqual(
            result.stage,
            KILL,
        )
        entry.assert_not_awaited()

    async def test_kill_mode_relay_claim_fails_closed(
        self,
    ):
        recovery_result = self.recovery(
            status=RECOVERY_ADVANCED
        )

        recovery_result = (
            LiveBuyRecoveryExecutionResult(
                executor_version=(
                    recovery_result.executor_version
                ),
                status=recovery_result.status,
                stage=recovery_result.stage,
                reasons=recovery_result.reasons,
                reservation_id=(
                    recovery_result.reservation_id
                ),
                recovery_state=(
                    recovery_result.recovery_state
                ),
                transaction_signature=(
                    recovery_result.transaction_signature
                ),
                status_observation_state=(
                    recovery_result.status_observation_state
                ),
                child_version=(
                    recovery_result.child_version
                ),
                child_status=(
                    recovery_result.child_status
                ),
                relay_invoked=True,
            )
        )

        entry = AsyncMock()

        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=AsyncMock(
                    return_value=recovery_result
                ),
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=entry,
            ),
        ):
            result = await self.invoke(
                kill_switch=True
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            RECOVERY,
        )
        self.assertIn(
            "LIVE_BUY_RUNTIME_KILL_RELAY_VIOLATION",
            result.reasons,
        )
        entry.assert_not_awaited()

    async def test_kill_mode_existing_recovery_still_wins(
        self,
    ):
        recovery = AsyncMock(
            return_value=self.recovery(
                status=RECOVERY_RECONCILED
            )
        )

        entry = AsyncMock()

        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=recovery,
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=entry,
            ),
        ):
            result = await self.invoke(
                kill_switch=True
            )

        recovery.assert_awaited_once_with(
            allow_submission=False,
            db_path=self.db_path,
        )

        self.assertEqual(
            result.status,
            RECONCILED,
        )
        self.assertEqual(
            result.stage,
            RECOVERY,
        )
        entry.assert_not_awaited()

    async def test_idle_recovery_signed_entry_stops_runtime(
        self,
    ):
        recovery = AsyncMock(
            return_value=self.recovery()
        )

        entry = AsyncMock(
            return_value=self.entry(
                status=ENTRY_SIGNED
            )
        )

        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=recovery,
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=entry,
            ),
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            SIGNED,
        )
        self.assertEqual(
            result.stage,
            ENTRY,
        )

        entry.assert_awaited_once()

    async def test_signed_entry_identity_mismatch_fails_closed(
        self,
    ):
        bad_entry = self.entry(
            status=ENTRY_SIGNED
        )

        bad_entry = (
            LiveBuyEntryExecutionResult(
                executor_version=(
                    bad_entry.executor_version
                ),
                status=bad_entry.status,
                stage=bad_entry.stage,
                reasons=bad_entry.reasons,
                wallet_pubkey="other-wallet",
                mint=bad_entry.mint,
                reservation_id=(
                    bad_entry.reservation_id
                ),
                authorization_version=(
                    bad_entry.authorization_version
                ),
                global_rpc_slot=(
                    bad_entry.global_rpc_slot
                ),
                blockhash_rpc_slot=(
                    bad_entry.blockhash_rpc_slot
                ),
                message_sha256=(
                    bad_entry.message_sha256
                ),
                signing_version=(
                    bad_entry.signing_version
                ),
                signing_status=(
                    bad_entry.signing_status
                ),
                transaction_signature=(
                    bad_entry.transaction_signature
                ),
                signed_transaction_sha256=(
                    bad_entry.signed_transaction_sha256
                ),
                signed_at=bad_entry.signed_at,
            )
        )

        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=AsyncMock(
                    return_value=self.recovery()
                ),
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=AsyncMock(
                    return_value=bad_entry
                ),
            ),
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            ENTRY,
        )
        self.assertIn(
            "LIVE_BUY_RUNTIME_ENTRY_IDENTITY_MISMATCH",
            result.reasons,
        )

    async def test_entry_block_propagates(
        self,
    ):
        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=AsyncMock(
                    return_value=self.recovery()
                ),
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=AsyncMock(
                    return_value=self.entry(
                        status=ENTRY_BLOCK,
                        reasons=("ENTRY_BLOCK",),
                    )
                ),
            ),
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertEqual(
            result.stage,
            ENTRY,
        )

    async def test_entry_unknown_propagates(
        self,
    ):
        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=AsyncMock(
                    return_value=self.recovery()
                ),
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=AsyncMock(
                    return_value=self.entry(
                        status=ENTRY_UNKNOWN,
                        reasons=("ENTRY_UNKNOWN",),
                    )
                ),
            ),
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            ENTRY,
        )

    async def test_entry_exception_is_unknown(
        self,
    ):
        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=AsyncMock(
                    return_value=self.recovery()
                ),
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=AsyncMock(
                    side_effect=RuntimeError(
                        "boom"
                    )
                ),
            ),
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            ENTRY,
        )
        self.assertIn(
            "LIVE_BUY_RUNTIME_ENTRY_EXCEPTION",
            result.reasons,
        )

    async def test_invalid_entry_contract_is_unknown(
        self,
    ):
        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=AsyncMock(
                    return_value=self.recovery()
                ),
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=AsyncMock(
                    return_value=object()
                ),
            ),
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            ENTRY,
        )

    async def test_entry_receives_exact_runtime_inputs(
        self,
    ):
        recovery = AsyncMock(
            return_value=self.recovery()
        )

        entry = AsyncMock(
            return_value=self.entry()
        )

        with (
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=recovery,
            ),
            patch(
                f"{MODULE}.execute_live_buy_entry_once",
                new=entry,
            ),
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            SIGNED,
        )

        kwargs = entry.await_args.kwargs

        self.assertEqual(
            kwargs["mint"],
            self.mint,
        )
        self.assertEqual(
            kwargs["wallet_pubkey"],
            self.wallet,
        )
        self.assertEqual(
            kwargs[
                "signal_virtual_quote_reserves"
            ],
            self.signal_virtual_quote_reserves,
        )
        self.assertIs(
            kwargs["live_curve"],
            self.live_curve,
        )
        self.assertIs(
            kwargs["safety"],
            self.safety,
        )
        self.assertEqual(
            kwargs[
                "signal_virtual_token_reserves"
            ],
            self.signal_virtual_token_reserves,
        )

        self.assertIs(
            kwargs["policy"],
            self.policy,
        )
        self.assertIs(
            kwargs["signer"],
            self.signer,
        )
        self.assertEqual(
            kwargs["compute_unit_limit"],
            250_000,
        )
        self.assertEqual(
            kwargs["db_path"],
            self.db_path,
        )


if __name__ == "__main__":
    unittest.main()
