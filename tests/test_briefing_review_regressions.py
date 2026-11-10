"""Regression coverage for briefing final-review safety findings."""
from datetime import UTC, datetime

import pandas as pd
import pytest

from src.briefing.actions import evaluate_desk_status
from src.briefing.formatter import format_pre_ny_briefing
from src.briefing.service import build_briefing
from src.config.settings import Settings


def frames():
    frame = pd.DataFrame(
        {"open": [100.] * 20, "high": [101.] * 20, "low": [99.] * 20, "close": [100.] * 20},
        index=pd.date_range(end="2026-08-24 11:15", periods=20, freq="15min", tz="UTC"),
    )
    return {tf: frame.copy() for tf in ("1h", "30m", "15m")}


@pytest.mark.parametrize("result", [
    {"fired": True, "aligned": False, "direction": "BUY", "no_trade_reasons": []},
    {"fired": True, "aligned": True, "direction": None, "no_trade_reasons": []},
    {"fired": True, "aligned": True, "direction": "BUY", "no_trade_reasons": ["blocked"]},
    {"fired": True, "aligned": True, "direction": "BUY"},
])
@pytest.mark.parametrize("blocked", [False, True])
def test_inconsistent_fired_result_fails_closed(monkeypatch, result, blocked):
    monkeypatch.setattr("src.scanner.evaluator.evaluate_entry", lambda *a, **kw: result)
    status = evaluate_desk_status("XAU/USD", frames(), news_blocked=blocked, settings=Settings())
    assert status.action == "STAND_ASIDE" and status.direction is None
    assert "evaluación V2 inconsistente" in status.reasons
    assert any("noticias" in reason for reason in status.reasons) is blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("verified,news_status,error,expected", [
    (False, None, None, "STAND_ASIDE"),
    (False, "forex_factory_or_grok", None, "STAND_ASIDE"),
    (True, None, None, "ENTER_ONLY_IF"),
    (True, None, "feed unavailable", "STAND_ASIDE"),
])
async def test_external_events_require_explicit_verified_provenance(
    monkeypatch, verified, news_status, error, expected
):
    monkeypatch.setattr("src.scanner.evaluator.evaluate_entry", lambda *a, **kw: {
        "fired": True, "aligned": True, "direction": "BUY", "no_trade_reasons": []
    })
    briefing = await build_briefing(
        Settings(), now=datetime(2026, 8, 24, 11, 20, tzinfo=UTC),
        fetch_mtf=lambda _: frames(), events=[], events_verified=verified,
        news_status=news_status, news_error=error, etr_reports={}, fetch_funding=False,
        active_signals={}, near_setups={}, instrument_ids=["XAU/USD"], attach_hermes=False,
    )
    plan = briefing.instruments[0].ny_plan
    assert plan.action == expected
    text = format_pre_ny_briefing(briefing)
    if expected == "ENTER_ONLY_IF":
        assert "si hay alineación" not in text
        assert "si cambian las condiciones" in text
    else:
        assert "noticias no verificadas" in text


def test_technical_timestamp_is_from_hourly_frame_only():
    from src.briefing.technical import bar_freshness, build_technical_pillar

    data = frames()
    data["1h"].index -= pd.Timedelta(hours=4)
    _, stamp = build_technical_pillar(data, point_size=0.01)
    assert stamp == data["1h"].index[-1].to_pydatetime()
    assert bar_freshness(stamp, datetime(2026, 8, 24, 11, 20, tzinfo=UTC)) == "OHLC atrasado ~4h"
    data.pop("1h")
    _, stamp = build_technical_pillar(data, point_size=0.01)
    assert stamp is None
