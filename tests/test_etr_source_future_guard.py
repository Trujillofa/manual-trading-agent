"""A source timestamp that cannot be real must never become the water mark.

ETR sometimes publishes ``08/12/2026`` where the reader expects DD/MM and the
page means US MM/DD. Parsed as 8 December it sits months in the future, and as
the high-water mark it rejected every honest render from then on — while the
committed report still looked perfectly plausible, so nothing pointed at it.

The same guard had a second, quieter way to die: a timezone-aware legacy
``updated_at`` was incomparable with a naive ETR stamp, so ``source_is_older``
failed open and ``source_timestamp_max`` refused to replace it. One such value
disabled both halves of the guard permanently.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from src.etr import service
from src.etr.coordination import coordination_lock
from src.etr.models import EtrPollResult
from src.etr.parser import parse_rendered_text
from src.etr.source_time import (
    MAX_SOURCE_FUTURE_HOURS,
    source_is_older,
    source_timestamp_in_future,
    source_timestamp_max,
)
from src.etr.state import load_etr_state

FIXTURE = Path(__file__).parent / "fixtures" / "etr_rendered_btc.txt"
NOW_ISO = "2026-08-12T12:00:00+00:00"


class _Notifier:
    enabled = True

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> bool:
        self.messages.append(message)
        return True


def _report(asset: str = "btc", updated_at: str = "12/08/2026 10:00"):
    report = parse_rendered_text(FIXTURE.read_text(encoding="utf-8"), asset)
    report.updated_at = updated_at
    return report


def _commit(tmp_path: Path, monkeypatch, updated_at: str, *, do_notify: bool = False):
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(tmp_path))
    config = SimpleNamespace(
        min_poll_interval_seconds=1,
        score_alert_low=50.0,
        score_alert_high=80.0,
        score_delta_alert=10.0,
        telegram_alert_fields=["bias"],
        shadow_horizon_hours=24.0,
    )
    report = _report(updated_at=updated_at)
    notifier = None
    with coordination_lock():
        return service._commit_poll(
            config,
            notifier=notifier,
            fetched=[("btc", report)],
            errors=[],
            results=[EtrPollResult(asset="btc", report=report)],
            do_notify=do_notify,
            force=True,
            now_iso=NOW_ISO,
            busy_count=0,
            assets=["btc"],
        )


def _commit_with(tmp_path: Path, monkeypatch, updated_at: str, notifier) -> None:
    """One poll with notifications on, delivering through the real flush."""
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(tmp_path))
    config = SimpleNamespace(
        min_poll_interval_seconds=1,
        score_alert_low=50.0,
        score_alert_high=80.0,
        score_delta_alert=10.0,
        telegram_alert_fields=["bias"],
        shadow_horizon_hours=24.0,
    )
    report = _report(updated_at=updated_at)
    with coordination_lock():
        commit = service._commit_poll(
            config,
            notifier=notifier,
            fetched=[("btc", report)],
            errors=[],
            results=[EtrPollResult(asset="btc", report=report)],
            do_notify=True,
            force=True,
            now_iso=NOW_ISO,
            busy_count=0,
            assets=["btc"],
        )
    asyncio.run(service._deliver_alerts(notifier, commit.alerts, commit.audits))


def _state(tmp_path: Path) -> dict:
    return load_etr_state(tmp_path / "etr_state.json")


def _audit(tmp_path: Path) -> list[dict]:
    path = tmp_path / "etr_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_future_detection_respects_the_horizon() -> None:
    now = datetime(2026, 8, 12, 12, 0)
    inside = (now + timedelta(hours=MAX_SOURCE_FUTURE_HOURS - 1)).strftime("%d/%m/%Y %H:%M")
    outside = (now + timedelta(hours=MAX_SOURCE_FUTURE_HOURS + 1)).strftime("%d/%m/%Y %H:%M")

    assert source_timestamp_in_future(inside, now=now) is False
    assert source_timestamp_in_future(outside, now=now) is True
    assert source_timestamp_in_future("hace 5 minutos", now=now) is False
    assert source_timestamp_in_future(None, now=now) is False


def test_an_aware_legacy_stamp_no_longer_disables_the_guard() -> None:
    # Naive 21:00 vs aware 20:00Z: comparable now, so the older render is older.
    assert source_is_older("2026-08-12T20:00:00+00:00", "12/08/2026 21:00") is True
    assert source_is_older("2026-08-12T22:00:00+00:00", "12/08/2026 21:00") is False
    # And the water mark replaces it instead of freezing on it.
    assert source_timestamp_max("2026-08-12T20:00:00+00:00", "12/08/2026 21:00") == (
        "12/08/2026 21:00"
    )


def test_a_misparsed_stamp_never_becomes_the_water_mark(tmp_path: Path, monkeypatch) -> None:
    _commit(tmp_path, monkeypatch, "12/08/2026 11:00")
    _commit(tmp_path, monkeypatch, "08/12/2026")  # 8 December: months ahead

    asset = _state(tmp_path)["btc"]
    assert asset.source_at_max == "12/08/2026 11:00", "a future stamp must not be stored"
    rejected = [row for row in _audit(tmp_path) if row["event"] == "source_timestamp_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["rejected_source_timestamp"] == "08/12/2026"

    # The ten honest days that used to be skipped all afternoon still commit.
    for day in range(13, 20):
        result = _commit(tmp_path, monkeypatch, f"{day:02d}/08/2026 09:00")
        assert result.results[0].skipped is False, day

    asset = _state(tmp_path)["btc"]
    assert asset.report["updated_at"] == "19/08/2026 09:00"
    assert asset.source_at_max == "19/08/2026 09:00"


def test_a_poisoned_water_mark_is_discarded_instead_of_blocking(
    tmp_path: Path, monkeypatch
) -> None:
    _commit(tmp_path, monkeypatch, "12/08/2026 11:00")

    # Reproduce a state file written by the old code: the water mark is the
    # misparse while the committed report looks correct.
    path = tmp_path / "etr_state.json"
    payload = json.loads(path.read_text())
    payload["assets"]["btc"]["source_at_max"] = "08/12/2026"
    path.write_text(json.dumps(payload))

    result = _commit(tmp_path, monkeypatch, "13/08/2026 09:00")

    assert result.results[0].skipped is False
    discarded = [row for row in _audit(tmp_path) if row["event"] == "source_watermark_discarded"]
    assert len(discarded) == 1
    assert discarded[0]["discarded_source_timestamp"] == "08/12/2026"
    assert _state(tmp_path)["btc"].report["updated_at"] == "13/08/2026 09:00"


def test_a_genuinely_older_render_is_still_rejected(tmp_path: Path, monkeypatch) -> None:
    _commit(tmp_path, monkeypatch, "12/08/2026 21:00")
    result = _commit(tmp_path, monkeypatch, "12/08/2026 20:30")

    assert result.results[0].skipped is True
    assert _state(tmp_path)["btc"].report["updated_at"] == "12/08/2026 21:00"
    assert [row for row in _audit(tmp_path) if row["event"] == "stale_source"]


def test_stale_skips_alert_once_after_the_threshold(tmp_path: Path, monkeypatch) -> None:
    _commit(tmp_path, monkeypatch, "12/08/2026 21:00")
    _commit(tmp_path, monkeypatch, "12/08/2026 20:30")
    assert _state(tmp_path)["btc"].report["updated_at"] == "12/08/2026 21:00"
    assert "stale_source" in json.loads((tmp_path / "etr_state.json").read_text())["meta"]

    # Push the streak past the threshold the way six stuck hours would.
    payload = json.loads((tmp_path / "etr_state.json").read_text())
    payload["meta"]["stale_source"]["btc"]["since_unix"] = (
        time.time() - service.STALE_SOURCE_ALERT_AFTER_SECONDS - 1
    )
    (tmp_path / "etr_state.json").write_text(json.dumps(payload))

    notifier = _Notifier()
    _commit_with(tmp_path, monkeypatch, "12/08/2026 20:45", notifier)
    assert len(notifier.messages) == 1
    assert "source timestamp stuck" in notifier.messages[0]
    assert [row for row in _audit(tmp_path) if row["event"] == "stale_source_stuck"]

    # Only once for the same streak, even though the skips continue.
    _commit(tmp_path, monkeypatch, "12/08/2026 20:50")
    assert len(notifier.messages) == 1
