#!/usr/bin/env python3
"""
Smallest gross-only post-release event drift falsifier per EVENT_DRIFT_CONTRACT_2026-06-19.md.

- High-impact indicator events: NFP, CPI, GDP, PMI, rate decisions.
- Surprise direction from Actual vs Forecast (post-release label at datetime_utc).
- Fixed entry delay 30 min; fixed hold 4 hours.
- Major FX pairs mapped to event currency.
- Gross-first; costs + IS/OOS only if gross passes.

Run:
  python -m research.new_edge.events.post_release_drift_test \
    --start 2016-01-01 --end 2025-04-07 \
    --output docs/research/events/EVENT_DRIFT_RESULTS_2026-06-19.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from research.new_edge.events.data.verify_event_data import (
    DEFAULT_PINNED_CSV,
    _is_high_impact,
    _is_missing_value,
    _is_non_economic,
    load_snapshot,
    normalize_datetimes,
)
from src.data.dukascopy_fetcher import _resample_ohlc, download_dukascopy_data

logger = logging.getLogger(__name__)

ENTRY_DELAY = timedelta(minutes=30)
HOLD_DURATION = timedelta(hours=4)

GROSS_PF_PASS = 1.10
GROSS_PF_DISCARD = 1.05
MIN_TRADES = 30
IS_OOS_SPLIT = 0.70
NET_OOS_PF_PASS = 1.20

BASE_SPREAD_PIPS = 2.0
RELEASE_SPREAD_MULT = 3.0
RELEASE_SLIPPAGE_PIPS = 1.0
ROUND_TRIP_COST_PIPS = 2 * (BASE_SPREAD_PIPS * RELEASE_SPREAD_MULT + RELEASE_SLIPPAGE_PIPS)

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / "data" / "cache"

# Fixed currency → primary major pair and leg position of event currency.
CURRENCY_PAIR_MAP: dict[str, tuple[str, str]] = {
    "USD": ("EUR/USD", "quote"),
    "EUR": ("EUR/USD", "base"),
    "GBP": ("GBP/USD", "base"),
    "JPY": ("USD/JPY", "quote"),
    "AUD": ("AUD/USD", "base"),
    "CAD": ("USD/CAD", "quote"),
    "CHF": ("USD/CHF", "quote"),
    "NZD": ("NZD/USD", "base"),
}

EVENT_FAMILY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("nfp", (r"non-farm employment change", r"non-farm payrolls")),
    ("cpi", (r"\bcpi\b", r"consumer price index")),
    ("gdp", (r"\bgdp\b", r"gross domestic product")),
    ("pmi", (r"\bpmi\b",)),
    (
        "rate_decision",
        (
            r"rate decision",
            r"interest rate decision",
            r"official bank rate",
            r"\bfomc\b.*rate",
            r"cash rate",
        ),
    ),
)

NUMERIC_VALUE_RE = re.compile(
    r"^[-+]?(\d{1,3}(,\d{3})*|\d+)(\.\d+)?([KMB%]|bp|bps)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DriftEvent:
    event_id: str
    datetime_utc: datetime
    currency: str
    family: str
    event_name: str
    actual_raw: str
    forecast_raw: str
    surprise_sign: int
    pair: str
    leg: str
    direction: int  # +1 BUY, -1 SELL


@dataclass
class DriftTrade:
    event: DriftEvent
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    gross_pips: float
    skipped_reason: str = ""


def parse_numeric_value(raw: str) -> float | None:
    """Parse FF-style numeric release values for surprise comparison."""
    if _is_missing_value(raw):
        return None
    text = str(raw).strip().replace(",", "").replace(" ", "")
    if text in {"--", "-"}:
        return None
    if "|" in text:
        parts = [p for p in text.split("|") if p]
        if not parts:
            return None
        text = parts[0]

    multiplier = 1.0
    if text.endswith("%"):
        text = text[:-1]
    elif text.upper().endswith("K"):
        text = text[:-1]
        multiplier = 1_000.0
    elif text.upper().endswith("M"):
        text = text[:-1]
        multiplier = 1_000_000.0
    elif text.upper().endswith("B"):
        text = text[:-1]
        multiplier = 1_000_000_000.0
    elif text.upper().endswith("BP"):
        text = text[:-2]
        multiplier = 0.01
    elif text.upper().endswith("BPS"):
        text = text[:-3]
        multiplier = 0.01

    if not NUMERIC_VALUE_RE.match(text + ("%" if multiplier == 1.0 else "")):
        # Allow parsed fragments after suffix stripping
        try:
            float(text)
        except ValueError:
            return None

    try:
        return float(text) * multiplier
    except ValueError:
        return None


def surprise_sign(actual_raw: str, forecast_raw: str) -> int:
    actual = parse_numeric_value(actual_raw)
    forecast = parse_numeric_value(forecast_raw)
    if actual is None or forecast is None:
        return 0
    if actual > forecast:
        return 1
    if actual < forecast:
        return -1
    return 0


def classify_event_family(event_name: str) -> str | None:
    name = event_name.lower()
    if "adp" in name and "non-farm" in name:
        return None
    for family, patterns in EVENT_FAMILY_RULES:
        if any(re.search(pat, name) for pat in patterns):
            return family
    return None


def trade_direction(surprise: int, leg: str) -> int:
    """Map surprise to BUY (+1) or SELL (-1) on the mapped pair."""
    if surprise == 0:
        return 0
    if leg == "base":
        return surprise
    return -surprise


def pip_size(pair: str) -> float:
    return 0.01 if pair.endswith("JPY") else 0.0001


def _dukascopy_symbol(pair: str) -> str:
    return pair.replace("/", "")


def _pair_cache_path(cache_dir: Path, pair: str, day: datetime) -> Path:
    return cache_dir / _dukascopy_symbol(pair) / f"{day.strftime('%Y-%m-%d')}.parquet"


def fetch_m15_day(
    pair: str,
    day: datetime,
    cache_dir: Path,
) -> pd.DataFrame:
    """Fetch or load cached M15 bars for a single UTC calendar day."""
    cache_path = _pair_cache_path(cache_dir, pair, day)
    if cache_path.exists():
        df = pd.read_parquet(cache_path)
        if not isinstance(df.index, pd.DatetimeIndex):
            if "datetime" in df.columns:
                df = df.set_index("datetime")
            df.index = pd.to_datetime(df.index, utc=True)
        return df

    sym = _dukascopy_symbol(pair)
    day_naive = datetime(day.year, day.month, day.day)
    m1, _ = download_dukascopy_data(sym, day_naive, day_naive, strict=False)
    if m1.empty:
        df = pd.DataFrame()
    else:
        m1 = m1.set_index("datetime")
        m1.index = pd.to_datetime(m1.index, utc=True)
        df = _resample_ohlc(m1, "15min")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if df.empty:
        df.to_parquet(cache_path)
        return df

    df.index = pd.to_datetime(df.index, utc=True)
    df.to_parquet(cache_path)
    return df


def fetch_m15_window(
    pair: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    current = datetime(start.year, start.month, start.day, tzinfo=UTC)
    end_day = datetime(end.year, end.month, end.day, tzinfo=UTC)
    while current <= end_day:
        day_df = fetch_m15_day(pair, current, cache_dir)
        if not day_df.empty:
            frames.append(day_df)
        current += timedelta(days=1)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames)
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined.sort_index()


def entry_exit_prices(
    bars: pd.DataFrame,
    entry_time: datetime,
    exit_time: datetime,
) -> tuple[float | None, float | None]:
    if bars.empty:
        return None, None

    idx = pd.to_datetime(bars.index, utc=True)
    entry_ts = pd.Timestamp(entry_time)
    exit_ts = pd.Timestamp(exit_time)

    entry_slice = bars[idx >= entry_ts]
    exit_slice = bars[idx <= exit_ts]
    if entry_slice.empty or exit_slice.empty:
        return None, None

    entry_price = float(entry_slice.iloc[0]["open"])
    exit_price = float(exit_slice.iloc[-1]["close"])
    return entry_price, exit_price


def gross_pips(direction: int, entry: float, exit_price: float, pair: str) -> float:
    pip = pip_size(pair)
    raw = (exit_price - entry) / pip
    return raw if direction == 1 else -raw


def load_drift_events(
    calendar_path: Path,
    start: datetime,
    end: datetime,
) -> list[DriftEvent]:
    df = load_snapshot(calendar_path)
    working, _, _ = normalize_datetimes(df)

    events: list[DriftEvent] = []
    seen_keys: set[str] = set()

    for row in working.itertuples(index=False):
        if not _is_high_impact(row.Impact) or _is_non_economic(row.Impact):
            continue

        family = classify_event_family(str(row.Event))
        if family is None:
            continue

        currency = str(row.Currency).strip().upper()
        if currency not in CURRENCY_PAIR_MAP:
            continue

        dt = row.datetime_utc
        if pd.isna(dt):
            continue
        release = dt.to_pydatetime() if hasattr(dt, "to_pydatetime") else dt
        if release.tzinfo is None:
            release = release.replace(tzinfo=UTC)
        if release < start or release > end:
            continue

        sign = surprise_sign(str(row.Actual), str(row.Forecast))
        if sign == 0:
            continue

        pair, leg = CURRENCY_PAIR_MAP[currency]
        direction = trade_direction(sign, leg)
        if direction == 0:
            continue

        dedupe_key = f"{release.isoformat()}|{currency}|{row.Event}"
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        events.append(
            DriftEvent(
                event_id=hashlib.sha256(dedupe_key.encode()).hexdigest()[:12],
                datetime_utc=release,
                currency=currency,
                family=family,
                event_name=str(row.Event),
                actual_raw=str(row.Actual),
                forecast_raw=str(row.Forecast),
                surprise_sign=sign,
                pair=pair,
                leg=leg,
                direction=direction,
            )
        )

    events.sort(key=lambda e: e.datetime_utc)
    return events


def simulate_trades(
    events: list[DriftEvent],
    cache_dir: Path,
) -> tuple[list[DriftTrade], dict[str, int]]:
    trades: list[DriftTrade] = []
    skip_counts: dict[str, int] = {}

    for event in events:
        entry_time = event.datetime_utc + ENTRY_DELAY
        exit_time = entry_time + HOLD_DURATION
        bars = fetch_m15_window(event.pair, entry_time, exit_time, cache_dir)
        entry_price, exit_price = entry_exit_prices(bars, entry_time, exit_time)

        if entry_price is None or exit_price is None:
            skip_counts["missing_ohlc"] = skip_counts.get("missing_ohlc", 0) + 1
            trades.append(
                DriftTrade(
                    event=event,
                    entry_time=entry_time,
                    exit_time=exit_time,
                    entry_price=0.0,
                    exit_price=0.0,
                    gross_pips=0.0,
                    skipped_reason="missing_ohlc",
                )
            )
            continue

        pips = gross_pips(event.direction, entry_price, exit_price, event.pair)
        trades.append(
            DriftTrade(
                event=event,
                entry_time=entry_time,
                exit_time=exit_time,
                entry_price=entry_price,
                exit_price=exit_price,
                gross_pips=pips,
            )
        )

    return trades, skip_counts


def _pf_from_values(values: list[float]) -> float:
    wins = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return wins / losses


def trade_stats(trades: list[DriftTrade], cost_pips: float = 0.0) -> dict[str, Any]:
    filled = [t for t in trades if not t.skipped_reason]
    gross_pnls = [t.gross_pips for t in filled]
    net_pnls = [t.gross_pips - cost_pips for t in filled]
    if not filled:
        return {
            "trades": 0,
            "gross_pf": 0.0,
            "net_pf": 0.0,
            "win_rate": 0.0,
            "total_gross_pips": 0.0,
            "total_net_pips": 0.0,
            "avg_gross_pips": 0.0,
        }
    return {
        "trades": len(filled),
        "gross_pf": _pf_from_values(gross_pnls),
        "net_pf": _pf_from_values(net_pnls),
        "win_rate": sum(1 for p in gross_pnls if p > 0) / len(gross_pnls),
        "total_gross_pips": sum(gross_pnls),
        "total_net_pips": sum(net_pnls),
        "avg_gross_pips": sum(gross_pnls) / len(gross_pnls),
    }


def is_oos_stats(
    trades: list[DriftTrade],
    cost_pips: float,
    split_ratio: float = IS_OOS_SPLIT,
) -> dict[str, Any]:
    filled = [t for t in trades if not t.skipped_reason]
    if not filled:
        return {"is": trade_stats([]), "oos": trade_stats([]), "split_time": None}
    split_idx = int(len(filled) * split_ratio)
    split_time = filled[split_idx].event.datetime_utc if split_idx < len(filled) else None
    is_trades = [t for t in filled if t.event.datetime_utc < split_time]
    oos_trades = [t for t in filled if t.event.datetime_utc >= split_time]
    return {
        "is": trade_stats(is_trades, cost_pips),
        "oos": trade_stats(oos_trades, cost_pips),
        "split_time": split_time.isoformat() if split_time else None,
    }


def year_concentration(trades: list[DriftTrade]) -> dict[int, float]:
    filled = [t for t in trades if not t.skipped_reason and t.gross_pips > 0]
    by_year: dict[int, float] = {}
    total_pos = 0.0
    for trade in filled:
        year = trade.event.datetime_utc.year
        by_year[year] = by_year.get(year, 0.0) + trade.gross_pips
        total_pos += trade.gross_pips
    if total_pos <= 0:
        return {}
    return {year: value / total_pos for year, value in sorted(by_year.items())}


def family_breakdown(trades: list[DriftTrade]) -> dict[str, dict[str, Any]]:
    filled = [t for t in trades if not t.skipped_reason]
    out: dict[str, dict[str, Any]] = {}
    for family in {t.event.family for t in filled}:
        subset = [t for t in filled if t.event.family == family]
        out[family] = trade_stats(subset)
    return out


def determine_verdict(
    stats: dict[str, Any],
    max_year_share: float,
    skipped: int,
    eligible: int,
) -> tuple[str, str]:
    if stats["trades"] < MIN_TRADES:
        if eligible >= MIN_TRADES and skipped > eligible * 0.5:
            return "BLOCKED", f"filled trades {stats['trades']} < {MIN_TRADES} with high OHLC skip rate"
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
    calendar_path: Path,
    eligible_events: int,
    trades: list[DriftTrade],
    skip_counts: dict[str, int],
    stats: dict[str, Any],
    splits: dict[str, Any],
    concentration: dict[int, float],
    families: dict[str, dict[str, Any]],
    verdict: str,
    reason: str,
    net_verdict: str,
    net_reason: str,
) -> str:
    max_year_share = max(concentration.values()) if concentration else 0.0
    top_year = max(concentration, key=concentration.get) if concentration else None
    filled = [t for t in trades if not t.skipped_reason]

    lines = [
        "# Post-Release Event Drift Results — 2026-06-19",
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
        f"- Calendar: `{calendar_path}`",
        f"- Eligible events (surprise ≠ 0, filters applied): {eligible_events}",
        f"- Filled trades: {stats['trades']}",
        f"- Skipped (missing OHLC): {skip_counts.get('missing_ohlc', 0)}",
        "",
        "## Parameters (fixed, no optimization)",
        f"- Entry delay: {int(ENTRY_DELAY.total_seconds() // 60)} minutes after `datetime_utc`",
        f"- Hold: {int(HOLD_DURATION.total_seconds() // 3600)} hours",
        "- Signal: sign(Actual − Forecast) at/after release; Actual is post-release label only",
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
                f"- Split time (70% events): {splits['split_time']}",
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

    lines.append("## Per-family breakdown")
    lines.append("")
    for family, fam_stats in sorted(families.items()):
        lines.append(
            f"- {family}: {fam_stats['trades']} trades, gross PF {fam_stats['gross_pf']:.3f}, "
            f"avg {fam_stats['avg_gross_pips']:.2f} pips"
        )
    lines.append("")

    lines.extend(["## Sample trades (first 10 filled)", ""])
    for trade in filled[:10]:
        side = "BUY" if trade.event.direction == 1 else "SELL"
        lines.append(
            f"- {trade.event.datetime_utc.date()} {trade.event.currency} {trade.event.family} "
            f"→ {side} {trade.event.pair} | gross {trade.gross_pips:+.1f} pips"
        )

    lines.extend(
        [
            "",
            "## Accounting notes",
            "- Entry: M15 open at or after entry time (Dukascopy M1 resampled).",
            "- Exit: M15 close at or before exit time.",
            "- Production NewsChecker / live faireconomy parser not used.",
            "- No parameter sweeps; single contract parameter set.",
            "",
            "## Next step",
        ]
    )
    if verdict == "GROSS_PASS" and net_verdict == "KEEP":
        lines.append("Proceed to paper-shadow monitoring design; do not optimize parameters.")
    elif verdict == "GROSS_PASS":
        lines.append("Gross passed but net/OOS failed. Lane falsified after costs or OOS gates.")
    elif verdict == "BLOCKED":
        lines.append("Improve OHLC cache coverage or narrow window; re-run before strategy claims.")
    else:
        lines.append("Post-release surprise drift falsified at gross stage. Do not tune. Next plan item.")

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
    eligible: int,
    result_doc: str,
    failure_reason: str,
) -> None:
    row = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lane": "events",
        "hypothesis": "post-release surprise drift (30m entry, 4h hold)",
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
        "eligible_events": eligible,
    }
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def run_backtest(
    calendar_path: Path,
    start: datetime,
    end: datetime,
    cache_dir: Path,
) -> dict[str, Any]:
    events = load_drift_events(calendar_path, start, end)
    logger.info("eligible_events", extra={"count": len(events)})
    trades, skip_counts = simulate_trades(events, cache_dir)
    stats = trade_stats(trades, cost_pips=0.0)
    splits = is_oos_stats(trades, cost_pips=ROUND_TRIP_COST_PIPS)
    concentration = year_concentration(trades)
    families = family_breakdown(trades)
    max_year_share = max(concentration.values()) if concentration else 0.0
    verdict, reason = determine_verdict(
        stats,
        max_year_share,
        skip_counts.get("missing_ohlc", 0),
        len(events),
    )
    net_stats = trade_stats([t for t in trades if not t.skipped_reason], ROUND_TRIP_COST_PIPS)
    if verdict == "GROSS_PASS":
        net_verdict, net_reason = determine_net_verdict(verdict, splits)
    else:
        net_verdict, net_reason = verdict, reason
    return {
        "events": events,
        "trades": trades,
        "skip_counts": skip_counts,
        "stats": stats,
        "net_stats": net_stats,
        "splits": splits,
        "concentration": concentration,
        "families": families,
        "verdict": verdict,
        "reason": reason,
        "net_verdict": net_verdict,
        "net_reason": net_reason,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Post-release event drift gross falsifier")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2025-04-07")
    parser.add_argument("--calendar", default=str(DEFAULT_PINNED_CSV))
    parser.add_argument(
        "--output",
        default="docs/research/events/EVENT_DRIFT_RESULTS_2026-06-19.md",
    )
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument(
        "--ledger",
        default="research/new_edge/research_ledger.jsonl",
    )
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)
    calendar_path = Path(args.calendar)
    cache_dir = Path(args.cache_dir)
    output_path = Path(args.output)

    command = (
        "python -m research.new_edge.events.post_release_drift_test "
        f"--start {args.start} --end {args.end} "
        f"--calendar {args.calendar} --output {args.output}"
    )

    result = run_backtest(calendar_path, start, end, cache_dir)
    doc = build_results_doc(
        command=command,
        start=args.start,
        end=args.end,
        calendar_path=calendar_path,
        eligible_events=len(result["events"]),
        trades=result["trades"],
        skip_counts=result["skip_counts"],
        stats=result["stats"],
        splits=result["splits"],
        concentration=result["concentration"],
        families=result["families"],
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
        eligible=len(result["events"]),
        result_doc=f"docs/research/events/EVENT_DRIFT_CONTRACT_2026-06-19.md + {args.output}",
        failure_reason=result["net_reason"] if ledger_status != "KEEP" else "N/A",
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
