from __future__ import annotations

import base64
import struct
import time
from dataclasses import dataclass

from solders.pubkey import Pubkey

from src.safety.token_safety_resolver import (
    COMMITMENT,
    HeliusRpcClient,
    PUMP_PROGRAM,
    SafetyResolutionError,
    context_slot,
)


LIVE_PUMP_GLOBAL_STATE_VERSION = (
    "live-pump-global-state-v1"
)

GLOBAL_DISCRIMINATOR = bytes(
    [
        167,
        232,
        232,
        177,
        200,
        108,
        114,
        127,
    ]
)

#
# Current Pump Global account layout from the
# official public IDL, including the 8-byte Anchor
# discriminator.
#
# Any protocol layout extension must be reviewed
# before Delve accepts it for live execution.
#
GLOBAL_ACCOUNT_SIZE = 1_045


@dataclass(frozen=True)
class PumpGlobalSnapshot:
    address: str

    account_size: int

    owner_verified: bool
    discriminator_verified: bool

    initialized: bool
    authority: str

    normal_fee_recipients: tuple[
        str,
        ...
    ]

    withdraw_authority: str

    create_v2_enabled: bool
    whitelist_pda: str

    mayhem_fee_recipients: tuple[
        str,
        ...
    ]

    mayhem_mode_enabled: bool
    is_cashback_enabled: bool

    buyback_fee_recipients: tuple[
        str,
        ...
    ]

    buyback_basis_points: int

    initial_virtual_quote_reserves: int

    whitelisted_quote_mints: tuple[
        str,
        ...
    ]


@dataclass(frozen=True)
class LivePumpGlobalState:
    resolver_version: str

    global_state: PumpGlobalSnapshot

    rpc_slot: int
    fetched_at: float


class _GlobalReader:
    def __init__(
        self,
        data: bytes,
    ) -> None:
        self.data = data
        self.offset = 0

    def read(
        self,
        size: int,
    ) -> bytes:

        if size < 0:
            raise SafetyResolutionError(
                "Negative Pump Global read size."
            )

        end = (
            self.offset
            + size
        )

        if end > len(
            self.data
        ):
            raise SafetyResolutionError(
                "Pump Global account ended "
                "unexpectedly."
            )

        result = self.data[
            self.offset:end
        ]

        self.offset = end

        return result

    def boolean(
        self,
        *,
        field: str,
    ) -> bool:

        value = self.read(
            1
        )[0]

        if value not in (
            0,
            1,
        ):
            raise SafetyResolutionError(
                f"Invalid Pump Global "
                f"{field} boolean: {value}."
            )

        return bool(
            value
        )

    def u64(
        self,
    ) -> int:

        return struct.unpack(
            "<Q",
            self.read(
                8
            ),
        )[0]

    def pubkey(
        self,
    ) -> str:

        return str(
            Pubkey.from_bytes(
                self.read(
                    32
                )
            )
        )


def derive_global(
) -> Pubkey:

    address, _ = (
        Pubkey.find_program_address(
            [
                b"global",
            ],
            PUMP_PROGRAM,
        )
    )

    return address


def _decode_account_data(
    account: dict,
) -> bytes:

    data = account.get(
        "data"
    )

    if (
        not isinstance(
            data,
            (list, tuple),
        )
        or len(data) != 2
        or data[1] != "base64"
        or not isinstance(
            data[0],
            str,
        )
    ):
        raise SafetyResolutionError(
            "Pump Global account data "
            "is not base64 encoded."
        )

    try:
        return base64.b64decode(
            data[0],
            validate=True,
        )

    except Exception as error:
        raise SafetyResolutionError(
            "Pump Global account base64 "
            "payload is invalid."
        ) from error


