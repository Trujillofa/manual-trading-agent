# Profitability Plan — New Edge Program (2026-06)

This plan defines the next attempt to make `manual-trading-agent` profitable. It starts from the
current evidence, not optimism:

- FX majors directional OHLC technical analysis on M15/H1 is closed.
- Daily multi-asset time-series momentum is closed.
- The 2026 new-edge program (carry, stat-arb, event drift, vol-regime) has completed with no KEEP candidate.
- Branch B is a selective manual-alert and observability tool, not a validated autonomous edge.

The next program must therefore change the premise. More RSI thresholds, more confirmation variants,
more Donchian/TSMOM lookbacks, or cheaper cost assumptions are out of scope.

## Objective

Find one strategy family with a real, deployable expectancy after costs, proven by the existing honest
harness discipline:

- gross edge first,
- realistic costs,
- chronological IS/OOS split,
- portfolio-level metrics where relevant,
- no OOS tuning,
- paper-shadow before any live-risk use.

## Non-Negotiable Rules

1. Do not reopen closed lanes.
2. Do not optimize a gross PF near 1.0.
3. Do not judge profitability from a single symbol or a single lucky period.
4. Do not promote a strategy with fewer than 30 OOS trades unless the strategy is explicitly slower-term
   and has a separate, pre-written statistical bar.
5. Do not use the Branch B observation window as a tuning dataset. It is for operational usefulness and
   blocker distribution only.

## Why The Previous Attempts Failed

The previous programs were structurally vulnerable:

- Intraday FX directional TA had too little gross edge and too much friction.
- Current strict live MTF RSI is too sparse to validate.
- Daily TSMOM diversified correctly, but the gross edge was still too close to 1.0 before costs.

The lesson is not "try more filters." The lesson is to seek a different source of return.

## Current Backtest Verdicts To Preserve

These are the current trustworthy research verdicts. Older optimistic per-pair promotion tables are
retired because they came from divergent engines and do not match the unified live entry.

| Strategy family | Scope | Trades / sample | Gross PF | Net PF / cost result | Verdict |
|---|---|---:|---:|---:|---|
| ORB reversal/breakout | FX majors/minors, M15 | 6,535 | 1.02 | 0.34 | DISCARD |
| Trend-pullback momentum | FX majors/minors, M15 | 8,298 | 1.07 | 0.19 | DISCARD |
| Trend-pullback momentum | FX majors/minors, H1 | 3,870 | 1.01 | 0.53 | DISCARD |
| Strict live MTF RSI | Current live-family harness | 0 recent IS / 0 OOS | 0.00 | 0.00 | DISCARD / too sparse |
| Daily multi-asset TSMOM | Metals, indices, core FX | ~10y / 10 instruments | 1.036 | before costs only | DISCARD |

Planning implication: the next profitable attempt must use a new edge source, not another member of
the same directional OHLC TA or daily TSMOM families.

## New Edge Program Lane Scoreboard (2026-06-20)

All ranked hypotheses from this plan were run under `research/new_edge/` with pre-written gates.
No lane reached KEEP / paper-shadow promotion.

| Lane | Status | Key result |
|---|---|---|
| FX directional TA | CLOSED | Gross PF ~1.0–1.07; no edge before costs |
| Daily TSMOM | CLOSED | Gross PF 1.036 before costs |
| Carry (Hetzner cTrader) | CLOSED_DISCARD | All resolved pairs returned 0.0 swap |
| Stat-arb (daily pairs) | DISCARD | Best OOS net PF 1.128 < 1.20 |
| Event data proof (HF calendar) | DATA_PASS | 83k rows, indicator coverage ≥96% |
| Event surprise drift | DISCARD | OOS net PF 0.375 < 1.20 |
| Vol-regime compression breakout | DISCARD | Gross PF 1.114 → pooled net PF 0.802; OOS net PF 0.782 |

Full closure record: `docs/research/CLOSED_RESEARCH_LANES.md`.

**Recommendation:** Microstructure / execution-quality research is **DEFERRED**. It is not a
standalone alpha lane. Pursue it only when attached to an already-positive gross edge (for example,
to quantify execution improvement on a strategy that already passed gross-first gates). Do not use
spread or execution analysis to rescue discarded signals.

## Ranked New Approaches

### 1. Carry / Funding / Swap-Aware FX Portfolio

**Premise:** Use overnight financing / interest-rate differential as the primary signal, not price
patterns. This satisfies the "different edge source" re-entry criterion.

