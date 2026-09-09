import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from solders.pubkey import Pubkey

from src.execution.live_pump_fee_state import (
    LIVE_PUMP_FEE_STATE_VERSION,
)
from src.execution.live_pump_sell_authorization import (
    LIVE_PUMP_SELL_AUTHORIZATION_VERSION,
    LivePumpSellAuthorization,
    _authorization_sha256,
)
from src.execution.order_authorization import (
    WRAPPED_SOL_MINT,
)
from src.portfolio.live_positions import (
    LIVE_POSITION_VERSION,
    OPEN,
    LivePosition,
    init_schema as init_position_schema,
)
from src.portfolio.live_sell_allocation import (
    PLANNED,
    plan_live_sell_allocation,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    BLOCK,
    LIVE_SELL_INVENTORY_CLAIM_VERSION,
    PASS,
    UNKNOWN,
    acquire_live_sell_inventory_claim,
)
from src.safety.token_safety_gate import (
    SOL_QUOTE_MINT,
)
from src.safety.token_safety_resolver import (
    TOKEN_2022_PROGRAM,
    derive_associated_token_account,
)


class LiveSellInventoryClaimTests(
    unittest.TestCase
):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()

        self.db_path = (
            Path(self.temp_dir.name)
            / "live.db"
        )

        self.wallet = str(
            Pubkey.new_unique()
        )

        self.mint = str(
            Pubkey.new_unique()
        )

        self.creator = str(
            Pubkey.new_unique()
        )

        self.bonding_curve = str(
            Pubkey.new_unique()
        )

        self.base_token_program = str(
            TOKEN_2022_PROGRAM
        )

        self.associated_base_user = str(
            derive_associated_token_account(
                owner=Pubkey.from_string(
                    self.wallet
                ),
                mint=Pubkey.from_string(
                    self.mint
                ),
                token_program=(
                    TOKEN_2022_PROGRAM
                ),
            )
        )

        self.positions = (
            self.position(
                position_id=1,
                entry_slot=100,
                tokens=300,
                exposure=600,
                cost_basis=300,
            ),
            self.position(
                position_id=2,
                entry_slot=200,
                tokens=400,
                exposure=800,
                cost_basis=400,
            ),
        )

        self.seed_positions(
            self.positions
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def position(
        self,
        *,
        position_id,
        entry_slot,
        tokens,
        exposure,
        cost_basis,
    ):
        return LivePosition(
            position_id=position_id,
            position_version=(
                LIVE_POSITION_VERSION
            ),
            reservation_id=(
                f"reservation-{position_id}"
            ),
            entry_signature=(
                f"entry-{position_id}"
            ),
            wallet_pubkey=self.wallet,
            mint=self.mint,
            status=OPEN,
            fill_resolver_version="fill-v1",
            signed_transaction_sha256=(
                "11" * 32
            ),
            observed_transaction_sha256=(
                "22" * 32
            ),
            entry_slot=entry_slot,
            entry_block_time=100,
            base_token_program=(
                self.base_token_program
            ),
            associated_base_user=(
                self.associated_base_user
            ),
            quote_mint=WRAPPED_SOL_MINT,
            authorized_token_amount=tokens,
            trade_event_token_amount=tokens,
            token_pre_amount=0,
            token_post_amount=tokens,
            entry_tokens=tokens,
            tokens_held=tokens,
            authorized_max_sol_cost_lamports=(
                exposure
            ),
            entry_exposure_lamports=(
                exposure
            ),
            remaining_exposure_lamports=(
                exposure
            ),
            entry_wallet_cost_lamports=(
                cost_basis
            ),
            remaining_cost_basis_lamports=(
                cost_basis
            ),
            cumulative_net_proceeds_lamports=0,
            cumulative_realized_pnl_lamports=0,
            network_fee_lamports=5_000,
            wallet_pre_balance_lamports=(
                10_000_000
            ),
            wallet_post_balance_lamports=(
                9_000_000
            ),
            wallet_balance_delta_lamports=(
                -1_000_000
            ),
            trade_event_sol_amount=(
                cost_basis
            ),
            protocol_fee_lamports=0,
            creator_fee_lamports=0,
            cashback_lamports=0,
            buyback_fee_lamports=0,
            quote_amount=cost_basis,
            created_at=100.0,
            updated_at=100.0,
        )

    def seed_positions(
        self,
        positions,
    ):
        #
        # These tests exercise SELL claim authority
        # in isolation. Foreign-key enforcement is
        # intentionally not enabled on this fixture
        # connection because the BUY reservation
        # lifecycle is outside this unit boundary.
        #
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            init_position_schema(
                connection
            )

            for position in positions:
                values = asdict(
                    position
                )

                columns = ", ".join(
                    values.keys()
                )

                placeholders = ", ".join(
                    "?"
                    for _ in values
                )

                connection.execute(
                    f"""
                    INSERT INTO live_positions (
                        {columns}
                    )
                    VALUES (
                        {placeholders}
                    )
                    """,
                    tuple(
                        values.values()
                    ),
                )

            connection.commit()

        finally:
            connection.close()

    def exit_execution(
        self,
        *,
        tokens_in,
        slippage_bps,
    ):
        return SimpleNamespace(
            contract_version=(
                "exit-execution-test-v1"
            ),
            venue="PUMP",
            simulator_version=(
                "pump-sell-test-v1"
            ),
            tokens_in=tokens_in,
            protocol_fee_bps=100,
            creator_fee_bps=50,
            slippage_bps=slippage_bps,
            gross_quote_out=1_000_000,
            protocol_fee=10_000,
            creator_fee=5_000,
            net_quote_out_after_venue_fees=(
                985_000
            ),
            min_quote_out=900_000,
            base_network_fee_lamports=5_000,
            priority_fee_lamports=0,
            total_transaction_overhead_lamports=(
                5_000
            ),
            net_wallet_proceeds_lamports=(
                980_000
            ),
            executable=True,
            ineligible_reason=None,
            venue_evidence=None,
        )

    def authorization(
        self,
        *,
        tokens_to_sell=500,
        slippage_bps=500,
    ):
        allocation = (
            plan_live_sell_allocation(
                wallet_pubkey=self.wallet,
                mint=self.mint,
                tokens_to_sell=(
                    tokens_to_sell
                ),
                positions=self.positions,
            )
        )

        self.assertEqual(
            allocation.status,
            PLANNED,
        )

        exit_execution = (
            self.exit_execution(
                tokens_in=tokens_to_sell,
                slippage_bps=slippage_bps,
            )
        )

        authorization_sha256 = (
            _authorization_sha256(
                wallet_pubkey=self.wallet,
                mint=self.mint,
                bonding_curve=(
                    self.bonding_curve
                ),
                base_token_program=(
                    self.base_token_program
                ),
                associated_base_user=(
                    self.associated_base_user
                ),
                creator=self.creator,
                mayhem_mode=False,
                curve_quote_mint=(
                    SOL_QUOTE_MINT
                ),
                quote_mint_for_instruction=(
                    WRAPPED_SOL_MINT
                ),
                tokens_to_sell=(
                    tokens_to_sell
                ),
                allocation=allocation,
                fee_rpc_slot=500,
                quote_mint=SOL_QUOTE_MINT,
                protocol_fee_bps=100,
                creator_fee_bps=50,
                slippage_bps=(
                    slippage_bps
                ),
                base_network_fee_lamports=(
                    5_000
                ),
                priority_fee_lamports=0,
                exit_execution=(
                    exit_execution
                ),
            )
        )

        return LivePumpSellAuthorization(
            authorization_version=(
                LIVE_PUMP_SELL_AUTHORIZATION_VERSION
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            wallet_pubkey=self.wallet,
            mint=self.mint,
            bonding_curve=(
                self.bonding_curve
            ),
            base_token_program=(
                self.base_token_program
            ),
            associated_base_user=(
                self.associated_base_user
            ),
            creator=self.creator,
            mayhem_mode=False,
            curve_quote_mint=(
                SOL_QUOTE_MINT
            ),
            quote_mint_for_instruction=(
                WRAPPED_SOL_MINT
            ),
            tokens_to_sell=(
                tokens_to_sell
            ),
            allocation=allocation,
            fee_state_version=(
                LIVE_PUMP_FEE_STATE_VERSION
            ),
            fee_rpc_slot=500,
            fee_fetched_at=100.0,
            protocol_fee_bps=100,
            creator_fee_bps=50,
            slippage_bps=(
                slippage_bps
            ),
            base_network_fee_lamports=(
                5_000
            ),
            priority_fee_lamports=0,
            exit_execution=(
                exit_execution
            ),
            authorized_at=100.0,
        )

    def test_claim_persists_exact_fifo_allocation_without_mutating_positions(
        self,
    ):
        authorization = (
            self.authorization()
        )

        result = (
            acquire_live_sell_inventory_claim(
                authorization=authorization,
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            result.status,
            PASS,
        )
        self.assertEqual(
            result.reasons,
            (),
        )
        self.assertTrue(
            result.changed
        )
        self.assertIsNotNone(
            result.claim
        )

        claim = result.claim

        self.assertEqual(
            claim.claim_version,
            LIVE_SELL_INVENTORY_CLAIM_VERSION,
        )
        self.assertEqual(
            claim.status,
            ACTIVE,
        )
        self.assertEqual(
            claim.authorization_sha256,
            authorization.authorization_sha256,
        )
        self.assertEqual(
            claim.allocation,
            authorization.allocation,
        )

        connection = sqlite3.connect(
            self.db_path
        )

        try:
            header_count = (
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM live_sell_inventory_claims
                    """
                ).fetchone()[0]
            )

            lots = connection.execute(
                """
                SELECT
                    ordinal,
                    position_id,
                    tokens_before,
                    tokens_to_sell,
                    tokens_after

                FROM live_sell_inventory_claim_lots

                ORDER BY ordinal ASC
                """
            ).fetchall()

            position_rows = (
                connection.execute(
                    """
                    SELECT
                        position_id,
                        tokens_held,
                        remaining_exposure_lamports,
                        remaining_cost_basis_lamports,
                        cumulative_net_proceeds_lamports,
                        cumulative_realized_pnl_lamports

                    FROM live_positions

                    ORDER BY position_id ASC
                    """
                ).fetchall()
            )

        finally:
            connection.close()

        self.assertEqual(
            header_count,
            1,
        )

        self.assertEqual(
            lots,
            [
                (
                    0,
                    1,
                    300,
                    300,
                    0,
                ),
                (
                    1,
                    2,
                    400,
                    200,
                    200,
                ),
            ],
        )

        #
        # Inventory claim != economic fill.
        #
        self.assertEqual(
            position_rows,
            [
                (
                    1,
                    300,
                    600,
                    300,
                    0,
                    0,
                ),
                (
                    2,
                    400,
                    800,
                    400,
                    0,
                    0,
                ),
            ],
        )

    def test_exact_retry_is_idempotent(
        self,
    ):
        authorization = (
            self.authorization()
        )

        first = (
            acquire_live_sell_inventory_claim(
                authorization=authorization,
                db_path=self.db_path,
            )
        )

        second = (
            acquire_live_sell_inventory_claim(
                authorization=authorization,
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            first.status,
            PASS,
        )
        self.assertTrue(
            first.changed
        )

        self.assertEqual(
            second.status,
            PASS,
        )
        self.assertFalse(
            second.changed
        )

        self.assertEqual(
            second.claim,
            first.claim,
        )

    def test_missing_live_database_is_unknown_and_not_created(
        self,
    ):
        authorization = (
            self.authorization()
        )

        self.db_path.unlink()

        result = (
            acquire_live_sell_inventory_claim(
                authorization=authorization,
                db_path=self.db_path,
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

        self.assertFalse(
            self.db_path.exists()
        )

    def test_missing_live_positions_table_is_unknown_without_claim_schema_creation(
        self,
    ):
        authorization = (
            self.authorization()
        )

        self.db_path.unlink()

        connection = sqlite3.connect(
            self.db_path
        )

        connection.close()

        result = (
            acquire_live_sell_inventory_claim(
                authorization=authorization,
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "LIVE_POSITIONS_TABLE_NOT_FOUND",
            ),
        )

        connection = sqlite3.connect(
            self.db_path
        )

        try:
            claim_tables = (
                connection.execute(
                    """
                    SELECT COUNT(*)

                    FROM sqlite_master

                    WHERE type = 'table'
                      AND name IN (
                          'live_sell_inventory_claims',
                          'live_sell_inventory_claim_lots'
                      )
                    """
                ).fetchone()[0]
            )

        finally:
            connection.close()

        self.assertEqual(
            claim_tables,
            0,
        )

    def test_tampered_authorization_fingerprint_fails_closed(
        self,
    ):
        authorization = (
            self.authorization()
        )

        tampered = replace(
            authorization,
            authorization_sha256=(
                "aa" * 32
            ),
        )

        self.assertNotEqual(
            tampered.authorization_sha256,
            authorization.authorization_sha256,
        )

        result = (
            acquire_live_sell_inventory_claim(
                authorization=tampered,
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "SELL_AUTHORIZATION_CONTRACT_INVALID",
            ),
        )

        connection = sqlite3.connect(
            self.db_path
        )

        try:
            table_exists = (
                connection.execute(
                    """
                    SELECT COUNT(*)

                    FROM sqlite_master

                    WHERE type = 'table'
                      AND name = ?
                    """,
                    (
                        "live_sell_inventory_claims",
                    ),
                ).fetchone()[0]
            )

        finally:
            connection.close()

        #
        # Invalid authority is rejected before
        # durable claim state is initialized.
        #
        self.assertEqual(
            table_exists,
            0,
        )

    def test_stale_authorized_allocation_blocks(
        self,
    ):
        authorization = (
            self.authorization()
        )

        connection = sqlite3.connect(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_positions

                SET
                    tokens_held = 250,
                    remaining_exposure_lamports = 500,
                    remaining_cost_basis_lamports = 250

                WHERE position_id = 1
                """
            )

            connection.commit()

        finally:
            connection.close()

        result = (
            acquire_live_sell_inventory_claim(
                authorization=authorization,
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertEqual(
            result.reasons,
            (
                "SELL_AUTHORIZATION_ALLOCATION_STALE",
            ),
        )

    def test_distinct_valid_authorization_cannot_overlap_active_claim(
        self,
    ):
        first_authorization = (
            self.authorization(
                slippage_bps=500
            )
        )

        competing_authorization = (
            self.authorization(
                slippage_bps=600
            )
        )

        self.assertNotEqual(
            first_authorization.authorization_sha256,
            competing_authorization.authorization_sha256,
        )

        self.assertEqual(
            first_authorization.allocation,
            competing_authorization.allocation,
        )

        first = (
            acquire_live_sell_inventory_claim(
                authorization=(
                    first_authorization
                ),
                db_path=self.db_path,
            )
        )

        competing = (
            acquire_live_sell_inventory_claim(
                authorization=(
                    competing_authorization
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            first.status,
            PASS,
        )

        self.assertEqual(
            competing.status,
            BLOCK,
        )

        self.assertEqual(
            competing.reasons,
            (
                "SELL_INVENTORY_ALREADY_CLAIMED",
            ),
        )

    def test_concurrent_distinct_authorizations_cannot_double_claim_inventory(
        self,
    ):
        authorization_a = (
            self.authorization(
                slippage_bps=500
            )
        )

        authorization_b = (
            self.authorization(
                slippage_bps=600
            )
        )

        def acquire(
            authorization,
        ):
            return (
                acquire_live_sell_inventory_claim(
                    authorization=(
                        authorization
                    ),
                    db_path=self.db_path,
                )
            )

        with ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            results = list(
                executor.map(
                    acquire,
                    (
                        authorization_a,
                        authorization_b,
                    ),
                )
            )

        pass_results = [
            result
            for result in results
            if result.status == PASS
        ]

        block_results = [
            result
            for result in results
            if result.status == BLOCK
        ]

        self.assertEqual(
            len(pass_results),
            1,
        )

        self.assertEqual(
            len(block_results),
            1,
        )

        self.assertEqual(
            block_results[0].reasons,
            (
                "SELL_INVENTORY_ALREADY_CLAIMED",
            ),
        )

        connection = sqlite3.connect(
            self.db_path
        )

        try:
            active_rows = (
                connection.execute(
                    """
                    SELECT
                        authorization_sha256,
                        wallet_pubkey,
                        mint,
                        status

                    FROM live_sell_inventory_claims

                    WHERE status = ?
                    """,
                    (
                        ACTIVE,
                    ),
                ).fetchall()
            )

        finally:
            connection.close()

        self.assertEqual(
            len(active_rows),
            1,
        )

        self.assertEqual(
            active_rows[0][1],
            self.wallet,
        )

        self.assertEqual(
            active_rows[0][2],
            self.mint,
        )

        self.assertEqual(
            active_rows[0][3],
            ACTIVE,
        )


if __name__ == "__main__":
    unittest.main()
