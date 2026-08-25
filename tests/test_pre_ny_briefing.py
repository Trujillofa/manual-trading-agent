"""Pre-NY briefing schedule, pillars, formatter, and send idempotency."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.briefing.formatter import format_pre_ny_briefing
from src.briefing.fundamental import (
    build_header_synthesis,
    events_in_lockout,
    events_in_ny_window,
    inventory_calendar_events,
    select_events,
)
from src.briefing.funding import FundingSnapshot, parse_funding_payload
from src.briefing.schedule import in_pre_ny_window, ny_open_at, should_send_briefing
from src.briefing.service import build_briefing, maybe_send_briefing
from src.briefing.technical import bar_freshness
from src.config.settings import BriefingConfig, BriefingInstrumentConfig, Settings
from src.etr.alerts import chunk_telegram
from src.etr.models import EtrReport, EtrScenario, PriceZone
from src.news.news_checker import NewsEvent
from src.news.surprise import SOURCE_FOREX_FACTORY


def _ohlc(start: float, n: int = 80, step: float = 0.4) -> pd.DataFrame:
    idx = pd.date_range("2026-08-21", periods=n, freq="h", tz="UTC")
    close = [start + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "open": [value - 0.1 for value in close],
            "high": [value + 0.5 for value in close],
            "low": [value - 0.5 for value in close],
            "close": close,
            "volume": [1000] * n,
        },
        index=idx,
    )


def _frames(start: float) -> dict[str, pd.DataFrame]:
    daily = _ohlc(start, n=80, step=0.3)
    return {"1h": daily, "30m": daily, "15m": daily}


def _event(
    name: str,
    currency: str,
    ts: datetime,
    *,
    forecast: str = "",
    actual: str = "",
    observed: datetime | None = None,
) -> NewsEvent:
    return NewsEvent(
        timestamp=ts,
        currency=currency,
        name=name,
        importance=3,
        country=currency,
        forecast=forecast,
        actual=actual,
        source=SOURCE_FOREX_FACTORY,
        actual_observed_at=observed,
    )


def _settings(tmp_path: Path) -> Settings:
    payload = {
        "trading": {"mode": "paper", "pairs": {"majors": ["XAU/USD"], "minors": [], "shadow": []}},
        "timeframes": {"regime": "1h", "momentum": "30m", "entry": "15m"},
        "strategy": {
            "rsi_period": 14,
            "rsi_overbought": 70,
            "rsi_oversold": 30,
            "sma_period": 50,
            "lookback_bars": 20,
            "ema": {},
        },
        "risk": {"tp_atr_multiplier": 1.0, "sl_atr_multiplier": 3.0},
        "news": {"enabled": True},
        "data": {"provider": "yfinance"},
        "telegram": {"enabled": True, "pre_ny_briefing_notifications": True},
        "briefing": {"enabled": True, "ny_open_utc": "12:00", "lead_minutes": 60},
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return Settings.load(path)


def test_in_window_before_open() -> None:
    now = datetime(2026, 8, 24, 11, 15, tzinfo=UTC)
    assert in_pre_ny_window(now, "12:00", 60) is True


def test_outside_window_after_open() -> None:
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    assert in_pre_ny_window(now, "12:00", 60) is False


def test_outside_window_too_early() -> None:
    now = datetime(2026, 8, 24, 10, 59, tzinfo=UTC)
    assert in_pre_ny_window(now, "12:00", 60) is False


def test_weekend_skipped() -> None:
    now = datetime(2026, 8, 22, 11, 30, tzinfo=UTC)  # Saturday
    should, reason, session = should_send_briefing(
        now=now,
        ny_open_utc="12:00",
        lead_minutes=60,
        last_session_date=None,
        skip_weekends=True,
    )
    assert should is False
    assert reason == "weekend"
    assert session.isoformat() == "2026-08-22"


def test_already_sent_same_session() -> None:
    now = datetime(2026, 8, 24, 11, 30, tzinfo=UTC)
    should, reason, _ = should_send_briefing(
        now=now,
        ny_open_utc="12:00",
        lead_minutes=60,
        last_session_date="2026-08-24",
        skip_weekends=True,
    )
    assert should is False
    assert reason == "already_sent"


def test_force_overrides_weekend_and_state() -> None:
    now = datetime(2026, 8, 22, 15, 0, tzinfo=UTC)
    should, reason, _ = should_send_briefing(
        now=now,
        ny_open_utc="12:00",
        lead_minutes=60,
        last_session_date="2026-08-22",
        skip_weekends=True,
        force=True,
    )
    assert should is True
    assert reason == "force"


def test_oil_matches_inventory_keyword() -> None:
    instrument = BriefingInstrumentConfig(
        id="OIL",
        etr_asset="oil",
        extra_news_currencies=("USD",),
        news_keywords=("oil", "crude", "eia", "inventory"),
    )
    ts = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)
    events = [
        _event("EIA Crude Inventories", "EUR", ts),
        _event("German CPI", "EUR", ts),
    ]
    matched = select_events(events, instrument, set())
    assert [event.name for event in matched] == ["EIA Crude Inventories"]


@pytest.mark.asyncio
async def test_build_briefing_has_four_instruments_and_three_pillars(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    now = datetime(2026, 8, 24, 11, 20, tzinfo=UTC)
    starts = {"XAU/USD": 2400.0, "BTC/USD": 65000.0, "NASDAQ": 20000.0, "OIL": 78.0}

    def fetch(instrument_id: str) -> dict[str, pd.DataFrame]:
        return _frames(starts[instrument_id])

    events = [
        _event("CPI", "USD", now + timedelta(hours=2), forecast="0.2%"),
        _event(
            "EIA Crude Inventories",
            "USD",
            now - timedelta(hours=1),
            forecast="1.2M",
            actual="2.0M",
            observed=now - timedelta(minutes=50),
        ),
    ]
    etr = {
        "gold": EtrReport(
            asset="gold",
            label="Oro",
            price=2400.0,
            updated_at="24/08/2026",
            context_score=70.0,
            bias="bajista",
            estado="Zona de reacción",
            lectura_headline="Oro bajo resistencia",
            lectura_body="",
            h4_context="",
            m5_execution="",
            structure="",
            primary=EtrScenario(
                name="principal",
                direction="Bajista",
                status="Esperando",
                role="Principal",
                activation_zone=PriceZone(low=2380.0, high=2420.0),
                invalidation=2450.0,
            ),
        )
    }
    briefing = await build_briefing(
        settings,
        now=now,
        fetch_mtf=fetch,
        events=events,
        etr_reports=etr,
        news_status="forex_factory_or_grok",
        fetch_funding=False,
        active_signals={},
        near_setups={},
    )
    assert [item.instrument_id for item in briefing.instruments] == [
        "XAU/USD",
        "BTC/USD",
        "NASDAQ",
        "OIL",
    ]
    gold = briefing.instruments[0]
    assert gold.technical.available is True
    assert gold.fundamental.available is True
    assert gold.sentiment.available is True
    assert any("RSI" in line for line in gold.technical.lines)
    assert any("sin 3★ propios" in line for line in gold.fundamental.lines)
    assert not any("CPI" in line for line in gold.fundamental.lines)
    assert any("Tesis ETR" in line for line in gold.sentiment.lines)
    assert any("Score tesis ETR" in line for line in gold.sentiment.lines)
    assert any("en zona SÍ" in line for line in gold.sentiment.lines)
    assert briefing.shared_fundamental is not None
    assert any("CPI" in line for line in briefing.shared_fundamental.lines)
    assert any("Lockout USD ahora" in line for line in briefing.shared_fundamental.lines)
    oil = briefing.instruments[3]
    assert any(
        "Inventarios" in line or "EIA Crude Inventories" in line for line in oil.fundamental.lines
    )
    assert any("EIA Crude Inventories" in line for line in oil.fundamental.lines)

    message = format_pre_ny_briefing(briefing)
    assert "Briefing pre-sesión NY" in message
    assert "Macro 3★" in message
    assert "Técnico" in message
    assert "Fundamental" in message
    assert "Sentimiento" in message
    assert "no es señal de entrada" in message.lower()
    assert "Oro" in message
    assert "Bitcoin" in message
    assert "Nasdaq" in message
    assert "Petróleo" in message
    assert "proxy" in message.lower()
    assert briefing.synthesis == "hoy: sin lockout; riesgo = CPI"
    assert message.count("CPI") == 2  # header synthesis + shared Macro 3★
    assert len(chunk_telegram(message)) == 1


@pytest.mark.asyncio
async def test_one_instrument_ohlc_failure_still_sends_others(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    now = datetime(2026, 8, 24, 11, 20, tzinfo=UTC)

    def fetch(instrument_id: str) -> dict[str, pd.DataFrame]:
        if instrument_id == "NASDAQ":
            raise RuntimeError("yfinance timeout")
        return _frames(100.0)

    briefing = await build_briefing(
        settings,
        now=now,
        fetch_mtf=fetch,
        events=[],
        fetch_funding=False,
        active_signals={},
        near_setups={},
    )
    nasdaq = next(item for item in briefing.instruments if item.instrument_id == "NASDAQ")
    gold = next(item for item in briefing.instruments if item.instrument_id == "XAU/USD")
    assert nasdaq.technical.available is False
    assert "yfinance timeout" in (nasdaq.technical.unavailable_reason or "")
    assert gold.technical.available is True
    message = format_pre_ny_briefing(briefing)
    assert "No disponible" in message


@pytest.mark.asyncio
async def test_maybe_send_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(tmp_path / "logs"))
    now = datetime(2026, 8, 24, 11, 20, tzinfo=UTC)
    sent: list[str] = []

    class _Notifier:
        enabled = True

        async def send(self, message: str, parse_mode: str = "Markdown") -> bool:
            sent.append(message)
            return True

    def fetch(_instrument_id: str) -> dict[str, pd.DataFrame]:
        return _frames(100.0)

    first = await maybe_send_briefing(
        settings,
        _Notifier(),
        now=now,
        fetch_mtf=fetch,
        events=[],
        etr_reports={},
        fetch_funding=False,
        active_signals={},
        near_setups={},
    )
    second = await maybe_send_briefing(
        settings,
        _Notifier(),
        now=now,
        fetch_mtf=fetch,
        events=[],
        etr_reports={},
        fetch_funding=False,
        active_signals={},
        near_setups={},
    )
    assert first.sent is True
    assert first.reason == "sent"
    assert first.chunks >= 1
    assert second.sent is False
    assert second.reason == "already_sent"
    assert len(sent) == first.chunks


def test_briefing_config_rejects_bad_open() -> None:
    with pytest.raises(ValueError, match="out of range|HH:MM"):
        BriefingConfig(ny_open_utc="25:99")


def test_parse_funding_payload_list() -> None:
    snapshot = parse_funding_payload(
        [{"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingTime": 1700000000000}]
    )
    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.rate == 0.0001
    assert snapshot.rate_pct_label() == "0.0100%"


def test_lockout_and_ny_window_helpers() -> None:
    now = datetime(2026, 8, 24, 11, 20, tzinfo=UTC)
    open_at = ny_open_at(now.date(), "12:00")
    cpi = _event("CPI", "USD", datetime(2026, 8, 24, 12, 30, tzinfo=UTC))
    old = _event("Old CPI", "USD", datetime(2026, 8, 23, 12, 30, tzinfo=UTC))
    hits_now = events_in_lockout([cpi, old], {"USD"}, now, lockout_before=60, lockout_after=30)
    hits_open = events_in_lockout([cpi, old], {"USD"}, open_at, lockout_before=60, lockout_after=30)
    assert hits_now == []
    assert [event.name for event in hits_open] == ["CPI"]
    window = events_in_ny_window([cpi, old], ny_open=open_at, lead_minutes=60)
    assert [event.name for event in window] == ["CPI"]


def test_inventory_calendar_ignores_bare_api_noise() -> None:
    ts = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)
    events = [
        _event("EIA Crude Inventories", "USD", ts),
        _event("API Application Count", "USD", ts),
    ]
    found = inventory_calendar_events(events)
    assert [event.name for event in found] == ["EIA Crude Inventories"]


@pytest.mark.asyncio
async def test_btc_funding_is_btc_only_and_skips_on_error(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    now = datetime(2026, 8, 24, 11, 20, tzinfo=UTC)

    def fetch(_instrument_id: str) -> dict[str, pd.DataFrame]:
        return _frames(100.0)

    snapshot = FundingSnapshot(symbol="BTCUSDT", rate=0.00012, funding_time_ms=1700000000000)
    with_funding = await build_briefing(
        settings,
        now=now,
        fetch_mtf=fetch,
        events=[],
        etr_reports={},
        btc_funding=snapshot,
        fetch_funding=False,
        active_signals={},
        near_setups={},
    )
    btc = next(item for item in with_funding.instruments if item.instrument_id == "BTC/USD")
    gold = next(item for item in with_funding.instruments if item.instrument_id == "XAU/USD")
    assert any("Funding BTCUSDT" in line for line in btc.sentiment.lines)
    assert any("proxy de crowding" in line for line in btc.sentiment.lines)
    assert not any("Funding" in line for line in gold.sentiment.lines)

    skipped = await build_briefing(
        settings,
        now=now,
        fetch_mtf=fetch,
        events=[],
        etr_reports={},
        btc_funding=None,
        fetch_funding=False,
        active_signals={},
        near_setups={},
    )
    btc_skip = next(item for item in skipped.instruments if item.instrument_id == "BTC/USD")
    assert not any("Funding" in line for line in btc_skip.sentiment.lines)

    # Production path: fetch_funding True but we inject nothing and monkeypatch
    # is not needed if we pass a raising try via fetch_funding True without
    # network — use the explicit error by calling sentiment through build with
    # fetch_funding False and no snapshot (already covered). Pillar stays up.


@pytest.mark.asyncio
async def test_funding_error_does_not_fail_btc_pillar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    now = datetime(2026, 8, 24, 11, 20, tzinfo=UTC)

    async def _boom() -> tuple[None, str]:
        return None, "timeout"

    monkeypatch.setattr("src.briefing.service.try_fetch_btc_funding", _boom)

    def fetch(_instrument_id: str) -> dict[str, pd.DataFrame]:
        return _frames(100.0)

    briefing = await build_briefing(
        settings,
        now=now,
        fetch_mtf=fetch,
        events=[],
        etr_reports={},
        fetch_funding=True,
        active_signals={},
        near_setups={},
    )
    btc = next(item for item in briefing.instruments if item.instrument_id == "BTC/USD")
    assert btc.sentiment.available is True
    assert any("Funding BTCUSDT: no disponible" in line for line in btc.sentiment.lines)


@pytest.mark.asyncio
async def test_scanner_cache_lines_and_ohlc_failure_isolation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    now = datetime(2026, 8, 24, 11, 20, tzinfo=UTC)

    def fetch(instrument_id: str) -> dict[str, pd.DataFrame]:
        if instrument_id == "NASDAQ":
            raise RuntimeError("yfinance timeout")
        return _frames(100.0)

    briefing = await build_briefing(
        settings,
        now=now,
        fetch_mtf=fetch,
        events=[],
        etr_reports={},
        fetch_funding=False,
        active_signals={
            "XAU/USD": {"direction": "BUY", "fired_at": int(now.timestamp())},
        },
        near_setups={
            "NASDAQ": {"kind": "aligned_pending_breakout"},
        },
    )
    gold = next(item for item in briefing.instruments if item.instrument_id == "XAU/USD")
    nasdaq = next(item for item in briefing.instruments if item.instrument_id == "NASDAQ")
    assert any("Rule C" in line and "BUY" in line for line in gold.technical.lines)
    assert nasdaq.technical.available is False
    assert any("aligned_pending_breakout" in line for line in nasdaq.technical.lines)
    assert "yfinance timeout" in (nasdaq.technical.unavailable_reason or "")
    assert gold.technical.available is True
    message = format_pre_ny_briefing(briefing)
    assert "Near-setup" in message


@pytest.mark.asyncio
async def test_etr_and_funding_absence_is_graceful(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    now = datetime(2026, 8, 24, 11, 20, tzinfo=UTC)

    def fetch(_instrument_id: str) -> dict[str, pd.DataFrame]:
        return _frames(80.0)

    briefing = await build_briefing(
        settings,
        now=now,
        fetch_mtf=fetch,
        events=[],
        etr_reports={},
        fetch_funding=False,
        active_signals={},
        near_setups={},
    )
    for item in briefing.instruments:
        assert item.sentiment.available is True
        assert any("Tesis ETR: no disponible" in line for line in item.sentiment.lines)
        assert item.fundamental.available is True
        assert any("sin 3★ propios" in line for line in item.fundamental.lines)
    assert briefing.shared_fundamental is not None
    assert any("Lockout USD ahora: no" in line for line in briefing.shared_fundamental.lines)


def test_bar_freshness_forming_and_stale() -> None:
    now = datetime(2026, 8, 25, 11, 20, tzinfo=UTC)
    forming = datetime(2026, 8, 25, 11, 0, tzinfo=UTC)
    stale = datetime(2026, 8, 25, 7, 0, tzinfo=UTC)
    assert bar_freshness(forming, now) == "barra 1h en curso"
    assert bar_freshness(stale, now) == "OHLC atrasado ~4h"
    assert bar_freshness(now - timedelta(hours=1, minutes=30), now) is None


@pytest.mark.asyncio
async def test_shared_macro_keeps_non_usd_and_does_not_repeat(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    now = datetime(2026, 8, 25, 11, 15, tzinfo=UTC)

    def fetch(_instrument_id: str) -> dict[str, pd.DataFrame]:
        return _frames(100.0)

    events = [
        _event("CPI m/m", "AUD", now + timedelta(hours=14)),
        _event("Core PCE Price Index m/m", "USD", now + timedelta(hours=25)),
        _event("Prelim GDP q/q", "USD", now + timedelta(hours=25)),
    ]
    briefing = await build_briefing(
        settings,
        now=now,
        fetch_mtf=fetch,
        events=events,
        etr_reports={},
        fetch_funding=False,
        active_signals={},
        near_setups={},
    )
    assert briefing.shared_fundamental is not None
    shared = "\n".join(briefing.shared_fundamental.lines)
    assert "CPI m/m" in shared
    assert "Core PCE" in shared
    assert "Prelim GDP" in shared
    gold = next(item for item in briefing.instruments if item.instrument_id == "XAU/USD")
    assert not any("CPI" in line or "PCE" in line for line in gold.fundamental.lines)
    message = format_pre_ny_briefing(briefing)
    assert message.count("Core PCE Price Index m/m") == 1
    assert briefing.synthesis == "hoy: sin lockout; riesgo = AUD CPI / PCE"
    assert "hoy: sin lockout; riesgo = AUD CPI / PCE" in message
    assert len(chunk_telegram(message)) == 1


@pytest.mark.asyncio
async def test_stale_etr_is_labeled(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    now = datetime(2026, 8, 25, 11, 20, tzinfo=UTC)

    def fetch(_instrument_id: str) -> dict[str, pd.DataFrame]:
        return _frames(100.0)

    etr = {
        "gold": EtrReport(
            asset="gold",
            label="Oro",
            price=2400.0,
            updated_at="11/08/2026",
            context_score=93.0,
            bias="alcista",
            estado="Zona",
            lectura_headline="",
            lectura_body="",
            h4_context="",
            m5_execution="",
            structure="",
        )
    }
    briefing = await build_briefing(
        settings,
        now=now,
        fetch_mtf=fetch,
        events=[],
        etr_reports=etr,
        etr_polled={"gold": "2026-08-11T22:45:47+00:00"},
        fetch_funding=False,
        active_signals={},
        near_setups={},
    )
    gold = next(item for item in briefing.instruments if item.instrument_id == "XAU/USD")
    assert any("caché vieja" in line for line in gold.sentiment.lines)
    assert not any("zona" in line.lower() or "Score tesis" in line for line in gold.sentiment.lines)


@pytest.mark.asyncio
async def test_partial_telegram_send_does_not_mark_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(
        "src.etr.alerts.chunk_telegram",
        lambda _message, limit=4000: ["chunk-a", "chunk-b"],
    )
    now = datetime(2026, 8, 24, 11, 20, tzinfo=UTC)
    sent: list[str] = []

    class _Notifier:
        enabled = True

        async def send(self, message: str, parse_mode: str = "Markdown") -> bool:
            sent.append(message)
            return message == "chunk-a"

    def fetch(_instrument_id: str) -> dict[str, pd.DataFrame]:
        return _frames(100.0)

    first = await maybe_send_briefing(
        settings,
        _Notifier(),
        now=now,
        fetch_mtf=fetch,
        events=[],
        etr_reports={},
        fetch_funding=False,
        active_signals={},
        near_setups={},
    )
    assert first.sent is False
    assert first.reason == "send_failed"
    assert sent == ["chunk-a", "chunk-b"]
    second = await maybe_send_briefing(
        settings,
        _Notifier(),
        now=now,
        fetch_mtf=fetch,
        events=[],
        etr_reports={},
        fetch_funding=False,
        active_signals={},
        near_setups={},
    )
    assert second.reason != "already_sent"


def test_header_synthesis_lockout_and_missing_calendar() -> None:
    now = datetime(2026, 8, 25, 11, 45, tzinfo=UTC)
    cpi = _event("CPI m/m", "USD", datetime(2026, 8, 25, 12, 30, tzinfo=UTC))
    pce = _event("Core PCE Price Index m/m", "USD", now + timedelta(hours=20))
    assert (
        build_header_synthesis(events=[cpi, pce], now=now) == "hoy: lockout USD (CPI); riesgo = PCE"
    )
    assert (
        build_header_synthesis(
            events=[],
            now=now,
            news_error="calendario no disponible: timeout",
        )
        == "hoy: calendario no disponible"
    )
    assert build_header_synthesis(events=[], now=now) == "hoy: sin lockout; riesgo = sin claves 3★"