**Universe:** FX majors and liquid minors where broker swap/financing data is available.

**Data required:**

- Broker long/short swap rates or financing charges by pair.
- Central-bank policy rates as a fallback sanity check.
- Daily OHLC only for risk, volatility, and drawdown simulation.

**Prototype:**

- Rank pairs by expected daily carry after broker swap.
- Long positive carry, short negative carry only when realized volatility is not spiking.
- Vol-target positions.
- Optional risk-off kill switch based on broad USD strength or volatility shock.

**Pass gate:**

- Gross carry return positive before price movement.
- Net OOS positive after swap/spread/slippage.
- Portfolio OOS PF >= 1.20 or Sharpe/MAR threshold agreed before the run.
- Drawdown acceptable under carry-crash stress.

**Stop gate:**

- Broker swap advantage disappears after realistic rollover costs.
- Returns come only from one regime or one pair.
- Tail drawdowns dominate the average carry.

### 2. FX / CFD Statistical Arbitrage Pairs

**Premise:** Trade relative mispricing, not outright directional TA. This changes the edge family.

**Candidate spreads:**

- EUR/USD vs GBP/USD residuals.
- AUD/USD vs NZD/USD.
- CAD/JPY vs AUD/JPY.
- Gold vs silver ratio.
- Index pairs such as US500 vs US100, if data quality allows.

**Data required:**

- Synchronized intraday or daily close data.
- Spread/commission model for both legs.
- Robust missing-data handling.

**Prototype:**

- Rolling hedge ratio.
- Stationarity / half-life filter.
- Enter only when z-score is extreme and expected reversion exceeds costs.
- Exit at mean reversion, time stop, or stationarity break.

**Pass gate:**

- Stable OOS spread half-life.
- OOS net PF >= 1.20 with sufficient trade count.
- No single pair contributes most of the profit.
- Monte-Carlo drawdown survives shuffled trade order.

**Stop gate:**

- Cointegration unstable across splits.
- Net edge vanishes after two-leg costs.
- Returns cluster in one historical episode.

### 3. Event / Calendar Strategy

**Premise:** Use scheduled macro events as the edge source: pre-event risk avoidance, post-event drift,
or volatility expansion. This uses information timing, not chart patterns.

**Events:**

- Central-bank rate decisions.
- CPI / NFP / GDP surprises.
- 3-star Forex Factory style events already used by the news module.

**Data required:**

- Historical economic calendar with timestamps, currency, impact, actual/forecast/previous if possible.
- Intraday OHLC around event windows.
- Spread widening assumptions around releases.

**Prototype lanes:**

- Avoidance edge: quantify whether news blocks prevented adverse movement.
- Post-event drift: trade only after first 15-30 minutes confirms direction and spreads normalize.
- Mean-reversion after overreaction: only where historical event type supports it.

**Pass gate:**

- OOS event-type profitability after widened spreads.
- Minimum count by event family.
- No evidence of look-ahead leakage.

**Stop gate:**

- Calendar data cannot be made reliable.
- Edge depends on forecast/actual data not available live at decision time.
- Spread widening consumes expected move.

### 4. Volatility Regime / Range Compression Breakout — DISCARD (2026-06-20)

**Status:** Implemented and falsified. Merged in PR #10 (`545fef0`). Lane closed.

**Fixed prototype run:** H1 Donchian range compression (20-bar, 252-bar 10th percentile, 3-bar
persistence) → breakout entry 07:00-17:00 UTC → 24-bar time stop. Seven FX majors,
2016-01-01 → 2026-06-01.

| Metric | Value |
|---|---:|
| Pooled trades | 3778 |
| Gross PF | 1.114 |
| Pooled costed net PF (6-pip RT) | 0.802 |
| OOS net PF | 0.782 |
| Max year concentration | 12.8% (2020) |

**Verdict:** Gross stage passed; lane **DISCARD** after costs and OOS net PF gate (0.782 < 1.20).
Gross edge too small to survive 6-pip round-trip costs.

**Do not retune:** compression percentile, persistence, entry window, hold, pairs, or timeframe.

**Artifacts:** `docs/research/vol_regime/VOL_REGIME_RESULTS_2026-06-19.md`,
`research/new_edge/vol_regime/range_compression_breakout_test.py`.

### 5. Microstructure / Execution-Quality Research — DEFERRED

**Status:** Not started as a standalone lane. Deferred unless tied to an already-positive gross edge.

