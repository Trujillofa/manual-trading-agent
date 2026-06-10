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
    outcomes = [r for r in rows if r.get("state") == "outcome" or r.get("kind") == "alert_outcome"]

    # Blockers from telemetry
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

    fav = [float(o.get("max_favorable_pips", 0)) for o in outcomes if o.get("max_favorable_pips") is not None]
    adv = [float(o.get("max_adverse_pips", 0)) for o in outcomes if o.get("max_adverse_pips") is not None]

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
    }


def print_table(stats: dict[str, Any]) -> None:
    print("Branch B Alert Quality Summary")
    print("=" * 40)
    print(f"Window:                  {stats['window_days']}")
    print(f"Scans (with telemetry):  {stats['total_scans_with_telemetry']}")
    print(f"Fired signals:           {stats['fired_signals']}")
    print(f"Resolved outcomes:       {stats['resolved_outcomes']}")
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
