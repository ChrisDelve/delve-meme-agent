import base64
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from solders.signature import Signature
from solders.transaction import (
    VersionedTransaction,
)

from src.execution.pump_buy_v2_execution_preflight import (
    APPROVE,
    DENY,
    UNKNOWN,
    PUMP_BUY_V2_EXECUTION_PREFLIGHT_VERSION,
    preflight_pump_buy_v2_execution,
)
from src.execution.pump_buy_v2_pre_sign_validation import (
    PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION,
    PumpBuyV2PreSignValidation,
)
from tests import (
    test_pump_buy_v2_unsigned_message
    as unsigned_message_tests,
)


MODULE = (
    "src.execution."
    "pump_buy_v2_execution_preflight"
)


class FakeSimulationRpc:
    def __init__(
        self,
        *,
        account_count,
        slot=202,
        fee=12_000,
        payer_debit=8_900_000,
        units_consumed=180_000,
        err=None,
        malformed_value=False,
        error=None,
    ):
        self.account_count = (
            account_count
        )

        self.slot = slot
        self.fee = fee
        self.payer_debit = (
            payer_debit
        )

        self.units_consumed = (
            units_consumed
        )

        self.err = err

        self.malformed_value = (
            malformed_value
        )

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

        if method != "simulateTransaction":
            raise RuntimeError(
                f"Unexpected RPC method: {method}"
            )

        if self.malformed_value:
            return {
                "context": {
                    "slot": self.slot
                },
                "value": "bad-value",
            }

        pre = [
            1_000_000
            for _ in range(
                self.account_count
            )
        ]

        post = list(
            pre
        )

        pre[
            0
        ] = 100_000_000

        post[
            0
        ] = (
            100_000_000
            - self.payer_debit
        )

        return {
            "context": {
                "slot": self.slot
            },
            "value": {
                "err": self.err,
                "logs": [
                    "Program test invoke [1]",
                    "Program test success",
                ],
                "replacementBlockhash": None,
                "unitsConsumed": (
                    self.units_consumed
                ),
                "fee": self.fee,
                "preBalances": pre,
                "postBalances": post,
            },
        }


