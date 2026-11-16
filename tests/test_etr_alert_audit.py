"""Every alert decision leaves a durable trace.

Delivery is at-most-once: the state commit reserves the alert, and a crash before
the send loses it. That trade is only acceptable if the loss is *observable*, so a
reservation is written to the audit log before the send and its outcome after it.
Fetch work discarded because another writer held the lock is recorded too; it used
to vanish with no trace at all.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src.config.settings import EtrConfig
from src.etr import service
from src.etr.client import EtrClient
from src.etr.coordination import poll_lock
from src.etr.parser import parse_rendered_text
from src.etr.state import etr_state_path, load_etr_state

FIXTURE = Path(__file__).parent / "fixtures" / "etr_rendered_btc.txt"
HOLDER = Path(__file__).parent / "support" / "lock_holder.py"


def _report(bias: str, updated_at: str = "12/08/2026"):
    report = parse_rendered_text(FIXTURE.read_text(encoding="utf-8"), "btc")
    report.updated_at = updated_at
    report.bias = bias
    return report


class _Client:
    def __init__(self, reports: deque) -> None:
        self.reports = reports

    async def fetch_report(self, asset: str):
        return self.reports.popleft()


class _Notifier:
    enabled = True

    def __init__(self, *, result: bool = True, raises: Exception | None = None) -> None:
        self.messages: list[str] = []
        self.result = result
        self.raises = raises

    async def send(self, message: str) -> bool:
        self.messages.append(message)
        if self.raises is not None:
            raise self.raises
        return self.result


def _short_poll_lock():
    """The real exclusive lock with a deadline short enough for a test."""
    return poll_lock(timeout=0.05)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        etr=EtrConfig(
            assets=["btc"],
            min_poll_interval_seconds=1,
            telegram_alerts=True,
            login="audit@example.com",
            password="offline",
        )
    )


def _audit(logs: Path) -> list[dict]:
    path = logs / "etr_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_change_alert_is_reserved_before_it_is_sent(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    reports = deque([_report("Bajista"), _report("Alcista")])
    monkeypatch.setattr(service, "build_client", lambda config: _Client(reports))
    notifier = _Notifier()

    await service.poll_and_notify(_settings(), notifier, force=True)
    await service.poll_and_notify(_settings(), notifier, force=True)

    events = [record["event"] for record in _audit(logs)]
    assert events.count("alert_reserved") == 1
    assert events.count("alert_sent") == 1
    assert events.index("alert_reserved") < events.index("alert_sent")
    reserved = next(r for r in _audit(logs) if r["event"] == "alert_reserved")
    assert reserved["kind"] == "change"
    assert reserved["asset"] == "btc"
    assert reserved["fingerprint"]


@pytest.mark.asyncio
async def test_crash_between_commit_and_send_leaves_a_reservation_and_a_loss(
    tmp_path: Path, monkeypatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    reports = deque([_report("Bajista"), _report("Alcista")])
    monkeypatch.setattr(service, "build_client", lambda config: _Client(reports))
    await service.poll_and_notify(_settings(), _Notifier(), force=True)

    crashing = _Notifier(raises=RuntimeError("telegram down"))
    with pytest.raises(RuntimeError, match="telegram down"):
        await service.poll_and_notify(_settings(), crashing, force=True)

    events = [record["event"] for record in _audit(logs)]
    assert events[-2:] == ["alert_reserved", "alert_lost"]
    lost = _audit(logs)[-1]
    assert lost["reason"] == "RuntimeError: telegram down"
    assert lost["notified"] is False


@pytest.mark.asyncio
async def test_send_returning_false_is_audited_as_lost(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    reports = deque([_report("Bajista"), _report("Alcista"), _report("Alcista")])
    monkeypatch.setattr(service, "build_client", lambda config: _Client(reports))
    await service.poll_and_notify(_settings(), _Notifier(), force=True)

    failing = _Notifier(result=False)
    summary = await service.poll_and_notify(_settings(), failing, force=True)

    # A failed send is not retried: the reservation is already saved, so the
    # alert is permanently lost. The audit record is the only evidence.
    assert summary.notified_count == 0
    assert failing.messages
    events = [record["event"] for record in _audit(logs)]
    assert events.index("alert_reserved") < events.index("alert_lost")
    lost = next(record for record in _audit(logs) if record["event"] == "alert_lost")
    assert lost["reason"] == "notifier returned False"

    retry = _Notifier()
    await service.poll_and_notify(_settings(), retry, force=True)
    assert retry.messages == []


@pytest.mark.asyncio
async def test_lock_busy_skip_records_the_discarded_fetch(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    monkeypatch.setattr(
        service, "build_client", lambda config: _Client(deque([_report("Bajista")]))
    )
    monkeypatch.setattr(service, "poll_lock", lambda: _short_poll_lock())

    holder = subprocess.Popen(
        [
            sys.executable,
            str(HOLDER),
            str(etr_state_path().with_name("etr_coordination.lock")),
            "10",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "HELD"
    try:
        summary = await service.poll_and_notify(_settings(), _Notifier(), force=True)
    finally:
        holder.kill()
        holder.wait()

    assert summary.skipped is True
    records = [record for record in _audit(logs) if record["event"] == "commit_skipped_lock_busy"]
    assert len(records) == 1
    assert records[0]["assets_fetched"] == ["btc"]
    assert records[0]["assets_requested"] == ["btc"]
    assert "another writer" in records[0]["reason"]
    # Nothing was committed, so the snapshot is untouched.
    assert load_etr_state(logs / "etr_state.json") == {}


@pytest.mark.asyncio
async def test_bare_503_from_the_renderer_alerts_end_to_end(tmp_path: Path, monkeypatch) -> None:
    """P1-1: a dead renderer used to be reclassified as 'busy' and stayed silent."""
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))

    client = EtrClient("user@example.com", "secret", renderer_url="http://renderer:8000")

    async def session(*, force_login: bool = False):
        return {"access_token": "offline"}

    monkeypatch.setattr(client, "ensure_session", session)
    original = httpx.AsyncClient
    monkeypatch.setattr(
        "src.etr.client.httpx.AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(lambda request: httpx.Response(503, text="busy")),
            **kwargs,
        ),
    )
    monkeypatch.setattr(service, "build_client", lambda config: client)

    notifier = _Notifier()
    summary = await service.poll_and_notify(_settings(), notifier, force=True)

    assert summary.error_count == 1
    assert len(notifier.messages) == 1
    assert "ETR error" in notifier.messages[0]
    assert [record["event"] for record in _audit(logs)] == [
        "error",
        "alert_reserved",
        "alert_sent",
    ]
