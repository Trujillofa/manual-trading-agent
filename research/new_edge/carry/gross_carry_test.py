#!/usr/bin/env python3
"""
Smallest gross-only carry falsifier per CARRY_CONTRACT_2026-06-11.md.

- Static positive-carry portfolio: long top-4 / short bottom-4 by long swap rate (from verified table).
- Constant vol-targeted sizing from full-history annualized price vol (minimal turnover).
- Daily rebalance accounting (but static ranks + fixed lots => only initial entry).
- Rollover: *3 on Wednesdays.
- Gross carry ONLY: swap financing income (pips/day/lot * pip_value) minus basic entry/turnover drag (spread+slip from contract/settings).
- NO price P&L included.
- Uses yfinance daily (same lightweight path as verifier --quick) + checked-in verified_swap_rates_2026-06.json.

Run:
  python -m research.new_edge.carry.gross_carry_test --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_GROSS_RESULTS_2026-06-12.md

This is the first falsification test. Verdict: GROSS_PASS / DISCARD / BLOCKED (sample data caveat applies).
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

CARRY_POSITIVE_PAIRS = [
    "AUD/JPY",
    "NZD/JPY",
    "AUD/USD",
    "NZD/USD",
    "USD/TRY",
    "USD/ZAR",
    "EUR/TRY",
    "GBP/TRY",
]


def load_verified_swap_rates(path: str = "research/new_edge/carry/data/verified_swap_rates_2026-06.json") -> dict[str, Any]:
    """Load swap rates. The JSON is a template; real broker data (with source_date, broker, etc.) must replace it
    before the lane can claim anything beyond sample methodology validation.
    """
    with open(path) as f:
        return json.load(f)


def _to_yfinance_fx_ticker(pair: str) -> str:
    return pair.replace("/", "") + "=X"


def get_aligned_daily_closes(pairs: list[str], start: datetime, end: datetime) -> pd.DataFrame:
    """Fetch daily closes via yfinance (lightweight, reproducible). Align on common trading days."""
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
                if len(s) > 100:  # require reasonable history
                    series_list.append(s)
                    valid_pairs.append(pair)
        except Exception as e:
            print(f"  yf warning for {pair}: {e}")
    if not series_list:
        raise RuntimeError("No daily data fetched from yfinance for any pair.")
    closes = pd.concat(series_list, axis=1)
    closes.columns = valid_pairs
    aligned = closes.dropna(how="any")  # strict common days
    if len(aligned) < 100:
        raise RuntimeError("Insufficient aligned daily bars after dropna.")
    return aligned


def compute_ann_vol(closes: pd.DataFrame) -> pd.Series:
    rets = closes.pct_change().dropna()
    ann_vol = rets.std() * (252 ** 0.5)
    return ann_vol


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2026-06-01")
    parser.add_argument("--output", default="docs/research/carry/CARRY_GROSS_RESULTS_2026-06-12.md")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--target-vol", type=float, default=0.10, help="Target portfolio ann vol for sizing")
    parser.add_argument("--cost-pips", type=float, default=3.0, help="Entry drag (spread+slippage) pips per lot changed")
    parser.add_argument("--pip-value", type=float, default=10.0, help="USD per pip per standard lot (approx)")
    parser.add_argument("--lot-notional", type=float, default=100_000.0)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)

    swap_data = load_verified_swap_rates()
    rates = swap_data["rates"]
    pairs = list(rates.keys())

    # Real data gate (same logic as verifier)
    src = swap_data.get("source", "")
    src_date = swap_data.get("source_date", "")
    brk = swap_data.get("broker", "")
    is_real_data = not (
        "TEMPLATE" in src or
        "illustration" in src.lower() or
        src_date.startswith("YYYY") or
        not brk or "replace" in brk.lower()
    )

    print("Fetching daily closes (yfinance) and computing vols...")
    closes = get_aligned_daily_closes(pairs, start, end)
    ann_vol = compute_ann_vol(closes)

    # Rank by long carry (highest = best to long)
    sorted_pairs = sorted(pairs, key=lambda p: rates[p]["long"], reverse=True)
    n_long = 4
    n_short = 4
    long_legs = sorted_pairs[:n_long]
    short_legs = sorted_pairs[-n_short:]

    # Constant sizing from full-sample vol (static portfolio => one-time entry, minimal turnover)
    CAPITAL = args.capital  # noqa: N806
    TARGET_ANN_VOL = args.target_vol  # noqa: N806
    PIP_VALUE = args.pip_value  # noqa: N806
    LOT_NOTIONAL = args.lot_notional  # noqa: N806
    COST_PIPS = args.cost_pips  # noqa: N806

    risk_per_leg = TARGET_ANN_VOL / (n_long + n_short)
    lots: dict[str, float] = {}
    for p in long_legs:
        v = ann_vol.get(p, 0.15)
        if v > 0:
            notional_frac = risk_per_leg / v
            notional = notional_frac * CAPITAL
            lots[p] = notional / LOT_NOTIONAL
        else:
            lots[p] = 1.0
    for p in short_legs:
        v = ann_vol.get(p, 0.15)
        if v > 0:
            notional_frac = risk_per_leg / v
            notional = notional_frac * CAPITAL
            lots[p] = -(notional / LOT_NOTIONAL)   # signed for clarity in contrib, but we use lists below
        else:
            lots[p] = -1.0

    # Separate signed for clarity
    long_lots = {p: abs(lots[p]) for p in long_legs}
    short_lots = {p: abs(lots[p]) for p in short_legs}

    # Initial entry drag (one time, static ranks)
    initial_drag = 0.0
    for p in long_legs:
        initial_drag += long_lots[p] * COST_PIPS * PIP_VALUE
    for p in short_legs:
        initial_drag += short_lots[p] * COST_PIPS * PIP_VALUE

    # Simulate daily carry (gross, price P&L ignored)
    # IMPORTANT: track POSITIVE carry and NEGATIVE carry (funding costs) at *leg level*,
    # not from net daily portfolio carry. This prevents swamping and gives a defensible PF
    # (gross income from positive-carry legs / gross funding costs from negative-carry legs + drag).
    index = closes.index
    total_pos = 0.0
    total_neg = 0.0
    daily_carry_series = []
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    leg_carry_totals: dict[str, float] = dict.fromkeys(pairs, 0.0)

    for dt in index:
        is_wed = dt.weekday() == 2  # Wednesday
        rollover = 3 if is_wed else 1
        day_carry = 0.0
        for p in long_legs:
            rate = rates[p]["long"]
            leg_c = long_lots[p] * rate * rollover * PIP_VALUE
            day_carry += leg_c
            if leg_c > 0:
                total_pos += leg_c
            else:
                total_neg += abs(leg_c)
            leg_carry_totals[p] += leg_c
        for p in short_legs:
            rate = rates[p]["short"]
            leg_c = short_lots[p] * rate * rollover * PIP_VALUE
            day_carry += leg_c
            if leg_c > 0:
                total_pos += leg_c
            else:
                total_neg += abs(leg_c)
            leg_carry_totals[p] += leg_c

        daily_carry_series.append((dt, day_carry))
        # cum / DD still use *net* day_carry (the actual carry P&L to the book)
        cum += day_carry
        peak = max(peak, cum)
        if peak > 0:
            dd = (peak - cum) / peak
            max_dd = max(max_dd, dd)

    gross_carry = total_pos - total_neg
    net_carry = gross_carry - initial_drag
    carry_pf = total_pos / (total_neg + initial_drag) if (total_neg + initial_drag) > 0 else float("inf")

    # Use exact leg-level accumulated carry (with daily rollover) for per-pair reporting
    pair_contrib = leg_carry_totals

    data_start = str(index.min().date())
    data_end = str(index.max().date())

    # Verdict per user spec for this gross step
    if net_carry > 0 and carry_pf > 1.0:  # noqa: SIM108
        base_verdict = "GROSS_PASS"
    else:
        base_verdict = "DISCARD"

    if is_real_data:
        verdict = base_verdict + "_REAL_DATA"
        failure = "N/A" if base_verdict == "GROSS_PASS" else "Gross carry (net of leg-level funding + drag) <=0 or PF<=1.0 even with real broker data."
    else:
        verdict = base_verdict + " (sample data only)"
        failure = "Gross positive on sample rates (illustration/template only). Real broker statement/API data (with filled source_date, broker, actual rates) must replace the JSON and the test re-run before claiming GROSS_PASS_REAL_DATA or advancing to price P&L / IS/OOS / carry-crash stress." if base_verdict == "GROSS_PASS" else "Sample data produced weak/negative gross. Additionally, source is still template/illustration."

    manifest = f"""# Carry Gross Falsifier Results - 2026-06-12 (sample data)

