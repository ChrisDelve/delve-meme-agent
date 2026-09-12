import unittest
from types import SimpleNamespace

from src.execution.live_pump_liquidation_value import (
    LIVE_PUMP_LIQUIDATION_VALUE_VERSION,
    RESOLVED as LIQUIDATION_RESOLVED,
)
from src.portfolio.live_wallet_valuation import (
    LiveMintInventoryValuation,
)
from src.strategies.live_exit_policy import (
    FULL_EXIT,
    HOLD,
    UNKNOWN,
    LiveExitPolicy,
    evaluate_live_exit,
)


class LiveExitPolicyTests(
    unittest.TestCase
):
    def setUp(self):
        self.mint = "mint-a"

        self.policy = LiveExitPolicy(
            take_profit_return_bps=2_000,
            stop_loss_return_bps=-4_000,
            max_hold_seconds=900,
        )

    def valuation(
        self,
        *,
        tokens=1_000,
        entry_cost=1_000_000,
        proceeds=0,
        liquidation_value=1_000_000,
        oldest_time=1_000,
        newest_time=1_000,
        liquidatable_tokens=None,
        unliquidatable_tokens=None,
        liquidation_status=(
            LIQUIDATION_RESOLVED
        ),
        liquidation_mint=None,
        liquidation_tokens=None,
        liquidation_value_binding=None,
    ):
        if liquidatable_tokens is None:
            liquidatable_tokens = tokens

        if unliquidatable_tokens is None:
            unliquidatable_tokens = (
                tokens
                - liquidatable_tokens
            )

        if liquidation_mint is None:
            liquidation_mint = self.mint

        if liquidation_tokens is None:
            liquidation_tokens = tokens

        if liquidation_value_binding is None:
            liquidation_value_binding = (
                liquidation_value
            )

        liquidation = SimpleNamespace(
            resolver_version=(
                LIVE_PUMP_LIQUIDATION_VALUE_VERSION
            ),
            status=liquidation_status,
            mint=liquidation_mint,
            tokens_held=(
                liquidation_tokens
            ),
            liquidatable_tokens=(
                liquidatable_tokens
            ),
            unliquidatable_tokens=(
                unliquidatable_tokens
            ),
            liquidation_value_lamports=(
                liquidation_value_binding
            ),
        )

        return LiveMintInventoryValuation(
            mint=self.mint,
            open_lots=2,
            tokens_held=tokens,
            total_entry_wallet_cost_lamports=(
                entry_cost
            ),
            remaining_cost_basis_lamports=(
                700_000
            ),
            cumulative_net_proceeds_lamports=(
                proceeds
            ),
            cumulative_realized_pnl_lamports=0,
            oldest_entry_slot=100,
            newest_entry_slot=200,
            oldest_entry_block_time=(
                oldest_time
            ),
            newest_entry_block_time=(
                newest_time
            ),
            liquidation_value_lamports=(
                liquidation_value
            ),
            valuation=liquidation,
        )

    def test_hold_inside_all_thresholds(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                liquidation_value=1_100_000,
            ),
            evaluated_at=1_500,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            HOLD,
        )

        self.assertIsNone(
            result.reason
        )

        self.assertEqual(
            result.tokens_to_sell,
            0,
        )

        self.assertFalse(
            result.should_exit
        )

    def test_take_profit_is_full_exit(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                liquidation_value=1_200_000,
            ),
            evaluated_at=1_500,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            FULL_EXIT,
        )

        self.assertEqual(
            result.reason,
            "TAKE_PROFIT",
        )

        self.assertEqual(
            result.tokens_to_sell,
            1_000,
        )

        self.assertTrue(
            result.should_exit
        )

    def test_stop_loss_is_full_exit(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                liquidation_value=600_000,
            ),
            evaluated_at=1_500,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            FULL_EXIT,
        )

        self.assertEqual(
            result.reason,
            "STOP_LOSS",
        )

        self.assertEqual(
            result.tokens_to_sell,
            1_000,
        )

    def test_prior_proceeds_count_toward_return(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                proceeds=500_000,
                liquidation_value=700_000,
            ),
            evaluated_at=1_500,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            FULL_EXIT,
        )

        self.assertEqual(
            result.reason,
            "TAKE_PROFIT",
        )

        self.assertEqual(
            result.return_bps,
            2_000.0,
        )

    def test_take_profit_exact_boundary_exits(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                liquidation_value=1_200_000,
            ),
            evaluated_at=1_500,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            FULL_EXIT,
        )

        self.assertEqual(
            result.reason,
            "TAKE_PROFIT",
        )

    def test_stop_loss_exact_boundary_exits(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                liquidation_value=600_000,
            ),
            evaluated_at=1_500,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            FULL_EXIT,
        )

        self.assertEqual(
            result.reason,
            "STOP_LOSS",
        )

    def test_max_hold_uses_newest_open_lot(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                liquidation_value=1_000_000,
                oldest_time=100,
                newest_time=1_100,
            ),
            evaluated_at=2_000,
            policy=self.policy,
        )

        self.assertEqual(
            result.age_seconds,
            900,
        )

        self.assertEqual(
            result.status,
            FULL_EXIT,
        )

        self.assertEqual(
            result.reason,
            "MAX_HOLD_TIME",
        )

    def test_missing_time_blocks_hold_when_max_hold_enabled(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                liquidation_value=1_000_000,
                oldest_time=None,
                newest_time=None,
            ),
            evaluated_at=2_000,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reason,
            "MAX_HOLD_AGE_UNAVAILABLE",
        )

        self.assertEqual(
            result.tokens_to_sell,
            0,
        )

    def test_time_policy_can_be_explicitly_disabled(
        self,
    ):
        policy = LiveExitPolicy(
            take_profit_return_bps=2_000,
            stop_loss_return_bps=-4_000,
            max_hold_seconds=None,
        )

        result = evaluate_live_exit(
            valuation=self.valuation(
                liquidation_value=1_000_000,
                oldest_time=None,
                newest_time=None,
            ),
            evaluated_at=2_000,
            policy=policy,
        )

        self.assertEqual(
            result.status,
            HOLD,
        )

    def test_price_exit_does_not_require_time_bound(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                liquidation_value=1_200_000,
                oldest_time=None,
                newest_time=None,
            ),
            evaluated_at=2_000,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            FULL_EXIT,
        )

        self.assertEqual(
            result.reason,
            "TAKE_PROFIT",
        )

    def test_partial_liquidation_never_becomes_policy_partial_exit(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                liquidation_value=500_000,
                liquidatable_tokens=600,
                unliquidatable_tokens=400,
            ),
            evaluated_at=2_000,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reason,
            "FULL_LIQUIDATION_UNAVAILABLE",
        )

        self.assertEqual(
            result.tokens_to_sell,
            0,
        )

    def test_liquidation_binding_mismatch_is_unknown(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                liquidation_mint="other-mint",
            ),
            evaluated_at=2_000,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reason,
            "LIQUIDATION_BINDING_MISMATCH",
        )

    def test_negative_position_age_is_unknown(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                newest_time=2_001,
            ),
            evaluated_at=2_000,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reason,
            "NEGATIVE_POSITION_AGE",
        )

    def test_invalid_evaluated_at_fails_closed(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(),
            evaluated_at=0,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reason,
            "INVALID_EVALUATED_AT",
        )

        self.assertEqual(
            result.tokens_to_sell,
            0,
        )

    def test_liquidation_version_mismatch_is_unknown(
        self,
    ):
        valuation = self.valuation()

        valuation = LiveMintInventoryValuation(
            **{
                **valuation.__dict__,
                "valuation": SimpleNamespace(
                    **{
                        **valuation.valuation.__dict__,
                        "resolver_version": (
                            "wrong-version"
                        ),
                    }
                ),
            }
        )

        result = evaluate_live_exit(
            valuation=valuation,
            evaluated_at=2_000,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reason,
            "LIQUIDATION_VERSION_MISMATCH",
        )

    def test_unresolved_liquidation_is_unknown(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                liquidation_status="UNKNOWN",
            ),
            evaluated_at=2_000,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reason,
            "LIQUIDATION_NOT_RESOLVED",
        )

        self.assertEqual(
            result.tokens_to_sell,
            0,
        )

    def test_incomplete_entry_time_bounds_are_unknown(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                oldest_time=None,
                newest_time=1_000,
            ),
            evaluated_at=2_000,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reason,
            "INCOMPLETE_ENTRY_TIME_BOUNDS",
        )

    def test_invalid_entry_slot_bounds_are_unknown(
        self,
    ):
        valuation = self.valuation()

        valuation = LiveMintInventoryValuation(
            **{
                **valuation.__dict__,
                "oldest_entry_slot": 300,
                "newest_entry_slot": 200,
            }
        )

        result = evaluate_live_exit(
            valuation=valuation,
            evaluated_at=2_000,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reason,
            "INVALID_ENTRY_SLOT_BOUNDS",
        )

    def test_take_profit_precedes_max_hold(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                liquidation_value=1_200_000,
                oldest_time=100,
                newest_time=100,
            ),
            evaluated_at=2_000,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            FULL_EXIT,
        )

        self.assertEqual(
            result.reason,
            "TAKE_PROFIT",
        )

        self.assertEqual(
            result.tokens_to_sell,
            1_000,
        )

    def test_stop_loss_precedes_max_hold(
        self,
    ):
        result = evaluate_live_exit(
            valuation=self.valuation(
                liquidation_value=600_000,
                oldest_time=100,
                newest_time=100,
            ),
            evaluated_at=2_000,
            policy=self.policy,
        )

        self.assertEqual(
            result.status,
            FULL_EXIT,
        )

        self.assertEqual(
            result.reason,
            "STOP_LOSS",
        )

        self.assertEqual(
            result.tokens_to_sell,
            1_000,
        )

    def test_invalid_policy_fails_closed(
        self,
    ):
        policy = LiveExitPolicy(
            take_profit_return_bps=0,
            stop_loss_return_bps=-4_000,
            max_hold_seconds=900,
        )

        result = evaluate_live_exit(
            valuation=self.valuation(),
            evaluated_at=2_000,
            policy=policy,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reason,
            "INVALID_TAKE_PROFIT_RETURN_BPS",
        )


if __name__ == "__main__":
    unittest.main()
