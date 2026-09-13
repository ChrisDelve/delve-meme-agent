from __future__ import annotations

import math
from dataclasses import dataclass

from src.execution.live_curve_state import (
    LivePumpCurveState,
)
from src.execution.live_pump_fee_state import (
    BPS_DENOMINATOR,
    LIVE_PUMP_FEE_STATE_VERSION,
    LivePumpFeeState,
    resolve_live_pump_fee_state,
)
from src.execution.model_entry_candidate import (
    MODEL_ENTRY_CANDIDATE_VERSION,
    ModelEntryCandidate,
)
from src.safety.token_safety_gate import (
    GATE_VERSION,
    PASS as SAFETY_PASS,
    REJECT as SAFETY_REJECT,
    UNKNOWN as SAFETY_UNKNOWN,
    TokenSafetyGateResult,
    resolve_and_gate,
)
from src.safety.token_safety_resolver import (
    BondingCurveSnapshot,
)


LIVE_ENTRY_EVIDENCE_VERSION = (
    "live-entry-evidence-v1"
)

LIVE_ENTRY_EVIDENCE_RESOLUTION_VERSION = (
    "live-entry-evidence-resolution-v1"
)

PASS = "PASS"
REJECT = "REJECT"
UNKNOWN = "UNKNOWN"


def _valid_nonnegative_int(
    value: object,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    )


def _valid_nonnegative_finite_number(
    value: object,
) -> bool:
    return (
        isinstance(
            value,
            (int, float),
        )
        and not isinstance(value, bool)
        and math.isfinite(
            float(value)
        )
        and float(value) >= 0.0
    )


def _valid_fee_bps(
    value: object,
) -> bool:
    return (
        _valid_nonnegative_int(
            value
        )
        and value <= BPS_DENOMINATOR
    )


@dataclass(
    frozen=True,
    slots=True,
)
class LiveEntryEvidence:
    """
    Immutable fresh on-chain evidence for one ModelEntryCandidate.

    This object proves only what was observed. It does not authorize
    capital.

    It deliberately owns no:
      - strategy threshold;
      - position sizing;
      - protected-cash decision;
      - execution-quality approval;
      - capital reservation;
      - signer access;
      - transaction construction/signing/submission;
      - BUY or SELL invocation.
    """

    evidence_version: str

    candidate: ModelEntryCandidate
    safety: TokenSafetyGateResult
    fee_state: LivePumpFeeState
    live_curve: LivePumpCurveState

    def __post_init__(
        self,
    ) -> None:
        if (
            self.evidence_version
            != LIVE_ENTRY_EVIDENCE_VERSION
        ):
            raise ValueError(
                "evidence_version is invalid"
            )

        if (
            not isinstance(
                self.candidate,
                ModelEntryCandidate,
            )
            or self.candidate.candidate_version
            != MODEL_ENTRY_CANDIDATE_VERSION
        ):
            raise ValueError(
                "candidate is invalid"
            )

        if not isinstance(
            self.safety,
            TokenSafetyGateResult,
        ):
            raise ValueError(
                "safety is invalid"
            )

        if (
            self.safety.gate_version
            != GATE_VERSION
        ):
            raise ValueError(
                "safety gate version is invalid"
            )

        if (
            self.safety.status
            != SAFETY_PASS
        ):
            raise ValueError(
                "safety does not allow trade"
            )

        if self.safety.snapshot is None:
            raise ValueError(
                "safety snapshot is missing"
            )

        mint = self.candidate.mint

        if self.safety.mint != mint:
            raise ValueError(
                "safety mint mismatch"
            )

        if (
            getattr(
                self.safety.snapshot,
                "mint",
                None,
            )
            != mint
        ):
            raise ValueError(
                "safety snapshot mint mismatch"
            )

        safety_rpc_max_slot = getattr(
            self.safety.snapshot,
            "rpc_max_slot",
            None,
        )

        if not _valid_nonnegative_int(
            safety_rpc_max_slot
        ):
            raise ValueError(
                "safety rpc_max_slot is invalid"
            )

        if not isinstance(
            self.fee_state,
            LivePumpFeeState,
        ):
            raise ValueError(
                "fee_state is invalid"
            )

        if (
            self.fee_state.resolver_version
            != LIVE_PUMP_FEE_STATE_VERSION
        ):
            raise ValueError(
                "fee-state version is invalid"
            )

        if self.fee_state.mint != mint:
            raise ValueError(
                "fee-state mint mismatch"
            )

        if not isinstance(
            self.fee_state.curve,
            BondingCurveSnapshot,
        ):
            raise ValueError(
                "fee-state curve is invalid"
            )

        for name, value in (
            (
                "fee-state market_cap_lamports",
                self.fee_state.market_cap_lamports,
            ),
            (
                "fee-state selected_tier_index",
                self.fee_state.selected_tier_index,
            ),
            (
                "fee-state selected_threshold_lamports",
                self.fee_state.selected_threshold_lamports,
            ),
        ):
            if not _valid_nonnegative_int(
                value
            ):
                raise ValueError(
                    f"{name} is invalid"
                )

        for name, value in (
            (
                "fee-state lp_fee_bps",
                self.fee_state.lp_fee_bps,
            ),
            (
                "fee-state protocol_fee_bps",
                self.fee_state.protocol_fee_bps,
            ),
            (
                "fee-state creator_fee_bps",
                self.fee_state.creator_fee_bps,
            ),
        ):
            if not _valid_fee_bps(
                value
            ):
                raise ValueError(
                    f"{name} is invalid"
                )

        if not _valid_nonnegative_int(
            self.fee_state.rpc_slot
        ):
            raise ValueError(
                "fee-state rpc_slot is invalid"
            )

        if (
            self.fee_state.rpc_slot
            < safety_rpc_max_slot
        ):
            raise ValueError(
                "fee-state rpc_slot precedes safety"
            )

        if not _valid_nonnegative_finite_number(
            self.fee_state.fetched_at
        ):
            raise ValueError(
                "fee-state fetched_at is invalid"
            )

        if not isinstance(
            self.live_curve,
            LivePumpCurveState,
        ):
            raise ValueError(
                "live_curve is invalid"
            )

        if self.live_curve.mint != mint:
            raise ValueError(
                "live-curve mint mismatch"
            )

        #
        # Strong construction-identity invariant:
        # LiveEntryEvidence must use the exact curve snapshot returned
        # by the fee-state resolver, not a separately fetched or
        # reconstructed curve.
        #
        if (
            self.live_curve.curve
            is not self.fee_state.curve
        ):
            raise ValueError(
                "live-curve snapshot identity mismatch"
            )

        if (
            self.live_curve.rpc_slot
            != self.fee_state.rpc_slot
        ):
            raise ValueError(
                "live-curve rpc_slot mismatch"
            )

        if (
            self.live_curve.fetched_at
            != self.fee_state.fetched_at
        ):
            raise ValueError(
                "live-curve fetched_at mismatch"
            )

    @property
    def min_context_slot(
        self,
    ) -> int:
        snapshot = self.safety.snapshot

        if snapshot is None:
            raise RuntimeError(
                "safety snapshot is missing"
            )

        return int(
            snapshot.rpc_max_slot
        )