**Premise:** If cTrader bid/ask or tick data is available, study spread behavior and executable levels.
The goal is not yet a signal; it is to learn where the current cost model is wrong or exploitable.

**Questions:**

- Which pairs have stable, low spreads during the scanner's active windows?
- Do spread spikes align with current false positives or blocked states?
- Is there an execution window where signal quality improves enough to matter?

**Pass gate:**

- Produces a measurable cost reduction or execution filter that improves an already-positive gross edge.

**Stop gate:**

- Only improves a no-edge signal family.
- Used as a rescue attempt for a discarded signal (carry, stat-arb, event drift, vol-regime, TA, TSMOM).

## Execution Sequence

### Phase 0 — Keep Branch B Running

Duration: current 2-week Hetzner observation window.

Deliverables:

- `scripts/summarize_alerts.py --days 14 --format table`
- Human labels for any fired or near-fired setups.
- Decision: useful alert tool, too quiet to evaluate, or needs observability-only polish.

No profitability claims come from this phase.

### Phase 1 — Prepare The New Research Surface

Create an isolated worktree:

```bash
./scripts/worktree-create.sh research-new-edge-program main
cd ../manual-trading-agent-research-new-edge-program
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Add research code under `research/new_edge/` so it cannot interfere with live Branch B.

Initial deliverables:

- Data manifest for carry/swap, calendar, and spread sources.
- Cost model document per asset class.
- Reusable portfolio metrics: net PF, Sharpe, MAR, max drawdown, turnover, trade count, exposure.
- One command per hypothesis that writes a results artifact.

### Phase 2 — Run Hypothesis 1 First: Carry / Funding

Reason: it is the cleanest break from failed OHLC directional TA and needs the least new market data.

Data prerequisite:

- `docs/research/carry/CARRY_DATA_MANIFEST_2026-06.md`

Required output:

- `docs/research/carry/CARRY_RESULTS_TEMPLATE.md` copied to a dated result file,
- `docs/research/carry/CARRY_RESULTS_YYYY-MM-DD.md`
- gross vs net table,
- IS/OOS split,
- pair contribution table,
- drawdown and carry-crash analysis,
- KEEP / DISCARD verdict.

### Phase 3 — If Carry Fails, Run Stat-Arb

Only proceed if Phase 2 is discarded or inconclusive for data reasons.

Required output:

- `docs/research/stat_arb/STAT_ARB_RESULTS_YYYY-MM-DD.md`
- spread stability diagnostics,
- OOS trade table,
- two-leg cost sensitivity,
- KEEP / DISCARD verdict.

### Phase 4 — Event / Calendar

Only proceed after data availability is proven. This lane is invalid without reliable historical event data.

Required output:

- `docs/research/events/EVENT_RESULTS_YYYY-MM-DD.md`
- event family counts,
- no-leakage audit,
- widened-spread sensitivity,
- KEEP / DISCARD verdict.

## Promotion Criteria

A strategy may move from research to paper-shadow only if:

- OOS result passes the pre-written gate.
- Gross result is meaningfully above 1.0 before costs.
- Net result survives realistic costs.
- Result is not concentrated in one pair or one month.
- The implementation can run side-by-side with Branch B without changing Branch B behavior.
- A kill switch and risk limits are defined before paper-shadow.

Paper-shadow must run at least 30 days, or longer for daily/low-frequency strategies, before any live-risk
discussion.

## Immediate Next Actions

The ranked new-edge hypotheses in this plan have been executed and closed (see lane scoreboard above).
No KEEP candidate emerged. Next work must define a **genuinely new premise** per
`docs/research/CLOSED_RESEARCH_LANES.md` — not retunes of closed lanes.

1. Keep Branch B Hetzner observation running as an alert/observability tool only.
2. Do not reopen carry, stat-arb, event drift, vol-regime, TA, or TSMOM without a new edge source.
3. Defer microstructure / execution-quality research unless attached to a future gross-positive strategy.
4. Any new research lane requires a new contract, data proof, and pre-written gates before code.
5. Consult [`GROK_RESEARCH_LOOP_ENGINEERING.md`](GROK_RESEARCH_LOOP_ENGINEERING.md) and update the ledger on every run.
6. Zacks MCP lane is open as schema-only (`docs/research/zacks_mcp/`). Do not write relationship code, and do not treat it as a PEAD sample, until a licensed historical extract records `DATA_PASS`.
