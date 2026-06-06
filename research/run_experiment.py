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
import sys
from pathlib import Path

from research.evaluate import IS_FRAC, MIN_OOS_PF, MIN_TRADES, evaluate_config
from research.strategy_config import CONFIG

# Agent-proof guard (see autosearch.py and docs/research/FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md).
_NEGATIVE_RESULT_REPORT = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "research"
    / "FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md"
)


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
    parser.add_argument(
        "--override-negative-result",
        type=str,
        default=None,
        help="Path to the negative-result report to bypass the FX-majors directional TA stop (see the 2026-06 locked finding).",
    )
    args = parser.parse_args()

    if args.override_negative_result is None:
        if _NEGATIVE_RESULT_REPORT.exists():
            print(
                f"STOP: FX-majors directional TA negative result locked (2026-06). "
                f"See {_NEGATIVE_RESULT_REPORT}. Use --override-negative-result to bypass only for qualifying re-entry criteria."
            )
            sys.exit(2)
    else:
        if not Path(args.override_negative_result).exists():
            print("ERROR: --override-negative-result file does not exist.")
            sys.exit(2)

    cfg = dict(CONFIG)
    if args.config:
        cfg.update(json.loads(Path(args.config).read_text()))
    _print_block(cfg)


if __name__ == "__main__":
    main()
