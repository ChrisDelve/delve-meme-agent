import time
import unittest
from dataclasses import replace
from types import SimpleNamespace

from solders.pubkey import Pubkey

from src.execution.exit_execution import (
    EXIT_EXECUTION_CONTRACT_VERSION,
    PUMP_BONDING_CURVE_VENUE,
)
from src.execution.live_pump_global_state import (
    GLOBAL_ACCOUNT_SIZE,
    LIVE_PUMP_GLOBAL_STATE_VERSION,
    LivePumpGlobalState,
    PumpGlobalSnapshot,
    derive_global,
)
from src.execution.live_pump_sell_authorization import (
    LIVE_PUMP_SELL_AUTHORIZATION_VERSION,
    LivePumpSellAuthorization,
)
from src.execution.order_authorization import (
    WRAPPED_SOL_MINT,
)
from src.execution.pump_sell_v2_account_context import (
    PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION,
    SELL_BUYBACK_RECIPIENT_SELECTOR_VERSION,
    SELL_FEE_RECIPIENT_SELECTOR_VERSION,
    SELL_V2_ACCOUNT_NAMES,
    PumpSellV2AccountContextError,
    resolve_pump_sell_v2_account_context,
)
from src.safety.token_safety_gate import (
    SOL_QUOTE_MINT,
)
from src.safety.token_safety_resolver import (
    PUMP_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    derive_associated_token_account,
    derive_bonding_curve,
)


