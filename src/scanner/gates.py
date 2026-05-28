"""Confirmation profiles, breakout checks, and trade gates."""

from __future__ import annotations

import json
import logging as _logging
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any, TypedDict

from src.config import get_settings
from src.data.fetcher import DataFetcher
from src.indicators.high_low import is_breakout_high, is_breakout_low
from src.indicators.rsi import calculate_rsi
from src.scanner.state import ActiveSignalRecord

ADX_TREND_THRESHOLD = 25.0

# Conservative fallback spreads (pips) when no live source is available.
DEFAULT_SPREAD_PIPS: dict[str, float] = {
    "EUR/USD": 0.8,
    "GBP/USD": 1.2,
    "USD/JPY": 0.9,
    "USD/CHF": 1.2,
    "USD/CAD": 1.4,
    "AUD/USD": 1.1,
    "NZD/USD": 1.4,
    "EUR/JPY": 1.3,
    "GBP/JPY": 2.1,
    "EUR/GBP": 1.0,
    "NZD/JPY": 1.9,
    "AUD/JPY": 1.6,
    "GBP/CHF": 2.0,
    "AUD/CAD": 2.0,
}


class ConfirmationProfile(TypedDict):
    variant: str
    buffer_pips: float
    confirm_bars: int


class SpreadQuote(TypedDict, total=False):
    bid: float
    ask: float
    spread: float
    source: str


class MicroContext(TypedDict):
    rsi_5m: float | None
    rsi_1m: float | None
    execution_note: str | None


# Per-pair confirmation profiles are loaded from config/settings.yaml.
# The format per profile: {"variant": str, "buffer_pips": float, "confirm_bars": int}
# V0 = RSI-only (no breakout gate, fires on MTF alignment alone)
# V1 = continuation breakout (BUY below LL, SELL above HH)
# V2 = reversal breakout (BUY wick through + close reclaim, SELL wick through + close reject)
# V2R = reversal structural break (BUY above HH, SELL below LL)
# buffer_pips = pip buffer on breakout threshold
# confirm_bars = max bars after MTF alignment to accept breakout (0 = immediate only)
DEFAULT_CONFIRMATION_PROFILE: ConfirmationProfile = {
    "variant": get_settings().strategy.confirmation_profiles.default.variant,
    "buffer_pips": get_settings().strategy.confirmation_profiles.default.buffer_pips,
    "confirm_bars": get_settings().strategy.confirmation_profiles.default.confirm_bars,
}


def _get_pair_param(pair: str, param: str, default: float | int) -> float | int:
    """Look up a per-pair override from config, falling back to the global default."""
    settings = get_settings()
    override = settings.strategy.pair_overrides.get(pair)
    if override is not None:
        value = getattr(override, param, None)
        if value is not None:
            return value
    return default


def _get_static_spread_quote(pair: str, pip_size: float) -> SpreadQuote | None:
    pips = DEFAULT_SPREAD_PIPS.get(pair.upper()) or DEFAULT_SPREAD_PIPS.get(pair)
    if pips is None:
        return None
    return {"spread": float(pips) * pip_size, "source": "static"}


def _get_ctrader_spread(pair: str) -> SpreadQuote | None:
    """Fetch live bid/ask from the cTrader spread endpoint.

    Expected endpoint: http://host.docker.internal:28081/spread/GBPUSD
    Returns {bid, ask, spread} or None on failure.
    """
    base_url = os.getenv("CTRADER_SPREAD_URL", "http://host.docker.internal:28081")
    normalized = pair.upper().replace("/", "")
    url = f"{base_url.rstrip('/')}/spread/{normalized}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            payload = json.loads(resp.read().decode())
        if not isinstance(payload, dict):
            return None
        bid = payload.get("bid")
        ask = payload.get("ask")
        spread = payload.get("spread")
        if (
            isinstance(bid, (int, float))
            and isinstance(ask, (int, float))
            and isinstance(spread, (int, float))
        ):
            return {"bid": float(bid), "ask": float(ask), "spread": float(spread)}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None
    return None


