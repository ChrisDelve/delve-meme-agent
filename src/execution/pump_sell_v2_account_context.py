from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

from solders.pubkey import Pubkey

from src.execution.exit_execution import (
    EXIT_EXECUTION_CONTRACT_VERSION,
    PUMP_BONDING_CURVE_VENUE,
)
from src.execution.live_pump_fee_state import (
    PUMP_FEE_PROGRAM,
    derive_fee_config,
)
from src.execution.live_pump_global_state import (
    GLOBAL_ACCOUNT_SIZE,
    LIVE_PUMP_GLOBAL_STATE_VERSION,
    LivePumpGlobalState,
    derive_global,
)
from src.execution.live_pump_sell_authorization import (
    LIVE_PUMP_SELL_AUTHORIZATION_VERSION,
    LivePumpSellAuthorization,
)
from src.execution.order_authorization import (
    WRAPPED_SOL_MINT,
)
from src.safety.token_safety_gate import (
    SOL_QUOTE_MINT,
)
from src.safety.token_safety_resolver import (
    ASSOCIATED_TOKEN_PROGRAM,
    PUMP_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    derive_associated_token_account,
    derive_bonding_curve,
)


PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION = (
    "pump-sell-v2-account-context-v1"
)

SELL_FEE_RECIPIENT_SELECTOR_VERSION = (
    "pump-sell-fee-recipient-selector-v1"
)

SELL_BUYBACK_RECIPIENT_SELECTOR_VERSION = (
    "pump-sell-buyback-recipient-selector-v1"
)

U64_MAX = (1 << 64) - 1


SELL_V2_ACCOUNT_NAMES = (
    "global",
    "base_mint",
    "quote_mint",
    "base_token_program",
    "quote_token_program",
    "associated_token_program",
    "fee_recipient",
    "associated_quote_fee_recipient",
    "buyback_fee_recipient",
    "associated_quote_buyback_fee_recipient",
    "bonding_curve",
    "associated_base_bonding_curve",
    "associated_quote_bonding_curve",
    "user",
    "associated_base_user",
    "associated_quote_user",
    "creator_vault",
    "associated_creator_vault",
    "sharing_config",
    "user_volume_accumulator",
    "associated_user_volume_accumulator",
    "fee_config",
    "fee_program",
    "system_program",
    "event_authority",
    "program",
)


class PumpSellV2AccountContextError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class PumpSellV2AccountContext:
    resolver_version: str

    authorization_version: str
    authorization_sha256: str

    authorization_fee_rpc_slot: int
    global_rpc_slot: int

    fee_recipient_selector_version: str
    fee_recipient_index: int

    buyback_recipient_selector_version: str
    buyback_fee_recipient_index: int

    amount: int
    min_sol_output: int

    global_account: str
    base_mint: str
    quote_mint: str
    base_token_program: str
    quote_token_program: str
    associated_token_program: str

    fee_recipient: str
    associated_quote_fee_recipient: str

    buyback_fee_recipient: str
    associated_quote_buyback_fee_recipient: str

    bonding_curve: str
    associated_base_bonding_curve: str
    associated_quote_bonding_curve: str

    user: str
    associated_base_user: str
    associated_quote_user: str

    creator_vault: str
    associated_creator_vault: str

    sharing_config: str

    user_volume_accumulator: str
    associated_user_volume_accumulator: str

    fee_config: str
    fee_program: str

    system_program: str
    event_authority: str
    program: str

    def ordered_accounts(
        self,
    ) -> tuple[
        str,
        ...
    ]:
        return (
            self.global_account,
            self.base_mint,
            self.quote_mint,
            self.base_token_program,
            self.quote_token_program,
            self.associated_token_program,
            self.fee_recipient,
            self.associated_quote_fee_recipient,
            self.buyback_fee_recipient,
            (
                self
                .associated_quote_buyback_fee_recipient
            ),
            self.bonding_curve,
            self.associated_base_bonding_curve,
            self.associated_quote_bonding_curve,
            self.user,
            self.associated_base_user,
            self.associated_quote_user,
            self.creator_vault,
            self.associated_creator_vault,
            self.sharing_config,
            self.user_volume_accumulator,
            (
                self
                .associated_user_volume_accumulator
            ),
            self.fee_config,
            self.fee_program,
            self.system_program,
            self.event_authority,
            self.program,
        )

    def ordered_named_accounts(
        self,
    ) -> tuple[
        tuple[
            str,
            str,
        ],
        ...
    ]:
        return tuple(
            zip(
                SELL_V2_ACCOUNT_NAMES,
                self.ordered_accounts(),
            )
        )


