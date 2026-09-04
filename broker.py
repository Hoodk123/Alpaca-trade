"""Places (simulated) orders on Alpaca's paper trading account.

Position-aware: won't try to sell shares you don't hold, and sizes BUYs
off a % of your actual paper cash instead of a blind fixed quantity.
"""
from datetime import datetime, timedelta

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.common.exceptions import APIError

from config import Config


def get_account_summary() -> dict:
    """Live paper-account summary straight from Alpaca (single fetch).

    Returns {"cash", "portfolio_value", "buying_power", "equity", "last_equity"}.
    `last_equity` is the equity recorded at the previous market close, so
    equity - last_equity = today's P/L in dollars.
    """
    client = get_client()
    account = client.get_account()
    return {
        "cash": float(account.cash),
        "portfolio_value": float(account.portfolio_value),
        "buying_power": float(account.buying_power),
        "equity": float(account.equity),
        "last_equity": float(account.last_equity),
    }


def get_equity_history(days: int = 30) -> list:
    """Returns portfolio equity over time as [{timestamp, equity}, ...].

    Wraps TradingClient.get_portfolio_history(). `timestamp` is an ISO
    datetime string, `equity` a float dollar amount.

    For a brand-new account that hasn't built up any daily history yet,
    get_portfolio_history() can return 0 or 1 points. In that case this falls
    back to appending the account's current equity as a single synthetic point
    dated today, so the chart never comes back empty.
    """
    client = get_client()
    points = []
    try:
        hist = client.get_portfolio_history(
            date_start=(datetime.now() - timedelta(days=days)).date(),
            date_end=datetime.now().date(),
            timeframe="1D",
        )
        if hist.timestamp and hist.equity:
            for ts, eq in zip(hist.timestamp, hist.equity):
                try:
                    points.append({"timestamp": datetime.fromtimestamp(ts).isoformat(), "equity": float(eq)})
                except (TypeError, ValueError, OSError):
                    continue
    except Exception:
        points = []

    # Fallback: new account / empty history -> single synthetic current-equity
    # point so the chart has something to show instead of a blank canvas.
    if len(points) < 2:
        try:
            current = get_account_summary()["equity"]
            points.append({"timestamp": datetime.now().isoformat(), "equity": float(current)})
        except Exception:
            pass

    return points


def get_client():
    return TradingClient(Config.ALPACA_API_KEY, Config.ALPACA_SECRET_KEY, paper=Config.ALPACA_PAPER)


def get_current_qty(client, symbol: str) -> int:
    """How many shares of `symbol` we currently hold (0 if none)."""
    try:
        position = client.get_open_position(symbol)
        return int(float(position.qty))
    except APIError:
        return 0  # no position open


def get_position_detail(client, symbol: str) -> dict:
    """Returns live position info for `symbol` straight from Alpaca:
    {"qty": int, "avg_entry_price": float|None,
     "unrealized_pl": float|None, "unrealized_plpc": float|None}

    `unrealized_pl` is the raw dollar P/L and `unrealized_plpc` the live P/L as a
    % of cost basis, exactly as Alpaca reports them (not derived from the daily
    close). Use `unrealized_plpc` for the hard stop-loss floor and for showing
    real, live P/L to the model. Returns an all-zero/None dict if no position.
    """
    try:
        position = client.get_open_position(symbol)
        return {
            "qty": int(float(position.qty)),
            "avg_entry_price": float(position.avg_entry_price) if position.avg_entry_price else None,
            "unrealized_pl": float(position.unrealized_pl) if position.unrealized_pl is not None else None,
            "unrealized_plpc": float(position.unrealized_plpc) if position.unrealized_plpc is not None else None,
        }
    except APIError:
        return {"qty": 0, "avg_entry_price": None, "unrealized_pl": None, "unrealized_plpc": None}


def has_pending_order(client, symbol: str, side: OrderSide) -> bool:
    """Whether there's already an open (unfilled) order for `symbol` on `side`.

    Prevents duplicate order stacking: if a prior BUY/SELL is still in flight
    (new/accepted/pending), we skip submitting another one for the same pair.
    """
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
        orders = client.get_orders(req)
    except APIError:
        # Treat a lookup failure as "assume open order exists" would block trading;
        # better to fail the check safely and let the order attempt happen.
        return False
    for order in orders:
        if order.side == side:
            return True
    return False


def calc_buy_qty(client, price: float) -> int:
    """Simple sizing: spend up to MAX_CASH_PCT_PER_TRADE of available cash."""
    account = client.get_account()
    cash = float(account.cash)
    budget = cash * Config.MAX_CASH_PCT_PER_TRADE
    return int(budget // price) if budget >= price else 0


def execute(symbol: str, action: str, latest_price: float):
    """Places a market order for BUY/SELL. Returns (order_or_None, note_str)."""
    client = get_client()

    if action == "HOLD":
        return None, "no order (HOLD)"

    if action == "SELL":
        held = get_current_qty(client, symbol)
        if held <= 0:
            return None, f"skipped SELL - no position held in {symbol}"
        qty, side = held, OrderSide.SELL
    else:  # BUY
        qty = calc_buy_qty(client, latest_price)
        if qty <= 0:
            return None, "skipped BUY - insufficient cash for even 1 share"
        side = OrderSide.BUY

    if has_pending_order(client, symbol, side):
        return None, f"skipped {action} - order already pending for {symbol}"

    order = MarketOrderRequest(symbol=symbol, qty=qty, side=side, time_in_force=TimeInForce.DAY)
    result = client.submit_order(order)
    return result, f"{action} {qty} share(s)"


def get_open_positions() -> list:
    """All open positions as a list of dicts for the dashboard.

    Each: {"symbol", "qty", "avg_entry_price", "market_value",
           "unrealized_pl", "unrealized_plpc", "current_price"}.
    """
    try:
        positions = get_client().get_all_positions()
    except APIError:
        return []
    out = []
    for p in positions:
        try:
            out.append({
                "symbol": p.symbol,
                "qty": int(float(p.qty)),
                "avg_entry_price": float(p.avg_entry_price) if p.avg_entry_price else None,
                "market_value": float(p.market_value) if p.market_value else None,
                "unrealized_pl": float(p.unrealized_pl) if p.unrealized_pl is not None else None,
                "unrealized_plpc": float(p.unrealized_plpc) if p.unrealized_plpc is not None else None,
                "current_price": float(p.current_price) if p.current_price else None,
            })
        except (TypeError, ValueError):
            continue
    return out


def liquidate_position(symbol: str):
    """Sells the entire held position in `symbol`. Returns (order_or_None, note).

    Position-aware: no-op (with a note) when nothing is held, and it never
    submits if an order for that symbol is already pending.
    """
    client = get_client()
    held = get_current_qty(client, symbol)
    if held <= 0:
        return None, f"nothing to liquidate - no position in {symbol}"

    if has_pending_order(client, symbol, OrderSide.SELL):
        return None, f"skipped liquidate - order already pending for {symbol}"

    order = MarketOrderRequest(symbol=symbol, qty=held, side=OrderSide.SELL, time_in_force=TimeInForce.DAY)
    result = client.submit_order(order)
    return result, f"liquidated {held} share(s) of {symbol}"