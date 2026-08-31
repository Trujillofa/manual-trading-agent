"""DST-safe clocks for the 13:30 UTC / NY cash-open lane.

Cash equity open is 09:30 America/New_York. That is 13:30 UTC during EDT
and 14:30 UTC during EST. The desk's ny_open_utc=12:00 is the FX NY window,
not this event.

Naive 13:30 UTC year-round is a *different* clock and is tested as such.

FP Markets and Vantage print server wall = America/New_York + 7h (EET/EEST
on US DST dates). Files that stamp +00:00 or Europe/Athens are mislabeled;
do not use a single export-time UTC offset.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

TZ_ET = ZoneInfo("America/New_York")
TZ_LONDON = ZoneInfo("Europe/London")
SERVER_AHEAD_OF_ET = timedelta(hours=7)

CASH_OPEN_ET = time(9, 30)
FX_NY_UTC = time(12, 0)
NAIVE_1330_UTC = time(13, 30)


def as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def to_et(ts: datetime) -> datetime:
    return as_utc(ts).astimezone(TZ_ET)


def cash_open_utc(et_day: date) -> datetime:
    """09:30 America/New_York on et_day, as UTC."""
    return datetime(et_day.year, et_day.month, et_day.day, 9, 30, tzinfo=TZ_ET).astimezone(UTC)


def naive_1330_utc(utc_day: date) -> datetime:
    return datetime(utc_day.year, utc_day.month, utc_day.day, 13, 30, tzinfo=UTC)


def fx_ny_open_utc(utc_day: date) -> datetime:
    return datetime(utc_day.year, utc_day.month, utc_day.day, 12, 0, tzinfo=UTC)


def london_open_utc(utc_day: date) -> datetime:
    """08:00 Europe/London — contrast session, not the hypothesis."""
    return datetime(utc_day.year, utc_day.month, utc_day.day, 8, 0, tzinfo=TZ_LONDON).astimezone(
        UTC
    )


def is_et_weekday(et_day: date) -> bool:
    return et_day.weekday() < 5


def et_dst_label(et_day: date) -> str:
    """EDT if 09:30 ET == 13:30 UTC, else EST."""
    return "EDT" if cash_open_utc(et_day).hour == 13 else "EST"


def server_naive_to_utc(naive: datetime) -> datetime:
    """Map one broker-server wall clock to UTC via ET+7.

    Documented check: Vantage 2025-03-05 15:00 -> 2025-03-05 13:00 UTC
    (Dukascopy EURUSD H1).
    """
    if naive.tzinfo is not None:
        naive = naive.replace(tzinfo=None)
    et = naive - SERVER_AHEAD_OF_ET
    return datetime(
        et.year,
        et.month,
        et.day,
        et.hour,
        et.minute,
        et.second,
        et.microsecond,
        tzinfo=TZ_ET,
    ).astimezone(UTC)


def server_wall_to_utc(wall: pd.Series) -> pd.Series:
    """Vectorized server_naive_to_utc. Drops any tz label (false +00:00).

    Ambiguous DST (US fall-back 01:00–02:00 ET = server 08:00–09:00): localize
    to NaT and drop. Do not infer or fold — a guessed occurrence is a silent
    1h error. Count is on the returned Series as attrs['ambiguous_dropped'].
    """
    naive = pd.to_datetime(wall, errors="coerce")
    if getattr(naive.dt, "tz", None) is not None:
        naive = naive.dt.tz_localize(None)
    et_wall = naive - pd.Timedelta(hours=7)
    # Safer for research than ambiguous="infer" (raises) or a fold guess:
    # drop the US fall-back hour instead of inventing which occurrence it is.
    # Today's books have 0 bars in that server hour (Sun 08:00–09:00).
    localized = et_wall.dt.tz_localize(TZ_ET, ambiguous="NaT", nonexistent="shift_forward")
    n_ambiguous = int(localized.isna().sum() - et_wall.isna().sum())
    out = localized.dt.tz_convert(UTC)
    out.attrs["ambiguous_dropped"] = n_ambiguous
    return out


# --- Clock admission guard (hourly log-return lag scan) ---

CLOCK_LAGS = range(-4, 5)
MIN_HOURLY_OVERLAP = 30


def hourly_log_returns(df: pd.DataFrame) -> pd.Series:
    """Resample OHLC to hourly last close, then log returns."""
    s = df.set_index(pd.to_datetime(df["time_utc"], utc=True))["close"].sort_index()
    hourly = s.resample("1h").last().dropna()
    px = hourly.astype(float)
    lr = np.log(px / px.shift(1))
    return lr.replace([np.inf, -np.inf], np.nan).dropna()


def lag_correlations(
    candidate: pd.Series,
    reference: pd.Series,
    lags: range = CLOCK_LAGS,
) -> dict[int, float]:
    """Pearson corr of candidate.shift(lag) vs reference at each lag (hours)."""
    scores: dict[int, float] = {}
    for lag in lags:
        both = pd.concat([candidate.shift(lag), reference], axis=1, join="inner").dropna()
        both.columns = ["c", "r"]
        if len(both) < MIN_HOURLY_OVERLAP:
            scores[lag] = float("nan")
        else:
            scores[lag] = float(both["c"].corr(both["r"]))
    return scores


def clock_lag_scan(
    candidate: pd.DataFrame,
    reference: pd.DataFrame,
    *,
    invert_reference: bool = False,
    lags: range = CLOCK_LAGS,
) -> dict:
    """Shift candidate hourly log returns by -4..+4h; report argmax vs reference.

    invert_reference: compare to -ref (DXY vs EURUSD is the textbook inverse).
    C1 (XAU stamped +00:00): argmax is lag -3, not 0.
    """
    cand = hourly_log_returns(candidate)
    ref = hourly_log_returns(reference)
    if invert_reference:
        ref = -ref
    scores = lag_correlations(cand, ref, lags=lags)
    finite = {k: v for k, v in scores.items() if v == v}
    joined = pd.concat([cand, ref], axis=1, join="inner").dropna()
    n = int(len(joined))
    if not finite:
        return {
            "best_lag_hours": None,
            "correlation": None,
            "n_hourly": n,
            "corr_by_lag": {str(k): (None if v != v else v) for k, v in scores.items()},
        }
    best = max(finite, key=finite.get)
    return {
        "best_lag_hours": int(best),
        "correlation": float(finite[best]),
        "n_hourly": n,
        "corr_by_lag": {str(k): (None if v != v else float(v)) for k, v in scores.items()},
    }


def clock_pair_passes(scan: dict, *, min_corr: float) -> bool:
    """Admit only when the correlation argmax is exactly lag 0 and above the pair floor."""
    return (
        scan.get("best_lag_hours") == 0
        and scan.get("correlation") is not None
        and scan["correlation"] >= min_corr
        and (scan.get("n_hourly") or 0) >= MIN_HOURLY_OVERLAP
    )
