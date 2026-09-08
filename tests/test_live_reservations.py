from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from solders.hash import Hash
from solders.pubkey import Pubkey

from src.execution.pump_execution_simulator import (
    PumpCurveState,
)
from src.portfolio.live_reservations import (
    ACTIVE,
    EXPIRED,
    RELEASED,
    SIGNED,
    SUBMITTED,
    get_connection,
    init_schema,
    load_capital_reservation,
    load_capital_reservation_read_only,
    bind_reservation_signed_transaction,
    release_active_reservation,
    reserve_pump_buy_capital,
)
from src.risk.risk_governor import (
    AccountRiskState,
    RiskPolicy,
)


class LiveReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()

        self.wallet_pubkey = str(
            Pubkey.new_unique()
        )

        self.recent_blockhash = str(
            Hash.new_unique()
        )
        self.last_valid_block_height = 350
        self.blockhash_rpc_slot = 200

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

    def bind_signed(
        self,
        *,
        reservation_id: str,
        transaction_signature: str,
        db_path=None,
        signed_message_sha256: str | None = None,
        signed_transaction_bytes: bytes | None = None,
        recent_blockhash: str | None = None,
        last_valid_block_height: int | None = None,
        blockhash_rpc_slot: int | None = None,
    ):
        if db_path is None:
            db_path = self.db_path

        if signed_message_sha256 is None:
            signed_message_sha256 = (
                "11" * 32
            )

        if signed_transaction_bytes is None:
            signed_transaction_bytes = (
                "signed-transaction:"
                f"{transaction_signature}"
            ).encode(
                "utf-8"
            )

        if recent_blockhash is None:
            recent_blockhash = (
                self.recent_blockhash
            )

        if last_valid_block_height is None:
            last_valid_block_height = (
                self.last_valid_block_height
            )

        if blockhash_rpc_slot is None:
            blockhash_rpc_slot = (
                self.blockhash_rpc_slot
            )

        return bind_reservation_signed_transaction(
            reservation_id=reservation_id,
            transaction_signature=(
                transaction_signature
            ),
            signed_message_sha256=(
                signed_message_sha256
            ),
            signed_transaction_bytes=(
                signed_transaction_bytes
            ),
            recent_blockhash=recent_blockhash,
            last_valid_block_height=(
                last_valid_block_height
            ),
            blockhash_rpc_slot=(
                blockhash_rpc_slot
            ),
            db_path=db_path,
        )

    def reserve(
        self,
        mint: str,
        *,
        ttl: float = 60.0,
        available_cash: int = 100_000_000,
    ):
        return reserve_pump_buy_capital(
            mint=mint,
            wallet_pubkey=self.wallet_pubkey,
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

    def test_invalid_wallet_pubkey_fails_closed(
        self,
    ):
        result = reserve_pump_buy_capital(
            mint="MintA",
            wallet_pubkey=(
                "not-a-solana-pubkey"
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
            "UNKNOWN",
        )

        self.assertEqual(
            result.reasons,
            ("INVALID_WALLET_PUBKEY",),
        )

        self.assertIsNone(
            result.reservation
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
            result.reservation.wallet_pubkey,
            self.wallet_pubkey,
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

        simulation = (
            result.risk_result
            .recommended_simulation
        )

        self.assertEqual(
            result.reservation.wallet_cost_lamports,
            (
                result.reservation.spend_lamports
                + simulation
                .total_transaction_overhead_lamports
            ),
        )

        self.assertGreaterEqual(
            result.reservation.wallet_cost_lamports,
            simulation.total_wallet_cost_lamports,
        )

        self.assertEqual(
            (
                result.reservation
                .wallet_cost_lamports
                - simulation
                .total_wallet_cost_lamports
            ),
            simulation.unused_pump_budget,
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

    def test_signed_reservation_remains_held(
        self,
    ):
        first = self.reserve(
            "MintSignedHeld",
            available_cash=50_000_000,
        )

        self.assertEqual(
            first.status,
            "PASS",
        )

        signed = self.bind_signed(
            reservation_id=(
                first.reservation
                .reservation_id
            ),
            transaction_signature="SignedHeldSignature",
            db_path=self.db_path,
        )

        self.assertEqual(
            signed.status,
            "PASS",
        )

        self.assertEqual(
            signed.reservation.status,
            SIGNED,
        )

        #
        # Simulate the original pre-sign reservation
        # TTL having elapsed.
        #
        connection = get_connection(
            self.db_path
        )

        try:
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
            "MintAfterSignedHold",
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
            SIGNED,
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


    def test_read_only_snapshot_does_not_expire_active_reservation(
        self,
    ):
        result = self.reserve(
            "MintReadOnlySnapshot"
        )

        reservation_id = (
            result.reservation
            .reservation_id
        )

        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_capital_reservations
                SET expires_at = ?
                WHERE reservation_id = ?
                """,
                (
                    0.0,
                    reservation_id,
                ),
            )

            connection.commit()

        finally:
            connection.close()

        snapshot = (
            load_capital_reservation_read_only(
                reservation_id=reservation_id,
                db_path=self.db_path,
            )
        )

        self.assertIsNotNone(
            snapshot
        )

        self.assertEqual(
            snapshot.status,
            ACTIVE,
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
                    reservation_id,
                ),
            ).fetchone()

        finally:
            connection.close()

        self.assertEqual(
            row["status"],
            ACTIVE,
        )

    def test_bind_signed_transaction_is_idempotent(
        self,
    ):
        result = self.reserve(
            "MintSubmit"
        )

        self.assertEqual(
            result.status,
            "PASS",
        )

        first = self.bind_signed(
            reservation_id=(
                result.reservation
                .reservation_id
            ),
            transaction_signature="SignatureA",
            db_path=self.db_path,
        )

        self.assertEqual(
            first.status,
            "PASS",
        )

        self.assertTrue(
            first.changed
        )

        self.assertEqual(
            first.reservation.status,
            SIGNED,
        )

        self.assertEqual(
            first.reservation
            .transaction_signature,
            "SignatureA",
        )

        self.assertIsNotNone(
            first.reservation.signed_at
        )

        self.assertEqual(
            first.reservation
            .signed_message_sha256,
            "11" * 32,
        )

        self.assertEqual(
            first.reservation
            .signed_transaction_bytes,
            b"signed-transaction:SignatureA",
        )

        self.assertIsNotNone(
            first.reservation
            .signed_transaction_sha256
        )

        self.assertEqual(
            len(
                first.reservation
                .signed_transaction_sha256
            ),
            64,
        )

        self.assertEqual(
            first.reservation.recent_blockhash,
            self.recent_blockhash,
        )

        self.assertEqual(
            first.reservation.last_valid_block_height,
            self.last_valid_block_height,
        )

        self.assertEqual(
            first.reservation.blockhash_rpc_slot,
            self.blockhash_rpc_slot,
        )

        retry = self.bind_signed(
            reservation_id=(
                result.reservation
                .reservation_id
            ),
            transaction_signature="SignatureA",
            db_path=self.db_path,
        )

        self.assertEqual(
            retry.status,
            "PASS",
        )

        self.assertFalse(
            retry.changed
        )

        self.assertEqual(
            retry.reservation
            .transaction_signature,
            "SignatureA",
        )

    def test_invalid_signed_recent_blockhash_fails_closed(
        self,
    ):
        result = self.reserve(
            "MintInvalidBlockhash"
        )

        transition = self.bind_signed(
            reservation_id=(
                result.reservation
                .reservation_id
            ),
            transaction_signature=(
                "SignatureInvalidBlockhash"
            ),
            recent_blockhash=(
                "not-a-solana-blockhash"
            ),
        )

        self.assertEqual(
            transition.status,
            "UNKNOWN",
        )

        self.assertIn(
            "INVALID_RECENT_BLOCKHASH",
            transition.reasons,
        )

        loaded = load_capital_reservation(
            reservation_id=(
                result.reservation
                .reservation_id
            ),
            db_path=self.db_path,
        )

        self.assertEqual(
            loaded.status,
            ACTIVE,
        )

    def test_invalid_signed_expiry_numbers_fail_closed(
        self,
    ):
        result = self.reserve(
            "MintInvalidExpiryNumbers"
        )

        cases = (
            (
                {
                    "last_valid_block_height": True,
                },
                "INVALID_LAST_VALID_BLOCK_HEIGHT",
            ),
            (
                {
                    "blockhash_rpc_slot": True,
                },
                "INVALID_BLOCKHASH_RPC_SLOT",
            ),
        )

        for updates, reason in cases:
            with self.subTest(
                reason=reason
            ):
                transition = self.bind_signed(
                    reservation_id=(
                        result.reservation
                        .reservation_id
                    ),
                    transaction_signature=(
                        "SignatureInvalidExpiry"
                    ),
                    **updates,
                )

                self.assertEqual(
                    transition.status,
                    "UNKNOWN",
                )

                self.assertIn(
                    reason,
                    transition.reasons,
                )

        loaded = load_capital_reservation(
            reservation_id=(
                result.reservation
                .reservation_id
            ),
            db_path=self.db_path,
        )

        self.assertEqual(
            loaded.status,
            ACTIVE,
        )

    def test_signed_expiry_artifact_mismatch_blocks(
        self,
    ):
        result = self.reserve(
            "MintExpiryMismatch"
        )

        reservation_id = (
            result.reservation
            .reservation_id
        )

        first = self.bind_signed(
            reservation_id=reservation_id,
            transaction_signature=(
                "SignatureExpiryMismatch"
            ),
        )

        self.assertEqual(
            first.status,
            "PASS",
        )

        retry = self.bind_signed(
            reservation_id=reservation_id,
            transaction_signature=(
                "SignatureExpiryMismatch"
            ),
            last_valid_block_height=(
                self.last_valid_block_height
                + 1
            ),
        )

        self.assertEqual(
            retry.status,
            "BLOCK",
        )

        self.assertIn(
            "SIGNED_TRANSACTION_ARTIFACT_MISMATCH",
            retry.reasons,
        )

        self.assertFalse(
            retry.changed
        )

    def test_signed_signature_mismatch_blocks(
        self,
    ):
        result = self.reserve(
            "MintSubmitMismatch"
        )

        submitted = (
            self.bind_signed(
                reservation_id=(
                    result.reservation
                    .reservation_id
                ),
                transaction_signature=(
                    "SignatureOriginal"
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            submitted.status,
            "PASS",
        )

        retry = self.bind_signed(
            reservation_id=(
                result.reservation
                .reservation_id
            ),
            transaction_signature=(
                "SignatureDifferent"
            ),
            db_path=self.db_path,
        )

        self.assertEqual(
            retry.status,
            "BLOCK",
        )

        self.assertFalse(
            retry.changed
        )

        self.assertIn(
            "RESERVATION_SIGNATURE_MISMATCH",
            retry.reasons,
        )

    def test_transaction_signature_unique_across_reservations(
        self,
    ):
        first = self.reserve(
            "MintSignatureA"
        )

        second = self.reserve(
            "MintSignatureB"
        )

        self.assertEqual(
            first.status,
            "PASS",
        )

        self.assertEqual(
            second.status,
            "PASS",
        )

        first_submit = (
            self.bind_signed(
                reservation_id=(
                    first.reservation
                    .reservation_id
                ),
                transaction_signature=(
                    "SharedSignature"
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            first_submit.status,
            "PASS",
        )

        second_submit = (
            self.bind_signed(
                reservation_id=(
                    second.reservation
                    .reservation_id
                ),
                transaction_signature=(
                    "SharedSignature"
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            second_submit.status,
            "BLOCK",
        )

        self.assertFalse(
            second_submit.changed
        )

        self.assertIn(
            "TRANSACTION_SIGNATURE_ALREADY_BOUND",
            second_submit.reasons,
        )

    def test_release_active_is_idempotent(
        self,
    ):
        result = self.reserve(
            "MintRelease"
        )

        first = release_active_reservation(
            reservation_id=(
                result.reservation
                .reservation_id
            ),
            reason="PRE_SUBMIT_ABORT",
            db_path=self.db_path,
        )

        self.assertEqual(
            first.status,
            "PASS",
        )

        self.assertTrue(
            first.changed
        )

        self.assertEqual(
            first.reservation.status,
            RELEASED,
        )

        retry = release_active_reservation(
            reservation_id=(
                result.reservation
                .reservation_id
            ),
            reason="PRE_SUBMIT_ABORT",
            db_path=self.db_path,
        )

        self.assertEqual(
            retry.status,
            "PASS",
        )

        self.assertFalse(
            retry.changed
        )

        mismatch = release_active_reservation(
            reservation_id=(
                result.reservation
                .reservation_id
            ),
            reason="DIFFERENT_REASON",
            db_path=self.db_path,
        )

        self.assertEqual(
            mismatch.status,
            "BLOCK",
        )

        self.assertIn(
            "RELEASE_REASON_MISMATCH",
            mismatch.reasons,
        )

    def test_signed_cannot_generic_release(
        self,
    ):
        result = self.reserve(
            "MintSubmittedRelease"
        )

        submitted = (
            self.bind_signed(
                reservation_id=(
                    result.reservation
                    .reservation_id
                ),
                transaction_signature=(
                    "SignatureSubmitted"
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            submitted.status,
            "PASS",
        )

        released = release_active_reservation(
            reservation_id=(
                result.reservation
                .reservation_id
            ),
            reason="TRY_RELEASE",
            db_path=self.db_path,
        )

        self.assertEqual(
            released.status,
            "BLOCK",
        )

        self.assertFalse(
            released.changed
        )

        self.assertEqual(
            released.reservation.status,
            SIGNED,
        )

        self.assertIn(
            "SIGNED_RESERVATION_REQUIRES_RECONCILIATION",
            released.reasons,
        )

    def test_expired_cannot_be_signed(
        self,
    ):
        result = self.reserve(
            "MintExpiredSubmit"
        )

        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_capital_reservations

                SET expires_at = 0

                WHERE reservation_id = ?
                """,
                (
                    result.reservation
                    .reservation_id,
                ),
            )

            connection.commit()

        finally:
            connection.close()

        submitted = (
            self.bind_signed(
                reservation_id=(
                    result.reservation
                    .reservation_id
                ),
                transaction_signature=(
                    "TooLateSignature"
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            submitted.status,
            "BLOCK",
        )

        self.assertFalse(
            submitted.changed
        )

        self.assertEqual(
            submitted.reservation.status,
            EXPIRED,
        )

        self.assertIn(
            "RESERVATION_NOT_ACTIVE",
            submitted.reasons,
        )


    def test_signed_artifact_mismatch_blocks(
        self,
    ):
        result = self.reserve(
            "MintSignedArtifactMismatch"
        )

        first = self.bind_signed(
            reservation_id=(
                result.reservation
                .reservation_id
            ),
            transaction_signature=(
                "SignatureArtifact"
            ),
        )

        self.assertEqual(
            first.status,
            "PASS",
        )

        retry = self.bind_signed(
            reservation_id=(
                result.reservation
                .reservation_id
            ),
            transaction_signature=(
                "SignatureArtifact"
            ),
            signed_transaction_bytes=(
                b"different-signed-transaction"
            ),
        )

        self.assertEqual(
            retry.status,
            "BLOCK",
        )

        self.assertFalse(
            retry.changed
        )

        self.assertIn(
            "SIGNED_TRANSACTION_ARTIFACT_MISMATCH",
            retry.reasons,
        )

    def test_invalid_signed_message_hash_fails_closed(
        self,
    ):
        result = self.reserve(
            "MintInvalidSignedMessageHash"
        )

        transition = (
            bind_reservation_signed_transaction(
                reservation_id=(
                    result.reservation
                    .reservation_id
                ),
                transaction_signature=(
                    "SignatureInvalidHash"
                ),
                signed_message_sha256=(
                    "not-a-sha256"
                ),
                signed_transaction_bytes=(
                    b"signed-transaction"
                ),
                recent_blockhash=(
                    self.recent_blockhash
                ),
                last_valid_block_height=(
                    self.last_valid_block_height
                ),
                blockhash_rpc_slot=(
                    self.blockhash_rpc_slot
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            transition.status,
            "UNKNOWN",
        )

        self.assertFalse(
            transition.changed
        )

        self.assertIn(
            "INVALID_SIGNED_MESSAGE_SHA256",
            transition.reasons,
        )

    def test_old_reservation_version_cannot_bind_signed_artifact(
        self,
    ):
        result = self.reserve(
            "MintOldReservationContract"
        )

        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_capital_reservations
                SET reservation_version = ?
                WHERE reservation_id = ?
                """,
                (
                    "live-capital-reservation-v5",
                    result.reservation
                    .reservation_id,
                ),
            )

            connection.commit()

        finally:
            connection.close()

        transition = self.bind_signed(
            reservation_id=(
                result.reservation
                .reservation_id
            ),
            transaction_signature=(
                "SignatureOldContract"
            ),
        )

        self.assertEqual(
            transition.status,
            "BLOCK",
        )

        self.assertFalse(
            transition.changed
        )

        self.assertIn(
            "RESERVATION_VERSION_MISMATCH",
            transition.reasons,
        )


if __name__ == "__main__":
    unittest.main()
