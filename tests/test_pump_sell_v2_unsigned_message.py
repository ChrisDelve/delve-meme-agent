import unittest
from dataclasses import replace
from types import SimpleNamespace

from solders.compute_budget import (
    ID as COMPUTE_BUDGET_PROGRAM,
)
from solders.hash import Hash
from solders.message import MessageV0
from solders.pubkey import Pubkey

from src.execution.live_blockhash_context import (
    LIVE_BLOCKHASH_CONTEXT_VERSION,
    LiveBlockhashContext,
)
from src.execution.live_pump_fee_state import (
    PUMP_FEE_PROGRAM,
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
    PumpSellV2AccountContext,
)
from src.execution.pump_sell_v2_instruction import (
    PUMP_SELL_V2_INSTRUCTION_VERSION,
    build_pump_sell_v2_instruction,
)
from src.execution.pump_sell_v2_unsigned_message import (
    PUMP_SELL_V2_UNSIGNED_MESSAGE_VERSION,
    PumpSellV2UnsignedMessageError,
    build_unsigned_pump_sell_v2_message,
)
from src.safety.token_safety_gate import (
    SOL_QUOTE_MINT,
)
from src.safety.token_safety_resolver import (
    ASSOCIATED_TOKEN_PROGRAM,
    COMMITMENT,
    PUMP_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
)


