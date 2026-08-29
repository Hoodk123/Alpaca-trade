"""Places (simulated) orders on Alpaca's paper trading account."""
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from config import Config

QTY = 1  # keep it simple: fixed 1-share orders for the demo


def get_client():
    return TradingClient(Config.ALPACA_API_KEY, Config.ALPACA_SECRET_KEY, paper=Config.ALPACA_PAPER)


def execute(symbol: str, action: str):
    """Places a market order for BUY/SELL. Returns the order or None for HOLD."""
    if action == "HOLD":
        return None

    client = get_client()
    order = MarketOrderRequest(
        symbol=symbol,
        qty=QTY,
        side=OrderSide.BUY if action == "BUY" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    return client.submit_order(order)
