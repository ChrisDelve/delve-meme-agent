from __future__ import annotations

import base64
import struct
import time
from dataclasses import dataclass

from solders.pubkey import Pubkey

from src.safety.token_safety_resolver import (
    BondingCurveSnapshot,
    COMMITMENT,
    HeliusRpcClient,
    PUMP_PROGRAM,
    SafetyResolutionError,
    context_slot,
    decode_bonding_curve,
    derive_bonding_curve,
)


LIVE_PUMP_FEE_STATE_VERSION = (
    "live-pump-fee-state-v1"
)

PUMP_FEE_PROGRAM = Pubkey.from_string(
    "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"
)

FEE_CONFIG_DISCRIMINATOR = bytes(
    [
        143,
        52,
        146,
        187,
        219,
        123,
        76,
        155,
    ]
)

MAX_FEE_TIERS = 128
BPS_DENOMINATOR = 10_000


@dataclass(frozen=True)
class PumpFees:
    lp_fee_bps: int
    protocol_fee_bps: int
    creator_fee_bps: int


@dataclass(frozen=True)
class PumpFeeTier:
    market_cap_lamports_threshold: int
    fees: PumpFees


@dataclass(frozen=True)
class PumpFeeConfigSnapshot:
    address: str

    account_size: int

    owner_verified: bool
    discriminator_verified: bool

    bump: int
    admin: str

    flat_fees: PumpFees

    fee_tiers: tuple[
        PumpFeeTier,
        ...
    ]

    stable_fee_tiers: tuple[
        PumpFeeTier,
        ...
    ]


@dataclass(frozen=True)
class LivePumpFeeState:
    resolver_version: str

    mint: str

    curve: BondingCurveSnapshot
    fee_config: PumpFeeConfigSnapshot

    market_cap_lamports: int

    selected_tier_index: int
    selected_threshold_lamports: int

    lp_fee_bps: int
    protocol_fee_bps: int
    creator_fee_bps: int

    rpc_slot: int
    fetched_at: float


class _BorshReader:
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
                "Negative Borsh read size."
            )

        end = (
            self.offset
            + size
        )

        if end > len(
            self.data
        ):
            raise SafetyResolutionError(
                "Pump FeeConfig account "
                "ended unexpectedly."
            )

        result = self.data[
            self.offset:end
        ]

        self.offset = end

        return result

    def u8(
        self,
    ) -> int:
        return self.read(
            1
        )[0]

    def u32(
        self,
    ) -> int:
        return struct.unpack(
            "<I",
            self.read(
                4
            ),
        )[0]

    def u64(
        self,
    ) -> int:
        return struct.unpack(
            "<Q",
            self.read(
                8
            ),
        )[0]

    def u128(
        self,
    ) -> int:
        return int.from_bytes(
            self.read(
                16
            ),
            byteorder="little",
            signed=False,
        )

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


def derive_fee_config(
) -> Pubkey:

    address, _ = (
        Pubkey.find_program_address(
            [
                b"fee_config",
                bytes(
                    PUMP_PROGRAM
                ),
            ],
            PUMP_FEE_PROGRAM,
        )
    )

    return address


def _read_fees(
    reader: _BorshReader,
) -> PumpFees:

    fees = PumpFees(
        lp_fee_bps=reader.u64(),
        protocol_fee_bps=reader.u64(),
        creator_fee_bps=reader.u64(),
    )

    for name, value in (
        (
            "lp_fee_bps",
            fees.lp_fee_bps,
        ),
        (
            "protocol_fee_bps",
            fees.protocol_fee_bps,
        ),
        (
            "creator_fee_bps",
            fees.creator_fee_bps,
        ),
    ):
        if (
            value < 0
            or value
            > BPS_DENOMINATOR
        ):
            raise SafetyResolutionError(
                f"Invalid Pump {name}: "
                f"{value}."
            )

    return fees


