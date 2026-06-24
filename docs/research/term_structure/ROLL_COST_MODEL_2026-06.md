# Roll-Yield Cost Model — 2026-06

**Status:** Phase-1 deliverable #2 for Premise #1 (futures term-structure / roll yield). Gate-definition only.
**Authority:** `docs/research/term_structure/ROLL_YIELD_DATA_MANIFEST_2026-06.md` §8 placeholders + `docs/research/PROFITABILITY_PLAN_2026-06.md` rule "if the strategy passes only under optimistic costs, the verdict is DISCARD."
**Purpose:** quantify every cost the backtester must apply, with an optimistic/baseline/pessimistic ladder and a hard rule: **pass only under optimistic costs → auto-DISCARD.** This directly addresses the failure mode that killed vol-regime (gross PF 1.114 → net PF 0.802 after 6-pip RT costs).

---

## 1. The core principle (from the profitability plan)

> Default assumptions are allowed only as a clearly labeled conservative baseline. If the strategy passes only under optimistic costs, the verdict is DISCARD.

Operationalized as a **three-tier ladder.** The harness must report verdicts under all three. The **baseline** tier is the canonical KEEP/DISCARD decision; optimistic and pessimistic bracket sensitivity.

| Tier | Meaning | Verdict implication |
|---|---|---|
| **Optimistic** | Best plausible execution (low commission, 1 tick slippage, no roll slippage) | Pass here alone is *not* sufficient to KEEP |
| **Baseline** | Conservative expected execution (default commission, 2 ticks/side, 1 tick/roll) | **Canonical decision tier** |
| **Pessimistic** | Adverse execution (high commission, 3 ticks/side, 2 ticks/roll, conservative roll liquidity) | Pass here is strong evidence; fail here is not auto-DISCARD |

**Hard rule (pre-committed):** KEEP requires baseline-tier pass. Optimistic-only pass → **DISCARD** with reason "passes only under optimistic costs." This cannot be relaxed after seeing results.

---

## 2. Cost components

Every realized position P&L in the backtester is decomposed and charged as:

### 2.1 Commission
- **Per contract per side**, round-turn quoted as `(commission_per_side × 2)`.
- Futures commission is per-contract, not per-share; position sizing is contract-count × multiplier.
- **Baseline default:** $2.50 per side ($5.00 round-turn) — conservative for retail discount futures brokers (IBKR overnight futures ~$0.85–$2.25/side; this leaves headroom).
- **Owner-configurable** per market if broker terms differ; defaults are conservative.

### 2.2 Slippage (execution friction)
- **Baseline:** 2 ticks per side (entry + exit). Tick values are contract-specific (CL = $12.50/tick; ZN = $15.625/tick; ES = $12.50/tick; etc. — derive from multiplier and tick size).
- **Pessimistic:** 3 ticks/side. **Optimistic:** 1 tick/side.
- Applied symmetrically to entries and exits. No assumption of favorable fills (no limit-fill benefit) — conservative for a passive signal that may cross the spread.

### 2.3 Roll slippage (the term-structure-specific cost)
- This is the cost the *lane itself* imposes and most retail backtests miss. Exiting the front month into the next at roll incurs the same slippage profile as a turn, plus thinner deferred-month liquidity.
- **Baseline:** 1 tick per roll (the long/short position rolls from F1 to F2 at each rebalance or OI crossover, whichever the harness uses).
- **Pessimistic:** 2 ticks/roll. **Optimistic:** 0 ticks/roll (unrealistic — included only to show sensitivity).

### 2.4 Roll-yield capture (the signal — not a cost, but accounted symmetrically)
- The roll-yield component is **credited** to P&L at each roll based on the front-vs-deferred spread realized over the hold (computed from raw individual-contract prices per the data manifest §6, not the adjusted series).
- This is the *edge*. Costs 2.1–2.3 are the friction. The pass gate requires roll-yield credit (net of 2.1–2.3) to exceed the spot P&L component — see attribution §4.

### 2.5 Margin opportunity cost — documented, NOT netted
- Per the plan's conservative-baseline rule: margin/collateral opportunity cost is **documented separately, not subtracted from research P&L.**
- Reason: it is a capital-allocation question, not an execution cost; netting it would conflate strategy quality with account-leverage choice.
- Reported as an exposure × collateral-rate × risk-free-rate figure in the RESULTS doc, alongside (not inside) net PF.

---

## 3. The three-tier ladder (per side, in ticks + commission)

Synthesized from §2 for the harness. Commission in $; slippage in market-specific ticks converted to $ via multiplier/tick-size.

| Component | Optimistic | Baseline | Pessimistic |
|---|---|---|---|
| Commission (per side) | $1.00 | $2.50 | $4.00 |
| Execution slippage (per side) | 1 tick | 2 ticks | 3 ticks |
| Roll slippage (per roll) | 0 ticks | 1 tick | 2 ticks |

**Round-trip baseline example (CL, tick = $12.50):** $5.00 commission + 2 × (2 × $12.50) slippage + 1 × $12.50 roll = **$67.50/contract** before any P&L. The strategy's roll-yield credit must clear this per-contract hurdle.

**The harness must report all three tiers** in every RESULTS doc. A "baseline DISCARD, optimistic KEEP" result is **DISCARD** (auto-rule §1).

