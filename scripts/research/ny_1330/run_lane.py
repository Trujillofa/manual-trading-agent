#!/usr/bin/env python3
"""NY 13:30 UTC / US cash-open research lane.

Paper / research only. Reads local workspace files. Writes JSON under
docs/research/ny_1330/. Does not place orders or retune V2 / avoids.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from clocks import (  # noqa: E402
    cash_open_utc,
    et_dst_label,
    fx_ny_open_utc,
    london_open_utc,
    naive_1330_utc,
    to_et,
)
from loaders import catalog_and_load  # noqa: E402

OUT_DIR = HERE.parents[2] / "docs" / "research" / "ny_1330"
WORKTREE = HERE.parents[2]

# Desk-research gates (not the cTrader 62/1.8/4%/200 live gates, not the
# archived US-index 1%/20% goal). Honest "is this even a coin-flip?".
MIN_TRADES = 80
MIN_HOLDOUT = 30
PF_SURVIVE = 1.30
PF_HOLDOUT = 1.10
PF_COIN_LO = 0.85
PF_COIN_HI = 1.15

COSTS = {
    "US100": {"point": 0.01, "slip_points": 10.0, "fallback_spread_points": 140.0},
    "US30": {"point": 0.01, "slip_points": 10.0, "fallback_spread_points": 120.0},
    "EURUSD": {"point": 0.00001, "slip_points": 10.0, "fallback_spread_points": 10.0},
    "XAUUSD": {"point": 0.01, "slip_points": 20.0, "fallback_spread_points": 20.0},
    "ETHUSDT": {"point": 0.01, "slip_points": 80.0, "fallback_spread_points": 20.0},
    "BTCUSD": {"point": 0.01, "slip_points": 800.0, "fallback_spread_points": 1050.0},
    "BTCUSDT": {"point": 0.01, "slip_points": 800.0, "fallback_spread_points": 200.0},
}


def _json_ready(obj):
    if isinstance(obj, dict):
        return {str(k): _json_ready(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_ready(v) for v in obj]
    if isinstance(obj, (np.floating,)):
        x = float(obj)
        return None if math.isnan(x) or math.isinf(x) else x
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, (datetime, date, pd.Timestamp)):
        return str(obj)
    return obj


def _arrays(df: pd.DataFrame):
    ts = pd.to_datetime(df["time_utc"], utc=True)
    return {
        "ts": ts.to_numpy(dtype="datetime64[ns]"),
        "open": df["open"].to_numpy(dtype=float),
        "high": df["high"].to_numpy(dtype=float),
        "low": df["low"].to_numpy(dtype=float),
        "close": df["close"].to_numpy(dtype=float),
        "spread": df["spread"].to_numpy(dtype=float)
        if "spread" in df.columns
        else np.zeros(len(df)),
        "et_date": np.array([to_et(t.to_pydatetime()).date() for t in ts], dtype=object),
        "utc_date": np.array([t.date() for t in ts], dtype=object),
        "utc_hour": ts.dt.hour.to_numpy(dtype=int),
    }


def _infer_bar_minutes(ts) -> int:
    if len(ts) < 2:
        return 5
    delta = np.diff(ts.astype("datetime64[m]").astype(np.int64))
    pos = delta[delta > 0]
    if pos.size == 0:
        return 5
    return max(1, int(np.median(pos)))


def _as_naive_utc_ns(ts: datetime) -> np.datetime64:
    return np.datetime64(ts.astimezone(UTC).replace(tzinfo=None), "ns")


def _slice(ts, start: datetime, minutes: int, *, bar_minutes: int) -> tuple[int, int]:
    """[i, j) starting at the left-labeled bar that contains `start`.

    H1 14:30 cash open samples the 14:00 bar, not 15:00. A :30 RTH grid
    at 14:00 samples 13:30 (covers 13:30–14:30), not 14:30.
    minutes<=0 is an index lookup (first bar at/after start).
    """
    event = start.astimezone(UTC)
    ev = _as_naive_utc_ns(event)
    if minutes <= 0:
        i = int(np.searchsorted(ts, ev, side="left"))
        return i, i
    contain = int(np.searchsorted(ts, ev, side="right")) - 1
    if contain < 0 or contain >= len(ts):
        return 0, 0
    bar_end = ts[contain] + np.timedelta64(int(bar_minutes), "m")
    if not (ts[contain] <= ev < bar_end):
        return 0, 0
    win_end = ts[contain] + np.timedelta64(int(minutes), "m")
    j = int(np.searchsorted(ts, win_end, side="left"))
    if j <= contain:
        j = contain + 1
    if not (contain < j):
        raise AssertionError(f"window from {ts[contain]} misses event {event.isoformat()}")
    return contain, j


def _range_pct(high, low, open_, close, i, j) -> float | None:
    if j <= i:
        return None
    hi = float(np.max(high[i:j]))
    lo = float(np.min(low[i:j]))
    mid = 0.5 * (float(open_[i]) + float(close[j - 1]))
    if mid <= 0:
        return None
    return (hi - lo) / mid


def _abs_ret(close, i, j) -> float | None:
    if j <= i:
        return None
    a = float(close[i])
    b = float(close[j - 1])
    if a <= 0:
        return None
    return abs(b / a - 1.0)


def event_study(df: pd.DataFrame, *, close_only: bool, bar_minutes: int) -> dict:
    a = _arrays(df)
    ts, high, low, open_, close = a["ts"], a["high"], a["low"], a["open"], a["close"]
    et_days = sorted({d for d in a["et_date"] if d.weekday() < 5})
    utc_days = sorted({d for d in a["utc_date"] if d.weekday() < 5})

    def collect(starts, minutes, metric="range"):
        vals = []
        by_dst = {"EDT": [], "EST": []}
        for day, start in starts:
            i, j = _slice(ts, start, minutes, bar_minutes=bar_minutes)
            if metric == "range":
                v = (
                    _abs_ret(close, i, j)
                    if close_only
                    else _range_pct(high, low, open_, close, i, j)
                )
            else:
                v = _abs_ret(close, i, j)
            if v is None:
                continue
            vals.append(v)
            lab = et_dst_label(day) if isinstance(day, date) else "EDT"
            if lab in by_dst:
                by_dst[lab].append(v)
        return vals, by_dst

    def starts_cash():
        out = []
        for d in et_days:
            out.append((d, cash_open_utc(d)))
        return out

    def starts_naive():
        return [(d, naive_1330_utc(d)) for d in utc_days]

    def starts_fx():
        return [(d, fx_ny_open_utc(d)) for d in utc_days]

    def starts_london():
        return [(d, london_open_utc(d)) for d in utc_days]

    def starts_utc_hour(hour: int):
        return [(d, datetime(d.year, d.month, d.day, hour, 0, tzinfo=UTC)) for d in utc_days]

    minutes = 30 if bar_minutes <= 30 else max(60, bar_minutes)
    # H1 series: 60-minute windows only
    if bar_minutes >= 60:
        minutes = 60

    windows = {
        "cash_0930_et": starts_cash(),
        "naive_1330_utc": starts_naive(),
        "fx_ny_1200_utc": starts_fx(),
        "london_0800_local": starts_london(),
    }
    summary = {}
    for name, st in windows.items():
        vals, by_dst = collect(st, minutes)
        summary[name] = _summarize_vals(vals, by_dst)

    hour_rank = []
    for h in range(24):
        vals, _ = collect(starts_utc_hour(h), minutes)
        hour_rank.append({"utc_hour": h, **_summarize_vals(vals, {})})
    hour_rank_sorted = sorted(
        [r for r in hour_rank if r.get("n", 0) >= 20],
        key=lambda r: (r.get("median") is None, -(r.get("median") or 0)),
    )

    # Daily: is cash window among that day's top-3 30/60m UTC hours?
    top_hits = 0
    compared = 0
    cash_vs_med = []
    for d in et_days:
        start = cash_open_utc(d)
        i, j = _slice(ts, start, minutes, bar_minutes=bar_minutes)
        cash_v = _abs_ret(close, i, j) if close_only else _range_pct(high, low, open_, close, i, j)
        if cash_v is None:
            continue
        others = []
        for h in range(24):
            s = datetime(d.year, d.month, d.day, h, 0, tzinfo=UTC)
            # use ET day's UTC date for hour grid? mix: use the UTC date of cash open
            usd = start.astimezone(UTC).date()
            s = datetime(usd.year, usd.month, usd.day, h, 0, tzinfo=UTC)
            ii, jj = _slice(ts, s, minutes, bar_minutes=bar_minutes)
            ov = (
                _abs_ret(close, ii, jj)
                if close_only
                else _range_pct(high, low, open_, close, ii, jj)
            )
            if ov is not None:
                others.append(ov)
        if len(others) < 8:
            continue
        compared += 1
        ranked = sorted(others, reverse=True)
        if cash_v >= ranked[min(2, len(ranked) - 1)]:
            top_hits += 1
        med = float(np.median(others))
        if med > 0:
            cash_vs_med.append(cash_v / med)

    cash_hour = {}
    # Which UTC hour contains 09:30 ET, vs the 13:00 UTC hour year-round
    for label, hour_picker in (
        ("cash_containing_hour", lambda d: cash_open_utc(d).hour),
        ("naive_13utc_hour", lambda d: 13),
        ("fx_12utc_hour", lambda d: 12),
    ):
        vals = []
        for d in et_days:
            h = hour_picker(d)
            usd = cash_open_utc(d).astimezone(UTC).date()
            s = datetime(usd.year, usd.month, usd.day, h, 0, tzinfo=UTC)
            i, j = _slice(ts, s, 60 if bar_minutes >= 60 else minutes, bar_minutes=bar_minutes)
            v = _abs_ret(close, i, j) if close_only else _range_pct(high, low, open_, close, i, j)
            if v is not None:
                vals.append(v)
        cash_hour[label] = _summarize_vals(vals, {})

    return {
        "bar_minutes": bar_minutes,
        "window_minutes": minutes,
        "close_only": close_only,
        "n_et_weekdays": len(et_days),
        "windows": summary,
        "hour_rank_top8": hour_rank_sorted[:8],
        "hour_rank_bottom4": hour_rank_sorted[-4:] if hour_rank_sorted else [],
        "cash_in_daily_top3_share": (top_hits / compared) if compared else None,
        "cash_in_daily_top3_n": compared,
        "cash_vs_same_day_median_ratio": _summarize_vals(cash_vs_med, {}),
        "hour_buckets": cash_hour,
        "structural_call": _structural_call(summary, cash_vs_med, top_hits, compared),
    }


def _summarize_vals(vals, by_dst) -> dict:
    if not vals:
        return {"n": 0, "median": None, "mean": None, "p75": None, "p90": None}
    arr = np.asarray(vals, dtype=float)
    out = {
        "n": int(arr.size),
        "median": float(np.median(arr)),
        "mean": float(np.mean(arr)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
    }
    for lab, xs in by_dst.items():
        if xs:
            out[f"median_{lab}"] = float(np.median(xs))
            out[f"n_{lab}"] = int(len(xs))
    return out


def _structural_call(summary, ratios, top_hits, compared) -> str:
    cash = summary.get("cash_0930_et", {})
    naive = summary.get("naive_1330_utc", {})
    fx = summary.get("fx_ny_1200_utc", {})
    if not cash.get("n"):
        return "no_sample"
    cm = cash.get("median") or 0
    nm = naive.get("median") or 0
    fm = fx.get("median") or 0
    ratio = float(np.median(ratios)) if ratios else None
    top_share = (top_hits / compared) if compared else 0
    # Structural = cash window reliably larger than typical same-day windows
    # and not just "13:30 UTC always" (naive should not dominate in EST).
    if ratio is not None and ratio >= 1.35 and top_share >= 0.40:
        if (
            cash.get("median_EST")
            and naive.get("median_EST")
            and cash["median_EST"] > 1.15 * naive["median_EST"]
        ):
            return "structural_cash_open_dst_aware"
        return "elevated_vs_typical_day"
    if nm > 1.15 * cm and nm > 1.15 * fm:
        return "naive_1330_not_cash_open"
    if cm <= 1.10 * (fm or cm) and (ratio is None or ratio < 1.15):
        return "not_special"
    return "weak_or_mixed"


def _cost_price(symbol: str, spread_points: float) -> float:
    cfg = COSTS.get(symbol, {"point": 0.00001, "slip_points": 10.0, "fallback_spread_points": 10.0})
    point = float(cfg["point"])
    slip = float(cfg["slip_points"]) * point
    sp = (
        spread_points
        if spread_points and spread_points > 0
        else float(cfg["fallback_spread_points"])
    )
    spread = sp * point
    # one-way friction: half-spread + slip
    return 0.5 * spread + slip


def _trade_metrics(pnls: list[float]) -> dict:
    if not pnls:
        return {
            "n": 0,
            "wins": 0,
            "win_rate": None,
            "pf": None,
            "sum": 0.0,
            "expectancy": None,
            "max_dd": 0.0,
        }
    arr = np.asarray(pnls, dtype=float)
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    gp = float(wins.sum()) if wins.size else 0.0
    gl = float(-losses.sum()) if losses.size else 0.0
    if gl > 0:
        pf: float | None = gp / gl
    elif gp > 0:
        pf = None  # undefined; do not fabricate 3.0 > PF_SURVIVE
    else:
        pf = 0.0
    eq = np.cumsum(arr)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    return {
        "n": int(arr.size),
        "wins": int((arr > 0).sum()),
        "win_rate": float((arr > 0).mean()),
        "pf": None if pf is None else float(pf),
        "sum": float(arr.sum()),
        "expectancy": float(arr.mean()),
        "max_dd": float(dd.min()) if dd.size else 0.0,
    }


def _verdict(all_m: dict, ho_m: dict, dv_m: dict | None = None) -> str:
    n = all_m.get("n") or 0
    nh = ho_m.get("n") or 0
    pf = all_m.get("pf")
    pfh = ho_m.get("pf")
    pfd = (dv_m or {}).get("pf")
    if n == 0:
        return "too_few"
    if pf is None:
        # zero-loss sample: PF is undefined, not a fabricated 3.0
        return "undefined_pf"
    if n < MIN_TRADES:
        if PF_COIN_LO <= pf <= PF_COIN_HI:
            return "coin_flip_undersized"
        return "too_few"
    if (
        pfd is not None
        and pfd >= PF_SURVIVE
        and pfh is not None
        and pfh >= PF_HOLDOUT
        and nh >= MIN_HOLDOUT
    ):
        return "survives_honest_gates"
    if PF_COIN_LO <= pf <= PF_COIN_HI:
        return "coin_flip"
    return "fails"


def run_scalp(
    df: pd.DataFrame,
    symbol: str,
    *,
    event: str,
    or_minutes: int,
    mode: str,
    time_stop_min: int,
    tp_r: float,
    holdout_start: date | None,
) -> dict:
    """Causal ORB / fade / dummy around an event.

    event: cash | naive1330 | fx12
    mode: break | fade | always_long | always_short
    Fill = next bar open after signal close. SL/TP: SL wins if both touch.
    """
    a = _arrays(df)
    ts, o, h, lo, c, sp = a["ts"], a["open"], a["high"], a["low"], a["close"], a["spread"]
    n = len(ts)
    if n < 50:
        return {"error": "short_series", "n_bars": n}

    if event == "cash":
        days = sorted({d for d in a["et_date"] if d.weekday() < 5})
        start_fn = cash_open_utc
    elif event == "naive1330":
        days = sorted({d for d in a["utc_date"] if d.weekday() < 5})
        start_fn = naive_1330_utc
    else:
        days = sorted({d for d in a["utc_date"] if d.weekday() < 5})
        start_fn = fx_ny_open_utc

    bar_minutes = _infer_bar_minutes(ts)
    trades = []
    for d in days:
        t0 = start_fn(d)
        i0, i1 = _slice(ts, t0, or_minutes, bar_minutes=bar_minutes)
        if i1 - i0 < 1:
            continue
        or_hi = float(np.max(h[i0:i1]))
        or_lo = float(np.min(lo[i0:i1]))
        if or_hi <= or_lo:
            continue
        # first bar at/after event+or_minutes
        scan_from = i1
        i_end, _ = _slice(ts, t0 + timedelta(minutes=time_stop_min), 0, bar_minutes=bar_minutes)

        side = 0
        sig = scan_from
        if mode == "always_long":
            side, sig = 1, i0
            if i_end <= sig + 1:
                i_end = min(n, sig + 2)
        elif mode == "always_short":
            side, sig = -1, i0
            if i_end <= sig + 1:
                i_end = min(n, sig + 2)
        elif mode == "break":
            if i_end <= scan_from:
                continue
            for k in range(scan_from, min(i_end, n)):
                if c[k] > or_hi:
                    side, sig = 1, k
                    break
                if c[k] < or_lo:
                    side, sig = -1, k
                    break
        elif mode == "fade_or":
            # Fade the opening-range print. SL is the far side of the OR (valid).
            if i_end <= scan_from:
                continue
            or_open = float(o[i0])
            or_close = float(c[i1 - 1])
            if or_close == or_open:
                continue
            side, sig = (-1 if or_close > or_open else 1), i1 - 1
        elif mode == "fade_break":
            # Fade first close beyond OR. SL = signal-bar extreme, never the near OR edge.
            if i_end <= scan_from:
                continue
            for k in range(scan_from, min(i_end, n)):
                if c[k] > or_hi:
                    side, sig = -1, k
                    break
                if c[k] < or_lo:
                    side, sig = 1, k
                    break
        else:
            continue
        if side == 0 or sig + 1 >= n:
            continue
        fill_i = sig + 1
        friction = _cost_price(symbol, float(sp[fill_i]))
        fill = float(o[fill_i]) + side * friction
        if mode in {"always_long", "always_short", "fade_break"}:
            sl = float(lo[sig]) if side == 1 else float(h[sig])
            sl_idx = sig
        elif mode == "fade_or":
            sl = or_hi if side == -1 else or_lo
            sl_idx = i1 - 1
        else:
            sl = or_lo if side == 1 else or_hi
            sl_idx = i1 - 1
        if sl_idx > sig:
            raise AssertionError(f"stop index {sl_idx} exceeds signal {sig}")
        # Stop must sit on the losing side of the fill. Skip unfillable gaps.
        if side == 1 and sl >= fill:
            continue
        if side == -1 and sl <= fill:
            continue
        risk = abs(fill - sl)
        if risk <= 0:
            continue
        tp = fill + side * tp_r * risk
        exit_px = None
        reason = "time"
        for k in range(fill_i, min(i_end, n)):
            # conservative: SL if low/high reaches it
            if side == 1:
                hit_sl = float(lo[k]) <= sl
                hit_tp = float(h[k]) >= tp
            else:
                hit_sl = float(h[k]) >= sl
                hit_tp = float(lo[k]) <= tp
            if hit_sl and hit_tp:
                exit_px, reason = sl, "sl_both"
                break
            if hit_sl:
                exit_px, reason = sl, "sl"
                break
            if hit_tp:
                exit_px, reason = tp, "tp"
                break
        if exit_px is None:
            # flatten at last bar close in window (known) minus friction
            k = min(i_end - 1, n - 1)
            if k < fill_i:
                continue
            exit_px = float(c[k])
            reason = "time"
        exit_px = exit_px - side * friction
        pnl = side * (exit_px - fill)
        et_d = d if event == "cash" else to_et(t0).date()
        trades.append(
            {
                "day": et_d.isoformat(),
                "side": side,
                "pnl": float(pnl),
                "r": float(pnl / risk),
                "reason": reason,
            }
        )

    if holdout_start is None and days:
        holdout_start = days[int(len(days) * 0.70)]
    all_p = [t["pnl"] for t in trades]
    all_r = [t["r"] for t in trades]
    ho = (
        [t for t in trades if date.fromisoformat(t["day"]) >= holdout_start]
        if holdout_start
        else []
    )
    dv = (
        [t for t in trades if date.fromisoformat(t["day"]) < holdout_start]
        if holdout_start
        else trades
    )
    all_m = _trade_metrics(all_p)
    all_m_r = _trade_metrics(all_r)
    ho_m = _trade_metrics([t["pnl"] for t in ho])
    dv_m = _trade_metrics([t["pnl"] for t in dv])
    return {
        "symbol": symbol,
        "event": event,
        "or_minutes": or_minutes,
        "mode": mode,
        "time_stop_min": time_stop_min,
        "tp_r": tp_r,
        "holdout_start": str(holdout_start),
        "n_session_days": len(days),
        "costs": COSTS.get(symbol),
        "all": all_m,
        "all_R": all_m_r,
        "develop": dv_m,
        "holdout": ho_m,
        "exits": dict(pd.Series([t["reason"] for t in trades]).value_counts().to_dict())
        if trades
        else {},
        "verdict": _verdict(all_m, ho_m, dv_m),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    loaded, inventory, clock_reports = catalog_and_load()
    refused = [r for r in clock_reports if not r.get("pass")]
    if refused:
        print("CLOCK GUARD refused:", ", ".join(r["series"] for r in refused), flush=True)
    else:
        print(f"CLOCK GUARD admitted {len(loaded)} series", flush=True)
    inv_rows = []
    for m in inventory:
        d = asdict(m)
        d.pop("extra", None)
        inv_rows.append(d)

    studies = {}
    bar_min = {
        "US100_M5": 5,
        "US30_M5": 5,
        "EURUSD_M5": 5,
        "XAUUSD_M15": 15,
        "ETHUSDT_15M": 15,
        "XAUUSD_H1": 60,
        "BTCUSD_H1": 60,
        "BTCUSDT_1H_MACRO": 60,
        "BTCUSDT_1H_2026": 60,
        "US100_H1": 60,
        "XAUUSD_DUKAS_H1": 60,
        "EURUSD_DUKAS_H1": 60,
        "QQQ_1H": 60,
        "DXY_1H": 60,
    }
    close_only = {"QQQ_1H", "DXY_1H"}
    for key, df in loaded.items():
        print(f"event-study {key} n={len(df)}", flush=True)
        studies[key] = event_study(
            df,
            close_only=key in close_only,
            bar_minutes=bar_min.get(key, 60),
        )

    # Scalp matrix — fixed approaches, not a search.
    approaches = [
        ("orb15_break", "cash", 15, "break", 90, 1.0),
        ("fade_or15", "cash", 15, "fade_or", 90, 1.0),
        ("fade_break15", "cash", 15, "fade_break", 90, 1.0),
        ("orb5_break", "cash", 5, "break", 60, 1.0),
        ("always_long_or15", "cash", 15, "always_long", 60, 1.0),
        ("always_short_or15", "cash", 15, "always_short", 60, 1.0),
        ("orb15_break_naive1330", "naive1330", 15, "break", 90, 1.0),
        ("orb15_break_fx12", "fx12", 15, "break", 90, 1.0),
        ("orb30_break", "cash", 30, "break", 120, 1.0),
    ]
    scalp_keys = {
        "US100_M5": ("US100", date(2026, 6, 1)),
        "US30_M5": ("US30", date(2026, 6, 1)),
        "EURUSD_M5": ("EURUSD", date(2025, 3, 1)),
        "XAUUSD_M15": ("XAUUSD", date(2025, 7, 1)),
        "ETHUSDT_15M": ("ETHUSDT", date(2025, 7, 1)),
    }
    matrix = []
    for key, (symbol, ho) in scalp_keys.items():
        df = loaded.get(key)
        if df is None:
            continue
        for name, event, orm, mode, tstop, tpr in approaches:
            if orm < 15 and bar_min.get(key, 5) >= 15 and name.startswith("orb5"):
                continue
            print(f"scalp {key} {name}", flush=True)
            res = run_scalp(
                df,
                symbol,
                event=event,
                or_minutes=orm,
                mode=mode,
                time_stop_min=tstop,
                tp_r=tpr,
                holdout_start=ho,
            )
            res["series"] = key
            res["approach"] = name
            matrix.append(res)

    # H1 dummy: buy/sell the cash-containing hour, exit next hour close.
    h1_dummy = []
    for key, symbol in (
        ("XAUUSD_H1", "XAUUSD"),
        ("BTCUSD_H1", "BTCUSD"),
        ("BTCUSDT_1H_MACRO", "BTCUSDT"),
    ):
        df = loaded.get(key)
        if df is None:
            continue
        print(f"h1-dummy {key}", flush=True)
        for mode in ("always_long", "always_short"):
            res = run_scalp(
                df,
                symbol,
                event="cash",
                or_minutes=60,
                mode=mode,
                time_stop_min=60,
                tp_r=1.0,
                holdout_start=date(2025, 1, 1),
            )
            res["series"] = key
            res["approach"] = f"h1_{mode}_cash_hour"
            h1_dummy.append(res)

    amb_dropped = sum(int((m.extra or {}).get("ambiguous_dropped", 0)) for m in inventory)
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "worktree": str(WORKTREE),
        "branch": "research/ny-1330-cash-open",
        "base": "origin/main e1f9612",
        "paper_only": True,
        "clock_validation": {
            "method": (
                "hourly last close, log returns, candidate shifted −4..+4h; "
                "admit only when argmax is exactly lag 0 and corr >= pair floor"
            ),
            "ambiguous_dst_policy": (
                "server_wall_to_utc localizes America/New_York with ambiguous='NaT' "
                "and drops those bars; do not infer or fold"
            ),
            "ambiguous_bars_dropped": amb_dropped,
            "series": clock_reports,
        },
        "inventory": inv_rows,
        "event_studies": studies,
        "scalp_matrix": matrix,
        "h1_dummy": h1_dummy,
        "gates": {
            "min_trades": MIN_TRADES,
            "min_holdout": MIN_HOLDOUT,
            "pf_survive": PF_SURVIVE,
            "pf_holdout": PF_HOLDOUT,
            "note": "Desk-research gates. Not cTrader live gates. Not US-index 1%/20%.",
        },
        "prior_locked": {
            "us_index_session_v1_v8": "promote=no; US100 M5 cash ORB/VWAP/EMA and later families missed 1%/20%",
            "eurusd_ny_scalp": "promote=no; 192-config + specified-book replay SCREEN_FAIL",
            "fx_directional_ta_2026_06": "ORB/trend-pullback on FX majors gross PF ~1.0–1.07",
        },
        "missing": [
            "OIL/WTI/CL — no local OHLC",
            "No 10y M1/M5 book for NQ/XAU/BTC/Oil. Longest fine grid is EURUSD M5 ~4.9y and XAU M15 ~4.2y.",
            "CreateGoal tool not available in this session.",
            "user-mt5-official MCP discovery failed; files read from workspace paths.",
        ],
    }
    out = OUT_DIR / "results.json"
    out.write_text(json.dumps(_json_ready(payload), indent=2))
    print(f"wrote {out}", flush=True)

    # compact table
    print("\n=== EVENT STUDY (cash 30/60m median range) ===")
    for key, st in studies.items():
        w = st.get("windows", {})
        cash = w.get("cash_0930_et", {})
        naive = w.get("naive_1330_utc", {})
        print(
            f"{key:18s} call={st.get('structural_call'):28s} "
            f"cash_med={cash.get('median')} n={cash.get('n')} "
            f"naive_med={naive.get('median')} top3={st.get('cash_in_daily_top3_share')}"
        )
    print("\n=== SCALP VERDICTS ===")
    for row in matrix + h1_dummy:
        all_m = row.get("all") or {}
        print(
            f"{row.get('series'):12s} {row.get('approach'):24s} "
            f"{row.get('verdict'):22s} n={all_m.get('n')} pf={all_m.get('pf')} "
            f"ho_pf={(row.get('holdout') or {}).get('pf')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
