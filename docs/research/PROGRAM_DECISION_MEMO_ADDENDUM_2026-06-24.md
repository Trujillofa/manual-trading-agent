# Program Decision Memo — Addendum (2026-06-24)

**Supersedes (partially):** [`PROGRAM_DECISION_MEMO_2026-06-20.md`](PROGRAM_DECISION_MEMO_2026-06-20.md) §Scope Boundaries and §Decision
**Authority:** Owner-approved reconciliation of governance conflict between the 2026-06-20 forex stop and draft PR #26 (new instrument-class research)
**Audience:** Humans and agents deciding what to build, run, or fund next in this repository

---

## What this addendum changes

The 2026-06-20 memo correctly **stopped retuning closed forex lanes** and preserved Branch B as an observability tool. It also declared the repository **forex-exclusive for all research**, which conflicts with the owner-approved new-instrument program documented in PR #26.

This addendum **supersedes only the forex-exclusive research restriction**. All other stop rules, closed-lane prohibitions, and Branch B posture from the 2026-06-20 memo remain in force.

---

## Decision (reconciled scope)

| Layer                       | Scope                                                                              | Status                          |
| --------------------------- | ---------------------------------------------------------------------------------- | ------------------------------- |
| **Production / Branch B**   | Forex scanner + Telegram alerts on Hetzner                                         | Continue unchanged; forex-only  |
| **`src/` live code**        | No equities, futures, or options execution                                         | Unchanged                       |
| **Closed forex lanes**      | FX directional TA, TSMOM, carry (Hetzner), stat-arb, event drift, vol-regime       | Permanently closed; no retuning |
| **New research (isolated)** | Listed-futures research under `research/new_edge/` in dedicated worktrees/branches | **Authorized, research-only**   |

**Stop rule preserved:** Do not reopen or retune any closed forex lane. Negative forex results remain authoritative.

**New rule:** New-instrument research MAY proceed only under the re-entry protocol in `research/program.md` and the lane contracts in PR #26 / successor docs. It MUST NOT modify Branch B behavior, production config, or `src/cli.py`.

---

## What remains prohibited

1. **Forex profitability retuning** on closed or discarded lanes (parameters, filters, cost tweaks, "one more variant").
2. **Profitability claims** from Branch B scan volume, near-setup counts, or observability metrics.
3. **Event strategies** on the discarded surprise-drift contract. Calendar `DATA_PASS` is infrastructure only; it does not authorize another event-trading prototype without a genuinely new, separately approved premise.
4. **FX directional COT systems.** COT research, if run, must target **broad listed-futures positioning** (≥15 COT-reported markets), not a seven-pair FX directional rewrite.
5. **Carry reopening** on the Hetzner cTrader account unless a **different account** proves nonzero long/short swaps first.
6. **Microstructure as standalone alpha** unless attached to a future gross-positive edge.
7. **Crypto lanes** in this repository.
8. **Automatic promotion** from any research `KEEP` verdict to paper or live trading.

---

## Authorized research program (PR #26 / Lane 7)

The owner-approved first new-instrument lane is **commodity futures term-structure / roll yield**, documented in:

- `docs/research/CANDIDATE_PREMISES_NEW_CLASS_2026-06.md`
- `docs/research/term_structure/ROLL_YIELD_DATA_MANIFEST_2026-06.md`
- `docs/research/term_structure/ROLL_COST_MODEL_2026-06.md`
- `docs/research/term_structure/ROLL_HARNESS_SPEC_2026-06.md`

Equities and equity-index options remain **candidate premises** in the ranking doc. They are not authorized for implementation until a separate lane contract and owner decision exist.

---

## Execution order (dependency waves, not calendar estimates)

| Wave  | Dependency                                        | Deliverable                                                                   |
| ----- | ------------------------------------------------- | ----------------------------------------------------------------------------- |
| **0** | This addendum + repaired PR #26                   | Governance reconciled; Lane 7 gates unambiguous                               |
| **1** | Wave 0 merged                                     | COT data proof + relationship tests (≥15 futures markets; not FX directional) |
| **2** | COT terminal verdict recorded                     | Owner decision on individual-contract commodity data source                   |
| **3** | Wave 2 `DATA_PASS` (≥10 of 12 markets, ≥15y each) | Roll-yield loader + verifier only                                             |
| **4** | Wave 3 `DATA_PASS`                                | Frozen roll-yield harness + one falsifying run                                |
| **5** | Wave 4 terminal verdict                           | `KEEP`, `DISCARD`, or `BLOCKED` recorded; lane closed if DISCARD              |

**ML rule (binding):** No classification model until a simple fixed-rule strategy clears **gross PF > 1.10** on the pre-registered test. Relationship proof (frequency tables, OLS) precedes any classifier.

---

## Isolation requirements

- Work in dedicated git worktrees or branches (`research/new-premises-2026-06`, `research-cot-positioning-*`, etc.).
- New entrypoints under `research/new_edge/<lane>/` MUST NOT route through FX STOP-guarded paths (`research/autosearch.py`, `research/run_experiment.py`).
- Research code MUST NOT import or alter `src/scanner/`, `src/cli.py`, Telegram, or Docker production services.

---

## How agents should consult governance

Before proposing work, read in order:

1. This addendum (scope reconciliation)
2. [`PROGRAM_DECISION_MEMO_2026-06-20.md`](PROGRAM_DECISION_MEMO_2026-06-20.md) (forex stop + Branch B)
3. [`CLOSED_RESEARCH_LANES.md`](CLOSED_RESEARCH_LANES.md)
4. Lane-specific contracts under `docs/research/<lane>/`

If a proposal conflicts with this addendum, **the addendum wins** for new-instrument research scope. If it conflicts with closed-lane rules, **closed lanes win**.

---

## Sign-off

Forex profitability search on closed lanes remains **stopped**. Branch B remains **forex-only in production**. Isolated, research-only listed-futures work under pre-written gates is **authorized** as the next program wave.

_Related:_ PR #26 · [`CANDIDATE_PREMISES_NEW_CLASS_2026-06.md`](CANDIDATE_PREMISES_NEW_CLASS_2026-06.md) · [`GROK_RESEARCH_LOOP_ENGINEERING.md`](GROK_RESEARCH_LOOP_ENGINEERING.md)
