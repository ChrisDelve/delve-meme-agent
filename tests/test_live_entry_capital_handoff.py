from __future__ import annotations

import asyncio
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
from src.execution.live_entry_candidate_pipeline import (
    LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION,
    LiveEntryCandidatePipelineResult,
)
from src.execution.live_entry_capital_handoff import (
    INVOKED,
    NOT_READY,
    LIVE_ENTRY_CAPITAL_HANDOFF_VERSION,
    LiveEntryCapitalHandoffError,
    run_live_entry_capital_handoff_once,
)
from src.execution.live_entry_evidence import (
    LIVE_ENTRY_EVIDENCE_VERSION,
    LiveEntryEvidence,
)
from src.execution.live_process_owner import (
    LiveProcessOwner,
)


class LiveEntryCapitalHandoffTests(
    unittest.IsolatedAsyncioTestCase
):
    def owner(
        self,
    ):
        return Mock(
            spec=LiveProcessOwner
        )

    def execution_config(
        self,
    ):
        value = Mock(
            spec=LiveBuyExecutionConfig
        )

        value.config_version = (
            LIVE_BUY_EXECUTION_CONFIG_VERSION
        )

        return value

    def pipeline_result(
        self,
        *,
        ready: bool,
    ):
        value = Mock(
            spec=LiveEntryCandidatePipelineResult
        )

        value.pipeline_version = (
            LIVE_ENTRY_CANDIDATE_PIPELINE_VERSION
        )

        value.evidence_ready = ready

        candidate = object()

        value.candidate = candidate

        if ready:
            evidence = Mock(
                spec=LiveEntryEvidence
            )

            evidence.evidence_version = (
                LIVE_ENTRY_EVIDENCE_VERSION
            )
            evidence.candidate = candidate

            resolution = Mock()
            resolution.evidence = evidence

            value.evidence_resolution = (
                resolution
            )

        else:
            value.evidence_resolution = None

        return value

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_ENTRY_CAPITAL_HANDOFF_VERSION,
            "live-entry-capital-handoff-v1",
        )

    async def test_non_ready_pipeline_never_invokes_buy_adapter(
        self,
    ):
        pipeline_result = (
            self.pipeline_result(
                ready=False
            )
        )

        adapter = AsyncMock()

        with patch(
            "src.execution."
            "live_entry_capital_handoff."
            "run_live_entry_buy_once",
            new=adapter,
        ):
            result = (
                await run_live_entry_capital_handoff_once(
                    owner=self.owner(),
                    pipeline_result=pipeline_result,
                    execution_config=(
                        self.execution_config()
                    ),
                )
            )

        self.assertEqual(
            result.status,
            NOT_READY,
        )
        self.assertIs(
            result.pipeline_result,
            pipeline_result,
        )
        self.assertIsNone(
            result.buy_result
        )

        adapter.assert_not_awaited()

    async def test_ready_pipeline_invokes_adapter_once_with_exact_evidence(
        self,
    ):
        owner = self.owner()
        execution_config = (
            self.execution_config()
        )
        pipeline_result = (
            self.pipeline_result(
                ready=True
            )
        )

        exact_evidence = (
            pipeline_result
            .evidence_resolution
            .evidence
        )

        adapter = AsyncMock(
            return_value=sentinel.buy_result
        )

        with patch(
            "src.execution."
            "live_entry_capital_handoff."
            "run_live_entry_buy_once",
            new=adapter,
        ):
            result = (
                await run_live_entry_capital_handoff_once(
                    owner=owner,
                    pipeline_result=pipeline_result,
                    execution_config=(
                        execution_config
                    ),
                )
            )

        self.assertEqual(
            result.status,
            INVOKED,
        )
        self.assertIs(
            result.pipeline_result,
            pipeline_result,
        )
        self.assertIs(
            result.buy_result,
            sentinel.buy_result,
        )

        adapter.assert_awaited_once_with(
            owner=owner,
            evidence=exact_evidence,
            execution_config=execution_config,
        )

    async def test_ready_pipeline_with_invalid_evidence_fails_closed(
        self,
    ):
        pipeline_result = (
            self.pipeline_result(
                ready=True
            )
        )

        pipeline_result.evidence_resolution.evidence = (
            object()
        )

        adapter = AsyncMock()

        with patch(
            "src.execution."
            "live_entry_capital_handoff."
            "run_live_entry_buy_once",
            new=adapter,
        ):
            with self.assertRaisesRegex(
                LiveEntryCapitalHandoffError,
                (
                    "^LIVE_ENTRY_CAPITAL_HANDOFF_"
                    "EVIDENCE_CONTRACT_INVALID$"
                ),
            ):
                await run_live_entry_capital_handoff_once(
                    owner=self.owner(),
                    pipeline_result=pipeline_result,
                    execution_config=(
                        self.execution_config()
                    ),
                )

        adapter.assert_not_awaited()

    async def test_invalid_pipeline_type_fails_before_adapter(
        self,
    ):
        adapter = AsyncMock()

        with patch(
            "src.execution."
            "live_entry_capital_handoff."
            "run_live_entry_buy_once",
            new=adapter,
        ):
            with self.assertRaisesRegex(
                TypeError,
                (
                    "^pipeline_result must be "
                    "LiveEntryCandidatePipelineResult$"
                ),
            ):
                await run_live_entry_capital_handoff_once(
                    owner=self.owner(),
                    pipeline_result=object(),
                    execution_config=(
                        self.execution_config()
                    ),
                )

        adapter.assert_not_awaited()

    async def test_invalid_authority_inputs_fail_even_when_not_ready(
        self,
    ):
        pipeline_result = (
            self.pipeline_result(
                ready=False
            )
        )

        adapter = AsyncMock()

        with patch(
            "src.execution."
            "live_entry_capital_handoff."
            "run_live_entry_buy_once",
            new=adapter,
        ):
            with self.assertRaisesRegex(
                TypeError,
                "^owner must be LiveProcessOwner$",
            ):
                await run_live_entry_capital_handoff_once(
                    owner=object(),
                    pipeline_result=pipeline_result,
                    execution_config=(
                        self.execution_config()
                    ),
                )

            with self.assertRaisesRegex(
                TypeError,
                (
                    "^execution_config must be "
                    "LiveBuyExecutionConfig$"
                ),
            ):
                await run_live_entry_capital_handoff_once(
                    owner=self.owner(),
                    pipeline_result=pipeline_result,
                    execution_config=object(),
                )

        adapter.assert_not_awaited()

    async def test_component_version_mismatch_fails_before_adapter(
        self,
    ):
        adapter = AsyncMock()

        with patch(
            "src.execution."
            "live_entry_capital_handoff."
            "LIVE_ENTRY_BUY_ADAPTER_VERSION",
            "unexpected-version",
        ):
            with patch(
                "src.execution."
                "live_entry_capital_handoff."
                "run_live_entry_buy_once",
                new=adapter,
            ):
                with self.assertRaisesRegex(
                    LiveEntryCapitalHandoffError,
                    (
                        "^LIVE_ENTRY_CAPITAL_HANDOFF_"
                        "COMPONENT_VERSION_MISMATCH$"
                    ),
                ):
                    await run_live_entry_capital_handoff_once(
                        owner=self.owner(),
                        pipeline_result=(
                            self.pipeline_result(
                                ready=True
                            )
                        ),
                        execution_config=(
                            self.execution_config()
                        ),
                    )

        adapter.assert_not_awaited()

    async def test_adapter_exception_propagates_without_retry(
        self,
    ):
        adapter = AsyncMock(
            side_effect=RuntimeError(
                "downstream-failure"
            )
        )

        with patch(
            "src.execution."
            "live_entry_capital_handoff."
            "run_live_entry_buy_once",
            new=adapter,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^downstream-failure$",
            ):
                await run_live_entry_capital_handoff_once(
                    owner=self.owner(),
                    pipeline_result=(
                        self.pipeline_result(
                            ready=True
                        )
                    ),
                    execution_config=(
                        self.execution_config()
                    ),
                )

        self.assertEqual(
            adapter.await_count,
            1,
        )

    async def test_cancellation_propagates_without_retry(
        self,
    ):
        adapter = AsyncMock(
            side_effect=asyncio.CancelledError()
        )

        with patch(
            "src.execution."
            "live_entry_capital_handoff."
            "run_live_entry_buy_once",
            new=adapter,
        ):
            with self.assertRaises(
                asyncio.CancelledError
            ):
                await run_live_entry_capital_handoff_once(
                    owner=self.owner(),
                    pipeline_result=(
                        self.pipeline_result(
                            ready=True
                        )
                    ),
                    execution_config=(
                        self.execution_config()
                    ),
                )

        self.assertEqual(
            adapter.await_count,
            1,
        )


if __name__ == "__main__":
    unittest.main()
