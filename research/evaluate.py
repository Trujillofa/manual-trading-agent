"""FIXED evaluation metric for autoresearch — DO NOT MODIFY.

This is the trading analog of autoresearch's `evaluate_bpb` on a pinned
validation shard. It is the ground truth. Modifying it to make a config "look
profitable" is cheating yourself — the whole point is an honest, held-out judge.

How it stays honest
-------------------
Each pair's cached 365d Dukascopy series is split chronologically into:
  * IN-SAMPLE  (first `IS_FRAC` of the timeline) — what you optimize on.
  * OUT-OF-SAMPLE (the remaining tail) — the held-out judge.

A config only earns verdict KEEP if it is profitable AND has enough trades on
the OUT-OF-SAMPLE window (which it never got to optimize against), and the
in/out performance is consistent. The numeric `score` is built so a search can
climb toward generalizing profitability without being able to win by overfitting.

Ground-truth engine: scripts/run_donchian_backtest.run_config (Dukascopy M1,
realistic costs: spread + commission + slippage, ATR sizing done correctly,
breakeven/trailing/time-exit modelled).

New engine mode (R1/R2): pass engine="live_mtf_rsi" (or "unified") in config to
evaluate_config / autosearch. This walks bars, calls the *pure* evaluate_entry
(src/scanner/evaluator.py) with injected historical mocks, maintains Rule C
active + alignment_state, and simulates TP/SL hits. Now the search can judge
the actual live entry family, not just Donchian.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_donchian_backtest import (  # noqa: E402
    TradeRecord,
    build_pair_cache,
    fetch_pair,
    run_config,
)
from src.scanner.evaluator import evaluate_entry  # noqa: E402  # the pure live entry (R2)

# --- fixed constants (the contract; do not tune these to flatter a config) ---
PAIRS: tuple[str, ...] = (
    # Expanded per plan (R1) for portfolio-level OOS stats.
    # Caches populated for these (as of 2026-06). Fetch more as needed for breadth.
    # Goal: enough pooled OOS trades so MIN_TRADES gate is meaningful.
    "EUR/USD",
    "GBP/USD",
    "GBP/CHF",
    "GBP/JPY",
    "USD/JPY",
    "NZD/JPY",
    "AUD/CAD",
    "USD/CHF",
)
DAYS = 365
IS_FRAC = 0.65  # first 65% optimized on, last 35% held out
MIN_TRADES = 30  # required on EACH window for a trustworthy verdict
MIN_OOS_PF = 1.20  # out-of-sample profit factor needed to KEEP
PF_CAP = 99.0  # cap PF when a window has no losing trades (low-N artifact)
OVERFIT_LAMBDA = 1.0  # penalty weight on (in-sample minus out-of-sample) edge
LOWN_PENALTY = 0.25  # score penalty per trade short of MIN_TRADES (out-of-sample)


@dataclass
class WindowStats:
    trades: int
    win_rate: float
    pf: float
    mean_pnl_pct: float  # mean of per-pair total_pnl_pct
    pooled_pnl_pct: float  # sum of every trade's pnl_pct (portfolio-ish)
    max_consec_losses: int


@dataclass
class EvalResult:
    score: float
    verdict: str  # "KEEP" or "DISCARD"
    reasons: list[str]
    is_stats: WindowStats
    oos_stats: WindowStats


def _split(df: pd.DataFrame, cutoff: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    return df[df.index <= cutoff], df[df.index > cutoff]


def _aggregate(results: list) -> WindowStats:
    pnls: list[float] = []
    per_pair_pct: list[float] = []
    max_cl = 0
    for r in results:
        pnls.extend(t.pnl_pct for t in r.trades_list)
        per_pair_pct.append(r.total_pnl_pct)
        max_cl = max(max_cl, r.max_consecutive_losses)
    n = len(pnls)
    wins = sum(1 for x in pnls if x > 0)
    gross_win = sum(x for x in pnls if x > 0)
    gross_loss = -sum(x for x in pnls if x < 0)
    if gross_loss <= 0:
        pf = PF_CAP if gross_win > 0 else 0.0
    else:
        pf = min(gross_win / gross_loss, PF_CAP)
    return WindowStats(
        trades=n,
        win_rate=(wins / n) if n else 0.0,
        pf=pf,
        mean_pnl_pct=(sum(per_pair_pct) / len(per_pair_pct)) if per_pair_pct else 0.0,
        pooled_pnl_pct=sum(pnls),
        max_consec_losses=max_cl,
    )


def _load_splits() -> list[tuple[str, dict, dict]]:
    """Load each pair once, return (pair, is_frames, oos_frames) with built caches deferred."""
    out = []
    for pair in PAIRS:
        data = fetch_pair(pair, DAYS)
        if data is None:
            continue
        idx15 = data["15m"].index
        cutoff = idx15[int(len(idx15) * IS_FRAC)]
        is_frames, oos_frames = {}, {}
        for tf in ("1h", "30m", "15m"):
            is_frames[tf], oos_frames[tf] = _split(data[tf], cutoff)
        out.append((pair, is_frames, oos_frames))
    return out


_SPLIT_CACHE: list[tuple[str, dict, dict]] | None = None


def _splits() -> list[tuple[str, dict, dict]]:
    global _SPLIT_CACHE
    if _SPLIT_CACHE is None:
        _SPLIT_CACHE = _load_splits()
    return _SPLIT_CACHE


def evaluate_config(config: dict) -> EvalResult:
    """Run `config` on every pair, in-sample and out-of-sample; judge it."""
    is_results, oos_results = [], []
    for pair, isf, oosf in _splits():
        try:
            eng = str(config.get("engine", "")).lower()
            if eng in ("live", "live_mtf_rsi", "mtf_rsi", "unified"):
                # Drive the *current live entry* (evaluate_entry) via thin bar-walker.
                # settings.yaml (profiles, pair_overrides, rsi thresh, tp/sl mults) controls behavior.
                # overrides= allows research CONFIG/PARAM_SPACE keys (lower_bound etc or explicit rsi_*) to
                # parameterize the live family for search without editing the yaml.
                spr = float(config.get("spread_pips", 0.0))
                live_ov: dict[str, Any] = {}
                for k in (
                    "rsi_oversold",
                    "rsi_overbought",
                    "adx_threshold",
                    "tp_atr_mult",
                    "sl_atr_mult",
                    "buffer_pips",
                    "confirm_bars",
                    "variant",
                    "rsi_ma_gate_enabled",
                    "session_filter_enabled",
                    "lookback",
                    "sma_period",
                ):
                    if k in config:
                        live_ov[k] = config[k]
                if "lower_bound" in config:
                    live_ov.setdefault("rsi_oversold", config["lower_bound"])
                if "upper_bound" in config:
                    live_ov.setdefault("rsi_overbought", config["upper_bound"])
                if "max_adx" in config:
                    live_ov.setdefault("adx_threshold", config["max_adx"])
                is_results.append(
                    backtest_live_entry(pair, isf, spread_pips=spr, overrides=live_ov)
                )
                oos_results.append(
                    backtest_live_entry(pair, oosf, spread_pips=spr, overrides=live_ov)
                )
            else:
                is_cache = build_pair_cache(pair, isf["1h"], isf["30m"], isf["15m"])
                oos_cache = build_pair_cache(pair, oosf["1h"], oosf["30m"], oosf["15m"])
                is_results.append(run_config(pair, is_cache, isf["1h"], **config))
                oos_results.append(run_config(pair, oos_cache, oosf["1h"], **config))
        except Exception as exc:  # a broken config is a DISCARD, not a crash of the loop
            return EvalResult(
                score=float("-inf"),
                verdict="DISCARD",
                reasons=[f"exception: {exc}"],
                is_stats=WindowStats(0, 0, 0, 0, 0, 0),
                oos_stats=WindowStats(0, 0, 0, 0, 0, 0),
            )

    is_s = _aggregate(is_results)
    oos_s = _aggregate(oos_results)

    # score: out-of-sample edge, penalized for overfit gap and thin samples.
    overfit_gap = max(0.0, is_s.mean_pnl_pct - oos_s.mean_pnl_pct)
    lown = max(0, MIN_TRADES - oos_s.trades)
    score = oos_s.mean_pnl_pct - OVERFIT_LAMBDA * overfit_gap - LOWN_PENALTY * lown

    reasons: list[str] = []
    if is_s.trades < MIN_TRADES:
        reasons.append(f"IS trades {is_s.trades} < {MIN_TRADES}")
    if oos_s.trades < MIN_TRADES:
        reasons.append(f"OOS trades {oos_s.trades} < {MIN_TRADES}")
    if oos_s.pf < MIN_OOS_PF:
        reasons.append(f"OOS PF {oos_s.pf:.2f} < {MIN_OOS_PF}")
    if oos_s.mean_pnl_pct <= 0:
        reasons.append(f"OOS PnL {oos_s.mean_pnl_pct:.2f}% <= 0")
    if is_s.mean_pnl_pct <= 0:
        reasons.append(f"IS PnL {is_s.mean_pnl_pct:.2f}% <= 0")

    verdict = "KEEP" if not reasons else "DISCARD"
    return EvalResult(score=score, verdict=verdict, reasons=reasons, is_stats=is_s, oos_stats=oos_s)


# ---------------------------------------------------------------------------
# Live entry backtest driver (R1/R2) — drives the *exact* pure evaluate_entry
# bar-by-bar over Dukascopy resampled frames (no Donchian reclaim).
# Maintains active_signal_state + alignment_state exactly as cli/run_scan does
# for Rule C and confirm window. Injects pure mocks (spread=0, news=False, now=bar ts).
# Returns a ConfigResult (compatible with existing _aggregate / evaluate_config).
# ---------------------------------------------------------------------------


def _to_utc_dt(ts: object) -> datetime:
    py = ts.to_pydatetime() if isinstance(ts, pd.Timestamp) else ts  # assume datetime-like
    if getattr(py, "tzinfo", None) is None:
        return py.replace(tzinfo=UTC)
    return py.astimezone(UTC)


def backtest_live_entry(
    pair: str,
    frames: dict[str, pd.DataFrame],
    spread_pips: float = 1.5,
    warmup: int = 60,
    overrides: dict[str, Any]
    | None = None,  # live family param overrides (see evaluate_entry); enables search over current entry logic
    commission_per_order: float = 3.0,
    slippage_pips: float = 2.0,
    risk_pct: float = 0.01,
) -> "ConfigResult":  # noqa: UP037,F821  (name imported inside fn; postponed eval + ruff local scope)
    """Run the live MTF RSI + V* + all-gates entry logic over the provided frames.

    frames: {"1h": df, "30m": df, "15m": df} with datetime index, ohlc cols.
    Uses the *current* config/settings.yaml for profiles, overrides, thresholds etc.
    Pass overrides= to vary rsi_oversold/overbought, adx_threshold, buffer_pips, confirm_bars,
    tp_atr_mult etc for this run (parameterizes the live entry family for research).
    Set env LIVE_BT_MAX_BARS=N for fast recent-only sampling (truncates input frames).
    """
    import os

    # Use same result classes for drop-in compatibility with harness
    from scripts.run_donchian_backtest import (  # noqa: F401 (reimported for local scope)
        ConfigResult,
        TradeRecord,
    )

    # Fast sampling for research (e.g. export LIVE_BT_MAX_BARS=3000 before calling)
    if os.environ.get("LIVE_BT_MAX_BARS"):
        maxb = int(os.environ["LIVE_BT_MAX_BARS"])
        frames = {k: v.iloc[-maxb:].copy() for k, v in frames.items()}

    d1h = frames["1h"]
    d30 = frames["30m"]
    d15 = frames["15m"]
    if d15 is None or d15.empty:
        return ConfigResult(
            pair=pair,
            config_label="live_mtf_rsi",
            trades_list=[],
            total_pnl_pct=0.0,
            max_consecutive_losses=0,
        )

    # Pre-extract for speed (index must be monotonic)
    idx15 = d15.index
    n = len(idx15)
    highs15 = d15["high"].astype(float).tolist()
    lows15 = d15["low"].astype(float).tolist()

    balance = 100000.0
    peak = balance
    max_dd_pct = 0.0
    trades_list: list[TradeRecord] = []
    trade_pnls: list[float] = []
    consecutive_losses = 0
    max_consecutive_losses = 0

    # active_state now list per pair to support concurrent signals after Rule C re-arms (midline/SMA invalidation)
    # without losing prior TP/SL levels. Mirrors that live allows re-fires same-dir after invalidation (not just TP/SL).
    # This makes signal frequency (and thus completed trades in P&L) closer to live.
    active_state: dict[str, list[dict]] = {}
    alignment_state: dict[str, dict] = {}

    from collections import Counter

    rejection_counter: Counter[str] = Counter()

    # Iterate 15m bars
    for i in range(warmup, n):
        ts = idx15[i]
        bar_h = highs15[i]
        bar_l = lows15[i]
        # bar_c = closes15[i]

        # 1) Exit simulation for any open actives (list support for Rule C re-arms after midline/SMA).
        # Each active has its own entry/tp/sl; we may have multiple (e.g. re-entry same dir after invalidation while prior still open).
        if pair in active_state and active_state[pair]:
            still_active: list[dict] = []
            for rec in list(active_state[pair]):
                direc = rec["direction"]
                tp = float(rec["tp"])
                sl = float(rec["sl"])
                ent = float(rec["entry"])
                hit = False
                exit_price = None
                ex_reason = None
                if direc == "BUY":
                    if bar_h >= tp:
                        hit = True
                        exit_price = tp
                        ex_reason = "tp"
                    elif bar_l <= sl:
                        hit = True
                        exit_price = sl
                        ex_reason = "sl"
                else:
                    if bar_l <= tp:
                        hit = True
                        exit_price = tp
                        ex_reason = "tp"
                    elif bar_h >= sl:
                        hit = True
                        exit_price = sl
                        ex_reason = "sl"
                if hit and exit_price is not None:
                    # Realistic P&L: risk-based sizing + spread/slippage/commission (parity with
                    # the Donchian engine run_config so harness PF/PnL are financially meaningful).
                    pip = 0.01 if "JPY" in pair else 0.0001
                    adverse = (spread_pips + slippage_pips) * pip
                    raw_price = (exit_price - ent) if direc == "BUY" else (ent - exit_price)
                    raw_price -= adverse  # costs work against the trade
                    sl_dist = abs(ent - sl)
                    position_size = (balance * risk_pct / sl_dist) if sl_dist > 0 else 1.0
                    commission_cash = 2 * commission_per_order  # round-trip
                    pnl = position_size * raw_price - commission_cash
                    pnl_pct = (pnl / balance * 100.0) if balance > 0 else 0.0
                    balance += pnl
                    peak = max(peak, balance)
                    dd = (peak - balance) / peak * 100.0 if peak > 0 else 0.0
                    max_dd_pct = max(max_dd_pct, dd)
                    if pnl < 0:
                        consecutive_losses += 1
                        max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
                    else:
                        consecutive_losses = 0
                    trade_pnls.append(pnl_pct)
                    try:
                        tr = TradeRecord(
                            pair=pair,
                            direction=direc,
                            entry_time=rec.get("fired_ts", ts),
                            exit_time=ts,
                            entry_price=ent,
                            exit_price=exit_price,
                            tp_price=tp,
                            sl_price=sl,
                            pnl=pnl,
                            pnl_pct=pnl_pct,
                            exit_reason=ex_reason,
                            bars_held=max(0, i - int(rec.get("entry_i", i))),
                            rsi_15m=0.0,
                            rsi_30m=0.0,
                            rsi_1h=0.0,
                        )
                        trades_list.append(tr)
                    except Exception:
                        trades_list.append(
                            type("T", (), {"pnl_pct": pnl_pct})()  # type: ignore[attr-defined]
                        )
                    # do not keep this rec
                else:
                    still_active.append(rec)
            if still_active:
                active_state[pair] = still_active
            else:
                active_state.pop(pair, None)
                alignment_state.pop(pair, None)

        # 2) Time-respecting prefixes (no future leak).
        # Optimization: bound slice sizes. For 15m, when active use from its fire bar (for Rule C _is
        # to see full since-fire H/L for TP/SL detection); otherwise only recent window (hh/ll, atr,
        # rsi, sma, patterns, div all use <<200 bars). Higher TFs always recent (no deep history needed).
        cur_ts = _to_utc_dt(ts)
        if pair in active_state and active_state[pair]:
            # slice from the earliest entry among current actives (for _is_signal_invalidated history on all)
            min_entry_i = min(int(r.get("entry_i", i)) for r in active_state[pair])
            start_i = max(0, min_entry_i)
            data_15m = d15.iloc[start_i : i + 1].copy()
        else:
            recent = 150  # sufficient for all internal calcs when no active Rule C history required
            start_i = max(0, i - recent)
            data_15m = d15.iloc[start_i : i + 1].copy()

        recent_htf = 120
        if not d30.empty:
            data_30m = d30[d30.index <= ts].iloc[-recent_htf:].copy()
        else:
            data_30m = d15.iloc[max(0, i - 10) : i + 1].copy()
        if not d1h.empty:
            data_1h = d1h[d1h.index <= ts].iloc[-recent_htf:].copy()
        else:
            data_1h = d15.iloc[max(0, i - 10) : i + 1].copy()

        # 3) Call the pure evaluator (single source)
        now_utc = cur_ts
        spread_q = {
            "spread": float(spread_pips) * (0.01 if "JPY" in pair else 0.0001),
            "source": "bt",
        }
        # For evaluator's Rule C check (expects dict[pair -> single record]), pass the *latest* active
        # (matches live behavior of overwriting active[pair] on re-arm after invalidation).
        # Full list is kept in driver only for multi-TP/SL tracking.
        call_active: dict[str, dict] = {}
        if pair in active_state and active_state[pair]:
            call_active[pair] = active_state[pair][-1]
        dec = evaluate_entry(
            pair,
            data_1h,
            data_30m,
            data_15m,
            active_signal_state=call_active,
            alignment_state=alignment_state,
            now_utc=now_utc,
            spread_quote=spread_q,
            news_blocked=False,
            spread_filter_enabled=False,  # in BT we already model via spread_pips or 0
            bars_aligned=None,  # evaluator recomputes from alignment_state (correct for this bar)
            overrides=overrides,  # forwarded for live-family param search (rsi_*, adx, buffer etc)
        )

        # 4) Update alignment state for next bar's confirm age (mirror cli)
        aligned = bool(dec.get("aligned"))
        ndir = dec.get("direction")
        if aligned and ndir:
            prev = alignment_state.get(pair)
            if prev and str(prev.get("direction", "")) == ndir:
                nbars = int(prev.get("bars", 0)) + 1
            else:
                nbars = 0
            alignment_state[pair] = {"direction": ndir, "bars": nbars}
        else:
            alignment_state.pop(pair, None)

        # 5) Collect rejection reasons on non-fires (for "why low volume" insight even when N small)
        if not dec.get("fired"):
            for r in dec.get("no_trade_reasons") or []:
                rejection_counter[r] += 1

        # Progress for long walks (sampled or full 365d) — visible in harness/diag runs
        if (i - warmup) % 500 == 0 and (i - warmup) > 0:
            print(f"  {pair}: walk progress {i - warmup}/{n - warmup} bars...", flush=True)

        # 6) Fire new entry if evaluator says so.
        # With list support + re-arm after midline/SMA, we append even if other actives (same or opp dir) exist.
        # This increases signal count (and completed trades) to better match live frequency after non-TP/SL invalidations.
        if dec.get("fired"):
            direc = dec.get("direction")
            ent = dec.get("entry")
            tp = dec.get("tp")
            sl = dec.get("sl")
            if direc and ent is not None and tp is not None and sl is not None:
                new_rec = {
                    "direction": direc,
                    "fired_at": int(now_utc.timestamp()),
                    "fired_ts": now_utc,
                    "entry": float(ent),
                    "tp": float(tp),
                    "sl": float(sl),
                    "entry_i": i,  # for efficient 15m slicing from fire for Rule C history in later bars
                }
                if pair not in active_state:
                    active_state[pair] = []
                active_state[pair].append(new_rec)

    # Finalize result (compat with donchian ConfigResult expectations)
    total_pnl_pct = ((balance - 100000.0) / 100000.0) * 100.0 if balance > 0 else 0.0

    # Report top rejection reasons for this pair's walk (helps explain low fire counts)
    top_rej = rejection_counter.most_common(8)
    if top_rej:
        print(f"{pair}: top rejection reasons: {top_rej}")
    # Build a minimal but compatible ConfigResult
    try:
        result = ConfigResult(
            pair=pair,
            config_label="live_mtf_rsi_v2_unified",
            upper_bound=0.0,
            lower_bound=0.0,
            use_fixed_pip=False,
            tp_pips=0.0,
            sl_pips=0.0,
            tp_atr_mult=0.0,
            sl_atr_mult=0.0,
            lookback=0,
            confirm_bars=0,
            buffer_pips=0.0,
            use_di_filter=False,
            di_ratio=0.0,
            use_adx_filter=False,
            max_adx=0.0,
            use_session=False,
            use_mom_fade=False,
            mom_fade_bars=0,
            use_trailing=False,
            trail_atr_mult=0.0,
            use_breakeven=False,
            be_trigger_pct=0.0,
            use_time_exit=False,
            max_bars_exit=0,
            spread_pips=spread_pips,
            commission_per_order=0.0,
            slippage_pips=0.0,
        )
        result.trades_list = trades_list
        result.total_pnl_pct = total_pnl_pct
        result.max_consecutive_losses = max_consecutive_losses
        result.max_drawdown_pct = max_dd_pct
    except Exception:
        # ultra fallback
        result = type(
            "R",
            (),
            {
                "trades_list": trades_list,
                "total_pnl_pct": total_pnl_pct,
                "max_consecutive_losses": max_consecutive_losses,
                "max_drawdown_pct": max_dd_pct,
            },
        )()

    return result
