# Roll-Yield Harness Spec — 2026-06

**Status:** Phase-1 deliverable #3 for Premise #1 (futures term-structure / roll yield). Design doc only — no strategy code is authorized by this spec until deliverable #4 and only after the owner data-source decision (manifest §5) and the §7 data-quality gate (manifest) both clear.
**Authority:** `ROLL_YIELD_DATA_MANIFEST_2026-06.md` (data) + `ROLL_COST_MODEL_2026-06.md` (costs) + `research/program.md` (re-entry protocol) + `CANDIDATE_PREMISES_NEW_CLASS_2026-06.md` (premise).
**Purpose:** define the falsification harness that will run the one pre-registered test for lane 7. It encodes the review's four gaps (A trade-count, B TSMOM control, D FX-guard isolation, E ledger hygiene) and the three-tier cost model as concrete, pluggable components — so that the implementation cannot quietly drop any of them.

---

## 0. What this spec is, and is not

**Is:** a design contract. Any implementation that deviates from §1–§7 without an explicit, recorded amendment is invalid by construction.
**Is not:** authorization to write the full strategy backtest. Deliverable #4 (the falsifying run) is gated on (a) owner data-source authorization, (b) Tier A verifier returning `DATA_PASS` (≥10 markets pass manifest §7), (c) this spec.

**Authorization tiers (binding):**

| Tier                        | Modules                                                                                                                      | Entry gate                                   | Exit status                                                       |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- | ----------------------------------------------------------------- |
| **A — data plumbing**       | `data/loader.py` (interface + `SyntheticLoader`), `data/verify_term_structure_data.py`, `data/metadata.py`, unit tests       | Owner authorizes a data source (manifest §5) | `DATA_PASS`, `BLOCKED`, or `DISCARD` (verifier runs §7 checklist) |
| **B — strategy simulation** | `signal.py`, `control_tsmom.py`, `backtest.py`, `attribution.py`, `cost_model.py`, `judge.py`, `results_writer.py`, `run.py` | Tier A = `DATA_PASS`                         | `KEEP` or `DISCARD`                                               |

Tier A builds the verifier that _produces_ the §7 checklist result; it is not blocked by a pre-existing checklist pass. Tier B MUST NOT start until Tier A exits `DATA_PASS`.

---

## 1. File layout and entrypoint (Gap D — FX-guard isolation)

The existing FX research lives at `research/run_experiment.py` + `research/autosearch.py`, both of which carry an FX-specific negative-result STOP guard (`--override-negative-result` requirement for FX-majors OHLC directional TA). The futures lane **must not** be gated by that guard — it is a different instrument class.

**New, isolated layout:**

```
research/new_edge/term_structure/
├── __init__.py
├── data/
│   ├── __init__.py
│   └── loader.py              # pluggable per-source loaders (see §2)
├── signal.py                   # roll-yield ranker (pure function)
├── control_tsmom.py            # 252-day time-series momentum control (Gap B)
├── backtest.py                 # portfolio backtester: long-short, monthly rebalance
├── attribution.py              # roll-vs-spot P&L decomposition (binding gate)
├── cost_model.py               # three-tier cost function (pluggable, default baseline)
├── judge.py                    # IS/OOS split + verdict ladder + bootstrap bar (Gap A)
├── results_writer.py           # RESULTS doc emitter (mirrors vol_regime template)
└── run.py                      # CLI entrypoint: --start --end --cost-tier --output
```

**Entrypoint contract** (mirrors `research/new_edge/vol_regime/range_compression_breakout_test.py`):

```bash
python -m research.new_edge.term_structure.run \
    --start 2005-01-01 --end 2026-06-01 \
    --cost-tier baseline \
    --output docs/research/term_structure/ROLL_YIELD_RESULTS_YYYY-MM-DD.md
```

`--start` MUST be early enough that every accepted market has ≥15 complete years before `--end`. The verifier enforces this per market; if any accepted market fails the history gate, the run MUST abort before strategy simulation.

**FX-guard scoping (Gap D, binding):** `research/autosearch.py` and `research/run_experiment.py` must remain FX-scoped. The new `run.py` does **not** import them, does **not** route through them, and carries its **own** verdict logic in `judge.py`. A pre-commit guard comment in `run.py` records: _"This entrypoint is term-structure/futures only. Do not add FX majors OHLC directional TA here — that lane is closed (see research/program.md STOP banner) and gated by --override-negative-result in the FX entrypoints."_

