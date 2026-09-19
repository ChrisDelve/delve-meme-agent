import asyncio
import json
import os
import ssl
import time
from collections.abc import Mapping

import certifi
import websockets
from dotenv import load_dotenv

from src.strategies.shadow_signals import (
    record_shadow_signal,
)

from src.strategies.shadow_position_manager import (
    initialize_shadow_position_manager,
    is_open_shadow_mint_tracked,
    process_shadow_position_event,
)
from src.strategies.shadow_position_sweeper import (
    run_shadow_position_sweeper,
)

from src.data.coverage import (
    close_coverage_interval,
    invalidate_stale_intervals,
    start_coverage_interval,
)

from src.data.market_db import (
    get_counts,
    init_db,
    save_buy,
    save_launch,
)
from src.data.trade_event import (
    extract_trade_event,
    extract_trade_event_from_logs,
)

from src.data.trade_recorder import save_trade_event
from src.data.transaction_decoder import (
    decode_create_v2_instruction,
    fetch_transaction,
)

from src.strategies.pretrade_shadow import (
    schedule_pretrade_shadow_candidate,
)

from src.portfolio.shadow_portfolio import (
    initialize_shadow_account,
)

from src.strategies.model_shadow_signals import (
    record_model_shadow_prediction,
)
from src.execution.live_entry_candidate_scheduler import (
    LiveEntryCandidateScheduler,
)
from src.execution.live_entry_result_mailbox import (
    LiveEntryResultMailbox,
)

load_dotenv()

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

if not HELIUS_API_KEY:
    raise RuntimeError("HELIUS_API_KEY is missing from .env")

WS_URL = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

SSL_CONTEXT = ssl.create_default_context(
    cafile=certifi.where()
)

# Prevent us from creating an unlimited number of
# simultaneous RPC requests during bursts.
PROCESSING_LIMIT = asyncio.Semaphore(10)


def find_create_v2(transaction):
    message = (
        transaction
        .get("transaction", {})
        .get("message", {})
    )

    for instruction in message.get("instructions", []):
        decoded = decode_create_v2_instruction(
            instruction
        )

        if decoded:
            return decoded

    return None


async def process_launch(
    signature,
    slot,
):


    async with PROCESSING_LIMIT:
        transaction = await fetch_transaction(
            signature
        )

        if transaction is None:
            return

        decoded = find_create_v2(
            transaction
        )

        if decoded is None:
            return

        block_time = transaction.get(
            "blockTime"
        )

        save_launch(
            mint=decoded["mint"],
            name=decoded["name"],
            symbol=decoded["symbol"],
            creator=decoded["creator"],
            mayhem_mode=decoded["mayhem_mode"],
            launch_slot=transaction.get(
                "slot",
                slot,
            ),
            launch_timestamp=block_time,
            signature=signature,
            metadata_uri=decoded["uri"],
            first_seen_at=int(time.time()),
        )

        print(
            "🚀 LAUNCH | "
            f"${decoded['symbol']} | "
            f"{decoded['mint']} | "
            f"mayhem={decoded['mayhem_mode']}"
        )

async def process_launch_safe(
    signature,
    slot,
):
    """
    Isolate launch enrichment from the trade collector while ensuring
    background failures are visible.

    Launch processing is non-critical to raw trade ingestion, so a launch
    failure must not terminate the market collector. However, failures must
    never disappear silently because they reduce launch-feature coverage.
    """
    try:
        await process_launch(
            signature,
            slot,
        )

    except asyncio.CancelledError:
        raise

    except Exception as error:
        print()
        print("⚠️ BACKGROUND LAUNCH PROCESSOR FAILED")
        print(f"Signature: {signature}")
        print(f"Slot:      {slot}")
        print(f"Error:     {error!r}")
        print("=" * 70)

