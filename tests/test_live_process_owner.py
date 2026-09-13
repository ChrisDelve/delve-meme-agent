from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import (
    AsyncMock,
    Mock,
    call,
    patch,
)

from src.execution.live_authority_gate import (
    LiveAuthorityGate,
)
from src.execution.live_operating_config import (
    LiveOperatingConfig,
)
from src.execution.live_process_authority_lease import (
    LIVE_PROCESS_AUTHORITY_ALREADY_HELD,
    LiveProcessAuthorityLeaseError,
)
from src.execution.live_process_owner import (
    LIVE_PROCESS_OWNER_BUSY,
    LIVE_PROCESS_OWNER_CLOSED,
    LIVE_PROCESS_OWNER_VERSION,
    LiveProcessOwner,
    LiveProcessOwnerError,
)


OWNER_MODULE = (
    "src.execution.live_process_owner"
)


class LiveProcessOwnerTests(
    unittest.IsolatedAsyncioTestCase
):
    @staticmethod
    def config(
    ) -> LiveOperatingConfig:
        return LiveOperatingConfig(
            db_path=Path(
                "/tmp/"
                "delve-process-owner-test.db"
            ),
            recovery_interval_seconds=5.0,
            operational_kill=False,
            max_trade_equity_bps=100,
            max_total_exposure_bps=1000,
            max_daily_loss_bps=500,
            max_drawdown_bps=1000,
            max_open_positions=5,
            min_trade_lamports=1_000_000,
            max_size_price_impact_bps=500,
        )

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_PROCESS_OWNER_VERSION,
            "live-process-owner-v1",
        )

    def test_invalid_config_fails_before_policy_lease_and_gate(
        self,
    ):
        policy = Mock()
        lease_constructor = Mock()
        gate_constructor = Mock()

        with (
            patch(
                f"{OWNER_MODULE}."
                "live_process_authority_lock_path",
                new=policy,
            ),
            patch(
                f"{OWNER_MODULE}."
                "LiveProcessAuthorityLease",
                new=lease_constructor,
            ),
            patch(
                f"{OWNER_MODULE}."
                "LiveAuthorityGate",
                new=gate_constructor,
            ),
        ):
            with self.assertRaisesRegex(
                TypeError,
                "^config must be LiveOperatingConfig$",
            ):
                LiveProcessOwner(
                    config=object()
                )

        policy.assert_not_called()
        lease_constructor.assert_not_called()
        gate_constructor.assert_not_called()
    def test_component_version_mismatch_fails_before_authority(
        self,
    ):
        policy = Mock()
        lease_constructor = Mock()
        gate_constructor = Mock()

        with (
            patch(
                f"{OWNER_MODULE}."
                "LIVE_AUTHORITY_GATE_VERSION",
                new="unexpected-version",
            ),
            patch(
                f"{OWNER_MODULE}."
                "live_process_authority_lock_path",
                new=policy,
            ),
            patch(
                f"{OWNER_MODULE}."
                "LiveProcessAuthorityLease",
                new=lease_constructor,
            ),
            patch(
                f"{OWNER_MODULE}."
                "LiveAuthorityGate",
                new=gate_constructor,
            ),
        ):
            with self.assertRaisesRegex(
                LiveProcessOwnerError,
                (
                    "^LIVE_PROCESS_OWNER_"
                    "COMPONENT_VERSION_MISMATCH$"
                ),
            ):
                LiveProcessOwner(
                    config=self.config()
                )

        policy.assert_not_called()
        lease_constructor.assert_not_called()
        gate_constructor.assert_not_called()


    def test_lease_is_acquired_before_gate_is_constructed(
        self,
    ):
        events = []

        lease = Mock()
        lease.release = Mock()

        gate = LiveAuthorityGate()

        def acquire_lease(
            *,
            lock_path,
        ):
            events.append(
                (
                    "lease",
                    lock_path,
                )
            )
            return lease

        def create_gate(
        ):
            events.append(
                (
                    "gate",
                    None,
                )
            )
            return gate

        lock_path = Path(
            "/tmp/"
            "delve-meme-agent-live-authority.lock"
        )

        with (
            patch(
                f"{OWNER_MODULE}."
                "live_process_authority_lock_path",
                return_value=lock_path,
            ),
            patch(
                f"{OWNER_MODULE}."
                "LiveProcessAuthorityLease",
                side_effect=acquire_lease,
            ),
            patch(
                f"{OWNER_MODULE}."
                "LiveAuthorityGate",
                side_effect=create_gate,
            ),
        ):
            owner = LiveProcessOwner(
                config=self.config()
            )

        self.assertEqual(
            events,
            [
                (
                    "lease",
                    lock_path,
                ),
                (
                    "gate",
                    None,
                ),
            ],
        )

        owner.close()

        lease.release.assert_called_once_with()

    def test_lease_failure_prevents_gate_construction(
        self,
    ):
        gate_constructor = Mock()

        with (
            patch(
                f"{OWNER_MODULE}."
                "LiveProcessAuthorityLease",
                side_effect=(
                    LiveProcessAuthorityLeaseError(
                        LIVE_PROCESS_AUTHORITY_ALREADY_HELD
                    )
                ),
            ),
            patch(
                f"{OWNER_MODULE}."
                "LiveAuthorityGate",
                new=gate_constructor,
            ),
        ):
            with self.assertRaisesRegex(
                LiveProcessAuthorityLeaseError,
                (
                    "^"
                    + LIVE_PROCESS_AUTHORITY_ALREADY_HELD
                    + "$"
                ),
            ):
                LiveProcessOwner(
                    config=self.config()
                )

        gate_constructor.assert_not_called()

    def test_gate_construction_failure_releases_lease(
        self,
    ):
        lease = Mock()
        lease.release = Mock()

        with (
            patch(
                f"{OWNER_MODULE}."
                "LiveProcessAuthorityLease",
                return_value=lease,
            ),
            patch(
                f"{OWNER_MODULE}."
                "LiveAuthorityGate",
                side_effect=RuntimeError(
                    "gate failed"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^gate failed$",
            ):
                LiveProcessOwner(
                    config=self.config()
                )

        lease.release.assert_called_once_with()

    async def test_same_config_and_gate_are_forwarded_everywhere(
        self,
    ):
        config = self.config()

        lease = Mock()
        lease.release = Mock()

        gate = LiveAuthorityGate()

        recovery = AsyncMock(
            return_value=None
        )
        buy = AsyncMock(
            return_value="BUY_RESULT"
        )
        sell = AsyncMock(
            return_value="SELL_RESULT"
        )

        with (
            patch(
                f"{OWNER_MODULE}."
                "LiveProcessAuthorityLease",
                return_value=lease,
            ),
            patch(
                f"{OWNER_MODULE}."
                "LiveAuthorityGate",
                return_value=gate,
            ),
            patch(
                f"{OWNER_MODULE}."
                "run_live_recovery_service",
                new=recovery,
            ),
            patch(
                f"{OWNER_MODULE}."
                "run_production_live_buy_once",
                new=buy,
            ),
            patch(
                f"{OWNER_MODULE}."
                "run_production_live_sell_once",
                new=sell,
            ),
        ):
            owner = LiveProcessOwner(
                config=config
            )

            await owner.run_recovery_service()

            buy_result = await owner.run_buy_once(
                mint="BUY_MINT"
            )

            sell_result = await owner.run_sell_once(
                wallet_pubkey="SELL_WALLET"
            )

            owner.close()

        self.assertEqual(
            buy_result,
            "BUY_RESULT",
        )

        self.assertEqual(
            sell_result,
            "SELL_RESULT",
        )

        recovery.assert_awaited_once()

        self.assertIs(
            recovery.await_args.kwargs[
                "config"
            ],
            config,
        )

        self.assertIs(
            recovery.await_args.kwargs[
                "authority_gate"
            ],
            gate,
        )

        self.assertIs(
            buy.await_args.kwargs[
                "config"
            ],
            config,
        )

        self.assertIs(
            buy.await_args.kwargs[
                "authority_gate"
            ],
            gate,
        )

        self.assertEqual(
            buy.await_args.kwargs[
                "mint"
            ],
            "BUY_MINT",
        )

        self.assertIs(
            sell.await_args.kwargs[
                "config"
            ],
            config,
        )

        self.assertIs(
            sell.await_args.kwargs[
                "authority_gate"
            ],
            gate,
        )

        self.assertEqual(
            sell.await_args.kwargs[
                "wallet_pubkey"
            ],
            "SELL_WALLET",
        )

        lease.release.assert_called_once_with()

    async def test_close_refuses_while_recovery_call_is_active(
        self,
    ):
        entered = asyncio.Event()

        async def recovery(
            **kwargs,
        ):
            entered.set()

            await asyncio.Event().wait()

        lease = Mock()
        lease.release = Mock()

        with (
            patch(
                f"{OWNER_MODULE}."
                "LiveProcessAuthorityLease",
                return_value=lease,
            ),
            patch(
                f"{OWNER_MODULE}."
                "run_live_recovery_service",
                side_effect=recovery,
            ),
        ):
            owner = LiveProcessOwner(
                config=self.config()
            )

            task = asyncio.create_task(
                owner.run_recovery_service()
            )

            await entered.wait()

            with self.assertRaisesRegex(
                LiveProcessOwnerError,
                (
                    "^"
                    + LIVE_PROCESS_OWNER_BUSY
                    + "$"
                ),
            ):
                owner.close()

            lease.release.assert_not_called()
            self.assertFalse(
                owner.closed
            )

            task.cancel()

            with self.assertRaises(
                asyncio.CancelledError
            ):
                await task

            owner.close()

        self.assertTrue(
            owner.closed
        )

        lease.release.assert_called_once_with()

    async def test_close_is_idempotent_and_future_authority_fails_closed(
        self,
    ):
        lease = Mock()
        lease.release = Mock()

        recovery = AsyncMock()
        buy = AsyncMock()
        sell = AsyncMock()

        with (
            patch(
                f"{OWNER_MODULE}."
                "LiveProcessAuthorityLease",
                return_value=lease,
            ),
            patch(
                f"{OWNER_MODULE}."
                "run_live_recovery_service",
                new=recovery,
            ),
            patch(
                f"{OWNER_MODULE}."
                "run_production_live_buy_once",
                new=buy,
            ),
            patch(
                f"{OWNER_MODULE}."
                "run_production_live_sell_once",
                new=sell,
            ),
        ):
            owner = LiveProcessOwner(
                config=self.config()
            )

            owner.close()
            owner.close()

            self.assertTrue(
                owner.closed
            )

            with self.assertRaisesRegex(
                LiveProcessOwnerError,
                (
                    "^"
                    + LIVE_PROCESS_OWNER_CLOSED
                    + "$"
                ),
            ):
                await owner.run_recovery_service()

            with self.assertRaisesRegex(
                LiveProcessOwnerError,
                (
                    "^"
                    + LIVE_PROCESS_OWNER_CLOSED
                    + "$"
                ),
            ):
                await owner.run_buy_once(
                    mint="MINT"
                )

            with self.assertRaisesRegex(
                LiveProcessOwnerError,
                (
                    "^"
                    + LIVE_PROCESS_OWNER_CLOSED
                    + "$"
                ),
            ):
                await owner.run_sell_once(
                    wallet_pubkey="WALLET"
                )

        lease.release.assert_called_once_with()
        recovery.assert_not_awaited()
        buy.assert_not_awaited()
        sell.assert_not_awaited()

    async def test_runtime_exception_unwinds_active_call_and_allows_close(
        self,
    ):
        lease = Mock()
        lease.release = Mock()

        buy = AsyncMock(
            side_effect=RuntimeError(
                "runtime failed"
            )
        )

        with (
            patch(
                f"{OWNER_MODULE}."
                "LiveProcessAuthorityLease",
                return_value=lease,
            ),
            patch(
                f"{OWNER_MODULE}."
                "run_production_live_buy_once",
                new=buy,
            ),
        ):
            owner = LiveProcessOwner(
                config=self.config()
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "^runtime failed$",
            ):
                await owner.run_buy_once(
                    mint="MINT"
                )

            owner.close()

        lease.release.assert_called_once_with()

    def test_context_manager_releases_lease_on_exception(
        self,
    ):
        lease = Mock()
        lease.release = Mock()

        with patch(
            f"{OWNER_MODULE}."
            "LiveProcessAuthorityLease",
            return_value=lease,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^body failed$",
            ):
                with LiveProcessOwner(
                    config=self.config()
                ) as owner:
                    self.assertFalse(
                        owner.closed
                    )

                    raise RuntimeError(
                        "body failed"
                    )

        self.assertTrue(
            owner.closed
        )

        lease.release.assert_called_once_with()

    def test_real_lease_is_held_for_owner_lifetime(
        self,
    ):
        with TemporaryDirectory() as directory:
            lock_path = (
                Path(directory)
                / "owner-authority.lock"
            )

            with patch(
                f"{OWNER_MODULE}."
                "live_process_authority_lock_path",
                return_value=lock_path,
            ):
                first = LiveProcessOwner(
                    config=self.config()
                )

                try:
                    with self.assertRaisesRegex(
                        LiveProcessAuthorityLeaseError,
                        (
                            "^"
                            + LIVE_PROCESS_AUTHORITY_ALREADY_HELD
                            + "$"
                        ),
                    ):
                        LiveProcessOwner(
                            config=self.config()
                        )
                finally:
                    first.close()

                second = LiveProcessOwner(
                    config=self.config()
                )

                self.assertFalse(
                    second.closed
                )

                second.close()

                self.assertTrue(
                    second.closed
                )


if __name__ == "__main__":
    unittest.main()