class PumpSellV2AccountContextTests(
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

        self.base_user = (
            derive_associated_token_account(
                owner=self.wallet,
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
        fee_rpc_slot=100,
        tokens_to_sell=500_000_000,
        min_sol_output=123_456,
    ):
        exit_execution = SimpleNamespace(
            contract_version=(
                EXIT_EXECUTION_CONTRACT_VERSION
            ),
            venue=(
                PUMP_BONDING_CURVE_VENUE
            ),
            tokens_in=(
                tokens_to_sell
            ),
            executable=True,
            min_quote_out=(
                min_sol_output
            ),
        )

        return LivePumpSellAuthorization(
            authorization_version=(
                LIVE_PUMP_SELL_AUTHORIZATION_VERSION
            ),
            authorization_sha256=(
                "ab" * 32
            ),
            wallet_pubkey=str(
                self.wallet
            ),
            mint=str(
                self.mint
            ),

            bonding_curve=str(
                self.curve
            ),
            base_token_program=str(
                TOKEN_2022_PROGRAM
            ),
            associated_base_user=str(
                self.base_user
            ),

            creator=str(
                self.creator
            ),
            mayhem_mode=(
                mayhem_mode
            ),

            curve_quote_mint=(
                SOL_QUOTE_MINT
            ),
            quote_mint_for_instruction=(
                WRAPPED_SOL_MINT
            ),

            tokens_to_sell=(
                tokens_to_sell
            ),

            allocation=object(),

            fee_state_version=(
                "live-pump-fee-state-v1"
            ),
            fee_rpc_slot=(
                fee_rpc_slot
            ),
            fee_fetched_at=100.0,

            protocol_fee_bps=100,
            creator_fee_bps=50,

            slippage_bps=300,
            base_network_fee_lamports=5_000,
            priority_fee_lamports=7_000,

            exit_execution=(
                exit_execution
            ),

            authorized_at=100.0,
        )

    def make_global_state(
        self,
        *,
        rpc_slot=101,
        mayhem_enabled=True,
        normal_recipients=None,
    ):
        if normal_recipients is None:
            normal_recipients = (
                self.normal_recipients
            )

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
                tuple(
                    normal_recipients
                )
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

    def resolve(
        self,
        *,
        authorization=None,
        global_state=None,
    ):
        if authorization is None:
            authorization = (
                self.make_authorization()
            )

        if global_state is None:
            global_state = (
                self.make_global_state()
            )

        return (
            resolve_pump_sell_v2_account_context(
                authorization=authorization,
                global_state=global_state,
            )
        )

    def test_resolves_exact_26_account_order(
        self,
    ):
        context = self.resolve()

        accounts = (
            context.ordered_accounts()
        )

        named = (
            context.ordered_named_accounts()
        )

        self.assertEqual(
            len(accounts),
            26,
        )

        self.assertEqual(
            len(
                SELL_V2_ACCOUNT_NAMES
            ),
            26,
        )

        self.assertEqual(
            tuple(
                name
                for name, _
                in named
            ),
            SELL_V2_ACCOUNT_NAMES,
        )

        self.assertNotIn(
            "global_volume_accumulator",
            SELL_V2_ACCOUNT_NAMES,
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
            accounts[14],
            str(
                self.base_user
            ),
        )

        self.assertEqual(
            accounts[22],
            context.fee_program,
        )

        self.assertEqual(
            accounts[23],
            str(
                Pubkey.default()
            ),
        )

        self.assertEqual(
            accounts[25],
            str(
                PUMP_PROGRAM
            ),
        )

        self.assertEqual(
            context.amount,
            500_000_000,
        )

        self.assertEqual(
            context.min_sol_output,
            123_456,
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
            resolve_pump_sell_v2_account_context(
                authorization=authorization,
                global_state=global_state,
            )
        )

        second = (
            resolve_pump_sell_v2_account_context(
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

        self.assertEqual(
            first.fee_recipient_selector_version,
            SELL_FEE_RECIPIENT_SELECTOR_VERSION,
        )

        self.assertEqual(
            first.buyback_recipient_selector_version,
            SELL_BUYBACK_RECIPIENT_SELECTOR_VERSION,
        )

    def test_mayhem_uses_reserved_recipient_set(
        self,
    ):
        context = self.resolve(
            authorization=(
                self.make_authorization(
                    mayhem_mode=True
                )
            ),
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

    def test_global_state_cannot_predate_authorized_sell(
        self,
    ):
        with self.assertRaises(
            PumpSellV2AccountContextError
        ):
            self.resolve(
                authorization=(
                    self.make_authorization(
                        fee_rpc_slot=100
                    )
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
            PumpSellV2AccountContextError
        ):
            self.resolve(
                authorization=authorization
            )

    def test_authorized_base_user_mismatch_rejected(
        self,
    ):
        authorization = replace(
            self.make_authorization(),
            associated_base_user=str(
                Pubkey.new_unique()
            ),
        )

        with self.assertRaises(
            PumpSellV2AccountContextError
        ):
            self.resolve(
                authorization=authorization
            )

    def test_quote_identity_mismatch_rejected(
        self,
    ):
        cases = (
            replace(
                self.make_authorization(),
                curve_quote_mint=(
                    WRAPPED_SOL_MINT
                ),
            ),
            replace(
                self.make_authorization(),
                quote_mint_for_instruction=(
                    SOL_QUOTE_MINT
                ),
            ),
        )

        for authorization in cases:
            with self.subTest(
                authorization=authorization
            ):
                with self.assertRaises(
                    PumpSellV2AccountContextError
                ):
                    self.resolve(
                        authorization=(
                            authorization
                        )
                    )

    def test_exit_execution_binding_is_required(
        self,
    ):
        authorization = (
            self.make_authorization()
        )

        bad_execution = SimpleNamespace(
            contract_version=(
                EXIT_EXECUTION_CONTRACT_VERSION
            ),
            venue=(
                PUMP_BONDING_CURVE_VENUE
            ),
            tokens_in=(
                authorization.tokens_to_sell
                - 1
            ),
            executable=True,
            min_quote_out=123_456,
        )

        authorization = replace(
            authorization,
            exit_execution=(
                bad_execution
            ),
        )

        with self.assertRaises(
            PumpSellV2AccountContextError
        ):
            self.resolve(
                authorization=authorization
            )

    def test_authorization_fingerprint_must_be_sha256(
        self,
    ):
        authorization = replace(
            self.make_authorization(),
            authorization_sha256="not-a-hash",
        )

        with self.assertRaises(
            PumpSellV2AccountContextError
        ):
            self.resolve(
                authorization=authorization
            )

    def test_public_contract_is_pure_context_only(
        self,
    ):
        import inspect

        parameters = (
            inspect.signature(
                resolve_pump_sell_v2_account_context
            )
            .parameters
        )

        self.assertEqual(
            set(
                parameters
            ),
            {
                "authorization",
                "global_state",
            },
        )

        self.assertEqual(
            PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION,
            "pump-sell-v2-account-context-v1",
        )


if __name__ == "__main__":
    unittest.main()
