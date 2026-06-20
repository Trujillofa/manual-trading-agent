# Forex DecisionSignal + Outcome Evaluation Contract — 2026-06-20

## Purpose

Define a structured **forex-only** layer for recording `DecisionSignal` records and evaluating
their post-hoc outcomes so we can measure **scanner and alert quality**, not claim profitability.

This contract is **infrastructure planning only**. It does not implement trading logic, does not
promote any strategy, and does not reopen closed alpha lanes.

Architecture inspiration (concepts only, not a port):

- `daily_stock_analysis` `DecisionSignal` lifecycle, evidence, risk/watch conditions, and status model.
- Fixed-horizon post-hoc outcome evaluation with frozen statistical dimensions.
- `AnalysisContextPack`-style data-quality context with field status and low-sensitivity summaries.

This repo remains **forex-exclusive**. Stock strategies, crypto instruments, and cross-asset
expansion are out of scope.

## Relationship to Branch B

Branch B is a **selective manual-alert and observability tool**, not a validated autonomous edge.
See `docs/BRANCH_B_ALERT_REVIEW.md` and `docs/research/FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md`.

This contract formalizes what Branch B already journals informally:

- `logs/signal_audit.jsonl` (append-only telemetry and lightweight outcome rows)
- `scripts/summarize_alerts.py` (periodic quality summaries)

The evaluation layer exists to answer operational questions:

- Was context complete when the signal fired?
- Was the alert timely, noisy, or correctly blocked?
- Did price move favorably or adversely in fixed windows after the signal?
- Are blockers (news, spread, session, account constraints) behaving as intended?

It does **not** answer: "Is this strategy profitable enough to trade live?"

## Non-Goals

| Non-goal | Rationale |
|---|---|
| Port stock strategies from `daily_stock_analysis` | Forex-only repo; stock signal semantics do not transfer |
| Add crypto instruments or feeds | Explicitly out of scope |
| Open a new alpha research lane | All current alpha lanes are closed or discarded; see §Closure |
| Retune MTF RSI, ADX, SMA, Donchian, ORB, or confirmation gates | FX directional TA is permanently closed |
| Use Branch B observation data for parameter tuning | Observation window is for operational usefulness only (`docs/research/PROFITABILITY_PLAN_2026-06.md`) |
| Implement live trading, order routing, or position sizing | Infrastructure contract only |
| Promote any signal family to production from outcome stats | Outcome evaluation is descriptive, not a promotion gate |
| Build a walk-forward optimizer on signal outcomes | No optimization in this layer |

## Scope Boundary

**In scope (future implementation, not this PR):**

- Canonical `DecisionSignal` schema for forex pairs.
- Append-only signal store (JSONL first; optional sidecar DB later).
- Data-quality context pack captured at signal time.
- Fixed-horizon outcome evaluator (1h / 4h / 1d).
- Summary stats API or CLI (hit rate, pips, MFE/MAE).

**Out of scope:**

- Any change to live scanner entry logic, gates, or Telegram alert copy.
- Strategy falsifiers under `research/new_edge/`.
- Gross-first / net / OOS promotion harnesses.
- Human feedback UI (may be added later; not required for v1).

## DecisionSignal Schema (v1)

Each signal is an append-only record. Fields below are the minimum contract.

### Core identity

| Field | Type | Required | Description |
|---|---|---|---|
| `signal_id` | string (UUID) | yes | Stable identifier for dedup and outcome join |
| `ts` | ISO 8601 UTC | yes | Signal creation / fire timestamp |
| `symbol` | string | yes | Normalized pair, e.g. `EUR/USD` |
| `direction` | enum | yes | `BUY` or `SELL` — describes the **setup direction**, not an order |
| `action` | enum | yes | `watch`, `avoid`, or `alert` only in v1 |
| `source` | enum | yes | `branch_b_scan`, `manual_review`, or `research_harness` |
| `source_ref` | string | no | Scanner run id, Telegram message id, harness run id, etc. |
| `status` | enum | yes | Lifecycle status (see §Lifecycle) |
| `expires_at` | ISO 8601 UTC | no | Explicit expiry; derived from horizon if omitted |
| `engine_version` | string | yes | Schema/evaluator version, e.g. `forex-decision-signal-v1` |

### Action semantics (v1)

| `action` | Meaning |
|---|---|
| `watch` | Setup forming or worth monitoring; no alert sent |
| `avoid` | Deliberate negative recommendation (e.g. news block, poor context) |
| `alert` | Branch B fired a human-facing alert |

