#!/usr/bin/env python3
"""Pivot Point entry strategy backtester — v2 (weekly + session + Camarilla).

Three entry types, each combined with MTF RSI alignment (1h+30m+15m < 30 or > 70),
SMA(50) gate, and ADX(<25) gate:

  WEEKLY    — standard pivots from previous week's H/L/C (S1/S12/S123)
  SESSION   — session open price (London 07:00 / NY 13:00 UTC) as S/R level
  CAMARILLA — Camarilla H3/H4 (resistance) and L3/L4 (support)

Bounce definition: bar wick touches the level zone; close recovers back out.

Causality: closed-bar signals, next-bar-open fills, stop-first same-bar exits.
Sweep ranking uses the chronological develop window only (first 65%); holdout
is unused for selection.

Clock: bar timestamps are UTC. SESSION entries use ``SESSION_WINDOWS_UTC``
(London 07–17, NY 13–22) on the bar's UTC hour, not broker-server time.
Session open is the 15m open at exactly 07:00 / 13:00 UTC.

Cost book: 2 pip spread + 2 pip slippage + $3/side commission (frozen).

Offline only: not a live-go or promote path. No broker orders are sent.

All RSI/ADX/SMA/pivots pre-computed once per pair (O(log n) asof lookups
in the inner loop) to keep runtime under 3 minutes per pair.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.backtest.cost_book import CostBook, pip_size_for_pair
from src.data.dukascopy_fetcher import get_multi_timeframe_data_dukascopy
from src.indicators.adx import calculate_adx
from src.indicators.pivot_points import (
    SESSION_WINDOWS_UTC,
    build_camarilla_map,
    build_daily_pivot_map,
    build_session_open_map,
    build_weekly_pivot_map,
)
from src.indicators.rsi import calculate_rsi
from src.indicators.sma import calculate_sma

ALL_PAIRS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "USD/CHF",
    "USD/CAD",
    "AUD/USD",
    "NZD/USD",
    "EUR/JPY",
    "GBP/JPY",
    "AUD/JPY",
    "NZD/JPY",
    "CHF/JPY",
    "CAD/JPY",
    "EUR/GBP",
    "EUR/AUD",
    "EUR/CAD",
    "EUR/NZD",
    "EUR/CHF",
    "GBP/AUD",
    "GBP/CAD",
    "GBP/NZD",
    "GBP/CHF",
    "AUD/CAD",
    "AUD/NZD",
    "AUD/CHF",
    "NZD/CAD",
    "NZD/CHF",
    "CAD/CHF",
]

# Level sets per entry type
WEEKLY_LEVEL_SETS: dict[str, tuple[list[str], list[str]]] = {
    "S1": (["s1"], ["r1"]),
    "S12": (["s1", "s2"], ["r1", "r2"]),
    "S123": (["s1", "s2", "s3"], ["r1", "r2", "r3"]),
}

CAMARILLA_LEVEL_SETS: dict[str, tuple[list[str], list[str]]] = {
    "L3H3": (["l3"], ["h3"]),
    "L4H4": (["l4"], ["h4"]),
    "L34H34": (["l3", "l4"], ["h3", "h4"]),
}

SESSION_SETS = ["london", "ny", "both"]
IS_FRACTION = 0.65


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TradeRecord:
    pair: str
    direction: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    exit_reason: str
    bars_held: int
    pivot_level: str
    entry_time: object = None


@dataclass
class ConfigResult:
    pair: str
    config_label: str
    entry_type: str
    level_set: str
    proximity_pips: float
    confirm_bars: int
    tp_mult: float
    sl_mult: float
    trades: int = 0
    wins: int = 0
    losses: int = 0
    timeouts: int = 0
    win_rate: float = 0.0
    total_pnl_pct: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_bars_held: float = 0.0
    trades_list: list[TradeRecord] = field(default_factory=list)


@dataclass
class PairCache:
    highs: list[float]
    lows: list[float]
    closes: list[float]
    opens: list[float]
    index_15m: list[object]
    rsi_15m: list[float | None]
    rsi_1h_series: pd.Series
    rsi_30m_series: pd.Series
    adx_1h_series: pd.Series
    sma_1h_series: pd.Series | None
    sma_30m_series: pd.Series | None
    sma_15m_list: list[float | None]
    close_1h_series: pd.Series
    close_30m_series: pd.Series
    daily_pivot_map: dict[object, dict[str, float]]
    weekly_pivot_map: dict[object, dict[str, float]]
    camarilla_map: dict[object, dict[str, float]]
    session_open_map: dict[tuple[object, str], float]
    pip_size: float
    atr: list[float | None]


# ---------------------------------------------------------------------------
# Pre-computation
# ---------------------------------------------------------------------------


def _precompute_rsi(df: pd.DataFrame, period: int = 14, window: int = 50) -> pd.Series:
    closes = df["close"].tolist()
    vals = [
        (lambda r: float("nan") if r is None else r)(
            calculate_rsi(closes[max(0, i - window) : i + 1], period)
        )
        for i in range(len(closes))
    ]
    return pd.Series(vals, index=df.index, dtype=float)


def _precompute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    closes = df["close"].tolist()
    lb = period * 3
    vals: list[float] = []
    for i in range(len(closes)):
        if i < lb:
            vals.append(float("nan"))
            continue
        adx = calculate_adx(
            highs[i - lb : i + 1], lows[i - lb : i + 1], closes[i - lb : i + 1], period
        )
        vals.append(float("nan") if adx is None else adx)
    return pd.Series(vals, index=df.index, dtype=float)


def _precompute_atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> list[float | None]:
    result: list[float | None] = []
    for i in range(len(closes)):
        if i < period:
            result.append(None)
            continue
        trs = [
            max(highs[j] - lows[j], abs(highs[j] - closes[j - 1]), abs(lows[j] - closes[j - 1]))
            for j in range(i - period + 1, i + 1)
        ]
        result.append(sum(trs) / period)
    return result


def _sma_series(df: pd.DataFrame, period: int) -> pd.Series:
    assert callable(calculate_sma)
    return cast(pd.Series, df["close"].rolling(window=period, min_periods=period).mean())


def build_pair_cache(
    pair: str,
    data_1h: pd.DataFrame,
    data_30m: pd.DataFrame,
    data_15m: pd.DataFrame,
    sma_period: int,
) -> PairCache:
    highs = data_15m["high"].tolist()
    lows = data_15m["low"].tolist()
    closes = data_15m["close"].tolist()
    opens = data_15m["open"].tolist() if "open" in data_15m.columns else closes

    rsi_15m = [calculate_rsi(closes[max(0, i - 50) : i + 1], 14) for i in range(len(closes))]

    sma_1h_s = sma_30m_s = None
    sma_15m_list: list[float | None] = []
    if sma_period > 0:
        sma_1h_s = _sma_series(data_1h, sma_period)
        sma_30m_s = _sma_series(data_30m, sma_period)
        raw = data_15m["close"].rolling(window=sma_period, min_periods=sma_period).mean()
        sma_15m_list = [None if pd.isna(v) else float(v) for v in raw]

    return PairCache(
        highs=highs,
        lows=lows,
        closes=closes,
        opens=opens,
        index_15m=list(data_15m.index),
        rsi_15m=rsi_15m,
        rsi_1h_series=_precompute_rsi(data_1h),
        rsi_30m_series=_precompute_rsi(data_30m),
        adx_1h_series=_precompute_adx(data_1h),
        sma_1h_series=sma_1h_s,
        sma_30m_series=sma_30m_s,
        sma_15m_list=sma_15m_list,
        close_1h_series=cast(pd.Series, data_1h["close"]),
        close_30m_series=cast(pd.Series, data_30m["close"]),
        daily_pivot_map=build_daily_pivot_map(data_1h),
        weekly_pivot_map=build_weekly_pivot_map(data_1h),
        camarilla_map=build_camarilla_map(data_1h),
        session_open_map=build_session_open_map(data_15m),
        pip_size=pip_size_for_pair(pair),
        atr=_precompute_atr(highs, lows, closes),
    )


# ---------------------------------------------------------------------------
# Core backtest loop
# ---------------------------------------------------------------------------


def run_config(
    pair: str,
    cache: PairCache,
    entry_type: str,  # "WEEKLY" | "SESSION" | "CAMARILLA"
    level_set: str,  # depends on entry_type
    proximity_pips: float,
    confirm_bars: int,
    tp_mult: float,
    sl_mult: float,
    rsi_ob: float = 70.0,
    rsi_os: float = 30.0,
    adx_threshold: float = 25.0,
    sma_period: int = 50,
    max_hold_bars: int = 16,
    warmup: int = 80,
    spread_pips: float = 2.0,
    slippage_pips: float = 2.0,
    commission_per_order: float = 3.0,
) -> ConfigResult:
    label = f"{entry_type}_{level_set}_prox{proximity_pips:g}_c{confirm_bars}_tp{tp_mult:g}_sl{sl_mult:g}"
    result = ConfigResult(
        pair=pair,
        config_label=label,
        entry_type=entry_type,
        level_set=level_set,
        proximity_pips=proximity_pips,
        confirm_bars=confirm_bars,
        tp_mult=tp_mult,
        sl_mult=sl_mult,
    )

    prox = proximity_pips * cache.pip_size
    costs = CostBook(
        spread_pips=spread_pips,
        slippage_pips=slippage_pips,
        commission_usd_per_lot_side=commission_per_order,
    )
    pip = cache.pip_size
    highs = cache.highs
    lows = cache.lows
    closes = cache.closes
    opens = cache.opens

    balance = 10_000.0
    peak = balance
    max_dd_pct = 0.0

    position: Literal["buy", "sell", None] = None
    entry_price = tp = sl = 0.0
    entry_idx = 0
    entry_level_name = ""
    trade_pnls: list[float] = []
    bars_held_list: list[int] = []
    timeout_count = 0

    alignment_start: int | None = None
    alignment_direction: str | None = None
    pending_signal: Literal["buy", "sell"] | None = None
    pending_atr = 0.0
    pending_level = ""

    for i in range(warmup, len(closes)):
        ts = cast(pd.Timestamp, cache.index_15m[i])
        close = closes[i]
        high_val = highs[i]
        low_val = lows[i]
        open_price = opens[i]

        if pending_signal is not None and position is None:
            position = pending_signal
            entry_price = costs.entry_fill(open_price, position, pip)
            entry_idx = i
            entry_level_name = pending_level
            if position == "buy":
                tp = entry_price + pending_atr * tp_mult
                sl = entry_price - pending_atr * sl_mult
            else:
                tp = entry_price - pending_atr * tp_mult
                sl = entry_price + pending_atr * sl_mult
            pending_signal = None

        rsi_15 = cache.rsi_15m[i]
        if rsi_15 is None:
            continue
        rsi_1h_raw = cache.rsi_1h_series.asof(ts)
        rsi_30m_raw = cache.rsi_30m_series.asof(ts)
        if pd.isna(rsi_1h_raw) or pd.isna(rsi_30m_raw):
            continue
        rsi_1h = float(rsi_1h_raw)
        rsi_30 = float(rsi_30m_raw)

        atr = cache.atr[i]
        if atr is None or atr <= 0:
            continue

        all_oversold = rsi_1h < rsi_os and rsi_30 < rsi_os and rsi_15 < rsi_os
        all_overbought = rsi_1h > rsi_ob and rsi_30 > rsi_ob and rsi_15 > rsi_ob
        aligned = all_oversold or all_overbought
        current_dir = "buy" if all_oversold else ("sell" if all_overbought else None)

        if aligned and current_dir:
            if alignment_direction == current_dir and alignment_start is not None:
                bars_since = i - alignment_start
            else:
                alignment_start = i
                alignment_direction = current_dir
                bars_since = 0
        else:
            alignment_start = None
            alignment_direction = None
            bars_since = 0

        within_window = confirm_bars == 0 or bars_since <= confirm_bars

        # Manage open position
        if position is not None:
            exit_price = None
            exit_reason = ""
            if position == "buy":
                if low_val <= sl:
                    exit_price, exit_reason = sl, "sl"
                elif high_val >= tp:
                    exit_price, exit_reason = tp, "tp"
            else:
                if high_val >= sl:
                    exit_price, exit_reason = sl, "sl"
                elif low_val <= tp:
                    exit_price, exit_reason = tp, "tp"

            if exit_price is None and i - entry_idx >= max_hold_bars:
                exit_price, exit_reason = close, "timeout"
                timeout_count += 1

            if exit_price is not None:
                filled_exit = costs.exit_fill(exit_price, position, pip)
                raw_move = (
                    (filled_exit - entry_price) / entry_price
                    if position == "buy"
                    else (entry_price - filled_exit) / entry_price
                )
                commission_pct = costs.round_trip_commission_usd() / balance if balance > 0 else 0.0
                pnl_pct = raw_move - commission_pct
                balance += balance * pnl_pct
                trade_pnls.append(pnl_pct)
                bars_held_list.append(i - entry_idx)
                peak = max(peak, balance)
                dd = ((peak - balance) / peak) * 100 if peak > 0 else 0.0
                max_dd_pct = max(max_dd_pct, dd)
                result.trades_list.append(
                    TradeRecord(
                        pair=pair,
                        direction=position,
                        entry_price=entry_price,
                        exit_price=filled_exit,
                        pnl_pct=pnl_pct * 100,
                        exit_reason=exit_reason,
                        bars_held=i - entry_idx,
                        pivot_level=entry_level_name,
                        entry_time=cache.index_15m[entry_idx],
                    )
                )
                position = None

        if position is not None or not aligned or not within_window:
            continue

        # ADX filter
        if adx_threshold > 0:
            adx_raw = cache.adx_1h_series.asof(ts)
            if not pd.isna(adx_raw) and float(adx_raw) >= adx_threshold:
                continue

        # SMA gate
        if sma_period > 0 and cache.sma_1h_series is not None and cache.sma_30m_series is not None:
            s1h = cache.sma_1h_series.asof(ts)
            s30m = cache.sma_30m_series.asof(ts)
            s15m = cache.sma_15m_list[i] if i < len(cache.sma_15m_list) else None
            c1h_raw = cache.close_1h_series.asof(ts)
            c30m_raw = cache.close_30m_series.asof(ts)
            if any(pd.isna(v) for v in (s1h, s30m, c1h_raw, c30m_raw)) or s15m is None:
                continue
            c1h = float(c1h_raw)
            c30m = float(c30m_raw)
            if all_oversold and not (c1h < float(s1h) and c30m < float(s30m) and close < s15m):
                continue
            if all_overbought and not (c1h > float(s1h) and c30m > float(s30m) and close > s15m):
                continue

        # ---- Entry level lookup ----
        bar_date = ts.date()
        current_signal: Literal["buy", "sell", None] = None
        matched_level = ""

        if entry_type == "WEEKLY":
            levels = cache.weekly_pivot_map.get(bar_date)
            if levels is None:
                continue
            support_keys, resistance_keys = WEEKLY_LEVEL_SETS[level_set]
            if all_oversold:
                for key in support_keys:
                    lv = levels[key]
                    if low_val <= lv + prox and close >= lv - prox:
                        current_signal = "buy"
                        matched_level = key
                        break
            elif all_overbought:
                for key in resistance_keys:
                    lv = levels[key]
                    if high_val >= lv - prox and close <= lv + prox:
                        current_signal = "sell"
                        matched_level = key
                        break

        elif entry_type == "SESSION":
            hour = ts.hour
            # Determine which session open prices are active at this bar
            active_sessions: list[str]
            if level_set == "london":
                active_sessions = ["london"]
            elif level_set == "ny":
                active_sessions = ["ny"]
            else:  # "both"
                active_sessions = ["london", "ny"]

            for session in active_sessions:
                win_start, win_end = SESSION_WINDOWS_UTC[session]
                if not (win_start <= hour < win_end):
                    continue
                open_price = cache.session_open_map.get((bar_date, session))
                if open_price is None:
                    continue
                if all_oversold and low_val <= open_price + prox and close >= open_price - prox:
                    current_signal = "buy"
                    matched_level = f"{session}_open"
                    break
                if all_overbought and high_val >= open_price - prox and close <= open_price + prox:
                    current_signal = "sell"
                    matched_level = f"{session}_open"
                    break

        elif entry_type == "CAMARILLA":
            levels = cache.camarilla_map.get(bar_date)
            if levels is None:
                continue
            support_keys, resistance_keys = CAMARILLA_LEVEL_SETS[level_set]
            if all_oversold:
                for key in support_keys:
                    lv = levels[key]
                    if low_val <= lv + prox and close >= lv - prox:
                        current_signal = "buy"
                        matched_level = key
                        break
            elif all_overbought:
                for key in resistance_keys:
                    lv = levels[key]
                    if high_val >= lv - prox and close <= lv + prox:
                        current_signal = "sell"
                        matched_level = key
                        break

        if current_signal is None:
            continue

        if pending_signal is None and i + 1 < len(closes):
            pending_signal = current_signal
            pending_atr = atr
            pending_level = matched_level

    wins = sum(1 for p in trade_pnls if p > 0)
    losses = len(trade_pnls) - wins
    gross_win = sum(p for p in trade_pnls if p > 0)
    gross_loss = sum(abs(p) for p in trade_pnls if p <= 0)

    result.trades = len(trade_pnls)
    result.wins = wins
    result.losses = losses
    result.timeouts = timeout_count
    result.win_rate = wins / len(trade_pnls) if trade_pnls else 0.0
    result.total_pnl_pct = ((balance - 10_000.0) / 10_000.0) * 100
    result.profit_factor = (
        gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    )
    result.avg_win = gross_win / wins if wins else 0.0
    result.avg_loss = gross_loss / losses if losses else 0.0
    result.max_drawdown_pct = max_dd_pct
    result.avg_bars_held = sum(bars_held_list) / len(bars_held_list) if bars_held_list else 0.0
    return result


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def _fetch_yfinance(pair: str, days: int) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    symbol = pair.replace("/", "") + "=X"
    result: dict[str, pd.DataFrame] = {}
    for interval, key in [("1h", "1h"), ("30m", "30m"), ("15m", "15m")]:
        try:
            df = yf.Ticker(symbol).history(period=f"{days}d", interval=interval)
            if df.empty:
                continue
            df.columns = [c.lower() for c in df.columns]
            for col in ["adj close", "dividends", "stock splits", "capital gains"]:
                if col in df.columns:
                    df = df.drop(columns=[col])
            df.index = pd.to_datetime(df.index, utc=True)
            result[key] = df.sort_index()
        except Exception as e:
            print(f"    {key}: error — {e}")
    return result


def _fetch_dukascopy(pair: str, days: int) -> dict[str, pd.DataFrame]:
    end_date = datetime.now(UTC)
    start_date = end_date - timedelta(days=days)
    mtf, _ = get_multi_timeframe_data_dukascopy(
        pair, start_date, end_date, timeframes=["h1", "m30", "m15"]
    )
    return {{"h1": "1h", "m30": "30m", "m15": "15m"}.get(k, k): v for k, v in mtf.items()}


def fetch_pair(pair: str, days: int, source: str) -> dict[str, pd.DataFrame] | None:
    try:
        data = _fetch_yfinance(pair, days) if source == "yfinance" else _fetch_dukascopy(pair, days)
        if any(tf not in data or data[tf].empty for tf in ["1h", "30m", "15m"]):
            print("    SKIPPED: incomplete data")
            return None
        n15 = len(data["15m"])
        print(f"    OK: {n15} bars on 15m ({n15 * 15 / 60 / 24:.0f} days)")
        return data
    except Exception as e:
        print(f"    ERROR: {e}")
        return None


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _trade_in_holdout(entry_time: object, cutoff: pd.Timestamp) -> bool:
    if entry_time is None:
        return False
    return pd.Timestamp(entry_time) > cutoff


def develop_metrics(
    results: list[ConfigResult],
    cutoffs: dict[str, pd.Timestamp],
    *,
    holdout: bool,
) -> tuple[int, float, float]:
    """Return develop/holdout trade count, compounded pnl%, and PF."""

    window: list[TradeRecord] = []
    for result in results:
        cutoff = cutoffs[result.pair]
        window.extend(
            trade
            for trade in result.trades_list
            if _trade_in_holdout(trade.entry_time, cutoff) == holdout
        )
    trades = len(window)
    pnls = [trade.pnl_pct / 100.0 for trade in window]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = sum(abs(p) for p in pnls if p <= 0)
    pf = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    equity = 1.0
    for pnl in pnls:
        equity *= 1.0 + pnl
    return trades, (equity - 1.0) * 100.0, pf


def write_outputs(
    results: list[ConfigResult],
    output_dir: Path,
    cutoffs: dict[str, pd.Timestamp] | None = None,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"pivot_v2_backtest_{stamp}.csv"
    md_path = output_dir / f"pivot_v2_backtest_{stamp}.md"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "pair",
                "config",
                "entry_type",
                "level_set",
                "proximity_pips",
                "confirm_bars",
                "tp_mult",
                "sl_mult",
                "trades",
                "wins",
                "losses",
                "timeouts",
                "win_rate",
                "total_pnl_pct",
                "profit_factor",
                "avg_win_pct",
                "avg_loss_pct",
                "max_drawdown_pct",
                "avg_bars_held",
            ]
        )
        for r in results:
            writer.writerow(
                [
                    r.pair,
                    r.config_label,
                    r.entry_type,
                    r.level_set,
                    r.proximity_pips,
                    r.confirm_bars,
                    r.tp_mult,
                    r.sl_mult,
                    r.trades,
                    r.wins,
                    r.losses,
                    r.timeouts,
                    f"{r.win_rate:.4f}",
                    f"{r.total_pnl_pct:.2f}",
                    f"{r.profit_factor:.2f}",
                    f"{r.avg_win:.4f}",
                    f"{r.avg_loss:.4f}",
                    f"{r.max_drawdown_pct:.2f}",
                    f"{r.avg_bars_held:.1f}",
                ]
            )

    config_agg: dict[str, list[ConfigResult]] = {}
    for r in results:
        config_agg.setdefault(r.config_label, []).append(r)

    agg_rows: list[dict[str, object]] = []
    for label, crs in config_agg.items():
        total_trades = sum(r.trades for r in crs)
        total_wins = sum(r.wins for r in crs)
        total_timeouts = sum(r.timeouts for r in crs)
        avg_pnl_pct = sum(r.total_pnl_pct for r in crs) / len(crs)
        gross_win = sum(r.avg_win * r.wins for r in crs)
        gross_loss = sum(r.avg_loss * r.losses for r in crs)
        pf = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
        max_dd = max(r.max_drawdown_pct for r in crs)
        wr = total_wins / total_trades if total_trades else 0.0
        pairs_profitable = sum(1 for r in crs if r.total_pnl_pct > 0)
        if cutoffs:
            dev_trades, dev_pnl, dev_pf = develop_metrics(crs, cutoffs, holdout=False)
            rank_trades, rank_pnl, rank_pf = dev_trades, dev_pnl, dev_pf
            rank_wr = wr
        else:
            rank_trades, rank_pnl, rank_pf, rank_wr = total_trades, avg_pnl_pct, pf, wr
        agg_rows.append(
            {
                "config": label,
                "entry_type": crs[0].entry_type,
                "level_set": crs[0].level_set,
                "total_trades": rank_trades,
                "win_rate": rank_wr,
                "avg_pnl_pct": rank_pnl,
                "profit_factor": rank_pf,
                "max_dd": max_dd,
                "timeouts": total_timeouts,
                "pairs_profitable": pairs_profitable,
                "pairs_tested": len(crs),
            }
        )

    agg_rows.sort(key=lambda r: (r["avg_pnl_pct"], r["profit_factor"]), reverse=True)

    lines = [
        "# Pivot Point Backtest v2 Results",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Entry types: WEEKLY (weekly standard pivots) | SESSION (London/NY open S/R) | CAMARILLA (H3/H4, L3/L4)",
        "Filters: MTF RSI 30/70 on 1h+30m+15m + SMA(50) gate + ADX(<25) gate",
        "Execution: closed-bar signal, next-bar-open fill, stop-first exits.",
        "Costs: 2 pip spread, 2 pip slippage/fill, $3/side commission.",
        "Ranking: develop window only (first 65%). Holdout is unused for selection.",
        "Clock: UTC bar timestamps; session windows are UTC hours.",
        "Not a live-go path.",
        "",
        "## Top 30 Configurations (by avg PnL % across all pairs)",
        "",
        "| Rank | Config | Type | Trades | WR | Avg PnL% | PF | Max DD% | Pairs +/total |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(agg_rows[:30], 1):
        lines.append(
            f"| {rank} | `{row['config']}` | {row['entry_type']} | {row['total_trades']} | "
            f"{row['win_rate']:.1%} | {row['avg_pnl_pct']:.2f}% | "
            f"{row['profit_factor']:.2f} | {row['max_dd']:.1f}% | "
            f"{row['pairs_profitable']}/{row['pairs_tested']} |"
        )

    # Per-type top 5
    for et in ["WEEKLY", "SESSION", "CAMARILLA"]:
        et_rows = [r for r in agg_rows if r["entry_type"] == et]
        lines += [
            "",
            f"## {et} — Top 10",
            "",
            "| Rank | Config | Trades | WR | Avg PnL% | PF | Pairs +/total |",
            "|---:|---|---:|---:|---:|---:|---:|",
        ]
        for rank, row in enumerate(et_rows[:10], 1):
            lines.append(
                f"| {rank} | `{row['config']}` | {row['total_trades']} | "
                f"{row['win_rate']:.1%} | {row['avg_pnl_pct']:.2f}% | "
                f"{row['profit_factor']:.2f} | {row['pairs_profitable']}/{row['pairs_tested']} |"
            )

    # Per-pair for overall top 5
    lines += ["", "## Per-Pair Breakdown (Overall Top 5 Configs)", ""]
    for row in agg_rows[:5]:
        label = cast(str, row["config"])
        lines += [
            f"### `{label}`",
            "",
            "| Pair | Trades | WR | PnL% | PF | Max DD% | Avg Bars |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for r in sorted(config_agg[label], key=lambda x: x.total_pnl_pct, reverse=True):
            lines.append(
                f"| {r.pair} | {r.trades} | {r.win_rate:.0%} | "
                f"{r.total_pnl_pct:.2f}% | {r.profit_factor:.2f} | "
                f"{r.max_drawdown_pct:.1f}% | {r.avg_bars_held:.0f} |"
            )
        lines.append("")

    zero_trades = [r for r in agg_rows if r["total_trades"] == 0]
    if zero_trades:
        lines += [f"## Configs with zero trades: {len(zero_trades)}", ""]

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path, md_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pivot Point v2 backtester (weekly/session/Camarilla)"
    )
    parser.add_argument("--pairs", default=",".join(ALL_PAIRS))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--source", default="dukascopy", choices=["yfinance", "dukascopy"])
    parser.add_argument(
        "--entry-types",
        default="WEEKLY,SESSION,CAMARILLA",
        help="Comma-separated: WEEKLY, SESSION, CAMARILLA",
    )
    parser.add_argument("--proximity", default="2,5,10,20")
    parser.add_argument("--confirm-bars", default="0,3,5")
    parser.add_argument("--tp-sl", default="1.0:2.0,1.5:2.0,2.0:2.0,1.0:3.0,2.0:3.0")
    parser.add_argument("--rsi-ob", type=float, default=70.0)
    parser.add_argument("--rsi-os", type=float, default=30.0)
    parser.add_argument("--adx-threshold", type=float, default=25.0)
    parser.add_argument("--sma-period", type=int, default=50)
    parser.add_argument("--max-hold", type=int, default=16)
    parser.add_argument("--output-dir", default="results")
    args = parser.parse_args()

    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    entry_types = [e.strip().upper() for e in args.entry_types.split(",")]
    proximities = [float(p.strip()) for p in args.proximity.split(",")]
    confirm_bars_list = [int(c.strip()) for c in args.confirm_bars.split(",")]
    tp_sl_pairs: list[tuple[float, float]] = []
    for ts_str in args.tp_sl.split(","):
        tp_v, sl_v = ts_str.strip().split(":")
        tp_sl_pairs.append((float(tp_v), float(sl_v)))

    # Build per-type level set lists
    type_level_sets: dict[str, list[str]] = {
        "WEEKLY": list(WEEKLY_LEVEL_SETS.keys()),
        "SESSION": SESSION_SETS,
        "CAMARILLA": list(CAMARILLA_LEVEL_SETS.keys()),
    }

    configs_per_pair = sum(
        len(type_level_sets.get(et, []))
        * len(proximities)
        * len(confirm_bars_list)
        * len(tp_sl_pairs)
        for et in entry_types
    )
    warmup = args.sma_period + 20 if args.sma_period > 0 else 40

    print("=== Pivot Point Backtester v2 ===")
    print(f"Pairs:         {len(pairs)}")
    print(f"Entry types:   {entry_types}")
    print(f"Proximity:     {proximities} pips")
    print(f"Confirm bars:  {confirm_bars_list}")
    print(f"TP/SL:         {tp_sl_pairs}")
    print(f"Configs/pair:  {configs_per_pair}")
    print(f"Total runs:    {configs_per_pair * len(pairs)}")
    print(f"Source:        {args.source}")
    print()

    # Fetch data
    pair_data: dict[str, dict[str, pd.DataFrame]] = {}
    print("[FETCHING DATA]")
    for pair in pairs:
        print(f"  [{args.source}] {pair}")
        data = fetch_pair(pair, args.days, args.source)
        if data is not None:
            pair_data[pair] = data

    if not pair_data:
        print("No data fetched. Aborting.")
        return 1

    print(f"\nFetched {len(pair_data)}/{len(pairs)} pairs")
    print(f"Running {configs_per_pair * len(pair_data)} backtests...\n")

    results: list[ConfigResult] = []
    run_count = 0
    t0 = time.time()

    for pair, mtf in pair_data.items():
        print(f"[CACHE] {pair} — pre-computing RSI/ADX/SMA/pivots/sessions...", flush=True)
        cache_t0 = time.time()
        cache = build_pair_cache(pair, mtf["1h"], mtf["30m"], mtf["15m"], args.sma_period)
        print(f"  cache built in {time.time() - cache_t0:.1f}s")

        print(f"[BACKTEST] {pair}")
        pair_t0 = time.time()

        for et in entry_types:
            level_sets = type_level_sets.get(et, [])
            for ls in level_sets:
                for prox in proximities:
                    for cb in confirm_bars_list:
                        for tp_m, sl_m in tp_sl_pairs:
                            r = run_config(
                                pair,
                                cache,
                                entry_type=et,
                                level_set=ls,
                                proximity_pips=prox,
                                confirm_bars=cb,
                                tp_mult=tp_m,
                                sl_mult=sl_m,
                                rsi_ob=args.rsi_ob,
                                rsi_os=args.rsi_os,
                                adx_threshold=args.adx_threshold,
                                sma_period=args.sma_period,
                                max_hold_bars=args.max_hold,
                                warmup=warmup,
                            )
                            results.append(r)
                            run_count += 1

        elapsed = time.time() - pair_t0
        total_elapsed = time.time() - t0
        rate = run_count / total_elapsed if total_elapsed > 0 else 1
        remaining = (configs_per_pair * len(pair_data) - run_count) / rate
        print(
            f"  {configs_per_pair} configs in {elapsed:.1f}s | {run_count} total | ~{remaining:.0f}s remaining"
        )

    total_time = time.time() - t0
    print(f"\nCompleted {run_count} backtests in {total_time:.0f}s ({total_time / 60:.1f}m)")

    cutoffs = {
        pair: pd.Timestamp(mtf["15m"].index[int(len(mtf["15m"].index) * IS_FRACTION)])
        for pair, mtf in pair_data.items()
        if len(mtf["15m"].index) > 0
    }
    csv_path, md_path = write_outputs(results, Path(args.output_dir), cutoffs)
    print(f"Saved CSV: {csv_path}")
    print(f"Saved MD:  {md_path}")

    # Quick top-10
    config_agg: dict[str, list[ConfigResult]] = {}
    for r in results:
        config_agg.setdefault(r.config_label, []).append(r)

    ranked = []
    for label, crs in config_agg.items():
        dev_trades, dev_pnl, dev_pf = develop_metrics(crs, cutoffs, holdout=False)
        if dev_trades < 10:
            continue
        ranked.append((label, dev_trades, dev_pnl, dev_pf))
    ranked.sort(key=lambda x: x[2], reverse=True)

    print("\n=== TOP 10 CONFIGS (develop window, ≥10 trades) ===")
    for i, (label, trades, pnl, pf) in enumerate(ranked[:10], 1):
        print(f"  {i:2d}. {label}")
        print(f"      {trades} trades | avg PnL {pnl:.2f}% | PF {pf:.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
