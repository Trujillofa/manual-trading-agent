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
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd
import requests

DUKASCOPY_BASE_URL = "https://datafeed.dukascopy.com/datafeed"

# Point values for converting integer prices to decimals.
# JPY pairs use 3 decimal places (1000), others use 5 (100000).
# Metals (XAUUSD/XAGUSD) on Dukascopy use 1000 (empirically verified 2026-06: gold ~4450,
# silver ~73 with pv=1000 on recent M1 bi5). Indices will require their own verified values.
POINT_VALUES: dict[str, int] = {}
JPY_PAIRS = {
    "USDJPY",
    "EURJPY",
    "GBPJPY",
    "CHFJPY",
    "AUDJPY",
    "CADJPY",
    "NZDJPY",
    "SGDJPY",
    "HKDJPY",
    "SEKJPY",
    "NOKJPY",
    "MXNJPY",
    "ZARJPY",
}

# Metals use pv=1000 on Dukascopy (see _point_value and Phase 0.2 verification).
METALS: set[str] = {"XAUUSD", "XAGUSD"}

# Index instrument symbols (Dukascopy folder names for the public bi5 feed).
# Sourced from Dukascopy range-of-markets + empirical probe (Phase 0.4).
# Common forms: USA500.IDXUSD, USATECH.IDXUSD, DEU.IDX (or DEU40), etc.
# The gate is relaxed for these because they observe exchange holidays (US, EU, JP).
INDEXES: set[str] = {
    "USA500",
    "US500",
    "USA500.IDXUSD",
    "US500.IDXUSD",
    "USATECH",
    "USATECH100",
    "USATECH.IDXUSD",
    "DEU40",
    "DEU.IDX",
    "GER40",
    "DEU40.IDXUSD",
    "GBR100",
    "UK100",
    "GBR100.IDXUSD",
    "JPN225",
    "JP225",
    "JPN225.IDXUSD",
}

# Verified point values for indices (populated after empirical probe + price sanity check,
# exactly as done for metals). Keys should be the *normalized* uppercased symbol (no /).
INDEX_POINT_VALUES: dict[str, int] = {}


class DukascopyDataQualityError(Exception):
    """Raised when data quality gates fail (e.g., >5% weekdays with 0 bars)."""

    def __init__(self, message: str, summary: FetchSummary) -> None:
        super().__init__(message)
        self.summary = summary


def _point_value(symbol: str) -> int:
    sym = symbol.upper().replace("/", "")
    if sym in JPY_PAIRS or sym.endswith("JPY"):
        return 1000
    if sym in METALS:
        return 1000
    if sym in INDEX_POINT_VALUES:
        return INDEX_POINT_VALUES[sym]
    if sym in INDEXES:
        # Will be replaced by the empirically verified value from probe (Phase 0.4).
        # Using 100 as a common default for many index CFDs until verification.
        return 100
    return 100000


OutcomeType = Literal[
    "ok",
    "http_404",
    "http_error",
    "request_exception",
    "lzma_error",
    "empty_payload",
    "zero_records",
]


@dataclass
class DayFetchResult:
    """Result of fetching a single day's data."""

    date: datetime
    outcome: OutcomeType
    bars: int
    retries: int = 0
    error_detail: str = ""


