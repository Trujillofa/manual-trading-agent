"""Tests for Telegram config fallback behavior."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from src.config.settings import Settings


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def test_settings_loads_telegram_env_fallback_when_yaml_keys_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "settings.yaml"
    _write_yaml(
        config_path,
        """
        trading:
          majors: ["EUR/USD"]
        telegram:
          enabled: true
        """,
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")

    settings = Settings.load(config_path)

    assert settings.telegram.bot_token == "env-token"
    assert settings.telegram.chat_id == "123456"
    assert settings.telegram.is_configured is True


def test_settings_explicit_telegram_values_override_env_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "settings.yaml"
    _write_yaml(
        config_path,
        """
        trading:
          majors: ["EUR/USD"]
        telegram:
          enabled: true
          bot_token: explicit-token
          chat_id: explicit-chat
        """,
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")

    settings = Settings.load(config_path)

    assert settings.telegram.bot_token == "explicit-token"
    assert settings.telegram.chat_id == "explicit-chat"


def test_settings_resolve_telegram_placeholders_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "settings.yaml"
    _write_yaml(
        config_path,
        """
        trading:
          majors: ["EUR/USD"]
        telegram:
          enabled: true
          bot_token: "${TELEGRAM_BOT_TOKEN}"
          chat_id: "${TELEGRAM_CHAT_ID}"
        """,
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")

    settings = Settings.load(config_path)

    assert settings.telegram.bot_token == "env-token"
    assert settings.telegram.chat_id == "env-chat"