async def process_shadow_position_event_safe(
    trade_event,
):
    try:
        result = (
            await process_shadow_position_event(
                mint=(
                    trade_event["mint"]
                ),

                event_user=(
                    trade_event.get(
                        "user"
                    )
                ),

                quote_amount=int(
                    trade_event[
                        "quote_amount"
                    ]
                ),

                protocol_fee_lamports=(
                    trade_event.get(
                        "fee"
                    )
                ),

                creator_fee_lamports=(
                    trade_event.get(
                        "creator_fee"
                    )
                ),

                event_protocol_fee_bps=(
                    trade_event.get(
                        "fee_basis_points"
                    )
                ),

                event_creator_fee_bps=(
                    trade_event.get(
                        "creator_fee_basis_points"
                    )
                ),

                virtual_quote_reserves=int(
                    trade_event[
                        "virtual_sol_reserves"
                    ]
                ),

                virtual_token_reserves=int(
                    trade_event[
                        "virtual_token_reserves"
                    ]
                ),

                real_quote_reserves=int(
                    trade_event[
                        "real_sol_reserves"
                    ]
                ),

                real_token_reserves=int(
                    trade_event[
                        "real_token_reserves"
                    ]
                ),

                observed_at=int(
                    time.time()
                ),
            )
        )

        if result.status == "CLOSED":
            print(
                "🔒 SHADOW POSITION CLOSED | "
                f"mint={result.mint} | "
                f"reason={result.reason}"
            )

        elif result.status == "UNKNOWN":
            print(
                "⚠️ SHADOW POSITION UNKNOWN | "
                f"mint={result.mint} | "
                f"reason={result.reason}"
            )

    except Exception as error:
        print(
            "⚠️ SHADOW POSITION ERROR | "
            f"{type(error).__name__}: "
            f"{error}"
        )

async def process_buy(
    signature,
    slot,
):
    async with PROCESSING_LIMIT:
        transaction = await fetch_transaction(
            signature
        )

        if transaction is None:
            return

        trade_event = extract_trade_event(
            transaction
        )

        if not trade_event:
            return

        if not trade_event["is_buy"]:
            return
        save_trade_event(
            signature,
            slot,
            trade_event,
        )

        observed_at = int(time.time())

        result = save_buy(
            signature=signature,
            mint=trade_event["mint"],
            buyer=trade_event["user"],
            quote_mint=trade_event["quote_mint"],
            slot=transaction.get(
                "slot",
                slot,
            ),
            trade_timestamp=trade_event["timestamp"],
            sol_amount_lamports=trade_event["sol_amount"],
            quote_amount=trade_event["quote_amount"],
            token_amount=trade_event["token_amount"],
            protocol_fee_lamports=trade_event["fee"],
            creator_fee_lamports=trade_event["creator_fee"],
            ix_name=trade_event["ix_name"],
            mayhem_mode=trade_event["mayhem_mode"],
            observed_at=observed_at,
        )

        # Duplicate transaction already saved.
        if result is None:
            return

        actual_sol = (
            trade_event["sol_amount"]
            / 1_000_000_000
        )

        rank = result[
            "observed_rank"
        ]

        age = result[
            "entry_age_seconds"
        ]

        print()
        print("🦍 BUY SAVED")
        print("=" * 70)
        print(f"Wallet:     {trade_event['user']}")
        print(f"Mint:       {trade_event['mint']}")
        print(f"SOL:        {actual_sol:.6f}")
        print(f"Rank:       #{rank}")

        if age is None:
            print("Entry Age:  waiting for launch record")
        else:
            print(f"Entry Age:  {age} sec")

        print(f"Mayhem:     {trade_event['mayhem_mode']}")

        counts = get_counts()

        print()
        print(
            f"DB → {counts['launches']} launches | "
            f"{counts['buys']} buys | "
            f"{counts['wallets']} wallets"
        )
        print("=" * 70)

def _prediction_is_exactly_eligible(
    prediction,
):
    if not isinstance(
        prediction,
        Mapping,
    ):
        return False

    value = prediction.get(
        "model_eligible"
    )

    return (
        value is True
        or (
            type(value) is int
            and value == 1
        )
    )


def _print_live_entry_candidate_result(
    result,
    *,
    mint,
):
    reasons = (
        ",".join(
            result.reasons
        )
        if result.reasons
        else "-"
    )

    print(
        "🔎 LIVE ENTRY CANDIDATE | "
        f"{mint[:8]}… | "
        f"status={result.status} | "
        f"stage={result.stage} | "
        f"reasons={reasons}"
    )


def _observe_live_entry_candidate_task(
    task,
    *,
    mint,
    result_mailbox=None,
):
    if task.cancelled():
        return

    try:
        result = task.result()

    except Exception:
        #
        # The scheduler already owns unexpected task-exception
        # visibility. Do not print the same exception twice here.
        #
        return

    if result_mailbox is not None:
        result_mailbox.publish(
            result
        )

    _print_live_entry_candidate_result(
        result,
        mint=mint,
    )


