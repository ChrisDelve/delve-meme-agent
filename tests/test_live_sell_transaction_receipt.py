import base64
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from src.execution.live_sell_transaction_receipt import (
    BLOCK,
    LIVE_SELL_TRANSACTION_RECEIPT_VERSION,
    RESOLVED,
    UNKNOWN,
    resolve_live_sell_transaction_receipt,
)
from src.execution.live_sell_transaction_status import (
    ABSENT_STILL_VALID,
    KNOWN,
    UNKNOWN as STATUS_UNKNOWN,
    LIVE_SELL_TRANSACTION_STATUS_RESOLVER_VERSION,
)
from src.portfolio.live_sell_execution_records import (
    PASS as EXECUTION_PASS,
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED,
    load_live_sell_execution_record_read_only,
)
from src.safety.token_safety_resolver import (
    COMMITMENT,
)
from tests import (
    test_live_sell_submission_boundary
    as submission_boundary_tests,
)


MODULE = (
    "src.execution."
    "live_sell_transaction_receipt"
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


class LiveSellTransactionReceiptTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        helper = (
            submission_boundary_tests
            .LiveSellSubmissionBoundaryTests(
                methodName=(
                    "test_signed_to_"
                    "submission_armed_is_durable"
                )
            )
        )

        helper.setUp()

        self.helper = helper
        self.db_path = helper.db_path

        self.authorization_sha256 = (
            helper.authorization
            .authorization_sha256
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

        self.assertIsNotNone(
            loaded.record
        )

        self.initial = (
            loaded.record
        )

        self.signature = (
            self.initial
            .transaction_signature
        )

        self.transaction_bytes = (
            self.initial
            .signed_transaction_bytes
        )

        self.transaction_sha256 = (
            self.initial
            .signed_transaction_sha256
        )

        self.wallet_pubkey = (
            self.initial
            .wallet_pubkey
        )

        self.last_valid_block_height = (
            self.initial
            .last_valid_block_height
        )

        self.blockhash_rpc_slot = (
            self.initial
            .blockhash_rpc_slot
        )

    def tearDown(self):
        self.helper.tearDown()

    def loader_result(
        self,
        record,
        *,
        status=EXECUTION_PASS,
        reasons=(),
    ):
        return SimpleNamespace(
            status=status,
            reasons=tuple(
                reasons
            ),
            record=record,
        )

    def status_result(
        self,
        *,
        state=KNOWN,
        reasons=(),
        resolver_version=(
            LIVE_SELL_TRANSACTION_STATUS_RESOLVER_VERSION
        ),
        authorization_sha256=None,
        signature=None,
        execution_status=None,
        transaction_slot=230,
        transaction_error=None,
        last_valid_block_height=None,
        blockhash_rpc_slot=None,
    ):
        if authorization_sha256 is None:
            authorization_sha256 = (
                self.authorization_sha256
            )

        if signature is None:
            signature = self.signature

        if execution_status is None:
            execution_status = (
                self.initial.status
            )

        if last_valid_block_height is None:
            last_valid_block_height = (
                self.last_valid_block_height
            )

        if blockhash_rpc_slot is None:
            blockhash_rpc_slot = (
                self.blockhash_rpc_slot
            )

        return SimpleNamespace(
            resolver_version=(
                resolver_version
            ),
            state=state,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            transaction_signature=(
                signature
            ),
            execution_status=(
                execution_status
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
            ]

        if post_balances is None:
            post_balances = [
                2_500_000,
            ]

        return {
            "slot": slot,
            "blockTime": block_time,
            "transaction": [
                base64.b64encode(
                    transaction_bytes
                ).decode(
                    "ascii"
                ),
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
                self.loader_result(
                    self.initial
                ),
                self.loader_result(
                    self.initial
                ),
                self.loader_result(
                    self.initial
                ),
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
                "load_live_sell_execution_record_read_only",
                load_mock,
            ),
            patch(
                f"{MODULE}."
                "resolve_live_sell_transaction_status",
                status_mock,
            ),
            patch(
                f"{MODULE}.HeliusRpcClient",
                return_value=rpc,
            ),
        ):
            result = await (
                resolve_live_sell_transaction_receipt(
                    authorization_sha256=(
                        self.authorization_sha256
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

    async def test_signed_known_external_relay_receipt_resolves(
        self,
    ):
        result, load_mock, status_mock, rpc = (
            await self.run_receipt()
        )

        self.assertEqual(
            result.resolver_version,
            LIVE_SELL_TRANSACTION_RECEIPT_VERSION,
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            result.execution_status,
            SIGNED,
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
            2_500_000,
        )

        self.assertEqual(
            result.fee_payer_balance_delta_lamports,
            500_000,
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
                            "commitment": COMMITMENT,
                            "maxSupportedTransactionVersion": 0,
                        },
                    ],
                )
            ],
        )

    async def test_failed_receipt_preserves_error_fee_and_balance_delta(
        self,
    ):
        error = {
            "InstructionError": [
                2,
                "Custom",
            ]
        }

        observation = (
            self.status_result(
                transaction_error=error
            )
        )

        receipt = (
            self.receipt_response(
                transaction_error=error,
                fee_lamports=9_000,
                pre_balances=[
                    2_000_000,
                ],
                post_balances=[
                    1_991_000,
                ],
            )
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

    async def test_submission_armed_is_receipt_resolvable(
        self,
    ):
        armed = replace(
            self.initial,
            status=SUBMISSION_ARMED,
            submission_started_at=20.0,
            submission_attempt_count=1,
            submitted_at=None,
            updated_at=20.0,
        )

        result, _, _, _ = (
            await self.run_receipt(
                snapshots=[
                    self.loader_result(
                        armed
                    ),
                    self.loader_result(
                        armed
                    ),
                    self.loader_result(
                        armed
                    ),
                ],
                observation=(
                    self.status_result(
                        execution_status=(
                            SUBMISSION_ARMED
                        )
                    )
                ),
            )
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            result.execution_status,
            SUBMISSION_ARMED,
        )

    async def test_submitted_is_receipt_resolvable(
        self,
    ):
        submitted = replace(
            self.initial,
            status=SUBMITTED,
            submission_started_at=20.0,
            submission_attempt_count=1,
            submitted_at=30.0,
            updated_at=30.0,
        )

        result, _, _, _ = (
            await self.run_receipt(
                snapshots=[
                    self.loader_result(
                        submitted
                    ),
                    self.loader_result(
                        submitted
                    ),
                    self.loader_result(
                        submitted
                    ),
                ],
                observation=(
                    self.status_result(
                        execution_status=(
                            SUBMITTED
                        )
                    )
                ),
            )
        )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            result.execution_status,
            SUBMITTED,
        )

    async def test_absent_still_valid_never_fetches_receipt(
        self,
    ):
        result, load_mock, status_mock, rpc = (
            await self.run_receipt(
                snapshots=[
                    self.loader_result(
                        self.initial
                    ),
                ],
                observation=(
                    self.status_result(
                        state=(
                            ABSENT_STILL_VALID
                        )
                    )
                ),
            )
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
        result, _, _, rpc = (
            await self.run_receipt(
                snapshots=[
                    self.loader_result(
                        self.initial
                    ),
                ],
                observation=(
                    self.status_result(
                        state=STATUS_UNKNOWN,
                        reasons=(
                            "RPC_UNCERTAIN",
                        ),
                        transaction_slot=None,
                    )
                ),
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

        self.assertIn(
            "RPC_UNCERTAIN",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_status_version_mismatch_never_fetches_receipt(
        self,
    ):
        result, _, _, rpc = (
            await self.run_receipt(
                snapshots=[
                    self.loader_result(
                        self.initial
                    ),
                ],
                observation=(
                    self.status_result(
                        resolver_version=(
                            "wrong-status-version"
                        )
                    )
                ),
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "STATUS_RESOLVER_VERSION_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_status_binding_mismatch_never_fetches_receipt(
        self,
    ):
        result, _, _, rpc = (
            await self.run_receipt(
                snapshots=[
                    self.loader_result(
                        self.initial
                    ),
                ],
                observation=(
                    self.status_result(
                        signature=(
                            "DifferentSignature"
                        )
                    )
                ),
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

    async def test_artifact_change_after_status_blocks_receipt_rpc(
        self,
    ):
        changed = replace(
            self.initial,
            signed_at=(
                self.initial.signed_at
                + 1.0
            ),
        )

        result, load_mock, _, rpc = (
            await self.run_receipt(
                snapshots=[
                    self.loader_result(
                        self.initial
                    ),
                    self.loader_result(
                        changed
                    ),
                ],
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_EXECUTION_CHANGED_DURING_STATUS_CHECK",
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
        load_mock = MagicMock(
            side_effect=[
                self.loader_result(
                    self.initial
                ),
                self.loader_result(
                    self.initial
                ),
            ]
        )

        status_mock = AsyncMock(
            return_value=(
                self.status_result()
            )
        )

        rpc = FakeReceiptRpc(
            response=None
        )

        with (
            patch(
                f"{MODULE}."
                "load_live_sell_execution_record_read_only",
                load_mock,
            ),
            patch(
                f"{MODULE}."
                "resolve_live_sell_transaction_status",
                status_mock,
            ),
            patch(
                f"{MODULE}.HeliusRpcClient",
                return_value=rpc,
            ),
        ):
            result = await (
                resolve_live_sell_transaction_receipt(
                    authorization_sha256=(
                        self.authorization_sha256
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
        mismatched = replace(
            self.initial,
            wallet_pubkey=(
                "DifferentWallet"
            ),
        )

        result, _, _, rpc = (
            await self.run_receipt(
                snapshots=[
                    self.loader_result(
                        mismatched
                    ),
                    self.loader_result(
                        mismatched
                    ),
                ],
            )
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
            rpc.calls,
            [],
        )

    async def test_receipt_transaction_mismatch_is_unknown(
        self,
    ):
        result, _, _, _ = (
            await self.run_receipt(
                receipt_response=(
                    self.receipt_response(
                        transaction_bytes=(
                            b"different-transaction"
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
            "RECEIPT_TRANSACTION_HASH_MISMATCH",
            result.reasons,
        )

    async def test_receipt_slot_mismatch_is_unknown(
        self,
    ):
        result, _, _, _ = (
            await self.run_receipt(
                receipt_response=(
                    self.receipt_response(
                        slot=231
                    )
                )
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
        result, _, _, _ = (
            await self.run_receipt(
                receipt_response=(
                    self.receipt_response(
                        transaction_error={
                            "InstructionError": [
                                1,
                                "Custom",
                            ]
                        }
                    )
                )
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
        result, _, _, _ = (
            await self.run_receipt(
                receipt_response=(
                    self.receipt_response(
                        fee_lamports=True
                    )
                )
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

    async def test_artifact_change_during_receipt_check_is_unknown(
        self,
    ):
        changed = replace(
            self.initial,
            signed_at=(
                self.initial.signed_at
                + 1.0
            ),
        )

        result, load_mock, _, rpc = (
            await self.run_receipt(
                snapshots=[
                    self.loader_result(
                        self.initial
                    ),
                    self.loader_result(
                        self.initial
                    ),
                    self.loader_result(
                        changed
                    ),
                ],
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_EXECUTION_CHANGED_DURING_RECEIPT_CHECK",
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

    async def test_receipt_resolution_does_not_mutate_claim_or_positions(
        self,
    ):
        claim_before = (
            self.helper.claim_snapshot()
        )

        positions_before = (
            self.helper.position_snapshot()
        )

        observation = (
            self.status_result()
        )

        rpc = FakeReceiptRpc(
            response=(
                self.receipt_response()
            )
        )

        with (
            patch(
                f"{MODULE}."
                "resolve_live_sell_transaction_status",
                AsyncMock(
                    return_value=(
                        observation
                    )
                ),
            ),
            patch(
                f"{MODULE}.HeliusRpcClient",
                return_value=rpc,
            ),
        ):
            result = await (
                resolve_live_sell_transaction_receipt(
                    authorization_sha256=(
                        self.authorization_sha256
                    ),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            RESOLVED,
        )

        self.assertEqual(
            self.helper.claim_snapshot(),
            claim_before,
        )

        self.assertEqual(
            self.helper.position_snapshot(),
            positions_before,
        )


if __name__ == "__main__":
    unittest.main()
