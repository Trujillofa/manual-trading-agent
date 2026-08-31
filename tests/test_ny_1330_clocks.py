"""Clock, slice, stop, and gate tests for the NY 13:30 research lane.

Uses committed fixtures only — no live 5y I/O.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parents[1]
LANE = HERE / "scripts" / "research" / "ny_1330"
FIX = HERE / "tests" / "fixtures" / "ny_1330"
sys.path.insert(0, str(LANE))

from clocks import (  # noqa: E402
    cash_open_utc,
    clock_lag_scan,
    clock_pair_passes,
    server_naive_to_utc,
    server_wall_to_utc,
)
from loaders import (  # noqa: E402
    admit_validated,
    load_fp_mt5,
    load_vantage_m5,
    load_xau_mixed,
    validate_loaded_clocks,
)
from run_lane import (  # noqa: E402
    PF_HOLDOUT,
    PF_SURVIVE,
    _slice,
    _trade_metrics,
    _verdict,
    run_scalp,
)

ATHENS = ZoneInfo("Europe/Athens")


def _hourly_ret_corr(a: pd.DataFrame, b: pd.DataFrame, *, lag: int) -> float:
    left = a.set_index("time_utc")["close"].sort_index()
    right = b.set_index("time_utc")["close"].sort_index()
    if lag != 0:
        right = right.shift(lag)
    both = pd.concat([left, right], axis=1, join="inner").dropna()
    both.columns = ["a", "b"]
    if len(both) < 8:
        return float("nan")
    ra = both["a"].pct_change()
    rb = both["b"].pct_change()
    ok = ra.notna() & rb.notna()
    if ok.sum() < 6:
        return float("nan")
    return float(ra[ok].corr(rb[ok]))


def _lag_peak(a: pd.DataFrame, b: pd.DataFrame, lags=range(-3, 4)) -> tuple[int, float]:
    scores = {lag: _hourly_ret_corr(a, b, lag=lag) for lag in lags}
    peak = max(scores, key=lambda k: scores[k] if scores[k] == scores[k] else -9)
    return peak, scores[peak]


def test_et7_maps_documented_vantage_2025_03_05():
    got = server_naive_to_utc(datetime(2025, 3, 5, 15, 0))
    assert got == datetime(2025, 3, 5, 13, 0, tzinfo=UTC)


def test_et7_shoulder_not_athens():
    # US DST on (2025-03-09), EU DST off until 2025-03-30.
    server = datetime(2025, 3, 12, 16, 0)
    et7 = server_naive_to_utc(server)
    athens = datetime(2025, 3, 12, 16, 0, tzinfo=ATHENS).astimezone(UTC)
    assert et7 == datetime(2025, 3, 12, 13, 0, tzinfo=UTC)
    assert athens == datetime(2025, 3, 12, 14, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("wall", "expect_utc"),
    [
        (datetime(2025, 1, 15, 16, 30), datetime(2025, 1, 15, 14, 30, tzinfo=UTC)),  # EST
        (datetime(2025, 7, 15, 16, 30), datetime(2025, 7, 15, 13, 30, tzinfo=UTC)),  # EDT
        (datetime(2025, 3, 12, 16, 30), datetime(2025, 3, 12, 13, 30, tzinfo=UTC)),  # shoulder
        (datetime(2025, 10, 28, 16, 30), datetime(2025, 10, 28, 13, 30, tzinfo=UTC)),  # autumn
    ],
)
def test_et7_dst_and_standard_and_shoulder(wall, expect_utc):
    assert server_naive_to_utc(wall) == expect_utc


def test_xau_false_utc_aligns_dukas_lag0():
    xau = load_xau_mixed(FIX / "xau_vantage_h1_false_utc.csv", "H1")
    dukas = pd.read_csv(FIX / "xau_dukas_h1_utc.csv")
    dukas["time_utc"] = pd.to_datetime(dukas["time_utc"], utc=True)
    peak, corr = _lag_peak(xau, dukas)
    assert peak == 0
    assert corr >= 0.95
    # as-stored (no ET+7) is the known mismatch
    raw = pd.read_csv(FIX / "xau_vantage_h1_false_utc.csv")
    raw["time_utc"] = pd.to_datetime(raw["time"], utc=True)
    raw_peak, raw_corr = _lag_peak(raw, dukas)
    assert raw_peak != 0 or raw_corr < 0.5


def test_vantage_eurusd_shoulder_lag0():
    van = load_vantage_m5(FIX / "eurusd_vantage_h1ish_server.csv")
    dukas = pd.read_csv(FIX / "eurusd_dukas_h1_utc.csv")
    dukas["time_utc"] = pd.to_datetime(dukas["time_utc"], utc=True)
    peak, corr = _lag_peak(van, dukas)
    assert peak == 0
    assert corr >= 0.95

    joined = van.set_index("time_utc")[["close"]].join(
        dukas.set_index("time_utc")[["close"]], lsuffix="_v", rsuffix="_d", how="inner"
    )
    # US-on / EU-off: server 16:00 is 13:00 UTC, not Athens 14:00
    for t in (
        pd.Timestamp("2025-03-12 13:00", tz="UTC"),
        pd.Timestamp("2025-10-28 13:00", tz="UTC"),
    ):
        assert t in joined.index
        assert abs(float(joined.loc[t, "close_v"]) - float(joined.loc[t, "close_d"])) < 0.002


def test_fp_btcusd_aligns_binance_lag0():
    fp = load_fp_mt5(FIX / "btcusd_fp_h1_server.csv")
    bn = pd.read_csv(FIX / "btcusdt_binance_h1_utc.csv")
    bn = bn.rename(
        columns={
            "open_price": "open",
            "high_price": "high",
            "low_price": "low",
            "close_price": "close",
        }
    )
    bn["time_utc"] = pd.to_datetime(bn["time"], utc=True)
    peak, corr = _lag_peak(fp, bn)
    assert peak == 0
    assert corr >= 0.95


def test_fp_us100_winter_not_10800():
    fp = load_fp_mt5(FIX / "us100_fp_m5_server.csv")
    ts = pd.to_datetime(fp["time_utc"], utc=True)
    winter = ts[ts.dt.date == date(2025, 12, 15)]
    summer = ts[ts.dt.date == date(2026, 7, 15)]
    assert not winter.empty and not summer.empty
    # server 16:30 is cash 09:30 ET
    assert datetime(2025, 12, 15, 14, 30, tzinfo=UTC) in set(winter)
    assert datetime(2026, 7, 15, 13, 30, tzinfo=UTC) in set(summer)
    # old scalar 10800 would put winter 16:30 at 13:30 UTC
    assert datetime(2025, 12, 15, 13, 30, tzinfo=UTC) not in set(winter)


def test_slice_h1_1430_uses_1400_not_1500():
    idx = pd.date_range("2026-01-15 12:00", periods=8, freq="h", tz="UTC")
    ts = idx.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    event = cash_open_utc(date(2026, 1, 15))  # EST -> 14:30 UTC
    assert event.hour == 14 and event.minute == 30
    i, j = _slice(ts, event, 60, bar_minutes=60)
    assert ts[i] == np.datetime64("2026-01-15T14:00:00")
    assert j > i
    ev = np.datetime64(event.replace(tzinfo=None), "ns")
    assert ts[i] <= ev < (ts[j] if j < len(ts) else ts[i] + np.timedelta64(60, "m"))
    assert ts[i] != np.datetime64("2026-01-15T15:00:00")


def test_slice_h1_edt_1330_uses_1300():
    idx = pd.date_range("2026-07-15 12:00", periods=8, freq="h", tz="UTC")
    ts = idx.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    event = cash_open_utc(date(2026, 7, 15))  # EDT -> 13:30 UTC
    assert event.hour == 13
    i, j = _slice(ts, event, 60, bar_minutes=60)
    assert ts[i] == np.datetime64("2026-07-15T13:00:00")
    ev = np.datetime64(event.replace(tzinfo=None), "ns")
    assert ts[i] <= ev < ts[j]


def test_slice_rth_30_grid_1400_uses_containing_1330():
    """Yahoo QQQ-style :30 labels: 14:00 UTC lives in the 13:30 bar."""
    idx = pd.date_range("2023-07-24 13:30", periods=7, freq="60min", tz="UTC")
    ts = idx.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    event = datetime(2023, 7, 24, 14, 0, tzinfo=UTC)
    i, j = _slice(ts, event, 60, bar_minutes=60)
    assert ts[i] == np.datetime64("2023-07-24T13:30:00")
    ev = np.datetime64(event.replace(tzinfo=None), "ns")
    assert ts[i] <= ev < ts[j]


def test_slice_m5_contains_event():
    idx = pd.date_range("2026-07-15 13:00", periods=24, freq="5min", tz="UTC")
    ts = idx.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    event = cash_open_utc(date(2026, 7, 15))
    i, j = _slice(ts, event, 30, bar_minutes=5)
    ev = np.datetime64(event.replace(tzinfo=None), "ns")
    assert ts[i] <= ev < ts[j]
    assert ts[i] == np.datetime64("2026-07-15T13:30:00")


def _m5_day(day: date, *, after: str, px: float) -> pd.DataFrame:
    """M5 bars 13:00–15:00 UTC. `after` is 'up' or 'down' from the 13:30 bar."""
    start = datetime(day.year, day.month, day.day, 13, 0, tzinfo=UTC)
    rows = []
    price = px
    for k in range(24):
        t = start + timedelta(minutes=5 * k)
        if t.hour == 13 and t.minute == 30:
            o = h_ = l_ = c = px
            h_, l_ = px + 0.00020, px - 0.00020
            c = px
        elif t > datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC):
            if after == "up":
                o, h_, l_, c = price, price + 0.00400, price - 0.00005, price + 0.00350
                price = c
            else:
                o, h_, l_, c = price, price + 0.00005, price - 0.00400, price - 0.00350
                price = c
        else:
            o = h_ = l_ = c = px
            h_, l_ = px + 0.00010, px - 0.00010
        rows.append(
            {
                "time_utc": t,
                "open": o,
                "high": h_,
                "low": l_,
                "close": c,
                "spread": 0.0,
            }
        )
    return pd.DataFrame(rows)


def test_always_long_stop_index_not_past_signal():
    # 13:35–13:40 crash hard. Old OR stop would use that low; new stop is 13:30 low.
    start = datetime(2026, 7, 15, 13, 0, tzinfo=UTC)
    rows = []
    for k in range(36):
        t = start + timedelta(minutes=5 * k)
        if t == datetime(2026, 7, 15, 13, 30, tzinfo=UTC):
            o, h_, l_, c = 1.10000, 1.10020, 1.09980, 1.10000
        elif t == datetime(2026, 7, 15, 13, 35, tzinfo=UTC):
            o, h_, l_, c = 1.10000, 1.10010, 1.09990, 1.10000
        elif t >= datetime(2026, 7, 15, 13, 40, tzinfo=UTC):
            o, h_, l_, c = 1.10000, 1.10000, 1.09000, 1.09000
        else:
            o, h_, l_, c = 1.10000, 1.10010, 1.09990, 1.10000
        rows.append({"time_utc": t, "open": o, "high": h_, "low": l_, "close": c, "spread": 0.0})
    # pad so run_scalp accepts the series
    t_last = rows[-1]["time_utc"]
    for k in range(20):
        t = t_last + timedelta(minutes=5 * (k + 1))
        rows.append(
            {
                "time_utc": t,
                "open": 1.09000,
                "high": 1.09010,
                "low": 1.08990,
                "close": 1.09000,
                "spread": 0.0,
            }
        )
    df = pd.DataFrame(rows)
    res = run_scalp(
        df,
        "EURUSD",
        event="cash",
        or_minutes=15,
        mode="always_long",
        time_stop_min=60,
        tp_r=1.0,
        holdout_start=date(2026, 8, 1),
    )
    assert "error" not in res
    assert res["all"]["n"] >= 1
    # Signal-bar stop (~2 pips) vs OR-including-crash (~100 pips).
    # A 1R loss at the signal stop is ~0.0002; crash-OR stop is ~0.01.
    pnl = res["all"]["sum"]
    assert abs(pnl) < 0.002


def test_holdout_only_profitable_does_not_survive_verdict():
    all_m = {"n": 100, "pf": 2.12}
    dv_m = {"n": 60, "pf": 1.00}
    ho_m = {"n": 40, "pf": 19.0}
    assert all_m["pf"] >= PF_SURVIVE
    assert ho_m["pf"] >= PF_HOLDOUT
    assert dv_m["pf"] < PF_SURVIVE
    assert _verdict(all_m, ho_m, dv_m) != "survives_honest_gates"


def test_develop_and_holdout_can_survive():
    assert (
        _verdict(
            {"n": 100, "pf": 1.40},
            {"n": 40, "pf": 1.20},
            {"n": 60, "pf": 1.35},
        )
        == "survives_honest_gates"
    )


def test_zero_loss_pf_not_fabricated():
    m = _trade_metrics([1.0, 2.0, 0.5])
    assert m["n"] == 3
    assert m["pf"] is None
    assert m["pf"] != 3.0
    assert _verdict({"n": 100, "pf": None}, {"n": 40, "pf": None}, {"n": 60, "pf": None}) != (
        "survives_honest_gates"
    )
    assert _verdict({"n": 100, "pf": None}, {"n": 40, "pf": None}, {"n": 60, "pf": None}) == (
        "undefined_pf"
    )


def test_holdout_only_synthetic_run_scalp():
    """First 20 days dump (develop), last 80 days rally (holdout)."""
    days = []
    d = date(2024, 7, 1)
    while len(days) < 100:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    frames = []
    for i, day in enumerate(days):
        frames.append(_m5_day(day, after="down" if i < 20 else "up", px=1.10000))
    df = pd.concat(frames, ignore_index=True)
    ho = days[20]
    res = run_scalp(
        df,
        "EURUSD",
        event="cash",
        or_minutes=15,
        mode="always_long",
        time_stop_min=60,
        tp_r=1.0,
        holdout_start=ho,
    )
    assert res["all"]["n"] >= 80
    assert (res["develop"]["pf"] or 0) < PF_SURVIVE
    # all-sample PF is holdout-inflated
    assert (res["all"]["pf"] or 0) >= PF_SURVIVE or (res["holdout"]["pf"] or 0) >= PF_HOLDOUT
    assert res["verdict"] != "survives_honest_gates"


def test_tz_vantage_removed():
    import clocks

    assert not hasattr(clocks, "TZ_VANTAGE")


def test_server_wall_series_matches_scalar():
    walls = pd.Series(
        [
            datetime(2025, 3, 5, 15, 0),
            datetime(2025, 3, 12, 16, 0),
            datetime(2025, 7, 15, 16, 0),
        ]
    )
    got = server_wall_to_utc(walls)
    expect = [server_naive_to_utc(w) for w in walls]
    for a, b in zip(got, expect, strict=True):
        assert pd.Timestamp(a).to_pydatetime() == b


def _ohlc_from_close(ts, close) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "time_utc": pd.to_datetime(ts, utc=True),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
        }
    )


def test_clock_lag_argmax_lag0_pass():
    """Identical hourly books: argmax must be lag 0 (the pass the guard requires)."""
    idx = pd.date_range("2024-01-01", periods=240, freq="h", tz="UTC")
    close = 100 + np.sin(np.arange(240) / 3.0) + 0.02 * np.arange(240)
    ref = _ohlc_from_close(idx, close)
    scan = clock_lag_scan(ref, ref)
    assert scan["best_lag_hours"] == 0
    assert scan["correlation"] >= 0.99
    assert clock_pair_passes(scan, min_corr=0.95)


def test_clock_lag_argmax_c1_lag_minus_3():
    """C1: XAU stamped +00:00 peaked at lag −3 vs Dukas. Guard must refuse."""
    raw = pd.read_csv(FIX / "xau_vantage_h1_false_utc.csv")
    raw["time_utc"] = pd.to_datetime(raw["time"], utc=True)
    dukas = pd.read_csv(FIX / "xau_dukas_h1_utc.csv")
    dukas["time_utc"] = pd.to_datetime(dukas["time_utc"], utc=True)
    scan = clock_lag_scan(raw, dukas)
    assert scan["best_lag_hours"] == -3
    assert clock_pair_passes(scan, min_corr=0.95) is False


def test_clock_lag_argmax_et7_xau_passes_fixture():
    xau = load_xau_mixed(FIX / "xau_vantage_h1_false_utc.csv", "H1")
    dukas = pd.read_csv(FIX / "xau_dukas_h1_utc.csv")
    dukas["time_utc"] = pd.to_datetime(dukas["time_utc"], utc=True)
    scan = clock_lag_scan(xau, dukas)
    assert scan["best_lag_hours"] == 0
    assert clock_pair_passes(scan, min_corr=0.95)


def test_clock_guard_refuses_shifted_candidate():
    """A +3h timestamp error (candidate labels late) must not be admitted."""
    idx = pd.date_range("2024-01-01", periods=240, freq="h", tz="UTC")
    close = 100 + np.sin(np.arange(240) / 3.0) + 0.02 * np.arange(240)
    ref = _ohlc_from_close(idx, close)
    cand = _ohlc_from_close(idx + pd.Timedelta(hours=3), close)
    reports = validate_loaded_clocks({"EURUSD_DUKAS_H1": ref, "EURUSD_M5": cand})
    by = {r["series"]: r for r in reports}
    assert by["EURUSD_M5"]["pass"] is False
    assert by["EURUSD_M5"]["best_lag_hours"] == -3
    kept, _ = admit_validated({"EURUSD_DUKAS_H1": ref, "EURUSD_M5": cand}, [], reports)
    assert "EURUSD_M5" not in kept
    assert "EURUSD_DUKAS_H1" in kept


def test_ambiguous_server_hour_is_nat_not_infer():
    """US fall-back 01:30 ET = server 08:30. infer raises; policy is NaT + drop."""
    wall = pd.Series([pd.Timestamp("2025-11-02 08:30:00")])
    naive = wall - pd.Timedelta(hours=7)
    with pytest.raises(ValueError, match="Cannot infer dst time"):
        naive.dt.tz_localize("America/New_York", ambiguous="infer")
    got = server_wall_to_utc(wall)
    assert pd.isna(got.iloc[0])
    assert got.attrs.get("ambiguous_dropped") == 1


def test_ambiguous_policy_leaves_unambiguous_bars():
    wall = pd.Series([pd.Timestamp("2025-07-15 16:30:00")])
    got = server_wall_to_utc(wall)
    assert got.attrs.get("ambiguous_dropped") == 0
    assert pd.Timestamp(got.iloc[0]).to_pydatetime() == datetime(2025, 7, 15, 13, 30, tzinfo=UTC)
