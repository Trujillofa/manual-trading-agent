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
from src.etr.client import EtrAuthError, EtrClient, EtrRendererBusyError
from src.etr.coordination import CoordinationBusyError, poll_lock, try_state_read_lock
from src.etr.diff import diff_reports
from src.etr.models import VALID_ASSETS, AssetState, EtrChange, EtrPollResult, EtrReport
from src.etr.parser import EtrUnavailableError
from src.etr.shadow import process_shadow_for_report
from src.etr.source_time import (
    MAX_SOURCE_FUTURE_HOURS,
    source_is_older,
    source_latest_instant,
    source_timestamp_in_future,
    source_timestamp_max,
)
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
    ``alert_reserved`` is written to the audit log before the send, so the loss
    is observable instead of being inferred from silence.
    """

    message: str
    result: EtrPollResult | None = None
    kind: str = "change"
    asset: str | None = None
    fingerprint: str | None = None


@dataclass
class _Commit:
    results: list[EtrPollResult] = field(default_factory=list)
    alerts: list[_PendingAlert] = field(default_factory=list)
    audits: list[tuple[EtrPollResult, dict[str, Any]]] = field(default_factory=list)
    skipped: bool = False
    message: str = ""


# One busy poll is scheduling. Three in a row is a starved renderer: at the
# default five-minute interval that is a quarter of an hour with no analysis for
# any asset, and it used to be counted as "unavailable" and never alerted.
RENDER_BUSY_ALERT_AFTER_POLLS = 3
# Same shape for the failure path, and **this is the one the deployed topology
# reaches**. ``etr-scan`` is one step of a strictly sequential shell loop
# (scripts/run_scanner_loop.sh), ``_fetch_reports`` awaits assets one at a time
# and uvicorn runs a single worker, so at most one /render is ever in flight and
# the renderer's 20 s queue timeout — the only thing that produces ``outcome=
# busy`` — cannot fire. A broken page or a wedged browser instead shows up as a
# render that returns no analysis, which the client retries once and then
# reports as an error. So escalation keys off *consecutive poll cycles that
# produced no analysis at all*, while the busy streak stays for the multi-client
# case (two agents sharing the sidecar, or a second poll overlapping a wedged
# holder) where busy really is reachable. The two counters are independent:
# whichever condition does not hold resets its own streak.
RENDER_FAILURE_ALERT_AFTER_POLLS = 3
# A rejected (older) render is not an error and must not alert on its own. But a
# watermark that stays ahead of every render is a stuck feed: after this long
# with nothing accepted, one alert fires so the silent skip becomes visible.
STALE_SOURCE_ALERT_AFTER_SECONDS = 6 * 3600


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

    fetched, results, errors, unavailable_count, busy_count = await _fetch_reports(
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
                busy_count=busy_count,
                assets=target_assets,
            )
    except CoordinationBusyError:
        logger.info("ETR poll skipped: another writer holds the state lock")
        # The fetch already happened; throwing it away without a record made a
        # repeated lock contention look exactly like a poll that never ran.
        append_etr_audit(
            {
                "ts": now_iso,
                "event": "commit_skipped_lock_busy",
                "reason": "etr_coordination.lock held by another writer",
                "assets_fetched": [asset for asset, _ in fetched],
                "assets_requested": list(target_assets),
                "errors": len(errors),
                "unavailable": unavailable_count,
                "renderer_busy": busy_count,
            }
        )
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
) -> tuple[list[tuple[str, EtrReport]], list[EtrPollResult], list[tuple[str, str]], int, int]:
    """Network phase: no lock held. Returns reports, results, errors, counts."""
    fetched: list[tuple[str, EtrReport]] = []
    results: list[EtrPollResult] = []
    errors: list[tuple[str, str]] = []
    unavailable_count = 0
    busy_count = 0
    for asset in assets:
        result = EtrPollResult(asset=asset, report=None)
        try:
            report = await client.fetch_report(asset)
        except EtrRendererBusyError as exc:
            # Scheduling, not a failure: count it separately so a renderer that
            # is busy for poll after poll can be alerted on.
            busy_count += 1
            unavailable_count += 1
            logger.warning("ETR %s unavailable: %s", asset, exc)
            append_etr_audit(
                {
                    "ts": now_iso,
                    "asset": asset,
                    "event": "unavailable",
                    "kind": "renderer_busy",
                    "reason": str(exc),
                }
            )
        except EtrUnavailableError as exc:
            # The terminal withholds scenarios until it has fresh M5 history.
            # That is a data state, not a failure alert.
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
    return fetched, results, errors, unavailable_count, busy_count


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
    busy_count: int = 0,
    assets: list[str] | None = None,
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
        previous_max = prev_state.source_at_max if prev_state else None
        previous_source = prev_report.updated_at if prev_report else None
        source_max = source_timestamp_max(previous_max, previous_source)
        report_source = _plausible_source(report.updated_at, now_iso, asset=asset, after=source_max)

        if source_is_older(report.updated_at, source_max):
            if _watermark_suspect(source_max, now_iso):
                # The watermark sits months ahead of the clock, so it is a
                # misparse rather than newer data — and the only way that shows
                # is an honest render being skipped. Drop it and fall through to
                # commit the render; this is also the self-heal for a state file
                # the old code poisoned.
                logger.warning(
                    "ETR %s watermark %s is implausibly far ahead of the poll clock; discarding",
                    asset,
                    source_max,
                )
                append_etr_audit(
                    {
                        "ts": now_iso,
                        "asset": asset,
                        "event": "source_watermark_discarded",
                        "discarded_source_timestamp": source_max,
                        "accepted_source_timestamp": report.updated_at,
                    }
                )
                source_max = None
                # Re-judged against the poll clock: the discarded value must not
                # go on licensing whatever comes next.
                report_source = _plausible_source(report.updated_at, now_iso, asset=asset)
            else:
                # A serialized poll can still carry an older render. Keeping the
                # newer snapshot is the point of the monotonic guard; an old page
                # is not an error and must not raise an alert on its own.
                result.skipped = True
                logger.warning(
                    "ETR %s source timestamp went backwards: kept %s, ignored %s",
                    asset,
                    source_max,
                    report.updated_at,
                )
                append_etr_audit(
                    {
                        "ts": now_iso,
                        "asset": asset,
                        "event": "stale_source",
                        "kept_source_timestamp": source_max,
                        "ignored_source_timestamp": report.updated_at,
                    }
                )
                _note_stale_source(
                    commit, meta, asset, source_max, now_iso, do_notify, notifier
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
                source_at_max=source_timestamp_max(source_max, report_source),
            )
            result.seeded = True
            _clear_stale_source(meta, asset)
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
                source_at_max=source_timestamp_max(source_max, report_source),
                # Carried until it is actually reserved: a suppressed change
                # outlives the commit that swallowed its diff.
                pending_alert=prev_state.pending_alert,
            )
            _clear_stale_source(meta, asset)

            carried = prev_state.pending_alert
            if carried and do_notify and notifier is not None and notifier.enabled:
                # The change a preview poll withheld. Delivered before this
                # poll's own alert so the older change is not ordered after a
                # newer one, and cleared in the same transaction that reserves
                # it, which keeps delivery at-most-once.
                message = str(carried.get("message") or "")
                if message:
                    state[asset].last_alerted_fingerprint = (
                        carried.get("fingerprint") or prev_state.last_alerted_fingerprint
                    )
                    state[asset].last_alerted_at = now_iso
                    commit.alerts.append(
                        _PendingAlert(
                            message,
                            result,
                            kind="change",
                            asset=asset,
                            fingerprint=state[asset].last_alerted_fingerprint,
                        )
                    )
                    append_etr_audit(
                        {
                            "ts": now_iso,
                            "asset": asset,
                            "event": "alert_carried_forward",
                            "fingerprint": state[asset].last_alerted_fingerprint,
                            "suppressed_because": carried.get("suppressed_because"),
                            "suppressed_at": carried.get("suppressed_at"),
                        }
                    )
                state[asset].pending_alert = None

            suppressed: str | None = None
            if should_alert and do_notify and notifier is not None and notifier.enabled:
                # Reserve before sending: the saved fingerprint is what makes a
                # crash between send and save impossible to double-deliver.
                state[asset].last_alerted_fingerprint = fp
                state[asset].last_alerted_at = now_iso
                commit.alerts.append(
                    _PendingAlert(
                        format_change_alert(report, notify_changes),
                        result,
                        kind="change",
                        asset=asset,
                        fingerprint=fp,
                    )
                )
            elif should_alert and not do_notify:
                # A preview poll must not *consume* the change. The commit below
                # saves the new snapshot, so the next poll's diff against it is
                # empty whatever the fingerprint says: the change is carried
                # forward as a ready-made message and delivered by the next
                # notifying poll. Advancing the fingerprint here (the old
                # behaviour) made ``--no-notify`` silently eat the alert and left
                # no trace of it at all.
                logger.info(
                    "ETR change on %s (%d fields) — notify disabled, alert left pending",
                    asset,
                    len(changes),
                )
                state[asset].pending_alert = {
                    "fingerprint": fp,
                    "message": format_change_alert(report, notify_changes),
                    "suppressed_because": "notifications disabled",
                    "suppressed_at": now_iso,
                }
                suppressed = "notifications disabled"
            elif should_alert:
                logger.warning(
                    "ETR change on %s (%d fields) — no notifier available, alert left pending",
                    asset,
                    len(changes),
                )
                state[asset].pending_alert = {
                    "fingerprint": fp,
                    "message": format_change_alert(report, notify_changes),
                    "suppressed_because": "notifier unavailable",
                    "suppressed_at": now_iso,
                }
                suppressed = "notifier unavailable"

            # Forward paper-shadow: log polls, open/resolve zone events
            process_shadow_for_report(
                report,
                changes=changes,
                now_iso=now_iso,
                horizon_hours=config.shadow_horizon_hours,
            )

            audit_row: dict[str, Any] = {
                "ts": now_iso,
                "asset": asset,
                "event": "poll",
                "fingerprint": fp,
                "changes": [c.to_dict() for c in changes],
                "notified": False,
                "price_in_primary_zone": report.price_in_primary_zone(),
            }
            if suppressed is not None:
                audit_row["alert_suppressed"] = suppressed
            commit.audits.append((result, audit_row))

    if do_notify and notifier is not None and notifier.enabled:
        for asset, error in errors:
            alert = _reserve_error_alert(config, meta, asset, error)
            if alert is not None:
                commit.alerts.append(alert)

    _note_renderer_busy(commit, meta, busy_count, assets or [], now_iso, do_notify, notifier)
    _note_render_failures(commit, meta, len(errors), len(assets or []), now_iso, do_notify, notifier)

    meta["last_poll_unix"] = time.time()
    meta["last_poll_at"] = now_iso
    save_state_with_meta(state, meta)

    # Written after the snapshot exists and before anything is sent: a crash
    # during delivery leaves a reservation with no matching send, which is the
    # only way a lost alert is visible at all.
    for alert in commit.alerts:
        append_etr_audit(
            {
                "ts": now_iso,
                "asset": alert.asset,
                "event": "alert_reserved",
                "kind": alert.kind,
                "fingerprint": alert.fingerprint,
            }
        )
    return commit


def _note_renderer_busy(
    commit: _Commit,
    meta: dict[str, Any],
    busy_count: int,
    assets: list[str],
    now_iso: str,
    do_notify: bool,
    notifier: NotifierLike | None,
) -> None:
    """Track consecutive busy polls and alert once when the streak is too long.

    The counter lives in durable meta, not on the client: a poll builds a fresh
    client every cycle, so an instance counter would reset before it could ever
    fire. It resets on any cycle where no asset came back busy.
    """
    try:
        streak = int(meta.get("renderer_busy_streak") or 0)
    except (TypeError, ValueError):
        streak = 0
    if busy_count <= 0:
        if streak:
            logger.info("ETR renderer busy streak ended after %d polls", streak)
            meta["renderer_busy_streak"] = 0
            meta.pop("renderer_busy_since", None)
        return

    streak += 1
    meta["renderer_busy_streak"] = streak
    meta.setdefault("renderer_busy_since", now_iso)
    if streak != RENDER_BUSY_ALERT_AFTER_POLLS:
        return

    # Exactly at the threshold, so a permanently wedged renderer alerts once per
    # degradation instead of once per poll.
    if not do_notify or notifier is None or notifier.enabled is False:
        return
    commit.alerts.append(
        _PendingAlert(
            _busy_streak_message(streak, assets, meta.get("renderer_busy_since")),
            kind="renderer_busy",
        )
    )
    append_etr_audit(
        {
            "ts": now_iso,
            "event": "renderer_busy_streak",
            "consecutive_busy_polls": streak,
            "since": meta.get("renderer_busy_since"),
            "assets": list(assets),
        }
    )


def _busy_streak_message(streak: int, assets: list[str], since: object) -> str:
    return (
        f"⚠️ *ETR renderer starved* · busy for {streak} consecutive polls\n"
        f"`no analysis for {', '.join(assets) or 'all assets'} since {since or 'unknown'}`"
    )


def _note_render_failures(
    commit: _Commit,
    meta: dict[str, Any],
    error_count: int,
    asset_count: int,
    now_iso: str,
    do_notify: bool,
    notifier: NotifierLike | None,
) -> None:
    """Escalate a feed that produced no analysis at all, poll after poll.

    This is the path the **deployed** topology reaches. ``etr-scan`` is one step
    of a strictly sequential shell loop, ``_fetch_reports`` awaits assets one at
    a time and uvicorn runs a single worker, so at most one /render is in flight
    and the renderer's queue timeout never fires: ``outcome=busy`` is
    unreachable and the busy streak above cannot alert. What does happen is
    every render returning no analysis — navigation timeout, upstream HTTP
    error, a page that never hydrated, or a browser wedged past its deadline.

    The client retries a render timeout once and then raises, so all of those
    arrive here as per-asset errors, and consecutive *overruns* are covered by
    the same counter because the renderer reports an overrun to its caller as
    ``outcome=timeout``. The busy streak stays in place for the multi-client
    case; it is not relied on.
    """
    try:
        streak = int(meta.get("render_failure_streak") or 0)
    except (TypeError, ValueError):
        streak = 0
    if asset_count <= 0 or error_count < asset_count:
        if streak:
            logger.info("ETR render failure streak ended after %d polls", streak)
            meta["render_failure_streak"] = 0
            meta.pop("render_failure_since", None)
        return

    streak += 1
    meta["render_failure_streak"] = streak
    meta.setdefault("render_failure_since", now_iso)
    if streak != RENDER_FAILURE_ALERT_AFTER_POLLS:
        return
    # Exactly at the threshold: one alert per run of failures, not one per poll.
    if not do_notify or notifier is None or notifier.enabled is False:
        return
    commit.alerts.append(
        _PendingAlert(
            _render_failure_message(streak, meta.get("render_failure_since")),
            kind="render_failure",
        )
    )
    append_etr_audit(
        {
            "ts": now_iso,
            "event": "render_failure_streak",
            "consecutive_failed_polls": streak,
            "since": meta.get("render_failure_since"),
        }
    )


def _render_failure_message(streak: int, since: object) -> str:
    return (
        f"⚠️ *ETR renderer failing* · no analysis for {streak} consecutive polls\n"
        f"`every render returned no page since {since or 'unknown'}`"
    )


def _now_from_iso(now_iso: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(now_iso)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _plausible_source(
    raw: object, now_iso: str, *, asset: str | None = None, after: object = None
) -> str | None:
    """Return the stamp if it can be a real render clock, else ``None``.

    A stamp more than ``MAX_SOURCE_FUTURE_HOURS`` ahead of the trusted frontier —
    the poll clock, or the asset's own accepted watermark when that is later — is
    a misparse (a US MM/DD date read as DD/MM) or a broken clock, and it is
    dropped instead of being stored. One such value used to become the
    high-water mark and reject every honest render from then on, and because the
    committed report still looked plausible there was nothing in the state file
    to point at.

    Judging against the later of the two matters: the source clock may
    legitimately run ahead of ours (a skewed host, a replay, a harness that
    advances the stamp faster than the wall clock), and a freshness guard that
    drops honest renders is the same bug it was added to prevent.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    reference = source_latest_instant(now_iso, after)
    if not source_timestamp_in_future(raw, now=reference):
        return raw
    logger.warning(
        "ETR %s source timestamp %r is implausibly far in the future; ignored", asset, raw
    )
    append_etr_audit(
        {
            "ts": now_iso,
            "asset": asset,
            "event": "source_timestamp_rejected",
            "rejected_source_timestamp": raw,
            "reason": "timestamp more than {}h ahead of {}".format(
                int(MAX_SOURCE_FUTURE_HOURS), reference.isoformat() if reference else "now"
            ),
        }
    )
    return None


