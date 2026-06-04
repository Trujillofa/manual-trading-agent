"""Automated overnight researcher — the hands-free version of the loop.

Mirrors autoresearch's keep/discard loop, but does the mutation automatically:
start from the baseline CONFIG, perturb 1-2 parameters from PARAM_SPACE, evaluate
on the held-out judge, keep the change only if the score improves, otherwise
revert. Every trial is appended to research/results.tsv. The best *KEEP-verdict*
config found is written to research/best_config.json.

Crucially, "improvement" is measured by the out-of-sample-gated score in
research/evaluate.py, so the search cannot win by overfitting.

Usage:
    python -m research.autosearch --iters 200 --seed 0
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

from research.evaluate import evaluate_config
from research.strategy_config import CONFIG, PARAM_SPACE

RESULTS = Path(__file__).resolve().parent / "results.tsv"
BEST_JSON = Path(__file__).resolve().parent / "best_config.json"
HEADER = "ts\tscore\toos_pf\toos_pnl_pct\toos_trades\tstatus\tdescription\n"


def _ensure_results() -> None:
    if not RESULTS.exists():
        RESULTS.write_text(HEADER)


def _log(score: float, oos_pf: float, oos_pnl: float, oos_trades: int, status: str, desc: str) -> None:
    ts = time.strftime("%Y%m%dT%H%M%S")
    desc = desc.replace("\t", " ").replace("\n", " ")
    with RESULTS.open("a") as f:
        f.write(f"{ts}\t{score:.4f}\t{oos_pf:.3f}\t{oos_pnl:.3f}\t{oos_trades}\t{status}\t{desc}\n")


def _perturb(cfg: dict, rng: random.Random) -> tuple[dict, str]:
    new = dict(cfg)
    n_changes = rng.choice([1, 1, 2])  # mostly single-param moves
    changed = []
    for _ in range(n_changes):
        key = rng.choice(list(PARAM_SPACE.keys()))
        val = rng.choice(PARAM_SPACE[key])
        new[key] = val
        changed.append(f"{key}={val}")
    return new, ", ".join(changed)


def main() -> None:
    parser = argparse.ArgumentParser(description="Automated trading autoresearch loop")
    parser.add_argument("--iters", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    _ensure_results()

    # Baseline
    best_cfg = dict(CONFIG)
    base = evaluate_config(best_cfg)
    best_score = base.score
    _log(base.score, base.oos_stats.pf, base.oos_stats.mean_pnl_pct, base.oos_stats.trades,
         "baseline", "baseline CONFIG")
    print(f"baseline: score={base.score:.4f} verdict={base.verdict} "
          f"oos_pf={base.oos_stats.pf:.2f} oos_pnl={base.oos_stats.mean_pnl_pct:.2f}% "
          f"oos_n={base.oos_stats.trades}")

    best_keep_score = base.score if base.verdict == "KEEP" else float("-inf")
    if base.verdict == "KEEP":
        BEST_JSON.write_text(json.dumps(best_cfg, indent=2))

    for i in range(1, args.iters + 1):
        cand, desc = _perturb(best_cfg, rng)
        res = evaluate_config(cand)
        improved = res.score > best_score
        status = "keep" if improved else "discard"
        _log(res.score, res.oos_stats.pf, res.oos_stats.mean_pnl_pct, res.oos_stats.trades,
             status, desc)
        flag = "+" if improved else " "
        print(f"[{i:04d}]{flag} score={res.score:7.4f} ({res.verdict:7s}) "
              f"oos_pf={res.oos_stats.pf:5.2f} oos_pnl={res.oos_stats.mean_pnl_pct:6.2f}% "
              f"oos_n={res.oos_stats.trades:3d} | {desc}")
        if improved:
            best_cfg, best_score = cand, res.score
            if res.verdict == "KEEP" and res.score > best_keep_score:
                best_keep_score = res.score
                BEST_JSON.write_text(json.dumps(best_cfg, indent=2))
                print(f"        ^ new KEEP-verdict best written to {BEST_JSON.name}")

    print("\n=== search done ===")
    if best_keep_score > float("-inf"):
        print(f"Best OOS-confirmed config (verdict KEEP) saved to {BEST_JSON}")
    else:
        print("No config passed the out-of-sample gates. This is an honest negative: "
              "no robustly profitable config was found in this space.")


if __name__ == "__main__":
    main()
