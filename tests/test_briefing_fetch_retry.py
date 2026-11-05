"""Briefing fetcher: transient storage errors are retried, everything else is not."""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from src.briefing import service


def _frames() -> dict[str, pd.DataFrame]:
    return {"1h": pd.DataFrame({"close": [1.0]}), "30m": pd.DataFrame(), "15m": pd.DataFrame()}


class _SeqFetcher:
    """DataFetcher stand-in returning/raising a scripted sequence per call."""

    outcomes: list[object] = []

    def __init__(self) -> None:
        pass

    def fetch_multi_timeframe(self, instrument_id: str, period: str | None = None):
        out = self.outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out


@pytest.fixture(autouse=True)
def _patch_fetcher(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("src.data.fetcher.DataFetcher", _SeqFetcher)
    monkeypatch.setattr(service, "_FETCH_BACKOFF_SECONDS", (0.0, 0.0))
    _SeqFetcher.outcomes = []
    yield
    _SeqFetcher.outcomes = []


def test_disk_io_error_is_retried_then_succeeds() -> None:
    """The 32-failure window: SQLite reports disk I/O error under pressure, then recovers."""
    frames = _frames()
    _SeqFetcher.outcomes = [
        sqlite3.OperationalError("disk I/O error"),
        sqlite3.OperationalError("disk I/O error"),
        frames,
    ]
    assert service._default_fetcher("XAU/USD") is frames
    assert _SeqFetcher.outcomes == []


def test_locked_database_is_retried() -> None:
    frames = _frames()
    _SeqFetcher.outcomes = [sqlite3.OperationalError("database is locked"), frames]
    assert service._default_fetcher("XAU/USD") is frames


def test_non_transient_error_is_not_retried() -> None:
    """A bad symbol/URL must fail on the first attempt, not three times."""
    _SeqFetcher.outcomes = [ValueError("no data found for XAU/USD"), _frames()]
    with pytest.raises(ValueError):
        service._default_fetcher("XAU/USD")
    assert len(_SeqFetcher.outcomes) == 1, "the second outcome must never be consumed"


def test_exhausted_retries_reraises_original_error() -> None:
    _SeqFetcher.outcomes = [sqlite3.OperationalError("disk I/O error")] * 3
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        service._default_fetcher("XAU/USD")
    assert _SeqFetcher.outcomes == []


def test_other_operational_errors_are_not_retried() -> None:
    """Only storage-pressure messages are transient; a syntax error is not."""
    _SeqFetcher.outcomes = [sqlite3.OperationalError("near \"SELEC\": syntax error"), _frames()]
    with pytest.raises(sqlite3.OperationalError):
        service._default_fetcher("XAU/USD")
    assert len(_SeqFetcher.outcomes) == 1