def _watermark_suspect(watermark: object, now_iso: str) -> bool:
    """True when a watermark sits implausibly far ahead of the poll clock.

    Only the poll clock is used here, deliberately. A watermark ahead of *real*
    time by months cannot be newer data; it can only be a misparse, and the way
    it manifests is exactly one thing — honest renders being skipped as older
    than it.
    """
    return source_timestamp_in_future(watermark, now=source_latest_instant(now_iso))


def _note_stale_source(
    commit: _Commit,
    meta: dict[str, Any],
    asset: str,
    kept: object,
    now_iso: str,
    do_notify: bool,
    notifier: NotifierLike | None,
) -> None:
    """Alert once when the same asset has been rejected as stale for hours.

    A single rejected older render is normal and silent. An asset whose
    high-water mark stays ahead of every render is not: it is a stuck feed, and
    it used to be invisible because each skip only wrote an audit row.
    """
    stale = meta.get("stale_source")
    if not isinstance(stale, dict):
        stale = {}
        meta["stale_source"] = stale
    entry = stale.get(asset)
    if not isinstance(entry, dict):
        entry = {}
        stale[asset] = entry
    now = time.time()
    since = entry.get("since_unix")
    if not isinstance(since, (int, float)):
        entry["since_unix"] = now
        entry["since"] = now_iso
        since = now
    if entry.get("alerted_at"):
        return
    if now - float(since) < STALE_SOURCE_ALERT_AFTER_SECONDS:
        return
    entry["alerted_at"] = now_iso
    hours = round((now - float(since)) / 3600, 1)
    append_etr_audit(
        {
            "ts": now_iso,
            "asset": asset,
            "event": "stale_source_stuck",
            "kept_source_timestamp": kept,
            "since": entry.get("since"),
            "hours": hours,
        }
    )
    if not do_notify or notifier is None or notifier.enabled is False:
        return
    commit.alerts.append(
        _PendingAlert(
            _stale_source_message(asset, kept, hours),
            kind="stale_source",
            asset=asset,
        )
    )


