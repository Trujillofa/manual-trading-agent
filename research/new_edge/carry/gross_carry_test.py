#!/usr/bin/env python3
"""
Smallest gross-only carry falsifier per CARRY_CONTRACT_2026-06-11.md.

- Static positive-carry portfolio: long top-N / short bottom-N by long swap rate.
- Constant vol-targeted sizing from full-history annualized price vol (minimal turnover).
- Rollover: *3 on Wednesdays.
- Gross carry ONLY: swap financing income (pips/day/lot * pip_value) minus entry drag.
- NO price P&L included.
- Uses yfinance daily + a verified_swap_rates*.json (--rates).

Run:
  python -m research.new_edge.carry.gross_carry_test \\
    --rates research/new_edge/carry/data/verified_swap_rates_VANTAGE_2026-08-13.json \\
    --start 2016-01-01 --end 2026-08-01 \\
    --output docs/research/carry/CARRY_GROSS_RESULTS_VANTAGE_2026-08-13.md
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf


def load_verified_swap_rates(path: str) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def _to_yfinance_fx_ticker(pair: str) -> str:
    return pair.replace("/", "") + "=X"


def get_aligned_daily_closes(pairs: list[str], start: datetime, end: datetime) -> pd.DataFrame:
    """Fetch daily closes via yfinance. Align on common trading days."""
    series_list = []
    valid_pairs = []
    for pair in pairs:
        try:
            ticker = _to_yfinance_fx_ticker(pair)
            df = yf.download(
                ticker, start=start.date(), end=end.date(), progress=False, interval="1d"
            )
            if df is not None and not df.empty:
                s = df["Close"].dropna()
                if len(s) > 100:
                    series_list.append(s)
                    valid_pairs.append(pair)
        except Exception as e:
            print(f"  yf warning for {pair}: {e}")
    if not series_list:
        raise RuntimeError("No daily data fetched from yfinance for any pair.")
    closes = pd.concat(series_list, axis=1, sort=True)
    closes.columns = valid_pairs
    aligned = closes.dropna(how="any")
    if len(aligned) < 100:
        raise RuntimeError("Insufficient aligned daily bars after dropna.")
    return aligned


def compute_ann_vol(closes: pd.DataFrame) -> pd.Series:
    rets = closes.pct_change().dropna()
    return rets.std() * (252**0.5)


def _choose_legs(sorted_pairs: list[str]) -> tuple[list[str], list[str]]:
    n = len(sorted_pairs)
    n_long = min(4, max(1, n // 2))
    n_short = min(4, max(1, n - n_long))
    while n_long + n_short > n and n_short > 1:
        n_short -= 1
    while n_long + n_short > n and n_long > 1:
        n_long -= 1
    long_legs = sorted_pairs[:n_long]
    short_legs = sorted_pairs[-n_short:] if n_short else []
    if set(long_legs) & set(short_legs):
        mid = n // 2
        long_legs = sorted_pairs[:mid]
        short_legs = sorted_pairs[mid:]
    return long_legs, short_legs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2026-06-01")
    parser.add_argument("--output", default="docs/research/carry/CARRY_GROSS_RESULTS_2026-06-12.md")
    parser.add_argument(
        "--rates",
        default="research/new_edge/carry/data/verified_swap_rates_2026-06.json",
        help="Path to verified_swap_rates*.json (template or live broker export)",
    )
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--target-vol", type=float, default=0.10)
    parser.add_argument("--cost-pips", type=float, default=3.0)
    parser.add_argument("--pip-value", type=float, default=10.0)
    parser.add_argument("--lot-notional", type=float, default=100_000.0)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)

    swap_data = load_verified_swap_rates(args.rates)
    rates = swap_data["rates"]
    pairs = list(rates.keys())

    src = str(swap_data.get("source") or swap_data.get("retrieved") or "")
    src_date = str(swap_data.get("source_date") or "")
    brk = str(swap_data.get("broker") or "")
    is_real_data = not (
        "TEMPLATE" in src
        or "illustration" in src.lower()
        or src_date.startswith("YYYY")
        or not brk
        or "replace" in brk.lower()
    )

    print(f"Fetching daily closes (yfinance) using rates={args.rates} ...")
    closes = get_aligned_daily_closes(pairs, start, end)
    # Restrict to pairs with OHLC so legs stay consistent with simulation.
    pairs = list(closes.columns)
    rates = {p: rates[p] for p in pairs}
    ann_vol = compute_ann_vol(closes)

    sorted_pairs = sorted(pairs, key=lambda p: rates[p]["long"], reverse=True)
    long_legs, short_legs = _choose_legs(sorted_pairs)
    n_long, n_short = len(long_legs), len(short_legs)
    n_legs = max(n_long + n_short, 1)

    capital = args.capital
    target_ann_vol = args.target_vol
    pip_value = args.pip_value
    lot_notional = args.lot_notional
    cost_pips = args.cost_pips

    risk_per_leg = target_ann_vol / n_legs
    lots: dict[str, float] = {}
    for p in long_legs:
        v = float(ann_vol.get(p, 0.15) or 0.15)
        lots[p] = (risk_per_leg / v) * capital / lot_notional if v > 0 else 1.0
    for p in short_legs:
        v = float(ann_vol.get(p, 0.15) or 0.15)
        lots[p] = -((risk_per_leg / v) * capital / lot_notional if v > 0 else 1.0)

    long_lots = {p: abs(lots[p]) for p in long_legs}
    short_lots = {p: abs(lots[p]) for p in short_legs}

    initial_drag = sum(long_lots[p] * cost_pips * pip_value for p in long_legs)
    initial_drag += sum(short_lots[p] * cost_pips * pip_value for p in short_legs)

    index = closes.index
    total_pos = 0.0
    total_neg = 0.0
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    leg_carry_totals: dict[str, float] = dict.fromkeys(pairs, 0.0)

    for dt in index:
        rollover = 3 if dt.weekday() == 2 else 1
        day_carry = 0.0
        for p in long_legs:
            leg_c = long_lots[p] * rates[p]["long"] * rollover * pip_value
            day_carry += leg_c
            total_pos += leg_c if leg_c > 0 else 0.0
            total_neg += abs(leg_c) if leg_c < 0 else 0.0
            leg_carry_totals[p] += leg_c
        for p in short_legs:
            leg_c = short_lots[p] * rates[p]["short"] * rollover * pip_value
            day_carry += leg_c
            total_pos += leg_c if leg_c > 0 else 0.0
            total_neg += abs(leg_c) if leg_c < 0 else 0.0
            leg_carry_totals[p] += leg_c

        cum += day_carry
        peak = max(peak, cum)
        if peak > 0:
            max_dd = max(max_dd, (peak - cum) / peak)

    gross_carry = total_pos - total_neg
    net_carry = gross_carry - initial_drag
    carry_pf = total_pos / (total_neg + initial_drag) if (total_neg + initial_drag) > 0 else float("inf")

    data_start = str(index.min().date())
    data_end = str(index.max().date())

    base_verdict = "GROSS_PASS" if net_carry > 0 and carry_pf > 1.0 else "DISCARD"
    if is_real_data:
        verdict = f"{base_verdict}_REAL_DATA"
        failure = (
            "N/A"
            if base_verdict == "GROSS_PASS"
            else "Gross carry (net of leg-level funding + drag) <=0 or PF<=1.0 even with real broker data."
        )
    else:
        verdict = f"{base_verdict} (sample data only)"
        failure = (
            "Sample/template rates only — replace with live broker export before GROSS_PASS_REAL_DATA."
            if base_verdict == "GROSS_PASS"
            else "Sample data weak/negative; also still template/illustration."
        )

    manifest = f"""# Carry Gross Falsifier Results

