"""Orchestrate ETR poll → diff → Telegram notify."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from src.etr.alerts import (
    format_change_alert,
    format_compact_summary,
    format_full_report,
    telegram_alert_changes,
)
from src.etr.client import EtrAuthError, EtrClient
from src.etr.coordination import CoordinationBusyError, poll_lock, state_read_lock
from src.etr.diff import diff_reports
from src.etr.models import VALID_ASSETS, AssetState, EtrChange, EtrPollResult, EtrReport
from src.etr.parser import EtrUnavailableError
from src.etr.shadow import process_shadow_for_report
from src.etr.source_time import source_is_older
from src.etr.state import append_etr_audit, load_etr_state, load_global_meta, save_state_with_meta

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


@dataclass
class _PendingAlert:
    """A notification already reflected in durable state, not yet sent.

    Reserving before sending makes delivery at-most-once: a crash between the
    state commit and the send loses that one alert, but can never repeat it.
    That trade is deliberate — a missed alert is quieter than duplicate noise.
    """

    message: str
    result: EtrPollResult | None = None


@dataclass
class _Commit:
    results: list[EtrPollResult] = field(default_factory=list)
    alerts: list[_PendingAlert] = field(default_factory=list)
    audits: list[tuple[EtrPollResult, dict[str, Any]]] = field(default_factory=list)
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
        renderer_url=config.renderer_url,
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
    """Fetch outside the lock, then commit one read-modify-write transaction.

    Network work (ETR render, Telegram send) never runs while the coordination
    lock is held, so a slow render cannot stall another poll cycle. The lock
    covers only the reload-diff-write window, which is what makes concurrent
    polls compose instead of overwriting each other.
    """
    config = settings.etr
    if not is_etr_enabled(config):
        msg = "ETR disabled or missing credentials"
        logger.info(msg)
        return PollSummary(results=[], notified_count=0, error_count=0, skipped=True, message=msg)

    # Cheap pre-check so an in-interval scheduler tick does not hit the network.
    # The authoritative check runs again under the lock before anything is saved.
    if not force and _should_skip_for_interval(
        load_global_meta(), config.min_poll_interval_seconds
    ):
        msg = f"ETR poll skipped (min interval {config.min_poll_interval_seconds}s not elapsed)"
        logger.info(msg)
        return PollSummary(results=[], notified_count=0, error_count=0, skipped=True, message=msg)

    target_assets = [a.lower() for a in (assets or list(config.assets))]
    for asset in target_assets:
        if asset not in VALID_ASSETS:
            raise ValueError(f"Invalid ETR asset: {asset}")

    do_notify = config.telegram_alerts if notify is None else notify
    client = build_client(config)
    now_iso = datetime.now(UTC).isoformat()

    fetched, results, errors, unavailable_count = await _fetch_reports(
        client, target_assets, now_iso
    )

    try:
        async with poll_lock():
            commit = _commit_poll(
                config,
                notifier=notifier,
                fetched=fetched,
                errors=errors,
                results=results,
                do_notify=do_notify,
                force=force,
                now_iso=now_iso,
            )
    except CoordinationBusyError:
        logger.info("ETR poll skipped: another writer holds the state lock")
        return PollSummary(
            results=results,
            notified_count=0,
            error_count=len(errors),
            skipped=True,
            message="ETR poll coordination busy",
        )

    notified_count = await _deliver_alerts(notifier, commit.alerts, commit.audits)

    return PollSummary(
        results=commit.results,
        notified_count=notified_count,
        error_count=len(errors),
        skipped=commit.skipped,
        message=commit.message
        or (
            f"polled={len(commit.results)} notified={notified_count} "
            f"errors={len(errors)} unavailable={unavailable_count}"
        ),
    )


async def _fetch_reports(
    client: EtrClient, assets: list[str], now_iso: str
) -> tuple[list[tuple[str, EtrReport]], list[EtrPollResult], list[tuple[str, str]], int]:
    """Network phase: no lock held. Returns reports, results, errors."""
    fetched: list[tuple[str, EtrReport]] = []
    results: list[EtrPollResult] = []
    errors: list[tuple[str, str]] = []
    unavailable_count = 0
    for asset in assets:
        result = EtrPollResult(asset=asset, report=None)
        try:
            report = await client.fetch_report(asset)
        except EtrUnavailableError as exc:
            # The terminal withholds scenarios until it has fresh M5 history, and
            # a busy renderer is a scheduling fact. Neither is a failure alert.
            unavailable_count += 1
            logger.warning("ETR %s unavailable: %s", asset, exc)
            append_etr_audit(
                {"ts": now_iso, "asset": asset, "event": "unavailable", "reason": str(exc)}
            )
        except Exception as exc:
            errors.append((asset, str(exc)))
            result.error = str(exc)
            logger.error("ETR poll failed for %s: %s", asset, exc)
            append_etr_audit({"ts": now_iso, "asset": asset, "event": "error", "error": str(exc)})
        else:
            result.report = report
            fetched.append((asset, report))
        results.append(result)
    return fetched, results, errors, unavailable_count


def _commit_poll(
    config: EtrConfig,
    *,
    notifier: NotifierLike | None,
    fetched: list[tuple[str, EtrReport]],
    errors: list[tuple[str, str]],
    results: list[EtrPollResult],
    do_notify: bool,
    force: bool,
    now_iso: str,
) -> _Commit:
    """Single-writer phase. Runs under the coordination lock; no network calls."""
    meta = load_global_meta()
    if not force and _should_skip_for_interval(meta, config.min_poll_interval_seconds):
        # Another process committed a poll while our fetch was in flight.
        logger.info("ETR poll not committed: a concurrent poll already ran")
        return _Commit(skipped=True, message="ETR poll skipped (concurrent poll committed)")

    state = load_etr_state()
    commit = _Commit(results=results)
    result_by_asset = {result.asset: result for result in results}

    for asset, report in fetched:
        result = result_by_asset[asset]
        prev_state = state.get(asset)
        prev_report = EtrReport.from_dict(prev_state.report) if prev_state else None
        previous_source = prev_report.updated_at if prev_report else None

        if source_is_older(report.updated_at, previous_source):
            # A serialized poll can still carry an older render. Keeping the
            # newer snapshot is the point of the monotonic guard; an old page is
            # not an error and must not raise an alert.
            result.skipped = True
            logger.warning(
                "ETR %s source timestamp went backwards: kept %s, ignored %s",
                asset,
                previous_source,
                report.updated_at,
            )
            append_etr_audit(
                {
                    "ts": now_iso,
                    "asset": asset,
                    "event": "stale_source",
                    "kept_source_timestamp": previous_source,
                    "ignored_source_timestamp": report.updated_at,
                }
            )
            continue

        fp = report.fingerprint(config.score_alert_low, config.score_alert_high)
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
            process_shadow_for_report(
                report,
                changes=[],
                now_iso=now_iso,
                horizon_hours=config.shadow_horizon_hours,
                seed=True,
            )
            append_etr_audit(
                {
                    "ts": now_iso,
                    "asset": asset,
                    "event": "seed",
                    "fingerprint": fp,
                    "notified": False,
                    "price_in_primary_zone": report.price_in_primary_zone(),
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
            # Full diff stays on the result/audit. Telegram only fires on
            # configured thesis fields (bias / direction / zone entry).
            notify_changes = telegram_alert_changes(changes, config.telegram_alert_fields)
            should_alert = bool(notify_changes)

            state[asset] = AssetState(
                fingerprint=fp,
                report=report.to_dict(),
                last_alerted_fingerprint=prev_state.last_alerted_fingerprint,
                last_polled_at=now_iso,
                last_alerted_at=prev_state.last_alerted_at,
                in_primary_zone=report.price_in_primary_zone(),
            )

            if should_alert and do_notify and notifier is not None and notifier.enabled:
                # Reserve before sending: the saved fingerprint is what makes a
                # crash between send and save impossible to double-deliver.
                state[asset].last_alerted_fingerprint = fp
                state[asset].last_alerted_at = now_iso
                commit.alerts.append(
                    _PendingAlert(format_change_alert(report, notify_changes), result)
                )
            elif should_alert and not do_notify:
                logger.info(
                    "ETR change on %s (%d fields) — notify disabled",
                    asset,
                    len(changes),
                )
                state[asset].last_alerted_fingerprint = fp
                state[asset].last_alerted_at = now_iso

            # Forward paper-shadow: log polls, open/resolve zone events
            process_shadow_for_report(
                report,
                changes=changes,
                now_iso=now_iso,
                horizon_hours=config.shadow_horizon_hours,
            )

            commit.audits.append(
                (
                    result,
                    {
                        "ts": now_iso,
                        "asset": asset,
                        "event": "poll",
                        "fingerprint": fp,
                        "changes": [c.to_dict() for c in changes],
                        "notified": False,
                        "price_in_primary_zone": report.price_in_primary_zone(),
                    },
                )
            )

    if do_notify and notifier is not None and notifier.enabled:
        for asset, error in errors:
            if not _error_alert_due(config, meta):
                continue
            # Same at-most-once reservation for error alerts: the cooldown is
            # advanced before the send so a crash cannot repeat it.
            meta["last_error_alert_unix"] = time.time()
            commit.alerts.append(_PendingAlert(_error_alert_message(asset, error)))

    meta["last_poll_unix"] = time.time()
    meta["last_poll_at"] = now_iso
    save_state_with_meta(state, meta)
    return commit


async def _deliver_alerts(
    notifier: NotifierLike | None,
    alerts: list[_PendingAlert],
    audits: list[tuple[EtrPollResult, dict[str, Any]]],
) -> int:
    """Send reserved notifications and finish deferred poll audits."""
    notified_count = 0
    if notifier is not None and notifier.enabled:
        for alert in alerts:
            ok = await notifier.send(alert.message)
            if alert.result is not None:
                alert.result.notified = bool(ok)
                if ok:
                    notified_count += 1
    for result, record in audits:
        record["notified"] = result.notified
        append_etr_audit(record)
    return notified_count


def _error_alert_message(asset: str, error: str) -> str:
    return f"⚠️ *ETR error* · `{asset}`\n`{error[:300]}`"


def _error_alert_due(config: EtrConfig, meta: dict[str, Any]) -> bool:
    last = meta.get("last_error_alert_unix")
    if last is None:
        return True
    cooldown = config.error_alert_cooldown_minutes * 60
    try:
        return time.time() - float(last) >= cooldown
    except (TypeError, ValueError):
        return True


async def _maybe_error_alert(
    config: EtrConfig,
    meta: dict[str, Any],
    notifier: NotifierLike | None,
    asset: str,
    error: str,
    do_notify: bool,
) -> None:
    """Direct error alert helper, at-most-once: reserve the cooldown first."""
    if not do_notify or notifier is None or not notifier.enabled:
        return
    if not _error_alert_due(config, meta):
        return
    meta["last_error_alert_unix"] = time.time()
    await notifier.send(_error_alert_message(asset, error))


async def fetch_one_report(settings: Settings, asset: str) -> EtrReport:
    config = settings.etr
    client = build_client(config)
    return await client.fetch_report(asset)


def cached_reports(assets: list[str] | None = None) -> list[EtrReport]:
    try:
        with state_read_lock():
            state = load_etr_state()
    except CoordinationBusyError:
        logger.warning("ETR cache read skipped: state coordination busy")
        return []
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
