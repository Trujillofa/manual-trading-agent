"""Forward paper-shadow for ETR primary-scenario zone approaches.

Prospective evidence only — not a historical backtest of ETR. Each poll:
1. Append a poll snapshot to logs/etr_shadow_polls.jsonl
2. Open a shadow event when price enters the primary activation zone
3. Track MFE/MAE from successive poll prices
4. Resolve on TP1 touch, invalidation touch, bias flip, or horizon timeout
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.etr.models import EtrChange, EtrReport
from src.scanner.state import _logs_dir

logger = logging.getLogger(__name__)

DEFAULT_HORIZON_HOURS = 24.0


def shadow_polls_path() -> Path:
    return _logs_dir() / "etr_shadow_polls.jsonl"


def shadow_events_path() -> Path:
    return _logs_dir() / "etr_shadow_events.jsonl"


def shadow_open_path() -> Path:
    return _logs_dir() / "etr_shadow_open.json"


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def _load_open_events() -> dict[str, dict[str, Any]]:
    path = shadow_open_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(k): v for k, v in payload.items() if isinstance(v, dict)}


def _save_open_events(events: dict[str, dict[str, Any]]) -> None:
    path = shadow_open_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(events, indent=2), encoding="utf-8")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _event_key(asset: str, direction: str, zone: str) -> str:
    return f"{asset}|{_norm(direction)}|{zone}"


def _norm(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _zone_key(report: EtrReport) -> str:
    primary = report.primary
    if primary is None or primary.activation_zone is None:
        return ""
    return primary.activation_zone.format()


def _direction(report: EtrReport) -> str:
    if report.primary is None:
        return ""
    return report.primary.direction or ""


def _poll_snapshot(report: EtrReport, now_iso: str, changes: list[EtrChange]) -> dict[str, Any]:
    primary = report.primary
    return {
        "ts": now_iso,
        "asset": report.asset,
        "price": report.price,
        "bias": report.bias,
        "estado": report.estado,
        "context_score": report.context_score,
        "price_in_primary_zone": report.price_in_primary_zone(),
        "primary_direction": primary.direction if primary else None,
        "primary_zone": primary.activation_zone.format() if primary and primary.activation_zone else None,
        "primary_invalidation": primary.invalidation if primary else None,
        "primary_tp1": primary.tp1 if primary else None,
        "primary_tp2": primary.tp2 if primary else None,
        "primary_status": primary.status if primary else None,
        "changes": [c.field for c in changes],
    }


def _open_event(report: EtrReport, now_iso: str) -> dict[str, Any] | None:
    if report.price is None or report.primary is None:
        return None
    if report.primary.activation_zone is None:
        return None
    direction = report.primary.direction or "Unknown"
    zone = report.primary.activation_zone.format()
    return {
        "id": uuid.uuid4().hex[:12],
        "asset": report.asset,
        "opened_at": now_iso,
        "entry_price": report.price,
        "direction": direction,
        "zone": zone,
        "invalidation": report.primary.invalidation,
        "tp1": report.primary.tp1,
        "tp2": report.primary.tp2,
        "bias_at_open": report.bias,
        "score_at_open": report.context_score,
        "estado_at_open": report.estado,
        "high_water": report.price,
        "low_water": report.price,
        "polls": 1,
        "status": "open",
        "key": _event_key(report.asset, direction, zone),
    }


def _mfe_mae(event: dict[str, Any]) -> tuple[float | None, float | None]:
    entry = event.get("entry_price")
    high = event.get("high_water")
    low = event.get("low_water")
    direction = _norm(str(event.get("direction") or ""))
    if entry is None or high is None or low is None:
        return None, None
    try:
        entry_f = float(entry)
        high_f = float(high)
        low_f = float(low)
    except (TypeError, ValueError):
        return None, None
    if "baj" in direction or "sell" in direction or "bear" in direction:
        # Short: MFE = entry - low, MAE = high - entry
        return entry_f - low_f, high_f - entry_f
    # Long default
    return high_f - entry_f, entry_f - low_f


def _resolve_reason(
    event: dict[str, Any],
    report: EtrReport,
    *,
    now: datetime,
    horizon_hours: float,
) -> str | None:
    price = report.price
    if price is None:
        return None
    direction = _norm(str(event.get("direction") or ""))
    is_short = "baj" in direction or "sell" in direction or "bear" in direction
    tp1 = event.get("tp1")
    invalidation = event.get("invalidation")

    try:
        if tp1 is not None:
            tp1_f = float(tp1)
            if is_short and price <= tp1_f:
                return "hit_tp1"
            if not is_short and price >= tp1_f:
                return "hit_tp1"
        if invalidation is not None:
            inv_f = float(invalidation)
            if is_short and price >= inv_f:
                return "hit_invalidation"
            if not is_short and price <= inv_f:
                return "hit_invalidation"
    except (TypeError, ValueError):
        pass

    # Bias flipped against primary direction
    bias = _norm(report.bias)
    if bias and direction:
        if "baj" in direction and "alc" in bias:
            return "bias_flip"
        if "alc" in direction and "baj" in bias:
            return "bias_flip"

    opened = _parse_iso(str(event.get("opened_at") or ""))
    if opened is not None:
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=UTC)
        if now - opened >= timedelta(hours=horizon_hours):
            return "expired"

    return None


def process_shadow_for_report(
    report: EtrReport,
    *,
    changes: list[EtrChange],
    now_iso: str,
    horizon_hours: float = DEFAULT_HORIZON_HOURS,
    seed: bool = False,
) -> dict[str, Any]:
    """Update poll log + open/resolve shadow events for one asset report."""
    summary: dict[str, Any] = {"opened": 0, "resolved": 0, "updated": 0}

    _append_jsonl(shadow_polls_path(), _poll_snapshot(report, now_iso, changes))

    open_events = _load_open_events()
    now = _parse_iso(now_iso) or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    # Update / resolve existing open events for this asset
    still_open: dict[str, dict[str, Any]] = {}
    for key, event in open_events.items():
        if event.get("asset") != report.asset:
            still_open[key] = event
            continue
        if report.price is not None:
            try:
                px = float(report.price)
                event["high_water"] = max(float(event.get("high_water", px)), px)
                event["low_water"] = min(float(event.get("low_water", px)), px)
            except (TypeError, ValueError):
                pass
        event["polls"] = int(event.get("polls") or 0) + 1
        event["last_price"] = report.price
        event["last_seen_at"] = now_iso

        reason = _resolve_reason(event, report, now=now, horizon_hours=horizon_hours)
        if reason:
            mfe, mae = _mfe_mae(event)
            closed = {
                **event,
                "status": reason,
                "closed_at": now_iso,
                "exit_price": report.price,
                "mfe": mfe,
                "mae": mae,
            }
            _append_jsonl(shadow_events_path(), closed)
            summary["resolved"] += 1
            logger.info(
                "ETR shadow resolved %s %s reason=%s mfe=%s mae=%s",
                report.asset,
                event.get("id"),
                reason,
                mfe,
                mae,
            )
        else:
            still_open[key] = event
            summary["updated"] += 1

    # Open only on explicit zone-entry edge (diff). Re-entry on restart / in-zone
    # without an edge is intentionally disabled so we never open every poll.
    # `seed` is accepted for call-site clarity but does not open events.
    _ = seed
    entered = any(c.field == "price_in_primary_zone" and c.new == "yes" for c in changes)
    zone = _zone_key(report)
    direction = _direction(report)
    key = _event_key(report.asset, direction, zone) if zone and direction else ""

    already_open = any(
        e.get("asset") == report.asset and e.get("status") == "open"
        for e in still_open.values()
    )

    should_open = bool(entered and key and not already_open)

    if should_open:
        event = _open_event(report, now_iso)
        if event is not None:
            still_open[event["key"]] = event
            summary["opened"] += 1
            logger.info(
                "ETR shadow opened %s id=%s dir=%s zone=%s entry=%s",
                report.asset,
                event["id"],
                event["direction"],
                event["zone"],
                event["entry_price"],
            )

    _save_open_events(still_open)
    return summary


def shadow_summary() -> dict[str, Any]:
    """Aggregate closed shadow events for a quick CLI readout."""
    path = shadow_events_path()
    open_events = _load_open_events()
    closed: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                closed.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    by_reason: dict[str, int] = {}
    by_asset: dict[str, int] = {}
    mfe_vals: list[float] = []
    for ev in closed:
        reason = str(ev.get("status") or "unknown")
        by_reason[reason] = by_reason.get(reason, 0) + 1
        asset = str(ev.get("asset") or "?")
        by_asset[asset] = by_asset.get(asset, 0) + 1
        if isinstance(ev.get("mfe"), (int, float)):
            mfe_vals.append(float(ev["mfe"]))

    hit_tp = by_reason.get("hit_tp1", 0)
    hit_inv = by_reason.get("hit_invalidation", 0)
    decided = hit_tp + hit_inv
    return {
        "open": len(open_events),
        "closed": len(closed),
        "by_reason": by_reason,
        "by_asset": by_asset,
        "tp1_vs_inval": {
            "hit_tp1": hit_tp,
            "hit_invalidation": hit_inv,
            "win_rate": (hit_tp / decided) if decided else None,
        },
        "mean_mfe": (sum(mfe_vals) / len(mfe_vals)) if mfe_vals else None,
    }


def format_shadow_summary() -> str:
    s = shadow_summary()
    lines = [
        "ETR forward shadow (prospective — not a historical backtest)",
        f"  open events: {s['open']}",
        f"  closed events: {s['closed']}",
    ]
    if s["by_reason"]:
        reasons = ", ".join(f"{k}={v}" for k, v in sorted(s["by_reason"].items()))
        lines.append(f"  outcomes: {reasons}")
    wr = s["tp1_vs_inval"].get("win_rate")
    if wr is not None:
        lines.append(
            f"  TP1 vs invalidation: {s['tp1_vs_inval']['hit_tp1']}/"
            f"{s['tp1_vs_inval']['hit_tp1'] + s['tp1_vs_inval']['hit_invalidation']} "
            f"({wr:.0%} hit TP1 first)"
        )
    if s["mean_mfe"] is not None:
        lines.append(f"  mean MFE (price units): {s['mean_mfe']:.4g}")
    if s["closed"] == 0:
        lines.append("  (need zone-entry events + time to resolve)")
    return "\n".join(lines)
