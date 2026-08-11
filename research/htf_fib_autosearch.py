"""Automated HTF Fib pivot/RSI/EMA + tool-combo search loop.

Mirrors ``research/autosearch.py``: perturb 1-2 parameters from PARAM_SPACE,
evaluate on chronological IS/OOS with locked promotion gates, keep changes only
when the OOS-penalized score improves. Always ranks the preregistered COMBOS
first. Writes ``research/htf_fib_results.tsv``, a ranking report under
``results/``, and ``research/htf_fib_best_config.json`` only when a KEEP-verdict
config is found.

Usage:
    python -m research.htf_fib_autosearch --iters 20 --seed 0 \\
        --override-negative-result docs/research/FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.htf_fib_config import COMBOS, CONFIG, PARAM_SPACE
from scripts.run_donchian_backtest import fetch_pair
from scripts.run_htf_fib_backtest import (
    DEFAULT_PAIRS,
    IS_FRACTION,
    MIN_OOS_PROFIT_FACTOR,
    MIN_WINDOW_TRADES,
    PreparedBacktestData,
    StrategyConfig,
    WindowStats,
    aggregate_window,
    load_usd_conversion_closes,
    prepare_backtest_data,
    run_prepared_backtest,
    verdict,
)

ROOT = Path(__file__).resolve().parent.parent
_NEGATIVE_RESULT_REPORT = (
    ROOT / "docs" / "research" / "FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md"
)
RESULTS = Path(__file__).resolve().parent / "htf_fib_results.tsv"
BEST_JSON = Path(__file__).resolve().parent / "htf_fib_best_config.json"
RANK_REPORT = ROOT / "results" / "htf_fib_combo_ranking.md"
HEADER = (
    "ts\tscore\tis_pf\tis_pnl_pct\tis_trades\toos_pf\toos_pnl_pct\toos_trades\t"
    "status\tdescription\n"
)
OVERFIT_LAMBDA = 0.5
LOWN_PENALTY = 0.25


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
    prepared: dict[str, PreparedBacktestData]
    frames: dict[str, pd.DataFrame]
    cutoffs: dict[str, pd.Timestamp]


# Combo identity: forced from COMBOS so combo_id stays meaningful.
# Free PARAM_SPACE knobs (rsi, tp/sl, hold, pivots, atr, fib_timeframe) always win
# over combo defaults when present on the candidate.
# Only tool-stack identity is forced from combo_id; entry filters remain free.
COMBO_IDENTITY_KEYS = (
    "require_liquidity_sweep",
    "require_anchored_vwap",
)


def _config_name(cfg: dict) -> str:
    return (
        f"{cfg.get('combo_id', 'custom')}"
        f"_{cfg['fib_timeframe']}"
        f"_z{cfg.get('fib_zone', 'golden')}"
        f"_l{cfg['left_bars']}r{cfg['right_bars']}"
        f"_rsi{cfg['rsi_long']:g}-{cfg['rsi_short']:g}"
        f"_tp{cfg['tp_atr']:g}_sl{cfg['sl_atr']:g}"
        f"_hold{cfg['max_hold_bars']}"
        f"_inv{cfg.get('invalidate_mode', 'wick')}"
        f"_sw{int(bool(cfg.get('require_liquidity_sweep', False)))}"
        f"_vw{int(bool(cfg.get('require_anchored_vwap', False)))}"
    )


def resolve_config(cfg: dict) -> dict:
    """Merge CONFIG → combo defaults → candidate free params.

    Free search dimensions on ``cfg`` override combo defaults. Combo identity
    flags (sweep/AVWAP + hardened vs soft filter stack) are re-asserted from
    ``COMBOS[combo_id]`` so the stack label stays coherent.
    """

    resolved = dict(CONFIG)
    combo_id = str(cfg.get("combo_id", resolved.get("combo_id", "hardened_mtf")))
    combo = COMBOS.get(combo_id, {})
    resolved.update(combo)
    resolved.update(cfg)
    resolved["combo_id"] = combo_id
    for key in COMBO_IDENTITY_KEYS:
        if key in combo:
            resolved[key] = combo[key]
    return resolved


def dict_to_strategy(cfg: dict) -> StrategyConfig:
    """Map a search dict onto StrategyConfig via resolve_config."""

    payload = resolve_config(cfg)
    combo_id = str(payload["combo_id"])
    rsi_long = float(payload["rsi_long"])
    rsi_short = float(payload["rsi_short"])
    if rsi_short <= rsi_long:
        raise ValueError(f"rsi_short {rsi_short} must exceed rsi_long {rsi_long}")
    name = _config_name(payload)
    return StrategyConfig(
        name=name,
        fib_timeframe=payload["fib_timeframe"],  # type: ignore[arg-type]
        left_bars=int(payload["left_bars"]),
        right_bars=int(payload["right_bars"]),
        rsi_long=rsi_long,
        rsi_short=rsi_short,
        require_mtf_rsi=bool(payload.get("require_mtf_rsi", True)),
        require_ema_stack=bool(payload.get("require_ema_stack", True)),
        require_candle=bool(payload.get("require_candle", True)),
        invalidate_swing=bool(payload.get("invalidate_swing", True)),
        one_entry_per_swing=bool(payload.get("one_entry_per_swing", True)),
        atr_period=int(payload["atr_period"]),
        tp_atr=float(payload["tp_atr"]),
        sl_atr=float(payload["sl_atr"]),
        max_hold_bars=int(payload["max_hold_bars"]),
        require_liquidity_sweep=bool(payload.get("require_liquidity_sweep", False)),
        require_anchored_vwap=bool(payload.get("require_anchored_vwap", False)),
        fib_zone=str(payload.get("fib_zone", "golden")),  # type: ignore[arg-type]
        invalidate_mode=str(payload.get("invalidate_mode", "wick")),  # type: ignore[arg-type]
    )


def _score(is_stats: WindowStats, oos_stats: WindowStats) -> float:
    """OOS-first score with overfit and thin-sample penalties (locked style)."""

    trade_gap = max(0, MIN_WINDOW_TRADES - oos_stats.trades) * LOWN_PENALTY
    is_gap = max(0, MIN_WINDOW_TRADES - is_stats.trades) * LOWN_PENALTY
    overfit = OVERFIT_LAMBDA * abs(is_stats.total_net_pnl_pct - oos_stats.total_net_pnl_pct)
    return float(oos_stats.total_net_pnl_pct - overfit - trade_gap - is_gap)


def _ensure_pivot_spec(
    prepared: PreparedBacktestData,
    frame: pd.DataFrame,
    spec: tuple[Literal["4h", "1d"], int, int],
) -> None:
    """Lazily attach a pivot schedule if a search trial needs a new left/right pair."""

    if spec in prepared.events_by_spec:
        return
    from scripts.run_htf_fib_backtest import _resample_ohlc, confirmed_pivot_events

    events_by_time: dict[pd.Timestamp, list] = {}
    for event in confirmed_pivot_events(
        _resample_ohlc(frame[["open", "high", "low", "close"]], spec[0]),
        spec[1],
        spec[2],
    ):
        events_by_time.setdefault(event.confirmation_time, []).append(event)
    prepared.events_by_spec[spec] = events_by_time


def evaluate_htf_fib_config(cfg: dict, *, ctx: SearchContext) -> EvalResult:
    """Evaluate one config on all prepared pairs; judge with locked gates."""

    empty = WindowStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0)
    try:
        strategy = dict_to_strategy(cfg)
    except (KeyError, ValueError, TypeError) as exc:
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
            spec = (strategy.fib_timeframe, strategy.left_bars, strategy.right_bars)
            _ensure_pivot_spec(data, ctx.frames[pair], spec)
            if strategy.atr_period not in data.atr_by_period:
                from scripts.run_htf_fib_backtest import _wilder_atr

                frame = ctx.frames[pair]
                data.atr_by_period[strategy.atr_period] = (
                    _wilder_atr(frame, strategy.atr_period).astype(float).tolist()
                )
            results.append(run_prepared_backtest(pair, data, strategy))
    except (KeyError, ValueError) as exc:
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
        score=_score(is_stats, oos_stats),
        verdict=decision,
        reasons=reasons,
        is_stats=is_stats,
        oos_stats=oos_stats,
        config=strategy,
    )


def _ensure_results() -> None:
    if not RESULTS.exists():
        RESULTS.write_text(HEADER, encoding="utf-8")


def _log(
    score: float,
    is_stats: WindowStats,
    oos_stats: WindowStats,
    status: str,
    desc: str,
) -> None:
    ts = time.strftime("%Y%m%dT%H%M%S")
    desc = desc.replace("\t", " ").replace("\n", " ")
    with RESULTS.open("a", encoding="utf-8") as handle:
        handle.write(
            f"{ts}\t{score:.4f}\t{is_stats.net_profit_factor:.3f}\t"
            f"{is_stats.total_net_pnl_pct:.3f}\t{is_stats.trades}\t"
            f"{oos_stats.net_profit_factor:.3f}\t{oos_stats.total_net_pnl_pct:.3f}\t"
            f"{oos_stats.trades}\t{status}\t{desc}\n"
        )


def _valid_rsi_pair(rsi_long: float, rsi_short: float) -> bool:
    return rsi_short > rsi_long


def _perturb(cfg: dict, rng: random.Random) -> tuple[dict, str]:
    """Perturb 1–2 PARAM_SPACE keys. Free knobs are never re-clobbered by COMBOS.

    When ``combo_id`` is selected, combo defaults are loaded first; any other
    free-parameter change in the same step is applied on top and kept.
    """

    new = dict(cfg)
    changed: list[str] = []
    n_changes = rng.choice([1, 1, 2])
    selected = [rng.choice(list(PARAM_SPACE.keys())) for _ in range(n_changes)]

    if "combo_id" in selected:
        combo_id = str(rng.choice(PARAM_SPACE["combo_id"]))
        new["combo_id"] = combo_id
        if combo_id in COMBOS:
            new.update(COMBOS[combo_id])
            new["combo_id"] = combo_id
        changed.append(f"combo_id={combo_id}")
        selected = [key for key in selected if key != "combo_id"]

    for key in selected:
        val = rng.choice(PARAM_SPACE[key])
        new[key] = val
        changed.append(f"{key}={val}")

    # Do not re-apply COMBOS here — that would wipe free PARAM_SPACE samples.
    if not _valid_rsi_pair(float(new["rsi_long"]), float(new["rsi_short"])):
        new["rsi_long"] = 30.0
        new["rsi_short"] = 70.0
        changed.append("rsi_repaired=30/70")
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
    # Only default pivot/ATR specs up front; _ensure_pivot_spec + atr lazy path
    # attach PARAM_SPACE variants during search (avoids 32× pivot precompute).
    pivot_specs: set[tuple[Literal["4h", "1d"], int, int]] = {
        ("4h", 5, 5),
        ("1d", 5, 5),
    }
    atr_periods = {14}
    prepared = {
        pair: prepare_backtest_data(
            pair,
            frame,
            pivot_specs=pivot_specs,
            atr_periods=atr_periods,
            usd_quote_close=conversion_closes[pair],
        )
        for pair, frame in full_frames.items()
    }
    return SearchContext(prepared=prepared, frames=full_frames, cutoffs=cutoffs)


def _combo_seed(combo_id: str) -> dict:
    seed = dict(CONFIG)
    seed["combo_id"] = combo_id
    seed.update(COMBOS[combo_id])
    return seed


def _write_ranking_report(
    rows: list[tuple[str, EvalResult]],
    *,
    best_explored: EvalResult,
    best_explored_cfg: dict,
    keep_found: bool,
) -> Path:
    RANK_REPORT.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# HTF Fib tool-combo ranking",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        "",
        "Preregistered partial stacks from "
        "`docs/research/HTF_FIB_TOOL_COMBINATIONS_2026-07.md`.",
        "Order-flow and full volume-profile are out of scope.",
        "",
        "Costs: 2.0 pip spread, 2.0 pip slippage/fill, $3 commission/order.",
        f"Gates: IS/OOS trades ≥ {MIN_WINDOW_TRADES}, "
        f"OOS net PF ≥ {MIN_OOS_PROFIT_FACTOR:.2f}, positive IS/OOS net PnL.",
        "",
        "## Preregistered combos",
        "",
        "| Combo | Verdict | Score | IS n | IS net PF | IS PnL% | OOS n | OOS net PF | OOS PnL% | Reasons |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for label, res in rows:
        reason = "; ".join(res.reasons) if res.reasons else "—"
        lines.append(
            f"| `{label}` | {res.verdict} | {res.score:.4f} | "
            f"{res.is_stats.trades} | {res.is_stats.net_profit_factor:.2f} | "
            f"{res.is_stats.total_net_pnl_pct:.2f} | "
            f"{res.oos_stats.trades} | {res.oos_stats.net_profit_factor:.2f} | "
            f"{res.oos_stats.total_net_pnl_pct:.2f} | {reason} |"
        )

    ranked = sorted(
        rows,
        key=lambda item: (
            item[1].score,
            item[1].oos_stats.trades + item[1].is_stats.trades,
        ),
        reverse=True,
    )
    winner_label, winner = ranked[0]
    active = [row for row in ranked if row[1].is_stats.trades + row[1].oos_stats.trades > 0]
    active_label, active_res = (active[0] if active else (None, None))
    lines.extend(
        [
            "",
            "## Single best under fixed score (not necessarily KEEP)",
            "",
            f"- **Best preregistered combo by score:** `{winner_label}` "
            f"(score={winner.score:.4f}, verdict={winner.verdict})",
            f"- **Best explored config (search + combos):** `{best_explored.config.name}` "
            f"(score={best_explored.score:.4f}, verdict={best_explored.verdict})",
            f"- **Config JSON:** `{json.dumps(best_explored_cfg, sort_keys=True)}`",
            "",
            "### Best explored window stats",
            "",
            f"- IS: trades={best_explored.is_stats.trades}, "
            f"net_pf={best_explored.is_stats.net_profit_factor:.3f}, "
            f"pnl%={best_explored.is_stats.total_net_pnl_pct:.3f}",
            f"- OOS: trades={best_explored.oos_stats.trades}, "
            f"net_pf={best_explored.oos_stats.net_profit_factor:.3f}, "
            f"pnl%={best_explored.oos_stats.total_net_pnl_pct:.3f}",
            f"- Reasons: {'; '.join(best_explored.reasons) if best_explored.reasons else 'none'}",
            "",
        ]
    )
    if active_label is not None and active_res is not None:
        lines.extend(
            [
                "### Highest-score combo with any trades (informational)",
                "",
                f"- **Combo:** `{active_label}` score={active_res.score:.4f} "
                f"verdict={active_res.verdict}",
                f"- IS n={active_res.is_stats.trades} net_pf={active_res.is_stats.net_profit_factor:.3f} "
                f"pnl%={active_res.is_stats.total_net_pnl_pct:.3f}",
                f"- OOS n={active_res.oos_stats.trades} "
                f"net_pf={active_res.oos_stats.net_profit_factor:.3f} "
                f"pnl%={active_res.oos_stats.total_net_pnl_pct:.3f}",
                f"- Reasons: {'; '.join(active_res.reasons)}",
                "",
                "Note: zero-trade stacks can outscore losing stacks under the locked "
                "score (not trading beats paying costs on a negative edge).",
                "",
            ]
        )
    if keep_found:
        lines.append(
            f"**KEEP:** OOS-confirmed config written to `{BEST_JSON.relative_to(ROOT)}`."
        )
    else:
        lines.append(
            "**all-DISCARD:** No configuration cleared the locked promotion gates in this run."
        )
    RANK_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return RANK_REPORT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iters", type=int, default=50)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--pairs", default=",".join(DEFAULT_PAIRS))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument(
        "--stop-on-keep",
        action="store_true",
        default=False,
        help="Stop early when a KEEP-verdict config is found.",
    )
    parser.add_argument(
        "--override-negative-result",
        type=str,
        default=None,
        help="Path to the locked FX directional-TA negative-result report.",
    )
    args = parser.parse_args()

    if args.override_negative_result is None:
        if _NEGATIVE_RESULT_REPORT.exists():
            print(
                "STOP: FX directional TA has a locked negative result (2026-06). See "
                f"{_NEGATIVE_RESULT_REPORT}. Use --override-negative-result to run "
                "HTF Fib autosearch."
            )
            return 2
    else:
        override = Path(args.override_negative_result)
        if not override.exists():
            print(f"ERROR: override file does not exist: {override}")
            return 2

    pairs = [pair.strip() for pair in args.pairs.split(",") if pair.strip()]
    rng = random.Random(args.seed)
    _ensure_results()

    print("Preparing cached market features (lazy pivot specs)...", flush=True)
    ctx = _load_context(pairs, args.days)
    print(f"Pairs ready: {len(ctx.prepared)}", flush=True)

    combo_rows: list[tuple[str, EvalResult]] = []
    best_cfg = dict(CONFIG)
    best_cfg.update(COMBOS[str(best_cfg.get("combo_id", "hardened_mtf"))])
    best_result = evaluate_htf_fib_config(best_cfg, ctx=ctx)
    best_score = best_result.score
    search_best_cfg = dict(best_cfg)
    search_best_result = best_result
    keep_found = best_result.verdict == "KEEP"
    best_keep_score = best_score if keep_found else float("-inf")

    # Always evaluate every preregistered combo once (fixed list).
    for combo_id in COMBOS:
        cfg = _combo_seed(combo_id)
        res = evaluate_htf_fib_config(cfg, ctx=ctx)
        combo_rows.append((combo_id, res))
        status = "combo"
        _log(res.score, res.is_stats, res.oos_stats, status, f"preregistered {combo_id}")
        print(
            f"combo {combo_id}: score={res.score:.4f} verdict={res.verdict} "
            f"is_n={res.is_stats.trades} oos_n={res.oos_stats.trades} "
            f"oos_pf={res.oos_stats.net_profit_factor:.2f} "
            f"oos_pnl={res.oos_stats.total_net_pnl_pct:.2f}%"
        )
        if res.score > best_score:
            best_cfg, best_score, best_result = dict(cfg), res.score, res
            search_best_cfg, search_best_result = dict(cfg), res
        if res.verdict == "KEEP" and res.score > best_keep_score:
            keep_found = True
            best_keep_score = res.score
            BEST_JSON.write_text(
                json.dumps({"config": cfg, "strategy": asdict(res.config)}, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"        ^ new KEEP-verdict best written to {BEST_JSON}")

    _log(
        best_result.score,
        best_result.is_stats,
        best_result.oos_stats,
        "baseline",
        "baseline after combo rank",
    )
    print(
        f"baseline: score={best_result.score:.4f} verdict={best_result.verdict} "
        f"is_pf={best_result.is_stats.net_profit_factor:.2f} "
        f"is_pnl={best_result.is_stats.total_net_pnl_pct:.2f}% "
        f"is_n={best_result.is_stats.trades} "
        f"oos_pf={best_result.oos_stats.net_profit_factor:.2f} "
        f"oos_pnl={best_result.oos_stats.total_net_pnl_pct:.2f}% "
        f"oos_n={best_result.oos_stats.trades}"
    )

    for i in range(1, args.iters + 1):
        cand, desc = _perturb(best_cfg, rng)
        res = evaluate_htf_fib_config(cand, ctx=ctx)
        improved = res.score > best_score
        status = "keep" if improved else "discard"
        _log(res.score, res.is_stats, res.oos_stats, status, desc)
        flag = "+" if improved else " "
        print(
            f"[{i:04d}]{flag} score={res.score:7.4f} ({res.verdict:7s}) "
            f"combo={cand.get('combo_id')} "
            f"oos_pf={res.oos_stats.net_profit_factor:5.2f} "
            f"oos_pnl={res.oos_stats.total_net_pnl_pct:6.2f}% "
            f"oos_n={res.oos_stats.trades:3d} | {desc}"
        )
        if improved:
            best_cfg, best_score = cand, res.score
            if res.score > search_best_result.score:
                search_best_cfg, search_best_result = dict(cand), res
            if res.verdict == "KEEP" and res.score > best_keep_score:
                keep_found = True
                best_keep_score = res.score
                BEST_JSON.write_text(
                    json.dumps(
                        {"config": cand, "strategy": asdict(res.config)},
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                print(f"        ^ new KEEP-verdict best written to {BEST_JSON}")
                if args.stop_on_keep:
                    print("STOP: KEEP-verdict config found.")
                    break

    report_path = _write_ranking_report(
        combo_rows,
        best_explored=search_best_result,
        best_explored_cfg=search_best_cfg,
        keep_found=keep_found,
    )

    print("\n=== HTF Fib search done ===")
    print(
        f"Best score config: {search_best_result.config.name} "
        f"(not necessarily KEEP) score={search_best_result.score:.4f}"
    )
    if keep_found:
        print(f"Best OOS-confirmed config (verdict KEEP) saved to {BEST_JSON}")
    else:
        print(
            "No config passed the out-of-sample gates in this run. Best explored score was "
            f"{search_best_result.score:.4f}. Locked gates: IS/OOS trades >= "
            f"{MIN_WINDOW_TRADES}, OOS net PF >= {MIN_OOS_PROFIT_FACTOR:.2f}, "
            "positive IS/OOS net PnL."
        )
    print(f"Ranking report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
