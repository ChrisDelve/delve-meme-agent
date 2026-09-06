from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import ssl
import struct
import time
from dataclasses import asdict, dataclass
from typing import Any

import aiohttp
import certifi
from dotenv import load_dotenv
from solders.pubkey import Pubkey


load_dotenv(dotenv_path=".env")

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

if not HELIUS_API_KEY:
    raise RuntimeError(
        "HELIUS_API_KEY is missing from .env"
    )

RPC_URL = (
    "https://mainnet.helius-rpc.com/"
    f"?api-key={HELIUS_API_KEY}"
)

SSL_CONTEXT = ssl.create_default_context(
    cafile=certifi.where()
)

RESOLVER_VERSION = "token-safety-resolver-v1"

COMMITMENT = "confirmed"

PUMP_PROGRAM = Pubkey.from_string(
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
)

TOKEN_PROGRAM = Pubkey.from_string(
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
)

TOKEN_2022_PROGRAM = Pubkey.from_string(
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
)

ASSOCIATED_TOKEN_PROGRAM = Pubkey.from_string(
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
)

MAYHEM_SOL_VAULT = Pubkey.from_string(
    "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
)

ZERO_PUBKEY = str(
    Pubkey.default()
)

BONDING_CURVE_DISCRIMINATOR = bytes(
    [
        23,
        183,
        248,
        55,
        96,
        216,
        172,
        96,
    ]
)

MAX_RPC_ATTEMPTS = 6


class SafetyResolutionError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class BondingCurveSnapshot:
    address: str

    account_size: int
    owner_verified: bool
    discriminator_verified: bool

    virtual_token_reserves: int
    virtual_quote_reserves: int

    real_token_reserves: int
    real_quote_reserves: int

    token_total_supply: int

    complete: bool

    creator: str

    is_mayhem_mode: bool

    is_cashback_coin: bool | None

    quote_mint: str | None

    layout: str


@dataclass(frozen=True)
class TokenSafetySnapshot:
    resolver_version: str

    mint: str

    token_program: str
    token_standard: str

    decimals: int
    supply_raw: int

    mint_authority: str | None
    freeze_authority: str | None

    token_2022_extensions: tuple[str, ...]

    bonding_curve: BondingCurveSnapshot

    associated_bonding_curve: str

    mayhem_token_vault: str | None

    protocol_inventory_raw: int
    protocol_inventory_pct_supply: float

    external_supply_raw: int
    external_supply_pct_total_supply: float

    largest_accounts_total: int
    largest_accounts_external: int

    largest_external_coverage_pct: float

    top_external_holder_pct: float | None
    top_5_external_holders_pct: float | None
    top_external_holder_pct_total_supply: float | None
    top_5_external_holders_pct_total_supply: float | None

    creator_balance_raw: int
    creator_pct_external_supply: float | None
    creator_pct_total_supply: float | None

    top_external_holders: tuple[
        dict[str, Any],
        ...
    ]

    rpc_min_slot: int
    rpc_max_slot: int
    rpc_slot_span: int

    fetched_at: int

    warnings: tuple[str, ...]


