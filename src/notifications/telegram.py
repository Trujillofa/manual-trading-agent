"""Telegram notifications for manual trading agent."""

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
    async def __aenter__(self) -> "_HttpxClient": ...

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
            logger.error(f"Error sending Telegram message: {e}")
            return False

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
        patterns: list | None = None,
        divergence: object | None = None,
    ) -> None:
        """Send signal alert with enhanced pattern/divergence info."""
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
            if divergence.divergence_type == DivergenceType.BULLISH:
                div_text += f"\n📈 Bullish Divergence (strength: {divergence.strength:.2f})"
            elif divergence.divergence_type == DivergenceType.BEARISH:
                div_text += f"\n📉 Bearish Divergence (strength: {divergence.strength:.2f})"

        # Build message with entry/TP/SL if provided
        if entry is not None and tp is not None and sl is not None:
            # Determine pip multiplier for display
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
                f"  15m: `{rsi_15m:.1f}`\n\n"
                f"20-bar Range:\n"
                f"  High: `{hh:.5f}`\n"
                f"  Low: `{ll:.5f}`"
                f"{pattern_text}"
                f"{div_text}"
            )
        else:
            # Fallback to basic message
            message = (
                f"{emoji} *{direction} Signal*\n\n"
                f"Pair: `{pair}`\n"
                f"Price: `{price:.5f}`\n\n"
                f"RSI(14):\n"
                f"  1h: `{rsi_1h:.1f}`\n"
                f"  30m: `{rsi_30m:.1f}`\n"
                f"  15m: `{rsi_15m:.1f}`\n\n"
                f"20-bar Range:\n"
                f"  High: `{hh:.5f}`\n"
                f"  Low: `{ll:.5f}`"
                f"{pattern_text}"
                f"{div_text}"
            )

        _ = await self.send(message)

    async def send_scan_error(self, pair: str, error: str) -> None:
        """Send scan error alert."""
        message = f"⚠️ *Scan Error*\n\nPair: `{pair}`\nError: `{error}`"
        _ = await self.send(message)