v1 does **not** include `buy`, `sell`, `add`, `reduce`, or `hold`. Those belong to execution
systems, not this observability layer.

### Evidence and context

| Field | Type | Required | Description |
|---|---|---|---|
| `evidence_summary` | string (≤ 500 chars) | yes | Human-readable reason the signal exists |
| `watch_conditions` | string[] | no | Conditions that would upgrade, downgrade, or invalidate |
| `risk_summary` | string (≤ 300 chars) | no | Spread, news, session, or constraint risks at signal time |
| `data_quality` | object | yes | Context pack at signal time (see §Data Quality) |
| `metadata` | object | no | Low-sensitivity scanner fields (RSI levels, ADX, blocker codes) |

Evidence must be **descriptive**, not prescriptive trading advice beyond the allowed actions.

### Optional plan fields (advisory only)

Branch B already surfaces advisory TP/SL in Telegram. These are optional and never drive execution:

| Field | Type | Description |
|---|---|---|
| `entry_ref_price` | float | Reference price at signal time (mid or close) |
| `tp_pips` | float | Advisory take-profit distance in pips |
| `sl_pips` | float | Advisory stop distance in pips |
| `invalidation` | string | Plain-language invalidation rule |

## Lifecycle Status

| `status` | Meaning | Transitions |
|---|---|---|
| `active` | Signal is current and evaluable | → `expired`, `invalidated`, `closed` |
| `expired` | Horizon or `expires_at` reached without explicit close | terminal |
| `invalidated` | Watch condition or opposite signal voided the premise | terminal |
| `closed` | Human or system marked complete (e.g. outcome resolved) | terminal |

Rules (adapted from DSA lifecycle discipline):

- Terminal states (`expired`, `invalidated`, `closed`) must not revert to `active`.
- A new opposing `active` signal for the same `(symbol, direction)` should mark the prior
  `active` signal `invalidated` and record the invalidation reason in `metadata`.
- Dedup key (best-effort): `(source, symbol, action, direction, ts_bucket_15m)` for scan-origin
  signals; `(source_ref)` when available.

## Data Quality Context Pack

Captured at `ts`. Describes **input completeness**, not signal correctness.

### Block catalog (forex v1)

| Block key | Fields | Purpose |
|---|---|---|
| `ohlc_m15` | bar count, latest bar ts, gap flags | M15 availability for alignment checks |
| `ohlc_m30` | bar count, latest bar ts, gap flags | M30 availability |
| `ohlc_h1` | bar count, latest bar ts, gap flags | H1 availability |
| `spread` | value (pips), source, ts | Spread at decision time |
| `news` | status, next_event, blocked | NewsChecker outcome |
| `session` | name, is_liquid_window | FX session state (London/NY/Asia/overlap) |
| `broker_account` | trading_allowed, constraint_codes | Prop-firm / risk lock / margin constraints |

### Field status enum

Each block item uses one of:

`available`, `missing`, `stale`, `fallback`, `partial`, `not_supported`, `fetch_failed`

These describe **data quality**, not whether the signal was profitable.

### Top-level `data_quality` object

```json
{
  "overall_level": "good | usable | limited | poor",
  "limitations": ["ohlc_m15: stale", "spread: missing"],
  "blocks": {
    "ohlc_h1": { "status": "available", "latest_bar_ts": "2026-06-20T14:00:00Z" },
    "spread": { "status": "available", "value_pips": 1.2 },
    "news": { "status": "available", "blocked": false, "summary": "clear" },
    "session": { "status": "available", "name": "london_ny_overlap" },
    "broker_account": { "status": "available", "trading_allowed": true }
  }
}
```

Scoring weights and thresholds may be defined at implementation time. This contract only requires
that `overall_level` and `limitations` are persisted for later aggregation.

### Redaction

The context pack must not store API keys, Telegram tokens, webhook URLs, account numbers, or raw
broker credentials. Follow the low-sensitivity principle from DSA `AnalysisContextPack` overviews.

## Fixed-Horizon Outcome Evaluation

Outcomes are stored in a **sidecar** structure keyed by `(signal_id, horizon, engine_version)`.
They do not mutate the original signal record.

### Horizons (fixed, no tuning)

| Horizon | Definition |
|---|---|
| `1h` | 60 minutes after `ts` |
| `4h` | 240 minutes after `ts` |
| `1d` | 24 hours after `ts` |