---

## 2. Data loader interface (pluggable; source-agnostic until owner decision)

The loader is defined as an **interface** so the harness can be built and unit-tested on synthetic data before the owner picks a source (manifest §5). The real source plugs in via a single conforming class.

```python
# research/new_edge/term_structure/data/loader.py (interface)
class TermStructureDataLoader(Protocol):
    def load_market(self, symbol: str, start: date, end: date) -> MarketData: ...
```

Where `MarketData` is the three-object set from manifest §2:

```python
@dataclass(frozen=True)
class MarketData:
    symbol: str
    contract_ohlc: dict[str, pd.DataFrame]   # by expiry, e.g. "CLZ2025" → OHLC+OI
    continuous: pd.DataFrame                  # ratio-adjusted, max-OI roll (for spot P&L + TSMOM control)
    roll_calendar: list[date]                 # active-contract switch dates (OI-confirmed)
    metadata: InstrumentMetadata              # tick size, multiplier, venue, sector
```

**Concrete loaders (one implemented per owner-chosen source):**

- `NorgateLoader`, `FirstRateLoader`, `CSILoader`, `PinnacleLoader` (paid) — all conform to the interface; the choice is the owner's (manifest §5).
- `CMEStitchLoader` (free, high-effort) — CME settlement + expiry calendar + OI stitching; only if budget is zero and universe is narrowed to CME-native markets.
- `SyntheticLoader` (test only) — deterministic synthetic curves for unit-testing the harness before any real data exists.

**No loader reads from yfinance or any continuous-only source for the signal** (manifest §2 forbids it — roll yield cannot be measured from an adjusted series). yfinance may be used **only** as a cross-check on the continuous series for spot-P&L sanity, never for the signal.

---

## 3. Signal (roll-yield ranker) — pure, fixed, no optimization

`signal.py` exposes one pure function:

```python
def rank_by_roll_yield(market_data: dict[str, MarketData], as_of: date) -> pd.Series:
    """Return cross-sectional rank of markets by annualized curve slope, descending.
    annualized_curve_slope =
        (log(F1_close) - log(F2_close)) * 365 / calendar_days_between_expiries
    where F1 = active contract (max OI), F2 = next expiry.
    calendar_days_between_expiries is positive, from contract metadata.
    Skip market if F1_close <= 0 or F2_close <= 0 (manifest §6 non-positive rule).
    Positive = backwardation (paid to roll long); negative = contango."""
```

**Fixed parameters (no sweep, no optimization):**

- Rebalance: **monthly** (last trading day of month).
- Universe each rebalance: all markets passing the §7 data-quality gate.
- Portfolio construction: long the top backwardated quintile, short the bottom contangoed quintile, equal-risk-weighted by trailing 60-day realized vol (vol-target, not equal-notional — standard for cross-sectional futures).
- Hold: positions held until next monthly rebalance; rolls executed at the OI-crossover in the roll calendar.

**No overlays.** No RSI, no breakout, no ADX, no session filter. Any post-hoc "let me add a filter to rescue this" is a closed-lane violation and is rejected at code review.

---

## 4. TSMOM control (Gap B — the binding KEEP-condition)

This is the single most important anti-self-deception mechanism in the harness. It exists because lane 2 (daily TSMOM) died at gross PF 1.036 and a roll-yield result that's secretly repackaged spot momentum would be a reopened closed lane, not a new edge.

The prior draft mislabeled **cross-sectional spot momentum** as TSMOM. The binding control is **true time-series momentum**.

`control_tsmom.py` runs, **on the identical commodity universe and identical monthly rebalance dates**:

```python
def tsmom_signal(market_data: MarketData, as_of: date) -> int:
    """Per-market signal: sign of trailing 252-trading-day continuous-series return.
    +1 if return > 0, -1 if return < 0, 0 if insufficient history.
    Portfolio: long markets with +1, short markets with -1.
    Risk weighting: same trailing 60-day realized-vol method as signal.py.
    Costs and universe: identical to the roll-yield run."""
```

Cross-sectional spot momentum (rank by trailing return, long top quintile / short bottom quintile) MAY be reported as a **secondary diagnostic** in the RESULTS doc. It MUST NOT replace the TSMOM control or satisfy the KEEP gate.

