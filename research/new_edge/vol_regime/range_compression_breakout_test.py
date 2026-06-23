#!/usr/bin/env python3
"""
Smallest gross-only vol-regime / range compression breakout falsifier.

Per VOL_REGIME_CONTRACT_2026-06-19.md:
- H1 Donchian range compression (20-bar, 252-bar 10th percentile, 3-bar persistence)
- Breakout entry 07:00-17:00 UTC, 24-bar time stop
- Seven FX majors, gross-first, net/OOS only if gross passes

Run:
  python -m research.new_edge.vol_regime.range_compression_breakout_test \
    --start 2016-01-01 --end 2026-06-01 \
    --output docs/research/vol_regime/VOL_REGIME_RESULTS_2026-06-19.md
"""

from __future__ import annotations

import argparse
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from src.data.dukascopy_fetcher import _download_day_raw, _resample_ohlc

logger = logging.getLogger(__name__)

FX_MAJORS: tuple[str, ...] = (
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CAD",
    "USD/CHF",
    "NZD/USD",
)

DONCHIAN_WINDOW = 20
ROLLING_HISTORY = 252
COMPRESSION_PERCENTILE = 0.10
COMPRESSION_PERSISTENCE = 3
ENTRY_HOUR_START = 7
ENTRY_HOUR_END = 17
TIME_STOP_BARS = 24

GROSS_PF_PASS = 1.10
GROSS_PF_DISCARD = 1.05
MIN_TRADES = 30
IS_OOS_SPLIT = 0.70
NET_OOS_PF_PASS = 1.20

BASE_SPREAD_PIPS = 2.0
SLIPPAGE_PIPS = 1.0
ROUND_TRIP_COST_PIPS = 2 * (BASE_SPREAD_PIPS + SLIPPAGE_PIPS)

MIN_H1_BARS = ROLLING_HISTORY + DONCHIAN_WINDOW + TIME_STOP_BARS + COMPRESSION_PERSISTENCE

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / "data" / "cache"


@dataclass
class BreakoutTrade:
    pair: str
    entry_idx: int
    exit_idx: int
    entry_time: datetime
    exit_time: datetime
    direction: int
    entry_price: float
    exit_price: float
    gross_pips: float


def pip_size(pair: str) -> float:
    return 0.01 if pair.endswith("JPY") else 0.0001


def _dukascopy_symbol(pair: str) -> str:
    return pair.replace("/", "")


def _pair_h1_cache_path(cache_dir: Path, pair: str) -> Path:
    return cache_dir / _dukascopy_symbol(pair) / "h1_consolidated.parquet"


def _load_cached_h1(cache_path: Path) -> pd.DataFrame:
    df = pd.read_parquet(cache_path)
    if not isinstance(df.index, pd.DatetimeIndex):
        if "datetime" in df.columns:
            df = df.set_index("datetime")
        df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


def _download_m1_range(symbol: str, start: datetime, end: datetime, workers: int = 16) -> pd.DataFrame:
    """Download M1 candles in parallel and return a sorted DataFrame."""
    start_naive = datetime(start.year, start.month, start.day)
    end_naive = datetime(end.year, end.month, end.day)
    days: list[datetime] = []
    current = start_naive
    while current <= end_naive:
        days.append(current)
        current += timedelta(days=1)

    records: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_download_day_raw, symbol, day): day for day in days}
        for future in as_completed(futures):
            records.extend(future.result())

    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    return df.drop_duplicates(subset=["datetime"], keep="last").sort_values("datetime")


def fetch_h1_window(
    pair: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
) -> pd.DataFrame:
    """Load H1 bars for [start, end) with per-pair consolidated Dukascopy cache."""
    cache_path = _pair_h1_cache_path(cache_dir, pair)
    if cache_path.exists():
        combined = _load_cached_h1(cache_path)
    else:
        sym = _dukascopy_symbol(pair)
        logger.info("downloading %s M1 (%s -> %s)", pair, start.date(), end.date())
        m1 = _download_m1_range(sym, start, end)
        if m1.empty:
            return pd.DataFrame()
        m1 = m1.set_index("datetime")
        m1.index = pd.to_datetime(m1.index, utc=True)
        combined = _resample_ohlc(m1, "1h")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        combined.index = pd.to_datetime(combined.index, utc=True)
        combined.to_parquet(cache_path)
        logger.info("cached %s H1 bars=%d", pair, len(combined))

    idx = pd.to_datetime(combined.index, utc=True)
    mask = (idx >= start) & (idx < end)
    return pd.DataFrame(combined.loc[mask])


