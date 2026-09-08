import base64
import struct
import unittest

from solders.pubkey import Pubkey

from src.execution.live_pump_global_state import (
    GLOBAL_ACCOUNT_SIZE,
    GLOBAL_DISCRIMINATOR,
    decode_global_state,
    derive_global,
)
from src.safety.token_safety_resolver import (
    PUMP_PROGRAM,
    SafetyResolutionError,
)


def _u64(
    value: int,
) -> bytes:
    return struct.pack(
        "<Q",
        value,
    )


def _bool(
    value: bool,
) -> bytes:
    return bytes(
        [
            1
            if value
            else 0
        ]
    )


def _pubkey_bytes(
    pubkey: Pubkey,
) -> bytes:
    return bytes(
        pubkey
    )


class LivePumpGlobalStateTests(
    unittest.TestCase,
):
    def setUp(self):
        self.address = str(
            derive_global()
        )

        self.authority = (
            Pubkey.new_unique()
        )

        self.primary_fee = (
            Pubkey.new_unique()
        )

        self.secondary_fees = tuple(
            Pubkey.new_unique()
            for _ in range(
                7
            )
        )

        self.withdraw_authority = (
            Pubkey.new_unique()
        )

        self.set_creator_authority = (
            Pubkey.new_unique()
        )

        self.admin_set_creator_authority = (
            Pubkey.new_unique()
        )

        self.whitelist_pda = (
            Pubkey.new_unique()
        )

        self.primary_reserved_fee = (
            Pubkey.new_unique()
        )

        self.secondary_reserved_fees = (
            tuple(
                Pubkey.new_unique()
                for _ in range(
                    7
                )
            )
        )

        self.buyback_fees = tuple(
            Pubkey.new_unique()
            for _ in range(
                8
            )
        )

        self.whitelisted_quote_mint = (
            Pubkey.new_unique()
        )

    def make_payload(
        self,
        *,
        initialized_byte: bytes | None = None,
    ) -> bytes:

        payload = bytearray(
            GLOBAL_DISCRIMINATOR
        )

        payload.extend(
            (
                _bool(True)
                if initialized_byte is None
                else initialized_byte
            )
        )

        payload.extend(
            _pubkey_bytes(
                self.authority
            )
        )

        payload.extend(
            _pubkey_bytes(
                self.primary_fee
            )
        )

        payload.extend(
            _u64(
                1_000
            )
        )

        payload.extend(
            _u64(
                2_000
            )
        )

        payload.extend(
            _u64(
                3_000
            )
        )

        payload.extend(
            _u64(
                4_000
            )
        )

        payload.extend(
            _u64(
                5_000
            )
        )

        payload.extend(
            _pubkey_bytes(
                self.withdraw_authority
            )
        )

        payload.extend(
            _bool(
                True
            )
        )

        payload.extend(
            _u64(
                6_000
            )
        )

        payload.extend(
            _u64(
                700
            )
        )

        for recipient in (
            self.secondary_fees
        ):
            payload.extend(
                _pubkey_bytes(
                    recipient
                )
            )

        payload.extend(
            _pubkey_bytes(
                self.set_creator_authority
            )
        )

        payload.extend(
            _pubkey_bytes(
                self.admin_set_creator_authority
            )
        )

        payload.extend(
            _bool(
                True
            )
        )

        payload.extend(
            _pubkey_bytes(
                self.whitelist_pda
            )
        )

        payload.extend(
            _pubkey_bytes(
                self.primary_reserved_fee
            )
        )

        payload.extend(
            _bool(
                True
            )
        )

        for recipient in (
            self.secondary_reserved_fees
        ):
            payload.extend(
                _pubkey_bytes(
                    recipient
                )
            )

        payload.extend(
            _bool(
                True
            )
        )

        for recipient in (
            self.buyback_fees
        ):
            payload.extend(
                _pubkey_bytes(
                    recipient
                )
            )

        payload.extend(
            _u64(
                125
            )
        )

        payload.extend(
            _u64(
                30_000_000_000
            )
        )

        payload.extend(
            _pubkey_bytes(
                self.whitelisted_quote_mint
            )
        )

        self.assertEqual(
            len(payload),
            GLOBAL_ACCOUNT_SIZE,
        )

        return bytes(
            payload
        )

    def make_account(
        self,
        payload: bytes,
        *,
        owner: str | None = None,
    ) -> dict:

        return {
            "owner": (
                str(PUMP_PROGRAM)
                if owner is None
                else owner
            ),
            "data": [
                base64.b64encode(
                    payload
                ).decode(
                    "ascii"
                ),
                "base64",
            ],
        }

    def test_valid_global_decodes(
        self,
    ):
        result = decode_global_state(
            address=self.address,
            account=self.make_account(
                self.make_payload()
            ),
        )

        self.assertEqual(
            result.account_size,
            GLOBAL_ACCOUNT_SIZE,
        )

        self.assertTrue(
            result.owner_verified
        )

        self.assertTrue(
            result.discriminator_verified
        )

        self.assertTrue(
            result.initialized
        )

        self.assertEqual(
            result.authority,
            str(self.authority),
        )

        self.assertEqual(
            result.normal_fee_recipients,
            (
                str(
                    self.primary_fee
                ),
                *tuple(
                    str(
                        recipient
                    )
                    for recipient
                    in self.secondary_fees
                ),
            ),
        )

        self.assertEqual(
            result.mayhem_fee_recipients,
            (
                str(
                    self.primary_reserved_fee
                ),
                *tuple(
                    str(
                        recipient
                    )
                    for recipient
                    in self.secondary_reserved_fees
                ),
            ),
        )

        self.assertEqual(
            result.buyback_fee_recipients,
            tuple(
                str(
                    recipient
                )
                for recipient
                in self.buyback_fees
            ),
        )

        self.assertEqual(
            result.buyback_basis_points,
            125,
        )

        self.assertEqual(
            result.initial_virtual_quote_reserves,
            30_000_000_000,
        )

        self.assertEqual(
            result.whitelisted_quote_mints,
            (
                str(
                    self.whitelisted_quote_mint
                ),
            ),
        )

    def test_wrong_owner_rejected(
        self,
    ):
        with self.assertRaises(
            SafetyResolutionError
        ):
            decode_global_state(
                address=self.address,
                account=self.make_account(
                    self.make_payload(),
                    owner=str(
                        Pubkey.new_unique()
                    ),
                ),
            )

    def test_wrong_discriminator_rejected(
        self,
    ):
        payload = bytearray(
            self.make_payload()
        )

        payload[0] ^= 0xFF

        with self.assertRaises(
            SafetyResolutionError
        ):
            decode_global_state(
                address=self.address,
                account=self.make_account(
                    bytes(
                        payload
                    )
                ),
            )

    def test_wrong_size_rejected(
        self,
    ):
        payload = (
            self.make_payload()[
                :-1
            ]
        )

        with self.assertRaises(
            SafetyResolutionError
        ):
            decode_global_state(
                address=self.address,
                account=self.make_account(
                    payload
                ),
            )

    def test_invalid_bool_rejected(
        self,
    ):
        payload = self.make_payload(
            initialized_byte=b"\x02",
        )

        with self.assertRaises(
            SafetyResolutionError
        ):
            decode_global_state(
                address=self.address,
                account=self.make_account(
                    payload
                ),
            )

    def test_wrong_global_address_rejected(
        self,
    ):
        with self.assertRaises(
            SafetyResolutionError
        ):
            decode_global_state(
                address=str(
                    Pubkey.new_unique()
                ),
                account=self.make_account(
                    self.make_payload()
                ),
            )


if __name__ == "__main__":
    unittest.main()
