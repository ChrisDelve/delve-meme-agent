import hashlib
import sqlite3
import unittest
from dataclasses import replace
from unittest.mock import patch

from solders.message import (
    to_bytes_versioned,
)

from src.execution.pump_sell_v2_pre_sign_validation import (
    APPROVE,
    DENY,
    UNKNOWN,
    PUMP_SELL_V2_PRE_SIGN_VALIDATION_VERSION,
    validate_pump_sell_v2_pre_sign,
)
from src.portfolio.live_sell_claims import (
    PASS as CLAIM_PASS,
    RELEASED,
    acquire_live_sell_inventory_claim,
    init_schema as init_claim_schema,
)
from tests import (
    test_live_sell_claims as claim_tests,
)
from tests import (
    test_pump_sell_v2_unsigned_message
    as unsigned_message_tests,
)


MODULE = (
    "src.execution."
    "pump_sell_v2_pre_sign_validation"
)


class FakeRpc:
    def __init__(
        self,
        *,
        blockhash_slot,
        fee_slot,
        fee_value,
        blockhash_result=None,
        fee_result=None,
        error=None,
    ):
        self.blockhash_result = (
            {
                "context": {
                    "slot": blockhash_slot,
                },
                "value": True,
            }
            if blockhash_result is None
            else blockhash_result
        )

        self.fee_result = (
            {
                "context": {
                    "slot": fee_slot,
                },
                "value": fee_value,
            }
            if fee_result is None
            else fee_result
        )

        self.error = error
        self.calls = []

    async def __aenter__(self):
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

        if method == "isBlockhashValid":
            return self.blockhash_result

        if method == "getFeeForMessage":
            return self.fee_result

        raise RuntimeError(
            f"Unexpected RPC method: {method}"
        )


