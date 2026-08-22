#!/usr/bin/env python3
"""MTF RSI Donchian Reversal Strategy Backtester.

Mirrors the Pine Script v2 logic exactly:
- MTF RSI alignment across 15m, 30m, 1h
- Donchian HH/LL reclaim (wick pierces then close fails back)
- RSI cross-back confirmation (RSI crosses back inside bounds)
- DI opposition filter, ADX range filter, session filter
- Fixed-pip or ATR-based TP/SL
- Breakeven stop, time-based exit

Causality: closed-bar signals, next-bar-open fills, stop-first same-bar exits.
Sweep ranking uses the chronological develop window only (first 65%); holdout
is reported and never used to pick a winner.

Clock: bar timestamps are UTC. Session filters use the bar's UTC hour
(``session_start <= hour < session_end``). This is not broker-server time.

Offline only: not a live-go or promote path. No broker orders are sent.

Usage:
    python scripts/run_donchian_backtest.py --pairs EUR/USD,GBP/USD --days 365
    python scripts/run_donchian_backtest.py --pairs EUR/USD --sweep tp-sl
    python scripts/run_donchian_backtest.py --pairs EUR/USD --sweep full
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.backtest.cost_book import CostBook, pip_size_for_pair
from src.data.dukascopy_fetcher import get_multi_timeframe_data_dukascopy
from src.indicators.high_low import previous_rolling_highest_high, previous_rolling_lowest_low
from src.indicators.rsi import calculate_rsi

IS_FRACTION = 0.65

# ---------------------------------------------------------------------------
# Forex pairs
# ---------------------------------------------------------------------------

TIER1_PAIRS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CHF",
    "EUR/GBP",
    "EUR/JPY",
    "GBP/JPY",
]

TIER2_PAIRS = [
    "NZD/USD",
    "USD/CAD",
    "AUD/JPY",
    "EUR/AUD",
    "GBP/CHF",
    "NZD/JPY",
    "CAD/JPY",
    "EUR/CAD",
    "AUD/NZD",
    "AUD/CAD",
]

ALL_PAIRS = TIER1_PAIRS + TIER2_PAIRS


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TradeRecord:
    pair: str
    direction: str
    entry_time: object
    exit_time: object
    entry_price: float
    exit_price: float
    tp_price: float
    sl_price: float
    pnl: float
    pnl_pct: float
    exit_reason: str
    bars_held: int
    rsi_15m: float
    rsi_30m: float
    rsi_1h: float
    be_triggered: bool = False


@dataclass
class ConfigResult:
    pair: str
    config_label: str
    upper_bound: float
    lower_bound: float
    use_fixed_pip: bool
    tp_pips: float
    sl_pips: float
    tp_atr_mult: float
    sl_atr_mult: float
    lookback: int
    confirm_bars: int
    buffer_pips: float
    use_di_filter: bool
    di_ratio: float
    use_adx_filter: bool
    max_adx: float
    use_session: bool
    use_mom_fade: bool
    mom_fade_bars: int
    use_trailing: bool
    trail_atr_mult: float
    use_breakeven: bool
    be_trigger_pct: float
    use_time_exit: bool
    max_bars_exit: int
    spread_pips: float
    commission_per_order: float
    slippage_pips: float
    trades: int = 0
    wins: int = 0
    losses: int = 0
    tp_exits: int = 0
    sl_exits: int = 0
    be_exits: int = 0
    time_exits: int = 0
    trail_exits: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_bars_held: float = 0.0
    max_consecutive_losses: int = 0
    trades_list: list[TradeRecord] = field(default_factory=list)


@dataclass
class PairCache:
    highs: list[float]
    lows: list[float]
    closes: list[float]
    opens: list[float]
    index_15m: list[object]
    pip_size: float
    rsi_15m: list[float | None]
    rsi_1h_series: pd.Series
    rsi_30m_series: pd.Series
    atr: list[float | None]
    atr_avg: list[float | None]
    plus_di_1h: pd.Series
    minus_di_1h: pd.Series
    adx_1h: pd.Series


# ---------------------------------------------------------------------------
# Pre-computation helpers
# ---------------------------------------------------------------------------


def _precompute_rsi_list(closes: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = []
    for i in range(len(closes)):
        start = max(0, i - 50)
        rsi = calculate_rsi(closes[start : i + 1], period)
        result.append(rsi)
    return result


def _precompute_rsi_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    closes = df["close"].tolist()
    vals = [
        (lambda r: float("nan") if r is None else r)(
            calculate_rsi(closes[max(0, i - 50) : i + 1], period)
        )
        for i in range(len(closes))
    ]
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
            max(
                highs[j] - lows[j],
                abs(highs[j] - closes[j - 1]),
                abs(lows[j] - closes[j - 1]),
            )
            for j in range(i - period + 1, i + 1)
        ]
        result.append(sum(trs) / period)
    return result


def _calc_adx_at_bar(
    data_1h: pd.DataFrame, ts: pd.Timestamp, period: int = 14
) -> tuple[float | None, float | None, float | None]:
    """Calculate +DI, -DI, ADX from 1h data at timestamp."""
    lb = period * 3
    subset = data_1h.loc[:ts]
    if len(subset) < lb:
        return None, None, None
    tail = subset.iloc[-lb:]
    h = tail["high"].tolist()
    lows_tail = tail["low"].tolist()
    c = tail["close"].tolist()
    n = len(h)
    if n < 2 * period + 1:
        return None, None, None

    tr_list: list[float] = []
    plus_dm_list: list[float] = []
    minus_dm_list: list[float] = []
    for i in range(1, n):
        high_diff = h[i] - h[i - 1]
        low_diff = lows_tail[i - 1] - lows_tail[i]
        plus_dm = max(high_diff, 0.0) if high_diff > low_diff else 0.0
        minus_dm = max(low_diff, 0.0) if low_diff > high_diff else 0.0
        tr_list.append(max(h[i] - lows_tail[i], abs(h[i] - c[i - 1]), abs(lows_tail[i] - c[i - 1])))
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    if len(tr_list) < 2 * period:
        return None, None, None

    smoothed_tr = sum(tr_list[:period])
    smoothed_plus = sum(plus_dm_list[:period])
    smoothed_minus = sum(minus_dm_list[:period])
    dx_list: list[float] = []
    last_plus_di = 0.0
    last_minus_di = 0.0

    for i in range(period, len(tr_list)):
        if i > period:
            smoothed_tr = smoothed_tr - smoothed_tr / period + tr_list[i]
            smoothed_plus = smoothed_plus - smoothed_plus / period + plus_dm_list[i]
            smoothed_minus = smoothed_minus - smoothed_minus / period + minus_dm_list[i]
        if smoothed_tr == 0:
            continue
        last_plus_di = 100.0 * smoothed_plus / smoothed_tr
        last_minus_di = 100.0 * smoothed_minus / smoothed_tr
        di_sum = last_plus_di + last_minus_di
        if di_sum == 0:
            dx_list.append(0.0)
        else:
            dx_list.append(100.0 * abs(last_plus_di - last_minus_di) / di_sum)

    if len(dx_list) < period:
        return last_plus_di, last_minus_di, None

    adx = sum(dx_list[:period]) / period
    for i in range(period, len(dx_list)):
        adx = (adx * (period - 1) + dx_list[i]) / period
    return last_plus_di, last_minus_di, adx


def _precompute_atr_avg(atr_values: list[float | None], period: int = 50) -> list[float | None]:
    """SMA of ATR for regime detection."""
    result: list[float | None] = []
    for i in range(len(atr_values)):
        window = atr_values[max(0, i - period + 1) : i + 1]
        valid = [v for v in window if v is not None]
        if len(valid) < period // 2:
            result.append(None)
        else:
            result.append(sum(valid) / len(valid))
    return result


def _precompute_dmi_series(
    df: pd.DataFrame, period: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Pre-compute +DI, -DI, ADX series for 1h data."""
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    closes = df["close"].tolist()
    n = len(highs)
    plus_di_vals: list[float | None] = [None] * n
    minus_di_vals: list[float | None] = [None] * n
    adx_vals: list[float | None] = [None] * n

    if n < 2 * period + 1:
        return (
            pd.Series(plus_di_vals, index=df.index),
            pd.Series(minus_di_vals, index=df.index),
            pd.Series(adx_vals, index=df.index),
        )

    tr_list: list[float] = []
    plus_dm_list: list[float] = []
    minus_dm_list: list[float] = []

    for i in range(1, n):
        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]
        plus_dm = max(high_diff, 0.0) if high_diff > low_diff else 0.0
        minus_dm = max(low_diff, 0.0) if low_diff > high_diff else 0.0
        tr_list.append(
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        )
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    if len(tr_list) < 2 * period:
        return (
            pd.Series(plus_di_vals, index=df.index),
            pd.Series(minus_di_vals, index=df.index),
            pd.Series(adx_vals, index=df.index),
        )

    smoothed_tr = sum(tr_list[:period])
    smoothed_plus = sum(plus_dm_list[:period])
    smoothed_minus = sum(minus_dm_list[:period])
    dx_list: list[float] = []

    for i in range(period, len(tr_list)):
        if i > period:
            smoothed_tr = smoothed_tr - smoothed_tr / period + tr_list[i]
            smoothed_plus = smoothed_plus - smoothed_plus / period + plus_dm_list[i]
            smoothed_minus = smoothed_minus - smoothed_minus / period + minus_dm_list[i]
        if smoothed_tr == 0:
            dx_list.append(0.0)
            pdi = 0.0
            mdi = 0.0
        else:
            pdi = 100.0 * smoothed_plus / smoothed_tr
            mdi = 100.0 * smoothed_minus / smoothed_tr
            di_sum = pdi + mdi
            if di_sum == 0:
                dx_list.append(0.0)
            else:
                dx_list.append(100.0 * abs(pdi - mdi) / di_sum)

        bar_idx = i + 1  # offset by 1 due to diff
        if bar_idx < n:
            plus_di_vals[bar_idx] = pdi
            minus_di_vals[bar_idx] = mdi

    if len(dx_list) >= period:
        adx = sum(dx_list[:period]) / period
        for i in range(period, len(dx_list)):
            adx = (adx * (period - 1) + dx_list[i]) / period
            bar_idx = i + 1 + period  # offset
            if bar_idx < n:
                adx_vals[bar_idx] = adx

    return (
        pd.Series(plus_di_vals, index=df.index, dtype=float),
        pd.Series(minus_di_vals, index=df.index, dtype=float),
        pd.Series(adx_vals, index=df.index, dtype=float),
    )


