import asyncio
import hashlib
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from solders.hash import Hash
from solders.instruction import (
    AccountMeta,
    Instruction,
)
from solders.keypair import Keypair
from solders.message import (
    MessageV0,
    to_bytes_versioned,
)
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from src.execution.live_blockhash_context import (
    LIVE_BLOCKHASH_CONTEXT_VERSION,
)
from src.execution.order_authorization import (
    AUTHORIZATION_VERSION,
    AUTHORIZE,
    BUY,
)
from src.execution.pump_buy_v2_account_context import (
    PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION,
)
from src.execution.pump_buy_v2_execution_preflight import (
    APPROVE,
    PUMP_BUY_V2_EXECUTION_PREFLIGHT_VERSION,
)
from src.execution.pump_buy_v2_pre_sign_validation import (
    PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION,
)
from src.execution.pump_buy_v2_signing import (
    BLOCK,
    PASS,
    UNKNOWN,
    PUMP_BUY_V2_SIGNING_VERSION,
    sign_and_bind_pump_buy_v2,
)
from src.execution.pump_buy_v2_unsigned_message import (
    PUMP_BUY_V2_UNSIGNED_MESSAGE_VERSION,
)
from src.portfolio.live_reservations import (
    ACTIVE,
    RESERVATION_VERSION,
    SIGNED,
    LiveCapitalReservation,
    ReservationTransitionResult,
)


MODULE = (
    "src.execution.pump_buy_v2_signing"
)


def ns_replace(
    value,
    **updates,
):
    data = vars(
        value
    ).copy()

    data.update(
        updates
    )

    return SimpleNamespace(
        **data
    )


class CountingSigner:
    def __init__(
        self,
        keypair,
    ):
        self.keypair = keypair
        self.sign_calls = 0

    def pubkey(self):
        return self.keypair.pubkey()

    def sign_message(
        self,
        message,
    ):
        self.sign_calls += 1

        return self.keypair.sign_message(
            message
        )


class InvalidSignatureSigner:
    def __init__(
        self,
        *,
        claimed_pubkey,
        signing_keypair,
    ):
        self.claimed_pubkey = (
            claimed_pubkey
        )

        self.signing_keypair = (
            signing_keypair
        )

        self.sign_calls = 0

    def pubkey(self):
        return self.claimed_pubkey

    def sign_message(
        self,
        message,
    ):
        self.sign_calls += 1

        return (
            self.signing_keypair
            .sign_message(
                message
            )
        )


class FakeFinalBlockhashRpc:
    def __init__(
        self,
        *,
        valid=True,
        slot=203,
        error=None,
    ):
        self.valid = valid
        self.slot = slot
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

        if method != "isBlockhashValid":
            raise RuntimeError(
                f"Unexpected RPC method: {method}"
            )

        return {
            "context": {
                "slot": self.slot
            },
            "value": self.valid,
        }


