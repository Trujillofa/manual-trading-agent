"""Tests for runtime health checks."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src import cli
from src.config.settings import Settings, TelegramConfig


def test_healthcheck_requires_recent_scan_and_telegram_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan_log = tmp_path / "scan.log"
    heartbeat = tmp_path / "telegram_heartbeat.json"
    scan_log.write_text("scan ok\n", encoding="utf-8")
    heartbeat.write_text("{}", encoding="utf-8")

    now = datetime.now(UTC)
    fresh_scan_time = now - timedelta(minutes=5)
    stale_heartbeat_time = now - timedelta(minutes=10)
    scan_ts = fresh_scan_time.timestamp()
    heartbeat_ts = stale_heartbeat_time.timestamp()
    os.utime(scan_log, (scan_ts, scan_ts))
    os.utime(heartbeat, (heartbeat_ts, heartbeat_ts))

    monkeypatch.setattr(cli, "_scan_log_path", lambda: scan_log)
    monkeypatch.setattr(cli, "_telegram_heartbeat_path", lambda: heartbeat)
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(telegram=TelegramConfig(enabled=True, bot_token="token", chat_id="chat")),
    )

    ok, message = cli._healthcheck_status(now)

    assert ok is False
    assert "telegram heartbeat stale" in message


def test_healthcheck_skips_telegram_heartbeat_when_telegram_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan_log = tmp_path / "scan.log"
    scan_log.write_text("scan ok\n", encoding="utf-8")
    now = datetime.now(UTC)
    scan_ts = (now - timedelta(minutes=5)).timestamp()
    os.utime(scan_log, (scan_ts, scan_ts))

    monkeypatch.setattr(cli, "_scan_log_path", lambda: scan_log)
    monkeypatch.setattr(cli, "_telegram_heartbeat_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(telegram=TelegramConfig(enabled=False)),
    )

    ok, message = cli._healthcheck_status(now)

    assert ok is True
    assert message == "ok"
