import time
import unittest
from dataclasses import replace

from solders.hash import Hash
from solders.pubkey import Pubkey

from src.execution.live_pump_fee_state import (
    PUMP_FEE_PROGRAM,
)
from src.execution.order_authorization import (
    AUTHORIZATION_VERSION,
    AUTHORIZE,
    BUY,
    WRAPPED_SOL_MINT,
    OrderAuthorization,
)
from src.execution.pump_buy_v2_account_context import (
    BUYBACK_RECIPIENT_SELECTOR_VERSION,
    FEE_RECIPIENT_SELECTOR_VERSION,
    PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION,
    PumpBuyV2AccountContext,
)
from src.execution.pump_buy_v2_instruction import (
    build_pump_buy_v2_instruction,
)
from src.execution.pump_buy_v2_unsigned_message import (
    MAX_COMPUTE_UNIT_LIMIT,
    PUMP_BUY_V2_UNSIGNED_MESSAGE_VERSION,
    SOLANA_PACKET_DATA_SIZE,
    PumpBuyV2UnsignedMessageError,
    build_unsigned_pump_buy_v2_message,
)
from src.portfolio.live_reservations import (
    RESERVATION_VERSION,
)
from src.safety.token_safety_resolver import (
    ASSOCIATED_TOKEN_PROGRAM,
    PUMP_PROGRAM,
    TOKEN_PROGRAM,
)


