from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
)

from src.execution.live_entry_candidate_pipeline import (
    ADAPTER,
    EVIDENCE,
    LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION,
    PASS,
    POLICY,
    REJECT,
    UNKNOWN,
    LiveEntryCandidatePipelineResult,
    resolve_live_entry_candidate_evidence_once,
)
from src.execution.live_entry_evidence import (
    LIVE_ENTRY_EVIDENCE_RESOLUTION_VERSION,
    PASS as EVIDENCE_PASS,
    REJECT as EVIDENCE_REJECT,
    UNKNOWN as EVIDENCE_UNKNOWN,
    LiveEntryEvidence,
    LiveEntryEvidenceResolution,
)
from src.execution.model_entry_candidate import (
    EXPECTED_ARTIFACT_VERSION,
    EXPECTED_MODEL_SHADOW_VERSION,
)
from src.strategies.live_entry_policy import (
    LiveEntryPolicy,
)


class LiveEntryCandidatePipelineTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.event = {
            "entry_signature":
                "signature-1",
            "mint": "mint-1",
            "event_user": "wallet-1",
            "quote_mint":
                "11111111111111111111111111111111",
            "slot": 123,
            "trade_timestamp": 1_000,
            "observed_at": 1_001,
            "signal_virtual_quote_reserves":
                30_000_000_000,
            "signal_virtual_token_reserves":
                1_000_000_000_000,
        }

        self.prediction = {
            "entry_signature":
                "signature-1",
            "shadow_version":
                EXPECTED_MODEL_SHADOW_VERSION,
            "artifact_version":
                EXPECTED_ARTIFACT_VERSION,
            "artifact_sha256":
                "ab" * 32,
            "mint": "mint-1",
            "wallet": "wallet-1",
            "quote_mint":
                "11111111111111111111111111111111",
            "slot": 123,
            "trade_timestamp": 1_000,
            "observed_at": 1_001,
            "model_eligible": 1,
            "probability_2x_15m": 0.42,
            "predicted_at": 1_002,
        }

        self.policy = LiveEntryPolicy(
            min_probability_2x_15m=0.40,
            max_candidate_age_seconds=5,
        )

    async def run_pipeline(
        self,
        *,
        prediction=None,
        policy=None,
        evaluated_at=1_004,
    ):
        return (
            await resolve_live_entry_candidate_evidence_once(
                prediction=(
                    dict(self.prediction)
                    if prediction is None
                    else prediction
                ),
                policy=(
                    self.policy
                    if policy is None
                    else policy
                ),
                evaluated_at=evaluated_at,
                **self.event,
            )
        )

    @staticmethod
    def pass_resolution(
        candidate,
    ):
        evidence = Mock(
            spec=LiveEntryEvidence
        )
        evidence.candidate = candidate
        evidence.safety = None

        return LiveEntryEvidenceResolution(
            resolution_version=(
                LIVE_ENTRY_EVIDENCE_RESOLUTION_VERSION
            ),
            candidate=candidate,
            status=EVIDENCE_PASS,
            reasons=(),
            safety=None,
            evidence=evidence,
        )

    @staticmethod
    def nonpass_resolution(
        candidate,
        *,
        status,
        reason,
    ):
        return LiveEntryEvidenceResolution(
            resolution_version=(
                LIVE_ENTRY_EVIDENCE_RESOLUTION_VERSION
            ),
            candidate=candidate,
            status=status,
            reasons=(reason,),
            safety=None,
            evidence=None,
        )

    async def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION,
            "live-entry-candidate-pipeline-v1",
        )

    async def test_candidate_failure_stops_at_adapter(
        self,
    ):
        prediction = dict(
            self.prediction
        )
        prediction[
            "entry_signature"
        ] = "wrong"

        with patch(
            "src.execution."
            "live_entry_candidate_pipeline."
            "resolve_live_entry_evidence",
            new_callable=AsyncMock,
        ) as resolver:
            result = await self.run_pipeline(
                prediction=prediction
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            ADAPTER,
        )
        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_PIPELINE_"
                "CANDIDATE_INVALID",
            ),
        )
        self.assertIsNone(
            result.candidate
        )
        resolver.assert_not_awaited()

    async def test_invalid_evaluated_at_is_valid_policy_unknown(
        self,
    ):
        with patch(
            "src.execution."
            "live_entry_candidate_pipeline."
            "resolve_live_entry_evidence",
            new_callable=AsyncMock,
        ) as resolver:
            result = await self.run_pipeline(
                evaluated_at=0
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            POLICY,
        )
        self.assertEqual(
            result.reasons,
            (
                "POLICY:"
                "INVALID_EVALUATED_AT",
            ),
        )
        self.assertIsNotNone(
            result.policy_decision
        )
        self.assertEqual(
            result.policy_decision.entry_signature,
            "",
        )
        self.assertEqual(
            result.policy_decision.mint,
            "",
        )
        resolver.assert_not_awaited()

    async def test_unsupported_quote_is_valid_policy_reject(
        self,
    ):
        prediction = dict(
            self.prediction
        )
        prediction[
            "quote_mint"
        ] = "unsupported-quote"

        event_quote = (
            self.event["quote_mint"]
        )
        self.event[
            "quote_mint"
        ] = "unsupported-quote"

        try:
            with patch(
                "src.execution."
                "live_entry_candidate_pipeline."
                "resolve_live_entry_evidence",
                new_callable=AsyncMock,
            ) as resolver:
                result = await self.run_pipeline(
                    prediction=prediction
                )
        finally:
            self.event[
                "quote_mint"
            ] = event_quote

        self.assertEqual(
            result.status,
            REJECT,
        )
        self.assertEqual(
            result.stage,
            POLICY,
        )
        self.assertEqual(
            result.reasons,
            (
                "POLICY:"
                "UNSUPPORTED_QUOTE_MINT",
            ),
        )
        self.assertIsNone(
            result.policy_decision
            .probability_2x_15m
        )
        resolver.assert_not_awaited()

    async def test_policy_reject_stops_before_evidence(
        self,
    ):
        policy = LiveEntryPolicy(
            min_probability_2x_15m=0.50,
            max_candidate_age_seconds=5,
        )

        with patch(
            "src.execution."
            "live_entry_candidate_pipeline."
            "resolve_live_entry_evidence",
            new_callable=AsyncMock,
        ) as resolver:
            result = await self.run_pipeline(
                policy=policy
            )

        self.assertEqual(
            result.status,
            REJECT,
        )
        self.assertEqual(
            result.stage,
            POLICY,
        )
        self.assertEqual(
            result.reasons,
            (
                "POLICY:"
                "PROBABILITY_BELOW_THRESHOLD",
            ),
        )
        resolver.assert_not_awaited()

    async def test_policy_unknown_stops_before_evidence(
        self,
    ):
        with patch(
            "src.execution."
            "live_entry_candidate_pipeline."
            "resolve_live_entry_evidence",
            new_callable=AsyncMock,
        ) as resolver:
            result = await self.run_pipeline(
                evaluated_at=0
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            POLICY,
        )
        self.assertEqual(
            result.reasons,
            (
                "POLICY:"
                "INVALID_EVALUATED_AT",
            ),
        )
        resolver.assert_not_awaited()

    async def test_policy_pass_resolves_exact_candidate(
        self,
    ):
        captured = {}

        async def resolve(
            *,
            candidate,
        ):
            captured["candidate"] = (
                candidate
            )
            return self.nonpass_resolution(
                candidate,
                status=EVIDENCE_UNKNOWN,
                reason="TEST_UNKNOWN",
            )

        with patch(
            "src.execution."
            "live_entry_candidate_pipeline."
            "resolve_live_entry_evidence",
            side_effect=resolve,
        ):
            result = await self.run_pipeline()

        self.assertIs(
            result.candidate,
            captured["candidate"],
        )
        self.assertIs(
            result.evidence_resolution.candidate,
            result.candidate,
        )

    async def test_evidence_pass_maps_to_pipeline_pass(
        self,
    ):
        async def resolve(
            *,
            candidate,
        ):
            return self.pass_resolution(
                candidate
            )

        with patch(
            "src.execution."
            "live_entry_candidate_pipeline."
            "resolve_live_entry_evidence",
            side_effect=resolve,
        ):
            result = await self.run_pipeline()

        self.assertEqual(
            result.status,
            PASS,
        )
        self.assertEqual(
            result.stage,
            EVIDENCE,
        )
        self.assertEqual(
            result.reasons,
            (),
        )
        self.assertTrue(
            result.evidence_ready
        )

    async def test_evidence_reject_maps_to_reject(
        self,
    ):
        async def resolve(
            *,
            candidate,
        ):
            return self.nonpass_resolution(
                candidate,
                status=EVIDENCE_REJECT,
                reason="TOKEN_UNSAFE",
            )

        with patch(
            "src.execution."
            "live_entry_candidate_pipeline."
            "resolve_live_entry_evidence",
            side_effect=resolve,
        ):
            result = await self.run_pipeline()

        self.assertEqual(
            result.status,
            REJECT,
        )
        self.assertEqual(
            result.stage,
            EVIDENCE,
        )
        self.assertEqual(
            result.reasons,
            (
                "EVIDENCE:TOKEN_UNSAFE",
            ),
        )
        self.assertFalse(
            result.evidence_ready
        )

    async def test_evidence_unknown_maps_to_unknown(
        self,
    ):
        async def resolve(
            *,
            candidate,
        ):
            return self.nonpass_resolution(
                candidate,
                status=EVIDENCE_UNKNOWN,
                reason="RPC_UNKNOWN",
            )

        with patch(
            "src.execution."
            "live_entry_candidate_pipeline."
            "resolve_live_entry_evidence",
            side_effect=resolve,
        ):
            result = await self.run_pipeline()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            EVIDENCE,
        )
        self.assertEqual(
            result.reasons,
            (
                "EVIDENCE:RPC_UNKNOWN",
            ),
        )

    async def test_evidence_exception_fails_closed(
        self,
    ):
        with patch(
            "src.execution."
            "live_entry_candidate_pipeline."
            "resolve_live_entry_evidence",
            new_callable=AsyncMock,
            side_effect=RuntimeError(
                "boom"
            ),
        ):
            result = await self.run_pipeline()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            EVIDENCE,
        )
        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_PIPELINE_"
                "EVIDENCE_EXCEPTION",
            ),
        )

    async def test_cancellation_propagates(
        self,
    ):
        with patch(
            "src.execution."
            "live_entry_candidate_pipeline."
            "resolve_live_entry_evidence",
            new_callable=AsyncMock,
            side_effect=asyncio.CancelledError(),
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await self.run_pipeline()

    async def test_invalid_evidence_contract_fails_closed(
        self,
    ):
        with patch(
            "src.execution."
            "live_entry_candidate_pipeline."
            "resolve_live_entry_evidence",
            new_callable=AsyncMock,
            return_value=object(),
        ):
            result = await self.run_pipeline()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            EVIDENCE,
        )
        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_PIPELINE_"
                "EVIDENCE_CONTRACT_INVALID",
            ),
        )

    async def test_wrong_evidence_version_fails_closed(
        self,
    ):
        async def resolve(
            *,
            candidate,
        ):
            resolution = Mock(
                spec=LiveEntryEvidenceResolution
            )
            resolution.resolution_version = (
                "wrong-version"
            )
            resolution.candidate = candidate
            resolution.status = (
                EVIDENCE_UNKNOWN
            )
            resolution.reasons = (
                "TEST",
            )
            resolution.evidence = None
            return resolution

        with patch(
            "src.execution."
            "live_entry_candidate_pipeline."
            "resolve_live_entry_evidence",
            side_effect=resolve,
        ):
            result = await self.run_pipeline()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_PIPELINE_"
                "EVIDENCE_CONTRACT_INVALID",
            ),
        )

    async def test_wrong_candidate_identity_fails_closed(
        self,
    ):
        first_candidate = None

        async def resolve(
            *,
            candidate,
        ):
            nonlocal first_candidate

            if first_candidate is None:
                first_candidate = candidate

            other = Mock(
                spec=LiveEntryEvidenceResolution
            )
            other.resolution_version = (
                LIVE_ENTRY_EVIDENCE_RESOLUTION_VERSION
            )
            other.candidate = object()
            other.status = (
                EVIDENCE_UNKNOWN
            )
            other.reasons = (
                "TEST",
            )
            other.evidence = None
            return other

        with patch(
            "src.execution."
            "live_entry_candidate_pipeline."
            "resolve_live_entry_evidence",
            side_effect=resolve,
        ):
            result = await self.run_pipeline()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_PIPELINE_"
                "EVIDENCE_CONTRACT_INVALID",
            ),
        )

    async def test_policy_stage_requires_policy_decision(
        self,
    ):
        policy = LiveEntryPolicy(
            min_probability_2x_15m=0.50,
            max_candidate_age_seconds=5,
        )

        result = await self.run_pipeline(
            policy=policy
        )

        with self.assertRaisesRegex(
            ValueError,
            (
                "^POLICY stage requires "
                "policy decision$"
            ),
        ):
            LiveEntryCandidatePipelineResult(
                pipeline_version=(
                    LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION
                ),
                status=REJECT,
                stage=POLICY,
                reasons=result.reasons,
                candidate=result.candidate,
                policy_decision=None,
                evidence_resolution=None,
            )

    async def test_evidence_stage_missing_resolution_only_allows_internal_unknown(
        self,
    ):
        async def resolve(
            *,
            candidate,
        ):
            return self.nonpass_resolution(
                candidate,
                status=EVIDENCE_UNKNOWN,
                reason="RPC_UNKNOWN",
            )

        with patch(
            "src.execution."
            "live_entry_candidate_pipeline."
            "resolve_live_entry_evidence",
            side_effect=resolve,
        ):
            result = await self.run_pipeline()

        with self.assertRaisesRegex(
            ValueError,
            (
                "^EVIDENCE stage requires "
                "evidence resolution$"
            ),
        ):
            LiveEntryCandidatePipelineResult(
                pipeline_version=(
                    LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION
                ),
                status=UNKNOWN,
                stage=EVIDENCE,
                reasons=(
                    "EVIDENCE:RPC_UNKNOWN",
                ),
                candidate=result.candidate,
                policy_decision=(
                    result.policy_decision
                ),
                evidence_resolution=None,
            )

    async def test_evidence_stage_requires_resolution(
        self,
    ):
        async def resolve(
            *,
            candidate,
        ):
            return self.nonpass_resolution(
                candidate,
                status=EVIDENCE_UNKNOWN,
                reason="RPC_UNKNOWN",
            )

        with patch(
            "src.execution."
            "live_entry_candidate_pipeline."
            "resolve_live_entry_evidence",
            side_effect=resolve,
        ):
            result = await self.run_pipeline()

        with self.assertRaisesRegex(
            ValueError,
            (
                "^EVIDENCE stage requires "
                "evidence resolution$"
            ),
        ):
            LiveEntryCandidatePipelineResult(
                pipeline_version=(
                    LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION
                ),
                status=UNKNOWN,
                stage=EVIDENCE,
                reasons=result.reasons,
                candidate=result.candidate,
                policy_decision=(
                    result.policy_decision
                ),
                evidence_resolution=None,
            )

    async def test_result_is_frozen(
        self,
    ):
        policy = LiveEntryPolicy(
            min_probability_2x_15m=0.50,
            max_candidate_age_seconds=5,
        )

        result = await self.run_pipeline(
            policy=policy
        )

        with self.assertRaises(
            FrozenInstanceError
        ):
            result.status = PASS


if __name__ == "__main__":
    unittest.main()
