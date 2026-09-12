import unittest
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from solders.pubkey import Pubkey

from src.portfolio.live_reservations import DB_PATH
from src.portfolio.live_account_risk_state import (
    LIVE_ACCOUNT_RISK_STATE_VERSION,
    RESOLVED,
    UNKNOWN,
    resolve_live_account_risk_state,
)
from src.portfolio.live_equity_continuity import (
    LIVE_EQUITY_CONTINUITY_VERSION,
)
from src.portfolio.live_positions import (
    LIVE_POSITION_RISK_TOTALS_VERSION,
)
from src.portfolio.live_wallet_valuation import (
    LIVE_WALLET_VALUATION_VERSION,
)


MODULE = (
    "src.portfolio.live_account_risk_state"
)


class LiveAccountRiskStateTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.wallet = str(
            Pubkey.new_unique()
        )

        self.slippage_bps = 300
        self.base_fee = 5_000
        self.priority_fee = 0

    def risk_totals(
        self,
        *,
        exposure=2_000_000,
        positions=2,
        status="PASS",
        version=(
            LIVE_POSITION_RISK_TOTALS_VERSION
        ),
        wallet=None,
        reasons=(),
    ):
        if wallet is None:
            wallet = self.wallet

        return SimpleNamespace(
            loader_version=version,
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=wallet,
            open_exposure_lamports=(
                exposure
            ),
            open_positions=positions,
        )

    def valuation(
        self,
        *,
        equity=10_000_000,
        slot=500,
        open_lots=2,
        status="RESOLVED",
        version=(
            LIVE_WALLET_VALUATION_VERSION
        ),
        wallet=None,
        reasons=(),
    ):
        if wallet is None:
            wallet = self.wallet

        return SimpleNamespace(
            resolver_version=version,
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=wallet,
            current_equity_lamports=(
                equity
            ),
            wallet_balance_rpc_slot=slot,
            open_position_lots=(
                open_lots
            ),
        )

    def continuity_state(
        self,
        *,
        current=10_000_000,
        day_start=11_000_000,
        high_water=12_000_000,
        slot=500,
        wallet=None,
        version=(
            LIVE_EQUITY_CONTINUITY_VERSION
        ),
        valuation_version=(
            LIVE_WALLET_VALUATION_VERSION
        ),
    ):
        if wallet is None:
            wallet = self.wallet

        return SimpleNamespace(
            continuity_version=version,
            wallet_pubkey=wallet,
            day_key="2026-09-09",
            day_start_equity_lamports=(
                day_start
            ),
            high_water_equity_lamports=(
                high_water
            ),
            latest_equity_lamports=(
                current
            ),
            latest_valuation_version=(
                valuation_version
            ),
            latest_wallet_balance_rpc_slot=(
                slot
            ),
            latest_observed_at=100.0,
            created_at=90.0,
            updated_at=100.0,
        )

    def continuity_result(
        self,
        *,
        state=None,
        status="PASS",
        reasons=(),
    ):
        if (
            state is None
            and status == "PASS"
        ):
            state = (
                self.continuity_state()
            )

        return SimpleNamespace(
            status=status,
            reasons=tuple(reasons),
            state=state,
            changed=True,
        )

    async def resolve(
        self,
        *,
        wallet=None,
        slippage_bps=None,
        base_fee=None,
        priority_fee=None,
        min_context_slot=None,
        risk_results=None,
        valuation=None,
        continuity=None,
        db_path=DB_PATH,
    ):
        if wallet is None:
            wallet = self.wallet

        if slippage_bps is None:
            slippage_bps = (
                self.slippage_bps
            )

        if base_fee is None:
            base_fee = self.base_fee

        if priority_fee is None:
            priority_fee = (
                self.priority_fee
            )

        if risk_results is None:
            risk = self.risk_totals()

            risk_results = [
                risk,
                risk,
            ]

        risk_mock = MagicMock(
            side_effect=risk_results
        )

        if valuation is None:
            valuation = self.valuation()

        valuation_mock = AsyncMock(
            return_value=valuation
        )

        if continuity is None:
            continuity = (
                self.continuity_result()
            )

        continuity_mock = MagicMock(
            return_value=continuity
        )

        with (
            patch(
                f"{MODULE}."
                "load_live_position_risk_totals_read_only",
                risk_mock,
            ),
            patch(
                f"{MODULE}."
                "resolve_live_wallet_valuation",
                valuation_mock,
            ),
            patch(
                f"{MODULE}."
                "record_live_equity_continuity",
                continuity_mock,
            ),
        ):
            result = await (
                resolve_live_account_risk_state(
                    wallet_pubkey=wallet,
                    slippage_bps=(
                        slippage_bps
                    ),
                    base_network_fee_lamports=(
                        base_fee
                    ),
                    priority_fee_lamports=(
                        priority_fee
                    ),
                    min_context_slot=(
                        min_context_slot
                    ),
                    db_path=db_path,
                )
            )

        return (
            result,
            risk_mock,
            valuation_mock,
            continuity_mock,
        )

    async def test_invalid_wallet_fails_before_any_authority(
        self,
    ):
        (
            result,
            risk,
            valuation,
            continuity,
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

        risk.assert_not_called()
        valuation.assert_not_awaited()
        continuity.assert_not_called()

    async def test_invalid_policy_inputs_fail_before_reads(
        self,
    ):
        cases = (
            {
                "slippage_bps":
                    10_001,
                "reason":
                    "INVALID_SLIPPAGE_BPS",
            },
            {
                "base_fee":
                    -1,
                "reason":
                    "INVALID_BASE_NETWORK_FEE",
            },
            {
                "priority_fee":
                    -1,
                "reason":
                    "INVALID_PRIORITY_FEE",
            },
            {
                "min_context_slot":
                    -1,
                "reason":
                    "INVALID_MIN_CONTEXT_SLOT",
            },
        )

        for case in cases:
            with self.subTest(
                reason=case["reason"]
            ):
                kwargs = dict(case)
                reason = kwargs.pop(
                    "reason"
                )

                (
                    result,
                    risk,
                    valuation,
                    continuity,
                ) = await self.resolve(
                    **kwargs
                )

                self.assertEqual(
                    result.status,
                    UNKNOWN,
                )

                self.assertIn(
                    reason,
                    result.reasons,
                )

                risk.assert_not_called()
                valuation.assert_not_awaited()
                continuity.assert_not_called()

    async def test_first_risk_totals_unknown_blocks_valuation(
        self,
    ):
        risk = self.risk_totals(
            status="UNKNOWN",
            reasons=(
                "LIVE_DATABASE_NOT_FOUND",
            ),
        )

        (
            result,
            _,
            valuation,
            continuity,
        ) = await self.resolve(
            risk_results=[
                risk,
            ],
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_POSITION_RISK_TOTALS_NOT_PASS",
            result.reasons,
        )

        valuation.assert_not_awaited()
        continuity.assert_not_called()

    async def test_first_risk_totals_identity_is_bound(
        self,
    ):
        cases = (
            (
                self.risk_totals(
                    version="wrong-version"
                ),
                "LIVE_POSITION_RISK_TOTALS_VERSION_MISMATCH",
            ),
            (
                self.risk_totals(
                    wallet=str(
                        Pubkey.new_unique()
                    )
                ),
                "LIVE_POSITION_RISK_TOTALS_WALLET_MISMATCH",
            ),
        )

        for risk_result, reason in cases:
            with self.subTest(
                reason=reason
            ):
                result = (
                    await self.resolve(
                        risk_results=[
                            risk_result,
                        ],
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

    async def test_wallet_valuation_unknown_blocks_continuity(
        self,
    ):
        risk = self.risk_totals()

        valuation = self.valuation(
            status="UNKNOWN",
            reasons=(
                "MINT_LIQUIDATION_UNKNOWN",
            ),
        )

        (
            result,
            risk_mock,
            _,
            continuity,
        ) = await self.resolve(
            risk_results=[
                risk,
            ],
            valuation=valuation,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_WALLET_VALUATION_NOT_RESOLVED",
            result.reasons,
        )

        self.assertEqual(
            risk_mock.call_count,
            1,
        )

        continuity.assert_not_called()

    async def test_wallet_valuation_identity_is_bound(
        self,
    ):
        wrong_wallet = str(
            Pubkey.new_unique()
        )

        cases = (
            (
                self.valuation(
                    version="wrong-version"
                ),
                "LIVE_WALLET_VALUATION_VERSION_MISMATCH",
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
                result = (
                    await self.resolve(
                        risk_results=[
                            self.risk_totals(),
                        ],
                        valuation=valuation,
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

    async def test_invalid_current_equity_blocks_continuity(
        self,
    ):
        (
            result,
            risk,
            _,
            continuity,
        ) = await self.resolve(
            risk_results=[
                self.risk_totals(),
            ],
            valuation=self.valuation(
                equity=0
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_ACCOUNT_CURRENT_EQUITY_INVALID",
            result.reasons,
        )

        self.assertEqual(
            risk.call_count,
            1,
        )

        continuity.assert_not_called()

    async def test_final_risk_totals_unknown_blocks_continuity(
        self,
    ):
        before = self.risk_totals()

        after = self.risk_totals(
            status="UNKNOWN",
            reasons=(
                "LIVE_POSITION_VERSION_MISMATCH",
            ),
        )

        (
            result,
            risk,
            _,
            continuity,
        ) = await self.resolve(
            risk_results=[
                before,
                after,
            ],
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FINAL_LIVE_POSITION_RISK_TOTALS_NOT_PASS",
            result.reasons,
        )

        self.assertEqual(
            risk.call_count,
            2,
        )

        continuity.assert_not_called()

    async def test_risk_totals_change_during_valuation_fails_closed(
        self,
    ):
        before = self.risk_totals(
            exposure=2_000_000,
            positions=2,
        )

        after = self.risk_totals(
            exposure=1_500_000,
            positions=1,
        )

        (
            result,
            _,
            _,
            continuity,
        ) = await self.resolve(
            risk_results=[
                before,
                after,
            ],
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_POSITION_RISK_CHANGED_DURING_VALUATION",
            result.reasons,
        )

        continuity.assert_not_called()

    async def test_valuation_position_count_must_match_risk_totals(
        self,
    ):
        risk = self.risk_totals(
            positions=2
        )

        (
            result,
            _,
            _,
            continuity,
        ) = await self.resolve(
            risk_results=[
                risk,
                risk,
            ],
            valuation=self.valuation(
                open_lots=1
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "VALUATION_POSITION_COUNT_MISMATCH",
            result.reasons,
        )

        continuity.assert_not_called()

    async def test_continuity_unknown_blocks_account_state(
        self,
    ):
        risk = self.risk_totals()

        continuity = (
            self.continuity_result(
                status="UNKNOWN",
                state=None,
                reasons=(
                    "STALE_WALLET_BALANCE_RPC_SLOT",
                ),
            )
        )

        result = (
            await self.resolve(
                risk_results=[
                    risk,
                    risk,
                ],
                continuity=continuity,
            )
        )[0]

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_EQUITY_CONTINUITY_NOT_PASS",
            result.reasons,
        )

        self.assertIsNone(
            result.account
        )

    async def test_continuity_binding_is_exact(
        self,
    ):
        risk = self.risk_totals()

        bad_state = (
            self.continuity_state(
                current=9_999_999
            )
        )

        result = (
            await self.resolve(
                risk_results=[
                    risk,
                    risk,
                ],
                continuity=(
                    self.continuity_result(
                        state=bad_state
                    )
                ),
            )
        )[0]

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_EQUITY_CONTINUITY_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertIsNone(
            result.account
        )

    async def test_resolved_account_state_binds_all_five_live_fields(
        self,
    ):
        risk = self.risk_totals(
            exposure=2_500_000,
            positions=3,
        )

        valuation = self.valuation(
            equity=10_000_000,
            slot=700,
            open_lots=3,
        )

        continuity_state = (
            self.continuity_state(
                current=10_000_000,
                day_start=11_000_000,
                high_water=12_000_000,
                slot=700,
            )
        )

        (
            result,
            risk_mock,
            valuation_mock,
            continuity_mock,
        ) = await self.resolve(
            risk_results=[
                risk,
                risk,
            ],
            valuation=valuation,
            continuity=(
                self.continuity_result(
                    state=continuity_state
                )
            ),
            min_context_slot=650,
        )

        self.assertEqual(
            result.resolver_version,
            LIVE_ACCOUNT_RISK_STATE_VERSION,
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        account = result.account

        self.assertIsNotNone(
            account
        )

        self.assertEqual(
            account.current_equity_lamports,
            10_000_000,
        )

        self.assertEqual(
            account.day_start_equity_lamports,
            11_000_000,
        )

        self.assertEqual(
            account.high_water_equity_lamports,
            12_000_000,
        )

        self.assertEqual(
            account.open_exposure_lamports,
            2_500_000,
        )

        self.assertEqual(
            account.open_positions,
            3,
        )

        self.assertEqual(
            risk_mock.call_count,
            2,
        )

        valuation_mock.assert_awaited_once_with(
            wallet_pubkey=self.wallet,
            slippage_bps=(
                self.slippage_bps
            ),
            base_network_fee_lamports=(
                self.base_fee
            ),
            priority_fee_lamports=(
                self.priority_fee
            ),
            min_context_slot=650,
            db_path=DB_PATH,
        )

        continuity_mock.assert_called_once_with(
            wallet_pubkey=self.wallet,
            valuation_version=(
                LIVE_WALLET_VALUATION_VERSION
            ),
            current_equity_lamports=(
                10_000_000
            ),
            wallet_balance_rpc_slot=700,
            db_path=DB_PATH,
        )

    async def test_zero_open_exposure_and_positions_are_valid(
        self,
    ):
        risk = self.risk_totals(
            exposure=0,
            positions=0,
        )

        valuation = self.valuation(
            open_lots=0,
        )

        result = (
            await self.resolve(
                risk_results=[
                    risk,
                    risk,
                ],
                valuation=valuation,
            )
        )[0]

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            result.account.open_exposure_lamports,
            0,
        )

        self.assertEqual(
            result.account.open_positions,
            0,
        )


    async def test_custom_database_path_reaches_entire_risk_bracket(
        self,
    ):
        custom_path = (
            DB_PATH.parent
            / "account-risk-custom.db"
        )

        (
            result,
            risk,
            valuation,
            continuity,
        ) = await self.resolve(
            db_path=custom_path,
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            risk.call_count,
            2,
        )

        self.assertEqual(
            [
                item.kwargs.get("db_path")
                for item
                in risk.call_args_list
            ],
            [
                custom_path,
                custom_path,
            ],
        )

        self.assertEqual(
            valuation.await_args.kwargs.get(
                "db_path"
            ),
            custom_path,
        )

        self.assertEqual(
            continuity.call_args.kwargs.get(
                "db_path"
            ),
            custom_path,
        )



if __name__ == "__main__":
    unittest.main()
