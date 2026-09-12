from __future__ import annotations

from pathlib import Path
from unittest.mock import (
    AsyncMock,
    patch,
)
import unittest

from src.execution.live_exit_controller import (
    BLOCK as EXIT_BLOCK,
    CLAIMED as EXIT_CLAIMED,
    IDLE as EXIT_IDLE,
    UNKNOWN as EXIT_UNKNOWN,
    LIVE_EXIT_CONTROLLER_VERSION,
    LiveExitControllerResult,
)
from src.execution.live_sell_recovery_executor import (
    ADVANCED as RECOVERY_ADVANCED,
    BLOCK as RECOVERY_BLOCK,
    HOLD as RECOVERY_HOLD,
    IDLE as RECOVERY_IDLE,
    RECONCILED as RECOVERY_RECONCILED,
    UNKNOWN as RECOVERY_UNKNOWN,
    LIVE_SELL_RECOVERY_EXECUTOR_VERSION,
    LiveSellRecoveryExecutionResult,
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
    INIT,
    KILL,
    RECOVERY,
    RECONCILED,
    SIGNED,
    SIGNING,
    UNKNOWN,
    LIVE_SELL_RUNTIME_VERSION,
    run_live_sell_once,
)
from src.execution.live_sell_unexecuted_executor import (
    BLOCK as SIGNING_BLOCK,
    IDLE as SIGNING_IDLE,
    SIGNED as SIGNING_SIGNED,
    UNKNOWN as SIGNING_UNKNOWN,
    LIVE_SELL_UNEXECUTED_EXECUTOR_VERSION,
    LiveSellUnexecutedExecutionResult,
)
from src.strategies.live_exit_policy import (
    LiveExitPolicy,
)


MODULE = (
    "src.execution.live_sell_runtime"
)


class LiveSellRuntimeTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.db_path = Path(
            "/tmp/live-sell-runtime.db"
        )

        self.wallet = (
            "11111111111111111111111111111112"
        )

        self.signer = object()
        self.compute_unit_limit = 250_000

        self.evaluated_at = 2_000

        self.policy = LiveExitPolicy(
            take_profit_return_bps=2_000,
            stop_loss_return_bps=-4_000,
            max_hold_seconds=900,
        )

    def recovery(
        self,
        *,
        status=RECOVERY_IDLE,
        reasons=(),
        version=(
            LIVE_SELL_RECOVERY_EXECUTOR_VERSION
        ),
    ):
        return LiveSellRecoveryExecutionResult(
            executor_version=version,
            status=status,
            reasons=tuple(reasons),
            discovered_candidates=0,
            authorization_sha256=None,
            execution_status=None,
            lifecycle_status=None,
            lifecycle_stage=None,
            transaction_signature=None,
        )

    def signing(
        self,
        *,
        status=SIGNING_IDLE,
        reasons=(),
        version=(
            LIVE_SELL_UNEXECUTED_EXECUTOR_VERSION
        ),
    ):
        return LiveSellUnexecutedExecutionResult(
            executor_version=version,
            status=status,
            reasons=tuple(reasons),
            discovered_candidates=0,
            authorization_sha256=None,
            global_rpc_slot=None,
            blockhash_min_context_slot=None,
            blockhash_rpc_slot=None,
            message_sha256=None,
            pre_sign_status=None,
            signing_status=None,
            transaction_signature=None,
        )

    def controller(
        self,
        *,
        status=EXIT_IDLE,
        reasons=(),
        version=(
            LIVE_EXIT_CONTROLLER_VERSION
        ),
    ):
        return LiveExitControllerResult(
            controller_version=version,
            status=status,
            reasons=tuple(reasons),
            wallet_pubkey=self.wallet,
            evaluated_at=self.evaluated_at,
            valuation=None,
            decisions=None,
            selected_decision=None,
            initiation=None,
        )

    async def run_runtime(
        self,
        *,
        kill_switch=False,
        signer=None,
        compute_unit_limit=None,
        recovery=None,
        signing=None,
        controller=None,
        db_path=None,
    ):
        if signer is None:
            signer = self.signer

        if compute_unit_limit is None:
            compute_unit_limit = (
                self.compute_unit_limit
            )

        if recovery is None:
            recovery = self.recovery()

        if signing is None:
            signing = self.signing()

        if controller is None:
            controller = self.controller()

        if db_path is None:
            db_path = self.db_path

        recovery_mock = AsyncMock(
            return_value=recovery
        )
        signing_mock = AsyncMock(
            return_value=signing
        )
        controller_mock = AsyncMock(
            return_value=controller
        )

        with (
            patch(
                f"{MODULE}."
                "recover_one_live_sell_once",
                recovery_mock,
            ),
            patch(
                f"{MODULE}."
                "sign_one_unexecuted_live_sell_once",
                signing_mock,
            ),
            patch(
                f"{MODULE}."
                "control_live_exit_once",
                controller_mock,
            ),
        ):
            result = await run_live_sell_once(
                kill_switch=kill_switch,
                signer=signer,
                compute_unit_limit=(
                    compute_unit_limit
                ),
                wallet_pubkey=self.wallet,
                evaluated_at=self.evaluated_at,
                policy=self.policy,
                slippage_bps=300,
                base_network_fee_lamports=5_000,
                priority_fee_lamports=0,
                min_context_slot=650,
                db_path=db_path,
            )

        return (
            result,
            recovery_mock,
            signing_mock,
            controller_mock,
        )

    async def test_invalid_kill_switch_stops_before_all_children(
        self,
    ):
        recovery_mock = AsyncMock()
        signing_mock = AsyncMock()
        controller_mock = AsyncMock()

        with (
            patch(
                f"{MODULE}."
                "recover_one_live_sell_once",
                recovery_mock,
            ),
            patch(
                f"{MODULE}."
                "sign_one_unexecuted_live_sell_once",
                signing_mock,
            ),
            patch(
                f"{MODULE}."
                "control_live_exit_once",
                controller_mock,
            ),
        ):
            result = await run_live_sell_once(
                kill_switch=1,
                signer=None,
                compute_unit_limit=None,
                wallet_pubkey=self.wallet,
                evaluated_at=self.evaluated_at,
                policy=self.policy,
                slippage_bps=300,
                base_network_fee_lamports=5_000,
                priority_fee_lamports=0,
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            INIT,
        )
        self.assertIn(
            "LIVE_SELL_RUNTIME_KILL_SWITCH_INVALID",
            result.reasons,
        )

        recovery_mock.assert_not_awaited()
        signing_mock.assert_not_awaited()
        controller_mock.assert_not_awaited()

    async def test_recovery_non_idle_stops_lower_authority(
        self,
    ):
        cases = (
            (
                RECOVERY_ADVANCED,
                ADVANCED,
            ),
            (
                RECOVERY_RECONCILED,
                RECONCILED,
            ),
            (
                RECOVERY_HOLD,
                HOLD,
            ),
            (
                RECOVERY_BLOCK,
                BLOCK,
            ),
            (
                RECOVERY_UNKNOWN,
                UNKNOWN,
            ),
        )

        for (
            child_status,
            expected_status,
        ) in cases:
            with self.subTest(
                status=child_status
            ):
                (
                    result,
                    recovery,
                    signing,
                    controller,
                ) = await self.run_runtime(
                    recovery=self.recovery(
                        status=child_status,
                        reasons=(
                            "CHILD_REASON",
                        ),
                    ),
                )

                self.assertEqual(
                    result.status,
                    expected_status,
                )
                self.assertEqual(
                    result.stage,
                    RECOVERY,
                )

                recovery.assert_awaited_once()
                signing.assert_not_awaited()
                controller.assert_not_awaited()

    async def test_recovery_exception_fails_closed(
        self,
    ):
        recovery_mock = AsyncMock(
            side_effect=RuntimeError(
                "boom"
            )
        )
        signing_mock = AsyncMock()
        controller_mock = AsyncMock()

        with (
            patch(
                f"{MODULE}."
                "recover_one_live_sell_once",
                recovery_mock,
            ),
            patch(
                f"{MODULE}."
                "sign_one_unexecuted_live_sell_once",
                signing_mock,
            ),
            patch(
                f"{MODULE}."
                "control_live_exit_once",
                controller_mock,
            ),
        ):
            result = await run_live_sell_once(
                kill_switch=False,
                signer=self.signer,
                compute_unit_limit=(
                    self.compute_unit_limit
                ),
                wallet_pubkey=self.wallet,
                evaluated_at=self.evaluated_at,
                policy=self.policy,
                slippage_bps=300,
                base_network_fee_lamports=5_000,
                priority_fee_lamports=0,
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            RECOVERY,
        )

        signing_mock.assert_not_awaited()
        controller_mock.assert_not_awaited()

    async def test_recovery_version_mismatch_stops(
        self,
    ):
        (
            result,
            _,
            signing,
            controller,
        ) = await self.run_runtime(
            recovery=self.recovery(
                version="wrong-version",
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            RECOVERY,
        )

        signing.assert_not_awaited()
        controller.assert_not_awaited()

    async def test_kill_is_reconciliation_only_and_requires_no_signer(
        self,
    ):
        recovery_result = self.recovery()

        recovery_mock = AsyncMock(
            return_value=recovery_result
        )
        signing_mock = AsyncMock()
        controller_mock = AsyncMock()

        with (
            patch(
                f"{MODULE}."
                "recover_one_live_sell_once",
                recovery_mock,
            ),
            patch(
                f"{MODULE}."
                "sign_one_unexecuted_live_sell_once",
                signing_mock,
            ),
            patch(
                f"{MODULE}."
                "control_live_exit_once",
                controller_mock,
            ),
        ):
            result = await run_live_sell_once(
                kill_switch=True,
                signer=None,
                compute_unit_limit=None,
                wallet_pubkey="bad downstream wallet",
                evaluated_at=0,
                policy=self.policy,
                slippage_bps=-1,
                base_network_fee_lamports=-1,
                priority_fee_lamports=-1,
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            HALTED,
        )
        self.assertEqual(
            result.stage,
            KILL,
        )
        self.assertIn(
            "LIVE_SELL_RUNTIME_KILL_ACTIVE",
            result.reasons,
        )

        recovery_mock.assert_awaited_once_with(
            allow_submission=False,
            db_path=self.db_path,
        )

        signing_mock.assert_not_awaited()
        controller_mock.assert_not_awaited()

    async def test_normal_recovery_uses_submission_authority(
        self,
    ):
        (
            result,
            recovery,
            _,
            _,
        ) = await self.run_runtime()

        self.assertEqual(
            result.status,
            IDLE,
        )

        recovery.assert_awaited_once_with(
            allow_submission=True,
            db_path=self.db_path,
        )

    async def test_signed_stops_before_new_exit(
        self,
    ):
        (
            result,
            _,
            signing,
            controller,
        ) = await self.run_runtime(
            signing=self.signing(
                status=SIGNING_SIGNED,
                reasons=(
                    "SIGNED_NOW",
                ),
            ),
        )

        self.assertEqual(
            result.status,
            SIGNED,
        )
        self.assertEqual(
            result.stage,
            SIGNING,
        )

        signing.assert_awaited_once()
        controller.assert_not_awaited()

    async def test_signing_block_and_unknown_stop_controller(
        self,
    ):
        cases = (
            (
                SIGNING_BLOCK,
                BLOCK,
            ),
            (
                SIGNING_UNKNOWN,
                UNKNOWN,
            ),
        )

        for (
            child_status,
            expected,
        ) in cases:
            with self.subTest(
                status=child_status
            ):
                (
                    result,
                    _,
                    signing,
                    controller,
                ) = await self.run_runtime(
                    signing=self.signing(
                        status=child_status,
                        reasons=(
                            "SIGN_REASON",
                        ),
                    ),
                )

                self.assertEqual(
                    result.status,
                    expected,
                )
                self.assertEqual(
                    result.stage,
                    SIGNING,
                )

                signing.assert_awaited_once()
                controller.assert_not_awaited()

    async def test_signing_version_mismatch_stops_controller(
        self,
    ):
        (
            result,
            _,
            _,
            controller,
        ) = await self.run_runtime(
            signing=self.signing(
                version="wrong-version",
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            SIGNING,
        )

        controller.assert_not_awaited()

    async def test_signing_exception_stops_controller(
        self,
    ):
        recovery_mock = AsyncMock(
            return_value=self.recovery()
        )
        signing_mock = AsyncMock(
            side_effect=RuntimeError(
                "boom"
            )
        )
        controller_mock = AsyncMock()

        with (
            patch(
                f"{MODULE}."
                "recover_one_live_sell_once",
                recovery_mock,
            ),
            patch(
                f"{MODULE}."
                "sign_one_unexecuted_live_sell_once",
                signing_mock,
            ),
            patch(
                f"{MODULE}."
                "control_live_exit_once",
                controller_mock,
            ),
        ):
            result = await run_live_sell_once(
                kill_switch=False,
                signer=self.signer,
                compute_unit_limit=(
                    self.compute_unit_limit
                ),
                wallet_pubkey=self.wallet,
                evaluated_at=self.evaluated_at,
                policy=self.policy,
                slippage_bps=300,
                base_network_fee_lamports=5_000,
                priority_fee_lamports=0,
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            SIGNING,
        )

        controller_mock.assert_not_awaited()

    async def test_claimed_exit_is_final_action(
        self,
    ):
        (
            result,
            _,
            signing,
            controller,
        ) = await self.run_runtime(
            controller=self.controller(
                status=EXIT_CLAIMED,
                reasons=(
                    "LIVE_EXIT_CLAIMED",
                ),
            ),
        )

        self.assertEqual(
            result.runtime_version,
            LIVE_SELL_RUNTIME_VERSION,
        )
        self.assertEqual(
            result.status,
            CLAIMED,
        )
        self.assertEqual(
            result.stage,
            EXIT,
        )

        signing.assert_awaited_once()
        controller.assert_awaited_once()

    async def test_exit_block_and_unknown_propagate(
        self,
    ):
        cases = (
            (
                EXIT_BLOCK,
                BLOCK,
            ),
            (
                EXIT_UNKNOWN,
                UNKNOWN,
            ),
        )

        for (
            child_status,
            expected,
        ) in cases:
            with self.subTest(
                status=child_status
            ):
                (
                    result,
                    _,
                    _,
                    controller,
                ) = await self.run_runtime(
                    controller=self.controller(
                        status=child_status,
                        reasons=(
                            "EXIT_REASON",
                        ),
                    ),
                )

                self.assertEqual(
                    result.status,
                    expected,
                )
                self.assertEqual(
                    result.stage,
                    EXIT,
                )

                controller.assert_awaited_once()

    async def test_controller_version_mismatch_fails_closed(
        self,
    ):
        (
            result,
            _,
            _,
            _,
        ) = await self.run_runtime(
            controller=self.controller(
                version="wrong-version",
            ),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            EXIT,
        )

    async def test_all_idle_is_runtime_idle(
        self,
    ):
        (
            result,
            recovery,
            signing,
            controller,
        ) = await self.run_runtime()

        self.assertEqual(
            result.status,
            IDLE,
        )
        self.assertEqual(
            result.stage,
            COMPLETE,
        )
        self.assertEqual(
            result.reasons,
            (
                "LIVE_SELL_RUNTIME_IDLE",
            ),
        )

        recovery.assert_awaited_once()
        signing.assert_awaited_once()
        controller.assert_awaited_once()

    async def test_one_database_path_reaches_all_children(
        self,
    ):
        custom_path = Path(
            "/tmp/custom-live-sell-runtime.db"
        )

        (
            result,
            recovery,
            signing,
            controller,
        ) = await self.run_runtime(
            db_path=custom_path,
        )

        self.assertEqual(
            result.status,
            IDLE,
        )

        self.assertEqual(
            recovery.await_args.kwargs[
                "db_path"
            ],
            custom_path,
        )

        self.assertEqual(
            signing.await_args.kwargs[
                "db_path"
            ],
            custom_path,
        )

        self.assertEqual(
            controller.await_args.kwargs[
                "db_path"
            ],
            custom_path,
        )

    async def test_downstream_parameters_are_forwarded_only_when_reached(
        self,
    ):
        (
            _,
            _,
            signing,
            controller,
        ) = await self.run_runtime()

        self.assertEqual(
            signing.await_args.kwargs[
                "signer"
            ],
            self.signer,
        )
        self.assertEqual(
            signing.await_args.kwargs[
                "compute_unit_limit"
            ],
            self.compute_unit_limit,
        )

        self.assertEqual(
            controller.await_args.kwargs[
                "min_context_slot"
            ],
            650,
        )


    async def test_invalid_database_path_stops_before_recovery(
        self,
    ):
        (
            result,
            recovery,
            signing,
            controller,
        ) = await self.run_runtime(
            db_path=object(),
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            INIT,
        )
        self.assertIn(
            "LIVE_SELL_RUNTIME_DATABASE_PATH_INVALID",
            result.reasons,
        )

        recovery.assert_not_awaited()
        signing.assert_not_awaited()
        controller.assert_not_awaited()

    async def test_kill_recovery_work_stops_before_signing(
        self,
    ):
        (
            result,
            recovery,
            signing,
            controller,
        ) = await self.run_runtime(
            kill_switch=True,
            recovery=self.recovery(
                status=RECOVERY_RECONCILED,
                reasons=(
                    "RECONCILED_EXISTING_SELL",
                ),
            ),
        )

        self.assertEqual(
            result.status,
            RECONCILED,
        )
        self.assertEqual(
            result.stage,
            RECOVERY,
        )

        recovery.assert_awaited_once_with(
            allow_submission=False,
            db_path=self.db_path,
        )

        signing.assert_not_awaited()
        controller.assert_not_awaited()

    async def test_exit_controller_exception_fails_closed(
        self,
    ):
        recovery_mock = AsyncMock(
            return_value=self.recovery()
        )
        signing_mock = AsyncMock(
            return_value=self.signing()
        )
        controller_mock = AsyncMock(
            side_effect=RuntimeError(
                "boom"
            )
        )

        with (
            patch(
                f"{MODULE}."
                "recover_one_live_sell_once",
                recovery_mock,
            ),
            patch(
                f"{MODULE}."
                "sign_one_unexecuted_live_sell_once",
                signing_mock,
            ),
            patch(
                f"{MODULE}."
                "control_live_exit_once",
                controller_mock,
            ),
        ):
            result = await run_live_sell_once(
                kill_switch=False,
                signer=self.signer,
                compute_unit_limit=(
                    self.compute_unit_limit
                ),
                wallet_pubkey=self.wallet,
                evaluated_at=self.evaluated_at,
                policy=self.policy,
                slippage_bps=300,
                base_network_fee_lamports=5_000,
                priority_fee_lamports=0,
                min_context_slot=650,
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            EXIT,
        )
        self.assertIn(
            "LIVE_SELL_RUNTIME_EXIT_CONTROLLER_EXCEPTION",
            result.reasons,
        )

        recovery_mock.assert_awaited_once()
        signing_mock.assert_awaited_once()
        controller_mock.assert_awaited_once()



if __name__ == "__main__":
    unittest.main()
