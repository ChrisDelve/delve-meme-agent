from __future__ import annotations

import sqlite3
from concurrent.futures import (
    ThreadPoolExecutor,
)
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.execution.model_entry_candidate import (
    EXPECTED_ARTIFACT_VERSION,
    EXPECTED_MODEL_SHADOW_VERSION,
    MODEL_ENTRY_CANDIDATE_VERSION,
    make_model_entry_candidate,
)
from src.portfolio.live_entry_admissions import (
    BLOCK,
    LIVE_ENTRY_ADMISSION_VERSION,
    PASS,
    UNKNOWN,
    _candidate_sha256,
    acquire_live_entry_admission,
)


MODULE = (
    "src.portfolio.live_entry_admissions"
)


class LiveEntryAdmissionsTests(
    unittest.TestCase
):
    @staticmethod
    def candidate(
        *,
        entry_signature: str = (
            "entry-signature-1"
        ),
        mint: str = "mint-1",
        event_user: str = "event-user-1",
    ):
        return make_model_entry_candidate(
            entry_signature=(
                entry_signature
            ),
            mint=mint,
            event_user=event_user,
            quote_mint="quote-mint-1",
            slot=123,
            trade_timestamp=1_700_000_000,
            observed_at=1_700_000_001,
            predicted_at=1_700_000_002,
            model_shadow_version=(
                EXPECTED_MODEL_SHADOW_VERSION
            ),
            artifact_version=(
                EXPECTED_ARTIFACT_VERSION
            ),
            artifact_sha256="a" * 64,
            model_eligible=True,
            probability_2x_15m=0.42,
            signal_virtual_quote_reserves=(
                30_000_000_000
            ),
            signal_virtual_token_reserves=(
                1_000_000_000_000
            ),
        )

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_ENTRY_ADMISSION_VERSION,
            "live-entry-admission-v1",
        )

    def test_candidate_hash_is_deterministic(
        self,
    ):
        first = self.candidate()
        second = self.candidate()

        self.assertEqual(
            _candidate_sha256(
                first
            ),
            _candidate_sha256(
                second
            ),
        )

        self.assertEqual(
            len(
                _candidate_sha256(
                    first
                )
            ),
            64,
        )

    def test_first_admission_passes_and_persists_exact_candidate(
        self,
    ):
        with TemporaryDirectory() as temp:
            db_path = Path(
                temp
            ) / "live.db"

            candidate = self.candidate()

            result = (
                acquire_live_entry_admission(
                    candidate=candidate,
                    db_path=db_path,
                )
            )

            self.assertEqual(
                result.status,
                PASS,
            )
            self.assertEqual(
                result.reasons,
                (),
            )
            self.assertTrue(
                result.changed
            )
            self.assertIsNotNone(
                result.admission
            )

            admission = result.admission
            assert admission is not None

            self.assertEqual(
                admission.entry_signature,
                candidate.entry_signature,
            )
            self.assertEqual(
                admission.candidate_version,
                MODEL_ENTRY_CANDIDATE_VERSION,
            )
            self.assertEqual(
                admission.candidate_sha256,
                _candidate_sha256(
                    candidate
                ),
            )
            self.assertEqual(
                admission.mint,
                candidate.mint,
            )
            self.assertEqual(
                admission.event_user,
                candidate.event_user,
            )
            self.assertEqual(
                admission.quote_mint,
                candidate.quote_mint,
            )
            self.assertEqual(
                admission.slot,
                candidate.slot,
            )
            self.assertEqual(
                admission.trade_timestamp,
                candidate.trade_timestamp,
            )
            self.assertEqual(
                admission.observed_at,
                candidate.observed_at,
            )
            self.assertEqual(
                admission.predicted_at,
                candidate.predicted_at,
            )
            self.assertEqual(
                admission.model_shadow_version,
                candidate.model_shadow_version,
            )
            self.assertEqual(
                admission.artifact_version,
                candidate.artifact_version,
            )
            self.assertEqual(
                admission.artifact_sha256,
                candidate.artifact_sha256,
            )
            self.assertIs(
                admission.model_eligible,
                True,
            )
            self.assertEqual(
                admission.probability_2x_15m,
                candidate.probability_2x_15m,
            )
            self.assertEqual(
                admission
                .signal_virtual_quote_reserves,
                candidate
                .signal_virtual_quote_reserves,
            )
            self.assertEqual(
                admission
                .signal_virtual_token_reserves,
                candidate
                .signal_virtual_token_reserves,
            )

    def test_exact_replay_is_permanently_blocked_without_change(
        self,
    ):
        with TemporaryDirectory() as temp:
            db_path = Path(
                temp
            ) / "live.db"

            candidate = self.candidate()

            first = acquire_live_entry_admission(
                candidate=candidate,
                db_path=db_path,
            )

            second = acquire_live_entry_admission(
                candidate=candidate,
                db_path=db_path,
            )

            self.assertEqual(
                first.status,
                PASS,
            )
            self.assertTrue(
                first.changed
            )

            self.assertEqual(
                second.status,
                BLOCK,
            )
            self.assertEqual(
                second.reasons,
                (
                    "LIVE_ENTRY_ALREADY_ADMITTED",
                ),
            )
            self.assertFalse(
                second.changed
            )
            self.assertIsNotNone(
                second.admission
            )
            self.assertEqual(
                second.admission,
                first.admission,
            )

    def test_same_signature_with_conflicting_identity_is_unknown(
        self,
    ):
        with TemporaryDirectory() as temp:
            db_path = Path(
                temp
            ) / "live.db"

            first_candidate = (
                self.candidate()
            )

            conflicting = self.candidate(
                mint="different-mint"
            )

            first = acquire_live_entry_admission(
                candidate=first_candidate,
                db_path=db_path,
            )

            second = acquire_live_entry_admission(
                candidate=conflicting,
                db_path=db_path,
            )

            self.assertEqual(
                first.status,
                PASS,
            )

            self.assertEqual(
                second.status,
                UNKNOWN,
            )
            self.assertEqual(
                second.reasons,
                (
                    "LIVE_ENTRY_ADMISSION_"
                    "IDENTITY_MISMATCH",
                ),
            )
            self.assertFalse(
                second.changed
            )
            self.assertIsNotNone(
                second.admission
            )
            self.assertEqual(
                second.admission,
                first.admission,
            )

    def test_concurrent_exact_claims_produce_one_pass_one_block(
        self,
    ):
        with TemporaryDirectory() as temp:
            db_path = Path(
                temp
            ) / "live.db"

            candidate = self.candidate()

            def claim():
                return acquire_live_entry_admission(
                    candidate=candidate,
                    db_path=db_path,
                )

            with ThreadPoolExecutor(
                max_workers=2
            ) as pool:
                results = list(
                    pool.map(
                        lambda _: claim(),
                        range(2),
                    )
                )

            self.assertEqual(
                sorted(
                    result.status
                    for result in results
                ),
                [
                    BLOCK,
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

    def test_invalid_candidate_type_is_unknown_without_database_write(
        self,
    ):
        with TemporaryDirectory() as temp:
            db_path = Path(
                temp
            ) / "live.db"

            result = (
                acquire_live_entry_admission(
                    candidate=object(),
                    db_path=db_path,
                )
            )

            self.assertEqual(
                result.status,
                UNKNOWN,
            )
            self.assertEqual(
                result.reasons,
                (
                    "INVALID_MODEL_ENTRY_CANDIDATE",
                ),
            )
            self.assertFalse(
                result.changed
            )
            self.assertFalse(
                db_path.exists()
            )

    def test_tampered_candidate_version_is_unknown_before_database_write(
        self,
    ):
        with TemporaryDirectory() as temp:
            db_path = Path(
                temp
            ) / "live.db"

            candidate = self.candidate()

            object.__setattr__(
                candidate,
                "candidate_version",
                "unexpected-version",
            )

            result = (
                acquire_live_entry_admission(
                    candidate=candidate,
                    db_path=db_path,
                )
            )

            self.assertEqual(
                result.status,
                UNKNOWN,
            )
            self.assertEqual(
                result.reasons,
                (
                    "MODEL_ENTRY_CANDIDATE_"
                    "STORAGE_INVALID",
                ),
            )
            self.assertFalse(
                result.changed
            )
            self.assertFalse(
                db_path.exists()
            )

    def test_database_error_is_unknown(
        self,
    ):
        candidate = self.candidate()

        with patch(
            f"{MODULE}.get_connection",
            side_effect=sqlite3.OperationalError(
                "database unavailable"
            ),
        ):
            result = (
                acquire_live_entry_admission(
                    candidate=candidate
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_ADMISSION_"
                "DATABASE_ERROR",
            ),
        )
        self.assertFalse(
            result.changed
        )

    def test_corrupted_persisted_identity_fails_closed(
        self,
    ):
        with TemporaryDirectory() as temp:
            db_path = Path(
                temp
            ) / "live.db"

            candidate = self.candidate()

            first = acquire_live_entry_admission(
                candidate=candidate,
                db_path=db_path,
            )

            self.assertEqual(
                first.status,
                PASS,
            )

            connection = sqlite3.connect(
                db_path
            )

            try:
                connection.execute(
                    """
                    UPDATE live_entry_admissions
                    SET mint = ?
                    WHERE entry_signature = ?
                    """,
                    (
                        "corrupted-mint",
                        candidate.entry_signature,
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            replay = acquire_live_entry_admission(
                candidate=candidate,
                db_path=db_path,
            )

            self.assertEqual(
                replay.status,
                UNKNOWN,
            )
            self.assertEqual(
                replay.reasons,
                (
                    "LIVE_ENTRY_ADMISSION_"
                    "IDENTITY_MISMATCH",
                ),
            )
            self.assertFalse(
                replay.changed
            )


if __name__ == "__main__":
    unittest.main()