**Binding KEEP-condition (pre-committed, non-relaxable):**

| Condition                                                           | If met                                         | If not met                                                                                                            |
| ------------------------------------------------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Roll-yield OOS net PF > TSMOM OOS net PF **by ≥0.10**               | Eligible for KEEP (subject to all other gates) | → **DISCARD**, reason `"repackaged TSMOM — roll yield does not outperform time-series momentum on the same universe"` |
| Roll-component > 50% of pre-friction gross OOS P&L (attribution §5) | Reinforces KEEP                                | → DISCARD, reason `"edge dominated by spot drift, not roll yield"`                                                    |

Both must hold. The TSMOM control run appears in the RESULTS doc as its own row, side-by-side with the roll-yield result, so the comparison is auditable and cannot be quietly omitted.

---

## 5. Attribution (binding gate — lane-3 lesson)

`attribution.py` decomposes pre-friction P&L into **economic spot drift** vs **economic roll yield (basis convergence)**. Contract-switch mechanics are bookkeeping, not roll yield.

```text
total_net_pnl = spot_component + roll_component - explicit_costs

  total_pre_cost[t]  = settlement MTM on the held contract (§5.1)
  roll_component[t]  = economic roll-yield accrual from F1–F2 basis convergence (§5.1)
  spot_component[t]  = total_pre_cost[t] - roll_component[t]
  explicit_costs     = commission + slippage dollar charges (cost model §2; NOT in settlement path)
```

**Reconciliation (per position, per market, portfolio):**

```text
|total_net_pnl - (spot_component + roll_component - explicit_costs)| < $0.01
```

The RESULTS doc emits an attribution table (IS / OOS × spot / roll / costs) plus a reconciliation check row. The pass gate requires `roll_component` to contribute >50% of gross OOS pre-friction P&L and to be positive in OOS.

### 5.1 Contract-level accounting algorithm (binding)

All **price-path P&L** uses **settlement prices only**. Slippage and commission are **never** embedded in fill prices; they appear only in `explicit_costs` as dollar charges at entry, exit, and roll events (cost model §2).

Let `M` = multiplier, `N` = signed contracts (+ long, − short), `S^held_t` = settlement on the contract held at end of day `t`.

**Daily settlement MTM (all days, including roll-switch days):**

```text
total_pre_cost[t] = (S^held_t - S^held_{t-1}) * M * N
```

On a roll-switch day at settlement: close `F1` at `S_F1_t`, open `F2` at `S_F2_t`. The held contract for day `t` is still `F1`, so `S^held_t = S_F1_t`. Position in `F2` begins at `t`; its MTM starts on `t+1`.

**Economic roll-yield accrual (basis convergence while holding the front contract):**

```text
roll_component[t] = N * M * (S_F1_{t-1} - S_F2_{t-1}) / max(days_to_F1_expiry_{t-1}, 1)
```

Where `F1` = active front contract at `t-1`, `F2` = next deferred contract, and `days_to_F1_expiry` = calendar days from `t-1` to `F1` expiry. Sign of `N` applies through the formula (long earns positive accrual when `F1 > F2` in backwardation).

```text
spot_component[t] = total_pre_cost[t] - roll_component[t]
```

This is the Gorton-style decomposition: roll yield is **basis convergence during the hold**, not the mechanical P&L of switching between differently priced contracts.

**Portfolio aggregation:**

```text
total_net_pnl = Σ_t total_pre_cost[t] - explicit_costs
              = Σ_t (spot_component[t] + roll_component[t]) - explicit_costs
```

At each roll the backtester MUST emit an audit row: old contract, new contract, settlements, `total_pre_cost[t]`, `roll_component[t]`, `spot_component[t]`, and slippage/commission charged in `explicit_costs`.

**Forbidden shortcuts:**

- Do not label contract-switch gap P&L as `roll_component`.
- Do not embed slippage in settlement or fill prices and also charge slippage in `explicit_costs`.
- Do not derive `spot_component` from the ratio-adjusted continuous series (TSMOM control / sanity only).

---

## 6. Cost model integration (pluggable, default baseline)

`cost_model.py` implements the three-tier ladder from `ROLL_COST_MODEL_2026-06.md` §3:

