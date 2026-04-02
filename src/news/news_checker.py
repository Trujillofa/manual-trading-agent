from __future__ import annotations

import json
import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsEvent:
    timestamp: datetime
    currency: str
    name: str
    importance: int
    country: str


class NewsChecker:
    """Check for 3-star news events that should block trading."""

    FOREX_FACTORY_URL: str = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
    CACHE_PATH: Path = Path("/app/logs/news_cache.json")
    _IMPACT_TO_IMPORTANCE: dict[str, int] = {
        "high": 3,
        "medium": 2,
        "low": 1,
    }

    def __init__(
        self,
        lockout_minutes_before: int = 60,
        lockout_minutes_after: int = 30,
        importance_threshold: int = 3,
    ) -> None:
        self.lockout_before: int = lockout_minutes_before
        self.lockout_after: int = lockout_minutes_after
        self.importance_threshold: int = importance_threshold
        self._events: list[NewsEvent] = []
        self._last_fetch: datetime | None = None
        self._next_allowed_fetch: datetime | None = None
        self._cache_ttl = timedelta(minutes=15)
        self._load_cache()

    def _load_cache(self) -> None:
        if not self.CACHE_PATH.exists():
            return
        try:
            payload = json.loads(self.CACHE_PATH.read_text(encoding="utf-8"))
            events_raw = payload.get("events", [])
            self._events = [
                NewsEvent(
                    timestamp=datetime.fromisoformat(item["timestamp"]),
                    currency=item["currency"],
                    name=item["name"],
                    importance=int(item["importance"]),
                    country=item.get("country", ""),
                )
                for item in events_raw
            ]
            last_fetch = payload.get("last_fetch")
            next_allowed = payload.get("next_allowed_fetch")
            self._last_fetch = datetime.fromisoformat(last_fetch) if last_fetch else None
            self._next_allowed_fetch = datetime.fromisoformat(next_allowed) if next_allowed else None
        except Exception:
            self._events = []
            self._last_fetch = None
            self._next_allowed_fetch = None

    def _save_cache(self) -> None:
        self.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_fetch": self._last_fetch.isoformat() if self._last_fetch else None,
            "next_allowed_fetch": self._next_allowed_fetch.isoformat() if self._next_allowed_fetch else None,
            "events": [
                {
                    **asdict(event),
                    "timestamp": event.timestamp.isoformat(),
                }
                for event in self._events
            ],
        }
        self.CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    async def fetch_events(self, hours_ahead: int = 24) -> list[NewsEvent]:
        """Fetch upcoming high-impact news events with cache/backoff."""
        now = datetime.now(UTC)
        window_end = now + timedelta(hours=hours_ahead)

        # Use recent cache first
        if self._last_fetch and now - self._last_fetch < self._cache_ttl:
            return list(self._events)

        # Respect backoff window
        if self._next_allowed_fetch and now < self._next_allowed_fetch:
            return list(self._events)

        response_text: str | None = None
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(self.FOREX_FACTORY_URL)
                if response.status_code == 429:
                    self._next_allowed_fetch = now + timedelta(minutes=30)
                    self._save_cache()
                    logger.warning("forex factory rate-limited (429), using cache until %s", self._next_allowed_fetch)
                    return list(self._events)
                _ = response.raise_for_status()
                response_text = response.text
        except httpx.HTTPError as exc:
            logger.warning("failed to fetch forex factory events: %s", exc)
            # Best-effort fallback: keep cached data, optionally ask Grok to verify if something major is expected
            await self._best_effort_grok_verify(now, window_end)
            return list(self._events)

        if response_text is None:
            return list(self._events)

        try:
            root = ET.fromstring(response_text)
        except ET.ParseError as exc:
            logger.warning("failed to parse forex factory xml: %s", exc)
            await self._best_effort_grok_verify(now, window_end)
            return list(self._events)

        parsed_events: list[NewsEvent] = []
        for event_node in root.findall(".//event"):
            event = self._parse_event_node(event_node)
            if event is None:
                continue
            if event.importance < self.importance_threshold:
                continue
            if not now <= event.timestamp <= window_end:
                continue
            parsed_events.append(event)

        parsed_events.sort(key=lambda event: event.timestamp)
        self._events = parsed_events
        self._last_fetch = now
        self._next_allowed_fetch = now + self._cache_ttl
        self._save_cache()
        return list(self._events)

    async def _best_effort_grok_verify(self, now: datetime, window_end: datetime) -> None:
        api_key = os.getenv("XAI_API_KEY")
        model = os.getenv("XAI_MODEL", "grok-4.20-beta-latest-non-reasoning")
        if not api_key:
            return
        prompt = (
            "Return only JSON array. List high-impact macroeconomic events within the next 24 hours relevant to FX majors. "
            "Each item must include: timestamp_utc, currency, name, importance, country. "
            "If unsure, return []."
        )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.x.ai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": f"Now UTC: {now.isoformat()}. Window end UTC: {window_end.isoformat()}."},
                        ],
                        "temperature": 0,
                    },
                )
                response.raise_for_status()
                payload = response.json()
            content = payload.get("choices", [{}])[0].get("message", {}).get("content", "[]")
            events_raw = json.loads(content)
            if not isinstance(events_raw, list):
                return
            parsed: list[NewsEvent] = []
            for item in events_raw:
                if not isinstance(item, dict):
                    continue
                try:
                    ts = datetime.fromisoformat(str(item["timestamp_utc"]).replace("Z", "+00:00")).astimezone(UTC)
                    importance = int(item.get("importance", 0))
                    if importance < self.importance_threshold:
                        continue
                    if not now <= ts <= window_end:
                        continue
                    parsed.append(
                        NewsEvent(
                            timestamp=ts,
                            currency=str(item.get("currency", "")).upper()[:3],
                            name=str(item.get("name", "")).strip(),
                            importance=importance,
                            country=str(item.get("country", "")).strip(),
                        )
                    )
                except Exception:
                    continue
            if parsed:
                parsed.sort(key=lambda event: event.timestamp)
                self._events = parsed
                self._last_fetch = now
                self._next_allowed_fetch = now + timedelta(minutes=30)
                self._save_cache()
                logger.info("loaded %d fallback news events via Grok", len(parsed))
        except Exception as exc:
            logger.warning("grok fallback verification failed: %s", exc)

    def is_blocked(self, symbol: str, timestamp: datetime | None = None) -> bool:
        """Check if symbol is blocked due to news."""
        current_time = timestamp.astimezone(UTC) if timestamp is not None else datetime.now(UTC)
        currencies = self._extract_currencies(symbol)

        for event in self._events:
            if event.importance < self.importance_threshold:
                continue
            if event.currency not in currencies:
                continue
            lockout_start = event.timestamp - timedelta(minutes=self.lockout_before)
            lockout_end = event.timestamp + timedelta(minutes=self.lockout_after)
            if lockout_start <= current_time <= lockout_end:
                return True

        return False

    def get_resume_time(self, symbol: str, timestamp: datetime | None = None) -> datetime | None:
        """Get next allowed trading time after news."""
        now = timestamp.astimezone(UTC) if timestamp is not None else datetime.now(UTC)
        currencies = self._extract_currencies(symbol)
        active_lockout_ends: list[datetime] = []

        for event in self._events:
            if event.importance < self.importance_threshold:
                continue
            if event.currency not in currencies:
                continue

            lockout_start = event.timestamp - timedelta(minutes=self.lockout_before)
            lockout_end = event.timestamp + timedelta(minutes=self.lockout_after)
            if lockout_start <= now <= lockout_end:
                active_lockout_ends.append(lockout_end)

        if not active_lockout_ends:
            return None

        return max(active_lockout_ends)

    def get_source_status(self) -> str:
        if self._next_allowed_fetch and self._last_fetch and self._next_allowed_fetch - self._last_fetch > self._cache_ttl:
            return "cache/backoff"
        if self._last_fetch:
            return "forex_factory_or_grok"
        return "none"

    def get_upcoming_events(self, hours_ahead: int = 24, timestamp: datetime | None = None) -> list[NewsEvent]:
        now = timestamp.astimezone(UTC) if timestamp is not None else datetime.now(UTC)
        window_end = now + timedelta(hours=hours_ahead)
        return [
            event for event in self._events
            if now <= event.timestamp <= window_end and event.importance >= self.importance_threshold
        ]

    def get_blocked_currencies(self, timestamp: datetime | None = None) -> set[str]:
        """Get set of currencies currently blocked by news."""
        current_time = timestamp.astimezone(UTC) if timestamp is not None else datetime.now(UTC)
        blocked: set[str] = set()

        for event in self._events:
            if event.importance < self.importance_threshold:
                continue

            lockout_start = event.timestamp - timedelta(minutes=self.lockout_before)
            lockout_end = event.timestamp + timedelta(minutes=self.lockout_after)
            if lockout_start <= current_time <= lockout_end:
                blocked.add(event.currency)

        return blocked

    @staticmethod
    def _extract_currencies(symbol: str) -> set[str]:
        normalized = symbol.strip().upper().replace("-", "/")
        if "/" in normalized:
            parts = [part.strip() for part in normalized.split("/") if part.strip()]
            if len(parts) >= 2:
                return {parts[0][:3], parts[1][:3]}

        compact = "".join(char for char in normalized if char.isalpha())
        if len(compact) >= 6:
            return {compact[:3], compact[3:6]}

        return set()

    @classmethod
    def _parse_event_node(cls, event_node: ET.Element) -> NewsEvent | None:
        title = cls._safe_text(event_node.find("title"))
        country = cls._safe_text(event_node.find("country"))
        date_text = cls._safe_text(event_node.find("date"))
        time_text = cls._safe_text(event_node.find("time"))
        impact_text = cls._safe_text(event_node.find("impact"))
        currency = cls._safe_text(event_node.find("currency")).upper()

        if not title or not date_text or not time_text or not currency:
            return None

        timestamp = cls._parse_timestamp(date_text, time_text)
        if timestamp is None:
            return None

        importance = cls._impact_to_importance(impact_text)
        return NewsEvent(
            timestamp=timestamp,
            currency=currency,
            name=title,
            importance=importance,
            country=country,
        )

    @classmethod
    def _impact_to_importance(cls, impact_text: str) -> int:
        normalized = impact_text.strip().lower()
        for keyword, level in cls._IMPACT_TO_IMPORTANCE.items():
            if keyword in normalized:
                return level
        return 0

    @staticmethod
    def _parse_timestamp(date_text: str, time_text: str) -> datetime | None:
        cleaned_time = time_text.strip().upper()
        if cleaned_time in {"ALL DAY", "TENTATIVE", ""}:
            return None

        timestamp_formats = (
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %I:%M%p",
            "%Y-%m-%d %I:%M %p",
        )

        datetime_text = f"{date_text.strip()} {cleaned_time}"
        for fmt in timestamp_formats:
            try:
                parsed = datetime.strptime(datetime_text, fmt)
                return parsed.replace(tzinfo=UTC)
            except ValueError:
                continue

        logger.debug(
            "unable to parse event timestamp date=%s time=%s",
            date_text,
            time_text,
        )
        return None

    @staticmethod
    def _safe_text(node: ET.Element | None) -> str:
        if node is None or node.text is None:
            return ""
        return node.text.strip()