@dataclass
class FetchSummary:
    """Summary of a multi-day fetch operation."""

    symbol: str
    start_date: datetime
    end_date: datetime
    total_days: int
    outcomes: dict[str, int] = field(default_factory=dict)
    total_bars: int = 0
    weekday_zero_bar_days: int = 0
    total_weekdays: int = 0

    @property
    def weekday_zero_bar_rate(self) -> float:
        """Fraction of weekdays that returned 0 bars."""
        if self.total_weekdays == 0:
            return 0.0
        return self.weekday_zero_bar_days / self.total_weekdays

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for serialization."""
        return {
            "symbol": self.symbol,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "total_days": self.total_days,
            "outcomes": self.outcomes,
            "total_bars": self.total_bars,
            "weekday_zero_bar_days": self.weekday_zero_bar_days,
            "total_weekdays": self.total_weekdays,
            "weekday_zero_bar_rate": round(self.weekday_zero_bar_rate, 4),
        }


def _is_weekday(date: datetime) -> bool:
    """Check if date is a weekday (Mon-Fri)."""
    return date.weekday() < 5


def _append_fetch_debug_log(line: str) -> None:
    """Append a line to the fetch debug log."""
    log_path = Path("results/fetch_debug.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _download_day_with_retry(symbol: str, date: datetime, max_retries: int = 3) -> DayFetchResult:
    """Download M1 candles for a single day from Dukascopy with retry logic.

    Retries only on http_error and request_exception (not 404s, which are
    legitimate for weekends/holidays).
    """
    sym = symbol.upper().replace("/", "")
    year = date.year
    month = date.month - 1  # 0-indexed
    day = date.day

    url = f"{DUKASCOPY_BASE_URL}/{sym}/{year}/{month:02d}/{day:02d}/BID_candles_min_1.bi5"

    attempt = 0
    last_error = ""

    while attempt <= max_retries:
        attempt += 1
        try:
            response = requests.get(url, timeout=30)

            if response.status_code == 404:
                # Legitimate 404 - weekends/holidays
                result = DayFetchResult(date=date, outcome="http_404", bars=0, retries=attempt - 1)
                _append_fetch_debug_log(
                    f"{datetime.now(UTC).isoformat()}|{sym}|{date.date()}|http_404|0|{attempt - 1}"
                )
                return result

            if response.status_code != 200:
                # HTTP error - may be transient, retry
                last_error = f"HTTP {response.status_code}"
                if attempt <= max_retries:
                    time.sleep(attempt**2)  # 1s, 4s, 9s backoff
                    continue
                result = DayFetchResult(
                    date=date,
                    outcome="http_error",
                    bars=0,
                    retries=attempt - 1,
                    error_detail=last_error,
                )
                _append_fetch_debug_log(
                    f"{datetime.now(UTC).isoformat()}|{sym}|{date.date()}|http_error|0|{attempt - 1}|{last_error}"
                )
                return result

            if len(response.content) == 0:
                result = DayFetchResult(
                    date=date, outcome="empty_payload", bars=0, retries=attempt - 1
                )
                _append_fetch_debug_log(
                    f"{datetime.now(UTC).isoformat()}|{sym}|{date.date()}|empty_payload|0|{attempt - 1}"
                )
                return result

            try:
                decompressed = lzma.decompress(response.content)
            except lzma.LZMAError as e:
                result = DayFetchResult(
                    date=date,
                    outcome="lzma_error",
                    bars=0,
                    retries=attempt - 1,
                    error_detail=str(e),
                )
                _append_fetch_debug_log(
                    f"{datetime.now(UTC).isoformat()}|{sym}|{date.date()}|lzma_error|0|{attempt - 1}|{e}"
                )
                return result

            record_size = 24
            n_records = len(decompressed) // record_size
            if n_records == 0:
                result = DayFetchResult(
                    date=date, outcome="zero_records", bars=0, retries=attempt - 1
                )
                _append_fetch_debug_log(
                    f"{datetime.now(UTC).isoformat()}|{sym}|{date.date()}|zero_records|0|{attempt - 1}"
                )
                return result

            pv = _point_value(sym)
            day_start = datetime(year, date.month, day, tzinfo=UTC)
            records = []

            for i in range(n_records):
                offset = i * record_size
                time_off, o, c, lo, hi, vol = struct.unpack(
                    ">IIIIIf", decompressed[offset : offset + record_size]
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

                records.append(
                    {
                        "datetime": ts,
                        "open": op,
                        "high": hg,
                        "low": lw,
                        "close": cl,
                        "volume": round(vol, 2),
                    }
                )

            result = DayFetchResult(date=date, outcome="ok", bars=len(records), retries=attempt - 1)
            _append_fetch_debug_log(
                f"{datetime.now(UTC).isoformat()}|{sym}|{date.date()}|ok|{len(records)}|{attempt - 1}"
            )
            return result

        except requests.RequestException as e:
            # Request exception - may be transient, retry
            last_error = str(e)
            if attempt <= max_retries:
                time.sleep(attempt**2)  # 1s, 4s, 9s backoff
                continue
            result = DayFetchResult(
                date=date,
                outcome="request_exception",
                bars=0,
                retries=attempt - 1,
                error_detail=last_error,
            )
            _append_fetch_debug_log(
                f"{datetime.now(UTC).isoformat()}|{sym}|{date.date()}|request_exception|0|{attempt - 1}|{last_error}"
            )
            return result

    # Should never reach here, but return error if it does
    return DayFetchResult(
        date=date,
        outcome="request_exception",
        bars=0,
        retries=max_retries,
        error_detail=last_error,
    )


def _download_day(symbol: str, date: datetime) -> list[dict[str, object]]:
    """Download M1 candles for a single day from Dukascopy.

    DEPRECATED: Use _download_day_with_retry for structured results.
    Kept for backward compatibility.
    """
    result = _download_day_with_retry(symbol, date, max_retries=0)
    if result.outcome == "ok":
        # Re-fetch without structured result for compatibility
        # (inefficient but maintains exact behavior)
        return _download_day_raw(symbol, date)
    return []


def _download_day_raw(symbol: str, date: datetime) -> list[dict[str, object]]:
    """Raw download without structured result (for backward compatibility)."""
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
            ">IIIIIf", decompressed[offset : offset + record_size]
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

        records.append(
            {
                "datetime": ts,
                "open": op,
                "high": hg,
                "low": lw,
                "close": cl,
                "volume": round(vol, 2),
            }
        )

    return records


def download_dukascopy_data(
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    progress_callback=None,
    strict: bool = True,
    max_weekday_zero_rate: float | None = None,
) -> tuple[pd.DataFrame, FetchSummary]:
    """Download M1 candle data from Dukascopy with structured logging.

    Args:
        symbol: Instrument (e.g. "EURUSD", "XAUUSD", "USA500").
        start_date: Start date (timezone-aware or naive)
        end_date: End date
        progress_callback: Optional callback(fraction) for progress
        strict: If True, applies the weekday zero-bar quality gate.
        max_weekday_zero_rate: Override for the allowed fraction of weekdays with
            zero bars (instrument-aware). If None, uses 0.05 for FX/metals and
            ~0.18 for known indices (they have exchange holidays that are
            legitimate zero-bar weekdays). See Phase 0.4.

    Returns:
        Tuple of (DataFrame ..., FetchSummary ...)

    Raises:
        DukascopyDataQualityError: If strict and rate exceeds the (instrument) threshold.
    """
    start = start_date.replace(tzinfo=None) if start_date.tzinfo else start_date
    end = end_date.replace(tzinfo=None) if end_date.tzinfo else end_date

    total_days = (end - start).days + 1
    all_records: list[dict[str, object]] = []
    day_results: list[DayFetchResult] = []

    current = start
    day_num = 0
    while current <= end:
        result = _download_day_with_retry(symbol, current, max_retries=3)
        day_results.append(result)

        if result.outcome == "ok":
            # Fetch the actual records for this day
            records = _download_day_raw(symbol, current)
            all_records.extend(records)

        current += timedelta(days=1)
        day_num += 1

        if progress_callback and day_num % 7 == 0:
            progress_callback(day_num / total_days)

    # Build summary
    outcomes: dict[str, int] = {}
    weekday_zero_bar_days = 0
    total_weekdays = 0

    for result in day_results:
        outcomes[result.outcome] = outcomes.get(result.outcome, 0) + 1

        if _is_weekday(result.date):
            total_weekdays += 1
            if result.bars == 0 and result.outcome != "http_404":
                # 404s are legitimate (weekends/holidays), but other 0-bar outcomes
                # on weekdays indicate data quality issues
                weekday_zero_bar_days += 1

    summary = FetchSummary(
        symbol=symbol,
        start_date=start,
        end_date=end,
        total_days=total_days,
        outcomes=outcomes,
        total_bars=len(all_records),
        weekday_zero_bar_days=weekday_zero_bar_days,
        total_weekdays=total_weekdays,
    )

    # Data quality gate (instrument-aware for indices)
    if max_weekday_zero_rate is None:
        sym_norm = symbol.upper().replace("/", "")
        max_weekday_zero_rate = 0.18 if sym_norm in INDEXES else 0.05

    if strict and summary.weekday_zero_bar_rate > max_weekday_zero_rate:
        raise DukascopyDataQualityError(
            f"Data quality gate failed: {summary.weekday_zero_bar_rate:.1%} of weekdays "
            f"({weekday_zero_bar_days}/{total_weekdays}) returned 0 bars. "
            f"Threshold: {max_weekday_zero_rate:.0%}. Outcomes: {outcomes}",
            summary,
        )

    if not all_records:
        return pd.DataFrame(), summary

    df = pd.DataFrame(all_records)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.drop_duplicates(subset=["datetime"], keep="last")
    df = df.sort_values("datetime").reset_index(drop=True)
    return df, summary


def _resample_ohlc(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Resample OHLCV data to a different timeframe."""
    import typing

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
    return typing.cast(pd.DataFrame, resampled)


def get_multi_timeframe_data_dukascopy(
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    timeframes: list[str] | None = None,
    progress_callback=None,
    strict: bool = True,
    max_weekday_zero_rate: float | None = None,
) -> tuple[dict[str, pd.DataFrame], FetchSummary]:
    """Download M1 data from Dukascopy and resample to multiple timeframes.

    Args:
        symbol: Instrument code.
        start_date / end_date: range
        timeframes: e.g. ["d1", "h4"]
        strict / max_weekday_zero_rate: forwarded to download_dukascopy_data
            (instrument-aware relaxed gate for indices).
    """
    if timeframes is None:
        timeframes = ["h1", "m30", "m15"]

    m1_data, summary = download_dukascopy_data(
        symbol,
        start_date,
        end_date,
        progress_callback,
        strict=strict,
        max_weekday_zero_rate=max_weekday_zero_rate,
    )
    if m1_data.empty:
        return {}, summary

    result: dict[str, pd.DataFrame] = {}
    freq_map = {
        "m1": "1min",
        "m5": "5min",
        "m15": "15min",
        "m30": "30min",
        "h1": "1h",
        "h4": "4h",
        "d1": "1D",
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

    return result, summary
