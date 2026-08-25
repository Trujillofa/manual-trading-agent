from __future__ import annotations

import json
import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from src.news.surprise import (
    SOURCE_FOREX_FACTORY,
    SurpriseResult,
    format_surprise_annotation,
    score_surprise,
    surprise_readiness_label,
)

logger = logging.getLogger(__name__)

SOURCE_GROK = "grok"


@dataclass(frozen=True)
class NewsEvent:
    timestamp: datetime
    currency: str
    name: str
    importance: int
    country: str
    forecast: str = ""
    actual: str = ""
    previous: str = ""
    source: str = SOURCE_FOREX_FACTORY
    actual_observed_at: datetime | None = None


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
            self._events = [self._event_from_cache_item(item) for item in events_raw]
            last_fetch = payload.get("last_fetch")
            next_allowed = payload.get("next_allowed_fetch")
            self._last_fetch = datetime.fromisoformat(last_fetch) if last_fetch else None
            self._next_allowed_fetch = (
                datetime.fromisoformat(next_allowed) if next_allowed else None
            )
        except Exception:
            self._events = []
            self._last_fetch = None
            self._next_allowed_fetch = None

    @staticmethod
    def _event_from_cache_item(item: dict[str, object]) -> NewsEvent:
        observed_raw = item.get("actual_observed_at")
        observed_at: datetime | None = None
        if isinstance(observed_raw, str) and observed_raw:
            observed_at = datetime.fromisoformat(observed_raw)
        return NewsEvent(
            timestamp=datetime.fromisoformat(str(item["timestamp"])),
            currency=str(item["currency"]),
            name=str(item["name"]),
            importance=int(str(item["importance"])),
            country=str(item.get("country", "")),
            forecast=str(item.get("forecast", "")),
            actual=str(item.get("actual", "")),
            previous=str(item.get("previous", "")),
            source=str(item.get("source", SOURCE_FOREX_FACTORY)),
            actual_observed_at=observed_at,
        )

    @staticmethod
    def _event_to_cache_item(event: NewsEvent) -> dict[str, object]:
        item = asdict(event)
        item["timestamp"] = event.timestamp.isoformat()
        observed = event.actual_observed_at
        item["actual_observed_at"] = observed.isoformat() if observed is not None else None
        return item

    def _save_cache(self) -> None:
        try:
            self.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "last_fetch": self._last_fetch.isoformat() if self._last_fetch else None,
                "next_allowed_fetch": (
                    self._next_allowed_fetch.isoformat() if self._next_allowed_fetch else None
                ),
                "events": [self._event_to_cache_item(event) for event in self._events],
            }
            self.CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("news cache persist failed: %s", exc)

    async def fetch_events(
        self,
        hours_ahead: int = 24,
        *,
        hours_behind: float | None = None,
        force: bool = False,
        now: datetime | None = None,
    ) -> list[NewsEvent]:
        """Fetch high-impact news events with cache/backoff.

        Retention includes the post-release lockout so a fresh fetch does not
        drop events that still block trading. Optional ``hours_behind`` widens
        the lookback (used by the pre-NY briefing). Default behavior is unchanged.
        """
        now = now.astimezone(UTC) if now is not None else datetime.now(UTC)
        window_end = now + timedelta(hours=hours_ahead)

        # Use recent cache first
        if not force and self._last_fetch and now - self._last_fetch < self._cache_ttl:
            return list(self._events)

        # Respect backoff window
        if not force and self._next_allowed_fetch and now < self._next_allowed_fetch:
            return list(self._events)

        response_text: str | None = None
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(self.FOREX_FACTORY_URL)
                if response.status_code == 429:
                    self._next_allowed_fetch = now + timedelta(minutes=30)
                    self._save_cache()
                    logger.warning(
                        "forex factory rate-limited (429), using cache until %s",
                        self._next_allowed_fetch,
                    )
                    return list(self._events)
                _ = response.raise_for_status()
                response_text = response.text
        except httpx.HTTPError as exc:
            logger.warning("failed to fetch forex factory events: %s", exc)
            await self._best_effort_grok_verify(now, window_end)
            return list(self._events)

        if response_text is None:
            return list(self._events)

        try:
            parsed_events = self._select_events_from_xml(
                response_text, now, hours_ahead, hours_behind=hours_behind
            )
        except ET.ParseError as exc:
            logger.warning("failed to parse forex factory xml: %s", exc)
            await self._best_effort_grok_verify(now, window_end)
            return list(self._events)

        parsed_events.sort(key=lambda event: event.timestamp)
        self._events = parsed_events
        self._last_fetch = now
        self._next_allowed_fetch = now + self._cache_ttl
        self._save_cache()
        return list(self._events)

    @staticmethod
    def _event_identity(event: NewsEvent) -> tuple[datetime, str, str]:
        return (event.timestamp, event.currency, event.name)

    def _stamp_actual_observation(
        self,
        event: NewsEvent,
        now: datetime,
        prior_by_id: dict[tuple[datetime, str, str], NewsEvent],
    ) -> NewsEvent:
        """Keep the first observation time for an unchanged actual value."""
        if not event.actual:
            return event
        previous = prior_by_id.get(self._event_identity(event))
        if (
            previous is not None
            and previous.actual == event.actual
            and previous.actual_observed_at is not None
        ):
            return replace(event, actual_observed_at=previous.actual_observed_at)
        return replace(event, actual_observed_at=now)

    def _select_events_from_xml(
        self,
        xml_text: str,
        now: datetime,
        hours_ahead: int = 24,
        prior_events: list[NewsEvent] | None = None,
        *,
        hours_behind: float | None = None,
    ) -> list[NewsEvent]:
        """Parse feed XML and keep events in [now - lookback, now + hours]."""
        root = ET.fromstring(xml_text)
        if hours_behind is None:
            window_start = now - timedelta(minutes=self.lockout_after)
        else:
            window_start = now - timedelta(hours=float(hours_behind))
        window_end = now + timedelta(hours=hours_ahead)
        source = self._events if prior_events is None else prior_events
        prior_by_id = {self._event_identity(event): event for event in source}
        parsed_events: list[NewsEvent] = []
        for event_node in root.findall(".//event"):
            event = self._parse_event_node(event_node)
            if event is None:
                continue
            if event.importance < self.importance_threshold:
                continue
            if not window_start <= event.timestamp <= window_end:
                continue
            event = self._stamp_actual_observation(event, now, prior_by_id)
            parsed_events.append(event)
        parsed_events.sort(key=lambda event: event.timestamp)
        return parsed_events

    async def _best_effort_grok_verify(self, now: datetime, window_end: datetime) -> None:
        api_key = os.getenv("XAI_API_KEY")
        model = os.getenv("XAI_MODEL", "grok-4.20-beta-latest-non-reasoning")
        if not api_key:
            return
        prompt = (
            "Return only JSON array. List high-impact macroeconomic events within "
            "the next 24 hours relevant to FX majors. "
            "Each item must include: timestamp_utc, currency, name, importance, country. "
            "If unsure, return []."
        )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.x.ai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": prompt},
                            {
                                "role": "user",
                                "content": (
                                    f"Now UTC: {now.isoformat()}. "
                                    f"Window end UTC: {window_end.isoformat()}."
                                ),
                            },
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
                    ts = datetime.fromisoformat(
                        str(item["timestamp_utc"]).replace("Z", "+00:00")
                    ).astimezone(UTC)
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
                            source=SOURCE_GROK,
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
        if (
            self._next_allowed_fetch
            and self._last_fetch
            and self._next_allowed_fetch - self._last_fetch > self._cache_ttl
        ):
            return "cache/backoff"
        if self._last_fetch:
            return "forex_factory_or_grok"
        return "none"

    def get_display_events(
        self,
        hours_ahead: int = 24,
        timestamp: datetime | None = None,
    ) -> list[NewsEvent]:
        """Events in the post-release lockout lookback plus the forward window."""
        now = timestamp.astimezone(UTC) if timestamp is not None else datetime.now(UTC)
        window_start = now - timedelta(minutes=self.lockout_after)
        window_end = now + timedelta(hours=hours_ahead)
        return [
            event
            for event in self._events
            if window_start <= event.timestamp <= window_end
            and event.importance >= self.importance_threshold
        ]

    def get_upcoming_events(
        self,
        hours_ahead: int = 24,
        timestamp: datetime | None = None,
    ) -> list[NewsEvent]:
        return self.get_display_events(hours_ahead, timestamp)

    def get_events_in_window(
        self,
        *,
        now: datetime | None = None,
        hours_ahead: int = 24,
        hours_behind: float = 0,
    ) -> list[NewsEvent]:
        """Filter cached events to an explicit lookback/lookahead window."""
        current = now.astimezone(UTC) if now is not None else datetime.now(UTC)
        window_start = current - timedelta(hours=hours_behind)
        window_end = current + timedelta(hours=hours_ahead)
        return [
            event
            for event in self._events
            if window_start <= event.timestamp <= window_end
            and event.importance >= self.importance_threshold
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

    def has_timestamped_actuals(self) -> bool:
        return any(
            event.source == SOURCE_FOREX_FACTORY
            and event.actual.strip()
            and event.actual_observed_at is not None
            and event.actual_observed_at >= event.timestamp
            for event in self._events
        )

    def get_surprise_readiness(self) -> str:
        return surprise_readiness_label(self.has_timestamped_actuals())

    @staticmethod
    def _extract_currencies(symbol: str) -> set[str]:
        """Currencies for news lockout.

        Prefer instrument registry (NASDAQ → {USD}, not {NAS, DAQ}; OIL → {}).
        Fall back to FX pair string splitting for unregistered symbols.
        """
        try:
            from src.config.instruments import get_instrument_optional

            inst = get_instrument_optional(symbol)
            if inst is not None:
                return {c.upper() for c in inst.currencies}
        except Exception:
            pass

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
    def _resolve_currency(cls, currency_text: str, country_text: str) -> str:
        currency = currency_text.strip().upper()
        if len(currency) == 3 and currency.isalpha():
            return currency
        country = country_text.strip().upper()
        if len(country) == 3 and country.isalpha():
            return country
        return ""

    @classmethod
    def _parse_event_node(cls, event_node: ET.Element) -> NewsEvent | None:
        title = cls._safe_text(event_node.find("title"))
        country = cls._safe_text(event_node.find("country"))
        date_text = cls._safe_text(event_node.find("date"))
        time_text = cls._safe_text(event_node.find("time"))
        impact_text = cls._safe_text(event_node.find("impact"))
        currency = cls._resolve_currency(
            cls._safe_text(event_node.find("currency")),
            country,
        )
        forecast = cls._safe_text(event_node.find("forecast"))
        actual = cls._safe_text(event_node.find("actual"))
        previous = cls._safe_text(event_node.find("previous"))

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
            forecast=forecast,
            actual=actual,
            previous=previous,
            source=SOURCE_FOREX_FACTORY,
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
            "%m-%d-%Y %H:%M",
            "%m-%d-%Y %I:%M%p",
            "%m-%d-%Y %I:%M %p",
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


def score_event(event: NewsEvent, now: datetime) -> SurpriseResult:
    """Descriptive surprise for display. Does not affect lockout or entries."""
    return score_surprise(
        actual_raw=event.actual,
        forecast_raw=event.forecast,
        event_timestamp=event.timestamp,
        now=now,
        source=event.source,
        observed_at=event.actual_observed_at,
    )


def format_cli_event_line(event: NewsEvent, now: datetime) -> str:
    result = score_event(event, now)
    stamp = event.timestamp.strftime("%Y-%m-%d %H:%M")
    annotation = format_surprise_annotation(
        forecast_raw=event.forecast,
        actual_raw=event.actual,
        result=result,
    )
    return f"  {stamp} {event.currency}: {event.name}  [{annotation}]"


def format_telegram_event_line(event: NewsEvent, now: datetime) -> str:
    result = score_event(event, now)
    stamp = event.timestamp.strftime("%Y-%m-%d %H:%M")
    annotation = format_surprise_annotation(
        forecast_raw=event.forecast,
        actual_raw=event.actual,
        result=result,
    )
    return f"• {stamp} UTC | {event.currency} | {event.name} | {annotation}"


def format_cli_news_report(
    events: list[NewsEvent],
    hours: int,
    now: datetime,
    readiness: str,
) -> str:
    lines = [
        "",
        f"[NEWS] High-impact events (post-release lockout + next {hours}h)",
        f"  {readiness}",
    ]
    if not events:
        lines.append(f"  No 3-star events in the lockout window or next {hours} hours")
        return "\n".join(lines)
    lines.extend(format_cli_event_line(event, now) for event in events)
    return "\n".join(lines)


def format_telegram_news_report(
    *,
    source_status: str,
    blocked: list[str],
    events: list[NewsEvent],
    now: datetime,
    readiness: str,
) -> str:
    parts = ["*News Status*", ""]
    parts.append(f"Source: `{source_status}`")
    parts.append(f"Blocked currencies: `{', '.join(blocked) if blocked else 'none'}`")
    status = readiness.removeprefix("Surprise scoring: ")
    parts.append(f"Surprise scoring: `{status}`")
    if not events:
        parts.append("No high-impact cached events in lockout lookback or next 24h.")
        return "\n".join(parts)
    parts.append("")
    parts.append("High-impact events:")
    for event in events[:5]:
        parts.append(format_telegram_event_line(event, now))
    return "\n".join(parts)
