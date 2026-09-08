import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from src.execution.pump_buy_v2_pre_sign_validation import (
    APPROVE,
    DENY,
    UNKNOWN,
    PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION,
    validate_pump_buy_v2_pre_sign,
)
from src.portfolio.live_reservations import (
    ACTIVE,
    RESERVATION_VERSION,
    LiveCapitalReservation,
)
from tests import (
    test_pump_buy_v2_unsigned_message
    as unsigned_message_tests,
)


MODULE = (
    "src.execution."
    "pump_buy_v2_pre_sign_validation"
)


class FakeRpc:
    def __init__(
        self,
        *,
        blockhash_result=None,
        fee_result=None,
        error=None,
    ):
        self.blockhash_result = (
            {
                "context": {
                    "slot": 200
                },
                "value": True,
            }
            if blockhash_result is None
            else blockhash_result
        )

        self.fee_result = (
            {
                "context": {
                    "slot": 201
                },
                "value": 12_000,
            }
            if fee_result is None
            else fee_result
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

        if method == "isBlockhashValid":
            return self.blockhash_result

        if method == "getFeeForMessage":
            return self.fee_result

        raise RuntimeError(
            f"Unexpected RPC method: {method}"
        )


class PumpBuyV2PreSignValidationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        helper = (
            unsigned_message_tests.PumpBuyV2UnsignedMessageTests(
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

        self.reservation = (
            self.make_reservation(
                self.authorization
            )
        )

    def make_reservation(
        self,
        authorization,
    ):
        now = time.time()

        return LiveCapitalReservation(
            reservation_id=(
                authorization.reservation_id
            ),
            reservation_version=(
                RESERVATION_VERSION
            ),
            mint=authorization.mint,
            side=authorization.side,
            wallet_pubkey=(
                authorization.wallet_pubkey
            ),
            spend_lamports=(
                authorization.spend_lamports
            ),
            wallet_cost_lamports=(
                authorization
                .wallet_cost_lamports
            ),
            status=ACTIVE,
            risk_governor_version=(
                authorization
                .risk_governor_version
            ),
            risk_simulation_sha256=(
                authorization
                .simulation_sha256
            ),
            base_available_cash_lamports=(
                100_000_000
            ),
            base_open_exposure_lamports=0,
            base_open_positions=0,
            reserved_exposure_before_lamports=0,
            reserved_cash_before_lamports=0,
            active_reservations_before=0,
            created_at=now,
            expires_at=(
                now
                + 60.0
            ),
            signed_at=None,
            transaction_signature=None,
        )

    async def validate(
        self,
        *,
        authorization=None,
        context=None,
        message_plan=None,
        reservation=None,
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

        reservation = (
            self.reservation
            if reservation is None
            else reservation
        )

        rpc = (
            FakeRpc()
            if rpc is None
            else rpc
        )

        with (
            patch(
                f"{MODULE}."
                "load_capital_reservation",
                return_value=reservation,
            ),
            patch(
                f"{MODULE}."
                "HeliusRpcClient",
                return_value=rpc,
            ),
        ):
            result = (
                await validate_pump_buy_v2_pre_sign(
                    authorization=authorization,
                    context=context,
                    message_plan=message_plan,
                )
            )

        return (
            result,
            rpc,
        )

    async def test_valid_chain_state_approves(
        self,
    ):
        result, rpc = await self.validate()

        self.assertEqual(
            result.validator_version,
            PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION,
        )

        self.assertEqual(
            result.status,
            APPROVE,
        )

        self.assertTrue(
            result.allows_signing
        )

        self.assertEqual(
            result.rpc_total_fee_lamports,
            12_000,
        )

        self.assertEqual(
            result.actual_base_fee_lamports,
            5_000,
        )

        self.assertEqual(
            [
                item[
                    0
                ]
                for item in rpc.calls
            ],
            [
                "isBlockhashValid",
                "getFeeForMessage",
            ],
        )

    async def test_invalid_blockhash_denies(
        self,
    ):
        rpc = FakeRpc(
            blockhash_result={
                "context": {
                    "slot": 200
                },
                "value": False,
            },
        )

        result, rpc = await self.validate(
            rpc=rpc
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
            len(
                rpc.calls
            ),
            1,
        )

    async def test_base_fee_over_budget_denies(
        self,
    ):
        rpc = FakeRpc(
            fee_result={
                "context": {
                    "slot": 201
                },
                "value": 12_001,
            },
        )

        result, _ = await self.validate(
            rpc=rpc
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            (
                "BASE_NETWORK_FEE_"
                "EXCEEDS_AUTHORIZATION"
            ),
            result.reasons,
        )

    async def test_null_fee_denies(
        self,
    ):
        rpc = FakeRpc(
            fee_result={
                "context": {
                    "slot": 201
                },
                "value": None,
            },
        )

        result, _ = await self.validate(
            rpc=rpc
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "FEE_UNAVAILABLE_FOR_BLOCKHASH",
            result.reasons,
        )

    async def test_malformed_fee_is_unknown(
        self,
    ):
        rpc = FakeRpc(
            fee_result={
                "context": {
                    "slot": 201
                },
                "value": "12000",
            },
        )

        result, _ = await self.validate(
            rpc=rpc
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertFalse(
            result.allows_signing
        )

    async def test_missing_reservation_denies(
        self,
    ):
        with (
            patch(
                f"{MODULE}."
                "load_capital_reservation",
                return_value=None,
            ),
            patch(
                f"{MODULE}."
                "HeliusRpcClient",
                return_value=FakeRpc(),
            ),
        ):
            result = (
                await validate_pump_buy_v2_pre_sign(
                    authorization=(
                        self.authorization
                    ),
                    context=self.context,
                    message_plan=(
                        self.message_plan
                    ),
                )
            )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "RESERVATION_NOT_FOUND",
            result.reasons,
        )

    async def test_non_active_reservation_denies(
        self,
    ):
        reservation = replace(
            self.reservation,
            status="SIGNED",
        )

        result, _ = await self.validate(
            reservation=reservation
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "RESERVATION_NOT_ACTIVE",
            result.reasons,
        )

    async def test_reservation_liability_mismatch_denies(
        self,
    ):
        reservation = replace(
            self.reservation,
            wallet_cost_lamports=(
                self.reservation
                .wallet_cost_lamports
                + 1
            ),
        )

        result, _ = await self.validate(
            reservation=reservation
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "RESERVATION_ECONOMICS_MISMATCH",
            result.reasons,
        )

    async def test_message_fingerprint_mismatch_denies(
        self,
    ):
        plan = replace(
            self.message_plan,
            message_sha256=(
                "00" * 32
            ),
        )

        result, rpc = await self.validate(
            message_plan=plan
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

    async def test_priority_fee_binding_mismatch_denies(
        self,
    ):
        plan = replace(
            self.message_plan,
            planned_priority_fee_lamports=(
                self.message_plan
                .planned_priority_fee_lamports
                - 1
            ),
        )

        result, rpc = await self.validate(
            message_plan=plan
        )

        self.assertEqual(
            result.status,
            DENY,
        )

        self.assertIn(
            "MESSAGE_PRIORITY_FEE_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_rpc_failure_is_unknown(
        self,
    ):
        rpc = FakeRpc(
            error=RuntimeError(
                "synthetic failure"
            )
        )

        result, _ = await self.validate(
            rpc=rpc
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertFalse(
            result.allows_signing
        )

    async def test_stale_fee_context_is_unknown(
        self,
    ):
        rpc = FakeRpc(
            blockhash_result={
                "context": {
                    "slot": 200
                },
                "value": True,
            },
            fee_result={
                "context": {
                    "slot": 199
                },
                "value": 12_000,
            },
        )

        result, _ = await self.validate(
            rpc=rpc
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FEE_RPC_CONTEXT_INVALID",
            result.reasons,
        )


if __name__ == "__main__":
    unittest.main()
