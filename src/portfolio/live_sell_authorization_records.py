from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.execution.exit_execution import (
    ExitExecution,
    PumpBondingCurveExitEvidence,
)
from src.execution.live_pump_sell_authorization import (
    LIVE_PUMP_SELL_AUTHORIZATION_VERSION,
    LivePumpSellAuthorization,
    _authorization_sha256,
)
from src.portfolio.live_reservations import (
    DB_PATH,
    get_connection,
)
from src.portfolio.live_sell_allocation import (
    LiveSellAllocationPlan,
    LiveSellLotAllocation,
)


LIVE_SELL_AUTHORIZATION_RECORD_VERSION = (
    "live-sell-authorization-record-v1"
)

LIVE_SELL_AUTHORIZATION_RECORD_LOADER_VERSION = (
    "live-sell-authorization-record-loader-v1"
)

PASS = "PASS"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveSellAuthorizationRecordResult:
    resolver_version: str

    status: str
    reasons: tuple[str, ...]

    authorization_sha256: str

    authorization: (
        LivePumpSellAuthorization
        | None
    )

    persisted_at: float | None

    changed: bool


_ROOT_KEYS = {
    "authorization_version",
    "authorization_sha256",
    "wallet_pubkey",
    "mint",
    "bonding_curve",
    "base_token_program",
    "associated_base_user",
    "creator",
    "mayhem_mode",
    "curve_quote_mint",
    "quote_mint_for_instruction",
    "tokens_to_sell",
    "allocation",
    "fee_state_version",
    "fee_rpc_slot",
    "fee_fetched_at",
    "protocol_fee_bps",
    "creator_fee_bps",
    "slippage_bps",
    "base_network_fee_lamports",
    "priority_fee_lamports",
    "exit_execution",
    "authorized_at",
}

_ALLOCATION_KEYS = {
    "allocation_version",
    "allocation_method",
    "status",
    "reasons",
    "wallet_pubkey",
    "mint",
    "requested_tokens",
    "total_tokens_before",
    "total_tokens_after",
    "total_exposure_before_lamports",
    "total_exposure_reduction_lamports",
    "total_exposure_after_lamports",
    "total_cost_basis_before_lamports",
    "total_cost_basis_reduction_lamports",
    "total_cost_basis_after_lamports",
    "allocations",
}

_LOT_KEYS = {
    "position_id",
    "position_version",
    "entry_slot",
    "tokens_before",
    "tokens_to_sell",
    "tokens_after",
    "exposure_before_lamports",
    "exposure_reduction_lamports",
    "exposure_after_lamports",
    "cost_basis_before_lamports",
    "cost_basis_reduction_lamports",
    "cost_basis_after_lamports",
    "cumulative_net_proceeds_before_lamports",
    "cumulative_realized_pnl_before_lamports",
}

_EXIT_KEYS = {
    "contract_version",
    "venue",
    "simulator_version",
    "tokens_in",
    "protocol_fee_bps",
    "creator_fee_bps",
    "slippage_bps",
    "gross_quote_out",
    "protocol_fee",
    "creator_fee",
    "net_quote_out_after_venue_fees",
    "min_quote_out",
    "price_impact_bps",
    "base_network_fee_lamports",
    "priority_fee_lamports",
    "total_transaction_overhead_lamports",
    "net_wallet_proceeds_lamports",
    "all_in_exit_price_raw",
    "executable",
    "ineligible_reason",
    "venue_evidence",
}

_EVIDENCE_KEYS = {
    "pre_virtual_quote_reserves",
    "pre_virtual_token_reserves",
    "pre_real_quote_reserves",
    "pre_real_token_reserves",
    "post_virtual_quote_reserves",
    "post_virtual_token_reserves",
    "post_real_quote_reserves",
    "post_real_token_reserves",
}


def _valid_sha256(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            str,
        )
        and len(value) == 64
        and all(
            character
            in "0123456789abcdef"
            for character in value
        )
    )


def _strict_timestamp(
    value: Any,
) -> bool:
    return (
        isinstance(
            value,
            (int, float),
        )
        and not isinstance(
            value,
            bool,
        )
        and math.isfinite(
            float(value)
        )
        and float(value) >= 0.0
    )


