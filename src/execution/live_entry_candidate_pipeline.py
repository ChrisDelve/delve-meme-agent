from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.execution.live_entry_evidence import (
    LIVE_ENTRY_EVIDENCE_RESOLUTION_VERSION,
    PASS as EVIDENCE_PASS,
    REJECT as EVIDENCE_REJECT,
    UNKNOWN as EVIDENCE_UNKNOWN,
    LiveEntryEvidenceResolution,
    resolve_live_entry_evidence,
)
from src.execution.model_entry_candidate import (
    MODEL_ENTRY_CANDIDATE_VERSION,
    ModelEntryCandidate,
)
from src.execution.model_entry_candidate_adapter import (
    MODEL_ENTRY_CANDIDATE_ADAPTER_VERSION,
    adapt_model_entry_candidate,
)
from src.strategies.live_entry_policy import (
    LIVE_ENTRY_POLICY_VERSION,
    PASS as POLICY_PASS,
    REJECT as POLICY_REJECT,
    UNKNOWN as POLICY_UNKNOWN,
    LiveEntryPolicy,
    LiveEntryPolicyDecision,
    evaluate_live_entry_candidate,
)


LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION = (
    "live-entry-candidate-pipeline-v1"
)

PASS = "PASS"
REJECT = "REJECT"
UNKNOWN = "UNKNOWN"

ADAPTER = "ADAPTER"
POLICY = "POLICY"
EVIDENCE = "EVIDENCE"


@dataclass(
    frozen=True,
    slots=True,
)
class LiveEntryCandidatePipelineResult:
    """
    Result of candidate provenance binding, strategy policy, and fresh
    live-evidence resolution.

    PASS means only that fresh LiveEntryEvidence exists.

    PASS does NOT:
      - authorize capital;
      - reserve capital;
      - invoke LiveProcessOwner;
      - invoke the live BUY adapter;
      - sign or submit anything.
    """

    pipeline_version: str

    status: str
    stage: str
    reasons: tuple[str, ...]

    candidate: ModelEntryCandidate | None
    policy_decision: (
        LiveEntryPolicyDecision | None
    )
    evidence_resolution: (
        LiveEntryEvidenceResolution | None
    )

    def __post_init__(
        self,
    ) -> None:
        if (
            self.pipeline_version
            != LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION
        ):
            raise ValueError(
                "pipeline_version is invalid"
            )

        if self.status not in (
            PASS,
            REJECT,
            UNKNOWN,
        ):
            raise ValueError(
                "status is invalid"
            )

        if self.stage not in (
            ADAPTER,
            POLICY,
            EVIDENCE,
        ):
            raise ValueError(
                "stage is invalid"
            )

        if not isinstance(
            self.reasons,
            tuple,
        ):
            raise ValueError(
                "reasons must be tuple"
            )

        if self.status == PASS:
            if self.reasons:
                raise ValueError(
                    "PASS cannot contain reasons"
                )

            if self.stage != EVIDENCE:
                raise ValueError(
                    "PASS requires EVIDENCE stage"
                )
        else:
            if not self.reasons:
                raise ValueError(
                    "non-PASS requires reasons"
                )

        if self.stage == ADAPTER:
            if (
                self.candidate is not None
                or self.policy_decision
                is not None
                or self.evidence_resolution
                is not None
            ):
                raise ValueError(
                    "ADAPTER stage cannot contain "
                    "downstream state"
                )

            return

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

        if self.stage == POLICY:
            if not isinstance(
                self.policy_decision,
                LiveEntryPolicyDecision,
            ):
                raise ValueError(
                    "POLICY stage requires "
                    "policy decision"
                )

            if (
                self.evidence_resolution
                is not None
            ):
                raise ValueError(
                    "POLICY stage cannot contain "
                    "evidence resolution"
                )

            expected_status = {
                POLICY_REJECT: REJECT,
                POLICY_UNKNOWN: UNKNOWN,
            }.get(
                self.policy_decision.status
            )

            if (
                expected_status is None
                or self.status
                != expected_status
            ):
                raise ValueError(
                    "POLICY stage status mismatch"
                )

            return

        #
        # EVIDENCE stage.
        #
        if (
            not isinstance(
                self.policy_decision,
                LiveEntryPolicyDecision,
            )
            or self.policy_decision.status
            != POLICY_PASS
        ):
            raise ValueError(
                "EVIDENCE stage requires "
                "PASS policy decision"
            )

        if self.evidence_resolution is None:
            #
            # Evidence-stage orchestration can fail before a trusted
            # LiveEntryEvidenceResolution exists:
            #
            #   - resolver raised an ordinary exception;
            #   - resolver returned an invalid/untrusted contract.
            #
            # Those states remain fail-closed UNKNOWN and deliberately
            # do not retain an untrusted resolution object.
            #
            if (
                self.status == UNKNOWN
                and self.reasons in (
                    (
                        "LIVE_ENTRY_PIPELINE_"
                        "EVIDENCE_EXCEPTION",
                    ),
                    (
                        "LIVE_ENTRY_PIPELINE_"
                        "EVIDENCE_CONTRACT_INVALID",
                    ),
                )
            ):
                return

            raise ValueError(
                "EVIDENCE stage requires "
                "evidence resolution"
            )

        if not isinstance(
            self.evidence_resolution,
            LiveEntryEvidenceResolution,
        ):
            raise ValueError(
                "evidence resolution is invalid"
            )

        if (
            self.evidence_resolution
            .resolution_version
            != LIVE_ENTRY_EVIDENCE_RESOLUTION_VERSION
        ):
            raise ValueError(
                "evidence resolution version "
                "mismatch"
            )

        if (
            self.evidence_resolution.candidate
            is not self.candidate
        ):
            raise ValueError(
                "evidence resolution candidate "
                "identity mismatch"
            )

        expected_status = {
            EVIDENCE_PASS: PASS,
            EVIDENCE_REJECT: REJECT,
            EVIDENCE_UNKNOWN: UNKNOWN,
        }.get(
            self.evidence_resolution.status
        )

        if (
            expected_status is None
            or self.status
            != expected_status
        ):
            raise ValueError(
                "EVIDENCE stage status mismatch"
            )

    @property
    def evidence_ready(
        self,
    ) -> bool:
        return (
            self.status == PASS
            and self.stage == EVIDENCE
            and self.evidence_resolution
            is not None
            and self.evidence_resolution.evidence
            is not None
        )


