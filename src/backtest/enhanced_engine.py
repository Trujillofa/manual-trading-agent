"""Enhanced backtesting engine with realistic TP/SL simulation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal

import pandas as pd

from src.indicators.adx import calculate_adx
from src.indicators.candlestick import (
    CandlePattern,
    PatternType,
    detect_patterns,
    get_pattern_score,
)
from src.indicators.high_low import (
    rolling_highest_highs,
    rolling_lowest_lows,
)
from src.indicators.rsi import (
    Divergence,
    calculate_rsi,
    detect_bearish_divergence,
    detect_bullish_divergence,
)
from src.indicators.sma import calculate_sma


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
        adx_threshold: float = 25.0,
        max_hold_bars: int = 96,  # Max 24 hours at 15m bars
        use_patterns: bool = True,
        use_divergence: bool = True,
        use_mtf_alignment: bool = False,  # Disabled by default: single-TF data in backtest
        rsi_overbought: float = 70.0,
        rsi_oversold: float = 30.0,
        use_sma_alignment: bool = True,
        sma_period: int = 50,
    ) -> None:
        self.initial_balance = initial_balance
        self.risk_per_trade = risk_per_trade
        self.reward_ratio = reward_ratio
        self.sl_atr_multiplier = sl_atr_multiplier
        self.spread_pips = spread_pips
        self.adx_threshold = adx_threshold
        self.max_hold_bars = max_hold_bars
        self.use_patterns = use_patterns
        self.use_divergence = use_divergence
        self.use_mtf_alignment = use_mtf_alignment
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.use_sma_alignment = use_sma_alignment
        self.sma_period = sma_period

    def _calculate_atr(
        self, highs: list[float], lows: list[float], closes: list[float], period: int = 14
    ) -> float | None:
        """Calculate ATR."""
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

    def _calculate_rsi_column(self, data: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate RSI column for dataframe."""
        window = period + 1

        def _window_rsi(values: pd.Series) -> float:
            rsi = calculate_rsi(values.tolist(), period)
            return float(rsi) if rsi is not None else float("nan")

        return data["close"].rolling(window=window, min_periods=window).apply(_window_rsi)

    def _check_tp_sl_hit(
        self,
        side: Literal["buy", "sell"],
        entry_price: float,
        tp_price: float,
        sl_price: float,
        high: float,
        low: float,
    ) -> Literal["tp", "sl", None]:
        """Check if TP or SL was hit during a bar."""
        if side == "buy":
            # For long: TP above entry, SL below entry
            if high >= tp_price:
                return "tp"
            if low <= sl_price:
                return "sl"
        else:
            # For short: TP below entry, SL above entry
            if low <= tp_price:
                return "tp"
            if high >= sl_price:
                return "sl"
        return None

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
            now = datetime.now()
            return EnhancedBacktestResult(
                symbol=symbol,
                start_date=now,
                end_date=now,
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
        data["hh"] = rolling_highest_highs(data["high"].tolist(), lookback)
        data["ll"] = rolling_lowest_lows(data["low"].tolist(), lookback)
        # SMA: rolling mean with min_periods=sma_period so early bars are NaN
        data["sma"] = data["close"].rolling(window=self.sma_period, min_periods=self.sma_period).mean()

        # Prepare data lists
        opens = data["open"].tolist() if "open" in data.columns else data["close"].tolist()
        highs = data["high"].tolist()
        lows = data["low"].tolist()
        closes = data["close"].tolist()

        # Track positions
        position: Literal["buy", "sell", None] = None
        entry_price = 0.0
        entry_idx = 0
        tp_price = 0.0
        sl_price = 0.0
        entry_confidence = 0.0
        entry_patterns: list[str] = []
        entry_divergence: str | None = None
        entry_rsi = 0.0

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
            timestamp = data.index[i] if isinstance(data.index[i], datetime) else datetime.now()
            close = closes[i]
            high = highs[i]
            low = lows[i]
            rsi = data["rsi"].iloc[i]
            # ATR needs period+1 bars, include current bar
            atr = self._calculate_atr(
                highs[max(0, i - atr_period) : i + 1],
                lows[max(0, i - atr_period) : i + 1],
                closes[max(0, i - atr_period) : i + 1],
                atr_period,
            )

            if pd.isna(rsi) or atr is None or atr <= 0:
                continue

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
                    # Position closed
                    if result == "tp":
                        if position == "buy":
                            pnl_pct = (tp_price - entry_price) / entry_price
                            pnl = balance * self.risk_per_trade * (self.reward_ratio / 2)
                        else:
                            pnl_pct = (entry_price - tp_price) / entry_price
                            pnl = balance * self.risk_per_trade * (self.reward_ratio / 2)
                        exit_reason: Literal["tp", "sl", "time", "signal"] = "tp"
                    else:  # SL hit
                        pnl_pct = -self.risk_per_trade
                        pnl = -balance * self.risk_per_trade
                        exit_reason = "sl"

                    balance += pnl
                    if balance > peak_balance:
                        peak_balance = balance

                    trade = Trade(
                        entry_time=data.index[entry_idx]
                        if isinstance(data.index[entry_idx], datetime)
                        else datetime.now(),
                        exit_time=timestamp,
                        side=position,
                        entry_price=entry_price,
                        exit_price=tp_price if result == "tp" else sl_price,
                        tp_price=tp_price,
                        sl_price=sl_price,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        exit_reason=exit_reason,
                        confidence=entry_confidence,
                        patterns=entry_patterns,
                        divergence=entry_divergence,
                        rsi_entry=entry_rsi,
                        rsi_exit=float(rsi),
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
                    # Force close at current price
                    if position == "buy":
                        pnl_pct = (close - entry_price) / entry_price
                    else:
                        pnl_pct = (entry_price - close) / entry_price
                    pnl = balance * pnl_pct
                    balance += pnl

                    trade = Trade(
                        entry_time=data.index[entry_idx]
                        if isinstance(data.index[entry_idx], datetime)
                        else datetime.now(),
                        exit_time=timestamp,
                        side=position,
                        entry_price=entry_price,
                        exit_price=close,
                        tp_price=tp_price,
                        sl_price=sl_price,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        exit_reason="time",
                        confidence=entry_confidence,
                        patterns=entry_patterns,
                        divergence=entry_divergence,
                        rsi_entry=entry_rsi,
                        rsi_exit=float(rsi),
                    )
                    trades.append(trade)
                    equity_curve.append(balance)
                    position = None
                    entry_idx = i
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

                # SMA alignment gate
                if (
                    signal != SignalType.HOLD
                    and self.use_sma_alignment
                    and not pd.isna(sma_val)
                ):
                    if signal == SignalType.BUY and close >= sma_val:
                        signal = SignalType.HOLD
                    elif signal == SignalType.SELL and close <= sma_val:
                        signal = SignalType.HOLD

                # Only enter with sufficient confidence
                if signal != SignalType.HOLD and confidence >= 0.4:
                    position = "buy" if signal == SignalType.BUY else "sell"
                    pip_size = 0.01 if "JPY" in symbol else 0.0001
                    spread_price = self.spread_pips * pip_size
                    entry_price = (
                        close + spread_price if position == "buy" else close - spread_price
                    )
                    entry_idx = i
                    entry_confidence = confidence
                    entry_patterns = pattern_names
                    entry_divergence = div_type
                    entry_rsi = float(rsi)

                    # Set TP/SL based on ATR
                    if position == "buy":
                        tp_price = entry_price + (atr * self.reward_ratio)
                        sl_price = entry_price - (atr * self.sl_atr_multiplier)
                    else:
                        tp_price = entry_price - (atr * self.reward_ratio)
                        sl_price = entry_price + (atr * self.sl_atr_multiplier)

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

        start_date = data.index[max(lookback, atr_period + 1)]
        end_date = data.index[-1]

        return EnhancedBacktestResult(
            symbol=symbol,
            start_date=start_date if isinstance(start_date, datetime) else datetime.now(),
            end_date=end_date if isinstance(end_date, datetime) else datetime.now(),
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
