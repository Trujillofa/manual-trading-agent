"""Data fetcher for forex using yfinance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, cast

import pandas as pd
import yfinance as yf


@dataclass
class Candle:
    """Single candle OHLCV data."""

    timestamp: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


class DataFetcher:
    """Fetch forex OHLCV data using yfinance."""

    SYMBOL_MAP: ClassVar[dict[str, str]] = {
        "EUR/USD": "EURUSD=X",
        "GBP/USD": "GBPUSD=X",
        "USD/JPY": "USDJPY=X",
        "USD/CHF": "USDCHF=X",
        "USD/CAD": "USDCAD=X",
        "AUD/USD": "AUDUSD=X",
        "NZD/USD": "NZDUSD=X",
        "EUR/GBP": "EURGBP=X",
        "EUR/JPY": "EURJPY=X",
        "EUR/CHF": "EURCHF=X",
        "GBP/JPY": "GBPJPY=X",
        "AUD/JPY": "AUDJPY=X",
    }

    _COLUMN_MAP: ClassVar[dict[str, str]] = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }

    def __init__(self) -> None:
        pass

    def _to_yfinance_symbol(self, symbol: str) -> str:
        mapped = self.SYMBOL_MAP.get(symbol)
        if mapped:
            return mapped

        normalized = symbol.replace("/", "").strip().upper()
        if normalized.endswith("=X"):
            return normalized
        return f"{normalized}=X"

    def _normalize_columns(self, data: pd.DataFrame) -> pd.DataFrame:
        if isinstance(data.columns, pd.MultiIndex):
            data = data.copy()
            data.columns = data.columns.get_level_values(0)

        normalized = data.rename(columns=self._COLUMN_MAP)

        required_cols = ["open", "high", "low", "close"]
        if any(column not in normalized.columns for column in required_cols):
            return pd.DataFrame()

        if "volume" not in normalized.columns:
            normalized["volume"] = 0

        result = cast(
            pd.DataFrame,
            normalized.loc[:, ["open", "high", "low", "close", "volume"]].copy(),
        )
        volume = cast(pd.Series, result["volume"])
        result.loc[:, "volume"] = volume.fillna(0).astype(int)
        return result

    def fetch(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        interval: str = "1h",
        period: str | None = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV data for a forex pair.

        Args:
            symbol: Trading pair (e.g., "EUR/USD")
            start: Start date (YYYY-MM-DD)
            end: End date (YYYY-MM-DD)
            interval: Candle interval (1h, 30m, 15m, 1d)
            period: Alternative to start/end (e.g., "60d")

        Returns:
            DataFrame with columns: open, high, low, close, volume
        """
        yf_symbol = self._to_yfinance_symbol(symbol)

        # Use ticker.history() for intraday data, download() for daily
        if interval in ("1m", "2m", "5m", "15m", "30m", "60m", "90m"):
            ticker = yf.Ticker(yf_symbol)
            data = ticker.history(start=start, end=end, period=period, interval=interval)
        else:
            data = yf.download(
                yf_symbol,
                start=start,
                end=end,
                period=period,
                interval=interval,
                progress=False,
            )

        if data is None or data.empty:
            return pd.DataFrame()

        return self._normalize_columns(data)

    def fetch_multi_timeframe(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        period: str | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch data across multiple timeframes.

        Returns:
            dict with keys: "1h", "30m", "15m"
        """
        return {
            "1h": self.fetch(symbol, start, end, interval="1h", period=period),
            "30m": self.fetch(symbol, start, end, interval="30m", period=period),
            "15m": self.fetch(symbol, start, end, interval="15m", period=period),
        }
