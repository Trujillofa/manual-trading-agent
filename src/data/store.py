"""SQLite persistence for OHLCV candles.

Stores historical price data fetched by the scanner so backtests can read from
a local database instead of re-fetching from yfinance every run.

Schema (table ``candles``):
    symbol      TEXT    e.g. "EURUSD"
    timeframe   TEXT    e.g. "15m", "30m", "1h"
    timestamp   INTEGER unix epoch seconds (UTC)
    open        REAL
    high        REAL
    low         REAL
    close       REAL
    volume      INTEGER
    PRIMARY KEY (symbol, timeframe, timestamp)

The composite primary key makes ``INSERT OR REPLACE`` idempotent: re-scanning
the same closed bar simply overwrites it. The store is intentionally
lightweight (stdlib ``sqlite3``, no extra dependencies) and every public
method is defensive — failures are logged and swallowed so the scan cycle
never crashes on a persistence error.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol     TEXT    NOT NULL,
    timeframe  TEXT    NOT NULL,
    timestamp  INTEGER NOT NULL,
    open       REAL    NOT NULL,
    high       REAL    NOT NULL,
    low        REAL    NOT NULL,
    close      REAL    NOT NULL,
    volume     INTEGER NOT NULL,
    PRIMARY KEY (symbol, timeframe, timestamp)
)
"""

_CREATE_TS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf_ts ON candles (symbol, timeframe, timestamp)"
)

