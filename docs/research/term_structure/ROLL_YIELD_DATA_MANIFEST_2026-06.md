# Roll-Yield Data Manifest — 2026-06

**Status:** Phase-1 deliverable for Premise #1 (futures term-structure / roll yield). Gate-definition only.
**Authority:** `docs/research/CANDIDATE_PREMISES_NEW_CLASS_2026-06.md` (accepted premise #1) + `research/program.md` re-entry protocol + `docs/research/PROFITABILITY_PLAN_2026-06.md` rule "no strategy logic until data and cost model are documented."
**Scope of this doc:** define exactly what data must exist, where it comes from, and the hard stop — *before* any strategy code.

---

## 1. Research question

Can a diversified long-short futures portfolio, ranked monthly by expected roll yield, earn a positive net return whose edge comes from **roll** (curve structure) rather than **spot** (price direction) — surviving realistic commission + slippage + roll costs on an out-of-sample window?

This phrasing is deliberate: the attribution (roll vs spot) is the whole point, and is the direct response to the failure mode of closed lane 3 (FX carry whose "edge" was really price drift, and whose funding data resolved to zero).

---

## 2. The core data-engineering requirement (this is the real gate)

**Roll yield cannot be computed from a back-adjusted continuous series.** Back-adjustment (Panama/ratio/calendar) removes exactly the price gaps at roll that *are* the roll yield. A continuous series alone is sufficient for spot-P&L representation and for the TSMOM control run, but **not** for measuring the signal itself.

Therefore the data manifest requires, for each market:

| Data object | Used for | Source shape |
|---|---|---|
| **Individual contract OHLC + open interest** by expiry (e.g., `CLZ2025`, `CLF2026`) | Computing front-vs-deferred spread → **roll yield (the signal)** + selecting the active contract by open interest | Per-contract daily bars |
| **Continuous series** (ratio/multiplicative back-adjusted, max-OI roll) | Spot-P&L representation + **TSMOM control run** | Single series per market |
| **Roll calendar** (active-contract switch dates, with OI/volume confirmation) | Timing rebalance, roll-cost accounting, attribution | Derived from individual-contract OI |

This three-object requirement is the **hard gate.** A data source that provides only a continuous series is *insufficient* for this lane, regardless of how clean or cheap it is.

---

## 3. Proposed universe (12 markets, confirm before proceed)

Start narrow, liquid, and cross-sector to support the "not concentrated" pass gate. Each has a deep, clean term structure. Proposed starting set:

| Sector | Market | Symbol | Notes |
|---|---|---|---|
| Energy | WTI crude | CL | most liquid commodity future |
| Energy | Natural gas | NG | seasonal, strong curve structure |
| Metals | Gold | GC | |
| Metals | Silver | SI | |
| Metals | Copper | HG | growth/cycle proxy |
| Agriculture | Corn | ZC | |
| Agriculture | Soybeans | ZS | |
| Agriculture | Wheat (CBOT) | ZW | |
| Financials | US 10-year note | ZN | interest-rate term structure |
| Financials | US 30-year bond | ZB | |
| Equity index | S&P 500 | ES | financial tail |
| Equity index | Nasdaq 100 | NQ | financial tail |

**Gate:** ≥10 of these 12 must clear the data-quality checklist (§7) for ≥10 years each before any backtest. If fewer than 10 clear, **reduce the universe and widen the history requirement**, or STOP — do not proceed with a thin universe that violates profitability-plan rule 3 ("do not judge from a single symbol or period").

**Excluded from v1:** livestock (LE, FE), softs (KC, SB — thinner roll structure), ICE-listed (Brent — dual-venue data friction). Candidates for a v2 expansion only if v1 clears gates.

---

## 4. Required history and splits

- **Minimum history:** 10 years per market (preference ≥15y where the source provides it).
- **Period:** through the most recent complete month.
- **IS/OOS split:** chronological 65/35 (sacred — never tune on OOS; see harness spec). With 10y: ~6.5y IS / ~3.5y OOS → ~42 OOS rebalance events at monthly cadence.
- **Trade-count semantics (addresses review Gap A):** For slow strategies the standard "≥30 trades" rule is ill-defined. Pre-registered definition for this lane: **one "trade" = one market's directional position change at a rebalance** (entry, exit, or flip). Statistical bar (in lieu of ≥30): OOS net PF ≥ 1.20 **AND** a bootstrap resample of the OOS rebalance-event returns whose 5th-percentile net PF > 1.0 (i.e., the edge is not an artifact of rebalance ordering). This directly uses the plan's "separate pre-written statistical bar for slower strategies" provision. Finalized in the harness spec.

---

## 5. Candidate data sources (owner decision: free-tier vs paid)

| Source | Individual contracts? | Continuous? | Coverage / history | Cost | Suitability |
|---|---|---|---|---|---|
| **FirstRate Data** | yes | yes (gap-adjusted) | ~130 active futures, daily + intraday, back to 2007 | paid (verify tier) | **Strong candidate** — meets §2 three-object requirement |
| **Norgate Data** (futures package) | yes | yes (Norgate-continuous) | broad futures, decades | paid subscription | **Strong candidate** — popular for futures research; pricing + coverage **needs verification** |
| **CSI Data** (Unfair Advantage) | yes | yes | deep history, many markets | paid | Strong; institutional-leaning cost |
| **Pinnacle Data Corp** | yes | yes | commodity-futures focus, deep | paid | Strong for commodities |
| **CME settlement files** | yes | no (build your own) | authoritative, free | free + build effort | Authoritative primary; requires stitching |
| **Quandl / Nasdaq Data Link (Sharadar)** | partial | yes | varies | paid | Verify per-market availability |
| **yfinance** | **no** (continuous only) | yes | gaps, symbol-map fragility | free | **Insufficient for this lane** (fails §2) — usable only as a sanity cross-check on the continuous series |

**Required owner decision before Phase-1 build:** authorize a paid source that provides individual contracts (FirstRate / Norgate / CSI / Pinnacle), **or** accept the free path of stitching CME settlement files (higher build effort, narrower coverage). The manifest does not prescribe the choice; it prescribes the *requirement* (§2). If no path satisfies §2 within the owner's budget/effort tolerance, the lane is **data-blocked** and is recorded as such (see lane 3's "blocked" precedent) rather than run on insufficient data.

