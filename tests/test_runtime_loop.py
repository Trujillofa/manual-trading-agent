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


def test_loop_skips_in_container_briefing_when_host_invoke() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "BRIEFING_INVOKE" in text
    assert 'BRIEFING_INVOKE" = "host"' in text
    assert "skip in-container briefing" in text
    assert "python -u -m src.cli pre-ny-briefing" in text


def test_host_briefing_script_uses_emilio_hermes_env() -> None:
    host = SCRIPT.parent / "run_pre_ny_briefing_host.sh"
    assert host.is_file()
    assert os.access(host, os.X_OK)
    text = host.read_text(encoding="utf-8")
    assert "HERMES_HOME" in text
    assert "/home/emilio/.local/bin" in text
    assert "pre-ny-briefing" in text
    exec_lines = [line for line in text.splitlines() if line.lstrip().startswith("exec ")]
    assert exec_lines
    assert all("--safe-mode" not in line for line in exec_lines)


def test_compose_defaults_briefing_invoke_to_host() -> None:
    compose = SCRIPT.parent.parent / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    assert "BRIEFING_INVOKE=${BRIEFING_INVOKE:-host}" in text


def test_deploy_installs_emilio_host_briefing_cron() -> None:
    deploy = SCRIPT.parent / "deploy.sh"
    text = deploy.read_text(encoding="utf-8")
    assert "manual-trading-pre-ny-briefing" in text
    assert "run_pre_ny_briefing_host.sh" in text
    assert "emilio" in text