def _components_are_compatible(
) -> bool:
    return (
        MODEL_ENTRY_CANDIDATE_ADAPTER_VERSION
        == "model-entry-candidate-adapter-v1"
        and LIVE_ENTRY_POLICY_VERSION
        == "live-entry-policy-v1"
        and LIVE_ENTRY_EVIDENCE_RESOLUTION_VERSION
        == "live-entry-evidence-resolution-v1"
    )


def _valid_policy_decision(
    *,
    decision: object,
    candidate: ModelEntryCandidate,
    policy: object,
    evaluated_at: object,
) -> bool:
    if not isinstance(
        decision,
        LiveEntryPolicyDecision,
    ):
        return False

    if (
        decision.evaluator_version
        != LIVE_ENTRY_POLICY_VERSION
    ):
        return False

    if decision.status not in (
        POLICY_PASS,
        POLICY_REJECT,
        POLICY_UNKNOWN,
    ):
        return False

    expected_policy = (
        policy
        if isinstance(
            policy,
            LiveEntryPolicy,
        )
        else None
    )

    if decision.policy is not expected_policy:
        return False

    expected_evaluated_at = (
        evaluated_at
        if (
            isinstance(
                evaluated_at,
                int,
            )
            and not isinstance(
                evaluated_at,
                bool,
            )
            and evaluated_at > 0
        )
        else 0
    )

    if (
        decision.evaluated_at
        != expected_evaluated_at
    ):
        return False

    exact_identity = (
        decision.entry_signature
        == candidate.entry_signature
        and decision.mint
        == candidate.mint
    )

    blank_identity = (
        decision.entry_signature == ""
        and decision.mint == ""
    )

    if decision.status == POLICY_PASS:
        return (
            exact_identity
            and decision.probability_2x_15m
            == candidate.probability_2x_15m
            and not decision.reasons
            and decision.should_resolve_evidence
        )

    if (
        not decision.reasons
        or decision.should_resolve_evidence
    ):
        return False

    #
    # Policy evaluation is deliberately progressive.
    #
    # Failures in policy configuration or evaluated_at happen before
    # candidate identity is bound into the decision, so a legitimate
    # early UNKNOWN has blank candidate metadata.
    #
    if blank_identity:
        return (
            decision.status == POLICY_UNKNOWN
            and decision.probability_2x_15m
            is None
            and decision.candidate_age_seconds
            is None
        )

    if not exact_identity:
        return False

    #
    # Later REJECT/UNKNOWN decisions may occur before probability is
    # bound (for example unsupported quote mint), or after it has been
    # validated. If present, probability must bind exactly.
    #
    return (
        decision.probability_2x_15m
        is None
        or decision.probability_2x_15m
        == candidate.probability_2x_15m
    )