class PumpBuyV2SigningTests(
    unittest.TestCase
):
    def setUp(self):
        self.keypair = Keypair()
        self.other_keypair = Keypair()

        self.wallet_pubkey = str(
            self.keypair.pubkey()
        )

        self.mint = str(
            Pubkey.new_unique()
        )

        self.reservation_id = (
            "signing-reservation-test"
        )

        self.simulation_sha256 = (
            "33" * 32
        )

        self.risk_governor_version = (
            "risk-governor-test"
        )

        instruction = Instruction(
            Pubkey.new_unique(),
            b"delve-signing-test",
            [
                AccountMeta(
                    self.keypair.pubkey(),
                    True,
                    True,
                )
            ],
        )

        recent_blockhash = (
            Hash.new_unique()
        )

        self.message = (
            MessageV0.try_compile(
                self.keypair.pubkey(),
                [
                    instruction
                ],
                [],
                recent_blockhash,
            )
        )

        self.message_bytes = (
            to_bytes_versioned(
                self.message
            )
        )

        self.message_sha256 = (
            hashlib.sha256(
                self.message_bytes
            ).hexdigest()
        )

        placeholder_transaction = (
            VersionedTransaction.populate(
                self.message,
                [
                    Signature.default()
                ],
            )
        )

        self.estimated_size = len(
            bytes(
                placeholder_transaction
            )
        )

        self.authorization = (
            SimpleNamespace(
                authorization_version=(
                    AUTHORIZATION_VERSION
                ),
                status=AUTHORIZE,
                side=BUY,
                reservation_id=(
                    self.reservation_id
                ),
                reservation_version=(
                    RESERVATION_VERSION
                ),
                wallet_pubkey=(
                    self.wallet_pubkey
                ),
                mint=self.mint,
                spend_lamports=9_000_000,
                wallet_cost_lamports=(
                    9_012_000
                ),
                risk_governor_version=(
                    self.risk_governor_version
                ),
                simulation_sha256=(
                    self.simulation_sha256
                ),
                is_valid=lambda: True,
            )
        )

        self.context = SimpleNamespace(
            resolver_version=(
                PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION
            ),
            authorization_version=(
                AUTHORIZATION_VERSION
            ),
            reservation_id=(
                self.reservation_id
            ),
            simulation_sha256=(
                self.simulation_sha256
            ),
            user=self.wallet_pubkey,
        )

        self.message_plan = (
            SimpleNamespace(
                builder_version=(
                    PUMP_BUY_V2_UNSIGNED_MESSAGE_VERSION
                ),
                authorization_version=(
                    AUTHORIZATION_VERSION
                ),
                reservation_id=(
                    self.reservation_id
                ),
                simulation_sha256=(
                    self.simulation_sha256
                ),
                payer=self.wallet_pubkey,
                blockhash_context_version=(
                    LIVE_BLOCKHASH_CONTEXT_VERSION
                ),
                last_valid_block_height=350,
                blockhash_rpc_slot=200,
                recent_blockhash=str(
                    recent_blockhash
                ),
                message_sha256=(
                    self.message_sha256
                ),
                estimated_signed_transaction_size_bytes=(
                    self.estimated_size
                ),
                message=self.message,
            )
        )

        self.network_validation = (
            SimpleNamespace(
                validator_version=(
                    PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION
                ),
                status=APPROVE,
                allows_signing=True,
                reservation_id=(
                    self.reservation_id
                ),
                payer=self.wallet_pubkey,
                message_sha256=(
                    self.message_sha256
                ),
                blockhash_context_version=(
                    LIVE_BLOCKHASH_CONTEXT_VERSION
                ),
                last_valid_block_height=350,
                blockhash_rpc_slot=200,
                authorized_wallet_liability_lamports=(
                    self.authorization
                    .wallet_cost_lamports
                ),
                min_context_slot=101,
                blockhash_valid_slot=200,
                fee_rpc_slot=201,
            )
        )

        self.execution_preflight = (
            SimpleNamespace(
                validator_version=(
                    PUMP_BUY_V2_EXECUTION_PREFLIGHT_VERSION
                ),
                status=APPROVE,
                allows_signing=True,
                reservation_id=(
                    self.reservation_id
                ),
                message_sha256=(
                    self.message_sha256
                ),
                blockhash_context_version=(
                    LIVE_BLOCKHASH_CONTEXT_VERSION
                ),
                last_valid_block_height=350,
                blockhash_rpc_slot=200,
                authorized_wallet_liability_lamports=(
                    self.authorization
                    .wallet_cost_lamports
                ),
                simulation_slot=202,
            )
        )

        now = time.time()

        self.reservation = (
            LiveCapitalReservation(
                reservation_id=(
                    self.reservation_id
                ),
                reservation_version=(
                    RESERVATION_VERSION
                ),
                mint=self.mint,
                side=BUY,
                wallet_pubkey=(
                    self.wallet_pubkey
                ),
                spend_lamports=(
                    self.authorization
                    .spend_lamports
                ),
                wallet_cost_lamports=(
                    self.authorization
                    .wallet_cost_lamports
                ),
                status=ACTIVE,
                risk_governor_version=(
                    self.risk_governor_version
                ),
                risk_simulation_sha256=(
                    self.simulation_sha256
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
                signed_message_sha256=None,
                signed_transaction_sha256=None,
                signed_transaction_bytes=None,
            )
        )

    def make_success_transition(
        self,
        reservation,
        **kwargs,
    ):
        transaction_bytes = (
            kwargs[
                "signed_transaction_bytes"
            ]
        )

        transaction_sha256 = (
            hashlib.sha256(
                transaction_bytes
            ).hexdigest()
        )

        persisted = replace(
            reservation,
            status=SIGNED,
            signed_at=time.time(),
            transaction_signature=(
                kwargs[
                    "transaction_signature"
                ]
            ),
            signed_message_sha256=(
                kwargs[
                    "signed_message_sha256"
                ]
            ),
            signed_transaction_sha256=(
                transaction_sha256
            ),
            signed_transaction_bytes=(
                transaction_bytes
            ),
            recent_blockhash=(
                kwargs[
                    "recent_blockhash"
                ]
            ),
            last_valid_block_height=(
                kwargs[
                    "last_valid_block_height"
                ]
            ),
            blockhash_rpc_slot=(
                kwargs[
                    "blockhash_rpc_slot"
                ]
            ),
        )

        return ReservationTransitionResult(
            status=PASS,
            reasons=(),
            reservation=persisted,
            changed=True,
        )

    def sign(
        self,
        *,
        signer=None,
        authorization=None,
        context=None,
        message_plan=None,
        network_validation=None,
        execution_preflight=None,
        reservation=None,
        bind_result=None,
        rpc=None,
        load_side_effect=None,
    ):
        signer = (
            CountingSigner(
                self.keypair
            )
            if signer is None
            else signer
        )

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

        execution_preflight = (
            self.execution_preflight
            if execution_preflight is None
            else execution_preflight
        )

        reservation = (
            self.reservation
            if reservation is None
            else reservation
        )

        rpc = (
            FakeFinalBlockhashRpc()
            if rpc is None
            else rpc
        )

        if load_side_effect is None:
            load_side_effect = [
                reservation,
                reservation,
            ]

        def bind_side_effect(
            **kwargs,
        ):
            if bind_result is not None:
                return bind_result

            return self.make_success_transition(
                reservation,
                **kwargs,
            )

        with (
            patch(
                f"{MODULE}."
                "load_capital_reservation",
                side_effect=(
                    load_side_effect
                ),
            ) as load_mock,
            patch(
                f"{MODULE}."
                "HeliusRpcClient",
                return_value=rpc,
            ),
            patch(
                f"{MODULE}."
                "bind_reservation_signed_transaction",
                side_effect=bind_side_effect,
            ) as bind_mock,
        ):
            result = asyncio.run(
                sign_and_bind_pump_buy_v2(
                    authorization=authorization,
                    context=context,
                    message_plan=message_plan,
                    network_validation=(
                        network_validation
                    ),
                    execution_preflight=(
                        execution_preflight
                    ),
                    signer=signer,
                )
            )

        return (
            result,
            signer,
            load_mock,
            bind_mock,
        )

    def test_expiry_binding_mismatch_blocks_before_signing(
        self,
    ):
        execution_preflight = ns_replace(
            self.execution_preflight,
            blockhash_rpc_slot=(
                self.message_plan
                .blockhash_rpc_slot
                + 1
            ),
        )

        signer = CountingSigner(
            self.keypair
        )

        (
            result,
            returned_signer,
            load_mock,
            bind_mock,
        ) = self.sign(
            signer=signer,
            execution_preflight=(
                execution_preflight
            ),
        )

        self.assertEqual(
            result.status,
            "BLOCK",
        )

        self.assertIn(
            "BLOCKHASH_EXPIRY_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            returned_signer.sign_calls,
            0,
        )

        bind_mock.assert_not_called()

    def test_valid_signing_binds_exact_artifact(
        self,
    ):
        (
            result,
            signer,
            load_mock,
            bind_mock,
        ) = self.sign()

        self.assertEqual(
            result.signer_version,
            PUMP_BUY_V2_SIGNING_VERSION,
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
            result.signer_pubkey,
            self.wallet_pubkey,
        )

        self.assertEqual(
            result.message_sha256,
            self.message_sha256,
        )

        self.assertEqual(
            result.signed_transaction_sha256,
            hashlib.sha256(
                result.signed_transaction_bytes
            ).hexdigest(),
        )

        transaction = (
            VersionedTransaction.from_bytes(
                result.signed_transaction_bytes
            )
        )

        self.assertEqual(
            transaction.verify_with_results(),
            [
                True
            ],
        )

        self.assertEqual(
            str(
                transaction.signatures[
                    0
                ]
            ),
            result.transaction_signature,
        )

        self.assertEqual(
            to_bytes_versioned(
                transaction.message
            ),
            self.message_bytes,
        )

        self.assertEqual(
            load_mock.call_count,
            2,
        )

        bind_mock.assert_called_once()

        bind_kwargs = (
            bind_mock.call_args.kwargs
        )

        self.assertEqual(
            bind_kwargs[
                "reservation_id"
            ],
            self.reservation_id,
        )

        self.assertEqual(
            bind_kwargs[
                "transaction_signature"
            ],
            result.transaction_signature,
        )

        self.assertEqual(
            bind_kwargs[
                "signed_message_sha256"
            ],
            self.message_sha256,
        )

        self.assertEqual(
            bind_kwargs[
                "signed_transaction_bytes"
            ],
            result.signed_transaction_bytes,
        )

    def test_final_blockhash_check_is_anchored_to_simulation(
        self,
    ):
        rpc = FakeFinalBlockhashRpc(
            valid=True,
            slot=203,
        )

        (
            result,
            signer,
            _,
            bind_mock,
        ) = self.sign(
            rpc=rpc
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertEqual(
            signer.sign_calls,
            1,
        )

        bind_mock.assert_called_once()

        self.assertEqual(
            len(
                rpc.calls
            ),
            1,
        )

        method, params = rpc.calls[
            0
        ]

        self.assertEqual(
            method,
            "isBlockhashValid",
        )

        self.assertEqual(
            params[
                0
            ],
            self.message_plan
            .recent_blockhash,
        )

        self.assertEqual(
            params[
                1
            ][
                "minContextSlot"
            ],
            202,
        )

    def test_expired_final_blockhash_blocks_before_signing(
        self,
    ):
        rpc = FakeFinalBlockhashRpc(
            valid=False,
            slot=203,
        )

        (
            result,
            signer,
            _,
            bind_mock,
        ) = self.sign(
            rpc=rpc
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

        bind_mock.assert_not_called()

        self.assertIsNone(
            result.signed_transaction_bytes
        )

    def test_reservation_expiring_during_final_check_blocks_signing(
        self,
    ):
        expired = replace(
            self.reservation,
            status="EXPIRED",
        )

        (
            result,
            signer,
            load_mock,
            bind_mock,
        ) = self.sign(
            load_side_effect=[
                self.reservation,
                expired,
            ]
        )

        self.assertEqual(
            load_mock.call_count,
            2,
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "RESERVATION_NOT_ACTIVE",
            result.reasons,
        )

        self.assertEqual(
            signer.sign_calls,
            0,
        )

        bind_mock.assert_not_called()

        self.assertIsNone(
            result.signed_transaction_bytes
        )

    def test_signer_pubkey_mismatch_blocks_before_signing(
        self,
    ):
        signer = CountingSigner(
            self.other_keypair
        )

        (
            result,
            signer,
            _,
            bind_mock,
        ) = self.sign(
            signer=signer
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
            signer.sign_calls,
            0,
        )

        bind_mock.assert_not_called()

        self.assertIsNone(
            result.signed_transaction_bytes
        )

    def test_invalid_signature_blocks_before_bind(
        self,
    ):
        signer = InvalidSignatureSigner(
            claimed_pubkey=(
                self.keypair.pubkey()
            ),
            signing_keypair=(
                self.other_keypair
            ),
        )

        (
            result,
            signer,
            _,
            bind_mock,
        ) = self.sign(
            signer=signer
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SIGNATURE_VERIFICATION_FAILED",
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

    def test_network_validation_must_be_approved(
        self,
    ):
        network_validation = ns_replace(
            self.network_validation,
            status=BLOCK,
            allows_signing=False,
        )

        (
            result,
            signer,
            load_mock,
            bind_mock,
        ) = self.sign(
            network_validation=(
                network_validation
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "NETWORK_PRE_SIGN_NOT_APPROVED",
            result.reasons,
        )

        self.assertEqual(
            signer.sign_calls,
            0,
        )

        load_mock.assert_not_called()
        bind_mock.assert_not_called()

    def test_execution_preflight_must_be_approved(
        self,
    ):
        execution_preflight = ns_replace(
            self.execution_preflight,
            status=BLOCK,
            allows_signing=False,
        )

        (
            result,
            signer,
            load_mock,
            bind_mock,
        ) = self.sign(
            execution_preflight=(
                execution_preflight
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "EXECUTION_PREFLIGHT_NOT_APPROVED",
            result.reasons,
        )

        self.assertEqual(
            signer.sign_calls,
            0,
        )

        load_mock.assert_not_called()
        bind_mock.assert_not_called()

    def test_message_fingerprint_mismatch_blocks(
        self,
    ):
        message_plan = ns_replace(
            self.message_plan,
            message_sha256=(
                "00" * 32
            ),
        )

        (
            result,
            signer,
            _,
            bind_mock,
        ) = self.sign(
            message_plan=message_plan
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "MESSAGE_FINGERPRINT_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            signer.sign_calls,
            0,
        )

        bind_mock.assert_not_called()

    def test_non_active_reservation_blocks_before_signer(
        self,
    ):
        reservation = replace(
            self.reservation,
            status="EXPIRED",
        )

        (
            result,
            signer,
            _,
            bind_mock,
        ) = self.sign(
            reservation=reservation
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "RESERVATION_NOT_ACTIVE",
            result.reasons,
        )

        self.assertEqual(
            signer.sign_calls,
            0,
        )

        bind_mock.assert_not_called()

    def test_active_reservation_with_signed_artifact_blocks(
        self,
    ):
        reservation = replace(
            self.reservation,
            transaction_signature=(
                "CorruptActiveSignature"
            ),
        )

        (
            result,
            signer,
            _,
            bind_mock,
        ) = self.sign(
            reservation=reservation
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "ACTIVE_RESERVATION_HAS_SIGNED_ARTIFACT",
            result.reasons,
        )

        self.assertEqual(
            signer.sign_calls,
            0,
        )

        bind_mock.assert_not_called()

    def test_bind_race_failure_discards_signed_bytes(
        self,
    ):
        bind_result = (
            ReservationTransitionResult(
                status=BLOCK,
                reasons=(
                    "RESERVATION_NOT_ACTIVE",
                ),
                reservation=replace(
                    self.reservation,
                    status="EXPIRED",
                ),
                changed=False,
            )
        )

        (
            result,
            signer,
            _,
            bind_mock,
        ) = self.sign(
            bind_result=bind_result
        )

        self.assertEqual(
            signer.sign_calls,
            1,
        )

        bind_mock.assert_called_once()

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "RESERVATION_SIGN_BIND_FAILED",
            result.reasons,
        )

        self.assertIn(
            "RESERVATION_NOT_ACTIVE",
            result.reasons,
        )

        self.assertIsNone(
            result.transaction_signature
        )

        self.assertIsNone(
            result.signed_transaction_sha256
        )

        self.assertIsNone(
            result.signed_transaction_bytes
        )

        self.assertFalse(
            result.is_durably_signed
        )

    def test_persisted_artifact_mismatch_fails_closed(
        self,
    ):
        corrupted = replace(
            self.reservation,
            status=SIGNED,
            signed_at=time.time(),
            transaction_signature=(
                "DifferentSignature"
            ),
            signed_message_sha256=(
                self.message_sha256
            ),
            signed_transaction_sha256=(
                "44" * 32
            ),
            signed_transaction_bytes=(
                b"different-transaction"
            ),
        )

        bind_result = (
            ReservationTransitionResult(
                status=PASS,
                reasons=(),
                reservation=corrupted,
                changed=True,
            )
        )

        (
            result,
            signer,
            _,
            bind_mock,
        ) = self.sign(
            bind_result=bind_result
        )

        self.assertEqual(
            signer.sign_calls,
            1,
        )

        bind_mock.assert_called_once()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "PERSISTED_SIGNED_ARTIFACT_MISMATCH",
            result.reasons,
        )

        self.assertIsNone(
            result.signed_transaction_bytes
        )

        self.assertFalse(
            result.is_durably_signed
        )

    def test_signed_transaction_size_mismatch_blocks_before_bind(
        self,
    ):
        message_plan = ns_replace(
            self.message_plan,
            estimated_signed_transaction_size_bytes=(
                self.estimated_size
                + 1
            ),
        )

        (
            result,
            signer,
            _,
            bind_mock,
        ) = self.sign(
            message_plan=message_plan
        )

        self.assertEqual(
            signer.sign_calls,
            1,
        )

        bind_mock.assert_not_called()

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SIGNED_TRANSACTION_SIZE_MISMATCH",
            result.reasons,
        )

        self.assertIsNone(
            result.signed_transaction_bytes
        )


if __name__ == "__main__":
    unittest.main()
