import struct
import unittest
from dataclasses import replace

from solders.pubkey import Pubkey

from src.execution.live_pump_fee_state import (
    PUMP_FEE_PROGRAM,
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
    SELL_V2_ACCOUNT_META_SPEC,
    SELL_V2_DISCRIMINATOR,
    PumpSellV2InstructionError,
    build_pump_sell_v2_instruction,
)
from src.safety.token_safety_resolver import (
    ASSOCIATED_TOKEN_PROGRAM,
    PUMP_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
)


class PumpSellV2InstructionTests(
    unittest.TestCase
):
    def make_context(
        self,
    ) -> PumpSellV2AccountContext:
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
                "live-pump-sell-authorization-v2"
            ),
            authorization_sha256=(
                "ab" * 32
            ),

            authorization_fee_rpc_slot=100,
            global_rpc_slot=101,

            fee_recipient_selector_version=(
                SELL_FEE_RECIPIENT_SELECTOR_VERSION
            ),
            fee_recipient_index=2,

            buyback_recipient_selector_version=(
                SELL_BUYBACK_RECIPIENT_SELECTOR_VERSION
            ),
            buyback_fee_recipient_index=5,

            amount=123_456_789,
            min_sol_output=8_765_432,

            global_account=random_accounts[0],
            base_mint=random_accounts[1],
            quote_mint=(
                WRAPPED_SOL_MINT
            ),
            base_token_program=str(
                TOKEN_2022_PROGRAM
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

            bonding_curve=random_accounts[10],
            associated_base_bonding_curve=(
                random_accounts[11]
            ),
            associated_quote_bonding_curve=(
                random_accounts[12]
            ),

            user=random_accounts[13],
            associated_base_user=(
                random_accounts[14]
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

    def test_builds_real_solders_instruction(
        self,
    ):
        context = self.make_context()

        result = (
            build_pump_sell_v2_instruction(
                context=context
            )
        )

        self.assertEqual(
            result.builder_version,
            PUMP_SELL_V2_INSTRUCTION_VERSION,
        )

        self.assertEqual(
            result.instruction.program_id,
            PUMP_PROGRAM,
        )

        self.assertEqual(
            len(
                result.instruction.accounts
            ),
            26,
        )

        self.assertEqual(
            result.context_version,
            context.resolver_version,
        )

        self.assertEqual(
            result.authorization_sha256,
            context.authorization_sha256,
        )

    def test_instruction_data_exactly_matches_idl(
        self,
    ):
        context = self.make_context()

        result = (
            build_pump_sell_v2_instruction(
                context=context
            )
        )

        expected = (
            SELL_V2_DISCRIMINATOR
            + struct.pack(
                "<QQ",
                context.amount,
                context.min_sol_output,
            )
        )

        self.assertEqual(
            bytes(
                result.instruction.data
            ),
            expected,
        )

        self.assertEqual(
            len(expected),
            24,
        )

        self.assertEqual(
            result.instruction_data_hex,
            expected.hex(),
        )

        self.assertEqual(
            SELL_V2_DISCRIMINATOR,
            bytes(
                [
                    93,
                    246,
                    130,
                    60,
                    231,
                    233,
                    64,
                    178,
                ]
            ),
        )

    def test_account_flags_exactly_match_idl(
        self,
    ):
        context = self.make_context()

        result = (
            build_pump_sell_v2_instruction(
                context=context
            )
        )

        actual = tuple(
            (
                meta.is_signer,
                meta.is_writable,
            )
            for meta
            in result.instruction.accounts
        )

        expected = tuple(
            (
                is_signer,
                is_writable,
            )
            for (
                _,
                is_signer,
                is_writable,
            )
            in SELL_V2_ACCOUNT_META_SPEC
        )

        self.assertEqual(
            actual,
            expected,
        )

        self.assertEqual(
            sum(
                1
                for signer, _
                in actual
                if signer
            ),
            1,
        )

        self.assertEqual(
            sum(
                1
                for _, writable
                in actual
                if writable
            ),
            14,
        )

        self.assertTrue(
            result
            .instruction
            .accounts[13]
            .is_signer
        )

        self.assertTrue(
            result
            .instruction
            .accounts[13]
            .is_writable
        )

        self.assertFalse(
            result
            .instruction
            .accounts[18]
            .is_writable
        )

    def test_account_pubkeys_preserve_context_order(
        self,
    ):
        context = self.make_context()

        result = (
            build_pump_sell_v2_instruction(
                context=context
            )
        )

        actual = tuple(
            str(
                meta.pubkey
            )
            for meta
            in result.instruction.accounts
        )

        self.assertEqual(
            actual,
            context.ordered_accounts(),
        )

    def test_instruction_fingerprint_is_deterministic(
        self,
    ):
        context = self.make_context()

        first = (
            build_pump_sell_v2_instruction(
                context=context
            )
        )

        second = (
            build_pump_sell_v2_instruction(
                context=context
            )
        )

        self.assertEqual(
            first.instruction_sha256,
            second.instruction_sha256,
        )

        self.assertEqual(
            first.instruction_data_hex,
            second.instruction_data_hex,
        )

        changed = (
            build_pump_sell_v2_instruction(
                context=replace(
                    context,
                    min_sol_output=(
                        context.min_sol_output
                        - 1
                    ),
                )
            )
        )

        self.assertNotEqual(
            first.instruction_sha256,
            changed.instruction_sha256,
        )

    def test_wrong_program_rejected(
        self,
    ):
        context = replace(
            self.make_context(),
            program=str(
                Pubkey.new_unique()
            ),
        )

        with self.assertRaises(
            PumpSellV2InstructionError
        ):
            build_pump_sell_v2_instruction(
                context=context
            )

    def test_canonical_program_identity_is_enforced(
        self,
    ):
        cases = (
            replace(
                self.make_context(),
                quote_mint=str(
                    Pubkey.new_unique()
                ),
            ),
            replace(
                self.make_context(),
                quote_token_program=str(
                    Pubkey.new_unique()
                ),
            ),
            replace(
                self.make_context(),
                fee_program=str(
                    Pubkey.new_unique()
                ),
            ),
        )

        for context in cases:
            with self.subTest(
                context=context
            ):
                with self.assertRaises(
                    PumpSellV2InstructionError
                ):
                    build_pump_sell_v2_instruction(
                        context=context
                    )

    def test_u64_contract_is_enforced(
        self,
    ):
        cases = (
            replace(
                self.make_context(),
                amount=0,
            ),
            replace(
                self.make_context(),
                amount=(
                    1 << 64
                ),
            ),
            replace(
                self.make_context(),
                min_sol_output=-1,
            ),
            replace(
                self.make_context(),
                min_sol_output=(
                    1 << 64
                ),
            ),
        )

        for context in cases:
            with self.subTest(
                context=context
            ):
                with self.assertRaises(
                    PumpSellV2InstructionError
                ):
                    build_pump_sell_v2_instruction(
                        context=context
                    )


if __name__ == "__main__":
    unittest.main()
