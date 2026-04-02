"""Data fetcher for forex using Twelve Data (primary), yfinance (fallback), or OANDA API."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar, cast

import pandas as pd
import yfinance as yf

try:
    import httpx

    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

logger = logging.getLogger(__name__)


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
    """Fetch forex OHLCV data using yfinance or OANDA API."""

    # yfinance symbols (free, 60-day intraday limit)
    YFINANCE_MAP: ClassVar[dict[str, str]] = {
        # Majors (7)
        "EUR/USD": "EURUSD=X",
        "GBP/USD": "GBPUSD=X",
        "USD/JPY": "USDJPY=X",
        "USD/CHF": "USDCHF=X",
        "USD/CAD": "USDCAD=X",
        "AUD/USD": "AUDUSD=X",
        "NZD/USD": "NZDUSD=X",
        # Minors (17)
        "EUR/GBP": "EURGBP=X",
        "EUR/JPY": "EURJPY=X",
        "EUR/CHF": "EURCHF=X",
        "EUR/AUD": "EURAUD=X",
        "EUR/CAD": "EURCAD=X",
        "GBP/JPY": "GBPJPY=X",
        "GBP/CHF": "GBPCHF=X",
        "GBP/AUD": "GBPAUD=X",
        "GBP/CAD": "GBPCAD=X",
        "GBP/NZD": "GBPNZD=X",
        "AUD/JPY": "AUDJPY=X",
        "AUD/CAD": "AUDCAD=X",
        "AUD/CHF": "AUDCHF=X",
        "AUD/NZD": "AUDNZD=X",
        "CAD/JPY": "CADJPY=X",
        "CHF/JPY": "CHFJPY=X",
        "NZD/JPY": "NZDJPY=X",
        # Exotics (optional, lower liquidity)
        "USD/SGD": "USDSGD=X",
        "USD/HKD": "USDHKD=X",
        "USD/SEK": "USDSEK=X",
        "USD/NOK": "USDNOK=X",
        "USD/MXN": "USDMXN=X",
        "USD/ZAR": "USDZAR=X",
        "EUR/SEK": "EURSEK=X",
        "EUR/NOK": "EURNOK=X",
    }

    # OANDA symbols (requires API key, no historical limit)
    OANDA_MAP: ClassVar[dict[str, str]] = {
        # Majors
        "EUR/USD": "EUR_USD",
        "GBP/USD": "GBP_USD",
        "USD/JPY": "USD_JPY",
        "USD/CHF": "USD_CHF",
        "USD/CAD": "USD_CAD",
        "AUD/USD": "AUD_USD",
        "NZD/USD": "NZD_USD",
        # Minors
        "EUR/GBP": "EUR_GBP",
        "EUR/JPY": "EUR_JPY",
        "EUR/CHF": "EUR_CHF",
        "EUR/AUD": "EUR_AUD",
        "EUR/CAD": "EUR_CAD",
        "GBP/JPY": "GBP_JPY",
        "GBP/CHF": "GBP_CHF",
        "GBP/AUD": "GBP_AUD",
        "GBP/CAD": "GBP_CAD",
        "GBP/NZD": "GBP_NZD",
        "AUD/JPY": "AUD_JPY",
        "AUD/CAD": "AUD_CAD",
        "AUD/CHF": "AUD_CHF",
        "AUD/NZD": "AUD_NZD",
        "CAD/JPY": "CAD_JPY",
        "CHF/JPY": "CHF_JPY",
        "NZD/JPY": "NZD_JPY",
        # Exotics
        "USD/SGD": "USD_SGD",
        "USD/HKD": "USD_HKD",
        "USD/SEK": "USD_SEK",
        "USD/NOK": "USD_NOK",
        "USD/MXN": "USD_MXN",
        "USD/ZAR": "USD_ZAR",
        "EUR/SEK": "EUR_SEK",
        "EUR/NOK": "EUR_NOK",
    }

    # Legacy alias for backward compatibility
    SYMBOL_MAP = YFINANCE_MAP

    _COLUMN_MAP: ClassVar[dict[str, str]] = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }

    # Twelve Data interval mapping
    _TD_INTERVAL_MAP: ClassVar[dict[str, str]] = {
        "1h": "1h",
        "30m": "30min",
        "15m": "15min",
        "5m": "5min",
        "1m": "1min",
        "1d": "1day",
    }

    # Twelve Data period-to-outputsize mapping (approximate bar counts)
    _TD_PERIOD_BARS: ClassVar[dict[str, int]] = {
        "1d": 24,       # ~1 day of hourly bars
        "2d": 192,      # ~2 days of 15m bars
        "3d": 144,      # ~3 days of 30m bars
        "5d": 120,      # ~5 days of 1h bars
        "7d": 168,
        "1mo": 720,
        "2y": 5000,
        "60d": 5000,
    }

    def __init__(self) -> None:
        self._td_api_key = os.environ.get("TWELVE_DATA_API_KEY", "")

    def _to_td_symbol(self, symbol: str) -> str:
        """Convert symbol to Twelve Data format (EUR/USD)."""
        clean = symbol.upper().replace("=X", "").replace("/", "").strip()
        return f"{clean[:3]}/{clean[3:]}"

    def _fetch_twelve_data(
        self,
        symbol: str,
        interval: str,
        period: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        """Fetch from Twelve Data API. Returns empty DataFrame on failure."""
        td_interval = self._TD_INTERVAL_MAP.get(interval)
        if not td_interval:
            return pd.DataFrame()

        td_symbol = self._to_td_symbol(symbol)
        outputsize = self._TD_PERIOD_BARS.get(period or "", 200)

        import requests
        params: dict[str, object] = {
            "symbol": td_symbol,
            "interval": td_interval,
            "apikey": self._td_api_key,
            "outputsize": outputsize,
        }
        if start:
            params["start_date"] = start
        if end:
            params["end_date"] = end

        response = requests.get(
            "https://api.twelvedata.com/time_series",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "error":
            logger.warning("Twelve Data error for %s %s: %s", td_symbol, interval, data.get("message"))
            return pd.DataFrame()

        values = data.get("values")
        if not values:
            return pd.DataFrame()

        df = pd.DataFrame(values)
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col])
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)
        else:
            df["volume"] = 0
        df = df.sort_values("datetime").set_index("datetime")
        return df[["open", "high", "low", "close", "volume"]]

    # Approximate trading bars per calendar day for each interval
    _TD_BARS_PER_DAY: ClassVar[dict[str, int]] = {
        "15min": 80,   # ~96 bars/day × 5/7 weekday ratio
        "30min": 40,
        "1h": 20,
        "1day": 1,
    }

    def _fetch_twelve_data_paginated(
        self,
        symbol: str,
        interval: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """Fetch long-range data from Twelve Data by paginating in chunks of 5000 bars."""
        import requests as req

        td_interval = self._TD_INTERVAL_MAP.get(interval)
        if not td_interval:
            return pd.DataFrame()

        bars_per_day = self._TD_BARS_PER_DAY.get(td_interval, 20)
        chunk_days = max(1, int(5000 / bars_per_day))

        td_symbol = self._to_td_symbol(symbol)
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        chunks: list[pd.DataFrame] = []

        cursor = start_dt
        while cursor < end_dt:
            chunk_end = min(cursor + timedelta(days=chunk_days), end_dt)
            logger.info(
                "Twelve Data paginated: %s %s %s → %s",
                td_symbol, td_interval, cursor.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d"),
            )
            params: dict[str, object] = {
                "symbol": td_symbol,
                "interval": td_interval,
                "apikey": self._td_api_key,
                "start_date": cursor.strftime("%Y-%m-%d"),
                "end_date": chunk_end.strftime("%Y-%m-%d"),
                "outputsize": 5000,
            }
            response = req.get("https://api.twelvedata.com/time_series", params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "error":
                msg = data.get("message", "")
                if "API credits" in msg or "rate" in msg.lower():
                    logger.warning("Twelve Data rate limit hit: %s", msg)
                    break
                logger.warning("Twelve Data error: %s", msg)
                cursor = chunk_end
                continue

            values = data.get("values")
            if values:
                df = pd.DataFrame(values)
                df["datetime"] = pd.to_datetime(df["datetime"])
                for col in ("open", "high", "low", "close"):
                    df[col] = pd.to_numeric(df[col])
                if "volume" in df.columns:
                    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)
                else:
                    df["volume"] = 0
                chunks.append(df)
                logger.info("  got %d bars", len(df))

            cursor = chunk_end

        if not chunks:
            return pd.DataFrame()

        result = pd.concat(chunks, ignore_index=True)
        result = result.drop_duplicates(subset=["datetime"], keep="last")
        result = result.sort_values("datetime").set_index("datetime")
        return result[["open", "high", "low", "close", "volume"]]

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

        Tries Twelve Data first (if API key configured), falls back to yfinance.
        Micro timeframes (1m, 5m) always use yfinance to conserve Twelve Data credits.

        Args:
            symbol: Trading pair (e.g., "EUR/USD")
            start: Start date (YYYY-MM-DD)
            end: End date (YYYY-MM-DD)
            interval: Candle interval (1h, 30m, 15m, 1d)
            period: Alternative to start/end (e.g., "60d")

        Returns:
            DataFrame with columns: open, high, low, close, volume
        """
        # Try Twelve Data for main timeframes (conserve credits by skipping 1m/5m)
        use_micro = interval in ("1m", "1min", "2m", "5m", "5min")
        if self._td_api_key and not use_micro:
            try:
                # Use paginated fetch for long date ranges (>50 days)
                if start and end and not period:
                    start_dt = datetime.strptime(start, "%Y-%m-%d")
                    end_dt = datetime.strptime(end, "%Y-%m-%d")
                    if (end_dt - start_dt).days > 50:
                        df = self._fetch_twelve_data_paginated(symbol, interval, start, end)
                        if not df.empty:
                            return df
                # Normal single-request fetch
                df = self._fetch_twelve_data(symbol, interval, period, start, end)
                if not df.empty:
                    return df
            except Exception as exc:
                logger.debug("Twelve Data fallback for %s %s: %s", symbol, interval, exc)

        # Fallback to yfinance
        yf_symbol = self._to_yfinance_symbol(symbol)

        # Use ticker.history() for intraday data, download() for daily
        if interval in ("1m", "2m", "5m", "15m", "30m", "60m", "90m",
                         "1min", "5min", "15min", "30min"):
            yf_interval = interval.replace("min", "m")
            ticker = yf.Ticker(yf_symbol)
            data = ticker.history(start=start, end=end, period=period, interval=yf_interval)
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


