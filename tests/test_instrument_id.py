"""Focused tests for the venue-neutral instrument identity contract."""

from dataclasses import FrozenInstanceError, fields
import unittest

from src.domain.instrument_id import InstrumentId


class InstrumentIdTests(unittest.TestCase):
    def setUp(self):
        self.values = {
            "venue_id": "Coinbase",
            "environment_id": "production",
            "venue_instrument_id": "BTC-USD",
        }

    def assert_rejected_for_each_field(self, values, exception):
        for field_name in self.values:
            for value in values:
                with self.subTest(field=field_name, value=value):
                    arguments = dict(self.values, **{field_name: value})
                    with self.assertRaises(exception):
                        InstrumentId(**arguments)

    def test_valid_construction(self):
        identity = InstrumentId(**self.values)
        for field_name, value in self.values.items():
            self.assertEqual(getattr(identity, field_name), value)

    def test_exactly_three_required_fields(self):
        self.assertEqual([field.name for field in fields(InstrumentId)], list(self.values))
        for field_name in self.values:
            with self.subTest(field=field_name):
                arguments = self.values.copy()
                del arguments[field_name]
                with self.assertRaises(TypeError):
                    InstrumentId(**arguments)

    def test_immutable(self):
        identity = InstrumentId(**self.values)
        for field_name in self.values:
            with self.subTest(field=field_name):
                with self.assertRaises(FrozenInstanceError):
                    setattr(identity, field_name, "changed")
                with self.assertRaises(FrozenInstanceError):
                    delattr(identity, field_name)
        self.assertFalse(hasattr(identity, "__dict__"))

    def test_hashable(self):
        identity = InstrumentId(**self.values)
        duplicate = InstrumentId(**self.values)
        self.assertEqual(hash(identity), hash(duplicate))
        self.assertEqual({identity: "found"}[duplicate], "found")

    def test_equal_full_identities(self):
        self.assertEqual(InstrumentId(**self.values), InstrumentId(**self.values))

    def test_different_venue(self):
        self.assertNotEqual(InstrumentId(**self.values), InstrumentId("Robinhood", "production", "BTC-USD"))

    def test_different_environment(self):
        self.assertNotEqual(InstrumentId(**self.values), InstrumentId("Coinbase", "sandbox", "BTC-USD"))

    def test_different_native_identifier(self):
        self.assertNotEqual(InstrumentId(**self.values), InstrumentId("Coinbase", "production", "ETH-USD"))

    def test_non_strings_rejected(self):
        self.assert_rejected_for_each_field([None, 1, True, b"BTC-USD", [], {}, object()], TypeError)

    def test_empty_strings_rejected(self):
        self.assert_rejected_for_each_field([""], ValueError)

    def test_whitespace_only_rejected(self):
        self.assert_rejected_for_each_field([" ", "\t", "\n", "\u00a0", "\u2003"], ValueError)

    def test_boundary_whitespace_rejected(self):
        self.assert_rejected_for_each_field([" BTC-USD", "BTC-USD ", "\tBTC-USD", "BTC-USD\n", "\u2003BTC-USD", "BTC-USD\u00a0"], ValueError)

    def test_control_characters_rejected(self):
        controls = [chr(code) for code in (*range(32), *range(127, 160))]
        self.assert_rejected_for_each_field([f"A{control}B" for control in controls], ValueError)

    def test_native_text_preserved_exactly(self):
        for native_id in ("BTC-USD", "aBc/XYZ:01._-", "native identifier", "é", "e\u0301", "<mint>"):
            with self.subTest(native_id=native_id):
                identity = InstrumentId("Venue", "Environment", native_id)
                self.assertEqual(identity.venue_instrument_id, native_id)
        self.assertNotEqual(InstrumentId("V", "E", "é"), InstrumentId("V", "E", "e\u0301"))

    def test_namespace_text_preserved_exactly(self):
        identity = InstrumentId("Venue.A-b", "Env_Prod-1", "native")
        self.assertEqual(identity.venue_id, "Venue.A-b")
        self.assertEqual(identity.environment_id, "Env_Prod-1")
        self.assertNotEqual(identity, InstrumentId("venue.a-b", "Env_Prod-1", "native"))
        self.assertNotEqual(identity, InstrumentId("Venue.A-b", "env_prod-1", "native"))


if __name__ == "__main__":
    unittest.main()
