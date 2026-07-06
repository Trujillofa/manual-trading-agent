"""Managed log size monitoring for rotation threshold alerts."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.scanner.state import _load_json_mapping, _logs_dir

LogLevel = Literal["ok", "warn", "critical"]
MANAGED_LOG_FILES = ("scan.log", "signal_audit.jsonl")
LOG_SIZE_WARN_RATIO = 0.80
LOG_SIZE_CRITICAL_RATIO = 0.95
DEFAULT_ROTATE_THRESHOLD_BYTES = 52_428_800  # 50 MiB


@dataclass(frozen=True)
class ManagedLogStatus:
    name: str
    path: Path
    size_bytes: int
    threshold_bytes: int
    pct_of_threshold: float
    level: LogLevel


def _rotate_threshold_bytes() -> int:
    raw = os.getenv("ROTATE_THRESHOLD_BYTES")
    if raw is None:
        return DEFAULT_ROTATE_THRESHOLD_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_ROTATE_THRESHOLD_BYTES
    return value if value > 0 else DEFAULT_ROTATE_THRESHOLD_BYTES


def _level_for_pct(pct: float) -> LogLevel:
    if pct >= LOG_SIZE_CRITICAL_RATIO * 100:
        return "critical"
    if pct >= LOG_SIZE_WARN_RATIO * 100:
        return "warn"
    return "ok"


def managed_log_statuses(
    *,
    logs_dir: Path | None = None,
    threshold_bytes: int | None = None,
) -> list[ManagedLogStatus]:
    """Return size status for logs rotated by run_scanner_loop.sh."""
    base = logs_dir or _logs_dir()
    threshold = threshold_bytes if threshold_bytes is not None else _rotate_threshold_bytes()
    statuses: list[ManagedLogStatus] = []

    for name in MANAGED_LOG_FILES:
        path = base / name
        size_bytes = path.stat().st_size if path.exists() else 0
        pct = (size_bytes / threshold * 100) if threshold else 0.0
        statuses.append(
            ManagedLogStatus(
                name=name,
                path=path,
                size_bytes=size_bytes,
                threshold_bytes=threshold,
                pct_of_threshold=pct,
                level=_level_for_pct(pct),
            )
        )
    return statuses


def _log_alert_state_path(logs_dir: Path | None = None) -> Path:
    return (logs_dir or _logs_dir()) / "log_size_alert_state.json"


def _load_log_alert_state(logs_dir: Path | None = None) -> dict[str, str]:
    return _load_json_mapping(_log_alert_state_path(logs_dir))


def _save_log_alert_state(state: dict[str, str], logs_dir: Path | None = None) -> None:
    path = _log_alert_state_path(logs_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _format_bytes(num_bytes: int) -> str:
    if num_bytes >= 1_048_576:
        return f"{num_bytes / 1_048_576:.1f} MiB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f} KiB"
    return f"{num_bytes} B"


def format_log_status_report(statuses: list[ManagedLogStatus]) -> str:
    lines = ["Managed log rotation status:"]
    for status in statuses:
        lines.append(
            f"- {status.name}: {_format_bytes(status.size_bytes)} / "
            f"{_format_bytes(status.threshold_bytes)} ({status.pct_of_threshold:.1f}%) "
            f"[{status.level}]"
        )
    return "\n".join(lines)


def build_log_alert_messages(
    statuses: list[ManagedLogStatus],
    state: dict[str, str],
) -> tuple[list[str], dict[str, str]]:
    """Build alert messages for newly escalated warn/critical levels."""
    messages: list[str] = []
    next_state = dict(state)

    for status in statuses:
        if status.level == "ok":
            if state.get(status.name) in {"warn", "critical"}:
                next_state.pop(status.name, None)
            continue

        previous = state.get(status.name)
        if previous == status.level:
            continue

        emoji = "🚨" if status.level == "critical" else "⚠️"
        messages.append(
            f"{emoji} *Log rotation alert*\n\n"
            f"File: `{status.name}`\n"
            f"Size: `{_format_bytes(status.size_bytes)}` "
            f"({status.pct_of_threshold:.1f}% of {_format_bytes(status.threshold_bytes)} threshold)\n"
            f"Level: `{status.level}`\n"
            f"Rotation retains the newest 25 MiB when the 50 MiB threshold is reached."
        )
        next_state[status.name] = status.level

    return messages, next_state
