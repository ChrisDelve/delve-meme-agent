import base64
import hashlib
import struct
import unittest
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import (
    VersionedTransaction,
)

from src.data.trade_event import (
    TRADE_EVENT_DISCRIMINATOR,
)
from src.execution.live_sell_transaction_receipt import (
    BLOCK as RECEIPT_BLOCK,
    LIVE_SELL_TRANSACTION_RECEIPT_VERSION,
    RESOLVED as RECEIPT_RESOLVED,
    UNKNOWN as RECEIPT_UNKNOWN,
    LiveSellTransactionReceiptResult,
)
from src.execution.live_sell_transaction_status import (
    KNOWN,
)
from src.execution.order_authorization import (
    WRAPPED_SOL_MINT,
)
from src.execution.successful_pump_sell_fill import (
    BLOCK,
    PROVEN,
    SUCCESSFUL_PUMP_SELL_FILL_VERSION,
    UNKNOWN,
    resolve_successful_pump_sell_fill,
)
from src.portfolio.live_reservations import (
    get_connection,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    RELEASED,
    _load_claim,
)
from src.portfolio.live_sell_execution_records import (
    PASS as EXECUTION_PASS,
    load_live_sell_execution_record_read_only,
)
from src.safety.token_safety_resolver import (
    PUMP_PROGRAM,
    TOKEN_PROGRAM,
    derive_associated_token_account,
)
from tests import (
    test_live_sell_execution_records
    as execution_record_tests,
)


MODULE = (
    "src.execution."
    "successful_pump_sell_fill"
)


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


class SuccessfulPumpSellFillTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        helper = (
            execution_record_tests
            .LiveSellExecutionRecordTests(
                methodName=(
                    "test_valid_signed_artifact_is_durable"
                )
            )
        )

        helper.setUp()

        self.helper = helper
        self.db_path = helper.db_path

        self.authorization = (
            helper.authorization
        )

        self.authorization_sha256 = (
            self.authorization
            .authorization_sha256
        )

        bind_result = (
            helper.bind()
        )

        self.assertEqual(
            bind_result.status,
            EXECUTION_PASS,
        )

        loaded = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    self.authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            loaded.status,
            EXECUTION_PASS,
        )

        self.execution = (
            loaded.record
        )

        self.transaction_bytes = (
            self.execution
            .signed_transaction_bytes
        )

        self.transaction_sha256 = (
            hashlib.sha256(
                self.transaction_bytes
            ).hexdigest()
        )

        self.transaction = (
            VersionedTransaction.from_bytes(
                self.transaction_bytes
            )
        )

        self.message = (
            self.transaction.message
        )

        self.account_keys = tuple(
            self.message.account_keys
        )

        self.wallet = (
            Pubkey.from_string(
                self.authorization
                .wallet_pubkey
            )
        )

        self.mint = (
            Pubkey.from_string(
                self.authorization.mint
            )
        )

        self.base_ata = (
            Pubkey.from_string(
                self.authorization
                .associated_base_user
            )
        )

        self.quote_mint = (
            Pubkey.from_string(
                str(
                    WRAPPED_SOL_MINT
                )
            )
        )

        self.quote_ata = (
            derive_associated_token_account(
                owner=self.wallet,
                mint=self.quote_mint,
                token_program=TOKEN_PROGRAM,
            )
        )

        self.base_index = (
            self.account_keys.index(
                self.base_ata
            )
        )

        self.quote_index = (
            self.account_keys.index(
                self.quote_ata
            )
        )

        self.amount = (
            self.authorization
            .tokens_to_sell
        )

        self.min_quote_out = (
            self.authorization
            .exit_execution
            .min_quote_out
        )

        self.base_pre = (
            self.amount
            + 10_000
        )

        self.base_post = 10_000

        self.quote_pre = 500_000

        self.quote_credit = (
            self.min_quote_out
            + 25_000
        )

        self.quote_post = (
            self.quote_pre
            + self.quote_credit
        )

        self.slot = 230
        self.block_time = 1_800_000_000

        self.fee_lamports = 5_000

        self.payer_pre = (
            20_000_000
        )

        self.payer_post = (
            self.payer_pre
            - self.fee_lamports
        )

        self.payer_delta = (
            self.payer_post
            - self.payer_pre
        )

    def tearDown(self):
        self.helper.tearDown()

    def load_claim_any(self):
        connection = get_connection(
            self.db_path
        )

        try:
            return _load_claim(
                connection=connection,
                authorization_sha256=(
                    self.authorization_sha256
                ),
            )

        finally:
            connection.close()

    def token_balance(
        self,
        *,
        index,
        mint,
        owner,
        token_program,
        amount,
    ):
        return {
            "accountIndex": index,
            "mint": str(
                mint
            ),
            "owner": str(
                owner
            ),
            "programId": str(
                token_program
            ),
            "uiTokenAmount": {
                "amount": str(
                    amount
                ),
                "decimals": 9,
                "uiAmountString": str(
                    amount
                ),
            },
        }

    def encode_event(
        self,
        *,
        is_buy=False,
        token_amount=None,
        quote_amount=None,
    ):
        if token_amount is None:
            token_amount = (
                self.amount
            )

        if quote_amount is None:
            quote_amount = (
                self.quote_credit
            )

        fee_recipient = (
            Keypair().pubkey()
        )

        creator = (
            Pubkey.from_string(
                self.authorization.creator
            )
        )

        ix_name = b"sell_v2"

        data = bytearray(
            TRADE_EVENT_DISCRIMINATOR
        )

        data += bytes(
            self.mint
        )

        data += struct.pack(
            "<Q",
            self.quote_credit,
        )

        data += struct.pack(
            "<Q",
            token_amount,
        )

        data += bytes(
            [
                1
                if is_buy
                else 0
            ]
        )

        data += bytes(
            self.wallet
        )

        data += struct.pack(
            "<q",
            1_800_000_000,
        )

        for value in (
            10_000_000,
            20_000_000,
            8_000_000,
            18_000_000,
        ):
            data += struct.pack(
                "<Q",
                value,
            )

        data += bytes(
            fee_recipient
        )

        data += struct.pack(
            "<Q",
            self.authorization
            .protocol_fee_bps,
        )

        data += struct.pack(
            "<Q",
            100,
        )

        data += bytes(
            creator
        )

        data += struct.pack(
            "<Q",
            self.authorization
            .creator_fee_bps,
        )

        data += struct.pack(
            "<Q",
            50,
        )

        data += b"\x01"

        for value in (
            0,
            0,
            self.quote_credit,
        ):
            data += struct.pack(
                "<Q",
                value,
            )

        data += struct.pack(
            "<q",
            1_800_000_000,
        )

        data += struct.pack(
            "<I",
            len(
                ix_name
            ),
        )

        data += ix_name

        data += b"\x00"

        data += struct.pack(
            "<Q",
            0,
        )

        data += struct.pack(
            "<Q",
            0,
        )

        data += struct.pack(
            "<Q",
            0,
        )

        data += struct.pack(
            "<Q",
            0,
        )

        #
        # shareholder_count = 0
        #
        data += struct.pack(
            "<I",
            0,
        )

        data += bytes(
            self.quote_mint
        )

        data += struct.pack(
            "<Q",
            quote_amount,
        )

        data += struct.pack(
            "<Q",
            10_000_000,
        )

        data += struct.pack(
            "<Q",
            8_000_000,
        )

        return bytes(
            data
        )

    def receipt(
        self,
        *,
        status=RECEIPT_RESOLVED,
        reasons=(),
        transaction_error=None,
        receipt_slot=None,
    ):
        if receipt_slot is None:
            receipt_slot = (
                self.slot
                if status
                == RECEIPT_RESOLVED
                else None
            )

        return (
            LiveSellTransactionReceiptResult(
                resolver_version=(
                    LIVE_SELL_TRANSACTION_RECEIPT_VERSION
                ),
                status=status,
                reasons=tuple(
                    reasons
                ),
                authorization_sha256=(
                    self.authorization_sha256
                ),
                transaction_signature=(
                    self.execution
                    .transaction_signature
                ),
                execution_status=(
                    self.execution.status
                ),
                status_observation_state=(
                    KNOWN
                    if status
                    == RECEIPT_RESOLVED
                    else None
                ),
                status_transaction_slot=(
                    receipt_slot
                ),
                receipt_slot=(
                    receipt_slot
                ),
                block_time=(
                    self.block_time
                    if status
                    == RECEIPT_RESOLVED
                    else None
                ),
                transaction_error=(
                    transaction_error
                ),
                fee_lamports=(
                    self.fee_lamports
                    if status
                    == RECEIPT_RESOLVED
                    else None
                ),
                fee_payer_pubkey=(
                    self.authorization
                    .wallet_pubkey
                    if status
                    == RECEIPT_RESOLVED
                    else None
                ),
                fee_payer_pre_balance_lamports=(
                    self.payer_pre
                    if status
                    == RECEIPT_RESOLVED
                    else None
                ),
                fee_payer_post_balance_lamports=(
                    self.payer_post
                    if status
                    == RECEIPT_RESOLVED
                    else None
                ),
                fee_payer_balance_delta_lamports=(
                    self.payer_delta
                    if status
                    == RECEIPT_RESOLVED
                    else None
                ),
                persisted_transaction_sha256=(
                    self.transaction_sha256
                    if status
                    == RECEIPT_RESOLVED
                    else None
                ),
                receipt_transaction_sha256=(
                    self.transaction_sha256
                    if status
                    == RECEIPT_RESOLVED
                    else None
                ),
                last_valid_block_height=(
                    self.execution
                    .last_valid_block_height
                ),
                blockhash_rpc_slot=(
                    self.execution
                    .blockhash_rpc_slot
                ),
            )
        )

    def response(
        self,
        *,
        base_pre=None,
        base_post=None,
        quote_pre=None,
        quote_post=None,
        include_quote_pre=True,
        quote_pre_account_lamports=None,
        event_is_buy=False,
        event_token_amount=None,
        transaction_bytes=None,
    ):
        if base_pre is None:
            base_pre = (
                self.base_pre
            )

        if base_post is None:
            base_post = (
                self.base_post
            )

        if quote_pre is None:
            quote_pre = (
                self.quote_pre
            )

        if quote_post is None:
            quote_post = (
                self.quote_post
            )

        if transaction_bytes is None:
            transaction_bytes = (
                self.transaction_bytes
            )

        account_count = len(
            self.account_keys
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

        if (
            quote_pre_account_lamports
            is not None
        ):
            pre_balances[
                self.quote_index
            ] = (
                quote_pre_account_lamports
            )

        pre_tokens = [
            self.token_balance(
                index=self.base_index,
                mint=self.mint,
                owner=self.wallet,
                token_program=(
                    Pubkey.from_string(
                        self.authorization
                        .base_token_program
                    )
                ),
                amount=base_pre,
            ),
        ]

        if include_quote_pre:
            pre_tokens.append(
                self.token_balance(
                    index=(
                        self.quote_index
                    ),
                    mint=(
                        self.quote_mint
                    ),
                    owner=self.wallet,
                    token_program=(
                        TOKEN_PROGRAM
                    ),
                    amount=quote_pre,
                )
            )

        post_tokens = [
            self.token_balance(
                index=self.base_index,
                mint=self.mint,
                owner=self.wallet,
                token_program=(
                    Pubkey.from_string(
                        self.authorization
                        .base_token_program
                    )
                ),
                amount=base_post,
            ),
            self.token_balance(
                index=(
                    self.quote_index
                ),
                mint=self.quote_mint,
                owner=self.wallet,
                token_program=(
                    TOKEN_PROGRAM
                ),
                amount=quote_post,
            ),
        ]

        encoded_event = (
            base64.b64encode(
                self.encode_event(
                    is_buy=(
                        event_is_buy
                    ),
                    token_amount=(
                        event_token_amount
                    ),
                )
            ).decode(
                "ascii"
            )
        )

        return {
            "slot": self.slot,
            "blockTime": (
                self.block_time
            ),
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
                "fee": (
                    self.fee_lamports
                ),
                "preBalances":
                    pre_balances,
                "postBalances":
                    post_balances,
                "preTokenBalances":
                    pre_tokens,
                "postTokenBalances":
                    post_tokens,
                "innerInstructions": [],
                "logMessages": [
                    (
                        f"Program "
                        f"{PUMP_PROGRAM} "
                        "invoke [1]"
                    ),
                    (
                        "Program data: "
                        f"{encoded_event}"
                    ),
                    (
                        f"Program "
                        f"{PUMP_PROGRAM} "
                        "success"
                    ),
                ],
            },
        }

    async def run_fill(
        self,
        *,
        receipt=None,
        response=None,
    ):
        if receipt is None:
            receipt = self.receipt()

        if response is None:
            response = (
                self.response()
            )

        rpc = FakeFillRpc(
            response=response
        )

        with (
            patch(
                f"{MODULE}."
                "resolve_live_sell_transaction_receipt",
                AsyncMock(
                    return_value=receipt
                ),
            ) as receipt_mock,
            patch(
                f"{MODULE}."
                "HeliusRpcClient",
                MagicMock(
                    return_value=rpc
                ),
            ),
        ):
            result = await (
                resolve_successful_pump_sell_fill(
                    authorization=(
                        self.authorization
                    ),
                    db_path=self.db_path,
                )
            )

        return (
            result,
            receipt_mock,
            rpc,
        )

    async def test_exact_successful_sell_fill_is_proven_without_mutation(
        self,
    ):
        claim_before = (
            self.load_claim_any()
        )

        positions_before = (
            self.helper
            .snapshot_claim_and_positions()[1]
        )

        result, receipt_mock, rpc = (
            await self.run_fill()
        )

        self.assertEqual(
            result.resolver_version,
            SUCCESSFUL_PUMP_SELL_FILL_VERSION,
        )

        self.assertEqual(
            result.status,
            PROVEN,
        )

        self.assertEqual(
            result.base_token_debit,
            self.amount,
        )

        self.assertEqual(
            result.trade_event_token_amount,
            self.amount,
        )

        self.assertEqual(
            result.quote_token_credit_lamports,
            self.quote_credit,
        )

        self.assertGreaterEqual(
            result.quote_token_credit_lamports,
            self.min_quote_out,
        )

        self.assertEqual(
            result.persisted_transaction_sha256,
            self.transaction_sha256,
        )

        self.assertEqual(
            result.observed_transaction_sha256,
            self.transaction_sha256,
        )

        receipt_mock.assert_awaited_once()

        self.assertEqual(
            len(
                rpc.calls
            ),
            1,
        )

        self.assertEqual(
            rpc.calls[0][0],
            "getTransaction",
        )

        self.assertEqual(
            self.load_claim_any(),
            claim_before,
        )

        self.assertEqual(
            self.helper
            .snapshot_claim_and_positions()[1],
            positions_before,
        )

    async def test_failed_receipt_never_fetches_fill_metadata(
        self,
    ):
        result, _, rpc = (
            await self.run_fill(
                receipt=self.receipt(
                    transaction_error={
                        "InstructionError": [
                            2,
                            {
                                "Custom":
                                    6001
                            },
                        ]
                    },
                )
            )
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
            rpc.calls,
            [],
        )

    async def test_unknown_receipt_never_fetches_fill_metadata(
        self,
    ):
        result, _, rpc = (
            await self.run_fill(
                receipt=self.receipt(
                    status=(
                        RECEIPT_UNKNOWN
                    ),
                    reasons=(
                        "RPC_UNCERTAIN",
                    ),
                )
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
        result, _, rpc = (
            await self.run_fill(
                receipt=self.receipt(
                    status=(
                        RECEIPT_BLOCK
                    ),
                    reasons=(
                        "NOT_READY",
                    ),
                )
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

    async def test_base_token_debit_must_equal_authorized_sell_amount(
        self,
    ):
        response = self.response(
            base_post=(
                self.base_post
                + 1
            )
        )

        result, _, _ = (
            await self.run_fill(
                response=response
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_BASE_TOKEN_DEBIT_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            self.load_claim_any().status,
            ACTIVE,
        )

    async def test_trade_event_must_be_sell_side(
        self,
    ):
        result, _, _ = (
            await self.run_fill(
                response=(
                    self.response(
                        event_is_buy=True
                    )
                )
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "TRADE_EVENT_SIDE_MISMATCH",
            result.reasons,
        )

    async def test_trade_event_amount_must_equal_authorized_sell_amount(
        self,
    ):
        result, _, _ = (
            await self.run_fill(
                response=(
                    self.response(
                        event_token_amount=(
                            self.amount
                            - 1
                        )
                    )
                )
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

    async def test_quote_credit_must_be_positive(
        self,
    ):
        result, _, _ = (
            await self.run_fill(
                response=(
                    self.response(
                        quote_post=(
                            self.quote_pre
                        )
                    )
                )
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_QUOTE_TOKEN_CREDIT_INVALID",
            result.reasons,
        )

    async def test_quote_credit_must_satisfy_authorized_minimum(
        self,
    ):
        self.assertGreater(
            self.min_quote_out,
            1,
        )

        quote_credit = (
            self.min_quote_out
            - 1
        )

        result, _, _ = (
            await self.run_fill(
                response=(
                    self.response(
                        quote_post=(
                            self.quote_pre
                            + quote_credit
                        )
                    )
                )
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_QUOTE_CREDIT_BELOW_AUTHORIZED_MINIMUM",
            result.reasons,
        )

    async def test_missing_pre_quote_balance_can_prove_zero_only_for_new_account(
        self,
    ):
        response = self.response(
            include_quote_pre=False,
            quote_pre=0,
            quote_post=(
                self.quote_credit
            ),
            quote_pre_account_lamports=0,
        )

        result, _, _ = (
            await self.run_fill(
                response=response
            )
        )

        self.assertEqual(
            result.status,
            PROVEN,
        )

        self.assertEqual(
            result.quote_token_pre_amount,
            0,
        )

        self.assertEqual(
            result.quote_token_credit_lamports,
            self.quote_credit,
        )

    async def test_missing_pre_quote_balance_for_existing_account_is_unknown(
        self,
    ):
        response = self.response(
            include_quote_pre=False,
            quote_post=(
                self.quote_credit
            ),
            quote_pre_account_lamports=(
                2_039_280
            ),
        )

        result, _, _ = (
            await self.run_fill(
                response=response
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_PRE_QUOTE_BALANCE_MISSING_FOR_EXISTING_ACCOUNT",
            result.reasons,
        )

    async def test_fresh_transaction_hash_mismatch_is_unknown(
        self,
    ):
        response = self.response(
            transaction_bytes=(
                b"different-transaction"
            )
        )

        result, _, _ = (
            await self.run_fill(
                response=response
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

    async def test_claim_change_after_receipt_blocks_fill_rpc(
        self,
    ):
        async def mutate_claim(
            **kwargs,
        ):
            connection = get_connection(
                self.db_path
            )

            try:
                connection.execute(
                    """
                    UPDATE live_sell_inventory_claims

                    SET
                        status = ?,
                        terminal_at = ?,
                        terminal_reason = ?

                    WHERE authorization_sha256 = ?
                    """,
                    (
                        RELEASED,
                        123.0,
                        "TEST_RELEASE",
                        self.authorization_sha256,
                    ),
                )

                connection.commit()

            finally:
                connection.close()

            return self.receipt()

        rpc = FakeFillRpc(
            response=self.response()
        )

        with (
            patch(
                f"{MODULE}."
                "resolve_live_sell_transaction_receipt",
                AsyncMock(
                    side_effect=(
                        mutate_claim
                    )
                ),
            ),
            patch(
                f"{MODULE}."
                "HeliusRpcClient",
                MagicMock(
                    return_value=rpc
                ),
            ),
        ):
            result = await (
                resolve_successful_pump_sell_fill(
                    authorization=(
                        self.authorization
                    ),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_CLAIM_CHANGED_DURING_RECEIPT_CHECK",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )


if __name__ == "__main__":
    unittest.main()
