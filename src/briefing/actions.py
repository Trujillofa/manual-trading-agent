"""Honest Plan NY action from the live V2 evaluator.

Hermes / ETR / EMA may supply context. They must not set the action.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal

from src.briefing.models import NyPlan

logger = logging.getLogger(__name__)

DeskAction = Literal["STAND_ASIDE", "WATCH", "ENTER_ONLY_IF"]


@dataclass(frozen=True)
class DeskDecision:
    action: DeskAction
    direction: str | None = None
    reasons: tuple[str, ...] = ()


ACTION_LABELS: dict[DeskAction, str] = {
    "STAND_ASIDE": "no operar",
    "WATCH": "esperar",
    "ENTER_ONLY_IF": "V2 activado en esta muestra (no autorización)",
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
    if fired and aligned:
        return "ENTER_ONLY_IF"
    if fired:
        return "STAND_ASIDE"
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
    v2_reasons: tuple[str, ...] = (),
    v2_direction: str | None = None,
    scenario_support: str = "",
    scenario_resistance: str = "",
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
            honesty=f"Contexto Hermes no disponible: {note or 'sin respuesta'}"[:140],
            v2_reasons=v2_reasons,
            v2_direction=v2_direction,
            scenario_support=scenario_support,
            scenario_resistance=scenario_resistance,
        )
    if not plan.available:
        reason = plan.unavailable_reason or note or "Hermes no disponible"
        return NyPlan(
            available=True,
            recommendation=label,
            action=action,
            htf_trend="indefinido",
            honesty=f"Contexto Hermes no disponible: {reason}"[:140],
            v2_reasons=v2_reasons,
            v2_direction=v2_direction,
            scenario_support=scenario_support,
            scenario_resistance=scenario_resistance,
        )
    # Free-form model prose may invent triggers or price levels. Only retain the
    # parser's own diagnostic; context levels come from the observed candles.
    honesty = plan.honesty if plan.honesty.startswith("Hermes no devolvió JSON") else ""
    if note:
        honesty = f"{honesty} · {note}".strip(" ·") if honesty else note
    return replace(
        plan,
        recommendation=label,
        action=action,
        support=(scenario_support,) if scenario_support else (),
        resistance=(scenario_resistance,) if scenario_resistance else (),
        htf_trend=plan.htf_trend
        if plan.htf_trend in {"alcista", "bajista", "rango", "indefinido"}
        else "indefinido",
        confidence=plan.confidence if plan.confidence in {"baja", "media", "alta"} else "",
        why="",
        invalidation="",
        honesty=honesty[:140],
        v2_reasons=v2_reasons,
        v2_direction=v2_direction,
        scenario_support=scenario_support,
        scenario_resistance=scenario_resistance,
        context_recommendation=(
            plan.recommendation if plan.recommendation in {"esperar", "no operar"} else ""
        ),
    )


def evaluate_desk_status(
    pair: str,
    frames: dict[str, Any] | None,
    *,
    news_blocked: bool,
    now_utc: datetime | None = None,
    active_signal_state: dict[str, Any] | None = None,
    alignment_state: dict[str, Any] | None = None,
    settings: Any | None = None,
) -> DeskDecision:
    """Same evaluate_entry contract as the scan. Direction only when V2 fires."""

    def unavailable(reason: str) -> DeskDecision:
        reasons: tuple[str, ...] = (reason,)
        if news_blocked:
            reasons += ("bloqueo por noticias de alto impacto",)
        return DeskDecision("STAND_ASIDE", reasons=reasons)

    if not frames:
        return unavailable("datos OHLC ausentes")
    data_1h = frames.get("1h")
    data_30m = frames.get("30m")
    data_15m = frames.get("15m")
    if data_1h is None or data_30m is None or data_15m is None:
        return unavailable("faltan marcos OHLC 1h/30m/15m")
    empty = (
        getattr(data_1h, "empty", False)
        or getattr(data_30m, "empty", False)
        or getattr(data_15m, "empty", False)
    )
    if empty:
        return unavailable("marco OHLC vacío")
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
        return unavailable("error al evaluar V2")
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("aligned"), bool)
        or not isinstance(result.get("fired"), bool)
    ):
        details = result.get("reasons", []) if isinstance(result, dict) else []
        detail = ", ".join(str(reason) for reason in details) if isinstance(details, list) else ""
        return unavailable(f"evaluación V2 incompleta{': ' + detail if detail else ''}")
    # evaluate_entry derives alignment and direction from the same RSI
    # predicates, and fires only with a direction and no blocked gates.
    if result["fired"] and (
        not result["aligned"]
        or result.get("direction") not in ("BUY", "SELL")
        or result.get("no_trade_reasons") != []
    ):
        return unavailable("evaluación V2 inconsistente")
    reasons_value = result.get("no_trade_reasons")
    if not isinstance(reasons_value, list) or not all(
        isinstance(reason, str) for reason in reasons_value
    ):
        return unavailable("evaluación V2 incompleta")
    action = desk_action_from_result(result, news_blocked=news_blocked)
    reasons = [str(reason) for reason in (result.get("no_trade_reasons") or [])]
    if news_blocked and not any("news" in reason.lower() for reason in reasons):
        reasons.insert(0, "bloqueo por noticias de alto impacto")
    if not result["aligned"]:
        reasons.append("sin alineación RSI MTF ahora")
    elif action == "WATCH" and not reasons:
        reasons.append("alineado; confirmación de entrada pendiente")
    elif action == "ENTER_ONLY_IF" and not reasons:
        reasons.append("evaluación V2 activada en esta muestra; no es autorización")
    direction = result.get("direction")
    return DeskDecision(
        action,
        str(direction) if action == "ENTER_ONLY_IF" and direction in {"BUY", "SELL"} else None,
        tuple(reasons),
    )


def evaluate_desk_decision(
    pair: str, frames: dict[str, Any] | None, **kwargs: Any
) -> tuple[DeskAction, str | None]:
    """Compatibility wrapper for callers needing the action and V2 direction."""
    decision = evaluate_desk_status(pair, frames, **kwargs)
    return decision.action, decision.direction


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
