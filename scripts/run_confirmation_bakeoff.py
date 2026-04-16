#!/usr/bin/env python3
"""Run confirmation bake-off backtests for the manual trading agent.

Initial matrix:
- V0: MTF RSI alignment only
- V1: Current breakout logic
- V2: Reversal breakout logic

Outputs:
- results/confirmation_bakeoff_<timestamp>.csv
- results/confirmation_bakeoff_<timestamp>.md
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

sys.path.insert(0, "/app")

import pandas as pd

from src.data.dukascopy_fetcher import get_multi_timeframe_data_dukascopy
from src.data.fetcher import DataFetcher
from src.indicators.adx import calculate_adx
from src.indicators.high_low import previous_rolling_highest_high, previous_rolling_lowest_low
from src.indicators.rsi import calculate_rsi

# Default spread in pips per pair (used when no live spread available)
DEFAULT_SPREAD_PIPS: dict[str, float] = {
    "EUR/USD": 1.0,
    "GBP/USD": 1.5,
    "USD/JPY": 1.2,
    "USD/CHF": 1.5,
    "AUD/USD": 1.2,
    "NZD/USD": 1.8,
    "USD/CAD": 1.5,
    "EUR/GBP": 1.2,
    "EUR/JPY": 1.5,
    "GBP/JPY": 2.5,
    "EUR/CHF": 1.8,
    "EUR/CAD": 2.0,
    "GBP/CHF": 2.0,
    "AUD/CAD": 2.0,
    "AUD/JPY": 1.8,
    "NZD/JPY": 2.0,
    "GBP/AUD": 2.5,
    "EUR/AUD": 2.0,
}

ADX_TREND_THRESHOLD = 25.0


def _fetch_dukascopy_mtf(pair: str, days: int) -> dict[str, pd.DataFrame]:
    """Fetch multi-timeframe data from Dukascopy and format for bakeoff."""
    symbol = pair.replace("/", "")
    end = datetime.now(UTC)
    start = end - timedelta(days=days)

    print(f"  Downloading {days}d of M1 data from Dukascopy (this may take a while)...")
    raw, _fetch_summary = get_multi_timeframe_data_dukascopy(
        symbol,
        start,
        end,
        timeframes=["h1", "m30", "m15"],
    )

    result: dict[str, pd.DataFrame] = {}
    key_map = {"h1": "1h", "m30": "30m", "m15": "15m"}
    for dk_key, bk_key in key_map.items():
        if dk_key not in raw or raw[dk_key].empty:
            continue
        df = raw[dk_key].copy()
        if "datetime" in df.columns:
            df = df.set_index("datetime")
        df.index = pd.to_datetime(df.index, utc=True)
        df = df.sort_index()
        result[bk_key] = df

    return result


@dataclass
class VariantResult:
    pair: str
    variant: str
    trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    total_pnl_pct: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    max_drawdown_pct: float


def latest_rsi_at_or_before(df: pd.DataFrame, ts: pd.Timestamp, period: int = 14) -> float | None:
    subset = df.loc[:ts]
    if len(subset) < period + 1:
        return None
    return calculate_rsi(subset["close"].tolist()[-50:], period)


def calc_atr(df: pd.DataFrame, idx: int, period: int = 14) -> float | None:
    if idx < period:
        return None
    highs = df["high"].tolist()[idx - period : idx + 1]
    lows = df["low"].tolist()[idx - period : idx + 1]
    closes = df["close"].tolist()[idx - period : idx + 1]
    if len(highs) < period + 1:
        return None
    trs = []
    for i in range(1, len(highs)):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        trs.append(max(tr1, tr2, tr3))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def run_variant(
    pair: str,
    variant: str,
    data_1h: pd.DataFrame,
    data_30m: pd.DataFrame,
    data_15m: pd.DataFrame,
    buffer_pips: float = 0.0,
    confirm_bars: int = 0,
) -> VariantResult:
    rsi_overbought = 70.0
    rsi_oversold = 30.0
    lookback = 20
    reward_ratio = 1.5
    sl_atr_multiplier = 2.0
    pip_size = 0.01 if "JPY" in pair else 0.0001
    spread_pips = DEFAULT_SPREAD_PIPS.get(pair, 2.0)
    spread_price = spread_pips * pip_size
    max_hold_bars = 16  # 4h on 15m bars
    balance = 10000.0
    peak = balance
    max_dd_pct = 0.0

    position: Literal["buy", "sell", None] = None
    entry_price = tp = sl = 0.0
    entry_idx = 0
    trade_pnls: list[float] = []

    # Track alignment age for confirm_bars window
    alignment_start: int | None = None
    alignment_direction: str | None = None

    highs = data_15m["high"].tolist()
    lows = data_15m["low"].tolist()
    closes = data_15m["close"].tolist()

    for i in range(lookback + 20, len(data_15m)):
        ts = data_15m.index[i]
        close = closes[i]
        high = highs[i]
        low = lows[i]

        rsi_15 = calculate_rsi(closes[max(0, i - 50) : i + 1], 14)
        rsi_30 = latest_rsi_at_or_before(data_30m, ts, 14)
        rsi_1h = latest_rsi_at_or_before(data_1h, ts, 14)
        if rsi_15 is None or rsi_30 is None or rsi_1h is None:
            continue

        hh_prev = previous_rolling_highest_high(highs, lookback, i)
        ll_prev = previous_rolling_lowest_low(lows, lookback, i)
        atr = calc_atr(data_15m, i, 14)
        if hh_prev is None or ll_prev is None or atr is None:
            continue

        up_trigger = hh_prev + buffer_pips * pip_size
        down_trigger = ll_prev - buffer_pips * pip_size

        adx_window = 2 * 14 + 1
        if i >= adx_window:
            adx = calculate_adx(
                highs[i - adx_window + 1 : i + 1],
                lows[i - adx_window + 1 : i + 1],
                closes[i - adx_window + 1 : i + 1],
                period=14,
            )
            if adx is not None and adx >= ADX_TREND_THRESHOLD:
                continue

        all_oversold = rsi_1h < rsi_oversold and rsi_30 < rsi_oversold and rsi_15 < rsi_oversold
        all_overbought = (
            rsi_1h > rsi_overbought and rsi_30 > rsi_overbought and rsi_15 > rsi_overbought
        )

        # Track alignment age
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

        # Check confirm_bars window
        within_window = confirm_bars == 0 or bars_since <= confirm_bars

        current_signal: Literal["buy", "sell", None] = None
        if variant == "V0":
            if all_oversold and within_window:
                current_signal = "buy"
            elif all_overbought and within_window:
                current_signal = "sell"
        elif variant == "V1":
            if all_oversold and close < down_trigger and within_window:
                current_signal = "buy"
            elif all_overbought and close > up_trigger and within_window:
                current_signal = "sell"
        elif variant == "V2":
            # Reversal confirmation: wick through level, close back inside with optional buffer
            if all_oversold and low <= down_trigger and close > ll_prev and within_window:
                current_signal = "buy"
            elif all_overbought and high >= up_trigger and close < hh_prev and within_window:
                current_signal = "sell"
        elif variant == "V2R":
            # Opposite-direction Structural Break Reversal: BUY breaks above HH, SELL breaks below LL
            if all_oversold and close > up_trigger and within_window:
                current_signal = "buy"
            elif all_overbought and close < down_trigger and within_window:
                current_signal = "sell"
        else:
            raise ValueError(f"Unknown variant: {variant}")

        # manage open trade
        if position is not None:
            exit_price = None
            if position == "buy":
                if low <= sl:
                    exit_price = sl
                elif high >= tp:
                    exit_price = tp
            else:
                if high >= sl:
                    exit_price = sl
                elif low <= tp:
                    exit_price = tp

            if exit_price is None and i - entry_idx >= max_hold_bars:
                exit_price = close

            if exit_price is not None:
                pnl_pct = (
                    (exit_price - entry_price) / entry_price
                    if position == "buy"
                    else (entry_price - exit_price) / entry_price
                )
                pnl = balance * pnl_pct
                balance += pnl
                trade_pnls.append(pnl)
                peak = max(peak, balance)
                dd_pct = ((peak - balance) / peak) * 100 if peak > 0 else 0.0
                max_dd_pct = max(max_dd_pct, dd_pct)
                position = None

        # open new trade only if flat
        if position is None and current_signal is not None:
            position = current_signal
            entry_price = close + spread_price if current_signal == "buy" else close - spread_price
            entry_idx = i
            if position == "buy":
                tp = entry_price + atr * reward_ratio
                sl = entry_price - atr * sl_atr_multiplier
            else:
                tp = entry_price - atr * reward_ratio
                sl = entry_price + atr * sl_atr_multiplier

    wins = sum(1 for pnl in trade_pnls if pnl > 0)
    losses = sum(1 for pnl in trade_pnls if pnl <= 0)
    total_pnl = sum(trade_pnls)
    total_pnl_pct = ((balance - 10000.0) / 10000.0) * 100
    avg_win = sum(p for p in trade_pnls if p > 0) / wins if wins else 0.0
    avg_loss = sum(abs(p) for p in trade_pnls if p <= 0) / losses if losses else 0.0
    gross_win = sum(p for p in trade_pnls if p > 0)
    gross_loss = sum(abs(p) for p in trade_pnls if p <= 0)
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)

    return VariantResult(
        pair=pair,
        variant=variant,
        trades=len(trade_pnls),
        wins=wins,
        losses=losses,
        win_rate=(wins / len(trade_pnls)) if trade_pnls else 0.0,
        total_pnl=total_pnl,
        total_pnl_pct=total_pnl_pct,
        profit_factor=profit_factor,
        avg_win=avg_win,
        avg_loss=avg_loss,
        max_drawdown_pct=max_dd_pct,
    )


def write_outputs(results: list[VariantResult], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"confirmation_bakeoff_{stamp}.csv"
    md_path = output_dir / f"confirmation_bakeoff_{stamp}.md"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "pair",
                "variant",
                "trades",
                "wins",
                "losses",
                "win_rate",
                "total_pnl",
                "total_pnl_pct",
                "profit_factor",
                "avg_win",
                "avg_loss",
                "max_drawdown_pct",
            ]
        )
        for r in results:
            writer.writerow(
                [
                    r.pair,
                    r.variant,
                    r.trades,
                    r.wins,
                    r.losses,
                    f"{r.win_rate:.4f}",
                    f"{r.total_pnl:.2f}",
                    f"{r.total_pnl_pct:.2f}",
                    f"{r.profit_factor:.2f}",
                    f"{r.avg_win:.2f}",
                    f"{r.avg_loss:.2f}",
                    f"{r.max_drawdown_pct:.2f}",
                ]
            )

    by_pair: dict[str, list[VariantResult]] = {}
    for r in results:
        by_pair.setdefault(r.pair, []).append(r)

    lines = ["# Confirmation Bake-off Results", ""]
    for pair, pair_results in by_pair.items():
        lines.append(f"## {pair}")
        lines.append("")
        lines.append("| Variant | Trades | Win Rate | Total PnL % | Profit Factor | Max DD % |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        pair_results = sorted(
            pair_results, key=lambda r: (r.total_pnl_pct, r.profit_factor), reverse=True
        )
        for r in pair_results:
            lines.append(
                f"| {r.variant} | {r.trades} | {r.win_rate:.1%} | {r.total_pnl_pct:.2f}% | {r.profit_factor:.2f} | {r.max_drawdown_pct:.2f}% |"
            )
        best = pair_results[0]
        lines.append("")
        lines.append(
            f"**Best:** {best.variant} (PnL {best.total_pnl_pct:.2f}%, PF {best.profit_factor:.2f})"
        )
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run confirmation bake-off backtests")
    parser.add_argument("--pairs", default="EUR/GBP,USD/JPY,EUR/CAD,EUR/CHF,GBP/CHF")
    parser.add_argument("--variants", default="V0,V1,V2")
    parser.add_argument("--output-dir", default="/app/results")
    parser.add_argument("--buffers", default="0.0")
    parser.add_argument("--confirm-bars", default="0", help="Comma-separated confirm_bars values")
    parser.add_argument(
        "--source",
        default="twelvedata",
        choices=["twelvedata", "dukascopy"],
        help="Data source (default: twelvedata)",
    )
    parser.add_argument("--days", type=int, default=60, help="Days of history (default: 60)")
    args = parser.parse_args()

    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    variants = [v.strip().upper() for v in args.variants.split(",") if v.strip()]
    buffers = [float(b.strip()) for b in args.buffers.split(",") if b.strip()]
    confirm_bars_list = [int(c.strip()) for c in args.confirm_bars.split(",") if c.strip()]
    use_dukascopy = args.source == "dukascopy"
    fetcher = DataFetcher() if not use_dukascopy else None

    end_date = datetime.now(UTC)
    start_date = end_date - timedelta(days=args.days)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    results: list[VariantResult] = []
    for pair in pairs:
        print(f"[FETCH] {pair} (source={args.source}, days={args.days})")
        if use_dukascopy:
            mtf = _fetch_dukascopy_mtf(pair, args.days)
        else:
            mtf = fetcher.fetch_multi_timeframe(pair, start=start_str, end=end_str)
        if any(tf not in mtf or mtf[tf].empty for tf in ["1h", "30m", "15m"]):
            print("  skipped: insufficient data")
            continue
        for variant in variants:
            for buffer_pips in buffers:
                for cb in confirm_bars_list:
                    variant_label = f"{variant}_b{buffer_pips:g}_c{cb}"
                    print(f"  [RUN] {variant_label}")
                    result = run_variant(
                        pair,
                        variant,
                        mtf["1h"],
                        mtf["30m"],
                        mtf["15m"],
                        buffer_pips=buffer_pips,
                        confirm_bars=cb,
                    )
                    result.variant = variant_label
                    print(
                        f"    trades={result.trades} pnl={result.total_pnl_pct:.2f}% pf={result.profit_factor:.2f}"
                    )
                    results.append(result)

    if not results:
        print("No results produced")
        return 1

    csv_path, md_path = write_outputs(results, Path(args.output_dir))
    print(f"\nSaved CSV: {csv_path}")
    print(f"Saved MD:  {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
