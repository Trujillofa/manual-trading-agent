"""Atomicity, crash and corruption behaviour of the ETR snapshot writer."""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.etr import state as state_module
from src.etr.models import AssetState
from src.etr.state import load_etr_state, save_etr_state, save_state_with_meta

ROOT = Path(__file__).resolve().parents[1]
WRITER = Path(__file__).parent / "support" / "state_write_loop.py"


def _snapshot(marker: str) -> dict[str, AssetState]:
    return {
        "btc": AssetState(
            fingerprint=marker,
            report={"asset": "btc", "bias": marker},
            last_polled_at="2026-11-16T00:00:00+00:00",
        )
    }


def _temp_files(target: Path) -> list[Path]:
    return list(target.parent.glob(f".{target.name}.*.tmp"))


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "etr_state.json"
    save_state_with_meta(_snapshot("new"), {"last_poll_unix": 1}, path=target)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["meta"] == {"last_poll_unix": 1}
    assert load_etr_state(target)["btc"].fingerprint == "new"
    assert _temp_files(target) == []


def test_crash_before_replace_keeps_previous_snapshot(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "etr_state.json"
    save_etr_state(_snapshot("old"), path=target)

    def crash(*args, **kwargs):
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(state_module.os, "replace", crash)

    with pytest.raises(OSError, match="simulated crash"):
        save_etr_state(_snapshot("new"), path=target)

    assert load_etr_state(target)["btc"].fingerprint == "old"
    assert _temp_files(target) == []


def test_fsync_failure_before_replace_keeps_previous_snapshot(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "etr_state.json"
    save_etr_state(_snapshot("old"), path=target)

    def fail_fsync(fd: int) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(state_module.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="simulated disk failure"):
        save_etr_state(_snapshot("new"), path=target)

    assert load_etr_state(target)["btc"].fingerprint == "old"
    assert _temp_files(target) == []


def test_directory_fsync_failure_does_not_undo_a_committed_replace(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "etr_state.json"

    def fail_directory_fsync(directory: Path) -> None:
        raise OSError("simulated directory fsync failure")

    monkeypatch.setattr(state_module, "_fsync_directory", fail_directory_fsync)

    save_etr_state(_snapshot("new"), path=target)

    assert load_etr_state(target)["btc"].fingerprint == "new"


def test_truncated_snapshot_is_reported_and_not_half_parsed(tmp_path: Path, caplog) -> None:
    target = tmp_path / "etr_state.json"
    target.write_text('{"assets": {"btc": {"fingerpr', encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="src.etr.state"):
        assert load_etr_state(target) == {}

    assert any("Failed to load ETR state" in record.message for record in caplog.records)


def test_sigkill_during_writes_leaves_readable_state(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    env = dict(os.environ)
    env["MANUAL_TRADING_AGENT_LOG_DIR"] = str(logs)
    process = subprocess.Popen(
        [sys.executable, str(WRITER), str(logs)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    target = logs / "etr_state.json"
    try:
        deadline = time.monotonic() + 20
        while not target.exists():
            assert process.poll() is None, "writer exited before writing"
            assert time.monotonic() < deadline, "writer never produced a snapshot"
            time.sleep(0.01)
        time.sleep(0.05)
    finally:
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=10)

    # Whatever instant the kill landed on, the published file is a complete
    # document: readers never see a half-written body.
    state = load_etr_state(target)
    assert len(state) == 500
    assert state["asset-499"].fingerprint == "fp-499"
