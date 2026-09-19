from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import (
    Mock,
    patch,
)

from src.execution.live_operating_config import (
    LiveOperatingConfig,
)
from src.execution.live_startup_preflight import (
    LIVE_STARTUP_PREFLIGHT_DATABASE_INTEGRITY_FAILED,
    LIVE_STARTUP_PREFLIGHT_DATABASE_UNAVAILABLE,
    LIVE_STARTUP_PREFLIGHT_VERSION,
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
    operational_kill: bool = False,
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


class LiveStartupPreflightTests(
    unittest.IsolatedAsyncioTestCase
):
    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_STARTUP_PREFLIGHT_VERSION,
            "live-startup-preflight-v2",
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

    async def test_success_returns_only_database_readiness_evidence(
        self,
    ):
        with TemporaryDirectory() as temp_dir:
            db_path = (
                Path(temp_dir)
                / "live.db"
            )

            result = (
                await run_live_startup_preflight(
                    operating_config=(
                        operating_config(
                            db_path=db_path,
                            operational_kill=False,
                        )
                    )
                )
            )

            self.assertEqual(
                result.preflight_version,
                LIVE_STARTUP_PREFLIGHT_VERSION,
            )

            self.assertEqual(
                result.database_path,
                db_path.resolve(),
            )

            self.assertEqual(
                tuple(
                    result.__dataclass_fields__
                ),
                (
                    "preflight_version",
                    "database_path",
                ),
            )

    async def test_operational_kill_does_not_change_structural_preflight(
        self,
    ):
        with TemporaryDirectory() as temp_dir:
            db_path = (
                Path(temp_dir)
                / "live.db"
            )

            result = (
                await run_live_startup_preflight(
                    operating_config=(
                        operating_config(
                            db_path=db_path,
                            operational_kill=True,
                        )
                    )
                )
            )

            self.assertEqual(
                result.database_path,
                db_path.resolve(),
            )

    async def test_invalid_operating_config_fails_before_database_probe(
        self,
    ):
        db_probe = Mock()

        with patch(
            f"{MODULE}._probe_live_database",
            new=db_probe,
        ):
            with self.assertRaisesRegex(
                TypeError,
                "^operating_config must be "
                "LiveOperatingConfig$",
            ):
                await run_live_startup_preflight(
                    operating_config=object()
                )

        db_probe.assert_not_called()

    async def test_database_failure_is_sanitized_and_process_fatal(
        self,
    ):
        with patch(
            f"{MODULE}._probe_live_database",
            side_effect=LiveStartupPreflightError(
                LIVE_STARTUP_PREFLIGHT_DATABASE_UNAVAILABLE
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
                            )
                        )
                    )
                )

    def test_database_integrity_failure_is_preserved(
        self,
    ):
        connection = Mock()
        connection.in_transaction = True

        cursor = Mock()
        cursor.fetchall.return_value = [
            (
                "not ok",
            )
        ]

        connection.execute.side_effect = [
            None,
            cursor,
        ]

        with patch(
            f"{MODULE}.get_connection",
            return_value=connection,
        ):
            with self.assertRaisesRegex(
                LiveStartupPreflightError,
                (
                    "^"
                    + LIVE_STARTUP_PREFLIGHT_DATABASE_INTEGRITY_FAILED
                    + "$"
                ),
            ):
                _probe_live_database(
                    Path(
                        "/tmp/live.db"
                    )
                )

        connection.rollback.assert_called()
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
