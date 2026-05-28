"""Dashboard and healthcheck reporting."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import cast

from src.config import get_settings
from src.data.fetcher import DataFetcher
from src.scanner.state import (
    SCAN_HEALTH_MAX_AGE_SECONDS,
    TELEGRAM_HEARTBEAT_MAX_AGE_SECONDS,
    _audit_log_path,
    _path_age_seconds,
    _scan_log_path,
    _telegram_heartbeat_path,
)
from src.scanner.telemetry import _aggregate_scan_telemetry


def _healthcheck_status(now_utc: datetime | None = None) -> tuple[bool, str]:
    settings = get_settings()
    current_time = now_utc or datetime.now(UTC)

    scan_age = _path_age_seconds(_scan_log_path(), current_time)
    if scan_age is None:
        return False, "scan log missing"
    if scan_age > SCAN_HEALTH_MAX_AGE_SECONDS:
        return False, f"scan log stale ({scan_age:.0f}s old)"

    if settings.telegram.enabled and settings.telegram.is_configured:
        telegram_age = _path_age_seconds(_telegram_heartbeat_path(), current_time)
        if telegram_age is None:
            return False, "telegram heartbeat missing"
        if telegram_age > TELEGRAM_HEARTBEAT_MAX_AGE_SECONDS:
            return False, f"telegram heartbeat stale ({telegram_age:.0f}s old)"

    return True, "ok"


async def run_healthcheck() -> None:
    ok, message = _healthcheck_status()
    print(message)
    if not ok:
        raise SystemExit(1)


async def run_dashboard(days: int) -> None:
    """Show signal dashboard: entries, block reasons, paper P&L tracking."""
    audit_path = _audit_log_path()
    if not audit_path.exists():
        print("No signal audit log found.")
        return

    cutoff = datetime.now(UTC) - timedelta(days=days)
    entries: list[dict[str, object]] = []
    blocked: list[dict[str, object]] = []
    aligned: list[dict[str, object]] = []
    watched: list[dict[str, object]] = []
    telemetry: list[dict[str, object]] = []

    with audit_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_str = rec.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_str)
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                continue
            if rec.get("kind") == "scan_telemetry":
                telemetry.append(cast(dict[str, object], rec))
                continue
            state = rec.get("state", "")
            if state == "entry":
                entries.append(rec)
            elif state == "blocked":
                blocked.append(rec)
            elif state == "aligned_pending_breakout":
                aligned.append(rec)
            elif state == "watch":
                watched.append(rec)

    print(f"=== Signal Dashboard (last {days} days) ===\n")

    # Summary counts
    print(f"Entry signals:    {len(entries)}")
    print(f"Blocked signals:  {len(blocked)}")
    print(f"Aligned pending:  {len(aligned)}")
    print(f"Watch list:       {len(watched)}")
    print()

    if telemetry:
        print("--- SCAN TELEMETRY ---")
        telemetry_summary = _aggregate_scan_telemetry(telemetry)
        print(
            f"{'Pair':<10} {'Scans':>5} {'Align':>5} {'Pending':>7} {'Entries':>7} {'Top blocker':>28}"
        )
        print("-" * 72)
        for pair, summary in sorted(
            telemetry_summary.items(),
            key=lambda item: (-item[1]["mtf_alignments"], item[0]),
        )[:15]:
            blocker_label = "-"
            if summary["blockers"]:
                blocker_label, blocker_count = max(
                    summary["blockers"].items(),
                    key=lambda item: item[1],
                )
                blocker_label = f"{blocker_label} ({blocker_count})"
            print(
                f"{pair:<10} {summary['scans']:>5} {summary['mtf_alignments']:>5} {summary['aligned_pending_breakout']:>7} {summary['entries']:>7} {blocker_label:>28}"
            )
        print()

    # Entry signals detail
    if entries:
        print("--- ENTRY SIGNALS ---")
        print(
            f"{'Timestamp':<22} {'Pair':<10} {'Dir':<5} {'Entry':>10} {'TP':>10} {'SL':>10} {'RSI 1h':>7} {'RSI 30m':>8} {'RSI 15m':>8}"
        )
        print("-" * 95)
        for e in entries:
            ts_display = str(e.get("ts", ""))[:19]
            pair_name = str(e.get("pair", ""))
            direction_name = str(e.get("direction", ""))
            entry_value = float(cast(float, e.get("entry", 0.0)))
            tp_value = float(cast(float, e.get("tp", 0.0)))
            sl_value = float(cast(float, e.get("sl", 0.0)))
            rsi_1h_value = float(cast(float, e.get("rsi_1h", 0.0)))
            rsi_30m_value = float(cast(float, e.get("rsi_30m", 0.0)))
            rsi_15m_value = float(cast(float, e.get("rsi_15m", 0.0)))
            print(
                f"{ts_display:<22} {pair_name:<10} {direction_name:<5} "
                f"{entry_value:>10.5f} {tp_value:>10.5f} {sl_value:>10.5f} "
                f"{rsi_1h_value:>7.1f} {rsi_30m_value:>8.1f} {rsi_15m_value:>8.1f}"
            )

        # Paper P&L estimation using current price
        print("\n--- PAPER P&L (mark-to-market) ---")
        fetcher = DataFetcher()
        pairs_seen = {str(e.get("pair", "")) for e in entries}
        current_prices: dict[str, float] = {}
        for pair in pairs_seen:
            if not pair:
                continue
            try:
                symbol = pair.replace("/", "")
                df = fetcher.fetch(symbol, period="1d", interval="15m")
                if not df.empty:
                    current_prices[pair] = float(df["close"].iloc[-1])
            except Exception:
                pass

        total_paper_pnl = 0.0
        print(
            f"{'Timestamp':<22} {'Pair':<10} {'Dir':<5} {'Entry':>10} {'Current':>10} {'P&L pips':>10} {'Status':>10}"
        )
        print("-" * 82)
        for e in entries:
            pair = str(e.get("pair", ""))
            direction = str(e.get("direction", ""))
            entry_px = float(cast(float, e.get("entry", 0.0)))
            tp_px = float(cast(float, e.get("tp", 0.0)))
            sl_px = float(cast(float, e.get("sl", 0.0)))
            pip_size = 0.01 if "JPY" in pair else 0.0001
            current = current_prices.get(pair)
            ts_display = str(e.get("ts", ""))[:19]

            if current is None:
                print(
                    f"{ts_display:<22} {pair:<10} {direction:<5} {entry_px:>10.5f} {'N/A':>10} {'N/A':>10} {'no data':>10}"
                )
                continue

            if direction == "BUY":
                pnl_pips = (current - entry_px) / pip_size
                hit_tp = current >= tp_px
                hit_sl = current <= sl_px
            else:
                pnl_pips = (entry_px - current) / pip_size
                hit_tp = current <= tp_px
                hit_sl = current >= sl_px

            status = "TP HIT" if hit_tp else ("SL HIT" if hit_sl else "OPEN")
            total_paper_pnl += pnl_pips
            print(
                f"{ts_display:<22} {pair:<10} {direction:<5} "
                f"{entry_px:>10.5f} {current:>10.5f} {pnl_pips:>+10.1f} {status:>10}"
            )
        print(f"\nTotal paper P&L: {total_paper_pnl:+.1f} pips")
    else:
        print("No entry signals in this period.")

    # Block reason breakdown
    if blocked:
        print("\n--- BLOCK REASONS ---")
        reason_counts: dict[str, int] = {}
        for b in blocked:
            reasons = cast(list[str], b.get("reasons", []))
            for reason in reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1])[:15]:
            print(f"  {count:>4}x  {reason}")

    # Pairs with most aligned-pending (closest to triggering)
    if aligned:
        print("\n--- MOST ACTIVE PAIRS (aligned pending breakout) ---")
        pair_counts: dict[str, int] = {}
        for a in aligned:
            p = str(a.get("pair", "unknown"))
            pair_counts[p] = pair_counts.get(p, 0) + 1
        for pair, count in sorted(pair_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"  {count:>4}x  {pair}")

    if watched:
        print("\n--- MOST WATCHED PAIRS ---")
        watch_counts: dict[str, int] = {}
        for w in watched:
            p = str(w.get("pair", "unknown"))
            watch_counts[p] = watch_counts.get(p, 0) + 1
        for pair, count in sorted(watch_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"  {count:>4}x  {pair}")

    print()
