"""Focused tests for isolated Pump instrument identity translation."""

import unittest

from src.domain.instrument_id import InstrumentId
from src.venues.pump.instrument_identity import (
    PUMP_VENUE_ID,
    SOLANA_MAINNET_ENVIRONMENT_ID,
    instrument_id_from_mint,
)


class PumpInstrumentIdentityTests(unittest.TestCase):
    def test_representative_mint(self):
        mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCaM2aF4YM1pPB263"
        self.assertEqual(
            instrument_id_from_mint(mint),
            InstrumentId("pump", "solana-mainnet", mint),
        )

    def test_stable_explicit_venue(self):
        self.assertEqual(PUMP_VENUE_ID, "pump")
        self.assertEqual(instrument_id_from_mint("mint").venue_id, "pump")

    def test_stable_explicit_environment(self):
        self.assertEqual(SOLANA_MAINNET_ENVIRONMENT_ID, "solana-mainnet")
        self.assertEqual(
            instrument_id_from_mint("mint").environment_id, "solana-mainnet"
        )

    def test_exact_text_preserved_without_native_format_validation(self):
        for mint in ("aBc/XYZ:01._-", "native identifier", "é", "e\u0301", "<mint>"):
            with self.subTest(mint=mint):
                self.assertEqual(
                    instrument_id_from_mint(mint).venue_instrument_id, mint
                )

    def test_different_mints_produce_different_identities(self):
        for first, second in (("MintA", "MintB"), ("Mint", "mint"), ("é", "e\u0301")):
            with self.subTest(first=first, second=second):
                self.assertNotEqual(
                    instrument_id_from_mint(first), instrument_id_from_mint(second)
                )

    def test_invalid_inputs_fail_with_generic_contract_errors(self):
        invalid = (
            None, 1, True, b"mint", [], {},
            "", " ", "\t", " mint", "mint ", "\u2003mint", "mint\u00a0",
            "mi\x00nt", "mi\nnt", "mi\x7fnt", "mi\x85nt",
        )
        for mint in invalid:
            with self.subTest(mint=mint):
                error_type = TypeError if not isinstance(mint, str) else ValueError
                with self.assertRaises(error_type) as generic:
                    InstrumentId("pump", "solana-mainnet", mint)
                with self.assertRaises(error_type) as adapted:
                    instrument_id_from_mint(mint)
                self.assertEqual(str(adapted.exception), str(generic.exception))

    def test_input_is_not_mutated(self):
        mint = "MiXeD-native-identifier"
        original = mint
        identity = instrument_id_from_mint(mint)
        self.assertEqual(mint, "MiXeD-native-identifier")
        self.assertIs(mint, original)
        self.assertEqual(identity.venue_instrument_id, original)


if __name__ == "__main__":
    unittest.main()
