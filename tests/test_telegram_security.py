"""Tests for Telegram secret redaction and poll coordination."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.notifications import telegram_commands
from src.notifications.telegram_commands import TelegramCommandHandler
from src.notifications.telegram_security import (
    TelegramPollHTTPError,
    TelegramPollLock,
    format_telegram_poll_error,
    parse_telegram_retry_after,
    redact_telegram_secrets,
)


def test_redact_telegram_secrets_masks_bot_url() -> None:
    token = "123456789:TEST_TOKEN_FOR_REDACTION_ONLY"
    raw = (
        "Client error '409 Conflict' for url "
        f"'https://api.telegram.org/bot{token}/getUpdates?offset=1&timeout=20&limit=20'"
    )

    redacted = redact_telegram_secrets(raw, token)

    assert token not in redacted
    assert "https://api.telegram.org/bot<REDACTED>/getUpdates" in redacted


def test_format_telegram_poll_error_from_http_status_error() -> None:
    token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
    request = httpx.Request(
        "GET",
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"offset": 1, "timeout": 20, "limit": 20},
    )
    response = httpx.Response(409, request=request)
    exc = httpx.HTTPStatusError("conflict", request=request, response=response)

    message = format_telegram_poll_error(exc, token)

    assert token not in message
    assert "duplicate getUpdates consumer" in message


@pytest.mark.asyncio
async def test_get_updates_raises_telegram_poll_http_error_on_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    offset_path = tmp_path / "telegram_offset.json"
    monkeypatch.setattr(telegram_commands, "OFFSET_PATH", offset_path)

    token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
    handler = TelegramCommandHandler(token, "123")

    request = httpx.Request("GET", f"https://api.telegram.org/bot{token}/getUpdates")
    response = httpx.Response(409, request=request)
    http_error = httpx.HTTPStatusError("conflict", request=request, response=response)

    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = http_error
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(handler, "_get_client", AsyncMock(return_value=mock_client))

    with pytest.raises(TelegramPollHTTPError, match="duplicate getUpdates consumer") as exc_info:
        await handler.get_updates()

    assert exc_info.value.status_code == 409
    assert token not in str(exc_info.value)


def test_parse_telegram_retry_after_prefers_body_parameters() -> None:
    request = httpx.Request("GET", "https://api.telegram.org/botTOKEN/getUpdates")
    response = httpx.Response(
        429,
        headers={"Retry-After": "10"},
        json={"ok": False, "parameters": {"retry_after": 42}},
        request=request,
    )
    assert parse_telegram_retry_after(response) == 42.0


@pytest.mark.asyncio
async def test_run_forever_honors_retry_after_on_429(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heartbeat_path = tmp_path / "telegram_heartbeat.json"
    offset_path = tmp_path / "telegram_offset.json"
    monkeypatch.setattr(telegram_commands, "HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(telegram_commands, "OFFSET_PATH", offset_path)

    handler = TelegramCommandHandler("token", "123")
    slept: list[float] = []

    async def fail_updates() -> list[dict[str, object]]:
        raise TelegramPollHTTPError(
            "Telegram getUpdates rate limited (429); retry_after=17s",
            status_code=429,
            retry_after=17.0,
        )

    async def capture_sleep(seconds: float) -> None:
        slept.append(seconds)
        raise SystemExit("stop after 429")

    monkeypatch.setattr(handler, "get_updates", fail_updates)
    monkeypatch.setattr(telegram_commands.asyncio, "sleep", capture_sleep)

    with pytest.raises(SystemExit, match="stop after 429"):
        await handler.run_forever()

    assert slept == [17.0]
    payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    assert payload["status"] == "error"
    assert "rate limited" in payload.get("error", "")


@pytest.mark.asyncio
async def test_run_forever_writes_redacted_error_heartbeat_on_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heartbeat_path = tmp_path / "telegram_heartbeat.json"
    offset_path = tmp_path / "telegram_offset.json"
    monkeypatch.setattr(telegram_commands, "HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(telegram_commands, "OFFSET_PATH", offset_path)

    token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
    handler = TelegramCommandHandler(token, "123")

    async def fail_updates() -> list[dict[str, object]]:
        raise RuntimeError(
            format_telegram_poll_error(
                httpx.HTTPStatusError(
                    "conflict",
                    request=httpx.Request("GET", f"https://api.telegram.org/bot{token}/getUpdates"),
                    response=httpx.Response(
                        409,
                        request=httpx.Request(
                            "GET", f"https://api.telegram.org/bot{token}/getUpdates"
                        ),
                    ),
                ),
                token,
            )
        )

    async def stop_sleep(_: float) -> None:
        raise SystemExit("stop after error")

    monkeypatch.setattr(handler, "get_updates", fail_updates)
    monkeypatch.setattr(telegram_commands.asyncio, "sleep", stop_sleep)

    with pytest.raises(SystemExit, match="stop after error"):
        await handler.run_forever()

    payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    assert payload["status"] == "error"
    assert token not in payload.get("error", "")
    assert "duplicate getUpdates consumer" in payload.get("error", "")


def test_telegram_poll_lock_prevents_second_acquire(tmp_path: Path) -> None:
    lock_path = tmp_path / "telegram_poll.lock"
    first = TelegramPollLock(lock_path)
    second = TelegramPollLock(lock_path)

    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
