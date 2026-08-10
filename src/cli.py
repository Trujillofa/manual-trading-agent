"""CLI entry point for manual trading agent."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from typing import Literal, TypedDict, cast

from src.config import get_settings
from src.dashboard.log_status import run_logs_status as _logs_status_run
from src.dashboard.report import run_dashboard as _dashboard_run
from src.dashboard.report import run_healthcheck as _healthcheck_run
from src.data.fetcher import DataFetcher
from src.evaluation.branch_b_audit import record_branch_b_scan_decision_signal
from src.indicators.adx import calculate_adx_full
from src.indicators.atr import calculate_atr
from src.indicators.candlestick import (
    CandlePattern,
    PatternType,
    detect_patterns,
)
from src.indicators.ema import (
    EMACrossover,
    EMACrossoverType,
    EMAPriceTouch,
    EMASlope,
    EMASlopeDirection,
    calculate_ema,
    detect_crossover,
    detect_price_cross,
    detect_price_touch,
    detect_slope,
)
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
)
from src.indicators.sma import calculate_sma
from src.news.news_checker import NewsChecker
from src.notifications.digest import (
    EmaCandidate,
    EmaSignalEntry,
    SetupCandidate,
    build_setup_digest_message,
    digest_fingerprint,
    should_send_digest,
)
from src.notifications.telegram import TelegramNotifier
from src.scanner.evaluator import evaluate_entry
from src.scanner.gates import (
    ADX_TREND_THRESHOLD,
    MicroContext,
    SpreadQuote,
    _check_breakout_with_profile,
    _execution_note,
    _fetch_micro_context,
    _get_confirmation_profile,
    _get_ctrader_spread,
    _get_pair_param,
    _get_static_spread_quote,
    _mtf_distance_to_buy,
    _mtf_distance_to_sell,
    _priority_for_pair,
    _profile_label,
    _session_allowed,
)
from src.scanner.state import (
    INVALIDATION_MISS_THRESHOLD,
    _append_audit_log,
    _check_trade_outcome,
    _load_active_signal_state,
    _load_alignment_state,
    _load_ema_near_state,
    _load_near_setup_state,
    _load_pending_trades,
    _load_setup_digest_state,
    _save_active_signal_state,
    _save_alignment_state,
    _save_ema_near_state,
    _save_near_setup_state,
    _save_pending_trades,
    _save_setup_digest_state,
)
from src.scanner.telemetry import _build_scan_telemetry_payload


def _ema_standalone_fingerprint(sig_type: str, data: object, pair: str) -> str:
    """Build anti-flicker fingerprint for a standalone EMA notification.

    Crossovers use fast/slow periods (EMACrossover has no ``ema_period``).
    """
    if isinstance(data, EMACrossover):
        period_str = f"{data.fast_period}/{data.slow_period}"
        tf_str = data.timeframe
        dir_str = data.crossover_type.value
    elif isinstance(data, EMAPriceTouch):
        period_str = str(data.ema_period)
        tf_str = data.timeframe
        dir_str = str(data.direction)
    elif isinstance(data, EMASlope):
        period_str = str(data.period)
        tf_str = data.timeframe
        dir_str = data.slope_direction.value
    else:
        period_str = ""
        tf_str = str(getattr(data, "timeframe", "") or "")
        dir_str = ""
    return f"ema_{sig_type}_{tf_str}_{pair}_{period_str}_{dir_str}"


def _filter_standalone_ema_signals(
    signals: list[EmaSignalEntry],
    *,
    allowed_types: list[str],
    allowed_timeframes: list[str],
) -> list[EmaSignalEntry]:
    """Keep only standalone-eligible EMA signals by type and timeframe."""
    type_set = set(allowed_types)
    tf_set = set(allowed_timeframes)
    filtered: list[EmaSignalEntry] = []
    for sig in signals:
        if str(sig.get("type", "")) not in type_set:
            continue
        data = sig["data"]
        tf = getattr(data, "timeframe", "") or ""
        if tf not in tf_set:
            continue
        filtered.append(sig)
    return filtered


class NearCandidate(TypedDict):
    pair: str
    direction: str
    distance: float
    remaining: int
    missing_timeframes: list[str]
    aligned: bool
    breakout_pending: bool
    rsi_1h: float
    rsi_30m: float
    rsi_15m: float
    rsi_5m: float | None
    rsi_1m: float | None
    rsi_ma_1h: float | None
    rsi_ma_30m: float | None
    rsi_ma_15m: float | None
    patterns: list[str]
    bullish_div: float | None
    bearish_div: float | None
    price: float
    no_trade_reasons: list[str]


def _parse_pairs(raw_pairs: str | None) -> list[str] | None:
    if raw_pairs is None:
        return None

    pairs = [pair.strip().upper() for pair in raw_pairs.split(",") if pair.strip()]
    return pairs or None


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

    enhanced_backtest_parser = subparsers.add_parser(
        "backtest-enhanced", help="Run enhanced backtest with TP/SL"
    )
    enhanced_backtest_parser.add_argument(
        "--pair", required=True, help="Pair to backtest (e.g., EUR/USD)"
    )
    enhanced_backtest_parser.add_argument("--start", help="Start date YYYY-MM-DD")
    enhanced_backtest_parser.add_argument("--end", help="End date YYYY-MM-DD")
    enhanced_backtest_parser.add_argument(
        "--no-patterns", action="store_true", help="Disable pattern detection"
    )
    enhanced_backtest_parser.add_argument(
        "--no-divergence", action="store_true", help="Disable divergence detection"
    )
    enhanced_backtest_parser.add_argument(
        "--rsi-ma", action="store_true", help="Enable RSI-MA curl gate (momentum confirmation)"
    )
    enhanced_backtest_parser.add_argument(
        "--rsi-ma-period", type=int, default=5, help="RSI-MA lookback period (default: 5)"
    )
    enhanced_backtest_parser.add_argument(
        "--rsi-ma-variant",
        choices=["curl", "fresh", "slope", "distance", "confidence", "conditional", "gate"],
        default="curl",
        help="RSI-MA variant: curl (cross back), fresh (not exhausted), slope (inflection), distance (momentum threshold), confidence (modifier not gate), conditional (low-conf only)",
    )
    enhanced_backtest_parser.add_argument(
        "--rsi-ma-distance-max",
        type=float,
        default=15.0,
        help="Max RSI-to-MA distance for distance variant (default: 15)",
    )
    enhanced_backtest_parser.add_argument(
        "--rsi-ma-confidence-mod",
        type=float,
        default=0.85,
        help="Confidence multiplier when no curl detected for confidence variant (default: 0.85)",
    )
    enhanced_backtest_parser.add_argument(
        "--ema-confidence",
        action="store_true",
        help="Enable EMA trend-alignment confidence modifier (boost when price agrees with EMA-ref trend, dampen when counter)",
    )
    enhanced_backtest_parser.add_argument(
        "--ema-confidence-ref",
        type=int,
        default=200,
        help="EMA period for trend reference (default: 200)",
    )
    enhanced_backtest_parser.add_argument(
        "--ema-confidence-boost",
        type=float,
        default=1.10,
        help="Confidence multiplier when trend-aligned (default: 1.10)",
    )
    enhanced_backtest_parser.add_argument(
        "--ema-confidence-dampen",
        type=float,
        default=0.85,
        help="Confidence multiplier when trend-counter (default: 0.85)",
    )

    subparsers.add_parser("telegram-poll", help="Poll Telegram commands (e.g. /watchlist)")
    subparsers.add_parser("healthcheck", help="Check scanner and Telegram runtime health")

    logs_status_parser = subparsers.add_parser(
        "logs-status",
        help="Report managed log sizes vs rotation threshold",
    )
    logs_status_parser.add_argument(
        "--notify",
        action="store_true",
        help="Send Telegram alerts when warn/critical thresholds are newly crossed",
    )

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
    shadow = list(getattr(settings.trading, "shadow", []))
    shadow_set = set(shadow)
    selected_pairs = pairs or (majors + minors + shadow)

    rsi_overbought = settings.strategy.rsi_overbought
    rsi_oversold = settings.strategy.rsi_oversold
    rsi_period = int(settings.strategy.rsi_period)
    lookback = int(settings.strategy.lookback_bars)

    print(f"\n[SCAN] Scanning {len(selected_pairs)} pairs (MTF: 1h + 30m + 15m)...")
    near_candidates: list[NearCandidate] = []
    near_state = _load_near_setup_state() if notifier else {}
    active_signal_state = _load_active_signal_state()
    alignment_state = _load_alignment_state()
    pending_trades = _load_pending_trades()
    now_ts = int(time.time())
    now_utc = datetime.now(UTC)
    scan_run_id = now_utc.isoformat()
    confirmed_pairs: set[str] = set()
    ema_near_state = _load_ema_near_state() if notifier else {}
    ema_candidates: list[EmaCandidate] = []
    # Expire trades older than 48 hours
    pending_trades = [t for t in pending_trades if now_ts - int(t.get("fired_at", 0)) < 48 * 3600]

    for pair in selected_pairs:
        is_shadow = pair in shadow_set
        telemetry_state = "data_unavailable"
        telemetry_direction: str | None = None
        telemetry_reasons: list[str] = []
        telemetry_aligned = False
        telemetry_breakout_pending = False
        telemetry_entry_triggered = False
        bars_aligned: int | None = None
        confirm_bars: int | None = None
        # ATR/TP/SL from evaluator (for durable verification in blocked/pending audit rows)
        atr_val: float | None = None
        tp_val: float | None = None
        sl_val: float | None = None
        entry_val: float | None = None
        within_confirm_window: bool | None = None
        spread_pips: float | None = None
        max_spread_for_pair: float | None = None
        spread_source: str | None = None
        adx_1h: float | None = None
        is_ranging: bool | None = None
        rsi_1h: float | None = None
        rsi_30m: float | None = None
        rsi_15m_val: float | None = None
        entry_signal_id: str | None = None
        try:
            # Fetch multi-timeframe data (extended windows for EMA-200 support)
            symbol = pair.replace("/", "")
            # Widen windows when EMA alerts OR the EMA confidence modifier is on
            # (EMA-200 on 1h needs >=200 bars).
            ema_enabled = (
                settings.strategy.ema.enabled or settings.strategy.ema.confidence_modifier_enabled
            )
            period_1h = "30d" if ema_enabled else "5d"
            period_30m = "7d" if ema_enabled else "3d"
            period_15m = "4d" if ema_enabled else "2d"
            data_1h = fetcher.fetch(symbol, period=period_1h, interval="1h")
            data_30m = fetcher.fetch(symbol, period=period_30m, interval="30m")
            data_15m = fetcher.fetch(symbol, period=period_15m, interval="15m")

            if data_1h.empty or data_30m.empty or data_15m.empty:
                telemetry_reasons = ["timeframe data unavailable"]
                _append_audit_log(
                    _build_scan_telemetry_payload(
                        ts=now_utc.isoformat(),
                        scan_run_id=scan_run_id,
                        pair=pair,
                        state=telemetry_state,
                        direction=telemetry_direction,
                        aligned=telemetry_aligned,
                        breakout_pending=telemetry_breakout_pending,
                        entry_triggered=telemetry_entry_triggered,
                        bars_aligned=bars_aligned,
                        confirm_bars=confirm_bars,
                        atr=atr_val,
                        tp=tp_val,
                        sl=sl_val,
                        computed_entry=entry_val,
                        within_confirm_window=within_confirm_window,
                        spread_pips=spread_pips,
                        max_spread_pips=max_spread_for_pair,
                        spread_source=spread_source,
                        adx_1h=adx_1h,
                        is_ranging=is_ranging,
                        rsi_1h=rsi_1h,
                        rsi_30m=rsi_30m,
                        rsi_15m=rsi_15m_val,
                        no_trade_reasons=telemetry_reasons,
                    )
                )
                continue

            # Extract price data from 15m (primary timeframe)
            close_15m = data_15m["close"].values.tolist()
            high_15m = data_15m["high"].values.tolist()
            low_15m = data_15m["low"].values.tolist()
            open_15m = data_15m["open"].values.tolist() if "open" in data_15m else close_15m
            bar_times_unix = [int(ts.timestamp()) for ts in data_15m.index]

            # Check pending trade outcomes for this pair
            pair_pending = [t for t in pending_trades if t.get("pair") == pair]
            for trade in pair_pending:
                outcome = _check_trade_outcome(trade, bar_times_unix, high_15m, low_15m)
                if outcome:
                    pending_trades = [t for t in pending_trades if t is not trade]
                    pip_mult = 100.0 if "JPY" in pair else 10000.0
                    tp_pips = abs(float(trade["tp"]) - float(trade["entry"])) * pip_mult
                    sl_pips = abs(float(trade["sl"]) - float(trade["entry"])) * pip_mult
                    result_pips = tp_pips if outcome == "tp" else -sl_pips
                    bars_held = sum(1 for ts in bar_times_unix if ts > int(trade["fired_at"]))
                    _append_audit_log(
                        {
                            "ts": now_utc.isoformat(),
                            "pair": pair,
                            "state": "outcome",
                            "direction": trade["direction"],
                            "outcome": outcome,
                            "entry": trade["entry"],
                            "tp": trade["tp"],
                            "sl": trade["sl"],
                            "result_pips": result_pips,
                            "bars_held": bars_held,
                            "signal_id": trade.get("signal_id"),
                        }
                    )
                    if notifier and pair not in shadow_set:
                        await notifier.send_trade_outcome(
                            pair=symbol,
                            direction=str(trade["direction"]),
                            entry=float(trade["entry"]),
                            tp=float(trade["tp"]),
                            sl=float(trade["sl"]),
                            outcome=outcome,
                            tp_pips=tp_pips,
                            sl_pips=sl_pips,
                            bars_held=bars_held,
                        )

            # Calculate RSI for each timeframe
            close_1h_list = data_1h["close"].values.tolist()
            close_30m_list = data_30m["close"].values.tolist()
            rsi_1h = calculate_rsi(close_1h_list[-50:], rsi_period)
            rsi_30m = calculate_rsi(close_30m_list[-50:], rsi_period)
            rsi_15m_val = calculate_rsi(close_15m[-50:], rsi_period)

            # RSI-MA series for all three TFs (gate + curl confidence modifier)
            rsi_ma_period = settings.strategy.rsi_ma_gate_period
            rsi_series_1h = calculate_rsi_series(close_1h_list, rsi_period)
            rsi_ma_1h = calculate_rsi_ma_series(
                [float(v) if v is not None else None for v in rsi_series_1h],
                ma_period=rsi_ma_period,
            )
            rsi_series_30m = calculate_rsi_series(close_30m_list, rsi_period)
            rsi_ma_30m = calculate_rsi_ma_series(
                [float(v) if v is not None else None for v in rsi_series_30m],
                ma_period=rsi_ma_period,
            )

            # Calculate SMA for each timeframe (used for trend-alignment gate)
            close_1h_list = data_1h["close"].values.tolist()
            close_30m_list = data_30m["close"].values.tolist()
            sma_period = int(_get_pair_param(pair, "sma_period", settings.strategy.sma_period))
            sma_1h = calculate_sma(close_1h_list, sma_period)
            sma_30m = calculate_sma(close_30m_list, sma_period)
            sma_15m = calculate_sma(close_15m, sma_period)
            close_1h_last = close_1h_list[-1] if close_1h_list else None
            close_30m_last = close_30m_list[-1] if close_30m_list else None

            # 20-bar HH/LL from 15m — prior-bar semantics so `close > hh`
            # / `close < ll` can fire. Including the current bar makes
            # close-based breakout impossible by OHLC construction.
            hh = previous_rolling_highest_high(high_15m, lookback, len(high_15m) - 1)
            ll = previous_rolling_lowest_low(low_15m, lookback, len(low_15m) - 1)
            close_price = close_15m[-1]

            # Calculate ATR for TP/SL
            # Use period+1 bars so the shared calculate_atr (fixed) can produce a full ATR(14).
            atr = calculate_atr(high_15m[-(14 + 1) :], low_15m[-(14 + 1) :], close_15m[-(14 + 1) :])
            pip_size = 0.01 if "JPY" in pair else 0.0001
            bar_high = high_15m[-1] if high_15m else None
            bar_low = low_15m[-1] if low_15m else None
            profile = _get_confirmation_profile(pair)

            breakout_buy = _check_breakout_with_profile(
                profile, "BUY", close_price, hh, ll, pip_size, bar_high, bar_low
            )
            breakout_sell = _check_breakout_with_profile(
                profile, "SELL", close_price, hh, ll, pip_size, bar_high, bar_low
            )

            # ADX trend filter on 1h timeframe — also capture +DI/-DI for signal context
            adx_1h_full = calculate_adx_full(
                data_1h["high"].values.tolist()[-50:],
                data_1h["low"].values.tolist()[-50:],
                data_1h["close"].values.tolist()[-50:],
            )
            adx_1h = adx_1h_full[0] if adx_1h_full else None
            plus_di_1h: float | None = adx_1h_full[1] if adx_1h_full else None
            minus_di_1h: float | None = adx_1h_full[2] if adx_1h_full else None
            is_ranging = adx_1h is not None and adx_1h < ADX_TREND_THRESHOLD
            # Spread: requires real bid/ask source (OANDA)
            quote: SpreadQuote | None = None
            try:
                from src.data.fetcher import OandaFetcher

                if os.getenv("OANDA_API_KEY") and os.getenv("OANDA_ACCOUNT_ID"):
                    quote = cast(
                        SpreadQuote | None,
                        OandaFetcher(
                            api_key=os.getenv("OANDA_API_KEY"),
                            account_id=os.getenv("OANDA_ACCOUNT_ID"),
                            practice=True,
                        ).get_bid_ask_quote(pair),
                    )
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

            spread_ok = True
            pair_spread_limits = getattr(settings.strategy, "spread_limits_pips", {}) or {}
            max_spread_for_pair = float(
                pair_spread_limits.get(pair, settings.strategy.max_spread_pips)
            )
            if quote and isinstance(quote.get("spread"), float):
                spread_value = cast(float, quote.get("spread"))
                spread_pips = float(spread_value) / pip_size
                spread_source = str(quote.get("source", "live"))
                spread_ok = spread_pips <= max_spread_for_pair
            elif settings.strategy.spread_filter_enabled:
                spread_ok = False

            # Compute news_blocked once per pair (using the scan-level news_checker) to inject into pure evaluator
            news_blocked = False
            if settings.news.enabled:
                try:
                    news_blocked = news_checker.is_blocked(pair, now_utc)
                except Exception:
                    news_blocked = False

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
            rsi_ma_15m = calculate_rsi_ma_series(
                [float(v) if v is not None else None for v in rsi_series],
                ma_period=rsi_ma_period,
            )
            bullish_div = detect_bullish_divergence(close_15m[-100:], rsi_series[-100:], lookback=5)
            bearish_div = detect_bearish_divergence(close_15m[-100:], rsi_series[-100:], lookback=5)

            bullish_pats = [p for p in candle_patterns if p.pattern_type == PatternType.BULLISH]
            bearish_pats = [p for p in candle_patterns if p.pattern_type == PatternType.BEARISH]

            print(f"\n{pair}:")
            print(f"  Price: {close_price:.5f}")
            print(f"  RSI 1h: {rsi_1h:.1f}" if rsi_1h else "  RSI 1h: N/A")
            print(f"  RSI 30m: {rsi_30m:.1f}" if rsi_30m else "  RSI 30m: N/A")
            print(f"  RSI 15m: {rsi_15m_val:.1f}" if rsi_15m_val else "  RSI 15m: N/A")
            if sma_1h is not None and close_1h_last is not None:
                pos_1h = "above" if close_1h_last > sma_1h else "below"
                print(f"  SMA({sma_period}) 1h: {sma_1h:.5f} (price {pos_1h})")
            else:
                print(f"  SMA({sma_period}) 1h: N/A")
            if sma_30m is not None and close_30m_last is not None:
                pos_30m = "above" if close_30m_last > sma_30m else "below"
                print(f"  SMA({sma_period}) 30m: {sma_30m:.5f} (price {pos_30m})")
            else:
                print(f"  SMA({sma_period}) 30m: N/A")
            if sma_15m is not None:
                pos_15m = "above" if close_price > sma_15m else "below"
                print(f"  SMA({sma_period}) 15m: {sma_15m:.5f} (price {pos_15m})")
            else:
                print(f"  SMA({sma_period}) 15m: N/A")
            print(f"  20-bar HH: {hh:.5f}" if hh else "  20-bar HH: N/A")
            print(f"  20-bar LL: {ll:.5f}" if ll else "  20-bar LL: N/A")
            print(f"  ATR(14): {atr:.5f}" if atr else "  ATR(14): N/A (will fallback)")
            print(f"  Confirmation profile: {_profile_label(profile)}")
            print(f"  Breakout BUY(low): {'yes' if breakout_buy else 'no'}")
            print(f"  Breakout SELL(high): {'yes' if breakout_sell else 'no'}")
            if spread_pips is not None:
                spread_source = quote.get("source", "live") if quote is not None else "unknown"
                print(
                    f"  Spread: {spread_pips:.2f} pips ({'ok' if spread_ok else 'too wide'}, max={max_spread_for_pair:.2f}, source={spread_source})"
                )
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

            # ── EMA Computation (only when enabled in config) ──
            ema_signals_this_pair: list[EmaSignalEntry] = []
            ema_ctx_parts: list[str] = []
            if settings.strategy.ema.enabled:
                ema_cfg = settings.strategy.ema
                close_by_tf = {"1h": close_1h_list, "30m": close_30m_list, "15m": close_15m}
                ema_periods = {
                    "9": ema_cfg.fast_period,
                    "21": ema_cfg.slow_period,
                    "50": ema_cfg.medium_period,
                    "200": ema_cfg.long_period,
                }

                # Compute all EMA series: 4 periods × 3 timeframes
                ema_series: dict[str, dict[str, list[float | None]]] = {}
                for tf_name, tf_data in [("1h", data_1h), ("30m", data_30m), ("15m", data_15m)]:
                    tf_close = tf_data["close"].values.tolist()
                    ema_series[tf_name] = {}
                    for label, period in ema_periods.items():
                        ema_series[tf_name][label] = calculate_ema(tf_close, period)

                # Detection passes per timeframe
                for tf in ("15m", "30m", "1h"):
                    ed = ema_series[tf]
                    fast = ed["9"]
                    slow = ed["21"]
                    med = ed["50"]
                    long_ema = ed["200"]
                    tf_close_vals = close_by_tf[tf]
                    current_price_tf = tf_close_vals[-1] if tf_close_vals else close_price
                    prev_price_tf = tf_close_vals[-2] if len(tf_close_vals) >= 2 else None

                    # 1) Fast/slow EMA crossover
                    if ema_cfg.crossover_enabled:
                        crossover = detect_crossover(
                            fast, slow, tf, ema_cfg.fast_period, ema_cfg.slow_period
                        )
                        if (
                            crossover is not None
                            and crossover.crossover_type != EMACrossoverType.NO_CROSS
                        ):
                            ema_signals_this_pair.append(
                                {
                                    "type": "crossover",
                                    "data": crossover,
                                    "pair": pair,
                                }
                            )
                            label = (
                                "GC"
                                if crossover.crossover_type == EMACrossoverType.GOLDEN_CROSS
                                else "DC"
                            )
                            ema_ctx_parts.append(
                                f"EMA{crossover.fast_period}/{crossover.slow_period} {label} {tf}"
                            )

                    # 2) Price vs EMA touch
                    if ema_cfg.price_touch_enabled:
                        for ema_label, ema_series_vals in [("50", med), ("200", long_ema)]:
                            touch = detect_price_touch(
                                current_price_tf,
                                ema_series_vals,
                                int(ema_label),
                                tf,
                                ema_cfg.touch_threshold_pips,
                                pip_size,
                            )
                            if touch is not None:
                                ema_signals_this_pair.append(
                                    {
                                        "type": "price_touch",
                                        "data": touch,
                                        "pair": pair,
                                    }
                                )
                                emoji = (
                                    "🟢" if touch.direction in ("cross_above", "above") else "🔴"
                                )
                                ema_ctx_parts.append(
                                    f"{emoji} EMA{touch.ema_period} {touch.direction} {tf}"
                                )

                            cross = detect_price_cross(
                                current_price_tf,
                                prev_price_tf,
                                ema_series_vals,
                                int(ema_label),
                                tf,
                                ema_cfg.touch_threshold_pips,
                                pip_size,
                            )
                            if cross is not None:
                                ema_signals_this_pair.append(
                                    {
                                        "type": "price_touch",
                                        "data": cross,
                                        "pair": pair,
                                    }
                                )

                    # 3) EMA slope
                    if ema_cfg.slope_enabled:
                        for ema_label, ema_series_vals in [("9", fast), ("21", slow)]:
                            slope = detect_slope(ema_series_vals, int(ema_label), tf)
                            if (
                                slope is not None
                                and slope.slope_direction != EMASlopeDirection.FLAT
                            ):
                                ema_signals_this_pair.append(
                                    {
                                        "type": "slope",
                                        "data": slope,
                                        "pair": pair,
                                    }
                                )
                                arrow = (
                                    "↑"
                                    if slope.slope_direction == EMASlopeDirection.RISING
                                    else "↓"
                                )
                                ema_ctx_parts.append(f"EMA{slope.period} {arrow} {tf}")

            ema_context_str: str | None = None
            if ema_ctx_parts:
                ema_context_str = "📊 " + " | ".join(ema_ctx_parts[:4])

            # Check MTF RSI alignment
            if rsi_1h is None or rsi_30m is None or rsi_15m_val is None:
                telemetry_reasons = ["rsi unavailable"]
                _append_audit_log(
                    _build_scan_telemetry_payload(
                        ts=now_utc.isoformat(),
                        scan_run_id=scan_run_id,
                        pair=pair,
                        state=telemetry_state,
                        direction=telemetry_direction,
                        aligned=telemetry_aligned,
                        breakout_pending=telemetry_breakout_pending,
                        entry_triggered=telemetry_entry_triggered,
                        bars_aligned=bars_aligned,
                        confirm_bars=confirm_bars,
                        atr=atr_val,
                        tp=tp_val,
                        sl=sl_val,
                        computed_entry=entry_val,
                        within_confirm_window=within_confirm_window,
                        spread_pips=spread_pips,
                        max_spread_pips=max_spread_for_pair,
                        spread_source=spread_source,
                        adx_1h=adx_1h,
                        is_ranging=is_ranging,
                        rsi_1h=rsi_1h,
                        rsi_30m=rsi_30m,
                        rsi_15m=rsi_15m_val,
                        no_trade_reasons=telemetry_reasons,
                    )
                )
                continue

            all_oversold = (
                rsi_1h < rsi_oversold and rsi_30m < rsi_oversold and rsi_15m_val < rsi_oversold
            )
            all_overbought = (
                rsi_1h > rsi_overbought
                and rsi_30m > rsi_overbought
                and rsi_15m_val > rsi_overbought
            )

            buy_distance = _mtf_distance_to_buy(
                float(rsi_1h), float(rsi_30m), float(rsi_15m_val), float(rsi_oversold)
            )
            sell_distance = _mtf_distance_to_sell(
                float(rsi_1h), float(rsi_30m), float(rsi_15m_val), float(rsi_overbought)
            )
            near_direction = "BUY" if buy_distance <= sell_distance else "SELL"
            near_distance = min(buy_distance, sell_distance)
            if near_direction == "BUY":
                missing_timeframes = [
                    tf
                    for tf, value in [
                        ("1h", float(rsi_1h)),
                        ("30m", float(rsi_30m)),
                        ("15m", float(rsi_15m_val)),
                    ]
                    if value >= float(rsi_oversold)
                ]
            else:
                missing_timeframes = [
                    tf
                    for tf, value in [
                        ("1h", float(rsi_1h)),
                        ("30m", float(rsi_30m)),
                        ("15m", float(rsi_15m_val)),
                    ]
                    if value <= float(rsi_overbought)
                ]
            remaining = len(missing_timeframes)

            micro_context: MicroContext = {"rsi_5m": None, "rsi_1m": None, "execution_note": None}

            if remaining == 1 or near_distance <= 4.0:
                micro_context = _fetch_micro_context(fetcher, symbol, rsi_period)

            aligned = bool(all_oversold or all_overbought)
            telemetry_aligned = aligned

            # Track alignment age for confirm_bars window
            confirm_bars = profile["confirm_bars"]
            if aligned:
                prev_align = alignment_state.get(pair)
                if prev_align and str(prev_align.get("direction", "")) == near_direction:
                    bars_aligned = prev_align.get("bars", 0) + 1
                else:
                    bars_aligned = 0
                alignment_state[pair] = {"direction": near_direction, "bars": bars_aligned}
            else:
                alignment_state.pop(pair, None)
                bars_aligned = 0

            # For confirm_bars > 0, allow breakout within N bars of first alignment
            within_confirm_window = aligned and bars_aligned <= confirm_bars
            breakout_confirmed = (near_direction == "BUY" and breakout_buy) or (
                near_direction == "SELL" and breakout_sell
            )
            breakout_pending = aligned and not breakout_confirmed
            # If breakout happened but outside the confirmation window, treat as pending
            # BUT only if still aligned — don't flag pending when RSI has drifted away
            if breakout_confirmed and confirm_bars > 0 and not within_confirm_window and aligned:
                breakout_confirmed = False
                breakout_pending = True

            # Single authoritative call to the pure evaluator (R2 unification).
            # All MTF/RSI-MA/breakout/gate/Rule C/ATR TP/SL logic lives in evaluate_entry now.
            # We removed the prior ~110 lines of duplicate signal_direction + RSI-MA computation that
            # ran in parallel and were then overridden (fragile + wasted work + source of drift).
            # The evaluator is called exactly once per pair per scan.
            signal_direction: Literal["BUY", "SELL", None] = None
            signal_confidence = 0.0
            signal_reasons: list[str] = []
            no_trade_reasons: list[str] = []

            decision = evaluate_entry(
                pair,
                data_1h,
                data_30m,
                data_15m,
                active_signal_state=active_signal_state,
                alignment_state=alignment_state,
                now_utc=now_utc,
                spread_quote=quote,
                news_blocked=news_blocked,
                spread_filter_enabled=settings.strategy.spread_filter_enabled,
                bars_aligned=bars_aligned,
                overrides=None,  # cli always uses live settings.yaml (research harness uses overrides for param search)
            )
            # Drive from evaluator (it returns direction even for !fired blocked cases, so telemetry can show
            # the candidate + full no_trade_reasons list from all gates).
            signal_direction = decision.get("direction")
            signal_confidence = decision.get("confidence", 0.0)
            signal_reasons = decision.get("reasons", [])
            no_trade_reasons = decision.get("no_trade_reasons", [])

            # For durable paper-shadow / ATR verification in audit (even for blocked/aligned_pending)
            # These are computed by the evaluator for any candidate (using ATR path post-fix).
            atr_val = decision.get("atr")
            tp_val = decision.get("tp")
            sl_val = decision.get("sl")
            entry_val = decision.get("entry")

            # Re-arm side-effect for Rule C state: only when the evaluator ACTUALLY fires a new
            # same-direction signal (fired == not blocked) does it mean the prior active was
            # invalidated. Gating on `fired` (not just `direction`) avoids erasing the active record
            # for a same-direction candidate that the evaluator is itself suppressing via Rule C.
            prev_active = active_signal_state.get(pair)
            if (
                decision.get("fired")
                and prev_active
                and prev_active.get("direction") == signal_direction
            ):
                active_signal_state.pop(pair, None)
                print("  ♻️  Re-armed (prior same-direction signal cleared per evaluator)")

            if signal_direction and no_trade_reasons:
                # no_trade_reasons (including all final gates) already authoritative from the evaluator override above.
                telemetry_state = "blocked"
                telemetry_direction = signal_direction
                telemetry_reasons = list(no_trade_reasons)
                print(f"  🚫 NO TRADE: {', '.join(no_trade_reasons)}")
                _append_audit_log(
                    {
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
                    }
                )
                signal_direction = None

            if not signal_direction and breakout_pending:
                telemetry_state = "aligned_pending_breakout"
                telemetry_direction = near_direction
                rsi_5m = micro_context.get("rsi_5m")
                rsi_1m = micro_context.get("rsi_1m")
                print(f"  ⏳ ALIGNED / BREAKOUT PENDING: {near_direction}")
                print("     Missing: breakout confirmation only")
                if isinstance(rsi_5m, float):
                    print(f"     RSI 5m: {rsi_5m:.1f}")
                if isinstance(rsi_1m, float):
                    print(f"     RSI 1m: {rsi_1m:.1f}")
                _append_audit_log(
                    {
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
                    }
                )
            elif not signal_direction and (near_distance <= 4.0 or remaining == 1):
                telemetry_state = "watch"
                telemetry_direction = near_direction
                rsi_5m = micro_context.get("rsi_5m")
                rsi_1m = micro_context.get("rsi_1m")
                print(
                    f"  👀 WATCH MODE: {near_direction} | missing={','.join(missing_timeframes) or '-'} | gap={near_distance:.1f}"
                )
                if isinstance(rsi_5m, float):
                    print(f"     RSI 5m: {rsi_5m:.1f}")
                if isinstance(rsi_1m, float):
                    print(f"     RSI 1m: {rsi_1m:.1f}")
                _append_audit_log(
                    {
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
                    }
                )

            if signal_direction:
                telemetry_state = "entry"
                telemetry_direction = signal_direction
                telemetry_entry_triggered = True
                print(f"  ⚠️ MTF SIGNAL: {signal_direction} (confidence: {signal_confidence:.0%})")
                print(f"     Reasons: {', '.join(signal_reasons)}")

                # TP/SL come from the single evaluator call (ATR(14) fixed, per-pair multipliers, exact same math as live).
                # Since we only reach here for cases where evaluator set fired=True (no no_trade_reasons),
                # decision must have valid tp/sl when signal_direction is set.
                tp = decision.get("tp")
                sl = decision.get("sl")
                entry = decision.get("entry", close_price)
                if tp is None or sl is None:
                    # Fallback (ATR missing or evaluator chose legacy path) — keep behavior but rare post-fix
                    tp_mult = float(
                        _get_pair_param(pair, "tp_atr_multiplier", settings.risk.tp_atr_multiplier)
                    )
                    sl_mult = float(
                        _get_pair_param(pair, "sl_atr_multiplier", settings.risk.sl_atr_multiplier)
                    )
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
                    micro_context["rsi_5m"],
                    micro_context["rsi_1m"],
                )

                print(f"     Entry: {entry:.5f}")
                print(f"     TP: {tp:.5f}")
                print(f"     SL: {sl:.5f}")
                if isinstance(micro_context["rsi_5m"], float):
                    print(f"     RSI 5m: {micro_context['rsi_5m']:.1f}")
                if isinstance(micro_context["rsi_1m"], float):
                    print(f"     RSI 1m: {micro_context['rsi_1m']:.1f}")
                print(f"     Execution: {exec_note}")

                # Send Telegram notification (skip for shadow pairs)
                is_shadow = pair in shadow_set
                if notifier and hh and ll and not is_shadow:
                    confirmed_pairs.add(pair)
                    entry_fp = f"entry|{signal_direction}"
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
                            adx=adx_1h,
                            plus_di=plus_di_1h,
                            minus_di=minus_di_1h,
                            ema_context=ema_context_str,
                        )
                    near_state[pair] = {"fingerprint": entry_fp, "sent_at": now_ts, "kind": "entry"}
                    sma_side = "below" if signal_direction == "BUY" else "above"
                    active_signal_state[pair] = {
                        "direction": signal_direction,
                        "fired_at": int(now_ts),
                        "entry": float(entry),
                        "tp": float(tp),
                        "sl": float(sl),
                        "sma_side": sma_side,
                    }
                    # Track for outcome notification
                    pip_mult = 100.0 if "JPY" in pair else 10000.0
                    signal_id = now_utc.isoformat()
                    entry_signal_id = signal_id
                    pending_trades.append(
                        {
                            "signal_id": signal_id,
                            "pair": pair,
                            "direction": signal_direction,
                            "entry": entry,
                            "tp": tp,
                            "sl": sl,
                            "tp_pips": abs(tp - entry) * pip_mult,
                            "sl_pips": abs(sl - entry) * pip_mult,
                            "fired_at": now_ts,
                        }
                    )
                    _append_audit_log(
                        {
                            "ts": now_utc.isoformat(),
                            "pair": pair,
                            "state": "entry",
                            "signal_id": signal_id,
                            "direction": signal_direction,
                            "confidence": signal_confidence,
                            "reasons": signal_reasons,
                            "entry": entry,
                            "tp": tp,
                            "sl": sl,
                            "rsi_1h": float(rsi_1h),
                            "rsi_30m": float(rsi_30m),
                            "rsi_15m": float(rsi_15m_val),
                            "adx_1h": adx_1h,
                            "plus_di_1h": plus_di_1h,
                            "minus_di_1h": minus_di_1h,
                            "breakout_buy": breakout_buy,
                            "breakout_sell": breakout_sell,
                            "shadow": is_shadow,
                        }
                    )

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
                    "rsi_ma_1h": rsi_ma_1h[-1] if rsi_ma_1h and rsi_ma_1h[-1] is not None else None,
                    "rsi_ma_30m": rsi_ma_30m[-1]
                    if rsi_ma_30m and rsi_ma_30m[-1] is not None
                    else None,
                    "rsi_ma_15m": rsi_ma_15m[-1]
                    if rsi_ma_15m and rsi_ma_15m[-1] is not None
                    else None,
                    "patterns": [p.name for p in candle_patterns],
                    "bullish_div": bullish_div.strength if bullish_div else None,
                    "bearish_div": bearish_div.strength if bearish_div else None,
                    "price": float(close_price),
                    "no_trade_reasons": list(no_trade_reasons),
                }
            )

            # Collect EMA candidates for standalone GC/DC alerts + digest context.
            # Always collect (even when an RSI signal fires) so crossovers still alert.
            if ema_signals_this_pair:
                ema_candidates.append(
                    {
                        "pair": pair,
                        "symbol": symbol,
                        "signals": list(ema_signals_this_pair),
                        "price": float(close_price),
                    }
                )

            if telemetry_state == "data_unavailable" and not telemetry_reasons:
                telemetry_state = "neutral"
                telemetry_direction = (
                    near_direction if near_distance <= 4.0 or remaining == 1 else None
                )

            telemetry_payload = _build_scan_telemetry_payload(
                ts=now_utc.isoformat(),
                scan_run_id=scan_run_id,
                pair=pair,
                state=telemetry_state,
                direction=telemetry_direction,
                aligned=telemetry_aligned,
                breakout_pending=breakout_pending,
                entry_triggered=telemetry_entry_triggered,
                bars_aligned=bars_aligned,
                confirm_bars=confirm_bars,
                within_confirm_window=within_confirm_window,
                spread_pips=spread_pips,
                max_spread_pips=max_spread_for_pair,
                spread_source=spread_source,
                adx_1h=adx_1h,
                is_ranging=is_ranging,
                rsi_1h=rsi_1h,
                rsi_30m=rsi_30m,
                rsi_15m=rsi_15m_val,
                no_trade_reasons=telemetry_reasons,
                is_shadow=is_shadow,
            )
            _append_audit_log(telemetry_payload)

            entry_tp_pips: float | None = None
            entry_sl_pips: float | None = None
            if entry_val is not None and tp_val is not None and sl_val is not None:
                pip_mult = 100.0 if "JPY" in pair else 10000.0
                entry_tp_pips = abs(float(tp_val) - float(entry_val)) * pip_mult
                entry_sl_pips = abs(float(sl_val) - float(entry_val)) * pip_mult
            record_branch_b_scan_decision_signal(
                ts=now_utc,
                pair=pair,
                scan_run_id=scan_run_id,
                telemetry_state=telemetry_state,
                direction=telemetry_direction,
                telemetry_payload=telemetry_payload,
                data_1h=data_1h,
                data_30m=data_30m,
                data_15m=data_15m,
                signal_reasons=signal_reasons if telemetry_state == "entry" else None,
                no_trade_reasons=telemetry_reasons if telemetry_state == "blocked" else None,
                signal_id=entry_signal_id,
                entry_ref_price=float(entry_val) if entry_val is not None else None,
                tp_pips=entry_tp_pips,
                sl_pips=entry_sl_pips,
                confidence=signal_confidence,
                profile=_profile_label(profile),
                missing_timeframes=missing_timeframes,
                distance=float(near_distance),
                breakout_pending=breakout_pending,
                bars_aligned=bars_aligned,
                confirm_bars=confirm_bars,
                news_blocked=news_blocked,
                is_shadow=is_shadow,
            )

        except Exception as exc:
            print(f"  Error: {exc}")
            _append_audit_log(
                _build_scan_telemetry_payload(
                    ts=now_utc.isoformat(),
                    scan_run_id=scan_run_id,
                    pair=pair,
                    state="data_unavailable",
                    direction=telemetry_direction,
                    aligned=telemetry_aligned,
                    breakout_pending=telemetry_breakout_pending,
                    entry_triggered=telemetry_entry_triggered,
                    bars_aligned=bars_aligned,
                    confirm_bars=confirm_bars,
                    within_confirm_window=within_confirm_window,
                    spread_pips=spread_pips,
                    max_spread_pips=max_spread_for_pair,
                    spread_source=spread_source,
                    adx_1h=adx_1h,
                    is_ranging=is_ranging,
                    rsi_1h=rsi_1h,
                    rsi_30m=rsi_30m,
                    rsi_15m=rsi_15m_val,
                    no_trade_reasons=[f"scan exception: {exc}"],
                    is_shadow=is_shadow,
                )
            )

    # Always persist alignment state (independent of notifier)
    _save_alignment_state(alignment_state)

    if near_candidates:
        ranked = sorted(
            near_candidates,
            key=lambda candidate: (
                candidate["distance"],
                -_priority_for_pair(settings, candidate["pair"]),
            ),
        )
        print("\n[CLOSEST MTF SETUPS]")
        for candidate in ranked[:10]:
            pair = str(candidate["pair"])
            direction = str(candidate["direction"])
            distance = float(candidate["distance"])
            r1 = float(candidate["rsi_1h"])
            r30 = float(candidate["rsi_30m"])
            r15 = float(candidate["rsi_15m"])
            remaining = candidate["remaining"]
            missing = ",".join(candidate["missing_timeframes"])
            label = (
                "aligned_pending_breakout"
                if bool(candidate.get("breakout_pending"))
                else ("near" if remaining > 0 else "aligned")
            )
            print(
                f"  {pair}: {direction} | state={label} | gap={distance:.1f} RSI points | remaining_tf={remaining} | missing={missing or '-'} | "
                f"1h={r1:.1f} 30m={r30:.1f} 15m={r15:.1f}"
            )

        digest_notifications_enabled = getattr(
            settings.telegram,
            "setup_digest_notifications",
            True,
        )
        if notifier and digest_notifications_enabled:
            digest_candidates = [
                SetupCandidate.from_mapping(cast(dict[str, object], candidate))
                for candidate in ranked
            ]
            digest_message = build_setup_digest_message(
                digest_candidates,
                ema_candidates,
                scanned_at=now_utc,
            )
            if digest_message:
                digest_state = _load_setup_digest_state()
                interval_seconds = (
                    int(getattr(settings.telegram, "setup_digest_interval_minutes", 60)) * 60
                )
                fingerprint = digest_fingerprint(
                    [candidate for candidate in digest_candidates if candidate.watchable]
                )
                sent_at = int(digest_state.get("sent_at", 0))
                previous = str(digest_state.get("fingerprint", ""))
                if should_send_digest(
                    previous_fingerprint=previous,
                    current_fingerprint=fingerprint,
                    sent_at=sent_at,
                    now_ts=now_ts,
                    interval_seconds=interval_seconds,
                ):
                    await notifier.send(digest_message)
                    _save_setup_digest_state(
                        {
                            "fingerprint": fingerprint,
                            "sent_at": now_ts,
                            "kind": "setup_digest",
                        }
                    )

        near_notifications_enabled = getattr(settings.telegram, "near_setup_notifications", True)
        aligned_pending_notifications_enabled = getattr(
            settings.telegram,
            "aligned_pending_notifications",
            True,
        )
        if notifier and (near_notifications_enabled or aligned_pending_notifications_enabled):
            changed = False
            active_pairs: set[str] = set(confirmed_pairs)
            for candidate in ranked[:3]:
                distance = candidate["distance"]
                pair = candidate["pair"]
                direction = candidate["direction"]
                r1 = candidate["rsi_1h"]
                r30 = candidate["rsi_30m"]
                r15 = candidate["rsi_15m"]
                r5 = candidate.get("rsi_5m")
                r1m = candidate.get("rsi_1m")
                remaining = candidate["remaining"]
                missing_timeframes = candidate["missing_timeframes"]
                breakout_pending = bool(candidate.get("breakout_pending"))
                qualifies_near = distance <= 4.0 or remaining == 1
                should_track_candidate = (
                    breakout_pending and aligned_pending_notifications_enabled
                ) or (not breakout_pending and qualifies_near and near_notifications_enabled)
                if not should_track_candidate:
                    continue
                active_pairs.add(pair)
                state_kind = "aligned_pending_breakout" if breakout_pending else "near"
                fingerprint = f"{state_kind}|{direction}"
                prev = near_state.get(pair)
                should_send = not prev or str(prev.get("fingerprint", "")) != fingerprint
                if should_send:
                    missing_txt = ", ".join(missing_timeframes) if missing_timeframes else "none"
                    micro_lines = []
                    if isinstance(r5, float):
                        micro_lines.append(f"RSI 5m: `{r5:.1f}`")
                    if isinstance(r1m, float):
                        micro_lines.append(f"RSI 1m: `{r1m:.1f}`")
                    rma1h = candidate.get("rsi_ma_1h")
                    rma30 = candidate.get("rsi_ma_30m")
                    rma15 = candidate.get("rsi_ma_15m")
                    if isinstance(rma1h, float):
                        micro_lines.append(f"RSI-MA 1h: `{rma1h:.1f}`")
                    if isinstance(rma30, float):
                        micro_lines.append(f"RSI-MA 30m: `{rma30:.1f}`")
                    if isinstance(rma15, float):
                        micro_lines.append(f"RSI-MA 15m: `{rma15:.1f}`")
                    micro_txt = ("\n" + "\n".join(micro_lines)) if micro_lines else ""
                    candidate_aligned = bool(candidate.get("aligned"))
                    if breakout_pending and candidate_aligned:
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
                    elif breakout_pending and not candidate_aligned:
                        # Breakout pending but RSI drifted out of alignment
                        message = (
                            f"⚠️ *Stale Alignment / Breakout Pending*\n\n"
                            f"Pair: `{pair.replace('/', '')}`\n"
                            f"Bias: `{direction}`\n"
                            f"MTF RSI: `alignment lost` (RSI drifted)\n"
                            f"Breakout still active but RSI no longer aligned\n\n"
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
                    near_state[pair] = {
                        "fingerprint": fingerprint,
                        "sent_at": now_ts,
                        "kind": state_kind,
                        "miss_count": 0,
                    }
                    changed = True
                else:
                    # Pair still in tracked state this scan: reset any miss streak.
                    if prev is not None and int(prev.get("miss_count", 0)) > 0:
                        near_state[pair] = {
                            **prev,
                            "miss_count": 0,
                        }
                        changed = True

            stale_pairs = [pair for pair in list(near_state.keys()) if pair not in active_pairs]
            for pair in stale_pairs:
                prev = near_state.get(pair)
                if not prev:
                    continue
                miss_count = int(prev.get("miss_count", 0)) + 1
                if miss_count < INVALIDATION_MISS_THRESHOLD:
                    near_state[pair] = {**prev, "miss_count": miss_count}
                    changed = True
                    continue
                kind = str(prev.get("kind", "near"))
                notifications_enabled_for_kind = (
                    kind == "near" and near_notifications_enabled
                ) or (kind == "aligned_pending_breakout" and aligned_pending_notifications_enabled)
                if kind == "near":
                    status = "near setup faded before confirmation"
                elif kind == "aligned_pending_breakout":
                    status = "MTF alignment remained/changed but breakout confirmation is no longer pending"
                else:
                    status = "entry condition no longer active"
                if notifications_enabled_for_kind:
                    await notifier.send(
                        f"❌ *Setup Invalidated*\n\nPair: `{pair.replace('/', '')}`\nStatus: `{status}`"
                    )
                near_state.pop(pair, None)
                changed = True

            if changed:
                _save_near_setup_state(near_state)
            _save_active_signal_state(active_signal_state)
            _save_alignment_state(alignment_state)
        elif notifier:
            # Near and aligned-pending notifications disabled: clear stale state so no
            # invalidation alerts are emitted later if the features are re-enabled.
            if near_state:
                near_state.clear()
                _save_near_setup_state(near_state)
            _save_active_signal_state(active_signal_state)
            _save_alignment_state(alignment_state)
        _save_pending_trades(pending_trades)

    # ── EMA Notification Dispatch (independent of near_candidates) ──
    ema_state_ttl = 7200  # 2 hours — fingerprints older than this are eligible for re-send
    ema_changed = False
    ema_cfg = settings.strategy.ema

    if ema_cfg.enabled and ema_cfg.standalone_notifications_enabled and notifier and ema_candidates:
        # Optional session gate reuses RSI windows (06-17 / 12-21 UTC by default).
        session_ok = True
        if ema_cfg.standalone_session_filter_enabled:
            session_ok = _session_allowed(now_utc, list(settings.strategy.session_allowed_utc))

        if session_ok:
            for ema_candidate in ema_candidates:
                pair = ema_candidate["pair"]
                symbol = ema_candidate["symbol"]
                if pair in shadow_set:
                    continue

                candidate_price = ema_candidate.get("price")
                signals = _filter_standalone_ema_signals(
                    list(ema_candidate["signals"]),
                    allowed_types=list(ema_cfg.standalone_signal_types),
                    allowed_timeframes=list(ema_cfg.standalone_timeframes),
                )
                # Rate-limit: prioritize crossover > price_touch > slope
                priority_map = {"crossover": 0, "price_touch": 1, "slope": 2}
                signals.sort(key=lambda s: priority_map.get(str(s.get("type", "")), 99))
                signals = signals[: ema_cfg.max_signals_per_pair]

                for sig in signals:
                    data = sig["data"]
                    sig_type = sig["type"]
                    ema_fp = _ema_standalone_fingerprint(sig_type, data, pair)

                    # Anti-flicker: keyed by fingerprint (not pair), so each
                    # unique signal type/timeframe/direction gets its own slot.
                    prev = ema_near_state.get(ema_fp)
                    should_send = prev is None

                    if should_send:
                        if sig_type == "crossover" and isinstance(data, EMACrossover):
                            direction = (
                                "bullish"
                                if data.crossover_type == EMACrossoverType.GOLDEN_CROSS
                                else "bearish"
                            )
                            await notifier.send_ema_crossover(
                                pair=symbol,
                                direction=direction,
                                fast_ema=data.fast_value,
                                slow_ema=data.slow_value,
                                fast_period=data.fast_period,
                                slow_period=data.slow_period,
                                timeframe=data.timeframe,
                                price=candidate_price,
                            )
                        elif sig_type == "price_touch" and isinstance(data, EMAPriceTouch):
                            await notifier.send_ema_price_touch(
                                pair=symbol,
                                price=data.price,
                                ema_value=data.ema_value,
                                ema_period=data.ema_period,
                                timeframe=data.timeframe,
                                touch_type=data.direction,
                                distance_pips=data.distance_pips,
                            )
                        elif sig_type == "slope" and isinstance(data, EMASlope):
                            await notifier.send_ema_slope(
                                pair=symbol,
                                ema_period=data.period,
                                slope_direction=data.slope_direction.value,
                                current_value=data.current_value,
                                timeframe=data.timeframe,
                            )

                        ema_near_state[ema_fp] = {
                            "fingerprint": ema_fp,
                            "sent_at": now_ts,
                            "kind": f"ema_{sig_type}",
                            "miss_count": 0,
                        }
                        ema_changed = True

    # TTL sweep: drop fingerprints whose sent_at is older than the TTL window.
    stale_keys = [
        fp
        for fp, rec in ema_near_state.items()
        if now_ts - int(rec.get("sent_at", 0)) > ema_state_ttl
    ]
    if stale_keys:
        for fp in stale_keys:
            ema_near_state.pop(fp, None)
        ema_changed = True

    if ema_changed:
        _save_ema_near_state(ema_near_state)


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


async def run_enhanced_backtest(
    pair: str,
    start: str | None,
    end: str | None,
    use_patterns: bool = True,
    use_divergence: bool = True,
    use_rsi_ma: bool = False,
    rsi_ma_period: int = 5,
    rsi_ma_variant: str = "curl",
    rsi_ma_distance_max: float = 15.0,
    rsi_ma_confidence_mod: float = 0.85,
    use_ema_confidence: bool = False,
    ema_confidence_ref_period: int = 200,
    ema_confidence_boost: float = 1.10,
    ema_confidence_dampen: float = 0.85,
) -> None:
    """Run enhanced backtest with realistic TP/SL simulation."""
    from src.backtest.enhanced_engine import EnhancedBacktestEngine

    fetcher = DataFetcher()

    print(f"\n[ENHANCED BACKTEST] {pair}")
    print(f"  Patterns: {'enabled' if use_patterns else 'disabled'}")
    print(f"  Divergence: {'enabled' if use_divergence else 'disabled'}")
    print(
        f"  RSI-MA: {'enabled (variant=' + rsi_ma_variant + ', period=' + str(rsi_ma_period) + ')' if use_rsi_ma else 'disabled'}"
    )

    try:
        # Fetch 1h data for longer history (15m limited to 60 days on yfinance)
        symbol = pair.replace("/", "").replace("-", "")
        data = fetcher.fetch(symbol, period="2y", interval="1h")

        if data.empty:
            print("  Error: No data available")
            return

        settings = get_settings()
        engine = EnhancedBacktestEngine(
            initial_balance=10000.0,
            risk_per_trade=0.02,
            use_patterns=use_patterns,
            use_divergence=use_divergence,
            sma_period=int(_get_pair_param(pair, "sma_period", settings.strategy.sma_period)),
            reward_ratio=float(
                _get_pair_param(pair, "tp_atr_multiplier", settings.risk.tp_atr_multiplier)
            ),
            sl_atr_multiplier=float(
                _get_pair_param(pair, "sl_atr_multiplier", settings.risk.sl_atr_multiplier)
            ),
            use_rsi_ma=use_rsi_ma,
            rsi_ma_period=rsi_ma_period,
            rsi_ma_variant=rsi_ma_variant,
            rsi_ma_distance_max=rsi_ma_distance_max,
            rsi_ma_confidence_mod=rsi_ma_confidence_mod,
            use_ema_confidence=use_ema_confidence,
            ema_confidence_ref_period=ema_confidence_ref_period,
            ema_confidence_boost=ema_confidence_boost,
            ema_confidence_dampen=ema_confidence_dampen,
        )
        if use_ema_confidence:
            print(
                f"  EMA confidence: enabled (ref=EMA{ema_confidence_ref_period}, "
                f"boost={ema_confidence_boost}, dampen={ema_confidence_dampen})"
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
            print(
                f"\n  Pattern trades: {result.pattern_trades} (win rate: {result.pattern_win_rate:.1%})"
            )
        if use_divergence:
            print(
                f"  Divergence trades: {result.divergence_trades} (win rate: {result.divergence_win_rate:.1%})"
            )
        if use_patterns and use_divergence:
            print(
                f"  Combined trades: {result.combined_trades} (win rate: {result.combined_win_rate:.1%})"
            )

    except Exception as exc:
        print(f"  Error: {exc}")


async def run_telegram_poll() -> None:
    settings = get_settings()
    if not settings.telegram.enabled:
        print("Telegram disabled in settings")
        return
    if not settings.telegram.poll_enabled:
        print("Telegram command polling disabled (TELEGRAM_POLL_ENABLED=false)")
        return
    token = settings.telegram.bot_token
    chat_id = settings.telegram.chat_id
    if not token or not chat_id:
        print("Telegram token/chat_id missing")
        return

    from src.notifications.telegram_commands import (
        HEARTBEAT_PATH,
        POLL_LOCK_PATH,
        TelegramCommandHandler,
    )
    from src.notifications.telegram_security import TelegramPollLock

    lock = TelegramPollLock(POLL_LOCK_PATH)
    if not lock.acquire():
        print("[TELEGRAM] Another telegram-poll process already holds getUpdates lock; exiting")
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(
            json.dumps(
                {
                    "status": "error",
                    "updated_at": datetime.now(UTC).isoformat(),
                    "error": "duplicate local telegram-poll process",
                }
            ),
            encoding="utf-8",
        )
        return

    try:
        handler = TelegramCommandHandler(token, chat_id)
        print("[TELEGRAM] Polling commands...")
        await handler.run_forever()
    finally:
        lock.release()


async def run_healthcheck() -> None:
    await _healthcheck_run()


async def run_logs_status(notify: bool) -> None:
    await _logs_status_run(notify=notify)


async def run_dashboard(days: int) -> None:
    await _dashboard_run(days)


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
        "backtest-enhanced": lambda: run_enhanced_backtest(
            args.pair,
            args.start,
            args.end,
            use_patterns=not args.no_patterns,
            use_divergence=not args.no_divergence,
            use_rsi_ma=getattr(args, "rsi_ma", False),
            rsi_ma_period=getattr(args, "rsi_ma_period", 5),
            rsi_ma_variant=getattr(args, "rsi_ma_variant", "curl"),
            rsi_ma_distance_max=getattr(args, "rsi_ma_distance_max", 15.0),
            rsi_ma_confidence_mod=getattr(args, "rsi_ma_confidence_mod", 0.85),
            use_ema_confidence=getattr(args, "ema_confidence", False),
            ema_confidence_ref_period=getattr(args, "ema_confidence_ref", 200),
            ema_confidence_boost=getattr(args, "ema_confidence_boost", 1.10),
            ema_confidence_dampen=getattr(args, "ema_confidence_dampen", 0.85),
        ),
        "telegram-poll": run_telegram_poll,
        "healthcheck": run_healthcheck,
        "logs-status": lambda: run_logs_status(args.notify),
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
