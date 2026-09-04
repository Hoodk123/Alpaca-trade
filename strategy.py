"""Turns price bars into a BUY/SELL/HOLD decision using simple indicators + an LLM."""
import json
import time
from datetime import datetime, timezone

import requests

from config import Config

MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 8  # free NIM tier throttles hard, so back off longer between retries

_COMPACT_RETRY_PROMPT = """Output ONLY a compact JSON object with no code fences and no
other text, based on this data: {summary}
Exact shape:
{{"action": "BUY|SELL|HOLD", "reason": "short sentence under 25 words", "confidence": 0.0-1.0}}"""


def _parse_decision(text):
    """Turns raw LLM text into a normalized decision dict handle-or-fail.

    Tries strict parse first, then salvages the first JSON object embedded in the
    text (handles truncated trailing output / trailing boilerplate), then falls
    back to a safe HOLD.
    """
    text = text.replace("```json", "").replace("```", "").strip()

    def _valid(d):
        return isinstance(d, dict) and d.get("action") in ("BUY", "SELL", "HOLD")

    try:
        decision = json.loads(text)
        return decision if _valid(decision) else _bad(text, "unexpected shape")
    except json.JSONDecodeError:
        pass

    # Salvage: scan for the outermost balanced {...} object and try to parse it.
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        decision = json.loads(candidate)
                        if _valid(decision):
                            return decision
                    except json.JSONDecodeError:
                        pass
                    break

    return _bad(text, "could not parse")


def _bad(text, why):
    return {
        "action": "HOLD",
        "reason": f"The model returned {why} output: {text[:100]}",
        "confidence": 0.0,
    }


def sma(values, window):
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def ema(values, period):
    """Exponential moving average over the full series (Wilder-style seeding with
    SMA of the first `period` points). Returns None if not enough data."""
    if len(values) < period:
        return None
    multiplier = 2.0 / (period + 1)
    ema_val = sum(values[:period]) / period
    for price in values[period:]:
        ema_val = (price - ema_val) * multiplier + ema_val
    return ema_val


def rsi(closes, period=14):
    """Relative Strength Index using Wilder's smoothing. Returns 0-100 or None."""
    if len(closes) <= period:
        return None
    gains = []
    losses = []
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain = max(change, 0)
        loss = max(-change, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(closes, fast=12, slow=26, signal=9):
    """MACD line, signal line, and histogram. Returns (macd, signal, hist) or
    (None, None, None) if there isn't enough price history (need slow+signal pts)."""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None, None, None
    macd_line = ema_fast - ema_slow

    # Signal line is an EMA of the MACD line over the points where it exists.
    macd_series = []
    # Recompute the MACD at each step so the signal EMA reflects the full history.
    for i in range(len(closes)):
        if i + 1 < slow:
            continue
        ef = ema(closes[: i + 1], fast)
        es = ema(closes[: i + 1], slow)
        if ef is not None and es is not None:
            macd_series.append(ef - es)
    if len(macd_series) < signal:
        return None, None, None
    signal_line = ema(macd_series, signal)
    return macd_line, signal_line, macd_line - signal_line



def build_summary(bars):
    closes = [b["close"] for b in bars]
    m_line, m_signal, m_hist = macd(closes)
    rsi_val = rsi(closes, 14)
    return {
        "latest_close": closes[-1],
        "sma_5": sma(closes, 5),
        "sma_20": sma(closes, 20),
        "rsi_14": round(rsi_val, 2) if rsi_val is not None else None,
        "macd": round(m_line, 3) if m_line is not None else None,
        "macd_signal": round(m_signal, 3) if m_signal is not None else None,
        "macd_histogram": round(m_hist, 3) if m_hist is not None else None,
        "pct_change_5d": round((closes[-1] - closes[-5]) / closes[-5] * 100, 2) if len(closes) >= 5 else None,
        "recent_closes": closes[-10:],
    }


def _call_llm_once(prompt: str):
    """One attempt at calling the LLM. Returns (text_or_None, error_or_None, was_truncated).
    `was_truncated` is True when the output hit the token ceiling mid-response."""
    try:
        response = requests.post(
            f"{Config.LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {Config.LLM_API_KEY}"},
            json={
                "model": Config.LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": Config.LLM_MAX_TOKENS,
            },
            timeout=120,
        )
    except requests.exceptions.Timeout:
        return None, "LLM request timed out after 120s", False
    except requests.exceptions.RequestException as e:
        return None, f"Could not reach the LLM provider: {e}", False

    if response.status_code == 429:
        return None, "rate limited (HTTP 429) - back off and retry", False

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        reason = f"LLM provider rejected the request (HTTP {status})"
        if status == 401:
            reason += " - check LLM_API_KEY in .env"
        elif status in (404, 410):
            reason += " - check LLM_MODEL and LLM_BASE_URL in .env"
        else:
            reason += " - check LLM_BASE_URL in .env"
        return None, reason, False

    body = response.json()
    choices = body.get("choices") or []
    content = choices[0].get("message", {}).get("content") if choices else None
    finish = choices[0].get("finish_reason") if choices else None

    if not content:
        return None, "LLM returned an empty response - will retry", False

    return content.strip(), None, (finish == "length")


