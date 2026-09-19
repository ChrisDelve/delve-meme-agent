from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
)

from solders.pubkey import Pubkey

from src.execution.live_buy_execution_config import (
    LiveBuyExecutionConfig,
)
from src.execution.live_operating_config import (
    LiveOperatingConfig,
)
from src.execution.live_startup_preflight import (
    LIVE_STARTUP_PREFLIGHT_DATABASE_UNAVAILABLE,
    LIVE_STARTUP_PREFLIGHT_RPC_CLUSTER_MISMATCH,
    LIVE_STARTUP_PREFLIGHT_RPC_RESULT_INVALID,
    LIVE_STARTUP_PREFLIGHT_RPC_UNAVAILABLE,
    LIVE_STARTUP_PREFLIGHT_SIGNER_UNAVAILABLE,
    LIVE_STARTUP_PREFLIGHT_SIGNER_WALLET_MISMATCH,
    LIVE_STARTUP_PREFLIGHT_VERSION,
    SOLANA_MAINNET_GENESIS_HASH,
    LiveStartupPreflightError,
    _probe_live_database,
    run_live_startup_preflight,
)


MODULE = (
    "src.execution.live_startup_preflight"
)


def operating_config(
    *,
    db_path: Path,
    operational_kill: bool,
) -> LiveOperatingConfig:
    return LiveOperatingConfig(
        db_path=db_path,
        recovery_interval_seconds=1.0,
        operational_kill=operational_kill,
        max_trade_equity_bps=100,
        max_total_exposure_bps=100,
        max_daily_loss_bps=100,
        max_drawdown_bps=100,
        max_open_positions=1,
        min_trade_lamports=1,
        max_size_price_impact_bps=100.0,
    )


def execution_config(
    *,
    wallet_pubkey: str,
) -> LiveBuyExecutionConfig:
    return LiveBuyExecutionConfig(
        config_version="live-buy-execution-config-v1",
        wallet_pubkey=wallet_pubkey,
        protected_cash_lamports=0,
        buy_slippage_bps=100,
        buy_base_network_fee_lamports=5000,
        buy_priority_fee_lamports=0,
        buy_rent_lamports=0,
        exit_slippage_bps=100,
        exit_base_network_fee_lamports=5000,
        exit_priority_fee_lamports=0,
        reservation_ttl_seconds=30.0,
        max_authorization_age_seconds=30.0,
        compute_unit_limit=200_000,
    )


def resolved_balance(
    *,
    wallet_pubkey: str,
    balance_lamports: int = 123,
    rpc_slot: int = 456,
):
    return SimpleNamespace(
        status="RESOLVED",
        reasons=(),
        wallet_pubkey=wallet_pubkey,
        balance_lamports=balance_lamports,
        rpc_slot=rpc_slot,
    )