def process_buy_event(
    signature,
    slot,
    trade_event,
    *,
    live_entry_scheduler=None,
    live_entry_result_mailbox=None,
):
    observed_at = int(time.time())
    result = save_buy(
        signature=signature,
        mint=trade_event["mint"],
        buyer=trade_event["user"],
        quote_mint=trade_event["quote_mint"],
        slot=slot,
        trade_timestamp=trade_event["timestamp"],
        sol_amount_lamports=trade_event["sol_amount"],
        quote_amount=trade_event["quote_amount"],
        token_amount=trade_event["token_amount"],
        protocol_fee_lamports=trade_event["fee"],
        creator_fee_lamports=trade_event["creator_fee"],
        ix_name=trade_event["ix_name"],
        mayhem_mode=trade_event["mayhem_mode"],
        observed_at=observed_at,
    )

    if not result:
        return

    rank = result["observed_rank"]
    entry_age = result["entry_age_seconds"]

    prediction = None

    try:
        prediction = (
            record_model_shadow_prediction(
                signature=signature,
                slot=slot,
                wallet=trade_event["user"],
                mint=trade_event["mint"],
                quote_mint=trade_event["quote_mint"],
                trade_timestamp=(
                    trade_event["timestamp"]
                ),
                mayhem_mode=(
                    trade_event["mayhem_mode"]
                ),
                observed_at=observed_at,
            )
        )

    except Exception as error:
        print(
            "⚠️ MODEL SHADOW ERROR | "
            f"{type(error).__name__}: {error}"
        )

    if prediction is not None:
        try:
            schedule_pretrade_shadow_candidate(
                entry_signature=(
                    signature
                ),

                mint=(
                    trade_event["mint"]
                ),

                event_user=(
                    trade_event["user"]
                ),

                quote_mint=(
                    trade_event["quote_mint"]
                ),

                slot=slot,

                trade_timestamp=(
                    trade_event["timestamp"]
                ),

                predicted_at=int(
                    prediction[
                        "predicted_at"
                    ]
                ),

                model_eligible=bool(
                    prediction[
                        "model_eligible"
                    ]
                ),

                probability_2x_15m=(
                    prediction[
                        "probability_2x_15m"
                    ]
                ),

                signal_virtual_quote_reserves=(
                    trade_event[
                        "virtual_sol_reserves"
                    ]
                ),

                signal_virtual_token_reserves=(
                    trade_event[
                        "virtual_token_reserves"
                    ]
                ),

                signal_real_quote_reserves=(
                    trade_event[
                        "real_sol_reserves"
                    ]
                ),

                signal_real_token_reserves=(
                    trade_event[
                        "real_token_reserves"
                    ]
                ),

                quote_amount=(
                    trade_event[
                        "quote_amount"
                    ]
                ),

                protocol_fee_lamports=(
                    trade_event[
                        "fee"
                    ]
                ),

                creator_fee_lamports=(
                    trade_event[
                        "creator_fee"
                    ]
                ),

                event_protocol_fee_bps=(
                    trade_event[
                        "fee_basis_points"
                    ]
                ),

                event_creator_fee_bps=(
                    trade_event[
                        "creator_fee_basis_points"
                    ]
                ),
                )

        except Exception as error:
            print(
                "⚠️ PRETRADE SCHEDULE ERROR | "
                f"{type(error).__name__}: "
                f"{error}"
            )

    if (
        live_entry_scheduler is not None
        and prediction is not None
        and _prediction_is_exactly_eligible(
            prediction
        )
    ):
        try:
            live_schedule = (
                live_entry_scheduler.schedule(
                    prediction=prediction,
                    entry_signature=signature,
                    mint=trade_event["mint"],
                    event_user=trade_event["user"],
                    quote_mint=(
                        trade_event["quote_mint"]
                    ),
                    slot=slot,
                    trade_timestamp=(
                        trade_event["timestamp"]
                    ),
                    observed_at=observed_at,
                    signal_virtual_quote_reserves=(
                        trade_event[
                            "virtual_sol_reserves"
                        ]
                    ),
                    signal_virtual_token_reserves=(
                        trade_event[
                            "virtual_token_reserves"
                        ]
                    ),
                )
            )

            if live_schedule.scheduled:
                live_schedule.task.add_done_callback(
                    lambda task, bound_mint=(
                        trade_event["mint"]
                    ), bound_result_mailbox=(
                        live_entry_result_mailbox
                    ): (
                        _observe_live_entry_candidate_task(
                            task,
                            mint=bound_mint,
                            result_mailbox=(
                                bound_result_mailbox
                            ),
                        )
                    )
                )

            elif live_schedule.resolved:
                _print_live_entry_candidate_result(
                    live_schedule.result,
                    mint=trade_event["mint"],
                )

            else:
                print(
                    "🔎 LIVE ENTRY CANDIDATE BLOCKED | "
                    f"{trade_event['mint'][:8]}… | "
                    f"reasons="
                    f"{','.join(live_schedule.reasons)}"
                )

        except Exception as error:
            print(
                "⚠️ LIVE ENTRY SCHEDULE ERROR | "
                f"{type(error).__name__}: "
                f"{error}"
            )

    shadow_signal = record_shadow_signal(
        signature=signature,
        slot=slot,
        wallet=trade_event["user"],
        mint=trade_event["mint"],
        quote_mint=trade_event["quote_mint"],
        trade_timestamp=trade_event["timestamp"],
        sol_amount_lamports=trade_event["sol_amount"],
        token_amount=trade_event["token_amount"],
        observed_rank=rank,
        entry_age_seconds=entry_age,
        mayhem_mode=trade_event["mayhem_mode"],
    )

    if shadow_signal is not None:
        print()
        print("👻 SHADOW SIGNAL")
        print(
            f"Wallet: {shadow_signal['wallet']}"
        )
        print(
            f"Mint:   {shadow_signal['mint']}"
        )
        print(
            "Frozen Alpha: "
            f"{shadow_signal['alpha_score']:.2f}"
        )

