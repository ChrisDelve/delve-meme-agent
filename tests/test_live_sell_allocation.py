import unittest
from dataclasses import replace

from solders.pubkey import Pubkey

from src.portfolio.live_positions import (
    LIVE_POSITION_VERSION,
    OPEN,
    LivePosition,
)
from src.portfolio.live_sell_allocation import (
    BLOCK,
    FIFO_ENTRY_SLOT,
    I64_MIN,
    LIVE_SELL_ALLOCATION_VERSION,
    PLANNED,
    UNKNOWN,
    U64_MAX,
    plan_live_sell_allocation,
)


class LiveSellAllocationTests(
    unittest.TestCase
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

    def position(
        self,
        *,
        position_id,
        mint=None,
        entry_slot=100,
        tokens=1_000,
        exposure=2_000,
        cost_basis=1_500,
        proceeds=0,
        realized=0,
        wallet=None,
        status=OPEN,
        version=LIVE_POSITION_VERSION,
    ):
        if mint is None:
            mint = self.mint

        if wallet is None:
            wallet = self.wallet

        return LivePosition(
            position_id=position_id,
            position_version=version,
            reservation_id=(
                f"reservation-{position_id}"
            ),
            entry_signature=(
                f"signature-{position_id}"
            ),
            wallet_pubkey=wallet,
            mint=mint,
            status=status,
            fill_resolver_version=(
                "fill-v1"
            ),
            signed_transaction_sha256=(
                "a" * 64
            ),
            observed_transaction_sha256=(
                "b" * 64
            ),
            entry_slot=entry_slot,
            entry_block_time=100.0,
            base_token_program=str(
                Pubkey.new_unique()
            ),
            associated_base_user=str(
                Pubkey.new_unique()
            ),
            quote_mint=(
                "11111111111111111111111111111111"
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
            cumulative_net_proceeds_lamports=(
                proceeds
            ),
            cumulative_realized_pnl_lamports=(
                realized
            ),
            network_fee_lamports=5_000,
            wallet_pre_balance_lamports=(
                10_000_000
            ),
            wallet_post_balance_lamports=(
                9_000_000
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
            quote_amount=(
                cost_basis
            ),
            created_at=100.0,
            updated_at=100.0,
        )

    def plan(
        self,
        *,
        tokens_to_sell=500,
        positions=None,
        wallet=None,
        mint=None,
    ):
        if positions is None:
            positions = (
                self.position(
                    position_id=1
                ),
            )

        if wallet is None:
            wallet = self.wallet

        if mint is None:
            mint = self.mint

        return plan_live_sell_allocation(
            wallet_pubkey=wallet,
            mint=mint,
            tokens_to_sell=(
                tokens_to_sell
            ),
            positions=positions,
        )

    def test_invalid_request_inputs_fail_closed(
        self,
    ):
        cases = (
            {
                "wallet":
                    "not-a-wallet",
                "reason":
                    "INVALID_WALLET_PUBKEY",
            },
            {
                "mint":
                    "not-a-mint",
                "reason":
                    "INVALID_MINT",
            },
            {
                "tokens_to_sell":
                    0,
                "reason":
                    "INVALID_TOKENS_TO_SELL",
            },
            {
                "tokens_to_sell":
                    -1,
                "reason":
                    "INVALID_TOKENS_TO_SELL",
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

                result = self.plan(
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

    def test_positions_must_be_tuple(
        self,
    ):
        result = self.plan(
            positions=[]
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "POSITIONS_NOT_TUPLE",
            result.reasons,
        )

    def test_nonmatching_mint_is_ignored(
        self,
    ):
        other = self.position(
            position_id=90,
            mint=self.other_mint,
            tokens=900,
        )

        target = self.position(
            position_id=10,
            tokens=500,
        )

        result = self.plan(
            tokens_to_sell=500,
            positions=(
                other,
                target,
            ),
        )

        self.assertEqual(
            result.status,
            PLANNED,
        )

        self.assertEqual(
            len(result.allocations),
            1,
        )

        self.assertEqual(
            result.allocations[0]
            .position_id,
            10,
        )

    def test_no_open_inventory_blocks(
        self,
    ):
        result = self.plan(
            positions=(
                self.position(
                    position_id=90,
                    mint=self.other_mint,
                ),
            ),
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "NO_OPEN_POSITION_FOR_MINT",
            result.reasons,
        )

    def test_sell_cannot_exceed_inventory(
        self,
    ):
        position = self.position(
            position_id=1,
            tokens=100,
        )

        result = self.plan(
            tokens_to_sell=101,
            positions=(
                position,
            ),
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SELL_EXCEEDS_OPEN_INVENTORY",
            result.reasons,
        )

    def test_single_partial_lot_reduces_cost_and_exposure_proportionally(
        self,
    ):
        position = self.position(
            position_id=1,
            tokens=1_000,
            exposure=2_000,
            cost_basis=1_500,
        )

        result = self.plan(
            tokens_to_sell=250,
            positions=(
                position,
            ),
        )

        self.assertEqual(
            result.status,
            PLANNED,
        )

        allocation = (
            result.allocations[0]
        )

        self.assertEqual(
            allocation.tokens_after,
            750,
        )

        self.assertEqual(
            allocation.exposure_reduction_lamports,
            500,
        )

        self.assertEqual(
            allocation.exposure_after_lamports,
            1_500,
        )

        self.assertEqual(
            allocation.cost_basis_reduction_lamports,
            375,
        )

        self.assertEqual(
            allocation.cost_basis_after_lamports,
            1_125,
        )

    def test_full_lot_consumes_all_residual_cost_and_exposure(
        self,
    ):
        position = self.position(
            position_id=1,
            tokens=3,
            exposure=2,
            cost_basis=1,
        )

        result = self.plan(
            tokens_to_sell=3,
            positions=(
                position,
            ),
        )

        allocation = (
            result.allocations[0]
        )

        self.assertEqual(
            allocation.exposure_after_lamports,
            0,
        )

        self.assertEqual(
            allocation.cost_basis_after_lamports,
            0,
        )

    def test_fifo_uses_entry_slot_not_input_order(
        self,
    ):
        newer = self.position(
            position_id=20,
            entry_slot=200,
            tokens=600,
        )

        older = self.position(
            position_id=10,
            entry_slot=100,
            tokens=400,
        )

        result = self.plan(
            tokens_to_sell=500,
            positions=(
                newer,
                older,
            ),
        )

        self.assertEqual(
            result.status,
            PLANNED,
        )

        self.assertEqual(
            [
                item.position_id
                for item
                in result.allocations
            ],
            [
                10,
                20,
            ],
        )

        self.assertEqual(
            [
                item.tokens_to_sell
                for item
                in result.allocations
            ],
            [
                400,
                100,
            ],
        )

    def test_equal_entry_slot_uses_position_id_tiebreaker(
        self,
    ):
        b = self.position(
            position_id=2,
            entry_slot=100,
            tokens=400,
        )

        a = self.position(
            position_id=1,
            entry_slot=100,
            tokens=400,
        )

        result = self.plan(
            tokens_to_sell=500,
            positions=(
                b,
                a,
            ),
        )

        self.assertEqual(
            [
                item.position_id
                for item
                in result.allocations
            ],
            [
                1,
                2,
            ],
        )

    def test_partial_rounding_never_over_reduces(
        self,
    ):
        position = self.position(
            position_id=1,
            tokens=3,
            exposure=2,
            cost_basis=2,
        )

        result = self.plan(
            tokens_to_sell=1,
            positions=(
                position,
            ),
        )

        allocation = (
            result.allocations[0]
        )

        self.assertEqual(
            allocation.exposure_reduction_lamports,
            0,
        )

        self.assertEqual(
            allocation.cost_basis_reduction_lamports,
            0,
        )

        self.assertEqual(
            allocation.exposure_after_lamports,
            2,
        )

        self.assertEqual(
            allocation.cost_basis_after_lamports,
            2,
        )

    def test_final_sale_releases_rounding_residual(
        self,
    ):
        position = self.position(
            position_id=1,
            tokens=2,
            exposure=1,
            cost_basis=1,
        )

        first = self.plan(
            tokens_to_sell=1,
            positions=(
                position,
            ),
        )

        first_allocation = (
            first.allocations[0]
        )

        residual = replace(
            position,
            tokens_held=(
                first_allocation
                .tokens_after
            ),
            remaining_exposure_lamports=(
                first_allocation
                .exposure_after_lamports
            ),
            remaining_cost_basis_lamports=(
                first_allocation
                .cost_basis_after_lamports
            ),
        )

        second = self.plan(
            tokens_to_sell=1,
            positions=(
                residual,
            ),
        )

        final = second.allocations[0]

        self.assertEqual(
            final.exposure_after_lamports,
            0,
        )

        self.assertEqual(
            final.cost_basis_after_lamports,
            0,
        )

    def test_aggregate_totals_conserve_exactly(
        self,
    ):
        positions = (
            self.position(
                position_id=1,
                entry_slot=100,
                tokens=400,
                exposure=800,
                cost_basis=600,
            ),
            self.position(
                position_id=2,
                entry_slot=200,
                tokens=600,
                exposure=1_200,
                cost_basis=900,
            ),
        )

        result = self.plan(
            tokens_to_sell=500,
            positions=positions,
        )

        self.assertEqual(
            result.total_tokens_before,
            1_000,
        )

        self.assertEqual(
            result.total_tokens_after,
            500,
        )

        self.assertEqual(
            result.total_exposure_before_lamports,
            (
                result.total_exposure_reduction_lamports
                + result.total_exposure_after_lamports
            ),
        )

        self.assertEqual(
            result.total_cost_basis_before_lamports,
            (
                result.total_cost_basis_reduction_lamports
                + result.total_cost_basis_after_lamports
            ),
        )

    def test_previous_proceeds_and_realized_pnl_are_bound_not_modified(
        self,
    ):
        position = self.position(
            position_id=1,
            proceeds=123,
            realized=-45,
        )

        result = self.plan(
            positions=(
                position,
            ),
        )

        allocation = (
            result.allocations[0]
        )

        self.assertEqual(
            allocation.cumulative_net_proceeds_before_lamports,
            123,
        )

        self.assertEqual(
            allocation.cumulative_realized_pnl_before_lamports,
            -45,
        )

    def test_invalid_position_contract_fails_closed(
        self,
    ):
        base = self.position(
            position_id=1
        )

        cases = (
            (
                replace(
                    base,
                    cumulative_realized_pnl_lamports=(
                        I64_MIN - 1
                    ),
                ),
                "POSITION_REALIZED_PNL_INVALID",
            ),
            (
                replace(
                    base,
                    position_id=0,
                ),
                "POSITION_ID_INVALID",
            ),
            (
                replace(
                    base,
                    position_version=(
                        "wrong-version"
                    ),
                ),
                "POSITION_VERSION_MISMATCH",
            ),
            (
                replace(
                    base,
                    wallet_pubkey=str(
                        Pubkey.new_unique()
                    ),
                ),
                "POSITION_WALLET_MISMATCH",
            ),
            (
                replace(
                    base,
                    status="CLOSED",
                ),
                "POSITION_NOT_OPEN",
            ),
            (
                replace(
                    base,
                    tokens_held=0,
                ),
                "POSITION_TOKENS_INVALID",
            ),
        )

        for position, reason in cases:
            with self.subTest(
                reason=reason
            ):
                result = self.plan(
                    positions=(
                        position,
                    ),
                )

                self.assertEqual(
                    result.status,
                    UNKNOWN,
                )

                self.assertIn(
                    reason,
                    result.reasons,
                )

    def test_duplicate_position_id_fails_closed(
        self,
    ):
        positions = (
            self.position(
                position_id=7,
                entry_slot=100,
            ),
            self.position(
                position_id=7,
                entry_slot=200,
            ),
        )

        result = self.plan(
            positions=positions,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "DUPLICATE_POSITION_ID",
            result.reasons,
        )

    def test_public_contract_is_pure_accounting_only(
        self,
    ):
        import inspect

        parameters = (
            inspect.signature(
                plan_live_sell_allocation
            )
            .parameters
        )

        self.assertEqual(
            set(parameters),
            {
                "wallet_pubkey",
                "mint",
                "tokens_to_sell",
                "positions",
            },
        )

        self.assertEqual(
            LIVE_SELL_ALLOCATION_VERSION,
            "live-sell-allocation-v1",
        )

        self.assertEqual(
            FIFO_ENTRY_SLOT,
            "FIFO_ENTRY_SLOT",
        )


if __name__ == "__main__":
    unittest.main()
