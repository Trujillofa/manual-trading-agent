"""CLI entry point for manual trading agent."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from src.backtest.engine import BacktestEngine
from src.config import get_settings
from src.data.fetcher import DataFetcher
from src.indicators.adx import calculate_adx
from src.indicators.candlestick import (
    CandlePattern,
    PatternType,
    detect_patterns,
)
from src.indicators.high_low import highest_high, is_breakout_high, is_breakout_low, lowest_low
from src.indicators.rsi import (
    calculate_rsi,
    calculate_rsi_series,
    detect_bearish_divergence,
    detect_bullish_divergence,
)
from src.news.news_checker import NewsChecker
from src.notifications.telegram import TelegramNotifier
from src.strategy.multi_timeframe import MTFRSIStrategy

# Pair-specific confirmation profiles from optimization bakeoff (2026-04-03).
# Format: {"variant": "V1"|"V2", "buffer_pips": float, "confirm_bars": int}
# V1 = continuation breakout (BUY below LL, SELL above HH)
# V2 = reversal breakout (BUY wick through + close reclaim, SELL wick through + close reject)
# buffer_pips = pip buffer on breakout threshold
# confirm_bars = max bars after MTF alignment to accept breakout (0 = immediate only)
CONFIRMATION_PROFILES: dict[str, dict[str, object]] = {
    "GBP/USD": {"variant": "V2", "buffer_pips": 2.0, "confirm_bars": 5},
}

# Default profile for pairs without a specific one
DEFAULT_CONFIRMATION_PROFILE: dict[str, object] = {
    "variant": "V2", "buffer_pips": 2.0, "confirm_bars": 5,
}

# ADX threshold: only take mean-reversion signals when ADX < this value (ranging market)
ADX_TREND_THRESHOLD = 25.0

# Conservative fallback spreads (pips) when no live source is available.
DEFAULT_SPREAD_PIPS: dict[str, float] = {
    'EUR/USD': 0.8,
    'GBP/USD': 1.2,
    'USD/JPY': 0.9,
    'USD/CHF': 1.2,
    'USD/CAD': 1.4,
    'AUD/USD': 1.1,
    'NZD/USD': 1.4,
    'EUR/JPY': 1.3,
    'GBP/JPY': 2.1,
    'EUR/GBP': 1.0,
    'NZD/JPY': 1.9,
    'AUD/JPY': 1.6,
}


def _get_static_spread_quote(pair: str, pip_size: float) -> dict[str, float] | None:
    pips = DEFAULT_SPREAD_PIPS.get(pair.upper()) or DEFAULT_SPREAD_PIPS.get(pair)
    if pips is None:
        return None
    return {"spread": float(pips) * pip_size, "source": "static"}


def _get_ctrader_spread(pair: str) -> dict[str, float] | None:
    """Fetch live bid/ask from the cTrader spread endpoint.

    Expected endpoint: http://host.docker.internal:28081/spread/GBPUSD
    Returns {bid, ask, spread} or None on failure.
    """
    import urllib.error
    import urllib.request

    base_url = os.getenv("CTRADER_SPREAD_URL", "http://host.docker.internal:28081")
    normalized = pair.upper().replace("/", "")
    url = f"{base_url.rstrip('/')}/spread/{normalized}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            payload = json.loads(resp.read().decode())
        bid = payload.get("bid")
        ask = payload.get("ask")
        spread = payload.get("spread")
        if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and isinstance(spread, (int, float)):
            return {"bid": float(bid), "ask": float(ask), "spread": float(spread)}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None
    return None


def _get_confirmation_profile(pair: str) -> dict[str, object]:
    return CONFIRMATION_PROFILES.get(pair, DEFAULT_CONFIRMATION_PROFILE)


def _profile_label(profile: dict[str, object]) -> str:
    v = profile["variant"]
    b = profile["buffer_pips"]
    c = profile["confirm_bars"]
    return f"{v}_b{b}_c{c}"


def _check_breakout_with_profile(
    profile: dict[str, object],
    direction: str,
    close_price: float,
    hh: float | None,
    ll: float | None,
    pip_size: float,
    bar_high: float | None = None,
    bar_low: float | None = None,
) -> bool:
    """Check breakout using the pair's confirmation profile."""
    buffer_pips = float(profile.get("buffer_pips", 0.0))
    variant = str(profile.get("variant", "V2"))
    buffer_pct = (buffer_pips * pip_size) / close_price if close_price else 0.0

    if variant == "V1":
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
            wick_through = (bar_low is not None and bar_low <= down_trigger) if bar_low is not None else True
            close_reclaim = close_price > ll
            return wick_through and close_reclaim
        if direction == "SELL" and hh is not None:
            up_trigger = hh + buffer_pips * pip_size
            wick_through = (bar_high is not None and bar_high >= up_trigger) if bar_high is not None else True
            close_reject = close_price < hh
            return wick_through and close_reject
    return False


def _parse_pairs(raw_pairs: str | None) -> list[str] | None:
    if raw_pairs is None:
        return None

    pairs = [pair.strip().upper() for pair in raw_pairs.split(",") if pair.strip()]
    return pairs or None


def _near_setup_state_path() -> Path:
    return Path("/app/logs/near_setup_state.json")


def _load_near_setup_state() -> dict[str, dict[str, object]]:
    path = _near_setup_state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_near_setup_state(state: dict[str, dict[str, object]]) -> None:
    path = _near_setup_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _alignment_state_path() -> Path:
    return Path("/app/logs/alignment_state.json")


def _load_alignment_state() -> dict[str, dict[str, object]]:
    path = _alignment_state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_alignment_state(state: dict[str, dict[str, object]]) -> None:
    path = _alignment_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _cooldown_state_path() -> Path:
    return Path("/app/logs/cooldown_state.json")


