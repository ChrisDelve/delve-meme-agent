import unittest
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from solders.pubkey import Pubkey

from src.execution.live_pump_liquidation_value import (
    LIVE_PUMP_LIQUIDATION_VALUE_VERSION,
)
from src.execution.live_wallet_balance import (
    LIVE_WALLET_BALANCE_VERSION,
)
from src.portfolio.live_positions import (
    LIVE_POSITION_VERSION,
    OPEN,
    OPEN_LIVE_POSITIONS_VERSION,
)
from src.portfolio.live_wallet_valuation import (
    LIVE_WALLET_VALUATION_VERSION,
    RESOLVED,
    UNKNOWN,
    I64_MAX,
    U64_MAX,
    resolve_live_wallet_valuation,
)


MODULE = (
    "src.portfolio.live_wallet_valuation"
)


class LiveWalletValuationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.wallet = str(
            Pubkey.new_unique()
        )

        self.mint_a = str(
            Pubkey.new_unique()
        )

        self.mint_b = str(
            Pubkey.new_unique()
        )

        self.slippage_bps = 300
        self.base_fee = 5_000
        self.priority_fee = 0

    def position(
        self,
        *,
        position_id,
        mint,
        tokens,
        reservation_id=None,

        entry_slot=100,
        entry_block_time=1_000,

        entry_wallet_cost_lamports=(
            1_000_000
        ),
        remaining_exposure_lamports=(
            1_000_000
        ),
        remaining_cost_basis_lamports=(
            900_000
        ),

        cumulative_net_proceeds_lamports=0,
        cumulative_realized_pnl_lamports=0,

        updated_at=100.0,
    ):
        if reservation_id is None:
            reservation_id = (
                f"reservation-{position_id}"
            )

        return SimpleNamespace(
            position_id=position_id,
            position_version=(
                LIVE_POSITION_VERSION
            ),
            reservation_id=(
                reservation_id
            ),
            wallet_pubkey=self.wallet,
            mint=mint,
            status=OPEN,

            entry_slot=entry_slot,
            entry_block_time=(
                entry_block_time
            ),

            tokens_held=tokens,

            entry_wallet_cost_lamports=(
                entry_wallet_cost_lamports
            ),
            remaining_exposure_lamports=(
                remaining_exposure_lamports
            ),
            remaining_cost_basis_lamports=(
                remaining_cost_basis_lamports
            ),

            cumulative_net_proceeds_lamports=(
                cumulative_net_proceeds_lamports
            ),
            cumulative_realized_pnl_lamports=(
                cumulative_realized_pnl_lamports
            ),

            updated_at=updated_at,
        )

    def positions_result(
        self,
        positions,
        *,
        status="PASS",
        version=(
            OPEN_LIVE_POSITIONS_VERSION
        ),
        reasons=(),
    ):
        return SimpleNamespace(
            loader_version=version,
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=self.wallet,
            positions=tuple(
                positions
            ),
        )

    def balance_result(
        self,
        *,
        status="RESOLVED",
        version=(
            LIVE_WALLET_BALANCE_VERSION
        ),
        wallet=None,
        balance=5_000_000,
        slot=500,
        reasons=(),
    ):
        if wallet is None:
            wallet = self.wallet

        return SimpleNamespace(
            resolver_version=version,
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=wallet,
            balance_lamports=balance,
            rpc_slot=slot,
        )

    def liquidation_result(
        self,
        *,
        mint,
        tokens,
        value,
        status="RESOLVED",
        version=(
            LIVE_PUMP_LIQUIDATION_VALUE_VERSION
        ),
        reasons=(),
    ):
        return SimpleNamespace(
            resolver_version=version,
            status=status,
            reasons=tuple(reasons),
            mint=mint,
            tokens_held=tokens,
            liquidation_value_lamports=(
                value
            ),
        )

    async def resolve(
        self,
        *,
        wallet=None,
        slippage_bps=None,
        base_fee=None,
        priority_fee=None,
        min_context_slot=None,
        position_results=None,
        balance_result=None,
        liquidation_side_effect=None,
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

        if position_results is None:
            empty = self.positions_result(
                ()
            )

            position_results = [
                empty,
                empty,
            ]

        positions_mock = MagicMock(
            side_effect=(
                position_results
            )
        )

        if balance_result is None:
            balance_result = (
                self.balance_result()
            )

        balance_mock = AsyncMock(
            return_value=(
                balance_result
            )
        )

        liquidation_mock = AsyncMock()

        if liquidation_side_effect is None:
            liquidation_mock.return_value = (
                self.liquidation_result(
                    mint=self.mint_a,
                    tokens=1,
                    value=1,
                )
            )

        elif callable(
            liquidation_side_effect
        ):
            liquidation_mock.side_effect = (
                liquidation_side_effect
            )

        else:
            liquidation_mock.side_effect = (
                liquidation_side_effect
            )

        with (
            patch(
                f"{MODULE}."
                "load_open_live_positions_read_only",
                positions_mock,
            ),
            patch(
                f"{MODULE}."
                "resolve_live_wallet_balance",
                balance_mock,
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_inventory_liquidation_value",
                liquidation_mock,
            ),
        ):
            result = await (
                resolve_live_wallet_valuation(
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
                )
            )

        return (
            result,
            positions_mock,
            balance_mock,
            liquidation_mock,
        )

    async def test_invalid_wallet_fails_before_any_authority(
        self,
    ):
        (
            result,
            positions,
            balance,
            liquidation,
        ) = await self.resolve(
            wallet="not-a-wallet",
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "INVALID_WALLET_PUBKEY",
            result.reasons,
        )

        positions.assert_not_called()
        balance.assert_not_awaited()
        liquidation.assert_not_awaited()

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
                    positions,
                    balance,
                    liquidation,
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

                positions.assert_not_called()
                balance.assert_not_awaited()
                liquidation.assert_not_awaited()

    async def test_open_position_unknown_blocks_before_rpc(
        self,
    ):
        unknown = self.positions_result(
            (),
            status="UNKNOWN",
            reasons=(
                "LIVE_DATABASE_NOT_FOUND",
            ),
        )

        (
            result,
            _,
            balance,
            liquidation,
        ) = await self.resolve(
            position_results=[
                unknown,
            ],
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "OPEN_POSITION_LOAD_UNKNOWN:"
            "LIVE_DATABASE_NOT_FOUND",
            result.reasons,
        )

        balance.assert_not_awaited()
        liquidation.assert_not_awaited()

    async def test_empty_book_equity_is_native_sol(
        self,
    ):
        empty = self.positions_result(
            ()
        )

        balance = self.balance_result(
            balance=7_000_000,
            slot=600,
        )

        (
            result,
            positions,
            balance_mock,
            liquidation,
        ) = await self.resolve(
            position_results=[
                empty,
                empty,
            ],
            balance_result=balance,
        )

        self.assertEqual(
            result.resolver_version,
            LIVE_WALLET_VALUATION_VERSION,
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            result.wallet_balance_lamports,
            7_000_000,
        )

        self.assertEqual(
            result.total_liquidation_value_lamports,
            0,
        )

        self.assertEqual(
            result.current_equity_lamports,
            7_000_000,
        )

        self.assertEqual(
            result.open_position_lots,
            0,
        )

        self.assertEqual(
            result.unique_open_mints,
            0,
        )

        self.assertEqual(
            result.mint_valuations,
            (),
        )

        self.assertEqual(
            positions.call_count,
            2,
        )

        balance_mock.assert_awaited_once()
        liquidation.assert_not_awaited()

    async def test_same_mint_lots_are_valued_once_as_aggregate(
        self,
    ):
        positions = (
            self.position(
                position_id=1,
                mint=self.mint_a,
                tokens=400_000,
            ),
            self.position(
                position_id=2,
                mint=self.mint_a,
                tokens=600_000,
            ),
        )

        snapshot = self.positions_result(
            positions
        )

        async def liquidation(
            **kwargs,
        ):
            return self.liquidation_result(
                mint=kwargs["mint"],
                tokens=kwargs[
                    "tokens_held"
                ],
                value=333_000,
            )

        (
            result,
            _,
            _,
            liquidation_mock,
        ) = await self.resolve(
            position_results=[
                snapshot,
                snapshot,
            ],
            liquidation_side_effect=(
                liquidation
            ),
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        liquidation_mock.assert_awaited_once()

        kwargs = (
            liquidation_mock
            .await_args
            .kwargs
        )

        self.assertEqual(
            kwargs["mint"],
            self.mint_a,
        )

        self.assertEqual(
            kwargs["tokens_held"],
            1_000_000,
        )

        valuation = (
            result.mint_valuations[0]
        )

        self.assertEqual(
            valuation.open_lots,
            2,
        )

        self.assertEqual(
            valuation.tokens_held,
            1_000_000,
        )

    async def test_same_mint_lots_aggregate_accounting_and_entry_bounds(
        self,
    ):
        positions = (
            self.position(
                position_id=1,
                mint=self.mint_a,
                tokens=400_000,
                entry_slot=100,
                entry_block_time=1_000,
                entry_wallet_cost_lamports=(
                    700_000
                ),
                remaining_cost_basis_lamports=(
                    300_000
                ),
                cumulative_net_proceeds_lamports=(
                    500_000
                ),
                cumulative_realized_pnl_lamports=(
                    100_000
                ),
            ),
            self.position(
                position_id=2,
                mint=self.mint_a,
                tokens=600_000,
                entry_slot=250,
                entry_block_time=1_200,
                entry_wallet_cost_lamports=(
                    900_000
                ),
                remaining_cost_basis_lamports=(
                    700_000
                ),
                cumulative_net_proceeds_lamports=(
                    150_000
                ),
                cumulative_realized_pnl_lamports=(
                    -50_000
                ),
            ),
        )

        snapshot = self.positions_result(
            positions
        )

        async def liquidation(
            **kwargs,
        ):
            return self.liquidation_result(
                mint=kwargs["mint"],
                tokens=kwargs[
                    "tokens_held"
                ],
                value=333_000,
            )

        result = (
            await self.resolve(
                position_results=[
                    snapshot,
                    snapshot,
                ],
                liquidation_side_effect=(
                    liquidation
                ),
            )
        )[0]

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            len(result.mint_valuations),
            1,
        )

        valuation = (
            result.mint_valuations[0]
        )

        self.assertEqual(
            valuation.open_lots,
            2,
        )

        self.assertEqual(
            valuation.tokens_held,
            1_000_000,
        )

        self.assertEqual(
            valuation.total_entry_wallet_cost_lamports,
            1_600_000,
        )

        self.assertEqual(
            valuation.remaining_cost_basis_lamports,
            1_000_000,
        )

        self.assertEqual(
            valuation.cumulative_net_proceeds_lamports,
            650_000,
        )

        self.assertEqual(
            valuation.cumulative_realized_pnl_lamports,
            50_000,
        )

        self.assertEqual(
            valuation.oldest_entry_slot,
            100,
        )

        self.assertEqual(
            valuation.newest_entry_slot,
            250,
        )

        self.assertEqual(
            valuation.oldest_entry_block_time,
            1_000,
        )

        self.assertEqual(
            valuation.newest_entry_block_time,
            1_200,
        )

        self.assertEqual(
            valuation.liquidation_value_lamports,
            333_000,
        )

    async def test_missing_lot_block_time_makes_mint_time_bounds_unknown(
        self,
    ):
        positions = (
            self.position(
                position_id=1,
                mint=self.mint_a,
                tokens=400_000,
                entry_slot=100,
                entry_block_time=None,
            ),
            self.position(
                position_id=2,
                mint=self.mint_a,
                tokens=600_000,
                entry_slot=250,
                entry_block_time=1_200,
            ),
        )

        snapshot = self.positions_result(
            positions
        )

        async def liquidation(
            **kwargs,
        ):
            return self.liquidation_result(
                mint=kwargs["mint"],
                tokens=kwargs[
                    "tokens_held"
                ],
                value=333_000,
            )

        result = (
            await self.resolve(
                position_results=[
                    snapshot,
                    snapshot,
                ],
                liquidation_side_effect=(
                    liquidation
                ),
            )
        )[0]

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        valuation = (
            result.mint_valuations[0]
        )

        self.assertEqual(
            valuation.oldest_entry_slot,
            100,
        )

        self.assertEqual(
            valuation.newest_entry_slot,
            250,
        )

        self.assertIsNone(
            valuation.oldest_entry_block_time
        )

        self.assertIsNone(
            valuation.newest_entry_block_time
        )

    async def test_accounting_change_during_valuation_is_unknown(
        self,
    ):
        first_position = self.position(
            position_id=1,
            mint=self.mint_a,
            tokens=100,
            cumulative_net_proceeds_lamports=0,
            cumulative_realized_pnl_lamports=0,
            updated_at=100.0,
        )

        changed_position = self.position(
            position_id=1,
            mint=self.mint_a,
            tokens=100,
            cumulative_net_proceeds_lamports=(
                25_000
            ),
            cumulative_realized_pnl_lamports=(
                25_000
            ),
            # Deliberately unchanged.
            #
            # This proves the strengthened
            # fingerprint observes accounting
            # state directly rather than relying
            # only on updated_at.
            updated_at=100.0,
        )

        first = self.positions_result(
            (
                first_position,
            )
        )

        changed = self.positions_result(
            (
                changed_position,
            )
        )

        async def liquidation(
            **kwargs,
        ):
            return self.liquidation_result(
                mint=kwargs["mint"],
                tokens=kwargs[
                    "tokens_held"
                ],
                value=100,
            )

        result = (
            await self.resolve(
                position_results=[
                    first,
                    changed,
                ],
                liquidation_side_effect=(
                    liquidation
                ),
            )
        )[0]

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "OPEN_POSITION_BOOK_CHANGED_DURING_VALUATION",
            result.reasons,
        )

        self.assertIsNone(
            result.current_equity_lamports
        )

    async def test_distinct_mints_are_valued_in_deterministic_order(
        self,
    ):
        positions = (
            self.position(
                position_id=1,
                mint=self.mint_b,
                tokens=200,
            ),
            self.position(
                position_id=2,
                mint=self.mint_a,
                tokens=100,
            ),
        )

        snapshot = self.positions_result(
            positions
        )

        async def liquidation(
            **kwargs,
        ):
            return self.liquidation_result(
                mint=kwargs["mint"],
                tokens=kwargs[
                    "tokens_held"
                ],
                value=100,
            )

        (
            result,
            _,
            _,
            liquidation_mock,
        ) = await self.resolve(
            position_results=[
                snapshot,
                snapshot,
            ],
            liquidation_side_effect=(
                liquidation
            ),
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        called_mints = [
            call.kwargs["mint"]
            for call
            in liquidation_mock.await_args_list
        ]

        self.assertEqual(
            called_mints,
            sorted(
                (
                    self.mint_a,
                    self.mint_b,
                )
            ),
        )

        self.assertEqual(
            result.unique_open_mints,
            2,
        )

    async def test_wallet_balance_unknown_blocks_valuation(
        self,
    ):
        position = self.position(
            position_id=1,
            mint=self.mint_a,
            tokens=100,
        )

        snapshot = self.positions_result(
            (
                position,
            )
        )

        balance = self.balance_result(
            status="UNKNOWN",
            reasons=(
                "BALANCE_RPC_FAILED",
            ),
        )

        (
            result,
            _,
            _,
            liquidation,
        ) = await self.resolve(
            position_results=[
                snapshot,
            ],
            balance_result=balance,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "WALLET_BALANCE_UNKNOWN:"
            "BALANCE_RPC_FAILED",
            result.reasons,
        )

        liquidation.assert_not_awaited()

    async def test_wallet_balance_identity_is_bound(
        self,
    ):
        empty = self.positions_result(
            ()
        )

        wrong_wallet = str(
            Pubkey.new_unique()
        )

        result = (
            await self.resolve(
                position_results=[
                    empty,
                ],
                balance_result=(
                    self.balance_result(
                        wallet=wrong_wallet
                    )
                ),
            )
        )[0]

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "WALLET_BALANCE_WALLET_MISMATCH",
            result.reasons,
        )

    async def test_liquidation_unknown_makes_whole_wallet_unknown(
        self,
    ):
        position = self.position(
            position_id=1,
            mint=self.mint_a,
            tokens=100,
        )

        snapshot = self.positions_result(
            (
                position,
            )
        )

        async def liquidation(
            **kwargs,
        ):
            return self.liquidation_result(
                mint=kwargs["mint"],
                tokens=kwargs[
                    "tokens_held"
                ],
                value=None,
                status="UNKNOWN",
                reasons=(
                    "LIVE_FEE_STATE_FAILED",
                ),
            )

        result = (
            await self.resolve(
                position_results=[
                    snapshot,
                ],
                liquidation_side_effect=(
                    liquidation
                ),
            )
        )[0]

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "MINT_LIQUIDATION_UNKNOWN:"
            + self.mint_a
            + ":LIVE_FEE_STATE_FAILED",
            result.reasons,
        )

        self.assertIsNone(
            result.current_equity_lamports
        )

    async def test_known_zero_liquidation_is_valid_equity_component(
        self,
    ):
        position = self.position(
            position_id=1,
            mint=self.mint_a,
            tokens=100,
        )

        snapshot = self.positions_result(
            (
                position,
            )
        )

        async def liquidation(
            **kwargs,
        ):
            return self.liquidation_result(
                mint=kwargs["mint"],
                tokens=kwargs[
                    "tokens_held"
                ],
                value=0,
                reasons=(
                    "NO_LIQUIDITY_FEASIBLE_TOKENS",
                ),
            )

        result = (
            await self.resolve(
                position_results=[
                    snapshot,
                    snapshot,
                ],
                balance_result=(
                    self.balance_result(
                        balance=9_000
                    )
                ),
                liquidation_side_effect=(
                    liquidation
                ),
            )
        )[0]

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            result.total_liquidation_value_lamports,
            0,
        )

        self.assertEqual(
            result.current_equity_lamports,
            9_000,
        )

    async def test_equity_is_balance_plus_all_mint_liquidation_values(
        self,
    ):
        positions = (
            self.position(
                position_id=1,
                mint=self.mint_a,
                tokens=100,
            ),
            self.position(
                position_id=2,
                mint=self.mint_b,
                tokens=200,
            ),
        )

        snapshot = self.positions_result(
            positions
        )

        values = {
            self.mint_a: 2_000,
            self.mint_b: 3_000,
        }

        async def liquidation(
            **kwargs,
        ):
            return self.liquidation_result(
                mint=kwargs["mint"],
                tokens=kwargs[
                    "tokens_held"
                ],
                value=values[
                    kwargs["mint"]
                ],
            )

        result = (
            await self.resolve(
                position_results=[
                    snapshot,
                    snapshot,
                ],
                balance_result=(
                    self.balance_result(
                        balance=10_000
                    )
                ),
                liquidation_side_effect=(
                    liquidation
                ),
            )
        )[0]

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            result.total_liquidation_value_lamports,
            5_000,
        )

        self.assertEqual(
            result.current_equity_lamports,
            15_000,
        )

    async def test_wallet_balance_slot_anchors_mint_valuations(
        self,
    ):
        position = self.position(
            position_id=1,
            mint=self.mint_a,
            tokens=100,
        )

        snapshot = self.positions_result(
            (
                position,
            )
        )

        async def liquidation(
            **kwargs,
        ):
            return self.liquidation_result(
                mint=kwargs["mint"],
                tokens=kwargs[
                    "tokens_held"
                ],
                value=100,
            )

        (
            result,
            _,
            balance_mock,
            liquidation_mock,
        ) = await self.resolve(
            position_results=[
                snapshot,
                snapshot,
            ],
            balance_result=(
                self.balance_result(
                    slot=700
                )
            ),
            min_context_slot=650,
            liquidation_side_effect=(
                liquidation
            ),
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        balance_mock.assert_awaited_once_with(
            wallet_pubkey=(
                self.wallet
            ),
            min_context_slot=650,
        )

        self.assertEqual(
            liquidation_mock
            .await_args
            .kwargs[
                "min_context_slot"
            ],
            700,
        )

    async def test_position_book_change_during_valuation_is_unknown(
        self,
    ):
        first_position = self.position(
            position_id=1,
            mint=self.mint_a,
            tokens=100,
            updated_at=100.0,
        )

        changed_position = (
            self.position(
                position_id=1,
                mint=self.mint_a,
                tokens=90,
                updated_at=101.0,
            )
        )

        first = self.positions_result(
            (
                first_position,
            )
        )

        changed = self.positions_result(
            (
                changed_position,
            )
        )

        async def liquidation(
            **kwargs,
        ):
            return self.liquidation_result(
                mint=kwargs["mint"],
                tokens=kwargs[
                    "tokens_held"
                ],
                value=100,
            )

        result = (
            await self.resolve(
                position_results=[
                    first,
                    changed,
                ],
                liquidation_side_effect=(
                    liquidation
                ),
            )
        )[0]

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "OPEN_POSITION_BOOK_CHANGED_DURING_VALUATION",
            result.reasons,
        )

        self.assertIsNone(
            result.current_equity_lamports
        )

    async def test_aggregate_accounting_overflow_fails_closed(
        self,
    ):
        positions = (
            self.position(
                position_id=1,
                mint=self.mint_a,
                tokens=100,
                entry_wallet_cost_lamports=(
                    U64_MAX
                ),
            ),
            self.position(
                position_id=2,
                mint=self.mint_a,
                tokens=100,
                entry_wallet_cost_lamports=1,
            ),
        )

        snapshot = self.positions_result(
            positions
        )

        (
            result,
            _,
            balance_mock,
            liquidation_mock,
        ) = await self.resolve(
            position_results=[
                snapshot,
            ],
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "OPEN_POSITION_ACCOUNTING_OVERFLOW",
            result.reasons,
        )

        balance_mock.assert_not_awaited()
        liquidation_mock.assert_not_awaited()

    async def test_aggregate_realized_pnl_overflow_fails_closed(
        self,
    ):
        positions = (
            self.position(
                position_id=1,
                mint=self.mint_a,
                tokens=100,
                cumulative_realized_pnl_lamports=(
                    I64_MAX
                ),
            ),
            self.position(
                position_id=2,
                mint=self.mint_a,
                tokens=100,
                cumulative_realized_pnl_lamports=1,
            ),
        )

        snapshot = self.positions_result(
            positions
        )

        (
            result,
            _,
            balance_mock,
            liquidation_mock,
        ) = await self.resolve(
            position_results=[
                snapshot,
            ],
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "OPEN_POSITION_REALIZED_PNL_OVERFLOW",
            result.reasons,
        )

        balance_mock.assert_not_awaited()
        liquidation_mock.assert_not_awaited()

    async def test_aggregate_inventory_overflow_fails_closed(
        self,
    ):
        positions = (
            self.position(
                position_id=1,
                mint=self.mint_a,
                tokens=U64_MAX,
            ),
            self.position(
                position_id=2,
                mint=self.mint_a,
                tokens=1,
            ),
        )

        first = self.positions_result(
            positions
        )

        (
            result,
            _,
            balance,
            liquidation,
        ) = await self.resolve(
            position_results=[
                first,
            ],
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "OPEN_POSITION_INVENTORY_OVERFLOW",
            result.reasons,
        )

        balance.assert_not_awaited()
        liquidation.assert_not_awaited()

    async def test_equity_overflow_fails_closed(
        self,
    ):
        position = self.position(
            position_id=1,
            mint=self.mint_a,
            tokens=100,
        )

        snapshot = self.positions_result(
            (
                position,
            )
        )

        async def liquidation(
            **kwargs,
        ):
            return self.liquidation_result(
                mint=kwargs["mint"],
                tokens=kwargs[
                    "tokens_held"
                ],
                value=1,
            )

        result = (
            await self.resolve(
                position_results=[
                    snapshot,
                    snapshot,
                ],
                balance_result=(
                    self.balance_result(
                        balance=U64_MAX
                    )
                ),
                liquidation_side_effect=(
                    liquidation
                ),
            )
        )[0]

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "CURRENT_EQUITY_OVERFLOW",
            result.reasons,
        )

        self.assertIsNone(
            result.current_equity_lamports
        )


if __name__ == "__main__":
    unittest.main()
