"""Pure builder for Branch B DecisionSignal payloads (no I/O).

Maps existing scanner scan/alert context into forex-decision-signal-v1 records.
Does not append to logs or wire into the scanner loop.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal, NotRequired, TypedDict
from uuid import UUID, uuid4

from src.evaluation.decision_signal_schema import (
    ENGINE_VERSION,
    KIND_DECISION_SIGNAL,
    normalize_decision_symbol,
    validate_decision_signal,
)

BranchBScanState = Literal[
    "entry",
    "watch",
    "aligned_pending_breakout",
    "blocked",
    "neutral",
    "data_unavailable",
]

Action = Literal["watch", "avoid", "alert"]
Direction = Literal["BUY", "SELL"]
FieldStatus = Literal[
    "available",
    "missing",
    "stale",
    "fallback",
    "partial",
    "not_supported",
    "fetch_failed",
]

# Compact EURUSD → EUR/USD for historical Branch B helpers; multi-asset uses registry.
_COMPACT_PAIR_RE = re.compile(r"^[A-Z]{6}$")

_SCAN_STATE_TO_ACTION: dict[BranchBScanState, Action] = {
    "entry": "alert",
    "watch": "watch",
    "aligned_pending_breakout": "watch",
    "blocked": "avoid",
}


class OhlcBlockContext(TypedDict, total=False):
    bar_count: int
    latest_bar_ts: datetime | str
    gap_flags: list[str]
    status: FieldStatus


class BranchBScanContext(TypedDict):
    ts: datetime | str
    pair: str
    direction: Direction
    scan_state: BranchBScanState
    scan_run_id: NotRequired[str]
    signal_id: NotRequired[str | UUID]
    signal_reasons: NotRequired[list[str]]
    no_trade_reasons: NotRequired[list[str]]
    blockers: NotRequired[dict[str, bool]]
    rsi_1h: NotRequired[float]
    rsi_30m: NotRequired[float]
    rsi_15m: NotRequired[float]
    adx_1h: NotRequired[float]
    spread_pips: NotRequired[float]
    spread_source: NotRequired[str]
    max_spread_pips: NotRequired[float]
    news_blocked: NotRequired[bool]
    news_summary: NotRequired[str]
    session_name: NotRequired[str]
    trading_allowed: NotRequired[bool]
    broker_constraint_codes: NotRequired[list[str]]
    ohlc_m15: NotRequired[OhlcBlockContext]
    ohlc_m30: NotRequired[OhlcBlockContext]
    ohlc_h1: NotRequired[OhlcBlockContext]
    entry_ref_price: NotRequired[float]
    tp_pips: NotRequired[float]
    sl_pips: NotRequired[float]
    confidence: NotRequired[float]
    profile: NotRequired[str]
    breakout_pending: NotRequired[bool]
    bars_aligned: NotRequired[int]
    confirm_bars: NotRequired[int]
    distance: NotRequired[float]
    missing_timeframes: NotRequired[list[str]]
    is_shadow: NotRequired[bool]
    invalidation: NotRequired[str]


class BranchBScanContextError(ValueError):
    """Raised when required Branch B scan context is missing or invalid."""


def normalize_fx_symbol(pair: str) -> str:
    """Normalize instrument/pair labels for Branch B decision signals.

    Accepts registry instrument ids (OIL, NASDAQ, XAU/USD, …) or slash-form FX
    pairs (EUR/USD). Also accepts compact 6-letter FX labels (EURUSD) for
    historical helper callers. Raises ``BranchBScanContextError`` otherwise.
    """
    text = pair.strip().upper()
    # Registry + slash FX via shared helper (no hardcoded multi-asset id list).
    try:
        return normalize_decision_symbol(text)
    except ValueError:
        pass

    # Compact EURUSD only — no hyphenated EUR-USD (treated as junk).
    if _COMPACT_PAIR_RE.fullmatch(text):
        candidate = f"{text[:3]}/{text[3:]}"
        try:
            return normalize_decision_symbol(candidate)
        except ValueError as exc:
            raise BranchBScanContextError(
                f"invalid instrument symbol {pair!r}: must be a registry id "
                f"(e.g. OIL, NASDAQ, XAU/USD) or FX pair like EUR/USD"
            ) from exc

    raise BranchBScanContextError(
        f"invalid instrument symbol {pair!r}: must be a registry id "
        f"(e.g. OIL, NASDAQ, XAU/USD) or FX pair like EUR/USD"
    )


def normalize_utc_timestamp(value: datetime | str) -> datetime:
    """Normalize timestamps to timezone-aware UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise BranchBScanContextError("ts must be a non-empty ISO 8601 timestamp")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    else:
        raise BranchBScanContextError("ts must be datetime or ISO 8601 string")

    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None:
        raise BranchBScanContextError("ts must be timezone-aware ISO 8601 UTC")
    if offset.total_seconds() != 0:
        raise BranchBScanContextError("ts must use UTC (Z or +00:00 offset)")
    return parsed.astimezone(UTC)