## Verdict
{verdict}

## Exact command run
python -m research.new_edge.carry.gross_carry_test --start {args.start} --end {args.end} --output {args.output}

## Git branch
docs/profitability-plan-2026-06

## Data sources and assumptions
- OHLC daily closes + vol: yfinance (lightweight daily, same as data verifier --quick). Aligned common trading days.
- Swap/financing + rollover: checked-in research/new_edge/carry/data/verified_swap_rates_2026-06.json ("Verified from broker statement sample... For illustration only; in real use, replace with actual broker API or statement data and re-verify.")
- Rollover applied: rate * 3 on Wednesdays (date.weekday()==2), *1 otherwise. (No holiday exceptions in this skeleton.)
- Sizing: static (full-history ann vol from price returns), risk-parity-ish per leg (target {TARGET_ANN_VOL*100:.0f}% portfolio ann vol / 8 legs), constant lots (minimal turnover = initial entry only).
- Universe & legs: top {n_long} by long_rate to LONG, bottom {n_short} to SHORT.
- Costs (gross carry net of...): entry/turnover drag only = {COST_PIPS} pips (spread+slippage) * pip_value per lot changed at start (per CARRY_CONTRACT cost model + settings spread_limits ~2-3 pips).
- Price P&L: deliberately IGNORED (first falsifier scope).
- Capital ref: ${CAPITAL:,.0f}; pip value ref: ${PIP_VALUE}; lot notional ref: ${LOT_NOTIONAL:,.0f}.
- Period actually simulated: {data_start} to {data_end} ({len(index)} trading days).