def build_pair_cache(
    pair: str, data_1h: pd.DataFrame, data_30m: pd.DataFrame, data_15m: pd.DataFrame
) -> PairCache:
    highs = data_15m["high"].tolist()
    lows = data_15m["low"].tolist()
    closes = data_15m["close"].tolist()
    opens = data_15m["open"].tolist() if "open" in data_15m.columns else closes

    plus_di_s, minus_di_s, adx_s = _precompute_dmi_series(data_1h)

    return PairCache(
        highs=highs,
        lows=lows,
        closes=closes,
        opens=opens,
        index_15m=list(data_15m.index),
        pip_size=pip_size_for_pair(pair),
        rsi_15m=_precompute_rsi_list(closes),
        rsi_1h_series=_precompute_rsi_series(data_1h),
        rsi_30m_series=_precompute_rsi_series(data_30m),
        atr=_precompute_atr(highs, lows, closes),
        atr_avg=_precompute_atr_avg(_precompute_atr(highs, lows, closes)),
        plus_di_1h=plus_di_s,
        minus_di_1h=minus_di_s,
        adx_1h=adx_s,
    )


# ---------------------------------------------------------------------------
# Core backtest — mirrors Pine Script v2 exactly
# ---------------------------------------------------------------------------


def run_config(
    pair: str,
    cache: PairCache,
    data_1h: pd.DataFrame,
    upper_bound: float = 65.0,
    lower_bound: float = 35.0,
    use_fixed_pip: bool = False,
    tp_pips: float = 10.0,
    sl_pips: float = 40.0,
    tp_atr_mult: float = 1.5,
    sl_atr_mult: float = 1.5,
    lookback: int = 20,
    confirm_bars: int = 8,
    buffer_pips: float = 0.0,
    use_di_filter: bool = True,
    di_ratio: float = 1.65,
    use_adx_filter: bool = True,
    max_adx: float = 25.0,
    use_session: bool = False,
    session_start: int = 6,
    session_end: int = 17,
    use_mom_fade: bool = True,
    mom_fade_bars: int = 3,
    use_breakeven: bool = True,
    be_trigger_pct: float = 50.0,
    use_trailing: bool = False,
    trail_atr_mult: float = 2.0,
    use_time_exit: bool = True,
    max_bars_exit: int = 32,
    spread_pips: float = 2.0,
    commission_per_order: float = 3.0,
    slippage_pips: float = 2.0,
    warmup: int = 80,
) -> ConfigResult:
    label = (
        f"ob{upper_bound:g}_os{lower_bound:g}"
        f"_{'F' if use_fixed_pip else 'A'}tp{tp_pips if use_fixed_pip else tp_atr_mult:g}"
        f"_sl{sl_pips if use_fixed_pip else sl_atr_mult:g}"
        f"_lb{lookback}_cb{confirm_bars}_buf{buffer_pips:g}"
        f"_di{'T' if use_di_filter else 'F'}"
        f"_adx{'T' if use_adx_filter else 'F'}"
        f"_mf{'T' if use_mom_fade else 'F'}{mom_fade_bars}"
        f"_tr{'T' if use_trailing else 'F'}"
        f"_be{'T' if use_breakeven else 'F'}{be_trigger_pct:g}"
        f"_sess{'T' if use_session else 'F'}"
    )
    result = ConfigResult(
        pair=pair,
        config_label=label,
        upper_bound=upper_bound,
        lower_bound=lower_bound,
        use_fixed_pip=use_fixed_pip,
        tp_pips=tp_pips,
        sl_pips=sl_pips,
        tp_atr_mult=tp_atr_mult,
        sl_atr_mult=sl_atr_mult,
        lookback=lookback,
        confirm_bars=confirm_bars,
        buffer_pips=buffer_pips,
        use_di_filter=use_di_filter,
        di_ratio=di_ratio,
        use_adx_filter=use_adx_filter,
        max_adx=max_adx,
        use_session=use_session,
        use_mom_fade=use_mom_fade,
        mom_fade_bars=mom_fade_bars,
        use_trailing=use_trailing,
        trail_atr_mult=trail_atr_mult,
        use_breakeven=use_breakeven,
        be_trigger_pct=be_trigger_pct,
        use_time_exit=use_time_exit,
        max_bars_exit=max_bars_exit,
        spread_pips=spread_pips,
        commission_per_order=commission_per_order,
        slippage_pips=slippage_pips,
    )

    pip = cache.pip_size
    buffer = buffer_pips * pip
    costs = CostBook(
        spread_pips=spread_pips,
        slippage_pips=slippage_pips,
        commission_usd_per_lot_side=commission_per_order,
    )
    slip = costs.slippage_pips * pip

    balance = 100000.0
    peak = balance
    max_dd_pct = 0.0

    position: Literal["buy", "sell", None] = None
    entry_price = 0.0
    entry_idx = 0
    tp_price = 0.0
    sl_price = 0.0
    be_active = False
    trail_stop: float | None = None
    entry_rsi = (0.0, 0.0, 0.0)

    trades_list: list[TradeRecord] = []
    trade_pnls: list[float] = []
    bars_held_list: list[int] = []
    consecutive_losses = 0
    max_consecutive_losses = 0

    # Track alignment state
    last_ob_bar: int | None = None
    last_os_bar: int | None = None
    pending_signal: Literal["buy", "sell"] | None = None
    pending_atr = 0.0
    pending_rsi = (0.0, 0.0, 0.0)

    highs = cache.highs
    lows = cache.lows
    closes = cache.closes
    opens = cache.opens

    for i in range(warmup, len(closes)):
        ts = cast(pd.Timestamp, cache.index_15m[i])
        close = closes[i]
        high_val = highs[i]
        low_val = lows[i]
        open_price = opens[i]

        if pending_signal is not None and position is None:
            position = pending_signal
            tp_distance = tp_pips * pip if use_fixed_pip else pending_atr * tp_atr_mult
            sl_distance = sl_pips * pip if use_fixed_pip else pending_atr * sl_atr_mult
            entry_price = costs.entry_fill(open_price, position, pip)
            if position == "buy":
                tp_price = entry_price + tp_distance
                sl_price = entry_price - sl_distance
            else:
                tp_price = entry_price - tp_distance
                sl_price = entry_price + sl_distance
            entry_idx = i
            entry_rsi = pending_rsi
            be_active = False
            trail_stop = None
            pending_signal = None

        # --- Multi-TF RSI ---
        rsi_15 = cache.rsi_15m[i]
        if rsi_15 is None:
            continue
        rsi_1h_raw = cache.rsi_1h_series.asof(ts)
        rsi_30m_raw = cache.rsi_30m_series.asof(ts)
        if pd.isna(rsi_1h_raw) or pd.isna(rsi_30m_raw):
            continue
        rsi_1h = float(rsi_1h_raw)
        rsi_30m = float(rsi_30m_raw)

        # --- ATR ---
        atr = cache.atr[i]
        if atr is None or atr <= 0:
            continue

        # --- ATR regime (pre-computed, available for future use) ---
        # atr_avg = cache.atr_avg[i]

        # --- HH/LL (previous bars, excluding current) ---
        hh_prev = previous_rolling_highest_high(highs, lookback, i)
        ll_prev = previous_rolling_lowest_low(lows, lookback, i)
        if hh_prev is None or ll_prev is None:
            continue

        # --- Alignment detection ---
        full_ob = rsi_15 > upper_bound and rsi_30m > upper_bound and rsi_1h > upper_bound
        full_os = rsi_15 < lower_bound and rsi_30m < lower_bound and rsi_1h < lower_bound
        htf_ob = rsi_30m > upper_bound and rsi_1h > upper_bound
        htf_os = rsi_30m < lower_bound and rsi_1h < lower_bound

        # Track alignment window — use barssince-style tracking
        # (remember last alignment even if it's no longer active)
        if full_ob:
            last_ob_bar = i
        if full_os:
            last_os_bar = i

        bars_since_ob = i - last_ob_bar if last_ob_bar is not None else warmup + 1
        bars_since_os = i - last_os_bar if last_os_bar is not None else warmup + 1
        within_window_short = bars_since_ob <= confirm_bars
        within_window_long = bars_since_os <= confirm_bars

        # --- Reclaim pattern ---
        short_reclaim = high_val > hh_prev + buffer and close < hh_prev
        long_reclaim = low_val < ll_prev - buffer and close > ll_prev

        # --- RSI cross-back ---
        prev_rsi_15 = cache.rsi_15m[i - 1] if i > 0 else None
        if prev_rsi_15 is None:
            continue

        short_rsi_cross = prev_rsi_15 > upper_bound and rsi_15 <= upper_bound
        long_rsi_cross = prev_rsi_15 < lower_bound and rsi_15 >= lower_bound

        # --- Momentum fade (NEW v2) ---
        rsi_lookback = cache.rsi_15m[i - mom_fade_bars] if i >= mom_fade_bars else None
        if rsi_lookback is not None:
            rsi_rising_from_os = rsi_15 > rsi_lookback
            rsi_falling_from_ob = rsi_15 < rsi_lookback
        else:
            rsi_rising_from_os = False
            rsi_falling_from_ob = False
        mom_fade_short_ok = not use_mom_fade or rsi_falling_from_ob
        mom_fade_long_ok = not use_mom_fade or rsi_rising_from_os

        # --- Filters ---
        adx_ok = True
        di_short_ok = True
        di_long_ok = True
        if use_adx_filter or use_di_filter:
            adx_raw = cache.adx_1h.asof(ts)
            plus_raw = cache.plus_di_1h.asof(ts)
            minus_raw = cache.minus_di_1h.asof(ts)

            if use_adx_filter and not pd.isna(adx_raw):
                adx_ok = float(adx_raw) < max_adx

            if use_di_filter and not pd.isna(plus_raw) and not pd.isna(minus_raw):
                p, m = float(plus_raw), float(minus_raw)
                di_short_ok = not (p > m * di_ratio)
                di_long_ok = not (m > p * di_ratio)

        session_ok = True
        if use_session:
            hour = ts.hour
            session_ok = session_start <= hour < session_end

        # --- Triggers ---
        short_trigger = (
            htf_ob
            and within_window_short
            and prev_rsi_15 > upper_bound
            and short_rsi_cross
            and short_reclaim
            and adx_ok
            and di_short_ok
            and session_ok
            and mom_fade_short_ok
        )
        long_trigger = (
            htf_os
            and within_window_long
            and prev_rsi_15 < lower_bound
            and long_rsi_cross
            and long_reclaim
            and adx_ok
            and di_long_ok
            and session_ok
            and mom_fade_long_ok
        )

        # --- Manage open position ---
        if position is not None:
            exit_price = None
            exit_reason = ""

            # Compute TP/SL distances for this bar
            tp_distance = tp_pips * pip if use_fixed_pip else atr * tp_atr_mult
            sl_distance = sl_pips * pip if use_fixed_pip else atr * sl_atr_mult

            # Breakeven check
            be_trigger_dist = tp_distance * (be_trigger_pct / 100)
            if (
                use_breakeven
                and not be_active
                and (
                    (position == "buy" and high_val >= entry_price + be_trigger_dist)
                    or (position == "sell" and low_val <= entry_price - be_trigger_dist)
                )
            ):
                be_active = True

            # Compute current SL
            if position == "buy":
                base_stop = entry_price if be_active else entry_price - sl_distance
                if use_trailing:
                    candidate = close - atr * trail_atr_mult
                    trail_stop = max(trail_stop, candidate) if trail_stop is not None else candidate
                    final_stop = max(base_stop, trail_stop)
                else:
                    final_stop = base_stop
                final_tp = entry_price + tp_distance

                if low_val <= final_stop:
                    exit_price, exit_reason = final_stop, "sl"
                elif high_val >= final_tp:
                    exit_price, exit_reason = final_tp, "tp"
            else:
                base_stop = entry_price if be_active else entry_price + sl_distance
                if use_trailing:
                    candidate = close + atr * trail_atr_mult
                    trail_stop = min(trail_stop, candidate) if trail_stop is not None else candidate
                    final_stop = min(base_stop, trail_stop)
                else:
                    final_stop = base_stop
                final_tp = entry_price - tp_distance

                if high_val >= final_stop:
                    exit_price, exit_reason = final_stop, "sl"
                elif low_val <= final_tp:
                    exit_price, exit_reason = final_tp, "tp"

            # Time exit
            if exit_price is None and use_time_exit and (i - entry_idx) >= max_bars_exit:
                exit_price = close
                exit_reason = "time"

            if exit_price is not None:
                effective_exit = costs.exit_fill(exit_price, position, pip)

                if position == "buy":
                    raw_pnl_price = effective_exit - entry_price
                else:
                    raw_pnl_price = entry_price - effective_exit

                # Risk-based sizing: risk_pct of equity per SL distance
                sl_dist = abs(final_stop - entry_price) if final_stop else sl_distance
                risk_pct = 0.01
                position_size = (balance * risk_pct / sl_dist) if sl_dist > 0 else 1
                gross_pnl = position_size * raw_pnl_price

                commission_cash = costs.round_trip_commission_usd()
                commission_impact = commission_cash / balance if balance > 0 else 0

                pnl = gross_pnl - commission_impact * balance

                balance += pnl
                trade_pnls.append(pnl)
                bars_held_list.append(i - entry_idx)

                if pnl <= 0:
                    consecutive_losses += 1
                    max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
                else:
                    consecutive_losses = 0

                peak = max(peak, balance)
                dd_pct = ((peak - balance) / peak) * 100 if peak > 0 else 0.0
                max_dd_pct = max(max_dd_pct, dd_pct)

                is_be_exit = be_active and exit_reason == "sl"
                trades_list.append(
                    TradeRecord(
                        pair=pair,
                        direction=position,
                        entry_time=cache.index_15m[entry_idx],
                        exit_time=ts,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        tp_price=final_tp,
                        sl_price=final_stop,
                        pnl=pnl,
                        pnl_pct=pnl / balance * 100 if balance > 0 else 0,
                        exit_reason=exit_reason,
                        bars_held=i - entry_idx,
                        rsi_15m=entry_rsi[0],
                        rsi_30m=entry_rsi[1],
                        rsi_1h=entry_rsi[2],
                        be_triggered=is_be_exit,
                    )
                )

                if exit_reason == "tp":
                    result.tp_exits += 1
                elif exit_reason == "sl":
                    if be_active:
                        result.be_exits += 1
                    else:
                        result.sl_exits += 1
                elif exit_reason == "time":
                    result.time_exits += 1

                position = None
                be_active = False
                trail_stop = None

        # --- Counter-signal close ---
        if position is not None:
            counter_signal = (position == "buy" and short_trigger) or (
                position == "sell" and long_trigger
            )
            if counter_signal:
                sl_dist = (
                    abs(sl_price - entry_price) if sl_price != entry_price else atr * sl_atr_mult
                )
                position_size = (balance * 0.01 / sl_dist) if sl_dist > 0 else 1
                if position == "buy":
                    pnl = position_size * (close - entry_price - slip)
                else:
                    pnl = position_size * (entry_price - close - slip)
                pnl -= costs.round_trip_commission_usd()
                balance += pnl
                trade_pnls.append(pnl)
                bars_held_list.append(i - entry_idx)
                if pnl <= 0:
                    consecutive_losses += 1
                    max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
                else:
                    consecutive_losses = 0
                peak = max(peak, balance)
                dd_pct = ((peak - balance) / peak) * 100 if peak > 0 else 0.0
                max_dd_pct = max(max_dd_pct, dd_pct)
                trades_list.append(
                    TradeRecord(
                        pair=pair,
                        direction=position,
                        entry_time=cache.index_15m[entry_idx],
                        exit_time=ts,
                        entry_price=entry_price,
                        exit_price=close,
                        tp_price=tp_price,
                        sl_price=sl_price,
                        pnl=pnl,
                        pnl_pct=pnl / balance * 100 if balance > 0 else 0,
                        exit_reason="signal",
                        bars_held=i - entry_idx,
                        rsi_15m=entry_rsi[0],
                        rsi_30m=entry_rsi[1],
                        rsi_1h=entry_rsi[2],
                    )
                )
                position = None
                be_active = False
                trail_stop = None

        # --- Arm next-bar fill (never fill on the signal bar close) ---
        if position is None and pending_signal is None:
            current_signal: Literal["buy", "sell", None] = None
            if long_trigger:
                current_signal = "buy"
            elif short_trigger:
                current_signal = "sell"

            if current_signal is not None and i + 1 < len(closes):
                pending_signal = current_signal
                pending_atr = atr
                pending_rsi = (rsi_15, rsi_30m, rsi_1h)

    # Calculate final stats
    wins = sum(1 for p in trade_pnls if p > 0)
    losses = sum(1 for p in trade_pnls if p <= 0)
    gross_win = sum(p for p in trade_pnls if p > 0)
    gross_loss = sum(abs(p) for p in trade_pnls if p <= 0)

    result.trades_list = trades_list
    result.trades = len(trade_pnls)
    result.wins = wins
    result.losses = losses
    result.win_rate = wins / len(trade_pnls) if trade_pnls else 0.0
    result.total_pnl = sum(trade_pnls)
    result.total_pnl_pct = ((balance - 100000.0) / 100000.0) * 100
    result.profit_factor = (
        gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    )
    result.avg_win = gross_win / wins if wins else 0.0
    result.avg_loss = gross_loss / losses if losses else 0.0
    result.max_drawdown_pct = max_dd_pct
    result.avg_bars_held = sum(bars_held_list) / len(bars_held_list) if bars_held_list else 0.0
    result.max_consecutive_losses = max_consecutive_losses
    return result


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


