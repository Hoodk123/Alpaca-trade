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
MARKET_STATE_PATH = "market_state.json"


def _load_market_state():
    """Reads the last saved market state. Returns True/False or None if unknown."""
    try:
        if os.path.exists(MARKET_STATE_PATH):
            with open(MARKET_STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            if isinstance(state, dict) and "was_open" in state:
                return bool(state["was_open"])
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return None


def _save_market_state(was_open: bool):
    try:
        with open(MARKET_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"was_open": bool(was_open)}, f)
    except OSError:
        pass


def _notify_market_change(changed_to_open: bool):
    """Fires a desktop notification via plyer. Never crashes the scan."""
    try:
        from plyer import notification
        if changed_to_open:
            title, message = "TradOX", "Market is now OPEN — TradOX will resume trading"
        else:
            title, message = "TradOX", "Market just CLOSED — TradOX will hold until it reopens"
        notification.notify(title=title, message=message, timeout=10)
        print(f"  [notify] {message}")
    except Exception as e:
        print(f"  [notify] could not show desktop notification: {e}")


def _track_market_transition(is_open: bool):
    """Compares the just-fetched clock against the last known state and fires a
    notification if the market opened or closed since the previous scan."""
    was_open = _load_market_state()
    _save_market_state(is_open)
    if was_open is None:
        return  # first run — just seed the file, don't notify
    if was_open != is_open:
        _notify_market_change(changed_to_open=is_open)


def _last_log_entries():
    """Reads decisions.jsonl and returns {symbol: last_log_entry} for each symbol."""
    entries = {}
    if not os.path.exists(LOG_PATH):
        return entries
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                symbol = entry.get("symbol")
                if symbol:
                    entries[symbol] = entry
            except json.JSONDecodeError:
                continue
    return entries


def evaluate(symbol: str, market_open: bool, last_entry=None):
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

    latest_close = bars[-1]["close"]

    # --- change detection: skip the LLM if nothing changed since the last scan ---
    if last_entry is not None:
        prev_price = last_entry.get("live_price")
        prev_close = last_entry.get("latest_close")
        if prev_price is not None and prev_close is not None:
            if live_price == prev_price and latest_close == prev_close:
                return {
                    "action": last_entry.get("action", "HOLD"),
                    "reason": "unchanged since last scan — reused prior decision",
                    "confidence": last_entry.get("confidence", 0.0),
                    "symbol": symbol,
                    "latest_close": latest_close,
                    "live_price": live_price,
                    "data_as_of": price_as_of,
                    "_reused": True,
                }

    decision = decide(symbol, bars, position_qty=held, price_as_of=price_as_of, market_open=market_open)
    decision["symbol"] = symbol
    decision["latest_close"] = latest_close
    decision["live_price"] = live_price
    decision["data_as_of"] = price_as_of
    return decision


def run_scan(symbols: list, trade: bool):
    print(f"\n[{datetime.now().isoformat(timespec='seconds')}] Scanning watchlist: {', '.join(symbols)}")

    # Market status is the same for every symbol, so fetch it once per scan.
    market = get_market_status()
    market_open = market["is_open"]
    _track_market_transition(market_open)
    if market_open:
        print(f"  Market: OPEN | next close {market['next_close']}")
    else:
        print(f"  Market: CLOSED | next open {market['next_open']}")

    # Load the most recent log entry per symbol for change detection.
    last_entries = _last_log_entries()

    # NIM free tier queues/CPU-throttles each call (~40s latency), so fire the
    # per-symbol LLM calls in parallel and let the latency overlap.
    results = []
    with ThreadPoolExecutor(max_workers=len(symbols)) as pool:
        for result in pool.map(lambda s: evaluate(s, market_open, last_entries.get(s)), symbols):
            if result:
                results.append(result)

    results.sort(key=lambda r: r.get("confidence", 0), reverse=True)  # strongest signal first

    print("\n  Rank  Symbol  Action  Conf   Reason")
    for i, r in enumerate(results, 1):
        print(f"  {i:<5} {r['symbol']:<7} {r['action']:<7} {r.get('confidence', 0):<6} {r['reason']}")

    os.makedirs("logs", exist_ok=True)
    for r in results:
        reused = r.pop("_reused", False)

        if reused:
            order_id, note = None, "unchanged since last scan — reused prior decision"
        else:
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

    return market


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
                market = run_scan(symbols, trade)
                if market["is_open"]:
                    time.sleep(args.loop)
                else:
                    next_open = datetime.fromisoformat(market["next_open"])
                    now = datetime.now(next_open.tzinfo)
                    sleep_seconds = max(0, int((next_open - now).total_seconds()))
                    next_open_display = next_open.strftime("%H:%M %Z")
                    print(f"\nMarket closed — next scan at {next_open_display} ({sleep_seconds}s from now)")
                    time.sleep(sleep_seconds)
            except KeyboardInterrupt:
                print("\nStopped.")
                break


if __name__ == "__main__":
    main()