**Real data gate (per CARRY_CONTRACT and user guidance):** Current run uses the template/placeholder rates in the JSON. The lane remains blocked on data until the JSON is replaced with actual broker statement or API export (filled source_date, broker, retrieved, notes, and accurate rates). Only a subsequent run with is_real_data=True will produce GROSS_PASS_REAL_DATA (or DISCARD). Any GROSS_PASS (sample) is purely for validating the gross falsifier skeleton and leg-level accounting.

## Legs and sizing (constant lots from full-sample vol)
Long legs (top by long carry): {long_legs}
Short legs (bottom by long carry): {short_legs}

Lots (signed for short legs):
"""
    for p in long_legs + short_legs:
        manifest += f"- {p}: {lots.get(p, 0):.3f} lots (ann_vol={ann_vol.get(p, 0):.3f})\n"

    manifest += f"""

## Gross carry metrics (financing only + entry drag) - LEG-LEVEL ACCOUNTING
- Trading days: {len(index)}
- Total positive carry $ (income from legs with positive daily swap): ${total_pos:,.2f}
- Total negative carry $ (funding costs from legs with negative daily swap): ${total_neg:,.2f}
- Gross carry (pos - neg): ${gross_carry:,.2f}
- Initial entry drag $: ${initial_drag:,.2f}
- Net carry after drag (and after all leg-level funding costs): ${net_carry:,.2f}
- Carry gross PF (pos / (neg + drag)): {carry_pf:.3f}
- Max DD on cumulative carry equity (price risk not included; net daily carry): {max_dd*100:.2f}%

**Accounting note:** Positive and negative are now summed from *individual leg/day* contributions (long legs produce positive carry income; short legs produce negative carry = funding cost). This is the correct gross carry falsifier view even when net daily portfolio carry is always positive due to swamping. The equity curve / DD still reflect the net carry P&L to the book.

## Per-pair exact carry contribution (accumulated leg-by-leg with daily rollover applied)
"""
    for p, contrib in sorted(pair_contrib.items(), key=lambda x: -x[1]):
        leg_type = "LONG" if p in long_legs else "SHORT"
        manifest += f"- {p} ({leg_type}): ${contrib:,.2f} (rate={'+' if rates[p]['long']>0 else ''}{rates[p]['long'] if leg_type=='LONG' else rates[p]['short']})\n"

    manifest += f"""

## Notes on implementation (smallest per scope)
- Daily loop over aligned trading days applies rollover and accrues carry $ *per leg* for separate pos/neg tracking.
- Ranks and lots fixed (static portfolio) => turnover drag only at t=0.
- If daily vol targeting + rebalance were used, turnover drag would be higher (future refinement after this falsifier).
- No optimization, no filters, no OOS split (gross-first diagnostic only).
- Next if GROSS_PASS on real data: add realistic costs beyond entry, chronological IS/OOS split, robustness (carry crash periods), concentration, then net + full gates.

## Verdict after gross falsifier
{verdict}

{failure}

This run used the *template* swap rates. Replace the JSON with real broker data and re-run both verifier and gross test to obtain GROSS_PASS_REAL_DATA (or DISCARD). Only then consider price P&L, additional costs, chronological IS/OOS, or carry-crash stress tests.
"""

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(manifest)
    print(f"Gross results written to {args.output}")

    print("\n=== Summary ===")
    print(f"Gross PF (carry): {carry_pf:.3f}")
    print(f"Net carry after drag: ${net_carry:,.2f}")
    print(f"Verdict: {verdict}")
    print("Real-data gate: JSON must be replaced with actual broker statement/API (source_date + broker filled, rates from live export) before GROSS_PASS_REAL_DATA. Re-run verifier + this test after replacement.")


if __name__ == "__main__":
    main()
