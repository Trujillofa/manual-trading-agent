#!/usr/bin/env python3
"""Evaluate one fixed HTF-Fib config under explicit capital/lot scenarios."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_donchian_backtest import fetch_pair
from scripts.run_htf_fib_backtest import (
    ACCOUNT_SCENARIOS,
    DEFAULT_PAIRS,
    IS_FRACTION,
    AccountScenario,
    StrategyConfig,
    WindowStats,
    aggregate_window,
    load_usd_conversion_closes,
    prepare_backtest_data,
    run_prepared_backtest,
    verdict,
)


def load_winner(path: Path) -> StrategyConfig:
    """Load a selected config from the bounded optimizer's JSON artifact."""

    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    raw = payload["winner"]
    allowed = {field.name for field in fields(StrategyConfig)}
    return StrategyConfig(**{key: value for key, value in raw.items() if key in allowed})


def max_concurrent_trades(results: list) -> int:
    """Return peak open positions across the tested portfolio."""

    events: list[tuple[object, int]] = []
    for result in results:
        for trade in result.trades:
            events.append((trade.entry_time, 1))
            events.append((trade.exit_time, -1))
    active = peak = 0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        active += delta
        peak = max(peak, active)
    return peak


def _ending_balance(account: AccountScenario, stats: WindowStats) -> float:
    return float(account.initial_capital_usd * (1.0 + stats.total_net_pnl_pct / 100.0))


def _write_report(
    output_dir: Path,
    config: StrategyConfig,
    pairs: list[str],
    stop_capital_fraction: float | None,
    rows: list[
        tuple[
            AccountScenario,
            WindowStats,
            WindowStats,
            int,
            dict[str, int],
            str,
            list[str],
        ]
    ],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"htf_fib_account_scenarios_{stamp}.md"
    lines = [
        "# HTF Fib Fixed-Lot Account Scenarios",
        "",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"Configuration: `{config.name}`",
        "",
        f"Pairs ({len(pairs)}): {', '.join(pairs)}",
        "",
        (
            f"Stop: {stop_capital_fraction:.0%} of starting capital per stop-out; "
            "target remains 2 ATR and time exit remains 64 bars."
            if stop_capital_fraction is not None
            else f"Stop: {config.sl_atr:g} ATR."
        ),
        "",
        "Costs: 2 pip spread, 2 pip adverse slippage per fill, and $3 per lot per side.",
        "P&L: converted from each pair's quote currency to USD using historical USD/quote closes.",
        "",
        "| Account | Window | Capital | Lots | Trades | WR | Net PF | Net PnL | "
        "Ending Balance | Max DD |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for account, ins, oos, concurrency, exit_counts, decision, reasons in rows:
        for window, stats in (("IS", ins), ("OOS", oos)):
            lines.append(
                f"| `{account.name}` | {window} | ${account.initial_capital_usd:,.2f} | "
                f"{account.lot_size:.2f} | {stats.trades} | {stats.win_rate:.1%} | "
                f"{stats.net_profit_factor:.2f} | {stats.total_net_pnl_pct:.2f}% | "
                f"${_ending_balance(account, stats):,.2f} | {stats.max_drawdown_pct:.2f}% |"
            )
        lines.extend(
            (
                "",
                f"- `{account.name}` base-notional leverage per position: "
                f"{account.base_notional_leverage:.2f}x.",
                f"- Peak concurrent positions: {concurrency}; approximate peak base-notional "
                f"multiple: {concurrency * account.base_notional_leverage:.2f}x.",
                "- Exit reasons: "
                + ", ".join(f"{reason}={count}" for reason, count in sorted(exit_counts.items())),
                f"- Verdict: **{decision}**. "
                + ("; ".join(reasons) if reasons else "All minimum gates passed."),
                "",
            )
        )
    lines.extend(
        (
            "## Limits",
            "",
            "- Fixed lots are maintained even if equity falls below broker margin requirements.",
            "- Margin calls, stop-out liquidation, financing/swap, and point-in-time news are not modeled.",
            "- A drawdown near or above 100% means the scenario is operationally impossible before the "
            "reported ending balance.",
            "",
        )
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-json", type=Path, required=True)
    parser.add_argument("--pairs", default=",".join(DEFAULT_PAIRS))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument(
        "--stop-capital-fraction",
        type=float,
        default=None,
        help="Approximate fraction of starting capital lost on a fixed-lot stop-out.",
    )
    args = parser.parse_args()

    config = load_winner(args.config_json)
    pairs = [pair.strip() for pair in args.pairs.split(",") if pair.strip()]
    pair_data = {}
    cutoffs = {}
    for pair in pairs:
        frames = fetch_pair(pair, args.days)
        if frames is None:
            continue
        frame = frames["15m"].sort_index()
        pair_data[pair] = frame
        cutoffs[pair] = frame.index[int(len(frame) * IS_FRACTION)]
    if not pair_data:
        print("No complete datasets were available.")
        return 1

    conversion_closes = load_usd_conversion_closes(pair_data, args.days)
    prepared = {
        pair: prepare_backtest_data(
            pair,
            frame,
            pivot_specs={(config.fib_timeframe, config.left_bars, config.right_bars)},
            atr_periods={config.atr_period},
            usd_quote_close=conversion_closes[pair],
        )
        for pair, frame in pair_data.items()
    }

    rows = []
    for account in ACCOUNT_SCENARIOS:
        results = [
            run_prepared_backtest(
                pair,
                data,
                config,
                account=account,
                stop_capital_fraction=args.stop_capital_fraction,
            )
            for pair, data in prepared.items()
        ]
        ins = aggregate_window(results, cutoffs, oos=False)
        oos = aggregate_window(results, cutoffs, oos=True)
        decision, reasons = verdict(ins, oos)
        concurrency = max_concurrent_trades(results)
        exit_counts = Counter(trade.exit_reason for result in results for trade in result.trades)
        rows.append(
            (
                account,
                ins,
                oos,
                concurrency,
                dict(exit_counts),
                decision,
                reasons,
            )
        )
        print(
            f"{account.name}: OOS {oos.trades} trades | PF {oos.net_profit_factor:.2f} | "
            f"PnL {oos.total_net_pnl_pct:.2f}% | DD {oos.max_drawdown_pct:.2f}% | "
            f"{decision}",
            flush=True,
        )

    report = _write_report(
        args.output_dir,
        config,
        list(pair_data),
        args.stop_capital_fraction,
        rows,
    )
    print(f"Report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
