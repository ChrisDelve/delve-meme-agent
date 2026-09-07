from copy import deepcopy
import unittest

from src.safety.token_safety_resolver import (
    SafetyResolutionError,
    parse_token_account,
)


PROGRAM = (
    "ExpectedTokenProgram111111111111111111111111"
)

MINT = (
    "ExpectedMint11111111111111111111111111111111"
)

AUTHORITY = (
    "ExpectedAuthority111111111111111111111111111"
)


def make_account() -> dict:
    return {
        "owner": PROGRAM,
        "data": {
            "parsed": {
                "info": {
                    "mint": MINT,
                    "owner": AUTHORITY,
                    "tokenAmount": {
                        "amount": "123456",
                    },
                },
            },
        },
    }


class ParseTokenAccountTests(
    unittest.TestCase
):
    def test_valid_account(self):
        result = parse_token_account(
            account=make_account(),
            expected_mint=MINT,
            expected_token_program=PROGRAM,
            expected_owner=AUTHORITY,
        )

        self.assertEqual(
            result,
            (
                AUTHORITY,
                123456,
            ),
        )

    def test_none_account_returns_none(self):
        result = parse_token_account(
            account=None,
            expected_mint=MINT,
            expected_token_program=PROGRAM,
        )

        self.assertIsNone(
            result
        )

    def test_wrong_token_program_rejected(self):
        account = make_account()
        account["owner"] = "WrongProgram"

        with self.assertRaisesRegex(
            SafetyResolutionError,
            "program-owner mismatch",
        ):
            parse_token_account(
                account=account,
                expected_mint=MINT,
                expected_token_program=PROGRAM,
            )

    def test_wrong_mint_rejected(self):
        account = make_account()
        account["data"]["parsed"]["info"][
            "mint"
        ] = "WrongMint"

        with self.assertRaisesRegex(
            SafetyResolutionError,
            "mint mismatch",
        ):
            parse_token_account(
                account=account,
                expected_mint=MINT,
                expected_token_program=PROGRAM,
            )

    def test_wrong_authority_rejected(self):
        with self.assertRaisesRegex(
            SafetyResolutionError,
            "authority mismatch",
        ):
            parse_token_account(
                account=make_account(),
                expected_mint=MINT,
                expected_token_program=PROGRAM,
                expected_owner="WrongAuthority",
            )

    def test_invalid_amount_rejected(self):
        account = make_account()
        account["data"]["parsed"]["info"][
            "tokenAmount"
        ]["amount"] = "not-an-int"

        with self.assertRaisesRegex(
            SafetyResolutionError,
            "Invalid token-account amount",
        ):
            parse_token_account(
                account=account,
                expected_mint=MINT,
                expected_token_program=PROGRAM,
            )

    def test_negative_amount_rejected(self):
        account = make_account()
        account["data"]["parsed"]["info"][
            "tokenAmount"
        ]["amount"] = "-1"

        with self.assertRaisesRegex(
            SafetyResolutionError,
            "Negative token-account amount",
        ):
            parse_token_account(
                account=account,
                expected_mint=MINT,
                expected_token_program=PROGRAM,
            )

    def test_malformed_data_rejected(self):
        account = make_account()
        account["data"] = "bad-data"

        with self.assertRaisesRegex(
            SafetyResolutionError,
            "not JSON parsed",
        ):
            parse_token_account(
                account=account,
                expected_mint=MINT,
                expected_token_program=PROGRAM,
            )

    def test_missing_owner_rejected(self):
        account = make_account()
        del account["data"]["parsed"]["info"][
            "owner"
        ]

        with self.assertRaisesRegex(
            SafetyResolutionError,
            "Incomplete parsed token account",
        ):
            parse_token_account(
                account=account,
                expected_mint=MINT,
                expected_token_program=PROGRAM,
            )


if __name__ == "__main__":
    unittest.main()
