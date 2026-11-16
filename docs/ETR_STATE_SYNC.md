# ETR state synchronization

Scope: the ETR poller (`src/etr/service.py`), cache readers and the renderer
busy contract. This is the contract any additional ETR writer must follow.

## One writer lock

- Lock file: `logs/etr_coordination.lock`, in the same directory as
  `etr_state.json`. Take it with `flock(2)` and never unlink it: the lock belongs
  to the open file description, so deleting the name lets two writers lock
  different inodes.
- Writers take it **exclusively** around load → diff → save. Cache and briefing
  reads take it **shared**; they do not need a second lock.
- Acquisition is bounded (30 s poll, 2 s read). A holder that overruns turns the
  cycle into a skip with no state write, not a wedged process.
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
(DD/MM/YYYY, optional time, ISO with or without zone). Before committing a
render:

- strictly older than the stored render → keep the stored state, mark the poll
  skipped, append a `stale_source` audit event, send no alert;
- equal timestamps → accepted (content conflicts at equal time are out of scope);
- missing/unparseable timestamps, or a naive-vs-zoned mismatch → fail open and
  accept the render, because guessing a timezone would be worse than the
  regression this guard covers.

## Busy renderer

`deploy/etr-renderer/app.py` waits at most 20 s for its process-local browser
lock, then answers `200` with `outcome: "busy"` instead of blocking for a full
render timeout. The client maps both that payload and a renderer/proxy
`HTTP 503` to `EtrUnavailableError`; the poller counts the asset as unavailable,
writes only an audit event and sends no error alert. A busy renderer is a
scheduling fact, not an ETR failure.

## Alert delivery is at-most-once

The state commit reserves delivery before sending: a change alert stores the new
fingerprint and `last_alerted_at`, and an error alert advances the global
cooldown. Then the notification is sent outside the lock.

Consequences, stated deliberately:

- a crash between the commit and the send **loses** that one alert;
- a crash, timeout or ambiguous Telegram outcome can never **duplicate** it;
- a failed error send still consumes its cooldown.

This is not exactly-once delivery. There is no outbox or retry loop, and the
receiving service offers no idempotency key. At-most-once was chosen because the
operational complaint was alert noise; a missed alert is the accepted failure
mode. Do not describe this path as crash-proof or exactly-once.

## Out of scope

- Renderer coordination across uvicorn workers, replicas or containers, and an
  end-to-end browser startup/cleanup deadline.
- A direct-fetch/freshness writer and its watermark identity protocol: this
  branch has no such writer. Any added writer must take the same exclusive lock.
- Equal-timestamp content conflicts and strict corruption quarantine.
- Transactional coupling of audit, shadow state and notifications with the state
  snapshot.
