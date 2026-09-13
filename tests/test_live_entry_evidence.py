from __future__ import annotations

import asyncio
from dataclasses import (
    FrozenInstanceError,
    replace,
)
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
)

from src.execution.live_entry_evidence import (
    LIVE_ENTRY_EVIDENCE_RESOLUTION_VERSION,
    LIVE_ENTRY_EVIDENCE_VERSION,
    PASS,
    REJECT,
    UNKNOWN,
    LiveEntryEvidence,
    LiveEntryEvidenceResolution,
    resolve_live_entry_evidence,
)
from src.execution.live_pump_fee_state import (
    LIVE_PUMP_FEE_STATE_VERSION,
    LivePumpFeeState,
)
from src.execution.model_entry_candidate import (
    EXPECTED_ARTIFACT_VERSION,
    EXPECTED_MODEL_SHADOW_VERSION,
    make_model_entry_candidate,
)
from src.safety.token_safety_gate import (
    GATE_VERSION,
    PASS as SAFETY_PASS,
    REJECT as SAFETY_REJECT,
    UNKNOWN as SAFETY_UNKNOWN,
    TokenSafetyGateResult,
)
from src.safety.token_safety_resolver import (
    BondingCurveSnapshot,
)


MODULE = (
    "src.execution.live_entry_evidence"
)

MINT = "mint-1"


