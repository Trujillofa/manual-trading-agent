# Roll-Yield Data Manifest — 2026-06

**Status:** Phase-1 deliverable for Premise #1 (futures term-structure / roll yield). Gate-definition only.
**Authority:** `docs/research/CANDIDATE_PREMISES_NEW_CLASS_2026-06.md` (accepted premise #1) + `research/program.md` re-entry protocol + `docs/research/PROFITABILITY_PLAN_2026-06.md` rule "no strategy logic until data and cost model are documented."
**Scope of this doc:** define exactly what data must exist, where it comes from, and the hard stop — _before_ any strategy code.

---

## 1. Research question

Can a diversified long-short futures portfolio, ranked monthly by expected roll yield, earn a positive net return whose edge comes from **roll** (curve structure) rather than **spot** (price direction) — surviving realistic commission + slippage + roll costs on an out-of-sample window?

This phrasing is deliberate: the attribution (roll vs spot) is the whole point, and is the direct response to the failure mode of closed lane 3 (FX carry whose "edge" was really price drift, and whose funding data resolved to zero).

---

## 2. The core data-engineering requirement (this is the real gate)

**Roll yield cannot be computed from a back-adjusted continuous price series.** Back-adjustment (Panama/ratio/calendar) removes exactly the price gaps at roll that _are_ the roll yield, and ratio-adjusted levels are undefined across zero/negative settlements (WTI April 2020).

Therefore the data manifest requires, for each market:

| Data object                                                                         | Used for                                                                                         | Source shape                        |
| ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | ----------------------------------- |
| **Individual contract OHLC + open interest** by expiry (e.g., `CLZ2025`, `CLF2026`) | Computing front-vs-deferred spread → **roll yield (the signal)** + settlement MTM + roll accrual | Per-contract daily bars             |
| **TSMOM return index** (chained active-contract simple returns; harness spec §2.1)  | **TSMOM control run only** — not for signal or attribution                                       | Derived from individual contracts   |
| **Roll calendar** (active-contract switch dates, with OI/volume confirmation)       | Timing rebalance, roll-cost accounting, attribution state machine                                | Derived from individual-contract OI |

This three-object requirement is the **hard gate.** A data source that provides only a continuous series is _insufficient_ for this lane, regardless of how clean or cheap it is.

---

## 3. Proposed universe (v1 commodity-only, 12 markets)

v1 uses **one economic carry definition** across commodity futures only. Equity-index futures (ES, NQ) and Treasury futures (ZN, ZB) are **excluded from v1** — they carry different curve mechanics and would require asset-class-specific definitions. Candidates for v2 expansion only if v1 clears gates.

| Sector      | Market        | Symbol | Notes                            |
| ----------- | ------------- | ------ | -------------------------------- |
| Energy      | WTI crude     | CL     | most liquid commodity future     |
| Energy      | Natural gas   | NG     | seasonal, strong curve structure |
| Energy      | RBOB gasoline | RB     | energy complex                   |
| Energy      | Heating oil   | HO     | energy complex                   |
| Metals      | Gold          | GC     |                                  |
| Metals      | Silver        | SI     |                                  |
| Metals      | Copper        | HG     | growth/cycle proxy               |
| Agriculture | Corn          | ZC     |                                  |
| Agriculture | Soybeans      | ZS     |                                  |
| Agriculture | Wheat (CBOT)  | ZW     |                                  |
| Livestock   | Live cattle   | LE     |                                  |
| Livestock   | Lean hogs     | HE     |                                  |

**Gate:** ≥10 of these 12 must clear the data-quality checklist (§7) for ≥15 complete years each before any backtest. The verifier MAY reject individual markets, but rejected markets **must not** be replaced after seeing strategy results. If fewer than 10 clear, **STOP** — record as data-blocked. Do not proceed with a thin universe that violates profitability-plan rule 3 ("do not judge from a single symbol or period").

**Excluded from v1:** equity indices (ES, NQ, RTY), Treasury/rate futures (ZN, ZB, ZF, ZT), softs (KC, SB — thinner roll structure), ICE-listed Brent (dual-venue data friction).

---

## 4. Required history and splits

- **Minimum history:** **15 complete years** per accepted market. No market with <15 years of individual-contract data may enter the universe.
- **Period:** through the most recent complete month.
- **IS/OOS split:** one chronological **65/35 holdout** (sacred — never tune on OOS; see harness spec). There is **no parameter search** and **no walk-forward optimization** in this test. With 15y: ~9.75y IS / ~5.25y OOS.
- **OOS calendar-year requirement:** the OOS window MUST contain **at least five complete calendar years**. This makes the pre-written single-year P&L concentration gate (≤25% from any one year) mathematically achievable and auditable.
- **Trade-count semantics (addresses review Gap A):** For slow strategies the standard "≥30 trades" rule is ill-defined. Pre-registered definition for this lane: **one "trade" = one market's directional position change at a rebalance** (entry, exit, or flip). Statistical bar (in lieu of ≥30): OOS net PF ≥ 1.20 **AND** a deterministic 3-month block bootstrap of OOS monthly portfolio returns (2,000 resamples, seed `20260624`) whose 5th-percentile net PF > 1.0. Finalized in the harness spec.

---

## 5. Candidate data sources (owner decision: free-tier vs paid)

| Source                                   | Individual contracts?    | Continuous?              | Coverage / history                                  | Cost                | Suitability                                                                                              |
| ---------------------------------------- | ------------------------ | ------------------------ | --------------------------------------------------- | ------------------- | -------------------------------------------------------------------------------------------------------- |
| **FirstRate Data**                       | yes                      | yes (gap-adjusted)       | ~130 active futures, daily + intraday, back to 2007 | paid (verify tier)  | **Strong candidate** — meets §2 three-object requirement                                                 |
| **Norgate Data** (futures package)       | yes                      | yes (Norgate-continuous) | broad futures, decades                              | paid subscription   | **Strong candidate** — popular for futures research; pricing + coverage **needs verification**           |
| **CSI Data** (Unfair Advantage)          | yes                      | yes                      | deep history, many markets                          | paid                | Strong; institutional-leaning cost                                                                       |
| **Pinnacle Data Corp**                   | yes                      | yes                      | commodity-futures focus, deep                       | paid                | Strong for commodities                                                                                   |
| **CME settlement files**                 | yes                      | no (build your own)      | authoritative, free                                 | free + build effort | Authoritative primary; requires stitching                                                                |
| **Quandl / Nasdaq Data Link (Sharadar)** | partial                  | yes                      | varies                                              | paid                | Verify per-market availability                                                                           |
| **yfinance**                             | **no** (continuous only) | yes                      | gaps, symbol-map fragility                          | free                | **Insufficient for this lane** (fails §2) — usable only as a sanity cross-check on the continuous series |

**Required owner decision before Phase-1 build:** authorize a paid source that provides individual contracts (FirstRate / Norgate / CSI / Pinnacle), **or** accept the free path of stitching CME settlement files (higher build effort, narrower coverage). The manifest does not prescribe the choice; it prescribes the _requirement_ (§2). If no path satisfies §2 within the owner's budget/effort tolerance, the lane is **data-blocked** and is recorded as such (see lane 3's "blocked" precedent) rather than run on insufficient data.