@dataclass(
    frozen=True,
    slots=True,
)
class LiveEntryEvidenceResolution:
    """
    Explicit fail-closed result of fresh evidence resolution.

    PASS:
      evidence is present and reasons are empty.

    REJECT:
      structurally valid safety evidence rejected the token.

    UNKNOWN:
      evidence could not be proven safely.
    """

    resolution_version: str

    candidate: ModelEntryCandidate

    status: str
    reasons: tuple[str, ...]

    safety: TokenSafetyGateResult | None
    evidence: LiveEntryEvidence | None

    def __post_init__(
        self,
    ) -> None:
        if (
            self.resolution_version
            != LIVE_ENTRY_EVIDENCE_RESOLUTION_VERSION
        ):
            raise ValueError(
                "resolution_version is invalid"
            )

        if not isinstance(
            self.candidate,
            ModelEntryCandidate,
        ):
            raise ValueError(
                "candidate is invalid"
            )

        if self.status not in (
            PASS,
            REJECT,
            UNKNOWN,
        ):
            raise ValueError(
                "status is invalid"
            )

        if not isinstance(
            self.reasons,
            tuple,
        ):
            raise ValueError(
                "reasons must be tuple"
            )

        if self.safety is not None:
            if not isinstance(
                self.safety,
                TokenSafetyGateResult,
            ):
                raise ValueError(
                    "safety is invalid"
                )

        if self.status == PASS:
            if self.reasons:
                raise ValueError(
                    "PASS cannot contain reasons"
                )

            if not isinstance(
                self.evidence,
                LiveEntryEvidence,
            ):
                raise ValueError(
                    "PASS requires evidence"
                )

            if (
                self.evidence.candidate
                is not self.candidate
            ):
                raise ValueError(
                    "PASS candidate identity mismatch"
                )

            if (
                self.safety
                is not self.evidence.safety
            ):
                raise ValueError(
                    "PASS safety identity mismatch"
                )

        else:
            if not self.reasons:
                raise ValueError(
                    "non-PASS requires reasons"
                )

            if self.evidence is not None:
                raise ValueError(
                    "non-PASS cannot contain evidence"
                )


def _finish(
    *,
    candidate: ModelEntryCandidate,
    status: str,
    reasons: tuple[str, ...],
    safety: TokenSafetyGateResult | None = None,
    evidence: LiveEntryEvidence | None = None,
) -> LiveEntryEvidenceResolution:
    return LiveEntryEvidenceResolution(
        resolution_version=(
            LIVE_ENTRY_EVIDENCE_RESOLUTION_VERSION
        ),
        candidate=candidate,
        status=status,
        reasons=reasons,
        safety=safety,
        evidence=evidence,
    )