def _strict_u64(
    value: Any,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= U64_MAX
    )


def _strict_positive_u64(
    value: Any,
) -> bool:
    return (
        _strict_u64(value)
        and value > 0
    )


def _required_pubkey(
    value: str | None,
    *,
    label: str,
) -> Pubkey:
    if not value:
        raise PumpSellV2AccountContextError(
            f"{label} is missing."
        )

    try:
        result = Pubkey.from_string(
            value
        )

    except Exception as error:
        raise PumpSellV2AccountContextError(
            f"{label} is not a valid "
            "Solana public key."
        ) from error

    if result == Pubkey.default():
        raise PumpSellV2AccountContextError(
            f"{label} cannot be the "
            "default public key."
        )

    return result


def _required_sha256(
    value: str,
) -> str:
    if (
        not isinstance(
            value,
            str,
        )
        or len(value) != 64
    ):
        raise PumpSellV2AccountContextError(
            "SELL authorization fingerprint "
            "is invalid."
        )

    try:
        bytes.fromhex(
            value
        )

    except ValueError as error:
        raise PumpSellV2AccountContextError(
            "SELL authorization fingerprint "
            "is invalid."
        ) from error

    return value.lower()


def _derive_pump_pda(
    *seeds: bytes,
) -> Pubkey:
    address, _ = (
        Pubkey.find_program_address(
            list(
                seeds
            ),
            PUMP_PROGRAM,
        )
    )

    return address


def _derive_fee_pda(
    *seeds: bytes,
) -> Pubkey:
    address, _ = (
        Pubkey.find_program_address(
            list(
                seeds
            ),
            PUMP_FEE_PROGRAM,
        )
    )

    return address


def _selection_index(
    *,
    domain: str,
    authorization: (
        LivePumpSellAuthorization
    ),
    recipient_count: int,
) -> int:
    if recipient_count <= 0:
        raise PumpSellV2AccountContextError(
            "Recipient set is empty."
        )

    authorization_sha256 = (
        _required_sha256(
            authorization
            .authorization_sha256
        )
    )

    material = "\x1f".join(
        (
            domain,
            authorization_sha256,
            authorization.mint,
            authorization.wallet_pubkey,
        )
    ).encode(
        "utf-8"
    )

    digest = hashlib.sha256(
        material
    ).digest()

    return (
        int.from_bytes(
            digest[
                :8
            ],
            byteorder="little",
            signed=False,
        )
        % recipient_count
    )