def _load_cooldown_state() -> dict[str, dict[str, object]]:
    path = _cooldown_state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cooldown_state(state: dict[str, dict[str, object]]) -> None:
    path = _cooldown_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _audit_log_path() -> Path:
    return Path("/app/logs/signal_audit.jsonl")


def _append_audit_log(payload: dict[str, object]) -> None:
    path = _audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


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


def _fetch_micro_context(fetcher: DataFetcher, symbol: str, rsi_period: int) -> dict[str, float | str | None]:
    """Fetch 5m/1m RSI for micro-timing context. Suppresses yfinance noise on cross pairs."""
    import logging as _logging

    context: dict[str, float | str | None] = {
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


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manual Forex Trading Agent - RSI Multi-Timeframe Strategy"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    scan_parser = subparsers.add_parser("scan", help="Scan pairs for signals")
    scan_parser.add_argument("--pairs", help="Comma-separated pairs (default: all)")
    scan_parser.add_argument("--timeframe", default="15m", help="Execution timeframe")

    analyze_parser = subparsers.add_parser("analyze", help="Analyze specific pair")
    analyze_parser.add_argument("pair", help="Pair to analyze (e.g., EUR/USD)")
    analyze_parser.add_argument("--timeframe", default="15m", help="Timeframe")

    news_parser = subparsers.add_parser("news", help="Check upcoming news")
    news_parser.add_argument("--hours", type=int, default=24, help="Hours ahead")

    backtest_parser = subparsers.add_parser("backtest", help="Run backtest")
    backtest_parser.add_argument("--pair", required=True, help="Pair to backtest (e.g., EUR/USD)")
    backtest_parser.add_argument("--start", help="Start date YYYY-MM-DD")
    backtest_parser.add_argument("--end", help="End date YYYY-MM-DD")

    enhanced_backtest_parser = subparsers.add_parser("backtest-enhanced", help="Run enhanced backtest with TP/SL")
    enhanced_backtest_parser.add_argument("--pair", required=True, help="Pair to backtest (e.g., EUR/USD)")
    enhanced_backtest_parser.add_argument("--start", help="Start date YYYY-MM-DD")
    enhanced_backtest_parser.add_argument("--end", help="End date YYYY-MM-DD")
    enhanced_backtest_parser.add_argument("--no-patterns", action="store_true", help="Disable pattern detection")
    enhanced_backtest_parser.add_argument("--no-divergence", action="store_true", help="Disable divergence detection")

    subparsers.add_parser("telegram-poll", help="Poll Telegram commands (e.g. /watchlist)")

    dash_parser = subparsers.add_parser("dashboard", help="Signal dashboard and paper P&L")
    dash_parser.add_argument("--days", type=int, default=30, help="Days of history to show")

    return parser


async def run_scan(pairs: list[str] | None, timeframe: str) -> None:
    settings = get_settings()
    fetcher = DataFetcher()
    news_checker = NewsChecker(
        lockout_minutes_before=settings.news.lockout_minutes_before,
        lockout_minutes_after=settings.news.lockout_minutes_after,
        importance_threshold=settings.news.importance_threshold,
    )
    if settings.news.enabled:
        with contextlib.suppress(Exception):
            await news_checker.fetch_events(hours_ahead=24)

    # Initialize Telegram notifier if enabled
    notifier = None
    if settings.telegram.enabled:
        token = settings.telegram.bot_token
        chat_id = settings.telegram.chat_id
        if token and chat_id:
            notifier = TelegramNotifier(token, chat_id)

    majors = list(getattr(settings.trading, "majors", []))
    minors = list(getattr(settings.trading, "minors", []))
    selected_pairs = pairs or (majors + minors)

    rsi_overbought = settings.strategy.rsi_overbought
    rsi_oversold = settings.strategy.rsi_oversold
    rsi_period = int(settings.strategy.rsi_period)
    lookback = int(settings.strategy.lookback_bars)

    print(f"\n[SCAN] Scanning {len(selected_pairs)} pairs (MTF: 1h + 30m + 15m)...")
    near_candidates: list[dict[str, object]] = []
    near_state = _load_near_setup_state() if notifier else {}
    cooldown_state = _load_cooldown_state()
    alignment_state = _load_alignment_state()
    now_ts = int(time.time())
    now_utc = datetime.now(UTC)
    confirmed_pairs: set[str] = set()

    for pair in selected_pairs:
        try:
            # Fetch multi-timeframe data
            symbol = pair.replace("/", "")
            data_1h = fetcher.fetch(symbol, period="5d", interval="1h")
            data_30m = fetcher.fetch(symbol, period="3d", interval="30m")
            data_15m = fetcher.fetch(symbol, period="2d", interval="15m")

            if data_1h.empty or data_30m.empty or data_15m.empty:
                continue

            # Extract price data from 15m (primary timeframe)
            close_15m = data_15m["close"].values.tolist()
            high_15m = data_15m["high"].values.tolist()
            low_15m = data_15m["low"].values.tolist()
            open_15m = data_15m["open"].values.tolist() if "open" in data_15m else close_15m

            # Calculate RSI for each timeframe
            rsi_1h = calculate_rsi(data_1h["close"].values.tolist()[-50:], rsi_period)
            rsi_30m = calculate_rsi(data_30m["close"].values.tolist()[-50:], rsi_period)
            rsi_15m_val = calculate_rsi(close_15m[-50:], rsi_period)

            # 20-bar HH/LL from 15m
            hh = highest_high(high_15m, lookback)
            ll = lowest_low(low_15m, lookback)
            close_price = close_15m[-1]

            # Calculate ATR for TP/SL
            atr = _calculate_atr(high_15m[-14:], low_15m[-14:], close_15m[-14:])
            pip_size = 0.01 if "JPY" in pair else 0.0001
            bar_high = high_15m[-1] if high_15m else None
            bar_low = low_15m[-1] if low_15m else None
            profile = _get_confirmation_profile(pair)
            breakout_buy = _check_breakout_with_profile(profile, "BUY", close_price, hh, ll, pip_size, bar_high, bar_low)
            breakout_sell = _check_breakout_with_profile(profile, "SELL", close_price, hh, ll, pip_size, bar_high, bar_low)

            # ADX trend filter on 1h timeframe
            adx_1h = calculate_adx(
                data_1h["high"].values.tolist()[-50:],
                data_1h["low"].values.tolist()[-50:],
                data_1h["close"].values.tolist()[-50:],
            )
            is_ranging = adx_1h is not None and adx_1h < ADX_TREND_THRESHOLD
            # Spread: requires real bid/ask source (OANDA)
            quote = None
            try:
                from src.data.fetcher import OandaFetcher
                if os.getenv("OANDA_API_KEY") and os.getenv("OANDA_ACCOUNT_ID"):
                    quote = OandaFetcher(
                        api_key=os.getenv("OANDA_API_KEY"),
                        account_id=os.getenv("OANDA_ACCOUNT_ID"),
                        practice=True,
                    ).get_bid_ask_quote(pair)
            except Exception:
                quote = None

            # Fallback 1: try cTrader spread server
            if quote is None:
                try:
                    quote = _get_ctrader_spread(pair)
                    if quote is not None:
                        quote["source"] = quote.get("source", "ctrader")
                except Exception:
                    pass

            # Fallback 2: conservative static spread defaults
            if quote is None:
                quote = _get_static_spread_quote(pair, pip_size)

            spread_pips = None
            spread_ok = True
            pair_spread_limits = getattr(settings.strategy, "spread_limits_pips", {}) or {}
            max_spread_for_pair = float(pair_spread_limits.get(pair, settings.strategy.max_spread_pips))
            if quote and isinstance(quote.get("spread"), float):
                spread_pips = float(quote["spread"]) / pip_size
                spread_ok = spread_pips <= max_spread_for_pair
            elif settings.strategy.spread_filter_enabled:
                spread_ok = False

            # Detect candlestick patterns
            candle_patterns: list[CandlePattern] = []
            if len(open_15m) >= 3:
                candle_patterns = detect_patterns(
                    open_15m[-20:] if len(open_15m) >= 20 else open_15m,
                    high_15m[-20:] if len(high_15m) >= 20 else high_15m,
                    low_15m[-20:] if len(low_15m) >= 20 else low_15m,
                    close_15m[-20:] if len(close_15m) >= 20 else close_15m,
                    lookback=3,
                )

            # Detect RSI divergence on 15m
            rsi_series = calculate_rsi_series(close_15m, rsi_period)
            bullish_div = detect_bullish_divergence(close_15m[-100:], rsi_series[-100:], lookback=5)
            bearish_div = detect_bearish_divergence(close_15m[-100:], rsi_series[-100:], lookback=5)

            bullish_pats = [p for p in candle_patterns if p.pattern_type == PatternType.BULLISH]
            bearish_pats = [p for p in candle_patterns if p.pattern_type == PatternType.BEARISH]

            print(f"\n{pair}:")
            print(f"  Price: {close_price:.5f}")
            print(f"  RSI 1h: {rsi_1h:.1f}" if rsi_1h else "  RSI 1h: N/A")
            print(f"  RSI 30m: {rsi_30m:.1f}" if rsi_30m else "  RSI 30m: N/A")
            print(f"  RSI 15m: {rsi_15m_val:.1f}" if rsi_15m_val else "  RSI 15m: N/A")
            print(f"  20-bar HH: {hh:.5f}" if hh else "  20-bar HH: N/A")
            print(f"  20-bar LL: {ll:.5f}" if ll else "  20-bar LL: N/A")
            print(f"  Confirmation profile: {_profile_label(profile)}")
            print(f"  Breakout BUY(low): {'yes' if breakout_buy else 'no'}")
            print(f"  Breakout SELL(high): {'yes' if breakout_sell else 'no'}")
            if spread_pips is not None:
                spread_source = quote.get('source', 'live') if isinstance(quote, dict) else 'unknown'
                print(f"  Spread: {spread_pips:.2f} pips ({'ok' if spread_ok else 'too wide'}, max={max_spread_for_pair:.2f}, source={spread_source})")
            else:
                print("  Spread: unavailable")
            if adx_1h is not None:
                regime = "ranging" if is_ranging else "trending"
                print(f"  ADX(14) 1h: {adx_1h:.1f} ({regime})")
            else:
                print("  ADX(14) 1h: insufficient data")

            # Show patterns and divergence
            if bullish_pats:
                print(f"  🟢 Patterns: {', '.join(p.name for p in bullish_pats)}")
            if bearish_pats:
                print(f"  🔴 Patterns: {', '.join(p.name for p in bearish_pats)}")
            if bullish_div:
                print(f"  📈 Bullish Div: {bullish_div.strength:.2f}")
            if bearish_div:
                print(f"  📉 Bearish Div: {bearish_div.strength:.2f}")

            # Check MTF RSI alignment
            if rsi_1h is None or rsi_30m is None or rsi_15m_val is None:
                continue

            all_oversold = rsi_1h < rsi_oversold and rsi_30m < rsi_oversold and rsi_15m_val < rsi_oversold
            all_overbought = rsi_1h > rsi_overbought and rsi_30m > rsi_overbought and rsi_15m_val > rsi_overbought

            buy_distance = _mtf_distance_to_buy(float(rsi_1h), float(rsi_30m), float(rsi_15m_val), float(rsi_oversold))
            sell_distance = _mtf_distance_to_sell(float(rsi_1h), float(rsi_30m), float(rsi_15m_val), float(rsi_overbought))
            near_direction = "BUY" if buy_distance <= sell_distance else "SELL"
            near_distance = min(buy_distance, sell_distance)
            if near_direction == "BUY":
                missing_timeframes = [
                    tf for tf, value in [("1h", float(rsi_1h)), ("30m", float(rsi_30m)), ("15m", float(rsi_15m_val))]
                    if value >= float(rsi_oversold)
                ]
            else:
                missing_timeframes = [
                    tf for tf, value in [("1h", float(rsi_1h)), ("30m", float(rsi_30m)), ("15m", float(rsi_15m_val))]
                    if value <= float(rsi_overbought)
                ]
            remaining = len(missing_timeframes)

            micro_context: dict[str, float | str | None] = {"rsi_5m": None, "rsi_1m": None, "execution_note": None}
            if remaining == 1 or near_distance <= 4.0:
                micro_context = _fetch_micro_context(fetcher, symbol, rsi_period)

            aligned = bool(all_oversold or all_overbought)

            # Track alignment age for confirm_bars window
            confirm_bars = int(profile.get("confirm_bars", 0))
            if aligned:
                prev_align = alignment_state.get(pair)
                if prev_align and str(prev_align.get("direction", "")) == near_direction:
                    bars_aligned = int(prev_align.get("bars", 0)) + 1
                else:
                    bars_aligned = 0
                alignment_state[pair] = {"direction": near_direction, "bars": bars_aligned}
            else:
                alignment_state.pop(pair, None)
                bars_aligned = 0

            # For confirm_bars > 0, allow breakout within N bars of first alignment
            within_confirm_window = aligned and bars_aligned <= confirm_bars
            breakout_confirmed = (
                (near_direction == "BUY" and breakout_buy) or (near_direction == "SELL" and breakout_sell)
            )
            breakout_pending = aligned and not breakout_confirmed
            # If breakout happened but outside the confirmation window, treat as pending
            if breakout_confirmed and confirm_bars > 0 and not within_confirm_window:
                breakout_confirmed = False
                breakout_pending = True

            near_candidates.append(
                {
                    "pair": pair,
                    "direction": near_direction,
                    "distance": float(near_distance),
                    "remaining": remaining,
                    "missing_timeframes": missing_timeframes,
                    "aligned": aligned,
                    "breakout_pending": breakout_pending,
                    "rsi_1h": float(rsi_1h),
                    "rsi_30m": float(rsi_30m),
                    "rsi_15m": float(rsi_15m_val),
                    "rsi_5m": micro_context.get("rsi_5m"),
                    "rsi_1m": micro_context.get("rsi_1m"),
                    "patterns": [p.name for p in candle_patterns],
                    "bullish_div": bullish_div.strength if bullish_div else None,
                    "bearish_div": bearish_div.strength if bearish_div else None,
                    "price": float(close_price),
                }
            )

            session_ok = True
            if settings.strategy.session_filter_enabled:
                session_ok = _session_allowed(now_utc, list(settings.strategy.session_allowed_utc))
            news_blocked = settings.news.enabled and news_checker.is_blocked(pair, now_utc)
            cooldown_info = cooldown_state.get(pair, {})
            cooldown_until = int(cooldown_info.get("until", 0))
            cooldown_active = now_ts < cooldown_until

            signal_direction: Literal["BUY", "SELL", None] = None
            signal_confidence = 0.0
            signal_reasons: list[str] = []
            no_trade_reasons: list[str] = []

            if all_oversold:
                signal_direction = "BUY"
                signal_confidence = 0.6
                signal_reasons.append(f"MTF RSI oversold (1h:{rsi_1h:.0f}, 30m:{rsi_30m:.0f}, 15m:{rsi_15m_val:.0f})")
                if breakout_confirmed:
                    signal_confidence += 0.1
                    signal_reasons.append("15m breakout low confirmed")
                    if confirm_bars > 0:
                        signal_reasons.append(f"confirmed at bar {bars_aligned}/{confirm_bars}")
                else:
                    no_trade_reasons.append("15m breakout low not confirmed")
                    if confirm_bars > 0 and not within_confirm_window:
                        no_trade_reasons.append(f"confirmation window expired ({bars_aligned} bars > {confirm_bars})")
                if bullish_div:
                    signal_confidence += bullish_div.strength * 0.2
                    signal_reasons.append("bullish divergence")
                if bullish_pats:
                    signal_confidence += 0.1
                    signal_reasons.append(f"bullish pattern ({', '.join(p.name for p in bullish_pats[:2])})")

            elif all_overbought:
                signal_direction = "SELL"
                signal_confidence = 0.6
                signal_reasons.append(f"MTF RSI overbought (1h:{rsi_1h:.0f}, 30m:{rsi_30m:.0f}, 15m:{rsi_15m_val:.0f})")
                if breakout_confirmed:
                    signal_confidence += 0.1
                    signal_reasons.append("15m breakout high confirmed")
                    if confirm_bars > 0:
                        signal_reasons.append(f"confirmed at bar {bars_aligned}/{confirm_bars}")
                else:
                    no_trade_reasons.append("15m breakout high not confirmed")
                    if confirm_bars > 0 and not within_confirm_window:
                        no_trade_reasons.append(f"confirmation window expired ({bars_aligned} bars > {confirm_bars})")
                if bearish_div:
                    signal_confidence += bearish_div.strength * 0.2
                    signal_reasons.append("bearish divergence")
                if bearish_pats:
                    signal_confidence += 0.1
                    signal_reasons.append(f"bearish pattern ({', '.join(p.name for p in bearish_pats[:2])})")

            if signal_direction:
                if not session_ok:
                    no_trade_reasons.append("outside allowed session")
                if news_blocked:
                    no_trade_reasons.append("blocked by high-impact news")
                if settings.strategy.spread_filter_enabled and not spread_ok:
                    no_trade_reasons.append("spread unavailable/too wide")
                if not is_ranging:
                    adx_str = f"{adx_1h:.0f}" if adx_1h is not None else "N/A"
                    no_trade_reasons.append(f"trending market (ADX {adx_str} >= {ADX_TREND_THRESHOLD:.0f})")
                if cooldown_active:
                    no_trade_reasons.append("cooldown active")
                if no_trade_reasons:
                    print(f"  🚫 NO TRADE: {', '.join(no_trade_reasons)}")
                    _append_audit_log({
                        "ts": now_utc.isoformat(),
                        "pair": pair,
                        "state": "blocked",
                        "candidate_direction": signal_direction,
                        "reasons": no_trade_reasons,
                        "rsi_1h": float(rsi_1h),
                        "rsi_30m": float(rsi_30m),
                        "rsi_15m": float(rsi_15m_val),
                        "breakout_buy": breakout_buy,
                        "breakout_sell": breakout_sell,
                    })
                    signal_direction = None

            if not signal_direction and breakout_pending:
                rsi_5m = micro_context.get("rsi_5m")
                rsi_1m = micro_context.get("rsi_1m")
                print(f"  ⏳ ALIGNED / BREAKOUT PENDING: {near_direction}")
                print("     Missing: breakout confirmation only")
                if isinstance(rsi_5m, float):
                    print(f"     RSI 5m: {rsi_5m:.1f}")
                if isinstance(rsi_1m, float):
                    print(f"     RSI 1m: {rsi_1m:.1f}")
                _append_audit_log({
                    "ts": now_utc.isoformat(),
                    "pair": pair,
                    "state": "aligned_pending_breakout",
                    "direction": near_direction,
                    "missing_timeframes": [],
                    "distance": near_distance,
                    "rsi_1h": float(rsi_1h),
                    "rsi_30m": float(rsi_30m),
                    "rsi_15m": float(rsi_15m_val),
                    "rsi_5m": rsi_5m,
                    "rsi_1m": rsi_1m,
                })
            elif not signal_direction and (near_distance <= 4.0 or remaining == 1):
                rsi_5m = micro_context.get("rsi_5m")
                rsi_1m = micro_context.get("rsi_1m")
                print(f"  👀 WATCH MODE: {near_direction} | missing={','.join(missing_timeframes) or '-'} | gap={near_distance:.1f}")
                if isinstance(rsi_5m, float):
                    print(f"     RSI 5m: {rsi_5m:.1f}")
                if isinstance(rsi_1m, float):
                    print(f"     RSI 1m: {rsi_1m:.1f}")
                _append_audit_log({
                    "ts": now_utc.isoformat(),
                    "pair": pair,
                    "state": "watch",
                    "direction": near_direction,
                    "missing_timeframes": missing_timeframes,
                    "distance": near_distance,
                    "rsi_1h": float(rsi_1h),
                    "rsi_30m": float(rsi_30m),
                    "rsi_15m": float(rsi_15m_val),
                    "rsi_5m": rsi_5m,
                    "rsi_1m": rsi_1m,
                })

            if signal_direction:
                print(f"  ⚠️ MTF SIGNAL: {signal_direction} (confidence: {signal_confidence:.0%})")
                print(f"     Reasons: {', '.join(signal_reasons)}")

                # Calculate TP/SL (TP=1×ATR, SL=3×ATR — validated over 360d OOS backtest)
                tp_mult = 1.0
                sl_mult = 3.0
                if atr and atr > 0:
                    if signal_direction == "SELL":
                        entry = close_price
                        tp = entry - (atr * tp_mult)
                        sl = entry + (atr * sl_mult)
                    else:
                        entry = close_price
                        tp = entry + (atr * tp_mult)
                        sl = entry - (atr * sl_mult)
                else:
                    pip_size = 0.0001 if "JPY" not in pair else 0.01
                    if signal_direction == "SELL":
                        entry = close_price
                        tp = entry - (30 * pip_size)
                        sl = entry + (90 * pip_size)
                    else:
                        entry = close_price
                        tp = entry + (30 * pip_size)
                        sl = entry - (90 * pip_size)

                micro_context = _fetch_micro_context(fetcher, symbol, rsi_period)
                exec_note = _execution_note(
                    signal_direction,
                    micro_context.get("rsi_5m") if isinstance(micro_context.get("rsi_5m"), float) else None,
                    micro_context.get("rsi_1m") if isinstance(micro_context.get("rsi_1m"), float) else None,
                )

                print(f"     Entry: {entry:.5f}")
                print(f"     TP: {tp:.5f}")
                print(f"     SL: {sl:.5f}")
                if isinstance(micro_context.get('rsi_5m'), float):
                    print(f"     RSI 5m: {float(micro_context['rsi_5m']):.1f}")
                if isinstance(micro_context.get('rsi_1m'), float):
                    print(f"     RSI 1m: {float(micro_context['rsi_1m']):.1f}")
                print(f"     Execution: {exec_note}")

                # Send Telegram notification
                if notifier and hh and ll:
                    confirmed_pairs.add(pair)
                    entry_fp = (
                        f"entry|{signal_direction}|{round(float(rsi_1h),1)}|{round(float(rsi_30m),1)}|{round(float(rsi_15m_val),1)}"
                    )
                    prev = near_state.get(pair)
                    should_send_entry = not prev or str(prev.get("fingerprint", "")) != entry_fp
                    if should_send_entry:
                        await notifier.send_signal(
                            pair=symbol,
                            direction=signal_direction,
                            rsi_1h=float(rsi_1h),
                            rsi_30m=float(rsi_30m),
                            rsi_15m=float(rsi_15m_val),
                            price=close_price,
                            hh=hh,
                            ll=ll,
                            entry=entry,
                            tp=tp,
                            sl=sl,
                            patterns=candle_patterns,
                            divergence=bullish_div if all_oversold else bearish_div,
                        )
                    near_state[pair] = {"fingerprint": entry_fp, "sent_at": now_ts, "kind": "entry"}
                    cooldown_state[pair] = {"until": now_ts + settings.strategy.cooldown_minutes * 60}
                    _append_audit_log({
                        "ts": now_utc.isoformat(),
                        "pair": pair,
                        "state": "entry",
                        "direction": signal_direction,
                        "confidence": signal_confidence,
                        "reasons": signal_reasons,
                        "entry": entry,
                        "tp": tp,
                        "sl": sl,
                        "rsi_1h": float(rsi_1h),
                        "rsi_30m": float(rsi_30m),
                        "rsi_15m": float(rsi_15m_val),
                        "breakout_buy": breakout_buy,
                        "breakout_sell": breakout_sell,
                    })

        except Exception as exc:
            print(f"  Error: {exc}")

    # Always persist alignment state (independent of notifier)
    _save_alignment_state(alignment_state)

    if near_candidates:
        ranked = sorted(
            near_candidates,
            key=lambda x: (float(x["distance"]), -_priority_for_pair(settings, str(x["pair"]))),
        )
        print("\n[CLOSEST MTF SETUPS]")
        for candidate in ranked[:10]:
            pair = str(candidate["pair"])
            direction = str(candidate["direction"])
            distance = float(candidate["distance"])
            r1 = float(candidate["rsi_1h"])
            r30 = float(candidate["rsi_30m"])
            r15 = float(candidate["rsi_15m"])
            remaining = int(candidate["remaining"])
            missing = ",".join(candidate["missing_timeframes"])
            label = "aligned_pending_breakout" if bool(candidate.get("breakout_pending")) else ("near" if remaining > 0 else "aligned")
            print(
                f"  {pair}: {direction} | state={label} | gap={distance:.1f} RSI points | remaining_tf={remaining} | missing={missing or '-'} | "
                f"1h={r1:.1f} 30m={r30:.1f} 15m={r15:.1f}"
            )

        if notifier and getattr(settings.telegram, "near_setup_notifications", True):
            changed = False
            active_pairs: set[str] = set(confirmed_pairs)
            for candidate in ranked[:3]:
                distance = float(candidate["distance"])
                pair = str(candidate["pair"])
                direction = str(candidate["direction"])
                r1 = float(candidate["rsi_1h"])
                r30 = float(candidate["rsi_30m"])
                r15 = float(candidate["rsi_15m"])
                r5 = candidate.get("rsi_5m")
                r1m = candidate.get("rsi_1m")
                remaining = int(candidate["remaining"])
                missing = list(candidate["missing_timeframes"])
                breakout_pending = bool(candidate.get("breakout_pending"))
                qualifies_near = distance <= 4.0 or remaining == 1 or breakout_pending
                if not qualifies_near:
                    continue
                active_pairs.add(pair)
                state_kind = "aligned_pending_breakout" if breakout_pending else "near"
                fingerprint = (
                    f"{state_kind}|{direction}|{round(r1,1)}|{round(r30,1)}|{round(r15,1)}|{remaining}"
                )
                prev = near_state.get(pair)
                should_send = False
                if not prev:
                    should_send = True
                else:
                    prev_fp = str(prev.get("fingerprint", ""))
                    prev_ts = int(prev.get("sent_at", 0))
                    if prev_fp != fingerprint or now_ts - prev_ts >= 6 * 3600:
                        should_send = True
                if should_send:
                    missing_txt = ", ".join(missing) if missing else "none"
                    micro_lines = []
                    if isinstance(r5, float):
                        micro_lines.append(f"RSI 5m: `{r5:.1f}`")
                    if isinstance(r1m, float):
                        micro_lines.append(f"RSI 1m: `{r1m:.1f}`")
                    micro_txt = ("\n" + "\n".join(micro_lines)) if micro_lines else ""
                    if breakout_pending:
                        message = (
                            f"⏳ *MTF Aligned / Breakout Pending*\n\n"
                            f"Pair: `{pair.replace('/', '')}`\n"
                            f"Bias: `{direction}`\n"
                            f"MTF RSI: `fully aligned`\n"
                            f"Missing: `15m breakout confirmation`\n\n"
                            f"RSI 1h: `{r1:.1f}`\n"
                            f"RSI 30m: `{r30:.1f}`\n"
                            f"RSI 15m: `{r15:.1f}`"
                            f"{micro_txt}"
                        )
                    else:
                        message = (
                            f"👀 *Near MTF Setup*\n\n"
                            f"Pair: `{pair.replace('/', '')}`\n"
                            f"Bias: `{direction}`\n"
                            f"Gap to full alignment: `{distance:.1f}` RSI points\n"
                            f"Timeframes left to confirm: `{remaining}`\n"
                            f"Missing timeframe(s): `{missing_txt}`\n\n"
                            f"RSI 1h: `{r1:.1f}`\n"
                            f"RSI 30m: `{r30:.1f}`\n"
                            f"RSI 15m: `{r15:.1f}`"
                            f"{micro_txt}"
                        )
                    await notifier.send(message)
                    near_state[pair] = {"fingerprint": fingerprint, "sent_at": now_ts, "kind": state_kind}
                    changed = True

            stale_pairs = [pair for pair in list(near_state.keys()) if pair not in active_pairs]
            for pair in stale_pairs:
                prev = near_state.get(pair)
                if not prev:
                    continue
                kind = str(prev.get("kind", "near"))
                if kind == "near":
                    status = "near setup faded before confirmation"
                elif kind == "aligned_pending_breakout":
                    status = "MTF alignment remained/changed but breakout confirmation is no longer pending"
                else:
                    status = "entry condition no longer active"
                await notifier.send(
                    f"❌ *Setup Invalidated*\n\nPair: `{pair.replace('/', '')}`\nStatus: `{status}`"
                )
                near_state.pop(pair, None)
                changed = True

            if changed:
                _save_near_setup_state(near_state)
            _save_cooldown_state(cooldown_state)
            _save_alignment_state(alignment_state)
        elif notifier:
            # Near/setup notifications disabled: clear stale state so no invalidation alerts
            # are emitted later if the feature is re-enabled.
            if near_state:
                near_state.clear()
                _save_near_setup_state(near_state)
            _save_cooldown_state(cooldown_state)
            _save_alignment_state(alignment_state)


def _calculate_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    """Calculate Average True Range."""
    if len(highs) < period or len(lows) < period or len(closes) < period:
        return None

    true_ranges = []
    for i in range(1, len(highs)):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        true_ranges.append(max(tr1, tr2, tr3))

    if len(true_ranges) < period:
        return None

    return sum(true_ranges[-period:]) / period


async def run_analyze(pair: str, timeframe: str) -> None:
    _ = get_settings()
    fetcher = DataFetcher()

    print(f"\n[ANALYZE] {pair} on {timeframe}")

    try:
        mtf_data = fetcher.fetch_multi_timeframe(pair, period="7d")

        for tf_name, data in mtf_data.items():
            if data.empty:
                print(f"  {tf_name}: No data")
                continue

            close = data["close"].values.tolist()
            rsi = calculate_rsi(close[-50:], 14) if len(close) >= 50 else None
            if rsi is None:
                print(f"  {tf_name}: {len(data)} candles, RSI: N/A")
                continue

            print(f"  {tf_name}: {len(data)} candles, latest RSI(14): {rsi:.1f}")
    except Exception as exc:
        print(f"  Error: {exc}")


async def run_news(hours: int) -> None:
    checker = NewsChecker()

    print("\n[NEWS] Fetching upcoming 3-star events...")

    try:
        events = await checker.fetch_events(hours_ahead=hours)

        if not events:
            print(f"  No 3-star events in the next {hours} hours")
            return

        for event in events:
            print(f"  {event.timestamp.strftime('%Y-%m-%d %H:%M')} {event.currency}: {event.name}")
    except Exception as exc:
        print(f"  Error: {exc}")


async def run_backtest(pair: str, start: str | None, end: str | None) -> None:
    _ = get_settings()
    fetcher = DataFetcher()
    strategy = MTFRSIStrategy()
    engine = BacktestEngine(strategy)

    print(f"\n[BACKTEST] {pair}")

    try:
        mtf_data = fetcher.fetch_multi_timeframe(pair, start=start, end=end)

        result = await engine.run(
            pair,
            mtf_data["1h"],
            mtf_data["30m"],
            mtf_data["15m"],
        )

        print(f"  Period: {result.start_date.date()} to {result.end_date.date()}")
        print(f"  Total trades: {result.total_trades}")
        print(f"  Win rate: {result.win_rate:.1%}")
        print(f"  Total PnL: ${result.total_pnl:.2f}")
        print(f"  Max drawdown: {result.max_drawdown:.1%}")
    except Exception as exc:
        print(f"  Error: {exc}")


async def run_enhanced_backtest(
    pair: str,
    start: str | None,
    end: str | None,
    use_patterns: bool = True,
    use_divergence: bool = True,
) -> None:
    """Run enhanced backtest with realistic TP/SL simulation."""
    from src.backtest.enhanced_engine import EnhancedBacktestEngine

    fetcher = DataFetcher()

    print(f"\n[ENHANCED BACKTEST] {pair}")
    print(f"  Patterns: {'enabled' if use_patterns else 'disabled'}")
    print(f"  Divergence: {'enabled' if use_divergence else 'disabled'}")

    try:
        # Fetch 1h data for longer history (15m limited to 60 days on yfinance)
        symbol = pair.replace("/", "").replace("-", "")
        data = fetcher.fetch(symbol, period="2y", interval="1h")

        if data.empty:
            print("  Error: No data available")
            return

        engine = EnhancedBacktestEngine(
            initial_balance=10000.0,
            risk_per_trade=0.02,
            use_patterns=use_patterns,
            use_divergence=use_divergence,
        )

        result = engine.run(pair, data, verbose=False)

        print(f"\n  Period: {result.start_date.date()} to {result.end_date.date()}")
        print(f"  Total trades: {result.total_trades}")
        print(f"  Win rate: {result.win_rate:.1%}")
        print(f"  Total PnL: ${result.total_pnl:.2f} ({result.total_pnl_pct:.2f}%)")
        print(f"  Max drawdown: {result.max_drawdown_pct:.2f}%")
        print(f"  Avg win: ${result.avg_win:.2f}")
        print(f"  Avg loss: ${result.avg_loss:.2f}")
        print(f"  Profit factor: {result.profit_factor:.2f}")
        print(f"  Sharpe ratio: {result.sharpe_ratio:.2f}")

        if use_patterns:
            print(f"\n  Pattern trades: {result.pattern_trades} (win rate: {result.pattern_win_rate:.1%})")
        if use_divergence:
            print(f"  Divergence trades: {result.divergence_trades} (win rate: {result.divergence_win_rate:.1%})")
        if use_patterns and use_divergence:
            print(f"  Combined trades: {result.combined_trades} (win rate: {result.combined_win_rate:.1%})")

    except Exception as exc:
        print(f"  Error: {exc}")


async def run_telegram_poll() -> None:
    settings = get_settings()
    if not settings.telegram.enabled:
        print("Telegram disabled in settings")
        return
    token = settings.telegram.bot_token
    chat_id = settings.telegram.chat_id
    if not token or not chat_id:
        print("Telegram token/chat_id missing")
        return

    from src.notifications.telegram_commands import TelegramCommandHandler

    handler = TelegramCommandHandler(token, chat_id)
    print("[TELEGRAM] Polling commands...")
    await handler.run_forever()


async def run_dashboard(days: int) -> None:
    """Show signal dashboard: entries, block reasons, paper P&L tracking."""
    audit_path = _audit_log_path()
    if not audit_path.exists():
        print("No signal audit log found.")
        return

    cutoff = datetime.now(UTC) - timedelta(days=days)
    entries: list[dict] = []
    blocked: list[dict] = []
    aligned: list[dict] = []
    watched: list[dict] = []

    with audit_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_str = rec.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_str)
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                continue
            state = rec.get("state", "")
            if state == "entry":
                entries.append(rec)
            elif state == "blocked":
                blocked.append(rec)
            elif state == "aligned_pending_breakout":
                aligned.append(rec)
            elif state == "watch":
                watched.append(rec)

    print(f"=== Signal Dashboard (last {days} days) ===\n")

    # Summary counts
    print(f"Entry signals:    {len(entries)}")
    print(f"Blocked signals:  {len(blocked)}")
    print(f"Aligned pending:  {len(aligned)}")
    print(f"Watch list:       {len(watched)}")
    print()

    # Entry signals detail
    if entries:
        print("--- ENTRY SIGNALS ---")
        print(f"{'Timestamp':<22} {'Pair':<10} {'Dir':<5} {'Entry':>10} {'TP':>10} {'SL':>10} {'RSI 1h':>7} {'RSI 30m':>8} {'RSI 15m':>8}")
        print("-" * 95)
        for e in entries:
            print(
                f"{e.get('ts', '')[:19]:<22} {e.get('pair', ''):<10} {e.get('direction', ''):<5} "
                f"{e.get('entry', 0):>10.5f} {e.get('tp', 0):>10.5f} {e.get('sl', 0):>10.5f} "
                f"{e.get('rsi_1h', 0):>7.1f} {e.get('rsi_30m', 0):>8.1f} {e.get('rsi_15m', 0):>8.1f}"
            )

        # Paper P&L estimation using current price
        print("\n--- PAPER P&L (mark-to-market) ---")
        fetcher = DataFetcher()
        pairs_seen = {e.get("pair") for e in entries}
        current_prices: dict[str, float] = {}
        for pair in pairs_seen:
            if not pair:
                continue
            try:
                symbol = pair.replace("/", "")
                df = fetcher.fetch(symbol, period="1d", interval="15m")
                if not df.empty:
                    current_prices[pair] = float(df["close"].iloc[-1])
            except Exception:
                pass

        total_paper_pnl = 0.0
        print(f"{'Timestamp':<22} {'Pair':<10} {'Dir':<5} {'Entry':>10} {'Current':>10} {'P&L pips':>10} {'Status':>10}")
        print("-" * 82)
        for e in entries:
            pair = e.get("pair", "")
            direction = e.get("direction", "")
            entry_px = float(e.get("entry", 0))
            tp_px = float(e.get("tp", 0))
            sl_px = float(e.get("sl", 0))
            pip_size = 0.01 if "JPY" in pair else 0.0001
            current = current_prices.get(pair)

            if current is None:
                print(f"{e.get('ts', '')[:19]:<22} {pair:<10} {direction:<5} {entry_px:>10.5f} {'N/A':>10} {'N/A':>10} {'no data':>10}")
                continue

            if direction == "BUY":
                pnl_pips = (current - entry_px) / pip_size
                hit_tp = current >= tp_px
                hit_sl = current <= sl_px
            else:
                pnl_pips = (entry_px - current) / pip_size
                hit_tp = current <= tp_px
                hit_sl = current >= sl_px

            status = "TP HIT" if hit_tp else ("SL HIT" if hit_sl else "OPEN")
            total_paper_pnl += pnl_pips
            print(
                f"{e.get('ts', '')[:19]:<22} {pair:<10} {direction:<5} "
                f"{entry_px:>10.5f} {current:>10.5f} {pnl_pips:>+10.1f} {status:>10}"
            )
        print(f"\nTotal paper P&L: {total_paper_pnl:+.1f} pips")
    else:
        print("No entry signals in this period.")

    # Block reason breakdown
    if blocked:
        print("\n--- BLOCK REASONS ---")
        reason_counts: dict[str, int] = {}
        for b in blocked:
            for reason in b.get("reasons", []):
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1])[:15]:
            print(f"  {count:>4}x  {reason}")

    # Pairs with most aligned-pending (closest to triggering)
    if aligned:
        print("\n--- MOST ACTIVE PAIRS (aligned pending breakout) ---")
        pair_counts: dict[str, int] = {}
        for a in aligned:
            p = a.get("pair", "unknown")
            pair_counts[p] = pair_counts.get(p, 0) + 1
        for pair, count in sorted(pair_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"  {count:>4}x  {pair}")

    print()


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    handlers = {
        "scan": lambda: run_scan(_parse_pairs(args.pairs), args.timeframe),
        "analyze": lambda: run_analyze(args.pair, args.timeframe),
        "news": lambda: run_news(args.hours),
        "backtest": lambda: run_backtest(args.pair, args.start, args.end),
        "backtest-enhanced": lambda: run_enhanced_backtest(
            args.pair, args.start, args.end,
            use_patterns=not args.no_patterns,
            use_divergence=not args.no_divergence,
        ),
        "telegram-poll": run_telegram_poll,
        "dashboard": lambda: run_dashboard(args.days),
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    asyncio.run(handler())
    return 0


if __name__ == "__main__":
    sys.exit(main())
