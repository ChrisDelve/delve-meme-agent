import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from solders.pubkey import Pubkey

from src.execution.exit_execution import (
    EXIT_EXECUTION_CONTRACT_VERSION,
    PUMP_BONDING_CURVE_VENUE,
)
from src.execution.live_pump_fee_state import (
    LIVE_PUMP_FEE_STATE_VERSION,
)
from src.execution.live_pump_liquidation_value import (
    LIVE_PUMP_LIQUIDATION_VALUE_VERSION,
    RESOLVED,
    UNKNOWN,
    U64_MAX,
    resolve_live_pump_inventory_liquidation_value,
)
from src.execution.pump_sell_simulator import (
    PumpSellSimulation,
    find_max_liquidity_feasible_sell,
)
from src.safety.token_safety_gate import (
    SOL_QUOTE_MINT,
)


MODULE = (
    "src.execution."
    "live_pump_liquidation_value"
)


class LivePumpLiquidationValueTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.mint = str(
            Pubkey.new_unique()
        )

        self.tokens = 1_000_000

        self.slippage_bps = 300
        self.base_fee = 5_000
        self.priority_fee = 0

    def fee_state(
        self,
        *,
        mint=None,
        resolver_version=(
            LIVE_PUMP_FEE_STATE_VERSION
        ),
        rpc_slot=500,
        fetched_at=100.0,
        protocol_fee_bps=95,
        creator_fee_bps=30,
        complete=False,
        quote_mint=SOL_QUOTE_MINT,
    ):
        if mint is None:
            mint = self.mint

        return SimpleNamespace(
            resolver_version=(
                resolver_version
            ),
            mint=mint,
            rpc_slot=rpc_slot,
            fetched_at=fetched_at,
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            curve=SimpleNamespace(
                complete=complete,
                quote_mint=quote_mint,
                virtual_quote_reserves=(
                    30_000_000_000
                ),
                virtual_token_reserves=(
                    1_000_000_000_000
                ),
                real_quote_reserves=(
                    10_000_000_000
                ),
                real_token_reserves=(
                    500_000_000_000
                ),
            ),
        )

    def exit_execution(
        self,
        *,
        tokens_in=None,
        executable=True,
        proceeds=100_000,
        reason=None,
        protocol_fee_bps=95,
        creator_fee_bps=30,
        slippage_bps=None,
        base_fee=None,
        priority_fee=None,
    ):
        if tokens_in is None:
            tokens_in = self.tokens

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

        return SimpleNamespace(
            contract_version=(
                EXIT_EXECUTION_CONTRACT_VERSION
            ),
            venue=PUMP_BONDING_CURVE_VENUE,
            tokens_in=tokens_in,
            protocol_fee_bps=(
                protocol_fee_bps
            ),
            creator_fee_bps=(
                creator_fee_bps
            ),
            slippage_bps=(
                slippage_bps
            ),
            base_network_fee_lamports=(
                base_fee
            ),
            priority_fee_lamports=(
                priority_fee
            ),
            net_wallet_proceeds_lamports=(
                proceeds
            ),
            executable=executable,
            ineligible_reason=reason,
        )

    async def resolve(
        self,
        *,
        mint=None,
        tokens=None,
        slippage_bps=None,
        base_fee=None,
        priority_fee=None,
        min_context_slot=None,
        fee_state=None,
        fee_error=None,
        helper_result=None,
        execution=None,
    ):
        if mint is None:
            mint = self.mint

        if tokens is None:
            tokens = self.tokens

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

        if fee_state is None:
            fee_state = self.fee_state()

        fee_mock = AsyncMock()

        if fee_error is not None:
            fee_mock.side_effect = (
                fee_error
            )
        else:
            fee_mock.return_value = (
                fee_state
            )

        if helper_result is None:
            helper_result = (
                tokens,
                object(),
            )

        helper_mock = MagicMock(
            return_value=helper_result
        )

        if execution is None:
            execution = (
                self.exit_execution(
                    tokens_in=(
                        helper_result[0]
                    ),
                )
            )

        normalize_mock = MagicMock(
            return_value=execution
        )

        with (
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                fee_mock,
            ),
            patch(
                f"{MODULE}."
                "find_max_liquidity_feasible_sell",
                helper_mock,
            ),
            patch(
                f"{MODULE}."
                "normalize_pump_sell_execution",
                normalize_mock,
            ),
        ):
            result = await (
                resolve_live_pump_inventory_liquidation_value(
                    mint=mint,
                    tokens_held=tokens,
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
                )
            )

        return (
            result,
            fee_mock,
            helper_mock,
            normalize_mock,
        )

    async def test_invalid_mint_never_resolves_chain_state(
        self,
    ):
        (
            result,
            fee_mock,
            helper_mock,
            normalize_mock,
        ) = await self.resolve(
            mint="not-a-mint",
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "INVALID_MINT",
            result.reasons,
        )

        fee_mock.assert_not_awaited()
        helper_mock.assert_not_called()
        normalize_mock.assert_not_called()

    async def test_invalid_inventory_never_resolves_chain_state(
        self,
    ):
        for invalid in (
            0,
            -1,
            True,
            U64_MAX + 1,
        ):
            with self.subTest(
                invalid=invalid
            ):
                (
                    result,
                    fee_mock,
                    helper_mock,
                    _,
                ) = await self.resolve(
                    tokens=invalid,
                )

                self.assertEqual(
                    result.status,
                    UNKNOWN,
                )

                self.assertIn(
                    "INVALID_TOKEN_INVENTORY",
                    result.reasons,
                )

                fee_mock.assert_not_awaited()
                helper_mock.assert_not_called()

    async def test_invalid_execution_assumptions_fail_before_rpc(
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
                    fee_mock,
                    helper_mock,
                    _,
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

                fee_mock.assert_not_awaited()
                helper_mock.assert_not_called()

    async def test_fee_state_failure_is_unknown(
        self,
    ):
        result, _, helper, _ = (
            await self.resolve(
                fee_error=RuntimeError(
                    "rpc failed"
                ),
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_FEE_STATE_FAILED",
            result.reasons,
        )

        helper.assert_not_called()

    async def test_fee_state_identity_is_bound(
        self,
    ):
        wrong_version = (
            await self.resolve(
                fee_state=self.fee_state(
                    resolver_version=(
                        "wrong-version"
                    ),
                ),
            )
        )[0]

        self.assertEqual(
            wrong_version.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_FEE_STATE_VERSION_MISMATCH",
            wrong_version.reasons,
        )

        wrong_mint = str(
            Pubkey.new_unique()
        )

        mismatch = (
            await self.resolve(
                fee_state=self.fee_state(
                    mint=wrong_mint
                ),
            )
        )[0]

        self.assertEqual(
            mismatch.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_FEE_STATE_MINT_MISMATCH",
            mismatch.reasons,
        )

    async def test_min_context_slot_is_forwarded_and_enforced(
        self,
    ):
        (
            result,
            fee_mock,
            _,
            _,
        ) = await self.resolve(
            min_context_slot=400,
            fee_state=self.fee_state(
                rpc_slot=500
            ),
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        fee_mock.assert_awaited_once_with(
            mint=self.mint,
            min_context_slot=400,
        )

        stale = (
            await self.resolve(
                min_context_slot=501,
                fee_state=self.fee_state(
                    rpc_slot=500
                ),
            )
        )[0]

        self.assertEqual(
            stale.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_FEE_STATE_SLOT_BELOW_MINIMUM",
            stale.reasons,
        )

    async def test_graduated_curve_is_unknown_not_zero(
        self,
    ):
        (
            result,
            _,
            helper,
            normalize,
        ) = await self.resolve(
            fee_state=self.fee_state(
                complete=True
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "GRADUATED_CURVE_EXIT_ROUTE_UNSUPPORTED",
            result.reasons,
        )

        self.assertIsNone(
            result.liquidation_value_lamports
        )

        helper.assert_not_called()
        normalize.assert_not_called()


    async def test_non_sol_quote_curve_is_unknown(
        self,
    ):
        non_sol_quote = str(
            Pubkey.new_unique()
        )

        (
            result,
            _,
            helper,
            normalize,
        ) = await self.resolve(
            fee_state=self.fee_state(
                quote_mint=(
                    non_sol_quote
                ),
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "NON_SOL_QUOTE_MINT_UNSUPPORTED",
            result.reasons,
        )

        self.assertEqual(
            result.quote_mint,
            non_sol_quote,
        )

        self.assertIsNone(
            result.liquidation_value_lamports
        )

        helper.assert_not_called()
        normalize.assert_not_called()

    async def test_full_inventory_liquidation_uses_net_wallet_proceeds(
        self,
    ):
        execution = (
            self.exit_execution(
                tokens_in=self.tokens,
                proceeds=777_000,
            )
        )

        (
            result,
            _,
            helper,
            normalize,
        ) = await self.resolve(
            helper_result=(
                self.tokens,
                object(),
            ),
            execution=execution,
        )

        self.assertEqual(
            result.resolver_version,
            LIVE_PUMP_LIQUIDATION_VALUE_VERSION,
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            result.liquidatable_tokens,
            self.tokens,
        )

        self.assertEqual(
            result.unliquidatable_tokens,
            0,
        )

        self.assertEqual(
            result.liquidation_value_lamports,
            777_000,
        )

        self.assertEqual(
            result.quote_mint,
            SOL_QUOTE_MINT,
        )

        self.assertIs(
            result.exit_execution,
            execution,
        )

        helper.assert_called_once()
        normalize.assert_called_once()

    async def test_partial_liquidity_values_only_feasible_inventory(
        self,
    ):
        liquidatable = 600_000

        execution = (
            self.exit_execution(
                tokens_in=liquidatable,
                proceeds=321_000,
            )
        )

        result = (
            await self.resolve(
                helper_result=(
                    liquidatable,
                    object(),
                ),
                execution=execution,
            )
        )[0]

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            result.liquidatable_tokens,
            liquidatable,
        )

        self.assertEqual(
            result.unliquidatable_tokens,
            400_000,
        )

        self.assertEqual(
            result.liquidation_value_lamports,
            321_000,
        )

    async def test_no_liquidity_feasible_tokens_is_known_zero(
        self,
    ):
        (
            result,
            _,
            _,
            normalize,
        ) = await self.resolve(
            helper_result=(
                0,
                None,
            ),
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertIn(
            "NO_LIQUIDITY_FEASIBLE_TOKENS",
            result.reasons,
        )

        self.assertEqual(
            result.liquidatable_tokens,
            0,
        )

        self.assertEqual(
            result.unliquidatable_tokens,
            self.tokens,
        )

        self.assertEqual(
            result.liquidation_value_lamports,
            0,
        )

        normalize.assert_not_called()

    async def test_economically_unexecutable_is_known_zero(
        self,
    ):
        execution = (
            self.exit_execution(
                executable=False,
                proceeds=0,
                reason=(
                    "FEES_EXHAUST_QUOTE_OUTPUT"
                ),
            )
        )

        result = (
            await self.resolve(
                execution=execution,
            )
        )[0]

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertIn(
            "ECONOMICALLY_UNEXITABLE:"
            "FEES_EXHAUST_QUOTE_OUTPUT",
            result.reasons,
        )

        self.assertEqual(
            result.liquidation_value_lamports,
            0,
        )

        self.assertIs(
            result.exit_execution,
            execution,
        )

    async def test_exit_execution_binding_mismatch_is_unknown(
        self,
    ):
        execution = (
            self.exit_execution(
                tokens_in=(
                    self.tokens - 1
                ),
            )
        )

        result = (
            await self.resolve(
                execution=execution,
            )
        )[0]

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "EXIT_EXECUTION_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertIsNone(
            result.liquidation_value_lamports
        )

    async def test_helper_receives_aggregate_inventory_and_exact_economics(
        self,
    ):
        (
            result,
            _,
            helper,
            _,
        ) = await self.resolve()

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        kwargs = (
            helper.call_args.kwargs
        )

        self.assertEqual(
            kwargs[
                "maximum_tokens_to_sell"
            ],
            self.tokens,
        )

        self.assertEqual(
            kwargs[
                "protocol_fee_bps"
            ],
            95,
        )

        self.assertEqual(
            kwargs[
                "creator_fee_bps"
            ],
            30,
        )

        self.assertEqual(
            kwargs[
                "slippage_bps"
            ],
            self.slippage_bps,
        )

        self.assertEqual(
            kwargs[
                "base_network_fee_lamports"
            ],
            self.base_fee,
        )

        self.assertEqual(
            kwargs[
                "priority_fee_lamports"
            ],
            self.priority_fee,
        )

    def test_real_sell_economics_exposes_required_valuation_contract(
        self,
    ):
        fields = set(
            PumpSellSimulation
            .__dataclass_fields__
        )

        required = {
            "tokens_in",
            "protocol_fee_bps",
            "creator_fee_bps",
            "net_wallet_proceeds_lamports",
            "executable",
            "ineligible_reason",
        }

        self.assertTrue(
            required.issubset(
                fields
            )
        )

        signature = inspect.signature(
            find_max_liquidity_feasible_sell
        )

        for required_parameter in (
            "state",
            "maximum_tokens_to_sell",
            "protocol_fee_bps",
            "creator_fee_bps",
            "slippage_bps",
            "base_network_fee_lamports",
            "priority_fee_lamports",
        ):
            self.assertIn(
                required_parameter,
                signature.parameters,
            )


if __name__ == "__main__":
    unittest.main()
