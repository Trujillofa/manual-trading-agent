"""Build and optionally send the once-per-day pre-NY briefing."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import pandas as pd

from src.briefing.formatter import format_pre_ny_briefing
from src.briefing.fundamental import (
    build_fundamental_pillar,
    build_header_synthesis,
    build_shared_fundamental_pillar,
)
from src.briefing.funding import FundingSnapshot, try_fetch_btc_funding
from src.briefing.models import InstrumentBriefing, Pillar, PreNyBriefing
from src.briefing.schedule import ny_open_at, should_send_briefing
from src.briefing.sentiment import build_sentiment_pillar
from src.briefing.state import load_briefing_state, save_briefing_state
from src.briefing.technical import bar_freshness, build_technical_pillar
from src.config.instruments import get_instrument_optional
from src.etr.models import EtrReport
from src.etr.state import load_etr_state
from src.news.news_checker import NewsChecker, NewsEvent
from src.news.surprise import SOURCE_FOREX_FACTORY, surprise_readiness_label

if TYPE_CHECKING:
    from src.config.settings import BriefingInstrumentConfig, Settings

logger = logging.getLogger(__name__)

MtfFetcher = Callable[[str], dict[str, pd.DataFrame]]

_DISPLAY_ES = {
    "XAU/USD": "Oro",
    "BTC/USD": "Bitcoin",
    "NASDAQ": "Nasdaq",
    "OIL": "Petróleo (WTI)",
}


class NotifierLike(Protocol):
    enabled: bool

    async def send(self, message: str, parse_mode: str = "Markdown") -> bool: ...


@dataclass
class BriefingRunResult:
    sent: bool
    skipped: bool
    reason: str
    session_date: str | None
    chunks: int = 0
    briefing: PreNyBriefing | None = None


def _display_name(instrument_id: str) -> str:
    if instrument_id in _DISPLAY_ES:
        return _DISPLAY_ES[instrument_id]
    spec = get_instrument_optional(instrument_id)
    return spec.display_name if spec is not None else instrument_id


def _currencies_for(instrument: BriefingInstrumentConfig) -> set[str]:
    spec = get_instrument_optional(instrument.id)
    base = set(spec.currencies) if spec is not None else set()
    base.update(currency.upper() for currency in instrument.extra_news_currencies)
    return {item.upper() for item in base if item}


def _etr_reports() -> tuple[dict[str, EtrReport], dict[str, str | None]]:
    reports: dict[str, EtrReport] = {}
    polled: dict[str, str | None] = {}
    try:
        state = load_etr_state()
    except Exception as exc:
        logger.warning("ETR cache unavailable for briefing: %s", exc)
        return reports, polled
    for key, asset_state in state.items():
        try:
            reports[key] = EtrReport.from_dict(asset_state.report)
            polled[key] = asset_state.last_polled_at
        except Exception as exc:
            logger.warning("ETR cache parse failed for %s: %s", key, exc)
    return reports, polled


def _scanner_cache() -> tuple[dict[str, object], dict[str, object]]:
    try:
        from src.scanner.state import _load_active_signal_state, _load_near_setup_state

        return dict(_load_active_signal_state()), dict(_load_near_setup_state())
    except Exception as exc:
        logger.warning("scanner cache unavailable for briefing: %s", exc)
        return {}, {}


def _lookup_pair_state(state: dict[str, object], instrument_id: str) -> object | None:
    if instrument_id in state:
        return state[instrument_id]
    compact = instrument_id.replace("/", "")
    for key, value in state.items():
        if str(key).replace("/", "") == compact:
            return value
    return None


def _scanner_extra_lines(
    instrument_id: str,
    *,
    active_signals: dict[str, object],
    near_setups: dict[str, object],
) -> tuple[str, ...]:
    lines: list[str] = []
    active = _lookup_pair_state(active_signals, instrument_id)
    if isinstance(active, dict) and active.get("direction"):
        fired = active.get("fired_at")
        when = ""
        try:
            if fired:
                stamp = datetime.fromtimestamp(int(fired), tz=UTC)
                when = f" · {stamp.strftime('%Y-%m-%d %H:%M')} UTC"
        except (TypeError, ValueError, OSError):
            when = ""
        lines.append(
            f"Alerta scanner activa (Rule C): {active['direction']}{when} · no es entrada nueva"
        )
    near = _lookup_pair_state(near_setups, instrument_id)
    if isinstance(near, dict) and near.get("kind"):
        lines.append(f"Near-setup en caché: {near['kind']} · no es señal")
    return tuple(lines)


def _surprise_readiness(events: list[NewsEvent]) -> str:
    has = any(
        event.source == SOURCE_FOREX_FACTORY
        and event.actual.strip()
        and event.actual_observed_at is not None
        and event.actual_observed_at >= event.timestamp
        for event in events
    )
    return surprise_readiness_label(has)


def _default_fetcher(instrument_id: str) -> dict[str, pd.DataFrame]:
    from src.data.fetcher import DataFetcher

    return DataFetcher().fetch_multi_timeframe(instrument_id, period="7d")


async def _load_news(
    settings: Settings,
    now: datetime,
) -> tuple[list[NewsEvent], str | None, str | None]:
    cfg = settings.briefing
    if not settings.news.enabled:
        return [], "news deshabilitado en settings", None
    checker = NewsChecker(
        lockout_minutes_before=settings.news.lockout_minutes_before,
        lockout_minutes_after=settings.news.lockout_minutes_after,
        importance_threshold=settings.news.importance_threshold,
    )
    try:
        await checker.fetch_events(
            hours_ahead=cfg.news_hours_ahead,
            hours_behind=cfg.news_hours_behind,
            force=True,
            now=now,
        )
    except Exception as exc:
        logger.warning("briefing news fetch failed: %s", exc)
        cached = checker.get_events_in_window(
            now=now,
            hours_ahead=cfg.news_hours_ahead,
            hours_behind=cfg.news_hours_behind,
        )
        status = checker.get_source_status()
        if cached:
            return cached, None, status
        return [], f"calendario no disponible: {exc}", status
    events = checker.get_events_in_window(
        now=now,
        hours_ahead=cfg.news_hours_ahead,
        hours_behind=cfg.news_hours_behind,
    )
    status = checker.get_source_status()
    if not events and status == "none":
        return [], "calendario no disponible (sin feed ni caché)", status
    return events, None, status


def build_instrument_briefing(
    instrument: BriefingInstrumentConfig,
    *,
    frames: dict[str, pd.DataFrame] | None,
    frame_error: str | None,
    events: list[NewsEvent],
    news_error: str | None,
    etr_report: EtrReport | None,
    etr_polled_at: str | None,
    now: datetime,
    settings: Settings,
    btc_funding: FundingSnapshot | None = None,
    funding_error: str | None = None,
    extra_technical_lines: tuple[str, ...] = (),
) -> InstrumentBriefing:
    spec = get_instrument_optional(instrument.id)
    point_size = spec.point_size if spec is not None else 0.01
    yf_symbol = spec.yf_symbol if spec is not None else instrument.id
    rsi_period = settings.strategy.rsi_period
    sma_period = settings.strategy.sma_period
    ema_fast = settings.strategy.ema.fast_period
    ema_slow = settings.strategy.ema.slow_period
    lookback = settings.strategy.lookback_bars

    as_of = None
    if frames is None:
        reason = frame_error or "OHLC no disponible"
        technical = Pillar(
            name="technical",
            available=False,
            lines=extra_technical_lines,
            unavailable_reason=reason,
            source="yfinance",
        )
    else:
        technical, as_of = build_technical_pillar(
            frames,
            point_size=point_size,
            rsi_period=rsi_period,
            sma_period=sma_period,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            lookback=lookback,
            extra_lines=extra_technical_lines,
        )

    ny_open = ny_open_at(now.astimezone(UTC).date(), settings.briefing.ny_open_utc)
    fundamental = build_fundamental_pillar(
        instrument=instrument,
        currencies=_currencies_for(instrument),
        events=events,
        now=now,
        max_events=settings.briefing.max_events_per_instrument,
        news_error=news_error,
        ny_open=ny_open,
        lockout_before=settings.news.lockout_minutes_before,
        lockout_after=settings.news.lockout_minutes_after,
        lead_minutes=settings.briefing.lead_minutes,
    )
    sentiment = build_sentiment_pillar(
        instrument=instrument,
        events=events,
        etr_report=etr_report,
        etr_polled_at=etr_polled_at,
        now=now,
        score_low=settings.etr.score_alert_low,
        score_high=settings.etr.score_alert_high,
        btc_funding=btc_funding,
        funding_error=funding_error,
    )
    return InstrumentBriefing(
        instrument_id=instrument.id,
        display_name=_display_name(instrument.id),
        yf_symbol=yf_symbol,
        technical=technical,
        fundamental=fundamental,
        sentiment=sentiment,
        data_as_of=as_of,
        data_freshness=bar_freshness(as_of, now),
    )


async def build_briefing(
    settings: Settings,
    *,
    now: datetime | None = None,
    fetch_mtf: MtfFetcher | None = None,
    events: list[NewsEvent] | None = None,
    etr_reports: dict[str, EtrReport] | None = None,
    etr_polled: dict[str, str | None] | None = None,
    news_error: str | None = None,
    news_status: str | None = None,
    btc_funding: FundingSnapshot | None = None,
    fetch_funding: bool = True,
    active_signals: dict[str, object] | None = None,
    near_setups: dict[str, object] | None = None,
) -> PreNyBriefing:
    current = now or datetime.now(UTC)
    cfg = settings.briefing
    caveats = [
        "Futuros continuos: niveles aprox.",
        "Sentimiento = proxy (ETR + FF + funding BTC).",
    ]
    loaded_error = news_error
    status = news_status
    if events is None:
        loaded_events, loaded_error, status = await _load_news(settings, current)
    else:
        loaded_events = events

    if etr_reports is None:
        cached_reports, cached_polled = _etr_reports()
    else:
        cached_reports, cached_polled = etr_reports, (etr_polled or {})

    funding_error: str | None = None
    funding = btc_funding
    if funding is None and fetch_funding:
        funding, funding_error = await try_fetch_btc_funding()

    if active_signals is None or near_setups is None:
        loaded_active, loaded_near = _scanner_cache()
        if active_signals is None:
            active_signals = loaded_active
        if near_setups is None:
            near_setups = loaded_near

    if loaded_events:
        readiness = _surprise_readiness(loaded_events)
        status = f"{status} · {readiness}" if status else readiness

    fetcher = fetch_mtf or _default_fetcher
    items: list[InstrumentBriefing] = []
    for instrument in cfg.instruments:
        frames: dict[str, pd.DataFrame] | None
        frame_error: str | None = None
        try:
            frames = fetcher(instrument.id)
        except Exception as exc:
            logger.warning("briefing OHLC failed for %s: %s", instrument.id, exc)
            frames = None
            frame_error = str(exc)
        extra = list(
            _scanner_extra_lines(
                instrument.id,
                active_signals=active_signals,
                near_setups=near_setups,
            )
        )
        etr_key = (instrument.etr_asset or "").lower()
        items.append(
            build_instrument_briefing(
                instrument,
                frames=frames,
                frame_error=frame_error,
                events=loaded_events,
                news_error=loaded_error,
                etr_report=cached_reports.get(etr_key),
                etr_polled_at=cached_polled.get(etr_key),
                now=current,
                settings=settings,
                btc_funding=funding,
                funding_error=funding_error,
                extra_technical_lines=tuple(extra),
            )
        )

    ny_open = ny_open_at(current.astimezone(UTC).date(), cfg.ny_open_utc)
    shared = build_shared_fundamental_pillar(
        events=loaded_events,
        now=current,
        news_error=loaded_error,
        ny_open=ny_open,
        lockout_before=settings.news.lockout_minutes_before,
        lockout_after=settings.news.lockout_minutes_after,
        lead_minutes=cfg.lead_minutes,
    )
    synthesis = build_header_synthesis(
        events=loaded_events,
        now=current,
        news_error=loaded_error,
        lockout_before=settings.news.lockout_minutes_before,
        lockout_after=settings.news.lockout_minutes_after,
    )
    return PreNyBriefing(
        session_date=current.astimezone(UTC).date().isoformat(),
        generated_at=current.astimezone(UTC),
        ny_open_utc=cfg.ny_open_utc,
        lead_minutes=cfg.lead_minutes,
        instruments=items,
        caveats=caveats,
        news_source_status=status,
        shared_fundamental=shared,
        synthesis=synthesis,
    )


async def maybe_send_briefing(
    settings: Settings,
    notifier: NotifierLike | None = None,
    *,
    now: datetime | None = None,
    force: bool = False,
    notify: bool = True,
    fetch_mtf: MtfFetcher | None = None,
    events: list[NewsEvent] | None = None,
    etr_reports: dict[str, EtrReport] | None = None,
    btc_funding: FundingSnapshot | None = None,
    fetch_funding: bool = True,
    active_signals: dict[str, object] | None = None,
    near_setups: dict[str, object] | None = None,
) -> BriefingRunResult:
    current = now or datetime.now(UTC)
    cfg = settings.briefing
    if not cfg.enabled and not force:
        return BriefingRunResult(False, True, "disabled", None)

    state = load_briefing_state()
    last = state.get("last_session_date")
    last_session = str(last) if last else None
    should, reason, session_date = should_send_briefing(
        now=current,
        ny_open_utc=cfg.ny_open_utc,
        lead_minutes=cfg.lead_minutes,
        last_session_date=last_session,
        skip_weekends=cfg.skip_weekends,
        force=force,
    )
    iso = session_date.isoformat()
    if not should:
        return BriefingRunResult(False, True, reason, iso)

    try:
        briefing = await build_briefing(
            settings,
            now=current,
            fetch_mtf=fetch_mtf,
            events=events,
            etr_reports=etr_reports,
            btc_funding=btc_funding,
            fetch_funding=fetch_funding,
            active_signals=active_signals,
            near_setups=near_setups,
        )
        message = format_pre_ny_briefing(briefing)
    except Exception as exc:
        logger.exception("pre-NY briefing build failed: %s", exc)
        return BriefingRunResult(False, True, f"build_failed: {exc}", iso)

    telegram_ok = bool(
        notify
        and cfg.telegram_notifications
        and getattr(settings.telegram, "pre_ny_briefing_notifications", True)
        and settings.telegram.enabled
        and notifier is not None
        and notifier.enabled
    )
    chunks_sent = 0
    if telegram_ok and notifier is not None:
        from src.etr.alerts import chunk_telegram

        try:
            chunks = chunk_telegram(message)
            for chunk in chunks:
                ok = await notifier.send(chunk)
                if not ok:
                    logger.warning(
                        "pre-NY briefing Telegram send failed after %s/%s chunks",
                        chunks_sent,
                        len(chunks),
                    )
                    return BriefingRunResult(
                        False, False, "send_failed", iso, chunks_sent, briefing=briefing
                    )
                chunks_sent += 1
        except Exception as exc:
            logger.warning("pre-NY briefing Telegram send failed: %s", exc)
            return BriefingRunResult(False, False, f"send_failed: {exc}", iso, briefing=briefing)
        save_briefing_state(
            {
                "last_session_date": iso,
                "sent_at": int(current.timestamp()),
                "kind": "pre_ny_briefing",
            }
        )
        return BriefingRunResult(True, False, "sent", iso, chunks_sent, briefing)

    if notify and not telegram_ok:
        return BriefingRunResult(False, False, "telegram_disabled", iso, briefing=briefing)

    return BriefingRunResult(False, False, "built", iso, briefing=briefing)