class LiveEntryEvidenceTests(
    unittest.IsolatedAsyncioTestCase
):
    @staticmethod
    def candidate():
        return make_model_entry_candidate(
            entry_signature="signature-1",
            mint=MINT,
            event_user="wallet-1",
            quote_mint="quote-1",
            slot=100,
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

    @staticmethod
    def safety(
        *,
        status=SAFETY_PASS,
        mint=MINT,
        snapshot_mint=MINT,
        rpc_max_slot=200,
        reasons=(),
    ):
        snapshot = None

        if status == SAFETY_PASS:
            snapshot = SimpleNamespace(
                mint=snapshot_mint,
                rpc_max_slot=rpc_max_slot,
            )

        return TokenSafetyGateResult(
            gate_version=GATE_VERSION,
            mint=mint,
            status=status,
            reasons=tuple(reasons),
            resolver_warnings=(),
            evaluated_at=1_700_000_003,
            snapshot=snapshot,
            resolution_error=None,
        )

    @staticmethod
    def fee_state(
        *,
        mint=MINT,
        rpc_slot=201,
        fetched_at=1_700_000_004.5,
    ):
        curve = Mock(
            spec=BondingCurveSnapshot,
            name="curve",
        )

        return LivePumpFeeState(
            resolver_version=(
                LIVE_PUMP_FEE_STATE_VERSION
            ),
            mint=mint,
            curve=curve,
            fee_config=Mock(
                name="fee_config"
            ),
            market_cap_lamports=100,
            selected_tier_index=0,
            selected_threshold_lamports=0,
            lp_fee_bps=5,
            protocol_fee_bps=95,
            creator_fee_bps=30,
            rpc_slot=rpc_slot,
            fetched_at=fetched_at,
        )

    def test_versions_are_locked(
        self,
    ):
        self.assertEqual(
            LIVE_ENTRY_EVIDENCE_VERSION,
            "live-entry-evidence-v1",
        )

        self.assertEqual(
            LIVE_ENTRY_EVIDENCE_RESOLUTION_VERSION,
            "live-entry-evidence-resolution-v1",
        )

    async def test_pass_binds_same_fee_state_curve_snapshot(
        self,
    ):
        candidate = self.candidate()
        safety = self.safety()
        fee_state = self.fee_state()

        with (
            patch(
                f"{MODULE}.resolve_and_gate",
                new=AsyncMock(
                    return_value=safety
                ),
            ) as safety_resolver,
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                new=AsyncMock(
                    return_value=fee_state
                ),
            ) as fee_resolver,
        ):
            result = (
                await resolve_live_entry_evidence(
                    candidate=candidate
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

        self.assertIs(
            result.candidate,
            candidate,
        )

        self.assertIs(
            result.safety,
            safety,
        )

        self.assertIsNotNone(
            result.evidence
        )

        evidence = result.evidence

        assert evidence is not None

        self.assertIs(
            evidence.candidate,
            candidate,
        )

        self.assertIs(
            evidence.safety,
            safety,
        )

        self.assertIs(
            evidence.fee_state,
            fee_state,
        )

        self.assertIs(
            evidence.live_curve.curve,
            fee_state.curve,
        )

        self.assertEqual(
            evidence.live_curve.rpc_slot,
            fee_state.rpc_slot,
        )

        self.assertEqual(
            evidence.live_curve.fetched_at,
            fee_state.fetched_at,
        )

        self.assertEqual(
            evidence.min_context_slot,
            200,
        )

        safety_resolver.assert_awaited_once_with(
            MINT
        )

        fee_resolver.assert_awaited_once_with(
            mint=MINT,
            min_context_slot=200,
        )

    async def test_safety_reject_stops_before_fee_resolution(
        self,
    ):
        candidate = self.candidate()

        safety = self.safety(
            status=SAFETY_REJECT,
            reasons=(
                "MINT_AUTHORITY_PRESENT",
            ),
        )

        fee_resolver = AsyncMock()

        with (
            patch(
                f"{MODULE}.resolve_and_gate",
                new=AsyncMock(
                    return_value=safety
                ),
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                new=fee_resolver,
            ),
        ):
            result = (
                await resolve_live_entry_evidence(
                    candidate=candidate
                )
            )

        self.assertEqual(
            result.status,
            REJECT,
        )

        self.assertEqual(
            result.reasons,
            (
                "SAFETY:MINT_AUTHORITY_PRESENT",
            ),
        )

        self.assertIsNone(
            result.evidence
        )

        self.assertIs(
            result.safety,
            safety,
        )

        fee_resolver.assert_not_awaited()

    async def test_safety_unknown_stops_before_fee_resolution(
        self,
    ):
        candidate = self.candidate()

        safety = self.safety(
            status=SAFETY_UNKNOWN,
            reasons=(
                "TOKEN_SAFETY_RESOLUTION_FAILED",
            ),
        )

        fee_resolver = AsyncMock()

        with (
            patch(
                f"{MODULE}.resolve_and_gate",
                new=AsyncMock(
                    return_value=safety
                ),
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                new=fee_resolver,
            ),
        ):
            result = (
                await resolve_live_entry_evidence(
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
                "SAFETY:"
                "TOKEN_SAFETY_RESOLUTION_FAILED",
            ),
        )

        self.assertIsNone(
            result.evidence
        )

        fee_resolver.assert_not_awaited()

    async def test_safety_exception_becomes_unknown(
        self,
    ):
        with patch(
            f"{MODULE}.resolve_and_gate",
            new=AsyncMock(
                side_effect=RuntimeError(
                    "boom"
                )
            ),
        ):
            result = (
                await resolve_live_entry_evidence(
                    candidate=self.candidate()
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_EVIDENCE_"
                "SAFETY_EXCEPTION",
            ),
        )

    async def test_malformed_safety_contract_fails_closed(
        self,
    ):
        fee_resolver = AsyncMock()

        with (
            patch(
                f"{MODULE}.resolve_and_gate",
                new=AsyncMock(
                    return_value=object()
                ),
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                new=fee_resolver,
            ),
        ):
            result = (
                await resolve_live_entry_evidence(
                    candidate=self.candidate()
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_EVIDENCE_"
                "SAFETY_CONTRACT_INVALID",
            ),
        )

        fee_resolver.assert_not_awaited()

    async def test_safety_mint_mismatch_fails_closed(
        self,
    ):
        safety = self.safety(
            mint="other-mint"
        )

        fee_resolver = AsyncMock()

        with (
            patch(
                f"{MODULE}.resolve_and_gate",
                new=AsyncMock(
                    return_value=safety
                ),
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                new=fee_resolver,
            ),
        ):
            result = (
                await resolve_live_entry_evidence(
                    candidate=self.candidate()
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        fee_resolver.assert_not_awaited()

    async def test_safety_snapshot_mint_mismatch_fails_closed(
        self,
    ):
        safety = self.safety(
            snapshot_mint=(
                "other-mint"
            )
        )

        fee_resolver = AsyncMock()

        with (
            patch(
                f"{MODULE}.resolve_and_gate",
                new=AsyncMock(
                    return_value=safety
                ),
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                new=fee_resolver,
            ),
        ):
            result = (
                await resolve_live_entry_evidence(
                    candidate=self.candidate()
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_EVIDENCE_"
                "SAFETY_SNAPSHOT_MINT_MISMATCH",
            ),
        )

        fee_resolver.assert_not_awaited()

    async def test_invalid_safety_slot_stops_before_fee_resolution(
        self,
    ):
        safety = self.safety(
            rpc_max_slot=-1
        )

        fee_resolver = AsyncMock()

        with (
            patch(
                f"{MODULE}.resolve_and_gate",
                new=AsyncMock(
                    return_value=safety
                ),
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                new=fee_resolver,
            ),
        ):
            result = (
                await resolve_live_entry_evidence(
                    candidate=self.candidate()
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_EVIDENCE_"
                "SAFETY_SLOT_INVALID",
            ),
        )

        fee_resolver.assert_not_awaited()

    async def test_fee_state_exception_becomes_unknown(
        self,
    ):
        safety = self.safety()

        with (
            patch(
                f"{MODULE}.resolve_and_gate",
                new=AsyncMock(
                    return_value=safety
                ),
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                new=AsyncMock(
                    side_effect=RuntimeError(
                        "rpc failed"
                    )
                ),
            ),
        ):
            result = (
                await resolve_live_entry_evidence(
                    candidate=self.candidate()
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_EVIDENCE_"
                "FEE_STATE_EXCEPTION",
            ),
        )

    async def test_fee_state_slot_regression_fails_closed(
        self,
    ):
        safety = self.safety(
            rpc_max_slot=200
        )

        fee_state = self.fee_state(
            rpc_slot=199
        )

        with (
            patch(
                f"{MODULE}.resolve_and_gate",
                new=AsyncMock(
                    return_value=safety
                ),
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                new=AsyncMock(
                    return_value=fee_state
                ),
            ),
        ):
            result = (
                await resolve_live_entry_evidence(
                    candidate=self.candidate()
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_EVIDENCE_"
                "FEE_STATE_SLOT_REGRESSION",
            ),
        )

    async def test_fee_state_mint_mismatch_fails_closed(
        self,
    ):
        safety = self.safety()

        fee_state = self.fee_state(
            mint="other-mint"
        )

        with (
            patch(
                f"{MODULE}.resolve_and_gate",
                new=AsyncMock(
                    return_value=safety
                ),
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                new=AsyncMock(
                    return_value=fee_state
                ),
            ),
        ):
            result = (
                await resolve_live_entry_evidence(
                    candidate=self.candidate()
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_EVIDENCE_"
                "FEE_STATE_MINT_MISMATCH",
            ),
        )

    async def test_invalid_fee_state_curve_fails_closed(
        self,
    ):
        safety = self.safety()

        fee_state = replace(
            self.fee_state(),
            curve=object(),
        )

        with (
            patch(
                f"{MODULE}.resolve_and_gate",
                new=AsyncMock(
                    return_value=safety
                ),
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                new=AsyncMock(
                    return_value=fee_state
                ),
            ),
        ):
            result = (
                await resolve_live_entry_evidence(
                    candidate=self.candidate()
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_EVIDENCE_"
                "CONSTRUCTION_FAILED",
            ),
        )

    async def test_invalid_fee_bps_fails_closed(
        self,
    ):
        safety = self.safety()

        fee_state = replace(
            self.fee_state(),
            protocol_fee_bps=-1,
        )

        with (
            patch(
                f"{MODULE}.resolve_and_gate",
                new=AsyncMock(
                    return_value=safety
                ),
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                new=AsyncMock(
                    return_value=fee_state
                ),
            ),
        ):
            result = (
                await resolve_live_entry_evidence(
                    candidate=self.candidate()
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_EVIDENCE_"
                "CONSTRUCTION_FAILED",
            ),
        )

    async def test_negative_fee_state_fetch_time_fails_closed(
        self,
    ):
        safety = self.safety()

        fee_state = replace(
            self.fee_state(),
            fetched_at=-1.0,
        )

        with (
            patch(
                f"{MODULE}.resolve_and_gate",
                new=AsyncMock(
                    return_value=safety
                ),
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                new=AsyncMock(
                    return_value=fee_state
                ),
            ),
        ):
            result = (
                await resolve_live_entry_evidence(
                    candidate=self.candidate()
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertEqual(
            result.reasons,
            (
                "LIVE_ENTRY_EVIDENCE_"
                "FEE_STATE_FETCH_TIME_INVALID",
            ),
        )

    async def test_safety_cancellation_propagates(
        self,
    ):
        with patch(
            f"{MODULE}.resolve_and_gate",
            new=AsyncMock(
                side_effect=(
                    asyncio.CancelledError
                )
            ),
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await resolve_live_entry_evidence(
                    candidate=self.candidate()
                )

    async def test_fee_state_cancellation_propagates(
        self,
    ):
        safety = self.safety()

        with (
            patch(
                f"{MODULE}.resolve_and_gate",
                new=AsyncMock(
                    return_value=safety
                ),
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                new=AsyncMock(
                    side_effect=(
                        asyncio.CancelledError
                    )
                ),
            ),
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await resolve_live_entry_evidence(
                    candidate=self.candidate()
                )

    async def test_invalid_candidate_fails_before_resolution(
        self,
    ):
        safety_resolver = AsyncMock()
        fee_resolver = AsyncMock()

        with (
            patch(
                f"{MODULE}.resolve_and_gate",
                new=safety_resolver,
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_fee_state",
                new=fee_resolver,
            ),
        ):
            with self.assertRaisesRegex(
                TypeError,
                (
                    "^candidate must be "
                    "ModelEntryCandidate$"
                ),
            ):
                await resolve_live_entry_evidence(
                    candidate=object()
                )

        safety_resolver.assert_not_awaited()
        fee_resolver.assert_not_awaited()

    def test_evidence_is_frozen(
        self,
    ):
        candidate = self.candidate()
        safety = self.safety()
        fee_state = self.fee_state()

        evidence = LiveEntryEvidence(
            evidence_version=(
                LIVE_ENTRY_EVIDENCE_VERSION
            ),
            candidate=candidate,
            safety=safety,
            fee_state=fee_state,
            live_curve=(
                __import__(
                    "src.execution.live_curve_state",
                    fromlist=[
                        "LivePumpCurveState"
                    ],
                ).LivePumpCurveState(
                    mint=MINT,
                    curve=fee_state.curve,
                    rpc_slot=fee_state.rpc_slot,
                    fetched_at=(
                        fee_state.fetched_at
                    ),
                )
            ),
        )

        with self.assertRaises(
            FrozenInstanceError
        ):
            evidence.candidate = (
                self.candidate()
            )

    def test_resolution_contract_rejects_pass_without_evidence(
        self,
    ):
        candidate = self.candidate()

        with self.assertRaises(
            ValueError
        ):
            LiveEntryEvidenceResolution(
                resolution_version=(
                    LIVE_ENTRY_EVIDENCE_RESOLUTION_VERSION
                ),
                candidate=candidate,
                status=PASS,
                reasons=(),
                safety=None,
                evidence=None,
            )


if __name__ == "__main__":
    unittest.main()