def _clear_stale_source(meta: dict[str, Any], asset: str) -> None:
    stale = meta.get("stale_source")
    if isinstance(stale, dict):
        stale.pop(asset, None)
        if not stale:
            meta.pop("stale_source", None)


def _stale_source_message(asset: str, kept: object, hours: float) -> str:
    return (
        f"⚠️ *ETR source timestamp stuck* · `{asset}`\n"
        f"`no render accepted for {hours:g}h; watermark still {kept or 'unknown'}`"
    )


async def _deliver_alerts(
    notifier: NotifierLike | None,
    alerts: list[_PendingAlert],
    audits: list[tuple[EtrPollResult, dict[str, Any]]],
) -> int:
    """Send reserved notifications and finish deferred poll audits.

    One failing send must not abort the batch. Before this, a notifier that
    raised on the second of three assets stopped the loop: the third alert stayed
    ``alert_reserved`` with no outcome row, and the poll audits after the loop
    were never written at all, so the silence looked like a poll that had not
    run. Every alert is now isolated and the deferred audits are flushed in
    ``finally``; the first failure is re-raised afterwards so a broken notifier
    is still loud.
    """
    notified_count = 0
    deliverable = notifier is not None and notifier.enabled
    first_error: Exception | None = None
    try:
        if alerts and not deliverable:
            for alert in alerts:
                _audit_alert_outcome(alert, delivered=False, reason="notifier unavailable")
        if deliverable and notifier is not None:
            for alert in alerts:
                try:
                    ok = await notifier.send(alert.message)
                except Exception as exc:
                    # Recorded before moving on: the reservation is already
                    # durable, so without this row the alert vanishes from the
                    # audit trail instead of showing up as lost.
                    _audit_alert_outcome(
                        alert, delivered=False, reason=f"{type(exc).__name__}: {exc}"
                    )
                    if first_error is None:
                        first_error = exc
                    continue
                _audit_alert_outcome(
                    alert,
                    delivered=bool(ok),
                    reason=None if ok else "notifier returned False",
                )
                if ok:
                    notified_count += 1
                if alert.result is not None:
                    alert.result.notified = bool(ok)
    finally:
        for result, record in audits:
            record["notified"] = result.notified
            append_etr_audit(record)
    if first_error is not None:
        raise first_error
    return notified_count


