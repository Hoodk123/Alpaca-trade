"""Turns price bars into a BUY/SELL/HOLD decision using simple indicators + an LLM."""
import json
import requests

from config import Config


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


def decide(symbol: str, bars: list) -> dict:
    """Returns {"action": "BUY"|"SELL"|"HOLD", "reason": str, "confidence": float}"""
    summary = build_summary(bars)

    prompt = f"""You are a cautious trading assistant operating on a PAPER (simulated) account.
Symbol: {symbol}
Recent data: {json.dumps(summary)}

Based only on this data, decide one action: BUY, SELL, or HOLD.
Respond with ONLY valid JSON, no other text, in this exact shape:
{{"action": "BUY|SELL|HOLD", "reason": "one short sentence", "confidence": 0.0-1.0}}"""

    try:
        response = requests.post(
            f"{Config.LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {Config.LLM_API_KEY}"},
            json={
                "model": Config.LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 200,
            },
            timeout=30,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout:
        reason = "LLM request timed out after 30s"
        print(f"  [strategy] {reason} - holding.")
        return {"action": "HOLD", "reason": reason, "confidence": 0.0}
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        reason = f"LLM provider rejected the request (HTTP {status})"
        if status == 401:
            reason += " - check LLM_API_KEY in .env"
        elif status in (404, 410):
            reason += " - check LLM_MODEL and LLM_BASE_URL in .env"
        else:
            reason += " - check LLM_BASE_URL in .env"
        print(f"  [strategy] {reason} (holding).")
        return {"action": "HOLD", "reason": reason, "confidence": 0.0}
    except requests.exceptions.RequestException as e:
        reason = f"Could not reach the LLM provider: {e}"
        print(f"  [strategy] {reason} (holding).")
        return {"action": "HOLD", "reason": reason, "confidence": 0.0}

    text = response.json()["choices"][0]["message"]["content"].strip()

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
