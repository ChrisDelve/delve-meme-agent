import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from solders.pubkey import Pubkey

from src.execution.live_pump_fee_state import (
    LIVE_PUMP_FEE_STATE_VERSION,
)
from src.execution.order_authorization import (
    WRAPPED_SOL_MINT,
)
from src.execution.live_pump_sell_authorization import (
    AUTHORIZED,
    BLOCK,
    LIVE_PUMP_SELL_AUTHORIZATION_VERSION,
    UNKNOWN,
    authorize_live_pump_sell,
)
from src.portfolio.live_positions import (
    LIVE_POSITION_VERSION,
    OPEN,
    OPEN_LIVE_POSITIONS_VERSION,
    LivePosition,
)
from src.safety.token_safety_gate import (
    SOL_QUOTE_MINT,
)
from src.safety.token_safety_resolver import (
    TOKEN_2022_PROGRAM,
    derive_associated_token_account,
    derive_bonding_curve,
)


MODULE = (
    "src.execution.live_pump_sell_authorization"
)


class LivePumpSellAuthorizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.wallet = str(
            Pubkey.new_unique()
        )

        self.mint = str(
            Pubkey.new_unique()
        )

        self.other_mint = str(
            Pubkey.new_unique()
        )

        self.creator = (
            Pubkey.new_unique()
        )

        self.curve = (
            derive_bonding_curve(
                Pubkey.from_string(
                    self.mint
                )
            )
        )

        self.base_token_program = (
            TOKEN_2022_PROGRAM
        )

        self.associated_base_user = (
            derive_associated_token_account(
                owner=Pubkey.from_string(
                    self.wallet
                ),
                mint=Pubkey.from_string(
                    self.mint
                ),
                token_program=(
                    self.base_token_program
                ),
            )
        )

    def position(
        self,
        *,
        position_id,
        entry_slot,
        tokens,
        mint=None,
        exposure=None,
        cost_basis=None,
        realized=0,
    ):
        if mint is None:
            mint = self.mint

        if exposure is None:
            exposure = tokens * 2

        if cost_basis is None:
            cost_basis = tokens

        return LivePosition(
            position_id=position_id,
            position_version=(
                LIVE_POSITION_VERSION
            ),
            reservation_id=(
                f"reservation-{position_id}"
            ),
            entry_signature=(
                f"signature-{position_id}"
            ),
            wallet_pubkey=self.wallet,
            mint=mint,
            status=OPEN,
            fill_resolver_version="fill-v1",
            signed_transaction_sha256=(
                "a" * 64
            ),
            observed_transaction_sha256=(
                "b" * 64
            ),
            entry_slot=entry_slot,
            entry_block_time=100,
            base_token_program=str(
                self.base_token_program
            ),
            associated_base_user=str(
                self.associated_base_user
            ),
            quote_mint=(
                WRAPPED_SOL_MINT
            ),
            authorized_token_amount=tokens,
            trade_event_token_amount=tokens,
            token_pre_amount=0,
            token_post_amount=tokens,
            entry_tokens=tokens,
            tokens_held=tokens,
            authorized_max_sol_cost_lamports=(
                exposure
            ),
            entry_exposure_lamports=(
                exposure
            ),
            remaining_exposure_lamports=(
                exposure
            ),
            entry_wallet_cost_lamports=(
                cost_basis
            ),
            remaining_cost_basis_lamports=(
                cost_basis
            ),
            cumulative_net_proceeds_lamports=0,
            cumulative_realized_pnl_lamports=(
                realized
            ),
            network_fee_lamports=5_000,
            wallet_pre_balance_lamports=(
                100_000_000
            ),
            wallet_post_balance_lamports=(
                99_000_000
            ),
            wallet_balance_delta_lamports=(
                -1_000_000
            ),
            trade_event_sol_amount=(
                cost_basis
            ),
            protocol_fee_lamports=0,
            creator_fee_lamports=0,
            cashback_lamports=0,
            buyback_fee_lamports=0,
            quote_amount=cost_basis,
            created_at=100.0,
            updated_at=100.0,
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
            positions=tuple(positions),
        )

    def fee_state(
        self,
        *,
        complete=False,
        quote_mint=None,
        real_quote_reserves=(
            1_000_000_000_000
        ),
        mint=None,
        version=(
            LIVE_PUMP_FEE_STATE_VERSION
        ),
        rpc_slot=500,
    ):
        if mint is None:
            mint = self.mint

        curve = SimpleNamespace(
            virtual_quote_reserves=(
                1_000_000_000_000
            ),
            virtual_token_reserves=(
                1_000_000_000_000_000
            ),
            real_quote_reserves=(
                real_quote_reserves
            ),
            real_token_reserves=(
                1_000_000_000_000_000
            ),
            complete=complete,
            quote_mint=quote_mint,
            address=str(
                self.curve
            ),
            creator=str(
                self.creator
            ),
            is_mayhem_mode=False,
        )

        return SimpleNamespace(
            resolver_version=version,
            mint=mint,
            curve=curve,
            protocol_fee_bps=100,
            creator_fee_bps=50,
            rpc_slot=rpc_slot,
            fetched_at=100.0,
        )

    async def resolve(
        self,
        *,
        wallet=None,
        mint=None,
        tokens_to_sell=500_000_000,
        slippage_bps=300,
        base_fee=5_000,
        priority_fee=7_000,
        min_context_slot=450,
        position_results=None,
        fee_state=None,
        fee_error=None,
    ):
        if wallet is None:
            wallet = self.wallet

        if mint is None:
            mint = self.mint

        if position_results is None:
            positions = (
                self.position(
                    position_id=1,
                    entry_slot=100,
                    tokens=400_000_000,
                ),
                self.position(
                    position_id=2,
                    entry_slot=200,
                    tokens=600_000_000,
                    realized=-10,
                ),
            )

            snapshot = self.positions_result(
                positions
            )

            position_results = [
                snapshot,
                snapshot,
            ]

        if fee_state is None:
            fee_state = self.fee_state()

        position_mock = MagicMock(
            side_effect=position_results
        )

        fee_mock = AsyncMock()

        if fee_error is not None:
            fee_mock.side_effect = fee_error

        else:
            fee_mock.return_value = (
                fee_state
            )

        with (
            patch(
                f"{MODULE}."
                "load_open_live_positions_read_only",
                position_mock,
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                fee_mock,
            ),
        ):
            result = await (
                authorize_live_pump_sell(
                    wallet_pubkey=wallet,
                    mint=mint,
                    tokens_to_sell=(
                        tokens_to_sell
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
                    min_context_slot=(
                        min_context_slot
                    ),
                )
            )

        return (
            result,
            position_mock,
            fee_mock,
        )

    async def test_invalid_request_inputs_fail_before_live_authority(
        self,
    ):
        cases = (
            (
                {
                    "wallet":
                        "not-a-wallet",
                },
                "INVALID_WALLET_PUBKEY",
            ),
            (
                {
                    "mint":
                        "not-a-mint",
                },
                "INVALID_MINT",
            ),
            (
                {
                    "tokens_to_sell":
                        0,
                },
                "INVALID_TOKENS_TO_SELL",
            ),
            (
                {
                    "slippage_bps":
                        10_001,
                },
                "INVALID_SLIPPAGE_BPS",
            ),
            (
                {
                    "base_fee":
                        -1,
                },
                "INVALID_BASE_NETWORK_FEE",
            ),
            (
                {
                    "priority_fee":
                        -1,
                },
                "INVALID_PRIORITY_FEE",
            ),
            (
                {
                    "min_context_slot":
                        -1,
                },
                "INVALID_MIN_CONTEXT_SLOT",
            ),
        )

        for kwargs, reason in cases:
            with self.subTest(
                reason=reason
            ):
                (
                    result,
                    positions,
                    fee,
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
                fee.assert_not_awaited()

    async def test_open_position_unknown_blocks_fee_resolution(
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
            fee,
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
            "OPEN_POSITION_LOAD_NOT_PASS",
            result.reasons,
        )

        fee.assert_not_awaited()

    async def test_no_inventory_blocks_before_fee_resolution(
        self,
    ):
        snapshot = self.positions_result(
            (
                self.position(
                    position_id=99,
                    entry_slot=100,
                    tokens=1_000_000_000,
                    mint=self.other_mint,
                ),
            )
        )

        (
            result,
            _,
            fee,
        ) = await self.resolve(
            position_results=[
                snapshot,
            ],
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "NO_OPEN_POSITION_FOR_MINT",
            result.reasons,
        )

        fee.assert_not_awaited()

    async def test_sell_exceeding_inventory_blocks_before_fee_resolution(
        self,
    ):
        position = self.position(
            position_id=1,
            entry_slot=100,
            tokens=100,
        )

        snapshot = self.positions_result(
            (
                position,
            )
        )

        (
            result,
            _,
            fee,
        ) = await self.resolve(
            tokens_to_sell=101,
            position_results=[
                snapshot,
            ],
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SELL_EXCEEDS_OPEN_INVENTORY",
            result.reasons,
        )

        fee.assert_not_awaited()

    async def test_fifo_allocation_is_bound_into_authorization(
        self,
    ):
        result = (
            await self.resolve()
        )[0]

        self.assertEqual(
            result.status,
            AUTHORIZED,
        )

        allocations = (
            result.authorization
            .allocation
            .allocations
        )

        self.assertEqual(
            [
                allocation.position_id
                for allocation
                in allocations
            ],
            [
                1,
                2,
            ],
        )

        self.assertEqual(
            [
                allocation.tokens_to_sell
                for allocation
                in allocations
            ],
            [
                400_000_000,
                100_000_000,
            ],
        )

    async def test_fee_state_failure_is_unknown(
        self,
    ):
        result = (
            await self.resolve(
                fee_error=RuntimeError(
                    "rpc failed"
                )
            )
        )[0]

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_PUMP_FEE_STATE_FAILED",
            result.reasons,
        )

    async def test_fee_state_identity_is_bound(
        self,
    ):
        cases = (
            (
                self.fee_state(
                    version="wrong-version"
                ),
                "LIVE_PUMP_FEE_STATE_VERSION_MISMATCH",
            ),
            (
                self.fee_state(
                    mint=str(
                        Pubkey.new_unique()
                    )
                ),
                "LIVE_PUMP_FEE_STATE_MINT_MISMATCH",
            ),
        )

        for state, reason in cases:
            with self.subTest(
                reason=reason
            ):
                result = (
                    await self.resolve(
                        fee_state=state
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


    async def test_position_construction_identity_is_bound(
        self,
    ):
        base = self.position(
            position_id=1,
            entry_slot=100,
            tokens=1_000_000_000,
        )

        cases = (
            (
                replace(
                    base,
                    associated_base_user=str(
                        Pubkey.new_unique()
                    ),
                ),
                "POSITION_ASSOCIATED_BASE_USER_DERIVATION_MISMATCH",
            ),
            (
                replace(
                    base,
                    quote_mint=str(
                        Pubkey.new_unique()
                    ),
                ),
                "POSITION_QUOTE_MINT_UNSUPPORTED",
            ),
        )

        for corrupted, reason in cases:
            with self.subTest(
                reason=reason
            ):
                snapshot = (
                    self.positions_result(
                        (
                            corrupted,
                        )
                    )
                )

                (
                    result,
                    _,
                    fee,
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
                    reason,
                    result.reasons,
                )

                fee.assert_not_awaited()

    async def test_curve_construction_identity_is_bound(
        self,
    ):
        state = self.fee_state()

        state.curve.address = str(
            Pubkey.new_unique()
        )

        result = (
            await self.resolve(
                fee_state=state
            )
        )[0]

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_BONDING_CURVE_ADDRESS_MISMATCH",
            result.reasons,
        )

    async def test_graduated_curve_is_unknown(
        self,
    ):
        result = (
            await self.resolve(
                fee_state=(
                    self.fee_state(
                        complete=True
                    )
                )
            )
        )[0]

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "GRADUATED_CURVE_EXIT_ROUTE_UNSUPPORTED",
            result.reasons,
        )

    async def test_non_sol_quote_curve_is_unknown(
        self,
    ):
        result = (
            await self.resolve(
                fee_state=(
                    self.fee_state(
                        quote_mint=str(
                            Pubkey.new_unique()
                        )
                    )
                )
            )
        )[0]

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "NON_SOL_QUOTE_MINT_UNSUPPORTED",
            result.reasons,
        )

    async def test_min_context_slot_is_forwarded_and_enforced(
        self,
    ):
        (
            result,
            _,
            fee,
        ) = await self.resolve(
            min_context_slot=450,
        )

        self.assertEqual(
            result.status,
            AUTHORIZED,
        )

        fee.assert_awaited_once_with(
            mint=self.mint,
            min_context_slot=450,
        )

        stale = (
            await self.resolve(
                min_context_slot=600,
                fee_state=(
                    self.fee_state(
                        rpc_slot=599
                    )
                ),
            )
        )[0]

        self.assertEqual(
            stale.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_FEE_RPC_SLOT_BELOW_MINIMUM",
            stale.reasons,
        )

    async def test_exact_sell_execution_is_bound(
        self,
    ):
        result = (
            await self.resolve()
        )[0]

        self.assertEqual(
            result.status,
            AUTHORIZED,
        )

        authorization = (
            result.authorization
        )

        execution = (
            authorization
            .exit_execution
        )

        self.assertEqual(
            authorization.tokens_to_sell,
            500_000_000,
        )

        self.assertEqual(
            authorization.protocol_fee_bps,
            100,
        )

        self.assertEqual(
            authorization.creator_fee_bps,
            50,
        )

        self.assertEqual(
            authorization.slippage_bps,
            300,
        )

        self.assertEqual(
            authorization.base_network_fee_lamports,
            5_000,
        )

        self.assertEqual(
            authorization.priority_fee_lamports,
            7_000,
        )

        self.assertEqual(
            authorization.curve_quote_mint,
            SOL_QUOTE_MINT,
        )

        self.assertEqual(
            authorization
            .quote_mint_for_instruction,
            WRAPPED_SOL_MINT,
        )

        self.assertEqual(
            authorization.bonding_curve,
            str(
                self.curve
            ),
        )

        self.assertEqual(
            authorization.base_token_program,
            str(
                self.base_token_program
            ),
        )

        self.assertEqual(
            authorization.associated_base_user,
            str(
                self.associated_base_user
            ),
        )

        self.assertEqual(
            authorization.creator,
            str(
                self.creator
            ),
        )

        self.assertFalse(
            authorization.mayhem_mode
        )

        self.assertTrue(
            execution.executable
        )

        self.assertEqual(
            execution.tokens_in,
            500_000_000,
        )

        self.assertGreater(
            execution.min_quote_out,
            0,
        )

        self.assertGreaterEqual(
            execution.net_wallet_proceeds_lamports,
            0,
        )

    async def test_non_executable_requested_sell_blocks_without_resizing(
        self,
    ):
        result = (
            await self.resolve(
                fee_state=(
                    self.fee_state(
                        real_quote_reserves=0
                    )
                )
            )
        )[0]

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertTrue(
            result.reasons[0].startswith(
                "SELL_NOT_EXECUTABLE:"
            )
        )

        self.assertIsNotNone(
            result.exit_execution
        )

        self.assertEqual(
            result.exit_execution.tokens_in,
            500_000_000,
        )

        self.assertIsNone(
            result.authorization
        )

    async def test_target_allocation_change_during_authorization_is_unknown(
        self,
    ):
        first_positions = (
            self.position(
                position_id=1,
                entry_slot=100,
                tokens=400_000_000,
            ),
            self.position(
                position_id=2,
                entry_slot=200,
                tokens=600_000_000,
            ),
        )

        changed_positions = (
            first_positions[0],
            replace(
                first_positions[1],
                tokens_held=(
                    599_999_999
                ),
                updated_at=101.0,
            ),
        )

        result = (
            await self.resolve(
                position_results=[
                    self.positions_result(
                        first_positions
                    ),
                    self.positions_result(
                        changed_positions
                    ),
                ],
            )
        )[0]

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "OPEN_POSITION_ALLOCATION_CHANGED_DURING_AUTHORIZATION",
            result.reasons,
        )

        self.assertIsNone(
            result.authorization
        )

    async def test_authorization_fingerprint_is_deterministic(
        self,
    ):
        first = (
            await self.resolve()
        )[0]

        second = (
            await self.resolve()
        )[0]

        self.assertEqual(
            first.status,
            AUTHORIZED,
        )

        self.assertEqual(
            second.status,
            AUTHORIZED,
        )

        first_hash = (
            first.authorization
            .authorization_sha256
        )

        second_hash = (
            second.authorization
            .authorization_sha256
        )

        self.assertEqual(
            first_hash,
            second_hash,
        )

        self.assertEqual(
            len(first_hash),
            64,
        )

        int(
            first_hash,
            16,
        )

    async def test_signed_realized_loss_in_existing_lot_remains_authorizable(
        self,
    ):
        position = self.position(
            position_id=1,
            entry_slot=100,
            tokens=1_000_000_000,
            realized=-123_456,
        )

        snapshot = self.positions_result(
            (
                position,
            )
        )

        result = (
            await self.resolve(
                position_results=[
                    snapshot,
                    snapshot,
                ],
            )
        )[0]

        self.assertEqual(
            result.status,
            AUTHORIZED,
        )

        self.assertEqual(
            result.authorization
            .allocation
            .allocations[0]
            .cumulative_realized_pnl_before_lamports,
            -123_456,
        )

    def test_public_contract_has_no_mutation_or_database_override(
        self,
    ):
        import inspect

        parameters = (
            inspect.signature(
                authorize_live_pump_sell
            )
            .parameters
        )

        self.assertEqual(
            set(parameters),
            {
                "wallet_pubkey",
                "mint",
                "tokens_to_sell",
                "slippage_bps",
                "base_network_fee_lamports",
                "priority_fee_lamports",
                "min_context_slot",
            },
        )

        self.assertNotIn(
            "db_path",
            parameters,
        )

        self.assertEqual(
            LIVE_PUMP_SELL_AUTHORIZATION_VERSION,
            "live-pump-sell-authorization-v2",
        )


if __name__ == "__main__":
    unittest.main()
