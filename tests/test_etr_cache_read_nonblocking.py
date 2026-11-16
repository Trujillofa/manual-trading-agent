"""Cache reads reached from the event loop must not block it.

The defect this guards: ``cached_reports`` took the shared coordination lock with
a blocking ``flock`` retry (``time.sleep``), and it is called from async Telegram
handlers and the briefing. A writer holding the lock therefore froze the event
loop for the full two-second read timeout and then returned an empty cache — a
stalled bot and a message that says ETR has nothing.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.briefing import service as briefing_service
from src.etr import service
from src.etr.coordination import try_state_read_lock
from src.etr.models import AssetState
from src.etr.state import etr_state_path, save_etr_state

HOLDER = Path(__file__).parent / "support" / "lock_holder.py"


def _seed(logs: Path) -> None:
    save_etr_state(
        {
            "btc": AssetState(
                fingerprint="fp",
                report={
                    "asset": "btc",
                    "label": "Bitcoin",
                    "price": 64000.0,
                    "updated_at": "12/08/2026 09:00",
                    "bias": "Bajista",
                },
                last_polled_at="2026-08-12T13:00:00+00:00",
            )
        },
        logs / "etr_state.json",
    )


def _hold_lock(logs: Path) -> subprocess.Popen[str]:
    lock = etr_state_path().with_name("etr_coordination.lock")
    holder = subprocess.Popen(
        [sys.executable, str(HOLDER), str(lock), "10"],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "HELD"
    return holder


def test_try_state_read_lock_never_blocks(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    holder = _hold_lock(logs)
    try:
        started = time.monotonic()
        with try_state_read_lock() as locked:
            elapsed = time.monotonic() - started
        assert locked is False
        assert elapsed < 0.25  # the old shared read waited 2.0 s
    finally:
        holder.kill()
        holder.wait()


def test_cached_reports_returns_content_while_a_writer_holds_the_lock(
    tmp_path: Path, monkeypatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    _seed(logs)

    holder = _hold_lock(logs)
    try:
        started = time.monotonic()
        reports = service.cached_reports(["btc"])
        elapsed = time.monotonic() - started
    finally:
        holder.kill()
        holder.wait()

    assert elapsed < 0.5, f"cache read blocked for {elapsed:.2f}s"
    assert [report.asset for report in reports] == ["btc"]
    assert reports[0].bias == "Bajista"


@pytest.mark.asyncio
async def test_cache_read_from_an_async_caller_does_not_stall_the_loop(
    tmp_path: Path, monkeypatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    _seed(logs)

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(ticker())
    holder = _hold_lock(logs)
    try:
        await asyncio.sleep(0.02)
        started = time.monotonic()
        reports = service.cached_reports(["btc"])  # what a Telegram handler calls
        elapsed = time.monotonic() - started
        ticks_after = ticks
        await asyncio.sleep(0.05)
    finally:
        holder.kill()
        holder.wait()
        task.cancel()

    assert reports, "a contended read must not return an empty cache"
    assert elapsed < 0.5, f"handler blocked the event loop for {elapsed:.2f}s"
    assert ticks > ticks_after  # the loop kept servicing other work


def test_briefing_reads_the_snapshot_while_a_writer_holds_the_lock(
    tmp_path: Path, monkeypatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    _seed(logs)

    holder = _hold_lock(logs)
    try:
        started = time.monotonic()
        reports, polled = briefing_service._etr_reports()
        elapsed = time.monotonic() - started
    finally:
        holder.kill()
        holder.wait()

    assert elapsed < 0.5
    assert set(reports) == {"btc"}
    assert polled["btc"] == "2026-08-12T13:00:00+00:00"
