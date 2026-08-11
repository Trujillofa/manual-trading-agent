"""Orchestrate ETR poll → diff → Telegram notify."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from src.etr.alerts import format_change_alert, format_compact_summary, format_full_report
from src.etr.client import EtrAuthError, EtrClient
from src.etr.diff import diff_reports
from src.etr.models import VALID_ASSETS, AssetState, EtrChange, EtrPollResult, EtrReport
from src.etr.state import append_etr_audit, load_etr_state, save_state_with_meta

if TYPE_CHECKING:
    from src.config.settings import EtrConfig, Settings

logger = logging.getLogger(__name__)


class NotifierLike(Protocol):
    enabled: bool

    async def send(self, message: str, parse_mode: str = "Markdown") -> bool: ...


@dataclass
class PollSummary:
    results: list[EtrPollResult]
    notified_count: int
    error_count: int
    skipped: bool = False
    message: str = ""


def _env_flag(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def is_etr_enabled(config: EtrConfig) -> bool:
    override = _env_flag("ETR_ENABLED")
    if override is not None:
        return override and config.has_credentials
    return config.enabled and config.has_credentials


def build_client(config: EtrConfig) -> EtrClient:
    if not config.login or not config.password:
        raise EtrAuthError("ETRACADEMY_LOGIN / ETRACADEMY_PASSWORD not configured")
    return EtrClient(
        config.login,
        config.password,
        supabase_url=config.supabase_url,
        supabase_anon_key=config.supabase_anon_key,
    )


def _should_skip_for_interval(meta: dict[str, Any], min_interval: int) -> bool:
    last = meta.get("last_poll_unix")
    if last is None:
        return False
    try:
        return (time.time() - float(last)) < float(min_interval)
    except (TypeError, ValueError):
        return False


async def poll_and_notify(
    settings: Settings,
    notifier: NotifierLike | None = None,
    *,
    assets: list[str] | None = None,
    force: bool = False,
    notify: bool | None = None,
) -> PollSummary:
    """Fetch configured assets, seed or alert on structural changes."""
    config = settings.etr
    if not is_etr_enabled(config):
        msg = "ETR disabled or missing credentials"
        logger.info(msg)
        return PollSummary(results=[], notified_count=0, error_count=0, skipped=True, message=msg)

    state = load_etr_state()
    meta: dict[str, Any] = {}
    # reload meta from file via state helper
    from src.etr.state import load_global_meta

    meta = load_global_meta()

    if not force and _should_skip_for_interval(meta, config.min_poll_interval_seconds):
        msg = (
            f"ETR poll skipped (min interval {config.min_poll_interval_seconds}s "
            f"not elapsed)"
        )
        logger.info(msg)
        return PollSummary(results=[], notified_count=0, error_count=0, skipped=True, message=msg)

    target_assets = [a.lower() for a in (assets or list(config.assets))]
    for asset in target_assets:
        if asset not in VALID_ASSETS:
            raise ValueError(f"Invalid ETR asset: {asset}")

    do_notify = config.telegram_alerts if notify is None else notify
    client = build_client(config)
    results: list[EtrPollResult] = []
    notified_count = 0
    error_count = 0
    now_iso = datetime.now(UTC).isoformat()

    for asset in target_assets:
        result = EtrPollResult(asset=asset, report=None)
        try:
            report = client.fetch_report(asset)
            result.report = report
            fp = report.fingerprint(config.score_alert_low, config.score_alert_high)
            prev_state = state.get(asset)
            prev_report = EtrReport.from_dict(prev_state.report) if prev_state else None

            if prev_state is None:
                # Silent baseline seed
                state[asset] = AssetState(
                    fingerprint=fp,
                    report=report.to_dict(),
                    last_alerted_fingerprint=fp,
                    last_polled_at=now_iso,
                    last_alerted_at=now_iso,
                    in_primary_zone=report.price_in_primary_zone(),
                )
                result.seeded = True
                logger.info("ETR baseline seeded for %s fp=%s", asset, fp)
                append_etr_audit(
                    {
                        "ts": now_iso,
                        "asset": asset,
                        "event": "seed",
                        "fingerprint": fp,
                        "notified": False,
                    }
                )
            else:
                changes = diff_reports(
                    prev_report,
                    report,
                    score_low=config.score_alert_low,
                    score_high=config.score_alert_high,
                    score_delta=config.score_delta_alert,
                    prev_in_zone=prev_state.in_primary_zone,
                )
                result.changes = changes
                # Also alert if fingerprint changed even when diff empty? Prefer diff only.
                should_alert = bool(changes) and fp != (prev_state.last_alerted_fingerprint or "")

                state[asset] = AssetState(
                    fingerprint=fp,
                    report=report.to_dict(),
                    last_alerted_fingerprint=prev_state.last_alerted_fingerprint,
                    last_polled_at=now_iso,
                    last_alerted_at=prev_state.last_alerted_at,
                    in_primary_zone=report.price_in_primary_zone(),
                )

                if should_alert and do_notify and notifier is not None and notifier.enabled:
                    message = format_change_alert(report, changes)
                    ok = await notifier.send(message)
                    result.notified = bool(ok)
                    if ok:
                        notified_count += 1
                        state[asset].last_alerted_fingerprint = fp
                        state[asset].last_alerted_at = now_iso
                elif should_alert and not do_notify:
                    logger.info(
                        "ETR change on %s (%d fields) — notify disabled",
                        asset,
                        len(changes),
                    )
                    state[asset].last_alerted_fingerprint = fp
                    state[asset].last_alerted_at = now_iso

                append_etr_audit(
                    {
                        "ts": now_iso,
                        "asset": asset,
                        "event": "poll",
                        "fingerprint": fp,
                        "changes": [c.to_dict() for c in changes],
                        "notified": result.notified,
                    }
                )
        except Exception as exc:
            error_count += 1
            result.error = str(exc)
            logger.error("ETR poll failed for %s: %s", asset, exc)
            append_etr_audit(
                {
                    "ts": now_iso,
                    "asset": asset,
                    "event": "error",
                    "error": str(exc),
                }
            )
            await _maybe_error_alert(config, meta, notifier, asset, str(exc), do_notify)

        results.append(result)

    meta["last_poll_unix"] = time.time()
    meta["last_poll_at"] = now_iso
    save_state_with_meta(state, meta)

    return PollSummary(
        results=results,
        notified_count=notified_count,
        error_count=error_count,
        message=f"polled={len(results)} notified={notified_count} errors={error_count}",
    )


async def _maybe_error_alert(
    config: EtrConfig,
    meta: dict[str, Any],
    notifier: NotifierLike | None,
    asset: str,
    error: str,
    do_notify: bool,
) -> None:
    if not do_notify or notifier is None or not notifier.enabled:
        return
    last = meta.get("last_error_alert_unix")
    cooldown = config.error_alert_cooldown_minutes * 60
    if last is not None:
        try:
            if time.time() - float(last) < cooldown:
                return
        except (TypeError, ValueError):
            pass
    ok = await notifier.send(
        f"⚠️ *ETR error* · `{asset}`\n`{error[:300]}`",
    )
    if ok:
        meta["last_error_alert_unix"] = time.time()


async def fetch_one_report(settings: Settings, asset: str) -> EtrReport:
    config = settings.etr
    client = build_client(config)
    return client.fetch_report(asset)


def cached_reports(assets: list[str] | None = None) -> list[EtrReport]:
    state = load_etr_state()
    keys = assets or list(state.keys())
    reports: list[EtrReport] = []
    for key in keys:
        asset_state = state.get(key)
        if asset_state and asset_state.report:
            reports.append(EtrReport.from_dict(asset_state.report))
    return reports


def format_cached_summary(assets: list[str] | None = None) -> str:
    return format_compact_summary(cached_reports(assets))


def format_report_message(report: EtrReport) -> str:
    return format_full_report(report)


def format_changes_message(report: EtrReport, changes: list[EtrChange]) -> str:
    return format_change_alert(report, changes)