```python
class CostModel:
    def __init__(self, tier: Literal["optimistic", "baseline", "pessimistic"]): ...
    def position_cost(self, market: InstrumentMetadata, fills: Fills) -> CostBreakdown:
        # commission + execution slippage + roll slippage, per-market tick values
```

**The harness runs all three tiers on every test** and emits a three-row cost-sensitivity table in the RESULTS doc. **Baseline is the canonical KEEP/DISCARD tier; optimistic-only pass → auto-DISCARD** (cost model §1 hard rule, enforced in `judge.py`, non-relaxable).

Margin opportunity cost is **documented in a separate exposure section, not netted** (cost model §2.5).

---

## 7. Judge — verdict ladder + statistical bar (Gap A)

`judge.py` implements a verdict ladder adapted for slow strategies (the standard "≥30 trades" rule is ill-defined for monthly rebalance — manifest §4 / review Gap A).

### 7.1 Shared definitions (binding)

**Chronological split:** sort all trading days; first 65% of days = IS, last 35% = OOS. Rebalance events inherit their window from `as_of` date. No walk-forward optimization. No parameter search.

**Monthly portfolio return:** for each calendar month `m` in a window, sum daily `total_net_pnl` across all markets and positions, then divide by the month's average allocated capital (sum of `|N| * M * S` across open positions, averaged over open days). Denote this `r_m`.

**Profit factor (PF) from monthly returns:**

```text
PF(window) = sum(r_m where r_m > 0) / abs(sum(r_m where r_m < 0))
```

If the denominator is 0 (no losing months), PF is undefined → DISCARD with reason `"no losing months — insufficient loss sample for PF"`.

**Gross PF:** same formula on `total_pre_cost` monthly returns (`explicit_costs = 0`; settlement path only).

**Net PF:** same formula on `total_net_pnl` monthly returns (baseline-tier `explicit_costs`).

**OOS sanctity:** OOS months are not consulted until Stage 2. Stage 1 uses **IS gross PF only** to qualify for net/OOS evaluation.

### 7.2 Verdict ladder

```
STAGE 1 — GROSS (IS only, costs = 0):
  if gross_pf_is <= 1.10:  → DISCARD "gross PF near 1.0, no edge before costs"
  else: GROSS_PASS

STAGE 2 — NET (OOS only, baseline tier; first OOS gate):
  if oos_net_pf < 1.20:  → DISCARD "baseline OOS net PF < 1.20"
  else: NET_PASS

STAGE 3 — STATISTICAL BAR (OOS only, baseline tier):
  Fixed-block bootstrap over OOS monthly portfolio returns {r_m}:
    resamples = 2,000
    block_length = 3 months
    random_seed = 20260624
    algorithm:
      1. Partition OOS months into consecutive blocks of length 3 (final partial block dropped if <3).
      2. For each resample i in 1..2000:
         a. Draw blocks with replacement until concatenated length >= n_OOS months.
         b. Truncate to exactly n_OOS months.
         c. Compute PF_i = PF(resampled months) per §7.1.
      3. Sort {PF_i}; p05 = 5th percentile (linear interpolation).
  if p05 <= 1.0:  → DISCARD "edge not robust to monthly return ordering"
  else: STAT_PASS

STAGE 4 — ATTRIBUTION + CONTROL (OOS only):
  gross_oos_pnl = spot_oos + roll_oos   # pre-friction
  if roll_oos <= 0:  → DISCARD "roll component not positive in OOS"
  if roll_oos / gross_oos_pnl <= 0.50:  → DISCARD "edge dominated by spot drift"
  if roll_yield_oos_net_pf <= tsmom_oos_net_pf + 0.10:  → DISCARD "repackaged TSMOM"
  positive_profit_y = max(net_pnl_y, 0)   # net_pnl_y = OOS daily total_net_pnl by calendar year
  total_positive_profit = sum_y positive_profit_y
  if total_positive_profit <= 0:  → DISCARD "no positive OOS profit to concentrate"
  if max_y (positive_profit_y / total_positive_profit) > 0.25:  → DISCARD "concentrated in one year"
  positive_profit_s = max(net_pnl_s, 0)   # by sector metadata
  total_positive_profit_sector = sum_s positive_profit_s
  if max_s (positive_profit_s / total_positive_profit_sector) > 0.50:  → DISCARD "concentrated in one sector"
  else: KEEP
```

Concentration uses **positive-profit contribution**, not absolute P&L ratios. This avoids false triggers when years or sectors net negative.

