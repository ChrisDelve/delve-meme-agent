import asyncio
import sqlite3
import unittest
from dataclasses import replace
from unittest.mock import patch

from solders.keypair import Keypair
from solders.signature import Signature

from src.execution.pump_sell_v2_pre_sign_validation import (
    APPROVE,
    DENY,
    PUMP_SELL_V2_PRE_SIGN_VALIDATION_VERSION,
    PumpSellV2PreSignValidation,
)
from src.execution.pump_sell_v2_signing import (
    BLOCK,
    PASS,
    UNKNOWN,
    PUMP_SELL_V2_SIGNING_VERSION,
    sign_and_bind_pump_sell_v2,
)
from src.portfolio import (
    live_sell_execution_records
    as execution_records,
)
from src.portfolio.live_sell_claims import (
    ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION,
    LIVE_SELL_INVENTORY_CLAIM_VERSION,
    RELEASED,
)
from src.portfolio.live_sell_execution_records import (
    PASS as EXECUTION_PASS,
    load_live_sell_execution_record_read_only,
)
from tests import (
    test_live_sell_execution_records
    as execution_record_tests,
)


MODULE = (
    "src.execution.pump_sell_v2_signing"
)


class CountingSigner:
    def __init__(
        self,
        keypair,
    ):
        self.keypair = keypair
        self.pubkey_calls = 0
        self.sign_calls = 0

    def pubkey(self):
        self.pubkey_calls += 1

        return self.keypair.pubkey()

    def sign_message(
        self,
        message,
    ):
        self.sign_calls += 1

        return self.keypair.sign_message(
            message
        )


class DefaultSignatureSigner(
    CountingSigner
):
    def sign_message(
        self,
        message,
    ):
        self.sign_calls += 1

        return Signature.default()


class FakeRpc:
    def __init__(
        self,
        *,
        slot,
        value=True,
        error=None,
        on_call=None,
    ):
        self.slot = slot
        self.value = value
        self.error = error
        self.on_call = on_call
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

        if method != "isBlockhashValid":
            raise AssertionError(
                f"unexpected RPC method: {method}"
            )

        if self.on_call is not None:
            self.on_call()

        return {
            "context": {
                "slot": self.slot,
            },
            "value": self.value,
        }


