"""Escalation has to target the failure path the deployed topology can reach.

``etr-scan`` is one step of a strictly sequential shell loop, ``_fetch_reports``
awaits assets one at a time, and uvicorn runs a single worker. At most one
/render is ever in flight, so the renderer's 20 s queue timeout never fires,
``outcome=busy`` never happens, and the busy-streak alert can never fire — while
a wedged renderer or a page that never hydrates produces nothing at all and, in
the old code, said nothing at all either.

The busy streak stays (it is reachable when a second client shares the sidecar)
but escalation now also, and mainly, counts consecutive poll cycles that produced
no analysis for any asset.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config.settings import EtrConfig
from src.etr import service
from src.etr.client import EtrRenderIncompleteError

ASSETS = ("btc", "gold")


class _FailingClient:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch_report(self, asset: str):
        self.calls += 1
        raise EtrRenderIncompleteError(
            f"ETR render for {asset} did not load the analysis page after 2 attempts"
        )


class _Notifier:
    enabled = True

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> bool:
        self.messages.append(message)
        return True


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        etr=EtrConfig(
            assets=list(ASSETS),
            telegram_alerts=True,
            login="alerts@example.com",
            password="offline",
            # Keep the per-asset error alert out of the way so the streak alert
            # is what these assertions are about.
            error_alert_cooldown_minutes=24 * 60,
        )
    )


def _audit(logs: Path) -> list[dict]:
    path = logs / "etr_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _streak_alerts(notifier: _Notifier) -> list[str]:
    return [message for message in notifier.messages if "renderer failing" in message]


@pytest.mark.asyncio
async def test_failed_polls_escalate_once_at_the_threshold(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    client = _FailingClient()
    monkeypatch.setattr(service, "build_client", lambda config: client)
    notifier = _Notifier()

    for _ in range(service.RENDER_FAILURE_ALERT_AFTER_POLLS - 1):
        await service.poll_and_notify(_settings(), notifier, force=True)
    assert _streak_alerts(notifier) == [], "one failed poll is not an incident"

    await service.poll_and_notify(_settings(), notifier, force=True)
    assert len(_streak_alerts(notifier)) == 1
    assert str(service.RENDER_FAILURE_ALERT_AFTER_POLLS) in _streak_alerts(notifier)[0]

    # Not one per poll afterwards.
    for _ in range(3):
        await service.poll_and_notify(_settings(), notifier, force=True)
    assert len(_streak_alerts(notifier)) == 1

    rows = [row for row in _audit(logs) if row["event"] == "render_failure_streak"]
    assert len(rows) == 1
    assert rows[0]["consecutive_failed_polls"] == service.RENDER_FAILURE_ALERT_AFTER_POLLS


@pytest.mark.asyncio
async def test_a_successful_poll_resets_the_streak(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    failing = _FailingClient()
    monkeypatch.setattr(service, "build_client", lambda config: failing)
    notifier = _Notifier()

    for _ in range(service.RENDER_FAILURE_ALERT_AFTER_POLLS + 1):
        await service.poll_and_notify(_settings(), notifier, force=True)
    assert len(_streak_alerts(notifier)) == 1

    reports = {}
    for asset in ASSETS:
        from tests.test_etr_alert_delivery import _report  # noqa: PLC0415

        reports[asset] = [_report(asset, "Bajista")]

    class _Client:
        async def fetch_report(self, asset: str):
            return reports[asset].pop(0)

    monkeypatch.setattr(service, "build_client", lambda config: _Client())
    await service.poll_and_notify(_settings(), notifier, force=True)

    state = json.loads((logs / "etr_state.json").read_text())
    assert state["meta"]["render_failure_streak"] == 0
    assert "render_failure_since" not in state["meta"]

    # A second incident alerts again, exactly once.
    monkeypatch.setattr(service, "build_client", lambda config: failing)
    for _ in range(service.RENDER_FAILURE_ALERT_AFTER_POLLS):
        await service.poll_and_notify(_settings(), notifier, force=True)
    assert len(_streak_alerts(notifier)) == 2


@pytest.mark.asyncio
async def test_partial_failure_does_not_count_as_a_failed_poll(
    tmp_path: Path, monkeypatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    from tests.test_etr_alert_delivery import _report  # noqa: PLC0415

    good = _report("btc", "Bajista")

    class _HalfClient:
        async def fetch_report(self, asset: str):
            if asset == "gold":
                raise EtrRenderIncompleteError("no page")
            return good

    monkeypatch.setattr(service, "build_client", lambda config: _HalfClient())
    notifier = _Notifier()
    for _ in range(service.RENDER_FAILURE_ALERT_AFTER_POLLS + 2):
        await service.poll_and_notify(_settings(), notifier, force=True)

    assert _streak_alerts(notifier) == [], "a partially served feed is not a dead renderer"


@pytest.mark.asyncio
async def test_streak_alert_respects_notifications_being_off(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    monkeypatch.setattr(service, "build_client", lambda config: _FailingClient())
    notifier = _Notifier()

    for _ in range(service.RENDER_FAILURE_ALERT_AFTER_POLLS):
        await service.poll_and_notify(_settings(), notifier, force=True, notify=False)

    assert _streak_alerts(notifier) == []
    # The counter still advanced, so the first notifying poll past the threshold
    # does not start the count over.
    state = json.loads((logs / "etr_state.json").read_text())
    assert state["meta"]["render_failure_streak"] == service.RENDER_FAILURE_ALERT_AFTER_POLLS