class HeliusRpcClient:
    def __init__(self) -> None:
        self.session: (
            aiohttp.ClientSession | None
        ) = None

    async def __aenter__(
        self,
    ) -> "HeliusRpcClient":
        connector = aiohttp.TCPConnector(
            ssl=SSL_CONTEXT
        )

        self.session = (
            aiohttp.ClientSession(
                connector=connector
            )
        )

        return self

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        if self.session is not None:
            await self.session.close()

    async def call(
        self,
        method: str,
        params: list,
    ) -> Any:
        if self.session is None:
            raise RuntimeError(
                "RPC session is not open."
            )

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }

        backoff = 0.5

        for attempt in range(
            MAX_RPC_ATTEMPTS
        ):
            try:
                async with self.session.post(
                    RPC_URL,
                    json=payload,
                ) as response:

                    if response.status == 429:
                        retry_after = (
                            response.headers.get(
                                "Retry-After"
                            )
                        )

                        try:
                            delay = float(
                                retry_after
                            )
                        except (
                            TypeError,
                            ValueError,
                        ):
                            delay = backoff

                        delay = min(
                            max(
                                delay,
                                0.5,
                            ),
                            8.0,
                        )

                        await asyncio.sleep(
                            delay
                        )

                        backoff = min(
                            backoff * 2,
                            8.0,
                        )

                        continue

                    if response.status >= 500:
                        await asyncio.sleep(
                            backoff
                        )

                        backoff = min(
                            backoff * 2,
                            8.0,
                        )

                        continue

                    if response.status != 200:
                        body = (
                            await response.text()
                        )

                        raise (
                            SafetyResolutionError(
                                f"Helius HTTP "
                                f"{response.status}: "
                                f"{body[:200]}"
                            )
                        )

                    response_json = (
                        await response.json()
                    )

            except aiohttp.ClientError as error:
                if (
                    attempt
                    == MAX_RPC_ATTEMPTS - 1
                ):
                    raise (
                        SafetyResolutionError(
                            "Helius network failure: "
                            f"{type(error).__name__}"
                        )
                    ) from error

                await asyncio.sleep(
                    backoff
                )

                backoff = min(
                    backoff * 2,
                    8.0,
                )

                continue

            if "error" in response_json:
                rpc_error = response_json[
                    "error"
                ]

                error_code = (
                    rpc_error.get("code")
                    if isinstance(
                        rpc_error,
                        dict,
                    )
                    else None
                )

                #
                # Solana RPC can transiently return
                # -32016 when a load-balanced backend
                # has not yet reached minContextSlot.
                #
                # This is not evidence that the token
                # itself is unsafe. Retry briefly while
                # preserving fail-closed behavior if
                # the required slot never becomes
                # available.
                #
                if error_code == -32016:
                    if (
                        attempt
                        == MAX_RPC_ATTEMPTS - 1
                    ):
                        raise SafetyResolutionError(
                            f"Helius RPC {method} "
                            "could not reach required "
                            "minimum context slot after "
                            f"{MAX_RPC_ATTEMPTS} attempts: "
                            f"{rpc_error}"
                        )

                    await asyncio.sleep(
                        min(
                            max(
                                backoff,
                                0.25,
                            ),
                            2.0,
                        )
                    )

                    backoff = min(
                        backoff * 1.5,
                        2.0,
                    )

                    continue

                raise SafetyResolutionError(
                    f"Helius RPC {method} error: "
                    f"{rpc_error}"
                )

            return response_json.get(
                "result"
            )

        raise SafetyResolutionError(
            f"Helius RPC {method} "
            "exhausted retries."
        )


def context_slot(
    result: Any,
) -> int | None:
    if not isinstance(
        result,
        dict,
    ):
        return None

    context = result.get(
        "context"
    )

    if not isinstance(
        context,
        dict,
    ):
        return None

    slot = context.get(
        "slot"
    )

    if slot is None:
        return None

    return int(slot)


def append_slot(
    slots: list[int],
    result: Any,
) -> None:
    slot = context_slot(
        result
    )

    if slot is not None:
        slots.append(
            slot
        )


def derive_bonding_curve(
    mint: Pubkey,
) -> Pubkey:
    address, _ = (
        Pubkey.find_program_address(
            [
                b"bonding-curve",
                bytes(mint),
            ],
            PUMP_PROGRAM,
        )
    )

    return address


def derive_associated_token_account(
    *,
    owner: Pubkey,
    mint: Pubkey,
    token_program: Pubkey,
) -> Pubkey:
    address, _ = (
        Pubkey.find_program_address(
            [
                bytes(owner),
                bytes(token_program),
                bytes(mint),
            ],
            ASSOCIATED_TOKEN_PROGRAM,
        )
    )

    return address


def u64(
    data: bytes,
    offset: int,
) -> int:
    return struct.unpack_from(
        "<Q",
        data,
        offset,
    )[0]


def read_bool(
    data: bytes,
    offset: int,
) -> bool:
    value = data[offset]

    if value not in (
        0,
        1,
    ):
        raise SafetyResolutionError(
            "Invalid boolean value in "
            "Pump bonding curve."
        )

    return bool(value)