CACHE_DIR = Path("results/cache")


def _cache_key(pair: str, days: int) -> Path:
    safe = pair.replace("/", "_")
    return CACHE_DIR / f"{safe}_{days}d.parquet"


def fetch_pair(pair: str, days: int) -> dict[str, pd.DataFrame] | None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ck = _cache_key(pair, days)

    # Try loading from cache
    if ck.exists():
        print(f"  {pair}: loading from cache")
        try:
            data: dict[str, pd.DataFrame] = {}
            store = pd.read_parquet(ck)
            for tf in ["1h", "30m", "15m"]:
                tf_df = store[store["_tf"] == tf].drop(columns=["_tf"])
                if not tf_df.empty:
                    tf_df = tf_df.set_index("datetime")
                    tf_df.index = pd.to_datetime(tf_df.index, utc=True)
                    data[tf] = tf_df
            if all(tf in data and not data[tf].empty for tf in ["1h", "30m", "15m"]):
                print(
                    f"    1h: {len(data['1h'])} | 30m: {len(data['30m'])} | 15m: {len(data['15m'])}"
                )
                return data
        except Exception:
            pass

    end_date = datetime.now(UTC)
    start_date = end_date - timedelta(days=days)
    print(f"  Fetching {days}d via Dukascopy ({pair})...")
    try:
        mtf, _summary = get_multi_timeframe_data_dukascopy(
            pair,
            start_date,
            end_date,
            timeframes=["h1", "m30", "m15"],
        )
        remapped: dict[str, pd.DataFrame] = {}
        key_map = {"h1": "1h", "m30": "30m", "m15": "15m"}
        for k, v in mtf.items():
            remapped[key_map.get(k, k)] = v
        for tf_key in ["1h", "30m", "15m"]:
            if tf_key in remapped:
                print(f"    {tf_key}: {len(remapped[tf_key])} bars")
        if any(tf not in remapped or remapped[tf].empty for tf in ["1h", "30m", "15m"]):
            print("    SKIPPED: incomplete data")
            return None
        n15 = len(remapped["15m"])
        print(f"    OK: {n15} bars on 15m ({n15 * 15 / 60 / 24:.0f} days)")

        # Save to cache
        all_frames = []
        for tf_key, df in remapped.items():
            chunk = df.reset_index()
            if "index" in chunk.columns:
                chunk = chunk.rename(columns={"index": "datetime"})
            chunk["_tf"] = tf_key
            all_frames.append(chunk)
        if all_frames:
            combined = pd.concat(all_frames, ignore_index=True)
            combined.to_parquet(ck, index=False)
            print(f"    Cached to {ck}")

        return remapped
    except Exception as e:
        print(f"    ERROR: {e}")
        return None


