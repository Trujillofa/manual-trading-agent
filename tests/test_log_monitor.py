"""Tests for managed log rotation monitoring."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.dashboard.log_status import run_logs_status
from src.scanner import log_monitor


def test_managed_log_statuses_reports_ok_below_warn_threshold(tmp_path: Path) -> None:
    (tmp_path / "scan.log").write_bytes(b"x" * 1000)
    (tmp_path / "signal_audit.jsonl").write_bytes(b"y" * 2000)

    statuses = log_monitor.managed_log_statuses(logs_dir=tmp_path, threshold_bytes=10_000)

    assert len(statuses) == 2
    assert statuses[0].level == "ok"
    assert statuses[1].level == "ok"
    assert "10.0%" in log_monitor.format_log_status_report(statuses)


def test_managed_log_statuses_warn_and_critical_levels(tmp_path: Path) -> None:
    threshold = 1000
    (tmp_path / "scan.log").write_bytes(b"x" * 850)
    (tmp_path / "signal_audit.jsonl").write_bytes(b"y" * 960)

    statuses = log_monitor.managed_log_statuses(logs_dir=tmp_path, threshold_bytes=threshold)

    assert statuses[0].level == "warn"
    assert statuses[1].level == "critical"


def test_build_log_alert_messages_only_on_escalation(tmp_path: Path) -> None:
    threshold = 1000
    (tmp_path / "scan.log").write_bytes(b"x" * 850)
    statuses = log_monitor.managed_log_statuses(logs_dir=tmp_path, threshold_bytes=threshold)

    messages, next_state = log_monitor.build_log_alert_messages(statuses, {})
    assert len(messages) == 1
    assert next_state["scan.log"] == "warn"

    repeat_messages, repeat_state = log_monitor.build_log_alert_messages(statuses, next_state)
    assert repeat_messages == []
    assert repeat_state == next_state


@pytest.mark.asyncio
async def test_run_logs_status_notify_sends_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    threshold = 1000
    (tmp_path / "scan.log").write_bytes(b"x" * 850)
    (tmp_path / "signal_audit.jsonl").write_bytes(b"y" * 100)

    sent: list[str] = []

    class FakeNotifier:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def send(self, message: str, parse_mode: str = "Markdown") -> bool:
            sent.append(message)
            return True

    monkeypatch.setattr(log_monitor, "_logs_dir", lambda: tmp_path)
    monkeypatch.setattr("src.dashboard.log_status._load_log_alert_state", lambda: {})
    monkeypatch.setattr("src.dashboard.log_status._save_log_alert_state", lambda _state: None)
    monkeypatch.setattr("src.dashboard.log_status.TelegramNotifier", FakeNotifier)
    monkeypatch.setattr(
        "src.dashboard.log_status.get_settings",
        lambda: type(
            "S",
            (),
            {
                "telegram": type(
                    "T",
                    (),
                    {"enabled": True, "is_configured": True, "bot_token": "t", "chat_id": "1"},
                )()
            },
        )(),
    )
    monkeypatch.setattr(
        "src.dashboard.log_status.managed_log_statuses",
        lambda: log_monitor.managed_log_statuses(logs_dir=tmp_path, threshold_bytes=threshold),
    )

    await run_logs_status(notify=True)

    assert len(sent) == 1
    assert "scan.log" in sent[0]
