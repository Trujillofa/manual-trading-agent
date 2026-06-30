#!/usr/bin/env python3
"""Bounded IS-only optimizer for the confirmed-HTF Fib strategy.

This script is intentionally guarded because the repository has a locked
negative result for FX directional OHLC technical analysis. It performs one
declared sensitivity study:

1. Rank entry configurations on the first 65% of each pair only.
2. Tune exits for the best IS entry candidates.
3. Test swing invalidation/one-entry hardening on IS.
4. Evaluate exactly one selected configuration on the untouched 35% OOS tail.

No configuration is selected or retried using OOS results.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_donchian_backtest import fetch_pair
from scripts.run_htf_fib_backtest import (
    DEFAULT_PAIRS,
    IS_FRACTION,
    MIN_WINDOW_TRADES,
    BacktestResult,
    PreparedBacktestData,
    StrategyConfig,
    WindowStats,
    aggregate_window,
    load_usd_conversion_closes,
    prepare_backtest_data,
    run_prepared_backtest,
    verdict,
)

ROOT = Path(__file__).resolve().parents[1]
NEGATIVE_RESULT_REPORT = ROOT / "docs" / "research" / "FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md"


def entry_configurations() -> list[StrategyConfig]:
    """Return the complete preregistered entry grid."""

    configs: list[StrategyConfig] = []
    for left_bars, right_bars in ((2, 2), (3, 3), (5, 2), (5, 5)):
        for rsi_long, rsi_short in ((30.0, 70.0), (35.0, 65.0), (40.0, 60.0), (45.0, 55.0)):
            for require_mtf_rsi in (False, True):
                for require_ema_stack in (False, True):
                    for require_candle in (False, True):
                        name = (
                            f"entry_l{left_bars}r{right_bars}"
                            f"_rsi{rsi_long:g}-{rsi_short:g}"
                            f"_mtf{int(require_mtf_rsi)}"
                            f"_stack{int(require_ema_stack)}"
                            f"_candle{int(require_candle)}"
                        )
                        configs.append(
                            StrategyConfig(
                                name=name,
                                left_bars=left_bars,
                                right_bars=right_bars,
                                rsi_long=rsi_long,
                                rsi_short=rsi_short,
                                require_mtf_rsi=require_mtf_rsi,
                                require_ema_stack=require_ema_stack,
                                require_candle=require_candle,
                            )
                        )
    return configs


def optimization_score(stats: WindowStats) -> float:
    """Rank IS results while penalizing thin samples and pair concentration."""

    trade_penalty = max(0, MIN_WINDOW_TRADES - stats.trades) * 0.50
    concentration_penalty = max(0.0, stats.tested_pairs / 2 - stats.profitable_pairs)
    return float(
        stats.total_net_pnl_pct - stats.max_drawdown_pct - trade_penalty - concentration_penalty
    )


def _is_stats(
    results: list[BacktestResult], prepared: dict[str, PreparedBacktestData]
) -> WindowStats:
    cutoffs = {pair: data.timestamps[-1] for pair, data in prepared.items()}
    return aggregate_window(results, cutoffs, oos=False)


def evaluate_is(
    config: StrategyConfig,
    prepared: dict[str, PreparedBacktestData],
) -> tuple[float, WindowStats]:
    """Evaluate one config exclusively on prepared in-sample data."""

    results = [run_prepared_backtest(pair, data, config) for pair, data in prepared.items()]
    stats = _is_stats(results, prepared)
    return optimization_score(stats), stats


def _rank(
    configs: list[StrategyConfig],
    prepared: dict[str, PreparedBacktestData],
    *,
    stage: str,
) -> list[tuple[float, StrategyConfig, WindowStats]]:
    ranked: list[tuple[float, StrategyConfig, WindowStats]] = []
    for index, config in enumerate(configs, 1):
        score, stats = evaluate_is(config, prepared)
        ranked.append((score, config, stats))
        if index % 32 == 0 or index == len(configs):
            print(f"{stage}: {index}/{len(configs)}", flush=True)
    return sorted(ranked, key=lambda item: item[0], reverse=True)


def exit_configurations(
    leaders: list[StrategyConfig],
) -> list[StrategyConfig]:
    """Tune bounded exit geometry around the best IS entries."""

    configs: list[StrategyConfig] = []
    for leader in leaders:
        for target_atr in (1.0, 1.5, 2.0):
            for stop_atr in (1.0, 1.5, 2.0):
                for max_hold_bars in (16, 32, 64):
                    configs.append(
                        replace(
                            leader,
                            name=(
                                f"{leader.name}_tp{target_atr:g}_sl{stop_atr:g}_hold{max_hold_bars}"
                            ),
                            tp_atr=target_atr,
                            sl_atr=stop_atr,
                            max_hold_bars=max_hold_bars,
                        )
                    )
    return configs


def hardening_configurations(leaders: list[StrategyConfig]) -> list[StrategyConfig]:
    """Test stale-swing and duplicate-entry controls on IS only."""

    configs: list[StrategyConfig] = []
    for leader in leaders:
        for invalidate_swing in (False, True):
            for one_entry_per_swing in (False, True):
                configs.append(
                    replace(
                        leader,
                        name=(
                            f"{leader.name}_inv{int(invalidate_swing)}"
                            f"_once{int(one_entry_per_swing)}"
                        ),
                        invalidate_swing=invalidate_swing,
                        one_entry_per_swing=one_entry_per_swing,
                    )
                )
    return configs


def _write_outputs(
    output_dir: Path,
    all_ranked: list[tuple[str, float, StrategyConfig, WindowStats]],
    winner: StrategyConfig,
    in_sample: WindowStats,
    out_of_sample: WindowStats,
    decision: str,
    reasons: list[str],
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"htf_fib_optimization_{stamp}.csv"
    report_path = output_dir / f"htf_fib_optimization_{stamp}.md"
    config_path = output_dir / f"htf_fib_optimization_{stamp}.json"

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "stage",
                "score",
                "config",
                "trades",
                "win_rate",
                "gross_pf",
                "net_pf",
                "net_pnl_pct",
                "max_drawdown_pct",
                "profitable_pairs",
                "tested_pairs",
            )
        )
        for stage, score, config, stats in all_ranked:
            writer.writerow(
                (
                    stage,
                    f"{score:.6f}",
                    config.name,
                    stats.trades,
                    f"{stats.win_rate:.6f}",
                    f"{stats.gross_profit_factor:.6f}",
                    f"{stats.net_profit_factor:.6f}",
                    f"{stats.total_net_pnl_pct:.6f}",
                    f"{stats.max_drawdown_pct:.6f}",
                    stats.profitable_pairs,
                    stats.tested_pairs,
                )
            )

    config_path.write_text(
        json.dumps(
            {
                "winner": asdict(winner),
                "verdict": decision,
                "reasons": reasons,
                "is": asdict(in_sample),
                "oos": asdict(out_of_sample),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [
        "# HTF Fib Bounded Optimization",
        "",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Selection protocol: entry grid, exit grid, and hardening were ranked on IS only. "
        "Exactly one winner was then evaluated on OOS.",
        "",
        f"Winner: `{winner.name}`",
        "",
        "| Window | Trades | WR | Gross PF | Net PF | Net PnL | Max DD | Pairs + |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for window, stats in (("IS", in_sample), ("OOS", out_of_sample)):
        lines.append(
            f"| {window} | {stats.trades} | {stats.win_rate:.1%} | "
            f"{stats.gross_profit_factor:.2f} | {stats.net_profit_factor:.2f} | "
            f"{stats.total_net_pnl_pct:.2f}% | {stats.max_drawdown_pct:.2f}% | "
            f"{stats.profitable_pairs}/{stats.tested_pairs} |"
        )
    lines.extend(
        (
            "",
            f"Verdict: **{decision}**",
            "",
            "Reasons: " + ("; ".join(reasons) if reasons else "all minimum gates passed"),
            "",
            "This is optimal only within the declared bounded grid. It is not evidence of a "
            "global optimum and does not supersede the repository's locked negative result unless "
            "the OOS gates pass.",
            "",
        )
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path, csv_path, config_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", default=",".join(DEFAULT_PAIRS))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument(
        "--override-negative-result",
        type=Path,
        required=True,
        help="Must point to the repository's locked FX directional-TA negative-result report.",
    )
    args = parser.parse_args()
    if args.override_negative_result.resolve() != NEGATIVE_RESULT_REPORT.resolve():
        parser.error(f"override must be {NEGATIVE_RESULT_REPORT}")

    pairs = [pair.strip() for pair in args.pairs.split(",") if pair.strip()]
    full_frames = {}
    cutoffs = {}
    for pair in pairs:
        frames = fetch_pair(pair, args.days)
        if frames is None:
            continue
        frame = frames["15m"].sort_index()
        full_frames[pair] = frame
        cutoffs[pair] = frame.index[int(len(frame) * IS_FRACTION)]
    if not full_frames:
        print("No complete datasets were available.")
        return 1
    conversion_closes = load_usd_conversion_closes(full_frames, args.days)

    entry_grid = entry_configurations()
    pivot_specs = {
        (config.fib_timeframe, config.left_bars, config.right_bars) for config in entry_grid
    }
    prepared_is = {
        pair: prepare_backtest_data(
            pair,
            frame.loc[: cutoffs[pair]],
            pivot_specs=pivot_specs,
            atr_periods={14},
            usd_quote_close=conversion_closes[pair],
        )
        for pair, frame in full_frames.items()
    }

    print(f"Stage 1: {len(entry_grid)} entry configs on IS only", flush=True)
    entry_ranked = _rank(entry_grid, prepared_is, stage="entry")
    exit_grid = exit_configurations([item[1] for item in entry_ranked[:5]])
    print(f"Stage 2: {len(exit_grid)} exit configs on IS only", flush=True)
    exit_ranked = _rank(exit_grid, prepared_is, stage="exit")
    hardening_grid = hardening_configurations([item[1] for item in exit_ranked[:3]])
    print(f"Stage 3: {len(hardening_grid)} hardening configs on IS only", flush=True)
    hardening_ranked = _rank(hardening_grid, prepared_is, stage="hardening")
    winner = hardening_ranked[0][1]

    print(f"Selected IS winner: {winner.name}", flush=True)
    prepared_full = {
        pair: prepare_backtest_data(
            pair,
            frame,
            pivot_specs={(winner.fib_timeframe, winner.left_bars, winner.right_bars)},
            atr_periods={winner.atr_period},
            usd_quote_close=conversion_closes[pair],
        )
        for pair, frame in full_frames.items()
    }
    full_results = [
        run_prepared_backtest(pair, data, winner) for pair, data in prepared_full.items()
    ]
    in_sample = aggregate_window(full_results, cutoffs, oos=False)
    out_of_sample = aggregate_window(full_results, cutoffs, oos=True)
    decision, reasons = verdict(in_sample, out_of_sample)

    all_ranked = [
        *[("entry", score, config, stats) for score, config, stats in entry_ranked],
        *[("exit", score, config, stats) for score, config, stats in exit_ranked],
        *[("hardening", score, config, stats) for score, config, stats in hardening_ranked],
    ]
    report_path, csv_path, config_path = _write_outputs(
        args.output_dir,
        all_ranked,
        winner,
        in_sample,
        out_of_sample,
        decision,
        reasons,
    )
    print(
        f"OOS: {out_of_sample.trades} trades | net PF {out_of_sample.net_profit_factor:.2f} "
        f"| net PnL {out_of_sample.total_net_pnl_pct:.2f}% | {decision}",
        flush=True,
    )
    if reasons:
        print("Reasons: " + "; ".join(reasons), flush=True)
    print(f"Report: {report_path}")
    print(f"Grid: {csv_path}")
    print(f"Winner: {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