---

## 6. Roll-yield computation and roll convention (pre-committed)

**Roll-yield signal (computed from raw individual contract prices, not the adjusted series):**

For each market, at each monthly rebalance, with the active contract `F1` and the next `F2` (selected by open interest):

```
roll_yield_annualized ≈ 12 × (log(F1_close) − log(F2_close))   # for monthly framing
```

Sign convention: **positive** roll yield when the front trades at a **premium** to the next (backwardation for a long, i.e., you are paid to roll long); **negative** when contangoed. Rank markets cross-sectionally by this value.

**Continuous-series construction (for spot P&L + TSMOM control only):** multiplicative (ratio) back-adjustment, active contract = max open interest, roll on the OI-crossover day. Ratio (not Panama/additive) is chosen because additive adjustment can produce negative prices on long histories, distorting log-returns.

**Attribution accounting:** every realized position P&L is decomposed into (a) spot component — change in continuous-series price over the hold — and (b) roll component — the roll yield captured over the hold, computed from the front-vs-deferred spread at each roll. The pass gate requires (b) to dominate, not (a). This is the direct, pre-committed response to review Gap B (TSMOM adjacency).

---

## 7. Data-quality checklist (hard gate before any backtest)

For each market in the universe, all must be true:

- [ ] Individual contracts reconstruct cleanly for ≥10 years (no missing expiries, no gaps >5 sessions).
- [ ] Active-contract series derivable by OI crossover matches a reference (paid source's continuous, or CME-published roll dates) to within ±1 session.
- [ ] Front-vs-deferred spread computable on ≥95% of trading days; outliers (>5σ) manually verified as real (not data errors).
- [ ] Continuous series (ratio-adjusted) has no negative prices and no unexplained >10% overnight gaps.
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
- **Gap B — TSMOM control run.** The harness runs, in addition to the roll-yield portfolio, a **12-month spot-momentum long-short portfolio on the identical universe** using the continuous series. **Pass-gate condition:** roll-yield OOS net PF must exceed spot-momentum OOS net PF, and roll-P&L must exceed spot-P&L in attribution. If spot momentum matches/beats, the verdict is **"repackaged TSMOM → DISCARD,"** not KEEP. This is pre-committed here so it cannot be rationalized away after seeing results.
- **Gap A — Trade-count + statistical bar.** Finalize the bootstrap-resample net-PF confidence procedure (§4) with the chosen confidence level and resample count.
- **Gap E — Ledger + closure hygiene.** Seed a `research/new_edge/research_ledger.jsonl` row at Phase-1 start (lane 7, status=in_progress). On DISCARD, append lane 7 to `docs/research/CLOSED_RESEARCH_LANES.md` and one line to `docs/PROJECT_STATUS_2026-06.md`. Pre-commit the closure entry now.
- **IS/OOS discipline:** 65/35 chronological, walk-forward; OOS is sacred and never tuned against (mirrors `research/evaluate.py`'s role for FX).

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
2. ⬜ `ROLL_COST_MODEL_2026-06.md` — quantify §8 placeholders; conservative defaults; pass-under-optimistic-costs → DISCARD.
3. ⬜ `ROLL_HARNESS_SPEC_2026-06.md` — entrypoint, FX-guard scoping (Gap D), TSMOM control (Gap B), trade-count + bootstrap bar (Gap A), IS/OOS judge, ledger integration (Gap E).
4. ⬜ One falsifiable test → `ROLL_YIELD_RESULTS_YYYY-MM-DD.md` with pre-committed KEEP/DISCARD.

**Immediate owner decision to unblock #2–4:** the §5 data-source choice (paid individual-contract feed vs CME-stitch free path vs data-blocked).