def _get_confirmation_profile(pair: str) -> ConfirmationProfile:
    profiles = get_settings().strategy.confirmation_profiles
    entry = profiles.pairs.get(pair)
    if entry is not None:
        return {"variant": entry.variant, "buffer_pips": entry.buffer_pips, "confirm_bars": entry.confirm_bars}
    return DEFAULT_CONFIRMATION_PROFILE


def _profile_label(profile: ConfirmationProfile) -> str:
    v = profile["variant"]
    b = profile["buffer_pips"]
    c = profile["confirm_bars"]
    return f"{v}_b{b}_c{c}"


def _check_breakout_with_profile(
    profile: ConfirmationProfile,
    direction: str,
    close_price: float,
    hh: float | None,
    ll: float | None,
    pip_size: float,
    bar_high: float | None = None,
    bar_low: float | None = None,
) -> bool:
    """Check breakout using the pair's confirmation profile."""
    buffer_pips = profile["buffer_pips"]
    variant = profile["variant"]
    buffer_pct = (buffer_pips * pip_size) / close_price if close_price else 0.0

    if variant == "V0":
        # RSI-only: no breakout gate required
        return True
    elif variant == "V1":
        # Continuation: BUY breaks below LL, SELL breaks above HH
        if direction == "BUY" and ll is not None:
            return is_breakout_low(close_price, ll, buffer_pct)
        if direction == "SELL" and hh is not None:
            return is_breakout_high(close_price, hh, buffer_pct)
    elif variant == "V2":
        # Reversal: wick through level + close back inside
        # BUY: bar low wicked through LL, but close reclaimed above LL
        # SELL: bar high wicked through HH, but close rejected below HH
        if direction == "BUY" and ll is not None:
            down_trigger = ll - buffer_pips * pip_size
            wick_through = (
                (bar_low is not None and bar_low <= down_trigger) if bar_low is not None else True
            )
            close_reclaim = close_price > ll
            return wick_through and close_reclaim
        if direction == "SELL" and hh is not None:
            up_trigger = hh + buffer_pips * pip_size
            wick_through = (
                (bar_high is not None and bar_high >= up_trigger) if bar_high is not None else True
            )
            close_reject = close_price < hh
            return wick_through and close_reject
    elif variant == "V2R":
        # Opposite-direction Structural Break Reversal: BUY breaks above HH, SELL breaks below LL
        if direction == "BUY" and hh is not None:
            return is_breakout_high(close_price, hh, buffer_pct)
        if direction == "SELL" and ll is not None:
            return is_breakout_low(close_price, ll, buffer_pct)
    return False


def _is_signal_invalidated(
    record: ActiveSignalRecord,
    data_15m: Any,
    rsi_15m_series: list[float],
    current_close: float,
    current_sma_15m: float | None,
) -> tuple[bool, str | None]:
    """Rule C invalidation: TP/SL hit, RSI(15m) midline cross, or SMA flip."""
    direction = record.get("direction")
    fired_at = int(record.get("fired_at", 0))
    tp = float(record.get("tp", 0.0))
    sl = float(record.get("sl", 0.0))
    if direction not in {"BUY", "SELL"} or fired_at <= 0:
        return True, "invalid_record"

    fired_at_dt = datetime.fromtimestamp(fired_at, tz=UTC)
    bars_since = data_15m[data_15m.index > fired_at_dt]

    if not bars_since.empty:
        highs = bars_since["high"].astype(float)
        lows = bars_since["low"].astype(float)
        if direction == "BUY":
            if (highs >= tp).any():
                return True, "tp_hit"
            if (lows <= sl).any():
                return True, "sl_hit"
        else:
            if (lows <= tp).any():
                return True, "tp_hit"
            if (highs >= sl).any():
                return True, "sl_hit"

        # RSI(15m) midline cross on any closed bar since fire
        bar_count = len(bars_since)
        rsi_tail = rsi_15m_series[-bar_count:] if bar_count <= len(rsi_15m_series) else rsi_15m_series
        rsi_clean = [r for r in rsi_tail if r is not None]
        if direction == "BUY" and any(r >= 50.0 for r in rsi_clean):
            return True, "rsi_midline_cross"
        if direction == "SELL" and any(r <= 50.0 for r in rsi_clean):
            return True, "rsi_midline_cross"

    # SMA flip against the original direction
    if current_sma_15m is not None:
        if direction == "BUY" and current_close > current_sma_15m:
            return True, "sma_flip"
        if direction == "SELL" and current_close < current_sma_15m:
            return True, "sma_flip"

    return False, None


