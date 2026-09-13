from __future__ import annotations

import inspect
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
    sentinel,
)

from src.execution.live_buy_execution_config import (
    LIVE_BUY_EXECUTION_CONFIG_VERSION,
    LiveBuyExecutionConfig,
)
from src.execution.live_curve_state import (
    LivePumpCurveState,
)
from src.execution.live_entry_buy_adapter import (
    LIVE_ENTRY_BUY_ADAPTER_VERSION,
    LiveEntryBuyAdapterError,
    run_live_entry_buy_once,
)
from src.execution.live_entry_evidence import (
    LIVE_ENTRY_EVIDENCE_VERSION,
    LiveEntryEvidence,
)
from src.execution.live_process_owner import (
    LiveProcessOwner,
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
    TokenSafetyGateResult,
)
from src.safety.token_safety_resolver import (
    BondingCurveSnapshot,
)


MODULE = (
    "src.execution.live_entry_buy_adapter"
)

MINT = "mint-1"

WALLET = (
    "So11111111111111111111111111111111111111112"
)


class LiveEntryBuyAdapterTests(
    unittest.IsolatedAsyncioTestCase
):
    @staticmethod
    def owner():
        #
        # The adapter does not use owner internals directly.
        # Bypass construction here so these tests do not acquire the
        # real process authority lease.
        #
        return object.__new__(
            LiveProcessOwner
        )

    @staticmethod
    def candidate():
        return make_model_entry_candidate(
            entry_signature="signature-1",
            mint=MINT,
            event_user="event-wallet",
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

    @classmethod
    def evidence(
        cls,
    ):
        candidate = cls.candidate()

        snapshot = SimpleNamespace(
            mint=MINT,
            rpc_max_slot=200,
        )

        safety = TokenSafetyGateResult(
            gate_version=GATE_VERSION,
            mint=MINT,
            status=SAFETY_PASS,
            reasons=(),
            resolver_warnings=(),
            evaluated_at=1_700_000_003,
            snapshot=snapshot,
            resolution_error=None,
        )

        curve = Mock(
            spec=BondingCurveSnapshot,
            name="curve",
        )

        fee_state = LivePumpFeeState(
            resolver_version=(
                LIVE_PUMP_FEE_STATE_VERSION
            ),
            mint=MINT,
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
            rpc_slot=201,
            fetched_at=1_700_000_004.5,
        )

        live_curve = LivePumpCurveState(
            mint=MINT,
            curve=curve,
            rpc_slot=201,
            fetched_at=1_700_000_004.5,
        )

        return LiveEntryEvidence(
            evidence_version=(
                LIVE_ENTRY_EVIDENCE_VERSION
            ),
            candidate=candidate,
            safety=safety,
            fee_state=fee_state,
            live_curve=live_curve,
        )

    @staticmethod
    def execution_config():
        return LiveBuyExecutionConfig(
            config_version=(
                LIVE_BUY_EXECUTION_CONFIG_VERSION
            ),
            wallet_pubkey=WALLET,
            protected_cash_lamports=(
                50_000_000
            ),
            buy_slippage_bps=300,
            buy_base_network_fee_lamports=(
                5_000
            ),
            buy_priority_fee_lamports=(
                10_000
            ),
            buy_rent_lamports=(
                2_039_280
            ),
            exit_slippage_bps=300,
            exit_base_network_fee_lamports=(
                5_000
            ),
            exit_priority_fee_lamports=(
                10_000
            ),
            reservation_ttl_seconds=30.0,
            max_authorization_age_seconds=10.0,
            compute_unit_limit=300_000,
        )

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_ENTRY_BUY_ADAPTER_VERSION,
            "live-entry-buy-adapter-v2",
        )

    def test_public_signature_has_only_owner_evidence_and_execution_config(
        self,
    ):
        signature = inspect.signature(
            run_live_entry_buy_once
        )

        self.assertEqual(
            tuple(
                signature.parameters
            ),
            (
                "owner",
                "evidence",
                "execution_config",
            ),
        )

        for parameter in (
            signature.parameters.values()
        ):
            self.assertIs(
                parameter.kind,
                inspect.Parameter.KEYWORD_ONLY,
            )

    async def test_exact_mapping_invokes_owner_once(
        self,
    ):
        owner = self.owner()
        evidence = self.evidence()
        execution_config = (
            self.execution_config()
        )

        run_buy = AsyncMock(
            return_value=sentinel.result
        )

        with patch.object(
            LiveProcessOwner,
            "run_buy_once",
            new=run_buy,
        ):
            result = (
                await run_live_entry_buy_once(
                    owner=owner,
                    evidence=evidence,
                    execution_config=(
                        execution_config
                    ),
                )
            )

        self.assertIs(
            result,
            sentinel.result,
        )

        run_buy.assert_awaited_once_with(
            candidate=evidence.candidate,
            mint=MINT,
            wallet_pubkey=WALLET,
            protected_cash_lamports=(
                50_000_000
            ),
            live_curve=(
                evidence.live_curve
            ),
            safety=evidence.safety,
            protocol_fee_bps=95,
            creator_fee_bps=30,
            buy_slippage_bps=300,
            buy_base_network_fee_lamports=(
                5_000
            ),
            buy_priority_fee_lamports=(
                10_000
            ),
            buy_rent_lamports=(
                2_039_280
            ),
            exit_slippage_bps=300,
            exit_base_network_fee_lamports=(
                5_000
            ),
            exit_priority_fee_lamports=(
                10_000
            ),
            reservation_ttl_seconds=30.0,
            max_authorization_age_seconds=10.0,
            compute_unit_limit=300_000,
            signal_virtual_quote_reserves=(
                30_000_000_000
            ),
            signal_virtual_token_reserves=(
                1_000_000_000_000
            ),
            min_context_slot=200,
        )

    async def test_invalid_owner_fails_before_authority(
        self,
    ):
        run_buy = AsyncMock()

        with patch.object(
            LiveProcessOwner,
            "run_buy_once",
            new=run_buy,
        ):
            with self.assertRaisesRegex(
                TypeError,
                (
                    "^owner must be "
                    "LiveProcessOwner$"
                ),
            ):
                await run_live_entry_buy_once(
                    owner=object(),
                    evidence=self.evidence(),
                    execution_config=(
                        self.execution_config()
                    ),
                )

        run_buy.assert_not_awaited()

    async def test_invalid_evidence_fails_before_authority(
        self,
    ):
        owner = self.owner()
        run_buy = AsyncMock()

        with patch.object(
            LiveProcessOwner,
            "run_buy_once",
            new=run_buy,
        ):
            with self.assertRaisesRegex(
                TypeError,
                (
                    "^evidence must be "
                    "LiveEntryEvidence$"
                ),
            ):
                await run_live_entry_buy_once(
                    owner=owner,
                    evidence=object(),
                    execution_config=(
                        self.execution_config()
                    ),
                )

        run_buy.assert_not_awaited()

    async def test_tampered_evidence_version_fails_before_authority(
        self,
    ):
        owner = self.owner()
        evidence = self.evidence()

        object.__setattr__(
            evidence,
            "evidence_version",
            "unexpected-version",
        )

        run_buy = AsyncMock()

        with patch.object(
            LiveProcessOwner,
            "run_buy_once",
            new=run_buy,
        ):
            with self.assertRaisesRegex(
                TypeError,
                (
                    "^evidence must be "
                    "LiveEntryEvidence$"
                ),
            ):
                await run_live_entry_buy_once(
                    owner=owner,
                    evidence=evidence,
                    execution_config=(
                        self.execution_config()
                    ),
                )

        run_buy.assert_not_awaited()

    async def test_invalid_execution_config_fails_before_authority(
        self,
    ):
        owner = self.owner()
        run_buy = AsyncMock()

        with patch.object(
            LiveProcessOwner,
            "run_buy_once",
            new=run_buy,
        ):
            with self.assertRaisesRegex(
                TypeError,
                (
                    "^execution_config must be "
                    "LiveBuyExecutionConfig$"
                ),
            ):
                await run_live_entry_buy_once(
                    owner=owner,
                    evidence=self.evidence(),
                    execution_config=object(),
                )

        run_buy.assert_not_awaited()

    async def test_tampered_execution_config_version_fails_before_authority(
        self,
    ):
        owner = self.owner()

        execution_config = (
            self.execution_config()
        )

        object.__setattr__(
            execution_config,
            "config_version",
            "unexpected-version",
        )

        run_buy = AsyncMock()

        with patch.object(
            LiveProcessOwner,
            "run_buy_once",
            new=run_buy,
        ):
            with self.assertRaisesRegex(
                TypeError,
                (
                    "^execution_config must be "
                    "LiveBuyExecutionConfig$"
                ),
            ):
                await run_live_entry_buy_once(
                    owner=owner,
                    evidence=self.evidence(),
                    execution_config=(
                        execution_config
                    ),
                )

        run_buy.assert_not_awaited()

    async def test_component_version_mismatch_fails_before_authority(
        self,
    ):
        owner = self.owner()
        run_buy = AsyncMock()

        with (
            patch(
                f"{MODULE}."
                "LIVE_PROCESS_OWNER_VERSION",
                new="unexpected-version",
            ),
            patch.object(
                LiveProcessOwner,
                "run_buy_once",
                new=run_buy,
            ),
        ):
            with self.assertRaisesRegex(
                LiveEntryBuyAdapterError,
                (
                    "^LIVE_ENTRY_BUY_ADAPTER_"
                    "COMPONENT_VERSION_MISMATCH$"
                ),
            ):
                await run_live_entry_buy_once(
                    owner=owner,
                    evidence=self.evidence(),
                    execution_config=(
                        self.execution_config()
                    ),
                )

        run_buy.assert_not_awaited()

    async def test_owner_exception_propagates_without_retry(
        self,
    ):
        owner = self.owner()

        run_buy = AsyncMock(
            side_effect=RuntimeError(
                "owner failed"
            )
        )

        with patch.object(
            LiveProcessOwner,
            "run_buy_once",
            new=run_buy,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^owner failed$",
            ):
                await run_live_entry_buy_once(
                    owner=owner,
                    evidence=self.evidence(),
                    execution_config=(
                        self.execution_config()
                    ),
                )

        self.assertEqual(
            run_buy.await_count,
            1,
        )


if __name__ == "__main__":
    unittest.main()
