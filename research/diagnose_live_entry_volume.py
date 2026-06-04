#!/usr/bin/env python3
"""Quick diagnostic for live entry fire rate and rejection reasons.

Uses the pure evaluate_entry on recent slices of the configured PAIRS.
Helps understand volume (why so few trades in the R1 harness runs).

Example:
  python research/diagnose_live_entry_volume.py --bars 1500
  LIVE_BT_MAX_BARS=2000 python research/diagnose_live_entry_volume.py
  # what-if volume if we relax the top blockers seen in 8-pair run:
  python research/diagnose_live_entry_volume.py --bars 1000 --no-session --adx-threshold 40
"""

from __future__ import annotations

import argparse
import collections
import os
import sys
from datetime import UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.evaluate import fetch_pair  # noqa: E402
from src.scanner.evaluator import evaluate_entry  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bars",
        type=int,
        default=int(os.environ.get("LIVE_BT_MAX_BARS", 1500)),
        help="Number of recent 15m bars to analyze per pair (default 1500 ~15-20 trading days)",
    )
    parser.add_argument("--warmup", type=int, default=200)
    parser.add_argument(
        "--pairs",
        type=str,
        default=None,
        help="Comma-separated list of pairs to analyze (default: current PAIRS from research.evaluate)",
    )
    # Overrides for "what-if" volume on live entry family (uses the new evaluate_entry(overrides=) support)
    parser.add_argument(
        "--adx-threshold",
        type=float,
        default=None,
        help="Override ADX for this run (higher = more ranging allowed)",
    )
    parser.add_argument("--rsi-oversold", type=float, default=None)
    parser.add_argument("--rsi-overbought", type=float, default=None)
    parser.add_argument("--buffer-pips", type=float, default=None)
    parser.add_argument("--confirm-bars", type=int, default=None)
    parser.add_argument(
        "--no-session",
        action="store_true",
        help="Force disable session filter (estimate volume if session relaxed)",
    )
    args = parser.parse_args()

    # Import here so we get the (possibly monkey-patched for tests) PAIRS
    from research.evaluate import PAIRS as PAIRS_LIST

    if args.pairs:
        pairs = [p.strip().upper().replace("_", "/") for p in args.pairs.split(",") if p.strip()]
    else:
        pairs = list(PAIRS_LIST)
    print(
        f"Diagnosing live entry volume on {len(pairs)} pairs, last ~{args.bars} 15m bars each (warmup {args.warmup})..."
    )

    total_fires = 0
    total_aligned = 0
    total_bars = 0
    all_reasons: collections.Counter[str] = collections.Counter()
    per_pair: dict[str, dict] = {}

    # Build overrides for this diagnostic run (enables quick "relax gate X" experiments on live family)
    ov: dict[str, object] = {}
    if args.adx_threshold is not None:
        ov["adx_threshold"] = args.adx_threshold
    if args.rsi_oversold is not None:
        ov["rsi_oversold"] = args.rsi_oversold
    if args.rsi_overbought is not None:
        ov["rsi_overbought"] = args.rsi_overbought
    if args.buffer_pips is not None:
        ov["buffer_pips"] = args.buffer_pips
    if args.confirm_bars is not None:
        ov["confirm_bars"] = args.confirm_bars
    if args.no_session:
        ov["session_filter_enabled"] = False
    overrides = ov if ov else None
    if overrides:
        print(f"  (using overrides: {overrides})")

    for pair in pairs:
        print(f"  {pair}: loading recent data...")
        data = fetch_pair(
            pair, days=365
        )  # use full cached window for reliable cache hits, then slice recent
        if data is None or data.get("15m") is None or len(data["15m"]) < args.bars:
            print("    skipping (insufficient data)")
            continue

        d15 = data["15m"].iloc[-args.bars :].copy()
        d30 = data["30m"]
        d1h = data["1h"]

        fires = 0
        aligned = 0
        reasons_ctr: collections.Counter[str] = collections.Counter()
        n = len(d15)

        for i in range(args.warmup, n):
            ts = d15.index[i]
            data_15m = d15.iloc[: i + 1]
            data_30m = d30[d30.index <= ts].iloc[-120:] if not d30.empty else data_15m.iloc[-10:]
            data_1h = d1h[d1h.index <= ts].iloc[-120:] if not d1h.empty else data_15m.iloc[-10:]

            dec = evaluate_entry(
                pair,
                data_1h,
                data_30m,
                data_15m,
                active_signal_state={},
                alignment_state={},
                now_utc=ts.to_pydatetime().replace(tzinfo=UTC),
                spread_quote={"spread": 0.0, "source": "diag"},
                news_blocked=False,
                spread_filter_enabled=False,
                bars_aligned=None,
                overrides=overrides,
            )
            if dec.get("aligned"):
                aligned += 1
            if dec.get("fired"):
                fires += 1
            for r in dec.get("no_trade_reasons", []):
                reasons_ctr[r] += 1

            if (i - args.warmup) % 200 == 0 and (i - args.warmup) > 0:
                print(
                    f"    {pair}: processed {i - args.warmup}/{n - args.warmup} bars...", flush=True
                )

        # Print per-pair results as soon as the pair finishes (good for long runs)
        print(f"    {pair}: bars={n - args.warmup}, aligned={aligned}, fires={fires}", flush=True)
        for r, c in reasons_ctr.most_common(5):
            print(f"      {c:5d} : {r}", flush=True)

        total_fires += fires
        total_aligned += aligned
        total_bars += n - args.warmup
        all_reasons.update(reasons_ctr)

        per_pair[pair] = {
            "bars": n - args.warmup,
            "aligned": aligned,
            "fires": fires,
            "top_reasons": reasons_ctr.most_common(5),
        }
        print(f"    {pair}: bars={n - args.warmup}, aligned={aligned}, fires={fires}")

    print("\n=== Summary (pooled) ===")
    print(f"Total analyzed bars (post-warmup): {total_bars}")
    print(f"Total MTF aligned events: {total_aligned}")
    print(f"Total fires (would-enter signals): {total_fires}")
    if total_bars > 0:
        print(
            f"Fire rate: {total_fires / total_bars:.4f} per bar (~{total_fires / (total_bars / (24 * 4)):.2f} per day)"
        )

    print("\nTop rejection reasons across all pairs:")
    for reason, cnt in all_reasons.most_common(10):
        print(f"  {cnt:6d} : {reason}")

    print("\nPer-pair detail:")
    for p, stats in per_pair.items():
        print(f"  {p}: fires={stats['fires']} / {stats['bars']} bars, aligned={stats['aligned']}")
        for r, c in stats["top_reasons"]:
            print(f"      {c:5d} {r}")


if __name__ == "__main__":
    main()