class LiveStartupPreflightTests(
    unittest.IsolatedAsyncioTestCase
):
    def test_version_and_mainnet_identity_are_locked(
        self,
    ):
        self.assertEqual(
            LIVE_STARTUP_PREFLIGHT_VERSION,
            "live-startup-preflight-v1",
        )

        self.assertEqual(
            SOLANA_MAINNET_GENESIS_HASH,
            (
                "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
                "Kuc147dw2N9d"
            ),
        )

    def test_database_probe_creates_only_empty_healthy_database(
        self,
    ):
        with TemporaryDirectory() as temp_dir:
            db_path = (
                Path(temp_dir)
                / "nested"
                / "live.db"
            )

            resolved = (
                _probe_live_database(
                    db_path
                )
            )

            self.assertEqual(
                resolved,
                db_path.resolve(),
            )

            self.assertTrue(
                db_path.is_file()
            )

            connection = sqlite3.connect(
                db_path
            )

            try:
                tables = connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                    ORDER BY name
                    """
                ).fetchall()

                quick_check = (
                    connection.execute(
                        "PRAGMA quick_check"
                    ).fetchone()
                )

            finally:
                connection.close()

            self.assertEqual(
                tables,
                [],
            )

            self.assertIsNotNone(
                quick_check
            )

            self.assertEqual(
                quick_check[0],
                "ok",
            )

    async def test_operational_kill_skips_signer_but_runs_db_and_rpc(
        self,
    ):
        with TemporaryDirectory() as temp_dir:
            wallet = str(
                Pubkey.new_unique()
            )

            db_path = (
                Path(temp_dir)
                / "live.db"
            )

            signer = Mock()
            rpc_probe = AsyncMock(
                return_value=(
                    SOLANA_MAINNET_GENESIS_HASH,
                    resolved_balance(
                        wallet_pubkey=wallet
                    ),
                )
            )

            with (
                patch(
                    f"{MODULE}."
                    "LazyEnvironmentMessageSigner",
                    new=signer,
                ),
                patch(
                    f"{MODULE}."
                    "_resolve_rpc_probe",
                    new=rpc_probe,
                ),
            ):
                result = (
                    await run_live_startup_preflight(
                        operating_config=(
                            operating_config(
                                db_path=db_path,
                                operational_kill=True,
                            )
                        ),
                        execution_config=(
                            execution_config(
                                wallet_pubkey=wallet
                            )
                        ),
                    )
                )

            signer.assert_not_called()

            rpc_probe.assert_awaited_once_with(
                wallet_pubkey=wallet
            )

            self.assertFalse(
                result.signer_checked
            )

            self.assertIsNone(
                result.signer_pubkey
            )

    async def test_live_mode_requires_exact_signer_identity(
        self,
    ):
        with TemporaryDirectory() as temp_dir:
            wallet = str(
                Pubkey.new_unique()
            )

            signer_instance = Mock()
            signer_instance.pubkey.return_value = (
                Pubkey.from_string(
                    wallet
                )
            )

            signer_factory = Mock(
                return_value=signer_instance
            )

            rpc_probe = AsyncMock(
                return_value=(
                    SOLANA_MAINNET_GENESIS_HASH,
                    resolved_balance(
                        wallet_pubkey=wallet
                    ),
                )
            )

            with (
                patch(
                    f"{MODULE}."
                    "LazyEnvironmentMessageSigner",
                    new=signer_factory,
                ),
                patch(
                    f"{MODULE}."
                    "_resolve_rpc_probe",
                    new=rpc_probe,
                ),
            ):
                result = (
                    await run_live_startup_preflight(
                        operating_config=(
                            operating_config(
                                db_path=(
                                    Path(temp_dir)
                                    / "live.db"
                                ),
                                operational_kill=False,
                            )
                        ),
                        execution_config=(
                            execution_config(
                                wallet_pubkey=wallet
                            )
                        ),
                    )
                )

            signer_factory.assert_called_once_with()
            signer_instance.pubkey.assert_called_once_with()

            self.assertTrue(
                result.signer_checked
            )

            self.assertEqual(
                result.signer_pubkey,
                wallet,
            )

    async def test_signer_failure_is_sanitized_before_db_or_rpc(
        self,
    ):
        with TemporaryDirectory() as temp_dir:
            wallet = str(
                Pubkey.new_unique()
            )

            signer_instance = Mock()
            signer_instance.pubkey.side_effect = (
                RuntimeError(
                    "secret parser detail"
                )
            )

            db_probe = Mock()
            rpc_probe = AsyncMock()

            with (
                patch(
                    f"{MODULE}."
                    "LazyEnvironmentMessageSigner",
                    return_value=signer_instance,
                ),
                patch(
                    f"{MODULE}."
                    "_probe_live_database",
                    new=db_probe,
                ),
                patch(
                    f"{MODULE}."
                    "_resolve_rpc_probe",
                    new=rpc_probe,
                ),
            ):
                with self.assertRaisesRegex(
                    LiveStartupPreflightError,
                    (
                        "^"
                        + LIVE_STARTUP_PREFLIGHT_SIGNER_UNAVAILABLE
                        + "$"
                    ),
                ):
                    await run_live_startup_preflight(
                        operating_config=(
                            operating_config(
                                db_path=(
                                    Path(temp_dir)
                                    / "live.db"
                                ),
                                operational_kill=False,
                            )
                        ),
                        execution_config=(
                            execution_config(
                                wallet_pubkey=wallet
                            )
                        ),
                    )

            db_probe.assert_not_called()
            rpc_probe.assert_not_awaited()

    async def test_signer_mismatch_fails_before_db_or_rpc(
        self,
    ):
        with TemporaryDirectory() as temp_dir:
            wallet = str(
                Pubkey.new_unique()
            )

            wrong_wallet = str(
                Pubkey.new_unique()
            )

            signer_instance = Mock()
            signer_instance.pubkey.return_value = (
                Pubkey.from_string(
                    wrong_wallet
                )
            )

            db_probe = Mock()
            rpc_probe = AsyncMock()

            with (
                patch(
                    f"{MODULE}."
                    "LazyEnvironmentMessageSigner",
                    return_value=signer_instance,
                ),
                patch(
                    f"{MODULE}."
                    "_probe_live_database",
                    new=db_probe,
                ),
                patch(
                    f"{MODULE}."
                    "_resolve_rpc_probe",
                    new=rpc_probe,
                ),
            ):
                with self.assertRaisesRegex(
                    LiveStartupPreflightError,
                    (
                        "^"
                        + LIVE_STARTUP_PREFLIGHT_SIGNER_WALLET_MISMATCH
                        + "$"
                    ),
                ):
                    await run_live_startup_preflight(
                        operating_config=(
                            operating_config(
                                db_path=(
                                    Path(temp_dir)
                                    / "live.db"
                                ),
                                operational_kill=False,
                            )
                        ),
                        execution_config=(
                            execution_config(
                                wallet_pubkey=wallet
                            )
                        ),
                    )

            db_probe.assert_not_called()
            rpc_probe.assert_not_awaited()

    async def test_database_failure_prevents_rpc(
        self,
    ):
        wallet = str(
            Pubkey.new_unique()
        )

        rpc_probe = AsyncMock()

        with (
            patch(
                f"{MODULE}."
                "_probe_live_database",
                side_effect=LiveStartupPreflightError(
                    LIVE_STARTUP_PREFLIGHT_DATABASE_UNAVAILABLE
                ),
            ),
            patch(
                f"{MODULE}."
                "_resolve_rpc_probe",
                new=rpc_probe,
            ),
        ):
            with self.assertRaisesRegex(
                LiveStartupPreflightError,
                (
                    "^"
                    + LIVE_STARTUP_PREFLIGHT_DATABASE_UNAVAILABLE
                    + "$"
                ),
            ):
                await run_live_startup_preflight(
                    operating_config=(
                        operating_config(
                            db_path=Path(
                                "/unusable/live.db"
                            ),
                            operational_kill=True,
                        )
                    ),
                    execution_config=(
                        execution_config(
                            wallet_pubkey=wallet
                        )
                    ),
                )

        rpc_probe.assert_not_awaited()

    async def test_rpc_exception_is_sanitized(
        self,
    ):
        with TemporaryDirectory() as temp_dir:
            wallet = str(
                Pubkey.new_unique()
            )

            with patch(
                f"{MODULE}."
                "_resolve_rpc_probe",
                new=AsyncMock(
                    side_effect=RuntimeError(
                        "provider secret detail"
                    )
                ),
            ):
                with self.assertRaisesRegex(
                    LiveStartupPreflightError,
                    (
                        "^"
                        + LIVE_STARTUP_PREFLIGHT_RPC_UNAVAILABLE
                        + "$"
                    ),
                ):
                    await run_live_startup_preflight(
                        operating_config=(
                            operating_config(
                                db_path=(
                                    Path(temp_dir)
                                    / "live.db"
                                ),
                                operational_kill=True,
                            )
                        ),
                        execution_config=(
                            execution_config(
                                wallet_pubkey=wallet
                            )
                        ),
                    )

    async def test_wrong_cluster_fails_closed(
        self,
    ):
        with TemporaryDirectory() as temp_dir:
            wallet = str(
                Pubkey.new_unique()
            )

            with patch(
                f"{MODULE}."
                "_resolve_rpc_probe",
                new=AsyncMock(
                    return_value=(
                        "wrong-genesis",
                        resolved_balance(
                            wallet_pubkey=wallet
                        ),
                    )
                ),
            ):
                with self.assertRaisesRegex(
                    LiveStartupPreflightError,
                    (
                        "^"
                        + LIVE_STARTUP_PREFLIGHT_RPC_CLUSTER_MISMATCH
                        + "$"
                    ),
                ):
                    await run_live_startup_preflight(
                        operating_config=(
                            operating_config(
                                db_path=(
                                    Path(temp_dir)
                                    / "live.db"
                                ),
                                operational_kill=True,
                            )
                        ),
                        execution_config=(
                            execution_config(
                                wallet_pubkey=wallet
                            )
                        ),
                    )

    async def test_malformed_balance_result_fails_closed(
        self,
    ):
        with TemporaryDirectory() as temp_dir:
            wallet = str(
                Pubkey.new_unique()
            )

            malformed = SimpleNamespace(
                status="UNKNOWN",
                reasons=(
                    "BALANCE_RPC_FAILED",
                ),
                wallet_pubkey=wallet,
                balance_lamports=None,
                rpc_slot=None,
            )

            with patch(
                f"{MODULE}."
                "_resolve_rpc_probe",
                new=AsyncMock(
                    return_value=(
                        SOLANA_MAINNET_GENESIS_HASH,
                        malformed,
                    )
                ),
            ):
                with self.assertRaisesRegex(
                    LiveStartupPreflightError,
                    (
                        "^"
                        + LIVE_STARTUP_PREFLIGHT_RPC_RESULT_INVALID
                        + "$"
                    ),
                ):
                    await run_live_startup_preflight(
                        operating_config=(
                            operating_config(
                                db_path=(
                                    Path(temp_dir)
                                    / "live.db"
                                ),
                                operational_kill=True,
                            )
                        ),
                        execution_config=(
                            execution_config(
                                wallet_pubkey=wallet
                            )
                        ),
                    )

    async def test_success_returns_exact_startup_evidence(
        self,
    ):
        with TemporaryDirectory() as temp_dir:
            wallet = str(
                Pubkey.new_unique()
            )

            db_path = (
                Path(temp_dir)
                / "live.db"
            )

            rpc_probe = AsyncMock(
                return_value=(
                    SOLANA_MAINNET_GENESIS_HASH,
                    resolved_balance(
                        wallet_pubkey=wallet,
                        balance_lamports=777,
                        rpc_slot=999,
                    ),
                )
            )

            with patch(
                f"{MODULE}."
                "_resolve_rpc_probe",
                new=rpc_probe,
            ):
                result = (
                    await run_live_startup_preflight(
                        operating_config=(
                            operating_config(
                                db_path=db_path,
                                operational_kill=True,
                            )
                        ),
                        execution_config=(
                            execution_config(
                                wallet_pubkey=wallet
                            )
                        ),
                    )
                )

            self.assertEqual(
                result.preflight_version,
                LIVE_STARTUP_PREFLIGHT_VERSION,
            )

            self.assertEqual(
                result.wallet_pubkey,
                wallet,
            )

            self.assertEqual(
                result.database_path,
                db_path.resolve(),
            )

            self.assertTrue(
                result.operational_kill
            )

            self.assertFalse(
                result.signer_checked
            )

            self.assertEqual(
                result.rpc_genesis_hash,
                SOLANA_MAINNET_GENESIS_HASH,
            )

            self.assertEqual(
                result.wallet_balance_lamports,
                777,
            )

            self.assertEqual(
                result.rpc_slot,
                999,
            )


if __name__ == "__main__":
    unittest.main()