def decode_global_state(
    *,
    address: str,
    account: dict,
) -> PumpGlobalSnapshot:

    try:
        address_pubkey = (
            Pubkey.from_string(
                address
            )
        )

    except Exception as error:
        raise SafetyResolutionError(
            "Invalid Pump Global address."
        ) from error

    expected_global = (
        derive_global()
    )

    if address_pubkey != expected_global:
        raise SafetyResolutionError(
            "Pump Global address does not "
            "match the canonical PDA."
        )

    if not isinstance(
        account,
        dict,
    ):
        raise SafetyResolutionError(
            "Pump Global account is malformed."
        )

    owner = account.get(
        "owner"
    )

    if owner != str(
        PUMP_PROGRAM
    ):
        raise SafetyResolutionError(
            "Pump Global account owner "
            "is invalid."
        )

    raw = _decode_account_data(
        account
    )

    if len(raw) != GLOBAL_ACCOUNT_SIZE:
        raise SafetyResolutionError(
            "Pump Global account size "
            f"is unsupported: {len(raw)}."
        )

    if (
        raw[
            :len(
                GLOBAL_DISCRIMINATOR
            )
        ]
        != GLOBAL_DISCRIMINATOR
    ):
        raise SafetyResolutionError(
            "Pump Global discriminator "
            "is invalid."
        )

    reader = _GlobalReader(
        raw[
            len(
                GLOBAL_DISCRIMINATOR
            ):
        ]
    )

    initialized = reader.boolean(
        field="initialized",
    )

    authority = reader.pubkey()

    primary_fee_recipient = (
        reader.pubkey()
    )

    #
    # Legacy / launch economics retained in Global.
    # They are consumed to preserve exact Borsh
    # alignment even though Delve's live fee engine
    # uses FeeConfig instead.
    #
    reader.u64()  # initial_virtual_token_reserves
    reader.u64()  # initial_virtual_sol_reserves
    reader.u64()  # initial_real_token_reserves
    reader.u64()  # token_total_supply
    reader.u64()  # fee_basis_points

    withdraw_authority = (
        reader.pubkey()
    )

    reader.boolean(
        field="enable_migrate",
    )

    reader.u64()  # pool_migration_fee
    reader.u64()  # creator_fee_basis_points

    secondary_fee_recipients = tuple(
        reader.pubkey()
        for _ in range(
            7
        )
    )

    normal_fee_recipients = (
        (
            primary_fee_recipient,
        )
        + secondary_fee_recipients
    )

    reader.pubkey()  # set_creator_authority
    reader.pubkey()  # admin_set_creator_authority

    create_v2_enabled = reader.boolean(
        field="create_v2_enabled",
    )

    whitelist_pda = (
        reader.pubkey()
    )

    primary_reserved_fee_recipient = (
        reader.pubkey()
    )

    mayhem_mode_enabled = reader.boolean(
        field="mayhem_mode_enabled",
    )

    secondary_reserved_fee_recipients = (
        tuple(
            reader.pubkey()
            for _ in range(
                7
            )
        )
    )

    mayhem_fee_recipients = (
        (
            primary_reserved_fee_recipient,
        )
        + secondary_reserved_fee_recipients
    )

    is_cashback_enabled = reader.boolean(
        field="is_cashback_enabled",
    )

    buyback_fee_recipients = tuple(
        reader.pubkey()
        for _ in range(
            8
        )
    )

    buyback_basis_points = (
        reader.u64()
    )

    initial_virtual_quote_reserves = (
        reader.u64()
    )

    whitelisted_quote_mints = tuple(
        reader.pubkey()
        for _ in range(
            1
        )
    )

    if reader.offset != (
        GLOBAL_ACCOUNT_SIZE
        - len(
            GLOBAL_DISCRIMINATOR
        )
    ):
        raise SafetyResolutionError(
            "Pump Global decoder did not "
            "consume the exact account layout."
        )

    if len(
        normal_fee_recipients
    ) != 8:
        raise SafetyResolutionError(
            "Pump Global normal fee "
            "recipient count is invalid."
        )

    if len(
        mayhem_fee_recipients
    ) != 8:
        raise SafetyResolutionError(
            "Pump Global Mayhem fee "
            "recipient count is invalid."
        )

    if len(
        buyback_fee_recipients
    ) != 8:
        raise SafetyResolutionError(
            "Pump Global buyback fee "
            "recipient count is invalid."
        )

    return PumpGlobalSnapshot(
        address=str(
            address_pubkey
        ),
        account_size=len(
            raw
        ),
        owner_verified=True,
        discriminator_verified=True,
        initialized=initialized,
        authority=authority,
        normal_fee_recipients=(
            normal_fee_recipients
        ),
        withdraw_authority=(
            withdraw_authority
        ),
        create_v2_enabled=(
            create_v2_enabled
        ),
        whitelist_pda=whitelist_pda,
        mayhem_fee_recipients=(
            mayhem_fee_recipients
        ),
        mayhem_mode_enabled=(
            mayhem_mode_enabled
        ),
        is_cashback_enabled=(
            is_cashback_enabled
        ),
        buyback_fee_recipients=(
            buyback_fee_recipients
        ),
        buyback_basis_points=int(
            buyback_basis_points
        ),
        initial_virtual_quote_reserves=int(
            initial_virtual_quote_reserves
        ),
        whitelisted_quote_mints=(
            whitelisted_quote_mints
        ),
    )


async def resolve_live_pump_global_state(
    *,
    min_context_slot: int | None = None,
) -> LivePumpGlobalState:

    global_address = (
        derive_global()
    )

    config = {
        "encoding": "base64",
        "commitment": COMMITMENT,
    }

    if min_context_slot is not None:
        config[
            "minContextSlot"
        ] = int(
            min_context_slot
        )

    async with HeliusRpcClient() as rpc:
        result = await rpc.call(
            "getAccountInfo",
            [
                str(
                    global_address
                ),
                config,
            ],
        )

        fetched_at = time.time()

    if not isinstance(
        result,
        dict,
    ):
        raise SafetyResolutionError(
            "Pump Global RPC response "
            "is malformed."
        )

    slot = context_slot(
        result
    )

    if slot is None:
        raise SafetyResolutionError(
            "Pump Global RPC context "
            "slot is missing."
        )

    account = result.get(
        "value"
    )

    if account is None:
        raise SafetyResolutionError(
            "Pump Global account "
            "does not exist."
        )

    global_state = decode_global_state(
        address=str(
            global_address
        ),
        account=account,
    )

    return LivePumpGlobalState(
        resolver_version=(
            LIVE_PUMP_GLOBAL_STATE_VERSION
        ),
        global_state=global_state,
        rpc_slot=int(
            slot
        ),
        fetched_at=float(
            fetched_at
        ),
    )
