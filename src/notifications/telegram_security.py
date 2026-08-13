"""Telegram secret redaction and single-poller coordination."""

from __future__ import annotations

import logging
import os
import re
from contextlib import AbstractContextManager
from pathlib import Path

logger = logging.getLogger(__name__)

_TELEGRAM_BOT_URL = re.compile(r"https://api\.telegram\.org/bot[^/\s'\"]+")
_DUPLICATE_CONSUMER_MESSAGE = (
    "duplicate getUpdates consumer (another telegram-poll process or webhook is active)"
)
_RATE_LIMITED_MESSAGE = "Telegram getUpdates rate limited (429)"


class TelegramPollHTTPError(RuntimeError):
    """Polling failure that may carry HTTP status / Retry-After metadata."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def parse_telegram_retry_after(response: object) -> float | None:
    """Extract Retry-After seconds from a Telegram HTTP response, if present."""
    headers = getattr(response, "headers", None) or {}
    header_val = None
    try:
        header_val = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:
        header_val = None

    body_val = None
    try:
        payload = response.json()  # type: ignore[attr-defined]
        if isinstance(payload, dict):
            params = payload.get("parameters")
            if isinstance(params, dict) and params.get("retry_after") is not None:
                body_val = params.get("retry_after")
    except Exception:
        body_val = None

    for raw in (body_val, header_val):
        if raw is None:
            continue
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            return seconds
    return None


def redact_telegram_secrets(text: str, bot_token: str | None = None) -> str:
    """Remove bot tokens from URLs and raw token substrings."""
    redacted = _TELEGRAM_BOT_URL.sub("https://api.telegram.org/bot<REDACTED>", text)
    if bot_token:
        redacted = redacted.replace(bot_token, "<REDACTED>")
    return redacted


def format_telegram_poll_error(exc: BaseException, bot_token: str | None = None) -> str:
    """Return a heartbeat-safe Telegram polling error message."""
    if isinstance(exc, TelegramPollHTTPError):
        if exc.status_code == 409:
            return _DUPLICATE_CONSUMER_MESSAGE
        if exc.status_code == 429:
            if exc.retry_after is not None:
                return f"{_RATE_LIMITED_MESSAGE}; retry_after={exc.retry_after:g}s"
            return _RATE_LIMITED_MESSAGE

    status_code = getattr(exc, "response", None)
    if status_code is not None:
        status_code = getattr(status_code, "status_code", None)
    if status_code == 409:
        return _DUPLICATE_CONSUMER_MESSAGE
    if status_code == 429:
        retry_after = parse_telegram_retry_after(getattr(exc, "response", None))
        if retry_after is not None:
            return f"{_RATE_LIMITED_MESSAGE}; retry_after={retry_after:g}s"
        return _RATE_LIMITED_MESSAGE
    return redact_telegram_secrets(str(exc), bot_token)


def log_telegram_poll_error(exc: BaseException, bot_token: str | None = None) -> None:
    """Log polling failures without leaking bot tokens."""
    logger.error("Telegram polling loop failed: %s", format_telegram_poll_error(exc, bot_token))


class TelegramPollLock(AbstractContextManager["TelegramPollLock"]):
    """Process-wide lock so only one telegram-poll holds getUpdates per logs dir."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._fd: int | None = None

    def acquire(self) -> bool:
        import fcntl

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False

        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        import fcntl

        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> TelegramPollLock:
        if not self.acquire():
            raise TelegramPollLockError(
                "another telegram-poll process already holds the getUpdates lock"
            )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        self.release()


class TelegramPollLockError(RuntimeError):
    """Raised when a second local telegram-poll tries to start."""
