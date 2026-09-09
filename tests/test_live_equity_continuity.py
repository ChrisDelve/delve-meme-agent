import sqlite3
import tempfile
import unittest
from pathlib import Path

from solders.pubkey import Pubkey

from src.portfolio.live_equity_continuity import (
    LIVE_EQUITY_CONTINUITY_VERSION,
    PASS,
    UNKNOWN,
    record_live_equity_continuity,
)
from src.portfolio.live_reservations import (
    SQLITE_INT_MAX,
)
from src.portfolio.live_wallet_valuation import (
    LIVE_WALLET_VALUATION_VERSION,
)


class LiveEquityContinuityTests(
    unittest.TestCase
):
    def setUp(self):
        self.temp_dir = (
            tempfile.TemporaryDirectory()
        )

        self.db_path = (
            Path(
                self.temp_dir.name
            )
            / "delve-live.db"
        )

        self.wallet = str(
            Pubkey.new_unique()
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def record(
        self,
        *,
        equity=1_000_000,
        slot=100,
        observed_at=1_750_000_000.0,
        wallet=None,
        valuation_version=(
            LIVE_WALLET_VALUATION_VERSION
        ),
    ):
        if wallet is None:
            wallet = self.wallet

        return record_live_equity_continuity(
            wallet_pubkey=wallet,
            valuation_version=(
                valuation_version
            ),
            current_equity_lamports=(
                equity
            ),
            wallet_balance_rpc_slot=(
                slot
            ),
            observed_at=observed_at,
            db_path=self.db_path,
        )

    def test_invalid_wallet_does_not_create_database(
        self,
    ):
        result = self.record(
            wallet="not-a-wallet"
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "INVALID_WALLET_PUBKEY",
            result.reasons,
        )

        self.assertFalse(
            self.db_path.exists()
        )

    def test_wrong_valuation_version_does_not_create_database(
        self,
    ):
        result = self.record(
            valuation_version=(
                "wrong-wallet-valuation"
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_WALLET_VALUATION_VERSION_MISMATCH",
            result.reasons,
        )

        self.assertFalse(
            self.db_path.exists()
        )

    def test_invalid_numeric_inputs_fail_before_database(
        self,
    ):
        cases = (
            {
                "equity": -1,
                "reason":
                    "INVALID_CURRENT_EQUITY",
            },
            {
                "equity":
                    SQLITE_INT_MAX + 1,
                "reason":
                    "INVALID_CURRENT_EQUITY",
            },
            {
                "slot": -1,
                "reason":
                    "INVALID_WALLET_BALANCE_RPC_SLOT",
            },
            {
                "slot":
                    SQLITE_INT_MAX + 1,
                "reason":
                    "INVALID_WALLET_BALANCE_RPC_SLOT",
            },
            {
                "observed_at":
                    float("nan"),
                "reason":
                    "INVALID_OBSERVED_AT",
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

                result = self.record(
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

                self.assertFalse(
                    self.db_path.exists()
                )

    def test_first_observation_initializes_all_equity_anchors(
        self,
    ):
        result = self.record(
            equity=2_000_000,
            slot=123,
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertTrue(
            result.changed
        )

        state = result.state

        self.assertIsNotNone(
            state
        )

        self.assertEqual(
            state.continuity_version,
            LIVE_EQUITY_CONTINUITY_VERSION,
        )

        self.assertEqual(
            state.wallet_pubkey,
            self.wallet,
        )

        self.assertEqual(
            state.day_start_equity_lamports,
            2_000_000,
        )

        self.assertEqual(
            state.high_water_equity_lamports,
            2_000_000,
        )

        self.assertEqual(
            state.latest_equity_lamports,
            2_000_000,
        )

        self.assertEqual(
            state.latest_wallet_balance_rpc_slot,
            123,
        )

    def test_same_day_gain_preserves_day_start_and_raises_high_water(
        self,
    ):
        first_time = 1_750_000_000.0

        first = self.record(
            equity=1_000_000,
            slot=100,
            observed_at=first_time,
        )

        second = self.record(
            equity=1_200_000,
            slot=101,
            observed_at=(
                first_time + 60
            ),
        )

        self.assertEqual(
            first.status,
            PASS,
        )

        self.assertEqual(
            second.status,
            PASS,
        )

        self.assertEqual(
            second.state.day_start_equity_lamports,
            1_000_000,
        )

        self.assertEqual(
            second.state.high_water_equity_lamports,
            1_200_000,
        )

        self.assertEqual(
            second.state.latest_equity_lamports,
            1_200_000,
        )

    def test_same_day_drawdown_preserves_high_water(
        self,
    ):
        first_time = 1_750_000_000.0

        self.record(
            equity=1_200_000,
            slot=100,
            observed_at=first_time,
        )

        result = self.record(
            equity=900_000,
            slot=101,
            observed_at=(
                first_time + 60
            ),
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertEqual(
            result.state.day_start_equity_lamports,
            1_200_000,
        )

        self.assertEqual(
            result.state.high_water_equity_lamports,
            1_200_000,
        )

        self.assertEqual(
            result.state.latest_equity_lamports,
            900_000,
        )

    def test_new_utc_day_resets_day_start_but_not_high_water(
        self,
    ):
        first_time = 1_750_000_000.0

        first = self.record(
            equity=1_500_000,
            slot=100,
            observed_at=first_time,
        )

        next_day = self.record(
            equity=1_100_000,
            slot=200,
            observed_at=(
                first_time
                + 86_400
            ),
        )

        self.assertEqual(
            first.status,
            PASS,
        )

        self.assertEqual(
            next_day.status,
            PASS,
        )

        self.assertNotEqual(
            first.state.day_key,
            next_day.state.day_key,
        )

        self.assertEqual(
            next_day.state.day_start_equity_lamports,
            1_100_000,
        )

        self.assertEqual(
            next_day.state.high_water_equity_lamports,
            1_500_000,
        )

    def test_new_day_new_all_time_high_updates_high_water(
        self,
    ):
        first_time = 1_750_000_000.0

        self.record(
            equity=1_500_000,
            slot=100,
            observed_at=first_time,
        )

        result = self.record(
            equity=1_800_000,
            slot=200,
            observed_at=(
                first_time
                + 86_400
            ),
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertEqual(
            result.state.day_start_equity_lamports,
            1_800_000,
        )

        self.assertEqual(
            result.state.high_water_equity_lamports,
            1_800_000,
        )

    def test_exact_replay_is_idempotent(
        self,
    ):
        first = self.record()

        replay = self.record()

        self.assertEqual(
            first.status,
            PASS,
        )

        self.assertEqual(
            replay.status,
            PASS,
        )

        self.assertFalse(
            replay.changed
        )

        self.assertEqual(
            replay.state,
            first.state,
        )

    def test_stale_observation_time_fails_closed(
        self,
    ):
        self.record(
            observed_at=1_750_000_100.0,
            slot=100,
        )

        stale = self.record(
            observed_at=1_750_000_000.0,
            slot=101,
            equity=900_000,
        )

        self.assertEqual(
            stale.status,
            UNKNOWN,
        )

        self.assertIn(
            "STALE_EQUITY_OBSERVATION_TIME",
            stale.reasons,
        )

        self.assertEqual(
            stale.state.latest_equity_lamports,
            1_000_000,
        )

    def test_stale_wallet_balance_slot_fails_closed(
        self,
    ):
        self.record(
            observed_at=1_750_000_000.0,
            slot=200,
        )

        stale = self.record(
            observed_at=1_750_000_100.0,
            slot=199,
            equity=900_000,
        )

        self.assertEqual(
            stale.status,
            UNKNOWN,
        )

        self.assertIn(
            "STALE_WALLET_BALANCE_RPC_SLOT",
            stale.reasons,
        )

        self.assertEqual(
            stale.state.latest_equity_lamports,
            1_000_000,
        )


    def test_high_water_below_day_start_fails_closed(
        self,
    ):
        first = self.record(
            equity=1_500_000,
            slot=100,
        )

        self.assertEqual(
            first.status,
            PASS,
        )

        connection = sqlite3.connect(
            self.db_path
        )

        try:
            connection.execute(
                """
                PRAGMA ignore_check_constraints = ON
                """
            )

            connection.execute(
                """
                UPDATE live_equity_continuity

                SET
                    day_start_equity_lamports = ?,
                    high_water_equity_lamports = ?,
                    latest_equity_lamports = ?

                WHERE wallet_pubkey = ?
                """,
                (
                    1_500_000,
                    1_400_000,
                    1_000_000,
                    self.wallet,
                ),
            )

            connection.commit()

        finally:
            connection.close()

        result = self.record(
            equity=1_100_000,
            slot=101,
            observed_at=1_750_000_100.0,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_EQUITY_HIGH_WATER_INVALID",
            result.reasons,
        )

        self.assertFalse(
            result.changed
        )

    def test_unknown_continuity_version_fails_closed(
        self,
    ):
        first = self.record()

        self.assertEqual(
            first.status,
            PASS,
        )

        connection = sqlite3.connect(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_equity_continuity
                SET continuity_version = ?
                WHERE wallet_pubkey = ?
                """,
                (
                    "unknown-continuity-version",
                    self.wallet,
                ),
            )
            connection.commit()

        finally:
            connection.close()

        result = self.record(
            equity=1_100_000,
            slot=101,
            observed_at=1_750_000_100.0,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_EQUITY_CONTINUITY_VERSION_MISMATCH",
            result.reasons,
        )

    def test_wallets_have_independent_continuity(
        self,
    ):
        other_wallet = str(
            Pubkey.new_unique()
        )

        first = self.record(
            equity=1_000_000,
            slot=100,
        )

        second = self.record(
            wallet=other_wallet,
            equity=2_000_000,
            slot=200,
        )

        self.assertEqual(
            first.status,
            PASS,
        )

        self.assertEqual(
            second.status,
            PASS,
        )

        self.assertEqual(
            first.state.wallet_pubkey,
            self.wallet,
        )

        self.assertEqual(
            second.state.wallet_pubkey,
            other_wallet,
        )

        self.assertEqual(
            first.state.high_water_equity_lamports,
            1_000_000,
        )

        self.assertEqual(
            second.state.high_water_equity_lamports,
            2_000_000,
        )


if __name__ == "__main__":
    unittest.main()
