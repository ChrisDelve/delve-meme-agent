from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
)

os.environ.setdefault(
    "HELIUS_API_KEY",
    "test-key",
)

from src.data import market_collector
from src.execution.live_entry_candidate_scheduler import (
    LiveEntryCandidateScheduler,
)
from src.strategies.live_entry_policy import (
    LiveEntryPolicy,
)


class MarketCollectorLiveEntrySchedulerTests(
    unittest.IsolatedAsyncioTestCase
):
    def trade_event(
        self,
    ):
        return {
            "mint": "mint-1",
            "user": "wallet-1",
            "quote_mint":
                "11111111111111111111111111111111",
            "timestamp": 1_000,
            "sol_amount": 1_000_000,
            "quote_amount": 1_000_000,
            "token_amount": 10_000,
            "fee": 100,
            "creator_fee": 50,
            "ix_name": "buy",
            "mayhem_mode": False,
            "virtual_sol_reserves":
                30_000_000_000,
            "virtual_token_reserves":
                1_000_000_000_000,
            "real_sol_reserves":
                10_000_000_000,
            "real_token_reserves":
                500_000_000_000,
            "fee_basis_points": 100,
            "creator_fee_basis_points": 50,
        }

    def prediction(
        self,
        *,
        eligible=1,
    ):
        return {
            "entry_signature":
                "signature-1",
            "model_eligible":
                eligible,
            "probability_2x_15m": (
                0.42
                if eligible in (
                    1,
                    True,
                )
                else None
            ),
            "predicted_at": 1_002,
        }

    def process_with_prediction(
        self,
        *,
        prediction,
        scheduler,
    ):
        with (
            patch.object(
                market_collector,
                "save_buy",
                return_value={
                    "observed_rank": 1,
                    "entry_age_seconds": 0,
                },
            ),
            patch.object(
                market_collector,
                "record_model_shadow_prediction",
                return_value=prediction,
            ),
            patch.object(
                market_collector,
                "schedule_pretrade_shadow_candidate",
                return_value=False,
            ),
            patch.object(
                market_collector,
                "record_shadow_signal",
                return_value=None,
            ),
            patch.object(
                market_collector.time,
                "time",
                return_value=1_001,
            ),
        ):
            market_collector.process_buy_event(
                "signature-1",
                123,
                self.trade_event(),
                live_entry_scheduler=scheduler,
            )

    async def test_ineligible_prediction_does_not_reach_live_scheduler(
        self,
    ):
        scheduler = Mock()

        self.process_with_prediction(
            prediction=self.prediction(
                eligible=0
            ),
            scheduler=scheduler,
        )

        scheduler.schedule.assert_not_called()

    async def test_truthy_nonexact_eligibility_does_not_reach_live_scheduler(
        self,
    ):
        scheduler = Mock()

        self.process_with_prediction(
            prediction=self.prediction(
                eligible="1"
            ),
            scheduler=scheduler,
        )

        scheduler.schedule.assert_not_called()

    async def test_malformed_prediction_does_not_reach_live_scheduler(
        self,
    ):
        scheduler = Mock()

        with patch(
            "builtins.print"
        ):
            self.process_with_prediction(
                prediction=object(),
                scheduler=scheduler,
            )

        scheduler.schedule.assert_not_called()

    async def test_exact_eligible_prediction_is_scheduled_with_causal_event(
        self,
    ):
        scheduler = Mock()

        async def finished():
            return SimpleNamespace(
                status="PASS",
                stage="EVIDENCE",
                reasons=(),
            )

        task = asyncio.create_task(
            finished()
        )

        scheduler.schedule.return_value = (
            SimpleNamespace(
                scheduled=True,
                reasons=(),
                task=task,
            )
        )

        prediction = self.prediction(
            eligible=1
        )

        with patch(
            "builtins.print"
        ):
            self.process_with_prediction(
                prediction=prediction,
                scheduler=scheduler,
            )

            await task
            await asyncio.sleep(0)

        scheduler.schedule.assert_called_once_with(
            prediction=prediction,
            entry_signature="signature-1",
            mint="mint-1",
            event_user="wallet-1",
            quote_mint=(
                "11111111111111111111111111111111"
            ),
            slot=123,
            trade_timestamp=1_000,
            observed_at=1_001,
            signal_virtual_quote_reserves=(
                30_000_000_000
            ),
            signal_virtual_token_reserves=(
                1_000_000_000_000
            ),
        )

    async def test_resolved_candidate_is_observed_without_task(
        self,
    ):
        scheduler = Mock()

        result = SimpleNamespace(
            status="REJECT",
            stage="POLICY",
            reasons=(
                "POLICY:"
                "PROBABILITY_BELOW_THRESHOLD",
            ),
        )

        scheduler.schedule.return_value = (
            SimpleNamespace(
                scheduled=False,
                resolved=True,
                reasons=(),
                task=None,
                result=result,
            )
        )

        prediction = self.prediction(
            eligible=1
        )

        with patch(
            "builtins.print"
        ) as printer:
            self.process_with_prediction(
                prediction=prediction,
                scheduler=scheduler,
            )

        self.assertTrue(
            any(
                "status=REJECT"
                in str(call)
                and "stage=POLICY"
                in str(call)
                for call
                in printer.call_args_list
            )
        )

    async def test_default_collector_path_passes_no_scheduler_to_listen(
        self,
    ):
        shadow_account = SimpleNamespace(
            current_equity_lamports=1,
            cash_balance_lamports=1,
            open_positions=0,
        )

        async def sweeper():
            await asyncio.Event().wait()

        listen_mock = AsyncMock(
            return_value=None
        )

        with (
            patch.object(
                market_collector,
                "initialize_shadow_account",
                return_value=shadow_account,
            ),
            patch.object(
                market_collector,
                "initialize_shadow_position_manager",
                return_value=set(),
            ),
            patch.object(
                market_collector,
                "run_shadow_position_sweeper",
                side_effect=sweeper,
            ),
            patch.object(
                market_collector,
                "listen",
                listen_mock,
            ),
            patch(
                "builtins.print"
            ),
        ):
            await (
                market_collector
                .run_market_collector()
            )

        listen_mock.assert_awaited_once_with(
            live_entry_scheduler=None
        )

    async def test_supplied_scheduler_is_closed_by_collector(
        self,
    ):
        policy = LiveEntryPolicy(
            min_probability_2x_15m=0.40,
            max_candidate_age_seconds=5,
        )

        scheduler = (
            LiveEntryCandidateScheduler(
                policy=policy,
                max_concurrency=1,
                max_pending_tasks=1,
            )
        )

        shadow_account = SimpleNamespace(
            current_equity_lamports=1,
            cash_balance_lamports=1,
            open_positions=0,
        )

        async def sweeper():
            await asyncio.Event().wait()

        async def listen_stub(
            *,
            live_entry_scheduler=None,
        ):
            self.assertIs(
                live_entry_scheduler,
                scheduler,
            )

        with (
            patch.object(
                market_collector,
                "initialize_shadow_account",
                return_value=shadow_account,
            ),
            patch.object(
                market_collector,
                "initialize_shadow_position_manager",
                return_value=set(),
            ),
            patch.object(
                market_collector,
                "run_shadow_position_sweeper",
                side_effect=sweeper,
            ),
            patch.object(
                market_collector,
                "listen",
                side_effect=listen_stub,
            ),
            patch(
                "builtins.print"
            ),
        ):
            await (
                market_collector
                .run_market_collector(
                    live_entry_scheduler=(
                        scheduler
                    )
                )
            )

        self.assertTrue(
            scheduler.closed
        )

    async def test_supplied_scheduler_closes_when_startup_fails(
        self,
    ):
        policy = LiveEntryPolicy(
            min_probability_2x_15m=0.40,
            max_candidate_age_seconds=5,
        )

        scheduler = (
            LiveEntryCandidateScheduler(
                policy=policy,
                max_concurrency=1,
                max_pending_tasks=1,
            )
        )

        with (
            patch.object(
                market_collector,
                "initialize_shadow_account",
                side_effect=RuntimeError(
                    "startup boom"
                ),
            ),
            patch.object(
                market_collector,
                "run_shadow_position_sweeper",
            ) as sweeper,
            patch(
                "builtins.print"
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^startup boom$",
            ):
                await (
                    market_collector
                    .run_market_collector(
                        live_entry_scheduler=(
                            scheduler
                        )
                    )
                )

        self.assertTrue(
            scheduler.closed
        )
        sweeper.assert_not_called()

    async def test_invalid_scheduler_type_fails_before_collector_startup(
        self,
    ):
        with self.assertRaisesRegex(
            TypeError,
            (
                "^live_entry_scheduler must be "
                "LiveEntryCandidateScheduler$"
            ),
        ):
            await (
                market_collector
                .run_market_collector(
                    live_entry_scheduler=object()
                )
            )


if __name__ == "__main__":
    unittest.main()