def resolve_pump_sell_v2_account_context(
    *,
    authorization: (
        LivePumpSellAuthorization
    ),
    global_state: LivePumpGlobalState,
) -> PumpSellV2AccountContext:
    """
    Resolve the exact 26-account Pump sell_v2
    construction context.

    This function is pure construction authority:
    it performs no RPC, database mutation, signing,
    submission, position mutation, or risk decision.
    """

    # --------------------------------------------------------
    # SELL authorization contract.
    # --------------------------------------------------------

    if not isinstance(
        authorization,
        LivePumpSellAuthorization,
    ):
        raise PumpSellV2AccountContextError(
            "SELL authorization type is invalid."
        )

    if (
        authorization.authorization_version
        != LIVE_PUMP_SELL_AUTHORIZATION_VERSION
    ):
        raise PumpSellV2AccountContextError(
            "SELL authorization contract "
            "version is unsupported."
        )

    authorization_sha256 = (
        _required_sha256(
            authorization
            .authorization_sha256
        )
    )

    if not isinstance(
        authorization.mayhem_mode,
        bool,
    ):
        raise PumpSellV2AccountContextError(
            "Authorized Mayhem mode is invalid."
        )

    if not _strict_positive_u64(
        authorization.tokens_to_sell
    ):
        raise PumpSellV2AccountContextError(
            "Authorized SELL token amount "
            "is invalid."
        )

    if not _strict_u64(
        authorization.fee_rpc_slot
    ):
        raise PumpSellV2AccountContextError(
            "Authorized fee RPC slot is invalid."
        )

    if (
        authorization.curve_quote_mint
        != SOL_QUOTE_MINT
    ):
        raise PumpSellV2AccountContextError(
            "Only SOL-paired Pump curve "
            "authorization is supported."
        )

    if (
        authorization
        .quote_mint_for_instruction
        != WRAPPED_SOL_MINT
    ):
        raise PumpSellV2AccountContextError(
            "Only wrapped-SOL Pump SELL "
            "instruction quotes are supported."
        )

    exit_execution = (
        authorization.exit_execution
    )

    if (
        getattr(
            exit_execution,
            "contract_version",
            None,
        )
        != EXIT_EXECUTION_CONTRACT_VERSION
    ):
        raise PumpSellV2AccountContextError(
            "Exit execution contract "
            "version is unsupported."
        )

    if (
        getattr(
            exit_execution,
            "venue",
            None,
        )
        != PUMP_BONDING_CURVE_VENUE
    ):
        raise PumpSellV2AccountContextError(
            "Exit execution venue is invalid."
        )

    if (
        getattr(
            exit_execution,
            "tokens_in",
            None,
        )
        != authorization.tokens_to_sell
    ):
        raise PumpSellV2AccountContextError(
            "Exit execution token amount "
            "does not match authorization."
        )

    if (
        getattr(
            exit_execution,
            "executable",
            None,
        )
        is not True
    ):
        raise PumpSellV2AccountContextError(
            "Exit execution is not executable."
        )

    min_sol_output = getattr(
        exit_execution,
        "min_quote_out",
        None,
    )

    if not _strict_u64(
        min_sol_output
    ):
        raise PumpSellV2AccountContextError(
            "Authorized minimum SOL output "
            "is invalid."
        )

    # --------------------------------------------------------
    # Current Pump Global contract.
    # --------------------------------------------------------

    if not isinstance(
        global_state,
        LivePumpGlobalState,
    ):
        raise PumpSellV2AccountContextError(
            "Pump Global state type is invalid."
        )

    if (
        global_state.resolver_version
        != LIVE_PUMP_GLOBAL_STATE_VERSION
    ):
        raise PumpSellV2AccountContextError(
            "Pump Global resolver version "
            "is unsupported."
        )

    if not _strict_u64(
        global_state.rpc_slot
    ):
        raise PumpSellV2AccountContextError(
            "Pump Global RPC slot is invalid."
        )

    if (
        global_state.rpc_slot
        < authorization.fee_rpc_slot
    ):
        raise PumpSellV2AccountContextError(
            "Pump Global state predates "
            "the authorized SELL state."
        )

    if (
        not isinstance(
            global_state.fetched_at,
            (int, float),
        )
        or isinstance(
            global_state.fetched_at,
            bool,
        )
        or not math.isfinite(
            float(
                global_state.fetched_at
            )
        )
        or float(
            global_state.fetched_at
        )
        < 0.0
    ):
        raise PumpSellV2AccountContextError(
            "Pump Global fetch time is invalid."
        )

    snapshot = (
        global_state.global_state
    )

    canonical_global = (
        derive_global()
    )

    if (
        snapshot.address
        != str(
            canonical_global
        )
    ):
        raise PumpSellV2AccountContextError(
            "Pump Global address is not "
            "the canonical PDA."
        )

    if (
        snapshot.account_size
        != GLOBAL_ACCOUNT_SIZE
        or not snapshot.owner_verified
        or not snapshot.discriminator_verified
        or not snapshot.initialized
    ):
        raise PumpSellV2AccountContextError(
            "Pump Global snapshot is not "
            "execution-valid."
        )

    if (
        authorization.mayhem_mode
        and not snapshot.mayhem_mode_enabled
    ):
        raise PumpSellV2AccountContextError(
            "Authorized coin is Mayhem "
            "while Global Mayhem mode "
            "is disabled."
        )

    # --------------------------------------------------------
    # Parse authorized identity, then independently
    # derive every canonical account possible.
    # --------------------------------------------------------

    base_mint = _required_pubkey(
        authorization.mint,
        label="base mint",
    )

    user = _required_pubkey(
        authorization.wallet_pubkey,
        label="authorized wallet",
    )

    creator = _required_pubkey(
        authorization.creator,
        label="creator",
    )

    authorized_curve = _required_pubkey(
        authorization.bonding_curve,
        label="authorized bonding curve",
    )

    base_token_program = (
        _required_pubkey(
            authorization.base_token_program,
            label="base token program",
        )
    )

    if base_token_program not in (
        TOKEN_PROGRAM,
        TOKEN_2022_PROGRAM,
    ):
        raise PumpSellV2AccountContextError(
            "Unsupported base token program."
        )

    authorized_base_user = (
        _required_pubkey(
            authorization.associated_base_user,
            label=(
                "authorized associated "
                "base user"
            ),
        )
    )

    quote_mint = _required_pubkey(
        authorization
        .quote_mint_for_instruction,
        label="quote mint",
    )

    quote_token_program = (
        TOKEN_PROGRAM
    )

    derived_curve = (
        derive_bonding_curve(
            base_mint
        )
    )

    if derived_curve != authorized_curve:
        raise PumpSellV2AccountContextError(
            "Authorized bonding curve does "
            "not match canonical PDA."
        )

    associated_base_bonding_curve = (
        derive_associated_token_account(
            owner=derived_curve,
            mint=base_mint,
            token_program=(
                base_token_program
            ),
        )
    )

    associated_quote_bonding_curve = (
        derive_associated_token_account(
            owner=derived_curve,
            mint=quote_mint,
            token_program=(
                quote_token_program
            ),
        )
    )

    derived_base_user = (
        derive_associated_token_account(
            owner=user,
            mint=base_mint,
            token_program=(
                base_token_program
            ),
        )
    )

    if (
        derived_base_user
        != authorized_base_user
    ):
        raise PumpSellV2AccountContextError(
            "Authorized user base ATA does "
            "not match canonical ATA."
        )

    associated_quote_user = (
        derive_associated_token_account(
            owner=user,
            mint=quote_mint,
            token_program=(
                quote_token_program
            ),
        )
    )

    # --------------------------------------------------------
    # Deterministic fee-recipient selection from the
    # current execution-valid Pump Global sets.
    # --------------------------------------------------------

    fee_recipients = (
        snapshot.mayhem_fee_recipients
        if authorization.mayhem_mode
        else snapshot.normal_fee_recipients
    )

    buyback_recipients = (
        snapshot.buyback_fee_recipients
    )

    if len(
        fee_recipients
    ) != 8:
        raise PumpSellV2AccountContextError(
            "Pump fee recipient set must "
            "contain exactly 8 entries."
        )

    if len(
        buyback_recipients
    ) != 8:
        raise PumpSellV2AccountContextError(
            "Pump buyback recipient set must "
            "contain exactly 8 entries."
        )

    fee_index = _selection_index(
        domain=(
            SELL_FEE_RECIPIENT_SELECTOR_VERSION
        ),
        authorization=authorization,
        recipient_count=len(
            fee_recipients
        ),
    )

    buyback_index = _selection_index(
        domain=(
            SELL_BUYBACK_RECIPIENT_SELECTOR_VERSION
        ),
        authorization=authorization,
        recipient_count=len(
            buyback_recipients
        ),
    )

    fee_recipient = _required_pubkey(
        fee_recipients[
            fee_index
        ],
        label="fee recipient",
    )

    buyback_fee_recipient = (
        _required_pubkey(
            buyback_recipients[
                buyback_index
            ],
            label="buyback fee recipient",
        )
    )

    associated_quote_fee_recipient = (
        derive_associated_token_account(
            owner=fee_recipient,
            mint=quote_mint,
            token_program=(
                quote_token_program
            ),
        )
    )

    associated_quote_buyback_fee_recipient = (
        derive_associated_token_account(
            owner=buyback_fee_recipient,
            mint=quote_mint,
            token_program=(
                quote_token_program
            ),
        )
    )

    # --------------------------------------------------------
    # Remaining canonical sell_v2 PDAs.
    # --------------------------------------------------------

    creator_vault = _derive_pump_pda(
        b"creator-vault",
        bytes(
            creator
        ),
    )

    associated_creator_vault = (
        derive_associated_token_account(
            owner=creator_vault,
            mint=quote_mint,
            token_program=(
                quote_token_program
            ),
        )
    )

    sharing_config = _derive_fee_pda(
        b"sharing-config",
        bytes(
            base_mint
        ),
    )

    user_volume_accumulator = (
        _derive_pump_pda(
            b"user_volume_accumulator",
            bytes(
                user
            ),
        )
    )

    associated_user_volume_accumulator = (
        derive_associated_token_account(
            owner=user_volume_accumulator,
            mint=quote_mint,
            token_program=(
                quote_token_program
            ),
        )
    )

    fee_config = (
        derive_fee_config()
    )

    event_authority = (
        _derive_pump_pda(
            b"__event_authority"
        )
    )

    return PumpSellV2AccountContext(
        resolver_version=(
            PUMP_SELL_V2_ACCOUNT_CONTEXT_VERSION
        ),

        authorization_version=(
            authorization.authorization_version
        ),
        authorization_sha256=(
            authorization_sha256
        ),

        authorization_fee_rpc_slot=int(
            authorization.fee_rpc_slot
        ),
        global_rpc_slot=int(
            global_state.rpc_slot
        ),

        fee_recipient_selector_version=(
            SELL_FEE_RECIPIENT_SELECTOR_VERSION
        ),
        fee_recipient_index=int(
            fee_index
        ),

        buyback_recipient_selector_version=(
            SELL_BUYBACK_RECIPIENT_SELECTOR_VERSION
        ),
        buyback_fee_recipient_index=int(
            buyback_index
        ),

        amount=int(
            authorization.tokens_to_sell
        ),
        min_sol_output=int(
            min_sol_output
        ),

        global_account=str(
            canonical_global
        ),
        base_mint=str(
            base_mint
        ),
        quote_mint=str(
            quote_mint
        ),
        base_token_program=str(
            base_token_program
        ),
        quote_token_program=str(
            quote_token_program
        ),
        associated_token_program=str(
            ASSOCIATED_TOKEN_PROGRAM
        ),

        fee_recipient=str(
            fee_recipient
        ),
        associated_quote_fee_recipient=str(
            associated_quote_fee_recipient
        ),

        buyback_fee_recipient=str(
            buyback_fee_recipient
        ),
        associated_quote_buyback_fee_recipient=str(
            associated_quote_buyback_fee_recipient
        ),

        bonding_curve=str(
            derived_curve
        ),
        associated_base_bonding_curve=str(
            associated_base_bonding_curve
        ),
        associated_quote_bonding_curve=str(
            associated_quote_bonding_curve
        ),

        user=str(
            user
        ),
        associated_base_user=str(
            derived_base_user
        ),
        associated_quote_user=str(
            associated_quote_user
        ),

        creator_vault=str(
            creator_vault
        ),
        associated_creator_vault=str(
            associated_creator_vault
        ),

        sharing_config=str(
            sharing_config
        ),

        user_volume_accumulator=str(
            user_volume_accumulator
        ),
        associated_user_volume_accumulator=str(
            associated_user_volume_accumulator
        ),

        fee_config=str(
            fee_config
        ),
        fee_program=str(
            PUMP_FEE_PROGRAM
        ),

        system_program=str(
            Pubkey.default()
        ),
        event_authority=str(
            event_authority
        ),
        program=str(
            PUMP_PROGRAM
        ),
    )
