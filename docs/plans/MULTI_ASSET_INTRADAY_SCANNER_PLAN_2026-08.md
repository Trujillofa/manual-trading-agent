# Plan: Multi-asset intraday scanner (XAU, BTC, OIL, NASDAQ) — v2

**Status:** reviewed against code + live Hetzner deploy (2026-08-10). Supersedes the v1 chat draft.

## Goal

Pivot the live scanner from the FX majors/minors watchlist to **four liquid multi-asset instruments** with **intraday signals** (RSI MTF stack **and** EMA golden/death cross). Delivery is **scanner + Telegram only** — Branch B alert tooling, **not** a KEEP / profitability claim.

## Locked decisions

| Decision | Choice |
|----------|--------|
| Contracts | **XAU/USD → `GC=F`**, **BTC/USD → `BTC-USD`**, **OIL → `CL=F` (WTI)**, **NASDAQ → `NQ=F`** |
| Watchlist | **Replace FX** — only these four live |
| Signals | **Both** RSI MTF (existing evaluator path) **and** EMA GC/DC |
| EMA periods | **20 / 50** (not 9/21) |
| Scope | Scanner + Telegram; no research harness / promotion gates |

## Hetzner production audit (2026-08-10) — read before executing

Live deploy inspected via `ssh -i ~/.ssh/hetzner_deploy root@46.225.119.221`:

- **Deploy location:** `/home/emilio/manual-trading-agent` — a git clone of `origin` (github.com/Trujillofa/manual-trading-agent), **not** the `/opt` rsync flow the ctrader skill documents. Container `manual-trading-agent` built 2026-07-25, up 2 weeks, healthy. Volume mounts: `data/`, `logs/`, `config/`, `results/`. **`src/` is baked into the image** → config flip needs container restart only; code changes need `docker compose build` + `up -d`.
- **Three-way git divergence (must reconcile before deploy):**
  - Server HEAD = `1d31a30` "feat: add OHLCV candle persistence via SQLite" (2026-07-25) — lives only on `origin/docs-issue-triage-plan`, **never merged to main**. Production is running a feature main doesn't have; deploying main as-is would silently remove OHLCV persistence.
  - Local main has unpushed `8c10620` (PEAD); `origin/main` has unpulled `83815d0` (results cleanup).
  - Server working tree has uncommitted edits in 4 src files (`rsi.py`, `digest.py`, `evaluator.py`, `gates.py`) — verified **typing-only** (casts/TypedDicts, no behavior change). Safe to discard, but stash/commit before any pull.
- **Live config:** FX watchlist (7 majors + minors), EMA `standalone_notifications_enabled: false`, periods 9/21. No config drift vs server HEAD. The local EMA branch work is **not** deployed.
- **Live state:** 2 active SELL signals (AUD/JPY, USD/JPY) in `logs/active_signal_state.json`; also `alignment_state.json`, `near_setup_state.json`, `ema_near_state.json`, legacy `cooldown_state.json`. `signal_audit.jsonl` = 47.7 MB (append-only; log-rotation monitoring already exists).

**Pre-deploy reconciliation (new Phase 0b):** pull `origin/main` into local main; push local commits; merge (or consciously drop) `1d31a30` / `origin/docs-issue-triage-plan` into main so production's SQLite persistence isn't lost on the next deploy. Stash the server's typing-only edits.

## Why this design

1. **Different instrument class** than closed FX directional TA research — OK as *operational* Branch B alerts, but do **not** market as validated edge.
2. Continuous futures / crypto proxies give free **yfinance** 15m/30m/1h OHLC without broker auth (60-day intraday limit — irrelevant for live scanning, matters if anyone backfills).
3. Hardcoded FX assumptions (`JPY` pip heuristics, `=X` fallback, Twelve Data 3+3 split, Forex Factory currency extraction) will mis-size or mis-map gold/BTC/oil/NQ — so **instrument metadata is the foundation**, not just a pair-list swap.

## Non-goals

- No honest IS/OOS KEEP evaluation, no Dukascopy multi-asset backtest, no paper-trade P&L promotion.
- No cTrader / OANDA execution for these symbols.
- No reopening closed FX TA research as "proven on NQ".
- Do not commit unrelated dirty research/pead/htf/plantillas files.

## Branch strategy (corrected)

`feat/ema-intraday-crossover-alerts` has **zero commits ahead of main** — the entire EMA standalone-alert implementation (354-line cli.py diff, telegram.py, settings, `tests/test_ema_standalone_alerts.py`) exists only as uncommitted working-tree changes. "Fresh branch from main + reuse EMA dispatch" is not an available combination.

**Order:** (1) commit the EMA work on this branch as its own `feat(alerts):` commit; (2) continue the multi-asset work on top (same branch or a new `feat/multi-asset-intraday-scanner` branched from it). PR them together or stacked.