async def listen(
    *,
    live_entry_scheduler=None,
    live_entry_result_mailbox=None,
):
    init_db()
    invalidate_stale_intervals()

    counts = get_counts()

    print("=" * 70)
    print("🧠 DELVE MEME AGENT — MARKET COLLECTOR")
    print("👁️  Pump.fun launches + buyers")
    print("🔒 READ-ONLY MODE")
    print("=" * 70)

    print(
        f"Starting DB → "
        f"{counts['launches']} launches | "
        f"{counts['buys']} buys | "
        f"{counts['wallets']} wallets"
    )

    print("=" * 70)

    while True:
        coverage_interval_id = None
        coverage_last_seen_at = None

        try:
            async with websockets.connect(
                WS_URL,
                ssl=SSL_CONTEXT,
                ping_interval=20,
                ping_timeout=20,
            ) as websocket:

                request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {
                            "mentions": [
                                PUMP_PROGRAM_ID
                            ]
                        },
                        {
                            "commitment": "processed"
                        }
                    ],
                }

                await websocket.send(
                    json.dumps(request)
                )

                response = json.loads(
                    await websocket.recv()
                )

                if "error" in response:
                    raise RuntimeError(
                        response["error"]
                    )

                coverage_interval_id = (
                    start_coverage_interval()
                )

                coverage_last_seen_at = int(
                    time.time()
                )

                print()
                print("✅ Connected to Solana")
                print("✅ Collector is learning")
                print("-" * 70)

                async for message in websocket:
                    payload = json.loads(
                        message
                    )
                    coverage_last_seen_at = int(
                        time.time()
                    )
                    params = payload.get(
                        "params"
                    )

                    if not params:
                        continue

                    result = params.get(
                        "result",
                        {},
                    )

                    value = result.get(
                        "value",
                        {},
                    )

                    if value.get("err") is not None:
                        continue

                    signature = value.get(
                        "signature"
                    )

                    logs = value.get(
                        "logs",
                        [],
                    )

                    slot = (
                        result
                        .get("context", {})
                        .get("slot")
                    )

                    is_create = any(
                        "Instruction: CreateV2"
                        in log
                        for log in logs
                    )
                    if is_create:
                        asyncio.create_task(
                            process_launch_safe(
                                signature,
                                slot,
                            )
                        )
                    trade_event = extract_trade_event_from_logs(
                        logs
                    )

                    if trade_event:
                        save_trade_event(
                            signature,
                            slot,
                            trade_event,
                        )

                        #
                        # Position management is BUY + SELL.
                        # Only create a task when this mint
                        # actually has an open shadow position.
                        #
                        if is_open_shadow_mint_tracked(
                            trade_event["mint"]
                        ):
                            asyncio.create_task(
                                process_shadow_position_event_safe(
                                    trade_event
                                )
                            )

                        #
                        # Candidate generation remains BUY-only.
                        #
                        if trade_event["is_buy"]:
                            process_buy_event(
                                signature,
                                slot,
                                trade_event,
                                live_entry_scheduler=(
                                    live_entry_scheduler
                                ),
                                live_entry_result_mailbox=(
                                    live_entry_result_mailbox
                                ),
                            )

                    #
                    # Pump traffic can keep recv()
                    # continuously ready. Explicitly
                    # yield so websocket keepalive and
                    # other asyncio tasks get scheduler
                    # time between messages.
                    #
                    await asyncio.sleep(0)

                close_coverage_interval(
                    coverage_interval_id,
                    "websocket_closed",
                    coverage_last_seen_at,
                )

                coverage_interval_id = None
        except asyncio.CancelledError:
            close_coverage_interval(
                coverage_interval_id,
                "cancelled",
                coverage_last_seen_at,
            )

            raise

        except Exception as error:
            close_coverage_interval(
                coverage_interval_id,
                "connection_error",
                coverage_last_seen_at,
            )

            print()
            print(
                f"⚠️ Collector error: {error}"
            )
            print(
                "🔄 Reconnecting in 2 seconds..."
            )

            await asyncio.sleep(2)

