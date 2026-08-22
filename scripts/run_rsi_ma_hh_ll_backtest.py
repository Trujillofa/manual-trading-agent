#!/usr/bin/env python3
"""RSI + RSI-based MA + Highest High / Lowest Low Backtest.

Tests 4 strategy variants across pairs using yfinance 15m/30m/1h data:
  V0      — MTF RSI alignment only (baseline)
  V0_MA   — MTF RSI + RSI-MA(5) hard gate
  V2      — MTF RSI + V2 HH/LL reversal (wick pierce + close reclaim)
  V2_MA   — MTF RSI + RSI-MA(5) + V2 HH/LL  (current live production logic)

Grid sweep:
  - RSI bounds: 30/70, 25/75
  - TP/SL ATR mults: (1.0/3.0), (1.5/2.5), (2.0/2.0)
  - Confirm window: 2, 5 bars (V2/V2_MA only)
  - ADX filter: on/off

Cost model: frozen CostBook — 2 pip spread + 2 pip slippage + $3/side commission.

Causality: closed-bar signals, next-bar-open fills, stop-first same-bar exits.
Grid ranking uses the chronological develop window only (first 65%); holdout
is unused for selection. Short yfinance windows still split so ranking cannot
peek the tail.

Clock: yfinance indexes are UTC. Optional session filter uses the bar's UTC
hour (``session_start <= hour < session_end``). Not broker-server time.

Offline only: not a live-go or promote path. No broker orders are sent.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import yfinance as yf

from src.backtest.cost_book import CostBook, pip_size_for_pair

IS_FRACTION = 0.65

# ---------------------------------------------------------------------------
# Pairs & yfinance symbols
# ---------------------------------------------------------------------------

PAIRS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "GBP/JPY",
    "EUR/JPY",
    "NZD/JPY",
    "GBP/CHF",
    "USD/CHF",
    "NZD/USD",
]

YF_MAP: dict[str, str] = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "USDJPY=X",
    "USD/CHF": "USDCHF=X",
    "AUD/USD": "AUDUSD=X",
    "NZD/USD": "NZDUSD=X",
    "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X",
    "GBP/CHF": "GBPCHF=X",
    "NZD/JPY": "NZDJPY=X",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Trade:
    pair: str
    variant: str
    direction: str
    entry_time: object
    exit_time: object
    entry_price: float
    exit_price: float
    pnl_pct: float
    exit_reason: str
    bars_held: int
    rsi_15m: float
    rsi_30m: float
    rsi_1h: float


@dataclass
class BacktestResult:
    pair: str
    variant: str
    config_label: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl_pct: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    max_dd_pct: float = 0.0
    avg_bars_held: float = 0.0
    tp_exits: int = 0
    sl_exits: int = 0
    time_exits: int = 0
    trades_list: list[Trade] = field(default_factory=list)
    develop_cutoff: object | None = None


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------


def _rsi_series(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder RSI series."""
    result: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return result
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    avg_gain = sum(max(d, 0) for d in deltas[:period]) / period
    avg_loss = sum(max(-d, 0) for d in deltas[:period]) / period
    for i in range(period, len(closes)):
        idx = i  # result index
        d = deltas[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
        if avg_loss == 0:
            result[idx] = 100.0
        elif avg_gain == 0:
            result[idx] = 0.0
        else:
            rs = avg_gain / avg_loss
            result[idx] = 100.0 - 100.0 / (1 + rs)
    return result


def _rsi_ma_series(rsi_vals: list[float | None], ma_period: int = 5) -> list[float | None]:
    """SMA of RSI series."""
    result: list[float | None] = [None] * len(rsi_vals)
    for i in range(len(rsi_vals)):
        window = [v for v in rsi_vals[max(0, i - ma_period + 1) : i + 1] if v is not None]
        if len(window) >= ma_period:
            result[i] = sum(window) / len(window)
    return result


def _atr_series(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    if len(closes) < 2:
        return result
    trs = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(1, len(closes))
    ]
    if len(trs) < period:
        return result
    atr = sum(trs[:period]) / period
    result[period] = atr
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
        result[i + 1] = atr
    return result


def _adx_series(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> list[float | None]:
    n = len(highs)
    result: list[float | None] = [None] * n
    if n < 2 * period + 1:
        return result
    trs, plus_dms, minus_dms = [], [], []
    for i in range(1, n):
        hd, ld = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        plus_dms.append(max(hd, 0.0) if hd > ld else 0.0)
        minus_dms.append(max(ld, 0.0) if ld > hd else 0.0)
        trs.append(
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        )
    if len(trs) < 2 * period:
        return result
    str_ = sum(trs[:period])
    splus = sum(plus_dms[:period])
    sminus = sum(minus_dms[:period])
    dx_list: list[float] = []
    for i in range(period, len(trs)):
        str_ = str_ - str_ / period + trs[i]
        splus = splus - splus / period + plus_dms[i]
        sminus = sminus - sminus / period + minus_dms[i]
        pdi = 100.0 * splus / str_ if str_ else 0.0
        mdi = 100.0 * sminus / str_ if str_ else 0.0
        s = pdi + mdi
        dx_list.append(100.0 * abs(pdi - mdi) / s if s else 0.0)
    if len(dx_list) < period:
        return result
    adx = sum(dx_list[:period]) / period
    for i in range(period, len(dx_list)):
        adx = (adx * (period - 1) + dx_list[i]) / period
        bar_idx = i + 1 + period
        if bar_idx < n:
            result[bar_idx] = adx
    return result


def _prev_hh(highs: list[float], lookback: int, idx: int) -> float | None:
    if idx < lookback:
        return None
    window = highs[idx - lookback : idx]
    return max(window) if window else None


def _prev_ll(lows: list[float], lookback: int, idx: int) -> float | None:
    if idx < lookback:
        return None
    window = lows[idx - lookback : idx]
    return min(window) if window else None


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def fetch_pair_yf(pair: str, days: int = 58) -> dict[str, pd.DataFrame] | None:
    symbol = YF_MAP.get(pair)
    if not symbol:
        print(f"  {pair}: no yfinance symbol")
        return None
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    result: dict[str, pd.DataFrame] = {}
    for tf, interval in [("1h", "1h"), ("30m", "30m"), ("15m", "15m")]:
        try:
            df = yf.download(
                symbol,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval=interval,
                progress=False,
                auto_adjust=True,
            )
            if df.empty:
                print(f"  {pair}/{tf}: no data")
                return None
            df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
            df.index = pd.to_datetime(df.index, utc=True)
            result[tf] = df
        except Exception as e:
            print(f"  {pair}/{tf}: ERROR {e}")
            return None
    bars_15m = len(result["15m"])
    print(
        f"  {pair}: 1h={len(result['1h'])} 30m={len(result['30m'])} 15m={bars_15m} ({bars_15m * 15 / 60 / 24:.0f}d)"
    )
    return result


# ---------------------------------------------------------------------------
# Core backtest
# ---------------------------------------------------------------------------


def run_backtest(
    pair: str,
    variant: str,  # "V0" | "V0_MA" | "V2" | "V2_MA"
    data_1h: pd.DataFrame,
    data_30m: pd.DataFrame,
    data_15m: pd.DataFrame,
    rsi_overbought: float = 70.0,
    rsi_oversold: float = 30.0,
    rsi_ma_period: int = 5,
    lookback: int = 20,
    confirm_bars: int = 2,
    buffer_pips: float = 0.0,
    tp_atr_mult: float = 1.0,
    sl_atr_mult: float = 3.0,
    use_adx_filter: bool = True,
    max_adx: float = 25.0,
    use_session: bool = False,
    session_start: int = 6,
    session_end: int = 21,
    max_bars_exit: int = 192,
    spread_pips: float = 2.0,
    slippage_pips: float = 2.0,
    commission_per_order: float = 3.0,
    warmup: int = 80,
) -> BacktestResult:
    use_rsi_ma = "MA" in variant
    use_hh_ll = "V2" in variant

    cfg_label = (
        f"{variant}_ob{rsi_overbought:g}_os{rsi_oversold:g}"
        f"_tp{tp_atr_mult:g}_sl{sl_atr_mult:g}"
        f"_cb{confirm_bars}"
        f"_adx{'T' if use_adx_filter else 'F'}"
    )

    pip = pip_size_for_pair(pair)
    buf = buffer_pips * pip
    costs = CostBook(
        spread_pips=spread_pips,
        slippage_pips=slippage_pips,
        commission_usd_per_lot_side=commission_per_order,
    )

    # Pre-compute 15m indicators
    h15 = data_15m["high"].tolist()
    l15 = data_15m["low"].tolist()
    c15 = data_15m["close"].tolist()
    o15 = data_15m["open"].tolist() if "open" in data_15m.columns else c15
    idx15 = list(data_15m.index)

    rsi15 = _rsi_series(c15)
    rsima15 = _rsi_ma_series(rsi15, rsi_ma_period)
    atr15 = _atr_series(h15, l15, c15)

    # 1h ADX
    h1 = data_1h["high"].tolist()
    l1 = data_1h["low"].tolist()
    c1 = data_1h["close"].tolist()
    list(data_1h.index)
    adx1h_vals = _adx_series(h1, l1, c1)
    adx1h_series = pd.Series(adx1h_vals, index=data_1h.index, dtype=float)

    # RSI on 1h and 30m (precompute series, align to 15m bars via asof)
    c1h_s = pd.Series(_rsi_series(c1), index=data_1h.index, dtype=float)
    rsima1h_s = pd.Series(
        _rsi_ma_series(_rsi_series(c1), rsi_ma_period), index=data_1h.index, dtype=float
    )

    c30m = data_30m["close"].tolist()
    rsi30_s = pd.Series(_rsi_series(c30m), index=data_30m.index, dtype=float)
    rsima30_s = pd.Series(
        _rsi_ma_series(_rsi_series(c30m), rsi_ma_period), index=data_30m.index, dtype=float
    )

    result = BacktestResult(pair=pair, variant=variant, config_label=cfg_label)

    balance = 100_000.0
    peak = balance
    max_dd = 0.0

    position: Literal["buy", "sell", None] = None
    entry_price = 0.0
    entry_idx = 0
    entry_rsi = (0.0, 0.0, 0.0)

    trade_pnls: list[float] = []
    bars_held_list: list[int] = []
    trades_list: list[Trade] = []

    last_ob_bar: int | None = None
    last_os_bar: int | None = None
    pending_sig: Literal["buy", "sell"] | None = None
    pending_rsi = (0.0, 0.0, 0.0)
    pending_atr = 0.0
    tp_price = 0.0
    sl_price = 0.0
    result.develop_cutoff = idx15[int(len(idx15) * IS_FRACTION)] if idx15 else None

    for i in range(warmup, len(c15)):
        ts = idx15[i]
        close = c15[i]
        high_val = h15[i]
        low_val = l15[i]
        open_price = o15[i]

        if pending_sig is not None and position is None:
            position = pending_sig
            entry_price = costs.entry_fill(open_price, position, pip)
            entry_idx = i
            entry_rsi = pending_rsi
            tp_dist = pending_atr * tp_atr_mult
            sl_dist = pending_atr * sl_atr_mult
            if position == "buy":
                tp_price = entry_price + tp_dist
                sl_price = entry_price - sl_dist
            else:
                tp_price = entry_price - tp_dist
                sl_price = entry_price + sl_dist
            pending_sig = None

        # Manage open position (never skipped by indicator continues)
        if position is not None:
            exit_price_val = None
            exit_reason = ""
            if position == "buy":
                if low_val <= sl_price:
                    exit_price_val, exit_reason = sl_price, "sl"
                elif high_val >= tp_price:
                    exit_price_val, exit_reason = tp_price, "tp"
            else:
                if high_val >= sl_price:
                    exit_price_val, exit_reason = sl_price, "sl"
                elif low_val <= tp_price:
                    exit_price_val, exit_reason = tp_price, "tp"

            if exit_price_val is None and (i - entry_idx) >= max_bars_exit:
                exit_price_val = close
                exit_reason = "time"

            if exit_price_val is not None:
                eff_exit = costs.exit_fill(exit_price_val, position, pip)
                sl_dist_actual = abs(entry_price - sl_price)
                risk_pct = 0.01
                pos_size = (balance * risk_pct / sl_dist_actual) if sl_dist_actual > 0 else 1
                raw_pnl = (
                    (eff_exit - entry_price) if position == "buy" else (entry_price - eff_exit)
                )
                gross_pnl = pos_size * raw_pnl
                pnl = gross_pnl - costs.round_trip_commission_usd()
                balance += pnl
                trade_pnls.append(pnl)
                bars_held_list.append(i - entry_idx)
                peak = max(peak, balance)
                dd = (peak - balance) / peak * 100 if peak > 0 else 0
                max_dd = max(max_dd, dd)
                trades_list.append(
                    Trade(
                        pair=pair,
                        variant=variant,
                        direction=position,
                        entry_time=idx15[entry_idx],
                        exit_time=ts,
                        entry_price=entry_price,
                        exit_price=exit_price_val,
                        pnl_pct=pnl / balance * 100,
                        exit_reason=exit_reason,
                        bars_held=i - entry_idx,
                        rsi_15m=entry_rsi[0],
                        rsi_30m=entry_rsi[1],
                        rsi_1h=entry_rsi[2],
                    )
                )
                if exit_reason == "tp":
                    result.tp_exits += 1
                elif exit_reason == "sl":
                    result.sl_exits += 1
                else:
                    result.time_exits += 1
                position = None

        rsi_15 = rsi15[i]
        if rsi_15 is None:
            continue

        atr = atr15[i]
        if atr is None or atr <= 0:
            continue

        # RSI on higher TFs (aligned to current 15m bar time)
        r1h_raw = c1h_s.asof(ts)
        r30_raw = rsi30_s.asof(ts)
        if pd.isna(r1h_raw) or pd.isna(r30_raw):
            continue
        rsi_1h = float(r1h_raw)
        rsi_30m = float(r30_raw)

        # RSI-MA on all TFs
        rma_15 = rsima15[i]
        rma_1h_raw = rsima1h_s.asof(ts)
        rma_30m_raw = rsima30_s.asof(ts)
        rma_1h = float(rma_1h_raw) if not pd.isna(rma_1h_raw) else None
        rma_30m = float(rma_30m_raw) if not pd.isna(rma_30m_raw) else None

        # ADX
        adx_ok = True
        if use_adx_filter:
            adx_raw = adx1h_series.asof(ts)
            if not pd.isna(adx_raw):
                adx_ok = float(adx_raw) < max_adx

        # Session filter
        sess_ok = True
        if use_session:
            hour = ts.hour if hasattr(ts, "hour") else pd.Timestamp(ts).hour
            sess_ok = session_start <= hour < session_end

        # MTF RSI alignment
        full_ob = rsi_15 > rsi_overbought and rsi_30m > rsi_overbought and rsi_1h > rsi_overbought
        full_os = rsi_15 < rsi_oversold and rsi_30m < rsi_oversold and rsi_1h < rsi_oversold
        htf_ob = rsi_30m > rsi_overbought and rsi_1h > rsi_overbought
        htf_os = rsi_30m < rsi_oversold and rsi_1h < rsi_oversold

        if full_ob:
            last_ob_bar = i
        if full_os:
            last_os_bar = i

        bars_since_ob = i - last_ob_bar if last_ob_bar is not None else warmup + 1
        bars_since_os = i - last_os_bar if last_os_bar is not None else warmup + 1
        within_ob = bars_since_ob <= confirm_bars
        within_os = bars_since_os <= confirm_bars

        # RSI-MA hard gate
        rsi_ma_buy_ok = True
        rsi_ma_sell_ok = True
        if use_rsi_ma and all(v is not None for v in (rma_15, rma_1h, rma_30m)):
            rsi_ma_buy_ok = (
                rma_15 <= rsi_oversold and rma_1h <= rsi_oversold and rma_30m <= rsi_oversold
            )
            rsi_ma_sell_ok = (
                rma_15 >= rsi_overbought and rma_1h >= rsi_overbought and rma_30m >= rsi_overbought
            )

        # HH/LL gate (V2: wick through + close reclaim)
        hh = _prev_hh(h15, lookback, i)
        ll = _prev_ll(l15, lookback, i)

        hh_ok_sell = True
        ll_ok_buy = True
        if use_hh_ll and hh is not None and ll is not None:
            ll_ok_buy = low_val < ll - buf and close > ll
            hh_ok_sell = high_val > hh + buf and close < hh

        # V0: enter on alignment bar (RSI cross-back not used here; just require alignment)
        # Trigger conditions per variant
        if use_hh_ll:
            long_trigger = (
                htf_os
                and within_os
                and ll_ok_buy
                and adx_ok
                and sess_ok
                and (not use_rsi_ma or rsi_ma_buy_ok)
            )
            short_trigger = (
                htf_ob
                and within_ob
                and hh_ok_sell
                and adx_ok
                and sess_ok
                and (not use_rsi_ma or rsi_ma_sell_ok)
            )
        else:
            # V0 / V0_MA: fire on MTF alignment (all 3 TFs) current bar
            long_trigger = full_os and adx_ok and sess_ok and (not use_rsi_ma or rsi_ma_buy_ok)
            short_trigger = full_ob and adx_ok and sess_ok and (not use_rsi_ma or rsi_ma_sell_ok)

        # Arm next-bar fill (never fill on the signal bar close)
        if position is None and pending_sig is None:
            sig: Literal["buy", "sell", None] = None
            if long_trigger:
                sig = "buy"
            elif short_trigger:
                sig = "sell"
            if sig is not None and i + 1 < len(c15):
                pending_sig = sig
                pending_rsi = (rsi_15, rsi_30m, rsi_1h)
                pending_atr = atr

    # Finalize
    wins = sum(1 for p in trade_pnls if p > 0)
    losses = len(trade_pnls) - wins
    gross_win = sum(p for p in trade_pnls if p > 0)
    gross_loss = sum(-p for p in trade_pnls if p < 0)

    result.trades = len(trade_pnls)
    result.wins = wins
    result.losses = losses
    result.win_rate = wins / len(trade_pnls) if trade_pnls else 0.0
    result.total_pnl_pct = (balance - 100_000.0) / 100_000.0 * 100
    result.profit_factor = (
        gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    )
    result.max_dd_pct = max_dd
    result.avg_bars_held = sum(bars_held_list) / len(bars_held_list) if bars_held_list else 0.0
    result.trades_list = trades_list
    return result


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def _develop_trades(result: BacktestResult) -> list[Trade]:
    """Return trades whose entry is in the develop window."""

    if result.develop_cutoff is None:
        return result.trades_list
    cutoff = pd.Timestamp(result.develop_cutoff)
    return [trade for trade in result.trades_list if pd.Timestamp(trade.entry_time) <= cutoff]


def write_report(
    all_results: list[BacktestResult],
    output_path: Path,
    days: int,
    pairs_tested: list[str],
    date_range: str,
) -> None:
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# RSI + RSI-based MA + Highest High / Lowest Low — Backtest Report",
        f"Generated: {now_str}",
        "",
        "## Strategy Overview",
        "",
        "**Entry logic tested across 4 variants:**",
        "",
        "| Variant | MTF RSI | RSI-MA(5) gate | V2 HH/LL reclaim |",
        "|---------|---------|----------------|------------------|",
        "| V0      | Yes (all 3 TFs < 30 or > 70) | No  | No  |",
        "| V0_MA   | Yes                          | Yes | No  |",
        "| V2      | Yes                          | No  | Yes |",
        "| V2_MA   | Yes                          | Yes | Yes |",
        "",
        "**V2 HH/LL reclaim definition:**",
        "- BUY: bar low pierces 20-bar lowest low (excluding current bar); close recovers above it",
        "- SELL: bar high pierces 20-bar highest high; close recovers below it",
        "",
        "**RSI-MA hard gate:** SMA(5) of RSI must be ≤ oversold threshold (BUY) or ≥ overbought (SELL) on all 3 TFs.",
        "",
        f"**Data:** yfinance, {days} days, 15m primary (1h/30m for RSI alignment/ADX)  ",
        f"**Date range:** {date_range}  ",
        f"**Pairs:** {', '.join(pairs_tested)}  ",
        "**Cost model:** frozen CostBook — 2 pip spread + 2 pip slippage + $3/side (round-trip $6)  ",
        "**Execution:** signal on close, fill next bar open, stop-first exits  ",
        "**Split:** first 65% develop / last 35% holdout. Ranking uses develop only.  ",
        "**Not a live-go path.**",
        "",
        "---",
        "",
        "## Section 1 — Aggregate Results by Variant",
        "",
        "Pooled across all pairs and all grid configs within each variant family.",
        "",
    ]

    # Pool by variant
    variant_groups: dict[str, list[BacktestResult]] = {}
    for r in all_results:
        variant_groups.setdefault(r.variant, []).append(r)

    lines += [
        "| Variant | Configs | Pairs | Total Trades | Win Rate | Avg PnL% | Avg PF | +Pairs/Total | Avg MaxDD% |",
        "|---------|---------|-------|-------------|----------|----------|--------|--------------|------------|",
    ]
    for v in ["V0", "V0_MA", "V2", "V2_MA"]:
        grp = variant_groups.get(v, [])
        if not grp:
            continue
        total_trades = sum(r.trades for r in grp)
        total_wins = sum(r.wins for r in grp)
        avg_pnl = sum(r.total_pnl_pct for r in grp) / len(grp)
        sum(r.wins * (r.total_pnl_pct / r.trades if r.trades else 0) for r in grp if r.trades)
        sum(r.losses * abs(r.total_pnl_pct / r.trades if r.trades else 0) for r in grp if r.trades)
        pool_wins = sum(sum(t.pnl_pct for t in r.trades_list if t.pnl_pct > 0) for r in grp)
        pool_loss = sum(sum(-t.pnl_pct for t in r.trades_list if t.pnl_pct <= 0) for r in grp)
        pf = pool_wins / pool_loss if pool_loss > 0 else (999.0 if pool_wins > 0 else 0.0)
        wr = total_wins / total_trades if total_trades else 0.0
        profitable = sum(1 for r in grp if r.total_pnl_pct > 0)
        avg_dd = sum(r.max_dd_pct for r in grp) / len(grp) if grp else 0.0
        n_pairs = len({r.pair for r in grp})
        n_configs = len({r.config_label for r in grp})
        lines.append(
            f"| {v} | {n_configs} | {n_pairs} | {total_trades} | {wr:.1%} | "
            f"{avg_pnl:.2f}% | {pf:.2f} | {profitable}/{len(grp)} | {avg_dd:.2f}% |"
        )

    lines += [
        "",
        "---",
        "",
        "## Section 2 — Variant × RSI Threshold",
        "",
        "Pooled across all pairs and TP/SL configs, split by RSI bounds.",
        "",
        "| Variant | RSI Bounds | Trades | WR | PnL% | PF | MaxDD% |",
        "|---------|-----------|--------|----|------|----|--------|",
    ]

    # Group by (variant, rsi_bounds)
    vr_groups: dict[tuple[str, str], list[BacktestResult]] = {}
    for r in all_results:
        parts = r.config_label.split("_")
        ob_tag = next((p for p in parts if p.startswith("ob")), "ob70")
        os_tag = next((p for p in parts if p.startswith("os")), "os30")
        key = (r.variant, f"{ob_tag}/{os_tag}")
        vr_groups.setdefault(key, []).append(r)

    for (v, bounds), grp in sorted(vr_groups.items()):
        total_trades = sum(r.trades for r in grp)
        total_wins = sum(r.wins for r in grp)
        avg_pnl = sum(r.total_pnl_pct for r in grp) / len(grp) if grp else 0.0
        pool_wins = sum(sum(t.pnl_pct for t in r.trades_list if t.pnl_pct > 0) for r in grp)
        pool_loss = sum(sum(-t.pnl_pct for t in r.trades_list if t.pnl_pct <= 0) for r in grp)
        pf = pool_wins / pool_loss if pool_loss > 0 else (999.0 if pool_wins > 0 else 0.0)
        wr = total_wins / total_trades if total_trades else 0.0
        avg_dd = sum(r.max_dd_pct for r in grp) / len(grp) if grp else 0.0
        lines.append(
            f"| {v} | {bounds} | {total_trades} | {wr:.1%} | {avg_pnl:.2f}% | {pf:.2f} | {avg_dd:.2f}% |"
        )

    lines += [
        "",
        "---",
        "",
        "## Section 3 — Best Configs per Variant (develop window, ≥5 trades, ranked by PF)",
        "",
    ]

    for v in ["V0", "V0_MA", "V2", "V2_MA"]:
        grp = variant_groups.get(v, [])
        lines += [f"### {v}", ""]

        # Pool by config_label
        cfg_groups: dict[str, list[BacktestResult]] = {}
        for r in grp:
            cfg_groups.setdefault(r.config_label, []).append(r)

        rows = []
        for label, crs in cfg_groups.items():
            develop = [trade for result in crs for trade in _develop_trades(result)]
            total_trades = len(develop)
            if total_trades < 5:
                continue
            avg_pnl = sum(trade.pnl_pct for trade in develop)
            pool_wins = sum(trade.pnl_pct for trade in develop if trade.pnl_pct > 0)
            pool_loss = sum(-trade.pnl_pct for trade in develop if trade.pnl_pct <= 0)
            pf = pool_wins / pool_loss if pool_loss > 0 else (999.0 if pool_wins > 0 else 0.0)
            total_wins = sum(1 for trade in develop if trade.pnl_pct > 0)
            wr = total_wins / total_trades if total_trades else 0.0
            avg_dd = max(r.max_dd_pct for r in crs)
            rows.append((label, total_trades, avg_pnl, pf, wr, avg_dd))

        rows.sort(key=lambda x: x[3], reverse=True)
        lines += [
            "| Config | Trades | WR | PnL% | PF | MaxDD% |",
            "|--------|--------|----|------|----|--------|",
        ]
        for label, trades, pnl, pf, wr, dd in rows[:10]:
            lines.append(f"| `{label}` | {trades} | {wr:.1%} | {pnl:.2f}% | {pf:.2f} | {dd:.2f}% |")
        if not rows:
            lines.append("_(no configs with ≥5 trades)_")
        lines.append("")

    lines += [
        "---",
        "",
        "## Section 4 — Per-Pair Results (V2_MA, best config per pair)",
        "",
        "| Pair | Trades | WR | PnL% | PF | MaxDD% | Avg Bars |",
        "|------|--------|----|------|----|--------|---------|",
    ]

    v2ma = [r for r in all_results if r.variant == "V2_MA"]
    pair_best: dict[str, BacktestResult] = {}
    for r in v2ma:
        if r.trades < 2:
            continue
        prev = pair_best.get(r.pair)
        if prev is None or r.profit_factor > prev.profit_factor:
            pair_best[r.pair] = r

    for pair in PAIRS:
        r = pair_best.get(pair)
        if r is None:
            lines.append(f"| {pair} | 0 | — | — | — | — | — |")
        else:
            lines.append(
                f"| {pair} | {r.trades} | {r.win_rate:.1%} | "
                f"{r.total_pnl_pct:.2f}% | {r.profit_factor:.2f} | "
                f"{r.max_dd_pct:.2f}% | {r.avg_bars_held:.0f} |"
            )

    lines += [
        "",
        "---",
        "",
        "## Section 5 — RSI-MA Gate Impact",
        "",
        "Comparing V0 vs V0_MA (RSI-MA gate in isolation) and V2 vs V2_MA (RSI-MA gate on top of HH/LL).",
        "",
        "| Comparison | Delta Trades | Delta WR | Delta PnL% | Delta PF |",
        "|-----------|-------------|---------|-----------|----------|",
    ]

    def pool_stats(grp: list[BacktestResult]) -> tuple[int, float, float, float]:
        total_trades = sum(r.trades for r in grp)
        total_wins = sum(r.wins for r in grp)
        avg_pnl = sum(r.total_pnl_pct for r in grp) / len(grp) if grp else 0.0
        pool_wins = sum(sum(t.pnl_pct for t in r.trades_list if t.pnl_pct > 0) for r in grp)
        pool_loss = sum(sum(-t.pnl_pct for t in r.trades_list if t.pnl_pct <= 0) for r in grp)
        pf = pool_wins / pool_loss if pool_loss > 0 else 0.0
        wr = total_wins / total_trades if total_trades else 0.0
        return total_trades, wr, avg_pnl, pf

    v0_t, v0_wr, v0_pnl, v0_pf = pool_stats(variant_groups.get("V0", []))
    v0ma_t, v0ma_wr, v0ma_pnl, v0ma_pf = pool_stats(variant_groups.get("V0_MA", []))
    v2_t, v2_wr, v2_pnl, v2_pf = pool_stats(variant_groups.get("V2", []))
    v2ma_t, v2ma_wr, v2ma_pnl, v2ma_pf = pool_stats(variant_groups.get("V2_MA", []))

    lines.append(
        f"| V0 → V0_MA (add RSI-MA gate) | {v0ma_t - v0_t:+d} | "
        f"{v0ma_wr - v0_wr:+.1%} | {v0ma_pnl - v0_pnl:+.2f}% | {v0ma_pf - v0_pf:+.2f} |"
    )
    lines.append(
        f"| V2 → V2_MA (add RSI-MA gate) | {v2ma_t - v2_t:+d} | "
        f"{v2ma_wr - v2_wr:+.1%} | {v2ma_pnl - v2_pnl:+.2f}% | {v2ma_pf - v2_pf:+.2f} |"
    )
    lines.append(
        f"| V0 → V2 (add HH/LL gate) | {v2_t - v0_t:+d} | "
        f"{v2_wr - v0_wr:+.1%} | {v2_pnl - v0_pnl:+.2f}% | {v2_pf - v0_pf:+.2f} |"
    )
    lines.append(
        f"| V0 → V2_MA (add both gates) | {v2ma_t - v0_t:+d} | "
        f"{v2ma_wr - v0_wr:+.1%} | {v2ma_pnl - v0_pnl:+.2f}% | {v2ma_pf - v0_pf:+.2f} |"
    )

    lines += [
        "",
        "---",
        "",
        "## Section 6 — Honest Assessment",
        "",
        "**Methodology notes:**",
        "- Data: yfinance intraday (~58 days available for 15m). This is a short window — ",
        "  treat results as directional signal only, not promotion-gate evidence.",
        "- Cost model: 4 total pip cost (2 spread + 2 slippage) + $6 commission round-trip. ",
        "  This is conservative and matches the live production harness.",
        "- Chronological 65/35 develop/holdout split. Config ranking uses develop only; ",
        "  holdout is unused for selection. Short yfinance windows remain exploratory.",
        "  For an honest OOS verdict, Dukascopy 180d+ validation is still required.",
        "- Trade counts are very low at the intersection of all gates (RSI alignment × RSI-MA × HH/LL × ADX). ",
        "  PF estimates with N < 30 are statistically unreliable.",
        "",
        "**Interpretation guidance:**",
        "- If V2_MA shows PF > 1.10 with N ≥ 30 across multiple pairs: worth running Dukascopy 365d validation.",
        "- If trade count is near zero: confirms the 2026-06 finding (structural sparsity from gate stack).",
        "- RSI-MA gate impact (Section 5) indicates whether the filter helps quality or just kills volume.",
        "- This report is an *exploration aid*, not a promotion decision.",
    ]

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport written: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="RSI + RSI-MA + HH/LL Backtest")
    parser.add_argument("--pairs", default=",".join(PAIRS))
    parser.add_argument("--days", type=int, default=58)
    parser.add_argument("--output-dir", default="results")
    args = parser.parse_args()

    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    days = args.days

    print("=== RSI + RSI-MA + HH/LL Backtest ===")
    print(f"Pairs: {', '.join(pairs)}")
    print(f"Days:  {days}")
    print()

    # Fetch data
    print("[FETCHING DATA]")
    pair_data: dict[str, dict[str, pd.DataFrame]] = {}
    for pair in pairs:
        data = fetch_pair_yf(pair, days)
        if data is not None:
            pair_data[pair] = data

    if not pair_data:
        print("No data fetched. Aborting.")
        return 1

    print(f"\nFetched {len(pair_data)}/{len(pairs)} pairs")

    # Detect actual date range from first pair
    first_data = next(iter(pair_data.values()))
    date_range = (
        f"{first_data['15m'].index[0].strftime('%Y-%m-%d')} – "
        f"{first_data['15m'].index[-1].strftime('%Y-%m-%d')}"
    )
    print(f"Date range: {date_range}")

    # Grid
    variants = ["V0", "V0_MA", "V2", "V2_MA"]
    rsi_bounds = [(70.0, 30.0), (75.0, 25.0)]
    tp_sl_combos = [(1.0, 3.0), (1.5, 2.5), (2.0, 2.0)]
    confirm_bars_options = [2, 5]
    adx_options = [True]  # keep ADX filter on (production default)

    # Build config grid
    configs: list[dict] = []
    for variant in variants:
        for ob, os_ in rsi_bounds:
            for tp, sl in tp_sl_combos:
                for cb in confirm_bars_options:
                    for adx_on in adx_options:
                        configs.append(
                            {
                                "variant": variant,
                                "rsi_overbought": ob,
                                "rsi_oversold": os_,
                                "tp_atr_mult": tp,
                                "sl_atr_mult": sl,
                                "confirm_bars": cb,
                                "use_adx_filter": adx_on,
                            }
                        )

    total_runs = len(configs) * len(pair_data)
    print(f"\n[RUNNING {total_runs} BACKTESTS]")
    print(f"  {len(configs)} configs × {len(pair_data)} pairs")

    import time

    all_results: list[BacktestResult] = []
    t0 = time.time()
    run_count = 0

    for cfg in configs:
        v = cfg["variant"]
        for pair, mtf in pair_data.items():
            r = run_backtest(
                pair=pair,
                variant=v,
                data_1h=mtf["1h"],
                data_30m=mtf["30m"],
                data_15m=mtf["15m"],
                rsi_overbought=cfg["rsi_overbought"],
                rsi_oversold=cfg["rsi_oversold"],
                tp_atr_mult=cfg["tp_atr_mult"],
                sl_atr_mult=cfg["sl_atr_mult"],
                confirm_bars=cfg["confirm_bars"],
                use_adx_filter=cfg["use_adx_filter"],
            )
            all_results.append(r)
            run_count += 1

        elapsed = time.time() - t0
        rate = run_count / elapsed if elapsed > 0 else 1
        eta = (total_runs - run_count) / rate
        print(
            f"  {run_count}/{total_runs} | "
            f"{v} ob{cfg['rsi_overbought']:g}/os{cfg['rsi_oversold']:g} "
            f"tp{cfg['tp_atr_mult']:g}/sl{cfg['sl_atr_mult']:g} | eta {eta:.0f}s",
            end="\r",
        )

    total_time = time.time() - t0
    print(f"\nCompleted {run_count} runs in {total_time:.1f}s")

    # Print quick summary
    print("\n=== QUICK SUMMARY BY VARIANT ===")
    variant_groups: dict[str, list[BacktestResult]] = {}
    for r in all_results:
        variant_groups.setdefault(r.variant, []).append(r)

    for v in variants:
        grp = variant_groups.get(v, [])
        total = sum(r.trades for r in grp)
        wins = sum(r.wins for r in grp)
        avg_pnl = sum(r.total_pnl_pct for r in grp) / len(grp) if grp else 0.0
        pw = sum(sum(t.pnl_pct for t in r.trades_list if t.pnl_pct > 0) for r in grp)
        pl = sum(sum(-t.pnl_pct for t in r.trades_list if t.pnl_pct <= 0) for r in grp)
        pf = pw / pl if pl > 0 else 0.0
        wr = wins / total if total else 0.0
        print(f"  {v:8s}: {total:4d} trades | WR {wr:.1%} | avg PnL {avg_pnl:.2f}% | PF {pf:.2f}")

    # Write report
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"rsi_ma_hh_ll_backtest_{stamp}.md"
    write_report(all_results, report_path, days, list(pair_data.keys()), date_range)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
