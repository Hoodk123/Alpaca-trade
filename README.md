# TradOX — AI Trading Agent (Alpaca Paper Trading)

A minimal but complete AI trading agent for the Alpaca AI Trading Agents Hackathon,
built by **TradOX**.

## What it does (end to end)
1. **Fetch** recent price data for a stock symbol from Alpaca's Market Data API.
2. **Decide** — an LLM looks at the recent price action and a couple of simple
   indicators (moving averages) and outputs a BUY / SELL / HOLD decision with
   a short reason.
3. **Act** — the agent places a paper (simulated) order on Alpaca based on
   that decision.
4. **Log** — every decision + reasoning + order result is printed and saved
   to `logs/decisions.jsonl` so you can show a history in your demo.

This is intentionally the smallest version that is still a genuine agent
(reads real data, reasons, acts, and is auditable). Everything below "Growth
ideas" is optional — the base loop above is enough to demo and submit.

## Setup
```bash
uv sync            # installs deps into .venv (uv.lock committed)
cp .env.example .env
# fill in your Alpaca paper trading keys (free, from alpaca.markets)
# and your LLM API key (NVIDIA NIM free tier, Anthropic, or OpenAI)
```

## Run it once
```bash
uv run python agent.py --symbol AAPL
```

## Run it on a loop (e.g. every 15 minutes)
```bash
uv run python agent.py --symbol AAPL --loop 900
```

## Files
- `config.py` — loads API keys/settings from `.env`
- `data_fetcher.py` — pulls recent bars from Alpaca
- `strategy.py` — computes simple indicators + calls the LLM for a decision
- `broker.py` — places the paper order on Alpaca
- `agent.py` — the main loop that wires it all together and logs results

## Growth ideas (add these once the base works)
- Swap single-symbol for a watchlist (loop over a list of tickers)
- Add a risk-guard step: a plain Python rule that can veto or shrink the
  LLM's order (e.g. "never risk more than 5% of paper cash on one trade")
- Add sentiment: fetch recent headlines for the symbol and pass them to the
  LLM alongside the price data
- Add options: once the base loop is solid, extend `strategy.py` to also
  consider a simple covered-call or protective-put suggestion
- Add a tiny dashboard: a single HTML page that reads `logs/decisions.jsonl`
  and renders a table/chart of decisions vs. price
