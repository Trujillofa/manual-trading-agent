# Roll-Yield Harness Spec — 2026-06

**Status:** Phase-1 deliverable #3 for Premise #1 (futures term-structure / roll yield). Design doc only — no strategy code is authorized by this spec until deliverable #4 and only after the owner data-source decision (manifest §5) and the §7 data-quality gate (manifest) both clear.
**Authority:** `ROLL_YIELD_DATA_MANIFEST_2026-06.md` (data) + `ROLL_COST_MODEL_2026-06.md` (costs) + `research/program.md` (re-entry protocol) + `CANDIDATE_PREMISES_NEW_CLASS_2026-06.md` (premise).
**Purpose:** define the falsification harness that will run the one pre-registered test for lane 7. It encodes the review's four gaps (A trade-count, B TSMOM control, D FX-guard isolation, E ledger hygiene) and the three-tier cost model as concrete, pluggable components — so that the implementation cannot quietly drop any of them.

---

## 0. What this spec is, and is not

**Is:** a design contract. Any implementation that deviates from §1–§7 without an explicit, recorded amendment is invalid by construction.
**Is not:** authorization to write strategy code. Deliverable #4 (the test) is gated on (a) owner data-source decision, (b) §7 data-quality checklist passing for ≥10 markets, (c) this spec. All three must hold.

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
├── control_tsmom.py            # 12m spot-momentum control on same universe (Gap B)
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
    --start 2016-01-01 --end 2026-06-01 \
    --cost-tier baseline \
    --output docs/research/term_structure/ROLL_YIELD_RESULTS_YYYY-MM-DD.md
```

**FX-guard scoping (Gap D, binding):** `research/autosearch.py` and `research/run_experiment.py` must remain FX-scoped. The new `run.py` does **not** import them, does **not** route through them, and carries its **own** verdict logic in `judge.py`. A pre-commit guard comment in `run.py` records: *"This entrypoint is term-structure/futures only. Do not add FX majors OHLC directional TA here — that lane is closed (see research/program.md STOP banner) and gated by --override-negative-result in the FX entrypoints."*

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
    """Return cross-sectional rank of markets by annualized roll yield, descending.
    roll_yield_annualized = 12 * (log(F1_close) - log(F2_close))
    where F1 = active contract (max OI), F2 = next expiry.
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

`control_tsmom.py` runs, **on the identical 12-market universe and identical rebalance dates**:

```python
def rank_by_spot_momentum(market_data: dict[str, MarketData], as_of: date) -> pd.Series:
    """Cross-sectional rank by 12-month spot return on the continuous series.
    Long top quintile, short bottom quintile, same vol-target weighting as signal.py."""
```

**Binding KEEP-condition (pre-committed, non-relaxable):**

| Condition | If met | If not met |
|---|---|---|
| Roll-yield OOS net PF > Spot-momentum OOS net PF **by a meaningful margin (≥0.10)** | Eligible for KEEP (subject to all other gates) | → **DISCARD**, reason `"repackaged TSMOM — roll yield does not outperform spot momentum on the same universe"` |
| Roll-component > 50% of pre-friction gross P&L in OOS (attribution §5) | Reinforces KEEP | → DISCARD, reason `"edge dominated by spot drift, not roll yield"` |

Both must hold. The control run appears in the RESULTS doc as its own row, side-by-side with the roll-yield result, so the comparison is auditable and cannot be quietly omitted.

---

## 5. Attribution (binding gate — lane-3 lesson)

`attribution.py` decomposes every realized position return:

```
realized_return = spot_component + roll_component − friction
  spot_component = Δ continuous_series over the hold (ratio-adjusted, so roll gaps excluded)
  roll_component = Σ front_vs_deferred_spread captured at each roll during the hold (raw prices)
  friction       = commission + slippage + roll_slippage (per cost model tier)
