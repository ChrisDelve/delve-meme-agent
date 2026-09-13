from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from src.execution.live_blockhash_context import (
    LIVE_BLOCKHASH_CONTEXT_VERSION,
)
from src.execution.live_pump_global_state import (
    LIVE_PUMP_GLOBAL_STATE_VERSION,
)
from src.execution.live_sell_unexecuted_discovery import (
    LIVE_SELL_UNEXECUTED_DISCOVERY_VERSION,
)
from src.execution.live_sell_unexecuted_executor import (
    BLOCK,
    IDLE,
    SIGNED,
    UNKNOWN,
    LIVE_SELL_UNEXECUTED_EXECUTOR_VERSION,
    sign_one_unexecuted_live_sell_once,
)
from src.execution.pump_sell_v2_account_context import (
    PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION,
)
from src.execution.pump_sell_v2_pre_sign_validation import (
    APPROVE,
    DENY,
    UNKNOWN as PRE_SIGN_UNKNOWN,
    PUMP_SELL_V2_PRE_SIGN_VALIDATION_VERSION,
)
from src.execution.pump_sell_v2_signing import (
    PUMP_SELL_V2_SIGNING_VERSION,
)
from src.execution.pump_sell_v2_unsigned_message import (
    PUMP_SELL_V2_UNSIGNED_MESSAGE_VERSION,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
)


MODULE = (
    "src.execution.live_sell_unexecuted_executor"
)


class LiveSellUnexecutedExecutorTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.db_path = Path(
            "/tmp/live-sell-unexecuted-executor.db"
        )

        self.sha_a = "aa" * 32
        self.sha_b = "bb" * 32

        self.compute_unit_limit = 250_000

        allocation = object()

        self.authorization = (
            SimpleNamespace(
                authorization_sha256=self.sha_a,
                authorization_version=(
                    "live-pump-sell-authorization-v2"
                ),
                wallet_pubkey="wallet",
                mint="mint",
                tokens_to_sell=100,
                allocation=allocation,
                fee_rpc_slot=100,
            )
        )

        self.claim = SimpleNamespace(
            authorization_sha256=self.sha_a,
            authorization_version=(
                self.authorization
                .authorization_version
            ),
            wallet_pubkey="wallet",
            mint="mint",
            tokens_to_sell=100,
            allocation=allocation,
            status=ACTIVE,
            claimed_at=1.0,
        )

        self.candidate = SimpleNamespace(
            authorization_sha256=self.sha_a,
            claimed_at=1.0,
            authorization=self.authorization,
            claim=self.claim,
        )

        self.signer = object()

    def discovery(
        self,
        *,
        candidates=None,
        status="PASS",
        reasons=(),
        resolver_version=(
            LIVE_SELL_UNEXECUTED_DISCOVERY_VERSION
        ),
    ):
        if candidates is None:
            candidates = (
                self.candidate,
            )

        return SimpleNamespace(
            resolver_version=resolver_version,
            status=status,
            reasons=tuple(reasons),
            candidates=tuple(candidates),
        )

    def global_state(
        self,
        *,
        rpc_slot=120,
    ):
        return SimpleNamespace(
            resolver_version=(
                LIVE_PUMP_GLOBAL_STATE_VERSION
            ),
            rpc_slot=rpc_slot,
        )

    def context(
        self,
        *,
        global_rpc_slot=120,
    ):
        return SimpleNamespace(
            resolver_version=(
                PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION
            ),
            authorization_sha256=self.sha_a,
            authorization_fee_rpc_slot=100,
            global_rpc_slot=global_rpc_slot,
        )

    def blockhash(
        self,
        *,
        min_context_slot=120,
        rpc_slot=130,
    ):
        return SimpleNamespace(
            resolver_version=(
                LIVE_BLOCKHASH_CONTEXT_VERSION
            ),
            min_context_slot=min_context_slot,
            rpc_slot=rpc_slot,
        )

    def message(
        self,
        *,
        global_rpc_slot=120,
        blockhash_min_context_slot=120,
        blockhash_rpc_slot=130,
    ):
        return SimpleNamespace(
            builder_version=(
                PUMP_SELL_V2_UNSIGNED_MESSAGE_VERSION
            ),
            authorization_sha256=self.sha_a,
            authorization_fee_rpc_slot=100,
            account_context_global_rpc_slot=(
                global_rpc_slot
            ),
            blockhash_min_context_slot=(
                blockhash_min_context_slot
            ),
            blockhash_rpc_slot=(
                blockhash_rpc_slot
            ),
            compute_unit_limit=(
                self.compute_unit_limit
            ),
            message_sha256="11" * 32,
        )

    def pre_sign(
        self,
        *,
        status=APPROVE,
        reasons=(),
    ):
        return SimpleNamespace(
            validator_version=(
                PUMP_SELL_V2_PRE_SIGN_VALIDATION_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            authorization_sha256=self.sha_a,
        )

    def signing(
        self,
        *,
        status="PASS",
        reasons=(),
        durable=True,
        authorization_sha256=None,
    ):
        if authorization_sha256 is None:
            authorization_sha256 = self.sha_a

        return SimpleNamespace(
            signer_version=(
                PUMP_SELL_V2_SIGNING_VERSION
            ),
            status=status,
            reasons=tuple(reasons),
            authorization_sha256=(
                authorization_sha256
            ),
            transaction_signature=(
                "signature"
            ),
            is_durably_signed=durable,
        )

    async def run_executor(
        self,
        *,
        discovery=None,
        global_state=None,
        context=None,
        blockhash=None,
        message=None,
        pre_sign=None,
        signing=None,
    ):
        if discovery is None:
            discovery = self.discovery()

        if global_state is None:
            global_state = self.global_state()

        if context is None:
            context = self.context()

        if blockhash is None:
            blockhash = self.blockhash()

        if message is None:
            message = self.message()

        if pre_sign is None:
            pre_sign = self.pre_sign()

        if signing is None:
            signing = self.signing()

        discovery_mock = MagicMock(
            return_value=discovery
        )

        global_mock = AsyncMock(
            return_value=global_state
        )

        context_mock = MagicMock(
            return_value=context
        )

        blockhash_mock = AsyncMock(
            return_value=blockhash
        )

        message_mock = MagicMock(
            return_value=message
        )

        pre_sign_mock = AsyncMock(
            return_value=pre_sign
        )

        signing_mock = AsyncMock(
            return_value=signing
        )

        with (
            patch(
                f"{MODULE}.discover_live_sell_unexecuted_candidates",
                discovery_mock,
            ),
            patch(
                f"{MODULE}.resolve_live_pump_global_state",
                global_mock,
            ),
            patch(
                f"{MODULE}.resolve_pump_sell_v2_account_context",
                context_mock,
            ),
            patch(
                f"{MODULE}.resolve_live_blockhash_context",
                blockhash_mock,
            ),
            patch(
                f"{MODULE}.build_unsigned_pump_sell_v2_message",
                message_mock,
            ),
            patch(
                f"{MODULE}.validate_pump_sell_v2_pre_sign",
                pre_sign_mock,
            ),
            patch(
                f"{MODULE}.sign_and_bind_pump_sell_v2",
                signing_mock,
            ),
        ):
            result = (
                await sign_one_unexecuted_live_sell_once(
                    signer=self.signer,
                    compute_unit_limit=(
                        self.compute_unit_limit
                    ),
                    db_path=self.db_path,
                )
            )

        return (
            result,
            discovery_mock,
            global_mock,
            context_mock,
            blockhash_mock,
            message_mock,
            pre_sign_mock,
            signing_mock,
        )

    def test_version_is_locked(
        self,
    ):
        self.assertEqual(
            LIVE_SELL_UNEXECUTED_EXECUTOR_VERSION,
            "live-sell-unexecuted-executor-v2",
        )

    async def test_empty_discovery_needs_no_signing_configuration(
        self,
    ):
        discovery_mock = MagicMock(
            return_value=self.discovery(
                candidates=(),
            )
        )

        global_mock = AsyncMock()

        with (
            patch(
                f"{MODULE}."
                "discover_live_sell_unexecuted_candidates",
                discovery_mock,
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_global_state",
                global_mock,
            ),
        ):
            result = (
                await sign_one_unexecuted_live_sell_once(
                    signer=None,
                    compute_unit_limit=0,
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            IDLE,
        )
        self.assertEqual(
            result.discovered_candidates,
            0,
        )

        discovery_mock.assert_called_once_with(
            db_path=self.db_path,
        )
        global_mock.assert_not_awaited()

    async def test_candidate_requires_signer_after_discovery(
        self,
    ):
        discovery_mock = MagicMock(
            return_value=self.discovery()
        )

        global_mock = AsyncMock()

        with (
            patch(
                f"{MODULE}."
                "discover_live_sell_unexecuted_candidates",
                discovery_mock,
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_global_state",
                global_mock,
            ),
        ):
            result = (
                await sign_one_unexecuted_live_sell_once(
                    signer=None,
                    compute_unit_limit=(
                        self.compute_unit_limit
                    ),
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertIn(
            "LIVE_SELL_SIGNER_REQUIRED",
            result.reasons,
        )
        self.assertEqual(
            result.discovered_candidates,
            1,
        )

        discovery_mock.assert_called_once_with(
            db_path=self.db_path,
        )
        global_mock.assert_not_awaited()

    async def test_candidate_requires_compute_config_after_discovery(
        self,
    ):
        discovery_mock = MagicMock(
            return_value=self.discovery()
        )

        global_mock = AsyncMock()

        with (
            patch(
                f"{MODULE}."
                "discover_live_sell_unexecuted_candidates",
                discovery_mock,
            ),
            patch(
                f"{MODULE}."
                "resolve_live_pump_global_state",
                global_mock,
            ),
        ):
            result = (
                await sign_one_unexecuted_live_sell_once(
                    signer=self.signer,
                    compute_unit_limit=0,
                    db_path=self.db_path,
                )
            )

        self.assertEqual(
            result.status,
            BLOCK,
        )
        self.assertIn(
            "LIVE_SELL_COMPUTE_UNIT_LIMIT_INVALID",
            result.reasons,
        )
        self.assertEqual(
            result.discovered_candidates,
            1,
        )

        discovery_mock.assert_called_once_with(
            db_path=self.db_path,
        )
        global_mock.assert_not_awaited()

    async def test_empty_discovery_is_idle_without_preparation(
        self,
    ):
        (
            result,
            _,
            global_mock,
            _,
            _,
            _,
            _,
            signing_mock,
        ) = await self.run_executor(
            discovery=self.discovery(
                candidates=(),
            )
        )

        self.assertEqual(
            result.status,
            IDLE,
        )

        global_mock.assert_not_awaited()
        signing_mock.assert_not_awaited()

    async def test_discovery_failure_never_prepares_or_signs(
        self,
    ):
        (
            result,
            _,
            global_mock,
            _,
            _,
            _,
            _,
            signing_mock,
        ) = await self.run_executor(
            discovery=self.discovery(
                status="UNKNOWN",
                reasons=(
                    "DISCOVERY_FAILURE",
                ),
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        global_mock.assert_not_awaited()
        signing_mock.assert_not_awaited()

    async def test_invalid_candidate_never_prepares_or_signs(
        self,
    ):
        bad = SimpleNamespace(
            authorization_sha256=self.sha_a,
            authorization=self.authorization,
            claim=SimpleNamespace(
                authorization_sha256=self.sha_b,
            ),
        )

        (
            result,
            _,
            global_mock,
            _,
            _,
            _,
            _,
            signing_mock,
        ) = await self.run_executor(
            discovery=self.discovery(
                candidates=(
                    bad,
                ),
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        global_mock.assert_not_awaited()
        signing_mock.assert_not_awaited()

    async def test_slot_chain_is_constructed_exactly(
        self,
    ):
        (
            result,
            _,
            global_mock,
            _,
            blockhash_mock,
            message_mock,
            _,
            signing_mock,
        ) = await self.run_executor()

        self.assertEqual(
            result.status,
            SIGNED,
        )

        global_mock.assert_awaited_once_with(
            min_context_slot=100,
        )

        blockhash_mock.assert_awaited_once_with(
            min_context_slot=120,
        )

        message_mock.assert_called_once()

        signing_mock.assert_awaited_once()

        self.assertEqual(
            result.global_rpc_slot,
            120,
        )

        self.assertEqual(
            result.blockhash_min_context_slot,
            120,
        )

        self.assertEqual(
            result.blockhash_rpc_slot,
            130,
        )

    async def test_pre_sign_denial_never_signs(
        self,
    ):
        (
            result,
            *_middle,
            signing_mock,
        ) = await self.run_executor(
            pre_sign=self.pre_sign(
                status=DENY,
                reasons=(
                    "NETWORK_DENIED",
                ),
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "NETWORK_DENIED",
            result.reasons,
        )

        signing_mock.assert_not_awaited()

    async def test_pre_sign_unknown_never_signs(
        self,
    ):
        (
            result,
            *_middle,
            signing_mock,
        ) = await self.run_executor(
            pre_sign=self.pre_sign(
                status=PRE_SIGN_UNKNOWN,
                reasons=(
                    "NETWORK_UNKNOWN",
                ),
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        signing_mock.assert_not_awaited()

    async def test_signing_block_is_propagated_without_submission(
        self,
    ):
        (
            result,
            *_,
        ) = await self.run_executor(
            signing=self.signing(
                status="BLOCK",
                reasons=(
                    "SIGNED_STATE_CONFLICT",
                ),
                durable=False,
            )
        )

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SIGNED_STATE_CONFLICT",
            result.reasons,
        )

    async def test_signing_unknown_is_propagated(
        self,
    ):
        (
            result,
            *_,
        ) = await self.run_executor(
            signing=self.signing(
                status="UNKNOWN",
                reasons=(
                    "SIGNING_UNKNOWN",
                ),
                durable=False,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

    async def test_pass_must_be_durably_signed(
        self,
    ):
        (
            result,
            *_,
        ) = await self.run_executor(
            signing=self.signing(
                status="PASS",
                durable=False,
            )
        )

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_SELL_SIGNING_PASS_NOT_DURABLE",
            result.reasons,
        )

    async def test_multiple_candidates_signs_only_oldest_once(
        self,
    ):
        second_auth = SimpleNamespace(
            authorization_sha256=self.sha_b,
        )

        second_claim = SimpleNamespace(
            authorization_sha256=self.sha_b,
        )

        second = SimpleNamespace(
            authorization_sha256=self.sha_b,
            claimed_at=2.0,
            authorization=second_auth,
            claim=second_claim,
        )

        (
            result,
            _,
            _,
            _,
            _,
            _,
            _,
            signing_mock,
        ) = await self.run_executor(
            discovery=self.discovery(
                candidates=(
                    self.candidate,
                    second,
                ),
            )
        )

        self.assertEqual(
            result.status,
            SIGNED,
        )

        self.assertEqual(
            result.discovered_candidates,
            2,
        )

        self.assertEqual(
            result.authorization_sha256,
            self.sha_a,
        )

        signing_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
