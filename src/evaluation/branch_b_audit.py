"""Wire Branch B scan telemetry to DecisionSignal audit rows."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from src.evaluation.branch_b_decision_signal import (
    BranchBScanContext,
    BranchBScanState,
    build_branch_b_decision_signal,
)
from src.evaluation.decision_signal_schema import record_decision_signal
from src.scanner.state import _audit_log_path

logger = logging.getLogger(__name__)

_RECORDABLE_STATES = frozenset({"entry", "watch", "aligned_pending_breakout", "blocked"})


def _latest_bar_ts_utc(data: Any) -> str | None:
    if data is None or getattr(data, "empty", True):
        return None

    index_ts = data.index[-1]
    if hasattr(index_ts, "to_pydatetime"):
        bar_ts = index_ts.to_pydatetime()
    elif isinstance(index_ts, datetime):
        bar_ts = index_ts
    else:
        return None

    bar_ts = bar_ts.replace(tzinfo=UTC) if bar_ts.tzinfo is None else bar_ts.astimezone(UTC)

    text = bar_ts.strftime("%Y-%m-%dT%H:%M:%S")
    if bar_ts.microsecond:
        frac = f"{bar_ts.microsecond:06d}".rstrip("0")
        text = f"{text}.{frac}"
    return f"{text}Z"


def _ohlc_block_from_df(data: Any) -> dict[str, Any]:
    latest_bar_ts = _latest_bar_ts_utc(data)
    if latest_bar_ts is None:
        return {"status": "missing"}
    return {
        "status": "available",
        "bar_count": len(data),
        "latest_bar_ts": latest_bar_ts,
    }


def _derive_session_name(now_utc: datetime) -> str:
    hour = now_utc.hour
    if 13 <= hour < 17:
        return "london_ny_overlap"
    if 8 <= hour < 17:
        return "london"
    if 13 <= hour < 22:
        return "new_york"
    if 0 <= hour < 9:
        return "asia"
    return "off_hours"


def record_branch_b_scan_decision_signal(
    *,
    ts: datetime,
    pair: str,
    scan_run_id: str,
    telemetry_state: str,
    direction: str | None,
    telemetry_payload: dict[str, object],
    data_1h: Any = None,
    data_30m: Any = None,
    data_15m: Any = None,
    signal_reasons: list[str] | None = None,
    no_trade_reasons: list[str] | None = None,
    signal_id: str | None = None,
    entry_ref_price: float | None = None,
    tp_pips: float | None = None,
    sl_pips: float | None = None,
    confidence: float | None = None,
    profile: str | None = None,
    missing_timeframes: list[str] | None = None,
    distance: float | None = None,
    breakout_pending: bool | None = None,
    bars_aligned: int | None = None,
    confirm_bars: int | None = None,
    news_blocked: bool | None = None,
    news_summary: str | None = None,
    is_shadow: bool = False,
    audit_path: Path | None = None,
) -> bool:
    """Build and append a decision_signal row for recordable Branch B scan states.

    Returns True when a row was appended. Failures are logged and do not raise.
    """
    if telemetry_state not in _RECORDABLE_STATES:
        return False
    if direction not in ("BUY", "SELL"):
        return False

    context_section = cast(dict[str, Any], telemetry_payload.get("context", {}))
    blockers = cast(dict[str, bool], telemetry_payload.get("blockers", {}))
    telemetry_reasons = cast(list[str], telemetry_payload.get("reasons", []))

    resolved_signal_reasons = signal_reasons
    resolved_no_trade_reasons = no_trade_reasons or telemetry_reasons

    context: BranchBScanContext = {
        "ts": ts,
        "pair": pair,
        "direction": cast(Any, direction),
        "scan_state": cast(BranchBScanState, telemetry_state),
        "scan_run_id": scan_run_id,
        "blockers": blockers,
        "ohlc_m15": cast(Any, _ohlc_block_from_df(data_15m)),
        "ohlc_m30": cast(Any, _ohlc_block_from_df(data_30m)),
        "ohlc_h1": cast(Any, _ohlc_block_from_df(data_1h)),
        "session_name": _derive_session_name(ts),
        "is_shadow": is_shadow,
    }

    if signal_id:
        context["signal_id"] = signal_id
    if resolved_signal_reasons:
        context["signal_reasons"] = resolved_signal_reasons
    if resolved_no_trade_reasons:
        context["no_trade_reasons"] = resolved_no_trade_reasons

    for field, key in (
        (confidence, "confidence"),
        (profile, "profile"),
        (distance, "distance"),
        (breakout_pending, "breakout_pending"),
        (bars_aligned, "bars_aligned"),
        (confirm_bars, "confirm_bars"),
        (entry_ref_price, "entry_ref_price"),
        (tp_pips, "tp_pips"),
        (sl_pips, "sl_pips"),
    ):
        if field is not None:
            context[key] = field  # type: ignore[literal-required]

    if missing_timeframes is not None:
        context["missing_timeframes"] = missing_timeframes

    spread_pips = context_section.get("spread_pips")
    if isinstance(spread_pips, (int, float)):
        context["spread_pips"] = float(spread_pips)
    spread_source = context_section.get("spread_source")
    if isinstance(spread_source, str):
        context["spread_source"] = spread_source

    for field, key in (
        (context_section.get("rsi_1h"), "rsi_1h"),
        (context_section.get("rsi_30m"), "rsi_30m"),
        (context_section.get("rsi_15m"), "rsi_15m"),
        (context_section.get("adx_1h"), "adx_1h"),
    ):
        if isinstance(field, (int, float)):
            context[key] = float(field)  # type: ignore[literal-required]

    if news_blocked is not None:
        context["news_blocked"] = news_blocked
        context["news_summary"] = news_summary or ("blocked" if news_blocked else "clear")

    try:
        payload = build_branch_b_decision_signal(context)
        record_decision_signal(payload, path=audit_path or _audit_log_path())
        return True
    except Exception as exc:
        logger.warning(
            "decision_signal audit append skipped for %s state=%s: %s",
            pair,
            telemetry_state,
            exc,
        )
        return False
