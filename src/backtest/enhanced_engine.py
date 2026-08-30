"""Enhanced backtesting engine with realistic TP/SL simulation.

Replay-only: one walk of a supplied OHLC frame. This engine does not rank
configs, does not promote to live, and does not send broker orders.

Causality
---------
Signals are taken from a *closed* bar. Market entries fill at the *next*
bar open (spread + slippage). Same-bar TP and SL use stop-first (pessimistic).

Clock
-----
Not session-aware. Bar timestamps come from the frame index as-is. Missing or
non-timestamp indexes raise; the engine never substitutes wall-clock ``now``.

Costs
-----
Uses a frozen ``CostBook`` (pips + USD/lot/side). See
``src.backtest.cost_book``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal, cast

import pandas as pd

from src.backtest.cost_book import CostBook, pip_size_for_pair
from src.backtest.exits import same_bar_exit
from src.indicators.adx import calculate_adx
from src.indicators.atr import calculate_atr
from src.indicators.candlestick import (
    CandlePattern,
    PatternType,
    detect_patterns,
    get_pattern_score,
)
from src.indicators.ema import calculate_ema
from src.indicators.high_low import (
    rolling_highest_highs,
    rolling_lowest_lows,
)
from src.indicators.rsi import (
    Divergence,
    calculate_rsi,
    calculate_rsi_ma_series,
    detect_bearish_divergence,
    detect_bullish_divergence,
    detect_rsi_curl,
    detect_rsi_slope_change,
    rsi_ma_distance,
)


class SignalType(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class Trade:
    """A single trade record."""

    entry_time: datetime
    exit_time: datetime
    side: Literal["buy", "sell"]
    entry_price: float
    exit_price: float
    tp_price: float
    sl_price: float
    pnl: float
    pnl_pct: float
    exit_reason: Literal["tp", "sl", "time", "signal"]
    confidence: float
    patterns: list[str] = field(default_factory=list)
    divergence: str | None = None
    rsi_entry: float = 0.0
    rsi_exit: float = 0.0


@dataclass
class EnhancedBacktestResult:
    """Results from an enhanced backtest run."""

    symbol: str
    start_date: datetime
    end_date: datetime
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    total_pnl_pct: float
    max_drawdown: float
    max_drawdown_pct: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    sharpe_ratio: float
    trades: list[Trade]

    # Pattern analysis
    pattern_trades: int
    pattern_win_rate: float
    divergence_trades: int
    divergence_win_rate: float
    combined_trades: int
    combined_win_rate: float


class EnhancedBacktestEngine:
    """Enhanced backtest engine with realistic TP/SL simulation."""

    def __init__(
        self,
        initial_balance: float = 10000.0,
        risk_per_trade: float = 0.02,  # 2% risk
        reward_ratio: float = 1.5,
        sl_atr_multiplier: float = 2.0,
        spread_pips: float = 2.0,
        slippage_pips: float = 2.0,
        commission_usd_per_lot_side: float = 3.0,
        lot_size: float = 1.0,
        adx_threshold: float = 25.0,
        max_hold_bars: int = 96,  # Max 24 hours at 15m bars
        use_patterns: bool = True,
        use_divergence: bool = True,
        use_mtf_alignment: bool = False,  # Disabled by default: single-TF data in backtest
        rsi_overbought: float = 70.0,
        rsi_oversold: float = 30.0,
        use_sma_alignment: bool = True,
        sma_period: int = 50,
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
        self.initial_balance = initial_balance
        self.risk_per_trade = risk_per_trade
        self.reward_ratio = reward_ratio
        self.sl_atr_multiplier = sl_atr_multiplier
        self.cost_book = CostBook(
            spread_pips=spread_pips,
            slippage_pips=slippage_pips,
            commission_usd_per_lot_side=commission_usd_per_lot_side,
            lot_size=lot_size,
        )
        self.adx_threshold = adx_threshold
        self.max_hold_bars = max_hold_bars
        self.use_patterns = use_patterns
        self.use_divergence = use_divergence
        self.use_mtf_alignment = use_mtf_alignment
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.use_sma_alignment = use_sma_alignment
        self.sma_period = sma_period
        self.use_rsi_ma = use_rsi_ma
        self.rsi_ma_period = rsi_ma_period
        self.rsi_ma_variant = rsi_ma_variant
        self.rsi_ma_distance_max = rsi_ma_distance_max
        self.rsi_ma_confidence_mod = rsi_ma_confidence_mod
        self.use_ema_confidence = use_ema_confidence
        self.ema_confidence_ref_period = ema_confidence_ref_period
        self.ema_confidence_boost = ema_confidence_boost
        self.ema_confidence_dampen = ema_confidence_dampen

    @property
    def spread_pips(self) -> float:
        """Entry half-spread in pair pips (from the frozen cost book)."""

        return self.cost_book.spread_pips

    def _calculate_rsi_column(self, data: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate RSI column for dataframe."""
        window = period + 1

        def _window_rsi(values: pd.Series) -> float:
            rsi = calculate_rsi(values.tolist(), period)
            return float(rsi) if rsi is not None else float("nan")

        return cast(
            pd.Series,
            data["close"].rolling(window=window, min_periods=window).apply(_window_rsi),
        )

    def _index_to_datetime(self, value: object) -> datetime:
        """Normalize a dataframe index value into a datetime.

        Wall-clock ``datetime.now()`` is not used — that would leak the
        machine clock into replay artifacts.
        """
        if isinstance(value, datetime):
            return value
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp):
            raise TypeError(f"backtest index value is not a timestamp: {value!r}")
        converted = timestamp.to_pydatetime()
        if not isinstance(converted, datetime):
            raise TypeError(f"backtest index value is not a timestamp: {value!r}")
        return converted

    def _check_tp_sl_hit(
        self,
        side: Literal["buy", "sell"],
        entry_price: float,
        tp_price: float,
        sl_price: float,
        high: float,
        low: float,
    ) -> Literal["tp", "sl"] | None:
        """Check if TP or SL was hit during a bar.

        Stop-first when both levels trade in the same bar (pessimistic).
        ``entry_price`` is unused for the hit test; kept for call-site stability.
        """
        del entry_price
        return same_bar_exit(side, high, low, tp_price, sl_price)

    def _fill_cash_pnl(
        self,
        *,
        side: Literal["buy", "sell"],
        entry_price: float,
        exit_price: float,
        sl_price: float,
        balance: float,
    ) -> tuple[float, float]:
        """Return (cash_pnl, price_return) from slipped fills, risk-sized on SL."""

        raw = (exit_price - entry_price) if side == "buy" else (entry_price - exit_price)
        sl_dist = abs(entry_price - sl_price)
        size = (balance * self.risk_per_trade / sl_dist) if sl_dist > 0 else 1.0
        pnl = size * raw - self.cost_book.round_trip_commission_usd()
        pnl_pct = raw / entry_price if entry_price else 0.0
        return pnl, pnl_pct

    def _generate_signal(
        self,
        rsi: float,
        bullish_patterns: list[CandlePattern],
        bearish_patterns: list[CandlePattern],
        bullish_div: Divergence | None,
        bearish_div: Divergence | None,
        rsi_1h: float | None = None,
        rsi_30m: float | None = None,
    ) -> tuple[SignalType, float, list[str], str | None]:
        """Generate trading signal based on indicators.

        If use_mtf_alignment is True, requires RSI alignment across 1h, 30m, 15m.

        Returns:
            Tuple of (signal_type, confidence, pattern_names, divergence_type)
        """
        signal = SignalType.HOLD
        confidence = 0.0
        pattern_names: list[str] = []
        divergence_type: str | None = None

        # Check MTF alignment if enabled
        if self.use_mtf_alignment:
            if rsi_1h is None or rsi_30m is None:
                return SignalType.HOLD, 0.0, [], None

            # MTF BUY: all timeframes oversold
            all_oversold = (
                rsi_1h < self.rsi_oversold
                and rsi_30m < self.rsi_oversold
                and rsi < self.rsi_oversold
            )
            # MTF SELL: all timeframes overbought
            all_overbought = (
                rsi_1h > self.rsi_overbought
                and rsi_30m > self.rsi_overbought
                and rsi > self.rsi_overbought
            )

            if all_oversold:
                signal = SignalType.BUY
                confidence = 0.6 + (self.rsi_oversold - rsi) / 100
            elif all_overbought:
                signal = SignalType.SELL
                confidence = 0.6 + (rsi - self.rsi_overbought) / 100
            else:
                # No MTF alignment - no signal
                return SignalType.HOLD, 0.0, [], None
        else:
            # Single timeframe mode (backward compatible)
            if rsi < self.rsi_oversold:
                signal = SignalType.BUY
                confidence = 0.5 + (self.rsi_oversold - rsi) / 100
            elif rsi > self.rsi_overbought:
                signal = SignalType.SELL
                confidence = 0.5 + (rsi - self.rsi_overbought) / 100

        # Boost from candlestick patterns
        if self.use_patterns:
            if signal == SignalType.BUY and bullish_patterns:
                pattern_score = get_pattern_score(bullish_patterns, PatternType.BULLISH)
                confidence = min(1.0, confidence + pattern_score * 0.3)
                pattern_names = [p.name for p in bullish_patterns]
            elif signal == SignalType.SELL and bearish_patterns:
                pattern_score = get_pattern_score(bearish_patterns, PatternType.BEARISH)
                confidence = min(1.0, confidence + pattern_score * 0.3)
                pattern_names = [p.name for p in bearish_patterns]

        # Boost from divergence
        if self.use_divergence:
            if signal == SignalType.BUY and bullish_div:
                confidence = min(1.0, confidence + bullish_div.strength * 0.2)
                divergence_type = "bullish"
            elif signal == SignalType.SELL and bearish_div:
                confidence = min(1.0, confidence + bearish_div.strength * 0.2)
                divergence_type = "bearish"

        # Invert signal on conflicting strong divergence
        if signal == SignalType.BUY and bearish_div and bearish_div.strength > 0.7:
            signal = SignalType.SELL
            confidence = min(1.0, 0.4 + bearish_div.strength * 0.3)
            divergence_type = "bearish"
        elif signal == SignalType.SELL and bullish_div and bullish_div.strength > 0.7:
            signal = SignalType.BUY
            confidence = min(1.0, 0.4 + bullish_div.strength * 0.3)
            divergence_type = "bullish"

        return signal, confidence, pattern_names, divergence_type

    def run(
        self,
        symbol: str,
        data: pd.DataFrame,
        lookback: int = 20,
        atr_period: int = 14,
        verbose: bool = False,
    ) -> EnhancedBacktestResult:
        """Run backtest with realistic TP/SL simulation.

        Args:
            symbol: Trading pair
            data: OHLCV data with columns [open, high, low, close]
            lookback: Bars for HH/LL calculation
            atr_period: Period for ATR calculation
            verbose: Print trade details

        Returns:
            EnhancedBacktestResult with detailed metrics
        """
        if data.empty or len(data) < lookback + atr_period:
            if len(data.index) > 0:
                start = self._index_to_datetime(data.index[0])
                end = self._index_to_datetime(data.index[-1])
            else:
                start = end = datetime(1970, 1, 1)
            return EnhancedBacktestResult(
                symbol=symbol,
                start_date=start,
                end_date=end,
                total_trades=0,
                wins=0,
                losses=0,
                win_rate=0.0,
                total_pnl=0.0,
                total_pnl_pct=0.0,
                max_drawdown=0.0,
                max_drawdown_pct=0.0,
                avg_win=0.0,
                avg_loss=0.0,
                profit_factor=0.0,
                sharpe_ratio=0.0,
                trades=[],
                pattern_trades=0,
                pattern_win_rate=0.0,
                divergence_trades=0,
                divergence_win_rate=0.0,
                combined_trades=0,
                combined_win_rate=0.0,
            )

        # Calculate indicators
        data = data.copy()
        data["rsi"] = self._calculate_rsi_column(data, 14)
        # RSI-MA: SMA of the RSI series for curl detection
        rsi_raw = data["rsi"].tolist()
        rsi_ma_series = calculate_rsi_ma_series(
            [float(v) if not pd.isna(v) else None for v in rsi_raw],
            ma_period=self.rsi_ma_period,
        )
        data["rsi_ma"] = rsi_ma_series
        # EMA trend reference for the confidence modifier (aligned to bars)
        if self.use_ema_confidence:
            data["ema_ref"] = calculate_ema(data["close"].tolist(), self.ema_confidence_ref_period)
        data["hh"] = rolling_highest_highs(data["high"].tolist(), lookback)
        data["ll"] = rolling_lowest_lows(data["low"].tolist(), lookback)
        # SMA: rolling mean with min_periods=sma_period so early bars are NaN
        data["sma"] = (
            data["close"].rolling(window=self.sma_period, min_periods=self.sma_period).mean()
        )

        # Prepare data lists
        opens = data["open"].tolist() if "open" in data.columns else data["close"].tolist()
        highs = data["high"].tolist()
        lows = data["low"].tolist()
        closes = data["close"].tolist()

        # Track positions. Signals arm on close; fills wait for the next open.
        position: Literal["buy", "sell", None] = None
        pending_side: Literal["buy", "sell"] | None = None
        pending_atr = 0.0
        pending_confidence = 0.0
        pending_patterns: list[str] = []
        pending_divergence: str | None = None
        pending_rsi = 0.0
        entry_price = 0.0
        entry_idx = 0
        tp_price = 0.0
        sl_price = 0.0
        entry_confidence = 0.0
        entry_patterns: list[str] = []
        entry_divergence: str | None = None
        entry_rsi = 0.0
        pip_size = pip_size_for_pair(symbol)

        trades: list[Trade] = []
        equity_curve: list[float] = [self.initial_balance]
        balance = self.initial_balance
        peak_balance = balance

        # Track pattern/divergence performance
        pattern_wins = 0
        pattern_total = 0
        divergence_wins = 0
        divergence_total = 0
        combined_wins = 0
        combined_total = 0

        for i in range(max(lookback, atr_period + 1), len(data)):
            timestamp = self._index_to_datetime(data.index[i])
            close = closes[i]
            high = highs[i]
            low = lows[i]
            rsi = data["rsi"].iloc[i]
            if pending_side is not None and position is None:
                position = pending_side
                entry_price = self.cost_book.entry_fill(opens[i], position, pip_size)
                entry_idx = i
                entry_confidence = pending_confidence
                entry_patterns = pending_patterns
                entry_divergence = pending_divergence
                entry_rsi = pending_rsi
                if position == "buy":
                    tp_price = entry_price + (pending_atr * self.reward_ratio)
                    sl_price = entry_price - (pending_atr * self.sl_atr_multiplier)
                else:
                    tp_price = entry_price - (pending_atr * self.reward_ratio)
                    sl_price = entry_price + (pending_atr * self.sl_atr_multiplier)
                pending_side = None
            # ATR needs period+1 bars, include current bar
            atr = calculate_atr(
                highs[max(0, i - atr_period) : i + 1],
                lows[max(0, i - atr_period) : i + 1],
                closes[max(0, i - atr_period) : i + 1],
                atr_period,
            )
            rsi_exit = 0.0 if pd.isna(rsi) else float(rsi)

            # Detect patterns
            bullish_pats: list[CandlePattern] = []
            bearish_pats: list[CandlePattern] = []
            if self.use_patterns and i >= 3:
                patterns = detect_patterns(
                    opens[i - 3 : i + 1],
                    highs[i - 3 : i + 1],
                    lows[i - 3 : i + 1],
                    closes[i - 3 : i + 1],
                    lookback=2,
                )
                bullish_pats = [p for p in patterns if p.pattern_type == PatternType.BULLISH]
                bearish_pats = [p for p in patterns if p.pattern_type == PatternType.BEARISH]

            # Detect divergence
            bullish_div = None
            bearish_div = None
            if self.use_divergence:
                rsi_series = data["rsi"].iloc[max(0, i - 100) : i].tolist()
                close_series = closes[max(0, i - 100) : i]
                bullish_div = detect_bullish_divergence(close_series, rsi_series, lookback=5)
                bearish_div = detect_bearish_divergence(close_series, rsi_series, lookback=5)

            # If in position, check for TP/SL hit
            if position is not None:
                result = self._check_tp_sl_hit(position, entry_price, tp_price, sl_price, high, low)

                if result:
                    # Position closed — cash PnL from slipped fills, sized off SL distance.
                    raw_exit = tp_price if result == "tp" else sl_price
                    exit_price = self.cost_book.exit_fill(raw_exit, position, pip_size)
                    pnl, pnl_pct = self._fill_cash_pnl(
                        side=position,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        sl_price=sl_price,
                        balance=balance,
                    )
                    exit_reason: Literal["tp", "sl", "time", "signal"] = (
                        "tp" if result == "tp" else "sl"
                    )
                    balance += pnl
                    if balance > peak_balance:
                        peak_balance = balance

                    trade = Trade(
                        entry_time=self._index_to_datetime(data.index[entry_idx]),
                        exit_time=timestamp,
                        side=position,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        tp_price=tp_price,
                        sl_price=sl_price,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        exit_reason=exit_reason,
                        confidence=entry_confidence,
                        patterns=entry_patterns,
                        divergence=entry_divergence,
                        rsi_entry=entry_rsi,
                        rsi_exit=rsi_exit,
                    )
                    trades.append(trade)
                    equity_curve.append(balance)

                    # Track pattern/divergence performance
                    if entry_patterns:
                        pattern_total += 1
                        if pnl > 0:
                            pattern_wins += 1
                    if entry_divergence:
                        divergence_total += 1
                        if pnl > 0:
                            divergence_wins += 1
                    if entry_patterns and entry_divergence:
                        combined_total += 1
                        if pnl > 0:
                            combined_wins += 1

                    position = None
                    entry_idx = i
                    continue

                # Check max hold time
                if i - entry_idx >= self.max_hold_bars:
                    exit_price = self.cost_book.exit_fill(close, position, pip_size)
                    pnl, pnl_pct = self._fill_cash_pnl(
                        side=position,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        sl_price=sl_price,
                        balance=balance,
                    )
                    balance += pnl

                    trade = Trade(
                        entry_time=self._index_to_datetime(data.index[entry_idx]),
                        exit_time=timestamp,
                        side=position,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        tp_price=tp_price,
                        sl_price=sl_price,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        exit_reason="time",
                        confidence=entry_confidence,
                        patterns=entry_patterns,
                        divergence=entry_divergence,
                        rsi_entry=entry_rsi,
                        rsi_exit=rsi_exit,
                    )
                    trades.append(trade)
                    equity_curve.append(balance)
                    position = None
                    entry_idx = i
                    continue

            if pd.isna(rsi) or atr is None or atr <= 0:
                continue

            sma_val = data["sma"].iloc[i]

            # Generate new signal if not in position
            if position is None:
                # ADX filter: skip signal generation in trending markets
                adx_window = 2 * 14 + 1  # 29 bars needed for ADX(14)
                if i >= adx_window:
                    adx_val = calculate_adx(
                        highs[i - adx_window + 1 : i + 1],
                        lows[i - adx_window + 1 : i + 1],
                        closes[i - adx_window + 1 : i + 1],
                        period=14,
                    )
                    if adx_val is not None and adx_val >= self.adx_threshold:
                        continue

                signal, confidence, pattern_names, div_type = self._generate_signal(
                    float(rsi), bullish_pats, bearish_pats, bullish_div, bearish_div
                )

                # SMA alignment gate: BUY requires price < SMA, SELL requires price > SMA
                if (
                    signal != SignalType.HOLD
                    and self.use_sma_alignment
                    and not pd.isna(sma_val)
                    and (
                        (signal == SignalType.BUY and close >= sma_val)
                        or (signal == SignalType.SELL and close <= sma_val)
                    )
                ):
                    signal = SignalType.HOLD

                # ── RSI-MA variant gates ──────────────────────────────────
                # Variants: curl, fresh, slope, distance, confidence, conditional
                if signal != SignalType.HOLD and self.use_rsi_ma:
                    rsi_val_now = float(rsi) if not pd.isna(rsi) else None
                    rsi_ma_now = data["rsi_ma"].iloc[i]
                    rsi_ma_now = float(rsi_ma_now) if not pd.isna(rsi_ma_now) else None

                    if rsi_val_now is not None and rsi_ma_now is not None:
                        direction = "buy" if signal == SignalType.BUY else "sell"

                        if self.rsi_ma_variant == "slope":
                            # Slope inflection: RSI-MA free-fall ending
                            ma_tail = [
                                data["rsi_ma"].iloc[j]
                                if not pd.isna(data["rsi_ma"].iloc[j])
                                else None
                                for j in range(max(0, i - 10), i + 1)
                            ]
                            if not detect_rsi_slope_change(ma_tail, direction, lookback=3):
                                signal = SignalType.HOLD

                        elif self.rsi_ma_variant == "distance":
                            # Distance threshold: RSI must be at right distance from MA
                            if not rsi_ma_distance(
                                rsi_val_now,
                                rsi_ma_now,
                                direction,
                                min_distance=3.0,
                                max_distance=self.rsi_ma_distance_max,
                            ):
                                signal = SignalType.HOLD

                        elif self.rsi_ma_variant == "fresh":
                            # Fresh momentum: RSI moving AWAY from MA (still extreme)
                            if (
                                signal == SignalType.BUY
                                and rsi_val_now > rsi_ma_now
                                or signal == SignalType.SELL
                                and rsi_val_now < rsi_ma_now
                            ):
                                signal = SignalType.HOLD

                        elif self.rsi_ma_variant == "confidence":
                            # Confidence modifier: curl boosts, no-curl dampens
                            rsi_tail = [
                                float(data["rsi"].iloc[j])
                                if not pd.isna(data["rsi"].iloc[j])
                                else None
                                for j in range(max(0, i - 10), i + 1)
                            ]
                            ma_tail = [
                                data["rsi_ma"].iloc[j]
                                if not pd.isna(data["rsi_ma"].iloc[j])
                                else None
                                for j in range(max(0, i - 10), i + 1)
                            ]
                            if detect_rsi_curl(rsi_tail, ma_tail, direction, lookback=3):
                                confidence = min(1.0, confidence * 1.10)  # boost
                            else:
                                confidence *= self.rsi_ma_confidence_mod  # dampen

                        elif self.rsi_ma_variant == "conditional":
                            # Only gate low-confidence signals
                            if confidence < 0.6:
                                rsi_tail = [
                                    float(data["rsi"].iloc[j])
                                    if not pd.isna(data["rsi"].iloc[j])
                                    else None
                                    for j in range(max(0, i - 10), i + 1)
                                ]
                                ma_tail = [
                                    data["rsi_ma"].iloc[j]
                                    if not pd.isna(data["rsi_ma"].iloc[j])
                                    else None
                                    for j in range(max(0, i - 10), i + 1)
                                ]
                                if not detect_rsi_curl(rsi_tail, ma_tail, direction, lookback=3):
                                    signal = SignalType.HOLD

                        elif self.rsi_ma_variant == "gate":
                            # Gate: SMA(RSI) must be outside 30/70 — mirrors live rsi_ma_gate_enabled
                            if (
                                signal == SignalType.BUY
                                and rsi_ma_now > self.rsi_oversold
                                or signal == SignalType.SELL
                                and rsi_ma_now < self.rsi_overbought
                            ):
                                signal = SignalType.HOLD

                        else:
                            # Default: curl variant (strict cross)
                            rsi_tail = [
                                float(data["rsi"].iloc[j])
                                if not pd.isna(data["rsi"].iloc[j])
                                else None
                                for j in range(max(0, i - 10), i + 1)
                            ]
                            ma_tail = [
                                data["rsi_ma"].iloc[j]
                                if not pd.isna(data["rsi_ma"].iloc[j])
                                else None
                                for j in range(max(0, i - 10), i + 1)
                            ]
                            if not detect_rsi_curl(rsi_tail, ma_tail, direction, lookback=3):
                                signal = SignalType.HOLD

                # ── EMA trend-alignment confidence modifier ───────────────
                # Not a gate: scales confidence by whether price is on the
                # trend-aligned side of EMA(ref). Can cross the 0.4 entry
                # threshold for weak signals if dampen is strong enough.
                if signal != SignalType.HOLD and self.use_ema_confidence:
                    ema_ref_now = data["ema_ref"].iloc[i]
                    if not pd.isna(ema_ref_now):
                        aligned = (signal == SignalType.BUY and close > ema_ref_now) or (
                            signal == SignalType.SELL and close < ema_ref_now
                        )
                        if aligned:
                            confidence = min(1.0, confidence * self.ema_confidence_boost)
                        else:
                            confidence *= self.ema_confidence_dampen

                # Arm a next-bar fill; never fill on the signal bar's close.
                if (
                    signal != SignalType.HOLD
                    and confidence >= 0.4
                    and i + 1 < len(data)
                    and pending_side is None
                ):
                    pending_side = "buy" if signal == SignalType.BUY else "sell"
                    pending_atr = atr
                    pending_confidence = confidence
                    pending_patterns = pattern_names
                    pending_divergence = div_type
                    pending_rsi = float(rsi)

        # Calculate metrics
        wins = sum(1 for t in trades if t.pnl > 0)
        losses = sum(1 for t in trades if t.pnl <= 0)
        total_pnl = balance - self.initial_balance
        total_pnl_pct = (balance - self.initial_balance) / self.initial_balance * 100

        # Calculate drawdown
        max_dd = 0.0
        max_dd_pct = 0.0
        peak = equity_curve[0]
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            dd_pct = (peak - eq) / peak if peak > 0 else 0
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
                max_dd = dd

        avg_win = sum(t.pnl for t in trades if t.pnl > 0) / max(1, wins)
        avg_loss = sum(abs(t.pnl) for t in trades if t.pnl <= 0) / max(1, losses)
        profit_factor = (wins * avg_win) / max(1, losses * avg_loss) if losses > 0 else float("inf")

        # Sharpe-like ratio (simplified)
        returns = [t.pnl_pct for t in trades]
        avg_return = sum(returns) / len(returns) if returns else 0
        std_return = (
            (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5
            if len(returns) > 1
            else 0
        )
        sharpe = (avg_return * 252) / (std_return * (252**0.5)) if std_return > 0 else 0

        start_date = self._index_to_datetime(data.index[max(lookback, atr_period + 1)])
        end_date = self._index_to_datetime(data.index[-1])

        return EnhancedBacktestResult(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            total_trades=len(trades),
            wins=wins,
            losses=losses,
            win_rate=wins / len(trades) if trades else 0.0,
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct,
            max_drawdown=max_dd,
            max_drawdown_pct=max_dd_pct * 100,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            sharpe_ratio=sharpe,
            trades=trades,
            pattern_trades=pattern_total,
            pattern_win_rate=pattern_wins / pattern_total if pattern_total > 0 else 0.0,
            divergence_trades=divergence_total,
            divergence_win_rate=divergence_wins / divergence_total if divergence_total > 0 else 0.0,
            combined_trades=combined_total,
            combined_win_rate=combined_wins / combined_total if combined_total > 0 else 0.0,
        )
