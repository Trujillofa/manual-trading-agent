from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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

    async def fetch_events(self, hours_ahead: int = 24) -> list[NewsEvent]:
        """Fetch upcoming high-impact news events."""
        now = datetime.now(UTC)
        window_end = now + timedelta(hours=hours_ahead)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(self.FOREX_FACTORY_URL)
                _ = response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("failed to fetch forex factory events: %s", exc)
            return list(self._events)

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            logger.warning("failed to parse forex factory xml: %s", exc)
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
        return list(self._events)

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
