#!/usr/bin/env python3
"""
Net carry falsifier (post GROSS_PASS): price P&L + richer costs + chronological IS/OOS.

Uses the same static long/short construction and MT5 POINTS×tick_value economics as
gross_carry_test --economics auto/mt5. Does **not** retune legs.

Pre-committed gates (CARRY_CONTRACT + research program KEEP bar):
- OOS net PnL > 0
- OOS daily PF >= 1.20
- Max single-leg |PnL| share <= 0.60 (full sample)
- Stress-window peak-to-trough DD <= 15% of capital (COVID 2020 window)

Run:
  python -m research.new_edge.carry.net_carry_test \\
    --rates research/new_edge/carry/data/verified_swap_rates_VANTAGE_2026-08-13.json \\
    --economics auto --start 2016-01-01 --end 2026-08-01 --is-end 2021-12-31 \\
    --output docs/research/carry/CARRY_NET_RESULTS_VANTAGE_2026-08-13.md
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research.new_edge.carry.gross_carry_test import (
    _choose_legs,
    compute_ann_vol,
    get_aligned_daily_closes,
    load_verified_swap_rates,
    resolve_economics,
)

# Pre-committed stress windows (inclusive dates, UTC calendar).
STRESS_WINDOWS: list[tuple[str, str, str]] = [
    ("covid_2020", "2020-02-15", "2020-04-15"),
    ("vol_2018", "2018-01-01", "2018-03-31"),
    ("hike_2022", "2022-01-01", "2022-03-31"),
]


def _is_real_data(swap_data: dict[str, Any]) -> bool:
    src = str(swap_data.get("source") or swap_data.get("retrieved") or "")
    src_date = str(swap_data.get("source_date") or "")
    brk = str(swap_data.get("broker") or "")
    return not (
        "TEMPLATE" in src
        or "illustration" in src.lower()
        or src_date.startswith("YYYY")
        or not brk
        or "replace" in brk.lower()
    )


def _point_tick(swap_data: dict[str, Any], pairs: list[str]) -> dict[str, tuple[float, float]]:
    raw = swap_data.get("raw_by_pair") or {}
    out: dict[str, tuple[float, float]] = {}
    for p in pairs:
        if p in raw and raw[p].get("point") and raw[p].get("tick_value") is not None:
            out[p] = (float(raw[p]["point"]), float(raw[p]["tick_value"]))
        else:
            # Fallback: 1 pip = 0.0001 (or 0.01 JPY), $10/pip — majors-only toy.
            out[p] = (1e-5, 1.0)
    return out


def _pf(pos: float, neg: float) -> float:
    if neg <= 0:
        return float("inf") if pos > 0 else 0.0
    return pos / neg


def _sharpe(daily: pd.Series) -> float:
    s = daily.dropna()
    if len(s) < 2 or float(s.std()) == 0.0:
        return 0.0
    return float(s.mean() / s.std() * np.sqrt(252.0))


def _max_dd_frac(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (peak - equity) / peak.replace(0, np.nan)
    return float(dd.max()) if dd.notna().any() else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2026-08-01")
    parser.add_argument("--is-end", default="2021-12-31", help="Last inclusive IS calendar date")
    parser.add_argument(
        "--output",
        default="docs/research/carry/CARRY_NET_RESULTS_VANTAGE_2026-08-13.md",
    )
    parser.add_argument(
        "--rates",
        default="research/new_edge/carry/data/verified_swap_rates_VANTAGE_2026-08-13.json",
    )
    parser.add_argument("--economics", choices=("auto", "mt5", "uniform"), default="auto")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--target-vol", type=float, default=0.10)
    parser.add_argument(
        "--spread-pips",
        type=float,
        default=3.0,
        help="Entry spread assumption (pips)",
    )
    parser.add_argument(
        "--slippage-pips",
        type=float,
        default=1.0,
        help="Entry slippage assumption (pips); richer than gross's bundled 3.0",
    )
    parser.add_argument("--pip-value", type=float, default=10.0)
    parser.add_argument("--lot-notional", type=float, default=100_000.0)
    parser.add_argument("--oos-pf-min", type=float, default=1.20)
    parser.add_argument("--max-leg-share", type=float, default=0.60)
    parser.add_argument("--stress-dd-max", type=float, default=0.15)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)
    is_end = pd.Timestamp(args.is_end)

    swap_data = load_verified_swap_rates(args.rates)
    rates = swap_data["rates"]
    pairs = list(rates.keys())
    is_real = _is_real_data(swap_data)

    print(f"Fetching daily closes (yfinance) rates={args.rates} ...")
    closes = get_aligned_daily_closes(pairs, start, end)
    pairs = list(closes.columns)
    ann_vol = compute_ann_vol(closes)
    econ_mode, long_usd, short_usd, pip_usd = resolve_economics(
        swap_data, pairs, args.economics, args.pip_value
    )
    point_tick = _point_tick(swap_data, pairs)

    sorted_pairs = sorted(pairs, key=lambda p: long_usd[p], reverse=True)
    long_legs, short_legs = _choose_legs(sorted_pairs)
    active = long_legs + short_legs
    n_legs = max(len(active), 1)

    risk_per_leg = args.target_vol / n_legs
    lots: dict[str, float] = {}
    for p in long_legs:
        v = float(ann_vol.get(p, 0.15) or 0.15)
        lots[p] = (risk_per_leg / v) * args.capital / args.lot_notional if v > 0 else 1.0
    for p in short_legs:
        v = float(ann_vol.get(p, 0.15) or 0.15)
        lots[p] = -((risk_per_leg / v) * args.capital / args.lot_notional if v > 0 else 1.0)

    cost_pips = args.spread_pips + args.slippage_pips
    entry_drag = sum(abs(lots[p]) * cost_pips * pip_usd[p] for p in active)

    index = closes.index
    # Daily series
    day_swap = pd.Series(0.0, index=index, dtype=float)
    day_price = pd.Series(0.0, index=index, dtype=float)
    leg_total: dict[str, float] = dict.fromkeys(active, 0.0)

    prev = closes.shift(1)
    for i, dt in enumerate(index):
        rollover = 3 if dt.weekday() == 2 else 1
        swap_d = 0.0
        price_d = 0.0
        for p in long_legs:
            swap_d += abs(lots[p]) * long_usd[p] * rollover
        for p in short_legs:
            swap_d += abs(lots[p]) * short_usd[p] * rollover
        if i > 0:
            for p in active:
                point, tick = point_tick[p]
                px0 = float(prev.loc[dt, p])
                px1 = float(closes.loc[dt, p])
                if not np.isfinite(px0) or not np.isfinite(px1) or point <= 0:
                    continue
                # Signed lots: long positive → profit when price rises.
                pnl = lots[p] * ((px1 - px0) / point) * tick
                price_d += pnl
                leg_total[p] += pnl
        # Attribute swap to legs for concentration (price already attributed).
        for p in long_legs:
            leg_total[p] += abs(lots[p]) * long_usd[p] * rollover
        for p in short_legs:
            leg_total[p] += abs(lots[p]) * short_usd[p] * rollover

        day_swap.loc[dt] = swap_d
        day_price.loc[dt] = price_d

    day_gross = day_swap + day_price
    # Charge entry drag on first bar.
    day_net = day_gross.copy()
    day_net.iloc[0] = day_net.iloc[0] - entry_drag

    equity = args.capital + day_net.cumsum()
    is_mask = index <= is_end
    oos_mask = ~is_mask

    def split_stats(mask: pd.Series) -> dict[str, float]:
        d = day_net[mask]
        pos = float(d[d > 0].sum())
        neg = float((-d[d < 0]).sum())
        return {
            "days": int(mask.sum()),
            "pnl": float(d.sum()),
            "swap": float(day_swap[mask].sum()),
            "price": float(day_price[mask].sum()),
            "pf": _pf(pos, neg),
            "sharpe": _sharpe(d),
            "max_dd": _max_dd_frac(args.capital + d.cumsum()),
        }

    is_s = split_stats(is_mask)
    oos_s = split_stats(oos_mask)
    full_s = split_stats(pd.Series(True, index=index))

    abs_legs = {p: abs(v) for p, v in leg_total.items()}
    abs_sum = sum(abs_legs.values()) or 1.0
    leg_share = {p: abs_legs[p] / abs_sum for p in active}
    max_leg = max(leg_share, key=leg_share.get)
    max_share = leg_share[max_leg]

    stress_rows: list[dict[str, Any]] = []
    stress_fail = False
    for name, a, b in STRESS_WINDOWS:
        a_ts, b_ts = pd.Timestamp(a), pd.Timestamp(b)
        m = (index >= a_ts) & (index <= b_ts)
        if int(m.sum()) < 5:
            stress_rows.append({"name": name, "days": int(m.sum()), "dd": None, "ok": None})
            continue
        # DD on equity path restricted to window (peak within window).
        eq_w = equity[m]
        dd = _max_dd_frac(eq_w)
        # Also absolute loss vs capital from window start.
        loss_frac = max(0.0, float(eq_w.iloc[0] - eq_w.min()) / args.capital)
        dd_gate = max(dd, loss_frac)
        ok = dd_gate <= args.stress_dd_max
        if not ok:
            stress_fail = True
        stress_rows.append(
            {
                "name": name,
                "days": int(m.sum()),
                "dd": dd_gate,
                "pnl": float(day_net[m].sum()),
                "ok": ok,
            }
        )

    gates = {
        "oos_pnl_pos": oos_s["pnl"] > 0,
        "oos_pf": oos_s["pf"] >= args.oos_pf_min,
        "concentration": max_share <= args.max_leg_share,
        "stress_dd": not stress_fail,
    }
    all_pass = all(gates.values())
    base = "KEEP" if all_pass else "DISCARD"
    verdict = f"{base}_REAL_DATA" if is_real else f"{base} (sample data only)"

    fail_reasons = [k for k, v in gates.items() if not v]
    failure = (
        "N/A"
        if all_pass
        else "Failed gates: " + ", ".join(fail_reasons)
    )

    # Markdown
    md = f"""# Carry Net Falsifier Results (price P&L + IS/OOS)

