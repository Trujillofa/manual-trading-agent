"""Telegram command polling for manual trading agent."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from src.news.news_checker import NewsChecker

OFFSET_PATH = Path("/app/logs/telegram_update_offset.json")
SCAN_LOG_PATH = Path("/app/logs/scan.log")


class TelegramCommandHandler:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.offset = self._load_offset()

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

    async def get_updates(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/getUpdates",
                params={"offset": self.offset, "timeout": 20, "limit": 20},
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                return []
            return list(payload.get("result", []))

    async def send_message(self, text: str) -> None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
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
        formatted = "\n".join(f"• {line}" for line in lines[:5])
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
        return "*Latest Confirmed Signal*\n\n" + "\n".join(lines)

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
        events = checker.get_upcoming_events(24, now)
        parts = ["*News Status*", ""]
        source = checker.get_source_status()
        parts.append(f"Source: `{source}`")
        parts.append(f"Blocked currencies: `{', '.join(blocked) if blocked else 'none'}`")
        if not events:
            parts.append("No high-impact cached events in next 24h.")
            return "\n".join(parts)
        parts.append("")
        parts.append("Upcoming high-impact events:")
        for event in events[:5]:
            parts.append(
                f"• {event.timestamp.strftime('%Y-%m-%d %H:%M')} UTC | {event.currency} | {event.name}"
            )
        return "\n".join(parts)

    def _extract_pairs(self) -> str:
        return (
            "*Tracked Pairs*\n\n"
            "Majors: EUR/USD, GBP/USD, USD/JPY, USD/CHF, USD/CAD, AUD/USD, NZD/USD\n\n"
            "Minors: EUR/GBP, EUR/JPY, EUR/CHF, EUR/AUD, EUR/CAD, GBP/JPY, GBP/CHF, GBP/AUD, GBP/CAD, GBP/NZD, AUD/JPY, AUD/CAD, AUD/CHF, AUD/NZD, CAD/JPY, CHF/JPY, NZD/JPY"
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
                    lines = []
                    for line in block[:14]:
                        if line.strip() == "" and lines:
                            break
                        lines.append(line.rstrip())
                    return "*Pair Review*\n\n" + "\n".join(lines)
            idx = output.rfind("[CLOSEST MTF SETUPS]")
            if idx != -1:
                tail = output[idx:].splitlines()
                lines = [line.strip() for line in tail[1:6] if line.startswith("  ")]
                if lines:
                    return "*Fresh Scan Complete*\n\n" + "\n".join(f"• {line}" for line in lines)
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
                await self.send_message("Usage: /pair GBP/USD")
            else:
                await self.send_message(f"Running pair scan for {parts[1]}...")
                await self.send_message(await self._run_fresh_scan(parts[1]))
        elif command in {"/help", "/start"}:
            await self.send_message(
                "*Manual Trading Bot Commands*\n\n"
                "/watchlist — latest ranked MTF setups\n"
                "/signal — latest confirmed entry signal\n"
                "/status — bot status + top setup\n"
                "/news — blocked currencies + cached events\n"
                "/pairs — tracked forex pairs\n"
                "/pair GBP/USD — explain one pair right now\n"
                "/scan — run a fresh scan now"
            )
        else:
            await self.send_message("Unknown command. Try /help")

    async def run_forever(self) -> None:
        while True:
            try:
                updates = await self.get_updates()
                for update in updates:
                    await self.handle_update(update)
            except Exception:
                pass
