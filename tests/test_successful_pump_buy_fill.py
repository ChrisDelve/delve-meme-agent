import base64
import hashlib
import json
import struct

import base58
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from solders.hash import Hash
from solders.instruction import (
    AccountMeta,
    Instruction,
)
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.transaction import (
    VersionedTransaction,
)

from src.data.trade_event import (
    TRADE_EVENT_DISCRIMINATOR,
)
from src.execution.pump_buy_v2_account_context import (
    BUY_V2_ACCOUNT_NAMES,
)
from src.execution.pump_buy_v2_instruction import (
    BUY_V2_ACCOUNT_META_SPEC,
    BUY_V2_DISCRIMINATOR,
)
from src.execution.order_authorization import (
    WRAPPED_SOL_MINT,
)
from src.execution.signed_transaction_receipt import (
    BLOCK as RECEIPT_BLOCK,
    RESOLVED as RECEIPT_RESOLVED,
    UNKNOWN as RECEIPT_UNKNOWN,
    SIGNED_TRANSACTION_RECEIPT_VERSION,
)
from src.execution.successful_pump_buy_fill import (
    BLOCK,
    PROVEN,
    UNKNOWN,
    SUCCESSFUL_PUMP_BUY_FILL_VERSION,
    resolve_successful_pump_buy_fill,
)
from src.portfolio.live_reservations import (
    RESERVATION_VERSION,
    SIGNED,
    SUBMITTED,
)
from src.safety.token_safety_resolver import (
    ASSOCIATED_TOKEN_PROGRAM,
    PUMP_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
)


MODULE = (
    "src.execution."
    "successful_pump_buy_fill"
)


def random_pubkey():
    return Keypair().pubkey()


def derive_ata(
    *,
    owner,
    mint,
    token_program,
):
    address, _ = (
        Pubkey.find_program_address(
            [
                bytes(owner),
                bytes(token_program),
                bytes(mint),
            ],
            ASSOCIATED_TOKEN_PROGRAM,
        )
    )

    return address


class FakeFillRpc:
    def __init__(
        self,
        *,
        response=None,
        error=None,
    ):
        self.response = response
        self.error = error
        self.calls = []

    async def __aenter__(
        self,
    ):
        return self

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        return None

    async def call(
        self,
        method,
        params,
    ):
        self.calls.append(
            (
                method,
                params,
            )
        )

        if self.error is not None:
            raise self.error

        return self.response


class SuccessfulPumpBuyFillTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.db_path = Path(
            "/tmp/success-fill-test.db"
        )

        self.reservation_id = (
            "success-fill-test"
        )

        self.signer = Keypair()

        self.wallet = (
            self.signer.pubkey()
        )

        self.wallet_pubkey = str(
            self.wallet
        )

        self.mint = random_pubkey()
        self.mint_text = str(
            self.mint
        )

        self.base_token_program = (
            TOKEN_2022_PROGRAM
        )

        self.base_ata = derive_ata(
            owner=self.wallet,
            mint=self.mint,
            token_program=(
                self.base_token_program
            ),
        )

        self.amount = 123_456_789
        self.max_sol_cost = 900_000

        self.blockhash = (
            Hash.new_unique()
        )

        self.accounts = {
            name: random_pubkey()
            for name
            in BUY_V2_ACCOUNT_NAMES
        }

        self.accounts.update(
            {
                "base_mint":
                    self.mint,

                "quote_mint":
                    Pubkey.from_string(
                        str(
                            WRAPPED_SOL_MINT
                        )
                    ),

                "base_token_program":
                    self.base_token_program,

                "quote_token_program":
                    TOKEN_PROGRAM,

                "associated_token_program":
                    ASSOCIATED_TOKEN_PROGRAM,

                "user":
                    self.wallet,

                "associated_base_user":
                    self.base_ata,

                "program":
                    PUMP_PROGRAM,
            }
        )

        metas = []

        for (
            name,
            is_signer,
            is_writable,
        ) in BUY_V2_ACCOUNT_META_SPEC:
            metas.append(
                AccountMeta(
                    self.accounts[name],
                    is_signer,
                    is_writable,
                )
            )

        data = (
            BUY_V2_DISCRIMINATOR
            + struct.pack(
                "<QQ",
                self.amount,
                self.max_sol_cost,
            )
        )

        pump_instruction = Instruction(
            PUMP_PROGRAM,
            data,
            metas,
        )

        pre_one = Instruction(
            random_pubkey(),
            b"\x01",
            [],
        )

        pre_two = Instruction(
            random_pubkey(),
            b"\x02",
            [],
        )

        self.message = (
            MessageV0.try_compile(
                self.wallet,
                [
                    pre_one,
                    pre_two,
                    pump_instruction,
                ],
                [],
                self.blockhash,
            )
        )

        self.transaction = (
            VersionedTransaction(
                self.message,
                [
                    self.signer,
                ],
            )
        )

        self.transaction_bytes = bytes(
            self.transaction
        )

        self.signature = str(
            self.transaction.signatures[
                0
            ]
        )

        self.transaction_sha256 = (
            hashlib.sha256(
                self.transaction_bytes
            ).hexdigest()
        )

        self.ata_index = (
            tuple(
                self.message.account_keys
            ).index(
                self.base_ata
            )
        )

        self.payer_pre = 10_000_000
        self.payer_post = 8_900_000
        self.payer_delta = -1_100_000
        self.fee_lamports = 5_000

        self.initial = (
            self.make_reservation()
        )

    def make_reservation(
        self,
        *,
        status=SIGNED,
        mint=None,
        signed_transaction_sha256=None,
    ):
        return SimpleNamespace(
            reservation_id=(
                self.reservation_id
            ),
            reservation_version=(
                RESERVATION_VERSION
            ),
            wallet_pubkey=(
                self.wallet_pubkey
            ),
            mint=(
                self.mint_text
                if mint is None
                else mint
            ),
            side="BUY",
            spend_lamports=(
                self.max_sol_cost
            ),
            wallet_cost_lamports=(
                1_200_000
            ),
            status=status,
            signed_at=2.0,
            transaction_signature=(
                self.signature
            ),
            signed_message_sha256=(
                "11" * 32
            ),
            signed_transaction_sha256=(
                self.transaction_sha256
                if signed_transaction_sha256
                is None
                else signed_transaction_sha256
            ),
            signed_transaction_bytes=(
                self.transaction_bytes
            ),
            recent_blockhash=str(
                self.blockhash
            ),
            last_valid_block_height=350,
            blockhash_rpc_slot=200,
        )

    def receipt(
        self,
        *,
        status=RECEIPT_RESOLVED,
        reasons=(),
        transaction_error=None,
        fee_payer_pubkey=None,
        receipt_sha256=None,
    ):
        return SimpleNamespace(
            resolver_version=(
                SIGNED_TRANSACTION_RECEIPT_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            reservation_id=(
                self.reservation_id
            ),
            transaction_signature=(
                self.signature
            ),
            receipt_slot=230,
            block_time=1_800_000_000,
            transaction_error=(
                transaction_error
            ),
            fee_lamports=(
                self.fee_lamports
            ),
            fee_payer_pubkey=(
                self.wallet_pubkey
                if fee_payer_pubkey
                is None
                else fee_payer_pubkey
            ),
            fee_payer_pre_balance_lamports=(
                self.payer_pre
            ),
            fee_payer_post_balance_lamports=(
                self.payer_post
            ),
            fee_payer_balance_delta_lamports=(
                self.payer_delta
            ),
            persisted_transaction_sha256=(
                self.transaction_sha256
            ),
            receipt_transaction_sha256=(
                self.transaction_sha256
                if receipt_sha256 is None
                else receipt_sha256
            ),
        )

    def encode_event(
        self,
        *,
        token_amount=None,
        user=None,
        mint=None,
        is_buy=True,
    ):
        if token_amount is None:
            token_amount = (
                self.amount
            )

        if user is None:
            user = self.wallet

        if mint is None:
            mint = self.mint

        fee_recipient = (
            random_pubkey()
        )

        creator = random_pubkey()

        quote_mint = (
            self.accounts[
                "quote_mint"
            ]
        )

        def u64(value):
            return struct.pack(
                "<Q",
                value,
            )

        def i64(value):
            return struct.pack(
                "<q",
                value,
            )

        def u32(value):
            return struct.pack(
                "<I",
                value,
            )

        ix_name = b"buy_v2"

        return b"".join(
            [
                TRADE_EVENT_DISCRIMINATOR,
                bytes(mint),
                u64(850_000),
                u64(token_amount),
                (
                    b"\x01"
                    if is_buy
                    else b"\x00"
                ),
                bytes(user),
                i64(1_800_000_000),
                u64(30_000_000_000),
                u64(900_000_000_000),
                u64(850_000),
                u64(700_000_000_000),
                bytes(fee_recipient),
                u64(100),
                u64(10_000),
                bytes(creator),
                u64(50),
                u64(5_000),
                b"\x01",
                u64(0),
                u64(0),
                u64(850_000),
                i64(1_800_000_000),
                u32(
                    len(ix_name)
                ),
                ix_name,
                b"\x00",
                u64(0),
                u64(0),
                u64(0),
                u64(0),
                u32(0),
                bytes(quote_mint),
                u64(850_000),
                u64(30_000_000_000),
                u64(850_000),
            ]
        )

    def token_balance(
        self,
        amount,
        *,
        owner=None,
        token_program=None,
        omit_owner=False,
        omit_program=False,
    ):
        entry = {
            "accountIndex":
                self.ata_index,

            "mint":
                self.mint_text,

            "uiTokenAmount": {
                "amount":
                    str(amount),

                "decimals": 6,
                "uiAmount": None,
                "uiAmountString":
                    str(amount),
            },
        }

        if not omit_owner:
            entry["owner"] = (
                self.wallet_pubkey
                if owner is None
                else owner
            )

        if not omit_program:
            entry["programId"] = (
                str(
                    self.base_token_program
                )
                if token_program is None
                else token_program
            )

        return entry

    def response(
        self,
        *,
        transaction_bytes=None,
        slot=230,
        event_token_amount=None,
        post_token_amount=None,
        pre_token_amount=100,
        omit_pre_token=False,
        pre_ata_lamports=2_039_280,
        post_owner=None,
        post_token_program=None,
        omit_post_owner=False,
        omit_post_program=False,
        event_program=None,
        inner_event=False,
    ):
        if transaction_bytes is None:
            transaction_bytes = (
                self.transaction_bytes
            )

        if post_token_amount is None:
            post_token_amount = (
                pre_token_amount
                + self.amount
            )

        account_count = len(
            self.message.account_keys
        )

        pre_balances = [
            1_000_000
            for _ in range(
                account_count
            )
        ]

        post_balances = list(
            pre_balances
        )

        pre_balances[0] = (
            self.payer_pre
        )

        post_balances[0] = (
            self.payer_post
        )

        pre_balances[
            self.ata_index
        ] = pre_ata_lamports

        post_balances[
            self.ata_index
        ] = 2_039_280

        if omit_pre_token:
            pre_token_balances = []
        else:
            pre_token_balances = [
                self.token_balance(
                    pre_token_amount
                )
            ]

        post_token_balances = [
            self.token_balance(
                post_token_amount,
                owner=post_owner,
                token_program=(
                    post_token_program
                ),
                omit_owner=(
                    omit_post_owner
                ),
                omit_program=(
                    omit_post_program
                ),
            )
        ]

        event = self.encode_event(
            token_amount=(
                self.amount
                if event_token_amount
                is None
                else event_token_amount
            )
        )

        if event_program is None:
            event_program = str(
                PUMP_PROGRAM
            )

        inner_instructions = []

        if inner_event:
            pump_program_index = (
                tuple(
                    self.message.account_keys
                ).index(
                    PUMP_PROGRAM
                )
            )

            inner_instructions = [
                {
                    "index": 2,
                    "instructions": [
                        {
                            "accounts": [],
                            "data": (
                                base58.b58encode(
                                    event
                                ).decode(
                                    "ascii"
                                )
                            ),
                            "programIdIndex": (
                                pump_program_index
                            ),
                            "stackHeight": 2,
                        },
                    ],
                },
            ]

            log_messages = []

        else:
            log_messages = [
                (
                    f"Program "
                    f"{event_program} "
                    f"invoke [1]"
                ),
                (
                    "Program data: "
                    + base64.b64encode(
                        event
                    ).decode(
                        "ascii"
                    )
                ),
                (
                    f"Program "
                    f"{event_program} "
                    f"success"
                ),
            ]

        return {
            "slot": slot,
            "blockTime":
                1_800_000_000,

            "transaction": [
                base64.b64encode(
                    transaction_bytes
                ).decode(
                    "ascii"
                ),
                "base64",
            ],

            "meta": {
                "err": None,
                "fee":
                    self.fee_lamports,

                "preBalances":
                    pre_balances,

                "postBalances":
                    post_balances,

                "preTokenBalances":
                    pre_token_balances,

                "postTokenBalances":
                    post_token_balances,

                "innerInstructions":
                    inner_instructions,

                "logMessages":
                    log_messages,
            },

            "version": 0,
        }

    async def run_fill(
        self,
        *,
        snapshots=None,
        receipt=None,
        response=None,
    ):
        if snapshots is None:
            snapshots = [
                self.initial,
                self.initial,
                self.initial,
            ]

        if receipt is None:
            receipt = self.receipt()

        if response is None:
            response = self.response()

        load_mock = MagicMock(
            side_effect=snapshots
        )

        receipt_mock = AsyncMock(
            return_value=receipt
        )

        rpc = FakeFillRpc(
            response=response
        )

        with (
            patch(
                f"{MODULE}."
                "load_capital_reservation_read_only",
                load_mock,
            ),
            patch(
                f"{MODULE}."
                "resolve_signed_transaction_receipt",
                receipt_mock,
            ),
            patch(
                f"{MODULE}.HeliusRpcClient",
                return_value=rpc,
            ),
        ):
            result = await (
                resolve_successful_pump_buy_fill(
                    reservation_id=(
                        self.reservation_id
                    ),
                    db_path=self.db_path,
                )
            )

        return (
            result,
            load_mock,
            receipt_mock,
            rpc,
        )

    async def test_existing_ata_successful_buy_fill_is_proven(
        self,
    ):
        (
            result,
            load_mock,
            receipt_mock,
            rpc,
        ) = await self.run_fill()

        self.assertEqual(
            result.resolver_version,
            SUCCESSFUL_PUMP_BUY_FILL_VERSION,
        )

        self.assertEqual(
            result.status,
            PROVEN,
        )

        self.assertEqual(
            result.authorized_token_amount,
            self.amount,
        )

        self.assertEqual(
            result.trade_event_token_amount,
            self.amount,
        )

        self.assertEqual(
            result.token_pre_amount,
            100,
        )

        self.assertEqual(
            result.token_post_amount,
            100 + self.amount,
        )

        self.assertEqual(
            result.token_delta,
            self.amount,
        )

        self.assertEqual(
            result.associated_base_user,
            str(
                self.base_ata
            ),
        )

        self.assertEqual(
            result.wallet_pre_balance_lamports,
            self.payer_pre,
        )

        self.assertEqual(
            result.wallet_post_balance_lamports,
            self.payer_post,
        )

        self.assertEqual(
            result.wallet_balance_delta_lamports,
            self.payer_delta,
        )

        self.assertEqual(
            result.wallet_cost_lamports,
            1_100_000,
        )

        self.assertEqual(
            result.protocol_fee_lamports,
            10_000,
        )

        self.assertEqual(
            result.creator_fee_lamports,
            5_000,
        )

        self.assertEqual(
            load_mock.call_count,
            3,
        )

        receipt_mock.assert_awaited_once()

        self.assertEqual(
            len(rpc.calls),
            1,
        )

    async def test_new_ata_missing_pre_token_balance_can_prove_zero(
        self,
    ):
        response = self.response(
            omit_pre_token=True,
            pre_token_amount=0,
            post_token_amount=(
                self.amount
            ),
            pre_ata_lamports=0,
        )

        result, _, _, _ = (
            await self.run_fill(
                response=response
            )
        )

        self.assertEqual(
            result.status,
            PROVEN,
        )

        self.assertEqual(
            result.token_pre_amount,
            0,
        )

        self.assertEqual(
            result.token_delta,
            self.amount,
        )

    async def test_failed_receipt_never_fetches_fill_metadata(
        self,
    ):
        receipt = self.receipt(
            transaction_error={
                "InstructionError": [
                    2,
                    "Custom",
                ],
            }
        )

        (
            result,
            load_mock,
            receipt_mock,
            rpc,
        ) = await self.run_fill(
            snapshots=[
                self.initial,
            ],
            receipt=receipt,
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "TRANSACTION_FAILED_ON_CHAIN",
            result.reasons,
        )

        self.assertEqual(
            load_mock.call_count,
            1,
        )

        receipt_mock.assert_awaited_once()

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_unknown_receipt_never_fetches_fill_metadata(
        self,
    ):
        receipt = self.receipt(
            status=RECEIPT_UNKNOWN,
            reasons=(
                "RPC_UNCERTAIN",
            ),
        )

        result, _, _, rpc = (
            await self.run_fill(
                snapshots=[
                    self.initial,
                ],
                receipt=receipt,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_blocked_receipt_never_fetches_fill_metadata(
        self,
    ):
        receipt = self.receipt(
            status=RECEIPT_BLOCK,
            reasons=(
                "TRANSACTION_NOT_KNOWN",
            ),
        )

        result, _, _, rpc = (
            await self.run_fill(
                snapshots=[
                    self.initial,
                ],
                receipt=receipt,
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_receipt_binding_mismatch_never_fetches_fill_metadata(
        self,
    ):
        receipt = self.receipt(
            fee_payer_pubkey=(
                str(
                    random_pubkey()
                )
            ),
        )

        result, _, _, rpc = (
            await self.run_fill(
                snapshots=[
                    self.initial,
                ],
                receipt=receipt,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SUCCESS_RECEIPT_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_reservation_change_after_receipt_blocks_fill_rpc(
        self,
    ):
        changed = self.make_reservation(
            signed_transaction_sha256=(
                "44" * 32
            ),
        )

        result, load_mock, _, rpc = (
            await self.run_fill(
                snapshots=[
                    self.initial,
                    changed,
                ],
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "RESERVATION_CHANGED_DURING_RECEIPT_CHECK",
            result.reasons,
        )

        self.assertEqual(
            load_mock.call_count,
            2,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_instruction_mint_mismatch_never_fetches_fill_metadata(
        self,
    ):
        reservation = self.make_reservation(
            mint=str(
                random_pubkey()
            ),
        )

        result, _, _, rpc = (
            await self.run_fill(
                snapshots=[
                    reservation,
                    reservation,
                ],
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "BUY_V2_MINT_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_fresh_transaction_hash_mismatch_is_unknown(
        self,
    ):
        response = self.response(
            transaction_bytes=(
                b"different-transaction"
            ),
        )

        result, _, _, _ = (
            await self.run_fill(
                snapshots=[
                    self.initial,
                    self.initial,
                ],
                response=response,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FILL_TRANSACTION_HASH_MISMATCH",
            result.reasons,
        )

    async def test_authenticated_pump_cpi_trade_event_proves_fill(
        self,
    ):
        response = self.response(
            inner_event=True,
        )

        result, _, _, rpc = (
            await self.run_fill(
                response=response
            )
        )

        self.assertEqual(
            result.status,
            PROVEN,
        )

        self.assertEqual(
            result.trade_event_token_amount,
            self.amount,
        )

        self.assertEqual(
            len(rpc.calls),
            1,
        )

    async def test_non_pump_program_data_cannot_spoof_trade_event(
        self,
    ):
        response = self.response(
            event_program=str(
                random_pubkey()
            ),
        )

        result, _, _, _ = (
            await self.run_fill(
                snapshots=[
                    self.initial,
                    self.initial,
                ],
                response=response,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "TRADE_EVENT_MISSING",
            result.reasons,
        )

    async def test_trade_event_amount_mismatch_is_unknown(
        self,
    ):
        response = self.response(
            event_token_amount=(
                self.amount - 1
            ),
        )

        result, _, _, _ = (
            await self.run_fill(
                snapshots=[
                    self.initial,
                    self.initial,
                ],
                response=response,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "TRADE_EVENT_TOKEN_AMOUNT_MISMATCH",
            result.reasons,
        )

    async def test_token_delta_mismatch_is_unknown(
        self,
    ):
        response = self.response(
            post_token_amount=(
                100
                + self.amount
                - 1
            ),
        )

        result, _, _, _ = (
            await self.run_fill(
                snapshots=[
                    self.initial,
                    self.initial,
                ],
                response=response,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "TOKEN_FILL_AMOUNT_MISMATCH",
            result.reasons,
        )

    async def test_missing_pre_token_balance_for_existing_ata_is_unknown(
        self,
    ):
        response = self.response(
            omit_pre_token=True,
            post_token_amount=(
                self.amount
            ),
            pre_ata_lamports=(
                2_039_280
            ),
        )

        result, _, _, _ = (
            await self.run_fill(
                snapshots=[
                    self.initial,
                    self.initial,
                ],
                response=response,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "PRE_TOKEN_BALANCE_MISSING_FOR_EXISTING_ACCOUNT",
            result.reasons,
        )

    async def test_optional_post_owner_and_program_metadata_may_be_absent(
        self,
    ):
        response = self.response(
            omit_post_owner=True,
            omit_post_program=True,
        )

        result, _, _, _ = (
            await self.run_fill(
                response=response
            )
        )

        self.assertEqual(
            result.status,
            PROVEN,
        )

        self.assertEqual(
            result.token_delta,
            self.amount,
        )

    async def test_present_token_program_mismatch_is_unknown(
        self,
    ):
        response = self.response(
            post_token_program=(
                str(
                    random_pubkey()
                )
            ),
        )

        result, _, _, _ = (
            await self.run_fill(
                snapshots=[
                    self.initial,
                    self.initial,
                ],
                response=response,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "TOKEN_BALANCE_PROGRAM_MISMATCH",
            result.reasons,
        )

    async def test_instruction_max_sol_cost_must_match_reservation_spend(
        self,
    ):
        changed = SimpleNamespace(
            **{
                **self.initial.__dict__,
                "spend_lamports":
                    self.max_sol_cost + 1,
            }
        )

        result, _, _, rpc = (
            await self.run_fill(
                snapshots=[
                    changed,
                    changed,
                ],
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "BUY_V2_MAX_SOL_COST_RESERVATION_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_non_buy_reservation_blocks_before_receipt(
        self,
    ):
        sell_reservation = SimpleNamespace(
            **{
                **self.initial.__dict__,
                "side": "SELL",
            }
        )

        (
            result,
            load_mock,
            receipt_mock,
            rpc,
        ) = await self.run_fill(
            snapshots=[
                sell_reservation,
            ],
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "RESERVATION_NOT_BUY",
            result.reasons,
        )

        self.assertEqual(
            load_mock.call_count,
            1,
        )

        receipt_mock.assert_not_awaited()

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_economic_authority_change_during_receipt_blocks_fill(
        self,
    ):
        mutations = (
            (
                "spend_lamports",
                self.max_sol_cost + 1,
            ),
            (
                "wallet_cost_lamports",
                1_300_000,
            ),
        )

        for field, value in mutations:
            with self.subTest(
                field=field,
            ):
                changed = SimpleNamespace(
                    **{
                        **self.initial.__dict__,
                        field: value,
                    }
                )

                (
                    result,
                    load_mock,
                    receipt_mock,
                    rpc,
                ) = await self.run_fill(
                    snapshots=[
                        self.initial,
                        changed,
                    ],
                )

                self.assertEqual(
                    result.status,
                    UNKNOWN,
                )

                self.assertIn(
                    "RESERVATION_CHANGED_DURING_RECEIPT_CHECK",
                    result.reasons,
                )

                self.assertEqual(
                    load_mock.call_count,
                    2,
                )

                receipt_mock.assert_awaited_once()

                self.assertEqual(
                    rpc.calls,
                    [],
                )

    async def test_actual_wallet_cost_cannot_exceed_reserved_liability(
        self,
    ):
        constrained = SimpleNamespace(
            **{
                **self.initial.__dict__,
                "wallet_cost_lamports":
                    1_099_999,
            }
        )

        result, _, _, rpc = (
            await self.run_fill(
                snapshots=[
                    constrained,
                ],
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SUCCESS_WALLET_COST_EXCEEDS_RESERVATION",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_post_token_owner_mismatch_is_unknown(
        self,
    ):
        response = self.response(
            post_owner=str(
                random_pubkey()
            ),
        )

        result, _, _, _ = (
            await self.run_fill(
                snapshots=[
                    self.initial,
                    self.initial,
                ],
                response=response,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "TOKEN_BALANCE_OWNER_MISMATCH",
            result.reasons,
        )

    async def test_final_reservation_change_never_proves_fill(
        self,
    ):
        changed = self.make_reservation(
            signed_transaction_sha256=(
                "55" * 32
            ),
        )

        result, load_mock, _, rpc = (
            await self.run_fill(
                snapshots=[
                    self.initial,
                    self.initial,
                    changed,
                ],
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "RESERVATION_CHANGED_DURING_FILL_CHECK",
            result.reasons,
        )

        self.assertEqual(
            load_mock.call_count,
            3,
        )

        self.assertEqual(
            len(rpc.calls),
            1,
        )


if __name__ == "__main__":
    unittest.main()
