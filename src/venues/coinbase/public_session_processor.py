"""Single-session Coinbase public frame routing and sequence processing."""

from dataclasses import dataclass

from src.venues.coinbase.public_market_source import CoinbasePublicWireMessage
from src.venues.coinbase.public_message_router import (
    CoinbaseHeartbeatWireFrame,
    CoinbaseIgnoredPublicFrame,
    CoinbaseParsedMarketTradesFrame,
    route_public_wire_message,
)
from src.venues.coinbase.public_sequence_tracker import (
    CoinbaseConnectionSequenceIntegrity,
    CoinbaseConnectionSequenceObservation,
    CoinbaseConnectionSequenceTracker,
)


CoinbasePublicFrame = (
    CoinbaseParsedMarketTradesFrame
    | CoinbaseHeartbeatWireFrame
    | CoinbaseIgnoredPublicFrame
)


@dataclass(frozen=True, slots=True)
class CoinbasePublicSessionObservation:
    """One routed frame and its connection-sequence observation, if supported."""

    frame: CoinbasePublicFrame
    sequence_observation: CoinbaseConnectionSequenceObservation | None

    def __post_init__(self) -> None:
        supported_frame_types = (
            CoinbaseParsedMarketTradesFrame,
            CoinbaseHeartbeatWireFrame,
            CoinbaseIgnoredPublicFrame,
        )
        if not isinstance(self.frame, supported_frame_types):
            raise TypeError("frame must be a supported Coinbase public frame")

        if isinstance(self.frame, CoinbaseIgnoredPublicFrame):
            if self.sequence_observation is not None:
                raise ValueError(
                    "ignored frames must not have a sequence observation"
                )
            return

        if not isinstance(
            self.sequence_observation,
            CoinbaseConnectionSequenceObservation,
        ):
            raise TypeError(
                "recognized sequenced frames require a sequence observation"
            )
        if (
            self.sequence_observation.assessment.current_sequence_num
            != self.frame.sequence_num
        ):
            raise ValueError(
                "sequence observation must match the recognized frame sequence"
            )


class CoinbasePublicSessionProcessor:
    """Process routed public frames for one Coinbase WebSocket session."""

    __slots__ = ("_sequence_tracker",)

    def __init__(self) -> None:
        self._sequence_tracker = CoinbaseConnectionSequenceTracker()

    @property
    def previous_sequence_num(self) -> int | None:
        return self._sequence_tracker.previous_sequence_num

    @property
    def integrity(self) -> CoinbaseConnectionSequenceIntegrity:
        return self._sequence_tracker.integrity

    def process(
        self,
        wire_message: CoinbasePublicWireMessage,
    ) -> CoinbasePublicSessionObservation:
        """Route one wire frame, then update sequence state when supported."""

        frame = route_public_wire_message(wire_message)
        if isinstance(
            frame,
            (CoinbaseParsedMarketTradesFrame, CoinbaseHeartbeatWireFrame),
        ):
            sequence_observation = self._sequence_tracker.observe(
                frame.sequence_num
            )
        else:
            sequence_observation = None
        return CoinbasePublicSessionObservation(
            frame=frame,
            sequence_observation=sequence_observation,
        )
