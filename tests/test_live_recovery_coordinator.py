import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
)

from src.execution.live_buy_recovery_discovery import (
    ARMED_SIGNED,
    PASS as BUY_DISCOVERY_PASS,
    PRISTINE_SIGNED,
    SUBMITTED as BUY_SUBMITTED,
    UNKNOWN as BUY_DISCOVERY_UNKNOWN,
    LIVE_BUY_RECOVERY_DISCOVERY_VERSION,
)
from src.execution.live_buy_recovery_executor import (
    ADVANCED as BUY_ADVANCED,
    LIVE_BUY_RECOVERY_EXECUTOR_VERSION,
)
from src.execution.live_recovery_coordinator import (
    ADVANCED,
    BLOCK,
    BUY,
    COMPLETE,
    DISCOVERY,
    EXECUTE,
    HOLD,
    IDLE,
    LIVE_RECOVERY_COORDINATOR_VERSION,
    RECONCILED,
    SELL,
    UNKNOWN,
    recover_one_live_obligation_once,
)
from src.execution.live_sell_recovery_discovery import (
    PASS as SELL_DISCOVERY_PASS,
    UNKNOWN as SELL_DISCOVERY_UNKNOWN,
    LIVE_SELL_RECOVERY_DISCOVERY_VERSION,
)
from src.execution.live_sell_recovery_executor import (
    HOLD as SELL_HOLD,
    LIVE_SELL_RECOVERY_EXECUTOR_VERSION,
)
from src.portfolio.live_sell_execution_records import (
    SIGNED as SELL_SIGNED,
    SUBMISSION_ARMED as SELL_SUBMISSION_ARMED,
    SUBMITTED as SELL_SUBMITTED,
)


MODULE = (
    "src.execution.live_recovery_coordinator"
)


class LiveRecoveryCoordinatorTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(
        self,
    ):
        self.db_path = Path(
            "/tmp/live-recovery-coordinator-test.sqlite3"
        )

    def buy_candidate(
        self,
        *,
        state,
        signed_at,
        identity,
    ):
        return SimpleNamespace(
            recovery_state=state,
            reservation=SimpleNamespace(
                signed_at=signed_at,
                reservation_id=identity,
            ),
        )

    def sell_candidate(
        self,
        *,
        status,
        signed_at,
        identity,
    ):
        return SimpleNamespace(
            execution_status=status,
            signed_at=signed_at,
            authorization_sha256=identity,
        )

    def buy_discovery(
        self,
        *,
        status=BUY_DISCOVERY_PASS,
        reasons=(),
        candidates=(),
        version=(
            LIVE_BUY_RECOVERY_DISCOVERY_VERSION
        ),
    ):
        return SimpleNamespace(
            resolver_version=version,
            status=status,
            reasons=tuple(
                reasons
            ),
            candidates=tuple(
                candidates
            ),
        )

    def sell_discovery(
        self,
        *,
        status=SELL_DISCOVERY_PASS,
        reasons=(),
        candidates=(),
        version=(
            LIVE_SELL_RECOVERY_DISCOVERY_VERSION
        ),
    ):
        return SimpleNamespace(
            resolver_version=version,
            status=status,
            reasons=tuple(
                reasons
            ),
            candidates=tuple(
                candidates
            ),
        )

    def child(
        self,
        *,
        version,
        status,
        reasons=(),
    ):
        return SimpleNamespace(
            executor_version=version,
            status=status,
            reasons=tuple(
                reasons
            ),
        )

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_RECOVERY_COORDINATOR_VERSION,
            "live-recovery-coordinator-v1",
        )

    async def test_invalid_allow_submission_stops_before_discovery(
        self,
    ):
        buy_discovery = Mock()
        sell_discovery = Mock()

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                new=buy_discovery,
            ),
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                new=sell_discovery,
            ),
        ):
            result = await recover_one_live_obligation_once(
                allow_submission="yes",
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            DISCOVERY,
        )

        buy_discovery.assert_not_called()
        sell_discovery.assert_not_called()

    async def test_both_empty_is_idle(
        self,
    ):
        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.buy_discovery(),
            ),
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=self.sell_discovery(),
            ),
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=AsyncMock(),
            ) as buy_executor,
            patch(
                f"{MODULE}.recover_one_live_sell_once",
                new=AsyncMock(),
            ) as sell_executor,
        ):
            result = await recover_one_live_obligation_once(
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            IDLE,
        )
        self.assertEqual(
            result.stage,
            COMPLETE,
        )
        self.assertIsNone(
            result.selected_side
        )

        buy_executor.assert_not_awaited()
        sell_executor.assert_not_awaited()

    async def test_buy_discovery_unknown_blocks_sell_execution(
        self,
    ):
        sell = self.sell_candidate(
            status=SELL_SIGNED,
            signed_at=1.0,
            identity="aa" * 32,
        )

        sell_executor = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.buy_discovery(
                    status=BUY_DISCOVERY_UNKNOWN,
                    reasons=("BUY_UNKNOWN",),
                ),
            ),
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=self.sell_discovery(
                    candidates=(sell,),
                ),
            ),
            patch(
                f"{MODULE}.recover_one_live_sell_once",
                new=sell_executor,
            ),
        ):
            result = await recover_one_live_obligation_once(
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "BUY_UNKNOWN",
            result.reasons,
        )

        sell_executor.assert_not_awaited()

    async def test_sell_discovery_unknown_blocks_buy_execution(
        self,
    ):
        buy = self.buy_candidate(
            state=ARMED_SIGNED,
            signed_at=1.0,
            identity="buy-1",
        )

        buy_executor = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.buy_discovery(
                    candidates=(buy,),
                ),
            ),
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=self.sell_discovery(
                    status=SELL_DISCOVERY_UNKNOWN,
                    reasons=("SELL_UNKNOWN",),
                ),
            ),
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=buy_executor,
            ),
        ):
            result = await recover_one_live_obligation_once(
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "SELL_UNKNOWN",
            result.reasons,
        )

        buy_executor.assert_not_awaited()

    async def test_armed_buy_beats_older_pristine_sell(
        self,
    ):
        buy = self.buy_candidate(
            state=ARMED_SIGNED,
            signed_at=20.0,
            identity="buy-armed",
        )

        sell = self.sell_candidate(
            status=SELL_SIGNED,
            signed_at=1.0,
            identity="11" * 32,
        )

        buy_executor = AsyncMock(
            return_value=self.child(
                version=(
                    LIVE_BUY_RECOVERY_EXECUTOR_VERSION
                ),
                status=BUY_ADVANCED,
                reasons=("BUY_ADVANCED",),
            )
        )

        sell_executor = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.buy_discovery(
                    candidates=(buy,),
                ),
            ),
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=self.sell_discovery(
                    candidates=(sell,),
                ),
            ),
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=buy_executor,
            ),
            patch(
                f"{MODULE}.recover_one_live_sell_once",
                new=sell_executor,
            ),
        ):
            result = await recover_one_live_obligation_once(
                db_path=self.db_path,
            )

        self.assertEqual(
            result.selected_side,
            BUY,
        )
        self.assertEqual(
            result.selected_state,
            ARMED_SIGNED,
        )
        self.assertEqual(
            result.status,
            ADVANCED,
        )

        buy_executor.assert_awaited_once_with(
            allow_submission=True,
            db_path=self.db_path,
        )
        sell_executor.assert_not_awaited()

    async def test_submitted_sell_beats_pristine_buy(
        self,
    ):
        buy = self.buy_candidate(
            state=PRISTINE_SIGNED,
            signed_at=1.0,
            identity="buy-pristine",
        )

        sell = self.sell_candidate(
            status=SELL_SUBMITTED,
            signed_at=20.0,
            identity="22" * 32,
        )

        sell_executor = AsyncMock(
            return_value=self.child(
                version=(
                    LIVE_SELL_RECOVERY_EXECUTOR_VERSION
                ),
                status=SELL_HOLD,
                reasons=("SELL_HOLD",),
            )
        )

        buy_executor = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.buy_discovery(
                    candidates=(buy,),
                ),
            ),
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=self.sell_discovery(
                    candidates=(sell,),
                ),
            ),
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=buy_executor,
            ),
            patch(
                f"{MODULE}.recover_one_live_sell_once",
                new=sell_executor,
            ),
        ):
            result = await recover_one_live_obligation_once(
                db_path=self.db_path,
            )

        self.assertEqual(
            result.selected_side,
            SELL,
        )
        self.assertEqual(
            result.selected_state,
            SELL_SUBMITTED,
        )
        self.assertEqual(
            result.status,
            HOLD,
        )

        sell_executor.assert_awaited_once_with(
            allow_submission=True,
            db_path=self.db_path,
        )
        buy_executor.assert_not_awaited()

    async def test_same_priority_uses_oldest_signed_at(
        self,
    ):
        buy = self.buy_candidate(
            state=BUY_SUBMITTED,
            signed_at=30.0,
            identity="buy-submitted",
        )

        sell = self.sell_candidate(
            status=SELL_SUBMITTED,
            signed_at=10.0,
            identity="33" * 32,
        )

        sell_executor = AsyncMock(
            return_value=self.child(
                version=(
                    LIVE_SELL_RECOVERY_EXECUTOR_VERSION
                ),
                status=SELL_HOLD,
            )
        )

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.buy_discovery(
                    candidates=(buy,),
                ),
            ),
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=self.sell_discovery(
                    candidates=(sell,),
                ),
            ),
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=AsyncMock(),
            ),
            patch(
                f"{MODULE}.recover_one_live_sell_once",
                new=sell_executor,
            ),
        ):
            result = await recover_one_live_obligation_once(
                db_path=self.db_path,
            )

        self.assertEqual(
            result.selected_side,
            SELL,
        )
        self.assertEqual(
            result.selected_signed_at,
            10.0,
        )

    async def test_kill_mode_mirrors_sell_relayed_preference(
        self,
    ):
        oldest_pristine = self.sell_candidate(
            status=SELL_SIGNED,
            signed_at=1.0,
            identity="44" * 32,
        )

        newer_armed = self.sell_candidate(
            status=SELL_SUBMISSION_ARMED,
            signed_at=20.0,
            identity="55" * 32,
        )

        sell_executor = AsyncMock(
            return_value=self.child(
                version=(
                    LIVE_SELL_RECOVERY_EXECUTOR_VERSION
                ),
                status=SELL_HOLD,
            )
        )

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.buy_discovery(),
            ),
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=self.sell_discovery(
                    candidates=(
                        oldest_pristine,
                        newer_armed,
                    ),
                ),
            ),
            patch(
                f"{MODULE}.recover_one_live_sell_once",
                new=sell_executor,
            ),
        ):
            result = await recover_one_live_obligation_once(
                allow_submission=False,
                db_path=self.db_path,
            )

        self.assertEqual(
            result.selected_side,
            SELL,
        )
        self.assertEqual(
            result.selected_state,
            SELL_SUBMISSION_ARMED,
        )
        self.assertEqual(
            result.selected_identity,
            "55" * 32,
        )

        sell_executor.assert_awaited_once_with(
            allow_submission=False,
            db_path=self.db_path,
        )

    async def test_normal_mode_mirrors_sell_oldest_preference(
        self,
    ):
        oldest_pristine = self.sell_candidate(
            status=SELL_SIGNED,
            signed_at=1.0,
            identity="66" * 32,
        )

        newer_armed = self.sell_candidate(
            status=SELL_SUBMISSION_ARMED,
            signed_at=20.0,
            identity="77" * 32,
        )

        sell_executor = AsyncMock(
            return_value=self.child(
                version=(
                    LIVE_SELL_RECOVERY_EXECUTOR_VERSION
                ),
                status=SELL_HOLD,
            )
        )

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.buy_discovery(),
            ),
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=self.sell_discovery(
                    candidates=(
                        oldest_pristine,
                        newer_armed,
                    ),
                ),
            ),
            patch(
                f"{MODULE}.recover_one_live_sell_once",
                new=sell_executor,
            ),
        ):
            result = await recover_one_live_obligation_once(
                allow_submission=True,
                db_path=self.db_path,
            )

        self.assertEqual(
            result.selected_side,
            SELL,
        )
        self.assertEqual(
            result.selected_state,
            SELL_SIGNED,
        )
        self.assertEqual(
            result.selected_identity,
            "66" * 32,
        )

    async def test_only_one_recovery_executor_is_invoked(
        self,
    ):
        buy = self.buy_candidate(
            state=ARMED_SIGNED,
            signed_at=1.0,
            identity="buy-only",
        )

        sell = self.sell_candidate(
            status=SELL_SUBMISSION_ARMED,
            signed_at=2.0,
            identity="88" * 32,
        )

        buy_executor = AsyncMock(
            return_value=self.child(
                version=(
                    LIVE_BUY_RECOVERY_EXECUTOR_VERSION
                ),
                status=BUY_ADVANCED,
            )
        )

        sell_executor = AsyncMock()

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.buy_discovery(
                    candidates=(buy,),
                ),
            ),
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=self.sell_discovery(
                    candidates=(sell,),
                ),
            ),
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=buy_executor,
            ),
            patch(
                f"{MODULE}.recover_one_live_sell_once",
                new=sell_executor,
            ),
        ):
            result = await recover_one_live_obligation_once(
                db_path=self.db_path,
            )

        self.assertEqual(
            result.selected_side,
            BUY,
        )

        buy_executor.assert_awaited_once()
        sell_executor.assert_not_awaited()

    async def test_malformed_child_fails_closed(
        self,
    ):
        buy = self.buy_candidate(
            state=ARMED_SIGNED,
            signed_at=1.0,
            identity="buy-malformed",
        )

        with (
            patch(
                f"{MODULE}.discover_live_buy_recovery_candidates",
                return_value=self.buy_discovery(
                    candidates=(buy,),
                ),
            ),
            patch(
                f"{MODULE}.discover_live_sell_recovery_candidates",
                return_value=self.sell_discovery(),
            ),
            patch(
                f"{MODULE}.recover_one_live_buy_once",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        executor_version="wrong",
                        status=BUY_ADVANCED,
                        reasons=(),
                    )
                ),
            ),
        ):
            result = await recover_one_live_obligation_once(
                db_path=self.db_path,
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            EXECUTE,
        )
        self.assertIn(
            "LIVE_BUY_RECOVERY_EXECUTOR_CONTRACT_INVALID",
            result.reasons,
        )


if __name__ == "__main__":
    unittest.main()
