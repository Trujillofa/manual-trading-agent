#!/usr/bin/env python3
"""
Smallest gross-only carry falsifier per CARRY_CONTRACT_2026-06-11.md.

- Static positive-carry portfolio: long top-N / short bottom-N by long swap.
- Constant vol-targeted sizing from full-history annualized price vol (minimal turnover).
- Rollover: *3 on Wednesdays.
- Gross carry ONLY: swap financing income minus entry drag.
- NO price P&L included.
- Uses yfinance daily + a verified_swap_rates*.json (--rates).

Economics modes (--economics):
  auto  — use MT5 raw POINTS × tick_value when raw_by_pair present; else uniform --pip-value
  mt5   — require raw_by_pair; account-currency $/lot/day = swap_*_raw × tick_value
  uniform — legacy pips/day × single --pip-value (audit baseline only)

Run:
  python -m research.new_edge.carry.gross_carry_test \\
    --rates research/new_edge/carry/data/verified_swap_rates_VANTAGE_2026-08-13.json \\
    --economics auto --start 2016-01-01 --end 2026-08-01 \\
    --output docs/research/carry/CARRY_GROSS_RESULTS_VANTAGE_PIPCORRECT_2026-08-13.md
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


def _points_per_pip(digits: int) -> int:
    # Standard FX: 3/5-digit quotes → 10 points per pip; 2/4-digit → 1.
    return 10 if digits in (3, 5) else 1


def resolve_economics(
    swap_data: dict[str, Any],
    pairs: list[str],
    mode: str,
    fallback_pip_value: float,
) -> tuple[str, dict[str, float], dict[str, float], dict[str, float]]:
    """Return (mode_used, long_usd/day/lot, short_usd/day/lot, pip_value_usd)."""
    raw = swap_data.get("raw_by_pair") or {}
    rates = swap_data.get("rates") or {}
    want_mt5 = mode in ("auto", "mt5")
    have_mt5 = want_mt5 and all(
        p in raw
        and raw[p].get("tick_value") is not None
        and raw[p].get("swap_long_raw") is not None
        and raw[p].get("swap_short_raw") is not None
        for p in pairs
    )
    if mode == "mt5" and not have_mt5:
        missing = [p for p in pairs if p not in raw]
        raise RuntimeError(
            f"--economics mt5 requires raw_by_pair for all pairs; missing={missing or 'fields'}"
        )
    if have_mt5:
        long_usd: dict[str, float] = {}
        short_usd: dict[str, float] = {}
        pip_usd: dict[str, float] = {}
        for p in pairs:
            row = raw[p]
            tick = float(row["tick_value"])
            digits = int(row.get("digits") or 5)
            long_usd[p] = float(row["swap_long_raw"]) * tick
            short_usd[p] = float(row["swap_short_raw"]) * tick
            pip_usd[p] = tick * _points_per_pip(digits)
        return "mt5", long_usd, short_usd, pip_usd

    # Uniform / fallback: rates are pips/day/lot × single pip_value.
    long_usd = {p: float(rates[p]["long"]) * fallback_pip_value for p in pairs}
    short_usd = {p: float(rates[p]["short"]) * fallback_pip_value for p in pairs}
    pip_usd = dict.fromkeys(pairs, fallback_pip_value)
    return "uniform", long_usd, short_usd, pip_usd


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
    parser.add_argument(
        "--economics",
        choices=("auto", "mt5", "uniform"),
        default="auto",
        help="Account-currency conversion: auto|mt5|uniform (default auto)",
    )
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--target-vol", type=float, default=0.10)
    parser.add_argument("--cost-pips", type=float, default=3.0)
    parser.add_argument(
        "--pip-value",
        type=float,
        default=10.0,
        help="Fallback USD per pip per lot when --economics uniform / no raw_by_pair",
    )
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
    pairs = list(closes.columns)
    rates = {p: rates[p] for p in pairs}
    ann_vol = compute_ann_vol(closes)

    econ_mode, long_usd, short_usd, pip_usd = resolve_economics(
        swap_data, pairs, args.economics, args.pip_value
    )

    # Rank by long account-currency $/day/lot (correct units), not raw pips.
    sorted_pairs = sorted(pairs, key=lambda p: long_usd[p], reverse=True)
    long_legs, short_legs = _choose_legs(sorted_pairs)
    n_long, n_short = len(long_legs), len(short_legs)
    n_legs = max(n_long + n_short, 1)

    capital = args.capital
    target_ann_vol = args.target_vol
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

    initial_drag = sum(long_lots[p] * cost_pips * pip_usd[p] for p in long_legs)
    initial_drag += sum(short_lots[p] * cost_pips * pip_usd[p] for p in short_legs)

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
            leg_c = long_lots[p] * long_usd[p] * rollover
            day_carry += leg_c
            total_pos += leg_c if leg_c > 0 else 0.0
            total_neg += abs(leg_c) if leg_c < 0 else 0.0
            leg_carry_totals[p] += leg_c
        for p in short_legs:
            leg_c = short_lots[p] * short_usd[p] * rollover
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

    econ_note = (
        "MT5 POINTS×tick_value account-currency $/lot/day; pip_$ = tick_value×points_per_pip"
        if econ_mode == "mt5"
        else f"uniform pips×${args.pip_value:.2f}/pip (legacy; not contract-correct for exotics)"
    )

    manifest = f"""# Carry Gross Falsifier Results