```

The RESULTS doc emits an attribution table (IS / OOS × spot / roll / friction), and the pass gate requires `roll_component` to dominate (>50% of gross) and to be net-positive in OOS. This is the second anti-self-deception gate (orthogonal to the TSMOM control): even if roll beats spot-momentum, if the roll *strategy's* P&L comes from spot drift rather than the roll yield itself, it's DISCARD.

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

`judge.py` implements a verdict ladder adapted for slow strategies (the standard "≥30 trades" rule is ill-defined for monthly rebalance — manifest §4 / review Gap A):

```
STAGE 1 — GROSS (no friction):
  if gross_pf <= 1.10:  → DISCARD "gross PF near 1.0, no edge before costs"
  else: GROSS_PASS

STAGE 2 — NET (baseline tier):
  if net_pf < 1.20:  → DISCARD "baseline OOS net PF < 1.20"
  else: NET_PASS

STAGE 3 — STATISTICAL BAR (Gap A, slow-strategy provision):
  Bootstrap-resample OOS rebalance-event returns (B=2000); compute net_pf for each.
  if 5th-percentile bootstrapped net_pf <= 1.0:  → DISCARD "edge not robust to rebalance ordering"
  else: STAT_PASS

STAGE 4 — ATTRIBUTION + CONTROL (Gaps B, lane-3):
  if roll_component <= 50% of gross OOS P&L:  → DISCARD "edge dominated by spot drift"
  if roll_yield_oos_net_pf <= tsmom_oos_net_pf + 0.10:  → DISCARD "repackaged TSMOM"
  if year_concentration > 25% from any single year:  → DISCARD "concentrated in one year"
  if sector_concentration > 50% from any single sector:  → DISCARD "concentrated in one sector"
  else: KEEP
```

**Trade-count definition (Gap A, pre-committed):** "one trade" = one market's directional position change at a rebalance (entry, exit, or flip). With ~42 OOS rebalance events over a 3.5y OOS window and a 12-market universe, the expected event count is well above any reasonable threshold; the bootstrap bar (Stage 3) replaces the fixed ≥30 rule with a robustness check appropriate to slow strategies. This satisfies profitability-plan rule 4's "separate pre-written statistical bar for slower-term strategies."

**KEEP requires passing all four stages under the baseline cost tier.** Any DISCARD is terminal — no "let me try a variant" rescue (closed-lane rule).

---

## 8. Ledger + closure hygiene (Gap E)

- **On Phase-1 start (already done, commit `cc9d32b`):** seed `research/new_edge/research_ledger.jsonl` with `{lane: "term_structure_roll_yield", status: "phase1_manifest"}`.
- **On test run:** append a row with `{status: "tested", gross_pf, net_pf, oos_pf, verdict}` matching the existing `vol_regime` row schema.
- **On DISCARD:** append lane 7 to `docs/research/CLOSED_RESEARCH_LANES.md` (status, one-paragraph finding, numbers, pointer to RESULTS doc) and one line to `docs/PROJECT_STATUS_2026-06.md`. Pre-commit the closure entry shape now so it can't be skipped: *"Lane 7 (term-structure roll yield): DISCARD — [reason]. See ROLL_YIELD_RESULTS_YYYY-MM-DD.md. Do not retune quintile, rebalance, vol-target, universe, or horizon."*
- **On KEEP:** do **not** promote to paper-shadow automatically. Promotion requires the profitability plan's separate paper-shadow gate (≥30d run, kill switch, risk limits) and explicit owner authorization.

---

## 9. Non-goals (pre-committed constraints)

- **No live trading integration.** No broker, no `src/cli.py`, no Telegram, no Branch B. Research-only until a KEEP verdict *and* a separate promotion decision.
- **No parameter optimization.** Quintile, rebalance, vol-target window, and universe are fixed in §3. Sweep/autosearch is explicitly out of scope for this lane (the FX autosearch pattern is not imported here — Gap D).
- **No rescue overlays.** A DISCARD is terminal.
- **No optimistic-only verdicts.** Enforced in `judge.py`.
- **No continuous-only data for the signal.** Enforced by the loader interface (§2).

---

## 10. Implementation order (when #4 is authorized)

Only after (a) owner data-source decision and (b) §7 data-quality gate passes for ≥10 markets:

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
