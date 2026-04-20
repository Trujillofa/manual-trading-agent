# Hetzner Drift Capture — 2026-04-19

## Scope

This document captures the **remaining Hetzner-only working tree drift** after the Telegram runtime hotfix was validated and preserved locally.

This is **not** the incident fix itself. The incident fix already lives cleanly on the local branch:

- `hotfix/telegram-runtime-health`
  - `660bb77` `fix(config): fall back to TELEGRAM_* env vars when yaml keys are missing`
  - `65f2157` `fix(ops): add Telegram heartbeat runtime health checks`

## Runtime status at capture time

Production was healthy when this capture was made:

- Telegram `/status` round-trip succeeded end-to-end
- Docker healthcheck was `healthy`
- `python -m src.cli healthcheck` returned `ok`
- `telegram-poll` and `scan` processes were both running
- `scan.log` and `telegram_heartbeat.json` were fresh

## Hetzner backup created before hotfix merge

Remote backup path:

- `/home/emilio/manual-trading-agent/.ops-backups/20260420T023721Z`

Captured files in backup:

- `.env`
- `config/settings.yaml`
- `docker-compose.yml`
- `logs/scan.log`
- `logs/telegram.log`
- `src/cli.py`
- `src/config/settings.py`
- `src/notifications/telegram_commands.py`

## Remaining dirty files on Hetzner

Modified:

- `.claude/settings.json`
- `.claude/settings.local.json`
- `.gitignore`
- `CLAUDE.md`
- `config/settings.yaml`
- `docker-compose.yml`
- `scripts/run_confirmation_bakeoff.py`
- `scripts/run_entry_optimization.py`
- `src/cli.py`
- `src/config/settings.py`
- `src/data/dukascopy_fetcher.py`
- `src/notifications/telegram_commands.py`
- `src/strategy/multi_timeframe.py`
- `tests/test_high_low.py`

Untracked:

- `.ops-backups/`
- `docs/reports/V2R_REJECTION_2026-04-15.md`
- `docs/reports/V2R_REPLACEMENT_RFC_2026-04-15.md`
- `docs/reports/WATCHLIST_EXPANSION_2026-04-14.md`

## Classification

### 1. Likely hotfix-only overlap

These files appear to contain only the incident-fix behavior or deployment wiring needed for it:

- `docker-compose.yml`
  - healthcheck changed from file existence to `python -m src.cli healthcheck`
- `src/notifications/telegram_commands.py`
  - adds heartbeat file updates
  - logs polling failures instead of silently swallowing them
  - backs off on poll loop errors

These are the safest files to treat as part of the verified Telegram/runtime hotfix stream.

### 2. Mixed hotfix + unrelated Hetzner-only work

These files include the incident fix **and** broader server-side changes that should not be assumed equivalent to the hotfix.

#### `config/settings.yaml`

Contains hotfix-relevant Telegram keys:

- `scan_results: true`
- `bot_token: "${TELEGRAM_BOT_TOKEN}"`
- `chat_id: "${TELEGRAM_CHAT_ID}"`

But also contains larger operational/strategy drift:

- pair list expansion across majors/minors
- session window widened to `00-24`
- per-pair spread limits expansion
- pair priority expansion
- OANDA block removed from YAML

#### `src/config/settings.py`

Contains hotfix-relevant config fallback:

- `_resolve_env_placeholder`
- Telegram env fallback when YAML keys are absent
- stricter `is_configured` check using non-empty strings

But also includes unrelated schema/runtime drift:

- adds `trading.shadow`
- relaxes `TradingConfig` requirement from majors-only to majors-or-shadow
- updates payload loading for `shadow`

#### `src/cli.py`

Contains hotfix-relevant runtime health behavior:

- `SCAN_HEALTH_MAX_AGE_SECONDS`
- `TELEGRAM_HEARTBEAT_MAX_AGE_SECONDS`
- `_scan_log_path`
- `_telegram_heartbeat_path`
- `_path_age_seconds`
- `_healthcheck_status`
- `healthcheck` CLI command

But it also includes substantial unrelated Hetzner-only work:

- prior-bar breakout semantics (`previous_rolling_highest_high`, `previous_rolling_lowest_low`)
- `V2R` structural breakout variant work
- `shadow` pair handling
- `_logs_dir()` indirection for log location
- scan telemetry payload and aggregation machinery
- broader scan loop instrumentation and data-unavailable reporting
- spread source handling changes
- dashboard telemetry changes

`src/cli.py` is the highest-risk mixed file and should be split hunk-by-hunk in any follow-up extraction.

### 3. Likely unrelated Hetzner-only strategy/data experimentation

These files appear unrelated to the Telegram incident fix and should be reviewed as a separate workstream:

- `scripts/run_confirmation_bakeoff.py`
  - expanded bakeoff inputs/outputs and CSV/reporting paths
- `scripts/run_entry_optimization.py`
  - broader optimization/reporting changes
- `src/data/dukascopy_fetcher.py`
  - large-scale data loader and resampling changes
- `src/strategy/multi_timeframe.py`
  - strategy behavior changes
- `tests/test_high_low.py`
  - supporting test additions for high/low behavior

### 4. Workspace / operator metadata drift

These should be treated as environment-local until intentionally promoted:

- `.claude/settings.json`
- `.claude/settings.local.json`
- `.gitignore`
- `CLAUDE.md`
- `.ops-backups/`
- report docs under `docs/reports/`

## Diff footprint summary

Approximate remote diff stats observed during capture:

- `src/cli.py` — `399 insertions, 33 deletions`
- `src/data/dukascopy_fetcher.py` — `347 insertions, 29 deletions`
- `scripts/run_entry_optimization.py` — `165 insertions, 71 deletions`
- `scripts/run_confirmation_bakeoff.py` — `97 insertions, 14 deletions`
- `config/settings.yaml` — `68 insertions, 14 deletions`
- `tests/test_high_low.py` — `60 insertions`
- `CLAUDE.md` — `43 insertions, 12 deletions`
- `src/config/settings.py` — `22 insertions, 9 deletions`
- `src/notifications/telegram_commands.py` — `18 insertions, 2 deletions`

## Recommended extraction order

1. **Keep the hotfix branch as the source of truth for the incident fix**
   - `660bb77`
   - `65f2157`

2. **Extract the remaining Hetzner drift as a second workstream**
   - start with `src/cli.py`
   - then `src/config/settings.py`
   - then `config/settings.yaml`

3. **Treat strategy/data experimentation separately from ops recovery**
   - `src/data/dukascopy_fetcher.py`
   - `src/strategy/multi_timeframe.py`
   - bakeoff/optimization scripts
   - `tests/test_high_low.py`

4. **Do not normalize Hetzner against a clean baseline yet**
   - not until the mixed files are intentionally classified and preserved

## Bottom line

The Telegram/runtime incident fix is preserved and validated.

The remaining risk is **change classification**, not runtime stability.

The main follow-up target is the mixed Hetzner drift in:

- `src/cli.py`
- `src/config/settings.py`
- `config/settings.yaml`

Those files should be split from the verified hotfix before any broader repo sync or cleanup work happens.
