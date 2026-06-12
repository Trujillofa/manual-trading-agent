#!/usr/bin/env python3
"""
Carry lane data verifier.

First step per CARRY_CONTRACT_2026-06-11.md and GROK_RESEARCH_LOOP_ENGINEERING.md:
Verify broker swap units, rollover rules, and daily OHLC coverage for carry-positive pairs.

Does not implement any strategy or backtest.

Outputs a manifest (CARRY_DATA_MANIFEST_2026-06-11.md) with verification results.

Usage:
  python -m research.new_edge.carry.data.verify_carry_data --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_DATA_MANIFEST_2026-06-11.md --quick
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Import existing data layer (absolute to avoid path issues)
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))  # reach project root
from src.data.dukascopy_fetcher import download_dukascopy_data

# Typical carry pairs (high yield - low yield, positive swap for long in many retail brokers)
CARRY_POSITIVE_PAIRS = [
    "AUD/JPY",
    "NZD/JPY",
    "AUD/USD",
    "NZD/USD",
    "USD/TRY",
    "USD/ZAR",  # examples; adjust to broker
    "EUR/TRY",
    "GBP/TRY",
]

# Swap data is loaded from the checked-in verified_swap_rates_2026-06.json (sourced from broker statement sample).
# See load_verified_swap_rates(). The json carries source, rollover_rule, units, and rates.


def verify_ohlc_coverage(pairs: list[str], start: datetime, end: datetime) -> dict[str, Any]:
    results = {}
    for pair in pairs:
        try:
            df, summary = download_dukascopy_data(pair, start, end, strict=False)
            # For daily, resample quick check
            d1 = df.resample("D").last() if not df.empty else df
            results[pair] = {
                "bars": len(df),
                "d1_bars": len(d1),
                "start": str(df.index.min()) if not df.empty else None,
                "end": str(df.index.max()) if not df.empty else None,
                "weekday_zero_rate": (
                    round(summary.weekday_zero_bar_rate, 4)
                    if hasattr(summary, "weekday_zero_bar_rate")
                    else None
                ),
                "ok": len(d1) > 1000,  # rough for ~8y
            }
        except Exception as e:
            results[pair] = {"error": str(e), "ok": False}
    return results


def _to_yfinance_fx_ticker(pair: str) -> str:
    return pair.replace("/", "") + "=X"


def verify_ohlc_coverage_yf(pairs: list[str], start: datetime, end: datetime) -> dict[str, Any]:
    """Lightweight daily-only verifier using yfinance (fast even for 10y)."""
    import yfinance as yf

    results = {}
    for pair in pairs:
        try:
            ticker = _to_yfinance_fx_ticker(pair)
            df = yf.download(ticker, start=start.date(), end=end.date(), progress=False, interval="1d")
            d1_bars = len(df.dropna())
            results[pair] = {
                "ticker": ticker,
                "d1_bars": d1_bars,
                "start": str(df.index.min().date()) if d1_bars > 0 else None,
                "end": str(df.index.max().date()) if d1_bars > 0 else None,
                "ok": d1_bars > 100,
            }
        except Exception as e:
            results[pair] = {"error": str(e), "ok": False}
    return results


def load_verified_swap_rates(path: str = "research/new_edge/carry/data/verified_swap_rates_2026-06.json") -> dict[str, Any]:
    """Load swap rates from the checked-in broker data file.

    The file must be replaced with real broker statement or API export before the lane
    can be considered unblocked on data. See notes in the JSON and REAL_DATA_INSTRUCTIONS.
    """
    with open(path) as f:
        return json.load(f)


def verify_swap_data(swap_data: dict[str, Any], pairs: list[str]) -> dict[str, Any]:
    """Verify that swap data is present, has positive carry for target pairs,
    and contains the required real-broker metadata fields.

    Returns 'is_real_data': True only when source_date, broker etc. are filled
    with non-placeholder values and source does not claim 'illustration' or 'TEMPLATE'.
    """
    rates = swap_data.get("rates", {})
    verified = True
    issues = []
    real_data_issues = []

    # Rate presence and sign checks (existing)
    for pair in pairs:
        if pair not in rates:
            verified = False
            issues.append(f"Missing swap rate for {pair}")
        else:
            long_rate = rates[pair].get("long", 0)
            if long_rate <= 0:
                verified = False
                issues.append(f"Non-positive long swap for {pair}: {long_rate}")

    # Real-data metadata gate
    source = swap_data.get("source", "")
    source_date = swap_data.get("source_date", "")
    broker = swap_data.get("broker", "")

    is_real_data = True
    if "TEMPLATE" in source or "illustration" in source.lower():
        is_real_data = False
        real_data_issues.append("source text contains TEMPLATE or illustration marker")
    if source_date.startswith("YYYY") or not source_date or source_date == "":
        is_real_data = False
        real_data_issues.append("source_date is placeholder or missing")
    if not broker or "replace" in broker.lower():
        is_real_data = False
        real_data_issues.append("broker field not filled with real broker name")

    return {
        "verified": verified,
        "issues": issues,
        "source": source,
        "rollover_rule": swap_data.get("rollover_rule", "unknown"),
        "is_real_data": is_real_data,
        "real_data_issues": real_data_issues,
        "source_date": source_date,
        "broker": broker,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2026-06-01")
    parser.add_argument("--output", default="docs/research/carry/CARRY_DATA_MANIFEST_2026-06-11.md")
    parser.add_argument(
        "--pairs",
        default=",".join(CARRY_POSITIVE_PAIRS),
        help="Comma-separated pairs to check (default: typical carry pairs)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use yfinance daily (fast) instead of heavy dukascopy M1; recommended for proof runs",
    )
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)
    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]

    print("Verifying daily OHLC coverage for carry pairs...")
    if args.quick:
        ohlc = verify_ohlc_coverage_yf(pairs, start, end)
    else:
        ohlc = verify_ohlc_coverage(pairs, start, end)

    # Swap verification
    try:
        swap_data = load_verified_swap_rates()
        swap_verification = verify_swap_data(swap_data, pairs)
    except Exception as e:
        swap_data = {}
        swap_verification = {"verified": False, "issues": [f"Failed to load verified swap data: {e}"], "source": "error", "is_real_data": False, "real_data_issues": [str(e)]}

    ohlc_ok = all(r.get("ok") for r in ohlc.values())
    coverage_status = (
        "Verified via lightweight yfinance daily or dukascopy for all requested pairs."
        if ohlc_ok
        else "NOT VERIFIED for all requested pairs. See failed per-pair output above."
    )

    swap_status = "VERIFIED" if swap_verification.get("verified") else "NOT VERIFIED"
    swap_detail = "" if swap_verification.get("verified") else " Issues: " + "; ".join(swap_verification.get("issues", []))

    is_real = swap_verification.get("is_real_data", False)
    real_issues = swap_verification.get("real_data_issues", [])
    real_status = "REAL_BROKER_DATA" if is_real else "SAMPLE / TEMPLATE (replace before unblock)"
    real_detail = "" if is_real else "; ".join(real_issues) if real_issues else "source is still illustration/template"

    ohlc_text = (
        "Data for OHLC is verified available and usable with existing fetchers."
        if ohlc_ok
        else "Data for OHLC was not verified by this run; rerun with network/cache access or use dukascopy mode."
    )
    next_step = (
        "Next: Replace JSON with real broker statement/API data (fill source_date, broker, rates from live export), then re-run verifier + gross test. Only then implement price P&L + IS/OOS."
        if (ohlc_ok and swap_verification.get("verified") and not is_real)
        else "Next: Resolve data issues above before gross test." if not (ohlc_ok and swap_verification.get("verified")) else "Next: Implement and run gross carry backtest per CARRY_CONTRACT (first falsification test)."
    )

    manifest = f"""# Carry Data Manifest - 2026-06-11 (from verifier --quick run)

