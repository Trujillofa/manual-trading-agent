"""A serialized poll can still return an older render; state must not regress."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config.settings import EtrConfig
from src.etr import service
from src.etr.parser import parse_rendered_text
from src.etr.source_time import parse_source_timestamp, source_is_older
from src.etr.state import load_etr_state

FIXTURE = Path(__file__).parent / "fixtures" / "etr_rendered_btc.txt"


def _report(updated_at: str, bias: str):
    report = parse_rendered_text(FIXTURE.read_text(encoding="utf-8"), "btc")
    report.updated_at = updated_at
    report.bias = bias
    return report


class _Notifier:
    enabled = True

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> bool:
        self.messages.append(message)
        return True


class _Client:
    def __init__(self, reports: deque) -> None:
        self.reports = reports

    async def fetch_report(self, asset: str):
        return self.reports.popleft()


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        etr=EtrConfig(
            assets=["btc"],
            telegram_alerts=True,
            login="source-time@example.com",
            password="offline",
        )
    )


@pytest.mark.parametrize(
    ("older", "newer"),
    [
        ("11/08/2026", "12/08/2026"),
        ("11/08/2026 09:00", "11/08/2026 10:00"),
        ("11/08/2026, 9:00 a. m.", "11/08/2026, 2:30 p. m."),
        ("2026-08-11T09:00:00+00:00", "2026-08-11T10:00:00+00:00"),
    ],
)
def test_source_timestamp_ordering(older: str, newer: str) -> None:
    assert source_is_older(older, newer) is True
    assert source_is_older(newer, older) is False
    assert parse_source_timestamp("not a date") is None


def test_naive_and_zoned_timestamps_are_not_compared() -> None:
    # ETR's rendered date has no timezone. Guessing UTC would be worse than
    # declaring the comparison unknown.
    assert source_is_older("2026-08-11T09:00:00+00:00", "11/08/2026") is False


@pytest.mark.asyncio
async def test_older_render_is_not_saved_and_does_not_alert(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    reports = deque(
        [
            _report("12/08/2026", "Bajista"),
            _report("11/08/2026", "Alcista"),
        ]
    )
    monkeypatch.setattr(service, "build_client", lambda config: _Client(reports))
    notifier = _Notifier()

    seed = await service.poll_and_notify(_settings(), notifier, force=True)
    stale = await service.poll_and_notify(_settings(), notifier, force=True)

    assert seed.results[0].seeded is True
    assert stale.results[0].skipped is True
    assert stale.notified_count == 0
    assert notifier.messages == []

    state = load_etr_state(logs / "etr_state.json")["btc"]
    assert state.report["updated_at"] == "12/08/2026"
    assert state.report["bias"] == "Bajista"

    audit = [
        json.loads(line)
        for line in (logs / "etr_audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    stale_events = [record for record in audit if record["event"] == "stale_source"]
    assert stale_events == [
        {
            "ts": stale_events[0]["ts"],
            "asset": "btc",
            "event": "stale_source",
            "kept_source_timestamp": "12/08/2026",
            "ignored_source_timestamp": "11/08/2026",
        }
    ]


@pytest.mark.asyncio
async def test_newer_render_still_commits_and_alerts(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    reports = deque(
        [
            _report("12/08/2026", "Bajista"),
            _report("13/08/2026", "Alcista"),
        ]
    )
    monkeypatch.setattr(service, "build_client", lambda config: _Client(reports))
    notifier = _Notifier()

    await service.poll_and_notify(_settings(), notifier, force=True)
    fresh = await service.poll_and_notify(_settings(), notifier, force=True)

    assert fresh.results[0].skipped is False
    assert fresh.notified_count == 1
    assert len(notifier.messages) == 1
    state = load_etr_state(logs / "etr_state.json")["btc"]
    assert state.report["updated_at"] == "13/08/2026"
    assert state.report["bias"] == "Alcista"


@pytest.mark.asyncio
async def test_equal_timestamp_is_not_treated_as_regression(tmp_path: Path, monkeypatch) -> None:
    # Equal source times are deliberately allowed: the guard only rejects an
    # unambiguously older render. Equal-time content conflicts are out of scope.
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    reports = deque(
        [
            _report("12/08/2026", "Bajista"),
            _report("12/08/2026", "Bajista"),
        ]
    )
    monkeypatch.setattr(service, "build_client", lambda config: _Client(reports))

    await service.poll_and_notify(_settings(), None, force=True)
    summary = await service.poll_and_notify(_settings(), None, force=True)

    assert summary.results[0].skipped is False
