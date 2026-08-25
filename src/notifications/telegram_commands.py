"""Telegram command polling for manual trading agent."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from src.news.news_checker import NewsChecker, format_telegram_news_report
from src.notifications.telegram_security import (
    TelegramPollHTTPError,
    format_telegram_poll_error,
    log_telegram_poll_error,
    parse_telegram_retry_after,
)

OFFSET_PATH = Path("/app/logs/telegram_update_offset.json")
SCAN_LOG_PATH = Path("/app/logs/scan.log")
HEARTBEAT_PATH = Path("/app/logs/telegram_heartbeat.json")
POLL_LOCK_PATH = Path("/app/logs/telegram_poll.lock")
logger = logging.getLogger(__name__)


class TelegramCommandHandler:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.offset = self._load_offset()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=45.0))
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _load_offset(self) -> int:
        if not OFFSET_PATH.exists():
            return 0
        try:
            payload = json.loads(OFFSET_PATH.read_text(encoding="utf-8"))
            return int(payload.get("offset", 0))
        except Exception:
            return 0

    def _save_offset(self) -> None:
        OFFSET_PATH.parent.mkdir(parents=True, exist_ok=True)
        OFFSET_PATH.write_text(json.dumps({"offset": self.offset}), encoding="utf-8")

    def _write_heartbeat(self, status: str, error: str | None = None) -> None:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, str] = {
            "status": status,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if error:
            payload["error"] = error
        HEARTBEAT_PATH.write_text(json.dumps(payload), encoding="utf-8")

    async def get_updates(self) -> list[dict[str, Any]]:
        client = await self._get_client()
        try:
            response = await client.get(
                f"{self.base_url}/getUpdates",
                params={"offset": self.offset, "timeout": 20, "limit": 20},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retry_after = parse_telegram_retry_after(exc.response) if status == 429 else None
            raise TelegramPollHTTPError(
                format_telegram_poll_error(exc, self.bot_token),
                status_code=status,
                retry_after=retry_after,
            ) from None
        payload = response.json()
        if not payload.get("ok"):
            return []
        return list(payload.get("result", []))

    async def send_message(self, text: str) -> None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
            )
            if response.status_code == 400 and "parse entities" in response.text:
                # Markdown formatting failed — retry as plain text so the message is never lost
                logger.warning(
                    "Telegram Markdown parse error, retrying as plain text: %s", response.text
                )
                response = await client.post(
                    f"{self.base_url}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text},
                )
            if not response.is_success:
                logger.error(
                    "Telegram command reply failed: status=%s body=%s",
                    response.status_code,
                    response.text,
                )

    def _read_scan_log(self) -> str:
        if not SCAN_LOG_PATH.exists():
            return ""
        try:
            return SCAN_LOG_PATH.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""

    def _extract_section(self, marker: str, max_lines: int = 10) -> list[str]:
        text = self._read_scan_log()
        if not text:
            return []
        idx = text.rfind(marker)
        if idx == -1:
            return []
        block = text[idx:].splitlines()
        lines: list[str] = []
        for line in block[1 : 1 + max_lines]:
            line = line.rstrip()
            if not line.strip():
                break
            if line.startswith("  "):
                lines.append(line.strip())
            else:
                break
        return lines

    def _extract_last_watchlist(self) -> str:
        lines = self._extract_section("[CLOSEST MTF SETUPS]", max_lines=10)
        if not lines:
            return "No watchlist section found yet."
        formatted = "\n".join(f"• `{line}`" for line in lines[:5])
        return f"*Current Watchlist*\n\n{formatted}"

    def _extract_last_signal(self) -> str:
        text = self._read_scan_log()
        if not text:
            return "No scan log available yet."
        marker = "⚠️ MTF SIGNAL:"
        idx = text.rfind(marker)
        if idx == -1:
            return "No confirmed MTF signal right now."
        snippet = text[max(0, idx - 220) : idx + 500]
        lines = [line.strip() for line in snippet.splitlines() if line.strip()]
        # Keep last relevant chunk
        lines = lines[-8:]
        formatted = "\n".join(f"`{line}`" for line in lines)
        return f"*Latest Confirmed Signal*\n\n{formatted}"

    def _extract_status(self) -> str:
        lines = self._extract_section("[CLOSEST MTF SETUPS]", max_lines=3)
        top = lines[0] if lines else "No ranked setup yet"
        checker = NewsChecker()
        source = checker.get_source_status()
        blocked = ", ".join(sorted(checker.get_blocked_currencies(datetime.now(UTC)))) or "none"
        return (
            "*Manual Trading Bot Status*\n\n"
            "Mode: `paper`\n"
            "Scanner: `MTF RSI (1h+30m+15m)`\n"
            "Breakout check: `enabled`\n"
            "Spread source: `cTrader live endpoint -> OANDA if configured -> static fallback`\n"
            f"News source: `{source}`\n"
            f"Blocked currencies now: `{blocked}`\n"
            "Commands: `online`\n"
            f"Top setup: `{top}`"
        )

    def _extract_news(self) -> str:
        checker = NewsChecker()
        now = datetime.now(UTC)
        blocked = sorted(checker.get_blocked_currencies(now))
        events = checker.get_display_events(24, now)
        return format_telegram_news_report(
            source_status=checker.get_source_status(),
            blocked=blocked,
            events=events,
            now=now,
            readiness=checker.get_surprise_readiness(),
        )

    def _extract_pairs(self) -> str:
        try:
            from src.config import get_settings

            settings = get_settings()
            majors = ", ".join(settings.trading.majors) or "(none)"
            minors = ", ".join(settings.trading.minors) or "(none)"
            return (
                "*Tracked instruments*\n\n"
                f"Majors: {majors}\n\n"
                f"Minors: {minors}\n\n"
                "_Multi-asset Branch B alerts (XAU/BTC/OIL/NASDAQ) — not a validated edge._"
            )
        except Exception:
            return (
                "*Tracked instruments*\n\nMajors: XAU/USD, BTC/USD, OIL, NASDAQ\n\nMinors: (none)"
            )

    async def _run_fresh_scan(self, pair: str | None = None) -> str:
        try:
            cmd = ["python", "-m", "src.cli", "scan"]
            if pair:
                cmd.extend(["--pairs", pair])
            result = subprocess.run(
                cmd,
                cwd="/app",
                capture_output=True,
                text=True,
                timeout=120,
            )
            output = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0:
                return f"Fresh scan failed.\n\n`{output[-1000:]}`"
            if pair:
                marker = f"{pair.strip()}:"
                idx = output.find(marker)
                if idx == -1 and "/" not in pair:
                    alt = f"{pair[:3]}/{pair[3:]}:" if len(pair) >= 6 else marker
                    idx = output.find(alt)
                if idx != -1:
                    block = output[idx:].splitlines()
                    lines: list[str] = []
                    for line in block[:14]:
                        if line.strip() == "" and lines:
                            break
                        lines.append(f"`{line.rstrip()}`")
                    return "*Pair Review*\n\n" + "\n".join(lines)
            idx = output.rfind("[CLOSEST MTF SETUPS]")
            if idx != -1:
                tail = output[idx:].splitlines()
                lines = [line.strip() for line in tail[1:6] if line.startswith("  ")]
                if lines:
                    formatted = "\n".join(f"• `{line}`" for line in lines)
                    return f"*Fresh Scan Complete*\n\n{formatted}"
            return "Fresh scan completed, but no ranked setups were found."
        except Exception as exc:
            return f"Fresh scan failed: `{exc}`"

    async def handle_update(self, update: dict[str, Any]) -> None:
        update_id = int(update.get("update_id", 0))
        self.offset = max(self.offset, update_id + 1)
        self._save_offset()

        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return

        chat = message.get("chat", {})
        if str(chat.get("id")) != self.chat_id:
            return

        text = str(message.get("text", "")).strip()
        if not text.startswith("/"):
            return

        command = text.split()[0].lower()
        # Strip @botname suffix from group commands
        if "@" in command:
            command = command.split("@", 1)[0]
        if command == "/watchlist":
            await self.send_message(self._extract_last_watchlist())
        elif command == "/signal":
            await self.send_message(self._extract_last_signal())
        elif command == "/status":
            await self.send_message(self._extract_status())
        elif command == "/pairs":
            await self.send_message(self._extract_pairs())
        elif command == "/news":
            await self.send_message(self._extract_news())
        elif command == "/scan":
            await self.send_message("Running fresh scan...")
            await self.send_message(await self._run_fresh_scan())
        elif command == "/pair":
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await self.send_message("Usage: /pair XAU/USD")
            else:
                await self.send_message(f"Running pair scan for {parts[1]}...")
                await self.send_message(await self._run_fresh_scan(parts[1]))
        elif command == "/etr":
            await self._handle_etr(text)
        elif command == "/plan":
            await self._handle_plan(text)
        elif command in {"/help", "/start"}:
            await self.send_message(
                "*Manual Trading Bot Commands*\n\n"
                "/watchlist — latest ranked MTF setups\n"
                "/signal — latest confirmed entry signal\n"
                "/status — bot status + top setup\n"
                "/news — blocked currencies + cached events\n"
                "/pairs — tracked instruments\n"
                "/pair XAU/USD — explain one pair right now\n"
                "/plan — today's desk card (V2-gated Plan NY)\n"
                "/plan XAU/USD — one-symbol card\n"
                "/scan — run a fresh scan now\n"
                "/etr — ETR Market Terminal summary (cached)\n"
                "/etr btc|gold|nasdaq|oil — live full report"
            )
        else:
            await self.send_message("Unknown command. Try /help")

    async def _handle_plan(self, text: str) -> None:
        """Reprint today's briefing card. Skips Hermes for pull latency."""
        from src.briefing.formatter import format_instrument_briefing, format_pre_ny_briefing
        from src.briefing.service import build_briefing
        from src.config.settings import get_settings
        from src.etr.alerts import chunk_telegram

        settings = get_settings()
        parts = text.split(maxsplit=1)
        symbol = parts[1].strip().upper() if len(parts) >= 2 else None
        await self.send_message("Building desk card...")
        try:
            briefing = await build_briefing(
                settings,
                instrument_ids=[symbol] if symbol else None,
                attach_hermes=False,
                fetch_funding=symbol is None,
            )
        except Exception as exc:
            await self.send_message(f"Plan NY no disponible: {exc}")
            return
        if symbol:
            if not briefing.instruments:
                await self.send_message(f"Sin card para `{symbol}`")
                return
            message = format_instrument_briefing(briefing.instruments[0])
        else:
            message = format_pre_ny_briefing(briefing)
        for chunk in chunk_telegram(message):
            await self.send_message(chunk)

    async def _handle_etr(self, text: str) -> None:
        """Handle /etr and /etr <asset> commands."""
        from src.config.settings import get_settings
        from src.etr.alerts import chunk_telegram, format_full_report
        from src.etr.models import VALID_ASSETS
        from src.etr.service import cached_reports, fetch_one_report, format_cached_summary

        parts = text.split()
        asset = parts[1].lower().strip() if len(parts) >= 2 else None
        if asset is None:
            summary = format_cached_summary()
            await self.send_message(summary)
            return
        if asset not in VALID_ASSETS:
            await self.send_message(f"Unknown asset `{asset}`. Use: btc, gold, nasdaq, oil")
            return
        await self.send_message(f"Fetching ETR {asset.upper()}...")
        try:
            settings = get_settings()
            report = await fetch_one_report(settings, asset)
            for chunk in chunk_telegram(format_full_report(report)):
                await self.send_message(chunk)
        except Exception as exc:
            # Fall back to cache if live fetch fails
            cached = cached_reports([asset])
            if cached:
                await self.send_message(
                    f"Live fetch failed (`{exc}`). Cached snapshot:\n\n"
                    + format_full_report(cached[0])
                )
            else:
                await self.send_message(f"ETR fetch failed: `{exc}`")

    async def run_forever(self) -> None:
        self._write_heartbeat("starting")
        backoff = 1.0
        while True:
            try:
                updates = await self.get_updates()
                backoff = 1.0
                self._write_heartbeat("ok")
                for update in updates:
                    await self.handle_update(update)
            except Exception as exc:
                log_telegram_poll_error(exc, self.bot_token)
                self._write_heartbeat("error", format_telegram_poll_error(exc, self.bot_token))
                sleep_for = backoff
                if isinstance(exc, TelegramPollHTTPError) and exc.status_code == 429:
                    # Honor Telegram Retry-After; keep a higher ceiling for sustained 429s.
                    retry_after = float(exc.retry_after) if exc.retry_after is not None else 30.0
                    sleep_for = max(retry_after, backoff)
                    backoff = min(max(backoff * 2.0, sleep_for), 300.0)
                else:
                    backoff = min(backoff * 2.0, 30.0)
                await asyncio.sleep(sleep_for)
