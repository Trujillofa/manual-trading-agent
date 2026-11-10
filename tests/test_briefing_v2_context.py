"""Current V2 evidence is separate from conditional session context."""

from datetime import UTC, datetime

import pandas as pd
import pytest

from src.briefing.actions import evaluate_desk_status
from src.briefing.formatter import format_pre_ny_briefing
from src.briefing.hermes import format_ny_plan
from src.briefing.models import NyPlan
from src.briefing.service import build_briefing
from src.config.settings import Settings


def frames():
    frame = pd.DataFrame(
        {"open": [100.0] * 20, "high": [101.0] * 20, "low": [99.0] * 20, "close": [100.0] * 20},
        index=pd.date_range(end="2026-08-24 11:15", periods=20, freq="15min", tz="UTC"),
    )
    return {"1h": frame, "30m": frame, "15m": frame}


@pytest.mark.parametrize(
    ("result", "blocked", "action", "reason"),
    [
        (
            {"fired": False, "aligned": False, "no_trade_reasons": []},
            False,
            "STAND_ASIDE",
            "sin alineación",
        ),
        ({"fired": False, "aligned": True, "no_trade_reasons": []}, False, "WATCH", "pendiente"),
        (
            {"fired": False, "aligned": True, "no_trade_reasons": ["outside allowed session"]},
            False,
            "STAND_ASIDE",
            "outside allowed session",
        ),
        (
            {"fired": True, "aligned": True, "direction": "BUY", "no_trade_reasons": []},
            True,
            "STAND_ASIDE",
            "noticias",
        ),
        (
            {"fired": True, "aligned": True, "direction": "SELL", "no_trade_reasons": []},
            False,
            "ENTER_ONLY_IF",
            "no es autorización",
        ),
        ({"fired": False, "reasons": ["rsi unavailable"]}, False, "STAND_ASIDE", "rsi unavailable"),
        (
            {
                "fired": False,
                "aligned": True,
                "no_trade_reasons": ["spread too wide or unavailable"],
            },
            False,
            "WATCH",
            "spread too wide",
        ),
    ],
)
def test_v2_reason_and_action(monkeypatch, result, blocked, action, reason):
    monkeypatch.setattr("src.scanner.evaluator.evaluate_entry", lambda *a, **kw: result)
    status = evaluate_desk_status("XAU/USD", frames(), news_blocked=blocked, settings=Settings())
    assert status.action == action
    assert any(reason in item for item in status.reasons)
    assert status.direction == ("SELL" if action == "ENTER_ONLY_IF" else None)


def test_missing_and_error_never_claim_no_alignment(monkeypatch):
    missing = evaluate_desk_status("XAU/USD", None, news_blocked=True)
    assert "datos OHLC ausentes" in missing.reasons
    assert any("noticias" in r for r in missing.reasons)
    assert not any("alineación" in r for r in missing.reasons)
    monkeypatch.setattr("src.scanner.evaluator.evaluate_entry", lambda *a, **kw: 1 / 0)
    error = evaluate_desk_status("XAU/USD", frames(), news_blocked=False, settings=Settings())
    assert error.action == "STAND_ASIDE"
    assert error.reasons == ("error al evaluar V2",)


@pytest.mark.parametrize(
    ("reason", "expected", "unevaluable"),
    [
        ("datos OHLC ausentes", "se recuperan los datos", True),
        ("evaluación V2 incompleta: rsi unavailable", "se recuperan los datos", True),
        ("error al evaluar V2", "se resuelve el error de evaluación", True),
        ("outside allowed session", "abre una sesión permitida", False),
        ("bloqueo por noticias de alto impacto", "termina el bloqueo", False),
        ("sin alineación RSI MTF ahora", "hay alineación", False),
    ],
)
def test_conditional_scenario_reflects_current_evidence(reason, expected, unevaluable):
    plan = NyPlan(
        available=True, action="STAND_ASIDE", recommendation="no operar", v2_reasons=(reason,)
    )
    rendered = "\n".join(format_ny_plan(plan))
    assert expected in rendered
    assert ("V2 no evaluable" in rendered) is unevaluable
    assert "reevaluar V2 y filtros" in rendered
    assert "No es señal" in rendered


@pytest.mark.asyncio
async def test_full_briefing_separates_current_and_context(monkeypatch):
    monkeypatch.setattr(
        "src.scanner.evaluator.evaluate_entry",
        lambda *a, **kw: {"fired": False, "aligned": False, "no_trade_reasons": []},
    )

    async def hermes(_prompt):
        return '{"XAU/USD": {"recommendation": "wait", "why": "rango"}}'

    briefing = await build_briefing(
        Settings(),
        now=datetime(2026, 8, 24, 11, 20, tzinfo=UTC),
        fetch_mtf=lambda _: frames(),
        events=[],
        news_error="feed unavailable",
        etr_reports={},
        fetch_funding=False,
        active_signals={},
        near_setups={},
        hermes_complete=hermes,
        instrument_ids=["XAU/USD"],
    )
    item = briefing.instruments[0]
    assert item.ny_plan.action == "STAND_ASIDE"
    message = format_pre_ny_briefing(briefing)
    assert "V2 ahora: sin alineación RSI MTF ahora" in message
    assert "noticias no verificadas" in message
    assert "si se verifican las noticias, reevaluar V2 y filtros" in message
    assert "Contexto Hermes (no acción): esperar" in message
    assert "No es señal" in message


