import time
import unittest
from dataclasses import replace

from solders.pubkey import Pubkey

from src.execution.live_pump_global_state import (
    GLOBAL_ACCOUNT_SIZE,
    LIVE_PUMP_GLOBAL_STATE_VERSION,
    LivePumpGlobalState,
    PumpGlobalSnapshot,
    derive_global,
)
from src.execution.order_authorization import (
    AUTHORIZATION_VERSION,
    AUTHORIZE,
    BUY,
    WRAPPED_SOL_MINT,
    OrderAuthorization,
)
from src.execution.pump_buy_v2_account_context import (
    BUY_V2_ACCOUNT_NAMES,
    PumpBuyV2AccountContextError,
    resolve_pump_buy_v2_account_context,
)
from src.portfolio.live_reservations import (
    RESERVATION_VERSION,
)
from src.safety.token_safety_resolver import (
    PUMP_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    derive_associated_token_account,
    derive_bonding_curve,
)


class PumpBuyV2AccountContextTests(
    unittest.TestCase
):
    def setUp(self):
        self.mint = (
            Pubkey.new_unique()
        )

        self.wallet = (
            Pubkey.new_unique()
        )

        self.creator = (
            Pubkey.new_unique()
        )

        self.curve = (
            derive_bonding_curve(
                self.mint
            )
        )

        self.base_curve_ata = (
            derive_associated_token_account(
                owner=self.curve,
                mint=self.mint,
                token_program=(
                    TOKEN_2022_PROGRAM
                ),
            )
        )

        self.normal_recipients = tuple(
            str(
                Pubkey.new_unique()
            )
            for _ in range(
                8
            )
        )

        self.mayhem_recipients = tuple(
            str(
                Pubkey.new_unique()
            )
            for _ in range(
                8
            )
        )

        self.buyback_recipients = tuple(
            str(
                Pubkey.new_unique()
            )
            for _ in range(
                8
            )
        )

    def make_authorization(
        self,
        *,
        mayhem_mode=False,
    ):
        now = time.time()

        return OrderAuthorization(
            authorization_version=(
                AUTHORIZATION_VERSION
            ),
            status=AUTHORIZE,
            reasons=(),
            mint=str(
                self.mint
            ),
            side=BUY,
            wallet_pubkey=str(
                self.wallet
            ),
            bonding_curve=str(
                self.curve
            ),
            associated_bonding_curve=str(
                self.base_curve_ata
            ),
            base_token_program=str(
                TOKEN_2022_PROGRAM
            ),
            creator=str(
                self.creator
            ),
            mayhem_mode=mayhem_mode,
            quote_mint_for_instruction=(
                WRAPPED_SOL_MINT
            ),
            token_amount=123_456_789,
            max_sol_cost=9_000_000,
            spend_lamports=9_000_000,
            wallet_cost_lamports=(
                9_050_000
            ),
            base_network_fee_lamports=(
                5_000
            ),
            priority_fee_lamports=(
                45_000
            ),
            rent_lamports=0,
            reservation_id=(
                "reservation-123"
            ),
            reservation_version=(
                RESERVATION_VERSION
            ),
            reservation_expires_at=(
                now
                + 60.0
            ),
            curve_rpc_slot=100,
            live_curve_fetched_at=now,
            live_curve_age_seconds=0.0,
            safety_gate_version=(
                "token-safety-test"
            ),
            execution_gate_version=(
                "execution-quality-test"
            ),
            risk_governor_version=(
                "risk-governor-v1"
            ),
            simulation_sha256=(
                "ab" * 32
            ),
            created_at=now,
            expires_at=(
                now
                + 30.0
            ),
        )

    def make_global_state(
        self,
        *,
        rpc_slot=101,
        mayhem_enabled=True,
    ):
        snapshot = PumpGlobalSnapshot(
            address=str(
                derive_global()
            ),
            account_size=(
                GLOBAL_ACCOUNT_SIZE
            ),
            owner_verified=True,
            discriminator_verified=True,
            initialized=True,
            authority=str(
                Pubkey.new_unique()
            ),
            normal_fee_recipients=(
                self.normal_recipients
            ),
            withdraw_authority=str(
                Pubkey.new_unique()
            ),
            create_v2_enabled=True,
            whitelist_pda=str(
                Pubkey.new_unique()
            ),
            mayhem_fee_recipients=(
                self.mayhem_recipients
            ),
            mayhem_mode_enabled=(
                mayhem_enabled
            ),
            is_cashback_enabled=True,
            buyback_fee_recipients=(
                self.buyback_recipients
            ),
            buyback_basis_points=5_000,
            initial_virtual_quote_reserves=(
                30_000_000_000
            ),
            whitelisted_quote_mints=(
                WRAPPED_SOL_MINT,
            ),
        )

        return LivePumpGlobalState(
            resolver_version=(
                LIVE_PUMP_GLOBAL_STATE_VERSION
            ),
            global_state=snapshot,
            rpc_slot=rpc_slot,
            fetched_at=time.time(),
        )

    def test_resolves_exact_27_account_order(
        self,
    ):
        authorization = (
            self.make_authorization()
        )

        context = (
            resolve_pump_buy_v2_account_context(
                authorization=authorization,
                global_state=(
                    self.make_global_state()
                ),
            )
        )

        accounts = (
            context.ordered_accounts()
        )

        named_accounts = (
            context.ordered_named_accounts()
        )

        self.assertEqual(
            len(accounts),
            27,
        )

        self.assertEqual(
            len(
                BUY_V2_ACCOUNT_NAMES
            ),
            27,
        )

        self.assertEqual(
            tuple(
                name
                for name, _
                in named_accounts
            ),
            BUY_V2_ACCOUNT_NAMES,
        )

        self.assertEqual(
            accounts[0],
            str(
                derive_global()
            ),
        )

        self.assertEqual(
            accounts[1],
            str(
                self.mint
            ),
        )

        self.assertEqual(
            accounts[2],
            WRAPPED_SOL_MINT,
        )

        self.assertEqual(
            accounts[3],
            str(
                TOKEN_2022_PROGRAM
            ),
        )

        self.assertEqual(
            accounts[4],
            str(
                TOKEN_PROGRAM
            ),
        )

        self.assertEqual(
            accounts[10],
            str(
                self.curve
            ),
        )

        self.assertEqual(
            accounts[13],
            str(
                self.wallet
            ),
        )

        self.assertEqual(
            accounts[24],
            str(
                Pubkey.default()
            ),
        )

        self.assertEqual(
            accounts[26],
            str(
                PUMP_PROGRAM
            ),
        )

        self.assertEqual(
            context.amount,
            authorization.token_amount,
        )

        self.assertEqual(
            context.max_sol_cost,
            authorization.max_sol_cost,
        )

    def test_recipient_selection_is_deterministic(
        self,
    ):
        authorization = (
            self.make_authorization()
        )

        global_state = (
            self.make_global_state()
        )

        first = (
            resolve_pump_buy_v2_account_context(
                authorization=authorization,
                global_state=global_state,
            )
        )

        second = (
            resolve_pump_buy_v2_account_context(
                authorization=authorization,
                global_state=global_state,
            )
        )

        self.assertEqual(
            first.fee_recipient_index,
            second.fee_recipient_index,
        )

        self.assertEqual(
            first.buyback_fee_recipient_index,
            second.buyback_fee_recipient_index,
        )

        self.assertEqual(
            first.fee_recipient,
            second.fee_recipient,
        )

        self.assertEqual(
            first.buyback_fee_recipient,
            second.buyback_fee_recipient,
        )

    def test_mayhem_uses_reserved_recipient_set(
        self,
    ):
        authorization = (
            self.make_authorization(
                mayhem_mode=True
            )
        )

        context = (
            resolve_pump_buy_v2_account_context(
                authorization=authorization,
                global_state=(
                    self.make_global_state()
                ),
            )
        )

        self.assertIn(
            context.fee_recipient,
            self.mayhem_recipients,
        )

        self.assertNotIn(
            context.fee_recipient,
            self.normal_recipients,
        )

        self.assertIn(
            context.buyback_fee_recipient,
            self.buyback_recipients,
        )

    def test_global_state_cannot_predate_authorized_curve(
        self,
    ):
        with self.assertRaises(
            PumpBuyV2AccountContextError
        ):
            resolve_pump_buy_v2_account_context(
                authorization=(
                    self.make_authorization()
                ),
                global_state=(
                    self.make_global_state(
                        rpc_slot=99
                    )
                ),
            )

    def test_bonding_curve_mismatch_rejected(
        self,
    ):
        authorization = replace(
            self.make_authorization(),
            bonding_curve=str(
                Pubkey.new_unique()
            ),
        )

        with self.assertRaises(
            PumpBuyV2AccountContextError
        ):
            resolve_pump_buy_v2_account_context(
                authorization=authorization,
                global_state=(
                    self.make_global_state()
                ),
            )

    def test_non_sol_quote_mint_rejected(
        self,
    ):
        authorization = replace(
            self.make_authorization(),
            quote_mint_for_instruction=str(
                Pubkey.new_unique()
            ),
        )

        with self.assertRaises(
            PumpBuyV2AccountContextError
        ):
            resolve_pump_buy_v2_account_context(
                authorization=authorization,
                global_state=(
                    self.make_global_state()
                ),
            )

    def test_expired_authorization_rejected(
        self,
    ):
        authorization = replace(
            self.make_authorization(),
            expires_at=(
                time.time()
                - 1.0
            ),
        )

        with self.assertRaises(
            PumpBuyV2AccountContextError
        ):
            resolve_pump_buy_v2_account_context(
                authorization=authorization,
                global_state=(
                    self.make_global_state()
                ),
            )

    def test_mayhem_requires_global_enablement(
        self,
    ):
        authorization = (
            self.make_authorization(
                mayhem_mode=True
            )
        )

        with self.assertRaises(
            PumpBuyV2AccountContextError
        ):
            resolve_pump_buy_v2_account_context(
                authorization=authorization,
                global_state=(
                    self.make_global_state(
                        mayhem_enabled=False
                    )
                ),
            )


if __name__ == "__main__":
    unittest.main()
