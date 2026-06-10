#!/usr/bin/env python3
"""
Branch B alert quality summarizer.

Reads the existing logs/signal_audit.jsonl (produced by the live scanner)
and produces the metrics needed for review:

- Alerts per period
- Blockers (especially news)
- TP-zone-before-SL rate (from outcome rows)
- Invalidation rate
- Favorable vs adverse excursion stats
- Time-to-outcome

Usage:
    python -m scripts.summarize_alerts --days 30 --format table
    python -m scripts.summarize_alerts --json

The script is intentionally lightweight (no extra DB required). It works on the
JSONL the scanner already writes.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

AUDIT_PATH = Path("logs/signal_audit.jsonl")
OBSERVATION_WINDOW_PATH = Path("logs/observation_window_start.txt")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_observation_start() -> datetime | None:
    if not OBSERVATION_WINDOW_PATH.exists():
        return None
    for line in OBSERVATION_WINDOW_PATH.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Start UTC:"):
            continue
        raw_value = line.split(":", 1)[1].strip()
        timestamp = raw_value.split()[0]
        return _parse_ts(timestamp)
    return None


def load_audit(days: int | None = None) -> list[dict[str, Any]]:
    if not AUDIT_PATH.exists():
        return []
    cutoff = None
    if days:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

    rows: list[dict[str, Any]] = []
    with AUDIT_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if cutoff and row.get("ts", "") < cutoff:
                continue
            rows.append(row)
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_scans = sum(1 for r in rows if r.get("kind") == "scan_telemetry")
    entries = [r for r in rows if r.get("state") == "entry" or r.get("kind") == "signal"]
    outcomes = [
        r for r in rows if r.get("state") == "outcome" or r.get("kind") == "alert_outcome"
    ]

    # Blockers from telemetry (explicit ones set in payload)
    blocker_counts: Counter[str] = Counter()
    news_blocks = 0
    for r in rows:
        if r.get("kind") == "scan_telemetry":
            blockers = r.get("blockers", {})
            for name, hit in blockers.items():
                if hit:
                    blocker_counts[name] += 1
            if blockers.get("news"):
                news_blocks += 1

    # Outcomes
    tp_first = sum(1 for o in outcomes if o.get("outcome") in ("tp", "tp_zone_first"))
    sl_first = sum(1 for o in outcomes if o.get("outcome") in ("sl", "sl_zone_first"))
    invalidated = sum(1 for o in outcomes if "invalidat" in str(o.get("outcome", "")).lower())

    fav = [
        float(o.get("max_favorable_pips", 0))
        for o in outcomes
        if o.get("max_favorable_pips") is not None
    ]
    adv = [
        float(o.get("max_adverse_pips", 0))
        for o in outcomes
        if o.get("max_adverse_pips") is not None
    ]

    # Explain neutral scans that have useful context but no explicit no-trade reasons.
    practical_blocker_counts: Counter[str] = Counter()
    for r in rows:
        if r.get("kind") != "scan_telemetry" or r.get("state") == "entry":
            continue
        context = r.get("context", {})
        if not isinstance(context, dict):
            context = {}

        rsi_1h = context.get("rsi_1h")
        rsi_30m = context.get("rsi_30m")
        rsi_15m = context.get("rsi_15m")
        if all(v is not None for v in (rsi_1h, rsi_30m, rsi_15m)):
            r1 = float(rsi_1h)
            r30 = float(rsi_30m)
            r15 = float(rsi_15m)
            all_oversold = r1 < 30 and r30 < 30 and r15 < 30
            all_overbought = r1 > 70 and r30 > 70 and r15 > 70
            if not (all_oversold or all_overbought):
                practical_blocker_counts["mtf_rsi_not_aligned"] += 1

        is_ranging = context.get("is_ranging")
        adx = context.get("adx_1h")
        if is_ranging is False or (adx is not None and float(adx) >= 25):
            practical_blocker_counts["adx_trending_context"] += 1

    # Merge practical into main blocker_counts for reporting (they are additional context)
    for k, v in practical_blocker_counts.items():
        blocker_counts[k] += v

    observation_start = _load_observation_start()
    rows_since_window = []
    if observation_start:
        rows_since_window = [
            r
            for r in rows
            if (row_ts := _parse_ts(str(r.get("ts", "")))) is not None
            and row_ts >= observation_start
        ]

    return {
        "window_days": "all" if not any(r.get("ts") for r in rows) else "filtered",
        "total_scans_with_telemetry": total_scans,
        "fired_signals": len(entries),
        "resolved_outcomes": len(outcomes),
        "tp_zone_first": tp_first,
        "sl_zone_first": sl_first,
        "invalidated": invalidated,
        "news_blocks": news_blocks,
        "blocker_counts": dict(blocker_counts.most_common()),
        "avg_fav_pips": round(sum(fav) / len(fav), 1) if fav else 0,
        "avg_adv_pips": round(sum(adv) / len(adv), 1) if adv else 0,
        "median_fav_pips": round(sorted(fav)[len(fav)//2], 1) if fav else 0,
        "median_adv_pips": round(sorted(adv)[len(adv)//2], 1) if adv else 0,
        "observation_start": observation_start.isoformat() if observation_start else None,
        "rows_since_window_start": len(rows_since_window),
        "telemetry_since_window_start": sum(
            1 for r in rows_since_window if r.get("kind") == "scan_telemetry"
        ),
        "latest_ts": max((r.get("ts") for r in rows if r.get("ts")), default=None),
    }


def print_table(stats: dict[str, Any]) -> None:
    print("Branch B Alert Quality Summary")
    print("=" * 40)
    print(f"Window:                  {stats['window_days']}")
    print(f"Scans (with telemetry):  {stats['total_scans_with_telemetry']}")
    print(f"Fired signals:           {stats['fired_signals']}")
    print(f"Resolved outcomes:       {stats['resolved_outcomes']}")
    if stats.get("observation_start"):
        print(f"Observation start:       {stats['observation_start']}")
        print(f"Rows since start:        {stats['rows_since_window_start']}")
        print(f"Telemetry since start:   {stats['telemetry_since_window_start']}")
    if stats.get("latest_ts"):
        print(f"Latest row:              {stats['latest_ts']}")
    print()
    print("Outcomes:")
    total_out = stats['resolved_outcomes'] or 1
    print(f"  TP zone first:   {stats['tp_zone_first']} ({stats['tp_zone_first']/total_out*100:.1f}%)")
    print(f"  SL zone first:   {stats['sl_zone_first']} ({stats['sl_zone_first']/total_out*100:.1f}%)")
    print(f"  Invalidated:     {stats['invalidated']} ({stats['invalidated']/total_out*100:.1f}%)")
    print()
    print("News & Blockers:")
    print(f"  News blocks:     {stats['news_blocks']}")
    for name, count in stats["blocker_counts"].items():
        print(f"  {name}: {count}")
    print()
    print("Excursion (pips, resolved outcomes):")
    print(f"  Avg favorable:   {stats['avg_fav_pips']}")
    print(f"  Avg adverse:     {stats['avg_adv_pips']}")
    print(f"  Median fav:      {stats['median_fav_pips']}")
    print(f"  Median adv:      {stats['median_adv_pips']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None, help="Limit to last N days")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    args = parser.parse_args()

    rows = load_audit(days=args.days)
    stats = summarize(rows)

    if args.format == "json":
        print(json.dumps(stats, indent=2))
    else:
        print_table(stats)


if __name__ == "__main__":
    main()
