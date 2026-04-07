"""Twelve Data API fetcher for high-quality forex data."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Literal, TypeAlias, cast

import pandas as pd
import requests

TWELVE_DATA_BASE_URL = "https://api.twelvedata.com"
DEFAULT_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")
TimeframeLiteral: TypeAlias = Literal["1min", "5min", "15min", "30min", "1h", "4h", "1d"]
RequestParam: TypeAlias = str | int | float | bool | None


class TwelveDataFetcher:
    """Fetch forex data from Twelve Data API.

    Free tier: 800 API credits/day
    - Time series: 8 credits per request
    - Intraday up to 2 years history
    """

    TIMEFRAMES = {
        "1min": "1min",
        "5min": "5min",
        "15min": "15min",
        "30min": "30min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1day",
    }

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or DEFAULT_API_KEY

    def fetch(
        self,
        symbol: str,
        interval: TimeframeLiteral = "1h",
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        outputsize: int = 5000,
    ) -> pd.DataFrame:
        """Fetch OHLCV data for a forex pair.

        Args:
            symbol: Forex pair (e.g., "EUR/USD", "GBP/USD", "EURUSD")
            interval: Timeframe
            start_date: Start date
            end_date: End date
            outputsize: Number of data points (max 5000)

        Returns:
            DataFrame with columns: datetime, open, high, low, close, volume
        """
        if not self.api_key:
            raise ValueError(
                "Twelve Data API key required. Set TWELVE_DATA_API_KEY env var or pass api_key."
            )

        # Convert symbol format - Twelve Data expects "EUR/USD" format for forex
        symbol_clean = symbol.upper().replace("/", "").replace("=X", "")
        symbol_formatted = f"{symbol_clean[:3]}/{symbol_clean[3:]}"
        endpoint = f"{TWELVE_DATA_BASE_URL}/time_series"

        params: dict[str, RequestParam] = {
            "symbol": symbol_formatted,
            "interval": interval,
            "apikey": self.api_key,
            "outputsize": outputsize,
        }

        if start_date:
            params["start_date"] = start_date.strftime("%Y-%m-%d")
        if end_date:
            params["end_date"] = end_date.strftime("%Y-%m-%d")

        response = requests.get(endpoint, params=params, timeout=30)
        response.raise_for_status()

        data = cast(dict[str, Any], response.json())

        if "status" in data and data["status"] == "error":
            raise ValueError(f"Twelve Data API error: {data.get('message', 'Unknown error')}")

        if "values" not in data or not data["values"]:
            return pd.DataFrame()

        df = pd.DataFrame(data["values"])
        df = df.rename(columns={
            "datetime": "datetime",
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
        })

        df["datetime"] = pd.to_datetime(df["datetime"])
        df["open"] = pd.to_numeric(df["open"])
        df["high"] = pd.to_numeric(df["high"])
        df["low"] = pd.to_numeric(df["low"])
        df["close"] = pd.to_numeric(df["close"])

        # Volume not always present for forex
        if "v" in df.columns:
            df["volume"] = pd.to_numeric(df["v"], errors="coerce").fillna(0)
        else:
            df["volume"] = 0

        df = df.sort_values("datetime").reset_index(drop=True)
        df = df.set_index("datetime")

        return df

    def fetch_multi_timeframe(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        timeframes: list[str] | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch data for multiple timeframes.

        Args:
            symbol: Forex pair
            start_date: Start date
            end_date: End date
            timeframes: List of timeframes

        Returns:
            Dictionary mapping timeframe to DataFrame
        """
        if timeframes is None:
            timeframes = ["1h", "30min", "15min"]
        result = {}

        for tf in timeframes:
            if tf not in self.TIMEFRAMES:
                continue
            try:
                df = self.fetch(
                    symbol,
                    interval=cast(TimeframeLiteral, tf),
                    start_date=start_date,
                    end_date=end_date,
                )
                if not df.empty:
                    result[tf] = df
            except Exception as e:
                print(f"Warning: Failed to fetch {symbol} {tf}: {e}")

        return result

    def get_quote(self, symbol: str) -> dict[str, Any]:
        """Get current quote for a forex pair.

        Args:
            symbol: Forex pair

        Returns:
            Dictionary with bid, ask, spread info
        """
        if not self.api_key:
            raise ValueError("API key required")

        endpoint = f"{TWELVE_DATA_BASE_URL}/quote"
        params: dict[str, RequestParam] = {
            "symbol": symbol.replace("/", ""),
            "apikey": self.api_key,
        }

        response = requests.get(endpoint, params=params, timeout=30)
        response.raise_for_status()

        return cast(dict[str, Any], response.json())


def get_free_forex_data(
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    timeframes: list[str] | None = None,
    api_key: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Get forex data using available sources.

    Order of preference:
    1. Twelve Data (if API key provided) - best quality
    2. yfinance (free, but limited intraday)

    Args:
        symbol: Forex pair
        start_date: Start date
        end_date: End date
        timeframes: List of timeframes
        api_key: Optional Twelve Data API key

    Returns:
        Dictionary mapping timeframe to DataFrame
    """
    if timeframes is None:
        timeframes = ["1h", "30min", "15min"]
    result = {}

    # Try Twelve Data first (if API key)
    if api_key or DEFAULT_API_KEY:
        try:
            fetcher = TwelveDataFetcher(api_key)
            result = fetcher.fetch_multi_timeframe(symbol, start_date, end_date, timeframes)
            if result:
                return result
        except Exception:
            pass

    # Fall back to yfinance
    from src.data.fetcher import DataFetcher

    yf_fetcher = DataFetcher()
    tf_map = {"1h": "1h", "30min": "30m", "15min": "15m", "5min": "5m", "1d": "1d"}

    for tf in timeframes:
        yf_interval = tf_map.get(tf, tf)
        # yfinance limits: 15m/30m to 60 days, 1h to 730 days
        if yf_interval in ["15m", "30m", "5m"]:
            max(start_date, end_date - timedelta(days=60))
            df = yf_fetcher.fetch(symbol.replace("/", "") + "=X", period="60d", interval=yf_interval)
        else:
            df = yf_fetcher.fetch(symbol.replace("/", "") + "=X", period="2y", interval=yf_interval)

        if not df.empty:
            result[tf] = df

    return result
