"""Real multi-process coordination tests: separate interpreters, real flocks.

Each child runs the production ``poll_and_notify`` against a temporary logs
directory. Only the upstream client is fake, so state, locks and atomic writes
are exercised exactly as deployed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.etr import coordination
from src.etr.state import etr_state_path, load_etr_state

ROOT = Path(__file__).resolve().parents[1]
CHILD = Path(__file__).parent / "support" / "etr_poll_child.py"


def _spawn(*args: str, logs: Path) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env["MANUAL_TRADING_AGENT_LOG_DIR"] = str(logs)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.Popen(
        [sys.executable, str(CHILD), "--logs", str(logs), *args],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish(process: subprocess.Popen[str], timeout: float = 30) -> dict:
    stdout, stderr = process.communicate(timeout=timeout)
    assert process.returncode == 0, f"child failed rc={process.returncode}\n{stderr}"
    return json.loads(stdout.strip().splitlines()[-1])


def test_concurrent_polls_preserve_each_others_assets(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    barrier = tmp_path / "barrier"
    barrier.mkdir()

    peers = [
        _spawn("--asset", asset, "--barrier", str(barrier), "--peers", "2", logs=logs)
        for asset in ("btc", "gold")
    ]
    summaries = [_finish(process) for process in peers]

    assert all(summary["skipped"] is False for summary in summaries)
    state = load_etr_state(logs / "etr_state.json")
    assert set(state) == {"btc", "gold"}
    # Both children saw the empty baseline before either wrote; the second
    # writer must reload under the lock instead of saving its stale snapshot.
    assert all(asset_state.last_polled_at for asset_state in state.values())
    payload = json.loads((logs / "etr_state.json").read_text(encoding="utf-8"))
    assert payload["meta"]["last_poll_unix"] > 0


def test_lock_is_not_held_across_the_network_fetch(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    release = tmp_path / "release"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))

    process = _spawn(
        "--asset",
        "btc",
        "--barrier",
        str(barrier),
        "--hold-file",
        str(release),
        logs=logs,
    )
    try:
        deadline = time.monotonic() + 20
        while not (barrier / "fetching-btc").exists():
            assert process.poll() is None, process.stderr.read() if process.stderr else ""
            assert time.monotonic() < deadline, "child never started fetching"
            time.sleep(0.02)

        # The child is blocked in fetch_report. The transaction lock must be
        # free: if it covered the network call this would raise busy.
        started = time.monotonic()
        with coordination.coordination_lock(timeout=0.5):
            pass
        assert time.monotonic() - started < 0.5
    finally:
        release.write_text("1", encoding="utf-8")
        _finish(process)

    assert set(load_etr_state(logs / "etr_state.json")) == {"btc"}


def test_busy_lock_is_bounded_and_skips_without_state(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))

    with coordination.coordination_lock(timeout=1):
        started = time.monotonic()
        process = _spawn(
            "--asset",
            "btc",
            "--barrier",
            str(barrier),
            "--lock-timeout",
            "0.2",
            logs=logs,
        )
        summary = _finish(process)
        elapsed = time.monotonic() - started

    assert summary["skipped"] is True
    assert "busy" in summary["message"]
    assert elapsed < 5
    assert not etr_state_path().exists() or load_etr_state(etr_state_path()) == {}


def test_lock_file_is_the_shared_etr_inode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(tmp_path / "logs"))
    path = coordination.coordination_lock_path()
    assert path.name == "etr_coordination.lock"
    assert path.parent == etr_state_path().parent
    with coordination.coordination_lock(timeout=1):
        assert path.exists()
        # Cache/briefing readers use the same inode, so they queue behind a
        # writer instead of inventing a second lock.
        with (
            pytest.raises(coordination.CoordinationBusyError),
            coordination.state_read_lock(timeout=0.05),
        ):
            pass
