"""Scan telemetry payload builders and aggregators."""

from __future__ import annotations

from typing import TypedDict, cast


class ScanTelemetrySummary(TypedDict):
    scans: int
    mtf_alignments: int
    aligned_pending_breakout: int
    entries: int
    blockers: dict[str, int]


def _build_scan_telemetry_payload(
    *,
    ts: str,
    scan_run_id: str,
    pair: str,
    state: str,
    direction: str | None,
    aligned: bool,
    breakout_pending: bool,
    entry_triggered: bool,
    bars_aligned: int | None,
    confirm_bars: int | None,
    within_confirm_window: bool | None,
    spread_pips: float | None,
    max_spread_pips: float | None,
    spread_source: str | None,
    adx_1h: float | None,
    is_ranging: bool | None,
    rsi_1h: float | None,
    rsi_30m: float | None,
    rsi_15m: float | None,
    no_trade_reasons: list[str],
    is_shadow: bool = False,
) -> dict[str, object]:
    reason_text = " | ".join(no_trade_reasons).lower()
    blockers = {
        "adx_trending": "trending market" in reason_text,
        "spread_unavailable_or_too_wide": "spread unavailable/too wide" in reason_text,
        "session": "outside allowed session" in reason_text,
        "news": "blocked by high-impact news" in reason_text,
        "active_signal": "active signal not yet invalidated" in reason_text,
        "confirmation_expired": "confirmation window expired" in reason_text,
        "breakout_unconfirmed": "breakout" in reason_text and "not confirmed" in reason_text,
        "rsi_ma_gate": "rsi-ma" in reason_text and "gate" in reason_text,
        "data_unavailable": state == "data_unavailable",
    }
    return {
        "ts": ts,
        "kind": "scan_telemetry",
        "scan_run_id": scan_run_id,
        "pair": pair,
        "state": state,
        "direction": direction,
        "is_shadow": is_shadow,
        "counts": {
            "scan": 1,
            "mtf_alignment": int(aligned),
            "aligned_pending_breakout": int(state == "aligned_pending_breakout"),
            "entry": int(entry_triggered),
        },
        "blockers": blockers,
        "context": {
            "bars_aligned": bars_aligned,
            "confirm_bars": confirm_bars,
            "within_confirm_window": within_confirm_window,
            "breakout_pending": breakout_pending,
            "spread_pips": spread_pips,
            "max_spread_pips": max_spread_pips,
            "spread_source": spread_source,
            "adx_1h": adx_1h,
            "is_ranging": is_ranging,
            "rsi_1h": rsi_1h,
            "rsi_30m": rsi_30m,
            "rsi_15m": rsi_15m,
        },
        "reasons": no_trade_reasons,
    }


def _aggregate_scan_telemetry(records: list[dict[str, object]]) -> dict[str, ScanTelemetrySummary]:
    per_pair: dict[str, ScanTelemetrySummary] = {}
    for rec in records:
        pair = str(rec.get("pair", "unknown"))
        counts = cast(dict[str, object], rec.get("counts", {}))
        blockers = cast(dict[str, object], rec.get("blockers", {}))
        summary = per_pair.setdefault(
            pair,
            {
                "scans": 0,
                "mtf_alignments": 0,
                "aligned_pending_breakout": 0,
                "entries": 0,
                "blockers": {},
            },
        )
        summary["scans"] += int(cast(int, counts.get("scan", 0)))
        summary["mtf_alignments"] += int(cast(int, counts.get("mtf_alignment", 0)))
        summary["aligned_pending_breakout"] += int(
            cast(int, counts.get("aligned_pending_breakout", 0))
        )
        summary["entries"] += int(cast(int, counts.get("entry", 0)))
        for blocker, active in blockers.items():
            if active:
                summary["blockers"][blocker] = summary["blockers"].get(blocker, 0) + 1
    return per_pair