def _audit_alert_outcome(alert: _PendingAlert, *, delivered: bool, reason: str | None) -> None:
    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "asset": alert.asset,
        "event": "alert_sent" if delivered else "alert_lost",
        "kind": alert.kind,
        "fingerprint": alert.fingerprint,
        "notified": delivered,
    }
    if reason:
        record["reason"] = reason
    append_etr_audit(record)


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


def _reserve_error_alert(
    config: EtrConfig, meta: dict[str, Any], asset: str, error: str
) -> _PendingAlert | None:
    """Reserve one error alert, at-most-once: the cooldown advances first.

    This is the live path — ``_commit_poll`` calls it under the lock, so the
    cooldown is durable before the send.
    """
    if not _error_alert_due(config, meta):
        return None
    meta["last_error_alert_unix"] = time.time()
    return _PendingAlert(_error_alert_message(asset, error), kind="error", asset=asset)


async def fetch_one_report(settings: Settings, asset: str) -> EtrReport:
    config = settings.etr
    client = build_client(config)
    return await client.fetch_report(asset)


def cached_reports(assets: list[str] | None = None) -> list[EtrReport]:
    """Last committed reports, readable without blocking the caller.

    Called from async Telegram handlers and the briefing, which run on the event
    loop. Taking the shared lock with a blocking retry stalled the loop for the
    full two-second read timeout and then returned nothing; instead try once and
    read anyway. Atomic replacement means a reader without the lock can only see
    the previous complete snapshot, and slightly stale content beats an empty
    cache.
    """
    with try_state_read_lock() as locked:
        if not locked:
            logger.info("ETR cache read proceeding without the shared lock: a writer is committing")
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
