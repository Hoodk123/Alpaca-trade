# TradOX — AI Trading Agent (Alpaca Paper Trading)

A minimal but complete AI trading agent for the Alpaca AI Trading Agents Hackathon,
built by **TradOX**.

## What it does (end to end)
1. **Fetch** recent price data for each ticker from Alpaca's Market Data API, plus
   the latest trade and the live market status (open/closed).
2. **Decide** — an LLM looks at the recent price action, indicator summary
   (SMA trend, RSI, MACD, joined so at least two of three must agree on a BUY),
   live unrealized P/L on any held position, and how old the latest price is, then
   outputs a BUY / SELL / HOLD decision with a short reason and confidence. It is
   told when the market is closed so it treats the price as a snapshot, not a
   live signal.
3. **Protect** — before the LLM decides, a hard stop-loss floor (default -8% on
   live unrealized P/L) force-sells any bleeding position regardless of what the
   model says.
4. **Act** — the agent places a paper (simulated) order on Alpaca based on that
   decision (sized off ~5% of your cash; never sells a stock you don't hold,
   never stacks a duplicate order on an already-pending one, never places orders
   while paused).
5. **Log** — every decision + reasoning + indicators + order result +
   data-freshness info is saved to `logs/decisions.jsonl`.
6. **Show** — a live Flask dashboard (`app.py`) that polls a single `/api/status`
   endpoint and renders account cards, positions, and the scan history with no
   page reload.

This is intentionally a small but genuine agent (reads real data, reasons,
protects, acts, and is auditable).

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

The dashboard polls `/api/status` automatically, so new scan results show up
with no page reload. It includes:

- **Header** — market open/closed pill with next open/close, agent Running/Paused
  pill, and a **Pause/Resume** button (pausing stops order placement but keeps
  scanning and the dashboard data fresh).
- **Account cards** — Cash, Portfolio Value, Buying Power, **Total Return**
  (vs `baseline.json`, seeded on first load) and **Today's P/L** (equity vs
  last close), each green/red.
- **Goal of the Day** — set a target % return; a progress bar and status show
  "In progress" / "Goal reached today".
- **Equity curve** — a Chart.js line chart of portfolio value over time from
  Alpaca's portfolio-history API (shows a friendly "not enough history" note on
  a brand-new account).
- **Current Positions** — live holdings with per-row **Liquidate** buttons
  (confirm dialog) and an amber warning when a position is within 2pp of the
  hard stop-loss threshold.
- **Latest Scan** — the decision log with RSI, MACD histogram (with up/down
  arrow), trend (SMA5 vs SMA20), a per-row Market Open/Closed chip + data-age,
  and **client-side pagination** (15/30/50 rows per page).
- **Strategy panel** — plain-English explainer that pulls its numbers straight
  from `config.py` (indicators, 5% sizing, -8% hard stop), so it never goes
  stale.

Backend safety behavior is untouched by the UI: position-aware sells, cash
sizing, the hard stop-loss floor, and the confidence threshold all still apply.

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
- `config.py` — loads API keys/settings from `.env` (never hardcoded); strategy
  constants (hard stop %, cash %, confidence floor) live here too
- `data_fetcher.py` — pulls recent bars, the latest trade, and market status from Alpaca
- `strategy.py` — computes indicators (SMA/EMA, RSI, MACD) + calls the LLM for a
  decision, factoring in how old the price is, whether the market is open, and
  live P/L on held positions
- `broker.py` — places the paper order on Alpaca (position-aware, cash-sized,
  duplicate-order guarded); account summary, open positions, liquidate, and
  portfolio-history helpers for the dashboard
- `agent.py` — the scan loop (change detection, hard stop-loss floor, market
  open/close notifications, start/stop pause flag) and goal-of-the-day state
- `app.py` — the Flask dashboard + background scheduler; routes for `/api/status`,
  `/api/equity-history`, `/liquidate/<symbol>`, `/api/pause`, `/api/goal`,
  and `/healthz`
- `templates/index.html`, `static/dashboard.css`, `static/dashboard.js` — the dashboard UI
- `Procfile`, `render.yaml` — Render deployment config (start command + env var fields)

## Growth ideas (add these once the base works)
- Add a risk-guard step: a plain Python rule that can veto or shrink the
  LLM's order (e.g. "never risk more than 5% of paper cash on one trade")
- Add sentiment: fetch recent headlines for the symbol and pass them to the
  LLM alongside the price data
- Add options: once the base loop is solid, extend `strategy.py` to also
  consider a simple covered-call or protective-put suggestion
