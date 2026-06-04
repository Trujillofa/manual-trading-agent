"""Run one experiment: evaluate research/strategy_config.CONFIG and print a summary.

Analog of `uv run train.py`. Reads the metric from the held-out judge in
research/evaluate.py and prints an autoresearch-style block. Extract the key
metric with:  grep "^score:" run.log

Usage:
    python -m research.run_experiment
    python -m research.run_experiment --config path/to/config.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.evaluate import IS_FRAC, MIN_OOS_PF, MIN_TRADES, evaluate_config
from research.strategy_config import CONFIG


def _print_block(cfg: dict) -> None:
    res = evaluate_config(cfg)
    is_s, oos_s = res.is_stats, res.oos_stats
    print("---")
    print(f"score:             {res.score:.4f}")
    print(f"verdict:           {res.verdict}")
    print(f"oos_pnl_pct:       {oos_s.mean_pnl_pct:.3f}")
    print(f"oos_pf:            {oos_s.pf:.3f}")
    print(f"oos_win_rate:      {oos_s.win_rate:.3f}")
    print(f"oos_trades:        {oos_s.trades}")
    print(f"oos_max_consec_l:  {oos_s.max_consec_losses}")
    print(f"is_pnl_pct:        {is_s.mean_pnl_pct:.3f}")
    print(f"is_pf:             {is_s.pf:.3f}")
    print(f"is_trades:         {is_s.trades}")
    print(f"is_frac:           {IS_FRAC}")
    print(f"gates:             min_trades={MIN_TRADES} min_oos_pf={MIN_OOS_PF}")
    if res.reasons:
        print(f"fail_reasons:      {'; '.join(res.reasons)}")
    print("---")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one autoresearch trading experiment")
    parser.add_argument("--config", type=str, default=None, help="JSON config override")
    args = parser.parse_args()

    cfg = dict(CONFIG)
    if args.config:
        cfg.update(json.loads(Path(args.config).read_text()))
    _print_block(cfg)


if __name__ == "__main__":
    main()