def _require_direction(value: object) -> Direction:
    if value not in ("BUY", "SELL"):
        raise BranchBScanContextError("direction must be BUY or SELL")
    return value


def _require_scan_state(value: object) -> BranchBScanState:
    if value not in _SCAN_STATE_TO_ACTION:
        raise BranchBScanContextError(
            f"scan_state must be one of {', '.join(sorted(_SCAN_STATE_TO_ACTION))}"
        )
    return value


def _truncate(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _ohlc_block(
    block: OhlcBlockContext | None,
    *,
    default_status: FieldStatus = "missing",
) -> dict[str, Any]:
    if block is None:
        return {"status": default_status}
    payload: dict[str, Any] = {"status": block.get("status", default_status)}
    if "bar_count" in block:
        payload["bar_count"] = block["bar_count"]
    if "latest_bar_ts" in block:
        latest = block["latest_bar_ts"]
        if isinstance(latest, datetime):
            normalized = normalize_utc_timestamp(latest)
            text = normalized.strftime("%Y-%m-%dT%H:%M:%S")
            if normalized.microsecond:
                frac = f"{normalized.microsecond:06d}".rstrip("0")
                text = f"{text}.{frac}"
            payload["latest_bar_ts"] = f"{text}Z"
        else:
            payload["latest_bar_ts"] = latest
    if "gap_flags" in block:
        payload["gap_flags"] = block["gap_flags"]
    return payload


def _build_data_quality(context: BranchBScanContext) -> dict[str, Any]:
    blocks: dict[str, dict[str, Any]] = {
        "ohlc_m15": _ohlc_block(context.get("ohlc_m15")),
        "ohlc_m30": _ohlc_block(context.get("ohlc_m30")),
        "ohlc_h1": _ohlc_block(context.get("ohlc_h1")),
    }

    spread_pips = context.get("spread_pips")
    if spread_pips is None:
        blocks["spread"] = {"status": "missing"}
    else:
        spread_block: dict[str, Any] = {"status": "available", "value_pips": float(spread_pips)}
        if context.get("spread_source"):
            spread_block["source"] = context["spread_source"]
        blocks["spread"] = spread_block

    news_blocked = context.get("news_blocked")
    if news_blocked is None and context.get("news_summary") is None:
        blocks["news"] = {"status": "missing"}
    else:
        blocks["news"] = {
            "status": "available",
            "blocked": bool(news_blocked),
            "summary": context.get("news_summary") or ("blocked" if news_blocked else "clear"),
        }

    session_name = context.get("session_name")
    blocks["session"] = (
        {"status": "available", "name": session_name}
        if session_name
        else {"status": "missing"}
    )

    trading_allowed = context.get("trading_allowed")
    if trading_allowed is None and not context.get("broker_constraint_codes"):
        blocks["broker_account"] = {"status": "missing"}
    else:
        broker_block: dict[str, Any] = {
            "status": "available",
            "trading_allowed": bool(trading_allowed) if trading_allowed is not None else True,
        }
        if context.get("broker_constraint_codes"):
            broker_block["constraint_codes"] = list(context["broker_constraint_codes"])
        blocks["broker_account"] = broker_block

    limitations: list[str] = []
    for key, block in blocks.items():
        status = block.get("status")
        if status in ("missing", "stale", "fetch_failed", "partial", "fallback"):
            limitations.append(f"{key}: {status}")

    statuses = [str(block.get("status", "missing")) for block in blocks.values()]
    if all(status == "available" for status in statuses):
        overall = "good"
    elif any(status in ("missing", "fetch_failed") for status in statuses):
        overall = "poor" if sum(1 for s in statuses if s in ("missing", "fetch_failed")) >= 2 else "limited"
    elif any(status in ("stale", "partial", "fallback") for status in statuses):
        overall = "usable"
    else:
        overall = "limited"

    return {
        "overall_level": overall,
        "limitations": limitations,
        "blocks": blocks,
    }


def _build_evidence_summary(context: BranchBScanContext, action: Action) -> str:
    direction = context["direction"]
    if action == "alert":
        reasons = context.get("signal_reasons") or []
        if not reasons:
            raise BranchBScanContextError("signal_reasons required for alert scan_state")
        return _truncate("; ".join(reasons), 500)

    if action == "avoid":
        reasons = context.get("no_trade_reasons") or []
        if not reasons:
            raise BranchBScanContextError("no_trade_reasons required for blocked scan_state")
        return _truncate(f"Avoid {direction}: " + "; ".join(reasons), 500)

    parts = [f"{direction} setup forming"]
    missing = context.get("missing_timeframes") or []
    if missing:
        parts.append(f"missing {', '.join(missing)}")
    if context.get("breakout_pending"):
        parts.append("breakout pending")
    distance = context.get("distance")
    if isinstance(distance, (int, float)):
        parts.append(f"gap {float(distance):.1f} RSI points")
    return _truncate("; ".join(parts), 500)


def _build_risk_summary(context: BranchBScanContext) -> str:
    parts: list[str] = []
    spread = context.get("spread_pips")
    if spread is not None:
        parts.append(f"Spread {float(spread):.1f} pips")
    if context.get("news_blocked"):
        parts.append(f"News blocked ({context.get('news_summary') or 'high-impact'})")
    else:
        parts.append("News clear")
    session = context.get("session_name")
    if session:
        parts.append(f"{session.replace('_', ' ')} session")
    if context.get("trading_allowed") is False:
        parts.append("Trading locked")
    return _truncate("; ".join(parts) if parts else "Context incomplete", 300)


def _build_watch_conditions(context: BranchBScanContext, action: Action) -> list[str] | None:
    if action == "avoid":
        return None
    direction = context["direction"]
    conditions: list[str] = []
    if context.get("breakout_pending"):
        conditions.append("Await breakout confirmation")
    missing = context.get("missing_timeframes") or []
    if missing:
        conditions.append(f"Align remaining timeframes: {', '.join(missing)}")
    if direction == "BUY":
        conditions.append("Invalidate on 15m RSI cross below 50")
    else:
        conditions.append("Invalidate on 15m RSI cross above 50")
    if context.get("invalidation"):
        conditions.append(context["invalidation"])
    return conditions or None


def _build_metadata(context: BranchBScanContext) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ("rsi_1h", "rsi_30m", "rsi_15m", "adx_1h", "confidence", "distance"):
        value = context.get(key)
        if isinstance(value, (int, float)):
            metadata[key] = float(value)
    if context.get("profile"):
        metadata["profile"] = context["profile"]
    if context.get("bars_aligned") is not None:
        metadata["bars_aligned"] = context["bars_aligned"]
    if context.get("confirm_bars") is not None:
        metadata["confirm_bars"] = context["confirm_bars"]
    if context.get("breakout_pending") is not None:
        metadata["breakout_pending"] = context["breakout_pending"]
    blockers = context.get("blockers") or {}
    active_blockers = sorted(name for name, active in blockers.items() if active)
    if active_blockers:
        metadata["blocker_codes"] = active_blockers
    if context.get("is_shadow"):
        metadata["is_shadow"] = True
    return metadata


def build_branch_b_decision_signal(context: BranchBScanContext) -> dict[str, Any]:
    """Build a validated DecisionSignal-compatible payload from Branch B scan context.

    Pure function: no log writes, no Telegram, no execution side effects.
    """
    if "ts" not in context:
        raise BranchBScanContextError("ts is required")
    if "pair" not in context:
        raise BranchBScanContextError("pair is required")
    if "direction" not in context:
        raise BranchBScanContextError("direction is required")
    if "scan_state" not in context:
        raise BranchBScanContextError("scan_state is required")

    ts = normalize_utc_timestamp(context["ts"])
    symbol = normalize_fx_symbol(context["pair"])
    direction = _require_direction(context["direction"])
    scan_state = _require_scan_state(context["scan_state"])
    action = _SCAN_STATE_TO_ACTION[scan_state]

    signal_id = context.get("signal_id")
    if signal_id is None:
        resolved_signal_id = uuid4()
    elif isinstance(signal_id, UUID):
        resolved_signal_id = signal_id
    else:
        resolved_signal_id = UUID(str(signal_id))

    payload: dict[str, Any] = {
        "kind": KIND_DECISION_SIGNAL,
        "signal_id": str(resolved_signal_id),
        "ts": ts,
        "symbol": symbol,
        "direction": direction,
        "action": action,
        "source": "branch_b_scan",
        "status": "active",
        "engine_version": ENGINE_VERSION,
        "evidence_summary": _build_evidence_summary(context, action),
        "risk_summary": _build_risk_summary(context),
        "data_quality": _build_data_quality(context),
    }

    if context.get("scan_run_id"):
        payload["source_ref"] = context["scan_run_id"]

    watch_conditions = _build_watch_conditions(context, action)
    if watch_conditions:
        payload["watch_conditions"] = watch_conditions

    metadata = _build_metadata(context)
    if metadata:
        payload["metadata"] = metadata

    if context.get("entry_ref_price") is not None:
        payload["entry_ref_price"] = float(context["entry_ref_price"])
    if context.get("tp_pips") is not None:
        payload["tp_pips"] = float(context["tp_pips"])
    if context.get("sl_pips") is not None:
        payload["sl_pips"] = float(context["sl_pips"])
    if context.get("invalidation"):
        payload["invalidation"] = context["invalidation"]

    validated = validate_decision_signal(payload)
    return validated.model_dump()