def age_minutes(price_as_of) -> int:
    """How old (in whole minutes) the given ISO timestamp is right now, vs UTC now."""
    try:
        as_of = datetime.fromisoformat(price_as_of)
    except (TypeError, ValueError):
        return -1  # unknown age
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return max(0, int((now - as_of).total_seconds() // 60))


def _market_line(market_open: bool) -> str:
    if market_open:
        return "Market status: OPEN."
    return "Market status: CLOSED - this is the last traded price before close, not a live quote."


def decide(symbol: str, bars: list, position_qty: int = 0, avg_entry_price: float = None,
           unrealized_plpc: float = None, price_as_of=None, market_open: bool = True) -> dict:
    """Returns {"action": "BUY"|"SELL"|"HOLD", "reason": str, "confidence": float}

    `position_qty` is how many shares we currently hold (0 = none). The model is
    told this so it never suggests SELL for a stock we don't own.

    `avg_entry_price` (float, optional) is the average price we bought at.

    `unrealized_plpc` (float, optional) is the LIVE P/L as a % of cost basis
    straight from Alpaca (not derived from the daily close). It's preferred for
    the unrealized-P/L line so the model treats SELL as a real take-profit /
    stop-loss exit. `avg_entry_price` is used only as a fallback when the live
    P/L% is unavailable.

    `price_as_of` is the ISO timestamp of the live price and `market_open` tells
    the model whether the market is currently trading, so it can factor staleness
    into its confidence.
    """
    summary = build_summary(bars)

    position_line = (
        f"Current position: you hold {position_qty} shares of {symbol}."
        if position_qty > 0
        else f"Current position: you hold NO shares of {symbol}."
    )

    # Unrealized P/L on an open position, for taking profit / cutting losses.
    # Prefer Alpaca's real-time unrealized_plpc; fall back to computing from the
    # average entry price vs. the latest close only when the live value is absent.
    pnl_line = ""
    if position_qty > 0:
        pnl_pct = unrealized_plpc
        if pnl_pct is None and avg_entry_price:
            pnl_pct = (bars[-1]["close"] - avg_entry_price) / avg_entry_price * 100.0
        if pnl_pct is not None:
            pnl_line = (
                f"You are currently {pnl_pct:+.2f}% on this position "
                f"({'up' if pnl_pct >= 0 else 'down'}). "
                f"Consider whether to hold for more upside, or exit to lock in gains / limit losses. "
                f"Treat SELL as a genuine take-profit or stop-loss decision."
            )

    if price_as_of:
        age = age_minutes(price_as_of)
        age_line = (
            f"Live price timestamp: {price_as_of} ({age} minute(s) ago)."
            if age >= 0
            else f"Live price timestamp: {price_as_of}."
        )
    else:
        age_line = "Live price timestamp: unknown."

    market_line = _market_line(market_open)

    prompt = f"""You are a cautious trading assistant operating on a PAPER (simulated) account.
Symbol: {symbol}
{position_line}
{pnl_line}
{age_line}
{market_line}
Recent data: {json.dumps(summary)}

Indicator guide - weigh these TOGETHER, do not rely on any single one:
- SMA: use for overall trend direction (price above SMA-20 = uptrend, below = downtrend).
- RSI(14): above 70 = overbought (caution on new BUYs); below 30 = oversold (caution on SELLs / possible bounce).
- MACD: histogram turning positive = bullish momentum building; turning negative = bearish momentum building. MACD above signal = momentum up, below = momentum down.

Only suggest BUY when at least two of the three indicators (SMA trend, RSI, MACD) agree the setup is attractive. Prefer HOLD when they conflict.

IMPORTANT:
- SELL is only valid when you currently hold shares of {symbol}.
- If you hold NO shares, you MUST NOT choose SELL (pick BUY or HOLD).
- BUY is only valid when the setup is actually attractive.
- If you hold a position, weigh SELL as a genuine risk decision using the unrealized P/L above: exit to lock in gains (take-profit) or to limit losses (stop-loss), vs. holding for more upside.
- If the market is CLOSED, treat the latest price as a snapshot (last traded price before close), NOT a live quote. Factor that staleness into your confidence - lower your confidence when reasoning off stale or closed-market data.

Respond with ONLY a compact JSON object and nothing else (no code fences, no
explanation). Keep the "reason" under 25 words and make "confidence" a number
between 0.0 and 1.0. Exact shape:
{{"action": "BUY|SELL|HOLD", "reason": "short", "confidence": 0.0-1.0}}"""

    text, last_reason = None, None
    truncated = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        text, last_reason, truncated = _call_llm_once(prompt)
        if text:
            break
        if attempt < MAX_ATTEMPTS:
            wait = min(RETRY_DELAY_SECONDS * attempt, 20)
            print(f"  [strategy] {symbol}: {last_reason} (attempt {attempt}/{MAX_ATTEMPTS}, retrying in {wait}s)")
            time.sleep(wait)
            if truncated:
                # If it was cut off, ask it to output the bare JSON this time.
                prompt = _COMPACT_RETRY_PROMPT.format(summary=json.dumps(summary))

    if not text:
        print(f"  [strategy] {symbol}: giving up after {MAX_ATTEMPTS} attempts - holding.")
        return {"action": "HOLD", "reason": last_reason or "no response from LLM", "confidence": 0.0}

    decision = _parse_decision(text)
    return decision