class PumpSellV2PreSignValidationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.claim_helper = (
            claim_tests.LiveSellInventoryClaimTests(
                methodName=(
                    "test_claim_persists_exact_fifo_"
                    "allocation_without_mutating_positions"
                )
            )
        )

        self.claim_helper.setUp()

        self.unsigned_helper = (
            unsigned_message_tests
            .PumpSellV2UnsignedMessageTests(
                methodName=(
                    "test_builds_unsigned_message_v0"
                )
            )
        )

        self.unsigned_helper.setUp()

        self.db_path = (
            self.claim_helper.db_path
        )

        self.authorization = (
            self.claim_helper.authorization()
        )

        self.context = (
            self.unsigned_helper.make_context(
                self.authorization,
                global_rpc_slot=501,
            )
        )

        self.blockhash_context = (
            self.unsigned_helper
            .make_blockhash_context(
                min_context_slot=501,
                rpc_slot=502,
            )
        )

        self.message_plan = (
            self.unsigned_helper.build(
                authorization=(
                    self.authorization
                ),
                context=self.context,
                blockhash_context=(
                    self.blockhash_context
                ),
            )
        )

    def tearDown(self):
        try:
            self.unsigned_helper.tearDown()

        finally:
            self.claim_helper.tearDown()

    def acquire_claim(self):
        result = (
            acquire_live_sell_inventory_claim(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            result.status,
            CLAIM_PASS,
        )

        return result.claim

    def make_rpc(
        self,
        *,
        blockhash_result=None,
        fee_result=None,
        error=None,
    ):
        blockhash_slot = (
            self.message_plan
            .blockhash_rpc_slot
            + 1
        )

        fee_slot = (
            blockhash_slot
            + 1
        )

        fee_value = (
            self.authorization
            .base_network_fee_lamports
            + self.authorization
            .priority_fee_lamports
        )

        return FakeRpc(
            blockhash_slot=(
                blockhash_slot
            ),
            fee_slot=fee_slot,
            fee_value=fee_value,
            blockhash_result=(
                blockhash_result
            ),
            fee_result=fee_result,
            error=error,
        )

    async def validate(
        self,
        *,
        context=None,
        message_plan=None,
        rpc=None,
        acquire_claim=True,
    ):
        if acquire_claim:
            self.acquire_claim()

        context = (
            self.context
            if context is None
            else context
        )

        message_plan = (
            self.message_plan
            if message_plan is None
            else message_plan
        )

        rpc = (
            self.make_rpc()
            if rpc is None
            else rpc
        )

        with patch(
            f"{MODULE}.HeliusRpcClient",
            return_value=rpc,
        ):
            result = (
                await validate_pump_sell_v2_pre_sign(
                    authorization=(
                        self.authorization
                    ),
                    context=context,
                    message_plan=message_plan,
                    db_path=self.db_path,
                )
            )

        return result, rpc

    def tamper_compiled_instruction(
        self,
        *,
        instruction_index,
    ):
        original_message = (
            self.message_plan.message
        )

        instructions = list(
            original_message.instructions
        )

        original_instruction = (
            instructions[
                instruction_index
            ]
        )

        tampered_data = bytearray(
            bytes(
                original_instruction.data
            )
        )

        self.assertTrue(
            tampered_data
        )

        tampered_data[-1] ^= 1

        instructions[
            instruction_index
        ] = type(
            original_instruction
        )(
            original_instruction
            .program_id_index,
            bytes(
                tampered_data
            ),
            bytes(
                original_instruction.accounts
            ),
        )

        tampered_message = type(
            original_message
        )(
            original_message.header,
            list(
                original_message.account_keys
            ),
            original_message.recent_blockhash,
            instructions,
            list(
                original_message
                .address_table_lookups
            ),
        )

        message_bytes = (
            to_bytes_versioned(
                tampered_message
            )
        )

        return replace(
            self.message_plan,
            message=(
                tampered_message
            ),
            message_sha256=(
                hashlib.sha256(
                    message_bytes
                ).hexdigest()
            ),
            estimated_signed_transaction_size_bytes=(
                len(
                    message_bytes
                )
                + 1
                + 64
            ),
        )

    async def test_valid_exact_chain_approves(
        self,
    ):
        result, rpc = await self.validate()

        self.assertEqual(
            result.validator_version,
            PUMP_SELL_V2_PRE_SIGN_VALIDATION_VERSION,
        )

        self.assertEqual(
            result.status,
            APPROVE,
        )

        self.assertTrue(
            result.allows_signing
        )

        self.assertEqual(
            result.authorization_sha256,
            self.authorization
            .authorization_sha256,
        )

        self.assertEqual(
            result.message_sha256,
            self.message_plan
            .message_sha256,
        )

        self.assertEqual(
            [
                call[0]
                for call in rpc.calls
            ],
            [
                "isBlockhashValid",
                "getFeeForMessage",
            ],
        )

    async def test_missing_claim_denies_before_rpc(
        self,
    ):
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            init_claim_schema(
                connection
            )
            connection.commit()

        finally:
            connection.close()

        result, rpc = await self.validate(
            acquire_claim=False,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "ACTIVE_SELL_CLAIM_NOT_FOUND",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_terminal_claim_denies_before_rpc(
        self,
    ):
        self.acquire_claim()

        connection = sqlite3.connect(
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
                    200.0,
                    "TEST_RELEASE",
                    self.authorization
                    .authorization_sha256,
                ),
            )

            connection.commit()

        finally:
            connection.close()

        result, rpc = await self.validate(
            acquire_claim=False,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "SELL_CLAIM_NOT_ACTIVE",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_message_hash_binding_mismatch_denies(
        self,
    ):
        message_plan = replace(
            self.message_plan,
            authorization_sha256=(
                "aa" * 32
            ),
        )

        result, rpc = await self.validate(
            message_plan=message_plan,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "AUTHORIZATION_HASH_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_account_context_version_binding_denies(
        self,
    ):
        message_plan = replace(
            self.message_plan,
            account_context_version=(
                "wrong-context-v1"
            ),
        )

        result, rpc = await self.validate(
            message_plan=message_plan,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "ACCOUNT_CONTEXT_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_context_fee_slot_binding_denies(
        self,
    ):
        context = replace(
            self.context,
            authorization_fee_rpc_slot=499,
        )

        result, rpc = await self.validate(
            context=context,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "STATE_SLOT_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_message_fingerprint_mismatch_denies(
        self,
    ):
        message_plan = replace(
            self.message_plan,
            message_sha256=(
                "aa" * 32
            ),
        )

        result, rpc = await self.validate(
            message_plan=message_plan,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "MESSAGE_FINGERPRINT_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_invalid_blockhash_denies(
        self,
    ):
        rpc = self.make_rpc(
            blockhash_result={
                "context": {
                    "slot": 503,
                },
                "value": False,
            },
        )

        result, rpc = await self.validate(
            rpc=rpc,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "BLOCKHASH_NOT_VALID",
            result.reasons,
        )

        self.assertEqual(
            len(rpc.calls),
            1,
        )

    async def test_stale_fee_context_is_unknown(
        self,
    ):
        rpc = self.make_rpc(
            fee_result={
                "context": {
                    "slot": 502,
                },
                "value": 5_000,
            },
        )

        result, _ = await self.validate(
            rpc=rpc,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FEE_RPC_CONTEXT_INVALID",
            result.reasons,
        )

    async def test_base_fee_over_budget_denies(
        self,
    ):
        rpc = self.make_rpc(
            fee_result={
                "context": {
                    "slot": 504,
                },
                "value": 5_001,
            },
        )

        result, _ = await self.validate(
            rpc=rpc,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "BASE_NETWORK_FEE_EXCEEDS_AUTHORIZATION",
            result.reasons,
        )

    async def test_missing_claim_schema_is_unknown_before_rpc(
        self,
    ):
        result, rpc = await self.validate(
            acquire_claim=False,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_SELL_CLAIM_TABLE_NOT_FOUND",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_corrupt_claim_evidence_is_unknown_before_rpc(
        self,
    ):
        self.acquire_claim()

        connection = sqlite3.connect(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE live_sell_inventory_claims

                SET tokens_to_sell =
                    tokens_to_sell + 1

                WHERE authorization_sha256 = ?
                """,
                (
                    self.authorization
                    .authorization_sha256,
                ),
            )

            connection.commit()

        finally:
            connection.close()

        result, rpc = await self.validate(
            acquire_claim=False,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_CLAIM_EVIDENCE_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_payer_binding_mismatch_denies_before_rpc(
        self,
    ):
        message_plan = replace(
            self.message_plan,
            payer=str(
                self.unsigned_helper.wallet
            ),
        )

        result, rpc = await self.validate(
            message_plan=message_plan,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "PAYER_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_blockhash_context_binding_mismatch_denies_before_rpc(
        self,
    ):
        message_plan = replace(
            self.message_plan,
            blockhash_min_context_slot=500,
        )

        result, rpc = await self.validate(
            message_plan=message_plan,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "BLOCKHASH_CONTEXT_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_malformed_blockhash_value_is_unknown(
        self,
    ):
        rpc = self.make_rpc(
            blockhash_result={
                "context": {
                    "slot": 503,
                },
                "value": "true",
            },
        )

        result, rpc = await self.validate(
            rpc=rpc,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "BLOCKHASH_RPC_VALUE_INVALID",
            result.reasons,
        )

        self.assertEqual(
            len(
                rpc.calls
            ),
            1,
        )

    async def test_null_fee_quote_denies(
        self,
    ):
        rpc = self.make_rpc(
            fee_result={
                "context": {
                    "slot": 504,
                },
                "value": None,
            },
        )

        result, rpc = await self.validate(
            rpc=rpc,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "FEE_UNAVAILABLE_FOR_BLOCKHASH",
            result.reasons,
        )

        self.assertEqual(
            len(
                rpc.calls
            ),
            2,
        )

    async def test_malformed_fee_value_is_unknown(
        self,
    ):
        rpc = self.make_rpc(
            fee_result={
                "context": {
                    "slot": 504,
                },
                "value": "5000",
            },
        )

        result, _ = await self.validate(
            rpc=rpc,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FEE_RPC_VALUE_INVALID",
            result.reasons,
        )

    async def test_rpc_exception_is_unknown(
        self,
    ):
        rpc = self.make_rpc(
            error=RuntimeError(
                "test rpc failure"
            ),
        )

        result, _ = await self.validate(
            rpc=rpc,
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "PRE_SIGN_RPC_FAILED:RuntimeError",
            result.reasons,
        )

    async def test_compiled_pump_instruction_tamper_denies_before_rpc(
        self,
    ):
        message_plan = (
            self.tamper_compiled_instruction(
                instruction_index=2,
            )
        )

        result, rpc = await self.validate(
            message_plan=message_plan,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "PUMP_SELL_INSTRUCTION_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_compute_instruction_tamper_denies_before_rpc(
        self,
    ):
        message_plan = (
            self.tamper_compiled_instruction(
                instruction_index=0,
            )
        )

        result, rpc = await self.validate(
            message_plan=message_plan,
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "COMPUTE_LIMIT_INSTRUCTION_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_pre_sign_validation_does_not_mutate_live_state(
        self,
    ):
        self.acquire_claim()

        def snapshot():
            connection = sqlite3.connect(
                self.db_path
            )

            try:
                claims = tuple(
                    connection.execute(
                        """
                        SELECT *

                        FROM live_sell_inventory_claims

                        ORDER BY authorization_sha256
                        """
                    ).fetchall()
                )

                lots = tuple(
                    connection.execute(
                        """
                        SELECT *

                        FROM live_sell_inventory_claim_lots

                        ORDER BY
                            authorization_sha256,
                            ordinal
                        """
                    ).fetchall()
                )

                positions = tuple(
                    connection.execute(
                        """
                        SELECT *

                        FROM live_positions

                        ORDER BY position_id
                        """
                    ).fetchall()
                )

                return (
                    claims,
                    lots,
                    positions,
                )

            finally:
                connection.close()

        before = snapshot()

        result, _ = await self.validate(
            acquire_claim=False,
        )

        after = snapshot()

        self.assertEqual(
            result.status,
            APPROVE,
        )

        self.assertEqual(
            after,
            before,
        )


if __name__ == "__main__":
    unittest.main()