def _mtf_distance_to_buy(rsi_1h: float, rsi_30m: float, rsi_15m: float, threshold: float) -> float:
    return max(rsi_1h - threshold, rsi_30m - threshold, rsi_15m - threshold)


def _mtf_distance_to_sell(rsi_1h: float, rsi_30m: float, rsi_15m: float, threshold: float) -> float:
    return max(threshold - rsi_1h, threshold - rsi_30m, threshold - rsi_15m)


def _session_allowed(now_utc: datetime, windows: list[str]) -> bool:
    hour = now_utc.hour
    for window in windows:
        try:
            start_s, end_s = window.split("-")
            start_h = int(start_s)
            end_h = int(end_s)
        except Exception:
            continue
        if start_h <= hour < end_h:
            return True
    return False


def _priority_for_pair(settings, pair: str) -> int:
    priorities = getattr(settings.strategy, "pair_priorities", {}) or {}
    return int(priorities.get(pair, 50))


def _fetch_micro_context(fetcher: DataFetcher, symbol: str, rsi_period: int) -> MicroContext:
    """Fetch 5m/1m RSI for micro-timing context. Suppresses yfinance noise on cross pairs."""
    context: MicroContext = {
        "rsi_5m": None,
        "rsi_1m": None,
        "execution_note": None,
    }
    # Suppress noisy yfinance warnings for unsupported cross pair intervals
    yf_logger = _logging.getLogger("yfinance")
    prev_level = yf_logger.level
    yf_logger.setLevel(_logging.CRITICAL)
    try:
        data_5m = fetcher.fetch(symbol, period="1d", interval="5min")
        if not data_5m.empty:
            context["rsi_5m"] = calculate_rsi(data_5m["close"].values.tolist()[-50:], rsi_period)
        data_1m = fetcher.fetch(symbol, period="1d", interval="1min")
        if not data_1m.empty:
            context["rsi_1m"] = calculate_rsi(data_1m["close"].values.tolist()[-50:], rsi_period)
    except Exception:
        pass
    finally:
        yf_logger.setLevel(prev_level)
    return context


def _execution_note(direction: str, rsi_5m: float | None, rsi_1m: float | None) -> str:
    if rsi_5m is None and rsi_1m is None:
        return "No 1m/5m context available"
    vals = [v for v in [rsi_5m, rsi_1m] if v is not None]
    if direction == "BUY":
        if any(v > 70 for v in vals):
            return "15m confirmed, but 1m/5m is stretched up — wait for a small pullback"
        if any(v < 30 for v in vals):
            return "15m confirmed and 1m/5m still depressed — watch for reversal trigger"
        return "15m confirmed and 1m/5m is balanced — market entry is acceptable"
    if any(v < 30 for v in vals):
        return "15m confirmed, but 1m/5m is stretched down — wait for a small bounce"
    if any(v > 70 for v in vals):
        return "15m confirmed and 1m/5m still elevated — watch for reversal trigger"
    return "15m confirmed and 1m/5m is balanced — market entry is acceptable"
