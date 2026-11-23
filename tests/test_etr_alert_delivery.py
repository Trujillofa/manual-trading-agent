"""Alert delivery is at-most-once by construction, never exactly-once."""

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


class _RecordingNotifier:
    enabled = True

    def __init__(self, *, fails: bool = False) -> None:
        self.messages: list[str] = []
        self.fails = fails

    async def send(self, message: str) -> bool:
        self.messages.append(message)
        if self.fails:
            raise RuntimeError("simulated crash while sending")
        return True


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        etr=EtrConfig(
            assets=["btc"],
            telegram_alerts=True,
            login="alerts@example.com",
            password="offline",
        )
    )


@pytest.mark.asyncio
async def test_crash_during_send_cannot_repeat_the_alert(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    reports = deque([_report("Bajista"), _report("Alcista"), _report("Alcista")])
    monkeypatch.setattr(service, "build_client", lambda config: _Client(reports))

    await service.poll_and_notify(_settings(), _RecordingNotifier(), force=True)

    crashing = _RecordingNotifier(fails=True)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await service.poll_and_notify(_settings(), crashing, force=True)

    # The state transaction committed (and reserved) before the send died.
    state = load_etr_state(logs / "etr_state.json")["btc"]
    assert state.report["bias"] == "Alcista"
    assert state.last_alerted_fingerprint == state.fingerprint

    # A restart re-polls the same page: no diff, therefore no duplicate.
    retry_notifier = _RecordingNotifier()
    summary = await service.poll_and_notify(_settings(), retry_notifier, force=True)
    assert summary.notified_count == 0
    assert retry_notifier.messages == []

    # The trade-off is explicit: that one alert is lost, not duplicated.
    assert crashing.messages


@pytest.mark.asyncio
async def test_error_alerts_share_one_global_cooldown(monkeypatch) -> None:
    cfg = SimpleNamespace(error_alert_cooldown_minutes=360)
    meta, reserved = {}, []
    now = [100000.0]
    monkeypatch.setattr(service.time, "time", lambda: now[0])

    for _ in range(96):
        for asset in ["nasdaq", "btc", "gold", "oil"]:
            alert = service._reserve_error_alert(cfg, meta, asset, "timeout")
            if alert is not None:
                reserved.append(alert)
        now[0] += 900

    # 96 * 15 minutes = 24h at a six-hour global cooldown: four alerts, not the
    # sixteen a per-asset cooldown would have produced.
    assert len(reserved) == 4
    assert all("nasdaq" in alert.message for alert in reserved)
    assert all(alert.kind == "error" for alert in reserved)


@pytest.mark.asyncio
async def test_failed_error_send_still_consumes_the_cooldown(monkeypatch) -> None:
    cfg = SimpleNamespace(error_alert_cooldown_minutes=360)
    meta = {}
    now = [100000.0]
    monkeypatch.setattr(service.time, "time", lambda: now[0])

    alert = service._reserve_error_alert(cfg, meta, "btc", "e")
    assert alert is not None
    assert meta["last_error_alert_unix"] == 100000.0

    # The cooldown is already durable, so the next reservation is suppressed even
    # though this alert never actually reached Telegram.
    assert service._reserve_error_alert(cfg, meta, "btc", "e") is None


@pytest.mark.asyncio
async def test_reserved_error_alert_is_audited_lost_when_the_send_fails(
    tmp_path: Path, monkeypatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))

    class FailingNotifier:
        enabled = True

        async def send(self, message: str) -> bool:
            return False

    alert = service._PendingAlert(
        "⚠️ *ETR error* · `btc`", kind="error", asset="btc", fingerprint=None
    )
    notified = await service._deliver_alerts(FailingNotifier(), [alert], [])

    assert notified == 0
    events = [
        json.loads(line)
        for line in (logs / "etr_audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["event"] for record in events] == ["alert_lost"]
    assert events[0]["reason"] == "notifier returned False"
