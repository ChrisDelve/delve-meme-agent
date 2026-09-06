from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


DB_PATH = Path("logs/delve_meme.db")

SOL_QUOTE_MINT = "11111111111111111111111111111111"

AUDIT_VERSION = "reserve-state-audit-v1"


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(
        DB_PATH,
        timeout=30.0,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA busy_timeout = 30000"
    )

    return connection


def build_audit_cte(
    limit: int | None,
) -> tuple[str, list]:
    limit_sql = ""

    parameters: list = [
        SOL_QUOTE_MINT,
    ]

    if limit is not None:
        limit_sql = "LIMIT ?"
        parameters.append(limit)

    sql = f"""
        WITH source AS (
            SELECT
                rowid AS trade_rowid,

                signature,
                mint,
                side,

                slot,
                trade_timestamp,
                observed_at,

                quote_amount,
                token_amount,

                virtual_sol_reserves,
                virtual_token_reserves,

                real_sol_reserves,
                real_token_reserves

            FROM trades

            WHERE
                quote_mint = ?

                AND side IN (
                    'BUY',
                    'SELL'
                )

                AND quote_amount > 0

                AND token_amount > 0

                AND observed_at IS NOT NULL

                AND virtual_sol_reserves
                    IS NOT NULL

                AND virtual_token_reserves
                    IS NOT NULL

                AND real_sol_reserves
                    IS NOT NULL

                AND real_token_reserves
                    IS NOT NULL

            ORDER BY
                trade_rowid

            {limit_sql}
        ),

        ordered AS (
            SELECT
                *,

                LAG(signature) OVER (
                    PARTITION BY mint
                    ORDER BY trade_rowid
                ) AS previous_signature,

                LAG(side) OVER (
                    PARTITION BY mint
                    ORDER BY trade_rowid
                ) AS previous_side,

                LAG(slot) OVER (
                    PARTITION BY mint
                    ORDER BY trade_rowid
                ) AS previous_slot,

                LAG(observed_at) OVER (
                    PARTITION BY mint
                    ORDER BY trade_rowid
                ) AS previous_observed_at,

                LAG(quote_amount) OVER (
                    PARTITION BY mint
                    ORDER BY trade_rowid
                ) AS previous_quote_amount,

                LAG(token_amount) OVER (
                    PARTITION BY mint
                    ORDER BY trade_rowid
                ) AS previous_token_amount,

                LAG(
                    virtual_sol_reserves
                ) OVER (
                    PARTITION BY mint
                    ORDER BY trade_rowid
                ) AS previous_virtual_sol,

                LAG(
                    virtual_token_reserves
                ) OVER (
                    PARTITION BY mint
                    ORDER BY trade_rowid
                ) AS previous_virtual_token,

                LAG(
                    real_sol_reserves
                ) OVER (
                    PARTITION BY mint
                    ORDER BY trade_rowid
                ) AS previous_real_sol,

                LAG(
                    real_token_reserves
                ) OVER (
                    PARTITION BY mint
                    ORDER BY trade_rowid
                ) AS previous_real_token

            FROM source
        ),

        reconstructed AS (
            SELECT
                *,

                CASE
                    WHEN side = 'BUY'
                    THEN
                        virtual_sol_reserves
                        - quote_amount

                    WHEN side = 'SELL'
                    THEN
                        virtual_sol_reserves
                        + quote_amount
                END
                AS post_hypothesis_pre_virtual_sol,

                CASE
                    WHEN side = 'BUY'
                    THEN
                        virtual_token_reserves
                        + token_amount

                    WHEN side = 'SELL'
                    THEN
                        virtual_token_reserves
                        - token_amount
                END
                AS post_hypothesis_pre_virtual_token,

                CASE
                    WHEN side = 'BUY'
                    THEN
                        real_sol_reserves
                        - quote_amount

                    WHEN side = 'SELL'
                    THEN
                        real_sol_reserves
                        + quote_amount
                END
                AS post_hypothesis_pre_real_sol,

                CASE
                    WHEN side = 'BUY'
                    THEN
                        real_token_reserves
                        + token_amount

                    WHEN side = 'SELL'
                    THEN
                        real_token_reserves
                        - token_amount
                END
                AS post_hypothesis_pre_real_token,

                CASE
                    WHEN previous_side = 'BUY'
                    THEN
                        previous_virtual_sol
                        + previous_quote_amount

                    WHEN previous_side = 'SELL'
                    THEN
                        previous_virtual_sol
                        - previous_quote_amount
                END
                AS pre_hypothesis_previous_post_virtual_sol,

                CASE
                    WHEN previous_side = 'BUY'
                    THEN
                        previous_virtual_token
                        - previous_token_amount

                    WHEN previous_side = 'SELL'
                    THEN
                        previous_virtual_token
                        + previous_token_amount
                END
                AS pre_hypothesis_previous_post_virtual_token,

                CASE
                    WHEN previous_side = 'BUY'
                    THEN
                        previous_real_sol
                        + previous_quote_amount

                    WHEN previous_side = 'SELL'
                    THEN
                        previous_real_sol
                        - previous_quote_amount
                END
                AS pre_hypothesis_previous_post_real_sol,

                CASE
                    WHEN previous_side = 'BUY'
                    THEN
                        previous_real_token
                        - previous_token_amount

                    WHEN previous_side = 'SELL'
                    THEN
                        previous_real_token
                        + previous_token_amount
                END
                AS pre_hypothesis_previous_post_real_token

            FROM ordered

            WHERE
                previous_signature
                IS NOT NULL
        ),

        scored AS (
            SELECT
                *,

                CASE
                    WHEN
                        previous_virtual_sol
                            =
                        post_hypothesis_pre_virtual_sol

                        AND previous_virtual_token
                            =
                        post_hypothesis_pre_virtual_token

                    THEN 1
                    ELSE 0
                END
                AS post_virtual_match,

                CASE
                    WHEN
                        previous_real_sol
                            =
                        post_hypothesis_pre_real_sol

                        AND previous_real_token
                            =
                        post_hypothesis_pre_real_token

                    THEN 1
                    ELSE 0
                END
                AS post_real_match,

                CASE
                    WHEN
                        previous_virtual_sol
                            =
                        post_hypothesis_pre_virtual_sol

                        AND previous_virtual_token
                            =
                        post_hypothesis_pre_virtual_token

                        AND previous_real_sol
                            =
                        post_hypothesis_pre_real_sol

                        AND previous_real_token
                            =
                        post_hypothesis_pre_real_token

                    THEN 1
                    ELSE 0
                END
                AS post_full_match,

                CASE
                    WHEN
                        virtual_sol_reserves
                            =
                        pre_hypothesis_previous_post_virtual_sol

                        AND virtual_token_reserves
                            =
                        pre_hypothesis_previous_post_virtual_token

                    THEN 1
                    ELSE 0
                END
                AS pre_virtual_match,

                CASE
                    WHEN
                        real_sol_reserves
                            =
                        pre_hypothesis_previous_post_real_sol

                        AND real_token_reserves
                            =
                        pre_hypothesis_previous_post_real_token

                    THEN 1
                    ELSE 0
                END
                AS pre_real_match,

                CASE
                    WHEN
                        virtual_sol_reserves
                            =
                        pre_hypothesis_previous_post_virtual_sol

                        AND virtual_token_reserves
                            =
                        pre_hypothesis_previous_post_virtual_token

                        AND real_sol_reserves
                            =
                        pre_hypothesis_previous_post_real_sol

                        AND real_token_reserves
                            =
                        pre_hypothesis_previous_post_real_token

                    THEN 1
                    ELSE 0
                END
                AS pre_full_match,

                CASE
                    WHEN
                        observed_at
                        =
                        previous_observed_at

                    THEN 1
                    ELSE 0
                END
                AS same_second,

                CASE
                    WHEN
                        slot
                        =
                        previous_slot

                    THEN 1
                    ELSE 0
                END
                AS same_slot

            FROM reconstructed
        )
    """

    return (
        sql,
        parameters,
    )


