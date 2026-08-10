"""Tests for runtime health checks."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.config.settings import Settings, TelegramConfig
from src.dashboard import report as dashboard_report


@pytest.fixture(autouse=True)
def _isolate_telegram_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """TelegramConfig.__post_init__ reads token/chat/poll from the process env.

    Clear those so unit tests control configuration via the constructor only.
    """
    monkeypatch.delenv("TELEGRAM_POLL_ENABLED", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


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

    monkeypatch.setattr(dashboard_report, "_scan_log_path", lambda: scan_log)
    monkeypatch.setattr(dashboard_report, "_telegram_heartbeat_path", lambda: heartbeat)
    monkeypatch.setattr(
        dashboard_report,
        "get_settings",
        lambda: Settings(telegram=TelegramConfig(enabled=True, bot_token="token", chat_id="chat")),
    )

    ok, message = dashboard_report._healthcheck_status(now)

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

    monkeypatch.setattr(dashboard_report, "_scan_log_path", lambda: scan_log)
    monkeypatch.setattr(dashboard_report, "_telegram_heartbeat_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(
        dashboard_report,
        "get_settings",
        lambda: Settings(telegram=TelegramConfig(enabled=False)),
    )

    ok, message = dashboard_report._healthcheck_status(now)

    assert ok is True
    assert message == "ok"


def test_healthcheck_fails_when_scan_log_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)

    monkeypatch.setattr(dashboard_report, "_scan_log_path", lambda: tmp_path / "missing-scan.log")
    monkeypatch.setattr(dashboard_report, "_telegram_heartbeat_path", lambda: tmp_path / "missing-heartbeat.json")
    monkeypatch.setattr(
        dashboard_report,
        "get_settings",
        lambda: Settings(telegram=TelegramConfig(enabled=False)),
    )

    ok, message = dashboard_report._healthcheck_status(now)

    assert ok is False
    assert message == "scan log missing"


def test_healthcheck_fails_when_scan_log_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan_log = tmp_path / "scan.log"
    scan_log.write_text("scan ok\n", encoding="utf-8")
    now = datetime.now(UTC)
    stale_scan_ts = (now - timedelta(minutes=40)).timestamp()
    os.utime(scan_log, (stale_scan_ts, stale_scan_ts))

    monkeypatch.setattr(dashboard_report, "_scan_log_path", lambda: scan_log)
    monkeypatch.setattr(dashboard_report, "_telegram_heartbeat_path", lambda: tmp_path / "missing-heartbeat.json")
    monkeypatch.setattr(
        dashboard_report,
        "get_settings",
        lambda: Settings(telegram=TelegramConfig(enabled=False)),
    )

    ok, message = dashboard_report._healthcheck_status(now)

    assert ok is False
    assert "scan log stale" in message


def test_healthcheck_fails_when_heartbeat_missing_for_configured_telegram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan_log = tmp_path / "scan.log"
    scan_log.write_text("scan ok\n", encoding="utf-8")
    now = datetime.now(UTC)
    fresh_scan_ts = (now - timedelta(minutes=2)).timestamp()
    os.utime(scan_log, (fresh_scan_ts, fresh_scan_ts))

    monkeypatch.setattr(dashboard_report, "_scan_log_path", lambda: scan_log)
    monkeypatch.setattr(dashboard_report, "_telegram_heartbeat_path", lambda: tmp_path / "missing-heartbeat.json")
    monkeypatch.setattr(
        dashboard_report,
        "get_settings",
        lambda: Settings(telegram=TelegramConfig(enabled=True, bot_token="token", chat_id="chat")),
    )

    ok, message = dashboard_report._healthcheck_status(now)

    assert ok is False
    assert message == "telegram heartbeat missing"


def test_healthcheck_skips_telegram_heartbeat_when_poll_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan_log = tmp_path / "scan.log"
    scan_log.write_text("scan ok\n", encoding="utf-8")
    now = datetime.now(UTC)
    fresh_scan_ts = (now - timedelta(minutes=2)).timestamp()
    os.utime(scan_log, (fresh_scan_ts, fresh_scan_ts))

    monkeypatch.setattr(dashboard_report, "_scan_log_path", lambda: scan_log)
    monkeypatch.setattr(dashboard_report, "_telegram_heartbeat_path", lambda: tmp_path / "missing-heartbeat.json")
    monkeypatch.setattr(
        dashboard_report,
        "get_settings",
        lambda: Settings(
            telegram=TelegramConfig(
                enabled=True,
                poll_enabled=False,
                bot_token="token",
                chat_id="chat",
            )
        ),
    )

    ok, message = dashboard_report._healthcheck_status(now)

    assert ok is True
    assert message == "ok"


def test_healthcheck_fails_when_heartbeat_status_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan_log = tmp_path / "scan.log"
    heartbeat = tmp_path / "telegram_heartbeat.json"
    scan_log.write_text("scan ok\n", encoding="utf-8")
    heartbeat.write_text('{"status": "error", "error": "duplicate consumer"}', encoding="utf-8")

    now = datetime.now(UTC)
    fresh_ts = (now - timedelta(minutes=1)).timestamp()
    os.utime(scan_log, (fresh_ts, fresh_ts))
    os.utime(heartbeat, (fresh_ts, fresh_ts))

    monkeypatch.setattr(dashboard_report, "_scan_log_path", lambda: scan_log)
    monkeypatch.setattr(dashboard_report, "_telegram_heartbeat_path", lambda: heartbeat)
    monkeypatch.setattr(
        dashboard_report,
        "get_settings",
        lambda: Settings(telegram=TelegramConfig(enabled=True, bot_token="token", chat_id="chat")),
    )

    ok, message = dashboard_report._healthcheck_status(now)

    assert ok is False
    assert "telegram heartbeat error" in message


def test_healthcheck_skips_heartbeat_when_enabled_but_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan_log = tmp_path / "scan.log"
    scan_log.write_text("scan ok\n", encoding="utf-8")
    now = datetime.now(UTC)
    fresh_scan_ts = (now - timedelta(minutes=2)).timestamp()
    os.utime(scan_log, (fresh_scan_ts, fresh_scan_ts))

    monkeypatch.setattr(dashboard_report, "_scan_log_path", lambda: scan_log)
    monkeypatch.setattr(dashboard_report, "_telegram_heartbeat_path", lambda: tmp_path / "missing-heartbeat.json")
    monkeypatch.setattr(
        dashboard_report,
        "get_settings",
        lambda: Settings(telegram=TelegramConfig(enabled=True, bot_token=None, chat_id=None)),
    )

    ok, message = dashboard_report._healthcheck_status(now)

    assert ok is True
    assert message == "ok"
