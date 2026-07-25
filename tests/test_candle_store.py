"""Tests for the SQLite candle persistence layer."""

from __future__ import annotations

import sqlite3
import time

import pandas as pd
import pytest

from src.data.store import CandleStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A CandleStore pointing at an isolated tmp database."""
    db = tmp_path / "candles.db"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_DATA_DIR", str(tmp_path))
    return CandleStore(db_path=db)


def _sample_df(n: int = 5, start_ts: int | None = None) -> pd.DataFrame:
    """Build an OHLCV DataFrame with a tz-aware UTC DatetimeIndex."""
    base = start_ts if start_ts is not None else int(time.time())
    idx = pd.date_range(
        start=pd.Timestamp(base, unit="s", tz="UTC"),
        periods=n,
        freq="15min",
    )
    return pd.DataFrame(
        {
            "open": [1.0850 + i * 0.0005 for i in range(n)],
            "high": [1.0860 + i * 0.0005 for i in range(n)],
            "low": [1.0840 + i * 0.0005 for i in range(n)],
            "close": [1.0855 + i * 0.0005 for i in range(n)],
            "volume": [1000 + i for i in range(n)],
        },
        index=idx,
    )


def test_save_and_count_round_trip(store: CandleStore) -> None:
    """Saved rows are counted and read back unchanged."""
    df = _sample_df(n=5)
    written = store.save_candles("EUR/USD", "15m", df)
    assert written == 5
    assert store.count() == 5
    assert store.count("EURUSD", "15m") == 5
    assert store.count("EURUSD", "1h") == 0
    assert store.count("GBPUSD") == 0


def test_save_normalizes_symbol_formats(store: CandleStore) -> None:
    """Symbol variants map to the same normalized key."""
    df = _sample_df(n=1)
    assert store.save_candles("EUR/USD", "15m", df) == 1
    # Slash form, raw form, and =X suffix form should all hit the same row.
    assert store.count("EURUSD") == 1
    assert store.count("eur/usd") == 1
    assert store.count("EURUSD=X") == 1


def test_upsert_is_idempotent(store: CandleStore) -> None:
    """Re-saving the same timestamp replaces instead of duplicating."""
    df = _sample_df(n=3)
    assert store.save_candles("EURUSD", "15m", df) == 3
    assert store.count() == 3

    # Mutate the close of the middle bar and re-save the same window.
    df2 = df.copy()
    df2.iloc[1, df2.columns.get_loc("close")] = 1.9999
    assert store.save_candles("EURUSD", "15m", df2) == 3
    assert store.count() == 3  # no duplicate

    back = store.get_candles("EURUSD", "15m")
    assert len(back) == 3
    # The overwritten middle close persists.
    assert pytest.approx(back.iloc[1]["close"]) == 1.9999


def test_get_candles_returns_utc_indexed_ohlcv(store: CandleStore) -> None:
    """Read-back produces the canonical schema with a UTC DatetimeIndex."""
    df = _sample_df(n=4)
    store.save_candles("GBPUSD", "15m", df)

    out = store.get_candles("GBPUSD", "15m")
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert out.index.name == "datetime"
    assert str(out.index.tz) == "UTC"
    # Monotonic ascending timestamps.
    assert out.index.is_monotonic_increasing


def test_get_candles_time_range_filter(store: CandleStore) -> None:
    """start/end bounds slice the window correctly."""
    base = int(time.time())
    df = _sample_df(n=10, start_ts=base)
    store.save_candles("EURUSD", "15m", df)

    from datetime import UTC, datetime

    # Window covers only the second half of the 10-bar series.
    start_dt = datetime.fromtimestamp(base + 5 * 15 * 60, tz=UTC)
    out = store.get_candles("EURUSD", "15m", start=start_dt)
    assert len(out) == 5


def test_save_multi_timeframe(store: CandleStore) -> None:
    """Multi-timeframe persist writes each TF independently."""
    mtf = {
        "1h": _sample_df(n=3),
        "30m": _sample_df(n=4),
        "15m": _sample_df(n=5),
    }
    results = store.save_multi_timeframe("EURUSD", mtf)
    assert results == {"1h": 3, "30m": 4, "15m": 5}
    assert store.count("EURUSD", "1h") == 3
    assert store.count("EURUSD", "30m") == 4
    assert store.count("EURUSD", "15m") == 5


def test_save_empty_df_is_noop(store: CandleStore) -> None:
    """Empty input writes nothing and returns 0."""
    assert store.save_candles("EURUSD", "15m", pd.DataFrame()) == 0
    assert store.count() == 0


def test_save_missing_columns_is_noop(store: CandleStore) -> None:
    """A DataFrame lacking OHLC columns is skipped, not crashed on."""
    bad = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    assert store.save_candles("EURUSD", "15m", bad) == 0
    assert store.count() == 0


def test_naive_timestamp_index_treated_as_utc(store: CandleStore) -> None:
    """A tz-naive DatetimeIndex is interpreted as UTC, not local time."""
    idx = pd.date_range("2024-01-01 00:00", periods=3, freq="15min")
    df = pd.DataFrame(
        {
            "open": [1.0, 1.1, 1.2],
            "high": [1.1, 1.2, 1.3],
            "low": [0.9, 1.0, 1.1],
            "close": [1.05, 1.15, 1.25],
            "volume": [10, 20, 30],
        },
        index=idx,
    )
    assert store.save_candles("EURUSD", "15m", df) == 3
    out = store.get_candles("EURUSD", "15m")
    assert str(out.index.tz) == "UTC"
    # The first bar's epoch should match 2024-01-01 00:00 UTC.
    assert int(out.index[0].timestamp()) == 1704067200


def test_symbols_and_pairs_stored(store: CandleStore) -> None:
    """Diagnostic listing helpers reflect what was persisted."""
    store.save_candles("EUR/USD", "15m", _sample_df(n=1))
    store.save_candles("GBP/USD", "1h", _sample_df(n=1))
    assert set(store.symbols()) == {"EURUSD", "GBPUSD"}
    pairs = set(store.pairs_stored())
    assert pairs == {("EURUSD", "15m"), ("GBPUSD", "1h")}


def test_corrupt_db_does_not_raise_on_count(tmp_path) -> None:
    """A corrupt/uninitialized db file degrades gracefully on read."""
    bad_db = tmp_path / "broken.db"
    bad_db.write_bytes(b"not a sqlite database")
    store = CandleStore(db_path=bad_db)
    # count() must return 0 (logged), not raise.
    assert store.count() == 0
    assert store.get_candles("EURUSD", "15m").empty


def test_table_schema_matches_contract(tmp_path) -> None:
    """The candles table has the documented columns and primary key."""
    store = CandleStore(db_path=tmp_path / "candles.db")
    store.save_candles("EURUSD", "15m", _sample_df(n=1))
    conn = sqlite3.connect(tmp_path / "candles.db")
    try:
        cur = conn.execute("PRAGMA table_info(candles)")
        cols = {row[1]: row[2] for row in cur.fetchall()}
        assert cols == {
            "symbol": "TEXT",
            "timeframe": "TEXT",
            "timestamp": "INTEGER",
            "open": "REAL",
            "high": "REAL",
            "low": "REAL",
            "close": "REAL",
            "volume": "INTEGER",
        }
        # Composite PK on (symbol, timeframe, timestamp) — introspect via
        # table_info (pk column flag) which is stable across sqlite versions.
        ti = conn.execute("PRAGMA table_info(candles)").fetchall()
        pk_cols = [row[1] for row in ti if row[5] > 0]  # row[5] = pk order, 0 = not pk
        assert pk_cols == ["symbol", "timeframe", "timestamp"]
    finally:
        conn.close()