def percentage(
    numerator: int,
    denominator: int,
) -> float:
    if denominator == 0:
        return 0.0

    return (
        100.0
        * numerator
        / denominator
    )


def print_overall(
    connection: sqlite3.Connection,
    cte: str,
    parameters: list,
) -> None:
    row = connection.execute(
        cte
        + """
        SELECT
            COUNT(*) AS transitions,

            COUNT(
                DISTINCT mint
            ) AS mints,

            SUM(
                CASE
                    WHEN side = 'BUY'
                    THEN 1
                    ELSE 0
                END
            ) AS buys,

            SUM(
                CASE
                    WHEN side = 'SELL'
                    THEN 1
                    ELSE 0
                END
            ) AS sells,

            SUM(
                post_virtual_match
            ) AS post_virtual_matches,

            SUM(
                post_real_match
            ) AS post_real_matches,

            SUM(
                post_full_match
            ) AS post_full_matches,

            SUM(
                pre_virtual_match
            ) AS pre_virtual_matches,

            SUM(
                pre_real_match
            ) AS pre_real_matches,

            SUM(
                pre_full_match
            ) AS pre_full_matches,

            SUM(
                same_second
            ) AS same_second_transitions,

            SUM(
                same_slot
            ) AS same_slot_transitions,

            SUM(
                CASE
                    WHEN
                        same_slot = 1
                        AND post_full_match = 1
                    THEN 1
                    ELSE 0
                END
            ) AS same_slot_post_matches

        FROM scored
        """,
        parameters,
    ).fetchone()

    transitions = int(
        row["transitions"]
    )

    print()
    print("OVERALL RESERVE SEMANTICS")
    print("-" * 78)

    print(
        f"Eligible transitions:     "
        f"{transitions:,}"
    )

    print(
        f"Distinct mints:           "
        f"{row['mints']:,}"
    )

    print(
        f"BUY transitions:          "
        f"{row['buys']:,}"
    )

    print(
        f"SELL transitions:         "
        f"{row['sells']:,}"
    )

    print()

    print(
        "POST-TRADE RESERVE HYPOTHESIS"
    )

    print(
        f"Virtual exact:            "
        f"{row['post_virtual_matches']:,} "
        f"("
        f"{percentage(row['post_virtual_matches'], transitions):.3f}%"
        f")"
    )

    print(
        f"Real exact:               "
        f"{row['post_real_matches']:,} "
        f"("
        f"{percentage(row['post_real_matches'], transitions):.3f}%"
        f")"
    )

    print(
        f"Full exact:               "
        f"{row['post_full_matches']:,} "
        f"("
        f"{percentage(row['post_full_matches'], transitions):.3f}%"
        f")"
    )

    print()

    print(
        "PRE-TRADE RESERVE HYPOTHESIS"
    )

    print(
        f"Virtual exact:            "
        f"{row['pre_virtual_matches']:,} "
        f"("
        f"{percentage(row['pre_virtual_matches'], transitions):.3f}%"
        f")"
    )

    print(
        f"Real exact:               "
        f"{row['pre_real_matches']:,} "
        f"("
        f"{percentage(row['pre_real_matches'], transitions):.3f}%"
        f")"
    )

    print(
        f"Full exact:               "
        f"{row['pre_full_matches']:,} "
        f"("
        f"{percentage(row['pre_full_matches'], transitions):.3f}%"
        f")"
    )

    print()
    print("ORDERING CHARACTERISTICS")

    print(
        f"Same-second transitions:  "
        f"{row['same_second_transitions']:,} "
        f"("
        f"{percentage(row['same_second_transitions'], transitions):.2f}%"
        f")"
    )

    print(
        f"Same-slot transitions:    "
        f"{row['same_slot_transitions']:,} "
        f"("
        f"{percentage(row['same_slot_transitions'], transitions):.2f}%"
        f")"
    )

    same_slot_count = int(
        row["same_slot_transitions"]
    )

    print(
        f"Same-slot POST exact:     "
        f"{row['same_slot_post_matches']:,} "
        f"("
        f"{percentage(row['same_slot_post_matches'], same_slot_count):.3f}%"
        f")"
    )


