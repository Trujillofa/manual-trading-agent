"""Persist ETR poll snapshots under logs/."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import suppress
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


def _fsync_directory(directory: Path) -> None:
    """Persist a rename.

    The caller logs a failure: the new file is already visible at that point, so
    raising here would misreport a committed write as failed.
    """
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    """Write JSON via temp file + fsync + os.replace.

    A crash before the replace leaves the previous complete file in place; a
    reader never observes a half-written body because the directory entry is
    swapped atomically.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        # os.fdopen owns the descriptor and closes it even if json.dump fails.
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        # mkstemp creates 0600; keep the snapshot as readable as the previous
        # write_text-based file was for other agents sharing the logs volume.
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
        try:
            _fsync_directory(target.parent)
        except OSError as exc:
            logger.warning("ETR state directory fsync failed for %s: %s", target.parent, exc)
    finally:
        with suppress(FileNotFoundError):
            tmp.unlink()


def save_etr_state(state: dict[str, AssetState], path: Path | None = None) -> None:
    target = path or etr_state_path()
    payload = {
        "assets": {key: value.to_dict() for key, value in state.items()},
    }
    _atomic_write_json(target, payload)


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
    payload = {
        "meta": meta,
        "assets": {key: value.to_dict() for key, value in state.items()},
    }
    _atomic_write_json(target, payload)
