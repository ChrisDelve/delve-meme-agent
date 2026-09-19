from __future__ import annotations

import asyncio
import unittest
from unittest.mock import (
    AsyncMock,
    Mock,
    call,
    patch,
)

from src.execution.live_buy_execution_config import (
    LIVE_BUY_EXECUTION_CONFIG_VERSION,
    LiveBuyExecutionConfig,
)
from src.execution.live_process_owner import (
    LiveProcessOwner,
)
from src.execution.live_sell_runtime import (
    ADVANCED,
    BLOCK,
    CLAIMED,
    COMPLETE,
    EXIT,
    HALTED,
    HOLD,
    IDLE,
    KILL,
    LIVE_SELL_RUNTIME_VERSION,
    RECOVERY,
    RECONCILED,
    SIGNED,
    SIGNING,
    UNKNOWN,
    LiveSellRuntimeResult,
)
from src.execution.live_sell_supervisor_config import (
    LIVE_SELL_SUPERVISOR_CONFIG_VERSION,
    LiveSellSupervisorConfig,
)
from src.execution.live_sell_supervisor_service import (
    LIVE_SELL_SUPERVISOR_CLOCK_INVALID,
    LIVE_SELL_SUPERVISOR_RESULT_CONTRACT_INVALID,
    LIVE_SELL_SUPERVISOR_SERVICE_VERSION,
    LiveSellSupervisorServiceError,
    run_live_sell_supervisor_service,
)


MODULE = (
    "src.execution.live_sell_supervisor_service"
)


