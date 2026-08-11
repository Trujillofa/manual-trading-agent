"""Persist ETR poll snapshots under logs/."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.etr.models import AssetState
from src.scanner.state import _logs_dir

logger = logging.getLogger(__name__)


def etr_state_path() -> Path:
    return _logs_dir() / "etr_state.json"


def etr_audit_path() -> Path:
    return _logs_dir() / "etr_audit.jsonl"


def load_etr_state(path: Path | None = None) -> dict[str, AssetState]:
    target = path or etr_state_path()
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load ETR state: %s", exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    assets_raw = payload.get("assets", payload)
    if not isinstance(assets_raw, dict):
        return {}
    out: dict[str, AssetState] = {}
    for key, value in assets_raw.items():
        if isinstance(value, dict):
            out[str(key)] = AssetState.from_dict(value)
    return out


def save_etr_state(state: dict[str, AssetState], path: Path | None = None) -> None:
    target = path or etr_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "assets": {key: value.to_dict() for key, value in state.items()},
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def append_etr_audit(record: dict[str, Any], path: Path | None = None) -> None:
    target = path or etr_audit_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def global_meta_path() -> Path:
    return etr_state_path()


def load_global_meta() -> dict[str, Any]:
    target = etr_state_path()
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    meta = payload.get("meta")
    return dict(meta) if isinstance(meta, dict) else {}


def save_state_with_meta(
    state: dict[str, AssetState],
    meta: dict[str, Any],
    path: Path | None = None,
) -> None:
    target = path or etr_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": meta,
        "assets": {key: value.to_dict() for key, value in state.items()},
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
