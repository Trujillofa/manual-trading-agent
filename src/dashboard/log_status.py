"""CLI-facing managed log status checks and optional Telegram alerts."""

from __future__ import annotations

from src.config import get_settings
from src.notifications.telegram import TelegramNotifier
from src.scanner.log_monitor import (
    _load_log_alert_state,
    _save_log_alert_state,
    build_log_alert_messages,
    format_log_status_report,
    managed_log_statuses,
)


async def run_logs_status(*, notify: bool = False) -> None:
    statuses = managed_log_statuses()
    print(format_log_status_report(statuses))

    if not notify:
        return

    settings = get_settings()
    if not settings.telegram.enabled or not settings.telegram.is_configured:
        print("Telegram not configured; skipping log alerts.")
        return

    state = _load_log_alert_state()
    messages, next_state = build_log_alert_messages(statuses, state)
    if not messages:
        print("No new log-size alerts.")
        return

    notifier = TelegramNotifier(settings.telegram.bot_token, settings.telegram.chat_id)
    for message in messages:
        sent = await notifier.send(message)
        if sent:
            print(f"Sent alert for managed log ({message.split(chr(10))[1]})")
        else:
            print("Failed to send log-size alert via Telegram.")

    _save_log_alert_state(next_state)
