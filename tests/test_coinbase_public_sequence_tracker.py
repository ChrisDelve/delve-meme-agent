import unittest
from dataclasses import FrozenInstanceError

from src.venues.coinbase.market_sequence import (
    CoinbaseSequenceStatus,
    assess_market_sequence,
)
from src.venues.coinbase.public_sequence_tracker import (
    CoinbaseConnectionSequenceIntegrity,
    CoinbaseConnectionSequenceObservation,
    CoinbaseConnectionSequenceTracker,
)


class CoinbasePublicSequenceTrackerTests(unittest.TestCase):
    def assert_observation(
        self,
        observation,
        status,
        integrity,
        previous,
        current,
        gap_count=None,
    ):
        self.assertIs(observation.assessment.status, status)
        self.assertIs(observation.integrity, integrity)
        self.assertEqual(observation.assessment.previous_sequence_num, previous)
        self.assertEqual(observation.assessment.current_sequence_num, current)
        self.assertEqual(observation.assessment.gap_count, gap_count)

    def test_first_sequence_is_initial_and_intact(self):
        tracker = CoinbaseConnectionSequenceTracker()
        observation = tracker.observe(42)
        self.assert_observation(
            observation,
            CoinbaseSequenceStatus.INITIAL,
            CoinbaseConnectionSequenceIntegrity.INTACT,
            None,
            42,
        )

    def test_zero_is_a_valid_first_sequence(self):
        tracker = CoinbaseConnectionSequenceTracker()
        observation = tracker.observe(0)
        self.assert_observation(
            observation,
            CoinbaseSequenceStatus.INITIAL,
            CoinbaseConnectionSequenceIntegrity.INTACT,
            None,
            0,
        )

    def test_exact_increment_is_contiguous_and_intact(self):
        tracker = CoinbaseConnectionSequenceTracker()
        tracker.observe(42)
        observation = tracker.observe(43)
        self.assert_observation(
            observation,
            CoinbaseSequenceStatus.CONTIGUOUS,
            CoinbaseConnectionSequenceIntegrity.INTACT,
            42,
            43,
        )

    def test_gap_compromises_integrity(self):
        tracker = CoinbaseConnectionSequenceTracker()
        tracker.observe(42)
        observation = tracker.observe(45)
        self.assert_observation(
            observation,
            CoinbaseSequenceStatus.GAP,
            CoinbaseConnectionSequenceIntegrity.COMPROMISED,
            42,
            45,
            gap_count=2,
        )

    def test_repeated_sequence_compromises_integrity(self):
        tracker = CoinbaseConnectionSequenceTracker()
        tracker.observe(42)
        observation = tracker.observe(42)
        self.assert_observation(
            observation,
            CoinbaseSequenceStatus.REPEATED,
            CoinbaseConnectionSequenceIntegrity.COMPROMISED,
            42,
            42,
        )

    def test_out_of_order_sequence_compromises_integrity(self):
        tracker = CoinbaseConnectionSequenceTracker()
        tracker.observe(42)
        observation = tracker.observe(40)
        self.assert_observation(
            observation,
            CoinbaseSequenceStatus.OUT_OF_ORDER,
            CoinbaseConnectionSequenceIntegrity.COMPROMISED,
            42,
            40,
        )

    def test_contiguous_after_gap_remains_compromised(self):
        tracker = CoinbaseConnectionSequenceTracker()
        tracker.observe(10)
        tracker.observe(15)
        observation = tracker.observe(16)
        self.assert_observation(
            observation,
            CoinbaseSequenceStatus.CONTIGUOUS,
            CoinbaseConnectionSequenceIntegrity.COMPROMISED,
            15,
            16,
        )

    def test_contiguous_after_repeated_or_out_of_order_remains_compromised(self):
        scenarios = (
            (10, 10, 11),
            (10, 8, 9),
        )
        for first, anomalous, contiguous in scenarios:
            with self.subTest(
                first=first,
                anomalous=anomalous,
                contiguous=contiguous,
            ):
                tracker = CoinbaseConnectionSequenceTracker()
                tracker.observe(first)
                tracker.observe(anomalous)
                observation = tracker.observe(contiguous)
                self.assert_observation(
                    observation,
                    CoinbaseSequenceStatus.CONTIGUOUS,
                    CoinbaseConnectionSequenceIntegrity.COMPROMISED,
                    anomalous,
                    contiguous,
                )

    def test_previous_sequence_updates_after_each_valid_observation(self):
        tracker = CoinbaseConnectionSequenceTracker()
        self.assertIsNone(tracker.previous_sequence_num)
        tracker.observe(5)
        self.assertEqual(tracker.previous_sequence_num, 5)
        tracker.observe(9)
        self.assertEqual(tracker.previous_sequence_num, 9)
        tracker.observe(7)
        self.assertEqual(tracker.previous_sequence_num, 7)

    def test_new_tracker_does_not_inherit_compromise(self):
        compromised = CoinbaseConnectionSequenceTracker()
        compromised.observe(1)
        compromised.observe(3)
        self.assertIs(
            compromised.integrity,
            CoinbaseConnectionSequenceIntegrity.COMPROMISED,
        )

        fresh = CoinbaseConnectionSequenceTracker()
        observation = fresh.observe(100)
        self.assertIs(
            observation.integrity,
            CoinbaseConnectionSequenceIntegrity.INTACT,
        )

    def test_invalid_types_are_rejected_without_mutating_state(self):
        for value in (True, False, 1.0, "4", None, object()):
            with self.subTest(value=value):
                tracker = CoinbaseConnectionSequenceTracker()
                tracker.observe(4)
                with self.assertRaises(TypeError):
                    tracker.observe(value)
                self.assertEqual(tracker.previous_sequence_num, 4)
                self.assertIs(
                    tracker.integrity,
                    CoinbaseConnectionSequenceIntegrity.INTACT,
                )

    def test_negative_sequence_is_rejected_without_mutating_compromise(self):
        tracker = CoinbaseConnectionSequenceTracker()
        tracker.observe(4)
        tracker.observe(6)
        with self.assertRaises(ValueError):
            tracker.observe(-1)
        self.assertEqual(tracker.previous_sequence_num, 6)
        self.assertIs(
            tracker.integrity,
            CoinbaseConnectionSequenceIntegrity.COMPROMISED,
        )

    def test_direct_observation_rejects_anomaly_with_intact_integrity(self):
        anomalous_assessments = (
            assess_market_sequence(1, 3),
            assess_market_sequence(1, 1),
            assess_market_sequence(3, 1),
        )
        for assessment in anomalous_assessments:
            with self.subTest(status=assessment.status), self.assertRaises(ValueError):
                CoinbaseConnectionSequenceObservation(
                    assessment,
                    CoinbaseConnectionSequenceIntegrity.INTACT,
                )

    def test_direct_observation_allows_contiguous_with_compromised_integrity(self):
        assessment = assess_market_sequence(10, 11)
        observation = CoinbaseConnectionSequenceObservation(
            assessment,
            CoinbaseConnectionSequenceIntegrity.COMPROMISED,
        )
        self.assertIs(
            observation.integrity,
            CoinbaseConnectionSequenceIntegrity.COMPROMISED,
        )

    def test_direct_observation_rejects_wrong_types(self):
        assessment = assess_market_sequence(None, 1)
        with self.assertRaises(TypeError):
            CoinbaseConnectionSequenceObservation(
                object(),
                CoinbaseConnectionSequenceIntegrity.INTACT,
            )
        with self.assertRaises(TypeError):
            CoinbaseConnectionSequenceObservation(assessment, "INTACT")

    def test_observation_is_immutable(self):
        observation = CoinbaseConnectionSequenceTracker().observe(1)
        with self.assertRaises(FrozenInstanceError):
            observation.integrity = CoinbaseConnectionSequenceIntegrity.COMPROMISED


if __name__ == "__main__":
    unittest.main()
