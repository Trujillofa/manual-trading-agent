"""Canonical pair-key normalization for persisted scanner state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scanner.state import (
    _canonical_pair_key,
    _load_alignment_state,
    _normalize_pair_keyed_state,
    _save_alignment_state,
)


def test_canonical_pair_key_rewrites_slashless_registry_and_fx() -> None:
    assert _canonical_pair_key("XAUUSD") == "XAU/USD"
    assert _canonical_pair_key("xau/usd") == "XAU/USD"
    assert _canonical_pair_key("NASDAQ") == "NASDAQ"
    assert _canonical_pair_key("eurusd") == "EUR/USD"


def test_normalize_pair_keyed_state_merges_legacy_keys() -> None:
    raw = {
        "XAUUSD": {"direction": "SELL", "bars": 0},
        "XAU/USD": {"direction": "SELL", "bars": 3},
        "NASDAQ": {"direction": "BUY", "bars": 1},
    }
    out = _normalize_pair_keyed_state(raw)
    assert set(out) == {"XAU/USD", "NASDAQ"}
    assert out["XAU/USD"]["bars"] == 3
    assert out["NASDAQ"]["direction"] == "BUY"


def test_load_alignment_state_rewrites_legacy_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    path = logs / "alignment_state.json"
    path.write_text(
        json.dumps({"XAUUSD": {"direction": "SELL", "bars": 2}}),
        encoding="utf-8",
    )

    monkeypatch.setattr("src.scanner.state._logs_dir", lambda: logs)

    loaded = _load_alignment_state()
    assert loaded == {"XAU/USD": {"direction": "SELL", "bars": 2}}
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == {"XAU/USD": {"direction": "SELL", "bars": 2}}


def test_save_alignment_state_writes_canonical_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr("src.scanner.state._logs_dir", lambda: logs)

    _save_alignment_state({"XAUUSD": {"direction": "BUY", "bars": 1}})
    on_disk = json.loads((logs / "alignment_state.json").read_text(encoding="utf-8"))
    assert on_disk == {"XAU/USD": {"direction": "BUY", "bars": 1}}
