"""A starved renderer must alert, not stay silent forever.

Busy is a scheduling fact, so a single busy outcome stays non-alerting. But the
poller used to count busy as "unavailable" forever: with a wedged holder every
render answered busy and nothing was ever sent, while ``/health`` reported ok.
After ``RENDER_BUSY_ALERT_AFTER_POLLS`` consecutive busy cycles one alert fires.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config.settings import EtrConfig
from src.etr import service
from src.etr.client import EtrRendererBusyError
from src.etr.parser import parse_rendered_text
from src.etr.state import etr_state_path

FIXTURE = Path(__file__).parent / "fixtures" / "etr_rendered_btc.txt"


class _Notifier:
    enabled = True

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> bool:
        self.messages.append(message)
        return True


class _BusyClient:
    async def fetch_report(self, asset: str):
        raise EtrRendererBusyError(f"ETR renderer busy for {asset}")


class _ReadyClient:
    def __init__(self) -> None:
        text = FIXTURE.read_text(encoding="utf-8")
        self.report = parse_rendered_text(text, "btc")
        self.report.updated_at = "12/08/2026"
        self.report.bias = "Bajista"

    async def fetch_report(self, asset: str):
        return self.report


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        etr=EtrConfig(
            assets=["btc"],
            min_poll_interval_seconds=1,
            telegram_alerts=True,
            login="busy-streak@example.com",
            password="offline",
        )
    )


def _meta(logs: Path) -> dict:
    return json.loads(etr_state_path().read_text(encoding="utf-8")).get("meta", {})


def _audit(logs: Path) -> list[dict]:
    path = logs / "etr_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_consecutive_busy_polls_alert_at_the_threshold_once(
    tmp_path: Path, monkeypatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    monkeypatch.setattr(service, "build_client", lambda config: _BusyClient())
    notifier = _Notifier()

    summaries = [
        await service.poll_and_notify(_settings(), notifier, force=True)
        for _ in range(service.RENDER_BUSY_ALERT_AFTER_POLLS)
    ]

    # Only the poll that reaches the threshold sends; nothing was sent before it.
    assert [summary.notified_count for summary in summaries] == [0, 0, 1]
    assert len(notifier.messages) == 1
    assert "starved" in notifier.messages[0]
    assert "3 consecutive polls" in notifier.messages[0]
    assert _meta(logs)["renderer_busy_streak"] == service.RENDER_BUSY_ALERT_AFTER_POLLS
    assert _meta(logs)["renderer_busy_since"]

    events = [record["event"] for record in _audit(logs)]
    assert "renderer_busy_streak" in events
    streak = next(r for r in _audit(logs) if r["event"] == "renderer_busy_streak")
    assert streak["consecutive_busy_polls"] == service.RENDER_BUSY_ALERT_AFTER_POLLS
    assert streak["assets"] == ["btc"]

    # A wedged renderer keeps the streak growing but does not alert again.
    await service.poll_and_notify(_settings(), notifier, force=True)
    assert len(notifier.messages) == 1
    assert _meta(logs)["renderer_busy_streak"] == service.RENDER_BUSY_ALERT_AFTER_POLLS + 1


@pytest.mark.asyncio
async def test_one_busy_poll_does_not_alert(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    monkeypatch.setattr(service, "build_client", lambda config: _BusyClient())
    notifier = _Notifier()

    summary = await service.poll_and_notify(_settings(), notifier, force=True)

    assert summary.error_count == 0
    assert summary.notified_count == 0
    assert notifier.messages == []
    assert _meta(logs)["renderer_busy_streak"] == 1


@pytest.mark.asyncio
async def test_a_served_render_resets_the_streak(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    notifier = _Notifier()

    monkeypatch.setattr(service, "build_client", lambda config: _BusyClient())
    for _ in range(service.RENDER_BUSY_ALERT_AFTER_POLLS - 1):
        await service.poll_and_notify(_settings(), notifier, force=True)
    assert _meta(logs)["renderer_busy_streak"] == service.RENDER_BUSY_ALERT_AFTER_POLLS - 1

    monkeypatch.setattr(service, "build_client", lambda config: _ReadyClient())
    served = await service.poll_and_notify(_settings(), notifier, force=True)

    assert served.error_count == 0
    assert _meta(logs)["renderer_busy_streak"] == 0
    assert "renderer_busy_since" not in _meta(logs)
    assert notifier.messages == []


@pytest.mark.asyncio
async def test_busy_streak_does_not_alert_when_notifications_are_off(
    tmp_path: Path, monkeypatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    monkeypatch.setattr(service, "build_client", lambda config: _BusyClient())
    notifier = _Notifier()

    for _ in range(service.RENDER_BUSY_ALERT_AFTER_POLLS):
        summary = await service.poll_and_notify(_settings(), notifier, force=True, notify=False)

    assert summary.notified_count == 0
    assert notifier.messages == []
    assert _meta(logs)["renderer_busy_streak"] == service.RENDER_BUSY_ALERT_AFTER_POLLS