class PumpBuyV2ExecutionPreflightTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        helper = (
            unsigned_message_tests
            .PumpBuyV2UnsignedMessageTests(
                methodName=(
                    "test_builds_unsigned_message_v0"
                )
            )
        )

        helper.setUp()

        self.helper = helper

        self.authorization = (
            helper.make_authorization()
        )

        self.context = (
            helper.make_context()
        )

        self.message_plan = (
            helper.build(
                authorization=(
                    self.authorization
                ),
                context=self.context,
            )
        )

        self.network_validation = (
            PumpBuyV2PreSignValidation(
                validator_version=(
                    PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION
                ),
                status=APPROVE,
                reasons=(),
                reservation_id=(
                    self.authorization
                    .reservation_id
                ),
                message_sha256=(
                    self.message_plan
                    .message_sha256
                ),
                blockhash_context_version=(
                    self.message_plan
                    .blockhash_context_version
                ),
                last_valid_block_height=(
                    self.message_plan
                    .last_valid_block_height
                ),
                blockhash_rpc_slot=(
                    self.message_plan
                    .blockhash_rpc_slot
                ),
                payer=(
                    self.authorization
                    .wallet_pubkey
                ),
                min_context_slot=101,
                blockhash_valid_slot=200,
                fee_rpc_slot=201,
                rpc_total_fee_lamports=(
                    12_000
                ),
                actual_base_fee_lamports=(
                    5_000
                ),
                authorized_base_fee_lamports=(
                    5_000
                ),
                authorized_priority_fee_lamports=(
                    7_000
                ),
                authorized_rent_lamports=0,
                authorized_wallet_liability_lamports=(
                    self.authorization
                    .wallet_cost_lamports
                ),
                checked_at=time.time(),
            )
        )

    def make_rpc(
        self,
        **kwargs,
    ):
        return FakeSimulationRpc(
            account_count=len(
                self.message_plan
                .message
                .account_keys
            ),
            **kwargs,
        )

    async def preflight(
        self,
        *,
        authorization=None,
        context=None,
        message_plan=None,
        network_validation=None,
        rpc=None,
    ):
        authorization = (
            self.authorization
            if authorization is None
            else authorization
        )

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

        network_validation = (
            self.network_validation
            if network_validation is None
            else network_validation
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
            result = await (
                preflight_pump_buy_v2_execution(
                    authorization=authorization,
                    context=context,
                    message_plan=message_plan,
                    network_validation=(
                        network_validation
                    ),
                )
            )

        return (
            result,
            rpc,
        )

    async def test_expiry_binding_mismatch_denies(
        self,
    ):
        from dataclasses import replace

        network_validation = replace(
            self.network_validation,
            last_valid_block_height=(
                self.message_plan
                .last_valid_block_height
                + 1
            ),
        )

        result, rpc = await self.preflight(
            network_validation=(
                network_validation
            ),
        )

        self.assertEqual(
            result.status,
            "DENY",
        )

        self.assertIn(
            "BLOCKHASH_EXPIRY_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertFalse(
            result.allows_signing
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_valid_simulation_approves(
        self,
    ):
        result, rpc = (
            await self.preflight()
        )

        self.assertEqual(
            result.validator_version,
            PUMP_BUY_V2_EXECUTION_PREFLIGHT_VERSION,
        )

        self.assertEqual(
            result.status,
            APPROVE,
        )

        self.assertTrue(
            result.allows_signing
        )

        self.assertEqual(
            result.simulated_fee_lamports,
            12_000,
        )

        self.assertEqual(
            result.payer_debit_lamports,
            8_900_000,
        )

        self.assertEqual(
            (
                result
                .non_fee_payer_debit_lamports
            ),
            8_888_000,
        )

        self.assertEqual(
            len(
                rpc.calls
            ),
            1,
        )

        method, params = (
            rpc.calls[
                0
            ]
        )

        self.assertEqual(
            method,
            "simulateTransaction",
        )

        config = params[
            1
        ]

        self.assertFalse(
            config[
                "sigVerify"
            ]
        )

        self.assertFalse(
            config[
                "replaceRecentBlockhash"
            ]
        )

        self.assertEqual(
            config[
                "encoding"
            ],
            "base64",
        )

        self.assertEqual(
            config[
                "minContextSlot"
            ],
            201,
        )

    async def test_wire_transaction_has_only_default_signature(
        self,
    ):
        _, rpc = await self.preflight()

        encoded = (
            rpc.calls[
                0
            ][
                1
            ][
                0
            ]
        )

        transaction = (
            VersionedTransaction.from_bytes(
                base64.b64decode(
                    encoded
                )
            )
        )

        self.assertEqual(
            tuple(
                transaction.signatures
            ),
            (
                Signature.default(),
            ),
        )

        self.assertEqual(
            transaction.verify_with_results(),
            [
                False
            ],
        )

    async def test_program_failure_denies(
        self,
    ):
        rpc = self.make_rpc(
            err={
                "InstructionError": [
                    2,
                    "Custom",
                ]
            }
        )

        result, _ = await self.preflight(
            rpc=rpc
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "EXECUTION_SIMULATION_FAILED",
            result.reasons,
        )

        self.assertIsNotNone(
            result.simulation_error
        )

    async def test_wallet_liability_overrun_denies(
        self,
    ):
        rpc = self.make_rpc(
            payer_debit=(
                self.authorization
                .wallet_cost_lamports
                + 1
            )
        )

        result, _ = await self.preflight(
            rpc=rpc
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "SIMULATED_WALLET_LIABILITY_EXCEEDED",
            result.reasons,
        )

    async def test_compute_limit_overrun_denies(
        self,
    ):
        rpc = self.make_rpc(
            units_consumed=(
                self.message_plan
                .compute_unit_limit
                + 1
            )
        )

        result, _ = await self.preflight(
            rpc=rpc
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "SIMULATION_COMPUTE_LIMIT_EXCEEDED",
            result.reasons,
        )

    async def test_fee_mismatch_is_unknown(
        self,
    ):
        rpc = self.make_rpc(
            fee=12_001
        )

        result, _ = await self.preflight(
            rpc=rpc
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SIMULATED_FEE_MISMATCH",
            result.reasons,
        )

    async def test_stale_simulation_context_is_unknown(
        self,
    ):
        rpc = self.make_rpc(
            slot=200
        )

        result, _ = await self.preflight(
            rpc=rpc
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SIMULATION_RPC_CONTEXT_INVALID",
            result.reasons,
        )

    async def test_malformed_simulation_is_unknown(
        self,
    ):
        rpc = self.make_rpc(
            malformed_value=True
        )

        result, _ = await self.preflight(
            rpc=rpc
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertFalse(
            result.allows_signing
        )

    async def test_unapproved_network_validation_stops_before_rpc(
        self,
    ):
        network_validation = replace(
            self.network_validation,
            status=DENY,
        )

        rpc = self.make_rpc()

        result, rpc = (
            await self.preflight(
                network_validation=(
                    network_validation
                ),
                rpc=rpc,
            )
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "NETWORK_PRE_SIGN_NOT_APPROVED",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )


if __name__ == "__main__":
    unittest.main()