class OandaFetcher:
    """Fetch forex OHLCV data from OANDA API (requires API key).

    Advantages over yfinance:
    - No 60-day intraday limit
    - More accurate forex data
    - Real-time data for live trading

    Usage:
        fetcher = OandaFetcher(api_key="your-key", account_id="account-id")
        data = fetcher.fetch("EUR/USD", interval="15m", count=500)
    """

    BASE_URL_PRACTICE = "https://api-fxpractice.oanda.com/v3"
    BASE_URL_LIVE = "https://api-fxtrade.oanda.com/v3"

    INTERVAL_MAP: ClassVar[dict[str, str]] = {
        "1m": "M1",
        "5m": "M5",
        "15m": "M15",
        "30m": "M30",
        "1h": "H1",
        "4h": "H4",
        "1d": "D",
    }

    def __init__(
        self,
        api_key: str | None = None,
        account_id: str | None = None,
        practice: bool = True,
    ) -> None:
        if not HAS_HTTPX:
            raise ImportError("httpx required for OANDA support: pip install httpx")

        self.api_key = api_key
        self.account_id = account_id
        self.base_url = self.BASE_URL_PRACTICE if practice else self.BASE_URL_LIVE

    def _get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _to_oanda_instrument(self, symbol: str) -> str:
        """Convert 'EUR/USD' to 'EUR_USD'."""
        return DataFetcher.OANDA_MAP.get(symbol, symbol.replace("/", "_"))

    def fetch(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        interval: str = "1h",
        count: int | None = 500,
    ) -> pd.DataFrame:
        """Fetch OHLCV data from OANDA.

        Args:
            symbol: Trading pair (e.g., "EUR/USD")
            start: Start date (YYYY-MM-DD or ISO format)
            end: End date (YYYY-MM-DD or ISO format)
            interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 1d)
            count: Number of candles (max 5000, ignored if start/end provided)

        Returns:
            DataFrame with columns: open, high, low, close, volume
        """
        if not self.api_key:
            raise ValueError("OANDA API key required")

        instrument = self._to_oanda_instrument(symbol)
        granularity = self.INTERVAL_MAP.get(interval, "H1")

        params: dict[str, object] = {
            "price": "M",  # Midpoint candles
            "granularity": granularity,
        }

        if start and end:
            params["from"] = start
            params["to"] = end
        elif count:
            params["count"] = min(count, 5000)

        url = f"{self.base_url}/instruments/{instrument}/candles"

        with httpx.Client() as client:
            response = client.get(url, headers=self._get_headers(), params=params)
            response.raise_for_status()

        data = response.json()
        candles = data.get("candles", [])

        if not candles:
            return pd.DataFrame()

        rows = []
        for candle in candles:
            if candle.get("complete", True):
                mid = candle.get("mid", {})
                rows.append(
                    {
                        "timestamp": pd.to_datetime(candle["time"]),
                        "open": float(mid.get("o", 0)),
                        "high": float(mid.get("h", 0)),
                        "low": float(mid.get("l", 0)),
                        "close": float(mid.get("c", 0)),
                        "volume": candle.get("volume", 0),
                    }
                )

        df = pd.DataFrame(rows)
        if df.empty:
            return df

        df.set_index("timestamp", inplace=True)
        return df[["open", "high", "low", "close", "volume"]]

    async def fetch_async(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        interval: str = "1h",
        count: int | None = 500,
    ) -> pd.DataFrame:
        """Async version of fetch."""
        if not self.api_key:
            raise ValueError("OANDA API key required")

        instrument = self._to_oanda_instrument(symbol)
        granularity = self.INTERVAL_MAP.get(interval, "H1")

        params: dict[str, object] = {
            "price": "M",
            "granularity": granularity,
        }

        if start and end:
            params["from"] = start
            params["to"] = end
        elif count:
            params["count"] = min(count, 5000)

        url = f"{self.base_url}/instruments/{instrument}/candles"

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self._get_headers(), params=params)
            response.raise_for_status()

        data = response.json()
        candles = data.get("candles", [])

        if not candles:
            return pd.DataFrame()

        rows = []
        for candle in candles:
            if candle.get("complete", True):
                mid = candle.get("mid", {})
                rows.append(
                    {
                        "timestamp": pd.to_datetime(candle["time"]),
                        "open": float(mid.get("o", 0)),
                        "high": float(mid.get("h", 0)),
                        "low": float(mid.get("l", 0)),
                        "close": float(mid.get("c", 0)),
                        "volume": candle.get("volume", 0),
                    }
                )

        df = pd.DataFrame(rows)
        if df.empty:
            return df

        df.set_index("timestamp", inplace=True)
        return df[["open", "high", "low", "close", "volume"]]

    def fetch_multi_timeframe(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch data across multiple timeframes."""
        return {
            "1h": self.fetch(symbol, start, end, interval="1h"),
            "30m": self.fetch(symbol, start, end, interval="30m"),
            "15m": self.fetch(symbol, start, end, interval="15m"),
        }

    def get_bid_ask_quote(self, symbol: str) -> dict[str, float] | None:
        """Get live bid/ask quote from OANDA when credentials are available."""
        api_key = os.getenv("OANDA_API_KEY")
        if not api_key or not HAS_HTTPX:
            return None
        instrument = self.OANDA_MAP.get(symbol, symbol.replace("/", "_"))
        url = f"{OandaFetcher.BASE_URL_PRACTICE}/accounts/{os.getenv('OANDA_ACCOUNT_ID','')}/pricing"
        params = {"instruments": instrument}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.get(url, headers=headers, params=params)
                response.raise_for_status()
            payload = response.json()
            prices = payload.get("prices", [])
            if not prices:
                return None
            price = prices[0]
            bids = price.get("bids", [])
            asks = price.get("asks", [])
            if not bids or not asks:
                return None
            bid = float(bids[0]["price"])
            ask = float(asks[0]["price"])
            return {"bid": bid, "ask": ask, "spread": ask - bid}
        except Exception:
            return None
