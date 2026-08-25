"""V2-gated Plan NY actions. Not a KEEP / promotion-gate suite."""

from __future__ import annotations

from datetime import UTC, datetime

from src.briefing.actions import (
    apply_desk_action,
    desk_action_from_entry,
    desk_action_from_result,
    evaluate_desk_action,
)
from src.briefing.models import NyPlan


def test_news_block_is_stand_aside_even_if_aligned() -> None:
    assert (
        desk_action_from_entry(fired=False, aligned=True, news_blocked=True, session_blocked=False)
        == "STAND_ASIDE"
    )


def test_session_block_is_stand_aside() -> None:
    assert (
        desk_action_from_entry(fired=False, aligned=True, news_blocked=False, session_blocked=True)
        == "STAND_ASIDE"
    )


def test_aligned_pending_is_watch() -> None:
    assert (
        desk_action_from_entry(fired=False, aligned=True, news_blocked=False, session_blocked=False)
        == "WATCH"
    )


def test_v2_fire_is_enter_only_if() -> None:
    assert (
        desk_action_from_entry(fired=True, aligned=True, news_blocked=False, session_blocked=False)
        == "ENTER_ONLY_IF"
    )


def test_default_is_stand_aside() -> None:
    assert (
        desk_action_from_entry(
            fired=False, aligned=False, news_blocked=False, session_blocked=False
        )
        == "STAND_ASIDE"
    )


def test_result_session_reason_maps_to_stand_aside() -> None:
    action = desk_action_from_result(
        {"fired": False, "aligned": True, "no_trade_reasons": ["outside allowed session"]},
        news_blocked=False,
    )
    assert action == "STAND_ASIDE"


def test_hermes_buy_cannot_survive_gate() -> None:
    hermes = NyPlan(
        available=True,
        htf_trend="alcista",
        recommendation="compra en retroceso",
        support=("64000",),
        resistance=("67000",),
    )
    gated = apply_desk_action(hermes, "STAND_ASIDE")
    assert gated.recommendation == "no operar"
    assert gated.action == "STAND_ASIDE"
    assert gated.htf_trend == "alcista"
    assert gated.support == ("64000",)


def test_unavailable_hermes_still_emits_action() -> None:
    gated = apply_desk_action(
        NyPlan(available=False, unavailable_reason="timeout 5s"),
        "WATCH",
        hermes_note="timeout 5s",
    )
    assert gated.available is True
    assert gated.recommendation == "esperar"
    assert gated.action == "WATCH"
    assert "timeout" in (gated.honesty or "")


def test_empty_frames_are_stand_aside() -> None:
    assert evaluate_desk_action("XAU/USD", None, news_blocked=False) == "STAND_ASIDE"
    assert evaluate_desk_action("XAU/USD", {}, news_blocked=True) == "STAND_ASIDE"


def test_enter_only_if_overwrites_buy() -> None:
    gated = apply_desk_action(
        NyPlan(available=True, recommendation="compra en retroceso", htf_trend="alcista"),
        "ENTER_ONLY_IF",
    )
    assert gated.recommendation == "entrar solo si V2"
    assert gated.action == "ENTER_ONLY_IF"
    assert gated.htf_trend == "alcista"


def test_hermes_why_compra_is_scrubbed() -> None:
    gated = apply_desk_action(
        NyPlan(
            available=True,
            recommendation="compra en retroceso",
            htf_trend="alcista",
            why="compra en retroceso sobre 64000",
            invalidation="SELL if close < 63000",
        ),
        "STAND_ASIDE",
    )
    assert gated.recommendation == "no operar"
    assert gated.why == ""
    assert gated.invalidation == ""
    assert gated.htf_trend == "alcista"


def test_desk_evaluate_entry_gets_live_overrides(monkeypatch: object) -> None:
    import pandas as pd

    from src.config.settings import Settings
    from src.scanner.evaluator import evaluate_entry as real_eval

    captured: dict[str, object] = {}

    def fake_eval(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return {"fired": False, "aligned": False, "no_trade_reasons": []}

    monkeypatch.setattr("src.scanner.evaluator.evaluate_entry", fake_eval)
    rows = {
        "open": [2400.0] * 20,
        "high": [2410.0] * 20,
        "low": [2390.0] * 20,
        "close": [2405.0] * 20,
    }
    frame = pd.DataFrame(rows)
    action = evaluate_desk_action(
        "XAU/USD",
        {"1h": frame, "30m": frame, "15m": frame},
        news_blocked=False,
        settings=Settings(),
        active_signal_state={"XAU/USD": {"direction": "BUY"}},
        alignment_state={"XAU/USD": {"direction": "BUY", "bars": 2}},
    )
    assert action == "STAND_ASIDE"
    overrides = captured.get("overrides") or {}
    assert overrides.get("pip_size") == 1.0
    assert float(overrides.get("buffer_pips") or 0) > 0.0001
    assert "00-21" in list(overrides.get("session_allowed_utc") or [])
    assert captured.get("active_signal_state") == {"XAU/USD": {"direction": "BUY"}}
    assert captured.get("alignment_state") == {"XAU/USD": {"direction": "BUY", "bars": 2}}
    _ = real_eval


def test_oil_usd_lockout_blocks_desk_news() -> None:
    from src.briefing.service import _news_blocked_for
    from src.config.settings import Settings, default_briefing_instruments
    from src.news.news_checker import NewsEvent

    oil = next(item for item in default_briefing_instruments() if item.id == "OIL")
    now = datetime(2026, 8, 25, 12, 35, tzinfo=UTC)
    events = [
        NewsEvent(
            timestamp=now,
            currency="USD",
            name="NFP",
            importance=3,
            country="USD",
        )
    ]
    assert _news_blocked_for(oil, events, now, Settings()) is True


def test_unknown_plan_symbol_is_dropped() -> None:
    from src.briefing.service import _resolve_briefing_instruments
    from src.config.settings import BriefingConfig

    cfg = BriefingConfig()
    assert _resolve_briefing_instruments(cfg, ["GOLD"]) == []
    resolved = _resolve_briefing_instruments(cfg, ["XAUUSD"])
    assert [item.id for item in resolved] == ["XAU/USD"]
