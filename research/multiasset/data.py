"""Thin caching wrapper over dukascopy_fetcher for multi-asset daily + H4 data.

Idempotent frame cache (pickle for broad venv compatibility; parquet is a drop-in
future optimization when pyarrow is stable in the env) under
data/cache/multiasset/{SYM}_d1.pkl etc.

Reuses the M1 download + _resample_ohlc from src.data.dukascopy_fetcher (extended for metals).

For indices, the caller must ensure the quality gate (strict=) is relaxed or the
instrument calendar is handled (see Phase 0.4).
"""

from __future__ import annotations

import typing
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from src.data.dukascopy_fetcher import (
    download_dukascopy_data,
)

CACHE_ROOT = Path("data") / "cache" / "multiasset"
CACHE_ROOT.mkdir(parents=True, exist_ok=True)

# Reasonable default history depth for momentum work (IS/OOS needs years).
# Dukascopy has more; start here and backfill as needed for a given experiment.
DEFAULT_START = datetime(2018, 1, 1, tzinfo=UTC)  # ~8+ years as of 2026

# Canonical symbols for the multi-asset momentum universe (gross-first breadth).
# Metals (XAU/XAG) + the five indices we target for diversification.
# FX majors daily are added in the gross path (step 5 of the sequence).
METALS = ("XAUUSD", "XAGUSD")
INDEX_UNIVERSE = (
    "USA500",
    "USATECH",
    "DEU40",
    "GBR100",
    "JPN225",
)  # normalized names; dukascopy_fetcher.INDEXES contains the actual feed variants + .IDXUSD forms


def _is_index(symbol: str) -> bool:
    s = symbol.upper().replace("/", "")
    return s in INDEX_UNIVERSE or any(s in idx for idx in INDEX_UNIVERSE)  # tolerant match


FREQ = {
    "m1": "1min",
    "d1": "1D",
    "h4": "4h",
}


def _sym(sym: str) -> str:
    return sym.upper().replace("/", "")


def _cache_path(symbol: str, tf: str) -> Path:
    # .pkl for reliable cross-venv research caching without pyarrow friction.
    # (Parquet is noted in the execution plan as the intended format; this is a
    # compatible implementation detail until the env stabilizes.)
    return CACHE_ROOT / f"{_sym(symbol)}_{tf}.pkl"


def _resample_ohlc(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Local thin wrapper to avoid importing private symbol."""
    # Reuse the one in dukascopy_fetcher by importing the module
    from src.data import dukascopy_fetcher as dfetch  # type: ignore[attr-defined]

    if hasattr(dfetch, "_resample_ohlc"):
        return typing.cast(pd.DataFrame, dfetch._resample_ohlc(df, freq))
    # Fallback (should not happen)
    d = df.copy()
    if "datetime" in d.columns:
        d = d.set_index("datetime")
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in d:
        agg["volume"] = "sum"
    out = d.resample(freq).agg(agg).dropna()
    return typing.cast(pd.DataFrame, out)


def fetch_and_cache(
    symbol: str,
    timeframes: tuple[str, ...] = ("d1", "h4"),
    start: datetime | None = None,
    end: datetime | None = None,
    force: bool = False,
    strict: bool = False,
) -> dict[str, pd.DataFrame]:
    """Download (if needed) M1, resample to requested TFs, write cache files.

    Returns the requested frames (DatetimeIndex, OHLCV). Idempotent unless force=True.
    Uses relaxed weekday-zero gate for indices (0.4).
    """
    sym = _sym(symbol)
    start = start or DEFAULT_START
    end = end or datetime.now(UTC)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)

    result: dict[str, pd.DataFrame] = {}
    is_idx = _is_index(sym)
    # Indices get a relaxed quality gate (holidays are expected zeros).
    dl_max_rate = 0.20 if is_idx else None

    for tf in timeframes:
        cpath = _cache_path(sym, tf)
        if cpath.exists() and not force:
            df = pd.read_pickle(cpath)
            result[tf] = df
            continue

        # Need fresh M1 then resample (instrument-aware gate)
        m1, _summary = download_dukascopy_data(
            sym, start, end, strict=strict, max_weekday_zero_rate=dl_max_rate
        )
        if m1.empty:
            result[tf] = pd.DataFrame()
            continue

        if tf == "m1":
            df = m1.set_index("datetime") if "datetime" in m1.columns else m1
        else:
            freq = FREQ.get(tf, "1D")
            df = _resample_ohlc(m1, freq)

        # Ensure consistent index name and dtypes
        if isinstance(df.index, pd.DatetimeIndex):
            df.index = (
                df.index.tz_convert(UTC) if df.index.tz is not None else df.index.tz_localize(UTC)
            )
        df = df.sort_index()

        cpath.parent.mkdir(parents=True, exist_ok=True)
        # Use pickle (reliable, no native engine). Parquet is the aspirational format
        # per the execution plan; pickle is the working backend in this venv.
        df.to_pickle(cpath)
        result[tf] = df

    return result


def load_cached(symbol: str, timeframe: str = "d1") -> pd.DataFrame | None:
    """Load from cache if present, else None."""
    p = _cache_path(symbol, timeframe)
    if p.exists():
        return pd.read_pickle(p)
    return None


def cache_info(symbol: str) -> dict[str, object]:
    """Quick report of cached files and their time spans for a symbol."""
    info: dict[str, object] = {"symbol": _sym(symbol)}
    for tf in ("d1", "h4", "m1"):
        p = _cache_path(symbol, tf)
        if p.exists():
            try:
                raw = pd.read_pickle(p)
                df = raw if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw)
                if isinstance(df.index, pd.DatetimeIndex):
                    idx = df.index
                elif "datetime" in df.columns:
                    idx = pd.to_datetime(df["datetime"])
                else:
                    idx = pd.DatetimeIndex([])
                info[tf] = {
                    "path": str(p),
                    "rows": int(len(df)),
                    "start": str(getattr(idx, "min", lambda: None)()),
                    "end": str(getattr(idx, "max", lambda: None)()),
                    "last_close": float(df["close"].iloc[-1])
                    if "close" in df.columns and len(df)
                    else None,
                }
            except Exception as e:  # noqa: BLE001
                info[tf] = {"path": str(p), "error": str(e)}
        else:
            info[tf] = None
    return info


if __name__ == "__main__":
    # Convenience entrypoint for gross-first data prep.
    # Examples:
    #   python -m research.multiasset.data --symbols XAUUSD,XAGUSD --start 2016-01-01 --force
    #   python -m research.multiasset.data --symbols USA500,DEU40,GBR100 --start 2018-01-01 --force
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="XAUUSD,XAGUSD")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    syms = [s.strip() for s in args.symbols.split(",")]
    start_dt = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    for s in syms:
        print(f"Populating cache for {s} from {start_dt.date()} (force={args.force}) ...")
        frames = fetch_and_cache(s, start=start_dt, force=args.force, strict=False)
        for tf, df in frames.items():
            if df is not None and not df.empty and hasattr(df.index, "min"):
                print(f"  {tf}: {len(df)} bars, span {df.index.min()}..{df.index.max()}")
            else:
                print(f"  {tf}: empty")
    print("Done. Use research/multiasset/data.py:cache_info() or load_cached() for verification.")
