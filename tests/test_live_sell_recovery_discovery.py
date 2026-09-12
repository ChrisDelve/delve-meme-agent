from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.execution.live_sell_recovery_discovery import (
    PASS,
    UNKNOWN,
    discover_live_sell_recovery_candidates,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    CONSUMED,
    RELEASED,
)
from src.portfolio.live_sell_execution_records import (
    SIGNED,
    SUBMISSION_ARMED,
    SUBMITTED,
)


MODULE = (
    "src.execution.live_sell_recovery_discovery"
)


class LiveSellRecoveryDiscoveryTests(
    unittest.TestCase
):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(
            delete=False
        )
        handle.close()

        self.db_path = Path(
            handle.name
        )

        self.sha_a = "aa" * 32
        self.sha_b = "bb" * 32

    def tearDown(self):
        try:
            os.unlink(
                self.db_path
            )
        except FileNotFoundError:
            pass

    def initialize_tables(self):
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            connection.execute(
                """
                CREATE TABLE
                    live_sell_inventory_claims (
                        authorization_sha256
                            TEXT PRIMARY KEY,

                        status
                            TEXT NOT NULL
                    )
                """
            )

            connection.execute(
                """
                CREATE TABLE
                    live_sell_execution_records (
                        authorization_sha256
                            TEXT PRIMARY KEY,

                        status
                            TEXT NOT NULL,

                        signed_at
                            REAL NOT NULL
                    )
                """
            )

            connection.commit()

        finally:
            connection.close()

    def insert(
        self,
        *,
        authorization_sha256,
        execution_status,
        claim_status=ACTIVE,
        signed_at=1.0,
        include_claim=True,
    ):
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            if include_claim:
                connection.execute(
                    """
                    INSERT INTO
                        live_sell_inventory_claims (
                            authorization_sha256,
                            status
                        )

                    VALUES (?, ?)
                    """,
                    (
                        authorization_sha256,
                        claim_status,
                    ),
                )

            connection.execute(
                """
                INSERT INTO
                    live_sell_execution_records (
                        authorization_sha256,
                        status,
                        signed_at
                    )

                VALUES (?, ?, ?)
                """,
                (
                    authorization_sha256,
                    execution_status,
                    signed_at,
                ),
            )

            connection.commit()

        finally:
            connection.close()

    def objects(
        self,
        *,
        authorization_sha256,
        execution_status,
        signed_at=1.0,
    ):
        allocation = object()

        authorization = SimpleNamespace(
            authorization_version=(
                "live-pump-sell-authorization-v2"
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            wallet_pubkey="wallet",
            mint="mint",
            tokens_to_sell=100,
            allocation=allocation,
        )

        claim = SimpleNamespace(
            authorization_version=(
                authorization
                .authorization_version
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            wallet_pubkey="wallet",
            mint="mint",
            tokens_to_sell=100,
            allocation=allocation,
            status=ACTIVE,
        )

        execution = SimpleNamespace(
            authorization_version=(
                authorization
                .authorization_version
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            wallet_pubkey="wallet",
            mint="mint",
            tokens_to_sell=100,
            status=execution_status,
            signed_at=signed_at,
        )

        return (
            authorization,
            claim,
            execution,
        )

    def loader_patches(
        self,
        *,
        authorization_sha256,
        execution_status,
        signed_at=1.0,
    ):
        (
            authorization,
            claim,
            execution,
        ) = self.objects(
            authorization_sha256=(
                authorization_sha256
            ),
            execution_status=(
                execution_status
            ),
            signed_at=signed_at,
        )

        authorization_result = (
            SimpleNamespace(
                status="PASS",
                reasons=(),
                authorization=(
                    authorization
                ),
            )
        )

        claim_result = SimpleNamespace(
            status="PASS",
            reasons=(),
            claim=claim,
        )

        execution_result = (
            SimpleNamespace(
                status="PASS",
                reasons=(),
                record=execution,
            )
        )

        return (
            patch(
                f"{MODULE}.load_live_sell_authorization_record_read_only",
                return_value=(
                    authorization_result
                ),
            ),
            patch(
                f"{MODULE}.load_active_live_sell_inventory_claim_read_only",
                return_value=(
                    claim_result
                ),
            ),
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only",
                return_value=(
                    execution_result
                ),
            ),
        )

    def test_missing_execution_table_is_empty_pass_without_mutation(
        self,
    ):
        before = self.db_path.read_bytes()

        result = (
            discover_live_sell_recovery_candidates(
                db_path=self.db_path
            )
        )

        after = self.db_path.read_bytes()

        self.assertEqual(
            result.status,
            PASS,
        )
        self.assertEqual(
            result.candidates,
            (),
        )
        self.assertEqual(
            result.scanned_execution_rows,
            0,
        )
        self.assertEqual(
            before,
            after,
        )

    def test_active_signed_execution_is_recovered(
        self,
    ):
        self.initialize_tables()

        self.insert(
            authorization_sha256=self.sha_a,
            execution_status=SIGNED,
            signed_at=10.0,
        )

        patches = self.loader_patches(
            authorization_sha256=self.sha_a,
            execution_status=SIGNED,
            signed_at=10.0,
        )

        with (
            patches[0],
            patches[1],
            patches[2],
        ):
            result = (
                discover_live_sell_recovery_candidates(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            PASS,
        )
        self.assertEqual(
            len(result.candidates),
            1,
        )
        self.assertEqual(
            result.candidates[0]
            .authorization_sha256,
            self.sha_a,
        )
        self.assertEqual(
            result.candidates[0]
            .execution_status,
            SIGNED,
        )
        self.assertEqual(
            result.active_execution_rows,
            1,
        )

    def test_terminal_claims_are_not_recovery_candidates(
        self,
    ):
        self.initialize_tables()

        self.insert(
            authorization_sha256=self.sha_a,
            execution_status=SUBMITTED,
            claim_status=RELEASED,
            signed_at=1.0,
        )

        self.insert(
            authorization_sha256=self.sha_b,
            execution_status=SUBMITTED,
            claim_status=CONSUMED,
            signed_at=2.0,
        )

        with (
            patch(
                f"{MODULE}.load_live_sell_authorization_record_read_only"
            ) as authorization_loader,
            patch(
                f"{MODULE}.load_active_live_sell_inventory_claim_read_only"
            ) as claim_loader,
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only"
            ) as execution_loader,
        ):
            result = (
                discover_live_sell_recovery_candidates(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            PASS,
        )
        self.assertEqual(
            result.candidates,
            (),
        )
        self.assertEqual(
            result.scanned_execution_rows,
            2,
        )
        self.assertEqual(
            result.active_execution_rows,
            0,
        )

        authorization_loader.assert_not_called()
        claim_loader.assert_not_called()
        execution_loader.assert_not_called()

    def test_orphan_execution_row_fails_closed(
        self,
    ):
        self.initialize_tables()

        self.insert(
            authorization_sha256=self.sha_a,
            execution_status=SIGNED,
            include_claim=False,
        )

        result = (
            discover_live_sell_recovery_candidates(
                db_path=self.db_path
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.candidates,
            (),
        )
        self.assertIn(
            "LIVE_SELL_RECOVERY_EXECUTION_ORPHANED",
            result.reasons,
        )
        self.assertEqual(
            result.failed_authorization_sha256,
            self.sha_a,
        )

    def test_authorization_load_failure_returns_no_partial_candidates(
        self,
    ):
        self.initialize_tables()

        self.insert(
            authorization_sha256=self.sha_a,
            execution_status=SIGNED,
            signed_at=1.0,
        )

        self.insert(
            authorization_sha256=self.sha_b,
            execution_status=SIGNED,
            signed_at=2.0,
        )

        (
            authorization_a,
            claim_a,
            execution_a,
        ) = self.objects(
            authorization_sha256=self.sha_a,
            execution_status=SIGNED,
            signed_at=1.0,
        )

        def load_authorization(
            *,
            authorization_sha256,
            db_path,
        ):
            if (
                authorization_sha256
                == self.sha_a
            ):
                return SimpleNamespace(
                    status="PASS",
                    reasons=(),
                    authorization=(
                        authorization_a
                    ),
                )

            return SimpleNamespace(
                status="UNKNOWN",
                reasons=(
                    "CORRUPT_AUTHORIZATION",
                ),
                authorization=None,
            )

        with (
            patch(
                f"{MODULE}.load_live_sell_authorization_record_read_only",
                side_effect=(
                    load_authorization
                ),
            ),
            patch(
                f"{MODULE}.load_active_live_sell_inventory_claim_read_only",
                return_value=(
                    SimpleNamespace(
                        status="PASS",
                        reasons=(),
                        claim=claim_a,
                    )
                ),
            ),
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only",
                return_value=(
                    SimpleNamespace(
                        status="PASS",
                        reasons=(),
                        record=execution_a,
                    )
                ),
            ),
        ):
            result = (
                discover_live_sell_recovery_candidates(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.candidates,
            (),
        )
        self.assertEqual(
            result.failed_authorization_sha256,
            self.sha_b,
        )

    def test_active_claim_loader_failure_fails_closed(
        self,
    ):
        self.initialize_tables()

        self.insert(
            authorization_sha256=self.sha_a,
            execution_status=(
                SUBMISSION_ARMED
            ),
        )

        (
            authorization,
            _claim,
            _execution,
        ) = self.objects(
            authorization_sha256=self.sha_a,
            execution_status=(
                SUBMISSION_ARMED
            ),
        )

        with (
            patch(
                f"{MODULE}.load_live_sell_authorization_record_read_only",
                return_value=(
                    SimpleNamespace(
                        status="PASS",
                        reasons=(),
                        authorization=(
                            authorization
                        ),
                    )
                ),
            ),
            patch(
                f"{MODULE}.load_active_live_sell_inventory_claim_read_only",
                return_value=(
                    SimpleNamespace(
                        status="BLOCK",
                        reasons=(
                            "CLAIM_NOT_ACTIVE",
                        ),
                        claim=None,
                    )
                ),
            ),
        ):
            result = (
                discover_live_sell_recovery_candidates(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.candidates,
            (),
        )
        self.assertIn(
            "LIVE_SELL_RECOVERY_ACTIVE_CLAIM_LOAD_FAILED",
            result.reasons,
        )

    def test_execution_loader_failure_fails_closed(
        self,
    ):
        self.initialize_tables()

        self.insert(
            authorization_sha256=self.sha_a,
            execution_status=SUBMITTED,
        )

        (
            authorization,
            claim,
            _execution,
        ) = self.objects(
            authorization_sha256=self.sha_a,
            execution_status=SUBMITTED,
        )

        with (
            patch(
                f"{MODULE}.load_live_sell_authorization_record_read_only",
                return_value=(
                    SimpleNamespace(
                        status="PASS",
                        reasons=(),
                        authorization=(
                            authorization
                        ),
                    )
                ),
            ),
            patch(
                f"{MODULE}.load_active_live_sell_inventory_claim_read_only",
                return_value=(
                    SimpleNamespace(
                        status="PASS",
                        reasons=(),
                        claim=claim,
                    )
                ),
            ),
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only",
                return_value=(
                    SimpleNamespace(
                        status="UNKNOWN",
                        reasons=(
                            "CORRUPT_EXECUTION",
                        ),
                        record=None,
                    )
                ),
            ),
        ):
            result = (
                discover_live_sell_recovery_candidates(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.candidates,
            (),
        )
        self.assertIn(
            "LIVE_SELL_RECOVERY_EXECUTION_LOAD_FAILED",
            result.reasons,
        )

    def test_candidates_are_oldest_signed_first(
        self,
    ):
        self.initialize_tables()

        self.insert(
            authorization_sha256=self.sha_a,
            execution_status=SIGNED,
            signed_at=20.0,
        )

        self.insert(
            authorization_sha256=self.sha_b,
            execution_status=SIGNED,
            signed_at=10.0,
        )

        objects = {
            self.sha_a: self.objects(
                authorization_sha256=self.sha_a,
                execution_status=SIGNED,
                signed_at=20.0,
            ),
            self.sha_b: self.objects(
                authorization_sha256=self.sha_b,
                execution_status=SIGNED,
                signed_at=10.0,
            ),
        }

        def auth_loader(
            *,
            authorization_sha256,
            db_path,
        ):
            return SimpleNamespace(
                status="PASS",
                reasons=(),
                authorization=objects[
                    authorization_sha256
                ][0],
            )

        def claim_loader(
            *,
            authorization,
            db_path,
        ):
            return SimpleNamespace(
                status="PASS",
                reasons=(),
                claim=objects[
                    authorization
                    .authorization_sha256
                ][1],
            )

        def execution_loader(
            *,
            authorization_sha256,
            db_path,
        ):
            return SimpleNamespace(
                status="PASS",
                reasons=(),
                record=objects[
                    authorization_sha256
                ][2],
            )

        with (
            patch(
                f"{MODULE}.load_live_sell_authorization_record_read_only",
                side_effect=auth_loader,
            ),
            patch(
                f"{MODULE}.load_active_live_sell_inventory_claim_read_only",
                side_effect=claim_loader,
            ),
            patch(
                f"{MODULE}.load_live_sell_execution_record_read_only",
                side_effect=execution_loader,
            ),
        ):
            result = (
                discover_live_sell_recovery_candidates(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertEqual(
            tuple(
                candidate
                .authorization_sha256
                for candidate
                in result.candidates
            ),
            (
                self.sha_b,
                self.sha_a,
            ),
        )


if __name__ == "__main__":
    unittest.main()
