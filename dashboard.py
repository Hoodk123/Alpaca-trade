"""Generates a small HTML dashboard from logs/decisions.jsonl.

Usage:
    uv run python dashboard.py [output.html]

Reads every decision recorded by the agent, calls Alpaca for the live market
status, and writes a single self-contained HTML file you can open in a browser.
"""
import json
import os
import sys
from datetime import datetime

from data_fetcher import get_market_status
from strategy import age_minutes

LOG_PATH = "logs/decisions.jsonl"
OUTPUT_PATH = sys.argv[1] if len(sys.argv) > 1 else "dashboard.html"


def _fmt_age(data_as_of, market_open: bool) -> tuple:
    """Returns (display_text, is_stale) for a row's data-age cell.

    is_stale means the data is older than 15 minutes while the market is open.
    """
    if not data_as_of:
        return ("n/a", False)
    age = age_minutes(data_as_of)
    if age < 0:
        return (data_as_of, False)
    if market_open:
        text = f"{age}m ago"
        return (text, age > 15)
    # Market closed: age vs "now" is expected to be large; label it clearly.
    try:
        as_of = datetime.fromisoformat(data_as_of)
        day = as_of.strftime("%a %m-%d")
    except (TypeError, ValueError):
        day = "?"
    return (f"closed - {day} close", False)


def _market_badge(market):
    if market["is_open"]:
        return '<span class="badge badge-open">Market: OPEN</span>'
    return '<span class="badge badge-closed">Market: CLOSED — showing last close</span>'


def build(market) -> str:
    entries = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    rows = []
    for e in entries:
        market_open = bool(e.get("market_open", market["is_open"]))
        age_text, is_stale = _fmt_age(e.get("data_as_of"), market_open)
        stale_cell = f' class="stale"' if is_stale else ""
        age_cell = f'<td{stale_cell}>{age_text}</td>'
        rows.append(
            "<tr>"
            f"<td>{e.get('timestamp', '')[:19]}</td>"
            f"<td>{e.get('symbol', '')}</td>"
            f"<td>{e.get('action', '')}</td>"
            f"<td>{e.get('confidence', '')}</td>"
            f"<td>{e.get('live_price', e.get('latest_close', ''))}</td>"
            f"<td>{('OPEN' if market_open else 'CLOSED')}</td>"
            f"{age_cell}"
            f"<td>{e.get('order_id', '') or ''}</td>"
            "</tr>"
        )

    if not rows:
        rows_html = '<tr><td colspan="8" class="empty">No decisions yet — run agent.py first.</td></tr>'
    else:
        rows_html = "\n".join(rows)

    latest = entries[-1]["timestamp"][:19] if entries else "never"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TradOX — Decision Dashboard</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background: #f4f6f9; color: #1a202c; }}
  .wrap {{ max-width: 1000px; margin: 0 auto; padding: 24px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: #718096; font-size: 14px; margin-bottom: 16px; }}
  .badge {{ display: inline-block; padding: 6px 14px; border-radius: 999px; font-size: 14px; font-weight: 600; }}
  .badge-open {{ background: #c6f6d5; color: #22543d; }}
  .badge-closed {{ background: #feebc8; color: #7b341e; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #edf2f7; font-size: 14px; }}
  th {{ background: #edf2f7; font-weight: 600; }}
  td.stale {{ background: #ffedd5; color: #7c2d12; font-weight: 600; }}
  .empty {{ text-align: center; color: #a0aec0; padding: 24px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>TradOX — AI Trading Decision Dashboard</h1>
  <div class="sub">Latest decision: {latest}</div>
  <div class="sub">{_market_badge(market)}</div>
  <h2 style="font-size:16px;margin:20px 0 8px;">Decision Log</h2>
  <table>
    <thead>
      <tr><th>Decided at</th><th>Symbol</th><th>Action</th><th>Conf</th><th>Price</th><th>Market</th><th>Data age</th><th>Order</th></tr>
    </thead>
    <tbody>
{rows_html}
    </tbody>
  </table>
</div>
</body>
</html>
"""


def main():
    try:
        market = get_market_status()
    except Exception as e:  # if Alpaca is unreachable, don't block the dashboard
        market = {"is_open": False, "next_open": "", "next_close": ""}
        print(f"  [dashboard] could not fetch market status ({e}); defaulting to CLOSED")

    html = build(market)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {OUTPUT_PATH} ({os.path.getsize(OUTPUT_PATH)} bytes)")


if __name__ == "__main__":
    main()
