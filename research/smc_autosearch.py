"""Automated LuxAlgo SMC parameter search.

Mirrors ``research/htf_fib_autosearch.py``: perturb 1-2 parameters from PARAM_SPACE,
evaluate on chronological IS/OOS with locked promotion gates, keep changes when
the OOS-penalized score improves. Writes ``research/smc_results.tsv``,
``research/smc_best_config.json``, and ``results/smc_optimal_comparison.md``.

Usage:
    python -m research.smc_autosearch --iters 100 --seed 0
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_donchian_backtest import fetch_pair
from scripts.run_htf_fib_backtest import (
    DEFAULT_PAIRS,
    IS_FRACTION,
    MIN_OOS_PROFIT_FACTOR,
    MIN_WINDOW_TRADES,
    WindowStats,
    aggregate_window,
    load_usd_conversion_closes,
    verdict,
)
from scripts.run_smc_backtest import (
    CONFIGS,
    OPTIMAL_COMPARISON_NAME,
    EvalRow,
    PreparedSmcData,
    StrategyConfig,
    _ensure_break_schedule,
    best_eval_row,
    config_from_dict,
    evaluate_config_on_pairs,
    prepare_smc_data,
    run_prepared_backtest,
    score_window_stats,
    write_comparison_report,
)

from research.smc_config import CONFIG, PARAM_SPACE

ROOT = Path(__file__).resolve().parent.parent
RESULTS = Path(__file__).resolve().parent / "smc_results.tsv"
BEST_JSON = Path(__file__).resolve().parent / "smc_best_config.json"
COMPARE_REPORT = ROOT / "results" / OPTIMAL_COMPARISON_NAME
HEADER = (
    "ts\tscore\tis_pf\tis_pnl_pct\tis_trades\toos_pf\toos_pnl_pct\toos_trades\t"
    "status\tentry_mode\ttag_filter\tdescription\n"
)


@dataclass(frozen=True)
class EvalResult:
    score: float
    verdict: str
    reasons: list[str]
    is_stats: WindowStats
    oos_stats: WindowStats
    config: StrategyConfig


@dataclass
class SearchContext:
    prepared: dict[str, PreparedSmcData]
    frames: dict[str, pd.DataFrame]
    cutoffs: dict[str, pd.Timestamp]


def _config_name(cfg: dict) -> str:
    return (
        f"{cfg['entry_mode']}_{cfg['tag_filter']}"
        f"_sw{cfg['swing_length']}_{cfg['structure_timeframe']}"
        f"_ob{cfg['ob_retest_bars']}"
        f"_tp{cfg['tp_atr']:g}_sl{cfg['sl_atr']:g}"
        f"_hold{cfg['max_hold_bars']}"
    )


def dict_to_strategy(cfg: dict) -> StrategyConfig:
    payload = dict(cfg)
    payload["name"] = _config_name(cfg)
    return config_from_dict(payload)


def _ensure_break_spec(prepared: PreparedSmcData, frame: pd.DataFrame, strategy: StrategyConfig) -> None:
    _ensure_break_schedule(prepared, frame, strategy.structure_spec)


def evaluate_smc_config(cfg: dict, *, ctx: SearchContext) -> EvalResult:
    try:
        strategy = dict_to_strategy(cfg)
    except (KeyError, ValueError, TypeError) as exc:
        empty = WindowStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0)
        return EvalResult(
            score=float("-inf"),
            verdict="DISCARD",
            reasons=[str(exc)],
            is_stats=empty,
            oos_stats=empty,
            config=StrategyConfig(name="invalid"),
        )

    try:
        results = []
        for pair, data in ctx.prepared.items():
            _ensure_break_spec(data, ctx.frames[pair], strategy)
            results.append(run_prepared_backtest(pair, data, strategy))
    except (KeyError, ValueError) as exc:
        empty = WindowStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0)
        return EvalResult(
            score=float("-inf"),
            verdict="DISCARD",
            reasons=[f"backtest error: {exc}"],
            is_stats=empty,
            oos_stats=empty,
            config=strategy,
        )

    is_stats = aggregate_window(results, ctx.cutoffs, oos=False)
    oos_stats = aggregate_window(results, ctx.cutoffs, oos=True)
    decision, reasons = verdict(is_stats, oos_stats)
    return EvalResult(
        score=score_window_stats(is_stats, oos_stats),
        verdict=decision,
        reasons=reasons,
        is_stats=is_stats,
        oos_stats=oos_stats,
        config=strategy,
    )


def _ensure_results() -> None:
    if not RESULTS.exists():
        RESULTS.write_text(HEADER)


def _log(score: float, is_stats: WindowStats, oos_stats: WindowStats, cfg: dict, status: str, desc: str) -> None:
    ts = time.strftime("%Y%m%dT%H%M%S")
    desc = desc.replace("\t", " ").replace("\n", " ")
    with RESULTS.open("a", encoding="utf-8") as handle:
        handle.write(
            f"{ts}\t{score:.4f}\t{is_stats.net_profit_factor:.3f}\t"
            f"{is_stats.total_net_pnl_pct:.3f}\t{is_stats.trades}\t"
            f"{oos_stats.net_profit_factor:.3f}\t{oos_stats.total_net_pnl_pct:.3f}\t"
            f"{oos_stats.trades}\t{status}\t{cfg['entry_mode']}\t{cfg['tag_filter']}\t{desc}\n"
        )


def _perturb(cfg: dict, rng: random.Random) -> tuple[dict, str]:
    new = dict(cfg)
    changed: list[str] = []
    n_changes = rng.choice([1, 1, 2])
    keys = list(PARAM_SPACE.keys())
    for _ in range(n_changes):
        key = rng.choice(keys)
        val = rng.choice(PARAM_SPACE[key])
        new[key] = val
        changed.append(f"{key}={val}")
    if new["entry_mode"] != "ob_retest":
        new["structure_timeframe"] = (
            new["structure_timeframe"] if new["entry_mode"] == "htf_swing_map" else "15m"
        )
    return new, ", ".join(changed)


def _load_context(pairs: list[str], days: int) -> SearchContext:
    full_frames: dict[str, pd.DataFrame] = {}
    cutoffs: dict[str, pd.Timestamp] = {}
    for pair in pairs:
        print(f"  loading {pair} ({days}d)...", flush=True)
        frames = fetch_pair(pair, days)
        if frames is None:
            continue
        frame = frames["15m"].sort_index()
        full_frames[pair] = frame
        cutoffs[pair] = pd.Timestamp(frame.index[int(len(frame) * IS_FRACTION)])

    if not full_frames:
        raise RuntimeError("No complete datasets were available.")

    conversion_closes = load_usd_conversion_closes(full_frames, days)
    atr_periods = set(PARAM_SPACE["atr_period"]) | {200}
    prepared = {
        pair: prepare_smc_data(
            pair,
            frame,
            atr_periods=atr_periods,
            break_specs=set(),
            usd_quote_close=conversion_closes[pair],
        )
        for pair, frame in full_frames.items()
    }
    return SearchContext(prepared=prepared, frames=full_frames, cutoffs=cutoffs)


def _preregistered_rows(ctx: SearchContext) -> list[EvalRow]:
    rows: list[EvalRow] = []
    for config in CONFIGS:
        for pair, frame in ctx.frames.items():
            _ensure_break_spec(ctx.prepared[pair], frame, config)
        rows.append(evaluate_config_on_pairs(config, ctx.frames, ctx.prepared, ctx.cutoffs))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iters", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--pairs", default=",".join(DEFAULT_PAIRS))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument(
        "--stop-on-keep",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    args = parser.parse_args()

    pairs = [pair.strip() for pair in args.pairs.split(",") if pair.strip()]
    rng = random.Random(args.seed)
    _ensure_results()

    print("Preparing cached market features (lazy break specs)...", flush=True)
    ctx = _load_context(pairs, args.days)
    print(f"Pairs ready: {len(ctx.prepared)}", flush=True)

    best_cfg = dict(CONFIG)
    base = evaluate_smc_config(best_cfg, ctx=ctx)
    best_score = base.score
    _log(base.score, base.is_stats, base.oos_stats, best_cfg, "baseline", "baseline CONFIG")
    print(
        f"baseline ({_config_name(best_cfg)}): score={base.score:.4f} verdict={base.verdict} "
        f"entry_mode={best_cfg['entry_mode']} tag_filter={best_cfg['tag_filter']} "
        f"is_pf={base.is_stats.net_profit_factor:.2f} is_pnl={base.is_stats.total_net_pnl_pct:.2f}% "
        f"is_n={base.is_stats.trades} "
        f"oos_pf={base.oos_stats.net_profit_factor:.2f} "
        f"oos_pnl={base.oos_stats.total_net_pnl_pct:.2f}% oos_n={base.oos_stats.trades}"
    )
    if base.reasons:
        print("  reasons: " + "; ".join(base.reasons))

    best_keep_score = base.score if base.verdict == "KEEP" else float("-inf")
    search_best_cfg = dict(best_cfg)
    search_best_score = best_score
    search_best_result = base

    for i in range(1, args.iters + 1):
        cand, desc = _perturb(best_cfg, rng)
        res = evaluate_smc_config(cand, ctx=ctx)
        improved = res.score > best_score
        status = "keep" if improved else "discard"
        _log(res.score, res.is_stats, res.oos_stats, cand, status, desc)
        flag = "+" if improved else " "
        print(
            f"[{i:04d}]{flag} score={res.score:7.4f} ({res.verdict:7s}) "
            f"mode={cand['entry_mode']:14s} tag={cand['tag_filter']:5s} "
            f"oos_pf={res.oos_stats.net_profit_factor:5.2f} "
            f"oos_pnl={res.oos_stats.total_net_pnl_pct:6.2f}% "
            f"oos_n={res.oos_stats.trades:3d} | {desc}"
        )
        if improved:
            best_cfg, best_score = cand, res.score
            if res.score > search_best_score:
                search_best_cfg, search_best_score, search_best_result = cand, res.score, res
            if res.verdict == "KEEP" and res.score > best_keep_score:
                best_keep_score = res.score
                payload = {
                    "config": best_cfg,
                    "strategy": asdict(res.config),
                    "verdict": res.verdict,
                    "reasons": res.reasons,
                    "is": asdict(res.is_stats),
                    "oos": asdict(res.oos_stats),
                }
                BEST_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                print(f"        ^ new KEEP-verdict best written to {BEST_JSON.name}")
                if args.stop_on_keep:
                    print("STOP: KEEP-verdict config found.")
                    break

    preregistered = _preregistered_rows(ctx)
    search_row = EvalRow(
        name=f"autosearch_{_config_name(search_best_cfg)}",
        score=search_best_result.score,
        verdict=search_best_result.verdict,
        is_stats=search_best_result.is_stats,
        oos_stats=search_best_result.oos_stats,
        rationale="; ".join(search_best_result.reasons) if search_best_result.reasons else "Best autosearch score.",
    )
    all_rows = preregistered + [search_row]
    compare_path = write_comparison_report(all_rows, COMPARE_REPORT)
    best_overall = best_eval_row(all_rows)

    print("\n=== SMC search done ===")
    print(
        f"Best explored config: {_config_name(search_best_cfg)} | score={search_best_score:.4f} "
        f"entry_mode={search_best_cfg['entry_mode']} tag_filter={search_best_cfg['tag_filter']}"
    )
    print(
        f"Most optimal overall: {best_overall.name} | score={best_overall.score:.4f} | "
        f"verdict={best_overall.verdict} | IS {best_overall.is_stats.trades} trades "
        f"net PF {best_overall.is_stats.net_profit_factor:.2f} | "
        f"OOS {best_overall.oos_stats.trades} trades net PF {best_overall.oos_stats.net_profit_factor:.2f}"
    )
    print(f"Comparison report: {compare_path}")
    if best_keep_score > float("-inf"):
        print(f"Best KEEP-verdict config saved to {BEST_JSON}")
    else:
        print(
            "No config passed KEEP gates. Locked gates: "
            f"IS/OOS trades >= {MIN_WINDOW_TRADES}, "
            f"OOS net PF >= {MIN_OOS_PROFIT_FACTOR:.2f}, positive IS/OOS net PnL."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())