class PumpBuyV2UnsignedMessageTests(
    unittest.TestCase
):
    def setUp(self):
        self.wallet = (
            Pubkey.new_unique()
        )

        self.accounts = [
            Pubkey.new_unique()
            for _ in range(
                27
            )
        ]

        self.simulation_sha256 = (
            "ab" * 32
        )

        self.blockhash = (
            Hash.new_unique()
        )

    def make_authorization(
        self,
        *,
        priority_fee_lamports=7_000,
    ) -> OrderAuthorization:

        now = time.time()

        return OrderAuthorization(
            authorization_version=(
                AUTHORIZATION_VERSION
            ),
            status=AUTHORIZE,
            reasons=(),
            mint=str(
                self.accounts[
                    1
                ]
            ),
            side=BUY,
            wallet_pubkey=str(
                self.wallet
            ),
            bonding_curve=str(
                self.accounts[
                    10
                ]
            ),
            associated_bonding_curve=str(
                self.accounts[
                    11
                ]
            ),
            base_token_program=str(
                TOKEN_PROGRAM
            ),
            creator=str(
                self.accounts[
                    16
                ]
            ),
            mayhem_mode=False,
            quote_mint_for_instruction=(
                WRAPPED_SOL_MINT
            ),
            token_amount=123_456_789,
            max_sol_cost=9_000_000,
            spend_lamports=9_000_000,
            wallet_cost_lamports=(
                9_000_000
                + 5_000
                + priority_fee_lamports
            ),
            base_network_fee_lamports=(
                5_000
            ),
            priority_fee_lamports=(
                priority_fee_lamports
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
                self.simulation_sha256
            ),
            created_at=now,
            expires_at=(
                now
                + 30.0
            ),
        )

    def make_context(
        self,
    ) -> PumpBuyV2AccountContext:

        return PumpBuyV2AccountContext(
            resolver_version=(
                PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION
            ),
            authorization_version=(
                AUTHORIZATION_VERSION
            ),
            reservation_id=(
                "reservation-123"
            ),
            simulation_sha256=(
                self.simulation_sha256
            ),
            global_rpc_slot=101,
            fee_recipient_selector_version=(
                FEE_RECIPIENT_SELECTOR_VERSION
            ),
            fee_recipient_index=2,
            buyback_recipient_selector_version=(
                BUYBACK_RECIPIENT_SELECTOR_VERSION
            ),
            buyback_fee_recipient_index=5,
            amount=123_456_789,
            max_sol_cost=9_000_000,

            global_account=str(
                self.accounts[
                    0
                ]
            ),
            base_mint=str(
                self.accounts[
                    1
                ]
            ),
            quote_mint=(
                WRAPPED_SOL_MINT
            ),
            base_token_program=str(
                TOKEN_PROGRAM
            ),
            quote_token_program=str(
                TOKEN_PROGRAM
            ),
            associated_token_program=str(
                ASSOCIATED_TOKEN_PROGRAM
            ),
            fee_recipient=str(
                self.accounts[
                    6
                ]
            ),
            associated_quote_fee_recipient=str(
                self.accounts[
                    7
                ]
            ),
            buyback_fee_recipient=str(
                self.accounts[
                    8
                ]
            ),
            associated_quote_buyback_fee_recipient=str(
                self.accounts[
                    9
                ]
            ),
            bonding_curve=str(
                self.accounts[
                    10
                ]
            ),
            associated_base_bonding_curve=str(
                self.accounts[
                    11
                ]
            ),
            associated_quote_bonding_curve=str(
                self.accounts[
                    12
                ]
            ),
            user=str(
                self.wallet
            ),
            associated_base_user=str(
                self.accounts[
                    14
                ]
            ),
            associated_quote_user=str(
                self.accounts[
                    15
                ]
            ),
            creator_vault=str(
                self.accounts[
                    16
                ]
            ),
            associated_creator_vault=str(
                self.accounts[
                    17
                ]
            ),
            sharing_config=str(
                self.accounts[
                    18
                ]
            ),
            global_volume_accumulator=str(
                self.accounts[
                    19
                ]
            ),
            user_volume_accumulator=str(
                self.accounts[
                    20
                ]
            ),
            associated_user_volume_accumulator=str(
                self.accounts[
                    21
                ]
            ),
            fee_config=str(
                self.accounts[
                    22
                ]
            ),
            fee_program=str(
                PUMP_FEE_PROGRAM
            ),
            system_program=str(
                Pubkey.default()
            ),
            event_authority=str(
                self.accounts[
                    25
                ]
            ),
            program=str(
                PUMP_PROGRAM
            ),
        )

    def build(
        self,
        *,
        authorization=None,
        context=None,
        blockhash=None,
        compute_unit_limit=250_000,
    ):
        return (
            build_unsigned_pump_buy_v2_message(
                authorization=(
                    self.make_authorization()
                    if authorization is None
                    else authorization
                ),
                context=(
                    self.make_context()
                    if context is None
                    else context
                ),
                recent_blockhash=str(
                    self.blockhash
                    if blockhash is None
                    else blockhash
                ),
                compute_unit_limit=(
                    compute_unit_limit
                ),
            )
        )

    def test_builds_unsigned_message_v0(
        self,
    ):
        result = self.build()

        self.assertEqual(
            result.builder_version,
            PUMP_BUY_V2_UNSIGNED_MESSAGE_VERSION,
        )

        self.assertEqual(
            len(
                result.message.instructions
            ),
            3,
        )

        self.assertEqual(
            len(
                result.message.address_table_lookups
            ),
            0,
        )

        self.assertLessEqual(
            (
                result
                .estimated_signed_transaction_size_bytes
            ),
            SOLANA_PACKET_DATA_SIZE,
        )

    def test_payer_is_exact_only_signer(
        self,
    ):
        result = self.build()

        message = result.message

        self.assertEqual(
            message
            .header
            .num_required_signatures,
            1,
        )

        self.assertEqual(
            message.account_keys[
                0
            ],
            self.wallet,
        )

        self.assertEqual(
            tuple(
                message.account_keys[
                    :1
                ]
            ),
            (
                self.wallet,
            ),
        )

    def test_priority_fee_is_bounded_and_deterministic(
        self,
    ):
        first = self.build()
        second = self.build()

        self.assertEqual(
            first.compute_unit_limit,
            250_000,
        )

        self.assertEqual(
            first.compute_unit_price_micro_lamports,
            28_000,
        )

        self.assertEqual(
            first.planned_priority_fee_lamports,
            7_000,
        )

        self.assertEqual(
            first.authorized_priority_fee_lamports,
            7_000,
        )

        self.assertLessEqual(
            first.planned_priority_fee_lamports,
            first.authorized_priority_fee_lamports,
        )

        self.assertEqual(
            first.compute_unit_price_micro_lamports,
            second.compute_unit_price_micro_lamports,
        )

        self.assertEqual(
            first.message_sha256,
            second.message_sha256,
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
            result.compute_unit_price_micro_lamports,
            0,
        )

        self.assertEqual(
            result.planned_priority_fee_lamports,
            0,
        )

    def test_compiled_pump_instruction_is_preserved(
        self,
    ):
        context = self.make_context()

        expected = (
            build_pump_buy_v2_instruction(
                context=context
            )
        )

        result = self.build(
            context=context
        )

        compiled = (
            result.message.instructions[
                2
            ]
        )

        account_keys = (
            result.message.account_keys
        )

        self.assertEqual(
            account_keys[
                compiled.program_id_index
            ],
            expected.instruction.program_id,
        )

        self.assertEqual(
            bytes(
                compiled.data
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
            in compiled.accounts
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

    def test_context_transaction_identity_mismatch_rejected(
        self,
    ):
        authorization = (
            self.make_authorization()
        )

        mutations = (
            (
                "base_mint",
                str(
                    Pubkey.new_unique()
                ),
            ),
            (
                "quote_mint",
                str(
                    Pubkey.new_unique()
                ),
            ),
            (
                "base_token_program",
                str(
                    Pubkey.new_unique()
                ),
            ),
            (
                "bonding_curve",
                str(
                    Pubkey.new_unique()
                ),
            ),
            (
                "associated_base_bonding_curve",
                str(
                    Pubkey.new_unique()
                ),
            ),
        )

        for (
            field,
            value,
        ) in mutations:
            with self.subTest(
                field=field
            ):
                context = replace(
                    self.make_context(),
                    **{
                        field: value
                    },
                )

                with self.assertRaises(
                    PumpBuyV2UnsignedMessageError
                ):
                    self.build(
                        authorization=authorization,
                        context=context,
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
            PumpBuyV2UnsignedMessageError
        ):
            self.build(
                authorization=authorization,
                compute_unit_limit=(
                    MAX_COMPUTE_UNIT_LIMIT
                ),
            )

    def test_context_wallet_mismatch_rejected(
        self,
    ):
        context = replace(
            self.make_context(),
            user=str(
                Pubkey.new_unique()
            ),
        )

        with self.assertRaises(
            PumpBuyV2UnsignedMessageError
        ):
            self.build(
                context=context
            )

    def test_reservation_identity_mismatch_rejected(
        self,
    ):
        context = replace(
            self.make_context(),
            reservation_id=(
                "different-reservation"
            ),
        )

        with self.assertRaises(
            PumpBuyV2UnsignedMessageError
        ):
            self.build(
                context=context
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
            PumpBuyV2UnsignedMessageError
        ):
            self.build(
                authorization=authorization
            )

    def test_default_blockhash_rejected(
        self,
    ):
        with self.assertRaises(
            PumpBuyV2UnsignedMessageError
        ):
            self.build(
                blockhash=Hash.default()
            )

    def test_invalid_compute_unit_limit_rejected(
        self,
    ):
        for invalid in (
            0,
            MAX_COMPUTE_UNIT_LIMIT
            + 1,
        ):
            with self.subTest(
                invalid=invalid
            ):
                with self.assertRaises(
                    PumpBuyV2UnsignedMessageError
                ):
                    self.build(
                        compute_unit_limit=invalid
                    )


if __name__ == "__main__":
    unittest.main()
