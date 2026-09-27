import unittest
from dataclasses import FrozenInstanceError

from src.venues.coinbase.market_sequence import (
    CoinbaseSequenceAssessment,
    CoinbaseSequenceStatus,
    assess_market_sequence,
)


class CoinbaseMarketSequenceTests(unittest.TestCase):
    def assert_assessment(
        self,
        previous_sequence_num,
        current_sequence_num,
        status,
        gap_count=None,
    ):
        assessment = assess_market_sequence(
            previous_sequence_num,
            current_sequence_num,
        )
        self.assertEqual(assessment.previous_sequence_num, previous_sequence_num)
        self.assertEqual(assessment.current_sequence_num, current_sequence_num)
        self.assertIs(assessment.status, status)
        self.assertEqual(assessment.gap_count, gap_count)
        return assessment

    def test_initial_sequence(self):
        self.assert_assessment(None, 42, CoinbaseSequenceStatus.INITIAL)

    def test_exact_contiguous_increment(self):
        self.assert_assessment(42, 43, CoinbaseSequenceStatus.CONTIGUOUS)

    def test_one_message_gap(self):
        self.assert_assessment(42, 44, CoinbaseSequenceStatus.GAP, 1)

    def test_multi_message_gap_has_exact_gap_count(self):
        self.assert_assessment(42, 50, CoinbaseSequenceStatus.GAP, 7)

    def test_repeated_sequence(self):
        self.assert_assessment(42, 42, CoinbaseSequenceStatus.REPEATED)

    def test_out_of_order_sequence(self):
        self.assert_assessment(42, 41, CoinbaseSequenceStatus.OUT_OF_ORDER)

    def test_zero_is_a_valid_sequence_number(self):
        self.assert_assessment(None, 0, CoinbaseSequenceStatus.INITIAL)
        self.assert_assessment(0, 1, CoinbaseSequenceStatus.CONTIGUOUS)
        self.assert_assessment(1, 0, CoinbaseSequenceStatus.OUT_OF_ORDER)

    def test_assessment_is_immutable(self):
        assessment = self.assert_assessment(
            42,
            43,
            CoinbaseSequenceStatus.CONTIGUOUS,
        )
        with self.assertRaises(FrozenInstanceError):
            assessment.current_sequence_num = 44

    def test_invalid_previous_sequence_types_are_rejected(self):
        for value in (True, False, 1.0, "1", [], object()):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    assess_market_sequence(value, 1)

    def test_invalid_current_sequence_types_are_rejected(self):
        for value in (True, False, 1.0, "1", [], object()):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    assess_market_sequence(None, value)

    def test_negative_sequence_numbers_are_rejected(self):
        with self.assertRaises(ValueError):
            assess_market_sequence(-1, 0)
        with self.assertRaises(ValueError):
            assess_market_sequence(None, -1)

    def test_direct_construction_accepts_consistent_values(self):
        assessment = CoinbaseSequenceAssessment(
            previous_sequence_num=10,
            current_sequence_num=13,
            status=CoinbaseSequenceStatus.GAP,
            gap_count=2,
        )
        self.assertEqual(assessment.gap_count, 2)

    def test_direct_construction_rejects_invalid_sequence_values(self):
        invalid_cases = (
            (True, 1),
            (-1, 1),
            (None, False),
            (None, -1),
        )
        for previous, current in invalid_cases:
            with self.subTest(previous=previous, current=current):
                with self.assertRaises((TypeError, ValueError)):
                    CoinbaseSequenceAssessment(
                        previous_sequence_num=previous,
                        current_sequence_num=current,
                        status=CoinbaseSequenceStatus.INITIAL,
                        gap_count=None,
                    )

    def test_direct_construction_rejects_non_status_values(self):
        with self.assertRaises(TypeError):
            CoinbaseSequenceAssessment(
                previous_sequence_num=None,
                current_sequence_num=1,
                status="INITIAL",
                gap_count=None,
            )

    def test_direct_construction_rejects_contradictory_statuses(self):
        cases = (
            (None, 1, CoinbaseSequenceStatus.CONTIGUOUS),
            (1, 2, CoinbaseSequenceStatus.GAP),
            (1, 3, CoinbaseSequenceStatus.REPEATED),
            (2, 2, CoinbaseSequenceStatus.OUT_OF_ORDER),
            (2, 1, CoinbaseSequenceStatus.CONTIGUOUS),
        )
        for previous, current, status in cases:
            with self.subTest(previous=previous, current=current, status=status):
                with self.assertRaises(ValueError):
                    CoinbaseSequenceAssessment(
                        previous_sequence_num=previous,
                        current_sequence_num=current,
                        status=status,
                        gap_count=None,
                    )

    def test_direct_construction_rejects_contradictory_gap_count(self):
        for gap_count in (None, 1, 3, True, 2.0, "2"):
            with self.subTest(gap_count=gap_count):
                with self.assertRaises((TypeError, ValueError)):
                    CoinbaseSequenceAssessment(
                        previous_sequence_num=10,
                        current_sequence_num=13,
                        status=CoinbaseSequenceStatus.GAP,
                        gap_count=gap_count,
                    )

        with self.assertRaises(ValueError):
            CoinbaseSequenceAssessment(
                previous_sequence_num=10,
                current_sequence_num=11,
                status=CoinbaseSequenceStatus.CONTIGUOUS,
                gap_count=0,
            )


if __name__ == "__main__":
    unittest.main()
