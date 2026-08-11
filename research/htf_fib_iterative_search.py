"""Three-round iterative HTF Fib refinement under locked promotion gates.

Each round uses a distinct PARAM_SPACE / seed (see REFINEMENT_ROUNDS in
``research.htf_fib_config``). Free PARAM_SPACE samples are applied via
``resolve_config`` / ``dict_to_strategy`` (no silent combo clobber).

Usage:
    python -m research.htf_fib_iterative_search \\
        --override-negative-result docs/research/FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.htf_fib_autosearch import (
    BEST_JSON,
    RESULTS,
    _ensure_results,
    _load_context,
    _log,
    _score,
    _valid_rsi_pair,
    dict_to_strategy,
    evaluate_htf_fib_config,
    resolve_config,
)
from research.htf_fib_config import CONFIG, REFINEMENT_ROUNDS
from scripts.run_htf_fib_backtest import (
    DEFAULT_PAIRS,
    MIN_OOS_PROFIT_FACTOR,
    MIN_WINDOW_TRADES,
)

ROOT = Path(__file__).resolve().parent.parent
_NEGATIVE = ROOT / "docs" / "research" / "FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md"
FINAL_REPORT = ROOT / "docs" / "research" / "HTF_FIB_ITERATIVE_REFINEMENT_RESULT_2026-07.md"
ROUND_DIR = ROOT / "results" / "htf_fib_refinement"


def _perturb_space(cfg: dict, space: dict[str, list], rng: random.Random) -> tuple[dict, str]:
    new = dict(cfg)
    changed: list[str] = []
    n_changes = rng.choice([1, 1, 2])
    keys = list(space.keys())
    selected = [rng.choice(keys) for _ in range(n_changes)]

    if "combo_id" in selected and "combo_id" in space:
        from research.htf_fib_config import COMBOS

        combo_id = str(rng.choice(space["combo_id"]))
        new["combo_id"] = combo_id
        if combo_id in COMBOS:
            new.update(COMBOS[combo_id])
            new["combo_id"] = combo_id
        changed.append(f"combo_id={combo_id}")
        selected = [k for k in selected if k != "combo_id"]

    for key in selected:
        if key not in space:
            continue
        val = rng.choice(space[key])
        new[key] = val
        changed.append(f"{key}={val}")

    if not _valid_rsi_pair(float(new["rsi_long"]), float(new["rsi_short"])):
        new["rsi_long"] = 40.0
        new["rsi_short"] = 60.0
        changed.append("rsi_repaired=40/60")
    return new, ", ".join(changed)


def _write_round_report(
    path: Path,
    *,
    round_spec: dict,
    seed_result,
    best_cfg: dict,
    best_result,
    trials: list[dict],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# HTF Fib refinement round `{round_spec['id']}`",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        "",
        f"**Change:** {round_spec['description']}",
        "",
        f"Iters: {round_spec['iters']} | PARAM_SPACE keys: "
        f"{', '.join(sorted(round_spec['param_space'].keys()))}",
        "",
        "## Seed",
        "",
        f"- config: `{json.dumps(round_spec['seed_config'], sort_keys=True)}`",
        f"- score={seed_result.score:.4f} verdict={seed_result.verdict}",
        f"- IS n={seed_result.is_stats.trades} pf={seed_result.is_stats.net_profit_factor:.3f} "
        f"pnl%={seed_result.is_stats.total_net_pnl_pct:.3f}",
        f"- OOS n={seed_result.oos_stats.trades} pf={seed_result.oos_stats.net_profit_factor:.3f} "
        f"pnl%={seed_result.oos_stats.total_net_pnl_pct:.3f}",
        "",
        "## Best in round",
        "",
        f"- name: `{best_result.config.name}`",
        f"- score={best_result.score:.4f} verdict={best_result.verdict}",
        f"- IS n={best_result.is_stats.trades} pf={best_result.is_stats.net_profit_factor:.3f} "
        f"pnl%={best_result.is_stats.total_net_pnl_pct:.3f}",
        f"- OOS n={best_result.oos_stats.trades} pf={best_result.oos_stats.net_profit_factor:.3f} "
        f"pnl%={best_result.oos_stats.total_net_pnl_pct:.3f}",
        f"- reasons: {'; '.join(best_result.reasons) if best_result.reasons else 'none'}",
        f"- config: `{json.dumps(best_cfg, sort_keys=True)}`",
        "",
        f"Trials logged: {len(trials)}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def run_round(round_spec: dict, *, ctx, rng: random.Random) -> dict:
    """Run one refinement round; return summary dict."""

    space = round_spec["param_space"]
    # Do not pre-merge CONFIG into the seed: CONFIG's hardened flags would
    # override soft combo defaults after resolve_config (candidate wins).
    seed = resolve_config(dict(round_spec["seed_config"]))

    print(f"\n=== Round {round_spec['id']} ===", flush=True)
    print(f"  {round_spec['description']}", flush=True)

    seed_res = evaluate_htf_fib_config(seed, ctx=ctx)
    _log(
        seed_res.score,
        seed_res.is_stats,
        seed_res.oos_stats,
        f"round_{round_spec['id']}_seed",
        round_spec["description"][:120],
    )
    print(
        f"  seed: score={seed_res.score:.4f} {seed_res.verdict} "
        f"is_n={seed_res.is_stats.trades} oos_n={seed_res.oos_stats.trades} "
        f"oos_pf={seed_res.oos_stats.net_profit_factor:.2f} "
        f"oos_pnl={seed_res.oos_stats.total_net_pnl_pct:.2f}%",
        flush=True,
    )

    best_cfg = dict(seed)
    best_res = seed_res
    best_score = seed_res.score
    keep_cfg = None
    keep_res = None
    trials: list[dict] = []

    for i in range(1, int(round_spec["iters"]) + 1):
        cand, desc = _perturb_space(best_cfg, space, rng)
        res = evaluate_htf_fib_config(cand, ctx=ctx)
        improved = res.score > best_score
        status = "keep" if improved else "discard"
        _log(res.score, res.is_stats, res.oos_stats, f"round_{round_spec['id']}", desc)
        trials.append(
            {
                "i": i,
                "score": res.score,
                "verdict": res.verdict,
                "is_n": res.is_stats.trades,
                "oos_n": res.oos_stats.trades,
                "desc": desc,
            }
        )
        flag = "+" if improved else " "
        print(
            f"  [{i:03d}]{flag} score={res.score:8.4f} ({res.verdict:7s}) "
            f"oos_n={res.oos_stats.trades:3d} oos_pf={res.oos_stats.net_profit_factor:5.2f} "
            f"oos_pnl={res.oos_stats.total_net_pnl_pct:7.2f}% | {desc}",
            flush=True,
        )
        if improved:
            best_cfg, best_score, best_res = dict(cand), res.score, res
        if res.verdict == "KEEP" and (
            keep_res is None or res.score > keep_res.score
        ):
            keep_cfg, keep_res = dict(cand), res
            BEST_JSON.write_text(
                json.dumps(
                    {
                        "round": round_spec["id"],
                        "config": cand,
                        "strategy": asdict(res.config),
                        "is": asdict(res.is_stats),
                        "oos": asdict(res.oos_stats),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            print(f"        ^ KEEP written to {BEST_JSON}", flush=True)

    report_path = ROUND_DIR / f"{round_spec['id']}.md"
    _write_round_report(
        report_path,
        round_spec=round_spec,
        seed_result=seed_res,
        best_cfg=best_cfg,
        best_result=best_res,
        trials=trials,
    )
    print(f"  round report: {report_path}", flush=True)

    return {
        "id": round_spec["id"],
        "description": round_spec["description"],
        "report": str(report_path.relative_to(ROOT)),
        "seed_score": seed_res.score,
        "seed_verdict": seed_res.verdict,
        "seed_is_n": seed_res.is_stats.trades,
        "seed_oos_n": seed_res.oos_stats.trades,
        "best_score": best_res.score,
        "best_verdict": best_res.verdict,
        "best_config": best_cfg,
        "best_strategy": asdict(best_res.config),
        "best_is": asdict(best_res.is_stats),
        "best_oos": asdict(best_res.oos_stats),
        "best_reasons": best_res.reasons,
        "keep_found": keep_res is not None,
        "keep_config": keep_cfg,
    }


def _write_final(summaries: list[dict], *, overall_best: dict) -> Path:
    keep_any = any(s["keep_found"] for s in summaries)
    lines = [
        "# HTF Fib iterative refinement result (2026-07)",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        "",
        "Locked gates unchanged: IS/OOS trades ≥ "
        f"{MIN_WINDOW_TRADES}, OOS net PF ≥ {MIN_OOS_PROFIT_FACTOR:.2f}, "
        "positive IS/OOS net PnL. Costs: 2 pip spread, 2 pip slip/fill, $3/order.",
        "",
        "Negative-result override required; FX majors OHLC directional TA remains closed "
        "unless KEEP under these gates.",
        "",
        "## Refinement rounds (≥3 distinct searchable spaces)",
        "",
    ]
    for s in summaries:
        lines.extend(
            [
                f"### {s['id']}",
                "",
                f"- **Change:** {s['description']}",
                f"- Artifact: `{s['report']}`",
                f"- Seed: score={s['seed_score']:.4f} {s['seed_verdict']} "
                f"IS n={s['seed_is_n']} OOS n={s['seed_oos_n']}",
                f"- Best: score={s['best_score']:.4f} {s['best_verdict']} "
                f"IS n={s['best_is']['trades']} OOS n={s['best_oos']['trades']} "
                f"OOS net PF={s['best_oos']['net_profit_factor']:.3f} "
                f"OOS pnl%={s['best_oos']['total_net_pnl_pct']:.3f}",
                f"- Reasons: {'; '.join(s['best_reasons']) if s['best_reasons'] else 'none'}",
                f"- KEEP in round: {s['keep_found']}",
                "",
            ]
        )

    lines.extend(
        [
            "## Single best under fixed score (across all rounds)",
            "",
            f"- **Round:** `{overall_best['id']}`",
            f"- **Verdict:** {overall_best['best_verdict']}",
            f"- **Score:** {overall_best['best_score']:.4f}",
            f"- **Strategy:** `{overall_best['best_strategy']['name']}`",
            f"- **IS:** trades={overall_best['best_is']['trades']}, "
            f"net_pf={overall_best['best_is']['net_profit_factor']:.3f}, "
            f"pnl%={overall_best['best_is']['total_net_pnl_pct']:.3f}",
            f"- **OOS:** trades={overall_best['best_oos']['trades']}, "
            f"net_pf={overall_best['best_oos']['net_profit_factor']:.3f}, "
            f"pnl%={overall_best['best_oos']['total_net_pnl_pct']:.3f}",
            f"- **Config:** `{json.dumps(overall_best['best_config'], sort_keys=True)}`",
            f"- **Reasons:** {'; '.join(overall_best['best_reasons']) if overall_best['best_reasons'] else 'none'}",
            "",
        ]
    )
    if keep_any:
        lines.append(
            f"**KEEP:** At least one config cleared locked gates; see `{BEST_JSON.relative_to(ROOT)}`."
        )
    else:
        lines.extend(
            [
                "**all-DISCARD / profit not found:** No configuration cleared the locked "
                "promotion gates across three distinct refinement rounds. Best-by-score "
                "candidate is named above (not a promotion claim).",
                "",
                "This is consistent with the locked FX directional-TA and HTF Fib negative "
                "results: expanding Fib zones / exits / invalidation did not produce an "
                "OOS-validated net edge under realistic costs.",
            ]
        )
    FINAL_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return FINAL_REPORT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--pairs", default=",".join(DEFAULT_PAIRS))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument(
        "--override-negative-result",
        type=str,
        default=None,
        help="Path to locked FX directional-TA negative-result report.",
    )
    parser.add_argument(
        "--iters-scale",
        type=float,
        default=1.0,
        help="Multiply each round's iter budget (e.g. 0.5 for a short gating run).",
    )
    args = parser.parse_args()

    if args.override_negative_result is None:
        if _NEGATIVE.exists():
            print(
                "STOP: FX directional TA locked negative result. "
                f"Use --override-negative-result {_NEGATIVE}"
            )
            return 2
    else:
        override = Path(args.override_negative_result)
        if not override.exists():
            print(f"ERROR: override missing: {override}")
            return 2

    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    rng = random.Random(args.seed)
    _ensure_results()
    ROUND_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading market features for iterative HTF Fib search...", flush=True)
    ctx = _load_context(pairs, args.days)
    print(f"Pairs ready: {len(ctx.prepared)}", flush=True)

    # Prove free-param path once before rounds.
    probe = resolve_config(
        {
            "combo_id": "soft_baseline",
            "rsi_long": 42.0,
            "rsi_short": 58.0,
            "tp_atr": 3.5,
            "fib_zone": "wide",
            "fib_timeframe": "4h",
            "left_bars": 5,
            "right_bars": 2,
            "atr_period": 14,
            "sl_atr": 2.0,
            "max_hold_bars": 64,
        }
    )
    strat = dict_to_strategy(probe)
    assert strat.rsi_long == 42.0 and strat.tp_atr == 3.5 and strat.fib_zone == "wide"
    print(
        f"param_resolve_ok: rsi_long={strat.rsi_long} tp_atr={strat.tp_atr} "
        f"fib_zone={strat.fib_zone} name={strat.name}",
        flush=True,
    )

    summaries: list[dict] = []
    for round_spec in REFINEMENT_ROUNDS:
        spec = dict(round_spec)
        spec["iters"] = max(4, int(round(round_spec["iters"] * args.iters_scale)))
        summaries.append(run_round(spec, ctx=ctx, rng=rng))

    overall = max(summaries, key=lambda s: (s["best_score"], s["best_oos"]["trades"]))
    final_path = _write_final(summaries, overall_best=overall)

    print("\n=== Iterative HTF Fib search done ===", flush=True)
    print(f"Final report: {final_path}", flush=True)
    print(
        f"Best overall: {overall['id']} score={overall['best_score']:.4f} "
        f"{overall['best_verdict']} oos_n={overall['best_oos']['trades']}",
        flush=True,
    )
    if any(s["keep_found"] for s in summaries):
        print(f"KEEP artifact: {BEST_JSON}")
    else:
        print(
            "all-DISCARD: profit not found under locked gates "
            f"(MIN_TRADES={MIN_WINDOW_TRADES}, MIN_OOS_PF={MIN_OOS_PROFIT_FACTOR})."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
