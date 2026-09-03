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
   that decision (sized off your cash; never sells a stock you don't hold).
4. **Log** — every decision + reasoning + order result + data-freshness info is
   saved to `logs/decisions.jsonl`. `dashboard.py` turns that log into an HTML
   report with a live market badge and data-age warnings.

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

## Run on a loop (e.g. every 15 minutes)
```bash
uv run python agent.py --loop 900
```

## Generate the dashboard
```bash
uv run python dashboard.py        # writes dashboard.html
```

## Files
- `config.py` — loads API keys/settings from `.env`
- `data_fetcher.py` — pulls recent bars, the latest trade, and market status from Alpaca
- `strategy.py` — computes simple indicators + calls the LLM for a decision,
  factoring in how old the price is and whether the market is open
- `broker.py` — places the paper order on Alpaca (position-aware, cash-sized)
- `agent.py` — the main loop that wires it all together and logs results
- `dashboard.py` — renders `logs/decisions.jsonl` into an HTML dashboard

## Growth ideas (add these once the base works)
- Add a risk-guard step: a plain Python rule that can veto or shrink the
  LLM's order (e.g. "never risk more than 5% of paper cash on one trade")
- Add sentiment: fetch recent headlines for the symbol and pass them to the
  LLM alongside the price data
- Add options: once the base loop is solid, extend `strategy.py` to also
  consider a simple covered-call or protective-put suggestion
