"""Tests for Telegram polling heartbeat transitions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.notifications import telegram_commands
from src.notifications.telegram_commands import TelegramCommandHandler


@pytest.mark.asyncio
async def test_run_forever_writes_starting_heartbeat_before_first_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heartbeat_path = tmp_path / "telegram_heartbeat.json"
    offset_path = tmp_path / "telegram_offset.json"
    monkeypatch.setattr(telegram_commands, "HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(telegram_commands, "OFFSET_PATH", offset_path)

    handler = TelegramCommandHandler("token", "123")

    async def stop_immediately() -> list[dict[str, object]]:
        raise SystemExit("stop loop")

    monkeypatch.setattr(handler, "get_updates", stop_immediately)

    with pytest.raises(SystemExit, match="stop loop"):
        await handler.run_forever()

    payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    assert payload["status"] == "starting"
    assert payload.get("updated_at")


@pytest.mark.asyncio
async def test_run_forever_writes_ok_heartbeat_after_successful_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heartbeat_path = tmp_path / "telegram_heartbeat.json"
    offset_path = tmp_path / "telegram_offset.json"
    monkeypatch.setattr(telegram_commands, "HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(telegram_commands, "OFFSET_PATH", offset_path)

    handler = TelegramCommandHandler("token", "123")

    calls = {"count": 0}

    async def get_updates_once_then_stop() -> list[dict[str, object]]:
        calls["count"] += 1
        if calls["count"] == 1:
            return []
        raise SystemExit("stop loop")

    monkeypatch.setattr(handler, "get_updates", get_updates_once_then_stop)

    with pytest.raises(SystemExit, match="stop loop"):
        await handler.run_forever()

    payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert payload.get("updated_at")


@pytest.mark.asyncio
async def test_run_forever_writes_error_heartbeat_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heartbeat_path = tmp_path / "telegram_heartbeat.json"
    offset_path = tmp_path / "telegram_offset.json"
    monkeypatch.setattr(telegram_commands, "HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(telegram_commands, "OFFSET_PATH", offset_path)

    handler = TelegramCommandHandler("token", "123")

    async def fail_updates() -> list[dict[str, object]]:
        raise RuntimeError("boom")

    async def stop_sleep(_: float) -> None:
        raise SystemExit("stop after error")

    monkeypatch.setattr(handler, "get_updates", fail_updates)
    monkeypatch.setattr(telegram_commands.asyncio, "sleep", stop_sleep)

    with pytest.raises(SystemExit, match="stop after error"):
        await handler.run_forever()

    payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    assert payload["status"] == "error"
    assert "boom" in payload.get("error", "")
    assert payload.get("updated_at")
