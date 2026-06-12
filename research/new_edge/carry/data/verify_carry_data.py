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

# Static example swap table (pips per day, long/short; from typical OANDA/cTrader public data around 2026; VERIFY WITH YOUR BROKER)
# Positive = receive when long the pair
STATIC_SWAP_TABLE: dict[str, dict[str, float]] = {
    "AUD/JPY": {"long": 1.8, "short": -2.5},
    "NZD/JPY": {"long": 1.5, "short": -2.2},
    # ... add more from your broker statements
    "USD/JPY": {"long": -0.5, "short": 0.2},  # usually negative for long USDJPY
}

ROLLOVER_RULE = "3x swap on Wednesdays for most pairs (exceptions for holidays, broker specific)."


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

    ohlc_ok = all(r.get("ok") for r in ohlc.values())
    coverage_status = (
        "Verified via lightweight yfinance daily or dukascopy for all requested pairs."
        if ohlc_ok
        else "NOT VERIFIED for all requested pairs. See failed per-pair output above."
    )

    manifest = f"""# Carry Data Manifest - 2026-06-11

## OHLC Coverage (dukascopy_fetcher or yfinance daily, quick mode)
Pairs tested: {pairs}

"""
    for p, r in ohlc.items():
        manifest += f"- {p}: {r}\n"

    manifest += f"""
## Swap / Financing Units
- Source: STATIC TABLE ONLY (see below). No broker API fetcher or live swap data in current data layer (dukascopy only OHLC; settings has spreads but no swaps; oanda config for execution only).
- Example values (pips/day, verify against your broker statements for exact units and sign):
{STATIC_SWAP_TABLE}
- Rollover rules: {ROLLOVER_RULE} (standard for most FX CFDs; confirm per broker calendar for holidays).
- Units note: Swaps usually quoted in base or quote currency per lot; convert consistently for portfolio P&L. Positive for the high-yield leg.

## Verification Result
- Daily OHLC: {coverage_status}
- Swap data: NOT INTEGRATED. Broker-specific, changes over time with rate decisions. Static table is for initial contract only.
- Rollover: Documented as standard rule; no code yet to apply 3x.

**Verdict for data verifier: BLOCKED**

{"Data for OHLC is verified available and usable with existing fetchers." if ohlc_ok else "Data for OHLC was not verified by this run; rerun with network/cache access or use dukascopy mode."}
Swap units and exact broker rollover not present in code/config -> cannot run even gross carry backtest without adding data source.
Next: Add swap data fetcher or verified static table + rollover calendar before strategy code.

## Recommended next command after data source added
python -m research.new_edge.carry.run --config research/new_edge/carry/config.yaml --gross-only

See CARRY_CONTRACT_2026-06-11.md for full gates.
"""

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(manifest)
    print(f"Manifest written to {args.output}")
    print("Conclusion: BLOCKED - swap data source missing.")

if __name__ == "__main__":
    main()
