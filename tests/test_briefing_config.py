"""Briefing config load and production YAML defaults."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.config.settings import BriefingInstrumentConfig, Settings


def test_settings_load_briefing_defaults(tmp_path: Path) -> None:
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
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    settings = Settings.load(path)
    assert settings.briefing.enabled is True
    assert settings.briefing.ny_open_utc == "12:00"
    assert settings.briefing.lead_minutes == 60
    assert [item.id for item in settings.briefing.instruments] == [
        "XAU/USD",
        "BTC/USD",
        "NASDAQ",
        "OIL",
    ]
    assert settings.telegram.pre_ny_briefing_notifications is True
    assert settings.briefing.hermes.enabled is True
    assert settings.briefing.hermes.endpoint == ""
    assert settings.briefing.hermes.cli_command == "hermes"
    assert settings.briefing.hermes.timeout_seconds == 240


def test_settings_load_briefing_overrides(tmp_path: Path) -> None:
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
        "telegram": {"enabled": True, "pre_ny_briefing_notifications": False},
        "briefing": {
            "enabled": False,
            "ny_open_utc": "13:00",
            "lead_minutes": 45,
            "instruments": [{"id": "XAU/USD", "etr_asset": "gold"}],
            "hermes": {"enabled": False, "timeout_seconds": 12, "endpoint": "http://127.0.0.1:9"},
        },
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    settings = Settings.load(path)
    assert settings.briefing.enabled is False
    assert settings.briefing.ny_open_utc == "13:00"
    assert settings.briefing.lead_minutes == 45
    assert [item.id for item in settings.briefing.instruments] == ["XAU/USD"]
    assert settings.telegram.pre_ny_briefing_notifications is False
    assert settings.briefing.hermes.enabled is False
    assert settings.briefing.hermes.timeout_seconds == 12
    assert settings.briefing.hermes.endpoint == "http://127.0.0.1:9"


def test_repo_settings_yaml_enables_pre_ny_briefing() -> None:
    repo_yaml = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
    settings = Settings.load(repo_yaml)
    assert settings.briefing.enabled is True
    assert settings.briefing.ny_open_utc == "12:00"
    assert settings.briefing.lead_minutes == 60
    assert settings.briefing.news_hours_ahead == 36
    assert settings.telegram.pre_ny_briefing_notifications is True
    assert settings.briefing.hermes.enabled is True
    assert settings.briefing.hermes.cli_command == "hermes"
    assert settings.briefing.hermes.timeout_seconds == 240
    assert settings.briefing.hermes.endpoint == ""
    assert [item.id for item in settings.briefing.instruments] == [
        "XAU/USD",
        "BTC/USD",
        "NASDAQ",
        "OIL",
    ]


def test_invalid_etr_asset_rejected() -> None:
    with pytest.raises(ValueError, match="etr_asset"):
        BriefingInstrumentConfig(id="XAU/USD", etr_asset="silver")
