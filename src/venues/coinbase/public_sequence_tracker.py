"""Connection-scoped Coinbase public message sequence integrity tracking."""

from dataclasses import dataclass
from enum import Enum

from src.venues.coinbase.market_sequence import (
    CoinbaseSequenceAssessment,
    CoinbaseSequenceStatus,
    assess_market_sequence,
)


class CoinbaseConnectionSequenceIntegrity(str, Enum):
    """Sticky integrity state for one Coinbase WebSocket connection."""

    INTACT = "INTACT"
    COMPROMISED = "COMPROMISED"


_COMPROMISING_STATUSES = frozenset(
    {
        CoinbaseSequenceStatus.GAP,
        CoinbaseSequenceStatus.REPEATED,
        CoinbaseSequenceStatus.OUT_OF_ORDER,
    }
)


@dataclass(frozen=True, slots=True)
class CoinbaseConnectionSequenceObservation:
    """One sequence assessment and the connection's sticky integrity state."""

    assessment: CoinbaseSequenceAssessment
    integrity: CoinbaseConnectionSequenceIntegrity

    def __post_init__(self) -> None:
        if not isinstance(self.assessment, CoinbaseSequenceAssessment):
            raise TypeError("assessment must be a CoinbaseSequenceAssessment")
        if type(self.integrity) is not CoinbaseConnectionSequenceIntegrity:
            raise TypeError(
                "integrity must be a CoinbaseConnectionSequenceIntegrity"
            )
        if (
            self.assessment.status in _COMPROMISING_STATUSES
            and self.integrity is CoinbaseConnectionSequenceIntegrity.INTACT
        ):
            raise ValueError("an anomalous assessment cannot have INTACT integrity")


class CoinbaseConnectionSequenceTracker:
    """Track sticky sequence integrity for one Coinbase connection session."""

    __slots__ = ("_previous_sequence_num", "_integrity")

    def __init__(self) -> None:
        self._previous_sequence_num: int | None = None
        self._integrity = CoinbaseConnectionSequenceIntegrity.INTACT

    @property
    def previous_sequence_num(self) -> int | None:
        return self._previous_sequence_num

    @property
    def integrity(self) -> CoinbaseConnectionSequenceIntegrity:
        return self._integrity

    def observe(
        self,
        sequence_num: int,
    ) -> CoinbaseConnectionSequenceObservation:
        """Assess one connection sequence and update state only after validation."""

        assessment = assess_market_sequence(
            self._previous_sequence_num,
            sequence_num,
        )
        next_integrity = self._integrity
        if assessment.status in _COMPROMISING_STATUSES:
            next_integrity = CoinbaseConnectionSequenceIntegrity.COMPROMISED

        observation = CoinbaseConnectionSequenceObservation(
            assessment=assessment,
            integrity=next_integrity,
        )
        self._previous_sequence_num = sequence_num
        self._integrity = next_integrity
        return observation
