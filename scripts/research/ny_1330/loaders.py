"""Load local OHLC only. No downloads. Paths are workspace-absolute."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from clocks import clock_lag_scan, clock_pair_passes, server_wall_to_utc

ROOT = Path("/home/yderf/Projects/trading")
MT5 = ROOT / "mt5-arch-integration"
CTRADER = ROOT / "ctrader-trading-agent"
CRYPTO = ROOT / "TRADING" / "crypto-agent"
MTA = ROOT / "manual-trading-agent"

CLOCK_ET7 = (
    "broker server wall via ET+7 → America/New_York → UTC "
    "(US DST; not Europe/Athens; not a single export offset)"
)


@dataclass
class SeriesMeta:
    key: str
    symbol: str
    timeframe: str
    source: str
    path: str
    used: bool
    n: int = 0
    tmin: str = ""
    tmax: str = ""
    years: float = 0.0
    notes: str = ""
    clock: str = ""
    extra: dict = field(default_factory=dict)


def _span(ts: pd.Series) -> tuple[str, str, float, int]:
    ts = pd.to_datetime(ts, utc=True)
    ts = ts.dropna()
    if ts.empty:
        return "", "", 0.0, 0
    tmin, tmax = ts.min(), ts.max()
    years = (tmax - tmin).total_seconds() / (365.25 * 24 * 3600)
    return str(tmin), str(tmax), round(years, 2), int(len(ts))


def _ohlc(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "spread" in out.columns:
        out["spread"] = pd.to_numeric(out["spread"], errors="coerce")
    else:
        out["spread"] = 0.0
    out = out.dropna(subset=["time_utc", "open", "high", "low", "close"])
    out = out.sort_values("time_utc").drop_duplicates("time_utc").reset_index(drop=True)
    # drop dead placeholders (dukas year-start flats)
    rng = out["high"] - out["low"]
    body = (out["close"] - out["open"]).abs()
    dead = (rng <= 0) & (body <= 0)
    # Drop placeholder flats (e.g. Dukas year-start) but keep close-only books.
    if dead.any() and not bool(dead.all()):
        out = out.loc[~dead].reset_index(drop=True)
    return out


def _attach_ambiguous(df: pd.DataFrame, utc: pd.Series) -> pd.DataFrame:
    df.attrs["ambiguous_dropped"] = int(getattr(utc, "attrs", {}).get("ambiguous_dropped", 0))
    return df


def load_fp_mt5(path: Path) -> pd.DataFrame:
    """FP Markets export: server_epoch is server wall labeled as UTC.

    Offset is per bar (ET+7), not symbol_meta server_utc_offset_sec (an
    Aug-2026 EEST snapshot that mis-times winter bars by 1h).
    """
    df = pd.read_csv(path)
    if "server_epoch" in df.columns:
        server = pd.to_datetime(
            pd.to_numeric(df["server_epoch"], errors="coerce"), unit="s", utc=True
        )
    else:
        server = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df = df.copy()
    utc = server_wall_to_utc(server)
    df["time_utc"] = utc
    return _attach_ambiguous(_ohlc(df), utc)


def load_vantage_m5(path: Path) -> pd.DataFrame:
    """Vantage server wall follows US DST, not Europe/Athens."""
    df = pd.read_csv(path)
    naive = pd.to_datetime(df["time"], format="%Y.%m.%d %H:%M", errors="coerce")
    df = df.copy()
    utc = server_wall_to_utc(naive)
    df["time_utc"] = utc
    return _attach_ambiguous(_ohlc(df), utc)


def load_xau_mixed(path: Path, timeframe: str) -> pd.DataFrame:
    """xauusd_data.csv is EET/EEST on US DST dates with a false +00:00 suffix."""
    df = pd.read_csv(path)
    df = df.loc[df["timeframe"].astype(str).str.upper() == timeframe.upper()].copy()
    raw = pd.to_datetime(df["time"], utc=True, errors="coerce")
    utc = server_wall_to_utc(raw)
    df["time_utc"] = utc
    return _attach_ambiguous(_ohlc(df), utc)


def load_dukas_h1(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    ts = pd.to_numeric(df["timestamp"], errors="coerce")
    # dukas timestamps are ms since epoch
    unit = "ms" if ts.max() > 1e12 else "s"
    df = df.copy()
    df["time_utc"] = pd.to_datetime(ts, unit=unit, utc=True)
    return _ohlc(df)


def load_binance(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.rename(
        columns={
            "open_price": "open",
            "high_price": "high",
            "low_price": "low",
            "close_price": "close",
        }
    )
    df["time_utc"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    return _ohlc(df)


def load_close_only(path: Path, ts_col: str, close_col: str = "close") -> pd.DataFrame:
    df = pd.read_csv(path)
    df["time_utc"] = pd.to_datetime(df[ts_col], utc=True, errors="coerce")
    df["close"] = pd.to_numeric(df[close_col], errors="coerce")
    df["open"] = df["close"]
    df["high"] = df["close"]
    df["low"] = df["close"]
    df["spread"] = 0.0
    return _ohlc(df)


def catalog_and_load() -> tuple[dict[str, pd.DataFrame], list[SeriesMeta], list[dict]]:
    """Load local files, then refuse any series whose clock is unconfirmed."""
    meta: list[SeriesMeta] = []
    loaded: dict[str, pd.DataFrame] = {}

    def add(key, symbol, tf, source, path, clock, notes, loader, used=True):
        p = Path(path)
        exists = p.exists()
        row = SeriesMeta(
            key=key,
            symbol=symbol,
            timeframe=tf,
            source=source,
            path=str(p),
            used=bool(used and exists),
            notes=notes if exists else "FILE MISSING — not used",
            clock=clock,
        )
        if not exists:
            meta.append(row)
            return
        df = loader(p)
        tmin, tmax, years, n = _span(df["time_utc"])
        row.n, row.tmin, row.tmax, row.years = n, tmin, tmax, years
        if row.used:
            loaded[key] = df
        row.extra["ambiguous_dropped"] = int(getattr(df, "attrs", {}).get("ambiguous_dropped", 0))
        meta.append(row)

    add(
        "US100_M5",
        "US100",
        "M5",
        "mt5-arch-integration FP Markets M5.hc export",
        MT5 / "results/us_index_data/history_US100_M5.csv",
        CLOCK_ET7,
        "Nasdaq CFD. Prior ny_cash_* screens on this file all promote=no.",
        load_fp_mt5,
    )
    add(
        "US30_M5",
        "US30",
        "M5",
        "mt5-arch-integration FP Markets M5 export",
        MT5 / "results/us_index_data/history_US30_M5.csv",
        CLOCK_ET7,
        "Dow CFD. Transfer-only in prior playbook.",
        load_fp_mt5,
    )
    add(
        "US100_H1",
        "US100",
        "H1",
        "mt5-arch-integration FP Markets H1.hc",
        MT5 / "results/us_index_data/history_US100_H1.csv",
        CLOCK_ET7,
        "Hour-rank corroboration only; too coarse for scalp fills.",
        load_fp_mt5,
        used=True,
    )
    add(
        "EURUSD_M5",
        "EURUSD",
        "M5",
        "mt5-arch-integration Vantage Export EURUSD M5 (60 months)",
        MT5 / "results/eurusd_data/history_EURUSD.csv",
        CLOCK_ET7,
        "Prior eurusd_ny_scalp_* promote=no. Server follows US DST, not Athens.",
        load_vantage_m5,
    )
    add(
        "XAUUSD_M15",
        "XAUUSD",
        "M15",
        "mt5-arch-integration Vantage xauusd_data.csv (Charts.MaxBars=100000)",
        MT5 / "xauusd_data.csv",
        CLOCK_ET7,
        "False +00:00 on EET/EEST-US-DST wall. M15 hits 100k-bar cap; starts 2022-05.",
        lambda p: load_xau_mixed(p, "M15"),
    )
    add(
        "XAUUSD_H1",
        "XAUUSD",
        "H1",
        "mt5-arch-integration Vantage xauusd_data.csv",
        MT5 / "xauusd_data.csv",
        CLOCK_ET7,
        "Same false +00:00 as M15. Used for hour-rank.",
        lambda p: load_xau_mixed(p, "H1"),
    )
    add(
        "BTCUSD_H1",
        "BTCUSD",
        "H1",
        "mt5-arch-integration FP Markets BTCUSD H1.hc",
        MT5 / "results/btc_data/history_BTCUSD_H1.csv",
        CLOCK_ET7,
        "H1 only locally — no BTC M5. Hour-rank + dummy hour trade.",
        load_fp_mt5,
    )
    add(
        "BTCUSDT_1H_MACRO",
        "BTCUSDT",
        "1h",
        "TRADING/crypto-agent data/macro_events",
        CRYPTO / "data/macro_events/BTCUSDT_1h_2021-01-01_2024-01-01.csv",
        "Binance UTC",
        "Stops 2024-01. Combined with 2026 snippet if present.",
        load_binance,
    )
    add(
        "BTCUSDT_1H_2026",
        "BTCUSDT",
        "1h",
        "TRADING/crypto-agent data",
        CRYPTO / "data/BTCUSDT_1h_2026-02-01_2026-06-10.csv",
        "Binance UTC",
        "Gap 2024-01 → 2026-02. Not stitched into one continuous book.",
        load_binance,
    )
    add(
        "ETHUSDT_15M",
        "ETHUSDT",
        "15m",
        "TRADING/crypto-agent data",
        CRYPTO / "data/ETHUSDT_15m_2024-01-01_2026-03-01.csv",
        "Binance UTC",
        "Liquid crypto extra name. Clock guard vs BTCUSDT_1H_MACRO.",
        load_binance,
    )
    add(
        "QQQ_1H",
        "QQQ",
        "1h",
        "crypto-agent tradfi yfinance proxy (close only)",
        CRYPTO / "data/tradfi/equity_risk_1h.csv",
        "Yahoo bar_open_utc (RTH)",
        "Close-only RTH |Δclose| proxy. Clock guard vs US100_H1.",
        lambda p: load_close_only(p, "bar_open_utc", "close"),
    )
    add(
        "DXY_1H",
        "DX-Y.NYB",
        "1h",
        "crypto-agent tradfi yfinance proxy (close only)",
        CRYPTO / "data/tradfi/dxy_1h.csv",
        "Yahoo bar_open_utc",
        "Close-only USD index. Clock guard vs −EURUSD_DUKAS_H1.",
        lambda p: load_close_only(p, "bar_open_utc", "close"),
    )
    add(
        "XAUUSD_DUKAS_H1",
        "XAUUSD",
        "H1",
        "ctrader-trading-agent Dukascopy",
        CTRADER / "data/dukascopy/xauusd_h1_2022-01-01_2026-03-01.csv",
        "unix ms UTC",
        "UTC-native reference for Vantage XAU admission.",
        load_dukas_h1,
    )
    add(
        "EURUSD_DUKAS_H1",
        "EURUSD",
        "H1",
        "ctrader-trading-agent Dukascopy",
        CTRADER / "data/dukascopy/eurusd_h1_2022-01-01_2026-03-01.csv",
        "unix ms UTC",
        "UTC-native reference for Vantage EURUSD admission.",
        load_dukas_h1,
    )

    # Inventory-only (not used in tests)
    for key, symbol, tf, source, path, note in [
        (
            "OIL_WTI",
            "OIL",
            "any",
            "workspace search",
            str(ROOT / "NO_LOCAL_WTI_OHLC"),
            "No WTI/CL/USOIL csv/parquet under live projects. Desk card lists OIL; data missing.",
        ),
        (
            "TICKS_US100",
            "US100",
            "tick",
            "mt5-arch tick_data",
            str(MT5 / "results/tick_data/ticks_US100_fpmarkets.csv"),
            "~36h around 2026-08-18. Too short for a 13:30 decade test.",
        ),
        (
            "OKX_CANDLES",
            "prediction-markets",
            "mixed",
            "okx-outcomes sqlite",
            str(ROOT / "okx-outcomes/data/okx_outcomes.sqlite"),
            "598k prediction-market candles, not BTC/NQ/XAU/Oil.",
        ),
        (
            "MTA_PARQUET_FX",
            "FX majors",
            "~15m?",
            "manual-trading-agent/results/cache",
            str(MTA / "results/cache"),
            "365d FX parquet present; not required (EURUSD M5 is longer). pyarrow missing on system python.",
        ),
        (
            "TRADING_EVIDENCE",
            "n/a",
            "n/a",
            "trading-evidence",
            str(ROOT / "trading-evidence"),
            "Envelope schema/validator, no OHLC history.",
        ),
    ]:
        meta.append(
            SeriesMeta(
                key=key,
                symbol=symbol,
                timeframe=tf,
                source=source,
                path=path,
                used=False,
                notes=note,
                n=0,
            )
        )

    reports = validate_loaded_clocks(loaded)
    loaded, meta = admit_validated(loaded, meta, reports)
    return loaded, meta, reports


@dataclass(frozen=True)
class ClockPair:
    """Admission pairing for the hourly log-return lag guard.

    Same-asset floors are 0.95 (true lag is 0.99+; off-lag ~0). Cross-asset
    floors are per pair — do not reuse 0.95. invert_reference flips the
    reference log returns so an inverse pair still argmaxes at lag 0.
    """

    candidate: str
    reference: str
    min_corr: float
    kind: str
    reason: str
    invert_reference: bool = False


# UTC-native yardsticks. Admitted without a pair (they *are* the pair).
CLOCK_GROUND_TRUTH = frozenset(
    {
        "EURUSD_DUKAS_H1",
        "XAUUSD_DUKAS_H1",
        "BTCUSDT_1H_MACRO",
        "BTCUSDT_1H_2026",
    }
)

# Dependency order: validate refs before any candidate that uses them.
CLOCK_PAIRS: tuple[ClockPair, ...] = (
    ClockPair(
        "EURUSD_M5",
        "EURUSD_DUKAS_H1",
        0.95,
        "same_asset",
        "Same EURUSD: Vantage M5 vs Dukas H1 UTC. C2 (Athens) peaked off lag 0 on EU/US shoulders.",
    ),
    ClockPair(
        "XAUUSD_M15",
        "XAUUSD_DUKAS_H1",
        0.95,
        "same_asset",
        "Same XAU: Vantage M15 vs Dukas H1 UTC. C1 (false +00:00) peaked at lag −3.",
    ),
    ClockPair(
        "XAUUSD_H1",
        "XAUUSD_DUKAS_H1",
        0.95,
        "same_asset",
        "Same XAU: Vantage H1 vs Dukas H1 UTC.",
    ),
    ClockPair(
        "BTCUSD_H1",
        "BTCUSDT_1H_MACRO",
        0.95,
        "same_asset",
        "Same BTC: FP H1 (ET+7) vs Binance UTC. MACRO overlap 2021-12→2024-01.",
    ),
    ClockPair(
        "DXY_1H",
        "EURUSD_DUKAS_H1",
        0.80,
        "cross_asset",
        "No DXY UTC book. DXY vs EURUSD is the textbook inverse dollar pair; "
        "negate EURUSD log returns so the argmax stays at lag 0 (raw corr ≈ −0.92).",
        invert_reference=True,
    ),
    ClockPair(
        "ETHUSDT_15M",
        "BTCUSDT_1H_MACRO",
        0.50,
        "cross_asset",
        "No ETH UTC book. Binance BTCUSDT is same-venue UTC-native; hourly ETH/BTC "
        "logrets comove (~0.70 on the Jan-2024 overlap). Floor 0.50, not 0.95.",
    ),
    ClockPair(
        "US100_M5",
        "DXY_1H",
        0.20,
        "cross_asset",
        "No Nasdaq UTC book. After DXY is confirmed vs Dukas EURUSD, US100 vs −DXY "
        "is the risk-on inverse (lag-0 ~0.33, off-lag ~0). Floor 0.20, not 0.95. "
        "Do not pair two FP index files — a shared broker offset would still look like lag 0.",
        invert_reference=True,
    ),
    ClockPair(
        "US100_H1",
        "DXY_1H",
        0.20,
        "cross_asset",
        "Same as US100_M5: Nasdaq CFD vs confirmed Yahoo DXY (inverted). Floor 0.20.",
        invert_reference=True,
    ),
    ClockPair(
        "US30_M5",
        "DXY_1H",
        0.20,
        "cross_asset",
        "No Dow UTC book. Same inverted-DXY contemporaneous check as US100. Floor 0.20.",
        invert_reference=True,
    ),
    ClockPair(
        "QQQ_1H",
        "US100_H1",
        0.35,
        "cross_asset",
        "Same Nasdaq (QQQ ETF vs US100 CFD). US100 is ET+7 and is admitted only after "
        "the DXY check. QQQ is a :30 RTH grid vs US100 :00 H1, so hourly-last corr "
        "caps ~0.47 (lag+1 residual ~0.31 is grid phase, not a second clock). Floor 0.35.",
    ),
)


def validate_loaded_clocks(
    loaded: dict[str, pd.DataFrame],
) -> list[dict]:
    """Lag-argmax guard. Series without a pair or a ground-truth flag are refused."""
    reports: list[dict] = []
    failed: set[str] = set()
    paired = {p.candidate for p in CLOCK_PAIRS}

    for key in sorted(CLOCK_GROUND_TRUTH):
        if key not in loaded:
            continue
        reports.append(
            {
                "series": key,
                "reference": None,
                "kind": "ground_truth",
                "best_lag_hours": 0,
                "correlation": 1.0,
                "threshold": None,
                "invert_reference": False,
                "n_hourly": None,
                "pass": True,
                "reason": "UTC-native yardstick (Dukascopy unix-ms or Binance time).",
            }
        )

    for pair in CLOCK_PAIRS:
        if pair.candidate not in loaded:
            continue
        if pair.reference not in loaded:
            reports.append(
                {
                    "series": pair.candidate,
                    "reference": pair.reference,
                    "kind": pair.kind,
                    "best_lag_hours": None,
                    "correlation": None,
                    "threshold": pair.min_corr,
                    "invert_reference": pair.invert_reference,
                    "n_hourly": 0,
                    "pass": False,
                    "reason": f"Reference {pair.reference} missing. {pair.reason}",
                }
            )
            failed.add(pair.candidate)
            continue
        if pair.reference in failed:
            reports.append(
                {
                    "series": pair.candidate,
                    "reference": pair.reference,
                    "kind": pair.kind,
                    "best_lag_hours": None,
                    "correlation": None,
                    "threshold": pair.min_corr,
                    "invert_reference": pair.invert_reference,
                    "n_hourly": 0,
                    "pass": False,
                    "reason": f"Reference {pair.reference} failed its own clock guard. {pair.reason}",
                }
            )
            failed.add(pair.candidate)
            continue
        scan = clock_lag_scan(
            loaded[pair.candidate],
            loaded[pair.reference],
            invert_reference=pair.invert_reference,
        )
        ok = clock_pair_passes(scan, min_corr=pair.min_corr)
        reports.append(
            {
                "series": pair.candidate,
                "reference": pair.reference,
                "kind": pair.kind,
                "best_lag_hours": scan["best_lag_hours"],
                "correlation": scan["correlation"],
                "threshold": pair.min_corr,
                "invert_reference": pair.invert_reference,
                "n_hourly": scan["n_hourly"],
                "corr_by_lag": scan["corr_by_lag"],
                "pass": bool(ok),
                "reason": pair.reason,
            }
        )
        if not ok:
            failed.add(pair.candidate)

    for key in list(loaded):
        if key in CLOCK_GROUND_TRUTH or key in paired:
            continue
        reports.append(
            {
                "series": key,
                "reference": None,
                "kind": "unpaired",
                "best_lag_hours": None,
                "correlation": None,
                "threshold": None,
                "invert_reference": False,
                "n_hourly": 0,
                "pass": False,
                "reason": "No clock pairing and not a UTC-native yardstick — refuse.",
            }
        )
        failed.add(key)

    reports.sort(key=lambda r: r["series"])
    return reports


def admit_validated(
    loaded: dict[str, pd.DataFrame],
    meta: list[SeriesMeta],
    reports: list[dict],
) -> tuple[dict[str, pd.DataFrame], list[SeriesMeta]]:
    """Drop failed series from the admitted book and mark inventory unused."""
    failed = {r["series"] for r in reports if not r["pass"]}
    for key in failed:
        loaded.pop(key, None)
    by_key = {m.key: m for m in meta}
    for key in failed:
        row = by_key.get(key)
        if row is None:
            continue
        row.used = False
        why = next(r["reason"] for r in reports if r["series"] == key)
        row.notes = f"CLOCK GUARD REFUSED — {why}"
    return loaded, meta