class PumpSellV2UnsignedMessageTests(
    unittest.TestCase
):
    def setUp(self):
        self.wallet = (
            Pubkey.new_unique()
        )

        self.mint = (
            Pubkey.new_unique()
        )

        self.curve = (
            Pubkey.new_unique()
        )

        self.creator = (
            Pubkey.new_unique()
        )

        self.blockhash = (
            Hash.new_unique()
        )

    def make_authorization(
        self,
        *,
        priority_fee_lamports=7_000,
        fee_rpc_slot=100,
    ):
        exit_execution = SimpleNamespace(
            min_quote_out=8_765_432,
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
                Pubkey.new_unique()
            ),

            creator=str(
                self.creator
            ),
            mayhem_mode=False,

            curve_quote_mint=(
                SOL_QUOTE_MINT
            ),
            quote_mint_for_instruction=(
                WRAPPED_SOL_MINT
            ),

            tokens_to_sell=123_456_789,

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
            priority_fee_lamports=(
                priority_fee_lamports
            ),

            exit_execution=(
                exit_execution
            ),

            authorized_at=100.0,
        )

    def make_context(
        self,
        authorization,
        *,
        global_rpc_slot=101,
    ):
        random_accounts = [
            str(
                Pubkey.new_unique()
            )
            for _ in range(
                26
            )
        ]

        return PumpSellV2AccountContext(
            resolver_version=(
                PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION
            ),

            authorization_version=(
                authorization.authorization_version
            ),
            authorization_sha256=(
                authorization.authorization_sha256
            ),

            authorization_fee_rpc_slot=(
                authorization.fee_rpc_slot
            ),
            global_rpc_slot=(
                global_rpc_slot
            ),

            fee_recipient_selector_version=(
                SELL_FEE_RECIPIENT_SELECTOR_VERSION
            ),
            fee_recipient_index=2,

            buyback_recipient_selector_version=(
                SELL_BUYBACK_RECIPIENT_SELECTOR_VERSION
            ),
            buyback_fee_recipient_index=5,

            amount=(
                authorization.tokens_to_sell
            ),
            min_sol_output=(
                authorization
                .exit_execution
                .min_quote_out
            ),

            global_account=random_accounts[0],
            base_mint=(
                authorization.mint
            ),
            quote_mint=(
                authorization
                .quote_mint_for_instruction
            ),
            base_token_program=(
                authorization.base_token_program
            ),
            quote_token_program=str(
                TOKEN_PROGRAM
            ),
            associated_token_program=str(
                ASSOCIATED_TOKEN_PROGRAM
            ),

            fee_recipient=random_accounts[6],
            associated_quote_fee_recipient=(
                random_accounts[7]
            ),

            buyback_fee_recipient=(
                random_accounts[8]
            ),
            associated_quote_buyback_fee_recipient=(
                random_accounts[9]
            ),

            bonding_curve=(
                authorization.bonding_curve
            ),
            associated_base_bonding_curve=(
                random_accounts[11]
            ),
            associated_quote_bonding_curve=(
                random_accounts[12]
            ),

            user=(
                authorization.wallet_pubkey
            ),
            associated_base_user=(
                authorization.associated_base_user
            ),
            associated_quote_user=(
                random_accounts[15]
            ),

            creator_vault=random_accounts[16],
            associated_creator_vault=(
                random_accounts[17]
            ),

            sharing_config=random_accounts[18],

            user_volume_accumulator=(
                random_accounts[19]
            ),
            associated_user_volume_accumulator=(
                random_accounts[20]
            ),

            fee_config=random_accounts[21],
            fee_program=str(
                PUMP_FEE_PROGRAM
            ),

            system_program=str(
                Pubkey.default()
            ),
            event_authority=random_accounts[24],
            program=str(
                PUMP_PROGRAM
            ),
        )

    def make_blockhash_context(
        self,
        *,
        min_context_slot=101,
        rpc_slot=102,
        blockhash=None,
    ):
        return LiveBlockhashContext(
            resolver_version=(
                LIVE_BLOCKHASH_CONTEXT_VERSION
            ),
            blockhash=str(
                self.blockhash
                if blockhash is None
                else blockhash
            ),
            last_valid_block_height=500,
            rpc_slot=rpc_slot,
            min_context_slot=(
                min_context_slot
            ),
            commitment=COMMITMENT,
            fetched_at=100.0,
        )

    def build(
        self,
        *,
        authorization=None,
        context=None,
        blockhash_context=None,
        compute_unit_limit=250_000,
    ):
        if authorization is None:
            authorization = (
                self.make_authorization()
            )

        if context is None:
            context = (
                self.make_context(
                    authorization
                )
            )

        if blockhash_context is None:
            blockhash_context = (
                self.make_blockhash_context()
            )

        return (
            build_unsigned_pump_sell_v2_message(
                authorization=authorization,
                context=context,
                blockhash_context=(
                    blockhash_context
                ),
                compute_unit_limit=(
                    compute_unit_limit
                ),
            )
        )

    def test_builds_unsigned_message_v0(
        self,
    ):
        authorization = (
            self.make_authorization()
        )

        context = (
            self.make_context(
                authorization
            )
        )

        result = self.build(
            authorization=authorization,
            context=context,
        )

        self.assertEqual(
            result.builder_version,
            PUMP_SELL_V2_UNSIGNED_MESSAGE_VERSION,
        )

        self.assertIsInstance(
            result.message,
            MessageV0,
        )

        self.assertEqual(
            result.authorization_sha256,
            authorization.authorization_sha256,
        )

        self.assertEqual(
            result.account_context_version,
            PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION,
        )

        self.assertEqual(
            result.instruction_builder_version,
            PUMP_SELL_V2_INSTRUCTION_VERSION,
        )

        self.assertEqual(
            result.payer,
            authorization.wallet_pubkey,
        )

        self.assertEqual(
            result.recent_blockhash,
            str(
                self.blockhash
            ),
        )

        self.assertEqual(
            result.last_valid_block_height,
            500,
        )

    def test_payer_is_exact_only_signer(
        self,
    ):
        result = self.build()

        message = result.message

        self.assertEqual(
            message.header.num_required_signatures,
            1,
        )

        self.assertEqual(
            message.account_keys[0],
            self.wallet,
        )

        self.assertEqual(
            tuple(
                message.account_keys[
                    :message.header
                    .num_required_signatures
                ]
            ),
            (
                self.wallet,
            ),
        )

    def test_priority_fee_is_exact_and_deterministic(
        self,
    ):
        first = self.build()
        second = self.build()

        self.assertEqual(
            first.compute_unit_limit,
            250_000,
        )

        self.assertEqual(
            first
            .compute_unit_price_micro_lamports,
            28_000,
        )

        self.assertEqual(
            first.planned_priority_fee_lamports,
            7_000,
        )

        self.assertEqual(
            first.planned_priority_fee_lamports,
            first.authorized_priority_fee_lamports,
        )

        self.assertEqual(
            first
            .compute_unit_price_micro_lamports,
            second
            .compute_unit_price_micro_lamports,
        )

    def test_zero_priority_budget_stays_zero(
        self,
    ):
        authorization = (
            self.make_authorization(
                priority_fee_lamports=0
            )
        )

        result = self.build(
            authorization=authorization
        )

        self.assertEqual(
            result
            .compute_unit_price_micro_lamports,
            0,
        )

        self.assertEqual(
            result.planned_priority_fee_lamports,
            0,
        )

    def test_compiled_instruction_contract_is_preserved(
        self,
    ):
        authorization = (
            self.make_authorization()
        )

        context = (
            self.make_context(
                authorization
            )
        )

        result = self.build(
            authorization=authorization,
            context=context,
        )

        message = result.message
        account_keys = tuple(
            message.account_keys
        )

        instructions = tuple(
            message.instructions
        )

        self.assertEqual(
            len(instructions),
            3,
        )

        for compiled in instructions[
            :2
        ]:
            self.assertEqual(
                account_keys[
                    compiled.program_id_index
                ],
                COMPUTE_BUDGET_PROGRAM,
            )

        expected = (
            build_pump_sell_v2_instruction(
                context=context
            )
        )

        compiled_pump = (
            instructions[2]
        )

        self.assertEqual(
            account_keys[
                compiled_pump.program_id_index
            ],
            PUMP_PROGRAM,
        )

        self.assertEqual(
            bytes(
                compiled_pump.data
            ),
            bytes(
                expected.instruction.data
            ),
        )

        actual_accounts = tuple(
            account_keys[
                index
            ]
            for index
            in compiled_pump.accounts
        )

        expected_accounts = tuple(
            meta.pubkey
            for meta
            in expected.instruction.accounts
        )

        self.assertEqual(
            actual_accounts,
            expected_accounts,
        )

        self.assertEqual(
            result.pump_instruction_sha256,
            expected.instruction_sha256,
        )

    def test_context_authorization_identity_mismatch_rejected(
        self,
    ):
        authorization = (
            self.make_authorization()
        )

        context = replace(
            self.make_context(
                authorization
            ),
            authorization_sha256=(
                "cd" * 32
            ),
        )

        with self.assertRaises(
            PumpSellV2UnsignedMessageError
        ):
            self.build(
                authorization=authorization,
                context=context,
            )

    def test_blockhash_must_be_anchored_after_sell_state(
        self,
    ):
        authorization = (
            self.make_authorization(
                fee_rpc_slot=100
            )
        )

        context = (
            self.make_context(
                authorization,
                global_rpc_slot=101,
            )
        )

        blockhash_context = (
            self.make_blockhash_context(
                min_context_slot=100,
                rpc_slot=102,
            )
        )

        with self.assertRaises(
            PumpSellV2UnsignedMessageError
        ):
            self.build(
                authorization=authorization,
                context=context,
                blockhash_context=(
                    blockhash_context
                ),
            )

    def test_default_blockhash_rejected(
        self,
    ):
        blockhash_context = (
            self.make_blockhash_context(
                blockhash=Hash.default()
            )
        )

        with self.assertRaises(
            PumpSellV2UnsignedMessageError
        ):
            self.build(
                blockhash_context=(
                    blockhash_context
                )
            )

    def test_compute_limit_must_represent_exact_priority_fee(
        self,
    ):
        authorization = (
            self.make_authorization(
                priority_fee_lamports=1
            )
        )

        with self.assertRaises(
            PumpSellV2UnsignedMessageError
        ):
            self.build(
                authorization=authorization,
                compute_unit_limit=1_400_000,
            )

    def test_invalid_compute_unit_limit_rejected(
        self,
    ):
        for invalid in (
            0,
            -1,
            1_400_001,
            True,
        ):
            with self.subTest(
                invalid=invalid
            ):
                with self.assertRaises(
                    PumpSellV2UnsignedMessageError
                ):
                    self.build(
                        compute_unit_limit=invalid
                    )


if __name__ == "__main__":
    unittest.main()
