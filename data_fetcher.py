"""Pulls recent daily bars for a symbol from Alpaca's market data API."""
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
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
