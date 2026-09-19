from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.execution.live_buy_execution_config import (
    LIVE_BUY_EXECUTION_CONFIG_VERSION,
    LiveBuyExecutionConfig,
)
from src.execution.live_entry_buy_adapter import (
    LIVE_ENTRY_BUY_ADAPTER_VERSION,
    run_live_entry_buy_once,
)
from src.execution.live_entry_candidate_pipeline import (
    LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION,
    LiveEntryCandidatePipelineResult,
)
from src.execution.live_entry_evidence import (
    LIVE_ENTRY_EVIDENCE_VERSION,
    LiveEntryEvidence,
)
from src.execution.live_process_owner import (
    LIVE_PROCESS_OWNER_VERSION,
    LiveProcessOwner,
)


LIVE_ENTRY_CAPITAL_HANDOFF_VERSION = (
    "live-entry-capital-handoff-v1"
)

NOT_READY = "NOT_READY"
INVOKED = "INVOKED"


class LiveEntryCapitalHandoffError(
    RuntimeError
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class LiveEntryCapitalHandoffResult:
    """
    Result of one explicit pipeline-result → BUY-authority handoff.

    NOT_READY:
        The trusted pipeline result does not contain a canonical fresh
        EVIDENCE PASS. No BUY authority was invoked.

    INVOKED:
        The exact LiveEntryEvidence from the trusted pipeline result was
        forwarded once into the existing LiveEntryBuyAdapter and the
        downstream authority call returned.

        INVOKED does not mean that a BUY was submitted, confirmed, or
        filled. buy_result preserves the downstream authority result.

    This result grants no authority by itself.
    """

    handoff_version: str
    status: str

    pipeline_result: (
        LiveEntryCandidatePipelineResult
    )

    buy_result: Any | None

    def __post_init__(
        self,
    ) -> None:
        if (
            self.handoff_version
            != LIVE_ENTRY_CAPITAL_HANDOFF_VERSION
        ):
            raise ValueError(
                "handoff_version is invalid"
            )

        if self.status not in (
            NOT_READY,
            INVOKED,
        ):
            raise ValueError(
                "status is invalid"
            )

        if (
            not isinstance(
                self.pipeline_result,
                LiveEntryCandidatePipelineResult,
            )
            or self.pipeline_result.pipeline_version
            != LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION
        ):
            raise ValueError(
                "pipeline_result is invalid"
            )

        if self.status == NOT_READY:
            if self.pipeline_result.evidence_ready:
                raise ValueError(
                    "NOT_READY cannot contain "
                    "evidence-ready pipeline result"
                )

            if self.buy_result is not None:
                raise ValueError(
                    "NOT_READY cannot contain buy result"
                )

            return

        if not self.pipeline_result.evidence_ready:
            raise ValueError(
                "INVOKED requires evidence-ready "
                "pipeline result"
            )


def _components_are_compatible(
) -> bool:
    return (
        LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION
        == "live-entry-candidate-pipeline-v1"
        and LIVE_ENTRY_EVIDENCE_VERSION
        == "live-entry-evidence-v1"
        and LIVE_ENTRY_BUY_ADAPTER_VERSION
        == "live-entry-buy-adapter-v2"
        and LIVE_BUY_EXECUTION_CONFIG_VERSION
        == "live-buy-execution-config-v1"
        and LIVE_PROCESS_OWNER_VERSION
        == "live-process-owner-v1"
    )


def _valid_owner(
    value: object,
) -> bool:
    return isinstance(
        value,
        LiveProcessOwner,
    )


def _valid_pipeline_result(
    value: object,
) -> bool:
    return (
        isinstance(
            value,
            LiveEntryCandidatePipelineResult,
        )
        and value.pipeline_version
        == LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION
    )


def _valid_execution_config(
    value: object,
) -> bool:
    return (
        isinstance(
            value,
            LiveBuyExecutionConfig,
        )
        and value.config_version
        == LIVE_BUY_EXECUTION_CONFIG_VERSION
    )


async def run_live_entry_capital_handoff_once(
    *,
    owner: LiveProcessOwner,
    pipeline_result: LiveEntryCandidatePipelineResult,
    execution_config: LiveBuyExecutionConfig,
) -> LiveEntryCapitalHandoffResult:
    """
    Consume exactly one trusted live-entry pipeline result.

    Non-ready pipeline results are terminal here and cannot reach BUY
    authority.

    A canonical EVIDENCE PASS forwards the exact immutable
    LiveEntryEvidence object once into run_live_entry_buy_once().

    This function deliberately does not:
      - evaluate candidates;
      - resolve policy;
      - perform evidence RPC;
      - schedule background work;
      - loop;
      - retry;
      - rebuild evidence;
      - size capital;
      - reserve capital directly;
      - access a signer directly;
      - construct/sign/submit a transaction directly.

    asyncio cancellation and downstream authority failures propagate.
    """

    if not _components_are_compatible():
        raise LiveEntryCapitalHandoffError(
            "LIVE_ENTRY_CAPITAL_HANDOFF_"
            "COMPONENT_VERSION_MISMATCH"
        )

    if not _valid_owner(
        owner
    ):
        raise TypeError(
            "owner must be LiveProcessOwner"
        )

    if not _valid_pipeline_result(
        pipeline_result
    ):
        raise TypeError(
            "pipeline_result must be "
            "LiveEntryCandidatePipelineResult"
        )

    if not _valid_execution_config(
        execution_config
    ):
        raise TypeError(
            "execution_config must be "
            "LiveBuyExecutionConfig"
        )

    if not pipeline_result.evidence_ready:
        return LiveEntryCapitalHandoffResult(
            handoff_version=(
                LIVE_ENTRY_CAPITAL_HANDOFF_VERSION
            ),
            status=NOT_READY,
            pipeline_result=pipeline_result,
            buy_result=None,
        )

    resolution = (
        pipeline_result.evidence_resolution
    )

    evidence = (
        None
        if resolution is None
        else resolution.evidence
    )

    if (
        not isinstance(
            evidence,
            LiveEntryEvidence,
        )
        or evidence.evidence_version
        != LIVE_ENTRY_EVIDENCE_VERSION
        or evidence.candidate
        is not pipeline_result.candidate
    ):
        raise LiveEntryCapitalHandoffError(
            "LIVE_ENTRY_CAPITAL_HANDOFF_"
            "EVIDENCE_CONTRACT_INVALID"
        )

    buy_result = await run_live_entry_buy_once(
        owner=owner,
        evidence=evidence,
        execution_config=execution_config,
    )

    return LiveEntryCapitalHandoffResult(
        handoff_version=(
            LIVE_ENTRY_CAPITAL_HANDOFF_VERSION
        ),
        status=INVOKED,
        pipeline_result=pipeline_result,
        buy_result=buy_result,
    )
