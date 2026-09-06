from __future__ import annotations


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