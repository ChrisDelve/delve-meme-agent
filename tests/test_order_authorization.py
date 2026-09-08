from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time
import unittest

from solders.pubkey import Pubkey

from src.execution.live_curve_state import (
    LivePumpCurveState,
)
from src.execution.order_authorization import (
    AUTHORIZE,
    DENY,
    authorize_pump_buy,
)
from src.execution.pump_execution_simulator import (
    PumpCurveState,
)
from src.portfolio.live_reservations import (
    RESERVATION_VERSION,
    bind_reservation_signed_transaction,
    get_connection,
    release_active_reservation,
    reserve_pump_buy_capital,
)
from src.risk.risk_governor import (
    AccountRiskState,
    RiskPolicy,
)
from src.safety.token_safety_gate import (
    SOL_QUOTE_MINT,
)
from src.safety.token_safety_resolver import (
    TOKEN_PROGRAM,
    derive_associated_token_account,
    derive_bonding_curve,
)


MINT = str(
    Pubkey.new_unique()
)


class OrderAuthorizationTests(
    unittest.TestCase
):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()

        self.wallet_pubkey = str(
            Pubkey.new_unique()
        )

        self.mint_pubkey = (
            Pubkey.from_string(
                MINT
            )
        )

        self.creator_pubkey = (
            Pubkey.new_unique()
        )

        self.bonding_curve_pubkey = (
            derive_bonding_curve(
                self.mint_pubkey
            )
        )

        self.associated_bonding_curve = (
            derive_associated_token_account(
                owner=(
                    self.bonding_curve_pubkey
                ),
                mint=self.mint_pubkey,
                token_program=TOKEN_PROGRAM,
            )
        )

        self.db_path = (
            Path(self.temp_dir.name)
            / "delve_live.db"
        )

        self.account = AccountRiskState(
            current_equity_lamports=100_000_000,
            day_start_equity_lamports=100_000_000,
            high_water_equity_lamports=100_000_000,
            open_exposure_lamports=0,
            open_positions=0,
        )

        self.curve_state = PumpCurveState(
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
            max_total_exposure_bps=6_000,
            max_daily_loss_bps=10_000,
            max_drawdown_bps=10_000,
            max_open_positions=10,
            min_trade_lamports=1_000_000,
            max_size_price_impact_bps=10_000.0,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_context(
        self,
        *,
        ttl: float = 60.0,
    ):
        decision = reserve_pump_buy_capital(
            mint=MINT,
            wallet_pubkey=self.wallet_pubkey,
            available_cash_lamports=(
                100_000_000
            ),
            account=self.account,
            curve_state=self.curve_state,
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

        self.assertEqual(
            decision.status,
            "PASS",
        )

        reservation = (
            decision.reservation
        )

        risk = decision.risk_result

        self.assertIsNotNone(
            reservation
        )

        self.assertIsNotNone(
            risk
        )

        simulation = (
            risk.recommended_simulation
        )

        self.assertIsNotNone(
            simulation
        )

        safety = SimpleNamespace(
            gate_version="token-safety-test",
            mint=MINT,
            allows_trade=True,
            snapshot=SimpleNamespace(
                mint=MINT,
                rpc_max_slot=100,
                token_program=str(
                    TOKEN_PROGRAM
                ),
                associated_bonding_curve=str(
                    self.associated_bonding_curve
                ),
                bonding_curve=SimpleNamespace(
                    address=str(
                        self.bonding_curve_pubkey
                    ),
                    creator=str(
                        self.creator_pubkey
                    ),
                    is_mayhem_mode=False,
                    quote_mint=SOL_QUOTE_MINT,
                ),
            ),
        )

        execution = SimpleNamespace(
            gate_version="execution-quality-test",
            mint=MINT,
            allows_order_build=True,
            spendable_quote_in=(
                reservation.spend_lamports
            ),
            simulation=simulation,
        )

        live_curve = LivePumpCurveState(
            mint=MINT,
            curve=SimpleNamespace(
                address=str(
                    self.bonding_curve_pubkey
                ),
                creator=str(
                    self.creator_pubkey
                ),
                is_mayhem_mode=False,
                quote_mint=SOL_QUOTE_MINT,
                virtual_quote_reserves=(
                    self.curve_state
                    .virtual_quote_reserves
                ),
                virtual_token_reserves=(
                    self.curve_state
                    .virtual_token_reserves
                ),
                real_quote_reserves=(
                    self.curve_state
                    .real_quote_reserves
                ),
                real_token_reserves=(
                    self.curve_state
                    .real_token_reserves
                ),
            ),
            rpc_slot=101,
            fetched_at=time.time(),
        )

        return (
            reservation,
            safety,
            execution,
            live_curve,
        )

    def authorize(
        self,
        *,
        reservation,
        safety,
        execution,
        live_curve,
        spend_lamports=None,
        max_age=30.0,
    ):
        if spend_lamports is None:
            spend_lamports = (
                reservation.spend_lamports
            )

        return authorize_pump_buy(
            mint=MINT,
            requested_spend_lamports=(
                spend_lamports
            ),
            reservation_id=(
                reservation.reservation_id
            ),
            live_curve=live_curve,
            safety=safety,
            execution=execution,
            max_authorization_age_seconds=(
                max_age
            ),
            reservation_db_path=(
                self.db_path
            ),
        )

    def update_reservation(
        self,
        reservation_id: str,
        *,
        sql: str,
        params: tuple,
    ):
        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                sql,
                params,
            )

            connection.commit()

        finally:
            connection.close()

    def test_active_reservation_authorizes(
        self,
    ):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
        )

        self.assertEqual(
            result.status,
            AUTHORIZE,
        )

        self.assertEqual(
            result.reasons,
            (),
        )

        self.assertEqual(
            result.reservation_id,
            reservation.reservation_id,
        )

        self.assertEqual(
            result.reservation_version,
            RESERVATION_VERSION,
        )

        self.assertEqual(
            result.wallet_cost_lamports,
            reservation.wallet_cost_lamports,
        )

        self.assertIsNotNone(
            result.simulation_sha256
        )

        self.assertEqual(
            result.simulation_sha256,
            reservation.risk_simulation_sha256,
        )

        self.assertEqual(
            result.risk_governor_version,
            reservation.risk_governor_version,
        )

        self.assertTrue(
            result.is_valid()
        )

    def test_active_authorization_binds_transaction_contract(
        self,
    ):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
        )

        self.assertEqual(
            result.status,
            AUTHORIZE,
        )

        self.assertEqual(
            result.authorization_version,
            "order-authorization-v3",
        )

        self.assertEqual(
            result.wallet_pubkey,
            reservation.wallet_pubkey,
        )

        self.assertEqual(
            result.bonding_curve,
            str(self.bonding_curve_pubkey),
        )

        self.assertEqual(
            result.associated_bonding_curve,
            str(
                self.associated_bonding_curve
            ),
        )

        self.assertEqual(
            result.base_token_program,
            str(TOKEN_PROGRAM),
        )

        self.assertEqual(
            result.creator,
            str(self.creator_pubkey),
        )

        self.assertFalse(
            result.mayhem_mode
        )

        self.assertEqual(
            result.quote_mint_for_instruction,
            (
                "So11111111111111111111111111111111111111112"
            ),
        )

        self.assertEqual(
            result.token_amount,
            execution.simulation.tokens_out,
        )

        self.assertEqual(
            result.max_sol_cost,
            execution.simulation.spendable_quote_in,
        )

    def test_non_sol_quote_mint_denies(
        self,
    ):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        safety.snapshot.bonding_curve.quote_mint = (
            str(
                Pubkey.new_unique()
            )
        )

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "NON_SOL_AUTHORIZATION_QUOTE_MINT",
            result.reasons,
        )

    def test_safety_live_creator_mismatch_denies(
        self,
    ):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        safety.snapshot.bonding_curve.creator = (
            str(
                Pubkey.new_unique()
            )
        )

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "SAFETY_LIVE_CURVE_CREATOR_MISMATCH",
            result.reasons,
        )

    def test_missing_reservation_denies(self):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        result = authorize_pump_buy(
            mint=MINT,
            requested_spend_lamports=(
                reservation.spend_lamports
            ),
            reservation_id=(
                "does-not-exist"
            ),
            live_curve=live_curve,
            safety=safety,
            execution=execution,
            max_authorization_age_seconds=30.0,
            reservation_db_path=(
                self.db_path
            ),
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "RESERVATION_NOT_FOUND",
            result.reasons,
        )

    def test_expired_reservation_denies(self):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        self.update_reservation(
            reservation.reservation_id,
            sql="""
                UPDATE live_capital_reservations
                SET expires_at = 0
                WHERE reservation_id = ?
            """,
            params=(
                reservation.reservation_id,
            ),
        )

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "RESERVATION_NOT_ACTIVE",
            result.reasons,
        )

    def test_released_reservation_denies(self):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        released = release_active_reservation(
            reservation_id=(
                reservation.reservation_id
            ),
            reason="TEST_RELEASE",
            db_path=self.db_path,
        )

        self.assertEqual(
            released.status,
            "PASS",
        )

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "RESERVATION_NOT_ACTIVE",
            result.reasons,
        )

    def test_signed_reservation_denies(self):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        signed = (
            bind_reservation_signed_transaction(
                reservation_id=(
                    reservation.reservation_id
                ),
                transaction_signature=(
                    "SignedTransactionA"
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            signed.status,
            "PASS",
        )

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "RESERVATION_NOT_ACTIVE",
            result.reasons,
        )

        self.assertIn(
            "RESERVATION_ALREADY_BOUND",
            result.reasons,
        )

    def test_reservation_mint_mismatch_denies(
        self,
    ):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        self.update_reservation(
            reservation.reservation_id,
            sql="""
                UPDATE live_capital_reservations
                SET mint = ?
                WHERE reservation_id = ?
            """,
            params=(
                "OtherMint",
                reservation.reservation_id,
            ),
        )

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "RESERVATION_MINT_MISMATCH",
            result.reasons,
        )

    def test_reservation_spend_mismatch_denies(
        self,
    ):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        self.update_reservation(
            reservation.reservation_id,
            sql="""
                UPDATE live_capital_reservations
                SET spend_lamports =
                    spend_lamports + 1
                WHERE reservation_id = ?
            """,
            params=(
                reservation.reservation_id,
            ),
        )

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "RESERVATION_SPEND_MISMATCH",
            result.reasons,
        )

    def test_wallet_cost_mismatch_denies(
        self,
    ):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        self.update_reservation(
            reservation.reservation_id,
            sql="""
                UPDATE live_capital_reservations
                SET wallet_cost_lamports =
                    wallet_cost_lamports + 1
                WHERE reservation_id = ?
            """,
            params=(
                reservation.reservation_id,
            ),
        )

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "SIMULATION_WALLET_COST_MISMATCH",
            result.reasons,
        )


    def test_reservation_contract_version_mismatch_denies(
        self,
    ):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        self.update_reservation(
            reservation.reservation_id,
            sql="""
                UPDATE live_capital_reservations
                SET reservation_version = ?
                WHERE reservation_id = ?
            """,
            params=(
                "unknown-reservation-version",
                reservation.reservation_id,
            ),
        )

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "RESERVATION_VERSION_MISMATCH",
            result.reasons,
        )

    def test_persisted_simulation_fingerprint_mismatch_denies(
        self,
    ):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        self.update_reservation(
            reservation.reservation_id,
            sql="""
                UPDATE live_capital_reservations
                SET risk_simulation_sha256 = ?
                WHERE reservation_id = ?
            """,
            params=(
                "0" * 64,
                reservation.reservation_id,
            ),
        )

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "RESERVATION_EXECUTION_SIMULATION_MISMATCH",
            result.reasons,
        )

    def test_reservation_load_failure_is_not_not_found(
        self,
    ):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        result = authorize_pump_buy(
            mint=MINT,
            requested_spend_lamports=(
                reservation.spend_lamports
            ),
            reservation_id=(
                reservation.reservation_id
            ),
            live_curve=live_curve,
            safety=safety,
            execution=execution,
            max_authorization_age_seconds=30.0,
            reservation_db_path=Path(
                self.temp_dir.name
            ),
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "RESERVATION_LOAD_FAILED",
            result.reasons,
        )

        self.assertNotIn(
            "RESERVATION_NOT_FOUND",
            result.reasons,
        )

    def test_nan_authorization_age_denies(
        self,
    ):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
            max_age=float("nan"),
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "INVALID_AUTHORIZATION_AGE",
            result.reasons,
        )

    def test_nan_live_curve_fetch_time_denies(
        self,
    ):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        invalid_curve = replace(
            live_curve,
            fetched_at=float("nan"),
        )

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=invalid_curve,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "INVALID_LIVE_CURVE_FETCH_TIME",
            result.reasons,
        )

    def test_stale_live_curve_denies(self):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        stale_curve = replace(
            live_curve,
            fetched_at=(
                time.time() - 100.0
            ),
        )

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=stale_curve,
            max_age=2.0,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "LIVE_CURVE_STATE_TOO_OLD",
            result.reasons,
        )

    def test_safety_failure_denies(self):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        safety.allows_trade = False

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "TOKEN_SAFETY_NOT_PASS",
            result.reasons,
        )

    def test_execution_failure_denies(self):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        execution.allows_order_build = False

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "EXECUTION_QUALITY_NOT_PASS",
            result.reasons,
        )


    def test_live_curve_binding_mismatch_denies(
        self,
    ):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        mismatched_curve = replace(
            live_curve,
            curve=SimpleNamespace(
                virtual_quote_reserves=(
                    self.curve_state
                    .virtual_quote_reserves
                    + 1
                ),
                virtual_token_reserves=(
                    self.curve_state
                    .virtual_token_reserves
                ),
                real_quote_reserves=(
                    self.curve_state
                    .real_quote_reserves
                ),
                real_token_reserves=(
                    self.curve_state
                    .real_token_reserves
                ),
            ),
        )

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=mismatched_curve,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "SIMULATION_LIVE_CURVE_MISMATCH",
            result.reasons,
        )


    def test_non_executable_simulation_denies(
        self,
    ):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        execution.simulation = replace(
            execution.simulation,
            executable=False,
        )

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "SIMULATION_NOT_EXECUTABLE",
            result.reasons,
        )

    def test_authorization_expiry_capped_by_reservation(
        self,
    ):
        (
            reservation,
            safety,
            execution,
            live_curve,
        ) = self.make_context()

        reservation_expiry = (
            time.time() + 5.0
        )

        self.update_reservation(
            reservation.reservation_id,
            sql="""
                UPDATE live_capital_reservations
                SET expires_at = ?
                WHERE reservation_id = ?
            """,
            params=(
                reservation_expiry,
                reservation.reservation_id,
            ),
        )

        result = self.authorize(
            reservation=reservation,
            safety=safety,
            execution=execution,
            live_curve=live_curve,
            max_age=30.0,
        )

        self.assertEqual(
            result.status,
            AUTHORIZE,
        )

        self.assertAlmostEqual(
            result.expires_at,
            reservation_expiry,
            places=4,
        )

        self.assertAlmostEqual(
            result.reservation_expires_at,
            reservation_expiry,
            places=4,
        )


if __name__ == "__main__":
    unittest.main()
