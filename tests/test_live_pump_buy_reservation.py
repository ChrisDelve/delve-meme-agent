import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from solders.pubkey import Pubkey

from src.portfolio.live_reservations import DB_PATH
from src.execution.pump_execution_simulator import (
    PumpCurveState,
)
from src.portfolio.live_account_risk_state import (
    LIVE_ACCOUNT_RISK_STATE_VERSION,
)
from src.portfolio.live_pump_buy_reservation import (
    BLOCK,
    LIVE_PUMP_BUY_RESERVATION_VERSION,
    PASS,
    UNKNOWN,
    reserve_live_pump_buy,
)
from src.portfolio.live_reservations import (
    ACTIVE,
    RESERVATION_VERSION,
    ReservationDecision,
)
from src.portfolio.live_wallet_valuation import (
    LIVE_WALLET_VALUATION_VERSION,
)
from src.risk.risk_governor import (
    AccountRiskState,
    RiskPolicy,
)


MODULE = (
    "src.portfolio.live_pump_buy_reservation"
)


class LivePumpBuyReservationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.wallet = str(
            Pubkey.new_unique()
        )

        self.mint = str(
            Pubkey.new_unique()
        )

        self.account = AccountRiskState(
            current_equity_lamports=(
                150_000_000
            ),
            day_start_equity_lamports=(
                160_000_000
            ),
            high_water_equity_lamports=(
                170_000_000
            ),
            open_exposure_lamports=(
                20_000_000
            ),
            open_positions=2,
        )

        self.curve = PumpCurveState(
            virtual_quote_reserves=(
                1_000_000_000_000
            ),
            virtual_token_reserves=(
                1_000_000_000_000_000
            ),
            real_quote_reserves=(
                1_000_000_000_000
            ),
            real_token_reserves=(
                1_000_000_000_000_000
            ),
        )

        self.policy = RiskPolicy(
            max_trade_equity_bps=5_000,
            max_total_exposure_bps=6_000,
            max_daily_loss_bps=10_000,
            max_drawdown_bps=10_000,
            max_open_positions=10,
            min_trade_lamports=1_000_000,
            max_size_price_impact_bps=(
                10_000.0
            ),
        )

    def valuation(
        self,
        *,
        raw_balance=100_000_000,
        equity=150_000_000,
        wallet=None,
        version=(
            LIVE_WALLET_VALUATION_VERSION
        ),
        status="RESOLVED",
    ):
        if wallet is None:
            wallet = self.wallet

        return SimpleNamespace(
            resolver_version=version,
            status=status,
            reasons=(),
            wallet_pubkey=wallet,
            wallet_balance_lamports=(
                raw_balance
            ),
            current_equity_lamports=(
                equity
            ),
            wallet_balance_rpc_slot=500,
        )

    def account_result(
        self,
        *,
        account=None,
        valuation=None,
        wallet=None,
        version=(
            LIVE_ACCOUNT_RISK_STATE_VERSION
        ),
        status="RESOLVED",
        reasons=(),
    ):
        if account is None:
            account = self.account

        if valuation is None:
            valuation = self.valuation()

        if wallet is None:
            wallet = self.wallet

        return SimpleNamespace(
            resolver_version=version,
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=wallet,
            account=account,
            wallet_valuation=valuation,
        )

    def pass_decision(
        self,
        *,
        available_cash=90_000_000,
        account=None,
        mint=None,
        wallet=None,
        reservation_version=(
            RESERVATION_VERSION
        ),
        reservation_status=ACTIVE,
    ):
        if account is None:
            account = self.account

        if mint is None:
            mint = self.mint

        if wallet is None:
            wallet = self.wallet

        reservation = SimpleNamespace(
            reservation_version=(
                reservation_version
            ),
            status=reservation_status,
            wallet_pubkey=wallet,
            mint=mint,
            base_available_cash_lamports=(
                available_cash
            ),
            base_open_exposure_lamports=(
                account.open_exposure_lamports
            ),
            base_open_positions=(
                account.open_positions
            ),
        )

        return ReservationDecision(
            status=PASS,
            reasons=(),
            reservation=reservation,
            risk_result=SimpleNamespace(),
        )

    async def resolve(
        self,
        *,
        mint=None,
        wallet=None,
        protected_cash=10_000_000,
        account_result=None,
        decision=None,
        call_overrides=None,
    ):
        if mint is None:
            mint = self.mint

        if wallet is None:
            wallet = self.wallet

        if account_result is None:
            account_result = (
                self.account_result()
            )

        if decision is None:
            decision = (
                self.pass_decision(
                    available_cash=(
                        100_000_000
                        - protected_cash
                    )
                )
            )

        account_mock = AsyncMock(
            return_value=(
                account_result
            )
        )

        reserve_mock = MagicMock(
            return_value=decision
        )

        with (
            patch(
                f"{MODULE}."
                "resolve_live_account_risk_state",
                account_mock,
            ),
            patch(
                f"{MODULE}."
                "reserve_pump_buy_capital",
                reserve_mock,
            ),
        ):
            call_kwargs = {
                "mint": mint,
                "wallet_pubkey": wallet,
                "protected_cash_lamports": (
                    protected_cash
                ),
                "curve_state": self.curve,
                "protocol_fee_bps": 120,
                "creator_fee_bps": 30,
                "buy_slippage_bps": 250,
                "buy_base_network_fee_lamports": (
                    5_000
                ),
                "buy_priority_fee_lamports": (
                    7_000
                ),
                "buy_rent_lamports": (
                    1_844_400
                ),
                "exit_slippage_bps": 400,
                "exit_base_network_fee_lamports": (
                    9_000
                ),
                "exit_priority_fee_lamports": (
                    11_000
                ),
                "reservation_ttl_seconds": 60.0,
                "policy": self.policy,
                "min_context_slot": 450,
            }

            if call_overrides:
                call_kwargs.update(
                    call_overrides
                )

            result = await (
                reserve_live_pump_buy(
                    **call_kwargs
                )
            )

        return (
            result,
            account_mock,
            reserve_mock,
        )

    async def test_invalid_mint_fails_before_account_resolution(
        self,
    ):
        (
            result,
            account,
            reserve,
        ) = await self.resolve(
            mint="   "
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "INVALID_MINT",
            result.reasons,
        )

        account.assert_not_awaited()
        reserve.assert_not_called()

    async def test_invalid_wallet_fails_before_account_resolution(
        self,
    ):
        (
            result,
            account,
            reserve,
        ) = await self.resolve(
            wallet="not-a-wallet"
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "INVALID_WALLET_PUBKEY",
            result.reasons,
        )

        account.assert_not_awaited()
        reserve.assert_not_called()

    async def test_invalid_protected_cash_fails_before_account_resolution(
        self,
    ):
        (
            result,
            account,
            reserve,
        ) = await self.resolve(
            protected_cash=-1
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "INVALID_PROTECTED_CASH",
            result.reasons,
        )

        account.assert_not_awaited()
        reserve.assert_not_called()


    async def test_invalid_execution_inputs_fail_before_live_authority(
        self,
    ):
        cases = (
            (
                {
                    "protocol_fee_bps":
                        10_001,
                },
                "INVALID_PROTOCOL_FEE_BPS",
            ),
            (
                {
                    "creator_fee_bps":
                        -1,
                },
                "INVALID_CREATOR_FEE_BPS",
            ),
            (
                {
                    "buy_slippage_bps":
                        10_001,
                },
                "INVALID_BUY_SLIPPAGE_BPS",
            ),
            (
                {
                    "buy_base_network_fee_lamports":
                        -1,
                },
                "INVALID_BUY_BASE_NETWORK_FEE",
            ),
            (
                {
                    "buy_priority_fee_lamports":
                        -1,
                },
                "INVALID_BUY_PRIORITY_FEE",
            ),
            (
                {
                    "buy_rent_lamports":
                        -1,
                },
                "INVALID_BUY_RENT",
            ),
            (
                {
                    "exit_slippage_bps":
                        10_001,
                },
                "INVALID_EXIT_SLIPPAGE_BPS",
            ),
            (
                {
                    "exit_base_network_fee_lamports":
                        -1,
                },
                "INVALID_EXIT_BASE_NETWORK_FEE",
            ),
            (
                {
                    "exit_priority_fee_lamports":
                        -1,
                },
                "INVALID_EXIT_PRIORITY_FEE",
            ),
            (
                {
                    "min_context_slot":
                        -1,
                },
                "INVALID_MIN_CONTEXT_SLOT",
            ),
            (
                {
                    "reservation_ttl_seconds":
                        0.0,
                },
                "INVALID_RESERVATION_TTL",
            ),
            (
                {
                    "reservation_ttl_seconds":
                        float("nan"),
                },
                "INVALID_RESERVATION_TTL",
            ),
        )

        for overrides, reason in cases:
            with self.subTest(
                reason=reason
            ):
                (
                    result,
                    account,
                    reserve,
                ) = await self.resolve(
                    call_overrides=(
                        overrides
                    )
                )

                self.assertEqual(
                    result.status,
                    UNKNOWN,
                )

                self.assertIn(
                    reason,
                    result.reasons,
                )

                account.assert_not_awaited()
                reserve.assert_not_called()

    async def test_account_state_unknown_blocks_reservation(
        self,
    ):
        account_result = (
            self.account_result(
                status="UNKNOWN",
                reasons=(
                    "LIVE_POSITION_RISK_CHANGED_DURING_VALUATION",
                ),
            )
        )

        (
            result,
            _,
            reserve,
        ) = await self.resolve(
            account_result=(
                account_result
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_ACCOUNT_RISK_STATE_NOT_RESOLVED",
            result.reasons,
        )

        reserve.assert_not_called()

    async def test_account_state_identity_is_bound(
        self,
    ):
        cases = (
            (
                self.account_result(
                    version="wrong-version"
                ),
                "LIVE_ACCOUNT_RISK_STATE_VERSION_MISMATCH",
            ),
            (
                self.account_result(
                    wallet=str(
                        Pubkey.new_unique()
                    )
                ),
                "LIVE_ACCOUNT_RISK_STATE_WALLET_MISMATCH",
            ),
            (
                self.account_result(
                    account=SimpleNamespace()
                ),
                None,
            ),
        )

        for account_result, reason in cases:
            with self.subTest(
                reason=reason
            ):
                if reason is None:
                    continue

                result = (
                    await self.resolve(
                        account_result=(
                            account_result
                        )
                    )
                )[0]

                self.assertEqual(
                    result.status,
                    UNKNOWN,
                )

                self.assertIn(
                    reason,
                    result.reasons,
                )

    async def test_wallet_valuation_identity_is_bound(
        self,
    ):
        wrong_wallet = str(
            Pubkey.new_unique()
        )

        cases = (
            (
                None,
                "LIVE_WALLET_VALUATION_MISSING",
            ),
            (
                self.valuation(
                    version="wrong-version"
                ),
                "LIVE_WALLET_VALUATION_VERSION_MISMATCH",
            ),
            (
                self.valuation(
                    status="UNKNOWN"
                ),
                "LIVE_WALLET_VALUATION_NOT_RESOLVED",
            ),
            (
                self.valuation(
                    wallet=wrong_wallet
                ),
                "LIVE_WALLET_VALUATION_WALLET_MISMATCH",
            ),
        )

        for valuation, reason in cases:
            with self.subTest(
                reason=reason
            ):
                account_result = (
                    self.account_result()
                )

                account_result.wallet_valuation = (
                    valuation
                )

                result = (
                    await self.resolve(
                        account_result=(
                            account_result
                        )
                    )
                )[0]

                self.assertEqual(
                    result.status,
                    UNKNOWN,
                )

                self.assertIn(
                    reason,
                    result.reasons,
                )

    async def test_invalid_raw_wallet_balance_blocks_reservation(
        self,
    ):
        valuation = self.valuation(
            raw_balance=-1
        )

        result, _, reserve = (
            await self.resolve(
                account_result=(
                    self.account_result(
                        valuation=valuation
                    )
                )
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_WALLET_BALANCE_INVALID",
            result.reasons,
        )

        reserve.assert_not_called()

    async def test_account_equity_must_match_wallet_valuation(
        self,
    ):
        valuation = self.valuation(
            equity=149_000_000
        )

        result, _, reserve = (
            await self.resolve(
                account_result=(
                    self.account_result(
                        valuation=valuation
                    )
                )
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_ACCOUNT_EQUITY_BINDING_MISMATCH",
            result.reasons,
        )

        reserve.assert_not_called()

    async def test_protected_cash_cannot_exceed_wallet_balance(
        self,
    ):
        result, _, reserve = (
            await self.resolve(
                protected_cash=(
                    100_000_001
                )
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "PROTECTED_CASH_EXCEEDS_WALLET_BALANCE",
            result.reasons,
        )

        self.assertEqual(
            result.available_cash_lamports,
            0,
        )

        reserve.assert_not_called()

    async def test_protected_cash_can_consume_entire_wallet_without_reserving(
        self,
    ):
        result, _, reserve = (
            await self.resolve(
                protected_cash=(
                    100_000_000
                )
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "NO_AVAILABLE_CASH_AFTER_PROTECTED_RESERVE",
            result.reasons,
        )

        reserve.assert_not_called()

    async def test_cash_is_raw_balance_minus_protected_once_and_policies_stay_separate(
        self,
    ):
        (
            result,
            account_mock,
            reserve_mock,
        ) = await self.resolve(
            protected_cash=(
                10_000_000
            )
        )

        self.assertEqual(
            result.resolver_version,
            LIVE_PUMP_BUY_RESERVATION_VERSION,
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertEqual(
            result.raw_wallet_balance_lamports,
            100_000_000,
        )

        self.assertEqual(
            result.protected_cash_lamports,
            10_000_000,
        )

        self.assertEqual(
            result.available_cash_lamports,
            90_000_000,
        )

        account_mock.assert_awaited_once_with(
            wallet_pubkey=self.wallet,
            slippage_bps=400,
            base_network_fee_lamports=(
                9_000
            ),
            priority_fee_lamports=(
                11_000
            ),
            min_context_slot=450,
            db_path=DB_PATH,
        )

        reserve_mock.assert_called_once_with(
            mint=self.mint,
            wallet_pubkey=self.wallet,
            available_cash_lamports=(
                90_000_000
            ),
            account=self.account,
            curve_state=self.curve,
            protocol_fee_bps=120,
            creator_fee_bps=30,
            slippage_bps=250,
            base_network_fee_lamports=(
                5_000
            ),
            priority_fee_lamports=(
                7_000
            ),
            rent_lamports=1_844_400,
            reservation_ttl_seconds=60.0,
            policy=self.policy,
            db_path=DB_PATH,
        )

    async def test_zero_protected_cash_passes_raw_balance_unchanged(
        self,
    ):
        decision = self.pass_decision(
            available_cash=(
                100_000_000
            )
        )

        (
            result,
            _,
            reserve,
        ) = await self.resolve(
            protected_cash=0,
            decision=decision,
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertEqual(
            result.available_cash_lamports,
            100_000_000,
        )

        self.assertEqual(
            reserve.call_args.kwargs[
                "available_cash_lamports"
            ],
            100_000_000,
        )

    async def test_block_reservation_decision_is_propagated(
        self,
    ):
        decision = ReservationDecision(
            status=BLOCK,
            reasons=(
                "INSUFFICIENT_UNRESERVED_CASH",
            ),
            reservation=None,
            risk_result=SimpleNamespace(),
        )

        result = (
            await self.resolve(
                decision=decision
            )
        )[0]

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertEqual(
            result.reasons,
            (
                "INSUFFICIENT_UNRESERVED_CASH",
            ),
        )

        self.assertIs(
            result.reservation_decision,
            decision,
        )

    async def test_unknown_reservation_decision_is_propagated(
        self,
    ):
        decision = ReservationDecision(
            status=UNKNOWN,
            reasons=(
                "RISK_SIMULATION_FINGERPRINT_FAILED",
            ),
            reservation=None,
            risk_result=None,
        )

        result = (
            await self.resolve(
                decision=decision
            )
        )[0]

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "RISK_SIMULATION_FINGERPRINT_FAILED",
            ),
        )

    async def test_pass_reservation_binding_is_verified(
        self,
    ):
        bad = self.pass_decision(
            available_cash=(
                89_999_999
            )
        )

        result = (
            await self.resolve(
                decision=bad
            )
        )[0]

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_CAPITAL_RESERVATION_BINDING_MISMATCH",
            result.reasons,
        )

    def test_public_contract_exposes_database_override_and_separates_buy_from_exit_policy(
        self,
    ):
        parameters = (
            inspect.signature(
                reserve_live_pump_buy
            )
            .parameters
        )

        self.assertIn(
            "db_path",
            parameters,
        )

        self.assertEqual(
            parameters["db_path"].default,
            DB_PATH,
        )

        for name in (
            "buy_slippage_bps",
            "buy_base_network_fee_lamports",
            "buy_priority_fee_lamports",
            "buy_rent_lamports",
            "exit_slippage_bps",
            "exit_base_network_fee_lamports",
            "exit_priority_fee_lamports",
            "protected_cash_lamports",
        ):
            self.assertIn(
                name,
                parameters,
            )


    async def test_custom_database_path_reaches_risk_and_reservation(
        self,
    ):
        custom_path = (
            DB_PATH.parent
            / "buy-reservation-custom.db"
        )

        (
            result,
            account,
            reserve,
        ) = await self.resolve(
            call_overrides={
                "db_path": custom_path,
            },
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertEqual(
            account.await_args.kwargs.get(
                "db_path"
            ),
            custom_path,
        )

        self.assertEqual(
            reserve.call_args.kwargs.get(
                "db_path"
            ),
            custom_path,
        )



if __name__ == "__main__":
    unittest.main()