## Verdict
{verdict}

## Exact command run
```bash
python -m research.new_edge.carry.net_carry_test \\
  --rates {args.rates} \\
  --economics {args.economics} \\
  --start {args.start} --end {args.end} --is-end {args.is_end} \\
  --spread-pips {args.spread_pips} --slippage-pips {args.slippage_pips} \\
  --output {args.output}
```

## Branch
cursor/research-lanes-2026-08

## Pre-committed gates
- OOS net PnL > 0
- OOS daily PF >= {args.oos_pf_min}
- Max single-leg |PnL| share <= {args.max_leg_share}
- Stress DD <= {args.stress_dd_max*100:.0f}% of capital (covid_2020, vol_2018, hike_2022)

Gate results: `{json.dumps(gates)}`

## Strategy (frozen from pip-correct gross)
- Economics: **{econ_mode}** (POINTS×tick_value; price PnL via Δprice/point×tick_value)
- Long: {long_legs}
- Short: {short_legs}
- Sizing: static full-sample ann-vol; target {args.target_vol*100:.0f}% port vol; capital ${args.capital:,.0f}
- Costs: entry once = ({args.spread_pips}+{args.slippage_pips}) pips × pair pip_$ = ${entry_drag:,.2f}
- No RSI/Donchian/TSMOM filters; no leg retune after gross
- IS: {args.start} → {args.is_end} ({is_s["days"]} days)
- OOS: after {args.is_end} → {args.end} ({oos_s["days"]} days)
- Simulated bars: {index.min().date()} → {index.max().date()} ({len(index)} days)

