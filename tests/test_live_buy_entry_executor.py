from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
)

from src.execution.execution_quality_gate import (
    ABORT as EXECUTION_ABORT,
    GATE_VERSION as EXECUTION_GATE_VERSION,
    PASS as EXECUTION_PASS,
    UNKNOWN as EXECUTION_UNKNOWN,
)
from src.execution.live_blockhash_context import (
    LIVE_BLOCKHASH_CONTEXT_VERSION,
)
from src.execution.live_buy_entry_executor import (
    ACCOUNT_CONTEXT,
    ADMISSION,
    AUTHORIZE,
    BLOCK,
    BLOCKHASH,
    COMPLETE,
    GLOBAL_STATE,
    LIVE_BUY_ENTRY_EXECUTOR_VERSION,
    MESSAGE,
    PREFLIGHT,
    PRE_SIGN,
    RESERVE,
    SIGN,
    SIGNED,
    UNKNOWN,
    VALIDATE,
    execute_live_buy_entry_once,
)
from src.execution.model_entry_candidate import (
    EXPECTED_ARTIFACT_VERSION,
    EXPECTED_MODEL_SHADOW_VERSION,
    make_model_entry_candidate,
)
from src.execution.live_pump_global_state import (
    LIVE_PUMP_GLOBAL_STATE_VERSION,
)
from src.execution.order_authorization import (
    AUTHORIZATION_VERSION,
    AUTHORIZE as ORDER_AUTHORIZE,
    BUY,
    DENY as ORDER_DENY,
)
from src.execution.pump_buy_v2_account_context import (
    PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION,
)
from src.execution.pump_buy_v2_execution_preflight import (
    APPROVE as PREFLIGHT_APPROVE,
    DENY as PREFLIGHT_DENY,
    UNKNOWN as PREFLIGHT_UNKNOWN,
    PUMP_BUY_V2_EXECUTION_PREFLIGHT_VERSION,
)
from src.execution.pump_buy_v2_pre_sign_validation import (
    APPROVE as PRE_SIGN_APPROVE,
    DENY as PRE_SIGN_DENY,
    UNKNOWN as PRE_SIGN_UNKNOWN,
    PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION,
)
from src.execution.pump_buy_v2_signing import (
    BLOCK as SIGNING_BLOCK,
    PASS as SIGNING_PASS,
    UNKNOWN as SIGNING_UNKNOWN,
    PUMP_BUY_V2_SIGNING_VERSION,
)
from src.execution.pump_buy_v2_unsigned_message import (
    PUMP_BUY_V2_UNSIGNED_MESSAGE_VERSION,
)
from src.portfolio.live_entry_admissions import (
    BLOCK as ADMISSION_BLOCK,
    PASS as ADMISSION_PASS,
    UNKNOWN as ADMISSION_UNKNOWN,
    LIVE_ENTRY_ADMISSION_VERSION,
    LiveEntryAdmission,
    LiveEntryAdmissionResult,
    _candidate_sha256,
)
from src.portfolio.live_pump_buy_reservation import (
    BLOCK as RESERVATION_BLOCK,
    PASS as RESERVATION_PASS,
    UNKNOWN as RESERVATION_UNKNOWN,
    LIVE_PUMP_BUY_RESERVATION_VERSION,
)
from src.portfolio.live_reservations import (
    ACTIVE,
    RESERVATION_VERSION,
)


from src.risk.risk_governor import (
    RiskPolicy,
)

from src.safety.token_safety_gate import (
    REJECT as SAFETY_REJECT,
    UNKNOWN as SAFETY_UNKNOWN,
)

MODULE = (
    "src.execution.live_buy_entry_executor"
)

_DEFAULT_POLICY = object()
_DEFAULT_SIMULATION = object()
_DEFAULT_CANDIDATE = object()


class LiveBuyEntryExecutorTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.db_path = Path(
            "/tmp/live-buy-entry-test.db"
        )

        self.wallet = (
            "wallet-test"
        )
        self.mint = (
            "mint-test"
        )

        self.reservation_id = (
            "reservation-test"
        )

        self.simulation_sha256 = (
            "11" * 32
        )

        self.message_sha256 = (
            "22" * 32
        )

        self.transaction_sha256 = (
            "33" * 32
        )

        self.curve_slot = 100
        self.global_slot = 120
        self.blockhash_slot = 130

        self.compute_unit_limit = 250_000

        self.signer = object()

        self.reservation = SimpleNamespace(
            reservation_id=(
                self.reservation_id
            ),
            reservation_version=(
                RESERVATION_VERSION
            ),
            status=ACTIVE,
            side=BUY,
            mint=self.mint,
            wallet_pubkey=self.wallet,
            spend_lamports=1_000_000,
            wallet_cost_lamports=(
                1_020_000
            ),
            risk_governor_version=(
                "risk-governor-v1"
            ),
            risk_simulation_sha256=(
                self.simulation_sha256
            ),
        )

    def reservation_result(
        self,
        *,
        status=RESERVATION_PASS,
        reasons=(),
        reservation=None,
        version=(
            LIVE_PUMP_BUY_RESERVATION_VERSION
        ),
    ):
        if (
            reservation is None
            and status == RESERVATION_PASS
        ):
            reservation = self.reservation

        decision = None

        if status == RESERVATION_PASS:
            decision = SimpleNamespace(
                status=RESERVATION_PASS,
                reasons=(),
                reservation=reservation,
            )

        return SimpleNamespace(
            resolver_version=version,
            status=status,
            reasons=tuple(
                reasons
            ),
            wallet_pubkey=self.wallet,
            mint=self.mint,
            reservation_decision=decision,
        )

    def live_curve_result(
        self,
    ):
        return SimpleNamespace(
            mint=self.mint,
            rpc_slot=self.curve_slot,
            fetched_at=123.0,
            curve=SimpleNamespace(
                virtual_quote_reserves=(
                    50_000_000_000
                ),
                virtual_token_reserves=(
                    100_000_000_000
                ),
                real_quote_reserves=(
                    40_000_000_000
                ),
                real_token_reserves=(
                    80_000_000_000
                ),
                complete=False,
            ),
        )

    def safety_result(
        self,
    ):
        return SimpleNamespace(
            status="PASS",
            reasons=(),
            snapshot=SimpleNamespace(
                mint=self.mint,
            ),
        )

    def execution_result(
        self,
        *,
        status=EXECUTION_PASS,
        reasons=(),
        spend_lamports=None,
        simulation=_DEFAULT_SIMULATION,
        **updates,
    ):
        if spend_lamports is None:
            spend_lamports = (
                self.reservation.spend_lamports
            )

        if simulation is _DEFAULT_SIMULATION:
            simulation = object()

        data = dict(
            gate_version=(
                EXECUTION_GATE_VERSION
            ),
            mint=self.mint,
            status=status,
            reasons=tuple(
                reasons
            ),
            spendable_quote_in=(
                spend_lamports
            ),
            protocol_fee_bps=100,
            creator_fee_bps=50,
            slippage_bps=500,
            simulation=simulation,
        )

        data.update(
            updates
        )

        return SimpleNamespace(
            **data
        )

    def authorization(
        self,
        *,
        status=ORDER_AUTHORIZE,
        reasons=(),
        valid=True,
        **updates,
    ):
        data = dict(
            authorization_version=(
                AUTHORIZATION_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            mint=self.mint,
            side=BUY,
            wallet_pubkey=self.wallet,
            token_amount=5_000_000,
            max_sol_cost=1_000_000,
            spend_lamports=(
                self.reservation
                .spend_lamports
            ),
            wallet_cost_lamports=(
                self.reservation
                .wallet_cost_lamports
            ),
            reservation_id=(
                self.reservation_id
            ),
            reservation_version=(
                RESERVATION_VERSION
            ),
            risk_governor_version=(
                self.reservation
                .risk_governor_version
            ),
            simulation_sha256=(
                self.simulation_sha256
            ),
            curve_rpc_slot=(
                self.curve_slot
            ),
            is_valid=lambda: valid,
        )

        data.update(
            updates
        )

        return SimpleNamespace(
            **data
        )

    def global_state(
        self,
        *,
        version=(
            LIVE_PUMP_GLOBAL_STATE_VERSION
        ),
        rpc_slot=None,
    ):
        if rpc_slot is None:
            rpc_slot = self.global_slot

        return SimpleNamespace(
            resolver_version=version,
            rpc_slot=rpc_slot,
        )

    def context(
        self,
        *,
        version=(
            PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION
        ),
        global_rpc_slot=None,
        **updates,
    ):
        if global_rpc_slot is None:
            global_rpc_slot = (
                self.global_slot
            )

        data = dict(
            resolver_version=version,
            authorization_version=(
                AUTHORIZATION_VERSION
            ),
            reservation_id=(
                self.reservation_id
            ),
            simulation_sha256=(
                self.simulation_sha256
            ),
            global_rpc_slot=(
                global_rpc_slot
            ),
            user=self.wallet,
            amount=5_000_000,
            max_sol_cost=1_000_000,
        )

        data.update(
            updates
        )

        return SimpleNamespace(
            **data
        )

    def blockhash(
        self,
        *,
        version=(
            LIVE_BLOCKHASH_CONTEXT_VERSION
        ),
        min_context_slot=None,
        rpc_slot=None,
    ):
        if min_context_slot is None:
            min_context_slot = max(
                self.curve_slot,
                self.global_slot,
            )

        if rpc_slot is None:
            rpc_slot = self.blockhash_slot

        return SimpleNamespace(
            resolver_version=version,
            blockhash=(
                "blockhash-test"
            ),
            last_valid_block_height=500,
            rpc_slot=rpc_slot,
            min_context_slot=(
                min_context_slot
            ),
        )

    def message(
        self,
        *,
        version=(
            PUMP_BUY_V2_UNSIGNED_MESSAGE_VERSION
        ),
        **updates,
    ):
        data = dict(
            builder_version=version,
            authorization_version=(
                AUTHORIZATION_VERSION
            ),
            account_context_version=(
                PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION
            ),
            reservation_id=(
                self.reservation_id
            ),
            simulation_sha256=(
                self.simulation_sha256
            ),
            payer=self.wallet,
            blockhash_context_version=(
                LIVE_BLOCKHASH_CONTEXT_VERSION
            ),
            recent_blockhash=(
                "blockhash-test"
            ),
            last_valid_block_height=500,
            blockhash_rpc_slot=(
                self.blockhash_slot
            ),
            compute_unit_limit=(
                self.compute_unit_limit
            ),
            message_sha256=(
                self.message_sha256
            ),
        )

        data.update(
            updates
        )

        return SimpleNamespace(
            **data
        )

    def pre_sign(
        self,
        *,
        status=PRE_SIGN_APPROVE,
        reasons=(),
        version=(
            PUMP_BUY_V2_PRE_SIGN_VALIDATION_VERSION
        ),
    ):
        return SimpleNamespace(
            validator_version=version,
            status=status,
            reasons=tuple(
                reasons
            ),
        )

    def preflight(
        self,
        *,
        status=PREFLIGHT_APPROVE,
        reasons=(),
        version=(
            PUMP_BUY_V2_EXECUTION_PREFLIGHT_VERSION
        ),
    ):
        return SimpleNamespace(
            validator_version=version,
            status=status,
            reasons=tuple(
                reasons
            ),
        )

    def signing(
        self,
        *,
        status=SIGNING_PASS,
        reasons=(),
        durable=True,
        version=(
            PUMP_BUY_V2_SIGNING_VERSION
        ),
        **updates,
    ):
        data = dict(
            signer_version=version,
            status=status,
            reasons=tuple(
                reasons
            ),
            is_durably_signed=durable,
            reservation_id=(
                self.reservation_id
            ),
            signer_pubkey=self.wallet,
            transaction_signature=(
                "signature-test"
            ),
            message_sha256=(
                self.message_sha256
            ),
            signed_transaction_sha256=(
                self.transaction_sha256
            ),
            signed_at=123.0,
            blockhash_context_version=(
                LIVE_BLOCKHASH_CONTEXT_VERSION
            ),
            last_valid_block_height=500,
            blockhash_rpc_slot=(
                self.blockhash_slot
            ),
        )

        data.update(
            updates
        )

        return SimpleNamespace(
            **data
        )

    def model_candidate(
        self,
        *,
        mint=None,
        quote_reserves=50_000_000_000,
        token_reserves=100_000_000_000,
    ):
        if mint is None:
            mint = self.mint

        return make_model_entry_candidate(
            entry_signature="entry-signature-test",
            mint=mint,
            event_user="event-user-test",
            quote_mint="quote-mint-test",
            slot=90,
            trade_timestamp=1_700_000_000,
            observed_at=1_700_000_001,
            predicted_at=1_700_000_002,
            model_shadow_version=(
                EXPECTED_MODEL_SHADOW_VERSION
            ),
            artifact_version=(
                EXPECTED_ARTIFACT_VERSION
            ),
            artifact_sha256="ab" * 32,
            model_eligible=True,
            probability_2x_15m=0.42,
            signal_virtual_quote_reserves=(
                quote_reserves
            ),
            signal_virtual_token_reserves=(
                token_reserves
            ),
        )

    def admission_result(
        self,
        *,
        candidate=None,
        status=ADMISSION_PASS,
        reasons=(),
        changed=True,
        version=LIVE_ENTRY_ADMISSION_VERSION,
    ):
        if candidate is None:
            candidate = self.model_candidate()

        digest = _candidate_sha256(
            candidate
        )

        admission = LiveEntryAdmission(
            admission_version=(
                LIVE_ENTRY_ADMISSION_VERSION
            ),
            candidate_version=(
                candidate.candidate_version
            ),
            candidate_sha256=digest,
            entry_signature=(
                candidate.entry_signature
            ),
            mint=candidate.mint,
            event_user=candidate.event_user,
            quote_mint=candidate.quote_mint,
            slot=candidate.slot,
            trade_timestamp=(
                candidate.trade_timestamp
            ),
            observed_at=(
                candidate.observed_at
            ),
            predicted_at=(
                candidate.predicted_at
            ),
            model_shadow_version=(
                candidate.model_shadow_version
            ),
            artifact_version=(
                candidate.artifact_version
            ),
            artifact_sha256=(
                candidate.artifact_sha256
            ),
            model_eligible=(
                candidate.model_eligible
            ),
            probability_2x_15m=(
                candidate.probability_2x_15m
            ),
            signal_virtual_quote_reserves=(
                candidate
                .signal_virtual_quote_reserves
            ),
            signal_virtual_token_reserves=(
                candidate
                .signal_virtual_token_reserves
            ),
            admitted_at=123.0,
        )

        return LiveEntryAdmissionResult(
            resolver_version=version,
            status=status,
            reasons=tuple(reasons),
            entry_signature=(
                candidate.entry_signature
            ),
            candidate_sha256=digest,
            admission=admission,
            changed=changed,
        )

    @contextmanager
    def orchestration(
        self,
        *,
        admission=None,
        reservation=None,
        execution=None,
        authorization=None,
        global_state=None,
        context=None,
        blockhash=None,
        message=None,
        pre_sign=None,
        preflight=None,
        signing=None,
    ):
        if admission is None:
            admission = self.admission_result()

        if reservation is None:
            reservation = (
                self.reservation_result()
            )

        if execution is None:
            execution = (
                self.execution_result()
            )

        if authorization is None:
            authorization = (
                self.authorization()
            )

        if global_state is None:
            global_state = (
                self.global_state()
            )

        if context is None:
            context = self.context()

        if blockhash is None:
            blockhash = self.blockhash()

        if message is None:
            message = self.message()

        if pre_sign is None:
            pre_sign = self.pre_sign()

        if preflight is None:
            preflight = self.preflight()

        if signing is None:
            signing = self.signing()

        mocks = {
            "admission": Mock(
                return_value=admission
            ),
            "reserve": AsyncMock(
                return_value=reservation
            ),
            "execution": Mock(
                return_value=execution
            ),
            "authorize": Mock(
                return_value=authorization
            ),
            "global": AsyncMock(
                return_value=global_state
            ),
            "context": Mock(
                return_value=context
            ),
            "blockhash": AsyncMock(
                return_value=blockhash
            ),
            "message": Mock(
                return_value=message
            ),
            "pre_sign": AsyncMock(
                return_value=pre_sign
            ),
            "preflight": AsyncMock(
                return_value=preflight
            ),
            "sign": AsyncMock(
                return_value=signing
            ),
        }

        with (
            patch(
                f"{MODULE}.acquire_live_entry_admission",
                new=mocks["admission"],
            ),
            patch(
                f"{MODULE}.reserve_live_pump_buy",
                new=mocks["reserve"],
            ),
            patch(
                f"{MODULE}.evaluate_execution_quality",
                new=mocks["execution"],
            ),
            patch(
                f"{MODULE}.authorize_pump_buy",
                new=mocks["authorize"],
            ),
            patch(
                f"{MODULE}.resolve_live_pump_global_state",
                new=mocks["global"],
            ),
            patch(
                f"{MODULE}.resolve_pump_buy_v2_account_context",
                new=mocks["context"],
            ),
            patch(
                f"{MODULE}.resolve_live_blockhash_context",
                new=mocks["blockhash"],
            ),
            patch(
                f"{MODULE}.build_unsigned_pump_buy_v2_message",
                new=mocks["message"],
            ),
            patch(
                f"{MODULE}.validate_pump_buy_v2_pre_sign",
                new=mocks["pre_sign"],
            ),
            patch(
                f"{MODULE}.preflight_pump_buy_v2_execution",
                new=mocks["preflight"],
            ),
            patch(
                f"{MODULE}.sign_and_bind_pump_buy_v2",
                new=mocks["sign"],
            ),
        ):
            yield mocks

    async def invoke(
        self,
        *,
        signer=None,
        candidate=_DEFAULT_CANDIDATE,
        compute_unit_limit=None,
        max_authorization_age_seconds=30.0,
        policy=_DEFAULT_POLICY,
        signal_virtual_quote_reserves=(
            50_000_000_000
        ),
        signal_virtual_token_reserves=(
            100_000_000_000
        ),
        live_curve=None,
        safety=None,
    ):
        if signer is None:
            signer = self.signer

        if candidate is _DEFAULT_CANDIDATE:
            candidate = self.model_candidate()

        if compute_unit_limit is None:
            compute_unit_limit = (
                self.compute_unit_limit
            )

        if policy is _DEFAULT_POLICY:
            policy = RiskPolicy()

        if live_curve is None:
            live_curve = (
                self.live_curve_result()
            )

        if safety is None:
            safety = (
                self.safety_result()
            )

        return await execute_live_buy_entry_once(
            mint=self.mint,
            wallet_pubkey=self.wallet,
            protected_cash_lamports=0,
            live_curve=live_curve,
            safety=safety,
              candidate=candidate,
            signal_virtual_quote_reserves=(
                signal_virtual_quote_reserves
            ),
            signal_virtual_token_reserves=(
                signal_virtual_token_reserves
            ),
            protocol_fee_bps=100,
            creator_fee_bps=50,
            buy_slippage_bps=500,
            buy_base_network_fee_lamports=5_000,
            buy_priority_fee_lamports=7_000,
            buy_rent_lamports=2_000,
            exit_slippage_bps=500,
            exit_base_network_fee_lamports=5_000,
            exit_priority_fee_lamports=7_000,
            reservation_ttl_seconds=30.0,
            max_authorization_age_seconds=(
                max_authorization_age_seconds
            ),
            compute_unit_limit=(
                compute_unit_limit
            ),
            signer=signer,
            policy=policy,
            min_context_slot=90,
            db_path=self.db_path,
        )

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_BUY_ENTRY_EXECUTOR_VERSION,
            "live-buy-entry-executor-v3",
        )

    async def test_signer_required_before_reservation(
        self,
    ):
        reserve = AsyncMock()

        with patch(
            f"{MODULE}.reserve_live_pump_buy",
            new=reserve,
        ):
            result = (
                await execute_live_buy_entry_once(
                    mint=self.mint,
                    wallet_pubkey=self.wallet,
                    protected_cash_lamports=0,
                    live_curve=(
                        self.live_curve_result()
                    ),
                    safety=(
                        self.safety_result()
                    ),
                    signal_virtual_quote_reserves=(
                        50_000_000_000
                    ),
                    signal_virtual_token_reserves=(
                        100_000_000_000
                    ),
                    protocol_fee_bps=100,
                    creator_fee_bps=50,
                    buy_slippage_bps=500,
                    buy_base_network_fee_lamports=5_000,
                    buy_priority_fee_lamports=7_000,
                    buy_rent_lamports=2_000,
                    exit_slippage_bps=500,
                    exit_base_network_fee_lamports=5_000,
                    exit_priority_fee_lamports=7_000,
                    reservation_ttl_seconds=30.0,
                    max_authorization_age_seconds=30.0,
                    compute_unit_limit=250_000,
                    signer=None,
                    policy=RiskPolicy(),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            VALIDATE,
        )
        self.assertIn(
            "LIVE_BUY_SIGNER_REQUIRED",
            result.reasons,
        )
        reserve.assert_not_awaited()

    async def test_invalid_compute_limit_fails_before_reservation(
        self,
    ):
        reserve = AsyncMock()

        with patch(
            f"{MODULE}.reserve_live_pump_buy",
            new=reserve,
        ):
            result = await self.invoke(
                compute_unit_limit=0
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            VALIDATE,
        )
        reserve.assert_not_awaited()

    async def test_explicit_policy_required_before_reservation(
        self,
    ):
        reserve = AsyncMock()

        with patch(
            f"{MODULE}.reserve_live_pump_buy",
            new=reserve,
        ):
            result = await self.invoke(
                policy=None
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            VALIDATE,
        )
        self.assertIn(
            "LIVE_BUY_POLICY_REQUIRED",
            result.reasons,
        )

        reserve.assert_not_awaited()

    async def test_invalid_signal_reserves_fail_before_reservation(
        self,
    ):
        reserve = AsyncMock()

        with patch(
            f"{MODULE}.reserve_live_pump_buy",
            new=reserve,
        ):
            result = await self.invoke(
                signal_virtual_quote_reserves=0
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            VALIDATE,
        )
        self.assertIn(
            "LIVE_BUY_SIGNAL_RESERVES_INVALID",
            result.reasons,
        )

        reserve.assert_not_awaited()

    async def test_rejected_safety_blocks_before_reservation(
        self,
    ):
        reserve = AsyncMock()

        safety = SimpleNamespace(
            status=SAFETY_REJECT,
            reasons=("ACTIVE_MINT_AUTHORITY",),
            snapshot=SimpleNamespace(
                mint=self.mint,
            ),
        )

        with patch(
            f"{MODULE}.reserve_live_pump_buy",
            new=reserve,
        ):
            result = await self.invoke(
                safety=safety
            )

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertEqual(
            result.stage,
            VALIDATE,
        )
        self.assertIn(
            "LIVE_BUY_SAFETY_REJECTED",
            result.reasons,
        )
        self.assertIn(
            "ACTIVE_MINT_AUTHORITY",
            result.reasons,
        )

        reserve.assert_not_awaited()

    async def test_unknown_safety_fails_before_reservation(
        self,
    ):
        reserve = AsyncMock()

        safety = SimpleNamespace(
            status=SAFETY_UNKNOWN,
            reasons=(
                "TOKEN_SAFETY_RESOLUTION_FAILED",
            ),
            snapshot=None,
        )

        with patch(
            f"{MODULE}.reserve_live_pump_buy",
            new=reserve,
        ):
            result = await self.invoke(
                safety=safety
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            VALIDATE,
        )
        self.assertIn(
            "LIVE_BUY_SAFETY_UNKNOWN",
            result.reasons,
        )
        self.assertIn(
            "TOKEN_SAFETY_RESOLUTION_FAILED",
            result.reasons,
        )

        reserve.assert_not_awaited()

    async def test_invalid_candidate_fails_before_admission_and_reservation(
        self,
    ):
        with self.orchestration() as mocks:
            result = await self.invoke(
                candidate=object()
            )

        self.assertEqual(result.status, UNKNOWN)
        self.assertEqual(result.stage, VALIDATE)
        self.assertEqual(
            result.reasons,
            (
                "LIVE_BUY_MODEL_ENTRY_CANDIDATE_INVALID",
            ),
        )

        mocks["admission"].assert_not_called()
        mocks["reserve"].assert_not_awaited()

    async def test_candidate_mint_mismatch_fails_before_admission(
        self,
    ):
        candidate = self.model_candidate(
            mint="different-mint"
        )

        with self.orchestration() as mocks:
            result = await self.invoke(
                candidate=candidate
            )

        self.assertEqual(result.status, UNKNOWN)
        self.assertEqual(result.stage, VALIDATE)
        self.assertEqual(
            result.reasons,
            (
                "LIVE_BUY_CANDIDATE_MINT_MISMATCH",
            ),
        )

        mocks["admission"].assert_not_called()
        mocks["reserve"].assert_not_awaited()

    async def test_candidate_reserves_mismatch_fails_before_admission(
        self,
    ):
        candidate = self.model_candidate(
            quote_reserves=50_000_000_001
        )

        with self.orchestration() as mocks:
            result = await self.invoke(
                candidate=candidate
            )

        self.assertEqual(result.status, UNKNOWN)
        self.assertEqual(result.stage, VALIDATE)
        self.assertEqual(
            result.reasons,
            (
                "LIVE_BUY_CANDIDATE_RESERVES_MISMATCH",
            ),
        )

        mocks["admission"].assert_not_called()
        mocks["reserve"].assert_not_awaited()

    async def test_admission_block_stops_before_reservation(
        self,
    ):
        candidate = self.model_candidate()

        admission = self.admission_result(
            candidate=candidate,
            status=ADMISSION_BLOCK,
            reasons=(
                "LIVE_ENTRY_ALREADY_ADMITTED",
            ),
            changed=False,
        )

        with self.orchestration(
            admission=admission
        ) as mocks:
            result = await self.invoke(
                candidate=candidate
            )

        self.assertEqual(result.status, BLOCK)
        self.assertEqual(result.stage, ADMISSION)
        self.assertEqual(
            result.reasons,
            (
                "LIVE_BUY_ENTRY_ADMISSION_BLOCK",
                "LIVE_ENTRY_ALREADY_ADMITTED",
            ),
        )

        mocks[
            "admission"
        ].assert_called_once_with(
            candidate=candidate,
            db_path=self.db_path,
        )
        mocks["reserve"].assert_not_awaited()

    async def test_admission_unknown_stops_before_reservation(
        self,
    ):
        candidate = self.model_candidate()

        admission = self.admission_result(
            candidate=candidate,
            status=ADMISSION_UNKNOWN,
            reasons=(
                "LIVE_ENTRY_ADMISSION_DATABASE_ERROR",
            ),
            changed=False,
        )

        with self.orchestration(
            admission=admission
        ) as mocks:
            result = await self.invoke(
                candidate=candidate
            )

        self.assertEqual(result.status, UNKNOWN)
        self.assertEqual(result.stage, ADMISSION)
        self.assertEqual(
            result.reasons,
            (
                "LIVE_BUY_ENTRY_ADMISSION_UNKNOWN",
                "LIVE_ENTRY_ADMISSION_DATABASE_ERROR",
            ),
        )

        mocks["reserve"].assert_not_awaited()

    async def test_new_exact_admission_precedes_reservation(
        self,
    ):
        candidate = self.model_candidate()

        with self.orchestration() as mocks:
            result = await self.invoke(
                candidate=candidate
            )

        self.assertEqual(result.status, SIGNED)

        mocks[
            "admission"
        ].assert_called_once_with(
            candidate=candidate,
            db_path=self.db_path,
        )
        mocks["reserve"].assert_awaited_once()

    async def test_pass_admission_must_be_new_and_exact(
        self,
    ):
        candidate = self.model_candidate()

        admission = self.admission_result(
            candidate=candidate,
            changed=False,
        )

        with self.orchestration(
            admission=admission
        ) as mocks:
            result = await self.invoke(
                candidate=candidate
            )

        self.assertEqual(result.status, UNKNOWN)
        self.assertEqual(result.stage, ADMISSION)
        self.assertEqual(
            result.reasons,
            (
                "LIVE_BUY_ENTRY_ADMISSION_PASS_INVALID",
            ),
        )

        mocks["reserve"].assert_not_awaited()

    async def test_admission_exception_fails_closed_before_reservation(
        self,
    ):
        candidate = self.model_candidate()

        with self.orchestration() as mocks:
            mocks[
                "admission"
            ].side_effect = RuntimeError(
                "database exploded"
            )

            result = await self.invoke(
                candidate=candidate
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            ADMISSION,
        )
        self.assertEqual(
            result.reasons,
            (
                "LIVE_BUY_ENTRY_ADMISSION_EXCEPTION",
            ),
        )

        mocks["reserve"].assert_not_awaited()

    async def test_admission_version_mismatch_fails_closed_before_reservation(
        self,
    ):
        candidate = self.model_candidate()

        admission = self.admission_result(
            candidate=candidate,
            version="wrong-admission-version",
        )

        with self.orchestration(
            admission=admission
        ) as mocks:
            result = await self.invoke(
                candidate=candidate
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            ADMISSION,
        )
        self.assertEqual(
            result.reasons,
            (
                "LIVE_BUY_ENTRY_ADMISSION_CONTRACT_INVALID",
            ),
        )

        mocks["reserve"].assert_not_awaited()

    async def test_admission_unknown_with_changed_true_is_invalid(
        self,
    ):
        candidate = self.model_candidate()

        admission = self.admission_result(
            candidate=candidate,
            status=ADMISSION_UNKNOWN,
            reasons=(
                "LIVE_ENTRY_ADMISSION_DATABASE_ERROR",
            ),
            changed=True,
        )

        with self.orchestration(
            admission=admission
        ) as mocks:
            result = await self.invoke(
                candidate=candidate
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            ADMISSION,
        )
        self.assertEqual(
            result.reasons,
            (
                "LIVE_BUY_ENTRY_ADMISSION_UNKNOWN_INVALID",
            ),
        )

        mocks["reserve"].assert_not_awaited()

    async def test_unrecognized_admission_status_fails_closed(
        self,
    ):
        candidate = self.model_candidate()

        admission = self.admission_result(
            candidate=candidate,
            status="SURPRISE",
            changed=False,
        )

        with self.orchestration(
            admission=admission
        ) as mocks:
            result = await self.invoke(
                candidate=candidate
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            ADMISSION,
        )
        self.assertEqual(
            result.reasons,
            (
                "LIVE_BUY_ENTRY_ADMISSION_STATUS_INVALID",
            ),
        )

        mocks["reserve"].assert_not_awaited()

    async def test_admission_identity_mismatch_fails_closed_before_reservation(
        self,
    ):
        candidate = self.model_candidate()

        conflicting_candidate = (
            self.model_candidate(
                mint="conflicting-admission-mint"
            )
        )

        admission = self.admission_result(
            candidate=conflicting_candidate,
        )

        with self.orchestration(
            admission=admission
        ) as mocks:
            result = await self.invoke(
                candidate=candidate
            )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            ADMISSION,
        )
        self.assertEqual(
            result.reasons,
            (
                "LIVE_BUY_ENTRY_ADMISSION_PASS_INVALID",
            ),
        )

        mocks["reserve"].assert_not_awaited()

    async def test_execution_quality_uses_exact_reserved_spend(
        self,
    ):
        with self.orchestration() as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            SIGNED,
        )

        execution_kwargs = (
            mocks["execution"]
            .call_args
            .kwargs
        )

        self.assertEqual(
            execution_kwargs[
                "spendable_quote_in"
            ],
            self.reservation.spend_lamports,
        )

        authorize_kwargs = (
            mocks["authorize"]
            .call_args
            .kwargs
        )

        self.assertEqual(
            authorize_kwargs[
                "requested_spend_lamports"
            ],
            self.reservation.spend_lamports,
        )

        self.assertIs(
            authorize_kwargs["execution"],
            mocks["execution"].return_value,
        )

    async def test_execution_abort_blocks_before_authorization(
        self,
    ):
        execution = self.execution_result(
            status=EXECUTION_ABORT,
            reasons=(
                "OWN_PRICE_IMPACT_TOO_HIGH",
            ),
        )

        with self.orchestration(
            execution=execution
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertEqual(
            result.stage,
            "EXECUTION",
        )
        self.assertIn(
            "OWN_PRICE_IMPACT_TOO_HIGH",
            result.reasons,
        )

        mocks[
            "authorize"
        ].assert_not_called()

    async def test_execution_unknown_stops_before_authorization(
        self,
    ):
        execution = self.execution_result(
            status=EXECUTION_UNKNOWN,
            reasons=(
                "LIVE_CURVE_STATE_STALE",
            ),
        )

        with self.orchestration(
            execution=execution
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            "EXECUTION",
        )
        self.assertIn(
            "LIVE_CURVE_STATE_STALE",
            result.reasons,
        )

        mocks[
            "authorize"
        ].assert_not_called()

    async def test_execution_binding_mismatch_fails_closed(
        self,
    ):
        execution = self.execution_result(
            spend_lamports=(
                self.reservation
                .spend_lamports
                + 1
            ),
        )

        with self.orchestration(
            execution=execution
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            "EXECUTION",
        )
        self.assertIn(
            "LIVE_BUY_EXECUTION_BINDING_MISMATCH",
            result.reasons,
        )

        mocks[
            "authorize"
        ].assert_not_called()

    async def test_execution_pass_without_simulation_fails_closed(
        self,
    ):
        execution = self.execution_result(
            status=EXECUTION_PASS,
            simulation=None,
        )

        with self.orchestration(
            execution=execution
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            "EXECUTION",
        )
        self.assertIn(
            "LIVE_BUY_EXECUTION_PASS_SIMULATION_MISSING",
            result.reasons,
        )

        mocks[
            "authorize"
        ].assert_not_called()

    async def test_reservation_block_stops_pipeline(
        self,
    ):
        blocked = self.reservation_result(
            status=RESERVATION_BLOCK,
            reasons=("RISK_BLOCK",),
        )

        with self.orchestration(
            reservation=blocked
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertEqual(
            result.stage,
            RESERVE,
        )
        self.assertEqual(
            result.reasons,
            ("RISK_BLOCK",),
        )
        mocks["authorize"].assert_not_called()

    async def test_reservation_unknown_stops_pipeline(
        self,
    ):
        unknown = self.reservation_result(
            status=RESERVATION_UNKNOWN,
            reasons=("RISK_UNKNOWN",),
        )

        with self.orchestration(
            reservation=unknown
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            RESERVE,
        )
        mocks["authorize"].assert_not_called()

    async def test_malformed_pass_reservation_fails_closed(
        self,
    ):
        malformed = self.reservation_result(
            reservation=SimpleNamespace(
                reservation_id="",
                reservation_version=(
                    RESERVATION_VERSION
                ),
            )
        )

        with self.orchestration(
            reservation=malformed
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            RESERVE,
        )
        mocks["authorize"].assert_not_called()

    async def test_authorization_deny_leaves_pipeline_before_global(
        self,
    ):
        denied = self.authorization(
            status=ORDER_DENY,
            reasons=("ENTRY_DENIED",),
        )

        with self.orchestration(
            authorization=denied
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertEqual(
            result.stage,
            AUTHORIZE,
        )
        self.assertEqual(
            result.reservation_id,
            self.reservation_id,
        )
        mocks["global"].assert_not_awaited()

    async def test_authorization_binding_mismatch_fails_closed(
        self,
    ):
        authorization = self.authorization(
            wallet_pubkey="other-wallet"
        )

        with self.orchestration(
            authorization=authorization
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            AUTHORIZE,
        )
        mocks["global"].assert_not_awaited()

    async def test_global_state_must_not_predate_curve(
        self,
    ):
        stale = self.global_state(
            rpc_slot=99
        )

        with self.orchestration(
            global_state=stale
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            GLOBAL_STATE,
        )

        mocks["global"].assert_awaited_once_with(
            min_context_slot=(
                self.curve_slot
            )
        )

        mocks["context"].assert_not_called()

    async def test_account_context_binding_mismatch_stops_before_blockhash(
        self,
    ):
        context = self.context(
            user="wrong-wallet"
        )

        with self.orchestration(
            context=context
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            ACCOUNT_CONTEXT,
        )
        mocks["blockhash"].assert_not_awaited()

    async def test_blockhash_uses_strongest_curve_and_global_slot(
        self,
    ):
        with self.orchestration() as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            SIGNED,
        )

        mocks["global"].assert_awaited_once_with(
            min_context_slot=(
                self.curve_slot
            )
        )

        mocks["blockhash"].assert_awaited_once_with(
            min_context_slot=max(
                self.curve_slot,
                self.global_slot,
            )
        )

    async def test_blockhash_binding_mismatch_stops_before_message(
        self,
    ):
        blockhash = self.blockhash(
            min_context_slot=119
        )

        with self.orchestration(
            blockhash=blockhash
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            BLOCKHASH,
        )
        mocks["message"].assert_not_called()

    async def test_message_binding_mismatch_stops_before_pre_sign(
        self,
    ):
        message = self.message(
            payer="wrong-wallet"
        )

        with self.orchestration(
            message=message
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            MESSAGE,
        )
        mocks["pre_sign"].assert_not_awaited()

    async def test_pre_sign_deny_never_preflights_or_signs(
        self,
    ):
        pre_sign = self.pre_sign(
            status=PRE_SIGN_DENY,
            reasons=("FEE_DENY",),
        )

        with self.orchestration(
            pre_sign=pre_sign
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertEqual(
            result.stage,
            PRE_SIGN,
        )
        mocks["preflight"].assert_not_awaited()
        mocks["sign"].assert_not_awaited()

    async def test_pre_sign_unknown_never_preflights_or_signs(
        self,
    ):
        pre_sign = self.pre_sign(
            status=PRE_SIGN_UNKNOWN,
            reasons=("RPC_UNKNOWN",),
        )

        with self.orchestration(
            pre_sign=pre_sign
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            PRE_SIGN,
        )
        mocks["preflight"].assert_not_awaited()
        mocks["sign"].assert_not_awaited()

    async def test_preflight_deny_never_signs(
        self,
    ):
        preflight = self.preflight(
            status=PREFLIGHT_DENY,
            reasons=("SIMULATION_DENY",),
        )

        with self.orchestration(
            preflight=preflight
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertEqual(
            result.stage,
            PREFLIGHT,
        )
        mocks["sign"].assert_not_awaited()

    async def test_preflight_unknown_never_signs(
        self,
    ):
        preflight = self.preflight(
            status=PREFLIGHT_UNKNOWN,
            reasons=("SIMULATION_UNKNOWN",),
        )

        with self.orchestration(
            preflight=preflight
        ) as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            PREFLIGHT,
        )
        mocks["sign"].assert_not_awaited()

    async def test_signing_block_propagates_without_submission(
        self,
    ):
        signing = self.signing(
            status=SIGNING_BLOCK,
            reasons=("SIGN_BLOCK",),
            durable=False,
        )

        with self.orchestration(
            signing=signing
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertEqual(
            result.stage,
            SIGN,
        )

    async def test_signing_unknown_propagates(
        self,
    ):
        signing = self.signing(
            status=SIGNING_UNKNOWN,
            reasons=("SIGN_UNKNOWN",),
            durable=False,
        )

        with self.orchestration(
            signing=signing
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            SIGN,
        )

    async def test_pass_without_durable_signed_artifact_is_unknown(
        self,
    ):
        signing = self.signing(
            status=SIGNING_PASS,
            durable=False,
        )

        with self.orchestration(
            signing=signing
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            SIGN,
        )
        self.assertIn(
            "LIVE_BUY_SIGNING_NOT_DURABLE",
            result.reasons,
        )

    async def test_signing_binding_mismatch_is_unknown(
        self,
    ):
        signing = self.signing(
            signer_pubkey="wrong-wallet"
        )

        with self.orchestration(
            signing=signing
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            SIGN,
        )
        self.assertIn(
            "LIVE_BUY_SIGNING_BINDING_MISMATCH",
            result.reasons,
        )

    async def test_signed_timestamp_must_be_positive_finite(
        self,
    ):
        signing = self.signing(
            signed_at=float("nan")
        )

        with self.orchestration(
            signing=signing
        ):
            result = await self.invoke()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )
        self.assertEqual(
            result.stage,
            SIGN,
        )
        self.assertIn(
            "LIVE_BUY_SIGNED_ARTIFACT_INVALID",
            result.reasons,
        )

    async def test_happy_path_stops_at_durably_signed(
        self,
    ):
        with self.orchestration() as mocks:
            result = await self.invoke()

        self.assertEqual(
            result.status,
            SIGNED,
        )
        self.assertEqual(
            result.stage,
            COMPLETE,
        )
        self.assertEqual(
            result.reservation_id,
            self.reservation_id,
        )
        self.assertEqual(
            result.transaction_signature,
            "signature-test",
        )
        self.assertEqual(
            result.signed_transaction_sha256,
            self.transaction_sha256,
        )

        mocks["authorize"].assert_called_once_with(
            mint=self.mint,
            requested_spend_lamports=(
                self.reservation
                .spend_lamports
            ),
            reservation_id=(
                self.reservation_id
            ),
            live_curve=unittest.mock.ANY,
            safety=unittest.mock.ANY,
            execution=unittest.mock.ANY,
            max_authorization_age_seconds=30.0,
            reservation_db_path=(
                self.db_path
            ),
        )

        mocks["sign"].assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
