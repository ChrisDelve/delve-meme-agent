from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from src.execution.live_pump_sell_authorization import (
    AUTHORIZED,
    BLOCK as AUTHORIZATION_BLOCK,
    UNKNOWN as AUTHORIZATION_UNKNOWN,
    LIVE_PUMP_SELL_AUTHORIZATION_VERSION,
    LivePumpSellAuthorizationResult,
)
from src.execution.live_sell_unexecuted_discovery import (
    PASS as DISCOVERY_PASS,
    discover_live_sell_unexecuted_candidates,
)
from src.execution.live_sell_initiator import (
    BLOCK,
    CLAIMED,
    UNKNOWN,
    initiate_live_sell_once,
)
from src.portfolio.live_sell_claims import (
    ACTIVE,
    BLOCK as CLAIM_BLOCK,
    PASS as CLAIM_PASS,
    UNKNOWN as CLAIM_UNKNOWN,
    LIVE_SELL_INVENTORY_CLAIM_VERSION,
    LiveSellInventoryClaim,
    LiveSellInventoryClaimResult,
)

from tests import (
    test_live_sell_claims as claim_test_helpers,
)


MODULE = "src.execution.live_sell_initiator"


class LiveSellInitiatorTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        #
        # Reuse the existing real-capital claim fixture.
        #
        # This gives us:
        # - real Pubkeys
        # - real LivePosition rows
        # - real temporary SQLite DB
        # - real FIFO allocation
        # - real LivePumpSellAuthorization
        #
        self.fixture = (
            claim_test_helpers.LiveSellInventoryClaimTests(
                methodName=(
                    "test_claim_persists_exact_fifo_"
                    "allocation_without_mutating_positions"
                )
            )
        )

        self.fixture.setUp()
        self.addCleanup(
            self.fixture.tearDown
        )

        self.authorization = (
            self.fixture.authorization()
        )

        self.wallet = self.fixture.wallet
        self.mint = self.fixture.mint
        self.db_path = self.fixture.db_path

        self.tokens_to_sell = (
            self.authorization.tokens_to_sell
        )

        self.slippage_bps = (
            self.authorization.slippage_bps
        )

        self.base_network_fee_lamports = (
            self.authorization
            .base_network_fee_lamports
        )

        self.priority_fee_lamports = (
            self.authorization
            .priority_fee_lamports
        )

    def authorization_result(
        self,
        *,
        status=AUTHORIZED,
        reasons=(),
        resolver_version=(
            LIVE_PUMP_SELL_AUTHORIZATION_VERSION
        ),
        authorization=None,
        wallet_pubkey=None,
        mint=None,
        allocation="DEFAULT",
        exit_execution="DEFAULT",
    ):
        if (
            authorization is None
            and status == AUTHORIZED
        ):
            authorization = (
                self.authorization
            )

        if wallet_pubkey is None:
            wallet_pubkey = self.wallet

        if mint is None:
            mint = self.mint

        if allocation == "DEFAULT":
            allocation = (
                authorization.allocation
                if authorization is not None
                else None
            )

        if exit_execution == "DEFAULT":
            exit_execution = (
                authorization.exit_execution
                if authorization is not None
                else None
            )

        return LivePumpSellAuthorizationResult(
            resolver_version=(
                resolver_version
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            wallet_pubkey=wallet_pubkey,
            mint=mint,
            allocation=allocation,
            exit_execution=exit_execution,
            authorization=authorization,
        )

    def claim(
        self,
    ):
        return LiveSellInventoryClaim(
            claim_version=(
                LIVE_SELL_INVENTORY_CLAIM_VERSION
            ),
            authorization_version=(
                self.authorization
                .authorization_version
            ),
            authorization_sha256=(
                self.authorization
                .authorization_sha256
            ),
            wallet_pubkey=(
                self.authorization.wallet_pubkey
            ),
            mint=self.authorization.mint,
            tokens_to_sell=(
                self.authorization.tokens_to_sell
            ),
            allocation=(
                self.authorization.allocation
            ),
            status=ACTIVE,
            claimed_at=123.0,
            terminal_at=None,
            terminal_reason=None,
        )

    def claim_result(
        self,
        *,
        status=CLAIM_PASS,
        reasons=(),
        claim=None,
        changed=True,
        resolver_version=(
            LIVE_SELL_INVENTORY_CLAIM_VERSION
        ),
        authorization_sha256=None,
    ):
        if (
            claim is None
            and status == CLAIM_PASS
        ):
            claim = self.claim()

        if authorization_sha256 is None:
            authorization_sha256 = (
                self.authorization
                .authorization_sha256
            )

        return LiveSellInventoryClaimResult(
            resolver_version=(
                resolver_version
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            claim=claim,
            changed=changed,
        )

    async def initiate(
        self,
    ):
        return await initiate_live_sell_once(
            wallet_pubkey=self.wallet,
            mint=self.mint,
            tokens_to_sell=(
                self.tokens_to_sell
            ),
            slippage_bps=(
                self.slippage_bps
            ),
            base_network_fee_lamports=(
                self.base_network_fee_lamports
            ),
            priority_fee_lamports=(
                self.priority_fee_lamports
            ),
            db_path=self.db_path,
        )

    async def test_authorized_request_creates_real_durable_claim(
        self,
    ):
        authorization_result = (
            self.authorization_result()
        )

        authorizer = AsyncMock(
            return_value=(
                authorization_result
            )
        )

        with patch(
            f"{MODULE}.authorize_live_pump_sell",
            authorizer,
        ):
            result = await self.initiate()

        self.assertEqual(
            result.status,
            CLAIMED,
        )

        self.assertEqual(
            result.authorization_sha256,
            self.authorization
            .authorization_sha256,
        )

        self.assertEqual(
            result.authorization_status,
            AUTHORIZED,
        )

        self.assertEqual(
            result.claim_status,
            CLAIM_PASS,
        )

        self.assertTrue(
            result.claim_changed
        )

        self.assertIs(
            result.authorization,
            self.authorization,
        )

        self.assertIsNotNone(
            result.claim
        )

        self.assertEqual(
            result.claim.status,
            ACTIVE,
        )

        self.assertEqual(
            result.claim.allocation,
            self.authorization.allocation,
        )

        authorizer.assert_awaited_once_with(
            wallet_pubkey=self.wallet,
            mint=self.mint,
            tokens_to_sell=(
                self.tokens_to_sell
            ),
            slippage_bps=(
                self.slippage_bps
            ),
            base_network_fee_lamports=(
                self.base_network_fee_lamports
            ),
            priority_fee_lamports=(
                self.priority_fee_lamports
            ),
        )

    async def test_claimed_sell_is_immediately_discoverable_for_signing(
        self,
    ):
        authorizer = AsyncMock(
            return_value=(
                self.authorization_result()
            )
        )

        with patch(
            f"{MODULE}.authorize_live_pump_sell",
            authorizer,
        ):
            initiation = (
                await self.initiate()
            )

        self.assertEqual(
            initiation.status,
            CLAIMED,
        )

        discovery = (
            discover_live_sell_unexecuted_candidates(
                db_path=self.db_path,
            )
        )

        self.assertEqual(
            discovery.status,
            DISCOVERY_PASS,
        )

        self.assertEqual(
            len(discovery.candidates),
            1,
        )

        candidate = discovery.candidates[0]

        self.assertEqual(
            candidate.authorization_sha256,
            initiation.authorization_sha256,
        )

        self.assertEqual(
            candidate.authorization,
            initiation.authorization,
        )

        self.assertEqual(
            candidate.claim,
            initiation.claim,
        )

        self.assertEqual(
            candidate.claim.status,
            ACTIVE,
        )

    async def test_exact_retry_is_claimed_without_second_mutation(
        self,
    ):
        authorization_result = (
            self.authorization_result()
        )

        authorizer = AsyncMock(
            return_value=(
                authorization_result
            )
        )

        with patch(
            f"{MODULE}.authorize_live_pump_sell",
            authorizer,
        ):
            first = await self.initiate()
            second = await self.initiate()

        self.assertEqual(
            first.status,
            CLAIMED,
        )

        self.assertTrue(
            first.claim_changed
        )

        self.assertEqual(
            second.status,
            CLAIMED,
        )

        self.assertFalse(
            second.claim_changed
        )

        self.assertEqual(
            first.authorization_sha256,
            second.authorization_sha256,
        )

        self.assertEqual(
            first.claim,
            second.claim,
        )

        self.assertEqual(
            authorizer.await_count,
            2,
        )

    async def test_authorization_block_never_attempts_claim(
        self,
    ):
        authorizer = AsyncMock(
            return_value=(
                self.authorization_result(
                    status=(
                        AUTHORIZATION_BLOCK
                    ),
                    reasons=(
                        "ECONOMICS_BLOCKED",
                    ),
                    authorization=None,
                )
            )
        )

        claim_mock = MagicMock()

        with (
            patch(
                f"{MODULE}.authorize_live_pump_sell",
                authorizer,
            ),
            patch(
                f"{MODULE}.acquire_live_sell_inventory_claim",
                claim_mock,
            ),
        ):
            result = await self.initiate()

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "ECONOMICS_BLOCKED",
            result.reasons,
        )

        claim_mock.assert_not_called()

    async def test_authorization_unknown_never_attempts_claim(
        self,
    ):
        authorizer = AsyncMock(
            return_value=(
                self.authorization_result(
                    status=(
                        AUTHORIZATION_UNKNOWN
                    ),
                    reasons=(
                        "CHAIN_STATE_UNKNOWN",
                    ),
                    authorization=None,
                )
            )
        )

        claim_mock = MagicMock()

        with (
            patch(
                f"{MODULE}.authorize_live_pump_sell",
                authorizer,
            ),
            patch(
                f"{MODULE}.acquire_live_sell_inventory_claim",
                claim_mock,
            ),
        ):
            result = await self.initiate()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        claim_mock.assert_not_called()

    async def test_authorization_wrapper_version_mismatch_never_claims(
        self,
    ):
        authorizer = AsyncMock(
            return_value=(
                self.authorization_result(
                    resolver_version=(
                        "stale-authorization-version"
                    ),
                )
            )
        )

        claim_mock = MagicMock()

        with (
            patch(
                f"{MODULE}.authorize_live_pump_sell",
                authorizer,
            ),
            patch(
                f"{MODULE}.acquire_live_sell_inventory_claim",
                claim_mock,
            ),
        ):
            result = await self.initiate()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_SELL_AUTHORIZATION_VERSION_MISMATCH",
            result.reasons,
        )

        claim_mock.assert_not_called()

    async def test_authorization_wrapper_binding_mismatch_never_claims(
        self,
    ):
        authorizer = AsyncMock(
            return_value=(
                self.authorization_result(
                    allocation=None,
                )
            )
        )

        claim_mock = MagicMock()

        with (
            patch(
                f"{MODULE}.authorize_live_pump_sell",
                authorizer,
            ),
            patch(
                f"{MODULE}.acquire_live_sell_inventory_claim",
                claim_mock,
            ),
        ):
            result = await self.initiate()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_SELL_AUTHORIZATION_BINDING_MISMATCH",
            result.reasons,
        )

        claim_mock.assert_not_called()

    async def test_authorization_for_different_request_never_claims(
        self,
    ):
        different_wallet = (
            claim_test_helpers.Pubkey.new_unique()
        )

        mismatched_authorization = replace(
            self.authorization,
            wallet_pubkey=str(
                different_wallet
            ),
        )

        authorizer = AsyncMock(
            return_value=(
                self.authorization_result(
                    authorization=(
                        mismatched_authorization
                    ),
                    wallet_pubkey=(
                        mismatched_authorization
                        .wallet_pubkey
                    ),
                    allocation=(
                        mismatched_authorization
                        .allocation
                    ),
                    exit_execution=(
                        mismatched_authorization
                        .exit_execution
                    ),
                )
            )
        )

        claim_mock = MagicMock()

        with (
            patch(
                f"{MODULE}.authorize_live_pump_sell",
                authorizer,
            ),
            patch(
                f"{MODULE}.acquire_live_sell_inventory_claim",
                claim_mock,
            ),
        ):
            result = await self.initiate()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_SELL_AUTHORIZATION_BINDING_MISMATCH",
            result.reasons,
        )

        claim_mock.assert_not_called()

    async def test_competing_active_claim_block_is_propagated(
        self,
    ):
        authorizer = AsyncMock(
            return_value=(
                self.authorization_result()
            )
        )

        claim_mock = MagicMock(
            return_value=(
                self.claim_result(
                    status=CLAIM_BLOCK,
                    reasons=(
                        "SELL_INVENTORY_ALREADY_CLAIMED",
                    ),
                    claim=None,
                    changed=False,
                )
            )
        )

        with (
            patch(
                f"{MODULE}.authorize_live_pump_sell",
                authorizer,
            ),
            patch(
                f"{MODULE}.acquire_live_sell_inventory_claim",
                claim_mock,
            ),
        ):
            result = await self.initiate()

        self.assertEqual(
            result.status,
            BLOCK,
        )

        self.assertIn(
            "SELL_INVENTORY_ALREADY_CLAIMED",
            result.reasons,
        )

        claim_mock.assert_called_once_with(
            authorization=(
                self.authorization
            ),
            db_path=self.db_path,
        )

    async def test_claim_unknown_is_propagated(
        self,
    ):
        with (
            patch(
                f"{MODULE}.authorize_live_pump_sell",
                AsyncMock(
                    return_value=(
                        self.authorization_result()
                    )
                ),
            ),
            patch(
                f"{MODULE}.acquire_live_sell_inventory_claim",
                MagicMock(
                    return_value=(
                        self.claim_result(
                            status=CLAIM_UNKNOWN,
                            reasons=(
                                "CLAIM_STATE_UNKNOWN",
                            ),
                            claim=None,
                            changed=False,
                        )
                    )
                ),
            ),
        ):
            result = await self.initiate()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "CLAIM_STATE_UNKNOWN",
            result.reasons,
        )

    async def test_claim_wrapper_version_mismatch_fails_closed(
        self,
    ):
        with (
            patch(
                f"{MODULE}.authorize_live_pump_sell",
                AsyncMock(
                    return_value=(
                        self.authorization_result()
                    )
                ),
            ),
            patch(
                f"{MODULE}.acquire_live_sell_inventory_claim",
                MagicMock(
                    return_value=(
                        self.claim_result(
                            resolver_version=(
                                "stale-claim-version"
                            ),
                        )
                    )
                ),
            ),
        ):
            result = await self.initiate()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_SELL_CLAIM_VERSION_MISMATCH",
            result.reasons,
        )

    async def test_claim_binding_mismatch_fails_closed(
        self,
    ):
        mismatched_claim = replace(
            self.claim(),
            mint="different-mint",
        )

        with (
            patch(
                f"{MODULE}.authorize_live_pump_sell",
                AsyncMock(
                    return_value=(
                        self.authorization_result()
                    )
                ),
            ),
            patch(
                f"{MODULE}.acquire_live_sell_inventory_claim",
                MagicMock(
                    return_value=(
                        self.claim_result(
                            claim=(
                                mismatched_claim
                            ),
                        )
                    )
                ),
            ),
        ):
            result = await self.initiate()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_SELL_CLAIM_BINDING_MISMATCH",
            result.reasons,
        )

    async def test_claim_exception_fails_closed(
        self,
    ):
        with (
            patch(
                f"{MODULE}.authorize_live_pump_sell",
                AsyncMock(
                    return_value=(
                        self.authorization_result()
                    )
                ),
            ),
            patch(
                f"{MODULE}.acquire_live_sell_inventory_claim",
                MagicMock(
                    side_effect=RuntimeError(
                        "db failure"
                    )
                ),
            ),
        ):
            result = await self.initiate()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_SELL_CLAIM_EXCEPTION",
            result.reasons,
        )

    async def test_authorization_exception_fails_closed(
        self,
    ):
        claim_mock = MagicMock()

        with (
            patch(
                f"{MODULE}.authorize_live_pump_sell",
                AsyncMock(
                    side_effect=RuntimeError(
                        "rpc failure"
                    )
                ),
            ),
            patch(
                f"{MODULE}.acquire_live_sell_inventory_claim",
                claim_mock,
            ),
        ):
            result = await self.initiate()

        self.assertEqual(
            result.status,
            UNKNOWN,
        )

        self.assertIn(
            "LIVE_SELL_AUTHORIZATION_EXCEPTION",
            result.reasons,
        )

        claim_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
