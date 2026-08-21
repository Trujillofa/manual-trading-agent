"""ETR config loading and soft-disable without credentials."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.config.settings import EtrConfig, Settings


def test_etr_config_soft_disables_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ETRACADEMY_LOGIN", raising=False)
    monkeypatch.delenv("ETRACADEMY_PASSWORD", raising=False)
    monkeypatch.delenv("ETR_ENABLED", raising=False)
    cfg = EtrConfig(enabled=True, login=None, password=None)
    assert cfg.enabled is False
    assert cfg.has_credentials is False


def test_etr_config_enabled_with_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ETR_ENABLED", raising=False)
    cfg = EtrConfig(enabled=True, login="user@example.com", password="secret")
    assert cfg.enabled is True
    assert cfg.has_credentials is True


def test_settings_load_includes_etr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ETRACADEMY_LOGIN", "user@example.com")
    monkeypatch.setenv("ETRACADEMY_PASSWORD", "secret")
    monkeypatch.delenv("ETR_ENABLED", raising=False)

    # Minimal config based on production shape
    payload = {
        "trading": {"mode": "paper", "pairs": {"majors": ["EUR/USD"], "minors": [], "shadow": []}},
        "timeframes": {"regime": "1h", "momentum": "30m", "entry": "15m"},
        "strategy": {
            "rsi_period": 14,
            "rsi_overbought": 70,
            "rsi_oversold": 30,
            "sma_period": 50,
            "lookback_bars": 20,
            "ema": {},
        },
        "risk": {
            "tp_atr_multiplier": 1.0,
            "sl_atr_multiplier": 3.0,
        },
        "news": {"enabled": False},
        "data": {"provider": "yfinance"},
        "telegram": {"enabled": False},
        "etr": {
            "enabled": True,
            "assets": ["btc", "gold"],
            "min_poll_interval_seconds": 900,
        },
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    settings = Settings.load(path)
    assert settings.etr.enabled is True
    assert settings.etr.assets == ["btc", "gold"]
    assert settings.etr.login == "user@example.com"
    assert settings.etr.telegram_alert_fields == [
        "bias",
        "primary_direction",
        "price_in_primary_zone",
    ]


def test_etr_config_rejects_unknown_telegram_alert_field() -> None:
    with pytest.raises(ValueError, match="telegram_alert_fields"):
        EtrConfig(telegram_alert_fields=["bias", "not_a_field"])
