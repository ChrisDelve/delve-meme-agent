from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from solders.hash import Hash
from solders.keypair import Keypair

from src.execution.signed_transaction_status import (
    ABSENT_EXPIRED,
    ABSENT_STILL_VALID,
    HISTORY,
    KNOWN,
    RECENT,
    UNKNOWN,
    SIGNED_TRANSACTION_STATUS_RESOLVER_VERSION,
    resolve_signed_transaction_status,
)
from src.portfolio.live_reservations import (
    RESERVATION_VERSION,
    SIGNED,
    SUBMITTED,
    LiveCapitalReservation,
)


MODULE = (
    "src.execution.signed_transaction_status"
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


class SignedTransactionStatusTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()

        self.db_path = (
            Path(self.temp_dir.name)
            / "live.db"
        )

        self.transaction_bytes = (
            b"durably-signed-transaction"
        )

        self.signature = str(
            Keypair().sign_message(
                b"status-resolver-test"
            )
        )

        self.blockhash = str(
            Hash.new_unique()
        )

        self.reservation = (
            LiveCapitalReservation(
                reservation_id=(
                    "status-reservation-test"
                ),
                reservation_version=(
                    RESERVATION_VERSION
                ),
                mint="MintStatus",
                side="BUY",
                wallet_pubkey="WalletStatus",
                spend_lamports=1_000_000,
                wallet_cost_lamports=(
                    1_100_000
                ),
                status=SIGNED,
                risk_governor_version=(
                    "risk-test"
                ),
                risk_simulation_sha256=(
                    "22" * 32
                ),
                base_available_cash_lamports=(
                    10_000_000
                ),
                base_open_exposure_lamports=0,
                base_open_positions=0,
                reserved_exposure_before_lamports=0,
                reserved_cash_before_lamports=0,
                active_reservations_before=0,
                created_at=1.0,
                expires_at=2.0,
                signed_at=3.0,
                transaction_signature=(
                    self.signature
                ),
                signed_message_sha256=(
                    "11" * 32
                ),
                signed_transaction_sha256=(
                    sha256(
                        self.transaction_bytes
                    ).hexdigest()
                ),
                signed_transaction_bytes=(
                    self.transaction_bytes
                ),
                recent_blockhash=(
                    self.blockhash
                ),
                last_valid_block_height=350,
                blockhash_rpc_slot=200,
            )
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def status_response(
        self,
        status,
        *,
        context_slot=250,
    ):
        return {
            "context": {
                "slot": context_slot,
            },
            "value": [
                status,
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
        reservation=None,
        rpc=None,
    ):
        if reservation is None:
            reservation = (
                self.reservation
            )

        if rpc is None:
            rpc = FakeStatusRpc(
                recent_response=(
                    self.status_response(
                        self.known_status()
                    )
                ),
            )

        with (
            patch(
                f"{MODULE}."
                "load_capital_reservation_read_only",
                return_value=reservation,
            ) as load_mock,
            patch(
                f"{MODULE}.HeliusRpcClient",
                return_value=rpc,
            ),
        ):
            result = await (
                resolve_signed_transaction_status(
                    reservation_id=(
                        reservation
                        .reservation_id
                    ),
                    db_path=self.db_path,
                )
            )

        return (
            result,
            rpc,
            load_mock,
        )

    async def test_submitted_reservation_remains_status_resolvable(
        self,
    ):
        reservation = replace(
            self.reservation,
            status=SUBMITTED,
            submission_started_at=3.5,
            submission_attempt_count=1,
            submitted_at=4.0,
        )

        rpc = FakeStatusRpc(
            recent_response=(
                self.status_response(
                    self.known_status()
                )
            ),
        )

        result, rpc, _ = await self.resolve(
            reservation=reservation,
            rpc=rpc,
        )

        self.assertEqual(
            result.state,
            KNOWN,
        )

        self.assertEqual(
            result.status_source,
            RECENT,
        )

        self.assertEqual(
            [call[0] for call in rpc.calls],
            [
                "getSignatureStatuses",
            ],
        )

    async def test_inconsistent_submitted_metadata_stops_before_rpc(
        self,
    ):
        reservation = replace(
            self.reservation,
            status=SUBMITTED,
            submission_started_at=None,
            submission_attempt_count=0,
            submitted_at=None,
        )

        rpc = FakeStatusRpc(
            recent_response=None,
        )

        result, rpc, _ = await self.resolve(
            reservation=reservation,
            rpc=rpc,
        )

        self.assertEqual(
            result.state,
            UNKNOWN,
        )

        self.assertIn(
            "SUBMISSION_METADATA_INCONSISTENT",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
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

        result, rpc, _ = await self.resolve(
            rpc=rpc
        )

        self.assertEqual(
            result.resolver_version,
            SIGNED_TRANSACTION_STATUS_RESOLVER_VERSION,
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
            [call[0] for call in rpc.calls],
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

        result, _, _ = await self.resolve(
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

    async def test_absent_still_valid_uses_height_without_history(
        self,
    ):
        rpc = FakeStatusRpc(
            recent_response=(
                self.status_response(
                    None
                )
            ),
            block_height=350,
        )

        result, rpc, _ = await self.resolve(
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
            [call[0] for call in rpc.calls],
            [
                "getSignatureStatuses",
                "getBlockHeight",
            ],
        )

        height_params = (
            rpc.calls[1][1]
        )

        self.assertEqual(
            height_params[0][
                "minContextSlot"
            ],
            200,
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
            block_height=351,
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

        result, rpc, _ = await self.resolve(
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
            block_height=351,
            history_response=(
                self.status_response(
                    None
                )
            ),
        )

        result, _, _ = await self.resolve(
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
            351,
        )

    async def test_stale_recent_context_is_unknown(
        self,
    ):
        rpc = FakeStatusRpc(
            recent_response=(
                self.status_response(
                    None,
                    context_slot=199,
                )
            ),
        )

        result, rpc, _ = await self.resolve(
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

        result, _, _ = await self.resolve(
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

    async def test_incomplete_signed_reservation_stops_before_rpc(
        self,
    ):
        reservation = replace(
            self.reservation,
            recent_blockhash=None,
        )

        rpc = FakeStatusRpc(
            recent_response=None,
        )

        result, rpc, _ = await self.resolve(
            reservation=reservation,
            rpc=rpc,
        )

        self.assertEqual(
            result.state,
            UNKNOWN,
        )

        self.assertIn(
            "INVALID_RECENT_BLOCKHASH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )

    async def test_transaction_hash_mismatch_stops_before_rpc(
        self,
    ):
        reservation = replace(
            self.reservation,
            signed_transaction_sha256=(
                "33" * 32
            ),
        )

        rpc = FakeStatusRpc(
            recent_response=None,
        )

        result, rpc, _ = await self.resolve(
            reservation=reservation,
            rpc=rpc,
        )

        self.assertEqual(
            result.state,
            UNKNOWN,
        )

        self.assertIn(
            "SIGNED_TRANSACTION_HASH_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            rpc.calls,
            [],
        )
