"""Pulls recent daily bars, the latest trade, and market status from Alpaca."""
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame

from config import Config


def get_recent_bars(symbol: str, days: int = 30):
    """Returns a list of dicts: [{date, open, high, low, close, volume}, ...]"""
    client = StockHistoricalDataClient(Config.ALPACA_API_KEY, Config.ALPACA_SECRET_KEY)

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=datetime.now() - timedelta(days=days * 2),  # buffer for weekends/holidays
    )
    bars = client.get_stock_bars(request)
    rows = bars.data.get(symbol, [])[-days:]

    return [
        {
            "date": bar.timestamp.strftime("%Y-%m-%d"),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in rows
    ]


def get_latest_price(symbol: str) -> dict:
    """Returns the most recent trade for `symbol`:
    {"price": float, "as_of": ISO-8601 timestamp of the trade itself}

    `as_of` is the trade's own timestamp (from Alpaca's `Trade.timestamp`),
    NOT the fetch time, so callers can tell how stale the quote really is.
    """
    client = StockHistoricalDataClient(Config.ALPACA_API_KEY, Config.ALPACA_SECRET_KEY)
    request = StockLatestTradeRequest(symbol_or_symbols=symbol)
    result = client.get_stock_latest_trade(request)
    trade = result[symbol]
    return {
        "price": float(trade.price),
        "as_of": trade.timestamp.isoformat(),
    }


def get_market_status() -> dict:
    """Returns whether the market is currently open plus the next open/close:
    {"is_open": bool, "next_open": ISO timestamp, "next_close": ISO timestamp}.
    """
    from alpaca.trading.client import TradingClient

    client = TradingClient(Config.ALPACA_API_KEY, Config.ALPACA_SECRET_KEY, paper=Config.ALPACA_PAPER)
    clock = client.get_clock()
    return {
        "is_open": bool(clock.is_open),
        "next_open": clock.next_open.isoformat(),
        "next_close": clock.next_close.isoformat(),
    }
