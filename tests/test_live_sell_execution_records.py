import hashlib
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from src.portfolio.live_sell_claims import (
    ACTIVE as CLAIM_ACTIVE,
    BLOCK as CLAIM_BLOCK,
    PASS as CLAIM_PASS,
    RELEASED,
    acquire_live_sell_inventory_claim,
    load_active_live_sell_inventory_claim_read_only,
)
from src.portfolio.live_sell_execution_records import (
    BLOCK,
    LIVE_SELL_EXECUTION_RECORD_VERSION,
    PASS,
    SIGNED,
    UNKNOWN,
    bind_live_sell_signed_artifact,
    load_live_sell_execution_record_read_only,
)
from src.safety.token_safety_resolver import (
    TOKEN_2022_PROGRAM,
    derive_associated_token_account,
)
from tests import (
    test_live_sell_claims as claim_tests,
)
from tests import (
    test_pump_sell_v2_unsigned_message
    as unsigned_message_tests,
)


class LiveSellExecutionRecordTests(
    unittest.TestCase
):
    def setUp(self):
        self.keypair = Keypair()
        self.other_keypair = Keypair()

        claim_helper = (
            claim_tests
            .LiveSellInventoryClaimTests(
                methodName=(
                    "test_claim_persists_exact_fifo_"
                    "allocation_without_mutating_positions"
                )
            )
        )

        claim_helper.setUp()

        self.claim_helper = claim_helper
        self.db_path = claim_helper.db_path

        #
        # The existing claim fixture intentionally uses
        # Pubkey.new_unique(). For execution/signature
        # tests we need a wallet whose private signing
        # material is actually available to the test.
        #
        claim_helper.wallet = str(
            self.keypair.pubkey()
        )

        claim_helper.associated_base_user = str(
            derive_associated_token_account(
                owner=self.keypair.pubkey(),
                mint=Pubkey.from_string(
                    claim_helper.mint
                ),
                token_program=(
                    TOKEN_2022_PROGRAM
                ),
            )
        )

        #
        # The fixture already seeded position IDs 1/2
        # for its original random wallet. Use distinct
        # IDs for the keypair-controlled wallet.
        #
        claim_helper.positions = (
            claim_helper.position(
                position_id=101,
                entry_slot=100,
                tokens=300,
                exposure=600,
                cost_basis=300,
            ),
            claim_helper.position(
                position_id=102,
                entry_slot=200,
                tokens=400,
                exposure=800,
                cost_basis=400,
            ),
        )

        claim_helper.seed_positions(
            claim_helper.positions
        )

        self.authorization = (
            claim_helper.authorization()
        )

        claim_result = (
            acquire_live_sell_inventory_claim(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            claim_result.status,
            CLAIM_PASS,
        )

        unsigned_helper = (
            unsigned_message_tests
            .PumpSellV2UnsignedMessageTests(
                methodName=(
                    "test_builds_unsigned_message_v0"
                )
            )
        )

        unsigned_helper.setUp()

        self.unsigned_helper = (
            unsigned_helper
        )

        self.context = (
            unsigned_helper.make_context(
                self.authorization,
                global_rpc_slot=501,
            )
        )

        self.blockhash_context = (
            unsigned_helper
            .make_blockhash_context(
                min_context_slot=501,
                rpc_slot=502,
            )
        )

        self.message_plan = (
            unsigned_helper.build(
                authorization=(
                    self.authorization
                ),
                context=self.context,
                blockhash_context=(
                    self.blockhash_context
                ),
            )
        )

        (
            self.transaction_signature,
            self.signed_transaction_bytes,
        ) = self.sign_plan(
            self.message_plan,
            self.keypair,
        )

    def tearDown(self):
        self.claim_helper.tearDown()

    def sign_plan(
        self,
        message_plan,
        keypair,
    ):
        message_bytes = (
            to_bytes_versioned(
                message_plan.message
            )
        )

        signature = (
            keypair.sign_message(
                message_bytes
            )
        )

        transaction = (
            VersionedTransaction.populate(
                message_plan.message,
                [
                    signature
                ],
            )
        )

        return (
            str(signature),
            bytes(transaction),
        )

    def bind(
        self,
        *,
        authorization=None,
        message_plan=None,
        transaction_signature=None,
        signed_transaction_bytes=None,
    ):
        return (
            bind_live_sell_signed_artifact(
                authorization=(
                    self.authorization
                    if authorization is None
                    else authorization
                ),
                message_plan=(
                    self.message_plan
                    if message_plan is None
                    else message_plan
                ),
                transaction_signature=(
                    self.transaction_signature
                    if transaction_signature
                    is None
                    else transaction_signature
                ),
                signed_transaction_bytes=(
                    self.signed_transaction_bytes
                    if signed_transaction_bytes
                    is None
                    else signed_transaction_bytes
                ),
                db_path=self.db_path,
            )
        )

    def execution_row_count(self):
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            try:
                row = connection.execute(
                    """
                    SELECT COUNT(*)

                    FROM live_sell_execution_records
                    """
                ).fetchone()

            except sqlite3.OperationalError:
                return 0

            return int(
                row[0]
            )

        finally:
            connection.close()

    def snapshot_claim_and_positions(self):
        connection = sqlite3.connect(
            self.db_path
        )

        connection.row_factory = (
            sqlite3.Row
        )

        try:
            claim = connection.execute(
                """
                SELECT *

                FROM live_sell_inventory_claims

                WHERE authorization_sha256 = ?
                """,
                (
                    self.authorization
                    .authorization_sha256,
                ),
            ).fetchone()

            positions = (
                connection.execute(
                    """
                    SELECT *

                    FROM live_positions

                    WHERE wallet_pubkey = ?

                    ORDER BY position_id ASC
                    """,
                    (
                        self.authorization
                        .wallet_pubkey,
                    ),
                ).fetchall()
            )

            return (
                None
                if claim is None
                else tuple(claim),
                tuple(
                    tuple(row)
                    for row in positions
                ),
            )

        finally:
            connection.close()

    def test_valid_signed_artifact_is_durable(
        self,
    ):
        result = self.bind()

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertTrue(
            result.changed
        )

        self.assertIsNotNone(
            result.record
        )

        record = result.record

        self.assertEqual(
            record.record_version,
            LIVE_SELL_EXECUTION_RECORD_VERSION,
        )

        self.assertEqual(
            record.status,
            SIGNED,
        )

        self.assertEqual(
            record.authorization_sha256,
            self.authorization
            .authorization_sha256,
        )

        self.assertEqual(
            record.wallet_pubkey,
            self.authorization.wallet_pubkey,
        )

        self.assertEqual(
            record.mint,
            self.authorization.mint,
        )

        self.assertEqual(
            record.tokens_to_sell,
            self.authorization.tokens_to_sell,
        )

        self.assertEqual(
            record.message_sha256,
            self.message_plan.message_sha256,
        )

        self.assertEqual(
            record.transaction_signature,
            self.transaction_signature,
        )

        self.assertEqual(
            record.signed_transaction_bytes,
            self.signed_transaction_bytes,
        )

        self.assertEqual(
            record.signed_transaction_sha256,
            hashlib.sha256(
                self.signed_transaction_bytes
            ).hexdigest(),
        )

        self.assertEqual(
            self.execution_row_count(),
            1,
        )

    def test_exact_retry_is_idempotent(
        self,
    ):
        first = self.bind()
        second = self.bind()

        self.assertEqual(
            first.status,
            PASS,
        )

        self.assertTrue(
            first.changed
        )

        self.assertEqual(
            second.status,
            PASS,
        )

        self.assertFalse(
            second.changed
        )

        self.assertEqual(
            second.record,
            first.record,
        )

        self.assertEqual(
            self.execution_row_count(),
            1,
        )

    def test_read_only_loader_reparses_and_verifies_artifact(
        self,
    ):
        bound = self.bind()

        self.assertEqual(
            bound.status,
            PASS,
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
            PASS,
        )

        self.assertIsNotNone(
            loaded.record
        )

        self.assertEqual(
            loaded.record,
            bound.record,
        )

        parsed = (
            VersionedTransaction.from_bytes(
                loaded.record
                .signed_transaction_bytes
            )
        )

        self.assertEqual(
            bytes(parsed),
            self.signed_transaction_bytes,
        )

        self.assertEqual(
            parsed.verify_with_results(),
            [True],
        )

    def test_read_only_loader_missing_table_is_unknown_without_creation(
        self,
    ):
        result = (
            load_live_sell_execution_record_read_only(
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_SELL_EXECUTION_TABLE_NOT_FOUND",
            result.reasons,
        )

        connection = sqlite3.connect(
            self.db_path
        )

        try:
            row = connection.execute(
                """
                SELECT name

                FROM sqlite_master

                WHERE type = 'table'
                  AND name = 'live_sell_execution_records'
                """
            ).fetchone()

        finally:
            connection.close()

        self.assertIsNone(
            row
        )

    def test_missing_active_claim_blocks_without_execution_schema_creation(
        self,
    ):
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            connection.execute(
                """
                DELETE FROM
                    live_sell_inventory_claim_lots

                WHERE authorization_sha256 = ?
                """,
                (
                    self.authorization
                    .authorization_sha256,
                ),
            )

            connection.execute(
                """
                DELETE FROM
                    live_sell_inventory_claims

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

        result = self.bind()

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "ACTIVE_SELL_CLAIM_VALIDATION_FAILED",
            result.reasons,
        )

        self.assertIn(
            "ACTIVE_SELL_CLAIM_NOT_FOUND",
            result.reasons,
        )

        self.assertEqual(
            self.execution_row_count(),
            0,
        )

        connection = sqlite3.connect(
            self.db_path
        )

        try:
            row = connection.execute(
                """
                SELECT name

                FROM sqlite_master

                WHERE type = 'table'
                  AND name = 'live_sell_execution_records'
                """
            ).fetchone()

        finally:
            connection.close()

        self.assertIsNone(
            row
        )

    def test_terminal_claim_blocks_signed_artifact(
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

        result = self.bind()

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SELL_CLAIM_NOT_ACTIVE",
            result.reasons,
        )

        self.assertEqual(
            self.execution_row_count(),
            0,
        )

    def test_message_authorization_binding_mismatch_blocks(
        self,
    ):
        altered_plan = replace(
            self.message_plan,
            authorization_sha256=(
                "00" * 32
            ),
        )

        result = self.bind(
            message_plan=altered_plan,
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "MESSAGE_AUTHORIZATION_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            self.execution_row_count(),
            0,
        )

    def test_message_payer_binding_mismatch_blocks(
        self,
    ):
        altered_plan = replace(
            self.message_plan,
            payer=str(
                self.other_keypair.pubkey()
            ),
        )

        result = self.bind(
            message_plan=altered_plan,
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "MESSAGE_PAYER_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            self.execution_row_count(),
            0,
        )

    def test_message_blockhash_metadata_mismatch_blocks(
        self,
    ):
        altered_plan = replace(
            self.message_plan,
            recent_blockhash=str(
                Hash.new_unique()
            ),
        )

        result = self.bind(
            message_plan=altered_plan,
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "MESSAGE_BLOCKHASH_BINDING_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            self.execution_row_count(),
            0,
        )

    def test_default_signature_blocks(
        self,
    ):
        default_signature = (
            Signature.default()
        )

        transaction = (
            VersionedTransaction.populate(
                self.message_plan.message,
                [
                    default_signature
                ],
            )
        )

        result = self.bind(
            transaction_signature=str(
                default_signature
            ),
            signed_transaction_bytes=(
                bytes(transaction)
            ),
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
            self.execution_row_count(),
            0,
        )

    def test_signature_from_wrong_key_blocks(
        self,
    ):
        (
            wrong_signature,
            wrong_transaction_bytes,
        ) = self.sign_plan(
            self.message_plan,
            self.other_keypair,
        )

        result = self.bind(
            transaction_signature=(
                wrong_signature
            ),
            signed_transaction_bytes=(
                wrong_transaction_bytes
            ),
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
            self.execution_row_count(),
            0,
        )

    def test_same_wallet_different_valid_message_blocks(
        self,
    ):
        alternate_blockhash = replace(
            self.blockhash_context,
            blockhash=str(
                Hash.new_unique()
            ),
            rpc_slot=503,
        )

        alternate_plan = (
            self.unsigned_helper.build(
                authorization=(
                    self.authorization
                ),
                context=self.context,
                blockhash_context=(
                    alternate_blockhash
                ),
            )
        )

        self.assertNotEqual(
            alternate_plan.message_sha256,
            self.message_plan.message_sha256,
        )

        (
            alternate_signature,
            alternate_bytes,
        ) = self.sign_plan(
            alternate_plan,
            self.keypair,
        )

        #
        # Correct wallet, valid signature, valid alternate
        # transaction — but NOT the exact plan supplied
        # to this bind attempt.
        #
        result = self.bind(
            message_plan=self.message_plan,
            transaction_signature=(
                alternate_signature
            ),
            signed_transaction_bytes=(
                alternate_bytes
            ),
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SIGNED_MESSAGE_MUTATED",
            result.reasons,
        )

        self.assertEqual(
            self.execution_row_count(),
            0,
        )

    def test_tampered_wire_signature_blocks(
        self,
    ):
        tampered = bytearray(
            self.signed_transaction_bytes
        )

        #
        # Byte zero is the compact signature-count
        # prefix. Byte one is inside the only 64-byte
        # signature, so this preserves transaction
        # structure while changing the wire signature.
        #
        tampered[1] ^= 1

        result = self.bind(
            signed_transaction_bytes=(
                bytes(tampered)
            ),
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SIGNED_TRANSACTION_SIGNATURE_MISMATCH",
            result.reasons,
        )

        self.assertEqual(
            self.execution_row_count(),
            0,
        )

    def test_corrupt_persisted_hash_is_unknown_on_read(
        self,
    ):
        result = self.bind()

        self.assertEqual(
            result.status,
            PASS,
        )

        connection = sqlite3.connect(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE
                    live_sell_execution_records

                SET
                    signed_transaction_sha256 = ?

                WHERE authorization_sha256 = ?
                """,
                (
                    "00" * 32,
                    self.authorization
                    .authorization_sha256,
                ),
            )

            connection.commit()

        finally:
            connection.close()

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
            UNKNOWN,
        )

        self.assertIn(
            "SELL_EXECUTION_RECORD_EVIDENCE_INVALID",
            loaded.reasons,
        )

    def test_bind_does_not_mutate_claim_or_positions(
        self,
    ):
        before = (
            self.snapshot_claim_and_positions()
        )

        result = self.bind()

        after = (
            self.snapshot_claim_and_positions()
        )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertEqual(
            after,
            before,
        )

        claim_result = (
            load_active_live_sell_inventory_claim_read_only(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            claim_result.status,
            CLAIM_PASS,
        )

        self.assertIsNotNone(
            claim_result.claim
        )

        self.assertEqual(
            claim_result.claim.status,
            CLAIM_ACTIVE,
        )

    def test_concurrent_identical_bind_is_single_durable_record(
        self,
    ):
        with ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            futures = [
                executor.submit(
                    self.bind
                )
                for _ in range(2)
            ]

            results = [
                future.result()
                for future in futures
            ]

        self.assertEqual(
            [result.status for result in results],
            [
                PASS,
                PASS,
            ],
        )

        self.assertEqual(
            sum(
                1
                for result in results
                if result.changed
            ),
            1,
        )

        self.assertEqual(
            self.execution_row_count(),
            1,
        )

        self.assertEqual(
            results[0].record,
            results[1].record,
        )

    def test_concurrent_conflicting_valid_artifacts_cannot_both_bind(
        self,
    ):
        alternate_blockhash = replace(
            self.blockhash_context,
            blockhash=str(
                Hash.new_unique()
            ),
            rpc_slot=503,
        )

        alternate_plan = (
            self.unsigned_helper.build(
                authorization=(
                    self.authorization
                ),
                context=self.context,
                blockhash_context=(
                    alternate_blockhash
                ),
            )
        )

        (
            alternate_signature,
            alternate_bytes,
        ) = self.sign_plan(
            alternate_plan,
            self.keypair,
        )

        def bind_primary():
            return self.bind()

        def bind_alternate():
            return self.bind(
                message_plan=alternate_plan,
                transaction_signature=(
                    alternate_signature
                ),
                signed_transaction_bytes=(
                    alternate_bytes
                ),
            )

        with ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            futures = [
                executor.submit(
                    bind_primary
                ),
                executor.submit(
                    bind_alternate
                ),
            ]

            results = [
                future.result()
                for future in futures
            ]

        statuses = sorted(
            result.status
            for result in results
        )

        self.assertEqual(
            statuses,
            sorted(
                [
                    PASS,
                    UNKNOWN,
                ]
            ),
        )

        failed = next(
            result
            for result in results
            if result.status == UNKNOWN
        )

        self.assertIn(
            "SELL_EXECUTION_RECORD_EVIDENCE_MISMATCH",
            failed.reasons,
        )

        self.assertEqual(
            self.execution_row_count(),
            1,
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
            PASS,
        )
