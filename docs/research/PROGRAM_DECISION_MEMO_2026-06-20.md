# Forex Program Decision Memo — 2026-06-20

**Repository:** `manual-trading-agent` (forex-exclusive)  
**Decision date:** 2026-06-20  
**Base context:** [`PROFITABILITY_PLAN_2026-06.md`](PROFITABILITY_PLAN_2026-06.md), [`CLOSED_RESEARCH_LANES.md`](CLOSED_RESEARCH_LANES.md)  
**Audience:** Humans and agents deciding what to build, run, or fund next in this repo.

---

## Decision

**Stop the forex profitability search loop.** The New Edge Program has completed every ranked lane in the 2026-06 plan. **No lane produced a KEEP candidate.** Current research work should halt rather than retune discarded hypotheses.

**Branch B remains the only justified operating mode:** a selective manual-alert and observability tool with no validated positive expectancy claim.

---

## Executive Summary

After honest gross-first falsification, realistic costs, and chronological IS/OOS gates across six distinct edge families (plus one data-proof lane), every forex-specific alpha hypothesis tested in this repository has failed promotion. The durable assets are the harness, evaluator, audit discipline, and the negative results themselves — not a deployable edge.

Continuing to tune parameters, widen universes, or add filters on discarded lanes would violate the project's fail-fast discipline and repeat known dead ends.

---

## Scope Boundaries

### This repo: forex only

`manual-trading-agent` is a **forex-exclusive** research and alert system. Crypto lanes, token-event strategies, and cross-asset ideas from external repos are **out of scope** here.

### External check: vibe-investing (closed, no follow-up)

The external `vibe-investing` repository was reviewed for transferable ideas. Its strongest crypto hypothesis — **token unlock 72-hour shock** — was independently falsified by `crypto-agent` on fresh Binance data:

| Metric | Result |
|---|---|
| Negative at 72h | 49.0% |
| Mean return at 72h | +0.98% |
| Paper claim | Did not survive independent prices |

**Conclusion:** No crypto follow-up is needed in this repo. Event-token / unlock-shock research belongs elsewhere, if anywhere.

---

## Lane Scoreboard (Final, 2026-06-20)

All lanes from the 2026-06 New Edge Program have written verdicts. **None are KEEP.**

| Lane | Status | One-line finding |
|---|---|---|
| FX directional TA (M15/H1 OHLC) | **CLOSED** | Gross PF ~1.0–1.07 across ORB and trend-pullback; live MTF variant extremely sparse |
| Daily multi-asset TSMOM | **CLOSED** | Gross PF 1.036, weak Sharpe; no edge before costs |
| Carry / swap (Hetzner cTrader) | **CLOSED_DISCARD** | All resolved pairs returned 0.0 swap; financing premise absent on account |
| Stat-arb (daily pairs z-score) | **DISCARD** | Gross pass on two spreads; OOS net PF failed gates |
| Event calendar data proof | **DATA_PASS** | Pinned HF snapshot validated (83k rows, 2007–2025); infrastructure only |
| Event surprise drift | **DISCARD** | Gross PF 1.200; OOS net PF 0.375 after widened spreads |
| Vol-regime compression breakout | **DISCARD** | Gross PF 1.114; OOS net PF 0.782 after 6-pip round-trip |

Authoritative detail: [`CLOSED_RESEARCH_LANES.md`](CLOSED_RESEARCH_LANES.md) and per-lane result documents under `docs/research/<lane>/`.

---

## What To Stop Doing

1. **No retuning** on any closed or discarded lane — parameters, lookbacks, buffers, confirmation windows, cost assumptions, pair lists, or "one more variant."
2. **No new falsifiers** on hypotheses that already have written DISCARD verdicts unless a genuinely new premise is documented first (see Re-Entry below).
3. **No profitability claims** from Branch B alert volume, near-setup counts, or observability metrics.
4. **No microstructure lane** as standalone alpha research. Execution-quality work is **deferred** unless attached to a future gross-positive forex edge that has already cleared the gross-first gate.
5. **No crypto lane** — token unlocks, funding rates, on-chain events, or vibe-investing event-token ideas do not belong in this repository.

The Event calendar **DATA_PASS** is a data infrastructure result, not an alpha result. It does not authorize further drift, avoidance, or mean-reversion prototypes on the discarded surprise-drift contract.

---

## Branch B: Continued Operating Posture