## Verdict
{verdict}

## Exact command run
```bash
python -m research.new_edge.carry.gross_carry_test \\
  --rates {args.rates} \\
  --economics {args.economics} \\
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
- Economics mode: **{econ_mode}** (requested={args.economics}) — {econ_note}
- Ranking key: long $/day/lot (account currency), not raw pip rates
- Rollover: ×3 on Wednesdays, ×1 otherwise (no holiday calendar)
- Sizing: static full-sample ann-vol risk split across {n_legs} legs; target portfolio ann vol {target_ann_vol*100:.0f}%
- Legs: top {n_long} by long_$ LONG, bottom {n_short} SHORT (universe n={len(pairs)})
- Costs: entry drag only = {cost_pips} pips × pair pip_$ at t=0
- Price P&L: ignored (gross-first falsifier)
- Capital ${capital:,.0f}; lot_notional ${lot_notional:,.0f}
- Simulated: {data_start} → {data_end} ({len(index)} trading days)

## Per-pair economics ($/day/lot and pip_$)
"""
    for p in sorted_pairs:
        manifest += (
            f"- {p}: long_usd/day={long_usd[p]:+.4f}, short_usd/day={short_usd[p]:+.4f}, "
            f"pip_usd={pip_usd[p]:.4f}\n"
        )

    manifest += f"""
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
        usd_rate = long_usd[p] if leg_type == "LONG" else short_usd[p] if leg_type == "SHORT" else 0.0
        manifest += f"- {p} ({leg_type}): ${contrib:,.2f} (usd/day/lot={usd_rate:+.4f})\n"

    next_if_pass = (
        "Next: richer costs, price P&L risk, chronological IS/OOS, carry-crash stress — not LIVE promotion."
        if econ_mode == "mt5"
        else "Next: re-run with --economics mt5/auto when raw_by_pair is present before treating as contract-correct."
    )
    manifest += f"""
## Failure reason / next step
{failure}

{"Real-broker metadata present (`is_real_data=True`)." if is_real_data else "Template/sample rates — not a real-data claim."}
Economics={econ_mode}. {next_if_pass}
If DISCARD_REAL_DATA: close or redesign premise; do not retune to rescue.
"""

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(manifest)
    print(f"Gross results written to {args.output}")
    print("\n=== Summary ===")
    print(f"economics={econ_mode}")
    print(f"Gross PF (carry): {carry_pf:.3f}")
    print(f"Net carry after drag: ${net_carry:,.2f}")
    print(f"Verdict: {verdict}")
    print(f"is_real_data={is_real_data}")
    print(f"Long={long_legs} Short={short_legs}")


if __name__ == "__main__":
    main()