def _table_exists(
    *,
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    row = connection.execute(
        """
        SELECT 1

        FROM sqlite_master

        WHERE type = 'table'
          AND name = ?

        LIMIT 1
        """,
        (
            table_name,
        ),
    ).fetchone()

    return row is not None


def _init_live_sell_authorization_record_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS
            live_sell_authorization_records (
                authorization_sha256
                    TEXT PRIMARY KEY,

                record_version
                    TEXT NOT NULL,

                authorization_version
                    TEXT NOT NULL,

                wallet_pubkey
                    TEXT NOT NULL,

                mint
                    TEXT NOT NULL,

                tokens_to_sell
                    INTEGER NOT NULL
                    CHECK (tokens_to_sell > 0),

                payload_json
                    TEXT NOT NULL,

                payload_sha256
                    TEXT NOT NULL,

                persisted_at
                    REAL NOT NULL
            )
        """
    )


def _canonical_authorization_document(
    authorization: LivePumpSellAuthorization,
) -> dict[str, Any]:
    allocation = authorization.allocation
    exit_execution = (
        authorization.exit_execution
    )

    evidence = getattr(
        exit_execution,
        "venue_evidence",
        None,
    )

    if evidence is None:
        evidence_document = None

    elif isinstance(
        evidence,
        PumpBondingCurveExitEvidence,
    ):
        evidence_document = asdict(
            evidence
        )

    else:
        raise ValueError(
            "Unsupported SELL venue evidence."
        )

    allocations = (
        None
        if allocation.allocations is None
        else [
            asdict(
                item
            )
            for item
            in allocation.allocations
        ]
    )

    return {
        "authorization_version": (
            authorization
            .authorization_version
        ),
        "authorization_sha256": (
            authorization
            .authorization_sha256
        ),

        "wallet_pubkey": (
            authorization.wallet_pubkey
        ),
        "mint": authorization.mint,

        "bonding_curve": (
            authorization.bonding_curve
        ),
        "base_token_program": (
            authorization.base_token_program
        ),
        "associated_base_user": (
            authorization.associated_base_user
        ),

        "creator": authorization.creator,
        "mayhem_mode": (
            authorization.mayhem_mode
        ),

        "curve_quote_mint": (
            authorization.curve_quote_mint
        ),
        "quote_mint_for_instruction": (
            authorization
            .quote_mint_for_instruction
        ),

        "tokens_to_sell": (
            authorization.tokens_to_sell
        ),

        "allocation": {
            "allocation_version": (
                allocation.allocation_version
            ),
            "allocation_method": (
                allocation.allocation_method
            ),
            "status": allocation.status,
            "reasons": list(
                allocation.reasons
            ),
            "wallet_pubkey": (
                allocation.wallet_pubkey
            ),
            "mint": allocation.mint,
            "requested_tokens": (
                allocation.requested_tokens
            ),
            "total_tokens_before": (
                allocation.total_tokens_before
            ),
            "total_tokens_after": (
                allocation.total_tokens_after
            ),
            "total_exposure_before_lamports": (
                allocation
                .total_exposure_before_lamports
            ),
            "total_exposure_reduction_lamports": (
                allocation
                .total_exposure_reduction_lamports
            ),
            "total_exposure_after_lamports": (
                allocation
                .total_exposure_after_lamports
            ),
            "total_cost_basis_before_lamports": (
                allocation
                .total_cost_basis_before_lamports
            ),
            "total_cost_basis_reduction_lamports": (
                allocation
                .total_cost_basis_reduction_lamports
            ),
            "total_cost_basis_after_lamports": (
                allocation
                .total_cost_basis_after_lamports
            ),
            "allocations": allocations,
        },

        "fee_state_version": (
            authorization.fee_state_version
        ),
        "fee_rpc_slot": (
            authorization.fee_rpc_slot
        ),
        "fee_fetched_at": (
            authorization.fee_fetched_at
        ),

        "protocol_fee_bps": (
            authorization.protocol_fee_bps
        ),
        "creator_fee_bps": (
            authorization.creator_fee_bps
        ),

        "slippage_bps": (
            authorization.slippage_bps
        ),
        "base_network_fee_lamports": (
            authorization
            .base_network_fee_lamports
        ),
        "priority_fee_lamports": (
            authorization
            .priority_fee_lamports
        ),

        "exit_execution": {
            "contract_version": (
                exit_execution.contract_version
            ),
            "venue": (
                exit_execution.venue
            ),
            "simulator_version": (
                exit_execution.simulator_version
            ),
            "tokens_in": (
                exit_execution.tokens_in
            ),
            "protocol_fee_bps": (
                exit_execution.protocol_fee_bps
            ),
            "creator_fee_bps": (
                exit_execution.creator_fee_bps
            ),
            "slippage_bps": (
                exit_execution.slippage_bps
            ),
            "gross_quote_out": (
                exit_execution.gross_quote_out
            ),
            "protocol_fee": (
                exit_execution.protocol_fee
            ),
            "creator_fee": (
                exit_execution.creator_fee
            ),
            "net_quote_out_after_venue_fees": (
                exit_execution
                .net_quote_out_after_venue_fees
            ),
            "min_quote_out": (
                exit_execution.min_quote_out
            ),

            #
            # These two fields are part of the complete
            # ExitExecution object even though the
            # authorization fingerprint intentionally
            # does not include them.
            #
            "price_impact_bps": getattr(
                exit_execution,
                "price_impact_bps",
                None,
            ),

            "base_network_fee_lamports": (
                exit_execution
                .base_network_fee_lamports
            ),
            "priority_fee_lamports": (
                exit_execution
                .priority_fee_lamports
            ),
            "total_transaction_overhead_lamports": (
                exit_execution
                .total_transaction_overhead_lamports
            ),
            "net_wallet_proceeds_lamports": (
                exit_execution
                .net_wallet_proceeds_lamports
            ),

            "all_in_exit_price_raw": getattr(
                exit_execution,
                "all_in_exit_price_raw",
                None,
            ),

            "executable": (
                exit_execution.executable
            ),
            "ineligible_reason": (
                exit_execution.ineligible_reason
            ),
            "venue_evidence": (
                evidence_document
            ),
        },

        #
        # These timestamps are intentionally retained
        # even though they are not part of the existing
        # authorization SHA fingerprint.
        #
        "authorized_at": (
            authorization.authorized_at
        ),
    }


def _canonical_authorization_payload_json(
    authorization: LivePumpSellAuthorization,
) -> str:
    return json.dumps(
        _canonical_authorization_document(
            authorization
        ),
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
        allow_nan=False,
    )


def _payload_sha256(
    payload_json: str,
) -> str:
    return hashlib.sha256(
        payload_json.encode(
            "utf-8"
        )
    ).hexdigest()


def _authorization_fingerprint_matches(
    authorization: LivePumpSellAuthorization,
) -> bool:
    try:
        expected_sha256 = (
            _authorization_sha256(
                wallet_pubkey=(
                    authorization.wallet_pubkey
                ),
                mint=authorization.mint,
                bonding_curve=(
                    authorization.bonding_curve
                ),
                base_token_program=(
                    authorization.base_token_program
                ),
                associated_base_user=(
                    authorization
                    .associated_base_user
                ),
                creator=authorization.creator,
                mayhem_mode=(
                    authorization.mayhem_mode
                ),
                curve_quote_mint=(
                    authorization
                    .curve_quote_mint
                ),
                quote_mint_for_instruction=(
                    authorization
                    .quote_mint_for_instruction
                ),
                tokens_to_sell=(
                    authorization.tokens_to_sell
                ),
                allocation=(
                    authorization.allocation
                ),
                fee_rpc_slot=(
                    authorization.fee_rpc_slot
                ),
                quote_mint=(
                    authorization
                    .curve_quote_mint
                ),
                protocol_fee_bps=(
                    authorization.protocol_fee_bps
                ),
                creator_fee_bps=(
                    authorization.creator_fee_bps
                ),
                slippage_bps=(
                    authorization.slippage_bps
                ),
                base_network_fee_lamports=(
                    authorization
                    .base_network_fee_lamports
                ),
                priority_fee_lamports=(
                    authorization
                    .priority_fee_lamports
                ),
                exit_execution=(
                    authorization.exit_execution
                ),
            )
        )

    except Exception:
        return False

    return (
        expected_sha256
        == authorization.authorization_sha256
    )


def _decode_authorization(
    payload_json: str,
) -> LivePumpSellAuthorization:
    document = json.loads(
        payload_json
    )

    if (
        not isinstance(
            document,
            dict,
        )
        or set(
            document
        ) != _ROOT_KEYS
    ):
        raise ValueError(
            "SELL authorization payload root invalid."
        )

    allocation_document = (
        document["allocation"]
    )

    if (
        not isinstance(
            allocation_document,
            dict,
        )
        or set(
            allocation_document
        ) != _ALLOCATION_KEYS
    ):
        raise ValueError(
            "SELL allocation payload invalid."
        )

    raw_allocations = (
        allocation_document[
            "allocations"
        ]
    )

    if raw_allocations is None:
        allocations = None

    else:
        if not isinstance(
            raw_allocations,
            list,
        ):
            raise ValueError(
                "SELL allocation lots invalid."
            )

        decoded_lots: list[
            LiveSellLotAllocation
        ] = []

        for item in raw_allocations:
            if (
                not isinstance(
                    item,
                    dict,
                )
                or set(
                    item
                ) != _LOT_KEYS
            ):
                raise ValueError(
                    "SELL allocation lot invalid."
                )

            decoded_lots.append(
                LiveSellLotAllocation(
                    **item
                )
            )

        allocations = tuple(
            decoded_lots
        )

    raw_reasons = (
        allocation_document[
            "reasons"
        ]
    )

    if not isinstance(
        raw_reasons,
        list,
    ):
        raise ValueError(
            "SELL allocation reasons invalid."
        )

    allocation = LiveSellAllocationPlan(
        allocation_version=(
            allocation_document[
                "allocation_version"
            ]
        ),
        allocation_method=(
            allocation_document[
                "allocation_method"
            ]
        ),
        status=(
            allocation_document[
                "status"
            ]
        ),
        reasons=tuple(
            raw_reasons
        ),
        wallet_pubkey=(
            allocation_document[
                "wallet_pubkey"
            ]
        ),
        mint=(
            allocation_document[
                "mint"
            ]
        ),
        requested_tokens=(
            allocation_document[
                "requested_tokens"
            ]
        ),
        total_tokens_before=(
            allocation_document[
                "total_tokens_before"
            ]
        ),
        total_tokens_after=(
            allocation_document[
                "total_tokens_after"
            ]
        ),
        total_exposure_before_lamports=(
            allocation_document[
                "total_exposure_before_lamports"
            ]
        ),
        total_exposure_reduction_lamports=(
            allocation_document[
                "total_exposure_reduction_lamports"
            ]
        ),
        total_exposure_after_lamports=(
            allocation_document[
                "total_exposure_after_lamports"
            ]
        ),
        total_cost_basis_before_lamports=(
            allocation_document[
                "total_cost_basis_before_lamports"
            ]
        ),
        total_cost_basis_reduction_lamports=(
            allocation_document[
                "total_cost_basis_reduction_lamports"
            ]
        ),
        total_cost_basis_after_lamports=(
            allocation_document[
                "total_cost_basis_after_lamports"
            ]
        ),
        allocations=allocations,
    )

    exit_document = (
        document[
            "exit_execution"
        ]
    )

    if (
        not isinstance(
            exit_document,
            dict,
        )
        or set(
            exit_document
        ) != _EXIT_KEYS
    ):
        raise ValueError(
            "SELL exit execution payload invalid."
        )

    evidence_document = (
        exit_document[
            "venue_evidence"
        ]
    )

    if evidence_document is None:
        evidence = None

    else:
        if (
            not isinstance(
                evidence_document,
                dict,
            )
            or set(
                evidence_document
            ) != _EVIDENCE_KEYS
        ):
            raise ValueError(
                "SELL venue evidence payload invalid."
            )

        evidence = (
            PumpBondingCurveExitEvidence(
                **evidence_document
            )
        )

    exit_values = dict(
        exit_document
    )
    exit_values[
        "venue_evidence"
    ] = evidence

    #
    # Production authorizations contain a real
    # ExitExecution. Existing historical tests used
    # a structurally equivalent SimpleNamespace before
    # the complete float fields were introduced.
    #
    # Preserve those legacy/test objects losslessly
    # while recovering the full production type whenever
    # all ExitExecution fields are available.
    #
    if (
        exit_values[
            "price_impact_bps"
        ]
        is not None
        and exit_values[
            "all_in_exit_price_raw"
        ]
        is not None
    ):
        exit_execution: Any = (
            ExitExecution(
                **exit_values
            )
        )

    else:
        exit_values.pop(
            "price_impact_bps",
            None,
        )
        exit_values.pop(
            "all_in_exit_price_raw",
            None,
        )

        exit_execution = (
            SimpleNamespace(
                **exit_values
            )
        )

    authorization = LivePumpSellAuthorization(
        authorization_version=(
            document[
                "authorization_version"
            ]
        ),
        authorization_sha256=(
            document[
                "authorization_sha256"
            ]
        ),
        wallet_pubkey=(
            document[
                "wallet_pubkey"
            ]
        ),
        mint=document["mint"],
        bonding_curve=(
            document[
                "bonding_curve"
            ]
        ),
        base_token_program=(
            document[
                "base_token_program"
            ]
        ),
        associated_base_user=(
            document[
                "associated_base_user"
            ]
        ),
        creator=(
            document[
                "creator"
            ]
        ),
        mayhem_mode=(
            document[
                "mayhem_mode"
            ]
        ),
        curve_quote_mint=(
            document[
                "curve_quote_mint"
            ]
        ),
        quote_mint_for_instruction=(
            document[
                "quote_mint_for_instruction"
            ]
        ),
        tokens_to_sell=(
            document[
                "tokens_to_sell"
            ]
        ),
        allocation=allocation,
        fee_state_version=(
            document[
                "fee_state_version"
            ]
        ),
        fee_rpc_slot=(
            document[
                "fee_rpc_slot"
            ]
        ),
        fee_fetched_at=(
            document[
                "fee_fetched_at"
            ]
        ),
        protocol_fee_bps=(
            document[
                "protocol_fee_bps"
            ]
        ),
        creator_fee_bps=(
            document[
                "creator_fee_bps"
            ]
        ),
        slippage_bps=(
            document[
                "slippage_bps"
            ]
        ),
        base_network_fee_lamports=(
            document[
                "base_network_fee_lamports"
            ]
        ),
        priority_fee_lamports=(
            document[
                "priority_fee_lamports"
            ]
        ),
        exit_execution=(
            exit_execution
        ),
        authorized_at=(
            document[
                "authorized_at"
            ]
        ),
    )

    if (
        authorization.authorization_version
        != LIVE_PUMP_SELL_AUTHORIZATION_VERSION
    ):
        raise ValueError(
            "SELL authorization version invalid."
        )

    if not _valid_sha256(
        authorization
        .authorization_sha256
    ):
        raise ValueError(
            "SELL authorization SHA invalid."
        )

    if not _strict_timestamp(
        authorization.fee_fetched_at
    ):
        raise ValueError(
            "SELL fee timestamp invalid."
        )

    if not _strict_timestamp(
        authorization.authorized_at
    ):
        raise ValueError(
            "SELL authorization timestamp invalid."
        )

    if not _authorization_fingerprint_matches(
        authorization
    ):
        raise ValueError(
            "SELL authorization fingerprint mismatch."
        )

    return authorization


def _result_from_row(
    *,
    row: sqlite3.Row,
    authorization_sha256: str,
) -> LiveSellAuthorizationRecordResult:
    def finish(
        status: str,
        *reasons: str,
        authorization: (
            LivePumpSellAuthorization
            | None
        ) = None,
        persisted_at: float | None = None,
    ) -> LiveSellAuthorizationRecordResult:
        return LiveSellAuthorizationRecordResult(
            resolver_version=(
                LIVE_SELL_AUTHORIZATION_RECORD_LOADER_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            authorization=authorization,
            persisted_at=persisted_at,
            changed=False,
        )

    try:
        if (
            str(
                row[
                    "record_version"
                ]
            )
            != LIVE_SELL_AUTHORIZATION_RECORD_VERSION
        ):
            return finish(
                UNKNOWN,
                "SELL_AUTHORIZATION_RECORD_VERSION_MISMATCH",
            )

        if (
            str(
                row[
                    "authorization_version"
                ]
            )
            != LIVE_PUMP_SELL_AUTHORIZATION_VERSION
        ):
            return finish(
                UNKNOWN,
                "SELL_AUTHORIZATION_VERSION_MISMATCH",
            )

        if (
            str(
                row[
                    "authorization_sha256"
                ]
            )
            != authorization_sha256
        ):
            return finish(
                UNKNOWN,
                "SELL_AUTHORIZATION_RECORD_SHA_BINDING_MISMATCH",
            )

        payload_json = str(
            row[
                "payload_json"
            ]
        )

        payload_sha256 = str(
            row[
                "payload_sha256"
            ]
        )

        if (
            not _valid_sha256(
                payload_sha256
            )
            or _payload_sha256(
                payload_json
            )
            != payload_sha256
        ):
            return finish(
                UNKNOWN,
                "SELL_AUTHORIZATION_PAYLOAD_HASH_MISMATCH",
            )

        persisted_at = float(
            row[
                "persisted_at"
            ]
        )

        if not _strict_timestamp(
            persisted_at
        ):
            return finish(
                UNKNOWN,
                "SELL_AUTHORIZATION_PERSISTED_AT_INVALID",
            )

        authorization = (
            _decode_authorization(
                payload_json
            )
        )

        if (
            authorization
            .authorization_sha256
            != authorization_sha256
            or authorization
            .wallet_pubkey
            != str(
                row[
                    "wallet_pubkey"
                ]
            )
            or authorization.mint
            != str(
                row[
                    "mint"
                ]
            )
            or authorization
            .tokens_to_sell
            != int(
                row[
                    "tokens_to_sell"
                ]
            )
        ):
            return finish(
                UNKNOWN,
                "SELL_AUTHORIZATION_RECORD_IDENTITY_MISMATCH",
            )

        if (
            _canonical_authorization_payload_json(
                authorization
            )
            != payload_json
        ):
            return finish(
                UNKNOWN,
                "SELL_AUTHORIZATION_PAYLOAD_NOT_CANONICAL",
            )

        return finish(
            PASS,
            authorization=(
                authorization
            ),
            persisted_at=(
                persisted_at
            ),
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_AUTHORIZATION_RECORD_INVALID",
        )


def _load_live_sell_authorization_record_in_transaction(
    *,
    connection: sqlite3.Connection,
    authorization_sha256: str,
) -> LiveSellAuthorizationRecordResult:
    if not _table_exists(
        connection=connection,
        table_name=(
            "live_sell_authorization_records"
        ),
    ):
        return LiveSellAuthorizationRecordResult(
            resolver_version=(
                LIVE_SELL_AUTHORIZATION_RECORD_LOADER_VERSION
            ),
            status=UNKNOWN,
            reasons=(
                "LIVE_SELL_AUTHORIZATION_RECORD_TABLE_NOT_FOUND",
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            authorization=None,
            persisted_at=None,
            changed=False,
        )

    row = connection.execute(
        """
        SELECT *

        FROM live_sell_authorization_records

        WHERE authorization_sha256 = ?
        """,
        (
            authorization_sha256,
        ),
    ).fetchone()

    if row is None:
        return LiveSellAuthorizationRecordResult(
            resolver_version=(
                LIVE_SELL_AUTHORIZATION_RECORD_LOADER_VERSION
            ),
            status=BLOCK,
            reasons=(
                "SELL_AUTHORIZATION_RECORD_NOT_FOUND",
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            authorization=None,
            persisted_at=None,
            changed=False,
        )

    return _result_from_row(
        row=row,
        authorization_sha256=(
            authorization_sha256
        ),
    )


def _persist_live_sell_authorization_record_in_transaction(
    *,
    connection: sqlite3.Connection,
    authorization: LivePumpSellAuthorization,
) -> LiveSellAuthorizationRecordResult:
    authorization_sha256 = (
        authorization.authorization_sha256
    )

    def finish(
        status: str,
        *reasons: str,
        recovered: (
            LivePumpSellAuthorization
            | None
        ) = None,
        persisted_at: float | None = None,
        changed: bool = False,
    ) -> LiveSellAuthorizationRecordResult:
        return LiveSellAuthorizationRecordResult(
            resolver_version=(
                LIVE_SELL_AUTHORIZATION_RECORD_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            authorization=recovered,
            persisted_at=persisted_at,
            changed=changed,
        )

    if (
        authorization.authorization_version
        != LIVE_PUMP_SELL_AUTHORIZATION_VERSION
        or not _valid_sha256(
            authorization_sha256
        )
        or not _authorization_fingerprint_matches(
            authorization
        )
    ):
        return finish(
            UNKNOWN,
            "SELL_AUTHORIZATION_RECORD_INPUT_INVALID",
        )

    try:
        payload_json = (
            _canonical_authorization_payload_json(
                authorization
            )
        )
        payload_sha256 = (
            _payload_sha256(
                payload_json
            )
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_AUTHORIZATION_PAYLOAD_ENCODING_FAILED",
        )

    _init_live_sell_authorization_record_schema(
        connection
    )

    existing = (
        _load_live_sell_authorization_record_in_transaction(
            connection=connection,
            authorization_sha256=(
                authorization_sha256
            ),
        )
    )

    if existing.status == PASS:
        if (
            existing.authorization
            is None
            or _canonical_authorization_payload_json(
                existing.authorization
            )
            != payload_json
        ):
            return finish(
                UNKNOWN,
                "SELL_AUTHORIZATION_RECORD_EVIDENCE_MISMATCH",
                recovered=(
                    existing.authorization
                ),
                persisted_at=(
                    existing.persisted_at
                ),
            )

        return finish(
            PASS,
            recovered=(
                existing.authorization
            ),
            persisted_at=(
                existing.persisted_at
            ),
            changed=False,
        )

    if (
        existing.status != BLOCK
        or existing.reasons
        != (
            "SELL_AUTHORIZATION_RECORD_NOT_FOUND",
        )
    ):
        return finish(
            UNKNOWN,
            "SELL_AUTHORIZATION_RECORD_EXISTING_STATE_INVALID",
            *existing.reasons,
        )

    persisted_at = time.time()

    if not _strict_timestamp(
        persisted_at
    ):
        return finish(
            UNKNOWN,
            "SYSTEM_TIME_INVALID",
        )

    connection.execute(
        """
        INSERT INTO
            live_sell_authorization_records (
                authorization_sha256,
                record_version,
                authorization_version,
                wallet_pubkey,
                mint,
                tokens_to_sell,
                payload_json,
                payload_sha256,
                persisted_at
            )

        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            authorization_sha256,
            LIVE_SELL_AUTHORIZATION_RECORD_VERSION,
            authorization.authorization_version,
            authorization.wallet_pubkey,
            authorization.mint,
            authorization.tokens_to_sell,
            payload_json,
            payload_sha256,
            persisted_at,
        ),
    )

    persisted = (
        _load_live_sell_authorization_record_in_transaction(
            connection=connection,
            authorization_sha256=(
                authorization_sha256
            ),
        )
    )

    if (
        persisted.status != PASS
        or persisted.authorization
        is None
        or _canonical_authorization_payload_json(
            persisted.authorization
        )
        != payload_json
    ):
        return finish(
            UNKNOWN,
            "SELL_AUTHORIZATION_RECORD_ATOMIC_VERIFICATION_FAILED",
        )

    return finish(
        PASS,
        recovered=(
            persisted.authorization
        ),
        persisted_at=(
            persisted.persisted_at
        ),
        changed=True,
    )