def decode_bonding_curve(
    *,
    address: str,
    account: dict,
) -> BondingCurveSnapshot:
    if account.get("owner") != str(
        PUMP_PROGRAM
    ):
        raise SafetyResolutionError(
            "Bonding curve is not owned "
            "by the Pump program."
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
        or encoded[1] != "base64"
    ):
        raise SafetyResolutionError(
            "Unexpected bonding curve "
            "account encoding."
        )

    raw = base64.b64decode(
        encoded[0]
    )

    #
    # Legacy Mayhem-aware Pump curves
    # contain at least:
    #
    # 8 discriminator
    # 5 * u64
    # 1 bool complete
    # 32 creator
    # 1 bool mayhem
    #
    if len(raw) < 82:
        raise SafetyResolutionError(
            f"Pump bonding curve is too "
            f"short: {len(raw)} bytes."
        )

    discriminator_ok = (
        raw[:8]
        == BONDING_CURVE_DISCRIMINATOR
    )

    if not discriminator_ok:
        raise SafetyResolutionError(
            "Pump bonding curve "
            "discriminator mismatch."
        )

    virtual_token = u64(
        raw,
        8,
    )

    virtual_quote = u64(
        raw,
        16,
    )

    real_token = u64(
        raw,
        24,
    )

    real_quote = u64(
        raw,
        32,
    )

    token_total_supply = u64(
        raw,
        40,
    )

    complete = read_bool(
        raw,
        48,
    )

    creator = str(
        Pubkey.from_bytes(
            raw[49:81]
        )
    )

    is_mayhem = read_bool(
        raw,
        81,
    )

    if len(raw) >= 115:
        is_cashback = read_bool(
            raw,
            82,
        )

        quote_mint = str(
            Pubkey.from_bytes(
                raw[83:115]
            )
        )

        layout = "current-115+"

    elif len(raw) >= 83:
        #
        # Transitional structure:
        # cashback exists but quote mint
        # is not available in this size.
        #
        is_cashback = read_bool(
            raw,
            82,
        )

        quote_mint = None

        layout = "transitional-83+"

    else:
        is_cashback = None
        quote_mint = None

        layout = "legacy-82"

    return BondingCurveSnapshot(
        address=address,

        account_size=len(raw),

        owner_verified=True,

        discriminator_verified=(
            discriminator_ok
        ),

        virtual_token_reserves=(
            virtual_token
        ),

        virtual_quote_reserves=(
            virtual_quote
        ),

        real_token_reserves=(
            real_token
        ),

        real_quote_reserves=(
            real_quote
        ),

        token_total_supply=(
            token_total_supply
        ),

        complete=complete,

        creator=creator,

        is_mayhem_mode=(
            is_mayhem
        ),

        is_cashback_coin=(
            is_cashback
        ),

        quote_mint=quote_mint,

        layout=layout,
    )


def extract_extensions(
    info: dict,
) -> tuple[str, ...]:
    extensions = info.get(
        "extensions"
    )

    if not isinstance(
        extensions,
        list,
    ):
        return ()

    names: set[str] = set()

    for extension in extensions:
        if not isinstance(
            extension,
            dict,
        ):
            continue

        name = (
            extension.get(
                "extension"
            )
            or extension.get(
                "type"
            )
        )

        if name is not None:
            names.add(
                str(name)
            )

    return tuple(
        sorted(
            names
        )
    )


def parse_token_account(
    *,
    account: dict | None,
    expected_mint: str,
) -> tuple[
    str,
    int,
] | None:
    if account is None:
        return None

    data = account.get(
        "data"
    )

    if not isinstance(
        data,
        dict,
    ):
        raise SafetyResolutionError(
            "Token account was not "
            "JSON parsed."
        )

    parsed = data.get(
        "parsed"
    )

    if not isinstance(
        parsed,
        dict,
    ):
        raise SafetyResolutionError(
            "Token account parsed data "
            "is missing."
        )

    info = parsed.get(
        "info"
    )

    if not isinstance(
        info,
        dict,
    ):
        raise SafetyResolutionError(
            "Token account info is missing."
        )

    if (
        info.get("mint")
        != expected_mint
    ):
        raise SafetyResolutionError(
            "Token account mint mismatch."
        )

    owner = info.get(
        "owner"
    )

    token_amount = info.get(
        "tokenAmount"
    )

    if (
        owner is None
        or not isinstance(
            token_amount,
            dict,
        )
        or token_amount.get(
            "amount"
        )
        is None
    ):
        raise SafetyResolutionError(
            "Incomplete parsed token account."
        )

    return (
        str(owner),
        int(
            token_amount[
                "amount"
            ]
        ),
    )


def pct(
    numerator: int,
    denominator: int,
) -> float:
    if denominator <= 0:
        return 0.0

    return (
        100.0
        * numerator
        / denominator
    )