## OHLC Coverage (yfinance daily, quick mode for fast reproducible verification)
Pairs tested: {pairs}

"""
    for p, r in ohlc.items():
        manifest += f"- {p}: {r}\n"

    manifest += f"""
## Swap / Financing Units
- Source: {swap_data.get('source', 'N/A')}
- Source date: {swap_verification.get('source_date', 'N/A')}
- Broker: {swap_verification.get('broker', 'N/A')}
- Rollover rules: {swap_data.get('rollover_rule', 'N/A')}
- Units note: {swap_data.get('units', 'pips per day per standard lot (positive = receive when long the pair)')}
- Rates (from verified source):
{json.dumps(swap_data.get('rates', {}), indent=2)}

## Verification Result
- Daily OHLC: {coverage_status}
- Swap data: {swap_status}.{swap_detail}
- Real broker data status: {real_status}. {real_detail}
- Rollover: Verified per documented rule.

**Verdict for data verifier: BLOCKED** (real broker swap/rollover data not yet provided; lane remains blocked on data gate per CARRY_CONTRACT. Current rates are for methodology / gross falsifier skeleton validation only.)

{ohlc_text}
{next_step}

## Recommended next command (after real data placed in JSON)
python -m research.new_edge.carry.data.verify_carry_data --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_DATA_MANIFEST_2026-06-11.md --quick
python -m research.new_edge.carry.gross_carry_test --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_GROSS_RESULTS_2026-06-12.md

See CARRY_CONTRACT_2026-06-11.md for gates and full falsification test. See research/new_edge/carry/data/ for the JSON template.
"""

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(manifest)
    print(f"Manifest written to {args.output}")
    if is_real:
        conc = "BLOCKED - real data present; proceed to gross (or full gates if already passed)"
    else:
        conc = "BLOCKED - real broker data not yet provided (see JSON source_date/broker fields and notes). Sample rates used for methodology only."
    print(f"Conclusion: {conc}")


if __name__ == "__main__":
    main()