## Lots
"""
    for p in active:
        md += f"- {p}: {lots[p]:+.3f} lots (ann_vol={float(ann_vol.get(p, 0)):.3f}, pip_usd={pip_usd[p]:.4f})\n"

    md += f"""
## Full-sample net metrics
- Net PnL $: ${full_s["pnl"]:,.2f} (swap ${full_s["swap"]:,.2f} + price ${full_s["price"]:,.2f} − drag ${entry_drag:,.2f})
- Daily PF: {full_s["pf"]:.3f}
- Sharpe: {full_s["sharpe"]:.3f}
- Max DD (equity): {full_s["max_dd"]*100:.2f}%

## IS metrics
- Net PnL $: ${is_s["pnl"]:,.2f} (swap ${is_s["swap"]:,.2f} / price ${is_s["price"]:,.2f})
- Daily PF: {is_s["pf"]:.3f}
- Sharpe: {is_s["sharpe"]:.3f}
- Max DD: {is_s["max_dd"]*100:.2f}%

## OOS metrics (binding)
- Net PnL $: ${oos_s["pnl"]:,.2f} (swap ${oos_s["swap"]:,.2f} / price ${oos_s["price"]:,.2f})
- Daily PF: {oos_s["pf"]:.3f} (gate >= {args.oos_pf_min})
- Sharpe: {oos_s["sharpe"]:.3f}
- Max DD: {oos_s["max_dd"]*100:.2f}%

## Concentration (|leg total PnL| share)
"""
    for p, sh in sorted(leg_share.items(), key=lambda x: -x[1]):
        md += f"- {p}: {sh*100:.1f}% (leg_pnl=${leg_total[p]:,.2f})\n"
    md += f"- Max: {max_leg} @ {max_share*100:.1f}% (gate <= {args.max_leg_share*100:.0f}%)\n"

    md += "\n## Stress windows\n"
    for row in stress_rows:
        if row["ok"] is None:
            md += f"- {row['name']}: insufficient days ({row['days']})\n"
        else:
            md += (
                f"- {row['name']}: days={row['days']}, dd={row['dd']*100:.2f}%, "
                f"pnl=${row['pnl']:,.2f}, ok={row['ok']}\n"
            )

    md += f"""
## Failure reason / next step
{failure}

{"KEEP_REAL_DATA is research KEEP only — paper-shadow before any live risk; not Branch B promotion." if all_pass else "DISCARD_REAL_DATA: close or redesign premise; do not retune legs/costs to rescue OOS."}
"""

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(md)
    print(f"Net results written to {args.output}")
    print("\n=== Summary ===")
    print(f"economics={econ_mode} verdict={verdict}")
    print(f"IS pnl=${is_s['pnl']:.2f} pf={is_s['pf']:.3f}")
    print(f"OOS pnl=${oos_s['pnl']:.2f} pf={oos_s['pf']:.3f} sharpe={oos_s['sharpe']:.3f}")
    print(f"gates={gates}")
    print(f"max_leg={max_leg} share={max_share:.3f}")


if __name__ == "__main__":
    main()
