import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.portfolio.live_reservations import DB_PATH
from src.execution.live_exit_controller import (
    BLOCK,
    CLAIMED,
    IDLE,
    UNKNOWN,
    control_live_exit_once,
)
from src.execution.live_sell_initiator import (
    BLOCK as INITIATOR_BLOCK,
    CLAIMED as INITIATOR_CLAIMED,
    UNKNOWN as INITIATOR_UNKNOWN,
    LIVE_SELL_INITIATOR_VERSION,
    LiveSellInitiationResult,
)
from src.portfolio.live_wallet_valuation import (
    RESOLVED,
    UNKNOWN as VALUATION_UNKNOWN,
    LIVE_WALLET_VALUATION_VERSION,
    LiveWalletValuationResult,
)
from src.strategies.live_exit_policy import (
    FULL_EXIT,
    HOLD,
    UNKNOWN as POLICY_UNKNOWN,
    LIVE_EXIT_POLICY_VERSION,
    LiveExitDecision,
    LiveExitPolicy,
)


class LiveExitControllerTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.wallet = (
            "11111111111111111111111111111112"
        )

        self.policy = LiveExitPolicy(
            take_profit_return_bps=2_000,
            stop_loss_return_bps=-4_000,
            max_hold_seconds=900,
        )

        self.evaluated_at = 2_000

    def mint_valuation(
        self,
        mint,
        tokens=1_000,
    ):
        return SimpleNamespace(
            mint=mint,
            tokens_held=tokens,
        )

    def wallet_result(
        self,
        valuations=(),
        *,
        status=RESOLVED,
        reasons=(),
        wallet=None,
        version=(
            LIVE_WALLET_VALUATION_VERSION
        ),
    ):
        if wallet is None:
            wallet = self.wallet

        return LiveWalletValuationResult(
            resolver_version=version,
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=wallet,
            positions_loader_version="positions-v",
            wallet_balance_version="balance-v",
            wallet_balance_lamports=1_000_000,
            wallet_balance_rpc_slot=100,
            open_position_lots=(
                len(valuations)
            ),
            unique_open_mints=(
                len(valuations)
            ),
            total_liquidation_value_lamports=0,
            current_equity_lamports=1_000_000,
            min_context_slot=100,
            mint_valuations=tuple(
                valuations
            ),
        )

    def decision(
        self,
        *,
        mint,
        status,
        reason=None,
        tokens=1_000,
    ):
        tokens_to_sell = (
            tokens
            if status == FULL_EXIT
            else 0
        )

        return LiveExitDecision(
            evaluator_version=(
                LIVE_EXIT_POLICY_VERSION
            ),
            status=status,
            reason=reason,
            mint=mint,
            tokens_held=tokens,
            tokens_to_sell=(
                tokens_to_sell
            ),
            evaluated_at=(
                self.evaluated_at
            ),
            age_seconds=500,
            total_entry_wallet_cost_lamports=(
                1_000_000
            ),
            cumulative_net_proceeds_lamports=0,
            liquidation_value_lamports=(
                1_000_000
            ),
            return_bps=0.0,
            policy=self.policy,
        )

    def initiation(
        self,
        *,
        mint,
        tokens=1_000,
        status=INITIATOR_CLAIMED,
        reasons=(),
    ):
        return LiveSellInitiationResult(
            initiator_version=(
                LIVE_SELL_INITIATOR_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=self.wallet,
            mint=mint,
            tokens_to_sell=tokens,
            authorization_sha256=(
                "a" * 64
                if status
                == INITIATOR_CLAIMED
                else None
            ),
            authorization_status=(
                "AUTHORIZED"
                if status
                == INITIATOR_CLAIMED
                else None
            ),
            claim_status=(
                "ACTIVE"
                if status
                == INITIATOR_CLAIMED
                else None
            ),
            claim_changed=(
                True
                if status
                == INITIATOR_CLAIMED
                else None
            ),
            authorization=(
                object()
                if status
                == INITIATOR_CLAIMED
                else None
            ),
            claim=(
                object()
                if status
                == INITIATOR_CLAIMED
                else None
            ),
        )

    async def run_controller(
        self,
        *,
        wallet_result,
        decisions=(),
        initiation=None,
        evaluated_at=None,
        db_path=DB_PATH,
    ):
        if evaluated_at is None:
            evaluated_at = self.evaluated_at

        with patch(
            "src.execution.live_exit_controller."
            "resolve_live_wallet_valuation",
            new=AsyncMock(
                return_value=wallet_result
            ),
        ) as valuation_mock, patch(
            "src.execution.live_exit_controller."
            "evaluate_live_exit",
            side_effect=list(decisions),
        ) as policy_mock, patch(
            "src.execution.live_exit_controller."
            "initiate_live_sell_once",
            new=AsyncMock(
                return_value=initiation
            ),
        ) as initiation_mock:
            result = await control_live_exit_once(
                wallet_pubkey=self.wallet,
                evaluated_at=evaluated_at,
                policy=self.policy,
                slippage_bps=300,
                base_network_fee_lamports=5_000,
                priority_fee_lamports=0,
                db_path=db_path,
            )

        return (
            result,
            valuation_mock,
            policy_mock,
            initiation_mock,
        )

    async def test_invalid_time_fails_before_reads(
        self,
    ):
        (
            result,
            valuation_mock,
            policy_mock,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=None,
            evaluated_at=0,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "INVALID_EVALUATED_AT",
            result.reasons,
        )

        valuation_mock.assert_not_awaited()
        policy_mock.assert_not_called()
        initiation_mock.assert_not_awaited()

    async def test_wallet_valuation_unknown_stops(
        self,
    ):
        wallet_result = self.wallet_result(
            status=VALUATION_UNKNOWN,
            reasons=("RPC_UNKNOWN",),
        )

        (
            result,
            _,
            policy_mock,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=wallet_result,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "WALLET_VALUATION_UNKNOWN",
            result.reasons,
        )

        self.assertIn(
            "RPC_UNKNOWN",
            result.reasons,
        )

        policy_mock.assert_not_called()
        initiation_mock.assert_not_awaited()

    async def test_empty_wallet_is_idle(
        self,
    ):
        wallet_result = self.wallet_result()

        (
            result,
            _,
            policy_mock,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=wallet_result,
        )

        self.assertEqual(
            result.status,
            IDLE,
        )

        self.assertEqual(
            result.decisions,
            (),
        )

        policy_mock.assert_not_called()
        initiation_mock.assert_not_awaited()

    async def test_all_hold_is_idle(
        self,
    ):
        a = self.mint_valuation(
            "mint-a"
        )
        b = self.mint_valuation(
            "mint-b"
        )

        wallet_result = self.wallet_result(
            (a, b)
        )

        (
            result,
            _,
            policy_mock,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=wallet_result,
            decisions=(
                self.decision(
                    mint="mint-a",
                    status=HOLD,
                ),
                self.decision(
                    mint="mint-b",
                    status=HOLD,
                ),
            ),
        )

        self.assertEqual(
            result.status,
            IDLE,
        )

        self.assertEqual(
            len(result.decisions),
            2,
        )

        self.assertEqual(
            policy_mock.call_count,
            2,
        )

        initiation_mock.assert_not_awaited()

    async def test_policy_unknown_without_exit_fails_closed(
        self,
    ):
        a = self.mint_valuation(
            "mint-a"
        )

        wallet_result = self.wallet_result(
            (a,)
        )

        (
            result,
            _,
            _,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=wallet_result,
            decisions=(
                self.decision(
                    mint="mint-a",
                    status=POLICY_UNKNOWN,
                    reason=(
                        "FULL_LIQUIDATION_UNAVAILABLE"
                    ),
                ),
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "EXIT_POLICY_UNKNOWN:"
            "mint-a:"
            "FULL_LIQUIDATION_UNAVAILABLE",
            result.reasons,
        )

        initiation_mock.assert_not_awaited()

    async def test_full_exit_initiates_exactly_once(
        self,
    ):
        a = self.mint_valuation(
            "mint-a"
        )

        wallet_result = self.wallet_result(
            (a,)
        )

        exit_decision = self.decision(
            mint="mint-a",
            status=FULL_EXIT,
            reason="TAKE_PROFIT",
        )

        initiation = self.initiation(
            mint="mint-a"
        )

        (
            result,
            _,
            _,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=wallet_result,
            decisions=(
                exit_decision,
            ),
            initiation=initiation,
        )

        self.assertEqual(
            result.status,
            CLAIMED,
        )

        self.assertEqual(
            result.selected_decision,
            exit_decision,
        )

        initiation_mock.assert_awaited_once_with(
            wallet_pubkey=self.wallet,
            mint="mint-a",
            tokens_to_sell=1_000,
            slippage_bps=300,
            base_network_fee_lamports=5_000,
            priority_fee_lamports=0,
            db_path=DB_PATH,
        )

    async def test_custom_database_path_reaches_valuation_and_initiation(
        self,
    ):
        custom_path = (
            DB_PATH.parent
            / "exit-controller-custom.db"
        )

        mint_valuation = self.mint_valuation(
            "mint-a"
        )

        wallet_result = self.wallet_result(
            (mint_valuation,)
        )

        exit_decision = self.decision(
            mint="mint-a",
            status=FULL_EXIT,
            reason="TAKE_PROFIT",
        )

        initiation = self.initiation(
            mint="mint-a"
        )

        (
            result,
            valuation,
            _,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=wallet_result,
            decisions=(
                exit_decision,
            ),
            initiation=initiation,
            db_path=custom_path,
        )

        self.assertEqual(
            result.status,
            CLAIMED,
        )

        self.assertEqual(
            valuation.await_args.kwargs.get(
                "db_path"
            ),
            custom_path,
        )

        self.assertEqual(
            initiation_mock.await_args.kwargs.get(
                "db_path"
            ),
            custom_path,
        )

    async def test_stop_loss_has_priority_over_take_profit(
        self,
    ):
        a = self.mint_valuation(
            "mint-a"
        )
        b = self.mint_valuation(
            "mint-b"
        )

        wallet_result = self.wallet_result(
            (a, b)
        )

        take_profit = self.decision(
            mint="mint-a",
            status=FULL_EXIT,
            reason="TAKE_PROFIT",
        )

        stop_loss = self.decision(
            mint="mint-b",
            status=FULL_EXIT,
            reason="STOP_LOSS",
        )

        initiation = self.initiation(
            mint="mint-b"
        )

        (
            result,
            _,
            _,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=wallet_result,
            decisions=(
                take_profit,
                stop_loss,
            ),
            initiation=initiation,
        )

        self.assertEqual(
            result.status,
            CLAIMED,
        )

        self.assertEqual(
            result.selected_decision,
            stop_loss,
        )

        initiation_mock.assert_awaited_once()

        self.assertEqual(
            initiation_mock.await_args.kwargs[
                "mint"
            ],
            "mint-b",
        )

    async def test_unrelated_unknown_does_not_suppress_valid_exit(
        self,
    ):
        a = self.mint_valuation(
            "mint-a"
        )
        b = self.mint_valuation(
            "mint-b"
        )

        wallet_result = self.wallet_result(
            (a, b)
        )

        unknown = self.decision(
            mint="mint-a",
            status=POLICY_UNKNOWN,
            reason=(
                "FULL_LIQUIDATION_UNAVAILABLE"
            ),
        )

        stop_loss = self.decision(
            mint="mint-b",
            status=FULL_EXIT,
            reason="STOP_LOSS",
        )

        initiation = self.initiation(
            mint="mint-b"
        )

        (
            result,
            _,
            _,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=wallet_result,
            decisions=(
                unknown,
                stop_loss,
            ),
            initiation=initiation,
        )

        self.assertEqual(
            result.status,
            CLAIMED,
        )

        self.assertEqual(
            result.selected_decision.mint,
            "mint-b",
        )

        initiation_mock.assert_awaited_once()

    async def test_malformed_exit_binding_never_initiates(
        self,
    ):
        a = self.mint_valuation(
            "mint-a"
        )

        wallet_result = self.wallet_result(
            (a,)
        )

        malformed = self.decision(
            mint="wrong-mint",
            status=FULL_EXIT,
            reason="STOP_LOSS",
        )

        (
            result,
            _,
            _,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=wallet_result,
            decisions=(
                malformed,
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "EXIT_DECISION_BINDING_MISMATCH",
            result.reasons,
        )

        initiation_mock.assert_not_awaited()

    async def test_initiator_block_propagates(
        self,
    ):
        a = self.mint_valuation(
            "mint-a"
        )

        wallet_result = self.wallet_result(
            (a,)
        )

        exit_decision = self.decision(
            mint="mint-a",
            status=FULL_EXIT,
            reason="STOP_LOSS",
        )

        initiation = self.initiation(
            mint="mint-a",
            status=INITIATOR_BLOCK,
            reasons=("SELL_BLOCKED",),
        )

        (
            result,
            _,
            _,
            _,
        ) = await self.run_controller(
            wallet_result=wallet_result,
            decisions=(
                exit_decision,
            ),
            initiation=initiation,
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SELL_BLOCKED",
            result.reasons,
        )

    async def test_initiator_unknown_propagates(
        self,
    ):
        a = self.mint_valuation(
            "mint-a"
        )

        wallet_result = self.wallet_result(
            (a,)
        )

        exit_decision = self.decision(
            mint="mint-a",
            status=FULL_EXIT,
            reason="STOP_LOSS",
        )

        initiation = self.initiation(
            mint="mint-a",
            status=INITIATOR_UNKNOWN,
            reasons=("SELL_UNKNOWN",),
        )

        (
            result,
            _,
            _,
            _,
        ) = await self.run_controller(
            wallet_result=wallet_result,
            decisions=(
                exit_decision,
            ),
            initiation=initiation,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_UNKNOWN",
            result.reasons,
        )

    async def test_initiator_binding_mismatch_fails_closed(
        self,
    ):
        a = self.mint_valuation(
            "mint-a"
        )

        wallet_result = self.wallet_result(
            (a,)
        )

        exit_decision = self.decision(
            mint="mint-a",
            status=FULL_EXIT,
            reason="STOP_LOSS",
        )

        initiation = self.initiation(
            mint="wrong-mint"
        )

        (
            result,
            _,
            _,
            _,
        ) = await self.run_controller(
            wallet_result=wallet_result,
            decisions=(
                exit_decision,
            ),
            initiation=initiation,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_SELL_INITIATION_BINDING_MISMATCH",
            result.reasons,
        )


    async def test_wallet_valuation_version_mismatch_stops_before_policy(
        self,
    ):
        wallet_result = self.wallet_result(
            version="wrong-version",
        )

        (
            result,
            _,
            policy_mock,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=wallet_result,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "WALLET_VALUATION_VERSION_MISMATCH",
            result.reasons,
        )

        policy_mock.assert_not_called()
        initiation_mock.assert_not_awaited()

    async def test_wallet_valuation_identity_mismatch_stops_before_policy(
        self,
    ):
        wallet_result = self.wallet_result(
            wallet=(
                "11111111111111111111111111111113"
            ),
        )

        (
            result,
            _,
            policy_mock,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=wallet_result,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "WALLET_VALUATION_IDENTITY_MISMATCH",
            result.reasons,
        )

        policy_mock.assert_not_called()
        initiation_mock.assert_not_awaited()

    async def test_mint_valuation_count_mismatch_stops_before_policy(
        self,
    ):
        a = self.mint_valuation(
            "mint-a"
        )

        wallet_result = self.wallet_result(
            (a,)
        )

        wallet_result = LiveWalletValuationResult(
            **{
                **wallet_result.__dict__,
                "unique_open_mints": 2,
            }
        )

        (
            result,
            _,
            policy_mock,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=wallet_result,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "MINT_VALUATION_COUNT_MISMATCH",
            result.reasons,
        )

        policy_mock.assert_not_called()
        initiation_mock.assert_not_awaited()

    async def test_duplicate_mint_valuation_stops_before_policy(
        self,
    ):
        a1 = self.mint_valuation(
            "mint-a"
        )
        a2 = self.mint_valuation(
            "mint-a"
        )

        wallet_result = self.wallet_result(
            (a1, a2)
        )

        (
            result,
            _,
            policy_mock,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=wallet_result,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "DUPLICATE_MINT_VALUATION",
            result.reasons,
        )

        policy_mock.assert_not_called()
        initiation_mock.assert_not_awaited()

    async def test_same_priority_uses_mint_as_deterministic_tiebreaker(
        self,
    ):
        b = self.mint_valuation(
            "mint-b"
        )
        a = self.mint_valuation(
            "mint-a"
        )

        wallet_result = self.wallet_result(
            (b, a)
        )

        exit_a = self.decision(
            mint="mint-a",
            status=FULL_EXIT,
            reason="TAKE_PROFIT",
        )

        exit_b = self.decision(
            mint="mint-b",
            status=FULL_EXIT,
            reason="TAKE_PROFIT",
        )

        initiation = self.initiation(
            mint="mint-a"
        )

        (
            result,
            _,
            policy_mock,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=wallet_result,
            decisions=(
                exit_a,
                exit_b,
            ),
            initiation=initiation,
        )

        self.assertEqual(
            result.status,
            CLAIMED,
        )

        self.assertEqual(
            policy_mock.call_count,
            2,
        )

        self.assertEqual(
            result.selected_decision.mint,
            "mint-a",
        )

        self.assertEqual(
            initiation_mock.await_args.kwargs[
                "mint"
            ],
            "mint-a",
        )

    async def test_policy_exception_never_initiates(
        self,
    ):
        a = self.mint_valuation(
            "mint-a"
        )

        wallet_result = self.wallet_result(
            (a,)
        )

        (
            result,
            _,
            _,
            initiation_mock,
        ) = await self.run_controller(
            wallet_result=wallet_result,
            decisions=(
                RuntimeError(
                    "policy exploded"
                ),
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "EXIT_POLICY_EXCEPTION",
            result.reasons,
        )

        initiation_mock.assert_not_awaited()

    async def test_wallet_valuation_exception_fails_closed(
        self,
    ):
        with patch(
            "src.execution.live_exit_controller."
            "resolve_live_wallet_valuation",
            new=AsyncMock(
                side_effect=RuntimeError(
                    "valuation exploded"
                )
            ),
        ) as valuation_mock, patch(
            "src.execution.live_exit_controller."
            "evaluate_live_exit",
        ) as policy_mock, patch(
            "src.execution.live_exit_controller."
            "initiate_live_sell_once",
            new=AsyncMock(),
        ) as initiation_mock:
            result = await control_live_exit_once(
                wallet_pubkey=self.wallet,
                evaluated_at=(
                    self.evaluated_at
                ),
                policy=self.policy,
                slippage_bps=300,
                base_network_fee_lamports=5_000,
                priority_fee_lamports=0,
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "WALLET_VALUATION_EXCEPTION",
            result.reasons,
        )

        valuation_mock.assert_awaited_once()
        policy_mock.assert_not_called()
        initiation_mock.assert_not_awaited()

    async def test_initiation_exception_fails_closed_after_single_attempt(
        self,
    ):
        a = self.mint_valuation(
            "mint-a"
        )

        wallet_result = self.wallet_result(
            (a,)
        )

        exit_decision = self.decision(
            mint="mint-a",
            status=FULL_EXIT,
            reason="STOP_LOSS",
        )

        with patch(
            "src.execution.live_exit_controller."
            "resolve_live_wallet_valuation",
            new=AsyncMock(
                return_value=wallet_result
            ),
        ), patch(
            "src.execution.live_exit_controller."
            "evaluate_live_exit",
            return_value=exit_decision,
        ), patch(
            "src.execution.live_exit_controller."
            "initiate_live_sell_once",
            new=AsyncMock(
                side_effect=RuntimeError(
                    "initiation exploded"
                )
            ),
        ) as initiation_mock:
            result = await control_live_exit_once(
                wallet_pubkey=self.wallet,
                evaluated_at=(
                    self.evaluated_at
                ),
                policy=self.policy,
                slippage_bps=300,
                base_network_fee_lamports=5_000,
                priority_fee_lamports=0,
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_SELL_INITIATION_EXCEPTION",
            result.reasons,
        )

        initiation_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
