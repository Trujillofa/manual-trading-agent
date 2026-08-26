"""Coded NY-scalp avoids. Catalog only — Hermes cannot invent these."""

from __future__ import annotations

from datetime import UTC, datetime

from src.briefing.actions import apply_desk_action, evaluate_desk_action
from src.briefing.avoids import (
    TapeSnapshot,
    build_instrument_avoids,
    build_session_avoids,
    format_avoid,
)
from src.briefing.formatter import format_instrument_briefing, format_pre_ny_briefing
from src.briefing.hermes import format_ny_plan
from src.briefing.models import InstrumentBriefing, NyPlan, Pillar, PreNyBriefing
from src.etr.models import EtrReport
from src.news.news_checker import NewsEvent


def _pce(ts: datetime) -> NewsEvent:
    return NewsEvent(
        timestamp=ts,
        currency="USD",
        name="Core PCE Price Index m/m",
        importance=3,
        country="USD",
        forecast="0.2%",
    )


def _gdp(ts: datetime) -> NewsEvent:
    return NewsEvent(
        timestamp=ts,
        currency="USD",
        name="Prelim GDP q/q",
        importance=3,
        country="USD",
        forecast="1.5%",
    )


def _etr(asset: str = "nasdaq") -> EtrReport:
    return EtrReport(
        asset=asset,
        label=asset,
        price=None,
        updated_at=None,
        context_score=77.0,
        bias="bajista",
        estado="Zona de reacción",
        lectura_headline="",
        lectura_body="",
        h4_context="",
        m5_execution="",
        structure="",
    )


def test_session_usd_3star_at_ny_open() -> None:
    now = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)
    ny_open = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    print_at = datetime(2026, 8, 26, 12, 30, tzinfo=UTC)
    codes, detail = build_session_avoids(
        [_pce(print_at), _gdp(print_at)],
        now=now,
        ny_open=ny_open,
    )
    assert codes == ("SESSION_USD_3STAR",)
    assert "PCE" in detail
    assert "GDP" in detail
    assert "12:30" in detail
    assert "PCE/GDP" in format_avoid("SESSION_USD_3STAR", detail=detail)


def test_gold_into_3star_and_midrange() -> None:
    codes = build_instrument_avoids(
        "XAU/USD",
        currencies={"USD"},
        session_codes=("SESSION_USD_3STAR",),
        snapshot=TapeSnapshot(adx_1h=16.0, rsi_1h=41.5, range_location_pct=37.0),
    )
    assert "AVOID_INTO_3STAR" in codes
    assert "AVOID_MIDRANGE_CHASE" in codes
    assert "AVOID_FADE_1H_TREND" not in codes
    assert len(codes) <= 3


def test_oil_fade_and_chase_extreme() -> None:
    codes = build_instrument_avoids(
        "OIL",
        currencies={"USD"},
        session_codes=("SESSION_USD_3STAR",),
        snapshot=TapeSnapshot(adx_1h=55.0, rsi_1h=33.2, range_location_pct=51.0),
        etr_report=_etr("oil"),
    )
    assert codes[0] == "AVOID_INTO_3STAR"
    assert "AVOID_CHASE_EXTREME_IN_TREND" in codes
    assert "AVOID_FADE_1H_TREND" in codes
    assert "AVOID_MIDRANGE_CHASE" not in codes
    assert "AVOID_ETR_WRONG_SCALE" not in codes
    assert len(codes) == 3


def test_nasdaq_etr_wrong_scale() -> None:
    codes = build_instrument_avoids(
        "NASDAQ",
        currencies={"USD"},
        session_codes=(),
        snapshot=TapeSnapshot(adx_1h=24.0, rsi_1h=50.1, range_location_pct=56.0),
        etr_report=_etr("nasdaq"),
    )
    assert "AVOID_ETR_WRONG_SCALE" in codes
    assert "AVOID_MIDRANGE_CHASE" in codes


def test_hermes_why_is_not_an_avoid() -> None:
    missing = Pillar(name="Técnico", available=False, unavailable_reason="test")
    plan = apply_desk_action(
        NyPlan(
            available=True,
            recommendation="no operar",
            htf_trend="rango",
            why="evitar short Nasdaq en ruptura falsa",
        ),
        "STAND_ASIDE",
    )
    item = InstrumentBriefing(
        instrument_id="NASDAQ",
        display_name="Nasdaq",
        yf_symbol="NQ=F",
        technical=missing,
        fundamental=missing,
        sentiment=missing,
        ny_plan=plan,
        avoids=("AVOID_MIDRANGE_CHASE",),
    )
    message = format_instrument_briefing(item)
    avoid_line = next(line for line in message.splitlines() if line.startswith("*Evitar*"))
    assert "medio de rango" in avoid_line
    assert "evitar short" not in avoid_line
    assert "evitar short" not in " ".join(format_ny_plan(plan))


