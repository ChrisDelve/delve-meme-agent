from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.execution.pump_execution_simulator import (
    PumpCurveState,
)
from src.portfolio.live_reservations import (
    ACTIVE,
    EXPIRED,
    SUBMITTED,
    get_connection,
    init_schema,
    reserve_pump_buy_capital,
)
from src.risk.risk_governor import (
    AccountRiskState,
    RiskPolicy,
)


class LiveReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()

        self.db_path = (
            Path(self.temp_dir.name)
            / "live_reservations.db"
        )

        self.account = AccountRiskState(
            current_equity_lamports=100_000_000,
            day_start_equity_lamports=100_000_000,
            high_water_equity_lamports=100_000_000,
            open_exposure_lamports=0,
            open_positions=0,
        )

        #
        # Deliberately deep synthetic curve so these
        # tests exercise portfolio-capacity logic
        # rather than liquidity constraints.
        #
        self.curve = PumpCurveState(
            virtual_quote_reserves=1_000_000_000_000,
            virtual_token_reserves=1_000_000_000_000_000,
            real_quote_reserves=1_000_000_000_000,
            real_token_reserves=1_000_000_000_000_000,
        )

        #
        # 50% per-trade cap.
        # 60% aggregate exposure cap.
        #
        # Therefore:
        # reservation 1 can consume <= 50M;
        # reservation 2 can consume only the
        # remaining <= 10M.
        #
        self.policy = RiskPolicy(
            max_trade_equity_bps=5_000,
            max_total_exposure_bps=6_000,
            max_daily_loss_bps=10_000,
            max_drawdown_bps=10_000,
            max_open_positions=10,
            min_trade_lamports=1_000_000,
            max_size_price_impact_bps=10_000.0,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def reserve(
        self,
        mint: str,
        *,
        ttl: float = 60.0,
        available_cash: int = 100_000_000,
    ):
        return reserve_pump_buy_capital(
            mint=mint,
            available_cash_lamports=(
                available_cash
            ),
            account=self.account,
            curve_state=self.curve,
            protocol_fee_bps=0,
            creator_fee_bps=0,
            slippage_bps=0,
            base_network_fee_lamports=0,
            priority_fee_lamports=0,
            rent_lamports=0,
            reservation_ttl_seconds=ttl,
            policy=self.policy,
            db_path=self.db_path,
        )

    def test_first_reservation_succeeds(self):
        result = self.reserve(
            "MintA"
        )

        self.assertEqual(
            result.status,
            "PASS",
        )

        self.assertIsNotNone(
            result.reservation
        )

        self.assertIsNotNone(
            result.risk_result
        )

        self.assertEqual(
            result.reservation.status,
            ACTIVE,
        )

        self.assertGreater(
            result.reservation.spend_lamports,
            0,
        )

        self.assertEqual(
            result.reservation.reserved_exposure_before_lamports,
            0,
        )

        self.assertEqual(
            result.reservation.reserved_cash_before_lamports,
            0,
        )

        self.assertEqual(
            result.reservation.active_reservations_before,
            0,
        )

        self.assertEqual(
            result.reservation.wallet_cost_lamports,
            result.risk_result
            .recommended_simulation
            .total_wallet_cost_lamports,
        )

    def test_second_reservation_sees_first_exposure(
        self,
    ):
        first = self.reserve(
            "MintA"
        )

        second = self.reserve(
            "MintB"
        )

        self.assertEqual(
            first.status,
            "PASS",
        )

        self.assertEqual(
            second.status,
            "PASS",
        )

        first_spend = (
            first.reservation.spend_lamports
        )

        second_spend = (
            second.reservation.spend_lamports
        )

        self.assertEqual(
            second.reservation
            .reserved_exposure_before_lamports,
            first_spend,
        )

        self.assertEqual(
            second.reservation
            .active_reservations_before,
            1,
        )

        self.assertEqual(
            second.reservation
            .reserved_cash_before_lamports,
            first.reservation
            .wallet_cost_lamports,
        )

        aggregate_cap = (
            self.account.current_equity_lamports
            * self.policy.max_total_exposure_bps
            // 10_000
        )

        self.assertLessEqual(
            first_spend + second_spend,
            aggregate_cap,
        )

    def test_concurrent_capacity_not_double_used(
        self,
    ):
        with ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            futures = [
                executor.submit(
                    self.reserve,
                    mint,
                )
                for mint in (
                    "MintConcurrentA",
                    "MintConcurrentB",
                )
            ]

            results = [
                future.result()
                for future in futures
            ]

        self.assertTrue(
            all(
                result.status == "PASS"
                for result in results
            )
        )

        total_reserved = sum(
            result.reservation.spend_lamports
            for result in results
        )

        aggregate_cap = (
            self.account.current_equity_lamports
            * self.policy.max_total_exposure_bps
            // 10_000
        )

        self.assertLessEqual(
            total_reserved,
            aggregate_cap,
        )

        prior_counts = sorted(
            result.reservation
            .active_reservations_before
            for result in results
        )

        self.assertEqual(
            prior_counts,
            [0, 1],
        )

    def test_duplicate_active_mint_blocks(self):
        first = self.reserve(
            "MintDuplicate"
        )

        second = self.reserve(
            "MintDuplicate"
        )

        self.assertEqual(
            first.status,
            "PASS",
        )

        self.assertEqual(
            second.status,
            "BLOCK",
        )

        self.assertIsNone(
            second.reservation
        )

        self.assertIn(
            "ACTIVE_RESERVATION_ALREADY_EXISTS_FOR_MINT",
            second.reasons,
        )

    def test_expired_reservation_releases_capacity(
        self,
    ):
        first = self.reserve(
            "MintExpired"
        )

        self.assertEqual(
            first.status,
            "PASS",
        )

        connection = get_connection(
            self.db_path
        )

        try:
            init_schema(
                connection
            )

            connection.execute(
                """
                UPDATE live_capital_reservations
                SET expires_at = 0
                WHERE reservation_id = ?
                """,
                (
                    first.reservation
                    .reservation_id,
                ),
            )

            connection.commit()

        finally:
            connection.close()

        second = self.reserve(
            "MintAfterExpiry"
        )

        self.assertEqual(
            second.status,
            "PASS",
        )

        self.assertEqual(
            second.reservation
            .reserved_exposure_before_lamports,
            0,
        )

        self.assertEqual(
            second.reservation
            .active_reservations_before,
            0,
        )

        connection = get_connection(
            self.db_path
        )

        try:
            row = connection.execute(
                """
                SELECT
                    status,
                    terminal_reason

                FROM live_capital_reservations

                WHERE reservation_id = ?
                """,
                (
                    first.reservation
                    .reservation_id,
                ),
            ).fetchone()

        finally:
            connection.close()

        self.assertEqual(
            row["status"],
            EXPIRED,
        )

        self.assertEqual(
            row["terminal_reason"],
            "TTL_EXPIRED",
        )

    def test_reserved_cash_cannot_be_reused(
        self,
    ):
        #
        # First reservation is allowed to consume
        # the available cash. The second proposal
        # may still have portfolio exposure room,
        # but it must not reuse cash already held
        # by the first reservation.
        #
        first = self.reserve(
            "MintCashA",
            available_cash=50_000_000,
        )

        self.assertEqual(
            first.status,
            "PASS",
        )

        second = self.reserve(
            "MintCashB",
            available_cash=50_000_000,
        )

        self.assertEqual(
            second.status,
            "BLOCK",
        )

        self.assertIsNone(
            second.reservation
        )

        self.assertIn(
            "INSUFFICIENT_UNRESERVED_CASH",
            second.reasons,
        )

    def test_submitted_reservation_remains_held(
        self,
    ):
        first = self.reserve(
            "MintSubmitted",
            available_cash=50_000_000,
        )

        self.assertEqual(
            first.status,
            "PASS",
        )

        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_capital_reservations

                SET
                    status = ?,
                    expires_at = 0

                WHERE reservation_id = ?
                """,
                (
                    SUBMITTED,
                    first.reservation
                    .reservation_id,
                ),
            )

            connection.commit()

        finally:
            connection.close()

        second = self.reserve(
            "MintAfterSubmitted",
            available_cash=50_000_000,
        )

        self.assertEqual(
            second.status,
            "BLOCK",
        )

        self.assertIsNone(
            second.reservation
        )

        self.assertIn(
            "INSUFFICIENT_UNRESERVED_CASH",
            second.reasons,
        )

        connection = get_connection(
            self.db_path
        )

        try:
            row = connection.execute(
                """
                SELECT status

                FROM live_capital_reservations

                WHERE reservation_id = ?
                """,
                (
                    first.reservation
                    .reservation_id,
                ),
            ).fetchone()

        finally:
            connection.close()

        self.assertEqual(
            row["status"],
            SUBMITTED,
        )

    def test_invalid_available_cash_fails_closed(
        self,
    ):
        result = self.reserve(
            "MintInvalidCash",
            available_cash=-1,
        )

        self.assertEqual(
            result.status,
            "UNKNOWN",
        )

        self.assertIsNone(
            result.reservation
        )

        self.assertIsNone(
            result.risk_result
        )

        self.assertIn(
            "INVALID_AVAILABLE_CASH",
            result.reasons,
        )

    def test_invalid_ttl_fails_closed(self):
        result = self.reserve(
            "MintInvalidTTL",
            ttl=0,
        )

        self.assertEqual(
            result.status,
            "UNKNOWN",
        )

        self.assertIsNone(
            result.reservation
        )

        self.assertIsNone(
            result.risk_result
        )

        self.assertIn(
            "INVALID_RESERVATION_TTL",
            result.reasons,
        )

    def test_reservation_is_persisted(self):
        result = self.reserve(
            "MintPersisted"
        )

        self.assertEqual(
            result.status,
            "PASS",
        )

        reservation = (
            result.reservation
        )

        connection = get_connection(
            self.db_path
        )

        try:
            row = connection.execute(
                """
                SELECT *

                FROM live_capital_reservations

                WHERE reservation_id = ?
                """,
                (
                    reservation.reservation_id,
                ),
            ).fetchone()

        finally:
            connection.close()

        self.assertIsNotNone(
            row
        )

        self.assertEqual(
            row["mint"],
            reservation.mint,
        )

        self.assertEqual(
            row["status"],
            ACTIVE,
        )

        self.assertEqual(
            int(row["spend_lamports"]),
            reservation.spend_lamports,
        )

        self.assertEqual(
            int(row["wallet_cost_lamports"]),
            reservation.wallet_cost_lamports,
        )

        self.assertEqual(
            int(
                row[
                    "reserved_exposure_before_lamports"
                ]
            ),
            reservation
            .reserved_exposure_before_lamports,
        )

        self.assertEqual(
            int(
                row[
                    "reserved_cash_before_lamports"
                ]
            ),
            reservation
            .reserved_cash_before_lamports,
        )

        self.assertEqual(
            int(
                row[
                    "base_available_cash_lamports"
                ]
            ),
            reservation
            .base_available_cash_lamports,
        )


if __name__ == "__main__":
    unittest.main()
