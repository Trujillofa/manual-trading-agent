"""Multi-timeframe RSI strategy for manual forex trading."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from src.config import get_settings
from src.indicators.high_low import highest_high, is_breakout_high, is_breakout_low, lowest_low
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalConfidence, SignalType


@dataclass
class MTFRSISignal:
    """MTF RSI signal data."""

    rsi_1h: float | None
    rsi_30m: float | None
    rsi_15m: float | None
    hh_15m: float | None
    ll_15m: float | None
    close_15m: float
    aligned: bool
    direction: Literal["bullish", "bearish"] | None


class MTFRSIStrategy(BaseStrategy):
    """Multi-timeframe RSI strategy.

    Entry rules:
    - RSI > 70 across 1h, 30m, 15m AND price breaks 15m highest high -> BUY
    - RSI < 30 across 1h, 30m, 15m AND price breaks 15m lowest low -> SELL
    """

    REQUIRED_TIMEFRAMES = {
        "regime": "1h",
        "momentum": "30m",
        "entry": "15m",
    }

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        news_checker=None,
    ) -> None:
        super().__init__(config)
        self.settings = get_settings()
        self.strategy_config = self.settings.strategy
        self.risk_config = self.settings.risk
        self.news_checker = news_checker

    def _classify_mtf_signal(self, indicators: dict[str, float]) -> MTFRSISignal | None:
        """Extract and classify MTF RSI signal from indicators dict."""
        rsi_1h = indicators.get("rsi_1h")
        rsi_30m = indicators.get("rsi_30m")
        rsi_15m = indicators.get("rsi_15m")
        hh_15m = indicators.get("hh_15m")
        ll_15m = indicators.get("ll_15m")
        close_15m = indicators.get("close_15m")

        if hh_15m is None:
            highs_15m = indicators.get("highs_15m")
            if isinstance(highs_15m, list) and all(isinstance(x, int | float) for x in highs_15m):
                hh_15m = highest_high(
                    [float(x) for x in highs_15m], self.strategy_config.lookback_bars
                )

        if ll_15m is None:
            lows_15m = indicators.get("lows_15m")
            if isinstance(lows_15m, list) and all(isinstance(x, int | float) for x in lows_15m):
                ll_15m = lowest_low(
                    [float(x) for x in lows_15m], self.strategy_config.lookback_bars
                )

        if any(v is None for v in (rsi_1h, rsi_30m, rsi_15m, hh_15m, ll_15m, close_15m)):
            return None

        assert rsi_1h is not None
        assert rsi_30m is not None
        assert rsi_15m is not None
        assert hh_15m is not None
        assert ll_15m is not None
        assert close_15m is not None

        rsi_1h_f = float(rsi_1h)
        rsi_30m_f = float(rsi_30m)
        rsi_15m_f = float(rsi_15m)
        hh_15m_f = float(hh_15m)
        ll_15m_f = float(ll_15m)
        close_15m_f = float(close_15m)

        overbought = all(
            rsi > self.strategy_config.rsi_overbought for rsi in [rsi_1h_f, rsi_30m_f, rsi_15m_f]
        )
        oversold = all(
            rsi < self.strategy_config.rsi_oversold for rsi in [rsi_1h_f, rsi_30m_f, rsi_15m_f]
        )
        aligned = overbought or oversold

        direction: Literal["bullish", "bearish"] | None
        if overbought:
            direction = "bearish"
        elif oversold:
            direction = "bullish"
        else:
            direction = None

        return MTFRSISignal(
            rsi_1h=rsi_1h_f,
            rsi_30m=rsi_30m_f,
            rsi_15m=rsi_15m_f,
            hh_15m=hh_15m_f,
            ll_15m=ll_15m_f,
            close_15m=close_15m_f,
            aligned=aligned,
            direction=direction,
        )

    def _calculate_confidence(self, mtf: MTFRSISignal) -> tuple[float, SignalConfidence]:
        """Calculate signal confidence."""
        if not mtf.aligned or mtf.direction is None:
            return 0.0, SignalConfidence.LOW

        if any(v is None for v in (mtf.rsi_1h, mtf.rsi_30m, mtf.rsi_15m)):
            return 0.0, SignalConfidence.LOW

        assert mtf.rsi_1h is not None
        assert mtf.rsi_30m is not None
        assert mtf.rsi_15m is not None

        avg_rsi = (mtf.rsi_1h + mtf.rsi_30m + mtf.rsi_15m) / 3
        if mtf.direction == "bearish":
            rsi_extreme = avg_rsi - self.strategy_config.rsi_overbought
            confidence = min(1.0, 0.5 + rsi_extreme / 30)
        else:
            rsi_extreme = self.strategy_config.rsi_oversold - avg_rsi
            confidence = min(1.0, 0.5 + rsi_extreme / 30)

        if confidence >= 0.8:
            level = SignalConfidence.HIGH
        elif confidence >= 0.6:
            level = SignalConfidence.MEDIUM
        else:
            level = SignalConfidence.LOW

        return confidence, level

    def _check_breakout(self, mtf: MTFRSISignal) -> bool:
        """Check if price breaks highest high or lowest low."""
        if mtf.direction == "bearish":
            if mtf.hh_15m is None:
                return False
            return is_breakout_high(mtf.close_15m, mtf.hh_15m)
        if mtf.direction == "bullish":
            if mtf.ll_15m is None:
                return False
            return is_breakout_low(mtf.close_15m, mtf.ll_15m)
        return False

    async def _is_news_blocking(self, symbol: str) -> bool:
        """Check whether injected news checker blocks this symbol."""
        if self.news_checker is None:
            return False

        for method_name in ("is_symbol_blocked", "is_blocked", "should_block_symbol"):
            method = getattr(self.news_checker, method_name, None)
            if method is None:
                continue
            result = method(symbol)
            if inspect.isawaitable(result):
                return bool(await result)
            return bool(result)

        return False

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal | None:
        """Evaluate MTF RSI strategy and generate signal."""
        mtf = self._classify_mtf_signal(indicators)
        if mtf is None:
            return None

        if not mtf.aligned:
            return None

        if not self._check_breakout(mtf):
            return None

        if await self._is_news_blocking(symbol):
            return None

        confidence, confidence_level = self._calculate_confidence(mtf)

        if mtf.direction == "bearish":
            side: Literal["buy", "sell"] = "sell"
            entry = indicators.get("close_15m")
            tp = indicators.get("tp_price")
            sl = indicators.get("sl_price")
            reason = (
                f"SELL: RSI overbought ({mtf.rsi_1h:.1f}, {mtf.rsi_30m:.1f}, {mtf.rsi_15m:.1f}) "
                "aligned bearish, broke 15m high"
            )
        elif mtf.direction == "bullish":
            side = "buy"
            entry = indicators.get("close_15m")
            tp = indicators.get("tp_price")
            sl = indicators.get("sl_price")
            reason = (
                f"BUY: RSI oversold ({mtf.rsi_1h:.1f}, {mtf.rsi_30m:.1f}, {mtf.rsi_15m:.1f}) "
                "aligned bullish, broke 15m low"
            )
        else:
            return None

        return Signal(
            symbol=symbol,
            side=side,
            signal_type=SignalType.BUY if side == "buy" else SignalType.SELL,
            confidence=confidence,
            confidence_level=confidence_level,
            entry_price=entry,
            tp_price=tp,
            sl_price=sl,
            lot_size=self.settings.trading.lot_size,
            reason=reason,
            timestamp_utc=datetime.now(UTC),
            indicators={
                "rsi_1h": cast(float, mtf.rsi_1h),
                "rsi_30m": cast(float, mtf.rsi_30m),
                "rsi_15m": cast(float, mtf.rsi_15m),
                "hh_15m": cast(float, mtf.hh_15m),
                "ll_15m": cast(float, mtf.ll_15m),
                "tp_usd": self.risk_config.tp_usd,
                "sl_usd": self.risk_config.sl_usd,
            },
        )