No other horizons in v1. Do not add 15m, 3d, or "swing" without a new contract revision.

### Look-ahead discipline

- Price paths are evaluated **only from bars strictly after `ts`**.
- Reference price for return math: `entry_ref_price` if present, else mid/close at `ts`.
- If OHLC coverage ends before horizon end, set `eval_status=unable` with `unable_reason`
  (e.g. `insufficient_forward_bars`, `market_closed_gap`).
- No peeking at future news, spreads, or session labels when computing returns.

### Directional return math

For each horizon, compute in **pips** (pair-appropriate pip size):

| Metric | Definition |
|---|---|
| `forward_return_pips` | Signed pip move in signal `direction` from reference price to horizon close |
| `max_favorable_pips` (MFE) | Best favorable excursion before horizon end |
| `max_adverse_pips` (MAE) | Worst adverse excursion before horizon end |
| `hit` | `forward_return_pips > 0` for directional signals; `null` for `avoid` |

For `action=avoid`, evaluate whether taking the **opposite** of the avoided direction would have
been painful (context QA only). Do not treat avoid-hit rate as alpha evidence.

### Frozen statistical dimensions

At evaluation time, copy and freeze:

`action`, `direction`, `source`, `symbol`, `data_quality.overall_level`, `session.name`,
`news.blocked`, `spread.value_pips` (bucketed), `status` at eval time.

Historical stats must not depend on live re-joins that rewrite past context.

### Aggregate reports (descriptive only)

Per cohort (source, symbol, action, horizon, data_quality level):

| Stat | Description |
|---|---|
| `count` | Evaluable signals |
| `hit_rate` | Fraction with `hit=true` |
| `avg_forward_return_pips` | Mean directional return |
| `median_forward_return_pips` | Median directional return |
| `avg_mfe_pips` | Mean max favorable excursion |
| `avg_mae_pips` | Mean max adverse excursion |
| `unable_rate` | Fraction with `eval_status=unable` |

**No optimization.** No threshold sweeps. No "best horizon" selection for strategy promotion.

## Storage and Integration (planned)

### Phase 0 — Extend existing JSONL (lowest friction)

Continue appending to `logs/signal_audit.jsonl` with normalized `kind` values:

- `decision_signal` — full v1 signal payload
- `decision_signal_outcome` — sidecar outcome row per horizon

`scripts/summarize_alerts.py` gains cohort stats; existing `alert_outcome` rows remain valid legacy.

### Phase 1 — Evaluator module

Add `src/evaluation/` (or `research/evaluation/`) with:

- `record_signal()` — validate schema, append JSONL
- `run_outcomes()` — explicit batch evaluation (DSA: `POST .../outcomes/run` pattern)
- `summarize_outcomes()` — CLI / report generation

No scanner imports from evaluation in reverse (evaluation reads audit log + OHLC cache only).

### Phase 2 — Optional persistence

If JSONL volume warrants it, mirror to SQLite or Timescale sidecar tables. Not required for v1.

## Gates

### This PR (docs-only)

| Gate | Requirement |
|---|---|
| G0 | Single contract document committed |
| G1 | No `src/` trading logic changes |
| G2 | No new `research/new_edge/` lane |
| G3 | Explicit closure of all alpha lanes restated |
| G4 | Branch B remains observability-only |

### Future implementation PRs (separate)

Each implementation PR must:

1. Reference this contract by path and `engine_version`.
2. Ship tests for schema validation, look-ahead safety, and horizon math.
3. Avoid changing live scanner gates or alert frequency targets.
4. Avoid copy that implies validated edge or profitability.
5. Fail CI if outcome code imports strategy promotion harnesses.

### Alpha research (not this layer)

Any future alpha attempt requires:

- A **separate contract** under `docs/research/<lane>/`.
- A **gross-first falsifier** with pre-written pass/stop gates.
- Registration in `research/new_edge/research_ledger.jsonl`.
- Isolation in `research/new_edge/` so Branch B behavior is unchanged.

Outcome evaluation stats **cannot** substitute for gross-first / OOS promotion gates.

## Explicit Closure — Alpha Lanes

The following remain **closed or discarded**. This infrastructure does not reopen them.

