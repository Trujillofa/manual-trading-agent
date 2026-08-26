"""Honest Plan NY action from the live V2 evaluator.

Hermes / ETR / EMA may supply context. They must not set the action.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import datetime
from typing import Any, Literal

from src.briefing.models import NyPlan

logger = logging.getLogger(__name__)

DeskAction = Literal["STAND_ASIDE", "WATCH", "ENTER_ONLY_IF"]

ACTION_LABELS: dict[DeskAction, str] = {
    "STAND_ASIDE": "no operar",
    "WATCH": "esperar",
    "ENTER_ONLY_IF": "entrar solo si V2",
}

_SESSION_BLOCK = "outside allowed session"


def desk_action_from_entry(
    *,
    fired: bool,
    aligned: bool,
    news_blocked: bool,
    session_blocked: bool = False,
) -> DeskAction:
    """Map a pure evaluate_entry result to a desk action.

    STAND_ASIDE is the default, including news lockout and session close.
    WATCH requires MTF alignment without a fire.
    ENTER_ONLY_IF requires the live V2 entry to fire.
    """
    if news_blocked or session_blocked:
        return "STAND_ASIDE"
    if fired:
        return "ENTER_ONLY_IF"
    if aligned:
        return "WATCH"
    return "STAND_ASIDE"


def desk_action_from_result(
    result: dict[str, Any] | None,
    *,
    news_blocked: bool,
) -> DeskAction:
    if result is None:
        return "STAND_ASIDE"
    reasons = [str(item) for item in (result.get("no_trade_reasons") or [])]
    session_blocked = any(_SESSION_BLOCK in reason for reason in reasons)
    return desk_action_from_entry(
        fired=bool(result.get("fired")),
        aligned=bool(result.get("aligned")),
        news_blocked=news_blocked,
        session_blocked=session_blocked,
    )


_DIRECTIONAL_RE = re.compile(r"(?i)\b(buy|sell|long|short|compra|venta)\b")


def _scrub_directional(text: str) -> str:
    """Drop Hermes prose that still names a trade after V2 overwrote the action."""
    raw = (text or "").strip()
    if not raw:
        return ""
    return "" if _DIRECTIONAL_RE.search(raw) else raw


def apply_desk_action(
    plan: NyPlan | None,
    action: DeskAction,
    *,
    hermes_note: str | None = None,
) -> NyPlan:
    """Overwrite recommendation with the V2-gated action. Keep Hermes HTF/S/R."""
    label = ACTION_LABELS[action]
    note = (hermes_note or "").strip()
    if plan is None:
        return NyPlan(
            available=True,
            recommendation=label,
            action=action,
            htf_trend="indefinido",
            honesty=note[:140],
        )
    if not plan.available:
        reason = plan.unavailable_reason or note or "Hermes no disponible"
        return NyPlan(
            available=True,
            recommendation=label,
            action=action,
            htf_trend="indefinido",
            honesty=reason[:140],
        )
    honesty = plan.honesty
    if note:
        honesty = f"{honesty} · {note}".strip(" ·") if honesty else note
    return replace(
        plan,
        recommendation=label,
        action=action,
        why=_scrub_directional(plan.why),
        invalidation=_scrub_directional(plan.invalidation),
        honesty=honesty[:140],
    )


def evaluate_desk_decision(
    pair: str,
    frames: dict[str, Any] | None,
    *,
    news_blocked: bool,
    now_utc: datetime | None = None,
    active_signal_state: dict[str, Any] | None = None,
    alignment_state: dict[str, Any] | None = None,
    settings: Any | None = None,
) -> tuple[DeskAction, str | None]:
    """Same evaluate_entry contract as the scan. Direction only when V2 fires."""
    if not frames:
        return "STAND_ASIDE", None
    data_1h = frames.get("1h")
    data_30m = frames.get("30m")
    data_15m = frames.get("15m")
    if data_1h is None or data_30m is None or data_15m is None:
        return "STAND_ASIDE", None
    empty = (
        getattr(data_1h, "empty", False)
        or getattr(data_30m, "empty", False)
        or getattr(data_15m, "empty", False)
    )
    if empty:
        return "STAND_ASIDE", None
    try:
        from src.config.settings import get_settings
        from src.scanner.evaluator import evaluate_entry
        from src.scanner.live_overrides import atr_from_15m, build_live_entry_overrides

        cfg = settings or get_settings()
        active = dict(active_signal_state or {})
        align = dict(alignment_state or {})
        overrides = build_live_entry_overrides(pair, atr=atr_from_15m(data_15m), settings=cfg)
        result = evaluate_entry(
            pair,
            data_1h,
            data_30m,
            data_15m,
            active_signal_state=active,
            alignment_state=align,
            now_utc=now_utc,
            news_blocked=news_blocked,
            spread_filter_enabled=overrides.get("spread_filter_enabled"),
            overrides=overrides or None,
        )
    except Exception:
        logger.exception("evaluate_desk_decision failed for %s", pair)
        return "STAND_ASIDE", None
    action = desk_action_from_result(result, news_blocked=news_blocked)
    direction = result.get("direction") if isinstance(result, dict) else None
    if action != "ENTER_ONLY_IF" or direction not in {"BUY", "SELL"}:
        return action, None
    return action, str(direction)


def evaluate_desk_action(
    pair: str,
    frames: dict[str, Any] | None,
    *,
    news_blocked: bool,
    now_utc: datetime | None = None,
    active_signal_state: dict[str, Any] | None = None,
    alignment_state: dict[str, Any] | None = None,
    settings: Any | None = None,
) -> DeskAction:
    """Run the same evaluate_entry contract as the 15-minute scan."""
    action, _direction = evaluate_desk_decision(
        pair,
        frames,
        news_blocked=news_blocked,
        now_utc=now_utc,
        active_signal_state=active_signal_state,
        alignment_state=alignment_state,
        settings=settings,
    )
    return action