def _read_fee_tiers(
    reader: _BorshReader,
    *,
    label: str,
) -> tuple[
    PumpFeeTier,
    ...
]:

    count = reader.u32()

    if count > MAX_FEE_TIERS:
        raise SafetyResolutionError(
            f"Pump {label} contains "
            f"too many tiers: {count}."
        )

    tiers: list[
        PumpFeeTier
    ] = []

    for _ in range(
        count
    ):
        tiers.append(
            PumpFeeTier(
                market_cap_lamports_threshold=(
                    reader.u128()
                ),
                fees=_read_fees(
                    reader
                ),
            )
        )

    previous_threshold: (
        int | None
    ) = None

    for tier in tiers:
        threshold = int(
            tier.market_cap_lamports_threshold
        )

        if (
            previous_threshold
            is not None
            and threshold
            < previous_threshold
        ):
            raise SafetyResolutionError(
                f"Pump {label} tiers "
                "are not ordered by "
                "market-cap threshold."
            )

        previous_threshold = (
            threshold
        )

    return tuple(
        tiers
    )


def decode_fee_config(
    *,
    address: str,
    account: dict,
) -> PumpFeeConfigSnapshot:

    if account.get(
        "owner"
    ) != str(
        PUMP_FEE_PROGRAM
    ):
        raise SafetyResolutionError(
            "Pump FeeConfig is not owned "
            "by the Pump Fees program."
        )

    encoded = account.get(
        "data"
    )

    if (
        not isinstance(
            encoded,
            list,
        )
        or len(encoded) < 2
        or encoded[1]
        != "base64"
    ):
        raise SafetyResolutionError(
            "Unexpected Pump FeeConfig "
            "account encoding."
        )

    try:
        raw = base64.b64decode(
            encoded[0],
            validate=True,
        )

    except Exception as error:
        raise SafetyResolutionError(
            "Invalid base64 in Pump "
            "FeeConfig account."
        ) from error

    #
    # Minimum serialized body:
    #
    # 8  discriminator
    # 1  bump
    # 32 admin
    # 24 flat Fees
    # 4  fee_tiers Vec length
    # 4  stable_fee_tiers Vec length
    #
    if len(raw) < 73:
        raise SafetyResolutionError(
            "Pump FeeConfig account "
            f"is too short: {len(raw)}."
        )

    discriminator_ok = (
        raw[:8]
        == FEE_CONFIG_DISCRIMINATOR
    )

    if not discriminator_ok:
        raise SafetyResolutionError(
            "Pump FeeConfig "
            "discriminator mismatch."
        )

    reader = _BorshReader(
        raw[8:]
    )

    bump = reader.u8()

    admin = reader.pubkey()

    flat_fees = (
        _read_fees(
            reader
        )
    )

    fee_tiers = (
        _read_fee_tiers(
            reader,
            label="SOL fee",
        )
    )

    stable_fee_tiers = (
        _read_fee_tiers(
            reader,
            label="stable fee",
        )
    )

    return PumpFeeConfigSnapshot(
        address=address,

        account_size=len(
            raw
        ),

        owner_verified=True,
        discriminator_verified=True,

        bump=int(
            bump
        ),

        admin=admin,

        flat_fees=(
            flat_fees
        ),

        fee_tiers=(
            fee_tiers
        ),

        stable_fee_tiers=(
            stable_fee_tiers
        ),
    )


def bonding_curve_market_cap_lamports(
    curve: BondingCurveSnapshot,
) -> int:

    virtual_quote = int(
        curve.virtual_quote_reserves
    )

    virtual_token = int(
        curve.virtual_token_reserves
    )

    token_supply = int(
        curve.token_total_supply
    )

    if virtual_quote <= 0:
        raise SafetyResolutionError(
            "Pump virtual SOL reserves "
            "must be positive."
        )

    if virtual_token <= 0:
        raise SafetyResolutionError(
            "Pump virtual token reserves "
            "must be positive."
        )

    if token_supply <= 0:
        raise SafetyResolutionError(
            "Pump token total supply "
            "must be positive."
        )

    return (
        virtual_quote
        * token_supply
        // virtual_token
    )


