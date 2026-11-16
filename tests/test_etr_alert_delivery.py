"""Alert delivery is at-most-once by construction, never exactly-once."""

from __future__ import annotations

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
    meta, messages = {}, []
    now = [100000.0]
    monkeypatch.setattr(service.time, "time", lambda: now[0])

    class Notifier:
        enabled = True

        async def send(self, message: str) -> bool:
            messages.append(message)
            return True

    notifier = Notifier()
    for _ in range(96):
        for asset in ["nasdaq", "btc", "gold", "oil"]:
            await service._maybe_error_alert(cfg, meta, notifier, asset, "timeout", True)
        now[0] += 900

    # 96 * 15 minutes = 24h at a six-hour global cooldown: four alerts, not the
    # sixteen a per-asset cooldown would have produced.
    assert len(messages) == 4
    assert all("nasdaq" in message for message in messages)


@pytest.mark.asyncio
async def test_failed_error_send_still_consumes_the_cooldown(monkeypatch) -> None:
    cfg = SimpleNamespace(error_alert_cooldown_minutes=360)
    meta = {}
    now = [100000.0]
    monkeypatch.setattr(service.time, "time", lambda: now[0])

    class FailingNotifier:
        enabled = True

        async def send(self, message: str) -> bool:
            return False

    await service._maybe_error_alert(cfg, meta, FailingNotifier(), "btc", "e", True)
    assert meta["last_error_alert_unix"] == 100000.0

    class RecordingNotifier:
        enabled = True

        def __init__(self) -> None:
            self.messages = []

        async def send(self, message: str) -> bool:
            self.messages.append(message)
            return True

    notifier = RecordingNotifier()
    await service._maybe_error_alert(cfg, meta, notifier, "btc", "e", True)
    assert notifier.messages == []