# ---------------------------------------------------------------------------
# Sweep configurations
# ---------------------------------------------------------------------------


def get_sweep_configs(sweep_type: str) -> list[dict]:
    """Generate parameter configurations for sweeping."""
    base = {
        "upper_bound": 65.0,
        "lower_bound": 35.0,
        "use_fixed_pip": True,
        "tp_pips": 10.0,
        "sl_pips": 40.0,
        "tp_atr_mult": 1.5,
        "sl_atr_mult": 1.5,
        "lookback": 20,
        "confirm_bars": 8,
        "buffer_pips": 0.0,
        "use_di_filter": True,
        "di_ratio": 1.65,
        "use_adx_filter": False,
        "max_adx": 25.0,
        "use_session": False,
        "use_mom_fade": False,
        "mom_fade_bars": 3,
        "use_breakeven": False,
        "be_trigger_pct": 50.0,
        "use_trailing": False,
        "trail_atr_mult": 2.0,
        "use_time_exit": True,
        "max_bars_exit": 192,
        "spread_pips": 2.0,
        "commission_per_order": 3.0,
        "slippage_pips": 2.0,
    }

    if sweep_type == "baseline":
        return [base]

    if sweep_type == "pine":
        # Faithful reproduction of pine_scripts/quant_mtf_rsi_donchian_v2_refactored.pine
        # production defaults. DI filter and momentum-fade are OFF (not used by the
        # Pine script). Sessions 06-17 + 12-21 are unioned to a single 06-21 window
        # (backtest supports one window; the two Pine sessions overlap). Partial-TP is
        # not modelled here (backtest limitation) — see notes in the review.
        return [
            {
                **base,
                "upper_bound": 70.0,
                "lower_bound": 30.0,
                "use_fixed_pip": False,
                "tp_atr_mult": 1.0,
                "sl_atr_mult": 3.0,
                "lookback": 20,
                "confirm_bars": 2,
                "buffer_pips": 0.5,
                "use_di_filter": False,
                "use_adx_filter": True,
                "max_adx": 25.0,
                "use_session": True,
                "session_start": 6,
                "session_end": 21,
                "use_mom_fade": False,
                "use_breakeven": True,
                "be_trigger_pct": 50.0,
                "use_trailing": False,
                "use_time_exit": True,
                "max_bars_exit": 96,
            }
        ]

    configs: list[dict] = []

    if sweep_type in ("atr-exits", "full"):
        # Priority 1: ATR TP/SL multiplier sweep
        atr_combos = [
            (0.5, 1.0),
            (0.5, 1.5),
            (1.0, 1.0),
            (1.0, 1.5),
            (1.0, 2.0),
            (1.5, 1.0),
            (1.5, 1.5),
            (1.5, 2.0),
            (1.5, 2.5),
            (2.0, 1.5),
            (2.0, 2.0),
            (2.0, 2.5),
            (2.5, 2.0),
            (2.5, 2.5),
            (3.0, 2.0),
            (3.0, 3.0),
        ]
        for tp_m, sl_m in atr_combos:
            cfg = {**base, "tp_atr_mult": tp_m, "sl_atr_mult": sl_m}
            configs.append(cfg)

    if sweep_type in ("rsi", "full"):
        # Priority 2: RSI bounds
        rsi_combos = [
            (60, 40),
            (65, 35),
            (70, 30),
            (75, 25),
        ]
        for ob, os_val in rsi_combos:
            cfg = {**base, "upper_bound": float(ob), "lower_bound": float(os_val)}
            configs.append(cfg)

    if sweep_type in ("structure", "full"):
        # Priority 3: Structure & timing
        struct_combos = [
            (10, 4, 0),
            (15, 6, 0),
            (20, 8, 0),
            (20, 8, 2),
            (25, 10, 0),
            (30, 12, 0),
        ]
        for lb, cb, buf in struct_combos:
            cfg = {**base, "lookback": lb, "confirm_bars": cb, "buffer_pips": float(buf)}
            configs.append(cfg)

    if sweep_type in ("costs", "full"):
        # Priority 5: Cost sensitivity analysis
        cost_combos = [
            {"slippage_pips": 0.0, "commission_per_order": 0.0, "spread_pips": 0.0},
            {"slippage_pips": 1.0, "commission_per_order": 3.0, "spread_pips": 2.0},
            {"slippage_pips": 2.0, "commission_per_order": 3.0, "spread_pips": 2.0},
            {"slippage_pips": 3.0, "commission_per_order": 3.0, "spread_pips": 2.0},
            {"slippage_pips": 4.0, "commission_per_order": 5.0, "spread_pips": 3.0},
        ]
        for combo in cost_combos:
            cfg = {**base, **combo}
            configs.append(cfg)

    if sweep_type in ("filters", "full"):
        # Priority 4: Filter combinations
        filter_combos = [
            {"use_di_filter": False, "use_adx_filter": False, "use_mom_fade": False},
            {"use_di_filter": True, "use_adx_filter": False, "use_mom_fade": False},
            {"use_di_filter": True, "use_adx_filter": True, "use_mom_fade": False},
            {"use_di_filter": True, "use_adx_filter": True, "use_mom_fade": True},
            {"use_di_filter": False, "use_adx_filter": True, "use_mom_fade": True},
            {"use_di_filter": True, "use_adx_filter": False, "use_mom_fade": True},
        ]
        for filt in filter_combos:
            cfg = {**base, **filt}
            configs.append(cfg)

    if sweep_type in ("exits", "full"):
        # Priority 5: Breakeven, trailing, time exit
        exit_combos = [
            {"use_breakeven": False, "use_trailing": False, "use_time_exit": False},
            {"use_breakeven": True, "be_trigger_pct": 30, "use_trailing": False},
            {"use_breakeven": True, "be_trigger_pct": 50, "use_trailing": False},
            {"use_breakeven": True, "be_trigger_pct": 70, "use_trailing": False},
            {
                "use_breakeven": True,
                "be_trigger_pct": 50,
                "use_trailing": True,
                "trail_atr_mult": 2.0,
            },
            {
                "use_breakeven": True,
                "be_trigger_pct": 50,
                "use_trailing": True,
                "trail_atr_mult": 3.0,
            },
            {"use_breakeven": False, "use_trailing": True, "trail_atr_mult": 2.0},
            {"use_breakeven": False, "use_trailing": True, "trail_atr_mult": 3.0},
            {"use_breakeven": True, "be_trigger_pct": 50, "max_bars_exit": 16},
            {"use_breakeven": True, "be_trigger_pct": 50, "max_bars_exit": 48},
        ]
        for ex in exit_combos:
            cfg = {**base, **ex}
            configs.append(cfg)

    return configs


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _trade_in_holdout(entry_time: object, cutoff: pd.Timestamp) -> bool:
    return pd.Timestamp(entry_time) > cutoff


