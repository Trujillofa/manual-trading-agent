#!/usr/bin/env python3
"""One-shot gross TSMOM diagnostic runner (the cheap path to the kill-switch number).

Usage (after caches exist):
    PYTHONPATH=. .venv/bin/python -m research.multiasset.run_gross_check \
        --symbols XAUUSD,XAGUSD,USA500,DEU40,GBR100,EURUSD,GBPUSD,USDJPY \
        --lookback 252

It will load whatever d1 caches are present, compute the correlation matrix,
run the gross portfolio backtest (signal-on-close → position-next-bar),
and print the PF / Sharpe that feed THE GATE.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from research.multiasset.backtest import (
    load_universe_d1,
    print_gate_report,
    run_gross_tsmom_backtest,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--symbols",
        default="XAUUSD,XAGUSD,USA500,USATECH,DEU40,GBR100,JPN225,EURUSD,GBPUSD,USDJPY",
    )
    ap.add_argument("--lookback", type=int, default=252)
    ap.add_argument("--cache-root", default="data/cache/multiasset")
    args = ap.parse_args()

    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    print(f"Loading d1 closes for: {syms}")
    closes = load_universe_d1(syms, cache_root=Path(args.cache_root))
    print(f"Loaded {len(closes)} days across {closes.shape[1]} instruments")

    if closes.empty:
        print("No data. Populate caches first (see MULTIASSET_MOMENTUM_EXECUTION_PLAN.md).")
        return

    result = run_gross_tsmom_backtest(closes, lookback=args.lookback)
    print_gate_report(result, universe_label=",".join(closes.columns.tolist()))

    # Also dump a tiny equity tail for eyeballing
    eq = result.get("equity")
    if eq is not None and len(eq) > 5:
        print("\nEquity tail (last 5):")
        print(eq.tail().round(4))


if __name__ == "__main__":
    main()