---

## 6. Roll-yield computation and roll convention (pre-committed)

**Roll-yield signal (computed from raw individual contract settlement prices, not the adjusted series):**

For each market, at each monthly rebalance, with the active contract `F1` and the next `F2` (selected by open interest):

```text
annualized_curve_slope =
    (log(F1_close) - log(F2_close))
    * 365
    / calendar_days_between_expiries
```

`calendar_days_between_expiries` MUST be positive and derived from contract metadata (expiry dates of F1 and F2). The fixed `12 × log(F1/F2)` monthly approximation is **forbidden** — maturity spacing varies by market and roll cycle.

**Non-positive settlement rule (binding):** `log()` is undefined for `≤ 0`. If either `F1_close ≤ 0` or `F2_close ≤ 0` on a signal date (e.g., WTI April 2020), that market-day is **excluded from ranking** for that rebalance. The RESULTS doc MUST report the count of excluded market-days. If exclusions exceed 1% of market-days in OOS for any accepted market, flag as a data-quality warning (not auto-DISCARD, but auditable). Alternative slope for diagnostics only (not for signal): `(F1 - F2) / max(F2, ε)` when both are positive; never use log on non-positive prices.

Sign convention: **positive** annualized curve slope when the front trades at a **premium** to the next (backwardation for a long, i.e., you are paid to roll long); **negative** when contangoed. Rank markets cross-sectionally by this value.

