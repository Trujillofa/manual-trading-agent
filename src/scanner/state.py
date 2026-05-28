"""JSON persistence and file path helpers for the scanner pipeline."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict, cast

SCAN_HEALTH_MAX_AGE_SECONDS = 30 * 60
TELEGRAM_HEARTBEAT_MAX_AGE_SECONDS = 5 * 60


class NearStateRecord(TypedDict, total=False):
    fingerprint: str
    sent_at: int
    kind: str
    miss_count: int


# Number of consecutive scans a pair must drop out of its tracked state
# before its pre-signal alert is considered invalidated (anti-flicker).
INVALIDATION_MISS_THRESHOLD = 2


class ActiveSignalRecord(TypedDict, total=False):
    direction: str
    fired_at: int
    entry: float
    tp: float
    sl: float
    sma_side: str


class AlignmentStateRecord(TypedDict, total=False):
    direction: str
    bars: int


def _logs_dir() -> Path:
    configured = os.getenv("MANUAL_TRADING_AGENT_LOG_DIR")
    if configured:
        return Path(configured)

    app_root = Path("/app")
    if app_root.exists() and os.access(app_root, os.W_OK):
        return app_root / "logs"

    return Path.cwd() / "logs"


def _near_setup_state_path() -> Path:
    return _logs_dir() / "near_setup_state.json"


def _load_json_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}


def _load_near_setup_state() -> dict[str, NearStateRecord]:
    path = _near_setup_state_path()
    return cast(dict[str, NearStateRecord], _load_json_mapping(path))


def _save_near_setup_state(state: dict[str, NearStateRecord]) -> None:
    path = _near_setup_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _alignment_state_path() -> Path:
    return _logs_dir() / "alignment_state.json"


def _load_alignment_state() -> dict[str, AlignmentStateRecord]:
    path = _alignment_state_path()
    return cast(dict[str, AlignmentStateRecord], _load_json_mapping(path))


def _save_alignment_state(state: dict[str, AlignmentStateRecord]) -> None:
    path = _alignment_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _active_signal_state_path() -> Path:
    return _logs_dir() / "active_signal_state.json"


def _load_active_signal_state() -> dict[str, ActiveSignalRecord]:
    path = _active_signal_state_path()
    return cast(dict[str, ActiveSignalRecord], _load_json_mapping(path))


def _save_active_signal_state(state: dict[str, ActiveSignalRecord]) -> None:
    path = _active_signal_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _audit_log_path() -> Path:
    return _logs_dir() / "signal_audit.jsonl"


def _scan_log_path() -> Path:
    return Path("/app/logs/scan.log")


def _telegram_heartbeat_path() -> Path:
    return Path("/app/logs/telegram_heartbeat.json")


def _append_audit_log(payload: dict[str, object]) -> None:
    path = _audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


def _pending_trades_path() -> Path:
    return _logs_dir() / "pending_trades.json"


def _load_pending_trades() -> list[dict[str, Any]]:
    path = _pending_trades_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_pending_trades(trades: list[dict[str, Any]]) -> None:
    path = _pending_trades_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trades, indent=2), encoding="utf-8")


def _check_trade_outcome(
    trade: dict[str, Any],
    bar_times_unix: list[int],
    highs: list[float],
    lows: list[float],
) -> str | None:
    """Return 'tp', 'sl', or None. Checks each 15m bar after signal fired_at."""
    fired_at = int(trade["fired_at"])
    direction = str(trade["direction"])
    tp = float(trade["tp"])
    sl = float(trade["sl"])
    for ts, h, lo in zip(bar_times_unix, highs, lows, strict=True):
        if ts <= fired_at:
            continue
        if direction == "BUY":
            if h >= tp:
                return "tp"
            if lo <= sl:
                return "sl"
        else:
            if lo <= tp:
                return "tp"
            if h >= sl:
                return "sl"
    return None


def _path_age_seconds(path: Path, now_utc: datetime) -> float | None:
    if not path.exists():
        return None
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return max((now_utc - modified_at).total_seconds(), 0.0)
