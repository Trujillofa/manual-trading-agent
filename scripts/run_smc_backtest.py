#!/usr/bin/env python3
"""Cost-aware backtest for LuxAlgo Smart Money Concepts swing structure.

The pasted TradingView indicator draws BOS/CHoCH labels but has no orders or exits.
This script evaluates preregistered interpretations plus an autosearch space:

* ``marker_baseline`` — enter on every swing BOS or CHoCH (close cross).
* ``bos_continuation`` — BOS only (trend continuation).
* ``zone_filtered`` — baseline with discount/premium half filter.
* ``ob_retest`` — arm on structure break, enter on order-block retest + rejection.
* ``htf_swing_map`` — structure on 1h bars, entries on 15m.
* ``choch_reversal`` — CHoCH only (character-change reversals).

Execution mirrors ``scripts/run_htf_fib_backtest.py``: signal on close, fill next bar
open, pessimistic stop-first intrabar exits, 2 pip spread/slippage, 1% equity risk.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import math
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_donchian_backtest import fetch_pair
from scripts.run_htf_fib_backtest import (
    DEFAULT_PAIRS,
    IS_FRACTION,
    BacktestResult,
    Trade,
    WindowStats,
    _pip_size,
    _resample_ohlc,
    _wilder_atr,
    aggregate_window,
    load_usd_conversion_closes,
    verdict,
)

BULLISH_LEG = 1
BEARISH_LEG = 0

EntryMode = Literal["immediate", "ob_retest", "htf_swing_map"]
TagFilter = Literal["all", "bos", "choch"]
StructureTimeframe = Literal["15m", "1h", "4h"]

_TIMEFRAME_FREQ = {"15m": "15min", "1h": "1h", "4h": "4h"}


@dataclass(frozen=True)
class StrategyConfig:
    """One SMC backtest interpretation."""

    name: str
    entry_mode: EntryMode = "immediate"
    tag_filter: TagFilter = "all"
    swing_length: int = 50
    structure_timeframe: StructureTimeframe = "15m"
    bos_only: bool = False
    require_zone: bool = False
    ob_retest_bars: int = 16
    atr_period: int = 14
    tp_atr: float = 1.5
    sl_atr: float = 1.5
    max_hold_bars: int = 32

    @property
    def effective_tag_filter(self) -> TagFilter:
        if self.bos_only:
            return "bos"
        return self.tag_filter

    @property
    def structure_spec(self) -> tuple[StructureTimeframe, int]:
        if self.entry_mode == "htf_swing_map":
            return self.structure_timeframe, self.swing_length
        return "15m", self.swing_length


CONFIGS = (
    StrategyConfig(name="marker_baseline"),
    StrategyConfig(name="bos_continuation", tag_filter="bos"),
    StrategyConfig(name="zone_filtered", require_zone=True),
    StrategyConfig(name="ob_retest", entry_mode="ob_retest"),
    StrategyConfig(name="htf_swing_map", entry_mode="htf_swing_map", structure_timeframe="1h"),
    StrategyConfig(name="choch_reversal", tag_filter="choch"),
)


@dataclass(frozen=True)
class StructureBreak:
    """Swing structure break detected on bar close."""

    bar_index: int
    direction: Literal["long", "short"]
    tag: Literal["BOS", "CHoCH"]
    pivot_level: float
    pivot_bar_index: int
    swing_top: float | None
    swing_bottom: float | None


@dataclass
class PivotState:
    current_level: float | None = None
    last_level: float | None = None
    crossed: bool = False
    bar_index: int = 0


@dataclass
class StructureTracker:
    """Python mirror of LuxAlgo swing pivot + BOS/CHoCH state."""

    swing_length: int
    leg: int = BEARISH_LEG
    trend_bias: int = 0
    swing_high: PivotState = field(default_factory=PivotState)
    swing_low: PivotState = field(default_factory=PivotState)
    swing_top: float | None = None
    swing_bottom: float | None = None

    def process_bar(
        self,
        bar_index: int,
        highs: list[float],
        lows: list[float],
        closes: list[float],
    ) -> StructureBreak | None:
        size = self.swing_length
        if bar_index < size:
            return None

        prev_leg = self.leg
        pivot_high = highs[bar_index - size]
        pivot_low = lows[bar_index - size]
        window_high = max(highs[bar_index - size + 1 : bar_index + 1])
        window_low = min(lows[bar_index - size + 1 : bar_index + 1])

        if pivot_high > window_high:
            self.leg = BEARISH_LEG
        elif pivot_low < window_low:
            self.leg = BULLISH_LEG

        if self.leg != prev_leg:
            if self.leg == BEARISH_LEG:
                self.swing_high.last_level = self.swing_high.current_level
                self.swing_high.current_level = pivot_high
                self.swing_high.crossed = False
                self.swing_high.bar_index = bar_index - size
                self.swing_top = pivot_high
            elif self.leg == BULLISH_LEG:
                self.swing_low.last_level = self.swing_low.current_level
                self.swing_low.current_level = pivot_low
                self.swing_low.crossed = False
                self.swing_low.bar_index = bar_index - size
                self.swing_bottom = pivot_low

        close = closes[bar_index]
        prev_close = closes[bar_index - 1]

        if (
            self.swing_high.current_level is not None
            and not self.swing_high.crossed
            and prev_close <= self.swing_high.current_level < close
        ):
            tag: Literal["BOS", "CHoCH"] = "CHoCH" if self.trend_bias == -1 else "BOS"
            self.swing_high.crossed = True
            self.trend_bias = 1
            return StructureBreak(
                bar_index=bar_index,
                direction="long",
                tag=tag,
                pivot_level=self.swing_high.current_level,
                pivot_bar_index=self.swing_high.bar_index,
                swing_top=self.swing_top,
                swing_bottom=self.swing_bottom,
            )

        if (
            self.swing_low.current_level is not None
            and not self.swing_low.crossed
            and prev_close >= self.swing_low.current_level > close
        ):
            tag = "CHoCH" if self.trend_bias == 1 else "BOS"
            self.swing_low.crossed = True
            self.trend_bias = -1
            return StructureBreak(
                bar_index=bar_index,
                direction="short",
                tag=tag,
                pivot_level=self.swing_low.current_level,
                pivot_bar_index=self.swing_low.bar_index,
                swing_top=self.swing_top,
                swing_bottom=self.swing_bottom,
            )

        return None


@dataclass
class PendingObRetest:
    direction: Literal["long", "short"]
    ob_high: float
    ob_low: float
    armed_bar: int
    expiry_bar: int
    atr: float


@dataclass
class PreparedSmcData:
    timestamps: list[pd.Timestamp]
    opens: list[float]
    highs: list[float]
    lows: list[float]
    closes: list[float]
    parsed_highs: list[float]
    parsed_lows: list[float]
    usd_per_quote: list[float]
    atr_by_period: dict[int, list[float]]
    breaks_by_spec: dict[tuple[StructureTimeframe, int], dict[int, StructureBreak]]


def _parse_ohlc(
    highs: list[float],
    lows: list[float],
    atr_measure: list[float],
) -> tuple[list[float], list[float]]:
    parsed_highs: list[float] = []
    parsed_lows: list[float] = []
    for high, low, atr_value in zip(highs, lows, atr_measure, strict=True):
        if math.isnan(atr_value):
            parsed_highs.append(high)
            parsed_lows.append(low)
            continue
        high_vol = (high - low) >= (2 * atr_value)
        parsed_highs.append(low if high_vol else high)
        parsed_lows.append(high if high_vol else low)
    return parsed_highs, parsed_lows


def build_break_schedule(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    swing_length: int,
) -> dict[int, StructureBreak]:
    tracker = StructureTracker(swing_length=swing_length)
    schedule: dict[int, StructureBreak] = {}
    for bar_index in range(len(closes)):
        event = tracker.process_bar(bar_index, highs, lows, closes)
        if event is not None:
            schedule[bar_index] = event
    return schedule


def map_htf_schedule_to_ltf(
    htf_timestamps: list[pd.Timestamp],
    htf_schedule: dict[int, StructureBreak],
    ltf_timestamps: list[pd.Timestamp],
) -> dict[int, StructureBreak]:
    """Map HTF close-time breaks onto the first 15m bar at/after HTF bar open."""

    if not ltf_timestamps:
        return {}
    ltf_epoch = [int(timestamp.value) for timestamp in ltf_timestamps]
    mapped: dict[int, StructureBreak] = {}
    for htf_index, event in htf_schedule.items():
        htf_time = htf_timestamps[htf_index]
        ltf_index = bisect.bisect_left(ltf_epoch, int(htf_time.value))
        if ltf_index >= len(ltf_timestamps):
            continue
        mapped[ltf_index] = StructureBreak(
            bar_index=ltf_index,
            direction=event.direction,
            tag=event.tag,
            pivot_level=event.pivot_level,
            pivot_bar_index=event.pivot_bar_index,
            swing_top=event.swing_top,
            swing_bottom=event.swing_bottom,
        )
    return mapped


def compute_order_block(
    break_event: StructureBreak,
    parsed_highs: list[float],
    parsed_lows: list[float],
) -> tuple[float, float]:
    start = max(0, break_event.pivot_bar_index)
    end = break_event.bar_index + 1
    if break_event.direction == "long":
        segment = parsed_lows[start:end]
        offset = segment.index(min(segment))
        index = start + offset
    else:
        segment = parsed_highs[start:end]
        offset = segment.index(max(segment))
        index = start + offset
    return parsed_highs[index], parsed_lows[index]


def _in_discount_half(close: float, top: float | None, bottom: float | None) -> bool:
    if top is None or bottom is None or top <= bottom:
        return False
    return close <= (top + bottom) / 2.0


def _in_premium_half(close: float, top: float | None, bottom: float | None) -> bool:
    if top is None or bottom is None or top <= bottom:
        return False
    return close >= (top + bottom) / 2.0


def _accept_break(break_event: StructureBreak, close: float, config: StrategyConfig) -> bool:
    tag_filter = config.effective_tag_filter
    if tag_filter == "bos" and break_event.tag != "BOS":
        return False
    if tag_filter == "choch" and break_event.tag != "CHoCH":
        return False
    if config.require_zone:
        if break_event.direction == "long":
            return _in_discount_half(close, break_event.swing_top, break_event.swing_bottom)
        return _in_premium_half(close, break_event.swing_top, break_event.swing_bottom)
    return True


def ob_retest_triggered(
    pending: PendingObRetest,
    bar_index: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> bool:
    if bar_index > pending.expiry_bar:
        return False
    if pending.direction == "long":
        if low > pending.ob_high or close < pending.ob_low:
            return False
        touched = low <= pending.ob_high
        rejected = close > open_price and close > pending.ob_low
        return touched and rejected
    if high < pending.ob_low or close > pending.ob_high:
        return False
    touched = high >= pending.ob_low
    rejected = close < open_price and close < pending.ob_high
    return touched and rejected


def ob_mitigated(
    pending: PendingObRetest,
    high: float,
    low: float,
    close: float,
) -> bool:
    if pending.direction == "long":
        return close < pending.ob_low
    return close > pending.ob_high


def _ensure_break_schedule(
    prepared: PreparedSmcData,
    frame: pd.DataFrame,
    spec: tuple[StructureTimeframe, int],
) -> None:
    if spec in prepared.breaks_by_spec:
        return
    timeframe, swing_length = spec
    if timeframe == "15m":
        schedule = build_break_schedule(
            prepared.highs,
            prepared.lows,
            prepared.closes,
            swing_length,
        )
    else:
        htf = _resample_ohlc(frame, _TIMEFRAME_FREQ[timeframe])
        htf_schedule = build_break_schedule(
            htf["high"].astype(float).tolist(),
            htf["low"].astype(float).tolist(),
            htf["close"].astype(float).tolist(),
            swing_length,
        )
        htf_timestamps = [pd.Timestamp(timestamp) for timestamp in htf.index]
        schedule = map_htf_schedule_to_ltf(htf_timestamps, htf_schedule, prepared.timestamps)
    prepared.breaks_by_spec[spec] = schedule


def prepare_smc_data(
    pair: str,
    data_15m: pd.DataFrame,
    *,
    atr_periods: set[int],
    break_specs: set[tuple[StructureTimeframe, int]] | None = None,
    usd_quote_close: pd.Series | None = None,
) -> PreparedSmcData:
    from scripts.run_htf_fib_backtest import _usd_per_quote_values

    data = data_15m[["open", "high", "low", "close"]].copy().sort_index()
    usd_per_quote = _usd_per_quote_values(pair, data, usd_quote_close)
    if any(math.isnan(value) for value in usd_per_quote):
        filled = pd.Series(usd_per_quote, dtype=float).ffill().bfill()
        usd_per_quote = [float(value) for value in filled.tolist()]
    atr_by_period = {
        period: _wilder_atr(data, period).astype(float).tolist() for period in atr_periods
    }
    atr200 = _wilder_atr(data, 200).astype(float).tolist()
    parsed_highs, parsed_lows = _parse_ohlc(
        data["high"].astype(float).tolist(),
        data["low"].astype(float).tolist(),
        atr200,
    )
    prepared = PreparedSmcData(
        timestamps=[pd.Timestamp(timestamp) for timestamp in data.index],
        opens=data["open"].astype(float).tolist(),
        highs=data["high"].astype(float).tolist(),
        lows=data["low"].astype(float).tolist(),
        closes=data["close"].astype(float).tolist(),
        parsed_highs=parsed_highs,
        parsed_lows=parsed_lows,
        usd_per_quote=usd_per_quote,
        atr_by_period=atr_by_period,
        breaks_by_spec={},
    )
    for spec in break_specs or set():
        _ensure_break_schedule(prepared, data, spec)
    return prepared


def run_prepared_backtest(
    pair: str,
    prepared: PreparedSmcData,
    config: StrategyConfig,
    *,
    spread_pips: float = 2.0,
    slippage_pips: float = 2.0,
    commission_per_order: float = 3.0,
    initial_balance: float = 100_000.0,
    risk_fraction: float = 0.01,
) -> BacktestResult:
    """Walk bars, apply SMC entry model, simulate ATR bracket exits."""

    atr = prepared.atr_by_period[config.atr_period]
    break_schedule = prepared.breaks_by_spec.get(config.structure_spec, {})
    result = BacktestResult(
        pair=pair,
        config=config.name,
        initial_capital_usd=initial_balance,
        ending_balance_usd=initial_balance,
    )
    balance = initial_balance
    position: Literal["long", "short"] | None = None
    pending_direction: Literal["long", "short"] | None = None
    pending_atr = 0.0
    pending_ob: PendingObRetest | None = None
    entry_mid = entry_price = stop_price = target_price = risk_distance = 0.0
    entry_index = 0
    entry_time = prepared.timestamps[0]
    pip = _pip_size(pair)
    spread = spread_pips * pip
    slippage = slippage_pips * pip

    for i, timestamp in enumerate(prepared.timestamps):
        open_price = prepared.opens[i]
        high = prepared.highs[i]
        low = prepared.lows[i]
        close = prepared.closes[i]

        if pending_direction is not None and position is None:
            position = pending_direction
            entry_index = i
            entry_time = timestamp
            entry_mid = open_price
            stop_distance = max(pending_atr * config.sl_atr, pip * 5)
            if position == "long":
                entry_price = entry_mid + spread + slippage
                stop_price = entry_price - stop_distance
                target_price = entry_price + pending_atr * config.tp_atr
            else:
                entry_price = entry_mid - spread - slippage
                stop_price = entry_price + stop_distance
                target_price = entry_price - pending_atr * config.tp_atr
            risk_distance = abs(entry_price - stop_price)
            pending_direction = None

        if position is not None:
            exit_price: float | None = None
            exit_reason = ""
            if i - entry_index > config.max_hold_bars:
                exit_price = open_price + (slippage if position == "short" else -slippage)
                exit_reason = "time"
            elif position == "long":
                if low <= stop_price:
                    exit_price = stop_price - slippage
                    exit_reason = "stop"
                elif high >= target_price:
                    exit_price = target_price - slippage
                    exit_reason = "target"
            else:
                if high >= stop_price:
                    exit_price = stop_price + slippage
                    exit_reason = "stop"
                elif low <= target_price:
                    exit_price = target_price + slippage
                    exit_reason = "target"
            if exit_price is not None and risk_distance > 0:
                net_move = (
                    exit_price - entry_price if position == "long" else entry_price - exit_price
                )
                if exit_reason == "stop":
                    gross_r = -1.0
                elif exit_reason == "target":
                    gross_r = config.tp_atr / config.sl_atr
                else:
                    mid_move = (
                        open_price - entry_mid if position == "long" else entry_mid - open_price
                    )
                    gross_r = mid_move / risk_distance
                usd_per_quote = prepared.usd_per_quote[i]
                if math.isnan(usd_per_quote):
                    raise ValueError(f"missing USD conversion at {timestamp}")
                risk_cash = initial_balance * risk_fraction
                quantity = risk_cash / (risk_distance * usd_per_quote)
                lots = quantity / 100_000.0
                commission = 2.0 * commission_per_order
                net_cash = quantity * net_move * usd_per_quote - commission
                net_r = net_cash / risk_cash if risk_cash > 0 else 0.0
                net_pnl_pct = net_cash / initial_balance * 100.0
                balance += net_cash
                result.trades.append(
                    Trade(
                        pair=pair,
                        config=config.name,
                        account_name="risk_fraction",
                        entry_time=entry_time,
                        exit_time=timestamp,
                        direction=position,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        gross_r=gross_r,
                        net_r=net_r,
                        net_pnl_usd=net_cash,
                        net_pnl_pct=net_pnl_pct,
                        lots=lots,
                    )
                )
                result.ending_balance_usd = balance
                position = None

        if position is not None or pending_direction is not None:
            continue

        if pending_ob is not None:
            if ob_mitigated(pending_ob, high, low, close):
                pending_ob = None
            elif ob_retest_triggered(pending_ob, i, open_price, high, low, close):
                if i + 1 < len(prepared.timestamps):
                    pending_direction = pending_ob.direction
                    pending_atr = pending_ob.atr
                pending_ob = None
                continue
            elif i > pending_ob.expiry_bar:
                pending_ob = None

        break_event = break_schedule.get(i)
        if break_event is None:
            continue

        current_atr = atr[i]
        if (
            math.isnan(current_atr)
            or current_atr <= 0
            or balance <= initial_balance * 0.05
            or not _accept_break(break_event, close, config)
        ):
            continue

        if config.entry_mode == "ob_retest":
            ob_high, ob_low = compute_order_block(
                break_event,
                prepared.parsed_highs,
                prepared.parsed_lows,
            )
            pending_ob = PendingObRetest(
                direction=break_event.direction,
                ob_high=ob_high,
                ob_low=ob_low,
                armed_bar=i,
                expiry_bar=i + config.ob_retest_bars,
                atr=current_atr,
            )
            continue

        if i + 1 < len(prepared.timestamps):
            pending_direction = break_event.direction
            pending_atr = current_atr

    return result


def config_from_dict(cfg: dict) -> StrategyConfig:
    entry_mode: EntryMode = cfg.get("entry_mode", "immediate")
    tag_filter: TagFilter = cfg.get("tag_filter", "all")
    structure_timeframe: StructureTimeframe = cfg.get("structure_timeframe", "15m")
    return StrategyConfig(
        name=str(cfg.get("name", "custom")),
        entry_mode=entry_mode,
        tag_filter=tag_filter,
        swing_length=int(cfg["swing_length"]),
        structure_timeframe=structure_timeframe,
        bos_only=bool(cfg.get("bos_only", False)),
        require_zone=bool(cfg.get("require_zone", False)),
        ob_retest_bars=int(cfg.get("ob_retest_bars", 16)),
        atr_period=int(cfg.get("atr_period", 14)),
        tp_atr=float(cfg.get("tp_atr", 1.5)),
        sl_atr=float(cfg.get("sl_atr", 1.5)),
        max_hold_bars=int(cfg.get("max_hold_bars", 32)),
    )


def run_backtest(
    pair: str,
    data_15m: pd.DataFrame,
    config: StrategyConfig,
    *,
    spread_pips: float = 2.0,
    slippage_pips: float = 2.0,
    commission_per_order: float = 3.0,
    initial_balance: float = 100_000.0,
    risk_fraction: float = 0.01,
    usd_quote_close: pd.Series | None = None,
) -> BacktestResult:
    prepared = prepare_smc_data(
        pair,
        data_15m,
        atr_periods={config.atr_period, 200},
        break_specs={config.structure_spec},
        usd_quote_close=usd_quote_close,
    )
    return run_prepared_backtest(
        pair,
        prepared,
        config,
        spread_pips=spread_pips,
        slippage_pips=slippage_pips,
        commission_per_order=commission_per_order,
        initial_balance=initial_balance,
        risk_fraction=risk_fraction,
    )


@dataclass(frozen=True)
class EvalRow:
    name: str
    score: float
    verdict: str
    is_stats: WindowStats
    oos_stats: WindowStats
    rationale: str


def score_window_stats(is_stats: WindowStats, oos_stats: WindowStats) -> float:
    if oos_stats.trades < 1:
        return float("-inf")
    overfit_gap = max(0.0, is_stats.total_net_pnl_pct - oos_stats.total_net_pnl_pct)
    lown = max(0, 30 - oos_stats.trades)
    return float(oos_stats.total_net_pnl_pct - 0.5 * overfit_gap - 0.25 * lown)


def evaluate_config_on_pairs(
    config: StrategyConfig,
    pair_data: dict[str, pd.DataFrame],
    prepared: dict[str, PreparedSmcData],
    cutoff_by_pair: dict[str, pd.Timestamp],
) -> EvalRow:
    results = [
        run_prepared_backtest(pair, prepared[pair], config)
        for pair in pair_data
    ]
    ins = aggregate_window(results, cutoff_by_pair, oos=False)
    oos = aggregate_window(results, cutoff_by_pair, oos=True)
    decision, reasons = verdict(ins, oos)
    score = score_window_stats(ins, oos)
    rationale = "; ".join(reasons) if reasons else "All minimum gates passed."
    return EvalRow(config.name, score, decision, ins, oos, rationale)


def write_comparison_report(
    rows: list[EvalRow],
    output_path: Path,
    *,
    title: str = "SMC Optimal Configuration Comparison",
) -> Path:
    ranked = sorted(rows, key=lambda row: row.score, reverse=True)
    best = ranked[0]
    lines = [
        f"# {title}",
        "",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Most optimal configuration",
        "",
        f"**{best.name}** ranks first by OOS-penalized score ({best.score:.4f}).",
        f"Rationale: {best.rationale}",
        "",
        "| Config | Score | Verdict | IS Trades | IS Net PF | IS Net PnL | "
        "OOS Trades | OOS Net PF | OOS Net PnL |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in ranked:
        lines.append(
            f"| `{row.name}` | {row.score:.4f} | {row.verdict} | {row.is_stats.trades} | "
            f"{row.is_stats.net_profit_factor:.2f} | {row.is_stats.total_net_pnl_pct:.2f}% | "
            f"{row.oos_stats.trades} | {row.oos_stats.net_profit_factor:.2f} | "
            f"{row.oos_stats.total_net_pnl_pct:.2f}% |"
        )
    lines.extend(
        (
            "",
            "## Notes",
            "",
            "- Ranking uses OOS net PnL minus overfit and low-N penalties (same family as HTF Fib autosearch).",
            "- A top score does not imply KEEP gates passed; check Verdict column.",
            "- Order blocks, internal structure, and FVG were not traded in this harness.",
            "",
        )
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def _write_report(
    output_dir: Path,
    rows: list[tuple[StrategyConfig, WindowStats, WindowStats, str, list[str]]],
    trades: list[Trade],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"smc_backtest_{stamp}.md"
    trades_path = output_dir / f"smc_backtest_{stamp}.csv"
    with trades_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "pair",
                "config",
                "account",
                "entry_time",
                "exit_time",
                "direction",
                "entry_price",
                "exit_price",
                "exit_reason",
                "gross_r",
                "net_r",
                "net_pnl_usd",
                "net_pnl_pct",
                "lots",
            )
        )
        for trade in trades:
            writer.writerow(
                (
                    trade.pair,
                    trade.config,
                    trade.account_name,
                    trade.entry_time.isoformat(),
                    trade.exit_time.isoformat(),
                    trade.direction,
                    f"{trade.entry_price:.6f}",
                    f"{trade.exit_price:.6f}",
                    trade.exit_reason,
                    f"{trade.gross_r:.6f}",
                    f"{trade.net_r:.6f}",
                    f"{trade.net_pnl_usd:.6f}",
                    f"{trade.net_pnl_pct:.6f}",
                    f"{trade.lots:.6f}",
                )
            )

    lines = [
        "# LuxAlgo Smart Money Concepts Backtest",
        "",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Variants: marker_baseline, bos_continuation, zone_filtered, ob_retest, "
        "htf_swing_map, choch_reversal.",
        "Data: Dukascopy 15-minute OHLC, 65% IS / 35% OOS chronological split.",
        "Execution: signal on close cross, entry next bar open, stop-first intrabar.",
        "Costs: 2.0 pip spread, 2.0 pip slippage per fill, $3 commission per order.",
        "Sizing: 1% equity risk per trade. Exits: 1.5 ATR TP / 1.5 ATR SL, 32-bar time stop.",
        "",
        "## Results",
        "",
        "| Config | Window | Trades | WR | Gross PF | Net PF | Net PnL | Max DD | Pairs + |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for config, ins, oos, decision, reasons in rows:
        for window, stats in (("IS", ins), ("OOS", oos)):
            lines.append(
                f"| `{config.name}` | {window} | {stats.trades} | {stats.win_rate:.1%} | "
                f"{stats.gross_profit_factor:.2f} | {stats.net_profit_factor:.2f} | "
                f"{stats.total_net_pnl_pct:.2f}% | {stats.max_drawdown_pct:.2f}% | "
                f"{stats.profitable_pairs}/{stats.tested_pairs} |"
            )
        lines.extend(
            (
                "",
                f"**{config.name}: {decision}.** "
                + ("; ".join(reasons) if reasons else "All minimum gates passed."),
                "",
            )
        )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path, trades_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", default=",".join(DEFAULT_PAIRS))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    args = parser.parse_args()
    pairs = [pair.strip() for pair in args.pairs.split(",") if pair.strip()]

    pair_data: dict[str, pd.DataFrame] = {}
    cutoff_by_pair: dict[str, pd.Timestamp] = {}
    for pair in pairs:
        frames = fetch_pair(pair, args.days)
        if frames is None:
            continue
        data_15m = frames["15m"].sort_index()
        pair_data[pair] = data_15m
        cutoff_by_pair[pair] = pd.Timestamp(data_15m.index[int(len(data_15m) * IS_FRACTION)])
    if not pair_data:
        print("No complete cached/fetched datasets were available.")
        return 1
    conversion_closes = load_usd_conversion_closes(pair_data, args.days)

    break_specs = {config.structure_spec for config in CONFIGS}
    atr_periods = {config.atr_period for config in CONFIGS} | {200}
    prepared = {
        pair: prepare_smc_data(
            pair,
            frame,
            atr_periods=atr_periods,
            break_specs=break_specs,
            usd_quote_close=conversion_closes[pair],
        )
        for pair, frame in pair_data.items()
    }

    report_rows: list[tuple[StrategyConfig, WindowStats, WindowStats, str, list[str]]] = []
    eval_rows: list[EvalRow] = []
    all_trades: list[Trade] = []
    for config in CONFIGS:
        row = evaluate_config_on_pairs(config, pair_data, prepared, cutoff_by_pair)
        eval_rows.append(row)
        ins, oos, decision, reasons = row.is_stats, row.oos_stats, row.verdict, [row.rationale]
        if row.rationale == "All minimum gates passed.":
            reasons = []
        report_rows.append((config, ins, oos, decision, reasons))
        results = [run_prepared_backtest(pair, prepared[pair], config) for pair in pair_data]
        all_trades.extend(trade for result in results for trade in result.trades)
        print(
            f"{config.name}: {decision} | "
            f"IS {ins.trades} trades net PF {ins.net_profit_factor:.2f} "
            f"PnL {ins.total_net_pnl_pct:.2f}% | "
            f"OOS {oos.trades} trades net PF {oos.net_profit_factor:.2f} "
            f"PnL {oos.total_net_pnl_pct:.2f}%"
        )
        if reasons and reasons != ["All minimum gates passed."]:
            print("  " + "; ".join(reasons))

    report_path, trades_path = _write_report(args.output_dir, report_rows, all_trades)
    compare_path = write_comparison_report(
        eval_rows,
        args.output_dir / "smc_optimal_comparison.md",
    )
    best = max(eval_rows, key=lambda row: row.score)
    print(f"Most optimal: {best.name} (score={best.score:.4f}, verdict={best.verdict})")
    print(f"Report: {report_path}")
    print(f"Trades: {trades_path}")
    print(f"Comparison: {compare_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())