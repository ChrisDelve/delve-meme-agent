from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

from src.features.live_signal_state import build_live_signal_state


DB_PATH = Path("logs/delve_meme.db")

CANDIDATE_VERSION = "candidate-features-v1"
DERIVED_VERSION = "derived-features-v1"

RATE_TOLERANCE = 1e-12


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def same_optional_number(left, right) -> bool:
    if left is None and right is None:
        return True

    if left is None or right is None:
        return False

    return float(left) == float(right)


def load_replay_rows(
    connection: sqlite3.Connection,
    limit: int | None,
):
    sql = """
        SELECT
            d.entry_signature,
            c.mint,
            c.quote_mint,
            c.entry_timestamp,
            c.mayhem_mode AS source_mayhem_mode,
            d.mayhem_mode AS expected_mayhem_mode,
            d.buy_rate_15s AS expected_buy_rate_15s

        FROM derived_features d

        JOIN candidate_features c
            ON c.entry_signature = d.entry_signature

        WHERE
            d.derived_version = ?
            AND c.feature_version = ?

        ORDER BY
            c.entry_timestamp ASC,
            d.entry_signature ASC
    """

    parameters = [
        DERIVED_VERSION,
        CANDIDATE_VERSION,
    ]

    if limit is not None:
        sql += "\nLIMIT ?"
        parameters.append(limit)

    return connection.execute(
        sql,
        parameters,
    ).fetchall()


def run_audit(limit: int | None = None) -> None:
    started = time.time()

    with get_connection() as connection:
        rows = load_replay_rows(
            connection,
            limit,
        )

        total = len(rows)

        rate_mismatches = 0
        mayhem_mismatches = 0
        unexpected_ineligible = 0

        maximum_rate_delta = 0.0

        worst_rate_signature = None
        first_mayhem_mismatch = None
        first_ineligible = None

        print()
        print("=" * 76)
        print("DELVE MEME AGENT — LIVE STATE REPLAY PARITY")
        print("=" * 76)
        print(f"Candidate version:       {CANDIDATE_VERSION}")
        print(f"Derived version:         {DERIVED_VERSION}")
        print(f"Rows scheduled:          {total}")
        print(f"Rate tolerance:          {RATE_TOLERANCE:.1e}")
        print("VALIDATION HOLDOUT READ: NO")
        print("MODEL FITTING:           NO")
        print("LIVE CAPITAL AUTHORIZATION: NO")
        print("=" * 76)

        for index, row in enumerate(rows, start=1):
            state = build_live_signal_state(
                connection,
                mint=row["mint"],
                quote_mint=row["quote_mint"],
                trade_timestamp=row["entry_timestamp"],
                mayhem_mode=row["source_mayhem_mode"],
            )

            if not state.eligible:
                unexpected_ineligible += 1

                if first_ineligible is None:
                    first_ineligible = {
                        "signature": row["entry_signature"],
                        "reason": state.ineligible_reason,
                    }

                continue

            expected_rate = float(
                row["expected_buy_rate_15s"]
            )

            rate_delta = abs(
                state.buy_rate_15s
                - expected_rate
            )

            if rate_delta > maximum_rate_delta:
                maximum_rate_delta = rate_delta
                worst_rate_signature = row["entry_signature"]

            if rate_delta > RATE_TOLERANCE:
                rate_mismatches += 1

            if not same_optional_number(
                state.mayhem_mode,
                row["expected_mayhem_mode"],
            ):
                mayhem_mismatches += 1

                if first_mayhem_mismatch is None:
                    first_mayhem_mismatch = {
                        "signature": row["entry_signature"],
                        "live": state.mayhem_mode,
                        "expected": row["expected_mayhem_mode"],
                    }

            if (
                index % 25000 == 0
                or index == total
            ):
                print(
                    f"{index:,}/{total:,} "
                    f"rate_mismatch={rate_mismatches:,} "
                    f"mayhem_mismatch={mayhem_mismatches:,} "
                    f"ineligible={unexpected_ineligible:,}"
                )

    passed = (
        rate_mismatches == 0
        and mayhem_mismatches == 0
        and unexpected_ineligible == 0
    )

    elapsed = time.time() - started

    print()
    print("=" * 76)
    print("REPLAY AUDIT RESULT")
    print("=" * 76)

    print(f"Rows tested:             {total:,}")
    print(f"buy_rate_15s mismatches: {rate_mismatches:,}")
    print(f"mayhem_mode mismatches:  {mayhem_mismatches:,}")
    print(f"Unexpected ineligible:   {unexpected_ineligible:,}")
    print(
        "Maximum rate delta:     "
        f"{maximum_rate_delta:.16g}"
    )
    print(
        "Worst rate signature:   "
        f"{worst_rate_signature}"
    )
    print(
        "First mayhem mismatch:  "
        f"{first_mayhem_mismatch}"
    )
    print(
        "First ineligible:        "
        f"{first_ineligible}"
    )

    print()
    print(
        "FINAL RESULT: "
        + ("PASS" if passed else "FAIL")
    )

    print("VALIDATION HOLDOUT READ: NO")
    print("MODEL FITTING: NO")
    print("LIVE CAPITAL AUTHORIZATION: NO")
    print(f"Runtime: {elapsed:.1f}s")
    print("=" * 76)

    if not passed:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Optional deterministic row limit. "
            "Default audits the entire joined dataset."
        ),
    )

    args = parser.parse_args()

    run_audit(
        limit=args.limit,
    )


if __name__ == "__main__":
    main()