def load_live_sell_authorization_record_read_only(
    *,
    authorization_sha256: str,
    db_path: Path = DB_PATH,
) -> LiveSellAuthorizationRecordResult:
    if isinstance(
        authorization_sha256,
        str,
    ):
        authorization_sha256 = (
            authorization_sha256.strip()
        )

    else:
        authorization_sha256 = ""

    def finish(
        status: str,
        *reasons: str,
    ) -> LiveSellAuthorizationRecordResult:
        return LiveSellAuthorizationRecordResult(
            resolver_version=(
                LIVE_SELL_AUTHORIZATION_RECORD_LOADER_VERSION
            ),
            status=status,
            reasons=tuple(
                reasons
            ),
            authorization_sha256=(
                authorization_sha256
            ),
            authorization=None,
            persisted_at=None,
            changed=False,
        )

    if not _valid_sha256(
        authorization_sha256
    ):
        return finish(
            UNKNOWN,
            "INVALID_AUTHORIZATION_SHA256",
        )

    try:
        normalized_path = Path(
            db_path
        )

        if not normalized_path.exists():
            return finish(
                UNKNOWN,
                "LIVE_DATABASE_NOT_FOUND",
            )

    except Exception:
        return finish(
            UNKNOWN,
            "LIVE_DATABASE_PATH_INVALID",
        )

    connection: sqlite3.Connection | None = None

    try:
        connection = get_connection(
            normalized_path
        )

        return (
            _load_live_sell_authorization_record_in_transaction(
                connection=connection,
                authorization_sha256=(
                    authorization_sha256
                ),
            )
        )

    except sqlite3.Error:
        return finish(
            UNKNOWN,
            "SELL_AUTHORIZATION_RECORD_DATABASE_ERROR",
        )

    except Exception:
        return finish(
            UNKNOWN,
            "SELL_AUTHORIZATION_RECORD_READ_FAILED",
        )

    finally:
        if connection is not None:
            connection.close()
