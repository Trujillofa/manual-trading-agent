# manual-trading-agent — Next Workflow (2026-11)

**Written:** 2026-11-16 · **For:** builders (Cursor, Hermes, Claude Code) and the owner
**Context:** cross-repo review of all trading agents. Posture is **Branch B**: a
selective manual-alert / decision-support desk (`docs/BRANCH_B_OPS_POSTURE_2026-08.md`),
not an alpha engine. FX OHLC TA is closed and agent-guarded. Remaining research lanes
(PEAD, listed futures) are BLOCKED on owner data decisions; carry is CLOSED. So the
work here is **desk reliability** plus **one owner decision memo**. No new alpha lane.

## Ground rules for every item

- `CLAUDE.md` and `research/program.md` STOP banner stay authoritative. Do not reopen
  closed lanes (FX OHLC TA, TSMOM, carry, stat-arb, event drift, vol regime,
  COT reversal, HTF fib).
- No live orders. Alert content changes must not be sold as expectancy.
- Python 3.11, `from __future__ import annotations`, ruff (line 100), never `print()`
  in library code (the scanner's existing CLI prints are legacy; don't add more).
- Tests: `pytest tests/ -v --tb=short`; `ruff check src/ tests/`; `mypy src/`.
- Branch per item: `fix/<item-id>-<slug>` / `feat/<item-id>-<slug>`.

## Status board

| ID | Item | Owner | Depends on | Size |
|----|------|-------|-----------|------|
| MA-1 | Fix #69 properly: feed freshness for the **scanned** universe | Builder | — | S |
| MA-2 | Owner decision memo: PEAD / listed futures / Zacks: buy or close | Builder drafts, **Owner decides** | — | XS |
| MA-3 | Optional: MT5 calendar as a cross-check for the Forex Factory news feed | Builder | mt5 MT-1, MT-2 | S |
| MA-4 | ETR render reliability (open PR #71) | Owner review | — | XS |

---

## MA-1 — Feed freshness for the scanned universe (re-scopes #69)

**Root cause of #69 (verified 2026-11-16):** the 27 symbols froze because commit
`17d6819` (#38, 2026-08-10) replaced the 27 FX pairs in `config/settings.yaml`
(`trading.pairs.majors/minors`) with `XAU/USD, BTC/USD, OIL, NASDAQ`. The scanner
stopped fetching them, so `CandleStore.save_multi_timeframe` stopped writing them.
Nothing in `src/` reads candles back from `CandleStore`: `run_scan()` in
`src/scanner/scan_service.py` fetches fresh data each cycle via `DataFetcher.fetch`.
So **no scan has been evaluating dead tape**; the old rows are history.

What #69 rightly wants still matters: a scanned instrument whose provider silently
returns an old last bar (yfinance does this for continuous futures) would produce
signals on stale data, and today nothing would say so.

**Build:**

1. `src/data/freshness.py` (pure):
   `bar_age_seconds(df, now_utc) -> float | None` (age of the last **closed** bar) and
   `is_stale(instrument_id, timeframe, age_s, now_utc) -> tuple[bool, str]`.
   Threshold = 3 × timeframe seconds, **only while the instrument is in session**:
   use `session_windows()` from `src/config/instruments.py`; add a weekend rule for
   non-crypto asset classes (closed Fri 21:00 → Sun 22:00 UTC). Crypto (`BTC/USD`) is
   24/7.
2. In `run_scan()`, after the three `fetcher.fetch(...)` calls: compute age for 1h,
   30m, 15m. If any is stale → set telemetry state `data_stale`, reasons
   `["stale_feed:<tf>:<age>s"]`, write the audit row through the existing
   `_build_scan_telemetry_payload(...)`, **skip evaluation** for that instrument
   (`continue`). Add `candle_age_s_1h/30m/15m` fields to every audit row (additive).
3. Alerting: one Telegram message on transition fresh → stale and stale → fresh per
   instrument, persisted in `logs/feed_freshness_state.json` (same pattern as the
   other `logs/*_state.json`). No message every 15 minutes.
4. `CandleStore`: add `latest_timestamps() -> dict[(symbol, tf), int]` and a CLI
   `python -m src.cli candles-status [--json]` that lists every stored symbol with
   last bar age **and** whether it is in the current watchlist (from settings). The
   27 FX symbols show `watchlist=no`. Do **not** delete their rows; they are history.
5. Tests: (a) stale 15m inside session → no signal and audit reason `stale_feed`;
   (b) same age on Saturday for `XAU/USD` → not stale; (c) BTC on Saturday → stale;
   (d) `candles-status` marks non-watchlist symbols.
6. Docs: one paragraph in `docs/MANUAL_TRADING_AGENT_OPERATOR_GUIDE.md`.

**Done when:** merged and deployed per the usual Docker path; comment on #69 with the
root cause above and close it via the PR.

## MA-2 — Owner decision memo for blocked lanes

**Why:** three lanes sit BLOCKED waiting for a purchase decision that never comes,
which keeps the research side idle by default.

Builder drafts `docs/research/OWNER_DATA_DECISION_2026-11.md`, one table row per lane:

| Lane | What would be bought | Indicative cost (owner to confirm) | Tradeable on current accounts? | Recommendation |
|------|----------------------|------------------------------------|--------------------------------|----------------|
| PEAD (`docs/research/pead/`) | Point-in-time US earnings estimates with `estimate_observed_ts` (Zacks or similar) | quote needed | **No**: needs a US-equity broker; current accounts are FX/CFD/prop | Close unless an equities account is planned |
| Listed futures (`docs/research/term_structure/`) | Contract-month OHLC + OI, ≥15y | quote needed | **No** futures account | Close |
| Zacks MCP (`docs/research/zacks_mcp/`) | Paid tier history | quote needed | Context only | Close |

Owner marks each `BUY (budget, deadline)` or `CLOSE`. Closing is a valid, useful
outcome: it ends the waiting state. Update `docs/research/RESEARCH_LANES_PROGRAM_2026-08.md`
and `docs/research/CLOSED_RESEARCH_LANES.md` accordingly.

## MA-3 — Optional: MT5 calendar cross-check for news lockouts

`src/news/news_checker.py` pulls `https://nfs.faireconomy.media/ff_calendar_thisweek.xml`.
That is a single scraped feed; when it fails, lockouts silently disappear (graceful
degradation by design). After mt5 MT-1/MT-2 ship and the PIT log is rsynced to the
server, add a read-only secondary source:

- `src/news/mt5_calendar_source.py` reads the rsynced JSONL, maps MT5 importance HIGH
  ↔ FF 3-star, returns the same `NewsEvent` objects.
- `NewsChecker` merges both; an event present in only one source is still a lockout;
  log disagreement counts daily.
- Test: FF feed down + MT5 file fresh → lockout still applies; both down → existing
  degradation path and an explicit audit flag.

Skip this item if the owner prefers fewer moving parts. It's resilience, not alpha.

## MA-4 — ETR render reliability (open PR #71)

PR #71 (`fix(etr): retry a render that timed out before the analysis hydrates`) is
open. Review and merge or close. No new ETR work beyond that.

---

## Cross-repo dependencies

| This item | Needs | From |
|-----------|-------|------|
| MA-3 | MT5 calendar PIT log on the server | mt5-arch-integration **MT-1, MT-2** |