**Trade-count definition (Gap A, pre-committed):** "one trade" = one market's directional position change at a rebalance (entry, exit, or flip). With ≥5 complete OOS calendar years (~60 monthly rebalance events) and a 12-market commodity universe, the expected event count is well above any reasonable threshold; the 3-month block bootstrap (Stage 3) replaces the fixed ≥30 rule with a robustness check appropriate to slow strategies. This satisfies profitability-plan rule 4's "separate pre-written statistical bar for slower-term strategies."

**History requirement (binding):** each accepted market MUST have ≥15 complete years of individual-contract data. The IS/OOS split is one chronological 65/35 holdout with no walk-forward optimization and no parameter search. The OOS window MUST contain ≥5 complete calendar years so the ≤25% single-year concentration gate is achievable.

**KEEP requires passing all four stages under the baseline cost tier.** Any DISCARD is terminal — no "let me try a variant" rescue (closed-lane rule).

---

## 8. Ledger + closure hygiene (Gap E)

- **On Phase-1 start (already done, commit `cc9d32b`):** seed `research/new_edge/research_ledger.jsonl` with `{lane: "term_structure_roll_yield", status: "phase1_manifest"}`.
- **On test run:** append a row with `{status: "tested", gross_pf, net_pf, oos_pf, verdict}` matching the existing `vol_regime` row schema.
- **On DISCARD:** append lane 7 to `docs/research/CLOSED_RESEARCH_LANES.md` (status, one-paragraph finding, numbers, pointer to RESULTS doc) and one line to `docs/PROJECT_STATUS_2026-06.md`. Pre-commit the closure entry shape now so it can't be skipped: _"Lane 7 (term-structure roll yield): DISCARD — [reason]. See ROLL_YIELD_RESULTS_YYYY-MM-DD.md. Do not retune quintile, rebalance, vol-target, universe, or horizon."_
- **On KEEP:** do **not** promote to paper-shadow automatically. Promotion requires the profitability plan's separate paper-shadow gate (≥30d run, kill switch, risk limits) and explicit owner authorization.

---

## 9. Non-goals (pre-committed constraints)

- **No live trading integration.** No broker, no `src/cli.py`, no Telegram, no Branch B. Research-only until a KEEP verdict _and_ a separate promotion decision.
- **No parameter optimization.** Quintile, rebalance, vol-target window, and universe are fixed in §3. Sweep/autosearch is explicitly out of scope for this lane (the FX autosearch pattern is not imported here — Gap D).
- **No rescue overlays.** A DISCARD is terminal.
- **No optimistic-only verdicts.** Enforced in `judge.py`.
- **No continuous-only data for the signal.** Enforced by the loader interface (§2).

---

## 10. Implementation order (when #4 is authorized)

Only after (a) owner data-source authorization and (b) Tier A verifier returns `DATA_PASS`:

1. `data/loader.py` + the chosen concrete loader + `SyntheticLoader` for unit tests.
2. `signal.py` + `control_tsmom.py` (pure functions, unit-testable on synthetic data).
3. `cost_model.py` + `attribution.py` (unit-testable).
4. `backtest.py` (portfolio simulation, uses 1–3).
5. `judge.py` + `results_writer.py` (verdict ladder + RESULTS doc).
6. `run.py` (CLI wiring).
7. **One run**, one RESULTS doc, one ledger row, one KEEP-or-DISCARD. Done.

Each step carries its own unit tests (mirroring `tests/test_vol_regime_breakout.py`'s shape). No step touches `src/` or the live scanner.

---

## Phase-1 deliverable sequence status

1. ✅ Data manifest — `ROLL_YIELD_DATA_MANIFEST_2026-06.md` (`cc9d32b`)
2. ✅ Cost model — `ROLL_COST_MODEL_2026-06.md` (`462a217`)
3. ✅ **This spec** — `ROLL_HARNESS_SPEC_2026-06.md`
4. ⬜ One falsifiable test — **GATED** on owner data-source decision (manifest §5) + §7 data-quality gate.

**The gate-definition phase is complete.** Everything needed to write trustworthy KEEP-or-DISCARD code is now specified and pre-committed. The remaining blocker is the owner's data-source authorization — without it, no individual-contract data exists and the test cannot run honestly (running it on insufficient data is explicitly forbidden, manifest §7 hard-stop).
