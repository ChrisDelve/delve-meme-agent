"""Coinbase Advanced market message sequence continuity assessment."""

from dataclasses import dataclass
from enum import Enum


class CoinbaseSequenceStatus(str, Enum):
    """The relationship between consecutive Coinbase message sequences."""

    INITIAL = "INITIAL"
    CONTIGUOUS = "CONTIGUOUS"
    GAP = "GAP"
    REPEATED = "REPEATED"
    OUT_OF_ORDER = "OUT_OF_ORDER"


def _validate_sequence_num(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an exact int")
    if value < 0:
        raise ValueError(f"{field_name} must be nonnegative")
    return value


def _expected_assessment(
    previous_sequence_num: int | None,
    current_sequence_num: int,
) -> tuple[CoinbaseSequenceStatus, int | None]:
    if previous_sequence_num is None:
        return CoinbaseSequenceStatus.INITIAL, None
    if current_sequence_num == previous_sequence_num + 1:
        return CoinbaseSequenceStatus.CONTIGUOUS, None
    if current_sequence_num > previous_sequence_num + 1:
        return (
            CoinbaseSequenceStatus.GAP,
            current_sequence_num - previous_sequence_num - 1,
        )
    if current_sequence_num == previous_sequence_num:
        return CoinbaseSequenceStatus.REPEATED, None
    return CoinbaseSequenceStatus.OUT_OF_ORDER, None


@dataclass(frozen=True, slots=True)
class CoinbaseSequenceAssessment:
    """A validated assessment of two Coinbase message sequence numbers."""

    previous_sequence_num: int | None
    current_sequence_num: int
    status: CoinbaseSequenceStatus
    gap_count: int | None

    def __post_init__(self) -> None:
        if self.previous_sequence_num is not None:
            _validate_sequence_num(
                self.previous_sequence_num,
                "previous_sequence_num",
            )
        _validate_sequence_num(self.current_sequence_num, "current_sequence_num")
        if type(self.status) is not CoinbaseSequenceStatus:
            raise TypeError("status must be a CoinbaseSequenceStatus")

        expected_status, expected_gap_count = _expected_assessment(
            self.previous_sequence_num,
            self.current_sequence_num,
        )
        if self.status is not expected_status:
            raise ValueError("status contradicts the sequence numbers")

        if expected_gap_count is None:
            if self.gap_count is not None:
                raise ValueError("gap_count must be None when status is not GAP")
        else:
            if type(self.gap_count) is not int:
                raise TypeError("gap_count must be an exact int for GAP status")
            if self.gap_count != expected_gap_count:
                raise ValueError("gap_count contradicts the sequence numbers")


def assess_market_sequence(
    previous_sequence_num: int | None,
    current_sequence_num: int,
) -> CoinbaseSequenceAssessment:
    """Assess Coinbase message sequence continuity without retaining state."""

    if previous_sequence_num is not None:
        _validate_sequence_num(previous_sequence_num, "previous_sequence_num")
    _validate_sequence_num(current_sequence_num, "current_sequence_num")
    status, gap_count = _expected_assessment(
        previous_sequence_num,
        current_sequence_num,
    )
    return CoinbaseSequenceAssessment(
        previous_sequence_num=previous_sequence_num,
        current_sequence_num=current_sequence_num,
        status=status,
        gap_count=gap_count,
    )
