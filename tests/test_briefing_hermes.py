"""Hermes NY-plan client: parse, attach, skip, timeout, bad JSON."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from src.briefing.formatter import format_pre_ny_briefing
from src.briefing.hermes import (
    HermesClient,
    HermesError,
    format_ny_plan,
    parse_plans,
    unavailable_plan,
)
from src.briefing.service import build_briefing
from src.config.settings import Settings
from src.etr.alerts import chunk_telegram


def _settings(tmp_path: Path, *, hermes_enabled: bool = True) -> Settings:
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
        "telegram": {"enabled": True},
        "briefing": {
            "enabled": True,
            "ny_open_utc": "12:00",
            "lead_minutes": 60,
            "hermes": {"enabled": hermes_enabled, "timeout_seconds": 5},
        },
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return Settings.load(path)


def _ok_json() -> str:
    return """
    {
      "XAU/USD": {
        "htf_trend": "rango",
        "htf_basis": "ADX 1h 18",
        "support": ["2380"],
        "resistance": ["2420"],
        "recommendation": "wait",
        "why": "rango y ETR esperando zona",
        "invalidation": "cierre 1h > 2450",
        "confidence": "medium"
      },
      "BTC/USD": {
        "htf_trend": "alcista",
        "htf_basis": "precio > SMA50 1h",
        "support": ["64000"],
        "resistance": ["67000"],
        "recommendation": "buy_pullback",
        "why": "tendencia 1h, esperar retroceso",
        "invalidation": "cierre 1h < 63000",
        "confidence": "low",
        "honesty": "sin funding en este test"
      },
      "NASDAQ": {
        "htf_trend": "bajista",
        "htf_basis": "EMA 20<50 15m",
        "support": ["19800"],
        "resistance": ["20200"],
        "recommendation": "sell_rally",
        "why": "estructura débil 15m",
        "invalidation": "cierre 1h > 20300",
        "confidence": "medium"
      },
      "OIL": {
        "htf_trend": "indefinido",
        "htf_basis": "OHLC incompleto",
        "support": [],
        "resistance": [],
        "recommendation": "stand_aside",
        "why": "inventarios recientes, sin borde claro",
        "invalidation": "esperar cierre 1h fuera de rango",
        "confidence": "low",
        "honesty": "datos flacos"
      }
    }
    """


def test_parse_plans_success_and_aliases() -> None:
    ids = ["XAU/USD", "BTC/USD", "NASDAQ", "OIL"]
    plans = parse_plans(_ok_json(), ids)
    assert plans["XAU/USD"].recommendation == "esperar"
    assert plans["BTC/USD"].recommendation == "compra en retroceso"
    assert plans["NASDAQ"].recommendation == "venta en rally"
    assert plans["OIL"].recommendation == "no operar"
    assert plans["XAU/USD"].support == ("2380",)
    assert "ADX" in plans["XAU/USD"].htf_basis


def test_parse_plans_bad_json_fallback() -> None:
    plans = parse_plans("esto no es json", ["XAU/USD"])
    plan = plans["XAU/USD"]
    assert plan.available is True
    assert plan.recommendation == "no operar"
    assert "JSON" in (plan.honesty or "")
    assert "esto no es json" in plan.why


def test_format_unavailable() -> None:
    lines = format_ny_plan(unavailable_plan("timeout 45s"))
    assert lines == ["*Plan NY* no disponible: timeout 45s"]


@pytest.mark.asyncio
async def test_attach_success_prints_plan_ny(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    called = {"n": 0}

    async def fake(prompt: str) -> str:
        called["n"] += 1
        assert "XAU/USD" in prompt
        assert "NO inventes D1/H4" in prompt
        return _ok_json()

    briefing = await build_briefing(
        settings,
        now=datetime(2026, 8, 24, 11, 20, tzinfo=UTC),
        fetch_mtf=lambda _i: {},
        events=[],
        etr_reports={},
        fetch_funding=False,
        active_signals={},
        near_setups={},
        hermes_complete=fake,
    )
    assert called["n"] == 1
    assert all(item.ny_plan is not None and item.ny_plan.available for item in briefing.instruments)
    assert all(item.ny_plan.action == "STAND_ASIDE" for item in briefing.instruments)
    assert all(item.ny_plan.recommendation == "no operar" for item in briefing.instruments)
    message = format_pre_ny_briefing(briefing)
    assert message.count("*Plan NY*") == 4
    assert "no operar" in message
    assert "compra en retroceso" not in message
    assert "venta en rally" not in message
    assert "BUY" not in message
    assert "SELL" not in message
    assert "*Plan NY* no disponible" not in message
    assert "alcista" in message
    assert len(chunk_telegram(message)) == 1


@pytest.mark.asyncio
async def test_attach_timeout(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def boom(_prompt: str) -> str:
        raise HermesError("timeout 5s")

    briefing = await build_briefing(
        settings,
        now=datetime(2026, 8, 24, 11, 20, tzinfo=UTC),
        fetch_mtf=lambda _i: {},
        events=[],
        etr_reports={},
        fetch_funding=False,
        active_signals={},
        near_setups={},
        hermes_complete=boom,
    )
    gold = briefing.instruments[0]
    assert gold.ny_plan is not None
    assert gold.ny_plan.available is True
    assert gold.ny_plan.recommendation == "no operar"
    assert gold.ny_plan.action == "STAND_ASIDE"
    assert "timeout" in (gold.ny_plan.honesty or "")
    message = format_pre_ny_briefing(briefing)
    assert "Plan NY" in message
    assert "no operar" in message
    assert "timeout" in message
    assert "*Plan NY* no disponible" not in message


@pytest.mark.asyncio
async def test_attach_bad_json(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def junk(_prompt: str) -> str:
        return "Hermes habló en prosa sin llaves"

    briefing = await build_briefing(
        settings,
        now=datetime(2026, 8, 24, 11, 20, tzinfo=UTC),
        fetch_mtf=lambda _i: {},
        events=[],
        etr_reports={},
        fetch_funding=False,
        active_signals={},
        near_setups={},
        hermes_complete=junk,
    )
    gold = briefing.instruments[0]
    assert gold.ny_plan is not None
    assert gold.ny_plan.available is True
    assert gold.ny_plan.recommendation == "no operar"
    assert "JSON" in (gold.ny_plan.honesty or "")
    message = format_pre_ny_briefing(briefing)
    assert "*Plan NY*" in message
    assert "no operar" in message


@pytest.mark.asyncio
async def test_hermes_skip_when_disabled(tmp_path: Path) -> None:
    settings = _settings(tmp_path, hermes_enabled=False)
    called = {"n": 0}

    async def fake(_prompt: str) -> str:
        called["n"] += 1
        return _ok_json()

    briefing = await build_briefing(
        settings,
        now=datetime(2026, 8, 24, 11, 20, tzinfo=UTC),
        fetch_mtf=lambda _i: {},
        events=[],
        etr_reports={},
        fetch_funding=False,
        active_signals={},
        near_setups={},
        hermes_complete=fake,
    )
    assert called["n"] == 0
    assert all(item.ny_plan is not None and item.ny_plan.available for item in briefing.instruments)
    assert all(item.ny_plan.recommendation == "no operar" for item in briefing.instruments)
    assert all(item.ny_plan.action == "STAND_ASIDE" for item in briefing.instruments)
    message = format_pre_ny_briefing(briefing)
    assert "Plan NY" in message
    assert "Hermes deshabilitado" in message


@pytest.mark.asyncio
async def test_cli_missing_binary() -> None:
    client = HermesClient(enabled=True, endpoint="", cli_command="hermes-not-installed-xyz")
    with pytest.raises(HermesError, match="no encontrado"):
        await client.complete("ping")


@pytest.mark.asyncio
async def test_http_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    client = HermesClient(enabled=True, endpoint="http://127.0.0.1:9", timeout_seconds=0.01)

    async def fake_post(self, *args, **kwargs):  # noqa: ANN001
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(HermesError, match="timeout"):
        await client.complete("ping")


def test_parse_plans_messy_hermes_shape() -> None:
    raw = """
    session_id: abc
    {
      "XAU/USD": {
        "htf_trend": "rango con sesgo alcista",
        "support": "4700 / 4680",
        "resistance": "4720 / 4750",
        "recommendation": "ESPERAR - no perseguir",
        "why": "ADX 20",
        "invalidation": "cierre 1h bajo 4680",
        "confidence": 60
      }
    }
    """
    plan = parse_plans(raw, ["XAU/USD"])["XAU/USD"]
    assert plan.recommendation == "esperar"
    assert plan.support == ("4700", "4680")
    assert plan.confidence == "media"


def test_format_ny_plan_keeps_levels_short() -> None:
    from src.briefing.models import NyPlan

    lines = format_ny_plan(
        NyPlan(
            available=True,
            htf_trend="alcista",
            htf_basis="ADX 1h 18 con mucho texto extra que no cabe",
            support=("4693.7 (SMA50 1h)", "4659.5 (rango 15m)"),
            resistance=("4716.4 (techo)",),
            recommendation="esperar",
            why="rango",
            invalidation="4659",
            confidence="media",
        )
    )
    assert lines[0] == "*Plan NY* esperar · HTF alcista · S 4693.7/4659.5 / R 4716.4"


@pytest.mark.asyncio
async def test_attach_hermes_false_still_emits_v2_action(tmp_path: Path) -> None:
    settings = _settings(tmp_path, hermes_enabled=True)
    called = {"n": 0}

    async def fake(_prompt: str) -> str:
        called["n"] += 1
        return _ok_json()

    briefing = await build_briefing(
        settings,
        now=datetime(2026, 8, 24, 11, 20, tzinfo=UTC),
        fetch_mtf=lambda _i: {},
        events=[],
        etr_reports={},
        fetch_funding=False,
        active_signals={},
        near_setups={},
        hermes_complete=fake,
        attach_hermes=False,
    )
    assert called["n"] == 0
    gold = briefing.instruments[0]
    assert gold.ny_plan is not None
    assert gold.ny_plan.recommendation == "no operar"
    assert gold.ny_plan.action == "STAND_ASIDE"


@pytest.mark.asyncio
async def test_attach_injected_enter_overwrites_hermes_buy(tmp_path: Path) -> None:
    from src.briefing.hermes import attach_ny_plans
    from src.briefing.models import InstrumentBriefing, Pillar, PreNyBriefing
    from src.config.settings import BriefingHermesConfig

    missing = Pillar(name="Técnico", available=False, unavailable_reason="test")
    briefing = PreNyBriefing(
        session_date="2026-08-25",
        generated_at=datetime(2026, 8, 25, 11, 0, tzinfo=UTC),
        ny_open_utc="12:00",
        lead_minutes=60,
        instruments=[
            InstrumentBriefing(
                instrument_id="BTC/USD",
                display_name="Bitcoin",
                yf_symbol="BTC-USD",
                technical=missing,
                fundamental=missing,
                sentiment=missing,
            )
        ],
    )

    async def fake(_prompt: str) -> str:
        return _ok_json()

    out = await attach_ny_plans(
        briefing,
        cfg=BriefingHermesConfig(enabled=True, timeout_seconds=5),
        complete=fake,
        actions={"BTC/USD": "ENTER_ONLY_IF"},
    )
    plan = out.instruments[0].ny_plan
    assert plan is not None
    assert plan.recommendation == "entrar solo si V2"
    assert plan.action == "ENTER_ONLY_IF"
    assert plan.htf_trend == "alcista"
    assert "compra" not in plan.recommendation