**TSMOM return index (binding):** built per harness spec §2.1 by chaining active-contract simple returns on the max-OI roll calendar. Do **not** use ratio-adjusted price levels for TSMOM. Near-zero denominators use `ε = max(tick_size, 1e-8)`; skip days with `abs(prior_settlement) < ε` and report skip count.

**Settlement-based mark-to-market accounting (binding):** per-position state machine (`active_contract`, `prior_settlement`, `opening_settlement` on roll) per harness spec §5.1. Slippage and commission are dollar charges in `explicit_costs`, not embedded in settlement fills.

Economic decomposition:

```text
total_pre_cost[t] = MTM on active contract only (never cross-contract delta)
roll_component[t] = N * M * (S_F1_{t-1} - S_F2_{t-1}) / max(calendar_days_between_expiries_{t-1}, 1)
spot_component[t] = total_pre_cost[t] - roll_component[t]
total_net_pnl     = Σ total_pre_cost[t] - explicit_costs
```

`calendar_days_between_expiries` = F1 expiry → F2 expiry (same denominator as signal `annualized_curve_slope`).

Reconciliation: `|total_net_pnl - (spot_component + roll_component - explicit_costs)| < $0.01`.

**Attribution accounting:** roll yield is **basis convergence during the hold**, not contract-switch gap P&L. The pass gate requires economic `roll_component` to dominate (>50% of gross OOS pre-friction P&L). This is the direct response to review Gap B (TSMOM adjacency).

---

## 7. Data-quality checklist (hard gate before any backtest)

For each market in the universe, all must be true:

