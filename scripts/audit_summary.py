#!/usr/bin/env python3
"""Audit summary for paper-shadow and Branch B monitoring.

Parses signal_audit.jsonl and reports states, block reasons, entry counts,
and telemetry scans over a recent window.

Usage examples:
  python scripts/audit_summary.py --days 7
  python scripts/audit_summary.py --days 30 --audit /path/to/logs/signal_audit.jsonl

This file is committed so it survives deploys (unlike ad-hoc prod installs).
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Summarize recent audit for monitoring.")
    p.add_argument("--days", type=int, default=7, help="Lookback window in days (default 7)")
    p.add_argument(
        "--audit",
        type=str,
        default="logs/signal_audit.jsonl",
        help="Path to audit jsonl (default logs/signal_audit.jsonl relative to cwd or /home/emilio/manual-trading-agent)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    audit_path = Path(args.audit)
    if not audit_path.exists():
        # Try common prod location
        audit_path = Path("/home/emilio/manual-trading-agent") / args.audit
    if not audit_path.exists():
        print(f"ERROR: audit file not found at {args.audit} or prod default", file=sys.stderr)
        sys.exit(2)

    cutoff = datetime.now(UTC) - timedelta(days=args.days)
    state_ctr: collections.Counter[str] = collections.Counter()
    block_ctr: collections.Counter[str] = collections.Counter()
    entry_count = 0
    tele_scans = 0

    with audit_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue

            ts_str = rec.get("ts")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception:
                continue
            if ts < cutoff:
                continue

            if rec.get("kind") == "scan_telemetry":
                tele_scans += 1
                cnt = rec.get("counts", {}) or {}
                entry_count += int(cnt.get("entry", 0))

            st = rec.get("state")
            if st:
                state_ctr[st] += 1

            for r in rec.get("reasons", []) or []:
                block_ctr[r] += 1

            if rec.get("state") == "entry":
                entry_count += 1

    print(f"=== Audit summary (last {args.days} days, cutoff ~{cutoff.date()}) ===")
    print(f"File: {audit_path}")
    print("States:", dict(state_ctr.most_common(8)) if state_ctr else "{}")
    print("Top blocks:", dict(block_ctr.most_common(8)) if block_ctr else "{}")
    print("Telemetry scans:", tele_scans)
    print("Entries (from counts + state):", entry_count)
    print("Note: low entry count is expected under current gates (Branch B). Compare distributions pre/post changes.")

if __name__ == "__main__":
    main()