def _valid_evidence_resolution(
    *,
    resolution: object,
    candidate: ModelEntryCandidate,
) -> bool:
    if not isinstance(
        resolution,
        LiveEntryEvidenceResolution,
    ):
        return False

    if (
        resolution.resolution_version
        != LIVE_ENTRY_EVIDENCE_RESOLUTION_VERSION
    ):
        return False

    if resolution.candidate is not candidate:
        return False

    if resolution.status not in (
        EVIDENCE_PASS,
        EVIDENCE_REJECT,
        EVIDENCE_UNKNOWN,
    ):
        return False

    if resolution.status == EVIDENCE_PASS:
        return (
            not resolution.reasons
            and resolution.evidence is not None
            and getattr(
                resolution.evidence,
                "candidate",
                None,
            )
            is candidate
        )

    return (
        bool(resolution.reasons)
        and resolution.evidence is None
    )


async def resolve_live_entry_candidate_evidence_once(
    *,
    prediction: Mapping[str, Any],
    entry_signature: str,
    mint: str,
    event_user: str,
    quote_mint: str,
    slot: int | None,
    trade_timestamp: int,
    observed_at: int,
    signal_virtual_quote_reserves: int,
    signal_virtual_token_reserves: int,
    policy: LiveEntryPolicy,
    evaluated_at: int,
) -> LiveEntryCandidatePipelineResult:
    """
    Resolve one model prediction through the complete non-capital
    live-entry preparation path:

        persisted model prediction
            ↓
        immutable ModelEntryCandidate
            ↓
        explicit LiveEntryPolicy
            ↓ PASS only
        fresh LiveEntryEvidence

    There is deliberately no execution handoff here.

    No LiveProcessOwner, LiveEntryBuyAdapter, signer, reservation,
    transaction construction, submission, or retry is reachable from
    this function.

    asyncio cancellation is deliberately allowed to propagate.
    """

    def finish(
        *,
        status: str,
        stage: str,
        reasons: tuple[str, ...],
        candidate: (
            ModelEntryCandidate | None
        ) = None,
        policy_decision: (
            LiveEntryPolicyDecision | None
        ) = None,
        evidence_resolution: (
            LiveEntryEvidenceResolution | None
        ) = None,
    ) -> LiveEntryCandidatePipelineResult:
        return LiveEntryCandidatePipelineResult(
            pipeline_version=(
                LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION
            ),
            status=status,
            stage=stage,
            reasons=reasons,
            candidate=candidate,
            policy_decision=(
                policy_decision
            ),
            evidence_resolution=(
                evidence_resolution
            ),
        )

    if not _components_are_compatible():
        return finish(
            status=UNKNOWN,
            stage=ADAPTER,
            reasons=(
                "LIVE_ENTRY_PIPELINE_"
                "COMPONENT_VERSION_MISMATCH",
            ),
        )

    try:
        candidate = (
            adapt_model_entry_candidate(
                prediction=prediction,
                entry_signature=(
                    entry_signature
                ),
                mint=mint,
                event_user=event_user,
                quote_mint=quote_mint,
                slot=slot,
                trade_timestamp=(
                    trade_timestamp
                ),
                observed_at=observed_at,
                signal_virtual_quote_reserves=(
                    signal_virtual_quote_reserves
                ),
                signal_virtual_token_reserves=(
                    signal_virtual_token_reserves
                ),
            )
        )
    except Exception:
        return finish(
            status=UNKNOWN,
            stage=ADAPTER,
            reasons=(
                "LIVE_ENTRY_PIPELINE_"
                "CANDIDATE_INVALID",
            ),
        )

    try:
        policy_decision = (
            evaluate_live_entry_candidate(
                candidate=candidate,
                evaluated_at=evaluated_at,
                policy=policy,
            )
        )
    except Exception:
        return finish(
            status=UNKNOWN,
            stage=POLICY,
            reasons=(
                "LIVE_ENTRY_PIPELINE_"
                "POLICY_EXCEPTION",
            ),
            candidate=candidate,
        )

    if not _valid_policy_decision(
        decision=policy_decision,
        candidate=candidate,
        policy=policy,
        evaluated_at=evaluated_at,
    ):
        return finish(
            status=UNKNOWN,
            stage=POLICY,
            reasons=(
                "LIVE_ENTRY_PIPELINE_"
                "POLICY_CONTRACT_INVALID",
            ),
            candidate=candidate,
        )

    if policy_decision.status == POLICY_REJECT:
        return finish(
            status=REJECT,
            stage=POLICY,
            reasons=tuple(
                "POLICY:" + reason
                for reason
                in policy_decision.reasons
            ),
            candidate=candidate,
            policy_decision=(
                policy_decision
            ),
        )

    if policy_decision.status == POLICY_UNKNOWN:
        return finish(
            status=UNKNOWN,
            stage=POLICY,
            reasons=tuple(
                "POLICY:" + reason
                for reason
                in policy_decision.reasons
            ),
            candidate=candidate,
            policy_decision=(
                policy_decision
            ),
        )

    try:
        evidence_resolution = (
            await resolve_live_entry_evidence(
                candidate=candidate
            )
        )
    except Exception:
        return finish(
            status=UNKNOWN,
            stage=EVIDENCE,
            reasons=(
                "LIVE_ENTRY_PIPELINE_"
                "EVIDENCE_EXCEPTION",
            ),
            candidate=candidate,
            policy_decision=(
                policy_decision
            ),
        )

    if not _valid_evidence_resolution(
        resolution=evidence_resolution,
        candidate=candidate,
    ):
        return finish(
            status=UNKNOWN,
            stage=EVIDENCE,
            reasons=(
                "LIVE_ENTRY_PIPELINE_"
                "EVIDENCE_CONTRACT_INVALID",
            ),
            candidate=candidate,
            policy_decision=(
                policy_decision
            ),
        )

    if (
        evidence_resolution.status
        == EVIDENCE_REJECT
    ):
        return finish(
            status=REJECT,
            stage=EVIDENCE,
            reasons=tuple(
                "EVIDENCE:" + reason
                for reason
                in evidence_resolution.reasons
            ),
            candidate=candidate,
            policy_decision=(
                policy_decision
            ),
            evidence_resolution=(
                evidence_resolution
            ),
        )

    if (
        evidence_resolution.status
        == EVIDENCE_UNKNOWN
    ):
        return finish(
            status=UNKNOWN,
            stage=EVIDENCE,
            reasons=tuple(
                "EVIDENCE:" + reason
                for reason
                in evidence_resolution.reasons
            ),
            candidate=candidate,
            policy_decision=(
                policy_decision
            ),
            evidence_resolution=(
                evidence_resolution
            ),
        )

    return finish(
        status=PASS,
        stage=EVIDENCE,
        reasons=(),
        candidate=candidate,
        policy_decision=(
            policy_decision
        ),
        evidence_resolution=(
            evidence_resolution
        ),
    )
