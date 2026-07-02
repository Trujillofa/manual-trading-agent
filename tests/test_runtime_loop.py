"""Tests for the production scanner loop shell script and log rotation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_scanner_loop.sh"


def _run_rotate_once(
    tmp_path: Path,
    *,
    threshold: int = 200,
    retain: int = 80,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "LOG_DIR": str(tmp_path),
        "ROTATE_THRESHOLD_BYTES": str(threshold),
        "RETAIN_BYTES": str(retain),
        "ROTATE_ONCE": "true",
        "TELEGRAM_POLL_ENABLED": "false",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["/bin/sh", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=30,
    )


def test_rotate_once_trims_scan_log_to_retain_bytes(tmp_path: Path) -> None:
    scan_log = tmp_path / "scan.log"
    payload = b"x" * 250
    scan_log.write_bytes(payload)

    result = _run_rotate_once(tmp_path, threshold=200, retain=80)

    assert result.returncode == 0
    assert scan_log.stat().st_size == 80
    assert scan_log.read_bytes() == payload[-80:]


def test_rotate_once_trims_signal_audit_jsonl(tmp_path: Path) -> None:
    audit = tmp_path / "signal_audit.jsonl"
    payload = b"a" * 300
    audit.write_bytes(payload)

    result = _run_rotate_once(tmp_path, threshold=200, retain=100)

    assert result.returncode == 0
    assert audit.stat().st_size == 100
    assert audit.read_bytes() == payload[-100:]


def test_rotate_once_skips_files_below_threshold(tmp_path: Path) -> None:
    scan_log = tmp_path / "scan.log"
    payload = b"small"
    scan_log.write_bytes(payload)

    result = _run_rotate_once(tmp_path, threshold=200, retain=80)

    assert result.returncode == 0
    assert scan_log.read_bytes() == payload


def test_rotate_once_never_touches_telegram_log_or_env(tmp_path: Path) -> None:
    telegram_log = tmp_path / "telegram.log"
    dot_env = tmp_path / ".env"
    telegram_payload = b"telegram" * 50
    env_payload = b"SECRET=1\n" * 50
    telegram_log.write_bytes(telegram_payload)
    dot_env.write_bytes(env_payload)

    result = _run_rotate_once(tmp_path, threshold=10, retain=5)

    assert result.returncode == 0
    assert telegram_log.read_bytes() == telegram_payload
    assert dot_env.read_bytes() == env_payload


def test_rotate_once_uses_configurable_log_dir(tmp_path: Path) -> None:
    custom_dir = tmp_path / "custom_logs"
    custom_dir.mkdir()
    scan_log = custom_dir / "scan.log"
    payload = b"z" * 250
    scan_log.write_bytes(payload)

    result = _run_rotate_once(custom_dir, threshold=200, retain=60)

    assert result.returncode == 0
    assert scan_log.stat().st_size == 60


@pytest.mark.skipif(not SCRIPT.exists(), reason="run_scanner_loop.sh missing")
def test_script_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)