def test_avoids_never_promote_action() -> None:
    assert evaluate_desk_action("XAU/USD", {}, news_blocked=False) == "STAND_ASIDE"
    codes = build_instrument_avoids(
        "XAU/USD",
        currencies={"USD"},
        session_codes=("SESSION_USD_3STAR",),
        snapshot=TapeSnapshot(adx_1h=16.0, rsi_1h=41.0, range_location_pct=37.0),
        action="STAND_ASIDE",
    )
    assert codes
    assert evaluate_desk_action("XAU/USD", None, news_blocked=False) == "STAND_ASIDE"


def test_other_side_only_when_v2_fires() -> None:
    idle = build_instrument_avoids(
        "XAU/USD",
        currencies=set(),
        session_codes=(),
        action="STAND_ASIDE",
        v2_direction="BUY",
    )
    assert "AVOID_OTHER_SIDE" not in idle
    fired = build_instrument_avoids(
        "XAU/USD",
        currencies=set(),
        session_codes=(),
        action="ENTER_ONLY_IF",
        v2_direction="BUY",
    )
    assert fired == ("AVOID_OTHER_SIDE",)


def test_session_avoid_renders_on_header() -> None:
    missing = Pillar(name="Técnico", available=False, unavailable_reason="test")
    briefing = PreNyBriefing(
        session_date="2026-08-26",
        generated_at=datetime(2026, 8, 26, 11, 0, tzinfo=UTC),
        ny_open_utc="12:00",
        lead_minutes=60,
        instruments=[
            InstrumentBriefing(
                instrument_id="XAU/USD",
                display_name="Oro",
                yf_symbol="GC=F",
                technical=missing,
                fundamental=missing,
                sentiment=missing,
                avoids=("AVOID_INTO_3STAR",),
            )
        ],
        session_avoids=("SESSION_USD_3STAR",),
        session_avoid_detail="PCE/GDP 12:30 UTC",
    )
    message = format_pre_ny_briefing(briefing)
    assert "*Evitar sesión* no scalp USD-beta hacia PCE/GDP 12:30 UTC" in message
    assert "*Evitar* no scalp direccional hacia el 3★ USD" in message


def test_session_avoid_does_not_linger_after_open() -> None:
    now = datetime(2026, 8, 26, 15, 3, tzinfo=UTC)
    ny_open = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    print_at = datetime(2026, 8, 26, 12, 30, tzinfo=UTC)
    codes, detail = build_session_avoids(
        [_pce(print_at), _gdp(print_at)],
        now=now,
        ny_open=ny_open,
    )
    assert codes == ()
    assert detail == ""


def test_oil_etr_cache_is_not_wrong_scale() -> None:
    codes = build_instrument_avoids(
        "OIL",
        currencies=set(),
        session_codes=(),
        etr_report=_etr("oil"),
    )
    assert codes == ()


def test_tape_snapshot_uses_cleaned_ohlc() -> None:
    import numpy as np
    import pandas as pd

    from src.briefing.avoids import tape_snapshot
    from src.briefing.technical import build_technical_pillar

    idx = pd.date_range("2026-08-01", periods=80, freq="h", tz="UTC")
    close = np.concatenate([np.linspace(100, 102, 60), np.linspace(102, 120, 20)])
    frame = pd.DataFrame(
        {"open": close - 0.2, "high": close + 0.5, "low": close - 0.5, "close": close},
        index=idx,
    )
    dirty = frame.copy()
    dirty.iloc[10:12] = np.nan
    frames = {"1h": dirty, "15m": frame, "30m": frame}
    snap = tape_snapshot(frames)
    pillar, _ = build_technical_pillar(frames, point_size=0.01)
    assert snap is not None
    assert snap.adx_1h is not None
    assert snap.adx_1h >= 25
    assert any("ADX" in line and "tendencia" in line for line in pillar.lines)
    codes = build_instrument_avoids(
        "XAU/USD",
        currencies=set(),
        session_codes=(),
        frames=frames,
    )
    assert "AVOID_FADE_1H_TREND" in codes
