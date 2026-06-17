"""Telegram notifications for manual trading agent."""

from __future__ import annotations

import asyncio
import importlib
import logging
import threading
from collections.abc import Callable, Coroutine
from types import ModuleType
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from src.indicators.candlestick import CandlePattern
    from src.indicators.rsi import Divergence

logger = logging.getLogger(__name__)


class _HttpxResponse(Protocol):
    status_code: int
    text: str


class _HttpxClient(Protocol):
    async def __aenter__(self) -> _HttpxClient: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None: ...

    async def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        timeout: float,
    ) -> _HttpxResponse: ...


HttpxClientFactory = Callable[[], _HttpxClient]


class _DivergenceLike(Protocol):
    strength: float
    divergence_type: object


class TelegramNotifier:
    """Send notifications via Telegram bot."""

    def __init__(self, bot_token: str | None, chat_id: str | None):
        self.enabled = bool(bot_token and chat_id)
        self._bot_token = bot_token
        self._chat_id = chat_id

        if self.enabled:
            logger.info("Telegram notifications enabled")
        else:
            logger.debug("Telegram notifications disabled (missing token or chat_id)")

    def is_configured(self) -> bool:
        """Check if Telegram is configured."""
        return self.enabled

    async def send(self, message: str, parse_mode: str = "Markdown") -> bool:
        """Send a message via Telegram."""
        if not self.enabled or not self._bot_token or not self._chat_id:
            logger.debug("Telegram notifications disabled")
            return False

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"

        try:
            httpx_module: ModuleType = importlib.import_module("httpx")
            client_factory = cast(HttpxClientFactory, httpx_module.AsyncClient)
            async with client_factory() as client:
                response = await client.post(
                    url,
                    json={
                        "chat_id": self._chat_id,
                        "text": message,
                        "parse_mode": parse_mode,
                    },
                    timeout=10.0,
                )

                if response.status_code == 200:
                    logger.info(f"Telegram message sent: {message[:50]}...")
                    return True
                else:
                    logger.error(f"Failed to send Telegram message: {response.text}")
                    return False

        except Exception as e:
            from src.notifications.telegram_security import redact_telegram_secrets

            logger.error(
                "Error sending Telegram message: %s",
                redact_telegram_secrets(str(e), self._bot_token),
            )
            return False

    # Alias for compatibility
    send_message = send

    def dispatch(self, coro: Coroutine[object, object, object], context: str) -> None:
        """Dispatch a coroutine to run asynchronously."""
        if not self.enabled:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            thread = threading.Thread(
                target=self._run_in_thread,
                args=(coro, context),
                daemon=True,
            )
            thread.start()
            return

        _ = loop.create_task(self._wrap_send(coro, context))

    async def _wrap_send(self, coro: Coroutine[object, object, object], context: str) -> None:
        try:
            await coro
        except Exception as exc:
            logger.error("Telegram notification failed (%s): %s", context, exc)

    def _run_in_thread(self, coro: Coroutine[object, object, object], context: str) -> None:
        try:
            asyncio.run(self._wrap_send(coro, context))
        except Exception as exc:
            logger.error("Telegram notification failed (%s): %s", context, exc)

    async def send_trade_outcome(
        self,
        pair: str,
        direction: str,
        entry: float,
        tp: float,
        sl: float,
        outcome: str,
        tp_pips: float,
        sl_pips: float,
        bars_held: int,
    ) -> None:
        """Send TP hit or SL hit notification."""
        if outcome == "tp":
            emoji = "✅"
            label = "TP Hit"
            exit_price = tp
            result = f"+{tp_pips:.1f} pips"
        else:
            emoji = "❌"
            label = "SL Hit"
            exit_price = sl
            result = f"-{sl_pips:.1f} pips"

        message = (
            f"{emoji} *{label}* — {direction}\n\n"
            f"Pair: `{pair}`\n"
            f"Entry: `{entry:.5f}`\n"
            f"Exit: `{exit_price:.5f}`\n"
            f"Result: `{result}`\n"
            f"Bars held: `{bars_held}`"
        )
        _ = await self.send(message)

    async def send_signal(
        self,
        pair: str,
        direction: str,
        rsi_1h: float,
        rsi_30m: float,
        rsi_15m: float,
        price: float,
        hh: float,
        ll: float,
        entry: float | None = None,
        tp: float | None = None,
        sl: float | None = None,
        patterns: list[CandlePattern] | None = None,
        divergence: Divergence | None = None,
        adx: float | None = None,
        plus_di: float | None = None,
        minus_di: float | None = None,
        ema_context: str | None = None,
    ) -> None:
        """Send signal alert with enhanced pattern/divergence/EMA info."""
        emoji = "🟢" if direction == "BUY" else "🔴"

        # Build pattern info
        pattern_text = ""
        if patterns:
            from src.indicators.candlestick import PatternType
            bullish = [p for p in patterns if hasattr(p, "pattern_type") and p.pattern_type == PatternType.BULLISH]
            bearish = [p for p in patterns if hasattr(p, "pattern_type") and p.pattern_type == PatternType.BEARISH]
            if bullish:
                pattern_text += f"\n🟢 Patterns: {', '.join(p.name for p in bullish)}"
            if bearish:
                pattern_text += f"\n🔴 Patterns: {', '.join(p.name for p in bearish)}"

        # Build divergence info
        div_text = ""
        if divergence and hasattr(divergence, "divergence_type"):
            from src.indicators.rsi import DivergenceType
            typed_divergence = cast(_DivergenceLike, divergence)
            if typed_divergence.divergence_type == DivergenceType.BULLISH:
                div_text += f"\n📈 Bullish Divergence (strength: {typed_divergence.strength:.2f})"
            elif typed_divergence.divergence_type == DivergenceType.BEARISH:
                div_text += f"\n📉 Bearish Divergence (strength: {typed_divergence.strength:.2f})"

        # Build ADX/DI context line — warn when directional bias opposes signal
        adx_text = ""
        if adx is not None and plus_di is not None and minus_di is not None:
            opposing = (direction == "BUY" and minus_di > plus_di * 2) or (
                direction == "SELL" and plus_di > minus_di * 2
            )
            warn = " ⚠️" if opposing else ""
            adx_text = f"\nADX: `{adx:.1f}` | +DI: `{plus_di:.1f}` | -DI: `{minus_di:.1f}`{warn}"

        # Build message with entry/TP/SL if provided
        news_text = "\nNews: `clear` (no 3-star block)"
        invalidation_hint = "\nInvalidate on: 15m RSI cross of 50 or close back through 20-bar extreme"

        if entry is not None and tp is not None and sl is not None:
            pip_mult = 100 if "JPY" in pair else 10000
            tp_pips = abs(tp - entry) * pip_mult
            sl_pips = abs(sl - entry) * pip_mult

            message = (
                f"{emoji} *{direction} Signal*\n\n"
                f"Pair: `{pair}`\n"
                f"Entry: `{entry:.5f}`\n"
                f"TP: `{tp:.5f}` ({tp_pips:.1f} pips)\n"
                f"SL: `{sl:.5f}` ({sl_pips:.1f} pips)\n\n"
                f"RSI(14):\n"
                f"  15m: `{rsi_15m:.1f}`"
                f"{adx_text}\n\n"
                f"20-bar Range:\n"
                f"  High: `{hh:.5f}`\n"
                f"  Low: `{ll:.5f}`"
                f"{news_text}"
                f"{invalidation_hint}"
                f"{pattern_text}"
                f"{div_text}"
            )
        else:
            message = (
                f"{emoji} *{direction} Signal*\n\n"
                f"Pair: `{pair}`\n"
                f"Price: `{price:.5f}`\n\n"
                f"RSI(14):\n"
                f"  1h: `{rsi_1h:.1f}`\n"
                f"  30m: `{rsi_30m:.1f}`\n"
                f"  15m: `{rsi_15m:.1f}`"
                f"{adx_text}\n\n"
                f"20-bar Range:\n"
                f"  High: `{hh:.5f}`\n"
                f"  Low: `{ll:.5f}`"
                f"{news_text}"
                f"{invalidation_hint}"
                f"{pattern_text}"
                f"{div_text}"
            )

        if ema_context:
            message += f"\n\n{ema_context}"

        _ = await self.send(message)

    async def send_ema_crossover(
        self,
        pair: str,
        direction: str,
        fast_ema: float,
        slow_ema: float,
        fast_period: int,
        slow_period: int,
        timeframe: str,
    ) -> None:
        """Send EMA crossover signal (Golden Cross or Death Cross)."""
        if direction == "bullish":
            emoji = "🟢"
            cross_label = "Golden Cross"
        else:
            emoji = "🔴"
            cross_label = "Death Cross"

        message = (
            f"{emoji} *EMA Crossover* — {cross_label}\n\n"
            f"Pair: `{pair}`\n"
            f"Timeframe: `{timeframe}`\n"
            f"EMA({fast_period}): `{fast_ema:.5f}`\n"
            f"EMA({slow_period}): `{slow_ema:.5f}`"
        )
        _ = await self.send(message)

    async def send_ema_price_touch(
        self,
        pair: str,
        price: float,
        ema_value: float,
        ema_period: int,
        timeframe: str,
        touch_type: str,
        distance_pips: float,
    ) -> None:
        """Send price-EMA touch/break notification."""
        if touch_type == "cross_above":
            emoji = "🟢"
            desc = f"Price crossed *above* EMA({ema_period})"
        elif touch_type == "cross_below":
            emoji = "🔴"
            desc = f"Price crossed *below* EMA({ema_period})"
        elif touch_type == "above":
            emoji = "📊"
            desc = f"Price touching EMA({ema_period}) from above"
        else:
            emoji = "📊"
            desc = f"Price touching EMA({ema_period}) from below"

        message = (
            f"{emoji} *EMA Price Touch*\n\n"
            f"Pair: `{pair}`\n"
            f"Timeframe: `{timeframe}`\n"
            f"{desc}\n"
            f"Price: `{price:.5f}`\n"
            f"EMA({ema_period}): `{ema_value:.5f}`\n"
            f"Distance: `{distance_pips:.1f}` pips"
        )
        _ = await self.send(message)

    async def send_ema_slope(
        self,
        pair: str,
        ema_period: int,
        slope_direction: str,
        current_value: float,
        timeframe: str,
    ) -> None:
        """Send EMA slope/direction notification."""
        emoji = "📈" if slope_direction == "rising" else "📉"
        message = (
            f"{emoji} *EMA Slope*\n\n"
            f"Pair: `{pair}`\n"
            f"Timeframe: `{timeframe}`\n"
            f"EMA({ema_period}) is `{slope_direction.upper()}`\n"
            f"Current: `{current_value:.5f}`"
        )
        _ = await self.send(message)

    async def send_scan_error(self, pair: str, error: str) -> None:
        """Send scan error alert."""
        message = f"⚠️ *Scan Error*\n\nPair: `{pair}`\nError: `{error}`"
        _ = await self.send(message)


# Global instance
_notifier: TelegramNotifier | None = None


def get_notifier() -> TelegramNotifier:
    """Get or create the global notifier instance."""
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier(None, None)
    return _notifier


async def notify_signal(*args, **kwargs) -> bool:
    """Convenience function to send signal notification."""
    notifier = get_notifier()
    await notifier.send_signal(*args, **kwargs)
    return True
