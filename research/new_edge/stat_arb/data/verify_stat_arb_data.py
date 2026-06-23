#!/usr/bin/env python3
"""
Stat-arb lane data verifier.

Per STAT_ARB_CONTRACT_2026-06-18.md: verify synchronized daily OHLC coverage for candidate
spread legs before any strategy code runs.

Usage:
  python -m research.new_edge.stat_arb.data.verify_stat_arb_data \
    --start 2016-01-01 --end 2026-06-01 \
    --output docs/research/stat_arb/STAT_ARB_DATA_MANIFEST_2026-06-18.md
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

CANDIDATE_SPREADS: list[dict[str, str]] = [
    {"id": "eur_gbp", "leg_a": "EUR/USD", "leg_b": "GBP/USD"},
    {"id": "aud_nzd", "leg_a": "AUD/USD", "leg_b": "NZD/USD"},
    {"id": "cad_aud_jpy", "leg_a": "CAD/JPY", "leg_b": "AUD/JPY"},
]

# Per-leg spread assumptions for two-leg cost model (majors / minors)
DEFAULT_SPREAD_PIPS: dict[str, float] = {
    "EUR/USD": 1.5,
    "GBP/USD": 2.0,
    "AUD/USD": 2.0,
    "NZD/USD": 2.5,
    "CAD/JPY": 2.5,
    "AUD/JPY": 2.5,
}


def _to_yfinance_fx_ticker(pair: str) -> str:
    return pair.replace("/", "") + "=X"


def fetch_daily_closes(pair: str, start: datetime, end: datetime) -> pd.Series | None:
    ticker = _to_yfinance_fx_ticker(pair)
    df = yf.download(ticker, start=start.date(), end=end.date(), progress=False, interval="1d")
    if df is None or df.empty:
        return None
    s = df["Close"].dropna()
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s.name = pair
    return s if len(s) > 100 else None


def verify_spread(spread: dict[str, str], start: datetime, end: datetime) -> dict[str, Any]:
    leg_a = spread["leg_a"]
    leg_b = spread["leg_b"]
    result: dict[str, Any] = {"id": spread["id"], "leg_a": leg_a, "leg_b": leg_b}

    series_a = fetch_daily_closes(leg_a, start, end)
    series_b = fetch_daily_closes(leg_b, start, end)

    if series_a is None:
        result["leg_a_status"] = {"ok": False, "error": "no data or <100 bars"}
    else:
        result["leg_a_status"] = {
            "ok": True,
            "bars": len(series_a),
            "start": str(series_a.index.min().date()),
            "end": str(series_a.index.max().date()),
        }

    if series_b is None:
        result["leg_b_status"] = {"ok": False, "error": "no data or <100 bars"}
    else:
        result["leg_b_status"] = {
            "ok": True,
            "bars": len(series_b),
            "start": str(series_b.index.min().date()),
            "end": str(series_b.index.max().date()),
        }

    if series_a is not None and series_b is not None:
        aligned = pd.concat([series_a, series_b], axis=1, join="inner").dropna()
        result["aligned_bars"] = len(aligned)
        result["aligned_start"] = str(aligned.index.min().date()) if len(aligned) else None
        result["aligned_end"] = str(aligned.index.max().date()) if len(aligned) else None
        result["ok"] = len(aligned) >= 1000
    else:
        result["aligned_bars"] = 0
        result["ok"] = False

    spread_a = DEFAULT_SPREAD_PIPS.get(leg_a, 2.5)
    spread_b = DEFAULT_SPREAD_PIPS.get(leg_b, 2.5)
    result["cost_model"] = {
        "spread_pips_leg_a": spread_a,
        "spread_pips_leg_b": spread_b,
        "round_trip_pips_gross": 2 * (spread_a + spread_b),
        "round_trip_pips_with_slippage": 2 * (spread_a + spread_b) + 2.0,
        "note": "Two-leg entry+exit; 1 pip slippage per leg per side",
    }
    return result


def build_manifest(
    results: list[dict[str, Any]],
    start: str,
    end: str,
    command: str,
) -> str:
    all_ok = all(r.get("ok") for r in results)
    verdict = "PASS" if all_ok else "BLOCKED"

    lines = [
        "# Stat-Arb Data Manifest — 2026-06-18",
        "",
        f"## Verdict: {verdict}",
        "",
        "## Command",
        "```bash",
        command,
        "```",
        "",
        f"## Window requested: {start} → {end}",
        "",
        "## Per-spread verification",
        "",
    ]

    for r in results:
        lines.append(f"### {r['id']} ({r['leg_a']} vs {r['leg_b']})")
        lines.append(f"- Leg A: {r.get('leg_a_status')}")
        lines.append(f"- Leg B: {r.get('leg_b_status')}")
        lines.append(f"- Aligned bars: {r.get('aligned_bars', 0)}")
        lines.append(f"- Aligned range: {r.get('aligned_start')} → {r.get('aligned_end')}")
        lines.append(f"- OK: {r.get('ok')}")
        cm = r.get("cost_model", {})
        lines.append(
            f"- Two-leg round-trip cost estimate: {cm.get('round_trip_pips_with_slippage')} pips "
            f"(spread {cm.get('spread_pips_leg_a')}+{cm.get('spread_pips_leg_b')} per side)"
        )
        lines.append("")

    lines.extend(
        [
            "## Data source",
            "- yfinance daily closes (`PAIR=X` tickers)",
            "- Strict inner-join alignment (common trading days only)",
            "",
            "## Next step",
            "If PASS: run gross_stat_arb_test (gross-first falsifier, zero friction).",
            "If BLOCKED: fix data gaps before any backtest.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2026-06-01")
    parser.add_argument(
        "--output",
        default="docs/research/stat_arb/STAT_ARB_DATA_MANIFEST_2026-06-18.md",
    )
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)

    command = (
        "python -m research.new_edge.stat_arb.data.verify_stat_arb_data "
        f"--start {args.start} --end {args.end} --output {args.output}"
    )

    results = [verify_spread(s, start, end) for s in CANDIDATE_SPREADS]
    manifest = build_manifest(results, args.start, args.end, command)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(manifest, encoding="utf-8")

    print(f"Manifest written to {out_path}")
    for r in results:
        status = "OK" if r.get("ok") else "FAIL"
        print(f"  {r['id']}: {status} ({r.get('aligned_bars', 0)} aligned bars)")


if __name__ == "__main__":
    main()
