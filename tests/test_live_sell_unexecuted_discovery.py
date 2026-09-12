from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.execution.live_sell_unexecuted_discovery import (
    PASS,
    UNKNOWN,
    discover_live_sell_unexecuted_candidates,
)
from src.portfolio.live_sell_authorization_records import (
    LIVE_SELL_AUTHORIZATION_RECORD_LOADER_VERSION,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION,
)


MODULE = (
    "src.execution.live_sell_unexecuted_discovery"
)


class LiveSellUnexecutedDiscoveryTests(
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

    def initialize_claim_table(self):
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
                            TEXT NOT NULL,

                        claimed_at
                            REAL NOT NULL
                    )
                """
            )

            connection.commit()

        finally:
            connection.close()

    def initialize_execution_table(self):
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            connection.execute(
                """
                CREATE TABLE
                    live_sell_execution_records (
                        authorization_sha256
                            TEXT PRIMARY KEY
                    )
                """
            )

            connection.commit()

        finally:
            connection.close()

    def insert_claim(
        self,
        *,
        authorization_sha256,
        claimed_at,
    ):
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            connection.execute(
                """
                INSERT INTO
                    live_sell_inventory_claims (
                        authorization_sha256,
                        status,
                        claimed_at
                    )

                VALUES (?, ?, ?)
                """,
                (
                    authorization_sha256,
                    ACTIVE,
                    claimed_at,
                ),
            )

            connection.commit()

        finally:
            connection.close()

    def insert_execution(
        self,
        *,
        authorization_sha256,
    ):
        connection = sqlite3.connect(
            self.db_path
        )

        try:
            connection.execute(
                """
                INSERT INTO
                    live_sell_execution_records (
                        authorization_sha256
                    )

                VALUES (?)
                """,
                (
                    authorization_sha256,
                ),
            )

            connection.commit()

        finally:
            connection.close()

    def objects(
        self,
        *,
        authorization_sha256,
        claimed_at,
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
            claimed_at=claimed_at,
        )

        return (
            authorization,
            claim,
        )

    def patches(
        self,
        *,
        authorization_sha256,
        claimed_at,
    ):
        authorization, claim = (
            self.objects(
                authorization_sha256=(
                    authorization_sha256
                ),
                claimed_at=claimed_at,
            )
        )

        return (
            patch(
                f"{MODULE}.load_live_sell_authorization_record_read_only",
                return_value=(
                    SimpleNamespace(
                        resolver_version=(
                            LIVE_SELL_AUTHORIZATION_RECORD_LOADER_VERSION
                        ),
                        status="PASS",
                        reasons=(),
                        authorization=authorization,
                    )
                ),
            ),
            patch(
                f"{MODULE}.load_active_live_sell_inventory_claim_read_only",
                return_value=(
                    SimpleNamespace(
                        loader_version=(
                            ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION
                        ),
                        status="PASS",
                        reasons=(),
                        claim=claim,
                    )
                ),
            ),
        )

    def test_missing_claim_table_is_empty_pass_without_mutation(
        self,
    ):
        before = self.db_path.read_bytes()

        result = (
            discover_live_sell_unexecuted_candidates(
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
            before,
            after,
        )

    def test_active_claim_without_execution_is_recovered(
        self,
    ):
        self.initialize_claim_table()

        self.insert_claim(
            authorization_sha256=self.sha_a,
            claimed_at=10.0,
        )

        patches = self.patches(
            authorization_sha256=self.sha_a,
            claimed_at=10.0,
        )

        with patches[0], patches[1]:
            result = (
                discover_live_sell_unexecuted_candidates(
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
            result.unexecuted_rows,
            1,
        )

    def test_execution_bearing_claim_is_not_candidate(
        self,
    ):
        self.initialize_claim_table()
        self.initialize_execution_table()

        self.insert_claim(
            authorization_sha256=self.sha_a,
            claimed_at=10.0,
        )

        self.insert_execution(
            authorization_sha256=self.sha_a,
        )

        with (
            patch(
                f"{MODULE}.load_live_sell_authorization_record_read_only"
            ) as auth_loader,
            patch(
                f"{MODULE}.load_active_live_sell_inventory_claim_read_only"
            ) as claim_loader,
        ):
            result = (
                discover_live_sell_unexecuted_candidates(
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
            result.scanned_active_claim_rows,
            1,
        )
        self.assertEqual(
            result.unexecuted_rows,
            0,
        )

        auth_loader.assert_not_called()
        claim_loader.assert_not_called()

    def test_authorization_failure_returns_no_partial_candidates(
        self,
    ):
        self.initialize_claim_table()

        self.insert_claim(
            authorization_sha256=self.sha_a,
            claimed_at=1.0,
        )

        self.insert_claim(
            authorization_sha256=self.sha_b,
            claimed_at=2.0,
        )

        authorization_a, claim_a = (
            self.objects(
                authorization_sha256=self.sha_a,
                claimed_at=1.0,
            )
        )

        def auth_loader(
            *,
            authorization_sha256,
            db_path,
        ):
            if authorization_sha256 == self.sha_a:
                return SimpleNamespace(
                    resolver_version=(
                        LIVE_SELL_AUTHORIZATION_RECORD_LOADER_VERSION
                    ),
                    status="PASS",
                    reasons=(),
                    authorization=(
                        authorization_a
                    ),
                )

            return SimpleNamespace(
                resolver_version=(
                    LIVE_SELL_AUTHORIZATION_RECORD_LOADER_VERSION
                ),
                status="UNKNOWN",
                reasons=(
                    "CORRUPT_AUTHORIZATION",
                ),
                authorization=None,
            )

        with (
            patch(
                f"{MODULE}.load_live_sell_authorization_record_read_only",
                side_effect=auth_loader,
            ),
            patch(
                f"{MODULE}.load_active_live_sell_inventory_claim_read_only",
                return_value=(
                    SimpleNamespace(
                        loader_version=(
                            ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION
                        ),
                        status="PASS",
                        reasons=(),
                        claim=claim_a,
                    )
                ),
            ),
        ):
            result = (
                discover_live_sell_unexecuted_candidates(
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
        self.initialize_claim_table()

        self.insert_claim(
            authorization_sha256=self.sha_a,
            claimed_at=1.0,
        )

        authorization, _claim = (
            self.objects(
                authorization_sha256=self.sha_a,
                claimed_at=1.0,
            )
        )

        with (
            patch(
                f"{MODULE}.load_live_sell_authorization_record_read_only",
                return_value=(
                    SimpleNamespace(
                        resolver_version=(
                            LIVE_SELL_AUTHORIZATION_RECORD_LOADER_VERSION
                        ),
                        status="PASS",
                        reasons=(),
                        authorization=authorization,
                    )
                ),
            ),
            patch(
                f"{MODULE}.load_active_live_sell_inventory_claim_read_only",
                return_value=(
                    SimpleNamespace(
                        loader_version=(
                            ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION
                        ),
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
                discover_live_sell_unexecuted_candidates(
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

    def test_identity_mismatch_fails_closed(
        self,
    ):
        self.initialize_claim_table()

        self.insert_claim(
            authorization_sha256=self.sha_a,
            claimed_at=1.0,
        )

        authorization, claim = (
            self.objects(
                authorization_sha256=self.sha_a,
                claimed_at=1.0,
            )
        )

        claim.mint = "different-mint"

        with (
            patch(
                f"{MODULE}.load_live_sell_authorization_record_read_only",
                return_value=(
                    SimpleNamespace(
                        resolver_version=(
                            LIVE_SELL_AUTHORIZATION_RECORD_LOADER_VERSION
                        ),
                        status="PASS",
                        reasons=(),
                        authorization=authorization,
                    )
                ),
            ),
            patch(
                f"{MODULE}.load_active_live_sell_inventory_claim_read_only",
                return_value=(
                    SimpleNamespace(
                        loader_version=(
                            ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION
                        ),
                        status="PASS",
                        reasons=(),
                        claim=claim,
                    )
                ),
            ),
        ):
            result = (
                discover_live_sell_unexecuted_candidates(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertIn(
            "LIVE_SELL_UNEXECUTED_IDENTITY_MISMATCH",
            result.reasons,
        )

    def test_execution_appearing_during_discovery_fails_closed(
        self,
    ):
        self.initialize_claim_table()
        self.initialize_execution_table()

        self.insert_claim(
            authorization_sha256=self.sha_a,
            claimed_at=1.0,
        )

        patches = self.patches(
            authorization_sha256=self.sha_a,
            claimed_at=1.0,
        )

        with (
            patches[0],
            patches[1],
            patch(
                f"{MODULE}._execution_exists_read_only",
                return_value=True,
            ),
        ):
            result = (
                discover_live_sell_unexecuted_candidates(
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
            "LIVE_SELL_UNEXECUTED_EXECUTION_APPEARED_DURING_DISCOVERY",
            result.reasons,
        )

    def test_candidates_are_oldest_claim_first(
        self,
    ):
        self.initialize_claim_table()

        self.insert_claim(
            authorization_sha256=self.sha_a,
            claimed_at=20.0,
        )

        self.insert_claim(
            authorization_sha256=self.sha_b,
            claimed_at=10.0,
        )

        objects = {
            self.sha_a: self.objects(
                authorization_sha256=self.sha_a,
                claimed_at=20.0,
            ),
            self.sha_b: self.objects(
                authorization_sha256=self.sha_b,
                claimed_at=10.0,
            ),
        }

        def auth_loader(
            *,
            authorization_sha256,
            db_path,
        ):
            return SimpleNamespace(
                resolver_version=(
                    LIVE_SELL_AUTHORIZATION_RECORD_LOADER_VERSION
                ),
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
                loader_version=(
                    ACTIVE_LIVE_SELL_INVENTORY_CLAIM_LOADER_VERSION
                ),
                status="PASS",
                reasons=(),
                claim=objects[
                    authorization
                    .authorization_sha256
                ][1],
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
        ):
            result = (
                discover_live_sell_unexecuted_candidates(
                    db_path=self.db_path
                )
            )

        self.assertEqual(
            result.status,
            PASS,
        )

        self.assertEqual(
            tuple(
                item.authorization_sha256
                for item
                in result.candidates
            ),
            (
                self.sha_b,
                self.sha_a,
            ),
        )


if __name__ == "__main__":
    unittest.main()