def print_by_side(
    connection: sqlite3.Connection,
    cte: str,
    parameters: list,
) -> None:
    rows = connection.execute(
        cte
        + """
        SELECT
            side,

            COUNT(*) AS transitions,

            SUM(
                post_full_match
            ) AS post_full_matches,

            SUM(
                pre_full_match
            ) AS pre_full_matches,

            SUM(
                same_slot
            ) AS same_slot_transitions,

            SUM(
                CASE
                    WHEN
                        same_slot = 1
                        AND post_full_match = 1
                    THEN 1
                    ELSE 0
                END
            ) AS same_slot_post_matches

        FROM scored

        GROUP BY side

        ORDER BY side
        """,
        parameters,
    ).fetchall()

    print()
    print("RESULTS BY CURRENT TRADE SIDE")
    print("-" * 78)

    for row in rows:
        transitions = int(
            row["transitions"]
        )

        same_slot = int(
            row["same_slot_transitions"]
        )

        print(
            f"{row['side']:<4} | "
            f"n={transitions:>8,d} | "
            f"POST exact="
            f"{percentage(row['post_full_matches'], transitions):7.3f}% | "
            f"PRE exact="
            f"{percentage(row['pre_full_matches'], transitions):7.3f}% | "
            f"same-slot POST="
            f"{percentage(row['same_slot_post_matches'], same_slot):7.3f}%"
        )


