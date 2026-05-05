"""Pivot point level calculations — standard, weekly, Camarilla, and session open."""
from __future__ import annotations

import datetime

import pandas as pd


# ---------------------------------------------------------------------------
# Standard (floor) pivots
# ---------------------------------------------------------------------------

def standard_pivots(prev_high: float, prev_low: float, prev_close: float) -> dict[str, float]:
    """Standard floor pivot points from previous period OHLC."""
    pp = (prev_high + prev_low + prev_close) / 3
    r1 = 2 * pp - prev_low
    r2 = pp + (prev_high - prev_low)
    r3 = prev_high + 2 * (pp - prev_low)
    s1 = 2 * pp - prev_high
    s2 = pp - (prev_high - prev_low)
    s3 = prev_low - 2 * (prev_high - pp)
    return {"pp": pp, "r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2, "s3": s3}


def build_daily_pivot_map(data_1h: pd.DataFrame) -> dict[datetime.date, dict[str, float]]:
    """Return {calendar_date: pivot_levels} — pivots from the *previous* calendar day's H/L/C."""
    daily = (
        data_1h.resample("1D")
        .agg({"high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    pivot_map: dict[datetime.date, dict[str, float]] = {}
    idx = daily.index.tolist()
    for i in range(1, len(idx)):
        prev = daily.iloc[i - 1]
        pivot_map[idx[i].date()] = standard_pivots(
            float(prev["high"]), float(prev["low"]), float(prev["close"])
        )
    return pivot_map


# ---------------------------------------------------------------------------
# Weekly pivots
# ---------------------------------------------------------------------------

def build_weekly_pivot_map(data_1h: pd.DataFrame) -> dict[datetime.date, dict[str, float]]:
    """Return {calendar_date: pivot_levels} where levels come from the *previous* week's H/L/C.

    Each Mon–Fri date of a given week maps to pivot levels computed from the
    prior week (resampled week ending Friday).
    """
    weekly = (
        data_1h.resample("W-FRI")
        .agg({"high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    pivot_map: dict[datetime.date, dict[str, float]] = {}
    idx = weekly.index.tolist()
    for i in range(1, len(idx)):
        prev = weekly.iloc[i - 1]
        pivots = standard_pivots(float(prev["high"]), float(prev["low"]), float(prev["close"]))
        # idx[i] is the Friday of the current week; Monday = Friday - 4 days
        week_monday = idx[i].date() - datetime.timedelta(days=4)
        for day_offset in range(5):  # Mon through Fri
            pivot_map[week_monday + datetime.timedelta(days=day_offset)] = pivots
    return pivot_map


# ---------------------------------------------------------------------------
# Camarilla pivots
# ---------------------------------------------------------------------------

def camarilla_pivots(prev_high: float, prev_low: float, prev_close: float) -> dict[str, float]:
    """Camarilla pivot levels from previous period OHLC.

    Mean-reversion zones: L3/L4 for support (BUY), H3/H4 for resistance (SELL).
    L4/H4 are the strongest reversal levels; L3/H3 are earlier warning levels.
    """
    rng = prev_high - prev_low
    return {
        "h4": prev_close + rng * 1.1 / 2,
        "h3": prev_close + rng * 1.1 / 4,
        "h2": prev_close + rng * 1.1 / 6,
        "h1": prev_close + rng * 1.1 / 12,
        "l1": prev_close - rng * 1.1 / 12,
        "l2": prev_close - rng * 1.1 / 6,
        "l3": prev_close - rng * 1.1 / 4,
        "l4": prev_close - rng * 1.1 / 2,
    }


def build_camarilla_map(data_1h: pd.DataFrame) -> dict[datetime.date, dict[str, float]]:
    """Return {calendar_date: camarilla_levels} from the *previous* calendar day's H/L/C."""
    daily = (
        data_1h.resample("1D")
        .agg({"high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    cam_map: dict[datetime.date, dict[str, float]] = {}
    idx = daily.index.tolist()
    for i in range(1, len(idx)):
        prev = daily.iloc[i - 1]
        cam_map[idx[i].date()] = camarilla_pivots(
            float(prev["high"]), float(prev["low"]), float(prev["close"])
        )
    return cam_map


# ---------------------------------------------------------------------------
# Session open S/R
# ---------------------------------------------------------------------------

# UTC hours at which each forex session opens
SESSION_OPENS_UTC: dict[str, int] = {
    "london": 7,   # 07:00 UTC
    "ny":     13,  # 13:00 UTC
}

# Active window (hours) for each session
SESSION_WINDOWS_UTC: dict[str, tuple[int, int]] = {
    "london": (7,  17),
    "ny":     (13, 22),
}


def build_session_open_map(
    data_15m: pd.DataFrame,
) -> dict[tuple[datetime.date, str], float]:
    """Return {(date, session_name): open_price} for London and NY session opens.

    Session open = open price of the first 15m bar at exactly HH:00 UTC.
    If no bar lands on the exact minute (holiday / gap), that session is absent
    from the map and will be skipped in the backtest.
    """
    result: dict[tuple[datetime.date, str], float] = {}
    for ts in data_15m.index:
        if ts.minute != 0:
            continue
        date = ts.date()
        hour = ts.hour
        for session, open_hour in SESSION_OPENS_UTC.items():
            if hour == open_hour:
                key = (date, session)
                if key not in result:
                    result[key] = float(data_15m.loc[ts, "open"])
    return result
