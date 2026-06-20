#!/usr/bin/env python3
"""
Vol-regime lane data verifier.

Per VOL_REGIME_CONTRACT_2026-06-19.md: verify H1 OHLC coverage for seven FX majors
before any falsifier runs. Dukascopy M1 resampled to H1 via existing project helpers.

Usage:
  python -m research.new_edge.vol_regime.data.verify_vol_regime_data \
    --start 2016-01-01 --end 2026-06-01 \
    --output docs/research/vol_regime/VOL_REGIME_DATA_MANIFEST_2026-06-19.md
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from research.new_edge.vol_regime.range_compression_breakout_test import (
    FX_MAJORS,
    MIN_H1_BARS,
    fetch_h1_window,
)

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / "cache"


def verify_pair(
    pair: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
) -> dict[str, Any]:
    """Verify H1 bar coverage for one pair."""
    result: dict[str, Any] = {"pair": pair}
    try:
        bars = fetch_h1_window(pair, start, end, cache_dir)
        if bars.empty:
            result.update({"ok": False, "error": "no H1 bars returned"})
            return result

        idx = pd.to_datetime(bars.index, utc=True)
        in_window = bars[(idx >= start) & (idx < end)]
        bar_count = len(in_window)

        issues: list[str] = []
        for col in ("open", "high", "low", "close"):
            if col not in in_window.columns:
                issues.append(f"missing column {col}")
            elif in_window[col].isna().any():
                issues.append(f"NaN values in {col}")

        if not in_window.empty:
            bad_ohlc = (
                (in_window["high"] < in_window["low"])
                | (in_window["high"] < in_window["open"])
                | (in_window["high"] < in_window["close"])
                | (in_window["low"] > in_window["open"])
                | (in_window["low"] > in_window["close"])
            )
            if bad_ohlc.any():
                issues.append(f"{int(bad_ohlc.sum())} bars with invalid OHLC")

        result.update(
            {
                "bars_total": len(bars),
                "bars_in_window": bar_count,
                "start": str(in_window.index.min()) if bar_count else None,
                "end": str(in_window.index.max()) if bar_count else None,
                "issues": issues,
                "ok": bar_count >= MIN_H1_BARS and not issues,
            }
        )
    except Exception as exc:
        result.update({"ok": False, "error": str(exc)})
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
        "# Vol-Regime Data Manifest — 2026-06-19",
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
        "## Universe (fixed)",
        "",
    ]
    for pair in FX_MAJORS:
        lines.append(f"- {pair}")
    lines.extend(
        [
            "",
            f"## Minimum H1 bars required: {MIN_H1_BARS}",
            "",
            "## Per-pair verification",
            "",
        ]
    )

    for result in results:
        pair = result["pair"]
        lines.append(f"### {pair}")
        if "error" in result:
            lines.append(f"- Error: {result['error']}")
        else:
            lines.append(f"- Bars in window: {result.get('bars_in_window', 0)}")
            lines.append(f"- Range: {result.get('start')} → {result.get('end')}")
            issues = result.get("issues", [])
            if issues:
                lines.append(f"- Issues: {issues}")
        lines.append(f"- OK: {result.get('ok')}")
        lines.append("")

    lines.extend(
        [
            "## Data source",
            "- Dukascopy M1 BID candles resampled to H1 (`src.data.dukascopy_fetcher`)",
            "- Per-pair consolidated H1 parquet cache under `research/new_edge/vol_regime/data/cache/`",
            "",
            "## Cost model (documented, not optimized)",
            "- Gross run: zero friction",
            "- Net stage (if gross passes): 6.0 pips round-trip (2.0 spread + 1.0 slippage per side)",
            "",
            "## Next step",
            "If PASS: run range_compression_breakout_test (gross-first falsifier).",
            "If BLOCKED: fix data gaps before any backtest.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Vol-regime H1 data verifier")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2026-06-01")
    parser.add_argument(
        "--output",
        default="docs/research/vol_regime/VOL_REGIME_DATA_MANIFEST_2026-06-19.md",
    )
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)
    cache_dir = Path(args.cache_dir)

    command = (
        "python -m research.new_edge.vol_regime.data.verify_vol_regime_data "
        f"--start {args.start} --end {args.end} --output {args.output}"
    )

    results = [verify_pair(pair, start, end, cache_dir) for pair in FX_MAJORS]
    manifest = build_manifest(results, args.start, args.end, command)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(manifest, encoding="utf-8")

    print(f"Manifest written to {out_path}")
    for result in results:
        status = "OK" if result.get("ok") else "FAIL"
        bars = result.get("bars_in_window", 0)
        print(f"  {result['pair']}: {status} ({bars} H1 bars)")


if __name__ == "__main__":
    main()
