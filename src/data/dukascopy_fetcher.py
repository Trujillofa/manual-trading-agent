"""Dukascopy data downloader for high-quality forex historical data.

Downloads M1 candle data from Dukascopy's public datafeed in bi5 (LZMA-compressed
binary) format, then resamples to any desired timeframe.

URL format: https://datafeed.dukascopy.com/datafeed/{SYMBOL}/{YEAR}/{MONTH-1}/{DAY}/BID_candles_min_1.bi5
Note: Dukascopy uses 0-indexed months (January = 00).

Each bi5 record is 24 bytes, big-endian:
  - time_offset (uint32): minutes from day start (0-1439)
  - open (uint32): price as integer (divide by point_value)
  - close (uint32)
  - low (uint32)
  - high (uint32)
  - volume (float32)
"""

from __future__ import annotations

import lzma
import struct
from datetime import UTC, datetime, timedelta

import pandas as pd
import requests

DUKASCOPY_BASE_URL = "https://datafeed.dukascopy.com/datafeed"

# Point values for converting integer prices to decimals.
# JPY pairs use 3 decimal places (1000), others use 5 (100000).
POINT_VALUES: dict[str, int] = {}
JPY_PAIRS = {"USDJPY", "EURJPY", "GBPJPY", "CHFJPY", "AUDJPY", "CADJPY", "NZDJPY",
             "SGDJPY", "HKDJPY", "SEKJPY", "NOKJPY", "MXNJPY", "ZARJPY"}


def _point_value(symbol: str) -> int:
    sym = symbol.upper().replace("/", "")
    if sym in JPY_PAIRS or sym.endswith("JPY"):
        return 1000
    return 100000


def _download_day(symbol: str, date: datetime) -> list[dict]:
    """Download M1 candles for a single day from Dukascopy."""
    sym = symbol.upper().replace("/", "")
    year = date.year
    month = date.month - 1  # 0-indexed
    day = date.day

    url = f"{DUKASCOPY_BASE_URL}/{sym}/{year}/{month:02d}/{day:02d}/BID_candles_min_1.bi5"

    try:
        response = requests.get(url, timeout=30)
        if response.status_code != 200 or len(response.content) == 0:
            return []
    except requests.RequestException:
        return []

    try:
        decompressed = lzma.decompress(response.content)
    except lzma.LZMAError:
        return []

    record_size = 24
    n_records = len(decompressed) // record_size
    if n_records == 0:
        return []

    pv = _point_value(sym)
    day_start = datetime(year, date.month, day, tzinfo=UTC)
    records = []

    for i in range(n_records):
        offset = i * record_size
        time_off, o, c, lo, hi, vol = struct.unpack(
            ">IIIIIf", decompressed[offset:offset + record_size]
        )
        # time_off is seconds from midnight UTC
        ts = day_start + timedelta(seconds=time_off)
        op = o / pv
        cl = c / pv
        lw = lo / pv
        hg = hi / pv

        # Skip zero-price records (market closed)
        if op == 0 and cl == 0:
            continue

        records.append({
            "datetime": ts,
            "open": op,
            "high": hg,
            "low": lw,
            "close": cl,
            "volume": round(vol, 2),
        })

    return records


def download_dukascopy_data(
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    progress_callback=None,
) -> pd.DataFrame:
    """Download M1 candle data from Dukascopy.

    Args:
        symbol: Forex pair (e.g., "EURUSD", "EUR/USD")
        start_date: Start date (timezone-aware or naive)
        end_date: End date
        progress_callback: Optional callback(fraction) for progress

    Returns:
        DataFrame with columns: datetime, open, high, low, close, volume
    """
    start = start_date.replace(tzinfo=None) if start_date.tzinfo else start_date
    end = end_date.replace(tzinfo=None) if end_date.tzinfo else end_date

    total_days = (end - start).days + 1
    all_records: list[dict] = []

    current = start
    day_num = 0
    while current <= end:
        records = _download_day(symbol, current)
        all_records.extend(records)
        current += timedelta(days=1)
        day_num += 1

        if progress_callback and day_num % 7 == 0:
            progress_callback(day_num / total_days)

    if not all_records:
        return pd.DataFrame()

    df = pd.DataFrame(all_records)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.drop_duplicates(subset=["datetime"], keep="last")
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def _resample_ohlc(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Resample OHLCV data to a different timeframe."""
    df = df.copy()
    if "datetime" in df.columns:
        df = df.set_index("datetime")

    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    if "volume" in df.columns:
        agg["volume"] = "sum"

    resampled = df.resample(freq).agg(agg)
    resampled = resampled.dropna()
    return resampled


def get_multi_timeframe_data_dukascopy(
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    timeframes: list[str] | None = None,
    progress_callback=None,
) -> dict[str, pd.DataFrame]:
    """Download M1 data from Dukascopy and resample to multiple timeframes.

    Args:
        symbol: Forex pair (e.g., "EURUSD")
        start_date: Start date
        end_date: End date
        timeframes: Target timeframes (default: ["h1", "m30", "m15"])
        progress_callback: Optional progress callback

    Returns:
        Dict mapping timeframe key to DataFrame with DatetimeIndex.
    """
    if timeframes is None:
        timeframes = ["h1", "m30", "m15"]

    m1_data = download_dukascopy_data(symbol, start_date, end_date, progress_callback)
    if m1_data.empty:
        return {}

    result: dict[str, pd.DataFrame] = {}
    freq_map = {
        "m1": "1min", "m5": "5min", "m15": "15min",
        "m30": "30min", "h1": "1h", "h4": "4h", "d1": "1D",
    }

    for tf in timeframes:
        freq = freq_map.get(tf)
        if freq is None:
            continue
        if tf == "m1":
            df = m1_data.copy()
            if "datetime" in df.columns:
                df = df.set_index("datetime")
            result["m1"] = df
        else:
            result[tf] = _resample_ohlc(m1_data, freq)

    return result
