# TradOX — AI Trading Agent (Alpaca Paper Trading)

A minimal but complete AI trading agent for the Alpaca AI Trading Agents Hackathon,
built by **TradOX**.

## What it does (end to end)
1. **Fetch** recent price data for each ticker from Alpaca's Market Data API, plus
   the latest trade and the live market status (open/closed).
2. **Decide** — an LLM looks at the recent price action, simple indicators
   (moving averages), and how old the latest price is, then outputs a
   BUY / SELL / HOLD decision with a short reason and confidence. It is told
   when the market is closed so it treats the price as a snapshot, not a live
   signal.
3. **Act** — the agent places a paper (simulated) order on Alpaca based on
   that decision (sized off your cash; never sells a stock you don't hold,
   never stacks a duplicate order on an already-pending one).
4. **Log** — every decision + reasoning + order result + data-freshness info is
   saved to `logs/decisions.jsonl`.
5. **Show** — a live Flask dashboard (`app.py`) serves `logs/decisions.jsonl`
   with a market badge, data-age warnings, and visual markers that clearly show
   which signals actually executed orders vs. which were only watched/skipped.

This is intentionally a small but genuine agent (reads real data, reasons,
acts, and is auditable).

## Setup
```bash
uv sync            # installs deps into .venv (uv.lock committed)
cp .env.example .env
# fill in your Alpaca paper trading keys (free, from alpaca.markets)
# and your LLM API key (NVIDIA NIM free tier, Anthropic, or OpenAI)
```

## Scan the watchlist (recommendations only — no orders)
```bash
uv run python agent.py --recommend-only
```

## Scan the watchlist and trade the strongest signals
```bash
uv run python agent.py
```

## Run the live dashboard + background scanner (single process)
```bash
uv run python app.py                 # starts the web app AND the scan loop
# then open http://127.0.0.1:5000
```
`app.py` runs an APScheduler background loop that calls the same `run_scan()`
the CLI uses, every `LOOP_INTERVAL_SECONDS` (default 900s = 15 min), in
recommend-only mode by default. Set `TRADE_ON_SCAN=true` to let it place orders.

The dashboard polls `/api/decisions` every few seconds, so new scan results show
up automatically with no page reload.

## Deploy to Render (persistent web service)
TradOX is ready to deploy as a Render web service using the native Python
buildpack — no Docker/CI needed.

1. Push this repo to GitHub.
2. In Render, **New → Blueprint** and connect the repo. `render.yaml` is picked
   up automatically and pre-creates a `web` service.
3. Fill in the env vars Render lists for you (the values are never in the repo):
   `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `LLM_API_KEY` (optional overrides:
   `LLM_MODEL`, `LLM_BASE_URL`, `LLM_MAX_TOKENS`, `LOOP_INTERVAL_SECONDS`,
   `TRADE_ON_SCAN`, `SCHEDULE_ENABLED`).
4. Deploy. Render runs `gunicorn app:app --workers 1 --timeout 120`
   (see `Procfile`), which serves the dashboard and starts the background scanner.

Caveats:
- **Single worker required.** The in-process scheduler starts once on import, so
  `--workers 1` prevents multiple scan loops.
- **Ephemeral filesystem.** `logs/`, `market_state.json` and other local state
  are recreated at runtime and reset on every redeploy. Fine for a hackathon
  demo; persistent data would need an external store.
- **Free instances sleep.** Use an uptime ping (e.g. UptimeRobot or cron hitting
  `https://<your-app>.onrender.com/healthz`) to keep the free instance awake.

## Files
- `config.py` — loads API keys/settings from `.env` (never hardcoded)
- `data_fetcher.py` — pulls recent bars, the latest trade, and market status from Alpaca
- `strategy.py` — computes simple indicators + calls the LLM for a decision,
  factoring in how old the price is and whether the market is open
- `broker.py` — places the paper order on Alpaca (position-aware, cash-sized,
  duplicate-order guarded)
- `agent.py` — the scan loop that wires it all together and logs results;
  includes change-detection and market open/close notifications
- `app.py` — the Flask dashboard + background scheduler; `/healthz` liveness probe
- `templates/index.html`, `static/dashboard.css`, `static/dashboard.js` — the dashboard UI
- `Procfile`, `render.yaml` — Render deployment config (start command + env var fields)

## Growth ideas (add these once the base works)
- Add a risk-guard step: a plain Python rule that can veto or shrink the
  LLM's order (e.g. "never risk more than 5% of paper cash on one trade")
- Add sentiment: fetch recent headlines for the symbol and pass them to the
  LLM alongside the price data
- Add options: once the base loop is solid, extend `strategy.py` to also
  consider a simple covered-call or protective-put suggestion