class LiveSellSupervisorServiceTests(
    unittest.IsolatedAsyncioTestCase
):
    @staticmethod
    def execution_config() -> LiveBuyExecutionConfig:
        return LiveBuyExecutionConfig(
            config_version=(
                LIVE_BUY_EXECUTION_CONFIG_VERSION
            ),
            wallet_pubkey=(
                "11111111111111111111111111111112"
            ),
            protected_cash_lamports=0,
            buy_slippage_bps=300,
            buy_base_network_fee_lamports=5_000,
            buy_priority_fee_lamports=7_000,
            buy_rent_lamports=2_000,
            exit_slippage_bps=400,
            exit_base_network_fee_lamports=6_000,
            exit_priority_fee_lamports=8_000,
            reservation_ttl_seconds=30.0,
            max_authorization_age_seconds=10.0,
            compute_unit_limit=300_000,
        )

    @staticmethod
    def supervisor_config() -> LiveSellSupervisorConfig:
        return LiveSellSupervisorConfig(
            config_version=(
                LIVE_SELL_SUPERVISOR_CONFIG_VERSION
            ),
            take_profit_return_bps=2_000,
            stop_loss_return_bps=-4_000,
            max_hold_seconds=900,
            evaluation_interval_seconds=2.0,
        )

    @staticmethod
    def result(
        *,
        status=IDLE,
        stage=COMPLETE,
        reasons=("TICK_RESULT",),
        version=LIVE_SELL_RUNTIME_VERSION,
    ) -> LiveSellRuntimeResult:
        return LiveSellRuntimeResult(
            runtime_version=version,
            status=status,
            stage=stage,
            reasons=tuple(reasons),
            recovery=None,
            signing=None,
            controller=None,
        )

    @staticmethod
    def owner() -> Mock:
        owner = Mock(
            spec=LiveProcessOwner
        )

        owner.run_sell_once = AsyncMock()

        return owner

    async def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_SELL_SUPERVISOR_SERVICE_VERSION,
            "live-sell-supervisor-service-v1",
        )

    async def test_preexisting_stop_invokes_no_authority(
        self,
    ):
        owner = self.owner()
        stop_event = asyncio.Event()
        stop_event.set()

        await run_live_sell_supervisor_service(
            owner=owner,
            supervisor_config=(
                self.supervisor_config()
            ),
            execution_config=(
                self.execution_config()
            ),
            stop_event=stop_event,
        )

        owner.run_sell_once.assert_not_awaited()

    async def test_exact_inputs_use_fresh_time_and_existing_configs(
        self,
    ):
        owner = self.owner()
        stop_event = asyncio.Event()

        async def run_sell_once(
            **kwargs,
        ):
            stop_event.set()
            return self.result()

        owner.run_sell_once.side_effect = (
            run_sell_once
        )

        supervisor_config = (
            self.supervisor_config()
        )
        execution_config = (
            self.execution_config()
        )

        with patch(
            f"{MODULE}.time.time",
            return_value=2_000.75,
        ):
            await run_live_sell_supervisor_service(
                owner=owner,
                supervisor_config=(
                    supervisor_config
                ),
                execution_config=(
                    execution_config
                ),
                stop_event=stop_event,
            )

        owner.run_sell_once.assert_awaited_once_with(
            compute_unit_limit=(
                execution_config.compute_unit_limit
            ),
            wallet_pubkey=(
                execution_config.wallet_pubkey
            ),
            evaluated_at=2_000,
            policy=supervisor_config.policy,
            slippage_bps=(
                execution_config.exit_slippage_bps
            ),
            base_network_fee_lamports=(
                execution_config
                .exit_base_network_fee_lamports
            ),
            priority_fee_lamports=(
                execution_config
                .exit_priority_fee_lamports
            ),
        )

    async def test_all_runtime_statuses_are_nonfatal_completed_ticks(
        self,
    ):
        cases = (
            (IDLE, COMPLETE),
            (HALTED, KILL),
            (ADVANCED, RECOVERY),
            (RECONCILED, RECOVERY),
            (HOLD, RECOVERY),
            (SIGNED, SIGNING),
            (CLAIMED, EXIT),
            (BLOCK, EXIT),
            (UNKNOWN, EXIT),
        )

        for status, stage in cases:
            with self.subTest(
                status=status,
            ):
                owner = self.owner()
                stop_event = asyncio.Event()

                async def run_sell_once(
                    **kwargs,
                ):
                    del kwargs
                    stop_event.set()
                    return self.result(
                        status=status,
                        stage=stage,
                    )

                owner.run_sell_once.side_effect = (
                    run_sell_once
                )

                with patch(
                    f"{MODULE}.time.time",
                    return_value=2_000.0,
                ):
                    await run_live_sell_supervisor_service(
                        owner=owner,
                        supervisor_config=(
                            self.supervisor_config()
                        ),
                        execution_config=(
                            self.execution_config()
                        ),
                        stop_event=stop_event,
                    )

                owner.run_sell_once.assert_awaited_once()

    async def test_invalid_runtime_contract_is_fatal(
        self,
    ):
        cases = (
            object(),
            self.result(
                status=CLAIMED,
                stage=RECOVERY,
            ),
            self.result(
                reasons=(),
            ),
            self.result(
                version="wrong-version",
            ),
        )

        for invalid_result in cases:
            with self.subTest(
                invalid_result=invalid_result,
            ):
                owner = self.owner()

                owner.run_sell_once.return_value = (
                    invalid_result
                )

                with patch(
                    f"{MODULE}.time.time",
                    return_value=2_000.0,
                ):
                    with self.assertRaisesRegex(
                        LiveSellSupervisorServiceError,
                        "^"
                        + LIVE_SELL_SUPERVISOR_RESULT_CONTRACT_INVALID
                        + "$",
                    ):
                        await run_live_sell_supervisor_service(
                            owner=owner,
                            supervisor_config=(
                                self.supervisor_config()
                            ),
                            execution_config=(
                                self.execution_config()
                            ),
                            stop_event=asyncio.Event(),
                        )

                owner.run_sell_once.assert_awaited_once()

    async def test_invalid_clock_fails_before_authority(
        self,
    ):
        owner = self.owner()

        for invalid in (
            0.0,
            -1.0,
            float("nan"),
            float("inf"),
        ):
            with self.subTest(
                invalid=invalid,
            ):
                owner.reset_mock()

                with patch(
                    f"{MODULE}.time.time",
                    return_value=invalid,
                ):
                    with self.assertRaisesRegex(
                        LiveSellSupervisorServiceError,
                        "^"
                        + LIVE_SELL_SUPERVISOR_CLOCK_INVALID
                        + "$",
                    ):
                        await run_live_sell_supervisor_service(
                            owner=owner,
                            supervisor_config=(
                                self.supervisor_config()
                            ),
                            execution_config=(
                                self.execution_config()
                            ),
                            stop_event=asyncio.Event(),
                        )

                owner.run_sell_once.assert_not_awaited()

    async def test_owner_failure_propagates(
        self,
    ):
        owner = self.owner()

        owner.run_sell_once.side_effect = (
            RuntimeError(
                "sell-owner-failure"
            )
        )

        with patch(
            f"{MODULE}.time.time",
            return_value=2_000.0,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^sell-owner-failure$",
            ):
                await run_live_sell_supervisor_service(
                    owner=owner,
                    supervisor_config=(
                        self.supervisor_config()
                    ),
                    execution_config=(
                        self.execution_config()
                    ),
                    stop_event=asyncio.Event(),
                )

    async def test_ticks_are_serial_and_receive_new_time(
        self,
    ):
        owner = self.owner()

        owner.run_sell_once.return_value = (
            self.result()
        )

        wait_mock = AsyncMock(
            side_effect=(
                False,
                True,
            )
        )

        with (
            patch(
                f"{MODULE}.time.time",
                side_effect=(
                    2_000.1,
                    2_001.9,
                ),
            ),
            patch(
                f"{MODULE}."
                "_wait_until_stop_or_interval",
                new=wait_mock,
            ),
        ):
            await run_live_sell_supervisor_service(
                owner=owner,
                supervisor_config=(
                    self.supervisor_config()
                ),
                execution_config=(
                    self.execution_config()
                ),
                stop_event=asyncio.Event(),
            )

        self.assertEqual(
            owner.run_sell_once.await_count,
            2,
        )

        self.assertEqual(
            [
                item.kwargs[
                    "evaluated_at"
                ]
                for item
                in owner.run_sell_once.await_args_list
            ],
            [
                2_000,
                2_001,
            ],
        )

        self.assertEqual(
            wait_mock.await_args_list,
            [
                call(
                    stop_event=(
                        wait_mock.await_args_list[
                            0
                        ].kwargs[
                            "stop_event"
                        ]
                    ),
                    interval_seconds=2.0,
                ),
                call(
                    stop_event=(
                        wait_mock.await_args_list[
                            1
                        ].kwargs[
                            "stop_event"
                        ]
                    ),
                    interval_seconds=2.0,
                ),
            ],
        )

        self.assertIs(
            wait_mock.await_args_list[
                0
            ].kwargs[
                "stop_event"
            ],
            wait_mock.await_args_list[
                1
            ].kwargs[
                "stop_event"
            ],
        )

    async def test_external_cancellation_settles_active_sell_tick(
        self,
    ):
        owner = self.owner()

        started = asyncio.Event()
        release = asyncio.Event()
        lifecycle = []

        async def run_sell_once(
            **kwargs,
        ):
            del kwargs
            started.set()
            await release.wait()
            lifecycle.append(
                "sell-return"
            )
            return self.result()

        owner.run_sell_once.side_effect = (
            run_sell_once
        )

        with patch(
            f"{MODULE}.time.time",
            return_value=2_000.0,
        ):
            service_task = asyncio.create_task(
                run_live_sell_supervisor_service(
                    owner=owner,
                    supervisor_config=(
                        self.supervisor_config()
                    ),
                    execution_config=(
                        self.execution_config()
                    ),
                    stop_event=asyncio.Event(),
                )
            )

            await started.wait()

            service_task.cancel()

            await asyncio.sleep(0)

            self.assertFalse(
                service_task.done()
            )

            release.set()

            with self.assertRaises(
                asyncio.CancelledError
            ):
                await service_task

        self.assertEqual(
            lifecycle,
            [
                "sell-return",
            ],
        )

        owner.run_sell_once.assert_awaited_once()

    async def test_repeated_cancellation_cannot_cancel_active_sell_tick(
        self,
    ):
        owner = self.owner()

        started = asyncio.Event()
        release = asyncio.Event()
        lifecycle = []

        async def run_sell_once(
            **kwargs,
        ):
            del kwargs
            started.set()

            try:
                await release.wait()

            except asyncio.CancelledError:
                lifecycle.append(
                    "sell-cancelled"
                )
                raise

            lifecycle.append(
                "sell-return"
            )

            return self.result()

        owner.run_sell_once.side_effect = (
            run_sell_once
        )

        with patch(
            f"{MODULE}.time.time",
            return_value=2_000.0,
        ):
            service_task = asyncio.create_task(
                run_live_sell_supervisor_service(
                    owner=owner,
                    supervisor_config=(
                        self.supervisor_config()
                    ),
                    execution_config=(
                        self.execution_config()
                    ),
                    stop_event=asyncio.Event(),
                )
            )

            await started.wait()

            service_task.cancel()
            await asyncio.sleep(0)

            self.assertFalse(
                service_task.done()
            )

            service_task.cancel()
            await asyncio.sleep(0)

            self.assertFalse(
                service_task.done()
            )

            self.assertEqual(
                lifecycle,
                [],
            )

            release.set()

            with self.assertRaises(
                asyncio.CancelledError
            ):
                await service_task

        self.assertEqual(
            lifecycle,
            [
                "sell-return",
            ],
        )

        owner.run_sell_once.assert_awaited_once()

    async def test_cancellation_still_validates_settled_result(
        self,
    ):
        owner = self.owner()

        started = asyncio.Event()
        release = asyncio.Event()

        async def run_sell_once(
            **kwargs,
        ):
            del kwargs
            started.set()
            await release.wait()

            return self.result(
                status=CLAIMED,
                stage=RECOVERY,
            )

        owner.run_sell_once.side_effect = (
            run_sell_once
        )

        with patch(
            f"{MODULE}.time.time",
            return_value=2_000.0,
        ):
            service_task = asyncio.create_task(
                run_live_sell_supervisor_service(
                    owner=owner,
                    supervisor_config=(
                        self.supervisor_config()
                    ),
                    execution_config=(
                        self.execution_config()
                    ),
                    stop_event=asyncio.Event(),
                )
            )

            await started.wait()

            service_task.cancel()

            await asyncio.sleep(0)

            self.assertFalse(
                service_task.done()
            )

            release.set()

            with self.assertRaisesRegex(
                LiveSellSupervisorServiceError,
                "^"
                + LIVE_SELL_SUPERVISOR_RESULT_CONTRACT_INVALID
                + "$",
            ):
                await service_task

        owner.run_sell_once.assert_awaited_once()

    async def test_invalid_dependencies_fail_before_authority(
        self,
    ):
        owner = self.owner()
        supervisor_config = (
            self.supervisor_config()
        )
        execution_config = (
            self.execution_config()
        )

        cases = (
            {
                "owner": object(),
                "supervisor_config": (
                    supervisor_config
                ),
                "execution_config": (
                    execution_config
                ),
                "stop_event": asyncio.Event(),
                "expected": (
                    "^owner must be "
                    "LiveProcessOwner$"
                ),
            },
            {
                "owner": owner,
                "supervisor_config": object(),
                "execution_config": (
                    execution_config
                ),
                "stop_event": asyncio.Event(),
                "expected": (
                    "^supervisor_config must be "
                    "LiveSellSupervisorConfig$"
                ),
            },
            {
                "owner": owner,
                "supervisor_config": (
                    supervisor_config
                ),
                "execution_config": object(),
                "stop_event": asyncio.Event(),
                "expected": (
                    "^execution_config must be "
                    "LiveBuyExecutionConfig$"
                ),
            },
            {
                "owner": owner,
                "supervisor_config": (
                    supervisor_config
                ),
                "execution_config": (
                    execution_config
                ),
                "stop_event": object(),
                "expected": (
                    "^stop_event must be "
                    "asyncio.Event$"
                ),
            },
        )

        for case in cases:
            with self.subTest(
                expected=case[
                    "expected"
                ],
            ):
                owner.run_sell_once.reset_mock()

                with self.assertRaisesRegex(
                    TypeError,
                    case["expected"],
                ):
                    await run_live_sell_supervisor_service(
                        owner=case["owner"],
                        supervisor_config=case[
                            "supervisor_config"
                        ],
                        execution_config=case[
                            "execution_config"
                        ],
                        stop_event=case[
                            "stop_event"
                        ],
                    )

                owner.run_sell_once.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
