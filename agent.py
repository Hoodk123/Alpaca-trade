"""Entry point: scan a watchlist -> LLM ranks/decides each -> trade the strongest
signals -> log everything."""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from config import Config
from data_fetcher import get_recent_bars, get_latest_price, get_market_status
from strategy import decide, age_minutes, build_summary
from broker import execute, get_client, get_position_detail, liquidate_position, get_open_positions
from messaging import send_discord_message

LOG_PATH = "logs/decisions.jsonl"
MARKET_STATE_PATH = "market_state.json"

# Paused flag (thread-safe): when paused, run_scan() returns before placing any
# order, so trading (and the goal auto-liquidate) halts but scanning/logging
# keep the dashboard data fresh. Defaults to Running.
_paused = False
_paused_lock = threading.Lock()


def set_paused(paused: bool):
    global _paused
    with _paused_lock:
        _paused = bool(paused)
    return _paused


def is_paused() -> bool:
    with _paused_lock:
        return _paused


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
            title, message = "TradOX", "Market is now OPEN - TradOX will resume trading"
            discord_msg = "🟢 ** TradOX Alert:** Market is now **OPEN** - Agent scanning & execution resumed."
        else:
            title, message = "TradOX", "Market just CLOSED - TradOX will hold until it reopens"
            discord_msg = "🍎 ** TradOX Alert:** Market is now **CLOSED** - Agent scanning & execution resumed."

        notification.notify(title=title, message=message, timeout=10)
        send_discord_message(discord_msg)
        print(f"  [notify] {message}")
    except Exception as e:
        print(f"  [notify] could not show desktop notification: {e}")


def _track_market_transition(is_open: bool):
    """Compares the just-fetched clock against the last known state and fires a
    notification if the market opened or closed since the previous scan."""
    was_open = _load_market_state()
    _save_market_state(is_open)
    if was_open is None:
        return  # first run - just seed the file, don't notify
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


def _compute_indicators(bars: list) -> dict:
    """Dashboard-facing technical summary for one symbol, derived from the same
    daily bars already fetched for the LLM decision (no extra network calls).

    Returns {"rsi_14", "macd_hist", "sma_trend"}: the RSI (1dp), the MACD
    histogram (3dp, for the up/down arrow), and a short trend label derived
    from SMA5 vs SMA20 ("above both" / "below both" / "mixed").
    """
    summary = build_summary(bars)
    sma5 = summary.get("sma_5")
    sma20 = summary.get("sma_20")
    close = bars[-1]["close"] if bars else None
    if sma5 is not None and sma20 is not None and close is not None:
        if close > sma5 > sma20:
            trend = "above both"
        elif close < sma5 < sma20:
            trend = "below both"
        else:
            trend = "mixed"
    else:
        trend = "mixed"
    macd_hist = summary.get("macd_histogram")
    return {
        "rsi_14": round(summary["rsi_14"], 1) if summary.get("rsi_14") is not None else None,
        "macd_hist": round(macd_hist, 3) if macd_hist is not None else None,
        "sma_trend": trend,
    }


def evaluate(symbol: str, market_open: bool, last_entry=None):
    """Fetches data + gets an LLM decision for one symbol. Returns None if not enough data."""
    bars = get_recent_bars(symbol)
    if len(bars) < 5:
        print(f"  {symbol}: not enough data yet, skipping.")
        return None

    position = get_position_detail(get_client(), symbol)
    held = position["qty"]

    try:
        live = get_latest_price(symbol)
        live_price = live["price"]
        price_as_of = live["as_of"]
    except Exception:
        # Fall back to the last daily close with no timestamp if the quote fails.
        live_price = bars[-1]["close"]
        price_as_of = None

    latest_close = bars[-1]["close"]
    indicators = _compute_indicators(bars)

    # --- hard stop-loss floor: independent of the LLM ----------------------
    # If an open position is losing more than the configured % in real time,
    # force a SELL this scan and skip the LLM call entirely. This floor can't be
    # overridden by the model's discretionary judgment.
    plpc = position.get("unrealized_plpc")
    if (held > 0
            and plpc is not None
            and Config.HARD_STOP_LOSS_PCT is not None
            and plpc <= Config.HARD_STOP_LOSS_PCT):
        decision = {
            "action": "SELL",
            "reason": "hard stop-loss triggered",
            "confidence": 1.0,
            "symbol": symbol,
            "latest_close": latest_close,
            "live_price": live_price,
            "data_as_of": price_as_of,
            "unrealized_plpc": plpc,
        }
        decision.update(indicators)
        print(f"  {symbol}: HARD STOP-LOSS ({plpc:+.2f}% <= {Config.HARD_STOP_LOSS_PCT:.2f}%) - forcing SELL, skipping LLM")
        return decision

    # --- change detection: skip the LLM if nothing changed since the last scan ---
    if last_entry is not None:
        prev_price = last_entry.get("live_price")
        prev_close = last_entry.get("latest_close")
        if prev_price is not None and prev_close is not None:
            if live_price == prev_price and latest_close == prev_close:
                reused = {
                    "action": last_entry.get("action", "HOLD"),
                    "reason": "unchanged since last scan - reused prior decision",
                    "confidence": last_entry.get("confidence", 0.0),
                    "symbol": symbol,
                    "latest_close": latest_close,
                    "live_price": live_price,
                    "data_as_of": price_as_of,
                    "unrealized_plpc": plpc,
                    "_reused": True,
                }
                reused.update(indicators)
                return reused

    decision = decide(symbol, bars, position_qty=held,
                      avg_entry_price=position["avg_entry_price"],
                      unrealized_plpc=position["unrealized_plpc"],
                      price_as_of=price_as_of, market_open=market_open)
    decision["symbol"] = symbol
    decision["latest_close"] = latest_close
    decision["live_price"] = live_price
    decision["data_as_of"] = price_as_of
    decision["unrealized_plpc"] = plpc
    decision.update(indicators)
    return decision


