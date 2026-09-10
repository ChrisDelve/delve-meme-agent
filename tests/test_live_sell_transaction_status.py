import sqlite3
import unittest
from unittest.mock import patch

from src.execution.live_sell_transaction_status import (
    ABSENT_EXPIRED,
    ABSENT_STILL_VALID,
    HISTORY,
    KNOWN,
    RECENT,
    UNKNOWN,
    LIVE_SELL_TRANSACTION_STATUS_RESOLVER_VERSION,
    resolve_live_sell_transaction_status,
)
from src.portfolio.live_sell_execution_records import (
    PASS,
    SUBMISSION_ARMED,
    SUBMITTED,
)
from tests import (
    test_live_sell_submission_boundary
    as submission_boundary_tests,
)


MODULE = (
    "src.execution."
    "live_sell_transaction_status"
)


class FakeStatusRpc:
    def __init__(
        self,
        *,
        recent_response,
        block_height=None,
        history_response=None,
    ):
        self.recent_response = (
            recent_response
        )

        self.block_height = (
            block_height
        )

        self.history_response = (
            history_response
        )

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

        if method == "getBlockHeight":
            return self.block_height

        if method != "getSignatureStatuses":
            raise RuntimeError(
                f"Unexpected method: {method}"
            )

        history = (
            len(params) > 1
            and isinstance(
                params[1],
                dict,
            )
            and params[1].get(
                "searchTransactionHistory"
            )
            is True
        )

        if history:
            return self.history_response

        return self.recent_response