async def run_market_collector(
    *,
    live_entry_scheduler: (
        LiveEntryCandidateScheduler | None
    ) = None,
    live_entry_result_mailbox: (
        LiveEntryResultMailbox | None
    ) = None,
) -> None:
    """
    Run the market collector.

    With no explicitly supplied live-entry scheduler, the collector
    performs no live-entry evidence RPC work.

    If a scheduler is supplied, this function owns its lifecycle and
    closes it during collector shutdown.
    """

    if (
        live_entry_scheduler is not None
        and not isinstance(
            live_entry_scheduler,
            LiveEntryCandidateScheduler,
        )
    ):
        raise TypeError(
            "live_entry_scheduler must be "
            "LiveEntryCandidateScheduler"
        )

    if (
        live_entry_result_mailbox is not None
        and not isinstance(
            live_entry_result_mailbox,
            LiveEntryResultMailbox,
        )
    ):
        raise TypeError(
            "live_entry_result_mailbox must be "
            "LiveEntryResultMailbox"
        )

    if (
        live_entry_result_mailbox is not None
        and live_entry_scheduler is None
    ):
        raise ValueError(
            "live_entry_result_mailbox requires "
            "live_entry_scheduler"
        )

    sweeper_task = None

    try:
        #
        # Initialize and validate the shadow ledger
        # before live market events can arrive.
        #
        shadow_account = (
            initialize_shadow_account()
        )

        print(
            "💰 SHADOW ACCOUNT | "
            f"equity="
            f"{shadow_account.current_equity_lamports / 1_000_000_000:.9f} SOL | "
            f"cash="
            f"{shadow_account.cash_balance_lamports / 1_000_000_000:.9f} SOL | "
            f"open={shadow_account.open_positions}"
        )

        #
        # Restore persisted OPEN shadow positions
        # before live market events can arrive.
        #
        tracked_mints = (
            initialize_shadow_position_manager()
        )

        print(
            "📒 SHADOW PORTFOLIO | "
            f"{len(tracked_mints)} open position(s) restored"
        )

        #
        # Exactly one periodic sweeper for the
        # lifetime of this collector process.
        #
        sweeper_task = asyncio.create_task(
            run_shadow_position_sweeper()
        )

        await listen(
            live_entry_scheduler=(
                live_entry_scheduler
            ),
            live_entry_result_mailbox=(
                live_entry_result_mailbox
            ),
        )

    finally:
        try:
            #
            # Ownership begins as soon as a valid scheduler is accepted,
            # not only after collector startup succeeds.
            #
            if live_entry_scheduler is not None:
                await live_entry_scheduler.close()

                print(
                    "🧹 Live-entry candidate "
                    "scheduler stopped."
                )

        finally:
            if sweeper_task is not None:
                sweeper_task.cancel()

                try:
                    await sweeper_task

                except asyncio.CancelledError:
                    pass

                print(
                    "🧹 Shadow position sweeper stopped."
                )


if __name__ == "__main__":
    try:
        asyncio.run(
            run_market_collector()
        )

    except KeyboardInterrupt:
        print()
        print("🛑 Market Collector stopped.")