def compute_donchian_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Donchian range, compression flag, and breakout boundaries."""
    high = bars["high"]
    low = bars["low"]
    donchian_high = high.rolling(DONCHIAN_WINDOW).max()
    donchian_low = low.rolling(DONCHIAN_WINDOW).min()
    donchian_range = donchian_high - donchian_low

    prior_threshold = donchian_range.shift(1).rolling(ROLLING_HISTORY).quantile(
        COMPRESSION_PERCENTILE
    )
    compressed = donchian_range <= prior_threshold

    return pd.DataFrame(
        {
            "open": bars["open"],
            "high": high,
            "low": low,
            "close": bars["close"],
            "donchian_high": donchian_high,
            "donchian_low": donchian_low,
            "donchian_range": donchian_range,
            "compressed": compressed,
        },
        index=bars.index,
    )


def in_entry_window(ts: pd.Timestamp) -> bool:
    hour = ts.hour
    return ENTRY_HOUR_START <= hour < ENTRY_HOUR_END


def breakout_direction(
    close: float,
    donchian_high: float,
    donchian_low: float,
) -> int:
    if close > donchian_high:
        return 1
    if close < donchian_low:
        return -1
    return 0


def gross_pips(direction: int, entry: float, exit_price: float, pair: str) -> float:
    pip = pip_size(pair)
    raw = (exit_price - entry) / pip
    return raw if direction == 1 else -raw


def simulate_pair_trades(
    pair: str,
    frame: pd.DataFrame,
    start: datetime,
    end: datetime,
) -> list[BreakoutTrade]:
    """Bar-by-bar simulation for one pair."""
    trades: list[BreakoutTrade] = []
    idx = pd.to_datetime(frame.index, utc=True)

    episode_armed = False
    armed_at: int | None = None
    compression_streak = 0
    in_trade_until = -1

    warmup = ROLLING_HISTORY + DONCHIAN_WINDOW
    for i in range(warmup, len(frame)):
        ts = pd.Timestamp(idx[i])
        if ts < start or ts >= end:
            continue
        if i <= in_trade_until:
            continue

        compressed = bool(frame["compressed"].iloc[i]) and not pd.isna(frame["compressed"].iloc[i])

        if compressed:
            compression_streak += 1
            if compression_streak >= COMPRESSION_PERSISTENCE and not episode_armed:
                episode_armed = True
                armed_at = i
        else:
            compression_streak = 0

        if not episode_armed or armed_at is None or i <= armed_at:
            continue

        if not in_entry_window(ts):
            continue

        direction = breakout_direction(
            float(frame["close"].iloc[i]),
            float(frame["donchian_high"].iloc[i - 1]),
            float(frame["donchian_low"].iloc[i - 1]),
        )
        if direction == 0:
            continue

        exit_idx = i + TIME_STOP_BARS
        if exit_idx >= len(frame):
            continue

        entry_price = float(frame["close"].iloc[i])
        exit_price = float(frame["close"].iloc[exit_idx])
        exit_ts = pd.Timestamp(idx[exit_idx])
        if exit_ts >= end:
            continue

        pips = gross_pips(direction, entry_price, exit_price, pair)
        trades.append(
            BreakoutTrade(
                pair=pair,
                entry_idx=i,
                exit_idx=exit_idx,
                entry_time=ts.to_pydatetime().replace(tzinfo=UTC),
                exit_time=exit_ts.to_pydatetime().replace(tzinfo=UTC),
                direction=direction,
                entry_price=entry_price,
                exit_price=exit_price,
                gross_pips=pips,
            )
        )
        in_trade_until = exit_idx
        episode_armed = False
        armed_at = None
        compression_streak = 0

    return trades


def _pf_from_values(values: list[float]) -> float:
    wins = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return wins / losses


def trade_stats(trades: list[BreakoutTrade], cost_pips: float = 0.0) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0,
            "gross_pf": 0.0,
            "net_pf": 0.0,
            "win_rate": 0.0,
            "total_gross_pips": 0.0,
            "total_net_pips": 0.0,
            "avg_gross_pips": 0.0,
        }
    gross_pnls = [t.gross_pips for t in trades]
    net_pnls = [t.gross_pips - cost_pips for t in trades]
    return {
        "trades": len(trades),
        "gross_pf": _pf_from_values(gross_pnls),
        "net_pf": _pf_from_values(net_pnls),
        "win_rate": sum(1 for p in gross_pnls if p > 0) / len(gross_pnls),
        "total_gross_pips": sum(gross_pnls),
        "total_net_pips": sum(net_pnls),
        "avg_gross_pips": sum(gross_pnls) / len(gross_pnls),
    }


def is_oos_stats(
    trades: list[BreakoutTrade],
    cost_pips: float,
    split_ratio: float = IS_OOS_SPLIT,
) -> dict[str, Any]:
    if not trades:
        return {"is": trade_stats([]), "oos": trade_stats([]), "split_time": None}
    ordered = sorted(trades, key=lambda t: t.entry_time)
    split_idx = int(len(ordered) * split_ratio)
    split_time = ordered[split_idx].entry_time if split_idx < len(ordered) else None
    if split_time is None:
        is_trades = ordered
        oos_trades: list[BreakoutTrade] = []
    else:
        is_trades = [t for t in ordered if t.entry_time < split_time]
        oos_trades = [t for t in ordered if t.entry_time >= split_time]
    return {
        "is": trade_stats(is_trades, cost_pips),
        "oos": trade_stats(oos_trades, cost_pips),
        "split_time": split_time.isoformat() if split_time else None,
    }


def year_concentration(trades: list[BreakoutTrade]) -> dict[int, float]:
    winners = [t for t in trades if t.gross_pips > 0]
    by_year: dict[int, float] = {}
    total_pos = 0.0
    for trade in winners:
        year = trade.entry_time.year
        by_year[year] = by_year.get(year, 0.0) + trade.gross_pips
        total_pos += trade.gross_pips
    if total_pos <= 0:
        return {}
    return {year: value / total_pos for year, value in sorted(by_year.items())}


def pair_breakdown(trades: list[BreakoutTrade]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for pair in {t.pair for t in trades}:
        subset = [t for t in trades if t.pair == pair]
        out[pair] = trade_stats(subset)
    return out


def determine_verdict(stats: dict[str, Any], max_year_share: float) -> tuple[str, str]:
    if stats["trades"] < MIN_TRADES:
        return "DISCARD", f"trades {stats['trades']} < {MIN_TRADES}"
    if stats["gross_pf"] <= GROSS_PF_DISCARD:
        return "DISCARD", f"gross PF {stats['gross_pf']:.3f} <= {GROSS_PF_DISCARD} (near 1.0)"
    if stats["gross_pf"] < GROSS_PF_PASS:
        return "DISCARD", f"gross PF {stats['gross_pf']:.3f} < {GROSS_PF_PASS}"
    if max_year_share > 0.50:
        return "DISCARD", f"profit concentrated in one year ({max_year_share:.0%})"
    return "GROSS_PASS", "N/A"


def determine_net_verdict(gross_verdict: str, splits: dict[str, Any]) -> tuple[str, str]:
    if gross_verdict != "GROSS_PASS":
        return gross_verdict, "gross stage did not pass"
    oos = splits["oos"]
    if oos["trades"] < MIN_TRADES:
        return "DISCARD", f"OOS trades {oos['trades']} < {MIN_TRADES}"
    if oos["gross_pf"] <= 1.05:
        return "DISCARD", f"OOS gross PF {oos['gross_pf']:.3f} <= 1.05"
    if oos["net_pf"] < NET_OOS_PF_PASS:
        return "DISCARD", f"OOS net PF {oos['net_pf']:.3f} < {NET_OOS_PF_PASS}"
    return "KEEP", "N/A"


def build_results_doc(
    *,
    command: str,
    start: str,
    end: str,
    trades: list[BreakoutTrade],
    stats: dict[str, Any],
    splits: dict[str, Any],
    concentration: dict[int, float],
    pairs: dict[str, dict[str, Any]],
    verdict: str,
    reason: str,
    net_verdict: str,
    net_reason: str,
) -> str:
    max_year_share = max(concentration.values()) if concentration else 0.0
    top_year = max(concentration, key=lambda y: concentration[y]) if concentration else None

    lines = [
        "# Vol-Regime Range Compression Breakout Results — 2026-06-19",
        "",
        f"## Lane verdict: **{verdict}**",
        "",
        f"Reason: {reason}",
        "",
        "## Command",
        "```bash",
        command,
        "```",
        "",
        f"## Window: {start} → {end}",
        f"- Universe: {', '.join(FX_MAJORS)}",
        f"- Pooled trades: {stats['trades']}",
        "",
        "## Parameters (fixed, no optimization)",
        f"- Donchian window: {DONCHIAN_WINDOW} H1 bars",
        f"- Compression threshold: {int(COMPRESSION_PERCENTILE * 100)}th percentile of prior "
        f"{ROLLING_HISTORY} H1 ranges",
        f"- Compression persistence: {COMPRESSION_PERSISTENCE} consecutive bars",
        f"- Entry window: {ENTRY_HOUR_START:02d}:00-{ENTRY_HOUR_END:02d}:00 UTC",
        f"- Time stop: {TIME_STOP_BARS} H1 bars",
        "- Costs (gross run): **zero**",
        f"- Net cost model (if gross passes): {ROUND_TRIP_COST_PIPS:.1f} pips round-trip",
        "",
        "## Pooled gross-first stats",
        f"- Trades: {stats['trades']}",
        f"- Gross PF: {stats['gross_pf']:.3f}",
        f"- Win rate: {stats['win_rate']:.1%}",
        f"- Total gross pips: {stats['total_gross_pips']:.1f}",
        f"- Avg gross pips/trade: {stats['avg_gross_pips']:.2f}",
        "",
    ]

    if verdict == "GROSS_PASS" or net_verdict in {"KEEP", "DISCARD"}:
        lines.extend(
            [
                "## Net + IS/OOS (after gross pass)",
                f"- Split time (70% entries): {splits['split_time']}",
                f"- IS: {splits['is']['trades']} trades, gross PF {splits['is']['gross_pf']:.3f}, "
                f"net PF {splits['is']['net_pf']:.3f}",
                f"- OOS: {splits['oos']['trades']} trades, gross PF {splits['oos']['gross_pf']:.3f}, "
                f"net PF {splits['oos']['net_pf']:.3f}",
                f"- Net stage verdict: **{net_verdict}** — {net_reason}",
                "",
            ]
        )

    if concentration:
        lines.append(f"- Max year concentration: {max_year_share:.1%} ({top_year})")
        lines.append("")

    lines.append("## Per-pair breakdown")
    lines.append("")
    for pair, pair_stats in sorted(pairs.items()):
        lines.append(
            f"- {pair}: {pair_stats['trades']} trades, gross PF {pair_stats['gross_pf']:.3f}, "
            f"avg {pair_stats['avg_gross_pips']:.2f} pips"
        )
    lines.append("")

    lines.extend(["## Sample trades (first 10)", ""])
    ordered = sorted(trades, key=lambda t: t.entry_time)
    for trade in ordered[:10]:
        side = "BUY" if trade.direction == 1 else "SELL"
        lines.append(
            f"- {trade.entry_time.date()} {side} {trade.pair} "
            f"| gross {trade.gross_pips:+.1f} pips"
        )

    lines.extend(
        [
            "",
            "## Accounting notes",
            "- Entry: H1 close on first breakout bar after compression arms (07:00-17:00 UTC).",
            "- Exit: H1 close after 24 bars.",
            "- Dukascopy M1 resampled to H1; per-pair consolidated parquet cache.",
            "- No parameter sweeps; single contract parameter set.",
            "- Closed lanes (TA, TSMOM, carry, stat-arb, event drift) not reopened.",
            "",
            "## Next step",
        ]
    )
    if verdict == "GROSS_PASS" and net_verdict == "KEEP":
        lines.append("Proceed to paper-shadow monitoring design; do not optimize parameters.")
    elif verdict == "GROSS_PASS":
        lines.append("Gross passed but net/OOS failed. Lane falsified after costs or OOS gates.")
    elif verdict == "BLOCKED":
        lines.append("Improve H1 cache coverage; re-run before strategy claims.")
    else:
        lines.append("Range compression breakout falsified at gross stage. Do not tune.")

    return "\n".join(lines)


def append_ledger_row(
    ledger_path: Path,
    *,
    status: str,
    command: str,
    start: str,
    end: str,
    stats: dict[str, Any],
    splits: dict[str, Any],
    result_doc: str,
    failure_reason: str,
) -> None:
    row = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lane": "vol_regime",
        "hypothesis": "H1 range-compression breakout after bottom-decile Donchian range",
        "status": status,
        "branch": "docs/profitability-plan-2026-06",
        "command": command,
        "data_start": start,
        "data_end": end,
        "gross_pf": round(stats["gross_pf"], 4),
        "net_pf": round(stats["net_pf"], 4),
        "oos_pf": round(splits["oos"]["net_pf"], 4),
        "oos_return_pct": 0.0,
        "trades_or_events": stats["trades"],
        "max_drawdown_pct": 0.0,
        "result_doc": result_doc,
        "failure_reason": failure_reason,
    }
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def run_backtest(
    start: datetime,
    end: datetime,
    cache_dir: Path,
) -> dict[str, Any]:
    all_trades: list[BreakoutTrade] = []
    pair_bars: dict[str, int] = {}

    for pair in FX_MAJORS:
        logger.info("loading %s", pair)
        bars = fetch_h1_window(pair, start, end, cache_dir)
        pair_bars[pair] = len(bars)
        if len(bars) < MIN_H1_BARS:
            logger.warning("%s insufficient bars: %d", pair, len(bars))
            continue
        frame = compute_donchian_features(bars)
        pair_trades = simulate_pair_trades(pair, frame, start, end)
        all_trades.extend(pair_trades)
        logger.info("%s trades=%d", pair, len(pair_trades))

    stats = trade_stats(all_trades, cost_pips=0.0)
    splits = is_oos_stats(all_trades, cost_pips=ROUND_TRIP_COST_PIPS)
    concentration = year_concentration(all_trades)
    pairs = pair_breakdown(all_trades)
    max_year_share = max(concentration.values()) if concentration else 0.0

    if any(pair_bars.get(p, 0) < MIN_H1_BARS for p in FX_MAJORS):
        verdict = "BLOCKED"
        reason = "insufficient H1 coverage for one or more pairs"
        net_verdict = "BLOCKED"
        net_reason = reason
    else:
        verdict, reason = determine_verdict(stats, max_year_share)
        if verdict == "GROSS_PASS":
            net_verdict, net_reason = determine_net_verdict(verdict, splits)
        else:
            net_verdict, net_reason = verdict, reason

    return {
        "trades": all_trades,
        "stats": stats,
        "splits": splits,
        "concentration": concentration,
        "pairs": pairs,
        "verdict": verdict,
        "reason": reason,
        "net_verdict": net_verdict,
        "net_reason": net_reason,
        "pair_bars": pair_bars,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Vol-regime range compression breakout falsifier")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2026-06-01")
    parser.add_argument(
        "--output",
        default="docs/research/vol_regime/VOL_REGIME_RESULTS_2026-06-19.md",
    )
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument(
        "--ledger",
        default="research/new_edge/research_ledger.jsonl",
    )
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)
    cache_dir = Path(args.cache_dir)
    output_path = Path(args.output)

    command = (
        "python -m research.new_edge.vol_regime.range_compression_breakout_test "
        f"--start {args.start} --end {args.end} --output {args.output}"
    )

    result = run_backtest(start, end, cache_dir)
    doc = build_results_doc(
        command=command,
        start=args.start,
        end=args.end,
        trades=result["trades"],
        stats=result["stats"],
        splits=result["splits"],
        concentration=result["concentration"],
        pairs=result["pairs"],
        verdict=result["verdict"],
        reason=result["reason"],
        net_verdict=result["net_verdict"],
        net_reason=result["net_reason"],
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(doc, encoding="utf-8")

    ledger_status = result["net_verdict"] if result["verdict"] == "GROSS_PASS" else result["verdict"]
    append_ledger_row(
        Path(args.ledger),
        status=ledger_status,
        command=command,
        start=args.start,
        end=args.end,
        stats=result["stats"],
        splits=result["splits"],
        result_doc=(
            "docs/research/vol_regime/VOL_REGIME_CONTRACT_2026-06-19.md + "
            f"{args.output}"
        ),
        failure_reason=result["net_reason"] if ledger_status not in {"KEEP", "GROSS_PASS"} else "N/A",
    )

    print(f"Results written to {output_path}")
    print(f"Lane verdict: {result['verdict']} ({result['reason']})")
    if result["verdict"] == "GROSS_PASS":
        print(f"Net/OOS verdict: {result['net_verdict']} ({result['net_reason']})")
    stats = result["stats"]
    print(
        f"Trades={stats['trades']} gross_PF={stats['gross_pf']:.3f} "
        f"total_pips={stats['total_gross_pips']:.1f}"
    )


if __name__ == "__main__":
    main()
