"""Pure entry evaluator.

This is the single source of truth for "would this pair fire a signal right now?"

Both the live scanner (cli.py run_scan) and backtesters / research harness
should call the same function on equivalent MTF data + state so that
"live == backtest" is true by construction (R2 unification).

The function is intentionally side-effect free (no logging, no audit append,
no Telegram, no state mutation). Callers do the I/O and side effects.

It encapsulates the current live production entry logic (MTF RSI alignment +
per-pair confirmation profile V0/V1/V2 + all gates + ATR TP/SL + Rule C).

Return a dict with at minimum:
{
  "fired": bool,
  "direction": "BUY" | "SELL" | None,
  "entry": float | None,
  "tp": float | None,
  "sl": float | None,
  "reasons": list[str],
  "confidence": float,
  "profile": str,
  "atr": float | None,
  ...
}
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal, cast

import pandas as pd

from src.config import get_settings
from src.indicators.atr import calculate_atr
from src.indicators.ema import calculate_ema
from src.indicators.high_low import (
    previous_rolling_highest_high,
    previous_rolling_lowest_low,
)
from src.indicators.rsi import (
    calculate_rsi,
    calculate_rsi_ma_series,
    calculate_rsi_series,
    detect_bearish_divergence,
    detect_bullish_divergence,
    detect_rsi_curl,
)
from src.scanner.gates import (
    ADX_TREND_THRESHOLD,
    SpreadQuote,
    _check_breakout_with_profile,
    _get_confirmation_profile,
    _get_pair_param,
    _is_signal_invalidated,
    _mtf_distance_to_buy,
    _mtf_distance_to_sell,
    _profile_label,
    _session_allowed,
)
from src.scanner.state import ActiveSignalRecord


def evaluate_entry(
    pair: str,
    data_1h: Any,  # pandas DF ...
    data_30m: Any,
    data_15m: Any,
    active_signal_state: dict[str, Any] | None = None,
    alignment_state: dict[str, Any] | None = None,
    now_utc: datetime | None = None,
    spread_quote: SpreadQuote | None = None,
    news_blocked: bool = False,
    spread_filter_enabled: bool | None = None,
    bars_aligned: int
    | None = None,  # precomputed by caller to avoid double-count in state mutation within same scan
    overrides: dict[str, Any]
    | None = None,  # for research harness: vary live-family params (rsi_*, adx_*, buffer/confirm, tp/sl, sessions etc) without mutating yaml
) -> dict[str, Any]:
    """Pure decision: does this pair fire a (non-shadow) signal on current data?

    overrides: optional dict of live tunables (e.g. {"rsi_oversold": 25, "rsi_overbought": 75,
    "adx_threshold": 20, "buffer_pips": 0.5, "confirm_bars": 3, "tp_atr_mult": 1.5, "lower_bound": 28}).
    When present, these take precedence over settings.yaml for this call (enables autosearch over the live entry family).
    """
    settings = get_settings()
    ov: dict[str, Any] = overrides or {}

    def _eff(key: str, default: Any) -> Any:
        return ov.get(key, default)

    def _get_pair_param_eff(pair: str, param: str, default: float | int) -> float | int:
        # Support research overrides: flat key (e.g. "tp_atr_multiplier") or nested "pair_overrides": {pair: {param: val}}
        if param in ov:
            return ov[param]
        po = ov.get("pair_overrides") or {}
        pconf = po.get(pair) if isinstance(po, dict) else None
        if isinstance(pconf, dict):
            val = pconf.get(param)
            if val is not None:
                return val
        return _get_pair_param(pair, param, default)

    active_state = active_signal_state or {}
    align_state = alignment_state or {}

    if data_15m is None or getattr(data_15m, "empty", True):
        return {"fired": False, "direction": None, "reasons": ["no 15m data"]}

    # Extract lists (assume columns exist)
    high_15m = data_15m["high"].values.tolist()
    low_15m = data_15m["low"].values.tolist()
    close_15m = data_15m["close"].values.tolist()
    open_15m = (
        data_15m.get("open", data_15m["close"]).values.tolist() if "open" in data_15m else close_15m
    )
    close_price = close_15m[-1]

    close_1h_list = data_1h["close"].values.tolist()
    close_30m_list = data_30m["close"].values.tolist()

    # Fast path for research driver: if the (time-respecting) df slices carry precomputed columns
    # (attached by backtest_live_entry precomp on full frames), use the last value instead of
    # recomputing indicators on every bar. Live calls pass plain price dfs and fall back here.
    # This makes full-history research iterations (365d) fast while preserving exact live behavior.
    pre_rsi_15m = pre_sma_15m = pre_atr = pre_adx_1h = None
    if len(data_15m) > 0 and hasattr(data_15m, "columns"):
        cols = data_15m.columns
        if "rsi" in cols:
            v = data_15m["rsi"].iloc[-1]
            pre_rsi_15m = float(v) if not pd.isna(v) else None
        if "sma" in cols:
            v = data_15m["sma"].iloc[-1]
            pre_sma_15m = float(v) if not pd.isna(v) else None
        if "atr" in cols:
            v = data_15m["atr"].iloc[-1]
            pre_atr = float(v) if not pd.isna(v) else None
    if len(data_1h) > 0 and hasattr(data_1h, "columns") and "adx" in data_1h.columns:
        v = data_1h["adx"].iloc[-1]
        pre_adx_1h = float(v) if not pd.isna(v) else None

    rsi_period = _eff("rsi_period", settings.strategy.rsi_period)
    rsi_oversold = _eff("rsi_oversold", _eff("lower_bound", settings.strategy.rsi_oversold))
    rsi_overbought = _eff("rsi_overbought", _eff("upper_bound", settings.strategy.rsi_overbought))
    rsi_ma_period = _eff("rsi_ma_period", settings.strategy.rsi_ma_gate_period)
    lookback = _eff("lookback", settings.strategy.lookback_bars)
    sma_period = int(_get_pair_param_eff(pair, "sma_period", settings.strategy.sma_period))

    # Indicators
    rsi_1h = calculate_rsi(close_1h_list[-50:], rsi_period)
    rsi_30m = calculate_rsi(close_30m_list[-50:], rsi_period)
    rsi_15m_val = (
        pre_rsi_15m if pre_rsi_15m is not None else calculate_rsi(close_15m[-50:], rsi_period)
    )

    rsi_series_1h = calculate_rsi_series(close_1h_list, rsi_period)
    rsi_ma_1h = calculate_rsi_ma_series(
        [float(v) if v is not None else None for v in rsi_series_1h], ma_period=rsi_ma_period
    )
    rsi_series_30m = calculate_rsi_series(close_30m_list, rsi_period)
    rsi_ma_30m = calculate_rsi_ma_series(
        [float(v) if v is not None else None for v in rsi_series_30m], ma_period=rsi_ma_period
    )
    rsi_series_15m = (
        data_15m["rsi"].tolist()
        if "rsi" in data_15m.columns
        else calculate_rsi_series(close_15m, rsi_period)
    )
    if "rsi_ma" in data_15m.columns:
        rsi_ma_15m = data_15m["rsi_ma"].tolist()
    else:
        rsi_ma_15m = calculate_rsi_ma_series(
            [float(v) if v is not None else None for v in rsi_series_15m], ma_period=rsi_ma_period
        )

    from src.indicators.sma import calculate_sma

    sma_15m = pre_sma_15m if pre_sma_15m is not None else calculate_sma(close_15m, sma_period)
    # sma_1h/30m needed for the 3-TF SMA alignment entry gate (parity with live cli)
    sma_1h = calculate_sma(close_1h_list, sma_period)
    sma_30m = calculate_sma(close_30m_list, sma_period)

    # HH/LL prior
    hh = previous_rolling_highest_high(high_15m, lookback, len(high_15m) - 1)
    ll = previous_rolling_lowest_low(low_15m, lookback, len(low_15m) - 1)

    # ATR (fixed) - prefer precomputed column from driver (research fast path)
    if pre_atr is not None:
        atr = pre_atr
    else:
        atr = calculate_atr(high_15m[-(14 + 1) :], low_15m[-(14 + 1) :], close_15m[-(14 + 1) :])

    # Profile + breakout (support overrides for research param search on live family)
    profile = _get_confirmation_profile(pair)
    profile = dict(profile)  # mutable copy
    if "variant" in ov:
        profile["variant"] = str(ov["variant"])
    if "buffer_pips" in ov:
        profile["buffer_pips"] = float(ov["buffer_pips"])
    if "confirm_bars" in ov:
        profile["confirm_bars"] = int(ov["confirm_bars"])
    profile_label = _profile_label(profile)
    pip_size = 0.01 if "JPY" in pair else 0.0001
    bar_high = high_15m[-1] if high_15m else None
    bar_low = low_15m[-1] if low_15m else None

    breakout_buy = _check_breakout_with_profile(
        profile, "BUY", close_price, hh, ll, pip_size, bar_high, bar_low
    )
    breakout_sell = _check_breakout_with_profile(
        profile, "SELL", close_price, hh, ll, pip_size, bar_high, bar_low
    )

    # ADX / ranging
    from src.indicators.adx import calculate_adx_full

    adx_threshold = float(_eff("adx_threshold", ADX_TREND_THRESHOLD))
    if pre_adx_1h is not None:
        adx_1h = pre_adx_1h
    else:
        adx_1h_full = calculate_adx_full(
            data_1h["high"].values.tolist()[-50:],
            data_1h["low"].values.tolist()[-50:],
            data_1h["close"].values.tolist()[-50:],
        )
        adx_1h = adx_1h_full[0] if adx_1h_full else None
    is_ranging = adx_1h is not None and adx_1h < adx_threshold

    # Spread: use injected quote (caller in CLI performs OANDA/cTrader/static fetches to keep evaluator pure and backtestable)
    quote = spread_quote
    spread_ok = True
    pair_spread_limits = getattr(settings.strategy, "spread_limits_pips", {}) or {}
    max_spread_for_pair = float(pair_spread_limits.get(pair, settings.strategy.max_spread_pips))
    spread_pips = None
    if quote and isinstance(quote.get("spread"), float):
        spread_value = cast(float, quote.get("spread"))
        spread_pips = float(spread_value) / pip_size
        spread_ok = spread_pips <= max_spread_for_pair
    elif _eff("spread_filter_enabled", getattr(settings.strategy, "spread_filter_enabled", True)):
        spread_ok = False

    # Patterns + div (for confidence)
    from src.indicators.candlestick import PatternType, detect_patterns

    candle_patterns = []
    if len(open_15m) >= 3:
        candle_patterns = detect_patterns(
            open_15m[-20:] if len(open_15m) >= 20 else open_15m,
            high_15m[-20:] if len(high_15m) >= 20 else high_15m,
            low_15m[-20:] if len(low_15m) >= 20 else low_15m,
            close_15m[-20:] if len(close_15m) >= 20 else close_15m,
            lookback=3,
        )
    bullish_pats = [p for p in candle_patterns if p.pattern_type == PatternType.BULLISH]
    bearish_pats = [p for p in candle_patterns if p.pattern_type == PatternType.BEARISH]

    rsi_series = calculate_rsi_series(close_15m, rsi_period)
    bullish_div = detect_bullish_divergence(close_15m[-100:], rsi_series[-100:], lookback=5)
    bearish_div = detect_bearish_divergence(close_15m[-100:], rsi_series[-100:], lookback=5)

    # MTF alignment
    if rsi_1h is None or rsi_30m is None or rsi_15m_val is None:
        return {
            "fired": False,
            "direction": None,
            "reasons": ["rsi unavailable"],
            "atr": atr,
            "profile": profile_label,
        }

    all_oversold = rsi_1h < rsi_oversold and rsi_30m < rsi_oversold and rsi_15m_val < rsi_oversold
    all_overbought = (
        rsi_1h > rsi_overbought and rsi_30m > rsi_overbought and rsi_15m_val > rsi_overbought
    )

    buy_distance = _mtf_distance_to_buy(
        float(rsi_1h), float(rsi_30m), float(rsi_15m_val), float(rsi_oversold)
    )
    sell_distance = _mtf_distance_to_sell(
        float(rsi_1h), float(rsi_30m), float(rsi_15m_val), float(rsi_overbought)
    )
    near_direction = "BUY" if buy_distance <= sell_distance else "SELL"

    aligned = bool(all_oversold or all_overbought)

    # alignment age (for confirm window)
    confirm_bars = profile["confirm_bars"]
    if bars_aligned is None:
        # Legacy path for direct callers / tests that pass alignment_state but not precomputed bars.
        # Recomputes from *previous* scan's state (cross-scan persistence).
        bars_aligned = 0
        if aligned:
            prev = align_state.get(pair)
            if prev and str(prev.get("direction", "")) == near_direction:
                bars_aligned = int(prev.get("bars", 0)) + 1
            else:
                bars_aligned = 0
    # When caller (cli) passes bars_aligned: it has already done the +1 for *this* scan's alignment
    # and written that value to alignment_state for persistence. We use the passed value as-is
    # to avoid double-increment within the same scan.
    within_confirm_window = aligned and bars_aligned <= confirm_bars
    breakout_confirmed = (near_direction == "BUY" and breakout_buy) or (
        near_direction == "SELL" and breakout_sell
    )
    if breakout_confirmed and confirm_bars > 0 and not within_confirm_window and aligned:
        breakout_confirmed = False

    # Signal candidate
    signal_direction: Literal["BUY", "SELL", None] = None
    signal_confidence = 0.0
    signal_reasons: list[str] = []
    no_trade_reasons: list[str] = []

    if all_oversold:
        signal_direction = "BUY"
        signal_confidence = 0.6
        signal_reasons.append(
            f"MTF RSI oversold (1h:{rsi_1h:.0f}, 30m:{rsi_30m:.0f}, 15m:{rsi_15m_val:.0f})"
        )
        if breakout_confirmed:
            signal_confidence += 0.1
            signal_reasons.append("15m breakout low confirmed")
            if confirm_bars > 0:
                signal_reasons.append(f"confirmed at bar {bars_aligned}/{confirm_bars}")
        else:
            no_trade_reasons.append("15m breakout low not confirmed")
            if confirm_bars > 0 and not within_confirm_window:
                no_trade_reasons.append(
                    f"confirmation window expired ({bars_aligned} bars > {confirm_bars})"
                )
        if bullish_div:
            signal_confidence += bullish_div.strength * 0.2
            signal_reasons.append("bullish divergence")
        if bullish_pats:
            signal_confidence += 0.1
            signal_reasons.append(
                f"bullish pattern ({', '.join(p.name for p in bullish_pats[:2])})"
            )
    elif all_overbought:
        signal_direction = "SELL"
        signal_confidence = 0.6
        signal_reasons.append(
            f"MTF RSI overbought (1h:{rsi_1h:.0f}, 30m:{rsi_30m:.0f}, 15m:{rsi_15m_val:.0f})"
        )
        if breakout_confirmed:
            signal_confidence += 0.1
            signal_reasons.append("15m breakout high confirmed")
            if confirm_bars > 0:
                signal_reasons.append(f"confirmed at bar {bars_aligned}/{confirm_bars}")
        else:
            no_trade_reasons.append("15m breakout high not confirmed")
            if confirm_bars > 0 and not within_confirm_window:
                no_trade_reasons.append(
                    f"confirmation window expired ({bars_aligned} bars > {confirm_bars})"
                )
        if bearish_div:
            signal_confidence += bearish_div.strength * 0.2
            signal_reasons.append("bearish divergence")
        if bearish_pats:
            signal_confidence += 0.1
            signal_reasons.append(
                f"bearish pattern ({', '.join(p.name for p in bearish_pats[:2])})"
            )

    # RSI-MA curl modifier
    if signal_direction and rsi_series_1h and rsi_ma_1h:
        rsi_val_now = rsi_series_1h[-1]
        rsi_ma_now = rsi_ma_1h[-1]
        if rsi_val_now is not None and rsi_ma_now is not None:
            rsi_tail = [float(v) if v is not None else None for v in rsi_series_1h[-12:]]
            ma_tail = [float(v) if v is not None else None for v in rsi_ma_1h[-12:]]
            direction = "buy" if signal_direction == "BUY" else "sell"
            if detect_rsi_curl(rsi_tail, ma_tail, direction, lookback=3):
                signal_confidence = min(1.0, signal_confidence * 1.10)
                signal_reasons.append("RSI-MA curl confirmed")
            else:
                signal_confidence *= 0.85

    # RSI-MA hard gate
    rsi_ma_gate_enabled = _eff(
        "rsi_ma_gate_enabled", getattr(settings.strategy, "rsi_ma_gate_enabled", True)
    )
    if signal_direction and rsi_ma_gate_enabled:
        ma_now_1h = rsi_ma_1h[-1] if rsi_ma_1h else None
        ma_now_30m = rsi_ma_30m[-1] if rsi_ma_30m else None
        ma_now_15m = rsi_ma_15m[-1] if rsi_ma_15m else None
        if all(v is not None for v in (ma_now_1h, ma_now_30m, ma_now_15m)):
            ob = float(rsi_overbought)
            os_ = float(rsi_oversold)
            gate_ok = True
            failing = []
            if signal_direction == "BUY":
                gate_ok = ma_now_1h <= os_ and ma_now_30m <= os_ and ma_now_15m <= os_
                if not gate_ok:
                    failing = [
                        f"{tf}={v:.1f}"
                        for tf, v in (("1h", ma_now_1h), ("30m", ma_now_30m), ("15m", ma_now_15m))
                        if v > os_
                    ]
            else:
                gate_ok = ma_now_1h >= ob and ma_now_30m >= ob and ma_now_15m >= ob
                if not gate_ok:
                    failing = [
                        f"{tf}={v:.1f}"
                        for tf, v in (("1h", ma_now_1h), ("30m", ma_now_30m), ("15m", ma_now_15m))
                        if v < ob
                    ]
            if not gate_ok:
                no_trade_reasons.append(
                    f"RSI-MA({rsi_ma_period}) gate: SMA(RSI) not outside 30-70 ({', '.join(failing)})"
                )

    # Final gates (session, news, spread, ranging, Rule C active)
    session_ok = True
    session_filter_enabled = _eff(
        "session_filter_enabled", getattr(settings.strategy, "session_filter_enabled", True)
    )
    if session_filter_enabled:
        # now_utc must be passed by caller (cli passes real scan time; backtest driver passes bar timestamp).
        # No datetime.now() here — keeps evaluator pure / deterministic for bar-by-bar backtesting.
        if now_utc is None:
            session_ok = True  # conservative for unit tests that call without time context
        else:
            allowed = list(_eff("session_allowed_utc", settings.strategy.session_allowed_utc))
            session_ok = _session_allowed(now_utc, allowed)

    # news_blocked injected by caller (who does the fetch once) to keep evaluator pure
    # (no NewsChecker() creation or network here)

    spread_ok_final = spread_ok
    if (
        spread_filter_enabled
        if spread_filter_enabled is not None
        else getattr(settings.strategy, "spread_filter_enabled", True)
    ) and not spread_ok:
        spread_ok_final = False

    active_record = active_state.get(pair)
    # Rule C suppresses only SAME-direction repeats; opposite-direction signals are always allowed.
    if signal_direction and active_record and active_record.get("direction") == signal_direction:
        # simplified invalidation check (full uses 15m data)
        try:
            invalidated, _ = _is_signal_invalidated(
                cast(ActiveSignalRecord, active_record),
                data_15m,  # approx
                rsi_series_15m,
                close_price,
                sma_15m,
            )
            if not invalidated:
                no_trade_reasons.append("active signal not yet invalidated (Rule C)")
        except Exception:
            pass

    if signal_direction:
        if not session_ok:
            no_trade_reasons.append("outside allowed session")
        if news_blocked:
            no_trade_reasons.append("blocked by high-impact news")
        if not spread_ok_final:
            no_trade_reasons.append("spread too wide or unavailable")
        if not is_ranging:
            adx_str = f"{adx_1h:.0f}" if adx_1h is not None else "?"
            no_trade_reasons.append(f"trending market (ADX {adx_str} >= {adx_threshold})")
        # SMA alignment gate: price must be on the signal's side of SMA on all 3 TFs (parity with live cli)
        if _eff("sma_alignment_enabled", settings.strategy.sma_alignment_enabled) and (
            sma_1h is not None and sma_30m is not None and sma_15m is not None
        ):
            c1h = close_1h_list[-1]
            c30 = close_30m_list[-1]
            if signal_direction == "BUY":
                sma_aligned = c1h < sma_1h and c30 < sma_30m and close_price < sma_15m
            else:
                sma_aligned = c1h > sma_1h and c30 > sma_30m and close_price > sma_15m
            if not sma_aligned:
                side_label = "below" if signal_direction == "BUY" else "above"
                no_trade_reasons.append(
                    f"SMA({sma_period}) misaligned (price not {side_label} on all TFs)"
                )

    # TP/SL (ATR path, now reliable)
    tp = sl = entry = None
    if signal_direction:
        tp_mult = float(
            _get_pair_param_eff(
                pair, "tp_atr_multiplier", _eff("tp_atr_mult", settings.risk.tp_atr_multiplier)
            )
        )
        sl_mult = float(
            _get_pair_param_eff(
                pair, "sl_atr_multiplier", _eff("sl_atr_mult", settings.risk.sl_atr_multiplier)
            )
        )
        if atr and atr > 0:
            entry = close_price
            if signal_direction == "SELL":
                tp = entry - (atr * tp_mult)
                sl = entry + (atr * sl_mult)
            else:
                tp = entry + (atr * tp_mult)
                sl = entry - (atr * sl_mult)
        else:
            # fallback (should be rare post-fix)
            pips = 30
            entry = close_price
            tp = (
                entry + (pips * pip_size)
                if signal_direction == "BUY"
                else entry - (pips * pip_size)
            )
            sl = entry - (90 * pip_size) if signal_direction == "BUY" else entry + (90 * pip_size)

    fired = bool(signal_direction and not no_trade_reasons)

    result: dict[str, Any] = {
        "fired": fired,
        "direction": signal_direction,
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "reasons": signal_reasons
        + (["BLOCKED: " + r for r in no_trade_reasons] if no_trade_reasons else []),
        "confidence": float(signal_confidence),
        "profile": profile_label,
        "atr": atr,
        "breakout_buy": breakout_buy,
        "breakout_sell": breakout_sell,
        "hh": hh,
        "ll": ll,
        "is_ranging": is_ranging,
        "aligned": aligned,
        "no_trade_reasons": no_trade_reasons,
    }
    return result


# ---------------------------------------------------------------------------
# Hypothesis #1 (Option C): Session / Opening-Range Breakout (London & NY)
# Pure entry function with same contract as evaluate_entry so it slots into
# the unified driver (backtest_live_entry) and (future) live cli with zero
# changes to TP/SL sim, Rule C (optional), harness judge, or audit.
# Design goals per brief: frequency-first (many opens/week), sane payoff
# (e.g. ~1.5:1 RR target, not 1:3 demanding 75% WR), no pre-commit to ranging.
# Test one thesis at a time via harness; only add filters if they lift OOS.
# ---------------------------------------------------------------------------


def _find_latest_session_open(
    now: datetime, session_defs: list[tuple[str, int, int]]
) -> tuple[str, datetime] | None:
    """Return (name, open_dt_utc) for the *most recent* (latest) session open <= now (today or prev day)."""
    now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    candidates: list[tuple[datetime, str, datetime]] = []
    for day_off in (0, -1):
        d = (now + timedelta(days=day_off)).date()
        for sname, sh, sm in session_defs:
            odt = datetime.combine(d, time(sh, sm), tzinfo=UTC)
            if odt <= now:
                candidates.append((odt, sname, odt))
    if not candidates:
        return None
    # latest odt
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, sname, odt = candidates[0]
    return sname, odt


def evaluate_session_breakout(
    pair: str,
    data_1h: pd.DataFrame,
    data_30m: pd.DataFrame,
    data_15m: pd.DataFrame,
    active_signal_state: dict | None = None,
    alignment_state: dict | None = None,
    spread_quote: dict | None = None,
    news_blocked: bool | None = None,
    now_utc: datetime | None = None,
    bars_aligned: int | None = None,
    spread_filter_enabled: bool = True,
    overrides: dict | None = None,
) -> dict[str, Any]:
    """Pure session opening-range breakout entry (hypothesis #1 for Option C).

    Fires on break of the opening range (first N 15m bars after London/NY open)
    within a short breakout window. Returns same dict shape as evaluate_entry
    so driver, harness (engine=live_mtf_rsi + entry=session_orb), and future live
    path treat it identically (including ATR TP/SL sim, rejection counters, audit).

    Tunables via overrides (for research search later): session_defs, or_bars,
    breakout_window_bars, buffer_pips, tp_atr_mult, sl_atr_mult, min_atr_pips.

    Frequency first: permissive (no ADX<25, no 3-TF SMA, minimal session gate).
    Sane geometry: default ~1.5R : 1R (tp 1.5x ATR, sl 1x ATR). Harness will
    tell us if this (or tuned) clears >=100 pooled OOS + PF>=1.2 + positive PnL.
    """
    ov = dict(overrides or {})
    if data_15m is None or getattr(data_15m, "empty", True) or len(data_15m) < 3:
        return {
            "fired": False,
            "direction": None,
            "no_trade_reasons": ["insufficient 15m data"],
            "profile": "session_orb",
        }

    # Determine "now" from injection (harness) or last bar (live path)
    if now_utc is None:
        last = data_15m.index[-1]
        if isinstance(last, pd.Timestamp):
            now_utc = last.to_pydatetime()
        else:
            now_utc = last if isinstance(last, datetime) else datetime.now(UTC)
    now_utc = now_utc.replace(tzinfo=UTC) if now_utc.tzinfo is None else now_utc.astimezone(UTC)

    if news_blocked:
        return {
            "fired": False,
            "direction": None,
            "no_trade_reasons": ["news blocked"],
            "profile": "session_orb",
        }

    # Tunables (sane defaults for first honest read)
    session_defs: list[tuple[str, int, int]] = ov.get(
        "session_defs", [("london", 8, 0), ("ny", 13, 30)]
    )
    or_bars = int(ov.get("or_bars", 1))  # first 1x15m bar after open = range
    breakout_window = int(ov.get("breakout_window_bars", 6))  # ~90min to act
    buffer_pips = float(ov.get("buffer_pips", 1.5))
    tp_mult = float(ov.get("tp_atr_mult", 1.5))
    sl_mult = float(ov.get("sl_atr_mult", 1.0))  # 1.5 : 1 RR target (sane vs old 1:3)
    min_atr_pips = float(ov.get("min_atr_pips", 4.0))

    pip = 0.01 if "JPY" in pair.upper() else 0.0001
    buf = buffer_pips * pip

    highs = data_15m["high"].astype(float).tolist()
    lows = data_15m["low"].astype(float).tolist()
    closes = data_15m["close"].astype(float).tolist()
    idx = data_15m.index

    active = _find_latest_session_open(now_utc, session_defs)
    if not active:
        return {
            "fired": False,
            "direction": None,
            "no_trade_reasons": ["no recent session open"],
            "profile": "session_orb",
        }
    sname, open_dt = active

    # Bars belonging to this open (ts >= open_dt)
    try:
        open_ts = pd.Timestamp(open_dt)
        mask = idx >= open_ts
        if not mask.any():
            return {
                "fired": False,
                "direction": None,
                "no_trade_reasons": [f"no bars after {sname} open"],
                "profile": "session_orb",
            }
        or_df = data_15m[mask].iloc[: max(1, or_bars)]
        or_h = float(or_df["high"].max())
        or_l = float(or_df["low"].min())
        or_end_ts = or_df.index[-1]
    except Exception as e:
        return {
            "fired": False,
            "direction": None,
            "no_trade_reasons": [f"orb calc error: {e}"],
            "profile": "session_orb",
        }

    # How many bars since OR formation ended (current bar is the latest)
    post_mask = idx > or_end_ts
    bars_since_or = int(post_mask.sum())
    if bars_since_or < 1 or bars_since_or > breakout_window:
        return {
            "fired": False,
            "direction": None,
            "no_trade_reasons": [f"outside breakout window for {sname} ({bars_since_or} bars)"],
            "profile": "session_orb",
        }

    cur_h = highs[-1]
    cur_l = lows[-1]
    cur_c = closes[-1]

    direction = None
    if cur_h >= or_h + buf:
        direction = "BUY"
    elif cur_l <= or_l - buf:
        direction = "SELL"
    if not direction:
        return {
            "fired": False,
            "direction": None,
            "no_trade_reasons": [f"no OR break on current bar for {sname}"],
            "profile": "session_orb",
        }

    # Quality filter (example iteration per brief): close must confirm the break side
    # (reduces pure-wick fakes; only add if it improves OOS in harness runs).
    if direction == "BUY" and cur_c < or_h or direction == "SELL" and cur_c > or_l:
        direction = None
    if not direction:
        return {
            "fired": False,
            "direction": None,
            "no_trade_reasons": [f"close did not confirm OR break for {sname}"],
            "profile": "session_orb",
        }

    # ATR for TP/SL (recent window)
    atr = calculate_atr(highs[-22:], lows[-22:], closes[-22:], 14)
    if atr is None or atr <= (min_atr_pips * pip):
        return {
            "fired": False,
            "direction": None,
            "no_trade_reasons": ["low ATR / insufficient range"],
            "profile": "session_orb",
            "atr": atr,
        }

    entry = cur_c
    risk = sl_mult * atr
    rew = tp_mult * atr
    if direction == "BUY":
        tp = entry + rew
        sl = entry - risk
    else:
        tp = entry - rew
        sl = entry + risk

    return {
        "fired": True,
        "direction": direction,
        "entry": float(entry),
        "tp": float(tp),
        "sl": float(sl),
        "atr": float(atr),
        "no_trade_reasons": [],
        "confidence": 0.55,
        "profile": f"session_orb_{sname}",
        "session": sname,
        "or_high": or_h,
        "or_low": or_l,
        "bars_since_or": bars_since_or,
    }


# ---------------------------------------------------------------------------
# Hypothesis #2 (Move 1): Trend-pullback momentum (ADX > 25 regime only)
# Pure entry function with identical contract to evaluate_entry / session_orb
# so it drops straight into the unified driver + full gross/net diagnostic.
#
# Thesis (per brief): Trade *with* the trend (ADX>25), enter on pullbacks to
# EMA / prior structure. Opposite of the exhausted mean-reversion family.
# Higher frequency in trending FX majors.
# ---------------------------------------------------------------------------


def evaluate_trend_pullback(
    pair: str,
    data_1h: pd.DataFrame,
    data_30m: pd.DataFrame,
    data_15m: pd.DataFrame,
    active_signal_state: dict | None = None,
    alignment_state: dict | None = None,
    spread_quote: dict | None = None,
    news_blocked: bool | None = None,
    now_utc: datetime | None = None,
    bars_aligned: int | None = None,
    spread_filter_enabled: bool = True,
    overrides: dict | None = None,
) -> dict[str, Any]:
    """Pure trend-pullback momentum entry (hypothesis #2).

    Only fires in trending regimes (ADX(1h) > threshold, default 25).
    Trend direction from +DI/-DI.
    Pullback to fast EMA in the direction of slow EMA / structure.
    Reclaim = entry trigger.

    Returns the standard contract for seam compatibility.
    Tunables via overrides: adx_threshold, ema_fast/slow, tp_atr_mult, sl_atr_mult, etc.
    """
    ov = dict(overrides or {})
    if data_15m is None or getattr(data_15m, "empty", True) or len(data_15m) < 30:
        return {
            "fired": False,
            "direction": None,
            "no_trade_reasons": ["insufficient data"],
            "profile": "trend_pullback",
        }

    if news_blocked:
        return {
            "fired": False,
            "direction": None,
            "no_trade_reasons": ["news blocked"],
            "profile": "trend_pullback",
        }

    # Determine now
    if now_utc is None:
        last = data_15m.index[-1]
        now_utc = last.to_pydatetime() if isinstance(last, pd.Timestamp) else last
    if getattr(now_utc, "tzinfo", None) is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    # --- Trend filter on 1h (stronger regime signal)
    from src.indicators.adx import calculate_adx_full

    adx_threshold = float(ov.get("adx_threshold", 25.0))
    h1_highs = data_1h["high"].astype(float).tolist()[-60:] if len(data_1h) > 0 else []
    h1_lows = data_1h["low"].astype(float).tolist()[-60:] if len(data_1h) > 0 else []
    h1_closes = data_1h["close"].astype(float).tolist()[-60:] if len(data_1h) > 0 else []

    adx_res = calculate_adx_full(h1_highs, h1_lows, h1_closes, 14) if len(h1_closes) >= 30 else None
    if adx_res is None:
        return {
            "fired": False,
            "direction": None,
            "no_trade_reasons": ["insufficient ADX data"],
            "profile": "trend_pullback",
        }
    adx_val, plus_di, minus_di = adx_res
    if adx_val < adx_threshold:
        return {
            "fired": False,
            "direction": None,
            "no_trade_reasons": [f"weak trend (ADX {adx_val:.0f} < {adx_threshold})"],
            "profile": "trend_pullback",
        }

    is_bull = plus_di > minus_di

    # --- 15m EMAs for pullback (fast) in direction of slow
    closes15 = data_15m["close"].astype(float).tolist()
    highs15 = data_15m["high"].astype(float).tolist()
    lows15 = data_15m["low"].astype(float).tolist()

    ema_fast_p = int(ov.get("ema_fast", 20))
    ema_slow_p = int(ov.get("ema_slow", 50))
    ema_fast = calculate_ema(closes15, ema_fast_p)
    ema_slow = calculate_ema(closes15, ema_slow_p)

    ema_f = ema_fast[-1] if ema_fast and ema_fast[-1] is not None else None
    ema_s = ema_slow[-1] if ema_slow and ema_slow[-1] is not None else None
    if ema_f is None or ema_s is None:
        return {
            "fired": False,
            "direction": None,
            "no_trade_reasons": ["insufficient EMA data"],
            "profile": "trend_pullback",
        }

    cur_close = closes15[-1]
    cur_low = lows15[-1]
    cur_high = highs15[-1]

    # Pullback + reclaim logic
    fired = False
    direction = None
    if is_bull:
        # Bull trend: price generally above slow EMA, pullback touches fast EMA, reclaim
        if cur_close > ema_s and cur_low <= ema_f and cur_close > ema_f:
            # Additional structure: not too extended (optional simple guard)
            fired = True
            direction = "BUY"
    else:
        if cur_close < ema_s and cur_high >= ema_f and cur_close < ema_f:
            fired = True
            direction = "SELL"

    if not fired:
        return {
            "fired": False,
            "direction": None,
            "no_trade_reasons": ["no pullback reclaim in trend"],
            "profile": "trend_pullback",
        }

    # ATR TP/SL (trend entries often get a bit more room)
    atr = calculate_atr(highs15[-22:], lows15[-22:], closes15[-22:], 14)
    if atr is None or atr <= 0:
        return {
            "fired": False,
            "direction": None,
            "no_trade_reasons": ["low ATR"],
            "profile": "trend_pullback",
        }

    tp_mult = float(ov.get("tp_atr_mult", 2.0))  # room for trends
    sl_mult = float(ov.get("sl_atr_mult", 1.0))

    entry = cur_close
    risk = sl_mult * atr
    rew = tp_mult * atr

    if direction == "BUY":
        tp = entry + rew
        sl = entry - risk
    else:
        tp = entry - rew
        sl = entry + risk

    return {
        "fired": True,
        "direction": direction,
        "entry": float(entry),
        "tp": float(tp),
        "sl": float(sl),
        "atr": float(atr),
        "no_trade_reasons": [],
        "confidence": 0.6,
        "profile": f"trend_pullback_{'bull' if is_bull else 'bear'}",
        "adx": float(adx_val),
        "trend": "bull" if is_bull else "bear",
    }
