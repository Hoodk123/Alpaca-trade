"""Entry point: fetch data -> LLM decides -> place paper order -> log it."""
import argparse
import json
import os
import sys
import time
from datetime import datetime

from config import Config
from data_fetcher import get_recent_bars
from strategy import decide
from broker import execute

LOG_PATH = "logs/decisions.jsonl"


def run_once(symbol: str):
    print(f"\n[{datetime.now().isoformat(timespec='seconds')}] Checking {symbol}...")

    bars = get_recent_bars(symbol)
    if len(bars) < 5:
        print(f"  Not enough data for {symbol} yet, skipping.")
        return

    decision = decide(symbol, bars)
    print(f"  Decision: {decision['action']} — {decision['reason']} (confidence {decision.get('confidence', '?')})")

    order = execute(symbol, decision["action"])
    order_id = str(order.id) if order else None
    print(f"  Order placed: {order_id}" if order_id else "  No order placed (HOLD).")

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "symbol": symbol,
        "action": decision["action"],
        "reason": decision["reason"],
        "confidence": decision.get("confidence"),
        "order_id": order_id,
        "latest_close": bars[-1]["close"],
    }
    os.makedirs("logs", exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description="Simple AI trading agent (Alpaca paper trading)")
    parser.add_argument("--symbol", default="AAPL", help="Stock ticker to trade")
    parser.add_argument("--loop", type=int, default=0, help="Seconds between runs. 0 = run once and exit.")
    args = parser.parse_args()

    Config.validate()

    if args.loop <= 0:
        run_once(args.symbol)
    else:
        print(f"Running every {args.loop}s. Press Ctrl+C to stop.")
        while True:
            try:
                run_once(args.symbol)
                time.sleep(args.loop)
            except KeyboardInterrupt:
                print("\nStopped.")
                break


if __name__ == "__main__":
    main()
