from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from solders.pubkey import Pubkey

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
from src.execution.order_authorization import (
    AUTHORIZATION_VERSION,
    AUTHORIZE,
    BUY,
    WRAPPED_SOL_MINT,
    OrderAuthorization,
)
from src.safety.token_safety_resolver import (
    ASSOCIATED_TOKEN_PROGRAM,
    PUMP_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    derive_associated_token_account,
    derive_bonding_curve,
)


PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION = (
    "pump-buy-v2-account-context-v1"
)

FEE_RECIPIENT_SELECTOR_VERSION = (
    "pump-fee-recipient-selector-v1"
)

BUYBACK_RECIPIENT_SELECTOR_VERSION = (
    "pump-buyback-recipient-selector-v1"
)


BUY_V2_ACCOUNT_NAMES = (
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
    "global_volume_accumulator",
    "user_volume_accumulator",
    "associated_user_volume_accumulator",
    "fee_config",
    "fee_program",
    "system_program",
    "event_authority",
    "program",
)


class PumpBuyV2AccountContextError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class PumpBuyV2AccountContext:
    resolver_version: str

    authorization_version: str
    reservation_id: str
    simulation_sha256: str

    global_rpc_slot: int

    fee_recipient_selector_version: str
    fee_recipient_index: int

    buyback_recipient_selector_version: str
    buyback_fee_recipient_index: int

    amount: int
    max_sol_cost: int

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
    global_volume_accumulator: str
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
            self.global_volume_accumulator,
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
                BUY_V2_ACCOUNT_NAMES,
                self.ordered_accounts(),
            )
        )


