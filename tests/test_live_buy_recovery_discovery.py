from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from src.execution.live_buy_recovery_discovery import (
    ARMED_SIGNED,
    LIVE_BUY_RECOVERY_DISCOVERY_VERSION,
    PASS,
    PRISTINE_SIGNED,
    SUBMITTED,
    UNKNOWN,
    discover_live_buy_recovery_candidates,
)
from src.execution.pump_execution_simulator import (
    PumpCurveState,
)
from src.portfolio.live_reservations import (
    ACTIVE,
    SIGNED,
    SUBMITTED as RESERVATION_SUBMITTED,
    acknowledge_reservation_submitted,
    arm_reservation_submission,
    bind_reservation_signed_transaction,
    load_capital_reservation_read_only,
    reserve_pump_buy_capital,
)
from src.risk.risk_governor import (
    AccountRiskState,
    RiskPolicy,
)


MODULE = (
    "src.execution.live_buy_recovery_discovery"
)


class LiveBuyRecoveryDiscoveryTests(
    unittest.TestCase
):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()

        self.db_path = (
            Path(
                self.temp_dir.name
            )
            / "live_buy_recovery.db"
        )

        self.wallet_pubkey = str(
            Pubkey.new_unique()
        )

        self.recent_blockhash = str(
            Hash.new_unique()
        )

        self.account = AccountRiskState(
            current_equity_lamports=(
                100_000_000
            ),
            day_start_equity_lamports=(
                100_000_000
            ),
            high_water_equity_lamports=(
                100_000_000
            ),
            open_exposure_lamports=0,
            open_positions=0,
        )

        self.curve = PumpCurveState(
            virtual_quote_reserves=(
                1_000_000_000_000
            ),
            virtual_token_reserves=(
                1_000_000_000_000_000
            ),
            real_quote_reserves=(
                1_000_000_000_000
            ),
            real_token_reserves=(
                1_000_000_000_000_000
            ),
        )

        self.policy = RiskPolicy(
            max_trade_equity_bps=5_000,
            max_total_exposure_bps=9_000,
            max_daily_loss_bps=10_000,
            max_drawdown_bps=10_000,
            max_open_positions=20,
            min_trade_lamports=1_000_000,
            max_size_price_impact_bps=(
                10_000.0
            ),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def reserve(
        self,
    ):
        result = reserve_pump_buy_capital(
            mint=str(
                Pubkey.new_unique()
            ),
            wallet_pubkey=(
                self.wallet_pubkey
            ),
            available_cash_lamports=(
                100_000_000
            ),
            account=self.account,
            curve_state=self.curve,
            protocol_fee_bps=0,
            creator_fee_bps=0,
            slippage_bps=0,
            base_network_fee_lamports=0,
            priority_fee_lamports=0,
            rent_lamports=0,
            reservation_ttl_seconds=60.0,
            policy=self.policy,
            db_path=self.db_path,
        )

        self.assertEqual(
            result.status,
            "PASS",
        )

        self.assertIsNotNone(
            result.reservation
        )

        return result.reservation

    def sign(
        self,
        reservation,
    ):
        signature = str(
            Keypair().sign_message(
                reservation
                .reservation_id
                .encode("utf-8")
            )
        )

        result = (
            bind_reservation_signed_transaction(
                reservation_id=(
                    reservation
                    .reservation_id
                ),
                transaction_signature=(
                    signature
                ),
                signed_message_sha256=(
                    "11" * 32
                ),
                signed_transaction_bytes=(
                    (
                        "signed-buy:"
                        + reservation
                        .reservation_id
                    ).encode(
                        "utf-8"
                    )
                ),
                recent_blockhash=(
                    self.recent_blockhash
                ),
                last_valid_block_height=350,
                blockhash_rpc_slot=200,
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            result.status,
            "PASS",
        )

        self.assertIsNotNone(
            result.reservation
        )

        self.assertEqual(
            result.reservation.status,
            SIGNED,
        )

        return result.reservation

    def arm(
        self,
        reservation,
    ):
        result = arm_reservation_submission(
            reservation_id=(
                reservation.reservation_id
            ),
            db_path=self.db_path,
        )

        self.assertEqual(
            result.status,
            "PASS",
        )

        self.assertIsNotNone(
            result.reservation
        )

        return result.reservation

    def submit(
        self,
        reservation,
    ):
        armed = self.arm(
            reservation
        )

        result = (
            acknowledge_reservation_submitted(
                reservation_id=(
                    armed.reservation_id
                ),
                transaction_signature=(
                    armed.transaction_signature
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            result.status,
            "PASS",
        )

        self.assertIsNotNone(
            result.reservation
        )

        self.assertEqual(
            result.reservation.status,
            RESERVATION_SUBMITTED,
        )

        return result.reservation

    def update(
        self,
        reservation_id: str,
        sql_fragment: str,
        parameters: tuple,
    ):
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            connection.execute(
                (
                    "UPDATE "
                    "live_capital_reservations "
                    f"SET {sql_fragment} "
                    "WHERE reservation_id = ?"
                ),
                (
                    *parameters,
                    reservation_id,
                ),
            )
            connection.commit()

        finally:
            connection.close()

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_BUY_RECOVERY_DISCOVERY_VERSION,
            "live-buy-recovery-discovery-v1",
        )

    def test_missing_database_fails_closed(
        self,
    ):
        missing = (
            Path(
                self.temp_dir.name
            )
            / "missing.db"
        )

        result = (
            discover_live_buy_recovery_candidates(
                db_path=missing
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "LIVE_DATABASE_NOT_FOUND",
            ),
        )

        self.assertEqual(
            result.candidates,
            (),
        )

    def test_missing_table_is_empty_pass_without_mutation(
        self,
    ):
        connection = sqlite3.connect(
            self.db_path
        )
        connection.close()

        result = (
            discover_live_buy_recovery_candidates(
                db_path=self.db_path
            )
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertEqual(
            result.candidates,
            (),
        )

        connection = sqlite3.connect(
            self.db_path
        )

        try:
            row = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'live_capital_reservations'
                """
            ).fetchone()

        finally:
            connection.close()

        self.assertIsNone(
            row
        )

    def test_pristine_signed_is_recovered(
        self,
    ):
        reservation = self.sign(
            self.reserve()
        )

        result = (
            discover_live_buy_recovery_candidates(
                db_path=self.db_path
            )
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertEqual(
            result.scanned_reservation_rows,
            1,
        )

        self.assertEqual(
            result.recovery_reservation_rows,
            1,
        )

        self.assertEqual(
            len(result.candidates),
            1,
        )

        candidate = result.candidates[0]

        self.assertEqual(
            candidate.recovery_state,
            PRISTINE_SIGNED,
        )

        self.assertEqual(
            candidate
            .reservation
            .reservation_id,
            reservation.reservation_id,
        )

    def test_armed_signed_is_recovered(
        self,
    ):
        reservation = self.sign(
            self.reserve()
        )

        armed = self.arm(
            reservation
        )

        result = (
            discover_live_buy_recovery_candidates(
                db_path=self.db_path
            )
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertEqual(
            len(result.candidates),
            1,
        )

        candidate = result.candidates[0]

        self.assertEqual(
            candidate.recovery_state,
            ARMED_SIGNED,
        )

        self.assertEqual(
            candidate
            .reservation
            .reservation_id,
            armed.reservation_id,
        )

    def test_submitted_is_recovered(
        self,
    ):
        submitted = self.submit(
            self.sign(
                self.reserve()
            )
        )

        result = (
            discover_live_buy_recovery_candidates(
                db_path=self.db_path
            )
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertEqual(
            len(result.candidates),
            1,
        )

        candidate = result.candidates[0]

        self.assertEqual(
            candidate.recovery_state,
            SUBMITTED,
        )

        self.assertEqual(
            candidate
            .reservation
            .reservation_id,
            submitted.reservation_id,
        )

    def test_active_reservation_is_excluded_without_expiration(
        self,
    ):
        reservation = self.reserve()

        self.update(
            reservation.reservation_id,
            "expires_at = ?",
            (
                0.0,
            ),
        )

        result = (
            discover_live_buy_recovery_candidates(
                db_path=self.db_path
            )
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertEqual(
            result.candidates,
            (),
        )

        persisted = (
            load_capital_reservation_read_only(
                reservation_id=(
                    reservation.reservation_id
                ),
                db_path=self.db_path,
            )
        )

        self.assertIsNotNone(
            persisted
        )

        self.assertEqual(
            persisted.status,
            ACTIVE,
        )

    def test_malformed_armed_metadata_fails_closed(
        self,
    ):
        valid = self.sign(
            self.reserve()
        )

        malformed = self.sign(
            self.reserve()
        )

        self.update(
            malformed.reservation_id,
            (
                "submission_attempt_count = ?, "
                "submission_started_at = NULL"
            ),
            (
                1,
            ),
        )

        result = (
            discover_live_buy_recovery_candidates(
                db_path=self.db_path
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.candidates,
            (),
        )

        self.assertEqual(
            result.failed_reservation_id,
            malformed.reservation_id,
        )

        self.assertEqual(
            result.reasons,
            (
                "LIVE_BUY_RECOVERY_RESERVATION_INVALID",
            ),
        )

        self.assertNotEqual(
            valid.reservation_id,
            malformed.reservation_id,
        )

    def test_malformed_submitted_metadata_fails_closed(
        self,
    ):
        submitted = self.submit(
            self.sign(
                self.reserve()
            )
        )

        self.update(
            submitted.reservation_id,
            "submission_started_at = NULL",
            (),
        )

        result = (
            discover_live_buy_recovery_candidates(
                db_path=self.db_path
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.candidates,
            (),
        )

        self.assertEqual(
            result.failed_reservation_id,
            submitted.reservation_id,
        )

    def test_incomplete_signed_artifact_fails_closed(
        self,
    ):
        signed = self.sign(
            self.reserve()
        )

        self.update(
            signed.reservation_id,
            "transaction_signature = NULL",
            (),
        )

        result = (
            discover_live_buy_recovery_candidates(
                db_path=self.db_path
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.candidates,
            (),
        )

        self.assertEqual(
            result.failed_reservation_id,
            signed.reservation_id,
        )

    def test_non_buy_unresolved_row_fails_closed(
        self,
    ):
        signed = self.sign(
            self.reserve()
        )

        self.update(
            signed.reservation_id,
            "side = ?",
            (
                "SELL",
            ),
        )

        result = (
            discover_live_buy_recovery_candidates(
                db_path=self.db_path
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "LIVE_BUY_RECOVERY_SIDE_INVALID",
            ),
        )

        self.assertEqual(
            result.candidates,
            (),
        )

    def test_loader_failure_returns_no_partial_candidates(
        self,
    ):
        first = self.sign(
            self.reserve()
        )

        second = self.sign(
            self.reserve()
        )

        real_loader = (
            load_capital_reservation_read_only
        )

        def loader(
            *,
            reservation_id,
            db_path,
        ):
            if (
                reservation_id
                == second.reservation_id
            ):
                return None

            return real_loader(
                reservation_id=reservation_id,
                db_path=db_path,
            )

        with patch(
            (
                f"{MODULE}."
                "load_capital_reservation_read_only"
            ),
            side_effect=loader,
        ):
            result = (
                discover_live_buy_recovery_candidates(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.candidates,
            (),
        )

        self.assertEqual(
            result.failed_reservation_id,
            second.reservation_id,
        )

        self.assertNotEqual(
            first.reservation_id,
            second.reservation_id,
        )

    def test_index_change_during_discovery_fails_closed(
        self,
    ):
        signed = self.sign(
            self.reserve()
        )

        real_loader = (
            load_capital_reservation_read_only
        )

        def loader(
            *,
            reservation_id,
            db_path,
        ):
            reservation = real_loader(
                reservation_id=reservation_id,
                db_path=db_path,
            )

            self.update(
                reservation_id,
                "submission_started_at = ?, "
                "submission_attempt_count = ?",
                (
                    123.0,
                    1,
                ),
            )

            return real_loader(
                reservation_id=reservation_id,
                db_path=db_path,
            )

        with patch(
            (
                f"{MODULE}."
                "load_capital_reservation_read_only"
            ),
            side_effect=loader,
        ):
            result = (
                discover_live_buy_recovery_candidates(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "LIVE_BUY_RECOVERY_INDEX_CHANGED_DURING_DISCOVERY",
            ),
        )

        self.assertEqual(
            result.failed_reservation_id,
            signed.reservation_id,
        )

    def test_priority_and_oldest_ordering(
        self,
    ):
        #
        # This test intentionally needs four concurrent
        # held BUY reservations. Use a smaller per-trade
        # cap so the risk governor legitimately permits
        # the fixture instead of bypassing live-capital
        # reservation authority.
        #
        self.policy = RiskPolicy(
            max_trade_equity_bps=1_000,
            max_total_exposure_bps=9_000,
            max_daily_loss_bps=10_000,
            max_drawdown_bps=10_000,
            max_open_positions=20,
            min_trade_lamports=1_000_000,
            max_size_price_impact_bps=10_000.0,
        )

        pristine = self.sign(
            self.reserve()
        )

        submitted = self.submit(
            self.sign(
                self.reserve()
            )
        )

        armed_newer = self.arm(
            self.sign(
                self.reserve()
            )
        )

        armed_older = self.arm(
            self.sign(
                self.reserve()
            )
        )

        self.update(
            pristine.reservation_id,
            "signed_at = ?",
            (
                1.0,
            ),
        )

        self.update(
            submitted.reservation_id,
            "signed_at = ?",
            (
                2.0,
            ),
        )

        self.update(
            armed_newer.reservation_id,
            "signed_at = ?",
            (
                4.0,
            ),
        )

        self.update(
            armed_older.reservation_id,
            "signed_at = ?",
            (
                3.0,
            ),
        )

        result = (
            discover_live_buy_recovery_candidates(
                db_path=self.db_path
            )
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertEqual(
            [
                candidate.recovery_state
                for candidate
                in result.candidates
            ],
            [
                ARMED_SIGNED,
                ARMED_SIGNED,
                SUBMITTED,
                PRISTINE_SIGNED,
            ],
        )

        self.assertEqual(
            [
                candidate
                .reservation
                .reservation_id
                for candidate
                in result.candidates[:2]
            ],
            [
                armed_older.reservation_id,
                armed_newer.reservation_id,
            ],
        )


if __name__ == "__main__":
    unittest.main()
