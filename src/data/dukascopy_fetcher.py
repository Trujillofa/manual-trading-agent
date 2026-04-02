"""Dukascopy data downloader for high-quality forex historical data."""

from __future__ import annotations

import gzip
import os
from datetime import datetime, timedelta
from io import BytesIO
from typing import Literal
from zoneinfo import ZoneInfo

import pandas as pd
import requests

# Dukascopy URL format
DUKASCOPY_BASE_URL = "https://datafeed.dukascopy.com/datafeed"
INTERVALS = {
    "tick": "TICK",
    "m1": "M1",
    "h1": "H1",
}
INTERVAL_MINUTES = {
    "m1": 1,
    "h1": 60,
}


def download_dukascopy_data(
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    interval: Literal["tick", "m1", "h1"] = "h1",
    bid_ask: Literal["bid", "ask"] = "bid",
    progress_callback=None,
) -> pd.DataFrame:
    """Download historical forex data from Dukascopy.
    
    Args:
        symbol: Forex pair (e.g., "EURUSD", "GBPUSD")
        start_date: Start date
        end_date: End date
        interval: Time interval (tick, m1, h1)
        bid_ask: Bid or ask side
        progress_callback: Optional callback for progress updates
        
    Returns:
        DataFrame with OHLCV data (and spread for tick data)
    """
    symbol_upper = symbol.upper().replace("/", "")
    interval_code = INTERVALS.get(interval, "H1")
    
    all_data = []
    current_date = start_date.replace(tzinfo=None)
    end_date_naive = end_date.replace(tzinfo=None) if end_date.tzinfo else end_date
    
    total_days = (end_date_naive - current_date).days
    processed_days = 0
    
    while current_date <= end_date_naive:
        # Build URL
        year = current_date.year
        month = current_date.month
        
        if interval == "tick":
            # Tick data URL format: /{YEAR}/{MONTH:02d}/{SYMBOL}_T_{BIDASK}.zip
            url = f"{DUKASCOPY_BASE_URL}/{year}/{month:02d}/{interval_code}/{symbol}.zip"
        else:
            # Bar data URL format: /{YEAR}/{MONTH:02d}/{SYMBOL}_{INTERVAL}_{BIDASK}.zip
            url = f"{DUKASCOPY_BASE_URL}/{year}/{month:02d}/{interval_code}/{symbol.lower()}/{symbol_upper}_{interval_code}_{bid_ask.upper()}.zip"
        
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                # Decompress and parse
                df = _parse_dukascopy_data(response.content, interval)
                if df is not None and not df.empty:
                    all_data.append(df)
        except requests.RequestException:
            pass  # Skip missing data
        
        current_date = current_date + timedelta(days=1)
        processed_days += 1
        
        if progress_callback and processed_days % 7 == 0:
            progress_callback(processed_days / total_days)
    
    if not all_data:
        return pd.DataFrame()
    
    result = pd.concat(all_data, ignore_index=True)
    result = result.drop_duplicates(subset=["datetime"], keep="last")
    result = result.sort_values("datetime").reset_index(drop=True)
    
    return result


def _parse_dukascopy_data(content: bytes, interval: str) -> pd.DataFrame | None:
    """Parse Dukascopy compressed data."""
    try:
        # Decompress gzip
        decompressed = gzip.decompress(content)
        lines = decompressed.decode("utf-8").strip().split("\n")
        
        if interval == "tick":
            # Tick format: timestamp, ask, bid, volume
            data = []
            for line in lines:
                parts = line.split(",")
                if len(parts) >= 4:
                    data.append({
                        "datetime": pd.to_datetime(float(parts[0]), unit="ms"),
                        "ask": float(parts[1]),
                        "bid": float(parts[2]),
                        "volume": float(parts[3]),
                    })
            df = pd.DataFrame(data)
            df["mid"] = (df["ask"] + df["bid"]) / 2
            df["spread"] = df["ask"] - df["bid"]
            return df
        else:
            # Bar format: timestamp, open, high, low, close, volume
            data = []
            for line in lines:
                parts = line.split(",")
                if len(parts) >= 6:
                    data.append({
                        "datetime": pd.to_datetime(float(parts[0]), unit="ms"),
                        "open": float(parts[1]),
                        "high": float(parts[2]),
                        "low": float(parts[3]),
                        "close": float(parts[4]),
                        "volume": float(parts[5]),
                    })
            return pd.DataFrame(data)
    except Exception:
        return None


def download_histdata_csv(
    symbol: str,
    year: int,
    month: int,
    data_dir: str = "/tmp/forex_data",
) -> pd.DataFrame | None:
    """Download historical data from HistData.com format.
    
    HistData provides free M1 data in CSV format.
    URL format: https://www.histdata.com/download-free-forex-data/?/meta/
    
    Args:
        symbol: Forex pair (e.g., "EURUSD")
        year: Year to download
        month: Month to download
        data_dir: Directory to save data
        
    Returns:
        DataFrame with M1 data
    """
    # HistData requires manual download from browser
    # For automated use, we'd need to use their FTP or direct links
    # This is a placeholder for the CSV parsing once downloaded
    
    os.makedirs(data_dir, exist_ok=True)
    filename = f"{data_dir}/{symbol}_{year}_{month:02d}.csv"
    
    if os.path.exists(filename):
        return pd.read_csv(filename, parse_dates=["datetime"])
    
    return None


def get_multi_timeframe_data_dukascopy(
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    timeframes: list[str] = ["h1", "m30", "m15"],
) -> dict[str, pd.DataFrame]:
    """Download multi-timeframe data from Dukascopy.
    
    Args:
        symbol: Forex pair
        start_date: Start date
        end_date: End date
        timeframes: List of timeframes to download
        
    Returns:
        Dictionary mapping timeframe to DataFrame
    """
    result = {}
    
    # Download M1 data (highest resolution needed)
    m1_data = download_dukascopy_data(symbol, start_date, end_date, interval="m1")
    
    if m1_data.empty:
        return result
    
    result["m1"] = m1_data
    
    # Resample to other timeframes
    for tf in timeframes:
        if tf == "m1":
            continue
        
        if tf == "m5":
            result["m5"] = _resample_ohlc(m1_data, "5min")
        elif tf == "m15":
            result["m15"] = _resample_ohlc(m1_data, "15min")
        elif tf == "m30":
            result["m30"] = _resample_ohlc(m1_data, "30min")
        elif tf == "h1":
            result["h1"] = _resample_ohlc(m1_data, "1h")
        elif tf == "h4":
            result["h4"] = _resample_ohlc(m1_data, "4h")
        elif tf == "d1":
            result["d1"] = _resample_ohlc(m1_data, "1D")
    
    return result


def _resample_ohlc(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Resample OHLCV data to a different timeframe."""
    df = df.copy()
    df = df.set_index("datetime")
    
    ohlc = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    
    if "volume" in df.columns:
        ohlc["volume"] = "sum"
    
    if "spread" in df.columns:
        ohlc["spread"] = "mean"
    
    resampled = df.resample(freq).agg(ohlc)
    resampled = resampled.dropna()
    resampled = resampled.reset_index()
    
    return resampled