| Lane | Status | Reference |
|---|---|---|
| FX directional TA (M15/H1) | CLOSED | `docs/research/FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md` |
| Daily multi-asset TSMOM | CLOSED | `docs/research/CLOSED_RESEARCH_LANES.md` §2 |
| Carry (Hetzner cTrader) | CLOSED_DISCARD | `docs/research/carry/CTRADER_CARRY_DISCARD_2026-06-12.md` |
| Daily FX stat-arb | DISCARD | `docs/research/stat_arb/STAT_ARB_RESULTS_2026-06-18.md` |
| Event surprise drift | DISCARD | `docs/research/events/EVENT_DRIFT_RESULTS_2026-06-19.md` |
| Vol-regime compression breakout | DISCARD | `docs/research/vol_regime/VOL_REGIME_RESULTS_2026-06-19.md` |

Full scoreboard: `docs/research/CLOSED_RESEARCH_LANES.md` (2026-06-20).

Recording Branch B alert outcomes **does not** revive any row in that table.

## Example Records

### Signal

```json
{
  "kind": "decision_signal",
  "signal_id": "8f3c2e1a-4b5d-6c7e-8f9a-0b1c2d3e4f5a",
  "ts": "2026-06-20T08:15:00Z",
  "symbol": "EUR/USD",
  "direction": "BUY",
  "action": "alert",
  "source": "branch_b_scan",
  "status": "active",
  "engine_version": "forex-decision-signal-v1",
  "evidence_summary": "M15/M30/H1 RSI aligned; 20-bar low wick reclaim; ADX 22",
  "watch_conditions": ["Invalidate on 15m RSI cross below 50", "Close back below 20-bar low"],
  "risk_summary": "Spread 1.1 pips; news clear; London session",
  "entry_ref_price": 1.0850,
  "tp_pips": 20.0,
  "sl_pips": 30.0,
  "data_quality": {
    "overall_level": "good",
    "limitations": [],
    "blocks": {
      "ohlc_m15": { "status": "available", "latest_bar_ts": "2026-06-20T08:00:00Z" },
      "ohlc_m30": { "status": "available", "latest_bar_ts": "2026-06-20T08:00:00Z" },
      "ohlc_h1": { "status": "available", "latest_bar_ts": "2026-06-20T08:00:00Z" },
      "spread": { "status": "available", "value_pips": 1.1 },
      "news": { "status": "available", "blocked": false, "summary": "clear" },
      "session": { "status": "available", "name": "london" },
      "broker_account": { "status": "available", "trading_allowed": true }
    }
  }
}
```

### Outcome sidecar

```json
{
  "kind": "decision_signal_outcome",
  "signal_id": "8f3c2e1a-4b5d-6c7e-8f9a-0b1c2d3e4f5a",
  "horizon": "4h",
  "engine_version": "forex-decision-signal-v1",
  "eval_status": "completed",
  "evaluated_at": "2026-06-20T12:15:00Z",
  "forward_return_pips": 12.4,
  "max_favorable_pips": 18.2,
  "max_adverse_pips": 3.1,
  "hit": true,
  "frozen": {
    "action": "alert",
    "direction": "BUY",
    "source": "branch_b_scan",
    "symbol": "EUR/USD",
    "data_quality_level": "good",
    "session": "london",
    "news_blocked": false
  }
}
```

## Success Criteria (infrastructure)

After implementation (future PRs), we consider the layer successful when:

1. Every Branch B `alert` can be queried with full `data_quality` context at fire time.
2. ≥ 95% of `alert` signals receive evaluable `1h` and `4h` outcomes when OHLC feed is healthy.
3. `scripts/summarize_alerts.py` (or successor) reports cohort hit rate, avg pips, MFE/MAE without
   manual JSONL parsing.
4. No live trading behavior changes and no new alpha lane is opened.

These criteria measure **observability maturity**, not trading profitability.

## References

| Document | Role |
|---|---|
| `docs/BRANCH_B_ALERT_REVIEW.md` | Current Branch B evaluation dimensions |
| `docs/research/PROFITABILITY_PLAN_2026-06.md` | Non-tuning rule for observation data |
| `docs/research/CLOSED_RESEARCH_LANES.md` | Alpha lane closure scoreboard |
| `daily_stock_analysis/docs/decision-signals.md` | Lifecycle + outcome sidecar inspiration |
| `daily_stock_analysis/docs/analysis-context-pack.md` | Data-quality block inspiration |
| `scripts/summarize_alerts.py` | Existing summarizer to extend |

## Revision History

| Date | Change |
|---|---|
| 2026-06-20 | Initial infrastructure contract (docs-only PR) |