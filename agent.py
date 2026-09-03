"""Entry point: scan a watchlist -> LLM ranks/decides each -> trade the strongest
signals -> log everything."""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from config import Config
from data_fetcher import get_recent_bars, get_latest_price, get_market_status
from strategy import decide, age_minutes
from broker import execute, get_client, get_current_qty

LOG_PATH = "logs/decisions.jsonl"


def evaluate(symbol: str, market_open: bool):
    """Fetches data + gets an LLM decision for one symbol. Returns None if not enough data."""
    bars = get_recent_bars(symbol)
    if len(bars) < 5:
        print(f"  {symbol}: not enough data yet, skipping.")
        return None

    held = get_current_qty(get_client(), symbol)

    try:
        live = get_latest_price(symbol)
        live_price = live["price"]
        price_as_of = live["as_of"]
    except Exception:
        # Fall back to the last daily close with no timestamp if the quote fails.
        live_price = bars[-1]["close"]
        price_as_of = None

    decision = decide(symbol, bars, position_qty=held, price_as_of=price_as_of, market_open=market_open)
    decision["symbol"] = symbol
    decision["latest_close"] = bars[-1]["close"]
    decision["live_price"] = live_price
    decision["data_as_of"] = price_as_of
    return decision


def run_scan(symbols: list, trade: bool):
    print(f"\n[{datetime.now().isoformat(timespec='seconds')}] Scanning watchlist: {', '.join(symbols)}")

    # Market status is the same for every symbol, so fetch it once per scan.
    market = get_market_status()
    market_open = market["is_open"]
    print(f"  Market: {'OPEN' if market_open else 'CLOSED'}"
          f" | next close {market['next_close'] if market_open else market['next_open']}")

    # NIM free tier queues/CPU-throttles each call (~40s latency), so fire the
    # per-symbol LLM calls in parallel and let the latency overlap.
    results = []
    with ThreadPoolExecutor(max_workers=len(symbols)) as pool:
        for result in pool.map(lambda s: evaluate(s, market_open), symbols):
            if result:
                results.append(result)

    results.sort(key=lambda r: r.get("confidence", 0), reverse=True)  # strongest signal first

    print("\n  Rank  Symbol  Action  Conf   Reason")
    for i, r in enumerate(results, 1):
        print(f"  {i:<5} {r['symbol']:<7} {r['action']:<7} {r.get('confidence', 0):<6} {r['reason']}")

    os.makedirs("logs", exist_ok=True)
    for r in results:
        order_id, note = None, "not traded (scan-only mode)"

        should_trade = trade and r.get("confidence", 0) >= Config.MIN_CONFIDENCE_TO_TRADE
        if should_trade:
            order, note = execute(r["symbol"], r["action"], r["live_price"])
            order_id = str(order.id) if order else None
            print(f"\n  -> {r['symbol']}: {note}" + (f" (order {order_id})" if order_id else ""))
        elif trade:
            note = f"below confidence floor ({Config.MIN_CONFIDENCE_TO_TRADE}) — watch only"

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "symbol": r["symbol"],
            "action": r["action"],
            "reason": r["reason"],
            "confidence": r.get("confidence"),
            "latest_close": r["latest_close"],
            "live_price": r["live_price"],
            "data_as_of": r["data_as_of"],
            "data_age_minutes": age_minutes(r["data_as_of"]),
            "market_open": market_open,
            "order_id": order_id,
            "note": note,
        }
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description="AI trading agent — scans a watchlist and recommends/trades on Alpaca paper trading")
    parser.add_argument("--symbols", default=",".join(Config.DEFAULT_WATCHLIST),
                         help="Comma-separated tickers, e.g. AAPL,MSFT,TSM")
    parser.add_argument("--loop", type=int, default=0, help="Seconds between scans. 0 = run once and exit.")
    parser.add_argument("--recommend-only", action="store_true",
                         help="Just rank and show recommendations — never place real paper orders.")
    args = parser.parse_args()

    Config.validate()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    trade = not args.recommend_only

    if args.loop <= 0:
        run_scan(symbols, trade)
    else:
        print(f"Running every {args.loop}s. Press Ctrl+C to stop.")
        while True:
            try:
                run_scan(symbols, trade)
                time.sleep(args.loop)
            except KeyboardInterrupt:
                print("\nStopped.")
                break


if __name__ == "__main__":
    main()