Branch B stays deployed as a **structured scanner + manual decision-support tool**:

- Telegram alerts and audit trail for human review
- Explainable gate stack (RSI MTF alignment, V2 breakout, ADX, session, news, Rule C)
- Observability scripts and human labeling — not P&L optimization

Branch B does **not** carry a validated edge. Alerts come from the same directional-TA family that failed honest backtests. Invest only in observability polish that helps a human operate a watchlist; do not gold-plate the alert stream or treat rare fires as evidence of expectancy.

References: [`FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md`](FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md), [`BRANCH_B_ALERT_REVIEW.md`](../BRANCH_B_ALERT_REVIEW.md), [`PROJECT_STATUS_2026-06.md`](../PROJECT_STATUS_2026-06.md).

---

## Microstructure: Explicitly Deferred

Per [`PROFITABILITY_PLAN_2026-06.md`](PROFITABILITY_PLAN_2026-06.md) §5, microstructure / execution-quality research asks whether spread behavior or tick data can improve an **already-positive** gross edge. Every tested edge family failed gross-first or net/OOS gates.

**Rule:** Do not open a microstructure lane to rescue discarded signals. Revisit only when a new forex hypothesis clears gross-first with PF meaningfully above 1.0 **before** costs, and microstructure work is scoped to cost reduction on that specific edge.

---

## Re-Entry Criteria (Forex-Specific Only)

Any future profitability work in this repository must **start from zero** with all of the following **before** writing strategy code:

1. **Genuinely new forex-specific premise** — a different edge source, data source, instrument structure, or timeframe architecture than any closed lane. "More parameters" or correlated OHLC variants are excluded.
2. **Written lane contract** — premise, why it is not a closed lane, universe, cost model, falsification test, pass gate, stop gate, first command. See [`GROK_RESEARCH_LOOP_ENGINEERING.md`](GROK_RESEARCH_LOOP_ENGINEERING.md).
3. **Data proof** — manifest confirming fields, units, history, and live availability. Missing or ambiguous data → **BLOCKED**, not DISCARD.
4. **Pre-written promotion gates** — gross-first, realistic costs, chronological IS/OOS, minimum trade counts, concentration checks. Gates are fixed before the run.
5. **Isolated worktree or branch** — research must not disturb Branch B on `main`/production.

Crypto, equity-only, or cross-repo ideas do not satisfy criterion 1 for this repository even if they pass gates elsewhere.

---

## Durable Assets (Keep Maintaining)

These remain valuable regardless of the profitability stop:

- Unified pure evaluator (`src/scanner/evaluator.py`) — live == backtest by construction
- Honest research harness (`research/`) — IS/OOS judge, costed driver, gross-vs-net diagnostic
- Audit trail (`logs/signal_audit.jsonl`) and Branch B deployment on Hetzner
- Closed-lane registry and machine-readable ledger (`research/new_edge/research_ledger.jsonl`)
- Agent-proof STOP guards on deprecated FX directional TA entrypoints

Maintain for operability and future **qualifying** research only.

---

## Immediate Actions

| Action | Owner | Status |
|---|---|---|
| Halt new-edge falsifier development on discarded lanes | Agents / humans | **Now** |
| Keep Branch B scanner running for observability | Ops (Hetzner) | Continue |
| Do not promote, retune, or paper-shadow any discarded family | All | **Now** |
| Consult this memo + `CLOSED_RESEARCH_LANES.md` before proposing work | All agents | **Required** |
| Future forex research: new premise → contract → data proof → gates → code | Future program | Only path in |

---

## Sign-Off

The 2026-06 forex profitability program is **complete with a negative result**. The honest conclusion is that no tested hypothesis in this repo's New Edge Program clears KEEP under pre-written gates.

**Stop searching inside closed lanes. Operate Branch B as an alert tool. Re-enter only on a documented, forex-specific new premise.**

---

*Related documents:* [`CLOSED_RESEARCH_LANES.md`](CLOSED_RESEARCH_LANES.md) · [`PROFITABILITY_PLAN_2026-06.md`](PROFITABILITY_PLAN_2026-06.md) · [`PROJECT_STATUS_2026-06.md`](../PROJECT_STATUS_2026-06.md) · [`GROK_RESEARCH_LOOP_ENGINEERING.md`](GROK_RESEARCH_LOOP_ENGINEERING.md)