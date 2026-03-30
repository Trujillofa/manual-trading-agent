"""Telegram notification service for trading alerts."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Send trading signals to Telegram."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.base_url = f"https://api.telegram.org/bot/{self.bot_token}"
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(timeout=10.0)
        return self._client

    def is_configured(self) -> bool:
        """Check if Telegram is configured."""
        return bool(self.bot_token and self.chat_id)

    async def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Send a message to Telegram."""
        if not self.is_configured():
            logger.debug("Telegram not configured, skipping notification")
            return False

        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }

        try:
            client = self._get_client()
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            if data.get("ok"):
                logger.info(f"Telegram message sent: {text[:50]}...")
                return True
            else:
                logger.error(f"Telegram API error: {data}")
                return False
        except httpx.HTTPError as e:
            logger.error(f"Telegram HTTP error: {e}")
            return False
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False

    async def send_signal(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        tp_price: float | None,
        sl_price: float | None,
        rsi_1h: float,
        rsi_30m: float,
        rsi_15m: float,
        confidence: float = 0.0,
    ) -> bool:
        """Send a trading signal notification."""
        if not self.is_configured():
            return False

        direction = "🟢 LONG" if side == "buy" else "🔴 SHORT"
        rsi_status = f"RSI: {rsi_1h:.0f} | {rsi_30m:.0f} | {rsi_15m:.0f}"

        message = f"""
{direction} *{symbol}*

📊 *Signal Details*
• Entry: `{entry_price:.5f}`
• TP: `{tp_price:.5f}` (+${tp_price:.2f}%)
• SL: `{sl_price:.5f}` (-${sl_price:.2f}%)
• Confidence: {confidence:.0%}

📈 *MTF RSI Alignment*
{rsi_status}

⏰ {self._get_timestamp()}
"""
        return await self.send_message(message)

    def _get_timestamp(self) -> str:
        """Get formatted timestamp."""
        from datetime import UTC, datetime

        return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


# Global instance
_notifier: TelegramNotifier | None = None


def get_notifier() -> TelegramNotifier:
    """Get or create the global notifier instance."""
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier()
    return _notifier


async def notify_signal(*args, **kwargs) -> bool:
    """Convenience function to send signal notification."""
    notifier = get_notifier()
    return await notifier.send_signal(*args, **kwargs)
