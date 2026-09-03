"""TradOX dashboard — a Flask app that serves the decision log live and runs
the trading scans automatically in the background.

Routes:
    GET /                    the dashboard page (serves templates/index.html)
    GET /api/decisions       latest decision log rows as JSON
    GET /api/market          live market status (open/closed, next open/close)
    GET /api/refresh         append any pending decisions from the JSONL to the
                             in-memory cache and return the count added
    GET /healthz             simple liveness probe (for uptime pings)

The dashboard page polls /api/decisions every few seconds, so new entries that
the background scanner appends to logs/decisions.jsonl show up automatically.

Background scans: an APScheduler BackgroundScheduler calls agent.run_scan()
on LOOP_INTERVAL_SECONDS (default 900s = 15 min). The scheduler is started once
when this module is imported. Under gunicorn you MUST run a single worker
(`--workers 1`, see Procfile / render.yaml) or start the scheduler under a lock,
otherwise each worker starts its own scan loop.

Usage:
    uv run python app.py          # local: serves on http://127.0.0.1:5000
    gunicorn app:app --workers 1  # production (Render)
"""
import json
import os
import threading

from flask import Flask, jsonify, render_template

from config import Config
import agent
from data_fetcher import get_market_status
from strategy import age_minutes

LOG_PATH = "logs/decisions.jsonl"
POLL_SECONDS = 5
_STALE_MINUTES = 15

# Background scan tuning (env-overridable, matching the CLI --loop pattern).
SCHEDULE_ENABLED = os.getenv("SCHEDULE_ENABLED", "true").lower() == "true"
LOOP_INTERVAL_SECONDS = int(os.getenv("LOOP_INTERVAL_SECONDS", "900"))
# Keep the auto-scan in recommend-only mode by default (no paper orders) unless
# TRADE_ON_SCAN=true is explicitly set.
TRADE_ON_SCAN = os.getenv("TRADE_ON_SCAN", "false").lower() == "true"

app = Flask(__name__)


def _ensure_state_dirs():
    """Create runtime dirs/files if missing.

    Render's filesystem is ephemeral — logs/, market_state.json and any other
    local state are wiped on every redeploy. For a hackathon demo that's fine:
    the agent just re-creates them here and re-seeds its state on the next scan.
    Anything that must survive a redeploy would need an external store instead.
    """
    try:
        os.makedirs("logs", exist_ok=True)
        if not os.path.exists(agent.MARKET_STATE_PATH):
            with open(agent.MARKET_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump({"was_open": None}, f)
    except OSError:
        pass


_ensure_state_dirs()


# --- shared state (thread-safe via a lock) --------------------------------
_lock = threading.Lock()
_cached_entries = []  # rows already read from the JSONL, newest first
_cursor = 0           # byte offset in the JSONL we've consumed so far


def _load_new_entries():
    """Incremental tail-read of logs/decisions.jsonl. Returns the rows found
    since the last read (newest first)."""
    global _cursor
    if not os.path.exists(LOG_PATH):
        return []
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            f.seek(_cursor)
            new_lines = f.readlines()
            _cursor = f.tell()
    except OSError:
        return []

    rows = []
    for line in new_lines:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    rows.reverse()  # newest first
    return rows


def _row_view(e: dict, market_open: bool) -> dict:
    """Project one log row into the shape the frontend wants, computing the
    data-age display + staleness flag on the fly."""
    data_as_of = e.get("data_as_of")
    age = age_minutes(data_as_of) if data_as_of else -1
    if data_as_of and market_open and age > _STALE_MINUTES:
        stale = True
    else:
        stale = False
    return {
        "timestamp": (e.get("timestamp") or "")[:19],
        "symbol": e.get("symbol", ""),
        "action": e.get("action", ""),
        "confidence": e.get("confidence"),
        "price": e.get("live_price", e.get("latest_close")),
        "market_open": bool(e.get("market_open", market_open)),
        "data_as_of": data_as_of,
        "data_age_minutes": age,
        "order_id": e.get("order_id") or "",
        "note": e.get("note", ""),
        "stale": stale,
    }


def _snapshot():
    """Return a fresh, sorted snapshot of all known entries plus row views."""
    with _lock:
        entries = [dict(x) for x in _cached_entries]
    entries.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    market_open = False
    try:
        market_open = bool(get_market_status()["is_open"])
    except Exception:
        market_open = bool(entries[0]["market_open"]) if entries else False
    return [_row_view(e, market_open) for e in entries], market_open


# --- routes ----------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", poll_seconds=POLL_SECONDS, stale_minutes=_STALE_MINUTES)


@app.route("/api/decisions")
def api_decisions():
    views, _ = _snapshot()
    return jsonify({"count": len(views), "entries": views})


@app.route("/api/market")
def api_market():
    try:
        return jsonify(get_market_status())
    except Exception as e:
        return jsonify({"error": str(e), "is_open": False}), 502


@app.route("/api/refresh")
def api_refresh():
    """Pull any newly-appended decisions into the cache. Returns how many were
    added so the frontend knows whether anything changed."""
    with _lock:
        added = _load_new_entries()
        _cached_entries.extend(added)
    return jsonify({"added": len(added)})


@app.route("/healthz")
def healthz():
    """Liveness probe for uptime-ping services (keeps the free instance awake)."""
    return "OK", 200


# --- background scheduler ---------------------------------------------------
_scheduler = None
_scheduler_started = False


def _start_scheduler():
    """Start the background scan loop once. Guarded so gunicorn's reloader /
    multiple workers can't start more than one scheduler per process."""
    global _scheduler, _scheduler_started
    if _scheduler_started or not SCHEDULE_ENABLED:
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        symbols = list(Config.DEFAULT_WATCHLIST)
        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.add_job(
            lambda: agent.run_scan(symbols, trade=TRADE_ON_SCAN),
            trigger="interval",
            seconds=LOOP_INTERVAL_SECONDS,
            id="tradox-scan",
            replace_existing=True,
        )
        _scheduler.start()
        _scheduler_started = True
        mode = "TRADING" if TRADE_ON_SCAN else "recommend-only (watch)"
        print(f"[app] background scan started: every {LOOP_INTERVAL_SECONDS}s, mode={mode}")
    except Exception as e:
        print(f"[app] could not start background scheduler: {e}")


# --- bootstrap ---------------------------------------------------------------
with _lock:
    _cached_entries = _load_new_entries()  # (newest first from the tail helper)

_start_scheduler()


if __name__ == "__main__":
    # Local dev: run the built-in dev server (no gunicorn). The scheduler is
    # already running from _start_scheduler() above, so agent scans happen too.
    from werkzeug.serving import run_simple

    run_simple("127.0.0.1", 5000, app, use_reloader=False, use_debugger=True)
