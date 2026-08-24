"""Idempotency state for the pre-NY briefing (logs/pre_ny_briefing_state.json)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.scanner.state import _load_json_mapping, _logs_dir


def briefing_state_path() -> Path:
    return _logs_dir() / "pre_ny_briefing_state.json"


def load_briefing_state() -> dict[str, Any]:
    return _load_json_mapping(briefing_state_path())


def save_briefing_state(state: dict[str, Any]) -> None:
    path = briefing_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
