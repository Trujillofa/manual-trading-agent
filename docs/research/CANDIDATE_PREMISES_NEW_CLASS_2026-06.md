# Candidate Premises — New Instrument-Class Research Program (2026-06)

**Status:** Owner-approved program premise (PR #26). Gate-definition phase complete for Lane 7; no strategy code authorized until data proof.
**Scope (owner-fixed):** genuinely new instrument class (single-stock equities, equity-index options, listed futures). FX/CFD is NOT the research target. Horizon open. All data sources on the table.
**Governance:** [`PROGRAM_DECISION_MEMO_ADDENDUM_2026-06-24.md`](PROGRAM_DECISION_MEMO_ADDENDUM_2026-06-24.md) reconciles production (forex Branch B) vs isolated new-instrument research.
**Re-entry authority:** `research/program.md` STOP banner + `docs/research/PROFITABILITY_PLAN_2026-06.md` non-negotiables.

---

## Executive summary

The 6 closed lanes (FX/CFD, mostly OHLC, mostly intraday-to-daily) all failed. This doc surveys 7 premises across a new instrument class against the re-entry criteria — **(1) different instrument, (2) different edge source, (3) non-OHLC data** — and pre-writes pass/stop gates for each.

**Ranked shortlist (re-entry clearance × evidence × data accessibility ÷ infra cost ÷ crowding):**

| Rank | Premise | Clears re-entry via | Horizon | Data cost | Infra lift |
|---|---|---|---|---|---|
| 1 | Futures term-structure / roll yield | different edge source (curve, not spot) | daily/weekly | low | medium (new harness) |
| 2 | Dealer gamma (GEX) positioning (SPX/NDX) | non-OHLC data | daily/weekly | medium-high | high (option chain + gamma calc) |
| 3 | Equity index variance risk premium (short vol) | different instrument + different edge | daily/weekly | medium | high (options/margin + tail ctrl) |
| 4 | Post-earnings announcement drift (PEAD) | different instrument + behavioral edge | swing (5–60d) | low-medium | medium |
| 5 | Cross-sectional equity factor premia | different instrument | weekly/monthly | low (daily px) | medium (universe mgmt) |
| 6 | COT positioning reversal (futures) | non-OHLC data | weekly | free | low |

**Recommended first premise: #1, Futures term-structure / roll yield.** Cleanest material-difference clearance (curve structure is genuinely not spot-directional), cheapest falsifiable data, slowest horizon (avoids the intraday-friction death that killed 5 of 6 FX lanes), and aligns with the plan's "slower-term with a separate pre-written bar" provision.

**Honest prior: LOW.** Every candidate edge below is documented in the academic/practitioner literature and is, to varying degrees, targeted by institutional capital. The most likely outcome of running #1 is a 7th disciplined DISCARD. That is still a valuable outcome — it closes the lane honestly and preserves the fail-fast discipline that is this repo's durable asset. **Do not start this expecting profit; start it to run one clean falsification with pre-committed gates.** See §Honest assessment.

---

## Closed-lane territory map

The territory to avoid, mapped on the axes the re-entry criteria care about:

| # | Closed lane | Instrument | Edge family | Data | Horizon | Gross/net result |
|---|---|---|---|---|---|---|
| 1 | FX Majors Directional TA | FX majors/minors | breakout / pullback / RSI | OHLC | M15/H1 | gross PF ~1.0–1.07 |
| 2 | Daily Multi-Asset TSMOM | metals/indices/FX | time-series momentum | OHLC | daily | gross PF 1.036 |
| 3 | Carry/Funding (FX) | FX majors/minors | financing carry | broker swap | daily | 0.0 swap (data died) |
| 4 | FX Stat-Arb | FX majors/minors | pairs z-score reversion | OHLC | daily | OOS net PF 1.128 |
| 5 | Event surprise drift | FX around macro events | post-event drift | OHLC + calendar | intraday/daily | OOS net PF 0.375 |
| 6 | Vol-regime compression breakout | H1 FX majors | low-vol→breakout | OHLC | intraday | OOS net PF 0.782 |
| (7) | EMA trend-alignment modifier | FX | trend filter on mean-rev | OHLC | intraday | negative (conceptual mismatch) |

**Re-entry implications for candidate screening:**
- A new FX/OHLC premise is **presumptively invalid** (lanes 1, 2, 4, 6, 7 all used FX + OHLC). The owner has correctly excluded FX/CFD from scope.
- "Carry" is closed *on FX* because of a data-specific failure (broker swap resolved to 0.0). A carry/roll premise on a **different instrument** (futures term-structure) is a materially different attempt, not a reopen. See premise #1's material-difference check.
- "Vol-regime" is closed as a **spot-FX breakout**. Selling index vol (premise #3) is a different instrument and a different edge source (premium capture, not breakout) — but the family overlap means #3 must justify its distinction explicitly.

---

## Candidate premises

Each premise uses the fixed rubric: thesis → material-difference check → universe → horizon → data → evidence → costs → infra → **pre-written gates** → re-entry score.

---

### Premise #1 — Futures term-structure / roll yield

**Thesis.** Futures returns decompose into spot change + roll yield + collateral. The roll-yield component (long backwardated markets that pay you to roll, short contangoed markets that charge you to roll) is a documented structural return source, distinct from spot direction. Rank a diversified **commodity** universe by annualized curve slope (front vs deferred, maturity-spacing-adjusted), long the backwardated tail, short the contangoed tail, rebalance monthly.

**Material-difference check.**
- *Different edge source* — **CLEARS.** The signal is curve shape (front vs deferred), not spot direction. This is orthogonal to every closed FX lane (all spot-directional) and to TSMOM (spot momentum).
- *Different instrument* — CLEARS (commodity/financial futures, not FX/CFD).
- *Non-OHLC data* — borderline (uses OHLC + the futures term structure, which is a different data object than a single OHLC series).
- *Not a restatement of lane 3 (FX carry)?* Yes — lane 3 died on broker-swap data being 0.0; this is a different return source (roll, not financing) on a different instrument (listed futures, not OTC FX). Different edge family.

**Universe (v1 commodity-only).** Twelve liquid commodity futures with one economic carry definition: energy (CL, NG, RB, HO), metals (GC, SI, HG), ags (ZC, ZS, ZW), livestock (LE, HE). Equity indices (ES, NQ) and Treasury futures (ZN, ZB) are excluded from v1. ≥10 of 12 must pass the data gate; rejected markets are not replaced after seeing results.

**Horizon.** Daily signals, monthly rebalance. Holding periods weeks to months. Natural fit for the roll-yield half-life.

**Data required + accessibility.**
- Continuous back-adjusted futures series + individual contract settlement prices (to compute the front-vs-deferred spread with actual expiry spacing). Sources: Pinnacle/FirstRate/CSI/Norgate for individual contracts (~paid); CME settlement for free stitching. **Phase-1 gate:** verify ≥15 complete years of individual-contract data for ≥10 commodity markets before any backtest. Needs Phase-1 verification on data cost for a retail account.
- Positioning (COT, free weekly) optional as a risk filter.

**Evidence base.** Gorton & Rouwenhorst (2006), "Facts and Fantasies about Commodity Futures" — roll return is a distinct, positive contributor to commodity-futures returns historically. Follow-up literature (Bianchi, Ward) documents roll-yield-momentum cross-sectionally. **Honest caveats:** the edge is well-known, implemented in commodity-index ETFs (e.g., roll-optimized variants), and has likely decayed since 2006 as a result. Net edge after roll costs + execution for a new retail-scale entrant is questionable — *needs Phase-1 verification on recent data (last ~5y).*

**Cost model considerations.** Futures: tight spreads in liquid contracts, commissions modest, but roll costs and slippage on the front month are real. The strategy's whole point is capturing roll, so the cost model must separate roll P&L from spot P&L cleanly (lane 3's lesson: don't let a "carry" win come from price drift). Risk: backwardation can coincide with adverse spot moves (e.g., falling commodities in contango-to-backwardation transitions).

**Infra feasibility.** **Medium lift.** The existing `research/evaluate.py` is Dukascopy M1 FX-specific and cannot be reused for futures term-structure. Phase 1 requires: (a) a futures term-structure data loader (new), (b) a roll-yield ranker, (c) a portfolio backtester with separate roll/spot P&L attribution, (d) an IS/OOS judge mirroring the discipline (never tune on OOS). No live trading — paper/research only until a KEEP verdict.

**Pre-written gates (commit before code).**
- *Universe:* ≥10 commodity futures from the fixed 12-market v1 set, ≥15 complete years each.
- *IS/OOS split:* one chronological 65/35 holdout; no walk-forward optimization; no parameter search. OOS MUST contain ≥5 complete calendar years.
- *Metrics:* net PF (after roll + commission + 2-tick slippage), Sharpe, MAR, max DD, turnover, # trades, exposure by sector, **settlement-based roll P&L vs spot P&L attribution** with exact reconciliation (`total_net_pnl = spot + roll − costs`).
- *TSMOM control:* true 252-trading-day time-series momentum on the same universe, monthly rebalance, same vol weighting and costs. Roll-yield OOS net PF must exceed TSMOM by ≥0.10; roll P&L must contribute >50% of gross OOS P&L.
- *Bootstrap:* deterministic 3-month block bootstrap over OOS monthly returns (2,000 resamples, seed `20260624`); 5th-percentile net PF > 1.0.
- *Pass gate:* OOS net PF ≥ 1.20 under baseline costs; positive roll contribution that survives costs; not concentrated in one sector (≤50%) or one year (≤25%).
- *Stop gate (DISCARD without rescue):* OOS net PF < 1.0; edge dominated by spot drift not roll; repackaged TSMOM; concentrated in one commodity/sector; works only pre-2015 (decayed). Any rescue attempt (adding TA filters, RSI, breakout) is a closed-lane violation and is forbidden.

**Material-difference score.** Instrument novelty 4/5 · Edge novelty 4/5 (curve ≠ spot) · Data novelty 3/5 → **CLEARS re-entry.**

---

### Premise #2 — Dealer gamma exposure (GEX) positioning (SPX/NDX)

**Thesis.** Options market makers run delta-hedged books; their net gamma exposure determines whether they buy/sell into moves (negative GEX → trend-amplifying, positive GEX → mean-reverting/pinning). The aggregate GEX is a positioning signal predicting intraday-to-daily index behavior.

**Material-difference check.**
- *Non-OHLC data* — **CLEARS.** Signal is derived from the option chain's gamma profile, not from price history.
- *Different instrument* — CLEARS (index options).
- *Not a restatement of lane 6 (vol-regime)?* Lane 6 used realized-vol compression to predict breakout. GEX uses dealer positioning to predict path behavior. Different data, different mechanism — defensible, but the "volatility-context" overlap must be kept in view.

**Universe.** SPX (and optionally SPY, NDX/QQQ) index options. Start SPX-only.

**Horizon.** Daily-to-intraday. GEX is most predictive at the daily open-to-close and around opex/pinning. Holding intraday to a few days.

**Data required + accessibility.**
- Historical option chains with Greeks (delta, gamma) by strike and expiry. Sources: ORATS, IVolatility, CBOE (paid for clean history); yfinance/CBOE delayed (free but gappy). This is the **main data gate** — clean historical gamma-by-strike is not free. Needs Phase-1 verification on data source/cost.
- Spot index for P&L.

**Evidence base.** Barbon & Buraschi (2021), "Affine risk premia on the gamma surface"; practitioner literature (SpotGamma, SqueezeMetrics) on GEX/charm flows; Bergsma & Pelsser on dealer hedging. The signal has academic and practitioner traction. **Honest caveats:** (a) GEX estimates vary by methodology (which strikes, dealer position assumption), (b) the signal became widely followed post-2020 and may be crowded/decayed, (c) predictive power is statistical, not deterministic. *Current decay magnitude needs Phase-1 verification.*

**Cost model considerations.** Index-future or ETF execution to express the view (ES/SPY), not the options themselves — keeps costs to futures-style. Spread/commission low; main cost is slippage around opens/opex. Risk: signal is weak per-trade, needs many trades to validate (good for the 30-trade gate).

**Infra feasibility.** **High lift.** Requires historical option-chain ingestion, gamma aggregation across strikes/expiries, and a dealer-positioning model (assumptions matter). New harness, substantial data engineering. This is the most infra-heavy candidate.

**Pre-written gates.**
- *Universe:* SPX options, ≥5y history (post-2020 ideal given the signal's regime dependence — explicitly note shorter history).
- *IS/OOS:* chronological 65/35.
- *Metrics:* net PF, hit-rate, Sharpe, max DD, # trades, performance stratified by GEX sign and magnitude, by opex week.
- *Pass gate:* OOS net PF ≥ 1.20 with ≥30 OOS trades; positive expectation in both positive- and negative-GEX regimes (not one-sided); survives 2020+ sub-sample.
- *Stop gate:* OOS net PF < 1.0; works only pre-2020 (decayed/crowded); depends on a single methodology assumption; disappears under realistic execution.

**Material-difference score.** Instrument novelty 3/5 · Edge novelty 4/5 (positioning-driven) · Data novelty 5/5 → **CLEARS re-entry.**

---

### Premise #3 — Equity index variance risk premium (short vol)

**Thesis.** Implied index volatility systematically exceeds realized volatility (the variance risk premium, VRP). Selling index variance (short straddles, variance swaps, or short-VIX-futures) captures this premium. One of the most documented premia in finance.

**Material-difference check.**
- *Different instrument* — CLEARS (index options / vol derivatives).
- *Different edge source* — CLEARS (premium capture, not directional).
- *Not a restatement of lane 6 (vol-regime)?* Lane 6 *traded breakouts after low realized vol* (spot direction). VRP *sells vol itself*. Different edge family. **But** the family overlap (both "volatility") means this premise carries reputational/overlap risk — the distinction must be airtight.

**Universe.** SPX/SPY options (straddles/strangles), or VIX futures (1M/2M front), or VXX/UVXY short.

**Horizon.** Daily-to-weekly holding of short-vol exposure; premium decays over days to weeks.

**Data required + accessibility.**
- Implied vs realized vol series (VIX + realized, free via CBOE/yfinance); option chains if expressing via straddles (paid for clean history).

**Evidence base.** Bollerslev, Tauchen & Zhou (2009) document a substantial VRP (~3–5%/mo at the index level historically); Carr & Madan (2001) on variance swap replication. **Honest caveats — severe:** (a) The Feb 5 2018 "Volmageddon" terminated XIV and demonstrated the strategy's path-dependent blow-up risk; short-vol funds have blown up repeatedly. (b) The premium is partly compensation for crash risk, so the *expected* return is positive but the *distribution* has fat left tails. (c) A 30-trade IS/OOS gate can easily "pass" during a quiet regime and blow up OOS — **the pass gate as written for other premises is unsafe here.**

**Cost model considerations.** Options spreads + margin (reg-T margin on short options is capital-inefficient for a retail account); slippage on unhedged shorts; the catastrophic cost is gap risk through a vol spike. VIX-futures implementation has basis/roll cost.

**Infra feasibility.** **High lift + high operational risk.** Requires options execution, margin management, and — critically — explicit tail-risk controls (position sizing for gap risk, stop-loss behavior under gaps). The strategy is simple in concept and dangerous in execution.

**Pre-written gates (modified for tail risk).**
- *Universe:* short SPX strangles or front-VIX-futures short.
- *IS/OOS:* chronological 65/35, **must include at least one vol-spike regime in each window** (2008, 2015/2018/2020).
- *Metrics:* net PF, Sharpe, **max DD with explicit gap-through accounting**, Calmar, % months with >20% loss, # trades.
- *Pass gate:* OOS net PF ≥ 1.20 AND max OOS drawdown ≤ 25% AND no month worse than −15% AND positive premium capture net of the worst spike in-sample. **The standard PF gate alone is insufficient** — a short-vol strategy can show PF > 2 in a quiet window and still be unviable.
- *Stop gate:* any OOS month worse than −20%; PF survives only by excluding vol-spike days; position sizing required to survive a 3σ vol jump is non-viable for the account. **DISCARD on any of these — do not "add a filter" to rescue (that is closed-lane 6 territory).**

**Material-difference score.** Instrument novelty 3/5 · Edge novelty 4/5 · Data novelty 2/5 → **CLEARS re-entry** (but see "defer" recommendation below).

---

### Premise #4 — Post-earnings announcement drift (PEAD)

**Thesis.** Stocks that beat (miss) earnings expectations drift up (down) for 5–60 days post-announcement. A behavioral anomaly: under-reaction to surprise.

**Material-difference check.**
- *Different instrument* — CLEARS (single stocks).
- *Different edge source* — CLEARS (behavioral drift, not momentum on price).
- *Not a restatement of lane 5 (event drift)?* Lane 5 was **FX** around **macro** events. PEAD is **equities** around **firm-specific** earnings. Different instrument, different event class — defensibly distinct.

**Universe.** US large-cap liquid earnings (top 1000 by liquidity). Avoid illiquid small-caps (cost death).

**Horizon.** Swing, 5–60 day holds.

**Data required + accessibility.**
- Earnings calendar + actual/estimate/surprise history (EOD Historical, Zacks, IEXCloud — low-cost; free tier gappy). Daily OHLC. **Data gate:** clean point-in-time surprise data (no look-ahead from restated estimates).

**Evidence base.** Bernard & Thomas (1989, 1990) foundational; Ball & Bartov (1996). Persistent in the academic record for decades. **Honest caveats:** widely known, targeted by quant funds and smart-order-flow routers; evidence of decay post-Reg-FD and post-2010s as anomaly arbitrage grew. *Current retail-net profitability needs Phase-1 verification* — the academic effect exists but may be fully captured by faster players before a retail entry gets filled.

**Cost model considerations.** Single-stock spreads + commissions; the edge per trade is small, so transaction costs and adverse selection (you fill on the slow side) are the main threat. Earnings-gap risk on the entry.

**Infra feasibility.** Medium lift. Earnings-data pipeline + point-in-time care + single-stock backtester. Less infra than options (#2/#3) but more than futures (#1).

**Pre-written gates.**
- *Universe:* ≥500 liquid US stocks, ≥10y history.
- *IS/OOS:* chronological 65/35.
- *Metrics:* net PF, alpha vs market, Sharpe, max DD, # trades, hit rate, decay curve (1d/5d/21d/60d).
- *Pass gate:* OOS net PF ≥ 1.20 with ≥30 OOS trades; drift positive 5–21d post-surprise; survives net of realistic single-stock costs; not concentrated in one sector.
- *Stop gate:* OOS net PF < 1.0; effect only pre-2012 (decayed); net turns negative after realistic slippage; requires micro-cap illiquidity to show.

**Material-difference score.** Instrument novelty 5/5 · Edge novelty 3/5 · Data novelty 3/5 → **CLEARS re-entry.**

---

### Premise #5 — Cross-sectional equity factor premia (momentum / value / quality / low-vol)

**Thesis.** Long-short equity portfolios ranked on a factor (12-month momentum, book-to-market value, profitability, low beta) earn positive expected returns cross-sectionally.

**Material-difference check.**
- *Different instrument* — CLEARS (single stocks, long-short).
- *Different edge source* — borderline; momentum is conceptually adjacent to the closed TSMOM (lane 2), but cross-sectional (rank stocks) ≠ time-series (absolute trend). Distinct enough, with care.
- *Non-OHLC data* — partial (value/quality need fundamentals, which is non-OHLC).

**Universe.** US large-cap (top 1000–3000). Long-short decile portfolios.

**Horizon.** Weekly to monthly rebalance.

**Data required + accessibility.** Daily OHLC (free) + fundamentals for value/quality (Compustat/PDCC — paid; or free via SEC EDGAR scrape). **Data gate:** point-in-time fundamentals to avoid look-ahead.

**Evidence base.** Jegadeesh & Titman (1993) momentum; Fama & French (1992, 2015) value/size/profitability; Frazzini & Pedersen (2014) betting-against-beta; Asness et al. QMJ. Enormous literature, real institutional allocation. **Honest caveats — severe crowding:** value underperformed 2010–2020 (the "value factor is dead" debate); momentum suffered the 2009 and 2020 crashes; factors are now tradeable via cheap ETFs. A new retail long-short attempt faces (a) borrow costs on shorts, (b) decayed premia, (c) factor crashes. *Most-crowded candidate on this list.*

**Cost model considerations.** Long-short means borrow/financing on the short leg + 2× commissions + rebalance turnover. Factor premia are small per unit time; net-of-cost survival is the whole question.

**Infra feasibility.** Medium lift. Single-stock backtester with a clean survivorship-bias-free universe + point-in-time fundamentals.

**Pre-written gates.**
- *Universe:* survivorship-bias-free US large-cap, ≥20y.
- *IS/OOS:* chronological 65/35.
- *Metrics:* factor long-short return net of borrow + commission, Sharpe, max DD, turnover, alpha vs Fama-French, # rebalances.
- *Pass gate:* OOS net Sharpe ≥ 0.5 and net PF ≥ 1.20 over the full OOS; positive in ≥2 of 3 OOS sub-periods; survives realistic short-borrow.
- *Stop gate:* net Sharpe < 0.3; works only pre-2010; requires ignoring short-borrow or survivorship bias.

**Material-difference score.** Instrument novelty 5/5 · Edge novelty 2/5 (well-trodden) · Data novelty 3/5 → **BORDERLINE-to-CLEARS** (cleared, but the weakest novelty — most-crowded).

---

### Premise #6 — COT positioning reversal (futures)

**Thesis.** Fade extreme non-commercial (speculator) positioning in futures markets: when specs are record-long, go short; record-short, go long. The thesis: extreme positioning is a contrary indicator at turning points.

**Material-difference check.**
- *Non-OHLC data* — **CLEARS.** Signal is weekly CFTC Commitment-of-Traders positioning.
- *Different edge source* — CLEARS (positioning/flow, not price pattern).

**Universe.** Markets with clean COT reporting: FX futures, commodity futures, equity-index futures. ~20–30 markets.

**Horizon.** Weekly (COT is weekly).

**Data required + accessibility.** CFTC COT data — **free**, weekly, back to ~1986 (some markets later). This is the cheapest data on the list.

**Evidence base.** Sanders, Irwin & Merfinin; work on large-spec vs commercial positioning as a contrary indicator. Mixed evidence in the literature — some markets show predictive power, others not. **Honest caveats:** weak/slow signal, contested academically, and the "disaggregated" COT reports changed the signal's character in 2009. Probably the lowest expected edge of the candidates, but the lowest-cost to falsify.

**Cost model considerations.** Futures execution (low cost). Weekly rebalance = low turnover.

**Infra feasibility.** **Low lift.** COT loader + simple backtester. Cheapest falsification on the list.

**Pre-written gates.**
- *Universe:* ≥15 COT-reported futures, ≥15y.
- *IS/OOS:* chronological 65/35.
- *Metrics:* net PF, Sharpe, max DD, # trades, hit rate by market, performance by positioning extremity quintile.
- *Pass gate:* OOS net PF ≥ 1.20; positive across ≥60% of markets (not one-market); monotonic in positioning extremity.
- *Stop gate:* OOS net PF < 1.0; works in one market only; no monotonicity in extremity.

**Material-difference score.** Instrument novelty 2/5 (futures, partially FX-futures overlap) · Edge novelty 4/5 · Data novelty 5/5 → **CLEARS re-entry** (cheapest path).

---

### Rejected / deferred (appendix — showing the discipline)

- **Put-call parity / conversion-reversal arbitrage** — REJECTED. Pure arbitrage, not a risk premium; bid-ask and fees consume it at retail scale. Captured by HFT/cross-market firms.
- **Merger / risk arbitrage** — DEFERRED. Different edge but event-specific, capacity-limited, and legally/operationally heavy. Not a clean first falsification.
- **Index reconstitution effects (Russell/S&P adds)** — REJECTED. Crowded by passive front-running; edge documented as near-fully arbitraged.
- **Equity dispersion trading** — DEFERRED. Genuinely different edge (short index vol / long single-stock vol), but infra-heaviest (option chains across many names). Possible Phase-2 candidate if #2 (GEX) establishes the options-infra base.
- **Microstructure / L2 / execution-quality** — the profitability plan explicitly DEFERS this until attached to an already-positive gross edge. No positive edge exists yet. Excluded by rule.

---

## Ranking & recommendation

Transparent rubric, 1–5 each (5 = best). "Crowding" is inverted (5 = least crowded).

| Premise | Re-entry clearance | Evidence | Data access | Infra cost (inv) | Crowding (inv) | **Total** |
|---|---|---|---|---|---|---|
| #1 Futures roll yield | 4 | 4 | 4 | 3 | 3 | **18** |
| #6 COT reversal | 4 | 2 | 5 | 5 | 4 | **20** |
| #4 PEAD | 4 | 3 | 3 | 3 | 2 | **15** |
| #2 GEX | 4 | 3 | 2 | 2 | 3 | **14** |
| #5 Equity factors | 3 | 4 | 4 | 3 | 1 | **15** |
| #3 VRP short-vol | 4 | 5 | 3 | 2 | 3 | **17** |

(Note: #6 scores highest on the raw rubric — but its *evidence* score is weak, and a cheap-to-falsify weak signal is still a weak signal. The recommendation weights evidence and infra-to-meaningful-result, not just raw total.)

**Recommendation: Premise #1, Futures term-structure / roll yield, as the first falsifiable test.**

Reasoning:
1. **Cleanest re-entry clearance.** Curve-structure edge is genuinely orthogonal to all 6 closed spot-directional FX lanes. Defensible against "you reopened a closed lane" criticism.
2. **Slowest horizon.** Monthly rebalance avoids the intraday-friction death that killed lanes 1, 5, 6. Aligns with the plan's "slower-term with a separate pre-written bar" provision.
3. **Clean attribution.** Roll P&L can be separated from spot P&L — directly addressing lane 3's failure mode (a "carry" win that was really price drift). The gate explicitly requires the edge to come from roll, not spot.
4. **Manageable infra.** New harness needed, but simpler than the options candidates (#2/#3/#4-dispersion).
5. **Honest downside is bounded.** A clean DISCARD closes a 7th lane with cheap data and clear attribution.

**Second option if #1 is unappealing:** #6 (COT) is the cheapest falsification (free data, low infra) if you want to spend the least to run one honest test; #2 (GEX) is the higher-ceiling / higher-cost bet if you want to invest in options infrastructure that unlocks dispersion trading later.

**Defer #3 (VRP).** Strongest theoretical premium, but the tail-risk distribution makes the standard pass gate unsafe, and the operational complexity (options/margin/gap risk) is the worst first-step for a project rebuilding its harness.

---

## Honest assessment

The realistic prior on #1 clearing its OOS net PF ≥ 1.20 gate is **low** — perhaps 15–25%. Every candidate edge here is documented in the literature and targeted by institutional capital with faster data, lower costs, and more capital than a retail-scale attempt. Roll-yield in particular has been commoditized in commodity-index products since the late 2000s. The honest expectation is a 7th DISCARD.

That is not a reason to skip the exercise — it is the reason the gates exist. The repo's durable asset, per `research/program.md` itself, is "the harness, unified evaluator, audit discipline, and the honesty of the negative result — not profitable alerts." Running #1 cleanly either (a) finds an edge (upside surprise), or (b) closes the term-structure lane with disciplined attribution (downside that still adds value and forecloses future temptation to retry it).

**Do not commit to this program expecting profit. Commit to it to run one pre-registered, honestly-costed falsification of a genuinely-different premise.** If the result is DISCARD, the correct response is documented closure — not "let me try a variant," which would violate the fail-fast rule that makes the prior closures trustworthy.

---

## Next steps (if #1 is accepted — gate-definition phase, no strategy logic yet)

Per the profitability plan's rule: **do not write strategy logic until the data and cost model are documented.**

**Phase 1 deliverables (in this worktree, `research/new-premises-2026-06`):**

1. **Data manifest** — `docs/research/term_structure/ROLL_YIELD_DATA_MANIFEST_2026-06.md`: exact commodity markets, source(s), ≥15y history, settlement-based accounting, maturity-spacing annualization, roll-convention, continuous-contract construction method. Gate: verified ≥15y individual-contract data for ≥10 markets before proceeding.
2. **Cost model doc** — `docs/research/term_structure/ROLL_COST_MODEL_2026-06.md`: commission per contract, assumed slippage (ticks), roll cost treatment, how roll P&L is separated from spot P&L. Conservative defaults; if the strategy passes only under optimistic costs → DISCARD.
3. **Harness spec** — a short design for the futures term-structure backtester + IS/OOS judge, mirroring the discipline of `research/evaluate.py` (never tune on OOS; the 65/35 split is sacred).
4. **One falsifiable test** — only after 1–3: implement the rank-by-roll-yield long-short portfolio, run the pre-written pass/stop gates, write `docs/research/term_structure/ROLL_YIELD_RESULTS_YYYY-MM-DD.md` with KEEP/DISCARD.

**Do NOT** in Phase 1: connect to live trading, write Telegram/scanner integration, or add TA filters to "improve" a failing result.

---

## References (canonical; current-decay specifics flagged for Phase-1 verification)

- Gorton, G. & Rouwenhorst, K. G. (2006). *Facts and Fantasies about Commodity Futures.* FAJ.
- Bianchi et al. (follow-ups on cross-sectional roll-yield / time-series momentum in commodities).
- Bollerslev, T., Tauchen, G. & Zhou, H. (2009). *Expected Stock Returns and Variance Risk Premia.* RFS.
- Carr, P. & Madan, D. (2001). *Towards a Theory of Volatility Trading.*
- Barbon, A. & Buraschi, A. (2021). *Affine Risk Premia on the Gamma Surface.*
- Bernard, V. & Thomas, J. (1989, 1990). *Post-earnings-announcement drift.* JAR / AOS.
- Jegadeesh, N. & Titman, S. (1993). *Returns to Buying Winners and Selling Losers.* Journal of Finance.
- Fama, E. & French, K. (1992, 2015). *Cross-Section of Expected Stock Returns* / *A Five-Factor Model.*
- Frazzini, A. & Pedersen, L. (2014). *Betting Against Beta.* JFE.
- Sanders, D., Irwin, S. & Merfinin (COT positioning literature).

*Empirical decay magnitudes, current factor/roll/VRP sizes post-2020, and retail-net-of-cost viability are explicitly marked "needs Phase-1 verification" throughout and must be measured, not assumed, before any KEEP verdict.*