---

## 4. Settlement-based attribution and reconciliation (lane-3 lesson, pre-committed)

This is the second binding gate (alongside the TSMOM control in the harness spec). P&L is computed from **settlement-based mark-to-market** on individual contracts (data manifest §6), not from a parallel continuous-series shortcut.

Every position's realized dollar P&L decomposes as:

```text
total_net_pnl = spot_component + roll_component - explicit_costs

  spot_component  = Σ daily settlement-to-settlement P&L on the held contract
                    (excluding roll-switch days)
  roll_component  = Σ P&L from closing old contract and opening new at each roll
  explicit_costs  = commission(§2.1) + execution_slippage(§2.2) + roll_slippage(§2.3)
```

At each roll the backtester MUST: (1) close the old contract at settlement or modeled fill, (2) open the replacement at settlement or modeled fill, (3) charge execution and roll slippage separately, and (4) emit an audit row with both contract identifiers and prices.

**Reconciliation requirement (binding):** for every position and the portfolio aggregate,

```text
|total_net_pnl - (spot_component + roll_component - explicit_costs)| < 1e-8   # normalized
|total_net_pnl - (spot_component + roll_component - explicit_costs)| < $0.01  # dollar P&L
```

**Pass-gate attribution condition (pre-committed, cannot be relaxed):**
- `roll_component` must contribute **> 50%** of pre-friction gross OOS P&L (`roll_oos / (spot_oos + roll_oos) > 0.50`), AND
- `roll_component` must be **positive** in OOS (pre-friction; friction is in `explicit_costs`).

If `spot_component` dominates → the strategy is spot-directional in disguise → **DISCARD** with reason "edge dominated by spot drift, not roll yield" (this is the lane-3 failure mode, pre-empted).

The TSMOM control run (harness spec, Gap B) provides the orthogonal check: if true 252-day time-series momentum on the same universe matches or beats roll-yield OOS net PF by less than 0.10, the verdict is **"repackaged TSMOM → DISCARD."** Cross-sectional spot momentum is a secondary diagnostic only and MUST NOT substitute for this control.

---

## 5. Universe-specific cost notes (v1 commodity 12-market set)

Per-market tick size and multiplier must be loaded from instrument metadata (part of the data manifest §2 object set). Indicative values for the v1 commodity universe (verify against source data, do not hardcode):

| Market | Tick size | Tick value | Notes |
|---|---|---|---|
| CL (WTI) | $0.01 | $12.50 | very liquid, slippage assumption sound |
| NG (natgas) | $0.001 | $10.00 | more volatile, 2-tick assumption may be optimistic |
| RB (RBOB) | $0.0001 | $4.20 | energy complex |
| HO (heating oil) | $0.0001 | $4.20 | energy complex |
| GC (gold) | $0.10 | $10.00 | liquid |
| SI (silver) | $0.005 | $25.00 | tick value larger — slippage $ impact higher |
| HG (copper) | $0.0005 | $12.50 | |
| ZC/ZS/ZW (ags) | 0.25¢ | $12.50 | seasonal roll liquidity varies |
| LE (live cattle) | $0.025 | $10.00 | livestock |
| HE (lean hogs) | $0.025 | $10.00 | livestock |

**Implication for the ladder:** the 2-tick/side baseline is reasonable for the energy/metals liquid core; it may flatter the ags, livestock, and NG. The harness must apply per-market tick values (not a flat pip assumption), and the pessimistic tier (3 ticks) is the honest stress for thinner markets.

---

## 6. What this cost model does NOT do (pre-committed constraints)

- **No parameter tuning to make a result pass.** The ladder is fixed; if baseline fails, the result fails.
- **No "rescue" overlays** (TA filters, RSI gates, breakout confirmation) — those are closed-lane violations and are forbidden by `research/program.md`.
- **No netting of margin opportunity cost** into research P&L (§2.5) — reported separately only.
- **No optimistic-only verdicts** (§1 hard rule).
- **No ignoring of roll slippage** (§2.3) — this is the term-structure-specific cost that makes naive backtests look better than reality.

---

## 7. Forward pointer to the harness spec (#3)

The harness spec must encode this cost model as a **pluggable cost function** with the three tiers selectable via a flag (e.g., `--cost-tier baseline|optimistic|pessimistic`), defaulting to baseline. The RESULTS doc template must include a three-row cost-sensitivity table (one verdict per tier) plus the attribution table (spot vs roll, IS vs OOS). Net of §2.1+2.2+2.3 only; margin in a separate exposure section.

---

## Phase-1 deliverable sequence status

1. ✅ Data manifest — `ROLL_YIELD_DATA_MANIFEST_2026-06.md` (commit `cc9d32b`)
2. ✅ **This doc** — Cost model
3. ⬜ Harness spec — `ROLL_HARNESS_SPEC_2026-06.md` (encodes Gaps A/B/D/E + this cost model as pluggable tiers)
4. ⬜ One falsifiable test — gated on owner data-source decision (§5 of manifest) + #3

**Still gated:** deliverable #4 (the test itself) and the §7 data-quality gate. Deliverables #2 and #3 are now complete or in-progress without any data purchase — confirming the review correction that the data decision blocks only #4, not the whole sequence.