## Verdict
{verdict}

## Exact command run
```bash
python -m research.new_edge.carry.gross_carry_test \\
  --rates {args.rates} \\
  --start {args.start} --end {args.end} \\
  --output {args.output}
```

## Branch
cursor/research-lanes-2026-08

## Data sources and assumptions
- OHLC daily closes + vol: yfinance (aligned common trading days)
- Swap file: `{args.rates}`
  - broker=`{brk}`
  - source_date=`{src_date}`
  - retrieved/source=`{src}`
  - is_real_data={is_real_data}
- Rollover: ×3 on Wednesdays, ×1 otherwise (no holiday calendar)
- Sizing: static full-sample ann-vol risk split across {n_legs} legs; target portfolio ann vol {target_ann_vol*100:.0f}%
- Legs: top {n_long} by long_rate LONG, bottom {n_short} SHORT (universe n={len(pairs)})
- Costs: entry drag only = {cost_pips} pips × pip_value per lot at t=0
- Price P&L: ignored (gross-first falsifier)
- Capital ${capital:,.0f}; pip_value ${pip_value}; lot_notional ${lot_notional:,.0f}
- Simulated: {data_start} → {data_end} ({len(index)} trading days)

## Legs and sizing
Long: {long_legs}
Short: {short_legs}
"""
    for p in long_legs + short_legs:
        manifest += f"- {p}: {lots.get(p, 0):.3f} lots (ann_vol={float(ann_vol.get(p, 0)):.3f})\n"

    manifest += f"""
## Gross carry metrics (leg-level accounting)
- Trading days: {len(index)}
- Positive carry $: ${total_pos:,.2f}
- Negative carry / funding $: ${total_neg:,.2f}
- Gross (pos − neg) $: ${gross_carry:,.2f}
- Initial entry drag $: ${initial_drag:,.2f}
- Net after drag $: ${net_carry:,.2f}
- Carry PF pos/(neg+drag): {carry_pf:.3f}
- Max DD on cum net-carry path: {max_dd*100:.2f}%

### Per-pair accumulated carry $
"""
    for p, contrib in sorted(leg_carry_totals.items(), key=lambda x: -x[1]):
        leg_type = "LONG" if p in long_legs else ("SHORT" if p in short_legs else "FLAT")
        rate = rates[p]["long"] if leg_type == "LONG" else rates[p]["short"] if leg_type == "SHORT" else 0.0
        manifest += f"- {p} ({leg_type}): ${contrib:,.2f} (rate={rate:+.4f})\n"

    manifest += f"""
## Failure reason / next step
{failure}

{"Real-broker metadata present (`is_real_data=True`)." if is_real_data else "Template/sample rates — not a real-data claim."}
If GROSS_PASS_REAL_DATA: next is richer costs, price P&L risk, chronological IS/OOS, carry-crash stress — not LIVE promotion.
If DISCARD_REAL_DATA: close or redesign premise; do not retune to rescue.
"""

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(manifest)
    print(f"Gross results written to {args.output}")
    print("\n=== Summary ===")
    print(f"Gross PF (carry): {carry_pf:.3f}")
    print(f"Net carry after drag: ${net_carry:,.2f}")
    print(f"Verdict: {verdict}")
    print(f"is_real_data={is_real_data}")


if __name__ == "__main__":
    main()