## Architecture

```
config/settings.yaml  →  TradingConfig majors = [XAU/USD, BTC/USD, OIL, NASDAQ]
                      →  instruments: { yf_symbol, point_size, currencies, sessions }
                      →  strategy.ema fast=20 slow=50, standalone GC/DC on 15m/30m

src/config/instruments.py (registry)  →  populates/overrides DataFetcher.SYMBOL_MAP
    → fetch 1h/30m/15m OHLC (yfinance only for these four; td_symbol=None → skip TD)

scan loop (cli.run_scan)
    → point_size(pair) from registry for display math (replace JPY heuristics)
    → buffer: ATR-scaled (see below), not point-size-scaled
    → session: per-instrument windows (BTC 24/7; futures ~23h)
    → news: registry-driven currency list (XAU/BTC keep USD lockout)
    → RSI MTF evaluate_entry (existing) + ATR TP/SL (prices, no pip conversion)
    → EMA 20/50 GC/DC standalone Telegram (existing dispatch path)
```

### Canonical product IDs

| ID | Label | yfinance | Asset class | point_size* | currencies (news) |
|----|-------|----------|-------------|-------------|-------------------|
| `XAU/USD` | Gold | `GC=F` | metal futures | `0.1` | `[XAU, USD]` → USD lockout active |
| `BTC/USD` | Bitcoin | `BTC-USD` | crypto spot | `1.0` | `[USD]` → USD lockout active |
| `OIL` | WTI crude | `CL=F` | energy futures | `0.01` | `[]` → no lockout |
| `NASDAQ` | Nasdaq 100 fut | `NQ=F` | index futures | `0.25` | `[USD]` (explicit — avoids the accidental `{NAS, DAQ}` split in `_extract_currencies`) |

\* Display/risk units for messages and audit fields only — **not** used for buffer sizing (ATR-scaled instead) and not broker contract multipliers.

### News posture (decided)

