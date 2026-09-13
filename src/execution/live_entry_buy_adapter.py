from __future__ import annotations

from typing import Any

from src.execution.live_buy_execution_config import (
    LIVE_BUY_EXECUTION_CONFIG_VERSION,
    LiveBuyExecutionConfig,
)
from src.execution.live_entry_evidence import (
    LIVE_ENTRY_EVIDENCE_VERSION,
    LiveEntryEvidence,
)
from src.execution.live_process_owner import (
    LIVE_PROCESS_OWNER_VERSION,
    LiveProcessOwner,
)


LIVE_ENTRY_BUY_ADAPTER_VERSION = (
    "live-entry-buy-adapter-v2"
)


class LiveEntryBuyAdapterError(
    RuntimeError
):
    pass


def _components_are_compatible(
) -> bool:
    return (
        LIVE_PROCESS_OWNER_VERSION
        == "live-process-owner-v1"
        and LIVE_ENTRY_EVIDENCE_VERSION
        == "live-entry-evidence-v1"
        and LIVE_BUY_EXECUTION_CONFIG_VERSION
        == "live-buy-execution-config-v1"
    )


def _valid_owner(
    value: object,
) -> bool:
    return isinstance(
        value,
        LiveProcessOwner,
    )


def _valid_evidence(
    value: object,
) -> bool:
    return (
        isinstance(
            value,
            LiveEntryEvidence,
        )
        and value.evidence_version
        == LIVE_ENTRY_EVIDENCE_VERSION
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


async def run_live_entry_buy_once(
    *,
    owner: LiveProcessOwner,
    evidence: LiveEntryEvidence,
    execution_config: LiveBuyExecutionConfig,
) -> Any:
    """
    Invoke at most one live BUY authority call from already-proven
    entry evidence and explicit non-secret execution policy.

    The LiveProcessOwner remains the sole source of:
      - LiveOperatingConfig;
      - process-lifetime authority lease;
      - shared LiveAuthorityGate;
      - production BUY composition.

    This adapter deliberately does not:
      - accept a second LiveOperatingConfig;
      - resolve candidate eligibility;
      - resolve token safety;
      - perform RPC work;
      - resolve wallet balance;
      - recalculate Pump fees;
      - alter evidence freshness;
      - size a position;
      - manipulate RiskPolicy;
      - reserve capital directly;
      - load or invoke a signer directly;
      - construct/sign/submit a transaction directly;
      - retry;
      - loop;
      - schedule background work.

    It performs one identity-preserving mapping into
    LiveProcessOwner.run_buy_once().
    """
    if not _components_are_compatible():
        raise LiveEntryBuyAdapterError(
            "LIVE_ENTRY_BUY_ADAPTER_"
            "COMPONENT_VERSION_MISMATCH"
        )

    if not _valid_owner(
        owner
    ):
        raise TypeError(
            "owner must be LiveProcessOwner"
        )

    if not _valid_evidence(
        evidence
    ):
        raise TypeError(
            "evidence must be LiveEntryEvidence"
        )

    if not _valid_execution_config(
        execution_config
    ):
        raise TypeError(
            "execution_config must be "
            "LiveBuyExecutionConfig"
        )

    return await owner.run_buy_once(
        candidate=evidence.candidate,
        mint=(
            evidence.candidate.mint
        ),
        wallet_pubkey=(
            execution_config.wallet_pubkey
        ),
        protected_cash_lamports=(
            execution_config.protected_cash_lamports
        ),
        live_curve=(
            evidence.live_curve
        ),
        safety=(
            evidence.safety
        ),
        protocol_fee_bps=(
            evidence.fee_state.protocol_fee_bps
        ),
        creator_fee_bps=(
            evidence.fee_state.creator_fee_bps
        ),
        buy_slippage_bps=(
            execution_config.buy_slippage_bps
        ),
        buy_base_network_fee_lamports=(
            execution_config
            .buy_base_network_fee_lamports
        ),
        buy_priority_fee_lamports=(
            execution_config
            .buy_priority_fee_lamports
        ),
        buy_rent_lamports=(
            execution_config.buy_rent_lamports
        ),
        exit_slippage_bps=(
            execution_config.exit_slippage_bps
        ),
        exit_base_network_fee_lamports=(
            execution_config
            .exit_base_network_fee_lamports
        ),
        exit_priority_fee_lamports=(
            execution_config
            .exit_priority_fee_lamports
        ),
        reservation_ttl_seconds=(
            execution_config
            .reservation_ttl_seconds
        ),
        max_authorization_age_seconds=(
            execution_config
            .max_authorization_age_seconds
        ),
        compute_unit_limit=(
            execution_config.compute_unit_limit
        ),
        signal_virtual_quote_reserves=(
            evidence.candidate
            .signal_virtual_quote_reserves
        ),
        signal_virtual_token_reserves=(
            evidence.candidate
            .signal_virtual_token_reserves
        ),
        min_context_slot=(
            evidence.min_context_slot
        ),
    )
