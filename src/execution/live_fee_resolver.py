from __future__ import annotations

from src.execution.pump_execution_simulator import (
    ceil_fee,
)

BPS_DENOMINATOR = 10_000
MAX_INFERRED_FEE_BPS = 10_000


def infer_fee_bps_range(
    *,
    quote_amount: int,
    fee_lamports: int,
) -> tuple[int, int] | None:
    """
    Solve for all integer fee-bps values consistent with:

        fee = ceil(
            quote_amount * fee_bps / 10_000
        )

    Returns the inclusive bps range.
    """

    if quote_amount <= 0:
        return None

    if fee_lamports < 0:
        return None

    if fee_lamports == 0:
        return (
            0,
            0,
        )

    lower = (
        (
            (fee_lamports - 1)
            * BPS_DENOMINATOR
        )
        // quote_amount
    ) + 1

    upper = (
        fee_lamports
        * BPS_DENOMINATOR
    ) // quote_amount

    lower = max(
        0,
        lower,
    )

    upper = min(
        MAX_INFERRED_FEE_BPS,
        upper,
    )

    if lower > upper:
        return None

    return (
        lower,
        upper,
    )


def resolve_observed_fee_bps(
    *,
    quote_amount: int,
    protocol_fee_lamports: int | None,
    creator_fee_lamports: int | None,
) -> tuple[
    str,
    int | None,
    int | None,
]:
    """
    Resolve the fee regime from the exact
    triggering on-chain trade.

    Live shadow execution proceeds only when
    both protocol and creator rates resolve
    uniquely.
    """

    if (
        protocol_fee_lamports is None
        or creator_fee_lamports is None
    ):
        return (
            "MISSING_FEE_DATA",
            None,
            None,
        )

    protocol_range = infer_fee_bps_range(
        quote_amount=int(
            quote_amount
        ),
        fee_lamports=int(
            protocol_fee_lamports
        ),
    )

    creator_range = infer_fee_bps_range(
        quote_amount=int(
            quote_amount
        ),
        fee_lamports=int(
            creator_fee_lamports
        ),
    )

    def resolve_live_event_fee_bps(
        *,
        quote_amount: int,
        protocol_fee_lamports: int | None,
        creator_fee_lamports: int | None,
        event_protocol_fee_bps: int | None,
        event_creator_fee_bps: int | None,
    ) -> tuple[
        str,
        int | None,
        int | None,
    ]:
        """
        Resolve live Pump fee rates using the fee-bps
        values emitted by the trade event.

        Event-provided bps are primary.

        Observed fee amounts and independently inferred
        bps are used as consistency checks.

        Fail closed on any contradiction.
        """

        if quote_amount <= 0:
            return (
                "INVALID_QUOTE_AMOUNT",
                None,
                None,
            )

        #
        # If the live event somehow lacks explicit bps,
        # retain the old inference path as a fallback.
        #
        if (
            event_protocol_fee_bps is None
            or event_creator_fee_bps is None
        ):
            return resolve_observed_fee_bps(
                quote_amount=quote_amount,
                protocol_fee_lamports=(
                    protocol_fee_lamports
                ),
                creator_fee_lamports=(
                    creator_fee_lamports
                ),
            )

        protocol_bps = int(
            event_protocol_fee_bps
        )

        creator_bps = int(
            event_creator_fee_bps
        )

        for name, value in (
            (
                "event_protocol_fee_bps",
                protocol_bps,
            ),
            (
                "event_creator_fee_bps",
                creator_bps,
            ),
        ):
            if (
                value < 0
                or value > BPS_DENOMINATOR
            ):
                return (
                    f"INVALID_{name.upper()}",
                    None,
                    None,
                )

        if (
            protocol_fee_lamports is None
            or creator_fee_lamports is None
        ):
            return (
                "MISSING_FEE_AMOUNT_DATA",
                None,
                None,
            )

        observed_protocol_fee = int(
            protocol_fee_lamports
        )

        observed_creator_fee = int(
            creator_fee_lamports
        )

        expected_protocol_fee = ceil_fee(
            quote_amount,
            protocol_bps,
        )

        expected_creator_fee = ceil_fee(
            quote_amount,
            creator_bps,
        )

        if (
            expected_protocol_fee
            != observed_protocol_fee
        ):
            return (
                "EVENT_PROTOCOL_FEE_MISMATCH",
                None,
                None,
            )

        if (
            expected_creator_fee
            != observed_creator_fee
        ):
            return (
                "EVENT_CREATOR_FEE_MISMATCH",
                None,
                None,
            )

        #
        # Independent reverse inference is a second
        # check whenever the amount uniquely identifies
        # an integer rate.
        #
        (
            inferred_status,
            inferred_protocol_bps,
            inferred_creator_bps,
        ) = resolve_observed_fee_bps(
            quote_amount=quote_amount,
            protocol_fee_lamports=(
                observed_protocol_fee
            ),
            creator_fee_lamports=(
                observed_creator_fee
            ),
        )

        if inferred_status == "RESOLVED":
            if (
                inferred_protocol_bps
                != protocol_bps
                or inferred_creator_bps
                != creator_bps
            ):
                return (
                    "EVENT_INFERENCE_BPS_MISMATCH",
                    None,
                    None,
                )

        return (
            "RESOLVED_EVENT_VERIFIED",
            protocol_bps,
            creator_bps,
        )

    if protocol_range is None:
        return (
            "PROTOCOL_FEE_NO_EXACT_BPS",
            None,
            None,
        )

    if creator_range is None:
        return (
            "CREATOR_FEE_NO_EXACT_BPS",
            None,
            None,
        )

    if (
        protocol_range[0]
        != protocol_range[1]
    ):
        return (
            "AMBIGUOUS_PROTOCOL_FEE_BPS",
            None,
            None,
        )

    if (
        creator_range[0]
        != creator_range[1]
    ):
        return (
            "AMBIGUOUS_CREATOR_FEE_BPS",
            None,
            None,
        )

    return (
        "RESOLVED",
        protocol_range[0],
        creator_range[0],
    )