def print_mismatch_examples(
    connection: sqlite3.Connection,
    cte: str,
    parameters: list,
    examples: int,
) -> None:
    rows = connection.execute(
        cte
        + """
        SELECT
            mint,
            side,

            previous_signature,
            signature,

            previous_slot,
            slot,

            previous_observed_at,
            observed_at,

            previous_virtual_sol,
            post_hypothesis_pre_virtual_sol,

            previous_virtual_token,
            post_hypothesis_pre_virtual_token,

            previous_real_sol,
            post_hypothesis_pre_real_sol,

            previous_real_token,
            post_hypothesis_pre_real_token

        FROM scored

        WHERE
            post_full_match = 0

        ORDER BY
            trade_rowid

        LIMIT ?
        """,
        parameters
        + [examples],
    ).fetchall()

    print()
    print("FIRST POST-HYPOTHESIS MISMATCHES")
    print("-" * 78)

    if not rows:
        print(
            "No mismatches found."
        )
        return

    for index, row in enumerate(
        rows,
        start=1,
    ):
        print()
        print(
            f"[{index}] "
            f"{row['side']} "
            f"{row['mint']}"
        )

        print(
            f"Previous signature: "
            f"{row['previous_signature']}"
        )

        print(
            f"Current signature:  "
            f"{row['signature']}"
        )

        print(
            f"Slots:              "
            f"{row['previous_slot']} -> "
            f"{row['slot']}"
        )

        print(
            f"Observed:           "
            f"{row['previous_observed_at']} -> "
            f"{row['observed_at']}"
        )

        print(
            "Virtual SOL:        "
            f"previous={row['previous_virtual_sol']} | "
            f"reconstructed_pre="
            f"{row['post_hypothesis_pre_virtual_sol']}"
        )

        print(
            "Virtual token:      "
            f"previous={row['previous_virtual_token']} | "
            f"reconstructed_pre="
            f"{row['post_hypothesis_pre_virtual_token']}"
        )

        print(
            "Real SOL:           "
            f"previous={row['previous_real_sol']} | "
            f"reconstructed_pre="
            f"{row['post_hypothesis_pre_real_sol']}"
        )

        print(
            "Real token:         "
            f"previous={row['previous_real_token']} | "
            f"reconstructed_pre="
            f"{row['post_hypothesis_pre_real_token']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Optional number of SOL trades to audit. "
            "Default audits the full database."
        ),
    )

    parser.add_argument(
        "--examples",
        type=int,
        default=5,
        help=(
            "Number of reserve-chain mismatch "
            "examples to print."
        ),
    )

    args = parser.parse_args()

    cte, parameters = build_audit_cte(
        args.limit
    )

    print()
    print("=" * 78)
    print(
        "DELVE MEME AGENT — "
        "PUMP RESERVE STATE AUDIT"
    )
    print("=" * 78)

    print(
        f"Audit version:          "
        f"{AUDIT_VERSION}"
    )

    print(
        f"Quote universe:         "
        f"native SOL"
    )

    print(
        f"Trade limit:            "
        f"{args.limit if args.limit is not None else 'FULL DATABASE'}"
    )

    print()
    print(
        "This audit compares two competing interpretations:"
    )

    print(
        "  POST hypothesis: recorded reserves are the state AFTER each trade."
    )

    print(
        "  PRE hypothesis:  recorded reserves are the state BEFORE each trade."
    )

    with get_connection() as connection:
        print_overall(
            connection,
            cte,
            parameters,
        )

        print_by_side(
            connection,
            cte,
            parameters,
        )

        print_mismatch_examples(
            connection,
            cte,
            parameters,
            args.examples,
        )

    print()
    print("=" * 78)
    print(
        "DATABASE MUTATION: NO"
    )

    print(
        "MODEL / SIGNAL CHANGES: NO"
    )

    print(
        "LIVE CAPITAL AUTHORIZATION: NO"
    )

    print("=" * 78)


if __name__ == "__main__":
    main()