async def resolve_token_safety(
    mint_string: str,
) -> TokenSafetySnapshot:
    try:
        mint = Pubkey.from_string(
            mint_string
        )
    except Exception as error:
        raise SafetyResolutionError(
            "Invalid mint public key."
        ) from error

    slots: list[int] = []
    warnings: list[str] = []

    bonding_curve_address = (
        derive_bonding_curve(
            mint
        )
    )

    async with HeliusRpcClient() as rpc:

        #
        # 1. Resolve the mint's actual
        # owning token program.
        #
        mint_result = await rpc.call(
            "getAccountInfo",
            [
                mint_string,
                {
                    "encoding": "jsonParsed",
                    "commitment": COMMITMENT,
                },
            ],
        )

        append_slot(
            slots,
            mint_result,
        )

        if (
            not isinstance(
                mint_result,
                dict,
            )
            or mint_result.get(
                "value"
            )
            is None
        ):
            raise SafetyResolutionError(
                "Mint account does not exist."
            )

        mint_account = mint_result[
            "value"
        ]

        token_program_string = (
            mint_account.get(
                "owner"
            )
        )

        if (
            token_program_string
            == str(TOKEN_PROGRAM)
        ):
            token_program = (
                TOKEN_PROGRAM
            )

            token_standard = (
                "SPL_TOKEN"
            )

        elif (
            token_program_string
            == str(
                TOKEN_2022_PROGRAM
            )
        ):
            token_program = (
                TOKEN_2022_PROGRAM
            )

            token_standard = (
                "TOKEN_2022"
            )

        else:
            raise SafetyResolutionError(
                "Mint is not owned by an "
                "approved SPL token program."
            )

        mint_data = mint_account.get(
            "data"
        )

        if not isinstance(
            mint_data,
            dict,
        ):
            raise SafetyResolutionError(
                "Mint account did not return "
                "JSON-parsed data."
            )

        mint_parsed = mint_data.get(
            "parsed"
        )

        if not isinstance(
            mint_parsed,
            dict,
        ):
            raise SafetyResolutionError(
                "Mint parsed data missing."
            )

        mint_info = mint_parsed.get(
            "info"
        )

        if not isinstance(
            mint_info,
            dict,
        ):
            raise SafetyResolutionError(
                "Mint parsed info missing."
            )

        decimals = int(
            mint_info[
                "decimals"
            ]
        )

        parsed_supply = int(
            mint_info[
                "supply"
            ]
        )

        mint_authority = (
            mint_info.get(
                "mintAuthority"
            )
        )

        freeze_authority = (
            mint_info.get(
                "freezeAuthority"
            )
        )

        extensions = (
            extract_extensions(
                mint_info
            )
        )

        minimum_slot = (
            min(slots)
            if slots
            else None
        )

        #
        # 2. Fetch Pump curve.
        #
        curve_config = {
            "encoding": "base64",
            "commitment": COMMITMENT,
        }

        if minimum_slot is not None:
            curve_config[
                "minContextSlot"
            ] = minimum_slot

        curve_result = await rpc.call(
            "getAccountInfo",
            [
                str(
                    bonding_curve_address
                ),
                curve_config,
            ],
        )

        append_slot(
            slots,
            curve_result,
        )

        if (
            not isinstance(
                curve_result,
                dict,
            )
            or curve_result.get(
                "value"
            )
            is None
        ):
            raise SafetyResolutionError(
                "Derived Pump bonding curve "
                "does not exist."
            )

        curve = decode_bonding_curve(
            address=str(
                bonding_curve_address
            ),
            account=curve_result[
                "value"
            ],
        )

        #
        # 3. Resolve supply and largest
        # token accounts.
        #
        (
            supply_result,
            largest_result,
        ) = await asyncio.gather(
            rpc.call(
                "getTokenSupply",
                [
                    mint_string,
                    {
                        "commitment": (
                            COMMITMENT
                        )
                    },
                ],
            ),

            rpc.call(
                "getTokenLargestAccounts",
                [
                    mint_string,
                    {
                        "commitment": (
                            COMMITMENT
                        )
                    },
                ],
            ),
        )

        append_slot(
            slots,
            supply_result,
        )

        append_slot(
            slots,
            largest_result,
        )

        supply_value = (
            supply_result.get(
                "value"
            )
            if isinstance(
                supply_result,
                dict,
            )
            else None
        )

        if (
            not isinstance(
                supply_value,
                dict,
            )
            or supply_value.get(
                "amount"
            )
            is None
        ):
            raise SafetyResolutionError(
                "getTokenSupply returned "
                "invalid data."
            )

        supply_raw = int(
            supply_value[
                "amount"
            ]
        )

        if (
            supply_raw
            != parsed_supply
        ):
            warnings.append(
                "MINT_SUPPLY_RPC_MISMATCH"
            )

        if (
            curve.token_total_supply
            != supply_raw
        ):
            if curve.is_mayhem_mode:
                warnings.append(
                    "MAYHEM_CURVE_SUPPLY_DIFFERS_FROM_MINT_SUPPLY"
                )
            else:
                warnings.append(
                    "CURVE_SUPPLY_MISMATCH"
                )

        largest_values = (
            largest_result.get(
                "value"
            )
            if isinstance(
                largest_result,
                dict,
            )
            else None
        )

        if not isinstance(
            largest_values,
            list,
        ):
            raise SafetyResolutionError(
                "getTokenLargestAccounts "
                "returned invalid data."
            )

        #
        # 4. Derive known protocol-owned
        # token accounts.
        #
        associated_curve = (
            derive_associated_token_account(
                owner=(
                    bonding_curve_address
                ),
                mint=mint,
                token_program=(
                    token_program
                ),
            )
        )

        mayhem_token_vault: (
            Pubkey | None
        ) = None

        protocol_addresses = [
            str(
                associated_curve
            )
        ]

        if curve.is_mayhem_mode:
            mayhem_token_vault = (
                derive_associated_token_account(
                    owner=(
                        MAYHEM_SOL_VAULT
                    ),
                    mint=mint,
                    token_program=(
                        token_program
                    ),
                )
            )

            protocol_addresses.append(
                str(
                    mayhem_token_vault
                )
            )

        current_min_slot = (
            max(slots)
            if slots
            else None
        )

        protocol_config = {
            "encoding": "jsonParsed",
            "commitment": COMMITMENT,
        }

        if (
            current_min_slot
            is not None
        ):
            protocol_config[
                "minContextSlot"
            ] = current_min_slot

        protocol_result = (
            await rpc.call(
                "getMultipleAccounts",
                [
                    protocol_addresses,
                    protocol_config,
                ],
            )
        )

        append_slot(
            slots,
            protocol_result,
        )

        protocol_values = (
            protocol_result.get(
                "value"
            )
            if isinstance(
                protocol_result,
                dict,
            )
            else None
        )

        if not isinstance(
            protocol_values,
            list,
        ):
            raise SafetyResolutionError(
                "Protocol token account "
                "resolution failed."
            )

        protocol_inventory = 0

        for account in (
            protocol_values
        ):
            parsed_account = (
                parse_token_account(
                    account=account,
                    expected_mint=(
                        mint_string
                    ),
                )
            )

            if (
                parsed_account
                is not None
            ):
                _, amount = (
                    parsed_account
                )

                protocol_inventory += (
                    amount
                )

        if (
            protocol_inventory
            > supply_raw
        ):
            raise SafetyResolutionError(
                "Protocol inventory exceeds "
                "mint supply."
            )

        external_supply = (
            supply_raw
            - protocol_inventory
        )

        external_supply_pct_total_supply = pct(
            external_supply,
            supply_raw,
        )

        #
        # 5. Resolve the wallet owner
        # behind each of the 20 largest
        # token accounts.
        #
        largest_addresses = [
            item["address"]
            for item in largest_values
            if (
                isinstance(
                    item,
                    dict,
                )
                and item.get(
                    "address"
                )
            )
        ]

        owner_balances: dict[
            str,
            int,
        ] = {}

        if largest_addresses:
            holder_config = {
                "encoding": "jsonParsed",
                "commitment": (
                    COMMITMENT
                ),
            }

            if slots:
                holder_config[
                    "minContextSlot"
                ] = max(slots)

            holder_result = (
                await rpc.call(
                    "getMultipleAccounts",
                    [
                        largest_addresses,
                        holder_config,
                    ],
                )
            )

            append_slot(
                slots,
                holder_result,
            )

            holder_values = (
                holder_result.get(
                    "value"
                )
                if isinstance(
                    holder_result,
                    dict,
                )
                else None
            )

            if (
                not isinstance(
                    holder_values,
                    list,
                )
                or len(holder_values)
                != len(
                    largest_addresses
                )
            ):
                raise (
                    SafetyResolutionError(
                        "Largest-holder account "
                        "resolution failed."
                    )
                )

            protocol_address_set = set(
                protocol_addresses
            )

            for (
                token_account_address,
                account,
            ) in zip(
                largest_addresses,
                holder_values,
            ):
                parsed_account = (
                    parse_token_account(
                        account=account,
                        expected_mint=(
                            mint_string
                        ),
                    )
                )

                if (
                    parsed_account
                    is None
                ):
                    continue

                holder_owner, amount = (
                    parsed_account
                )

                if (
                    token_account_address
                    in protocol_address_set
                ):
                    continue

                owner_balances[
                    holder_owner
                ] = (
                    owner_balances.get(
                        holder_owner,
                        0,
                    )
                    + amount
                )

        sorted_external = sorted(
            owner_balances.items(),
            key=lambda item: (
                item[1]
            ),
            reverse=True,
        )

        observed_external_total = sum(
            amount
            for _, amount
            in sorted_external
        )

        if external_supply > 0:
            top_external_pct = (
                pct(
                    sorted_external[0][1],
                    external_supply,
                )
                if sorted_external
                else 0.0
            )

            top_5_external_pct = pct(
                sum(
                    amount
                    for _, amount
                    in sorted_external[:5]
                ),
                external_supply,
            )

            top_external_total_supply_pct = (
                pct(
                    sorted_external[0][1],
                    supply_raw,
                )
                if sorted_external
                else 0.0
            )

            top_5_external_total_supply_pct = pct(
                sum(
                    amount
                    for _, amount
                    in sorted_external[:5]
                ),
                supply_raw,
            )

            external_coverage_pct = (
                pct(
                    observed_external_total,
                    external_supply,
                )
            )

        else:
            top_external_pct = None
            top_5_external_pct = None
            external_coverage_pct = 0.0
            top_external_total_supply_pct = None
            top_5_external_total_supply_pct = None

            warnings.append(
                "NO_EXTERNAL_SUPPLY"
            )

        top_external_holders = tuple(
            {
                "owner": owner,
                "amount_raw": amount,
                "pct_external_supply": (
                    pct(
                        amount,
                        external_supply,
                    )
                    if external_supply > 0
                    else None
                ),
            }
            for owner, amount
            in sorted_external[:10]
        )

        #
        # 6. Resolve the creator's exact
        # token balance across all of their
        # token accounts for this mint.
        #
        creator_config = {
            "encoding": "jsonParsed",
            "commitment": COMMITMENT,
        }

        if slots:
            creator_config[
                "minContextSlot"
            ] = max(slots)

        creator_result = (
            await rpc.call(
                "getTokenAccountsByOwner",
                [
                    curve.creator,
                    {
                        "mint": (
                            mint_string
                        )
                    },
                    creator_config,
                ],
            )
        )

        append_slot(
            slots,
            creator_result,
        )

        creator_values = (
            creator_result.get(
                "value"
            )
            if isinstance(
                creator_result,
                dict,
            )
            else None
        )

        if not isinstance(
            creator_values,
            list,
        ):
            raise SafetyResolutionError(
                "Creator token-account "
                "resolution failed."
            )

        creator_balance = 0

        for item in creator_values:
            if not isinstance(
                item,
                dict,
            ):
                continue

            account = item.get(
                "account"
            )

            parsed_account = (
                parse_token_account(
                    account=account,
                    expected_mint=(
                        mint_string
                    ),
                )
            )

            if (
                parsed_account
                is None
            ):
                continue

            _, amount = (
                parsed_account
            )

            creator_balance += amount

        creator_pct = (
            pct(
                creator_balance,
                external_supply,
            )
            if external_supply > 0
            else None
        )

        creator_pct_total_supply = (
            pct(
                creator_balance,
                supply_raw,
            )
            if supply_raw > 0
            else None
        )
    #
    # RPC coherence information.
    #
    if not slots:
        raise SafetyResolutionError(
            "No RPC context slots captured."
        )

    minimum_rpc_slot = min(
        slots
    )

    maximum_rpc_slot = max(
        slots
    )

    slot_span = (
        maximum_rpc_slot
        - minimum_rpc_slot
    )

    if slot_span > 32:
        warnings.append(
            "RPC_SLOT_SPAN_GT_32"
        )

    #
    # Agent v1 is SOL-paired Pump only.
    #
    if (
        curve.quote_mint
        not in (
            None,
            ZERO_PUBKEY,
        )
    ):
        warnings.append(
            "NON_DEFAULT_QUOTE_MINT"
        )

    if (
        token_standard
        == "TOKEN_2022"
        and not extensions
    ):
        warnings.append(
            "TOKEN_2022_EXTENSIONS_NOT_REPORTED"
        )

    return TokenSafetySnapshot(
        resolver_version=(
            RESOLVER_VERSION
        ),

        mint=mint_string,

        token_program=(
            token_program_string
        ),

        token_standard=(
            token_standard
        ),

        decimals=decimals,

        supply_raw=supply_raw,

        mint_authority=(
            str(mint_authority)
            if mint_authority
            else None
        ),

        freeze_authority=(
            str(freeze_authority)
            if freeze_authority
            else None
        ),

        token_2022_extensions=(
            extensions
        ),

        bonding_curve=curve,

        associated_bonding_curve=(
            str(
                associated_curve
            )
        ),

        mayhem_token_vault=(
            str(
                mayhem_token_vault
            )
            if mayhem_token_vault
            is not None
            else None
        ),

        protocol_inventory_raw=(
            protocol_inventory
        ),

        protocol_inventory_pct_supply=(
            pct(
                protocol_inventory,
                supply_raw,
            )
        ),

        external_supply_raw=(
            external_supply
        ),

        external_supply_pct_total_supply=(
            external_supply_pct_total_supply
        ),

        largest_accounts_total=(
            len(
                largest_addresses
            )
        ),

        largest_accounts_external=(
            len(
                sorted_external
            )
        ),

        largest_external_coverage_pct=(
            external_coverage_pct
        ),

        top_external_holder_pct=(
            top_external_pct
        ),

        top_5_external_holders_pct=(
            top_5_external_pct
        ),

        top_external_holder_pct_total_supply=(
            top_external_total_supply_pct
        ),

        top_5_external_holders_pct_total_supply=(
            top_5_external_total_supply_pct
        ),

        creator_balance_raw=(
            creator_balance
        ),

        creator_pct_external_supply=(
            creator_pct
        ),

        creator_pct_total_supply=(
            creator_pct_total_supply
        ),

        top_external_holders=(
            top_external_holders
        ),

        rpc_min_slot=(
            minimum_rpc_slot
        ),

        rpc_max_slot=(
            maximum_rpc_slot
        ),

        rpc_slot_span=(
            slot_span
        ),

        fetched_at=int(
            time.time()
        ),

        warnings=tuple(
            warnings
        ),
    )


