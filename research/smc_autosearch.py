"""Automated LuxAlgo SMC parameter search with an untouched final holdout.

Mirrors ``research/htf_fib_autosearch.py``: perturb 1-2 parameters from PARAM_SPACE,
evaluate on chronological IS/validation windows, and keep changes when the
validation score improves. The selected candidate is evaluated once on the
untouched holdout. Writes a complete run manifest and comparison report.

Usage:
    python -m research.smc_autosearch --iters 100 --seed 0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.smc_config import CONFIG, PARAM_SPACE
from scripts.run_donchian_backtest import fetch_pair
from scripts.run_htf_fib_backtest import (
    DEFAULT_PAIRS,
    MIN_OOS_PROFIT_FACTOR,
    MIN_WINDOW_TRADES,
    BacktestResult,
    WindowStats,
    aggregate_window,
    load_usd_conversion_closes,
    verdict,
)
from scripts.run_smc_backtest import (
    OPTIMAL_COMPARISON_NAME,
    EvalRow,
    PreparedSmcData,
    StrategyConfig,
    _ensure_break_schedule,
    best_eval_row,
    config_from_dict,
    prepare_smc_data,
    run_prepared_backtest,
    score_window_stats,
    write_comparison_report,
)

ROOT = Path(__file__).resolve().parent.parent
RESULTS = Path(__file__).resolve().parent / "smc_results.tsv"
BEST_JSON = Path(__file__).resolve().parent / "smc_best_config.json"
COMPARE_REPORT = ROOT / "results" / OPTIMAL_COMPARISON_NAME
MANIFEST = ROOT / "results" / "smc_search_manifest.json"
IS_FRACTION = 0.50
VALIDATION_FRACTION = 0.25
HEADER = (
    "run_id\titeration\tscore\tis_pf\tis_pnl_pct\tis_trades\tvalidation_pf\t"
    "validation_pnl_pct\tvalidation_trades\tstatus\tconfig_json\tdescription\n"
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
    validation_starts: dict[str, pd.Timestamp]
    holdout_starts: dict[str, pd.Timestamp]
    data_manifest: dict[str, dict[str, str | int]]


def _config_name(cfg: dict) -> str:
    return (
        f"{cfg['entry_mode']}_{cfg['tag_filter']}"
        f"_sw{cfg['swing_length']}_{cfg['structure_timeframe']}"
        f"_ob{cfg['ob_retest_bars']}"
        f"_atr{cfg['atr_period']}"
        f"_tp{cfg['tp_atr']:g}_sl{cfg['sl_atr']:g}"
        f"_hold{cfg['max_hold_bars']}"
    )


def dict_to_strategy(cfg: dict) -> StrategyConfig:
    payload = dict(cfg)
    payload["name"] = _config_name(cfg)
    return config_from_dict(payload)


def _ensure_break_spec(
    prepared: PreparedSmcData, frame: pd.DataFrame, strategy: StrategyConfig
) -> None:
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

    is_results = _filter_results(results, end_by_pair=ctx.validation_starts)
    validation_results = _filter_results(
        results,
        start_by_pair=ctx.validation_starts,
        end_by_pair=ctx.holdout_starts,
    )
    is_stats = aggregate_window(is_results, ctx.validation_starts, oos=False)
    validation_stats = aggregate_window(validation_results, ctx.holdout_starts, oos=False)
    decision, reasons = verdict(is_stats, validation_stats)
    return EvalResult(
        score=score_window_stats(is_stats, validation_stats),
        verdict=decision,
        reasons=reasons,
        is_stats=is_stats,
        oos_stats=validation_stats,
        config=strategy,
    )


def _filter_results(
    results: list[BacktestResult],
    *,
    start_by_pair: dict[str, pd.Timestamp] | None = None,
    end_by_pair: dict[str, pd.Timestamp] | None = None,
) -> list[BacktestResult]:
    filtered: list[BacktestResult] = []
    for result in results:
        start = start_by_pair[result.pair] if start_by_pair else None
        end = end_by_pair[result.pair] if end_by_pair else None
        trades = [
            trade
            for trade in result.trades
            if (start is None or trade.entry_time > start)
            and (end is None or trade.entry_time <= end)
        ]
        filtered.append(
            BacktestResult(
                pair=result.pair,
                config=result.config,
                account_name=result.account_name,
                initial_capital_usd=result.initial_capital_usd,
                ending_balance_usd=result.ending_balance_usd,
                trades=trades,
            )
        )
    return filtered


def evaluate_holdout(
    cfg: dict,
    *,
    ctx: SearchContext,
) -> tuple[WindowStats, WindowStats, str, list[str]]:
    """Evaluate one already-selected configuration on the untouched holdout."""

    strategy = dict_to_strategy(cfg)
    results = []
    for pair, data in ctx.prepared.items():
        _ensure_break_spec(data, ctx.frames[pair], strategy)
        results.append(run_prepared_backtest(pair, data, strategy))
    pre_holdout = _filter_results(results, end_by_pair=ctx.holdout_starts)
    holdout = _filter_results(results, start_by_pair=ctx.holdout_starts)
    pre_stats = aggregate_window(pre_holdout, ctx.holdout_starts, oos=False)
    holdout_stats = aggregate_window(holdout, ctx.holdout_starts, oos=True)
    decision, reasons = verdict(pre_stats, holdout_stats)
    return pre_stats, holdout_stats, decision, reasons


def _ensure_results() -> None:
    if not RESULTS.exists():
        RESULTS.write_text(HEADER)


def _log(
    run_id: str,
    iteration: int,
    score: float,
    is_stats: WindowStats,
    validation_stats: WindowStats,
    cfg: dict,
    status: str,
    desc: str,
) -> None:
    desc = desc.replace("\t", " ").replace("\n", " ")
    with RESULTS.open("a", encoding="utf-8") as handle:
        handle.write(
            f"{run_id}\t{iteration}\t{score:.4f}\t{is_stats.net_profit_factor:.3f}\t"
            f"{is_stats.total_net_pnl_pct:.3f}\t{is_stats.trades}\t"
            f"{validation_stats.net_profit_factor:.3f}\t"
            f"{validation_stats.total_net_pnl_pct:.3f}\t{validation_stats.trades}\t"
            f"{status}\t{json.dumps(cfg, sort_keys=True)}\t{desc}\n"
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
    validation_starts: dict[str, pd.Timestamp] = {}
    holdout_starts: dict[str, pd.Timestamp] = {}
    data_manifest: dict[str, dict[str, str | int]] = {}
    for pair in pairs:
        print(f"  loading {pair} ({days}d)...", flush=True)
        frames = fetch_pair(pair, days)
        if frames is None:
            continue
        frame = frames["15m"].sort_index()
        full_frames[pair] = frame
        validation_starts[pair] = pd.Timestamp(frame.index[int(len(frame) * IS_FRACTION)])
        holdout_index = int(len(frame) * (IS_FRACTION + VALIDATION_FRACTION))
        holdout_starts[pair] = pd.Timestamp(frame.index[holdout_index])
        digest = hashlib.sha256(frame.to_csv(index=True, float_format="%.10g").encode()).hexdigest()
        data_manifest[pair] = {
            "rows": len(frame),
            "start": str(frame.index[0]),
            "end": str(frame.index[-1]),
            "sha256": digest,
        }

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
    return SearchContext(
        prepared=prepared,
        frames=full_frames,
        validation_starts=validation_starts,
        holdout_starts=holdout_starts,
        data_manifest=data_manifest,
    )


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
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    _ensure_results()

    print("Preparing cached market features (lazy break specs)...", flush=True)
    ctx = _load_context(pairs, args.days)
    print(f"Pairs ready: {len(ctx.prepared)}", flush=True)

    best_cfg = dict(CONFIG)
    base = evaluate_smc_config(best_cfg, ctx=ctx)
    best_score = base.score
    _log(
        run_id,
        0,
        base.score,
        base.is_stats,
        base.oos_stats,
        best_cfg,
        "baseline",
        "baseline CONFIG",
    )
    print(
        f"baseline ({_config_name(best_cfg)}): score={base.score:.4f} verdict={base.verdict} "
        f"entry_mode={best_cfg['entry_mode']} tag_filter={best_cfg['tag_filter']} "
        f"is_pf={base.is_stats.net_profit_factor:.2f} is_pnl={base.is_stats.total_net_pnl_pct:.2f}% "
        f"is_n={base.is_stats.trades} "
        f"validation_pf={base.oos_stats.net_profit_factor:.2f} "
        f"validation_pnl={base.oos_stats.total_net_pnl_pct:.2f}% "
        f"validation_n={base.oos_stats.trades}"
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
        _log(run_id, i, res.score, res.is_stats, res.oos_stats, cand, status, desc)
        flag = "+" if improved else " "
        print(
            f"[{i:04d}]{flag} score={res.score:7.4f} ({res.verdict:7s}) "
            f"mode={cand['entry_mode']:14s} tag={cand['tag_filter']:5s} "
            f"validation_pf={res.oos_stats.net_profit_factor:5.2f} "
            f"validation_pnl={res.oos_stats.total_net_pnl_pct:6.2f}% "
            f"validation_n={res.oos_stats.trades:3d} | {desc}"
        )
        if improved:
            best_cfg, best_score = cand, res.score
            if res.score > search_best_score:
                search_best_cfg, search_best_score, search_best_result = cand, res.score, res
            if res.verdict == "KEEP" and res.score > best_keep_score:
                best_keep_score = res.score
                print("        ^ candidate passed IS/validation gates; holdout remains untouched")
                if args.stop_on_keep:
                    print("STOP: IS/validation candidate found; evaluating final holdout.")
                    break

    pre_holdout, holdout, final_verdict, final_reasons = evaluate_holdout(
        search_best_cfg,
        ctx=ctx,
    )
    search_row = EvalRow(
        name=f"autosearch_{_config_name(search_best_cfg)}",
        score=search_best_result.score,
        verdict=final_verdict,
        is_stats=pre_holdout,
        oos_stats=holdout,
        rationale="; ".join(final_reasons) if final_reasons else "Final holdout gates passed.",
    )
    all_rows = [search_row]
    compare_path = write_comparison_report(all_rows, COMPARE_REPORT)
    best_overall = best_eval_row(all_rows)
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        "seed": args.seed,
        "iterations": args.iters,
        "requested_days": args.days,
        "requested_pairs": pairs,
        "split": {
            "is_fraction": IS_FRACTION,
            "validation_fraction": VALIDATION_FRACTION,
            "holdout_fraction": 1.0 - IS_FRACTION - VALIDATION_FRACTION,
        },
        "costs": {
            "spread_pips": 2.0,
            "slippage_pips_per_fill": 2.0,
            "commission_per_order": 3.0,
        },
        "data": ctx.data_manifest,
        "selected_config": search_best_cfg,
        "selected_strategy": asdict(search_best_result.config),
        "selection_score": search_best_score,
        "selection_is": asdict(search_best_result.is_stats),
        "selection_validation": asdict(search_best_result.oos_stats),
        "final_holdout": asdict(holdout),
        "final_verdict": final_verdict,
        "final_reasons": final_reasons,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if final_verdict == "KEEP":
        payload = {
            "config": search_best_cfg,
            "strategy": asdict(search_best_result.config),
            "verdict": final_verdict,
            "reasons": final_reasons,
            "selection_is": asdict(search_best_result.is_stats),
            "selection_validation": asdict(search_best_result.oos_stats),
            "final_holdout": asdict(holdout),
            "run_id": run_id,
            "manifest": str(MANIFEST.relative_to(ROOT)),
        }
        BEST_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print("\n=== SMC search done ===")
    print(
        f"Best explored config: {_config_name(search_best_cfg)} | score={search_best_score:.4f} "
        f"entry_mode={search_best_cfg['entry_mode']} tag_filter={search_best_cfg['tag_filter']}"
    )
    print(
        f"Final selected candidate: {best_overall.name} | selection_score={best_overall.score:.4f} | "
        f"verdict={best_overall.verdict} | IS {best_overall.is_stats.trades} trades "
        f"net PF {best_overall.is_stats.net_profit_factor:.2f} | "
        f"holdout {best_overall.oos_stats.trades} trades "
        f"net PF {best_overall.oos_stats.net_profit_factor:.2f}"
    )
    print(f"Comparison report: {compare_path}")
    print(f"Run manifest: {MANIFEST}")
    if final_verdict == "KEEP":
        print(f"Final holdout KEEP config saved to {BEST_JSON}")
    else:
        print(
            "Selected candidate failed final holdout gates. Locked gates: "
            f"pre-holdout/holdout trades >= {MIN_WINDOW_TRADES}, "
            f"holdout net PF >= {MIN_OOS_PROFIT_FACTOR:.2f}, "
            "positive pre-holdout/holdout net PnL."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
