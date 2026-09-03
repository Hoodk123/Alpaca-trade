"""Places (simulated) orders on Alpaca's paper trading account.

Position-aware: won't try to sell shares you don't hold, and sizes BUYs
off a % of your actual paper cash instead of a blind fixed quantity.
"""
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.common.exceptions import APIError

from config import Config


def get_client():
    return TradingClient(Config.ALPACA_API_KEY, Config.ALPACA_SECRET_KEY, paper=Config.ALPACA_PAPER)


def get_current_qty(client, symbol: str) -> int:
    """How many shares of `symbol` we currently hold (0 if none)."""
    try:
        position = client.get_open_position(symbol)
        return int(float(position.qty))
    except APIError:
        return 0  # no position open


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
            return None, f"skipped SELL — no position held in {symbol}"
        qty, side = held, OrderSide.SELL
    else:  # BUY
        qty = calc_buy_qty(client, latest_price)
        if qty <= 0:
            return None, "skipped BUY — insufficient cash for even 1 share"
        side = OrderSide.BUY

    order = MarketOrderRequest(symbol=symbol, qty=qty, side=side, time_in_force=TimeInForce.DAY)
    result = client.submit_order(order)
    return result, f"{action} {qty} share(s)"