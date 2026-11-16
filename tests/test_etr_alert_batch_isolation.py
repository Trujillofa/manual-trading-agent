"""A batch of alerts is delivered one send at a time, and a preview sends none.

Two independent ways the old code lost an alert without saying so:

* One notifier exception aborted the whole loop. Later alerts stayed
  ``alert_reserved`` with no outcome row, and the poll audits queued behind the
  loop were never written, so an operator reading the audit trail saw a poll that
  had simply not happened.
* ``--no-notify`` advanced ``last_alerted_fingerprint`` and committed the new
  snapshot. The next notifying poll therefore had an empty diff — the change was
  silently eaten by the preview that was supposed to only preview it.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config.settings import EtrConfig
from src.etr import service
from src.etr.parser import parse_rendered_text
from src.etr.state import load_etr_state

FIXTURE = Path(__file__).parent / "fixtures" / "etr_rendered_btc.txt"
ASSETS = ("btc", "gold", "oil")


def _report(asset: str, bias: str, updated_at: str = "12/08/2026"):
    report = parse_rendered_text(FIXTURE.read_text(encoding="utf-8"), asset)
    report.updated_at = updated_at
    report.bias = bias
    return report


class _Client:
    def __init__(self, reports: dict[str, deque]) -> None:
        self.reports = reports

    async def fetch_report(self, asset: str):
        return self.reports[asset].popleft()


class _Notifier:
    enabled = True

    def __init__(self, *, fail_on: int | None = None) -> None:
        self.messages: list[str] = []
        self.fail_on = fail_on
        self.calls = 0

    async def send(self, message: str) -> bool:
        self.calls += 1
        if self.fail_on is not None and self.calls == self.fail_on:
            raise ConnectionError("telegram exploded")
        self.messages.append(message)
        return True


def _settings(assets=ASSETS) -> SimpleNamespace:
    return SimpleNamespace(
        etr=EtrConfig(
            assets=list(assets),
            telegram_alerts=True,
            login="alerts@example.com",
            password="offline",
        )
    )


def _audit(logs: Path) -> list[dict]:
    path = logs / "etr_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_one_failing_send_does_not_abort_the_batch(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    reports = {asset: deque([_report(asset, "Bajista")]) for asset in ASSETS}
    monkeypatch.setattr(service, "build_client", lambda config: _Client(reports))
    await service.poll_and_notify(_settings(), _Notifier(), force=True)

    for asset in ASSETS:
        reports[asset].append(_report(asset, "Alcista"))
    notifier = _Notifier(fail_on=2)

    with pytest.raises(ConnectionError):
        await service.poll_and_notify(_settings(), notifier, force=True)

    # The two healthy sends went out around the failing one.
    assert len(notifier.messages) == 2
    assert {message.split("·")[1].strip() for message in notifier.messages} == {"BTC", "OIL"}

    rows = _audit(logs)
    outcomes = {row["asset"]: row for row in rows if row["event"].startswith("alert_")}
    assert set(outcomes) == set(ASSETS), "every reserved alert needs an outcome row"
    assert outcomes["gold"]["event"] == "alert_lost"
    assert "ConnectionError" in outcomes["gold"]["reason"]
    assert outcomes["btc"]["event"] == "alert_sent"
    assert outcomes["oil"]["event"] == "alert_sent"

    # And the poll rows the abort used to skip are written too.
    assert len([row for row in rows if row["event"] == "poll"]) == len(ASSETS)


@pytest.mark.asyncio
async def test_failing_send_still_commits_state_and_does_not_repeat(
    tmp_path: Path, monkeypatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    reports = {asset: deque([_report(asset, "Bajista"), _report(asset, "Alcista")]) for asset in ASSETS}
    monkeypatch.setattr(service, "build_client", lambda config: _Client(reports))
    await service.poll_and_notify(_settings(), _Notifier(), force=True)

    with pytest.raises(ConnectionError):
        await service.poll_and_notify(_settings(), _Notifier(fail_on=1), force=True)

    state = load_etr_state(logs / "etr_state.json")
    for asset in ASSETS:
        assert state[asset].report["bias"] == "Alcista"
        # Reserved before the send, so a restart cannot double-deliver.
        assert state[asset].last_alerted_fingerprint == state[asset].fingerprint

    for asset in ASSETS:
        reports[asset].append(_report(asset, "Alcista"))
    replay = _Notifier()
    summary = await service.poll_and_notify(_settings(), replay, force=True)
    assert replay.messages == []
    assert summary.notified_count == 0


@pytest.mark.asyncio
async def test_preview_poll_does_not_consume_the_change(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    reports = {asset: deque([_report(asset, "Bajista")]) for asset in ASSETS}
    monkeypatch.setattr(service, "build_client", lambda config: _Client(reports))
    await service.poll_and_notify(_settings(), _Notifier(), force=True)

    preview_reports = {asset: deque([_report(asset, "Alcista")]) for asset in ASSETS}
    monkeypatch.setattr(service, "build_client", lambda config: _Client(preview_reports))
    preview = _Notifier()
    preview_summary = await service.poll_and_notify(
        _settings(), preview, force=True, notify=False
    )
    assert preview.messages == []
    assert preview_summary.notified_count == 0

    # The snapshot committed, so the diff against it is empty: the alert has to
    # be carried, and the audit row has to say why nothing went out.
    state = load_etr_state(logs / "etr_state.json")
    for asset in ASSETS:
        assert state[asset].report["bias"] == "Alcista"
        assert state[asset].pending_alert is not None
        assert state[asset].pending_alert["suppressed_because"] == "notifications disabled"
    suppressed = [row for row in _audit(logs) if row.get("alert_suppressed")]
    assert {row["asset"] for row in suppressed} == set(ASSETS)

    same_page = {asset: deque([_report(asset, "Alcista")]) for asset in ASSETS}
    monkeypatch.setattr(service, "build_client", lambda config: _Client(same_page))
    notifying = _Notifier()
    await service.poll_and_notify(_settings(), notifying, force=True)
    assert len(notifying.messages) == len(ASSETS), "the previewed change must still be delivered"

    carried = [row for row in _audit(logs) if row["event"] == "alert_carried_forward"]
    assert {row["asset"] for row in carried} == set(ASSETS)
    assert all(row["suppressed_because"] == "notifications disabled" for row in carried)

    # Delivered once: the pending marker is gone and a further poll is silent.
    state = load_etr_state(logs / "etr_state.json")
    assert all(state[asset].pending_alert is None for asset in ASSETS)
    again = {asset: deque([_report(asset, "Alcista")]) for asset in ASSETS}
    monkeypatch.setattr(service, "build_client", lambda config: _Client(again))
    silent = _Notifier()
    await service.poll_and_notify(_settings(), silent, force=True)
    assert silent.messages == []


@pytest.mark.asyncio
async def test_pending_alert_survives_a_state_reload(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    reports = {"btc": deque([_report("btc", "Bajista")])}
    monkeypatch.setattr(service, "build_client", lambda config: _Client(reports))
    await service.poll_and_notify(_settings(("btc",)), _Notifier(), force=True)

    preview = {"btc": deque([_report("btc", "Alcista")])}
    monkeypatch.setattr(service, "build_client", lambda config: _Client(preview))
    await service.poll_and_notify(_settings(("btc",)), _Notifier(), force=True, notify=False)

    # Round-trips through JSON, so the marker has to be plain data.
    reloaded = load_etr_state(logs / "etr_state.json")["btc"]
    assert reloaded.pending_alert is not None
    assert "CAMBIO" in reloaded.pending_alert["message"]

    same = {"btc": deque([_report("btc", "Alcista")])}
    monkeypatch.setattr(service, "build_client", lambda config: _Client(same))
    notifier = _Notifier()
    await service.poll_and_notify(_settings(("btc",)), notifier, force=True)
    assert len(notifier.messages) == 1
    assert "CAMBIO" in notifier.messages[0]