# Insert/replace so re-scanning a closed bar refreshes it instead of failing.
_UPSERT_CANDLE = (
    "INSERT OR REPLACE INTO candles "
    "(symbol, timeframe, timestamp, open, high, low, close, volume) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


def _data_dir() -> Path:
    """Resolve the data directory.

    Mirrors ``src.scanner.state._logs_dir()`` precedence:
      1. ``MANUAL_TRADING_AGENT_DATA_DIR`` env var (explicit override, used in tests).
      2. ``/app/data`` inside the Docker container (writable bind mount).
      3. ``./data`` relative to the current working directory (local dev).
    """
    configured = os.getenv("MANUAL_TRADING_AGENT_DATA_DIR")
    if configured:
        return Path(configured)

    app_root = Path("/app")
    if app_root.exists() and os.access(app_root, os.W_OK):
        return app_root / "data"

    return Path.cwd() / "data"


def _db_path() -> Path:
    return _data_dir() / "candles.db"


class CandleStore:
    """Persistent OHLCV candle store backed by SQLite.

    A single ``CandleStore`` instance opens a short-lived connection per write
    (closes the handle after committing), which is fine for the 15-minute scan
    cadence and avoids holding a lock across the whole scan cycle. Reads open
    their own connection and close it on return.
    """

    # Recognized scanner timeframes — persisted as-is for cross-tool consistency.
    KNOWN_TIMEFRAMES: ClassVar[tuple[str, ...]] = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else _db_path()
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()
        except Exception:
            # Schema init failure is non-fatal to the scan; methods will
            # attempt lazy init on next call and log if still broken.
            logger.exception("candle_store: schema init failed at %s", self._db_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        """Open a connection with sane defaults for concurrent scanner writes."""
        conn = sqlite3.connect(self._db_path, timeout=10.0, isolation_level=None)
        # WAL tolerates concurrent readers while the scanner writes; falls back
        # silently to journal mode on filesystems that don't support WAL.
        with contextlib.suppress(sqlite3.DatabaseError):
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(_SCHEMA)
            conn.execute(_CREATE_TS_INDEX)
        finally:
            conn.close()

    @staticmethod
    def _to_unix_epoch(ts: Any) -> int:
        """Coerce a pandas Timestamp (or compatible) to UTC unix seconds."""
        if isinstance(ts, (int, float)):
            return int(ts)
        pts = pd.Timestamp(ts)
        pts = pts.tz_localize("UTC") if pts.tzinfo is None else pts.tz_convert("UTC")
        return int(pts.timestamp())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def save_candles(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
    ) -> int:
        """Upsert OHLCV rows from a DataFrame.

        Expected columns: ``open, high, low, close, volume`` with a
        DatetimeIndex (tz-aware or naive — naive is assumed UTC).

        Args:
            symbol: Normalized pair without slash, e.g. ``"EURUSD"``.
            timeframe: Bar interval, e.g. ``"15m"``.
            df: OHLCV DataFrame returned by ``DataFetcher.fetch``.

        Returns:
            Number of rows written (0 on empty input or failure).
        """
        if df is None or df.empty:
            return 0

        required = ("open", "high", "low", "close")
        if any(col not in df.columns for col in required):
            logger.warning(
                "candle_store: skipping %s %s — missing columns %s",
                symbol,
                timeframe,
                set(required) - set(df.columns),
            )
            return 0

        symbol_norm = symbol.upper().replace("/", "").replace("=X", "")
        try:
            rows = self._df_to_rows(symbol_norm, timeframe, df)
        except Exception:
            logger.exception("candle_store: row extraction failed for %s %s", symbol, timeframe)
            return 0
        if not rows:
            return 0

        try:
            self._init_schema()
            conn = self._connect()
            try:
                conn.executemany(_UPSERT_CANDLE, rows)
            finally:
                conn.close()
            logger.info("candle_store: wrote %d rows for %s %s", len(rows), symbol_norm, timeframe)
            return len(rows)
        except Exception:
            logger.exception(
                "candle_store: write failed for %s %s (%d rows)",
                symbol_norm,
                timeframe,
                len(rows),
            )
            return 0

    def _df_to_rows(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
    ) -> list[tuple[Any, ...]]:
        """Convert an OHLCV DataFrame to sqlite upsert tuples."""
        index = df.index
        # Normalize column dtypes so None/NaN never lands in sqlite.
        frame = df.copy()
        if "volume" not in frame.columns:
            frame["volume"] = 0
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").fillna(0).astype(int)

        rows: list[tuple[Any, ...]] = []
        for ts, record in zip(index, frame.itertuples(index=False), strict=False):
            # itertuples yields a namedtuple in column order.
            rec = record._asdict()
            open_ = float(rec["open"])
            high = float(rec["high"])
            low = float(rec["low"])
            close = float(rec["close"])
            volume = int(rec["volume"])
            # Skip malformed bars (NaN OHLC) rather than poison the table.
            if not (open_ == open_ and high == high and low == low and close == close):
                continue
            ts_int = self._to_unix_epoch(ts)
            rows.append((symbol, timeframe, ts_int, open_, high, low, close, volume))
        return rows

    def save_multi_timeframe(
        self,
        symbol: str,
        mtf: dict[str, pd.DataFrame],
    ) -> dict[str, int]:
        """Persist a ``{"15m": df, "30m": df, "1h": df}`` mapping.

        Returns a ``{timeframe: rows_written}`` dict. Each timeframe is written
        independently so one failure does not block the others.
        """
        results: dict[str, int] = {}
        for timeframe, frame in mtf.items():
            results[timeframe] = self.save_candles(symbol, timeframe, frame)
        return results

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | int | None = None,
        end: datetime | int | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Read candles back as an OHLCV DataFrame indexed by UTC timestamp.

        Args:
            symbol: Pair, with or without slash/``=X`` suffix.
            timeframe: Bar interval.
            start: Optional inclusive lower bound (datetime or unix seconds).
            end: Optional exclusive upper bound (datetime or unix seconds).
            limit: Optional max number of rows (most recent first when set).

        Returns:
            DataFrame with columns ``open, high, low, close, volume`` and a
            tz-aware UTC DatetimeIndex. Empty on error or missing data.
        """
        symbol_norm = symbol.upper().replace("/", "").replace("=X", "")
        query = "SELECT timestamp, open, high, low, close, volume FROM candles "
        query += "WHERE symbol = ? AND timeframe = ?"
        params: list[Any] = [symbol_norm, timeframe]
        if start is not None:
            query += " AND timestamp >= ?"
            params.append(int(start.timestamp()) if isinstance(start, datetime) else int(start))
        if end is not None:
            query += " AND timestamp < ?"
            params.append(int(end.timestamp()) if isinstance(end, datetime) else int(end))
        query += " ORDER BY timestamp ASC"
        if limit is not None and limit > 0:
            query = (
                "SELECT * FROM ("
                + query.replace(
                    "SELECT timestamp",
                    "SELECT timestamp",
                    1,
                )
                + ") ORDER BY timestamp DESC LIMIT ?"
            )
            params.append(int(limit))

        try:
            conn = self._connect()
            try:
                cur = conn.execute(query, params)
                rows = cur.fetchall()
            finally:
                conn.close()
        except Exception:
            logger.exception("candle_store: read failed for %s %s", symbol_norm, timeframe)
            return pd.DataFrame()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        idx = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        df = df.drop(columns=["timestamp"]).set_index(idx)
        df.index.name = "datetime"
        return df

    def count(
        self,
        symbol: str | None = None,
        timeframe: str | None = None,
    ) -> int:
        """Return total row count, optionally filtered by symbol/timeframe."""
        query = "SELECT COUNT(*) FROM candles WHERE 1=1"
        params: list[Any] = []
        if symbol is not None:
            query += " AND symbol = ?"
            params.append(symbol.upper().replace("/", "").replace("=X", ""))
        if timeframe is not None:
            query += " AND timeframe = ?"
            params.append(timeframe)
        try:
            conn = self._connect()
            try:
                cur = conn.execute(query, params)
                (total,) = cur.fetchone()
            finally:
                conn.close()
            return int(total)
        except Exception:
            logger.exception("candle_store: count failed")
            return 0

    def symbols(self) -> list[str]:
        """Return distinct persisted symbols (for diagnostics)."""
        try:
            conn = self._connect()
            try:
                cur = conn.execute("SELECT DISTINCT symbol FROM candles ORDER BY symbol")
                return [row[0] for row in cur.fetchall()]
            finally:
                conn.close()
        except Exception:
            logger.exception("candle_store: symbols() failed")
            return []

    def close(self) -> None:
        """No-op kept for API symmetry; connections are opened per-call."""

    # Iterables of (symbol, timeframe) for bulk verification/tooling.
    def pairs_stored(self) -> Iterable[tuple[str, str]]:
        try:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT DISTINCT symbol, timeframe FROM candles ORDER BY symbol, timeframe"
                )
                return [(r[0], r[1]) for r in cur.fetchall()]
            finally:
                conn.close()
        except Exception:
            logger.exception("candle_store: pairs_stored() failed")
            return []
