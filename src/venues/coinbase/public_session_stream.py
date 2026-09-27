"""Async composition of one Coinbase public wire session and processor."""

from collections.abc import AsyncIterator, Callable, Iterable

from src.venues.coinbase.public_market_source import (
    CoinbasePublicWireMessage,
    stream_public_market_messages,
)
from src.venues.coinbase.public_session_processor import (
    CoinbasePublicSessionObservation,
    CoinbasePublicSessionProcessor,
)


async def stream_public_session_observations(
    product_ids: Iterable[str],
    *,
    wire_source_factory: Callable[
        [Iterable[str]], AsyncIterator[CoinbasePublicWireMessage]
    ] = stream_public_market_messages,
) -> AsyncIterator[CoinbasePublicSessionObservation]:
    """Yield processed observations from one Coinbase public wire session."""

    processor = CoinbasePublicSessionProcessor()
    wire_source = wire_source_factory(product_ids)
    close = getattr(wire_source, "aclose", None)
    try:
        async for wire_message in wire_source:
            yield processor.process(wire_message)
    except BaseException as primary_error:
        if close is not None:
            try:
                await close()
            except BaseException as close_error:
                if isinstance(primary_error, GeneratorExit):
                    raise close_error
                raise primary_error from close_error
        raise
    else:
        if close is not None:
            await close()