@pytest.mark.asyncio
async def test_fetch_failure_is_not_misreported_as_no_alignment():
    def fail(_):
        raise RuntimeError()

    briefing = await build_briefing(
        Settings(),
        now=datetime(2026, 8, 24, 11, 20, tzinfo=UTC),
        fetch_mtf=fail,
        events=[],
        etr_reports={},
        fetch_funding=False,
        active_signals={},
        near_setups={},
        instrument_ids=["XAU/USD"],
        attach_hermes=False,
    )
    message = format_pre_ny_briefing(briefing)
    assert "V2 no evaluable" in message
    assert "falló obtención OHLC" in message
    assert "sin alineación RSI MTF ahora" not in message
    assert "si se recuperan los datos, reevaluar V2 y filtros" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["news", "stale_1h", "stale_30m", "stale_15m", "unknown_time"])
async def test_fired_sample_never_implies_permission_with_unverified_inputs(monkeypatch, failure):
    monkeypatch.setattr(
        "src.scanner.evaluator.evaluate_entry",
        lambda *a, **kw: {
            "fired": True,
            "aligned": True,
            "direction": "BUY",
            "no_trade_reasons": [],
        },
    )
    data = frames()
    if failure.startswith("stale_"):
        tf = failure.removeprefix("stale_")
        data[tf] = data[tf].copy()
        data[tf].index -= pd.Timedelta(hours=4)
    if failure == "unknown_time":
        data["15m"] = data["15m"].reset_index(drop=True)
    briefing = await build_briefing(
        Settings(),
        now=datetime(2026, 8, 24, 11, 20, tzinfo=UTC),
        fetch_mtf=lambda _: data,
        events=[],
        news_error="offline" if failure == "news" else None,
        etr_reports={},
        fetch_funding=False,
        active_signals={},
        near_setups={},
        instrument_ids=["XAU/USD"],
        attach_hermes=False,
    )
    plan = briefing.instruments[0].ny_plan
    message = format_pre_ny_briefing(briefing)
    assert plan.action == "STAND_ASIDE"
    assert "entrar solo" not in message
    assert "Dirección V2 observada: BUY (no autorización)" in message
    assert "noticias no verificadas" in message if failure == "news" else "OHLC" in message
    if failure != "news":
        assert "niveles OHLC fiables no disponibles" in message
    else:
        assert "soporte 99.0 o resistencia 101.0" in message


def test_watch_with_spread_block_does_not_claim_confirmation():
    plan = NyPlan(
        available=True,
        action="WATCH",
        recommendation="esperar",
        v2_reasons=("spread too wide or unavailable",),
    )
    rendered = "\n".join(format_ny_plan(plan))
    assert "spread too wide" in rendered
    assert "se confirma la entrada" not in rendered
    assert "cambia el estado de los filtros" in rendered


def test_hermes_unavailable_explicit_and_v2_direction_preserved():
    from src.briefing.actions import apply_desk_action
    from src.briefing.hermes import unavailable_plan

    plan = apply_desk_action(
        unavailable_plan("timeout"),
        "ENTER_ONLY_IF",
        v2_direction="SELL",
        v2_reasons=("muestra V2",),
    )
    text = "\n".join(format_ny_plan(plan))
    assert "Contexto Hermes no disponible: timeout" in text
    assert "Dirección V2 observada: SELL (no autorización)" in text


def test_actual_rsi_unavailable_result_is_not_mislabeled(monkeypatch):
    monkeypatch.setattr(
        "src.scanner.evaluator.evaluate_entry",
        lambda *a, **kw: {"fired": False, "direction": None, "reasons": ["rsi unavailable"]},
    )
    result = evaluate_desk_status("XAU/USD", frames(), news_blocked=False, settings=Settings())
    assert result.action == "STAND_ASIDE"
    assert "rsi unavailable" in " ".join(result.reasons)
    assert "sin alineación" not in " ".join(result.reasons)


def test_scenarios_use_ohlc_not_hermes_levels():
    from src.briefing.actions import apply_desk_action
    from src.briefing.service import _scenario_levels

    support, resistance = _scenario_levels(frames(), 20)
    plan = apply_desk_action(
        NyPlan(available=True, support=("123456",), resistance=("999999",)),
        "WATCH",
        scenario_support=support,
        scenario_resistance=resistance,
    )
    scenario = next(x for x in format_ny_plan(plan) if x.startswith("Escenarios"))
    assert "soporte 99.0 o resistencia 101.0" in scenario
    assert "123456" not in scenario and "999999" not in scenario
    assert "No es señal ni autorización" in scenario


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stale", "grok", "fresh"])
async def test_news_swallowed_failure_and_grok_are_not_verified(monkeypatch, mode):
    from datetime import timedelta
    from types import SimpleNamespace

    from src.briefing.service import _load_news

    now = datetime(2026, 8, 24, 11, 20, tzinfo=UTC)

    class FakeChecker:
        def __init__(self, **kwargs):
            self._last_fetch = now - timedelta(hours=1) if mode == "stale" else now
            self._events = [SimpleNamespace(source="grok" if mode == "grok" else "forex_factory")]

        async def fetch_events(self, **kwargs):
            return self._events

        def get_events_in_window(self, **kwargs):
            return self._events

        def get_source_status(self):
            return "forex_factory_or_grok"

    monkeypatch.setattr("src.briefing.service.NewsChecker", FakeChecker)
    events, error, status = await _load_news(Settings(), now)
    assert len(events) == 1 and status == "forex_factory_or_grok"
    assert (error is None) is (mode == "fresh")


def test_real_evaluator_insufficient_rsi_is_not_no_alignment():
    data = {key: value.iloc[:3] for key, value in frames().items()}
    result = evaluate_desk_status(
        "XAU/USD",
        data,
        news_blocked=False,
        settings=Settings(),
        now_utc=datetime(2026, 8, 24, 11, 20, tzinfo=UTC),
    )
    assert result.action == "STAND_ASIDE"
    assert "rsi unavailable" in " ".join(result.reasons)
    assert "sin alineación" not in " ".join(result.reasons)
