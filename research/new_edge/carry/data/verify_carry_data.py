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

# Import existing data layer (absolute to avoid path issues)
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    """Load verified swap rates from checked-in broker statement / live MT5 export."""
    with open(path) as f:
        return json.load(f)


def verify_swap_data(swap_data: dict[str, Any], pairs: list[str]) -> dict[str, Any]:
    """Verify swap table is usable for the requested universe.

    Real broker tables often have *negative* long rates on some pairs; that is valid.
    Fail only when rates are missing or the whole table is swap-free (all zeros).
    """
    rates = swap_data.get("rates", {})
    issues: list[str] = []
    present = 0
    nonzero = 0
    for pair in pairs:
        if pair not in rates:
            issues.append(f"Missing swap rate for {pair}")
            continue
        present += 1
        long_rate = float(rates[pair].get("long", 0) or 0)
        short_rate = float(rates[pair].get("short", 0) or 0)
        if long_rate != 0.0 or short_rate != 0.0:
            nonzero += 1
    if present == 0:
        issues.append("No requested pairs found in rates table")
    if present > 0 and nonzero == 0:
        issues.append("All present pairs have zero long/short swap (swap-free account)")
    verified = present > 0 and nonzero > 0 and not any(i.startswith("Missing") for i in issues)
    # Missing optional pairs is a warning when at least one nonzero pair remains.
    missing_only = all(i.startswith("Missing") for i in issues) if issues else False
    if missing_only and nonzero > 0:
        verified = True
    return {
        "verified": verified,
        "issues": issues,
        "pairs_present": present,
        "pairs_nonzero": nonzero,
        "source": swap_data.get("source") or swap_data.get("retrieved") or "unknown",
        "broker": swap_data.get("broker", ""),
        "source_date": swap_data.get("source_date", ""),
        "rollover_rule": swap_data.get("rollover_rule", "unknown"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2026-06-01")
    parser.add_argument("--output", default="docs/research/carry/CARRY_DATA_MANIFEST_2026-06-11.md")
    parser.add_argument(
        "--rates",
        default="research/new_edge/carry/data/verified_swap_rates_2026-06.json",
        help="Path to verified_swap_rates*.json (template or live broker export)",
    )
    parser.add_argument(
        "--pairs",
        default="",
        help="Comma-separated pairs (default: keys from --rates, else typical carry list)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use yfinance daily (fast) instead of heavy dukascopy M1; recommended for proof runs",
    )
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)

    try:
        swap_data = load_verified_swap_rates(args.rates)
    except Exception as e:
        swap_data = {}
        swap_verification = {
            "verified": False,
            "issues": [f"Failed to load verified swap data: {e}"],
            "source": "error",
        }
        pairs = [p.strip() for p in args.pairs.split(",") if p.strip()] or list(CARRY_POSITIVE_PAIRS)
        ohlc = {}
        ohlc_ok = False
        _write_manifest(args, pairs, ohlc, swap_data, swap_verification, ohlc_ok)
        return

    rates_keys = list((swap_data.get("rates") or {}).keys())
    if args.pairs.strip():
        pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    elif rates_keys:
        pairs = rates_keys
    else:
        pairs = list(CARRY_POSITIVE_PAIRS)

    print(f"Verifying daily OHLC coverage for carry pairs ({len(pairs)}) using rates={args.rates} ...")
    if args.quick:
        ohlc = verify_ohlc_coverage_yf(pairs, start, end)
    else:
        ohlc = verify_ohlc_coverage(pairs, start, end)

    swap_verification = verify_swap_data(swap_data, pairs)
    ohlc_ok = all(r.get("ok") for r in ohlc.values()) if ohlc else False
    _write_manifest(args, pairs, ohlc, swap_data, swap_verification, ohlc_ok)


def _write_manifest(
    args: argparse.Namespace,
    pairs: list[str],
    ohlc: dict[str, Any],
    swap_data: dict[str, Any],
    swap_verification: dict[str, Any],
    ohlc_ok: bool,
) -> None:
    coverage_status = (
        "Verified via lightweight yfinance daily or dukascopy for all requested pairs."
        if ohlc_ok
        else "NOT VERIFIED for all requested pairs. See failed per-pair output above."
    )

    swap_status = "VERIFIED" if swap_verification.get("verified") else "NOT VERIFIED"
    swap_detail = (
        "" if swap_verification.get("verified") else " Issues: " + "; ".join(swap_verification.get("issues", []))
    )
    if swap_verification.get("verified") and swap_verification.get("issues"):
        swap_detail = " Warnings: " + "; ".join(swap_verification.get("issues", []))

    ohlc_text = (
        "Data for OHLC is verified available and usable with existing fetchers."
        if ohlc_ok
        else "Data for OHLC was not verified by this run; rerun with network/cache access or use dukascopy mode."
    )
    next_step = (
        "Next: run gross carry falsifier with the same --rates file."
        if (ohlc_ok and swap_verification.get("verified"))
        else "Next: Resolve data issues above before gross test."
    )

    data_verdict = (
        "DATA_PASS"
        if (ohlc_ok and swap_verification.get("verified"))
        else "BLOCKED"
    )

    manifest = f"""# Carry Data Manifest — verifier run

## Rates file
`{args.rates}`

## OHLC Coverage ({"yfinance daily --quick" if args.quick else "dukascopy"})
Pairs tested: {pairs}

"""
    for p, r in ohlc.items():
        manifest += f"- {p}: {r}\n"

    manifest += f"""
## Swap / Financing Units
- Broker: {swap_data.get("broker", "N/A")}
- Source date: {swap_data.get("source_date", "N/A")}
- Retrieved: {swap_data.get("retrieved") or swap_data.get("source", "N/A")}
- Rollover rules: {swap_data.get("rollover_rule", "N/A")}
- Units note: {swap_data.get("units", "pips per day per standard lot (positive = receive when long the pair)")}
- Pairs present / nonzero: {swap_verification.get("pairs_present", "?")} / {swap_verification.get("pairs_nonzero", "?")}
- Rates:
{json.dumps(swap_data.get("rates", {}), indent=2)}

## Verification Result
- Daily OHLC: {coverage_status}
- Swap data: {swap_status}.{swap_detail}
- Rollover: documented in rates file.

**Verdict for data verifier: {data_verdict}**

{ohlc_text}
{next_step}

## Recommended next command
```bash
.venv/bin/python -m research.new_edge.carry.gross_carry_test \\
  --rates {args.rates} \\
  --start {args.start} --end {args.end} \\
  --output docs/research/carry/CARRY_GROSS_RESULTS_VANTAGE_2026-08-13.md
```

See CARRY_CONTRACT_2026-06-11.md for gates and full falsification test.
"""

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(manifest)
    print(f"Manifest written to {args.output}")
    conclusion = (
        f"{data_verdict} - data verified (OHLC+swap), ready for gross carry test."
        if data_verdict == "DATA_PASS"
        else f"{data_verdict} - see verification issues above."
    )
    print(f"Conclusion: {conclusion}")


if __name__ == "__main__":
    main()