async def resolve_live_entry_evidence(
    *,
    candidate: ModelEntryCandidate,
) -> LiveEntryEvidenceResolution:
    """
    Resolve fresh safety + Pump curve/FeeConfig evidence.

    Freshness ordering:

        ModelEntryCandidate
            ↓
        token safety
            ↓
        safety.snapshot.rpc_max_slot
            ↓
        Pump curve + FeeConfig in ONE RPC context
            ↓
        immutable LiveEntryEvidence

    Ordinary resolution failures become UNKNOWN.

    asyncio cancellation is deliberately not swallowed.

    This function does not size, reserve, sign, submit, or invoke
    BUY/SELL execution.
    """
    if (
        not isinstance(
            candidate,
            ModelEntryCandidate,
        )
        or candidate.candidate_version
        != MODEL_ENTRY_CANDIDATE_VERSION
    ):
        raise TypeError(
            "candidate must be ModelEntryCandidate"
        )

    try:
        safety = await resolve_and_gate(
            candidate.mint
        )

    except Exception:
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_SAFETY_EXCEPTION",
            ),
        )

    if not isinstance(
        safety,
        TokenSafetyGateResult,
    ):
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_SAFETY_CONTRACT_INVALID",
            ),
        )

    if (
        safety.gate_version
        != GATE_VERSION
    ):
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_SAFETY_VERSION_MISMATCH",
            ),
        )

    if safety.mint != candidate.mint:
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_SAFETY_MINT_MISMATCH",
            ),
        )

    if safety.status == SAFETY_REJECT:
        reasons = tuple(
            f"SAFETY:{reason}"
            for reason in safety.reasons
        )

        return _finish(
            candidate=candidate,
            status=REJECT,
            reasons=(
                reasons
                or (
                    "SAFETY:REJECT",
                )
            ),
            safety=safety,
        )

    if safety.status == SAFETY_UNKNOWN:
        reasons = tuple(
            f"SAFETY:{reason}"
            for reason in safety.reasons
        )

        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                reasons
                or (
                    "SAFETY:UNKNOWN",
                )
            ),
            safety=safety,
        )

    if safety.status != SAFETY_PASS:
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_SAFETY_STATUS_INVALID",
            ),
            safety=safety,
        )

    if safety.snapshot is None:
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_SAFETY_SNAPSHOT_MISSING",
            ),
            safety=safety,
        )

    if (
        getattr(
            safety.snapshot,
            "mint",
            None,
        )
        != candidate.mint
    ):
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_SAFETY_SNAPSHOT_MINT_MISMATCH",
            ),
            safety=safety,
        )

    min_context_slot = getattr(
        safety.snapshot,
        "rpc_max_slot",
        None,
    )

    if not _valid_nonnegative_int(
        min_context_slot
    ):
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_SAFETY_SLOT_INVALID",
            ),
            safety=safety,
        )

    try:
        fee_state = (
            await resolve_live_pump_fee_state(
                mint=candidate.mint,
                min_context_slot=(
                    min_context_slot
                ),
            )
        )

    except Exception:
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_FEE_STATE_EXCEPTION",
            ),
            safety=safety,
        )

    if not isinstance(
        fee_state,
        LivePumpFeeState,
    ):
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_FEE_STATE_CONTRACT_INVALID",
            ),
            safety=safety,
        )

    if (
        fee_state.resolver_version
        != LIVE_PUMP_FEE_STATE_VERSION
    ):
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_FEE_STATE_VERSION_MISMATCH",
            ),
            safety=safety,
        )

    if fee_state.mint != candidate.mint:
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_FEE_STATE_MINT_MISMATCH",
            ),
            safety=safety,
        )

    if not _valid_nonnegative_int(
        fee_state.rpc_slot
    ):
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_FEE_STATE_SLOT_INVALID",
            ),
            safety=safety,
        )

    if (
        fee_state.rpc_slot
        < min_context_slot
    ):
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_FEE_STATE_SLOT_REGRESSION",
            ),
            safety=safety,
        )

    if not _valid_nonnegative_finite_number(
        fee_state.fetched_at
    ):
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_FEE_STATE_FETCH_TIME_INVALID",
            ),
            safety=safety,
        )

    live_curve = LivePumpCurveState(
        mint=candidate.mint,
        curve=fee_state.curve,
        rpc_slot=fee_state.rpc_slot,
        fetched_at=fee_state.fetched_at,
    )

    try:
        evidence = LiveEntryEvidence(
            evidence_version=(
                LIVE_ENTRY_EVIDENCE_VERSION
            ),
            candidate=candidate,
            safety=safety,
            fee_state=fee_state,
            live_curve=live_curve,
        )

    except Exception:
        return _finish(
            candidate=candidate,
            status=UNKNOWN,
            reasons=(
                "LIVE_ENTRY_EVIDENCE_CONSTRUCTION_FAILED",
            ),
            safety=safety,
        )

    return _finish(
        candidate=candidate,
        status=PASS,
        reasons=(),
        safety=safety,
        evidence=evidence,
    )
