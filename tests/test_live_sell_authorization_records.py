from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

from src.execution.exit_execution import (
    EXIT_EXECUTION_CONTRACT_VERSION,
    PUMP_BONDING_CURVE_VENUE,
    ExitExecution,
    PumpBondingCurveExitEvidence,
)
from src.execution.live_pump_sell_authorization import (
    _authorization_sha256,
)
from src.portfolio.live_reservations import (
    get_connection,
)
from src.portfolio.live_sell_authorization_records import (
    PASS,
    UNKNOWN,
    load_live_sell_authorization_record_read_only,
)
from src.portfolio.live_sell_claims import (
    PASS as CLAIM_PASS,
    acquire_live_sell_inventory_claim,
)
from tests import (
    test_live_sell_claims
    as claim_tests,
)


class LiveSellAuthorizationRecordTests(
    unittest.TestCase
):
    def setUp(self):
        self.helper = (
            claim_tests
            .LiveSellInventoryClaimTests(
                methodName=(
                    "test_claim_persists_exact_fifo_allocation_without_mutating_positions"
                )
            )
        )
        self.helper.setUp()

        self.db_path = (
            self.helper.db_path
        )

        self.authorization = (
            self.helper.authorization()
        )

    def tearDown(self):
        self.helper.tearDown()

    def load(self):
        return (
            load_live_sell_authorization_record_read_only(
                authorization_sha256=(
                    self.authorization
                    .authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

    def test_claim_acquisition_persists_exact_recoverable_authorization(
        self,
    ):
        claimed = (
            acquire_live_sell_inventory_claim(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            claimed.status,
            CLAIM_PASS,
        )

        recovered = self.load()

        self.assertEqual(
            recovered.status,
            PASS,
        )
        self.assertIsNotNone(
            recovered.authorization
        )
        self.assertEqual(
            recovered.authorization,
            self.authorization,
        )
        self.assertIsNotNone(
            recovered.persisted_at
        )
        self.assertFalse(
            recovered.changed
        )

    def test_exact_claim_retry_backfills_missing_authorization_record(
        self,
    ):
        first = (
            acquire_live_sell_inventory_claim(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            first.status,
            CLAIM_PASS,
        )

        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                DELETE FROM
                    live_sell_authorization_records

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

        second = (
            acquire_live_sell_inventory_claim(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            second.status,
            CLAIM_PASS,
        )
        self.assertFalse(
            second.changed
        )

        recovered = self.load()

        self.assertEqual(
            recovered.status,
            PASS,
        )
        self.assertEqual(
            recovered.authorization,
            self.authorization,
        )

    def test_corrupt_payload_hash_is_unknown(
        self,
    ):
        claimed = (
            acquire_live_sell_inventory_claim(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            claimed.status,
            CLAIM_PASS,
        )

        connection = get_connection(
            self.db_path
        )

        try:
            connection.execute(
                """
                UPDATE
                    live_sell_authorization_records

                SET payload_json = payload_json || ' '

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

        recovered = self.load()

        self.assertEqual(
            recovered.status,
            UNKNOWN,
        )
        self.assertIn(
            "SELL_AUTHORIZATION_PAYLOAD_HASH_MISMATCH",
            recovered.reasons,
        )

    def test_tampered_payload_with_recomputed_payload_hash_still_fails_authorization_fingerprint(
        self,
    ):
        claimed = (
            acquire_live_sell_inventory_claim(
                authorization=(
                    self.authorization
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            claimed.status,
            CLAIM_PASS,
        )

        connection = get_connection(
            self.db_path
        )

        try:
            row = connection.execute(
                """
                SELECT payload_json

                FROM live_sell_authorization_records

                WHERE authorization_sha256 = ?
                """,
                (
                    self.authorization
                    .authorization_sha256,
                ),
            ).fetchone()

            self.assertIsNotNone(
                row
            )

            document = json.loads(
                row["payload_json"]
            )

            document[
                "protocol_fee_bps"
            ] = (
                int(
                    document[
                        "protocol_fee_bps"
                    ]
                )
                + 1
            )

            payload_json = json.dumps(
                document,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )

            payload_sha256 = hashlib.sha256(
                payload_json.encode(
                    "utf-8"
                )
            ).hexdigest()

            connection.execute(
                """
                UPDATE
                    live_sell_authorization_records

                SET payload_json = ?,
                    payload_sha256 = ?

                WHERE authorization_sha256 = ?
                """,
                (
                    payload_json,
                    payload_sha256,
                    self.authorization
                    .authorization_sha256,
                ),
            )

            connection.commit()

        finally:
            connection.close()

        recovered = self.load()

        self.assertEqual(
            recovered.status,
            UNKNOWN,
        )
        self.assertIn(
            "SELL_AUTHORIZATION_RECORD_INVALID",
            recovered.reasons,
        )

    def test_missing_record_table_is_read_only_unknown(
        self,
    ):
        handle = tempfile.NamedTemporaryFile(
            delete=False
        )
        handle.close()

        try:
            result = (
                load_live_sell_authorization_record_read_only(
                    authorization_sha256=(
                        self.authorization
                        .authorization_sha256
                    ),
                    db_path=handle.name,
                )
            )

            self.assertEqual(
                result.status,
                UNKNOWN,
            )
            self.assertIn(
                "LIVE_SELL_AUTHORIZATION_RECORD_TABLE_NOT_FOUND",
                result.reasons,
            )

            connection = get_connection(
                Path(handle.name)
            )

            try:
                table = connection.execute(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table'
                      AND name =
                          'live_sell_authorization_records'
                    """
                ).fetchone()

                self.assertIsNone(
                    table
                )

            finally:
                connection.close()

        finally:
            try:
                os.unlink(
                    handle.name
                )
            except FileNotFoundError:
                pass

    def test_claim_failure_rolls_back_authorization_record(
        self,
    ):
        with patch(
            "src.portfolio.live_sell_claims._load_claim",
            return_value=None,
        ):
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
            UNKNOWN,
        )
        self.assertIn(
            "SELL_CLAIM_ATOMIC_VERIFICATION_FAILED",
            result.reasons,
        )

        connection = get_connection(
            self.db_path
        )

        try:
            table = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table'
                  AND name =
                      'live_sell_authorization_records'
                """
            ).fetchone()

            if table is not None:
                row = connection.execute(
                    """
                    SELECT 1

                    FROM live_sell_authorization_records

                    WHERE authorization_sha256 = ?
                    """,
                    (
                        self.authorization
                        .authorization_sha256,
                    ),
                ).fetchone()

                self.assertIsNone(
                    row
                )

        finally:
            connection.close()

    def test_full_exit_execution_and_venue_evidence_round_trip(
        self,
    ):
        evidence = (
            PumpBondingCurveExitEvidence(
                pre_virtual_quote_reserves=10_000,
                pre_virtual_token_reserves=20_000,
                pre_real_quote_reserves=5_000,
                pre_real_token_reserves=15_000,
                post_virtual_quote_reserves=11_000,
                post_virtual_token_reserves=19_000,
                post_real_quote_reserves=6_000,
                post_real_token_reserves=14_000,
            )
        )

        exit_execution = ExitExecution(
            contract_version=(
                EXIT_EXECUTION_CONTRACT_VERSION
            ),
            venue=(
                PUMP_BONDING_CURVE_VENUE
            ),
            simulator_version=(
                "pump-sell-test-v1"
            ),
            tokens_in=(
                self.authorization
                .tokens_to_sell
            ),
            protocol_fee_bps=(
                self.authorization
                .protocol_fee_bps
            ),
            creator_fee_bps=(
                self.authorization
                .creator_fee_bps
            ),
            slippage_bps=(
                self.authorization
                .slippage_bps
            ),
            gross_quote_out=1_000_000,
            protocol_fee=10_000,
            creator_fee=5_000,
            net_quote_out_after_venue_fees=(
                985_000
            ),
            min_quote_out=900_000,
            price_impact_bps=125.25,
            base_network_fee_lamports=(
                self.authorization
                .base_network_fee_lamports
            ),
            priority_fee_lamports=(
                self.authorization
                .priority_fee_lamports
            ),
            total_transaction_overhead_lamports=(
                self.authorization
                .base_network_fee_lamports
                + self.authorization
                .priority_fee_lamports
            ),
            net_wallet_proceeds_lamports=(
                980_000
            ),
            all_in_exit_price_raw=(
                1960.125
            ),
            executable=True,
            ineligible_reason=None,
            venue_evidence=evidence,
        )

        authorization_sha256 = (
            _authorization_sha256(
                wallet_pubkey=(
                    self.authorization
                    .wallet_pubkey
                ),
                mint=(
                    self.authorization.mint
                ),
                bonding_curve=(
                    self.authorization
                    .bonding_curve
                ),
                base_token_program=(
                    self.authorization
                    .base_token_program
                ),
                associated_base_user=(
                    self.authorization
                    .associated_base_user
                ),
                creator=(
                    self.authorization.creator
                ),
                mayhem_mode=(
                    self.authorization
                    .mayhem_mode
                ),
                curve_quote_mint=(
                    self.authorization
                    .curve_quote_mint
                ),
                quote_mint_for_instruction=(
                    self.authorization
                    .quote_mint_for_instruction
                ),
                tokens_to_sell=(
                    self.authorization
                    .tokens_to_sell
                ),
                allocation=(
                    self.authorization
                    .allocation
                ),
                fee_rpc_slot=(
                    self.authorization
                    .fee_rpc_slot
                ),
                quote_mint=(
                    self.authorization
                    .curve_quote_mint
                ),
                protocol_fee_bps=(
                    self.authorization
                    .protocol_fee_bps
                ),
                creator_fee_bps=(
                    self.authorization
                    .creator_fee_bps
                ),
                slippage_bps=(
                    self.authorization
                    .slippage_bps
                ),
                base_network_fee_lamports=(
                    self.authorization
                    .base_network_fee_lamports
                ),
                priority_fee_lamports=(
                    self.authorization
                    .priority_fee_lamports
                ),
                exit_execution=(
                    exit_execution
                ),
            )
        )

        authorization = replace(
            self.authorization,
            authorization_sha256=(
                authorization_sha256
            ),
            exit_execution=(
                exit_execution
            ),
        )

        claimed = (
            acquire_live_sell_inventory_claim(
                authorization=(
                    authorization
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            claimed.status,
            CLAIM_PASS,
        )

        recovered = (
            load_live_sell_authorization_record_read_only(
                authorization_sha256=(
                    authorization_sha256
                ),
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            recovered.status,
            PASS,
        )
        self.assertEqual(
            recovered.authorization,
            authorization,
        )
        self.assertEqual(
            recovered.authorization
            .exit_execution
            .venue_evidence,
            evidence,
        )
        self.assertEqual(
            recovered.authorization
            .exit_execution
            .price_impact_bps,
            125.25,
        )
        self.assertEqual(
            recovered.authorization
            .exit_execution
            .all_in_exit_price_raw,
            1960.125,
        )


if __name__ == "__main__":
    unittest.main()