def print_summary(
    snapshot: TokenSafetySnapshot,
) -> None:
    curve = snapshot.bonding_curve

    print()
    print("=" * 82)

    print(
        "DELVE MEME AGENT — "
        "LIVE TOKEN SAFETY RESOLVER"
    )

    print("=" * 82)

    print(
        f"Resolver:                "
        f"{snapshot.resolver_version}"
    )

    print(
        f"Mint:                    "
        f"{snapshot.mint}"
    )

    print(
        f"Token standard:          "
        f"{snapshot.token_standard}"
    )

    print(
        f"Token program:           "
        f"{snapshot.token_program}"
    )

    print(
        f"Decimals:                "
        f"{snapshot.decimals}"
    )

    print(
        f"Supply raw:              "
        f"{snapshot.supply_raw:,}"
    )

    print()

    print("TOKEN CONTROL")
    print("-" * 82)

    print(
        f"Mint authority:          "
        f"{snapshot.mint_authority or 'NONE'}"
    )

    print(
        f"Freeze authority:        "
        f"{snapshot.freeze_authority or 'NONE'}"
    )

    print(
        f"Token-2022 extensions:   "
        f"{', '.join(snapshot.token_2022_extensions) or 'NONE REPORTED'}"
    )

    print()

    print("PUMP CURVE")
    print("-" * 82)

    print(
        f"Bonding curve:           "
        f"{curve.address}"
    )

    print(
        f"Layout:                  "
        f"{curve.layout}"
    )

    print(
        f"Account size:            "
        f"{curve.account_size}"
    )

    print(
        f"Complete / graduated:    "
        f"{curve.complete}"
    )

    print(
        f"Creator:                 "
        f"{curve.creator}"
    )

    print(
        f"Mayhem mode:             "
        f"{curve.is_mayhem_mode}"
    )

    print(
        f"Cashback coin:           "
        f"{curve.is_cashback_coin}"
    )

    print(
        f"Quote mint:              "
        f"{curve.quote_mint or 'LEGACY / NOT STORED'}"
    )

    print(
        f"Real token reserves:     "
        f"{curve.real_token_reserves:,}"
    )

    print(
        f"Virtual token reserves:  "
        f"{curve.virtual_token_reserves:,}"
    )

    print(
        f"Virtual quote reserves:  "
        f"{curve.virtual_quote_reserves:,}"
    )

    print()

    print("PROTOCOL INVENTORY")
    print("-" * 82)

    print(
        f"Associated curve ATA:    "
        f"{snapshot.associated_bonding_curve}"
    )

    print(
        f"Mayhem token vault:      "
        f"{snapshot.mayhem_token_vault or 'N/A'}"
    )

    print(
        f"Protocol inventory:      "
        f"{snapshot.protocol_inventory_raw:,}"
    )

    print(
        f"Protocol % supply:       "
        f"{snapshot.protocol_inventory_pct_supply:.3f}%"
    )

    print(
        f"External supply:         "
        f"{snapshot.external_supply_raw:,}"
    )

    print()

    print("EXTERNAL HOLDER CONCENTRATION")
    print("-" * 82)

    print(
        f"Largest accounts read:   "
        f"{snapshot.largest_accounts_total}"
    )

    print(
        f"External wallets seen:   "
        f"{snapshot.largest_accounts_external}"
    )

    print(
        f"Top-20 coverage external:"
        f" {snapshot.largest_external_coverage_pct:.3f}%"
    )

    print(
        f"External % total supply: "
        f"{snapshot.external_supply_pct_total_supply:.3f}%"
    )

    print(
        f"Top external holder:     "
        f"{snapshot.top_external_holder_pct:.3f}%"
        if (
            snapshot.top_external_holder_pct
            is not None
        )
        else
        "Top external holder:     UNKNOWN"
    )

    print(
        f"Top holder % total:      "
        f"{snapshot.top_external_holder_pct_total_supply:.3f}%"
        if (
            snapshot.top_external_holder_pct_total_supply
            is not None
        )
        else
        "Top holder % total:      UNKNOWN"
    )

    print(
        f"Top 5 external holders:  "
        f"{snapshot.top_5_external_holders_pct:.3f}%"
        if (
            snapshot.top_5_external_holders_pct
            is not None
        )
        else
        "Top 5 external holders:  UNKNOWN"
    )

    print(
        f"Top 5 % total supply:    "
        f"{snapshot.top_5_external_holders_pct_total_supply:.3f}%"
        if (
            snapshot.top_5_external_holders_pct_total_supply
            is not None
        )
        else
        "Top 5 % total supply:    UNKNOWN"
    )

    print(
        f"Creator external share:  "
        f"{snapshot.creator_pct_external_supply:.3f}%"
        if (
            snapshot.creator_pct_external_supply
            is not None
        )
        else
        "Creator external share:  UNKNOWN"
    )

    print(
        f"Creator % total supply:  "
        f"{snapshot.creator_pct_total_supply:.3f}%"
        if (
            snapshot.creator_pct_total_supply
            is not None
        )
        else
        "Creator % total supply:  UNKNOWN"
    )

    print()

    print("RPC COHERENCE")
    print("-" * 82)

    print(
        f"Minimum slot:            "
        f"{snapshot.rpc_min_slot}"
    )

    print(
        f"Maximum slot:            "
        f"{snapshot.rpc_max_slot}"
    )

    print(
        f"Slot span:               "
        f"{snapshot.rpc_slot_span}"
    )

    print()

    print(
        f"Warnings:                "
        f"{', '.join(snapshot.warnings) or 'NONE'}"
    )

    print()
    print(
        "SAFETY DECISION: NOT YET APPLIED"
    )

    print(
        "TRANSACTION SUBMISSION: NO"
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )

    print("=" * 82)


async def async_main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--mint",
        required=True,
    )

    parser.add_argument(
        "--json",
        action="store_true",
    )

    args = parser.parse_args()

    snapshot = await resolve_token_safety(
        args.mint
    )

    if args.json:
        print(
            json.dumps(
                asdict(snapshot),
                indent=2,
                sort_keys=True,
            )
        )

        return

    print_summary(
        snapshot
    )


def main() -> None:
    asyncio.run(
        async_main()
    )


if __name__ == "__main__":
    main()