class LiveSellTransactionStatusTests(
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

        #
        # Status observations must be anchored to
        # the exact blockhash provenance carried by
        # this SELL artifact. Do not inherit the
        # arbitrary constants used by the standalone
        # BUY status-resolver tests.
        #
        self.blockhash_rpc_slot = (
            helper.message_plan
            .blockhash_rpc_slot
        )

        self.last_valid_block_height = (
            helper.message_plan
            .last_valid_block_height
        )

    def tearDown(self):
        self.helper.tearDown()

    def status_response(
        self,
        status,
        *,
        context_slot=None,
    ):
        if context_slot is None:
            context_slot = (
                self.blockhash_rpc_slot
            )

        return {
            "context": {
                "slot": context_slot,
            },
            "value": [
                status
            ],
        }

    def known_status(
        self,
        *,
        err=None,
        confirmation_status="confirmed",
    ):
        return {
            "slot": 230,
            "confirmations": 1,
            "err": err,
            "status": {
                "Ok": None,
            },
            "confirmationStatus": (
                confirmation_status
            ),
        }

    async def resolve(
        self,
        *,
        rpc,
    ):
        with patch(
            f"{MODULE}.HeliusRpcClient",
            return_value=rpc,
        ):
            return await (
                resolve_live_sell_transaction_status(
                    authorization_sha256=(
                        self.authorization_sha256
                    ),
                    db_path=self.db_path,
                )
            )

    async def test_known_recent_success_stops_before_height(
        self,
    ):
        rpc = FakeStatusRpc(
            recent_response=(
                self.status_response(
                    self.known_status()
                )
            ),
        )

        result = await self.resolve(
            rpc=rpc
        )

        self.assertEqual(
            result.resolver_version,
            LIVE_SELL_TRANSACTION_STATUS_RESOLVER_VERSION,
        )

        self.assertEqual(
            result.state,
            KNOWN,
        )

        self.assertEqual(
            result.status_source,
            RECENT,
        )

        self.assertIsNone(
            result.transaction_error
        )

        self.assertEqual(
            [
                call[0]
                for call in rpc.calls
            ],
            [
                "getSignatureStatuses",
            ],
        )

    async def test_known_recent_failure_is_still_known(
        self,
    ):
        error = {
            "InstructionError": [
                0,
                "Custom",
            ]
        }

        rpc = FakeStatusRpc(
            recent_response=(
                self.status_response(
                    self.known_status(
                        err=error
                    )
                )
            ),
        )

        result = await self.resolve(
            rpc=rpc
        )

        self.assertEqual(
            result.state,
            KNOWN,
        )

        self.assertEqual(
            result.transaction_error,
            error,
        )

    async def test_absent_still_valid_uses_blockhash_slot(
        self,
    ):
        rpc = FakeStatusRpc(
            recent_response=(
                self.status_response(
                    None
                )
            ),
            block_height=(
                self.last_valid_block_height
            ),
        )

        result = await self.resolve(
            rpc=rpc
        )

        self.assertEqual(
            result.state,
            ABSENT_STILL_VALID,
        )

        self.assertFalse(
            result.history_searched
        )

        self.assertEqual(
            [
                call[0]
                for call in rpc.calls
            ],
            [
                "getSignatureStatuses",
                "getBlockHeight",
            ],
        )

        self.assertEqual(
            rpc.calls[1][1][0][
                "minContextSlot"
            ],
            result.blockhash_rpc_slot,
        )

    async def test_expired_recent_absence_recovers_known_history(
        self,
    ):
        rpc = FakeStatusRpc(
            recent_response=(
                self.status_response(
                    None
                )
            ),
            block_height=(
                self.last_valid_block_height
                + 1
            ),
            history_response=(
                self.status_response(
                    self.known_status(
                        confirmation_status=(
                            "finalized"
                        )
                    )
                )
            ),
        )

        result = await self.resolve(
            rpc=rpc
        )

        self.assertEqual(
            result.state,
            KNOWN,
        )

        self.assertEqual(
            result.status_source,
            HISTORY,
        )

        self.assertTrue(
            result.history_searched
        )

        self.assertEqual(
            len(rpc.calls),
            3,
        )

        self.assertTrue(
            rpc.calls[2][1][1][
                "searchTransactionHistory"
            ]
        )

    async def test_expired_history_absence_is_absent_expired(
        self,
    ):
        rpc = FakeStatusRpc(
            recent_response=(
                self.status_response(
                    None
                )
            ),
            block_height=(
                self.last_valid_block_height
                + 1
            ),
            history_response=(
                self.status_response(
                    None
                )
            ),
        )

        result = await self.resolve(
            rpc=rpc
        )

        self.assertEqual(
            result.state,
            ABSENT_EXPIRED,
        )

        self.assertTrue(
            result.history_searched
        )

        self.assertEqual(
            result.current_block_height,
            (
                self.last_valid_block_height
                + 1
            ),
        )

    async def test_stale_recent_context_is_unknown(
        self,
    ):
        rpc = FakeStatusRpc(
            recent_response=(
                self.status_response(
                    None,
                    context_slot=(
                        self.blockhash_rpc_slot
                        - 1
                    ),
                )
            ),
        )

        result = await self.resolve(
            rpc=rpc
        )

        self.assertEqual(
            result.state,
            UNKNOWN,
        )

        self.assertIn(
            "RECENT_STATUS_RESPONSE_INVALID",
            result.reasons,
        )

        self.assertEqual(
            len(rpc.calls),
            1,
        )

    async def test_malformed_block_height_is_unknown(
        self,
    ):
        rpc = FakeStatusRpc(
            recent_response=(
                self.status_response(
                    None
                )
            ),
            block_height=True,
        )

        result = await self.resolve(
            rpc=rpc
        )

        self.assertEqual(
            result.state,
            UNKNOWN,
        )

        self.assertIn(
            "BLOCK_HEIGHT_RESPONSE_INVALID",
            result.reasons,
        )

    async def test_submission_armed_record_remains_resolvable(
        self,
    ):
        armed = self.helper.arm()

        self.assertEqual(
            armed.status,
            PASS,
        )

        self.assertEqual(
            armed.record.status,
            SUBMISSION_ARMED,
        )

        rpc = FakeStatusRpc(
            recent_response=(
                self.status_response(
                    self.known_status()
                )
            ),
        )

        result = await self.resolve(
            rpc=rpc
        )

        self.assertEqual(
            result.state,
            KNOWN,
        )

        self.assertEqual(
            result.execution_status,
            SUBMISSION_ARMED,
        )

    async def test_submitted_record_remains_resolvable(
        self,
    ):
        armed = self.helper.arm()

        self.assertEqual(
            armed.status,
            PASS,
        )

        submitted = (
            self.helper.acknowledge()
        )

        self.assertEqual(
            submitted.status,
            PASS,
        )

        self.assertEqual(
            submitted.record.status,
            SUBMITTED,
        )

        rpc = FakeStatusRpc(
            recent_response=(
                self.status_response(
                    self.known_status()
                )
            ),
        )

        result = await self.resolve(
            rpc=rpc
        )

        self.assertEqual(
            result.state,
            KNOWN,
        )

        self.assertEqual(
            result.execution_status,
            SUBMITTED,
        )

    async def test_corrupt_persisted_artifact_stops_before_rpc(
        self,
    ):
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            updated = connection.execute(
                """
                UPDATE
                    live_sell_execution_records

                SET
                    signed_transaction_sha256 = ?

                WHERE authorization_sha256 = ?
                """,
                (
                    "00" * 32,
                    self.authorization_sha256,
                ),
            )

            self.assertEqual(
                updated.rowcount,
                1,
            )

            connection.commit()

        finally:
            connection.close()

        rpc = FakeStatusRpc(
            recent_response=None,
        )

        result = await self.resolve(
            rpc=rpc
        )

        self.assertEqual(
            result.state,
            UNKNOWN,
        )

        self.assertIn(
            "SELL_EXECUTION_RECORD_UNAVAILABLE",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_status_resolution_does_not_mutate_claim_or_positions(
        self,
    ):
        claim_before = (
            self.helper.claim_snapshot()
        )

        positions_before = (
            self.helper.position_snapshot()
        )

        rpc = FakeStatusRpc(
            recent_response=(
                self.status_response(
                    None
                )
            ),
            block_height=(
                self.last_valid_block_height
            ),
        )

        result = await self.resolve(
            rpc=rpc
        )

        self.assertEqual(
            result.state,
            ABSENT_STILL_VALID,
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
