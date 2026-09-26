"""Focused tests for the venue-neutral trading account identity contract."""

from dataclasses import FrozenInstanceError, fields
import unittest

from src.domain.trading_account_id import TradingAccountId


class TradingAccountIdTests(unittest.TestCase):
    def setUp(self):
        self.values = {
            "venue_id": "VenueA",
            "environment_id": "production",
            "venue_account_id": "account-01",
        }

    def assert_rejected_for_each_field(self, values, exception):
        for field_name in self.values:
            for value in values:
                with self.subTest(field=field_name, value=value):
                    arguments = dict(self.values, **{field_name: value})
                    with self.assertRaises(exception):
                        TradingAccountId(**arguments)

    def test_valid_construction(self):
        identity = TradingAccountId(**self.values)
        for field_name, value in self.values.items():
            self.assertEqual(getattr(identity, field_name), value)

    def test_exactly_three_required_fields(self):
        self.assertEqual([field.name for field in fields(TradingAccountId)], list(self.values))
        for field_name in self.values:
            with self.subTest(field=field_name):
                arguments = self.values.copy()
                del arguments[field_name]
                with self.assertRaises(TypeError):
                    TradingAccountId(**arguments)

    def test_immutable(self):
        identity = TradingAccountId(**self.values)
        for field_name in self.values:
            with self.subTest(field=field_name):
                with self.assertRaises(FrozenInstanceError):
                    setattr(identity, field_name, "changed")
                with self.assertRaises(FrozenInstanceError):
                    delattr(identity, field_name)
        self.assertFalse(hasattr(identity, "__dict__"))

    def test_hashable(self):
        identity = TradingAccountId(**self.values)
        duplicate = TradingAccountId(**self.values)
        self.assertEqual(hash(identity), hash(duplicate))
        self.assertEqual({identity: "found"}[duplicate], "found")

    def test_equal_full_identities(self):
        self.assertEqual(TradingAccountId(**self.values), TradingAccountId(**self.values))

    def test_different_venue(self):
        self.assertNotEqual(TradingAccountId(**self.values), TradingAccountId("VenueB", "production", "account-01"))

    def test_different_environment(self):
        self.assertNotEqual(TradingAccountId(**self.values), TradingAccountId("VenueA", "sandbox", "account-01"))

    def test_different_native_identifier(self):
        self.assertNotEqual(TradingAccountId(**self.values), TradingAccountId("VenueA", "production", "account-02"))

    def test_non_strings_rejected(self):
        self.assert_rejected_for_each_field([None, 1, True, b"account-01", [], {}, object()], TypeError)

    def test_empty_strings_rejected(self):
        self.assert_rejected_for_each_field([""], ValueError)

    def test_whitespace_only_rejected(self):
        self.assert_rejected_for_each_field([" ", "\t", "\n", "\u00a0", "\u2003"], ValueError)

    def test_boundary_whitespace_rejected(self):
        self.assert_rejected_for_each_field([" account-01", "account-01 ", "\taccount-01", "account-01\n", "\u2003account-01", "account-01\u00a0"], ValueError)

    def test_control_characters_rejected(self):
        controls = [chr(code) for code in (*range(32), *range(127, 160))]
        self.assert_rejected_for_each_field([f"A{control}B" for control in controls], ValueError)

    def test_native_text_preserved_exactly(self):
        for native_id in ("account-01", "aBc/XYZ:01._-", "native identifier", "é", "e\u0301", "<account>"):
            with self.subTest(native_id=native_id):
                identity = TradingAccountId("Venue", "Environment", native_id)
                self.assertEqual(identity.venue_account_id, native_id)
        self.assertNotEqual(TradingAccountId("V", "E", "é"), TradingAccountId("V", "E", "e\u0301"))

    def test_namespace_text_preserved_exactly(self):
        identity = TradingAccountId("Venue.A-b", "Env_Prod-1", "native")
        self.assertEqual(identity.venue_id, "Venue.A-b")
        self.assertEqual(identity.environment_id, "Env_Prod-1")
        self.assertNotEqual(identity, TradingAccountId("venue.a-b", "Env_Prod-1", "native"))
        self.assertNotEqual(identity, TradingAccountId("Venue.A-b", "env_prod-1", "native"))


    def test_only_identity_state_and_public_surface(self):
        identity = TradingAccountId(**self.values)
        authoritative_fields = {field.name for field in fields(TradingAccountId)}
        self.assertEqual(
            authoritative_fields,
            {"venue_id", "environment_id", "venue_account_id"},
        )
        for extra_field in (
            "custody", "wallet", "signer", "fee_payer", "settlement_destination",
            "token_account", "capital_pool", "authorization", "process_authority",
            "database_authority", "account_class", "portfolio", "balance",
        ):
            with self.subTest(extra_field=extra_field):
                self.assertNotIn(extra_field, authoritative_fields)
                self.assertFalse(hasattr(identity, extra_field))
                with self.assertRaises(TypeError):
                    TradingAccountId(**self.values, **{extra_field: "extra"})


if __name__ == "__main__":
    unittest.main()