def _required_pubkey(
    value: str | None,
    *,
    label: str,
) -> Pubkey:

    if not value:
        raise PumpBuyV2AccountContextError(
            f"{label} is missing."
        )

    try:
        result = Pubkey.from_string(
            value
        )

    except Exception as error:
        raise PumpBuyV2AccountContextError(
            f"{label} is not a valid "
            "Solana public key."
        ) from error

    if result == Pubkey.default():
        raise PumpBuyV2AccountContextError(
            f"{label} cannot be the "
            "default public key."
        )

    return result


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
    authorization: OrderAuthorization,
    recipient_count: int,
) -> int:

    if recipient_count <= 0:
        raise PumpBuyV2AccountContextError(
            "Recipient set is empty."
        )

    if not authorization.wallet_pubkey:
        raise PumpBuyV2AccountContextError(
            "Authorization wallet is missing."
        )

    if not authorization.simulation_sha256:
        raise PumpBuyV2AccountContextError(
            "Authorization simulation "
            "fingerprint is missing."
        )

    material = "\x1f".join(
        (
            domain,
            authorization.reservation_id,
            authorization.mint,
            authorization.wallet_pubkey,
            authorization.simulation_sha256,
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


def resolve_pump_buy_v2_account_context(
    *,
    authorization: OrderAuthorization,
    global_state: LivePumpGlobalState,
) -> PumpBuyV2AccountContext:

    #
    # Authority contract.
    #
    if (
        authorization.authorization_version
        != AUTHORIZATION_VERSION
    ):
        raise PumpBuyV2AccountContextError(
            "Authorization contract "
            "version is unsupported."
        )

    if authorization.status != AUTHORIZE:
        raise PumpBuyV2AccountContextError(
            "Order is not authorized."
        )

    if authorization.side != BUY:
        raise PumpBuyV2AccountContextError(
            "Only Pump BUY authorization "
            "is supported."
        )

    if not authorization.is_valid():
        raise PumpBuyV2AccountContextError(
            "Order authorization is expired."
        )

    if not authorization.reservation_id:
        raise PumpBuyV2AccountContextError(
            "Authorization reservation "
            "identity is missing."
        )

    if not authorization.simulation_sha256:
        raise PumpBuyV2AccountContextError(
            "Authorization simulation "
            "fingerprint is missing."
        )

    if (
        not isinstance(
            authorization.mayhem_mode,
            bool,
        )
    ):
        raise PumpBuyV2AccountContextError(
            "Authorization Mayhem mode "
            "is invalid."
        )

    if (
        not isinstance(
            authorization.token_amount,
            int,
        )
        or isinstance(
            authorization.token_amount,
            bool,
        )
        or authorization.token_amount <= 0
    ):
        raise PumpBuyV2AccountContextError(
            "Authorized token amount "
            "is invalid."
        )

    if (
        not isinstance(
            authorization.max_sol_cost,
            int,
        )
        or isinstance(
            authorization.max_sol_cost,
            bool,
        )
        or authorization.max_sol_cost <= 0
    ):
        raise PumpBuyV2AccountContextError(
            "Authorized max SOL cost "
            "is invalid."
        )

    #
    # Global-state contract.
    #
    if (
        global_state.resolver_version
        != LIVE_PUMP_GLOBAL_STATE_VERSION
    ):
        raise PumpBuyV2AccountContextError(
            "Pump Global resolver version "
            "is unsupported."
        )

    if (
        not isinstance(
            global_state.rpc_slot,
            int,
        )
        or isinstance(
            global_state.rpc_slot,
            bool,
        )
        or global_state.rpc_slot < 0
    ):
        raise PumpBuyV2AccountContextError(
            "Pump Global RPC slot "
            "is invalid."
        )

    if (
        global_state.rpc_slot
        < authorization.curve_rpc_slot
    ):
        raise PumpBuyV2AccountContextError(
            "Pump Global state predates "
            "the authorized curve state."
        )

    if not math.isfinite(
        global_state.fetched_at
    ):
        raise PumpBuyV2AccountContextError(
            "Pump Global fetch time "
            "is invalid."
        )

    snapshot = (
        global_state.global_state
    )

    canonical_global = (
        derive_global()
    )

    if snapshot.address != str(
        canonical_global
    ):
        raise PumpBuyV2AccountContextError(
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
        raise PumpBuyV2AccountContextError(
            "Pump Global snapshot "
            "is not execution-valid."
        )

    if (
        authorization.mayhem_mode
        and not snapshot.mayhem_mode_enabled
    ):
        raise PumpBuyV2AccountContextError(
            "Authorized coin is Mayhem "
            "while Global Mayhem mode "
            "is disabled."
        )

    #
    # Parse and independently re-derive everything
    # that can be derived. Authorization remains the
    # authority; derivation is used as a consistency
    # proof, not as a replacement.
    #
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

    authorized_base_curve_ata = (
        _required_pubkey(
            (
                authorization
                .associated_bonding_curve
            ),
            label=(
                "authorized associated "
                "bonding curve"
            ),
        )
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
        raise PumpBuyV2AccountContextError(
            "Unsupported base token program."
        )

    if (
        authorization.quote_mint_for_instruction
        != WRAPPED_SOL_MINT
    ):
        raise PumpBuyV2AccountContextError(
            "Only SOL-paired Pump buys "
            "are authorized."
        )

    quote_mint = _required_pubkey(
        authorization.quote_mint_for_instruction,
        label="quote mint",
    )

    quote_token_program = TOKEN_PROGRAM

    derived_curve = (
        derive_bonding_curve(
            base_mint
        )
    )

    if derived_curve != authorized_curve:
        raise PumpBuyV2AccountContextError(
            "Authorized bonding curve "
            "does not match canonical PDA."
        )

    derived_base_curve_ata = (
        derive_associated_token_account(
            owner=derived_curve,
            mint=base_mint,
            token_program=(
                base_token_program
            ),
        )
    )

    if (
        derived_base_curve_ata
        != authorized_base_curve_ata
    ):
        raise PumpBuyV2AccountContextError(
            "Authorized bonding-curve ATA "
            "does not match canonical ATA."
        )

    #
    # Fee-recipient choice is deterministic for one
    # risk-authorized reservation. It is selected
    # only from the current on-chain Global set.
    #
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
        raise PumpBuyV2AccountContextError(
            "Pump fee recipient set "
            "must contain exactly 8 entries."
        )

    if len(
        buyback_recipients
    ) != 8:
        raise PumpBuyV2AccountContextError(
            "Pump buyback recipient set "
            "must contain exactly 8 entries."
        )

    fee_index = _selection_index(
        domain=(
            FEE_RECIPIENT_SELECTOR_VERSION
        ),
        authorization=authorization,
        recipient_count=len(
            fee_recipients
        ),
    )

    buyback_index = _selection_index(
        domain=(
            BUYBACK_RECIPIENT_SELECTOR_VERSION
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

    (
        associated_quote_buyback_fee_recipient
    ) = derive_associated_token_account(
        owner=buyback_fee_recipient,
        mint=quote_mint,
        token_program=(
            quote_token_program
        ),
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

    associated_base_user = (
        derive_associated_token_account(
            owner=user,
            mint=base_mint,
            token_program=(
                base_token_program
            ),
        )
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

    global_volume_accumulator = (
        _derive_pump_pda(
            b"global_volume_accumulator"
        )
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

    return PumpBuyV2AccountContext(
        resolver_version=(
            PUMP_BUY_V2_ACCOUNT_CONTEXT_VERSION
        ),
        authorization_version=(
            authorization.authorization_version
        ),
        reservation_id=(
            authorization.reservation_id
        ),
        simulation_sha256=(
            authorization.simulation_sha256
        ),
        global_rpc_slot=int(
            global_state.rpc_slot
        ),
        fee_recipient_selector_version=(
            FEE_RECIPIENT_SELECTOR_VERSION
        ),
        fee_recipient_index=int(
            fee_index
        ),
        buyback_recipient_selector_version=(
            BUYBACK_RECIPIENT_SELECTOR_VERSION
        ),
        buyback_fee_recipient_index=int(
            buyback_index
        ),
        amount=int(
            authorization.token_amount
        ),
        max_sol_cost=int(
            authorization.max_sol_cost
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
            derived_base_curve_ata
        ),
        associated_quote_bonding_curve=str(
            associated_quote_bonding_curve
        ),
        user=str(
            user
        ),
        associated_base_user=str(
            associated_base_user
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
        global_volume_accumulator=str(
            global_volume_accumulator
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
