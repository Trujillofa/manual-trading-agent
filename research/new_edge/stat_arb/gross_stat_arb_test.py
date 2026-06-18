#!/usr/bin/env python3
"""
Smallest gross-only stat-arb falsifier per STAT_ARB_CONTRACT_2026-06-18.md.

Daily pairs-trade on rolling hedge-ratio spread residuals. Zero friction (gross-first).
No optimization. Single parameter set across all candidate spreads.

Run:
  python -m research.new_edge.stat_arb.gross_stat_arb_test \
    --start 2016-01-01 --end 2026-06-01 \
    --output docs/research/stat_arb/STAT_ARB_GROSS_RESULTS_2026-06-18.md
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from research.new_edge.stat_arb.data.verify_stat_arb_data import (
    CANDIDATE_SPREADS,
    DEFAULT_SPREAD_PIPS,
)

LOOKBACK = 60
ENTRY_Z = 2.0
EXIT_Z = 0.0
TIME_STOP = 20
NOTIONAL_USD = 100_000.0
GROSS_PF_PASS = 1.10
MIN_TRADES = 30


@dataclass
class Trade:
    entry_idx: int
    exit_idx: int
    direction: int  # +1 long spread, -1 short spread
    gross_pnl: float
    bars_held: int
    exit_reason: str


def _to_yfinance_fx_ticker(pair: str) -> str:
    return pair.replace("/", "") + "=X"


def fetch_aligned_closes(leg_a: str, leg_b: str, start: datetime, end: datetime) -> pd.DataFrame:
    series: list[pd.Series] = []
    for pair in (leg_a, leg_b):
        ticker = _to_yfinance_fx_ticker(pair)
        df = yf.download(ticker, start=start.date(), end=end.date(), progress=False, interval="1d")
        if df is None or df.empty:
            raise RuntimeError(f"No data for {pair}")
        s = df["Close"].dropna()
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        s.name = pair
        series.append(s)
    aligned = pd.concat(series, axis=1, join="inner").dropna()
    if len(aligned) < LOOKBACK + 50:
        raise RuntimeError(f"Insufficient aligned bars: {len(aligned)}")
    return aligned


def rolling_hedge_ratio(log_a: pd.Series, log_b: pd.Series, window: int) -> pd.Series:
    """OLS beta: log_a = alpha + beta * log_b over rolling window."""
    cov = log_a.rolling(window).cov(log_b)
    var = log_b.rolling(window).var()
    beta = cov / var
    return beta


def compute_spread_and_zscore(closes: pd.DataFrame, leg_a: str, leg_b: str) -> pd.DataFrame:
    log_a = np.log(closes[leg_a])
    log_b = np.log(closes[leg_b])
    beta = rolling_hedge_ratio(log_a, log_b, LOOKBACK)
    spread = log_a - beta * log_b
    roll_mean = spread.rolling(LOOKBACK).mean()
    roll_std = spread.rolling(LOOKBACK).std()
    z = (spread - roll_mean) / roll_std
    return pd.DataFrame(
        {
            "close_a": closes[leg_a],
            "close_b": closes[leg_b],
            "beta": beta,
            "spread": spread,
            "z": z,
        },
        index=closes.index,
    )


def simulate_spread_trades(frame: pd.DataFrame) -> list[Trade]:
    """Bar-by-bar simulation; gross P&L from beta-neutral two-leg price moves."""
    trades: list[Trade] = []
    position = 0
    entry_idx: int | None = None
    entry_beta = 0.0
    entry_notional_b = 0.0

    n = len(frame)

    for i in range(LOOKBACK + 1, n):
        z = frame["z"].iloc[i]
        if pd.isna(z):
            continue

        if position == 0:
            if z < -ENTRY_Z:
                position = 1
                entry_idx = i
                entry_beta = float(frame["beta"].iloc[i])
                entry_notional_b = NOTIONAL_USD * entry_beta
            elif z > ENTRY_Z:
                position = -1
                entry_idx = i
                entry_beta = float(frame["beta"].iloc[i])
                entry_notional_b = NOTIONAL_USD * entry_beta
            continue

        assert entry_idx is not None
        bars_held = i - entry_idx
        exit_reason = ""
        should_exit = False

        if position == 1 and z >= EXIT_Z or position == -1 and z <= -EXIT_Z:
            should_exit = True
            exit_reason = "z_cross_zero"
        elif bars_held >= TIME_STOP:
            should_exit = True
            exit_reason = "time_stop"

        if not should_exit:
            continue

        pnl = _trade_gross_pnl(frame, entry_idx, i, position, entry_notional_b)
        trades.append(
            Trade(
                entry_idx=entry_idx,
                exit_idx=i,
                direction=position,
                gross_pnl=pnl,
                bars_held=bars_held,
                exit_reason=exit_reason,
            )
        )
        position = 0
        entry_idx = None

    return trades


def _trade_gross_pnl(
    frame: pd.DataFrame,
    entry_idx: int,
    exit_idx: int,
    direction: int,
    notional_b: float,
) -> float:
    """Dollar P&L from leg A and leg B price moves with fixed notionals."""
    entry_a = float(frame["close_a"].iloc[entry_idx])
    exit_a = float(frame["close_a"].iloc[exit_idx])
    entry_b = float(frame["close_b"].iloc[entry_idx])
    exit_b = float(frame["close_b"].iloc[exit_idx])

    ret_a = (exit_a - entry_a) / entry_a
    ret_b = (exit_b - entry_b) / entry_b

    if direction == 1:  # long spread: long A, short B
        pnl = NOTIONAL_USD * ret_a - notional_b * ret_b
    else:  # short spread: short A, long B
        pnl = -NOTIONAL_USD * ret_a + notional_b * ret_b
    return pnl


def _round_trip_cost_usd(leg_a: str, leg_b: str, pip_value: float = 10.0) -> float:
    spread_a = DEFAULT_SPREAD_PIPS.get(leg_a, 2.5)
    spread_b = DEFAULT_SPREAD_PIPS.get(leg_b, 2.5)
    round_trip_pips = 2 * (spread_a + spread_b) + 2.0  # entry+exit, 1 pip slip per leg per side
    return round_trip_pips * pip_value


def _pf_from_pnls(pnls: list[float]) -> float:
    wins = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    return wins / losses if losses > 0 else float("inf")


def trade_stats(trades: list[Trade], cost_per_trade: float = 0.0) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0,
            "gross_pf": 0.0,
            "net_pf": 0.0,
            "win_rate": 0.0,
            "total_gross_pnl": 0.0,
            "total_net_pnl": 0.0,
            "avg_pnl": 0.0,
            "avg_net_pnl": 0.0,
        }
    gross_pnls = [t.gross_pnl for t in trades]
    net_pnls = [t.gross_pnl - cost_per_trade for t in trades]
    return {
        "trades": len(trades),
        "gross_pf": _pf_from_pnls(gross_pnls),
        "net_pf": _pf_from_pnls(net_pnls),
        "win_rate": sum(1 for p in gross_pnls if p > 0) / len(gross_pnls),
        "total_gross_pnl": sum(gross_pnls),
        "total_net_pnl": sum(net_pnls),
        "avg_pnl": sum(gross_pnls) / len(gross_pnls),
        "avg_net_pnl": sum(net_pnls) / len(net_pnls),
    }


def is_oos_trade_stats(
    frame: pd.DataFrame,
    trades: list[Trade],
    cost_per_trade: float,
    split_ratio: float = 0.70,
) -> dict[str, Any]:
    if not trades:
        return {"is": trade_stats([]), "oos": trade_stats([]), "split_date": None}
    split_idx = int(len(frame) * split_ratio)
    split_date = frame.index[split_idx]
    is_trades = [t for t in trades if frame.index[t.exit_idx] < split_date]
    oos_trades = [t for t in trades if frame.index[t.exit_idx] >= split_date]
    return {
        "is": trade_stats(is_trades, cost_per_trade),
        "oos": trade_stats(oos_trades, cost_per_trade),
        "split_date": str(split_date.date()),
    }


def year_concentration(frame: pd.DataFrame, trades: list[Trade]) -> dict[int, float]:
    """Share of gross profit by calendar year of exit."""
    by_year: dict[int, float] = {}
    total_pos = 0.0
    for t in trades:
        if t.gross_pnl <= 0:
            continue
        year = frame.index[t.exit_idx].year
        by_year[year] = by_year.get(year, 0.0) + t.gross_pnl
        total_pos += t.gross_pnl
    if total_pos <= 0:
        return {}
    return {y: v / total_pos for y, v in sorted(by_year.items())}


def run_spread(spread: dict[str, str], start: datetime, end: datetime) -> dict[str, Any]:
    leg_a = spread["leg_a"]
    leg_b = spread["leg_b"]
    closes = fetch_aligned_closes(leg_a, leg_b, start, end)
    frame = compute_spread_and_zscore(closes, leg_a, leg_b)
    frame = frame.dropna()
    trades = simulate_spread_trades(frame)
    cost_per_trade = _round_trip_cost_usd(leg_a, leg_b)
    stats = trade_stats(trades, cost_per_trade)
    splits = is_oos_trade_stats(frame, trades, cost_per_trade)
    concentration = year_concentration(frame, trades)
    max_year_share = max(concentration.values()) if concentration else 0.0

    if stats["trades"] < MIN_TRADES:
        verdict = "DISCARD"
        reason = f"trades {stats['trades']} < {MIN_TRADES}"
    elif stats["gross_pf"] <= 1.05:
        verdict = "DISCARD"
        reason = f"gross PF {stats['gross_pf']:.3f} <= 1.05"
    elif stats["gross_pf"] < GROSS_PF_PASS:
        verdict = "DISCARD"
        reason = f"gross PF {stats['gross_pf']:.3f} < {GROSS_PF_PASS} pass threshold"
    elif max_year_share > 0.50:
        verdict = "DISCARD"
        reason = f"profit concentrated in one year ({max_year_share:.0%})"
    else:
        verdict = "GROSS_PASS"
        reason = "N/A"

    return {
        "id": spread["id"],
        "leg_a": leg_a,
        "leg_b": leg_b,
        "data_start": str(frame.index.min().date()),
        "data_end": str(frame.index.max().date()),
        "bars": len(frame),
        "stats": stats,
        "splits": splits,
        "cost_per_trade": cost_per_trade,
        "year_concentration": concentration,
        "max_year_share": max_year_share,
        "verdict": verdict,
        "reason": reason,
        "params": {
            "lookback": LOOKBACK,
            "entry_z": ENTRY_Z,
            "exit_z": EXIT_Z,
            "time_stop": TIME_STOP,
            "notional_usd": NOTIONAL_USD,
        },
    }


def build_results_doc(
    results: list[dict[str, Any]],
    start: str,
    end: str,
    command: str,
) -> tuple[str, str, str]:
    any_pass = any(r["verdict"] == "GROSS_PASS" for r in results)
    lane_verdict = "GROSS_PASS" if any_pass else "DISCARD"

    lines = [
        "# Stat-Arb Gross Falsifier Results — 2026-06-18",
        "",
        f"## Lane verdict: {lane_verdict}",
        "",
        "## Command",
        "```bash",
        command,
        "```",
        "",
        f"## Window: {start} → {end}",
        "",
        "## Parameters (fixed, no optimization)",
        f"- Lookback: {LOOKBACK} days (hedge ratio + z-score)",
        f"- Entry z: ±{ENTRY_Z}",
        f"- Exit z: cross {EXIT_Z}",
        f"- Time stop: {TIME_STOP} bars",
        f"- Notional leg A: ${NOTIONAL_USD:,.0f}; leg B sized by rolling beta",
        "- Costs: **zero** (gross-first)",
        "",
        "## Per-spread results",
        "",
    ]

    for r in results:
        s = r["stats"]
        lines.append(f"### {r['id']} ({r['leg_a']} vs {r['leg_b']})")
        lines.append(f"- Period: {r['data_start']} → {r['data_end']} ({r['bars']} bars)")
        lines.append(f"- Trades: {s['trades']}")
        lines.append(f"- Gross PF: {s['gross_pf']:.3f}")
        lines.append(
            f"- Net PF (after ${r['cost_per_trade']:.0f}/trade two-leg costs): {s['net_pf']:.3f}"
        )
        lines.append(f"- Win rate: {s['win_rate']:.1%}")
        lines.append(f"- Total gross P&L: ${s['total_gross_pnl']:,.2f}")
        lines.append(f"- Total net P&L: ${s['total_net_pnl']:,.2f}")
        lines.append(f"- Avg P&L/trade: ${s['avg_pnl']:,.2f}")
        sp = r["splits"]
        lines.append(f"- IS/OOS split at {sp['split_date']} (70/30 chronological)")
        lines.append(
            f"  - IS: {sp['is']['trades']} trades, gross PF {sp['is']['gross_pf']:.3f}, "
            f"net PF {sp['is']['net_pf']:.3f}"
        )
        lines.append(
            f"  - OOS: {sp['oos']['trades']} trades, gross PF {sp['oos']['gross_pf']:.3f}, "
            f"net PF {sp['oos']['net_pf']:.3f}"
        )
        if r["year_concentration"]:
            top_year = max(r["year_concentration"], key=r["year_concentration"].get)
            lines.append(f"- Max year concentration: {r['max_year_share']:.1%} ({top_year})")
        lines.append(f"- Verdict: **{r['verdict']}** — {r['reason']}")
        lines.append("")

    if lane_verdict == "GROSS_PASS":
        failure = "At least one spread passed gross-first. Next: add two-leg costs, chronological IS/OOS, half-life stability."
    else:
        failure = "No spread met gross PF >= 1.10 with >= 30 trades and acceptable concentration. Lane falsified at gross stage."

    lines.extend(
        [
            "## Next steps",
            failure,
            "",
            "## Accounting notes",
            "- P&L from actual leg price moves with beta-sized leg B notional at entry.",
            "- No spread, slippage, or commission in this gross run.",
            "- Round-trip two-leg costs (~10 pips majors) will be applied only if gross passes.",
        ]
    )
    return "\n".join(lines), lane_verdict, failure


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2026-06-01")
    parser.add_argument(
        "--output",
        default="docs/research/stat_arb/STAT_ARB_GROSS_RESULTS_2026-06-18.md",
    )
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)

    command = (
        "python -m research.new_edge.stat_arb.gross_stat_arb_test "
        f"--start {args.start} --end {args.end} --output {args.output}"
    )

    results = [run_spread(s, start, end) for s in CANDIDATE_SPREADS]
    doc, lane_verdict, _ = build_results_doc(results, args.start, args.end, command)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc, encoding="utf-8")

    print(f"Results written to {out_path}")
    print(f"\n=== Lane verdict: {lane_verdict} ===")
    for r in results:
        s = r["stats"]
        print(
            f"  {r['id']}: PF={s['gross_pf']:.3f} trades={s['trades']} "
            f"pnl=${s['total_gross_pnl']:,.0f} → {r['verdict']}"
        )


if __name__ == "__main__":
    main()