- [ ] Individual contracts reconstruct cleanly for ≥15 complete years (no missing expiries, no gaps >5 sessions).
- [ ] Active-contract series derivable by OI crossover matches a reference (paid source's continuous, or CME-published roll dates) to within ±1 session.
- [ ] Front-vs-deferred spread computable on ≥95% of trading days; outliers (>5σ) manually verified as real (not data errors).
- [ ] TSMOM return index builds without ratio-adjusted prices; negative-settlement days handled per §6 (Apr 2020 fixture in unit tests).
- [ ] Currency/units consistent (USD, contract-multiplier-aware for P&L, not for signal).
- [ ] Survivorship: futures markets that delisted within the window are handled (include to delist date or exclude consistently).

**Hard stop (review Gap C):** if fewer than 10 markets pass §7, **STOP.** Record as data-blocked. Do not proceed to a backtest on a thin or noisy universe — that violates rule 3 and would produce an untrustworthy KEEP-or-DISCARD either way.

---

## 8. Cost model placeholders (full doc next: `ROLL_COST_MODEL_2026-06.md`)

Captured here as placeholders the data layer must enable; quantified in the next Phase-1 deliverable.

- Commission per contract per side (round-turn): broker-specific, placeholder conservative default.
- Slippage: 2 ticks per side (conservative for liquid contracts; revisit per-market).
- Roll slippage: 1 tick per roll (front-month exit liquidity assumption).
- Margin/financing: research P&L reported **net of explicit transaction costs only** (commission + slippage + roll); margin opportunity cost documented separately, not netted, per the plan's "conservative baseline labeled" rule.

---

## 9. Forward pointers to the harness spec (addresses Gaps B, D, E)

The harness spec (`ROLL_HARNESS_SPEC_2026-06.md`, next Phase-1 deliverable) **must** encode:

- **Gap D — Isolation from FX STOP guard.** New entrypoint path `research/new_edge/term_structure/run.py` (sibling to the FX-specific `research/run_experiment.py`). The FX negative-result STOP guard in `research/autosearch.py` / `research/run_experiment.py` is scoped to the FX engine and must not gate futures entrypoints. Document the scoping explicitly.
- **Gap B — TSMOM control run.** The harness runs, in addition to the roll-yield portfolio, a **true time-series momentum (TSMOM) control** on the identical universe: signal per market = sign of trailing **252-trading-day** continuous-series return; monthly rebalance on the same dates; same trailing-volatility risk weighting; identical costs. Cross-sectional spot momentum MAY be reported as a secondary diagnostic but MUST NOT replace the TSMOM control. **Pass-gate condition:** roll-yield OOS net PF must exceed TSMOM OOS net PF by ≥0.10, and roll P&L must contribute >50% of gross OOS P&L. If TSMOM matches/beats, the verdict is **"repackaged TSMOM → DISCARD,"** not KEEP.
- **Gap A — Trade-count + statistical bar.** Deterministic 3-month block bootstrap over OOS monthly portfolio returns: 2,000 resamples, seed `20260624`, pass when 5th-percentile bootstrapped net PF > 1.0.
- **Gap E — Ledger + closure hygiene.** Seed a `research/new_edge/research_ledger.jsonl` row at Phase-1 start (lane 7, status=in_progress). On DISCARD, append lane 7 to `docs/research/CLOSED_RESEARCH_LANES.md` and one line to `docs/PROJECT_STATUS_2026-06.md`. Pre-commit the closure entry now.
- **IS/OOS discipline:** one chronological 65/35 holdout; OOS contains ≥5 complete calendar years; OOS is sacred and never tuned against.

---

## 10. Optional Phase-0.5 (review suggestion: #6 COT as a de-risking spike)

Before committing to paid futures data (§5 owner decision), run a **1–2 day COT-loader spike inside this worktree** using the free weekly CFTC Commitment-of-Traders data (premise #6). Purpose: de-risk harness-building (term-structure backtester skeleton, IS/OOS judge, ledger plumbing) on free data before spending on the roll-yield dataset. **COT is not the program thesis** — it is a cheap warm-up that either (a) produces an incidental honest DISCARD of premise #6 for free, or (b) merely validates the harness. Either outcome is acceptable. Optional; skip if the owner prefers to go straight to the roll-yield data decision.

---

## 11. What this manifest does NOT authorize

- No strategy logic, signal thresholds, or backtest runs (rule: data + cost model first).
- No live trading, broker, Telegram, or scanner integration.
- No connection to the live `src/cli.py` scanner or Branch B.
- No "rescue" overlays (TA filters, RSI, breakout) on a failing result — those are closed-lane violations and are forbidden by `research/program.md`.

---

## Phase-1 deliverable sequence (this is #1 of 4)

1. ✅ **This doc** — Data manifest (gate-definition).
2. ✅ `ROLL_COST_MODEL_2026-06.md` — quantify §8 placeholders; conservative defaults; pass-under-optimistic-costs → DISCARD.
3. ✅ `ROLL_HARNESS_SPEC_2026-06.md` — entrypoint, FX-guard scoping (Gap D), TSMOM control (Gap B), settlement accounting (§5.1), bootstrap + PF definitions (§7), ledger integration (Gap E).
4. ⬜ One falsifiable test → `ROLL_YIELD_RESULTS_YYYY-MM-DD.md` with pre-committed KEEP/DISCARD.

**Governance:** [`PROGRAM_DECISION_MEMO_ADDENDUM_2026-06-24.md`](../PROGRAM_DECISION_MEMO_ADDENDUM_2026-06-24.md) authorizes isolated listed-futures research; production Branch B remains forex-only.

**Immediate owner decision to unblock #4:** the §5 data-source choice (paid individual-contract feed vs CME-stitch free path vs data-blocked). Tier-A loader/verifier code is authorized before #4; see harness spec authorization tiers.
