"""Mixed-precision "Actualizado" values must not drop a newer same-day render.

The defect this guards: a date-only stamp parsed to midnight, so a render from
later that same day compared *older* than the stored one. Seeding
``12/08/2026 09:00`` and then polling ``12/08/2026`` kept the old snapshot,
wrote ``stale_source`` and sent nothing, all day.
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
from src.etr.source_time import source_is_older, source_timestamp_max
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
            min_poll_interval_seconds=1,
            telegram_alerts=True,
            login="precision@example.com",
            password="offline",
        )
    )


@pytest.mark.parametrize(
    ("candidate", "previous", "expected"),
    [
        # Same calendar day, one side has no clock time: not older.
        ("12/08/2026", "12/08/2026 09:00", False),
        ("12/08/2026 09:00", "12/08/2026", False),
        # Different day, one side date-only: the date decides.
        ("11/08/2026", "12/08/2026 09:00", True),
        ("12/08/2026", "11/08/2026 23:00", False),
        # Both timed: unchanged full comparison.
        ("12/08/2026 09:00", "12/08/2026 10:00", True),
        ("12/08/2026 10:00", "12/08/2026 09:00", False),
    ],
)
def test_ordering_uses_the_coarsest_common_precision(
    candidate: str, previous: str, expected: bool
) -> None:
    assert source_is_older(candidate, previous) is expected


def test_max_watermark_keeps_the_precise_stamp() -> None:
    # A date-only render must not erase a clock time already observed that day,
    # or a genuinely older render of the same day would compare equal later.
    assert source_timestamp_max("12/08/2026 19:00", "12/08/2026") == "12/08/2026 19:00"
    assert source_timestamp_max("12/08/2026", "12/08/2026 19:00") == "12/08/2026 19:00"
    assert source_timestamp_max(None, "12/08/2026") == "12/08/2026"
    assert source_timestamp_max("11/08/2026", "12/08/2026") == "12/08/2026"
    assert source_timestamp_max("12/08/2026", "garbage") == "12/08/2026"


@pytest.mark.asyncio
async def test_same_day_date_only_update_is_committed_and_alerted(
    tmp_path: Path, monkeypatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    reports = deque(
        [
            _report("12/08/2026 09:00", "Bajista"),  # seed
            _report("12/08/2026", "Alcista"),  # a later same-day render
            _report("12/08/2026", "Alcista"),
        ]
    )
    monkeypatch.setattr(service, "build_client", lambda config: _Client(reports))
    notifier = _Notifier()

    seed = await service.poll_and_notify(_settings(), notifier, force=True)
    updated = await service.poll_and_notify(_settings(), notifier, force=True)
    await service.poll_and_notify(_settings(), notifier, force=True)

    assert seed.results[0].seeded is True
    assert updated.results[0].skipped is False
    assert updated.notified_count == 1
    assert len(notifier.messages) == 1

    state = load_etr_state(logs / "etr_state.json")["btc"]
    assert state.report["bias"] == "Alcista"
    assert state.source_at_max == "12/08/2026 09:00"  # watermark never downgraded

    audit = [
        json.loads(line)
        for line in (logs / "etr_audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["event"] for record in audit if record["event"] == "stale_source"] == []


@pytest.mark.asyncio
async def test_date_only_commit_does_not_lower_the_watermark(tmp_path: Path, monkeypatch) -> None:
    # After a date-only commit, a genuinely older timed render of that day must
    # still be rejected: the high-water mark, not the last committed report, is
    # what the guard compares against.
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    reports = deque(
        [
            _report("12/08/2026 19:00", "Bajista"),
            _report("12/08/2026", "Alcista"),
            _report("12/08/2026 08:00", "Lateral"),
        ]
    )
    monkeypatch.setattr(service, "build_client", lambda config: _Client(reports))
    notifier = _Notifier()

    await service.poll_and_notify(_settings(), notifier, force=True)
    date_only = await service.poll_and_notify(_settings(), notifier, force=True)
    older = await service.poll_and_notify(_settings(), notifier, force=True)

    assert date_only.results[0].skipped is False
    assert older.results[0].skipped is True

    state = load_etr_state(logs / "etr_state.json")["btc"]
    assert state.report["bias"] == "Alcista"
    assert state.source_at_max == "12/08/2026 19:00"
    assert len(notifier.messages) == 1


def test_naive_and_zoned_still_not_compared() -> None:
    assert source_is_older("2026-08-11T09:00:00+00:00", "11/08/2026") is False
