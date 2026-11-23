# ETR state synchronization

Scope: the ETR poller (`src/etr/service.py`), cache readers, the renderer busy
contract and alert delivery. This is the contract any additional ETR writer must
follow.

## One writer lock

- Lock file: `logs/etr_coordination.lock`, in the same directory as
  `etr_state.json`. Take it with `flock(2)` and never unlink it: the lock belongs
  to the open file description, so deleting the name lets two writers lock
  different inodes.
- Writers take it **exclusively** around load → diff → save. Cache and briefing
  reads try it **shared** with `try_state_read_lock()`, which never blocks; they
  read the snapshot anyway if a writer holds it, because publication is atomic.
  Never use the blocking `state_read_lock()` from an async caller: `flock` retries
  with `time.sleep`, so a contended read freezes the event loop for the whole
  timeout.
- Acquisition is bounded (30 s poll). A holder that overruns turns the cycle into
  a skip with no state write, not a wedged process. The skip is recorded as
  `commit_skipped_lock_busy` with the assets that were fetched and thrown away.
- The lock never covers network I/O. ETR fetches happen before it; Telegram
  sends happen after it. A slow render cannot stall another poll.
- The lock coordinates cooperating processes that see the same inode. It does
  not coordinate separate containers/volumes unless the same file is mounted.

## Atomic snapshot

`save_state_with_meta` writes a same-directory temp file, fsyncs it, renames it
with `os.replace`, then fsyncs the directory. Readers therefore see either the
previous complete file or the next complete file. A crash before the rename
leaves the previous snapshot intact; a SIGKILL can leave an unreferenced
`.etr_state.json.*.tmp` file behind, which is harmless.

A truncated or unparseable snapshot is logged as a warning and read as empty
state. Fail-closed quarantine is deliberately **not** implemented here: it
changes availability and needs a caller-level policy.

## Monotonic source timestamps

ETR's own "Actualizado" value is parsed by `src/etr/source_time.py`
(DD/MM/YYYY, optional time, ISO with or without zone) from the rendered page
text or HTML, inline (`Actualizado: 11/08/2026 14:30`) or on the line below the
label. A candidate that is not a timestamp is rejected, so the guard cannot go
inert on a section title.

Ordering compares at the coarsest precision both values carry: if either side has
no clock time and the calendar dates match, the render is **not** older. A
date-only stamp is ETR's whole day, not midnight, so treating it as 00:00 dropped
every later same-day update for the rest of the day.

Before committing a render:

- strictly older than the stored watermark → keep the stored state, mark the poll
  skipped, append a `stale_source` audit event, send no alert;
- equal timestamps → accepted (content conflicts at equal time are out of scope);
- missing/unparseable timestamps, or a naive-vs-zoned mismatch → fail open and
  accept the render, because guessing a timezone would be worse than the
  regression this guard covers.

The watermark is `AssetState.source_at_max`: the latest source value ever seen,
not the last committed one. It never moves backwards, and on a tie it keeps the
value that carries a clock time, so a date-only commit cannot lower it and let a
genuinely older render of the same day through later.

## Busy renderer

`deploy/etr-renderer/app.py` serializes renders on a process-local browser lock
and bounds **every** phase: waiting for the lock (20 s), then the render itself
(navigation budget + 30 s for browser launch, page setup and teardown). The
holder is bounded too: when its deadline expires the request answers
`outcome: "timeout"`, the browser is abandoned (cleanup detached and bounded) and
the lock is released, so a Playwright await that never returns cannot starve the
feed. A caller that disconnects also releases it.

Outcomes:

- `outcome: "busy"` — the 20 s queue deadline expired. The client maps it to
  `EtrRendererBusyError`, the poller counts the asset as unavailable, writes an
  `unavailable` audit event with `kind: renderer_busy` and sends no error alert
  for that cycle.
- `HTTP 503` with `outcome: "busy"` in the body — same thing, e.g. through a
  proxy. Any other 503 (proxy error page, readiness probe, dead sidecar, empty
  body) is a **renderer failure**: it is counted as an error and alerts. Only the
  body distinguishes scheduling from an outage.
- A busy renderer that never recovers is not silent: after
  `RENDER_BUSY_ALERT_AFTER_POLLS` (3) consecutive busy cycles the poller sends one
  `renderer_busy_streak` alert with the assets affected and the streak start. The
  counter lives in durable meta (`renderer_busy_streak`, `renderer_busy_since`)
  and resets on the first cycle where nothing was busy.

`GET /health` reports `status`, `degraded`, `render_in_flight`, `busy_since`,
`busy_for_seconds`, `consecutive_busy` and `consecutive_overruns`, and answers
`503` (not a plain `200`) once the renderer has been unable to complete a render
for 4 minutes or has abandoned 2 renders in a row. The compose healthcheck relies
on that status code. Nothing restarts an unhealthy container automatically: the
signal is for an operator or an external watchdog.

## Alert delivery is at-most-once

The state commit reserves delivery before sending: a change alert stores the new
fingerprint and `last_alerted_at`, an error alert advances the global cooldown,
and every alert appends an `alert_reserved` audit event **before** the send. The
send then happens outside the lock and appends `alert_sent` or `alert_lost`.

Consequences, stated deliberately:

- a crash between the commit and the send **loses** that one alert: the
  `alert_reserved` record has no matching outcome;
- a notifier that returns `False`, or raises, is recorded as `alert_lost` with the
  reason, and that alert is never retried;
- a crash, timeout or ambiguous Telegram outcome can never **duplicate** it;
- a failed error send still consumes its cooldown, and a failed send of any kind
  is not re-queued.

This is not exactly-once delivery. There is no outbox or retry loop, and the
receiving service offers no idempotency key. At-most-once was chosen because the
operational complaint was alert noise; a missed alert is the accepted failure
mode. Do not describe this path as crash-proof or exactly-once.

## Audit events

`logs/etr_audit.jsonl`, one JSON object per line:

| event | meaning |
| --- | --- |
| `error` | fetch/parse failure for an asset |
| `unavailable` | ETR has no fresh analysis, or the renderer was busy (`kind`) |
| `stale_source` | an older render was refused; the watermark was kept |
| `commit_skipped_lock_busy` | fetch succeeded, another writer held the lock, work discarded |
| `renderer_busy_streak` | long busy streak alert fired (`consecutive_busy_polls`) |
| `alert_reserved` | alert is durable in state, send about to happen |
| `alert_sent` / `alert_lost` | delivery outcome, with `reason` when lost |

## Out of scope

- Renderer coordination across uvicorn workers, replicas or containers. The
  deadline and health signals assume the single worker the image runs.
- A direct-fetch/freshness writer and its watermark identity protocol: this
  branch has no such writer. Any added writer must take the same exclusive lock.
- Equal-timestamp content conflicts and strict corruption quarantine.
- Transactional coupling of audit, shadow state and notifications with the state
  snapshot: audit lines are appended after the snapshot is durable, so a crash can
  leave an alert reserved with no outcome (`alert_reserved` alone) and cannot leave
  an outcome without its reservation.
