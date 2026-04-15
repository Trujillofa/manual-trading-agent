#!/usr/bin/env python3
"""Comprehensive entry configuration optimizer.

Tests all combinations of:
- Entry variant: V0 (RSI-only), V1 (continuation breakout), V2 (reversal breakout)
- RSI thresholds: 30/70, 35/65, 25/75
- Buffer pips: 0.0, 0.5, 1.0, 2.0, 5.0
- Confirm bars: 0, 1, 2, 3, 5
- TP/SL ratios: 1.5:2 (current), 1:1, 2:1, 1:3 (ATR multiples)

Uses Dukascopy M1 data resampled to 1h/30m/15m for accuracy.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import yfinance as yf

from src.data.dukascopy_fetcher import get_multi_timeframe_data_dukascopy
from src.indicators.adx import calculate_adx
from src.indicators.high_low import previous_rolling_highest_high, previous_rolling_lowest_low
from src.indicators.rsi import calculate_rsi


@dataclass
class TradeRecord:
    pair: str
    direction: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    exit_reason: str  # "tp", "sl", "timeout"
    bars_held: int
    rsi_1h: float
    rsi_30m: float
    rsi_15m: float


@dataclass
class ConfigResult:
    pair: str
    config_label: str
    variant: str
    rsi_ob: float
    rsi_os: float
    buffer_pips: float
    confirm_bars: int
    tp_mult: float
    sl_mult: float
    trades: int = 0
    wins: int = 0
    losses: int = 0
    timeouts: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_bars_held: float = 0.0
    trades_list: list[TradeRecord] = field(default_factory=list)


def _adx_at_bar(data_1h: pd.DataFrame, ts: pd.Timestamp, period: int = 14) -> float | None:
    """Compute ADX from 1h data up to timestamp ts."""
    subset = data_1h.loc[:ts]
    if len(subset) < period * 2 + 1:
        return None
    tail = subset.iloc[-(period * 3):]  # enough history for smoothing
    return calculate_adx(
        tail["high"].tolist(),
        tail["low"].tolist(),
        tail["close"].tolist(),
        period,
    )


def latest_rsi_at_or_before(df: pd.DataFrame, ts: pd.Timestamp, period: int = 14) -> float | None:
    subset = df.loc[:ts]
    if len(subset) < period + 1:
        return None
    return calculate_rsi(subset["close"].tolist()[-50:], period)


def calc_atr(highs: list[float], lows: list[float], closes: list[float], idx: int, period: int = 14) -> float | None:
    if idx < period:
        return None
    trs = []
    for i in range(idx - period + 1, idx + 1):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        trs.append(max(tr1, tr2, tr3))
    return sum(trs) / period if trs else None


def run_config(
    pair: str,
    data_1h: pd.DataFrame,
    data_30m: pd.DataFrame,
    data_15m: pd.DataFrame,
    variant: str = "V0",
    rsi_ob: float = 70.0,
    rsi_os: float = 30.0,
    buffer_pips: float = 0.0,
    confirm_bars: int = 0,
    tp_mult: float = 1.5,
    sl_mult: float = 2.0,
    max_hold_bars: int = 16,
    lookback: int = 20,
    adx_threshold: float = 0.0,
) -> ConfigResult:
    adx_label = f"_adx{adx_threshold:g}" if adx_threshold > 0 else ""
    label = f"{variant}_ob{rsi_ob:g}_os{rsi_os:g}_b{buffer_pips:g}_c{confirm_bars}_tp{tp_mult:g}_sl{sl_mult:g}{adx_label}"
    result = ConfigResult(
        pair=pair, config_label=label, variant=variant,
        rsi_ob=rsi_ob, rsi_os=rsi_os, buffer_pips=buffer_pips,
        confirm_bars=confirm_bars, tp_mult=tp_mult, sl_mult=sl_mult,
    )

    balance = 10000.0
    peak = balance
    max_dd_pct = 0.0

    position: Literal["buy", "sell", None] = None
    entry_price = tp = sl = 0.0
    entry_idx = 0
    entry_rsi = (0.0, 0.0, 0.0)
    trade_pnls: list[float] = []
    timeout_count = 0
    bars_held_list: list[int] = []

    alignment_start: int | None = None
    alignment_direction: str | None = None

    highs = data_15m["high"].tolist()
    lows = data_15m["low"].tolist()
    closes = data_15m["close"].tolist()

    pip_size = 0.01 if "JPY" in pair else 0.0001

    for i in range(lookback + 20, len(data_15m)):
        ts = data_15m.index[i]
        close = closes[i]
        high_val = highs[i]
        low_val = lows[i]

        rsi_15 = calculate_rsi(closes[max(0, i - 50): i + 1], 14)
        rsi_30 = latest_rsi_at_or_before(data_30m, ts, 14)
        rsi_1h = latest_rsi_at_or_before(data_1h, ts, 14)
        if rsi_15 is None or rsi_30 is None or rsi_1h is None:
            continue

        hh_prev = previous_rolling_highest_high(highs, lookback, i)
        ll_prev = previous_rolling_lowest_low(lows, lookback, i)
        atr = calc_atr(highs, lows, closes, i, 14)
        if hh_prev is None or ll_prev is None or atr is None or atr <= 0:
            continue

        up_trigger = hh_prev + buffer_pips * pip_size
        down_trigger = ll_prev - buffer_pips * pip_size

        all_oversold = rsi_1h < rsi_os and rsi_30 < rsi_os and rsi_15 < rsi_os
        all_overbought = rsi_1h > rsi_ob and rsi_30 > rsi_ob and rsi_15 > rsi_ob

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
            if all_oversold and low_val <= down_trigger and close > ll_prev and within_window:
                current_signal = "buy"
            elif all_overbought and high_val >= up_trigger and close < hh_prev and within_window:
                current_signal = "sell"
        elif variant == "V2R":
            if all_oversold and close > up_trigger and within_window:
                current_signal = "buy"
            elif all_overbought and close < down_trigger and within_window:
                current_signal = "sell"

        # Manage open position
        if position is not None:
            exit_price = None
            exit_reason = ""
            if position == "buy":
                if low_val <= sl:
                    exit_price = sl
                    exit_reason = "sl"
                elif high_val >= tp:
                    exit_price = tp
                    exit_reason = "tp"
            else:
                if high_val >= sl:
                    exit_price = sl
                    exit_reason = "sl"
                elif low_val <= tp:
                    exit_price = tp
                    exit_reason = "tp"

            if exit_price is None and i - entry_idx >= max_hold_bars:
                exit_price = close
                exit_reason = "timeout"
                timeout_count += 1

            if exit_price is not None:
                pnl_pct = (exit_price - entry_price) / entry_price if position == "buy" else (entry_price - exit_price) / entry_price
                pnl = balance * pnl_pct
                balance += pnl
                trade_pnls.append(pnl)
                bars_held_list.append(i - entry_idx)
                peak = max(peak, balance)
                dd_pct = ((peak - balance) / peak) * 100 if peak > 0 else 0.0
                max_dd_pct = max(max_dd_pct, dd_pct)

                result.trades_list.append(TradeRecord(
                    pair=pair, direction=position, entry_price=entry_price,
                    exit_price=exit_price, pnl=pnl, pnl_pct=pnl_pct * 100,
                    exit_reason=exit_reason, bars_held=i - entry_idx,
                    rsi_1h=entry_rsi[0], rsi_30m=entry_rsi[1], rsi_15m=entry_rsi[2],
                ))
                position = None

        # ADX trend filter: skip mean-reversion signals in trending markets
        if current_signal is not None and adx_threshold > 0:
            adx_1h = _adx_at_bar(data_1h, ts)
            if adx_1h is not None and adx_1h >= adx_threshold:
                current_signal = None

        # Open new trade
        if position is None and current_signal is not None:
            position = current_signal
            entry_price = close
            entry_idx = i
            entry_rsi = (rsi_1h, rsi_30, rsi_15)
            if position == "buy":
                tp = entry_price + atr * tp_mult
                sl = entry_price - atr * sl_mult
            else:
                tp = entry_price - atr * tp_mult
                sl = entry_price + atr * sl_mult

    # Compute stats
    wins = sum(1 for p in trade_pnls if p > 0)
    losses = sum(1 for p in trade_pnls if p <= 0)
    gross_win = sum(p for p in trade_pnls if p > 0)
    gross_loss = sum(abs(p) for p in trade_pnls if p <= 0)

    result.trades = len(trade_pnls)
    result.wins = wins
    result.losses = losses
    result.timeouts = timeout_count
    result.win_rate = wins / len(trade_pnls) if trade_pnls else 0.0
    result.total_pnl = sum(trade_pnls)
    result.total_pnl_pct = ((balance - 10000.0) / 10000.0) * 100
    result.profit_factor = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    result.avg_win = gross_win / wins if wins else 0.0
    result.avg_loss = gross_loss / losses if losses else 0.0
    result.max_drawdown_pct = max_dd_pct
    result.avg_bars_held = sum(bars_held_list) / len(bars_held_list) if bars_held_list else 0.0
    return result


def fetch_mtf_data(pair: str, days: int) -> dict[str, pd.DataFrame]:
    """Fetch multi-timeframe data using yfinance directly (free, no API limits)."""
    symbol = pair.replace("/", "") + "=X"
    period = f"{days}d"
    print(f"  Fetching {days}d via yfinance ({symbol})...")
    result: dict[str, pd.DataFrame] = {}
    for interval, key in [("1h", "1h"), ("30m", "30m"), ("15m", "15m")]:
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)
            if df.empty:
                print(f"    {key}: empty")
                continue
            df.columns = [c.lower() for c in df.columns]
            if "adj close" in df.columns:
                df = df.drop(columns=["adj close"])
            if "dividends" in df.columns:
                df = df.drop(columns=["dividends"])
            if "stock splits" in df.columns:
                df = df.drop(columns=["stock splits"])
            if "capital gains" in df.columns:
                df = df.drop(columns=["capital gains"])
            df.index = pd.to_datetime(df.index, utc=True)
            df = df.sort_index()
            result[key] = df
            print(f"    {key}: {len(df)} bars")
        except Exception as e:
            print(f"    {key}: error - {e}")
    return result


def write_outputs(results: list[ConfigResult], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"entry_optimization_{stamp}.csv"
    md_path = output_dir / f"entry_optimization_{stamp}.md"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "pair", "config", "variant", "rsi_ob", "rsi_os", "buffer_pips",
            "confirm_bars", "tp_mult", "sl_mult", "trades", "wins", "losses",
            "timeouts", "win_rate", "total_pnl_pct", "profit_factor",
            "avg_win", "avg_loss", "max_drawdown_pct", "avg_bars_held",
        ])
        for r in results:
            writer.writerow([
                r.pair, r.config_label, r.variant, r.rsi_ob, r.rsi_os,
                r.buffer_pips, r.confirm_bars, r.tp_mult, r.sl_mult,
                r.trades, r.wins, r.losses, r.timeouts,
                f"{r.win_rate:.4f}", f"{r.total_pnl_pct:.2f}",
                f"{r.profit_factor:.2f}", f"{r.avg_win:.2f}",
                f"{r.avg_loss:.2f}", f"{r.max_drawdown_pct:.2f}",
                f"{r.avg_bars_held:.1f}",
            ])

    # Aggregate across pairs for each config
    config_agg: dict[str, list[ConfigResult]] = {}
    for r in results:
        config_agg.setdefault(r.config_label, []).append(r)

    agg_rows: list[dict] = []
    for label, config_results in config_agg.items():
        total_trades = sum(r.trades for r in config_results)
        total_wins = sum(r.wins for r in config_results)
        total_timeouts = sum(r.timeouts for r in config_results)
        avg_pnl_pct = sum(r.total_pnl_pct for r in config_results) / len(config_results)
        gross_win = sum(r.avg_win * r.wins for r in config_results)
        gross_loss = sum(r.avg_loss * r.losses for r in config_results)
        pf = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
        max_dd = max(r.max_drawdown_pct for r in config_results)
        wr = total_wins / total_trades if total_trades else 0.0
        pairs_profitable = sum(1 for r in config_results if r.total_pnl_pct > 0)

        agg_rows.append({
            "config": label,
            "variant": config_results[0].variant,
            "total_trades": total_trades,
            "win_rate": wr,
            "avg_pnl_pct": avg_pnl_pct,
            "profit_factor": pf,
            "max_dd": max_dd,
            "timeouts": total_timeouts,
            "pairs_profitable": pairs_profitable,
            "pairs_tested": len(config_results),
        })

    # Sort by avg PnL % descending
    agg_rows.sort(key=lambda r: (r["avg_pnl_pct"], r["profit_factor"]), reverse=True)

    lines = [
        "# Entry Configuration Optimization Results",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Top 30 Configurations (by avg PnL %)",
        "",
        "| Rank | Config | Trades | Win Rate | Avg PnL % | PF | Max DD % | Timeouts | Pairs +/total |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(agg_rows[:30], 1):
        lines.append(
            f"| {rank} | `{row['config']}` | {row['total_trades']} | "
            f"{row['win_rate']:.1%} | {row['avg_pnl_pct']:.2f}% | "
            f"{row['profit_factor']:.2f} | {row['max_dd']:.1f}% | "
            f"{row['timeouts']} | {row['pairs_profitable']}/{row['pairs_tested']} |"
        )

    # Bottom 10 (worst)
    lines.extend([
        "",
        "## Bottom 10 Configurations",
        "",
        "| Config | Trades | Win Rate | Avg PnL % | PF | Max DD % |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for row in agg_rows[-10:]:
        lines.append(
            f"| `{row['config']}` | {row['total_trades']} | "
            f"{row['win_rate']:.1%} | {row['avg_pnl_pct']:.2f}% | "
            f"{row['profit_factor']:.2f} | {row['max_dd']:.1f}% |"
        )

    # Per-pair breakdown for top 5 configs
    lines.extend(["", "## Per-Pair Breakdown (Top 5 Configs)", ""])
    top5_labels = [r["config"] for r in agg_rows[:5]]
    for label in top5_labels:
        lines.append(f"### `{label}`")
        lines.append("")
        lines.append("| Pair | Trades | WR | PnL % | PF | Max DD % | Avg Bars |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for r in sorted(config_agg[label], key=lambda x: x.total_pnl_pct, reverse=True):
            lines.append(
                f"| {r.pair} | {r.trades} | {r.win_rate:.0%} | "
                f"{r.total_pnl_pct:.2f}% | {r.profit_factor:.2f} | "
                f"{r.max_drawdown_pct:.1f}% | {r.avg_bars_held:.0f} |"
            )
        lines.append("")

    # Configs with zero trades
    zero_trade = [r for r in agg_rows if r["total_trades"] == 0]
    if zero_trade:
        lines.extend([
            f"## Configs with Zero Trades: {len(zero_trade)}",
            "",
            "These configurations are too restrictive to generate any entries.",
            "",
        ])

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Entry configuration optimizer")
    parser.add_argument(
        "--pairs",
        default="EUR/USD,GBP/USD,USD/JPY,AUD/USD,EUR/GBP,EUR/JPY,GBP/JPY,GBP/CHF,EUR/AUD,EUR/CAD,AUD/NZD,NZD/USD",
        help="Comma-separated pairs",
    )
    parser.add_argument("--days", type=int, default=58, help="Days of history (yfinance limit ~60d for intraday)")
    parser.add_argument(
        "--variants", default="V0,V1,V2",
        help="Comma-separated variants",
    )
    parser.add_argument(
        "--rsi-thresholds", default="25/75,30/70,35/65",
        help="Comma-separated OS/OB pairs (e.g., 30/70,35/65)",
    )
    parser.add_argument(
        "--buffers", default="0.0,0.5,1.0,2.0",
        help="Comma-separated buffer pips (only used for V1/V2)",
    )
    parser.add_argument(
        "--confirm-bars", default="0,2,3,5",
        help="Comma-separated confirm bar values",
    )
    parser.add_argument(
        "--tp-sl-ratios", default="1.5:2.0,1.0:1.0,2.0:1.0,1.0:3.0,2.0:2.0,3.0:2.0",
        help="Comma-separated TP:SL ATR multiplier pairs",
    )
    parser.add_argument("--adx-threshold", type=float, default=0.0, help="ADX threshold (0=disabled, 25=recommended)")
    parser.add_argument("--source", default="yfinance", choices=["yfinance", "dukascopy"], help="Data source")
    parser.add_argument("--output-dir", default="results")
    parser.add_argument("--max-hold", type=int, default=16, help="Max bars to hold (15m)")
    args = parser.parse_args()

    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    variants = [v.strip().upper() for v in args.variants.split(",")]
    rsi_pairs = []
    for rp in args.rsi_thresholds.split(","):
        os_val, ob_val = rp.strip().split("/")
        rsi_pairs.append((float(os_val), float(ob_val)))
    buffers = [float(b.strip()) for b in args.buffers.split(",")]
    confirm_bars_list = [int(c.strip()) for c in args.confirm_bars.split(",")]
    tp_sl_pairs = []
    for ts in args.tp_sl_ratios.split(","):
        tp_val, sl_val = ts.strip().split(":")
        tp_sl_pairs.append((float(tp_val), float(sl_val)))

    # Calculate total configs
    v0_configs = len(rsi_pairs) * len(confirm_bars_list) * len(tp_sl_pairs)
    vx_configs = len(rsi_pairs) * len(buffers) * len(confirm_bars_list) * len(tp_sl_pairs)
    n_vx = sum(1 for v in variants if v in ("V1", "V2", "V2R"))
    total_configs = (v0_configs if "V0" in variants else 0) + n_vx * vx_configs
    total_runs = total_configs * len(pairs)
    adx_threshold = args.adx_threshold

    print("=== Entry Configuration Optimizer ===")
    print(f"Source: {args.source}")
    print(f"Pairs: {len(pairs)}")
    print(f"Variants: {variants}")
    print(f"RSI thresholds: {rsi_pairs}")
    print(f"Buffers: {buffers}")
    print(f"Confirm bars: {confirm_bars_list}")
    print(f"TP/SL ratios: {tp_sl_pairs}")
    print(f"ADX threshold: {adx_threshold} {'(disabled)' if adx_threshold == 0 else ''}")
    print(f"Total configs per pair: {total_configs}")
    print(f"Total runs: {total_runs}")
    print(f"Days: {args.days}")
    print()

    # Fetch data for all pairs first
    pair_data: dict[str, dict[str, pd.DataFrame]] = {}
    for pair in pairs:
        print(f"[FETCH] {pair}")
        try:
            if args.source == "dukascopy":
                end_date = datetime.now(UTC)
                start_date = end_date - timedelta(days=args.days)
                print(f"  Fetching {args.days}d via Dukascopy ({pair})...")
                mtf = get_multi_timeframe_data_dukascopy(
                    pair, start_date, end_date,
                    timeframes=["h1", "m30", "m15"],
                )
                # Remap keys to match yfinance format
                mtf_remapped: dict[str, pd.DataFrame] = {}
                key_map = {"h1": "1h", "m30": "30m", "m15": "15m"}
                for k, v in mtf.items():
                    mtf_remapped[key_map.get(k, k)] = v
                mtf = mtf_remapped
                for tf_key in ["1h", "30m", "15m"]:
                    if tf_key in mtf:
                        print(f"    {tf_key}: {len(mtf[tf_key])} bars")
            else:
                mtf = fetch_mtf_data(pair, args.days)
            if any(tf not in mtf or mtf[tf].empty for tf in ["1h", "30m", "15m"]):
                print("  SKIPPED: insufficient data")
                continue
            bars_15m = len(mtf["15m"])
            print(f"  OK: {bars_15m} bars on 15m ({bars_15m * 15 / 60 / 24:.0f} days)")
            pair_data[pair] = mtf
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

    if not pair_data:
        print("No data fetched, aborting")
        return 1

    print(f"\nFetched data for {len(pair_data)}/{len(pairs)} pairs")
    print(f"Running {total_configs} configs x {len(pair_data)} pairs = {total_configs * len(pair_data)} backtests\n")

    results: list[ConfigResult] = []
    run_count = 0
    t0 = time.time()

    for pair, mtf in pair_data.items():
        print(f"[BACKTEST] {pair}")
        pair_t0 = time.time()

        for variant in variants:
            for rsi_os, rsi_ob in rsi_pairs:
                buffer_list = [0.0] if variant == "V0" else buffers
                for buffer_pips in buffer_list:
                    for cb in confirm_bars_list:
                        for tp_mult, sl_mult in tp_sl_pairs:
                            r = run_config(
                                pair, mtf["1h"], mtf["30m"], mtf["15m"],
                                variant=variant, rsi_ob=rsi_ob, rsi_os=rsi_os,
                                buffer_pips=buffer_pips, confirm_bars=cb,
                                tp_mult=tp_mult, sl_mult=sl_mult,
                                max_hold_bars=args.max_hold,
                                adx_threshold=adx_threshold,
                            )
                            results.append(r)
                            run_count += 1

        elapsed = time.time() - pair_t0
        total_elapsed = time.time() - t0
        rate = run_count / total_elapsed if total_elapsed > 0 else 0
        remaining = (total_configs * len(pair_data) - run_count) / rate if rate > 0 else 0
        print(f"  {pair} done in {elapsed:.1f}s ({run_count} runs, ~{remaining / 60:.0f}m remaining)")

    total_time = time.time() - t0
    print(f"\nCompleted {run_count} backtests in {total_time:.0f}s ({total_time / 60:.1f}m)")

    csv_path, md_path = write_outputs(results, Path(args.output_dir))
    print(f"\nSaved CSV: {csv_path}")
    print(f"Saved MD:  {md_path}")

    # Quick summary
    config_agg: dict[str, list[ConfigResult]] = {}
    for r in results:
        config_agg.setdefault(r.config_label, []).append(r)

    print("\n=== TOP 10 CONFIGS ===")
    ranked = []
    for label, crs in config_agg.items():
        total_trades = sum(r.trades for r in crs)
        if total_trades < 5:
            continue
        avg_pnl = sum(r.total_pnl_pct for r in crs) / len(crs)
        ranked.append((label, total_trades, avg_pnl))
    ranked.sort(key=lambda x: x[2], reverse=True)

    for i, (label, trades, pnl) in enumerate(ranked[:10], 1):
        print(f"  {i}. {label} — {trades} trades, avg PnL {pnl:.2f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
