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
    return {
        tf: frame.set_axis(pd.date_range(end=end, periods=20, freq=freq, tz="UTC"))
        for tf, end, freq in (
            ("1h", "2026-08-24 11:00", "1h"),
            ("30m", "2026-08-24 11:00", "30min"),
            ("15m", "2026-08-24 11:15", "15min"),
        )
    }


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


@pytest.mark.parametrize("reasons", [None, "missing", {}, [None], [1]])
def test_nonfiring_incomplete_reasons_fail_closed(monkeypatch, reasons):
    result = {"fired": False, "aligned": True}
    if reasons != "missing":
        result["no_trade_reasons"] = reasons
    monkeypatch.setattr("src.scanner.evaluator.evaluate_entry", lambda *a, **kw: result)
    status = evaluate_desk_status("XAU/USD", frames(), news_blocked=False, settings=Settings())
    assert status.action == "STAND_ASIDE"
    assert status.reasons == ("evaluación V2 incompleta",)


@pytest.mark.parametrize("tf,source", [("1h", "15m"), ("1h", "30m"), ("30m", "15m")])
@pytest.mark.asyncio
async def test_wrong_cadence_cannot_authorize_fired_result(monkeypatch, tf, source):
    monkeypatch.setattr("src.scanner.evaluator.evaluate_entry", lambda *a, **kw: {
        "fired": True, "aligned": True, "direction": "BUY", "no_trade_reasons": []
    })
    data = frames()
    data[tf] = data[source].copy()
    briefing = await build_briefing(
        Settings(), now=datetime(2026, 8, 24, 11, 20, tzinfo=UTC),
        fetch_mtf=lambda _: data, events=[], events_verified=True,
        etr_reports={}, fetch_funding=False, active_signals={}, near_setups={},
        instrument_ids=["XAU/USD"], attach_hermes=False,
    )
    plan = briefing.instruments[0].ny_plan
    assert plan.action == "STAND_ASIDE"
    assert f"OHLC {tf} cadencia no verificable" in plan.v2_reasons


def test_cadence_accepts_weekend_gap_and_naive_timestamps():
    from src.briefing.service import _briefing_freshness

    data = frames()
    for frame in data.values():
        idx = frame.index.tz_localize(None).to_series().reset_index(drop=True)
        idx.iloc[:10] -= pd.Timedelta(days=2)
        frame.index = pd.DatetimeIndex(idx)
    assert _briefing_freshness(data, datetime(2026, 8, 24, 11, 20, tzinfo=UTC)) == ()


@pytest.mark.parametrize("mode", ["duplicate", "single", "nat", "only_gaps"])
def test_unverifiable_cadence_or_index_fails_closed(mode):
    from src.briefing.service import _briefing_freshness

    data = frames()
    frame = data["1h"]
    if mode == "single":
        data["1h"] = frame.iloc[-1:]
    elif mode == "nat":
        frame.index = frame.index.where(frame.index != frame.index[1], pd.NaT)
    elif mode == "duplicate":
        idx = list(frame.index)
        idx[-2] = idx[-1]
        frame.index = pd.DatetimeIndex(idx)
    else:
        frame.index = pd.date_range(end=frame.index[-1], periods=len(frame), freq="2h")
    issues = _briefing_freshness(data, datetime(2026, 8, 24, 11, 20, tzinfo=UTC))
    assert any("OHLC 1h" in issue for issue in issues)
