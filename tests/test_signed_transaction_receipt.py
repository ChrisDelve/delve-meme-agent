import base64
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.transaction import VersionedTransaction

from src.execution.signed_transaction_receipt import (
    BLOCK,
    RESOLVED,
    SIGNED_TRANSACTION_RECEIPT_VERSION,
    UNKNOWN,
    resolve_signed_transaction_receipt,
)
from src.execution.signed_transaction_status import (
    ABSENT_STILL_VALID,
    KNOWN,
    UNKNOWN as STATUS_UNKNOWN,
)
from src.portfolio.live_reservations import (
    RESERVATION_VERSION,
    SIGNED,
    SUBMITTED,
)


MODULE = (
    "src.execution."
    "signed_transaction_receipt"
)


class FakeReceiptRpc:
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


class SignedTransactionReceiptTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.db_path = Path(
            "/tmp/receipt-test.db"
        )

        self.reservation_id = (
            "receipt-reservation-test"
        )

        self.signer = Keypair()

        self.wallet_pubkey = str(
            self.signer.pubkey()
        )

        blockhash = Hash.new_unique()

        self.blockhash = str(
            blockhash
        )

        self.message = (
            MessageV0.try_compile(
                self.signer.pubkey(),
                [],
                [],
                blockhash,
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
            self.transaction.signatures[0]
        )

        import hashlib

        self.transaction_sha256 = (
            hashlib.sha256(
                self.transaction_bytes
            ).hexdigest()
        )

        self.initial = (
            self.make_reservation()
        )

    def make_reservation(
        self,
        *,
        status=SIGNED,
        signed_transaction_bytes=None,
        signed_transaction_sha256=None,
        wallet_pubkey=None,
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
                if wallet_pubkey is None
                else wallet_pubkey
            ),
            status=status,
            signed_at=10.0,
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
                if signed_transaction_bytes
                is None
                else signed_transaction_bytes
            ),
            recent_blockhash=(
                self.blockhash
            ),
            last_valid_block_height=350,
            blockhash_rpc_slot=200,
        )

    def status_result(
        self,
        state=KNOWN,
        *,
        reasons=(),
        reservation_id=None,
        signature=None,
        transaction_slot=230,
        transaction_error=None,
        last_valid_block_height=350,
        blockhash_rpc_slot=200,
    ):
        return SimpleNamespace(
            state=state,
            reasons=tuple(reasons),
            reservation_id=(
                self.reservation_id
                if reservation_id is None
                else reservation_id
            ),
            transaction_signature=(
                self.signature
                if signature is None
                else signature
            ),
            transaction_slot=(
                transaction_slot
            ),
            transaction_error=(
                transaction_error
            ),
            last_valid_block_height=(
                last_valid_block_height
            ),
            blockhash_rpc_slot=(
                blockhash_rpc_slot
            ),
        )

    def receipt_response(
        self,
        *,
        slot=230,
        block_time=1_800_000_000,
        transaction_error=None,
        fee_lamports=5_000,
        transaction_bytes=None,
        pre_balances=None,
        post_balances=None,
    ):
        if transaction_bytes is None:
            transaction_bytes = (
                self.transaction_bytes
            )

        if pre_balances is None:
            pre_balances = [
                2_000_000,
                0,
            ]

        if post_balances is None:
            post_balances = [
                1_500_000,
                0,
            ]

        return {
            "slot": slot,
            "blockTime": block_time,
            "transaction": [
                base64.b64encode(
                    transaction_bytes
                ).decode("ascii"),
                "base64",
            ],
            "meta": {
                "err": transaction_error,
                "fee": fee_lamports,
                "preBalances": pre_balances,
                "postBalances": post_balances,
            },
            "version": 0,
        }

    async def run_receipt(
        self,
        *,
        snapshots=None,
        observation=None,
        receipt_response=None,
    ):
        if snapshots is None:
            snapshots = [
                self.initial,
                self.initial,
                self.initial,
            ]

        if observation is None:
            observation = (
                self.status_result()
            )

        if receipt_response is None:
            receipt_response = (
                self.receipt_response()
            )

        load_mock = MagicMock(
            side_effect=snapshots
        )

        status_mock = AsyncMock(
            return_value=observation
        )

        rpc = FakeReceiptRpc(
            response=receipt_response
        )

        with (
            patch(
                f"{MODULE}."
                "load_capital_reservation_read_only",
                load_mock,
            ),
            patch(
                f"{MODULE}."
                "resolve_signed_transaction_status",
                status_mock,
            ),
            patch(
                f"{MODULE}.HeliusRpcClient",
                return_value=rpc,
            ),
        ):
            result = await (
                resolve_signed_transaction_receipt(
                    reservation_id=(
                        self.reservation_id
                    ),
                    db_path=self.db_path,
                )
            )

        return (
            result,
            load_mock,
            status_mock,
            rpc,
        )

    async def test_success_receipt_resolves_exact_transaction(
        self,
    ):
        (
            result,
            load_mock,
            status_mock,
            rpc,
        ) = await self.run_receipt()

        self.assertEqual(
            result.resolver_version,
            SIGNED_TRANSACTION_RECEIPT_VERSION,
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            result.receipt_slot,
            230,
        )

        self.assertIsNone(
            result.transaction_error
        )

        self.assertEqual(
            result.fee_lamports,
            5_000,
        )

        self.assertEqual(
            result.fee_payer_pubkey,
            self.wallet_pubkey,
        )

        self.assertEqual(
            result.fee_payer_pre_balance_lamports,
            2_000_000,
        )

        self.assertEqual(
            result.fee_payer_post_balance_lamports,
            1_500_000,
        )

        self.assertEqual(
            result.fee_payer_balance_delta_lamports,
            -500_000,
        )

        self.assertEqual(
            result.persisted_transaction_sha256,
            self.transaction_sha256,
        )

        self.assertEqual(
            result.receipt_transaction_sha256,
            self.transaction_sha256,
        )

        self.assertEqual(
            load_mock.call_count,
            3,
        )

        status_mock.assert_awaited_once()

        self.assertEqual(
            rpc.calls,
            [
                (
                    "getTransaction",
                    [
                        self.signature,
                        {
                            "encoding": "base64",
                            "commitment": "confirmed",
                            "maxSupportedTransactionVersion": 0,
                        },
                    ],
                ),
            ],
        )

    async def test_failed_receipt_preserves_error_fee_and_balance_delta(
        self,
    ):
        error = {
            "InstructionError": [
                2,
                "Custom",
            ],
        }

        observation = self.status_result(
            transaction_error=error,
        )

        receipt = self.receipt_response(
            transaction_error=error,
            fee_lamports=9_000,
            pre_balances=[
                2_000_000,
            ],
            post_balances=[
                1_991_000,
            ],
        )

        result, _, _, _ = (
            await self.run_receipt(
                observation=observation,
                receipt_response=receipt,
            )
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            result.transaction_error,
            error,
        )

        self.assertEqual(
            result.fee_lamports,
            9_000,
        )

        self.assertEqual(
            result.fee_payer_balance_delta_lamports,
            -9_000,
        )

    async def test_submitted_reservation_is_receipt_resolvable(
        self,
    ):
        submitted = self.make_reservation(
            status=SUBMITTED,
        )

        result, _, _, _ = (
            await self.run_receipt(
                snapshots=[
                    submitted,
                    submitted,
                    submitted,
                ],
            )
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

    async def test_absent_still_valid_never_fetches_receipt(
        self,
    ):
        observation = self.status_result(
            state=ABSENT_STILL_VALID,
        )

        (
            result,
            load_mock,
            status_mock,
            rpc,
        ) = await self.run_receipt(
            snapshots=[
                self.initial,
            ],
            observation=observation,
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "TRANSACTION_NOT_KNOWN",
            result.reasons,
        )

        self.assertEqual(
            load_mock.call_count,
            1,
        )

        status_mock.assert_awaited_once()

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_unknown_status_never_fetches_receipt(
        self,
    ):
        observation = self.status_result(
            state=STATUS_UNKNOWN,
            reasons=(
                "RPC_UNCERTAIN",
            ),
        )

        result, _, _, rpc = (
            await self.run_receipt(
                snapshots=[
                    self.initial,
                ],
                observation=observation,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "STATUS_OBSERVATION_UNKNOWN",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_status_binding_mismatch_never_fetches_receipt(
        self,
    ):
        observation = self.status_result(
            signature=(
                "DifferentSignature"
            ),
        )

        result, _, _, rpc = (
            await self.run_receipt(
                snapshots=[
                    self.initial,
                ],
                observation=observation,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "STATUS_OBSERVATION_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_reservation_change_after_status_blocks_receipt_rpc(
        self,
    ):
        changed = self.make_reservation(
            signed_transaction_sha256=(
                "33" * 32
            ),
        )

        result, load_mock, _, rpc = (
            await self.run_receipt(
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
            "RESERVATION_CHANGED_DURING_STATUS_CHECK",
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

    async def test_known_but_receipt_missing_is_unknown(
        self,
    ):
        #
        # run_receipt(None) substitutes the default
        # fixture, so explicitly patch a null RPC
        # response here.
        #
        load_mock = MagicMock(
            side_effect=[
                self.initial,
                self.initial,
            ]
        )

        status_mock = AsyncMock(
            return_value=self.status_result()
        )

        rpc = FakeReceiptRpc(
            response=None
        )

        with (
            patch(
                f"{MODULE}."
                "load_capital_reservation_read_only",
                load_mock,
            ),
            patch(
                f"{MODULE}."
                "resolve_signed_transaction_status",
                status_mock,
            ),
            patch(
                f"{MODULE}.HeliusRpcClient",
                return_value=rpc,
            ),
        ):
            result = await (
                resolve_signed_transaction_receipt(
                    reservation_id=(
                        self.reservation_id
                    ),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "KNOWN_TRANSACTION_RECEIPT_NOT_FOUND",
            result.reasons,
        )

    async def test_fee_payer_wallet_mismatch_never_fetches_receipt(
        self,
    ):
        mismatched = self.make_reservation(
            wallet_pubkey=(
                "DifferentWallet"
            ),
        )

        (
            result,
            load_mock,
            status_mock,
            rpc,
        ) = await self.run_receipt(
            snapshots=[
                mismatched,
                mismatched,
            ],
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "FEE_PAYER_WALLET_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            load_mock.call_count,
            2,
        )

        status_mock.assert_awaited_once()

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_receipt_transaction_mismatch_is_unknown(
        self,
    ):
        receipt = self.receipt_response(
            transaction_bytes=(
                b"different-transaction"
            ),
        )

        result, _, _, _ = (
            await self.run_receipt(
                snapshots=[
                    self.initial,
                    self.initial,
                ],
                receipt_response=receipt,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "RECEIPT_TRANSACTION_HASH_MISMATCH",
            result.reasons,
        )

    async def test_receipt_slot_mismatch_is_unknown(
        self,
    ):
        receipt = self.receipt_response(
            slot=231,
        )

        result, _, _, _ = (
            await self.run_receipt(
                snapshots=[
                    self.initial,
                    self.initial,
                ],
                receipt_response=receipt,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "RECEIPT_SLOT_MISMATCH",
            result.reasons,
        )

    async def test_receipt_error_mismatch_is_unknown(
        self,
    ):
        receipt = self.receipt_response(
            transaction_error={
                "InstructionError": [
                    1,
                    "Custom",
                ],
            },
        )

        result, _, _, _ = (
            await self.run_receipt(
                snapshots=[
                    self.initial,
                    self.initial,
                ],
                receipt_response=receipt,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "RECEIPT_ERROR_MISMATCH",
            result.reasons,
        )

    async def test_invalid_receipt_fee_is_unknown(
        self,
    ):
        receipt = self.receipt_response(
            fee_lamports=-1,
        )

        result, _, _, _ = (
            await self.run_receipt(
                snapshots=[
                    self.initial,
                    self.initial,
                ],
                receipt_response=receipt,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "RECEIPT_FEE_INVALID",
            result.reasons,
        )

    async def test_reservation_change_during_receipt_check_is_unknown(
        self,
    ):
        changed = self.make_reservation(
            signed_transaction_sha256=(
                "44" * 32
            ),
        )

        result, load_mock, _, rpc = (
            await self.run_receipt(
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
            "RESERVATION_CHANGED_DURING_RECEIPT_CHECK",
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