def select_sol_fee_tier(
    *,
    fee_config: PumpFeeConfigSnapshot,
    market_cap_lamports: int,
) -> tuple[
    int,
    PumpFeeTier,
]:

    tiers = (
        fee_config.fee_tiers
    )

    if not tiers:
        raise SafetyResolutionError(
            "Pump SOL fee tier list "
            "is empty."
        )

    #
    # Pump's documented selection:
    #
    # - below first threshold -> first tier
    # - otherwise select the highest threshold
    #   not exceeding current market cap.
    #
    selected_index = 0

    for index, tier in enumerate(
        tiers
    ):
        if (
            market_cap_lamports
            >= tier.market_cap_lamports_threshold
        ):
            selected_index = index

        else:
            break

    return (
        selected_index,
        tiers[
            selected_index
        ],
    )


async def resolve_live_pump_fee_state(
    *,
    mint: str,
    min_context_slot: int | None = None,
) -> LivePumpFeeState:

    try:
        mint_pubkey = (
            Pubkey.from_string(
                mint
            )
        )

    except Exception as error:
        raise SafetyResolutionError(
            "Invalid mint public key."
        ) from error

    bonding_curve = (
        derive_bonding_curve(
            mint_pubkey
        )
    )

    fee_config_address = (
        derive_fee_config()
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

    #
    # Fetch curve and fee configuration in one
    # RPC request so both accounts share the
    # same context slot.
    #
    async with HeliusRpcClient() as rpc:
        result = await rpc.call(
            "getMultipleAccounts",
            [
                [
                    str(
                        bonding_curve
                    ),
                    str(
                        fee_config_address
                    ),
                ],
                config,
            ],
        )

        fetched_at = time.time()

    if not isinstance(
        result,
        dict,
    ):
        raise SafetyResolutionError(
            "Pump fee-state RPC response "
            "is malformed."
        )

    slot = context_slot(
        result
    )

    if slot is None:
        raise SafetyResolutionError(
            "Pump fee-state RPC context "
            "slot is missing."
        )

    values = result.get(
        "value"
    )

    if (
        not isinstance(
            values,
            list,
        )
        or len(values) != 2
    ):
        raise SafetyResolutionError(
            "Pump fee-state RPC did not "
            "return exactly two accounts."
        )

    curve_account = values[0]
    fee_account = values[1]

    if curve_account is None:
        raise SafetyResolutionError(
            "Live Pump bonding curve "
            "does not exist."
        )

    if fee_account is None:
        #
        # We deliberately fail closed here.
        # Global-account fallback can be added
        # separately and verified before live use.
        #
        raise SafetyResolutionError(
            "Live Pump FeeConfig "
            "does not exist."
        )

    curve = decode_bonding_curve(
        address=str(
            bonding_curve
        ),
        account=curve_account,
    )

    fee_config = decode_fee_config(
        address=str(
            fee_config_address
        ),
        account=fee_account,
    )

    market_cap_lamports = (
        bonding_curve_market_cap_lamports(
            curve
        )
    )

    (
        selected_index,
        selected_tier,
    ) = select_sol_fee_tier(
        fee_config=fee_config,
        market_cap_lamports=(
            market_cap_lamports
        ),
    )

    return LivePumpFeeState(
        resolver_version=(
            LIVE_PUMP_FEE_STATE_VERSION
        ),

        mint=mint,

        curve=curve,
        fee_config=fee_config,

        market_cap_lamports=int(
            market_cap_lamports
        ),

        selected_tier_index=int(
            selected_index
        ),

        selected_threshold_lamports=int(
            selected_tier
            .market_cap_lamports_threshold
        ),

        lp_fee_bps=int(
            selected_tier
            .fees
            .lp_fee_bps
        ),

        protocol_fee_bps=int(
            selected_tier
            .fees
            .protocol_fee_bps
        ),

        creator_fee_bps=int(
            selected_tier
            .fees
            .creator_fee_bps
        ),

        rpc_slot=int(
            slot
        ),

        fetched_at=float(
            fetched_at
        ),
    )