def develop_metrics(
    results: list[ConfigResult],
    cutoffs: dict[str, pd.Timestamp],
    *,
    holdout: bool,
) -> tuple[int, int, float, float, float]:
    """Return trades, wins, pnl% (vs 100k), PF, max DD for one chronological window."""

    window: list[TradeRecord] = []
    max_dd = 0.0
    for result in results:
        cutoff = cutoffs[result.pair]
        selected = [
            trade
            for trade in result.trades_list
            if _trade_in_holdout(trade.entry_time, cutoff) == holdout
        ]
        window.extend(selected)
        max_dd = max(max_dd, result.max_drawdown_pct)
    trades = len(window)
    wins = sum(1 for trade in window if trade.pnl > 0)
    gross_win = sum(trade.pnl for trade in window if trade.pnl > 0)
    gross_loss = sum(-trade.pnl for trade in window if trade.pnl <= 0)
    pf = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    total_pnl_pct = sum(trade.pnl for trade in window) / 1000.0
    return trades, wins, total_pnl_pct, pf, max_dd


def write_outputs(
    results: list[ConfigResult],
    output_dir: Path,
    cutoffs: dict[str, pd.Timestamp] | None = None,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"donchian_backtest_{stamp}.csv"
    md_path = output_dir / f"donchian_backtest_{stamp}.md"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "pair",
                "config",
                "upper_bound",
                "lower_bound",
                "tp_pips",
                "sl_pips",
                "lookback",
                "confirm_bars",
                "buffer_pips",
                "use_di",
                "di_ratio",
                "use_adx",
                "max_adx",
                "use_session",
                "use_be",
                "be_pct",
                "use_time_exit",
                "max_bars",
                "trades",
                "wins",
                "losses",
                "tp_exits",
                "sl_exits",
                "be_exits",
                "time_exits",
                "win_rate",
                "total_pnl_pct",
                "profit_factor",
                "avg_win",
                "avg_loss",
                "max_dd_pct",
                "avg_bars_held",
                "max_consec_losses",
            ]
        )
        for r in results:
            writer.writerow(
                [
                    r.pair,
                    r.config_label,
                    r.upper_bound,
                    r.lower_bound,
                    r.tp_pips,
                    r.sl_pips,
                    r.lookback,
                    r.confirm_bars,
                    r.buffer_pips,
                    r.use_di_filter,
                    r.di_ratio,
                    r.use_adx_filter,
                    r.max_adx,
                    r.use_session,
                    r.use_breakeven,
                    r.be_trigger_pct,
                    r.use_time_exit,
                    r.max_bars_exit,
                    r.trades,
                    r.wins,
                    r.losses,
                    r.tp_exits,
                    r.sl_exits,
                    r.be_exits,
                    r.time_exits,
                    f"{r.win_rate:.4f}",
                    f"{r.total_pnl_pct:.2f}",
                    f"{r.profit_factor:.2f}",
                    f"{r.avg_win:.2f}",
                    f"{r.avg_loss:.2f}",
                    f"{r.max_drawdown_pct:.2f}",
                    f"{r.avg_bars_held:.1f}",
                    r.max_consecutive_losses,
                ]
            )

    # Aggregate per config
    config_agg: dict[str, list[ConfigResult]] = {}
    for r in results:
        config_agg.setdefault(r.config_label, []).append(r)

    agg_rows: list[dict[str, object]] = []
    for label, crs in config_agg.items():
        total_trades = sum(r.trades for r in crs)
        total_wins = sum(r.wins for r in crs)
        total_tp = sum(r.tp_exits for r in crs)
        total_sl = sum(r.sl_exits for r in crs)
        total_be = sum(r.be_exits for r in crs)
        total_time = sum(r.time_exits for r in crs)
        avg_pnl_pct = sum(r.total_pnl_pct for r in crs) / len(crs)
        gross_win = sum(r.avg_win * r.wins for r in crs)
        gross_loss = sum(r.avg_loss * r.losses for r in crs)
        pf = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
        max_dd = max(r.max_drawdown_pct for r in crs)
        wr = total_wins / total_trades if total_trades else 0.0
        pairs_profitable = sum(1 for r in crs if r.total_pnl_pct > 0)
        max_cl = max(r.max_consecutive_losses for r in crs)

        # Composite score uses develop trades when a split is provided.
        if cutoffs:
            dev_trades, dev_wins, dev_pnl, dev_pf, dev_dd = develop_metrics(
                crs, cutoffs, holdout=False
            )
            score = (
                dev_pf
                * min((dev_wins / dev_trades if dev_trades else 0.0) / 0.75, 1.0)
                * min(dev_trades / 30, 1.0)
                / (1 + dev_dd / 1000)
            )
            rank_trades, rank_wr, rank_pnl, rank_pf = (
                dev_trades,
                dev_wins / dev_trades if dev_trades else 0.0,
                dev_pnl,
                dev_pf,
            )
        else:
            score = pf * min(wr / 0.75, 1.0) * min(total_trades / 30, 1.0) / (1 + max_dd / 1000)
            rank_trades, rank_wr, rank_pnl, rank_pf = total_trades, wr, avg_pnl_pct, pf

        agg_rows.append(
            {
                "config": label,
                "total_trades": rank_trades,
                "win_rate": rank_wr,
                "avg_pnl_pct": rank_pnl,
                "profit_factor": rank_pf,
                "max_dd": max_dd,
                "pairs_profitable": pairs_profitable,
                "pairs_tested": len(crs),
                "score": score,
                "tp_exits": total_tp,
                "sl_exits": total_sl,
                "be_exits": total_be,
                "time_exits": total_time,
                "max_consec_losses": max_cl,
            }
        )

    agg_rows.sort(key=lambda r: r["score"], reverse=True)

    lines = [
        "# MTF RSI Donchian Reversal v2 — Backtest Results",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Strategy: MTF RSI (15m/30m/1h) + Donchian HH/LL Reclaim + RSI Cross-back",
        "Execution: closed-bar signal, next-bar-open fill, stop-first exits.",
        "Ranking: develop window only (first 65% by bar). Holdout is unused for selection.",
        "Not a live-go path.",
        f"Pairs tested: {len({r.pair for r in results})}",
        f"Total configurations: {len(agg_rows)}",
        "",
        "### Composite Score Formula",
        "`Score = PF × min(WR/0.75, 1) × min(Trades/30, 1) / (1 + MaxDD/1000)`",
        "",
        "## Top 30 Configurations (by Composite Score)",
        "",
        "| Rank | Config | Trades | WR | PnL% | PF | Max DD% | +/total | TP | SL | BE | Time | Max CL | Score |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for rank, row in enumerate(agg_rows[:30], 1):
        lines.append(
            f"| {rank} | `{row['config']}` | {row['total_trades']} | "
            f"{row['win_rate']:.1%} | {row['avg_pnl_pct']:.2f}% | "
            f"{row['profit_factor']:.2f} | {row['max_dd']:.1f}% | "
            f"{row['pairs_profitable']}/{row['pairs_tested']} | "
            f"{row['tp_exits']} | {row['sl_exits']} | {row['be_exits']} | "
            f"{row['time_exits']} | {row['max_consec_losses']} | "
            f"{row['score']:.4f} |"
        )

    # Per-pair breakdown for top 5
    lines += ["", "## Per-Pair Breakdown (Top 5 Configs)", ""]
    top5_labels = [cast(str, r["config"]) for r in agg_rows[:5]]
    for label in top5_labels:
        lines += [
            f"### `{label}`",
            "",
            "| Pair | Trades | WR | PnL% | PF | Max DD% | Avg Bars | Max CL |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in sorted(config_agg[label], key=lambda x: x.total_pnl_pct, reverse=True):
            lines.append(
                f"| {r.pair} | {r.trades} | {r.win_rate:.0%} | "
                f"{r.total_pnl_pct:.2f}% | {r.profit_factor:.2f} | "
                f"{r.max_drawdown_pct:.1f}% | {r.avg_bars_held:.0f} | "
                f"{r.max_consecutive_losses} |"
            )
        lines.append("")

    # Zero-trade configs
    zero_trade = [r for r in agg_rows if r["total_trades"] == 0]
    if zero_trade:
        lines += [f"## Configs with Zero Trades: {len(zero_trade)}", ""]

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path, md_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="MTF RSI Donchian Reversal Backtester")
    parser.add_argument(
        "--pairs",
        default=",".join(TIER1_PAIRS),
        help="Comma-separated pairs (default: Tier 1)",
    )
    parser.add_argument("--days", type=int, default=365, help="Days of history")
    parser.add_argument(
        "--sweep",
        default="baseline",
        choices=[
            "baseline",
            "pine",
            "atr-exits",
            "rsi",
            "structure",
            "filters",
            "exits",
            "costs",
            "full",
        ],
        help="Sweep type",
    )
    parser.add_argument("--output-dir", default="results")
    args = parser.parse_args()

    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    configs = get_sweep_configs(args.sweep)

    print("=== MTF RSI Donchian Reversal Backtester ===")
    print(f"Pairs:       {len(pairs)} ({', '.join(pairs)})")
    print(f"Sweep:       {args.sweep}")
    print(f"Configs:     {len(configs)}")
    print(f"Total runs:  {len(configs) * len(pairs)}")
    print(f"Days:        {args.days}")
    print()

    # Fetch data
    pair_data: dict[str, dict[str, pd.DataFrame]] = {}
    pair_caches: dict[str, PairCache] = {}
    print("[FETCHING DATA]")
    for pair in pairs:
        print(f"  {pair}")
        data = fetch_pair(pair, args.days)
        if data is not None:
            pair_data[pair] = data

    if not pair_data:
        print("No data fetched. Aborting.")
        return 1

    print(f"\nFetched {len(pair_data)}/{len(pairs)} pairs")

    # Build caches
    print("\n[PRE-COMPUTING INDICATORS]")
    for pair, mtf in pair_data.items():
        print(f"  {pair}...", end=" ", flush=True)
        t0 = time.time()
        pair_caches[pair] = build_pair_cache(pair, mtf["1h"], mtf["30m"], mtf["15m"])
        print(f"done ({time.time() - t0:.1f}s)")

    # Run backtests
    print(f"\n[RUNNING {len(configs) * len(pair_caches)} BACKTESTS]")
    results: list[ConfigResult] = []
    run_count = 0
    t0 = time.time()

    for cfg in configs:
        for pair in pair_caches:
            cache = pair_caches[pair]
            data_1h = pair_data[pair]["1h"]
            r = run_config(pair, cache, data_1h, **cfg)
            results.append(r)
            run_count += 1

        total_elapsed = time.time() - t0
        rate = run_count / total_elapsed if total_elapsed > 0 else 1
        remaining = (len(configs) * len(pair_caches) - run_count) / rate
        print(
            f"  Config {run_count // len(pair_caches)}/{len(configs)} | "
            f"{run_count} runs | {remaining:.0f}s remaining",
            flush=True,
        )

    total_time = time.time() - t0
    print(f"\nCompleted {run_count} backtests in {total_time:.0f}s ({total_time / 60:.1f}m)")

    cutoffs = {
        pair: pd.Timestamp(cache.index_15m[int(len(cache.index_15m) * IS_FRACTION)])
        for pair, cache in pair_caches.items()
        if cache.index_15m
    }
    csv_path, md_path = write_outputs(results, Path(args.output_dir), cutoffs)
    print(f"\nSaved CSV: {csv_path}")
    print(f"Saved MD:  {md_path}")

    # Quick summary
    config_agg: dict[str, list[ConfigResult]] = {}
    for r in results:
        config_agg.setdefault(r.config_label, []).append(r)

    ranked = []
    for label, crs in config_agg.items():
        dev_trades, dev_wins, dev_pnl, dev_pf, _dev_dd = develop_metrics(
            crs, cutoffs, holdout=False
        )
        if dev_trades < 5:
            continue
        wr = dev_wins / dev_trades if dev_trades else 0.0
        ranked.append((label, dev_trades, dev_pnl, dev_pf, wr))
    ranked.sort(key=lambda x: x[2], reverse=True)

    print("\n=== TOP 10 CONFIGS (develop window, ≥5 trades, by PnL%) ===")
    for i, (label, trades, pnl, pf, wr) in enumerate(ranked[:10], 1):
        print(f"  {i:2d}. {label}")
        print(f"      {trades} trades | WR {wr:.0%} | avg PnL {pnl:.2f}% | PF {pf:.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
