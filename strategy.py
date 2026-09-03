"""Turns price bars into a BUY/SELL/HOLD decision using simple indicators + an LLM."""
import json
import time
from datetime import datetime, timezone

import requests

from config import Config

MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 8  # free NIM tier throttles hard, so back off longer between retries


def sma(values, window):
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def build_summary(bars):
    closes = [b["close"] for b in bars]
    return {
        "latest_close": closes[-1],
        "sma_5": sma(closes, 5),
        "sma_20": sma(closes, 20),
        "pct_change_5d": round((closes[-1] - closes[-5]) / closes[-5] * 100, 2) if len(closes) >= 5 else None,
        "recent_closes": closes[-10:],
    }


def _call_llm_once(prompt: str):
    """One attempt at calling the LLM. Returns (text_or_None, hold_reason_or_None)."""
    try:
        response = requests.post(
            f"{Config.LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {Config.LLM_API_KEY}"},
            json={
                "model": Config.LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 500,
            },
            timeout=120,
        )
    except requests.exceptions.Timeout:
        return None, "LLM request timed out after 120s"
    except requests.exceptions.RequestException as e:
        return None, f"Could not reach the LLM provider: {e}"

    if response.status_code == 429:
        return None, "rate limited (HTTP 429) — back off and retry"

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
        return None, reason

    body = response.json()
    choices = body.get("choices") or []
    content = choices[0].get("message", {}).get("content") if choices else None
    finish = choices[0].get("finish_reason") if choices else None

    if finish == "length":
        return None, "LLM output truncated (increase max_tokens)"

    if not content:
        return None, "LLM returned an empty response — will retry"

    return content.strip(), None


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
    return "Market status: CLOSED — this is the last traded price before close, not a live quote."


def decide(symbol: str, bars: list, position_qty: int = 0,
           price_as_of=None, market_open: bool = True) -> dict:
    """Returns {"action": "BUY"|"SELL"|"HOLD", "reason": str, "confidence": float}

    `position_qty` is how many shares we currently hold (0 = none). The model is
    told this so it never suggests SELL for a stock we don't own.

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
{age_line}
{market_line}
Recent data: {json.dumps(summary)}

IMPORTANT:
- SELL is only valid when you currently hold shares of {symbol}.
- If you hold NO shares, you MUST NOT choose SELL (pick BUY or HOLD).
- BUY is only valid when the setup is actually attractive.
- If the market is CLOSED, treat the live price as a snapshot (last traded price
  before close), NOT a live quote. Factor that staleness into your confidence —
  lower your confidence when reasoning off stale or closed-market data.

Based only on this data, decide one action: BUY, SELL, or HOLD.
Respond with ONLY valid JSON, no other text, in this exact shape:
{{"action": "BUY|SELL|HOLD", "reason": "one short sentence", "confidence": 0.0-1.0}}"""

    text, last_reason = None, None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        text, last_reason = _call_llm_once(prompt)
        if text:
            break
        if attempt < MAX_ATTEMPTS:
            wait = min(RETRY_DELAY_SECONDS * attempt, 20)
            print(f"  [strategy] {symbol}: {last_reason} (attempt {attempt}/{MAX_ATTEMPTS}, retrying in {wait}s)")
            time.sleep(wait)

    if not text:
        print(f"  [strategy] {symbol}: giving up after {MAX_ATTEMPTS} attempts - holding.")
        return {"action": "HOLD", "reason": last_reason or "no response from LLM", "confidence": 0.0}

    # Strip markdown fences if the model added them anyway
    text = text.replace("```json", "").replace("```", "").strip()

    try:
        decision = json.loads(text)
    except json.JSONDecodeError:
        decision = {"action": "HOLD", "reason": f"Could not parse LLM output: {text[:100]}", "confidence": 0.0}

    if not isinstance(decision, dict) or decision.get("action") not in ("BUY", "SELL", "HOLD"):
        decision = {
            "action": "HOLD",
            "reason": f"The model returned unexpected output: {text[:100]}",
            "confidence": 0.0,
        }

    return decision