class PumpSellV2SigningTests(
    unittest.TestCase
):
    def setUp(self):
        helper = (
            execution_record_tests
            .LiveSellExecutionRecordTests(
                methodName=(
                    "test_valid_signed_artifact_"
                    "is_durable"
                )
            )
        )

        helper.setUp()

        self.helper = helper
        self.db_path = helper.db_path

        self.keypair = helper.keypair
        self.authorization = (
            helper.authorization
        )
        self.context = helper.context
        self.message_plan = (
            helper.message_plan
        )

        self.pre_sign = (
            self.make_pre_sign()
        )

    def tearDown(self):
        self.helper.tearDown()

    def make_pre_sign(
        self,
        *,
        status=APPROVE,
        reasons=(),
        message_plan=None,
    ):
        if message_plan is None:
            message_plan = (
                self.message_plan
            )

        min_context_slot = max(
            self.authorization.fee_rpc_slot,
            self.context.global_rpc_slot,
            message_plan.blockhash_rpc_slot,
        )

        return PumpSellV2PreSignValidation(
            validator_version=(
                PUMP_SELL_V2_PRE_SIGN_VALIDATION_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                self.authorization
                .authorization_sha256
            ),
            claim_loader_version=(
                ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION
            ),
            claim_version=(
                LIVE_SELL_INVENTORY_CLAIM_VERSION
            ),
            claim_authorization_sha256=(
                self.authorization
                .authorization_sha256
            ),
            message_sha256=(
                message_plan.message_sha256
            ),
            payer=(
                self.authorization.wallet_pubkey
            ),
            min_context_slot=(
                min_context_slot
            ),
            blockhash_valid_slot=(
                min_context_slot + 1
            ),
            fee_rpc_slot=(
                min_context_slot + 2
            ),
            rpc_total_fee_lamports=(
                self.authorization
                .base_network_fee_lamports
                + self.authorization
                .priority_fee_lamports
            ),
            actual_base_fee_lamports=(
                self.authorization
                .base_network_fee_lamports
            ),
            authorized_base_fee_lamports=(
                self.authorization
                .base_network_fee_lamports
            ),
            authorized_priority_fee_lamports=(
                self.authorization
                .priority_fee_lamports
            ),
            blockhash_context_version=(
                message_plan
                .blockhash_context_version
            ),
            last_valid_block_height=(
                message_plan
                .last_valid_block_height
            ),
            blockhash_rpc_slot=(
                message_plan
                .blockhash_rpc_slot
            ),
            checked_at=100.0,
        )

    def final_min_slot(
        self,
        *,
        pre_sign=None,
        message_plan=None,
    ):
        if pre_sign is None:
            pre_sign = self.pre_sign

        if message_plan is None:
            message_plan = (
                self.message_plan
            )

        return max(
            self.authorization.fee_rpc_slot,
            self.context.global_rpc_slot,
            message_plan.blockhash_rpc_slot,
            pre_sign.min_context_slot,
            pre_sign.blockhash_valid_slot,
            pre_sign.fee_rpc_slot,
        )

    def run_sign(
        self,
        *,
        signer=None,
        rpc=None,
        pre_sign=None,
        message_plan=None,
    ):
        if signer is None:
            signer = CountingSigner(
                self.keypair
            )

        if pre_sign is None:
            pre_sign = self.pre_sign

        if message_plan is None:
            message_plan = (
                self.message_plan
            )

        if rpc is None:
            rpc = FakeRpc(
                slot=(
                    self.final_min_slot(
                        pre_sign=pre_sign,
                        message_plan=(
                            message_plan
                        ),
                    )
                    + 1
                )
            )

        with patch(
            f"{MODULE}.HeliusRpcClient",
            return_value=rpc,
        ):
            result = asyncio.run(
                sign_and_bind_pump_sell_v2(
                    authorization=(
                        self.authorization
                    ),
                    context=self.context,
                    message_plan=(
                        message_plan
                    ),
                    network_validation=(
                        pre_sign
                    ),
                    signer=signer,
                    db_path=self.db_path,
                )
            )

        return (
            result,
            signer,
            rpc,
        )

    def release_claim(
        self,
    ):
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE
                    live_sell_inventory_claims

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

    def test_pre_sign_denied_never_touches_signer_or_rpc(
        self,
    ):
        pre_sign = replace(
            self.pre_sign,
            status=DENY,
            reasons=(
                "TEST_DENIAL",
            ),
        )

        signer = CountingSigner(
            self.keypair
        )

        rpc = FakeRpc(
            slot=10_000
        )

        result, signer, rpc = (
            self.run_sign(
                signer=signer,
                rpc=rpc,
                pre_sign=pre_sign,
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "PRE_SIGN_VALIDATION_DENIED",
            result.reasons,
        )

        self.assertEqual(
            signer.pubkey_calls,
            0,
        )

        self.assertEqual(
            signer.sign_calls,
            0,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

        self.assertIsNone(
            result.signed_transaction_bytes
        )

    def test_terminal_claim_never_touches_signer_or_rpc(
        self,
    ):
        self.release_claim()

        signer = CountingSigner(
            self.keypair
        )

        rpc = FakeRpc(
            slot=10_000
        )

        result, signer, rpc = (
            self.run_sign(
                signer=signer,
                rpc=rpc,
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SELL_CLAIM_VALIDATION_BLOCKED",
            result.reasons,
        )

        self.assertEqual(
            signer.pubkey_calls,
            0,
        )

        self.assertEqual(
            signer.sign_calls,
            0,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    def test_signer_pubkey_mismatch_blocks_before_signing_and_rpc(
        self,
    ):
        signer = CountingSigner(
            Keypair()
        )

        rpc = FakeRpc(
            slot=10_000
        )

        result, signer, rpc = (
            self.run_sign(
                signer=signer,
                rpc=rpc,
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SIGNER_PUBKEY_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            signer.pubkey_calls,
            1,
        )

        self.assertEqual(
            signer.sign_calls,
            0,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    def test_final_blockhash_check_uses_strongest_prior_slot(
        self,
    ):
        signer = CountingSigner(
            self.keypair
        )

        expected_min = (
            self.final_min_slot()
        )

        rpc = FakeRpc(
            slot=expected_min + 1
        )

        result, signer, rpc = (
            self.run_sign(
                signer=signer,
                rpc=rpc,
            )
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertTrue(
            result.is_durably_signed
        )

        self.assertEqual(
            signer.sign_calls,
            1,
        )

        self.assertEqual(
            len(rpc.calls),
            1,
        )

        method, params = (
            rpc.calls[0]
        )

        self.assertEqual(
            method,
            "isBlockhashValid",
        )

        self.assertEqual(
            params[0],
            self.message_plan
            .recent_blockhash,
        )

        self.assertEqual(
            params[1][
                "minContextSlot"
            ],
            expected_min,
        )

        self.assertEqual(
            result.final_blockhash_slot,
            expected_min + 1,
        )

    def test_expired_final_blockhash_never_signs(
        self,
    ):
        signer = CountingSigner(
            self.keypair
        )

        rpc = FakeRpc(
            slot=(
                self.final_min_slot()
                + 1
            ),
            value=False,
        )

        result, signer, rpc = (
            self.run_sign(
                signer=signer,
                rpc=rpc,
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "BLOCKHASH_EXPIRED_BEFORE_SIGNING",
            result.reasons,
        )

        self.assertEqual(
            signer.sign_calls,
            0,
        )

        self.assertEqual(
            len(rpc.calls),
            1,
        )

        self.assertIsNone(
            result.signed_transaction_bytes
        )

    def test_claim_terminalized_during_final_rpc_never_signs(
        self,
    ):
        signer = CountingSigner(
            self.keypair
        )

        rpc = FakeRpc(
            slot=(
                self.final_min_slot()
                + 1
            ),
            on_call=self.release_claim,
        )

        result, signer, rpc = (
            self.run_sign(
                signer=signer,
                rpc=rpc,
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "FINAL_SELL_CLAIM_VALIDATION_BLOCKED",
            result.reasons,
        )

        self.assertEqual(
            signer.sign_calls,
            0,
        )

        self.assertEqual(
            len(rpc.calls),
            1,
        )

        self.assertIsNone(
            result.transaction_signature
        )

        self.assertIsNone(
            result.signed_transaction_bytes
        )

    def test_valid_signing_binds_exact_artifact_once(
        self,
    ):
        signer = CountingSigner(
            self.keypair
        )

        result, signer, rpc = (
            self.run_sign(
                signer=signer,
            )
        )

        self.assertEqual(
            result.signer_version,
            PUMP_SELL_V2_SIGNING_VERSION,
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertTrue(
            result.is_durably_signed
        )

        self.assertEqual(
            signer.pubkey_calls,
            1,
        )

        self.assertEqual(
            signer.sign_calls,
            1,
        )

        self.assertTrue(
            result.execution_record_changed
        )

        self.assertEqual(
            result.message_sha256,
            self.message_plan.message_sha256,
        )

        self.assertIsNotNone(
            result.transaction_signature
        )

        self.assertIsNotNone(
            result.signed_transaction_sha256
        )

        self.assertIsNotNone(
            result.signed_transaction_bytes
        )

        loaded = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            loaded.status,
            EXECUTION_PASS,
        )

        self.assertIsNotNone(
            loaded.record
        )

        self.assertEqual(
            loaded.record
            .transaction_signature,
            result.transaction_signature,
        )

        self.assertEqual(
            loaded.record
            .signed_transaction_sha256,
            result.signed_transaction_sha256,
        )

        self.assertEqual(
            loaded.record
            .signed_transaction_bytes,
            result.signed_transaction_bytes,
        )

    def test_default_signature_never_reaches_durable_bind(
        self,
    ):
        signer = DefaultSignatureSigner(
            self.keypair
        )

        with patch(
            f"{MODULE}."
            "bind_live_sell_signed_artifact"
        ) as bind_mock:
            result, signer, _ = (
                self.run_sign(
                    signer=signer,
                )
            )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "DEFAULT_SIGNATURE_REJECTED",
            result.reasons,
        )

        self.assertEqual(
            signer.sign_calls,
            1,
        )

        bind_mock.assert_not_called()

        self.assertIsNone(
            result.transaction_signature
        )

        self.assertIsNone(
            result.signed_transaction_bytes
        )

    def test_bind_race_discards_created_signed_bytes(
        self,
    ):
        signer = CountingSigner(
            self.keypair
        )

        real_bind = (
            execution_records
            .bind_live_sell_signed_artifact
        )

        def race_bind(
            **kwargs,
        ):
            self.release_claim()

            return real_bind(
                **kwargs
            )

        with patch(
            f"{MODULE}."
            "bind_live_sell_signed_artifact",
            side_effect=race_bind,
        ):
            result, signer, _ = (
                self.run_sign(
                    signer=signer,
                )
            )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SELL_EXECUTION_SIGN_BIND_FAILED",
            result.reasons,
        )

        self.assertEqual(
            signer.sign_calls,
            1,
        )

        #
        # A cryptographic signature existed internally,
        # but because the durable bind lost the ACTIVE
        # claim race, NONE of the signed artifact may
        # escape from the signing result.
        #
        self.assertIsNone(
            result.transaction_signature
        )

        self.assertIsNone(
            result.signed_transaction_sha256
        )

        self.assertIsNone(
            result.signed_transaction_bytes
        )

        self.assertIsNone(
            result.signed_at
        )

    def test_exact_retry_returns_durable_artifact_without_resigning_or_rpc(
        self,
    ):
        signer = CountingSigner(
            self.keypair
        )

        first, signer, first_rpc = (
            self.run_sign(
                signer=signer,
            )
        )

        self.assertEqual(
            first.status,
            PASS,
        )

        self.assertEqual(
            signer.sign_calls,
            1,
        )

        second_rpc = FakeRpc(
            slot=10_000,
            error=AssertionError(
                "RPC must not be called on "
                "durable exact retry"
            ),
        )

        second, signer, second_rpc = (
            self.run_sign(
                signer=signer,
                rpc=second_rpc,
            )
        )

        self.assertEqual(
            second.status,
            PASS,
        )

        self.assertTrue(
            second.is_durably_signed
        )

        self.assertFalse(
            second.execution_record_changed
        )

        self.assertEqual(
            signer.pubkey_calls,
            1,
        )

        self.assertEqual(
            signer.sign_calls,
            1,
        )

        self.assertEqual(
            second_rpc.calls,
            [],
        )

        self.assertEqual(
            second.transaction_signature,
            first.transaction_signature,
        )

        self.assertEqual(
            second.signed_transaction_sha256,
            first.signed_transaction_sha256,
        )

        self.assertEqual(
            second.signed_transaction_bytes,
            first.signed_transaction_bytes,
        )

        self.assertEqual(
            second.signed_at,
            first.signed_at,
        )