`NewsChecker._extract_currencies` (src/news/news_checker.py:282) already degrades gracefully (`OIL` → empty set → no lockout), but `NASDAQ` accidentally splits to `{NAS, DAQ}` and `XAU/USD`/`BTC/USD` inherit USD lockout. Decision: **keep USD 3-star lockout for XAU, BTC, and NASDAQ** (NFP/CPI move all three; it's free risk filtering) and make the currency list **registry-driven** — the news gate consults `instrument.currencies` instead of string-splitting the symbol. OIL gets an empty list for v1.

### Buffer sizing (changed from v1)

A single global `breakout_buffer_pips × point_size` cannot span assets 4 orders of magnitude apart in price ($1 buffer on $60k BTC is noise; $0.10 on gold is plausible). Instead: **ATR-scaled buffer** — `buffer = buffer_atr_frac × ATR(14, 15m)`, default `0.05`. Scale-free, one tunable instead of four, and deletes the point-size-for-buffers concern entirely. Keep `breakout_buffer_pips` only for FX backward-compat in the evaluator override path; the four new instruments use the ATR fraction. Same treatment for EMA `touch_threshold_pips` if price-touch context stays enabled.

### Fixed-pip TP/SL fallback (deleted)

`src/cli.py:1013–1021` falls back to fixed 30/90-pip TP/SL when ATR is unavailable — with the new instruments this emits absurd levels ($30 TP on BTC, 7.5 pt on NQ). The 2026-06 ATR fix means it should rarely trigger. **Delete it: no ATR → no signal** (log the skip). Aligns with the deletion principle.

## Implementation phases

### Phase 0 — Commit EMA branch work
Commit the existing uncommitted EMA standalone-alert implementation (cli.py, telegram.py, settings.py, settings.yaml, tests) as its own commit on this branch. Everything below builds on it.

### Phase 0b — Git reconciliation (from Hetzner audit)
- `git pull origin main` locally (picks up `83815d0`); push local `8c10620`.
- Merge `origin/docs-issue-triage-plan` (`1d31a30` OHLCV SQLite persistence) into main — or record an explicit decision to drop it. Production must not lose it silently on next deploy.
- On the server: `git stash` the typing-only src edits before any pull.

### Phase 1 — Instrument registry + data maps

**Add `src/config/instruments.py`** (single module, not a package — one dataclass, one dict, three helpers; promote to a package only if execution-agent symbol maps ever land):

- `InstrumentSpec`: `id`, `display_name`, `asset_class`, `yf_symbol`, `td_symbol: str | None` (None = yfinance only), `point_size`, `currencies: list[str]`, `session_windows_utc`, `spread_filter_enabled`.
- Helpers: `get_instrument(id)`, `point_size(id)`, `session_windows(id)`. Unknown IDs raise in scan.

**Update `src/data/fetcher.py`:**

- Registry **populates/overrides** `YFINANCE_MAP` (aliased as `SYMBOL_MAP`, fetcher.py:121) at init — one source of truth, no parallel map to keep in sync.
- `_to_yfinance_symbol` (fetcher.py:297) must never append `=X` to registry instruments (verified: unmapped `NQ=F`-style IDs currently get mangled).
- `_to_td_symbol` (fetcher.py:156) guard: `td_symbol is None` → skip Twelve Data entirely (verified breakage: `BTC-USD` → `BTC/-USD`, `GC=F` → `GC/=F`).
- Unit test: mapped fetch path resolves correct tickers (mock yfinance).

### Phase 2 — Config watchlist + strategy retune

**`config/settings.yaml`:**

```yaml
trading:
  pairs:
    majors: [XAU/USD, BTC/USD, OIL, NASDAQ]
    minors: []
    shadow: []
strategy:
  session_filter_enabled: true
  session_allowed_utc: ["00-24"]     # global fallback; instruments override
  spread_filter_enabled: false       # no reliable free bid/ask for GC/CL/NQ/BTC
  breakout_buffer_atr_frac: 0.05     # replaces point-size buffer for these instruments
  ema:
    enabled: true
    fast_period: 20
    slow_period: 50
    medium_period: 100   # NOT 50 — would duplicate slow; check consumers, delete if unused
    long_period: 200
    standalone_notifications_enabled: true
    standalone_signal_types: [crossover]
    standalone_timeframes: ["15m", "30m"]
    standalone_session_filter_enabled: true

instruments:
  XAU/USD:  { yf_symbol: GC=F,    point_size: 0.1,  currencies: [XAU, USD], session_allowed_utc: ["00-21", "22-24"] }
  BTC/USD:  { yf_symbol: BTC-USD, point_size: 1.0,  currencies: [USD],      session_allowed_utc: ["00-24"] }
  OIL:      { yf_symbol: CL=F,    point_size: 0.01, currencies: [],         session_allowed_utc: ["00-21", "22-24"] }
  NASDAQ:   { yf_symbol: NQ=F,    point_size: 0.25, currencies: [USD],      session_allowed_utc: ["00-21", "22-24"] }
```

**Session windows note:** `_session_allowed` (src/scanner/gates.py:245) does `start_h <= hour < end_h` — **wrap-around windows like `"22-06"` silently never match**. Real CME Globex wraps midnight (~22:00 UTC reopen → 21:00 UTC close), so express it as two windows `["00-21", "22-24"]` (`hour < 24` always satisfiable). Comment this in the registry so nobody "fixes" it later.

**`src/config/settings.py`:** load instrument specs; YAML is source of truth for production; keep `TradingConfig.majors` non-empty validation.

### Phase 3 — Kill FX-only heuristics in the scan path

Pip-heuristic sites (verified): `src/cli.py:405, 478, 1013, 1077, 1192`.

| Site | Change |
|------|--------|
| cli.py:405, 1077, 1192 | Display math (`tp_pips`/`sl_pips` → audit + dashboard) → `point_size(pair)`; audit fields become "points" |
| cli.py:478 | `pip_size` feeding breakout check → ATR-scaled buffer (above); pip_size retained only for FX display |
| cli.py:1013–1021 | Fixed 30/90-pip TP/SL fallback → **delete** (no ATR → no signal, log skip) |

Other gates:

| Concern | v1 behavior |
|---------|-------------|
| ATR TP/SL | Keep ATR multipliers; levels are prices — works unchanged |
| Spread gate | Off for these instruments (`spread_filter_enabled: false` in registry) |
| News lockout | Registry `currencies` list; empty list → no lockout (OIL) |
| Session | Per-instrument windows; BTC never blocked |
| Lot size | Leave config value; Telegram advisory only |

**Backtest/analyze guards (new):** `backtest`, `backtest-enhanced`, and Dukascopy paths have no notion of these instruments — `backtest --pair BTC/USD` would silently mangle to `BTCUSD=X` or 404 on Dukascopy. Add a registry check at the top of those commands → clear error "instrument not supported for backtest". One `if` per command; keeps the non-goal honest.

**EMA dispatch:** keep standalone GC/DC path; periods 20/50; 15m/30m filter; fingerprint uses `20/50`.
**RSI path:** keep single `evaluate_entry` call; overrides/ATR-buffer must not break purity (injected params only).

### Phase 4 — Telegram UX

- RSI messages: instrument label + "points" wording when `asset_class != fx`.
- EMA crossover: pair displays as canonical IDs.
- `telegram_commands.py`: update help text watchlist.
- Digest: no structural change (fingerprints stay pair-based).

### Phase 5 — Tests + manual smoke

**Automated**
- Registry: four IDs → yf symbols, point sizes, currencies, sessions.
- Settings load: majors = the four; EMA 20/50; spread off.
- `_to_yfinance_symbol` never appends `=X` for registry instruments; `td_symbol=None` skips TD.
- ATR-buffer helper unit test; fixed-pip fallback removed (test asserts skip-on-no-ATR).
- News: `NASDAQ` → `[USD]` (no NAS/DAQ split); `OIL` → no lockout; `XAU/USD` blocked during USD 3-star window.
- Session: BTC always allowed; two-window futures session blocks 21–22 UTC; wrap-window regression test.
- EMA fingerprint `20/50`; existing `test_ema*.py` / `test_telegram_config.py` updated for new defaults.
- Backtest guard: `backtest --pair BTC/USD` errors cleanly.

**Manual smoke**
```bash
.venv/bin/python -m src.cli scan --pairs "XAU/USD,BTC/USD,OIL,NASDAQ"
```
Confirm: data non-empty, no `=X` mangling, RSI/EMA paths log cleanly, Telegram dry without tokens, **and `tp_pips`/`sl_pips` values in `signal_audit.jsonl` are sane for BTC (~$60k asset) and NQ (~$20k)** — point-size bugs surface here first.

### Phase 6 — Deploy + docs

Deploy mechanics for **this** repo (differs from the ctrader skill's rsync flow):

1. Phase 0b reconciliation done; PR merged to main; push.
2. On server: `cd /home/emilio/manual-trading-agent && git stash && git pull`.
3. **Prune state files** (mounted `logs/`): clear FX records from `active_signal_state.json` (2 live JPY SELLs will be orphaned), `alignment_state.json`, `near_setup_state.json`, `ema_near_state.json`; delete legacy `cooldown_state.json`. Keep `signal_audit.jsonl` (append-only, 47.7 MB, rotation monitored).
4. `docker compose build && docker compose up -d` (src is baked into image — restart alone is not enough for code changes).
5. Verify: container healthy, first scan logs the four instruments, no FX invalidation spam.

Docs (short):
- Operator note: watchlist is multi-asset Branch B; not a validated edge.
- Settings comment: continuous contracts (`GC=F`/`CL=F`/`NQ=F`) roll; levels approximate; yfinance 15m limited to 60d history.
- Research governance: **not** a reopen of closed FX TA; any future KEEP requires new contract + costs + IS/OOS.

## File touch list

| File | Action |
|------|--------|
| `src/config/instruments.py` | **Add** (single module) |
| `src/data/fetcher.py` | Registry wiring into SYMBOL_MAP; `=X`/TD guards |
| `src/config/settings.py` | Instrument config load |
| `config/settings.yaml` | Four majors; EMA 20/50; instruments block; ATR buffer frac |
| `src/cli.py` | point_size display; ATR buffer; delete fixed-pip fallback; session/news via registry; backtest guards |
| `src/news/news_checker.py` | Consult registry currencies (keep string fallback for FX) |
| `src/notifications/telegram.py` | pips→points wording |
| `src/notifications/telegram_commands.py` | Help text |
| `tests/test_instruments.py` | **Add** |
| `tests/test_telegram_config.py`, `tests/test_ema_standalone_alerts.py` | Update defaults |
| `docs/` operator note | Add |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Deploying main removes prod-only OHLCV persistence (`1d31a30`) | Phase 0b merge/decision **before** deploy |
| Continuous futures roll gaps | Document; alerts qualitative; no backtest claim |
| yfinance gaps / delayed futures | Accept for Branch B; log empty frames, continue |
| RSI V2 sparse on new assets | Alert aid only; optional V0 profile later if too quiet |
| EMA 20/50 slower than 9/21 | Intentional noise control on BTC/NQ |
| Session window wrap bug | Two-window convention + registry comment + regression test |
| Stale FX state on flip | Explicit prune step in deploy runbook |
| TD API mis-parses symbols | `td_symbol=None` → yfinance only |
| Replacing FX surprises deploy | Feature branch; config+code deploy via documented runbook above |

## Success criteria

- `scan` on the four symbols fetches multi-TF data without `=X` mangling or TD splits.
- TP/SL prices and audit `tp_pips`/`sl_pips` sane for gold (~$2.4k), BTC (~$60k), NQ (~$20k).
- EMA 20/50 GC/DC fires standalone on 15m/30m with per-instrument sessions.
- Config has no live FX majors/minors; backtest commands reject the new IDs cleanly.
- Tests green; no research-ledger KEEP claims; prod OHLCV persistence preserved.

## Out of scope follow-ups

- Per-instrument RSI thresholds / ADX; NQ RTH-only session profile.
- Costed smoke harness on these four.
- Broker symbol map for execution agents.
- Re-add FX as shadow list if dual mode is needed.