def run_scan(symbols: list, trade: bool):
    print(f"\n[{datetime.now().isoformat(timespec='seconds')}] Scanning watchlist: {', '.join(symbols)}")

    # Market status is the same for every symbol, so fetch it once per scan.
    try:
        market = get_market_status()
        market_open = market["is_open"]
        _track_market_transition(market_open)
    except Exception as e:
        print(f"[Warning] could not fetch market status from Alpaca(Server error): {e}")
        print(f"[Warning] Defaulting market_open to True and proceeding with scan...")        
        market_open = True
        market = {"is_open": True, "next_close": "unknown", "next_open": "unknown"}
    if market_open:
        print(f"  Market: OPEN | next close {market.get('next_close', 'N/A')}")
    else:
        print(f"  Market: CLOSED | next open {market.get('next_open', 'N/A')}")

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
            order_id, note = None, "unchanged since last scan - reused prior decision"
        else:
            order_id, note = None, "not traded (scan-only mode)"

            should_trade = (not is_paused()) and trade and r.get("confidence", 0) >= Config.MIN_CONFIDENCE_TO_TRADE
            if should_trade:
                order, note = execute(r["symbol"], r["action"], r["live_price"])
                order_id = str(order.id) if order else None
                print(f"\n  -> {r['symbol']}: {note}" + (f" (order {order_id})" if order_id else ""))

                # Send Discord Alert for Trade Execution
                if order_id:
                    trade_msg = (
                        f"🚨 **TradeOX Trade Executed**\n"
                        f"**Action:** `{r['action']}`\n"
                        f"**Symbol:** `{r['symbol']}`\n"
                        f"**Price:** `{r['live_price']}`\n"
                        f"**Reason:** `{r['reason']}`\n"
                    )
                    send_discord_message(trade_msg)
            elif is_paused():
                note = "paused - no orders placed (scanning continues)"
            elif trade:
                note = f"below confidence floor ({Config.MIN_CONFIDENCE_TO_TRADE}) - watch only"

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
            "unrealized_plpc": r.get("unrealized_plpc"),
            # Dashboard indicator columns. These are attached to every decision
            # (including the "reused prior decision" reuse path) by evaluate(),
            # so they carry forward correctly instead of being dropped.
            "rsi_14": r.get("rsi_14"),
            "macd_hist": r.get("macd_hist"),
            "sma_trend": r.get("sma_trend"),
        }
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")

    return market


# --- goal-of-the-day state --------------------------------------------------
def _load_goal_state() -> dict:
    try:
        if os.path.exists(Config.GOAL_STATE_PATH):
            with open(Config.GOAL_STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            if isinstance(state, dict):
                return state
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {"goal_pct": None, "achieved": False, "date": None, "set_on": None}


def _save_goal_state(state: dict):
    try:
        with open(Config.GOAL_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass


def set_goal(goal_pct: float):
    """Sets today's return goal, resetting the achieved flag for a new day."""
    state = _load_goal_state()
    today = datetime.now().strftime("%Y-%m-%d")
    state["goal_pct"] = float(goal_pct)
    state["date"] = today
    state["set_on"] = datetime.now().isoformat()
    state["achieved"] = False
    _save_goal_state(state)
    return state


def get_goal_state() -> dict:
    return _load_goal_state()


def mark_goal_achieved() -> dict:
    """Flag today's goal as achieved (persisted). Returns the state."""
    state = _load_goal_state()
    if state.get("goal_pct") is not None:
        state["achieved"] = True
        state["achieved_on"] = datetime.now().isoformat()
        _save_goal_state(state)
    return state


def liquidate_all(trade_ok: bool = True):
    """Sells every open position (position-aware, pending-order safe).

    Returns a list of {"symbol", "order_id", "note"} for the dashboard. When
    `trade_ok` is False (paused) nothing is sold and notes explain why.
    """
    results = []
    if not trade_ok:
        for s in get_open_positions():
            results.append({"symbol": s.get("symbol"), "order_id": None,
                            "note": "paused - not liquidating"})
        return results
    for pos in get_open_positions():
        order, note = liquidate_position(pos["symbol"])
        results.append({
            "symbol": pos["symbol"],
            "order_id": str(order.id) if order else None,
            "note": note,
        })
    return results


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description="AI trading agent - scans a watchlist and recommends/trades on Alpaca paper trading")
    parser.add_argument("--symbols", default=",".join(Config.DEFAULT_WATCHLIST),
                         help="Comma-separated tickers, e.g. AAPL,MSFT,TSM")
    parser.add_argument("--loop", type=int, default=0, help="Seconds between scans. 0 = run once and exit.")
    parser.add_argument("--recommend-only", action="store_true",
                         help="Just rank and show recommendations - never place real paper orders.")
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
                    print(f"\nMarket closed - next scan at {next_open_display} ({sleep_seconds}s from now)")
                    time.sleep(sleep_seconds)
            except KeyboardInterrupt:
                print("\nStopped.")
                break


if __name__ == "__main__":
    main()