# COT Positioning — Phase-0.5 Spike Note (2026-06-23)

**Status:** Spike note, not the program thesis. COT is a **separate pre-registered lane** (Premise #6) authorized after PR #26 merges per [`PROGRAM_DECISION_MEMO_ADDENDUM_2026-06-24.md`](../PROGRAM_DECISION_MEMO_ADDENDUM_2026-06-24.md). It is **not** a seven-pair FX directional system — the universe is **≥15 COT-reported listed-futures markets** (energy, metals, ags, livestock, financials as coverage allows).

This note records what the spike verified and where it hit an environment boundary, so the next session can resume without rediscovering it.

---

## What the spike verified

1. **Premise #6's data gate is real and free.** CFTC Commitment-of-Traders data is publicly accessible; a maintained Python package (`cot_reports` v0.1.3 on PyPI) wraps the fetch, including annual archives and year-to-date accumulators. No paid source required for premise #6.

2. **The financial-futures COT file for 2026 is directly reachable** from this environment:
   - `https://www.cftc.gov/files/dea/history/fut_fin_txt_2026.zip` → 335 KB, inner `FinFutYY.txt`, well-formed CSV with the expected schema (`Market_and_Exchange_Names`, `As_of_Date_In_Form_YYMDD`, `Open_Interest_All`, `Dealer_*`, `Asset_Mgr_*`, `NonComm_*`, …).

3. **The existing `research/new_edge/vol_regime/` lane is a clean template** for the COT spike to mirror:
   - Entrypoint: argparse with `--start/--end/--output`
   - Verdict ladder: `GROSS_PASS → DISCARD/KEEP` via `determine_verdict` + `determine_net_verdict` (gross gate → OOS net gate)
   - RESULTS doc writer: `build_results_doc(...)` emits the canonical lane-verdict markdown
   - Lane constants: `MIN_TRADES`, `GROSS_PF_DISCARD = 1.05`, `NET_OOS_PF_PASS = 1.20`
     The COT spike should reproduce this shape exactly (different signal, same plumbing) — that is the point of the warm-up.

---

## Where the spike hit an environment boundary (honest record)

- **Direct fetch of the futures-only / disaggregated / complete-history archives failed** (`fut_txt*.zip`, `disag_txt*.zip`, and bare `fut_txt.zip` all returned HTTP 404 from this environment; only `fut_fin_txt_2026.zip` resolved). The CFTC URL scheme appears to have changed or these specific paths are stale.
- **Web research tools were unavailable** in this session (`pi-research` returned no readable sources; `web_fetch`/`web_search` against cftc.gov failed) — appears to be a connectivity/reachability issue specific to cftc.gov from this host, not a research-method problem.
- **Implication:** the spike can prove the plumbing pattern and the financial-futures data path, but cannot complete a full multi-year, multi-market COT download in this session. A network-accessible environment (or the `cot_reports` package, which handles URL/versioning internally) is needed to run the actual falsification.

---

## Resume plan (next session, ~half day)

1. `pip install cot_reports` in the worktree venv — delegates URL/version handling to the maintained package (sidesteps the 404 archaeology).
2. Create `research/new_edge/cot_positioning/cot_positioning_test.py` mirroring `vol_regime/range_compression_breakout_test.py`:
   - Signal: fade extreme non-commercial positioning z-score (record-long → short, record-short → long), weekly rebalance.
   - Universe: ≥15 COT-reported futures markets across sectors (not FX spot pairs). Start from the Lane 7 commodity set where coverage overlaps, then add financial/index futures (ZN, ZB, ES, NQ, etc.) to reach breadth.
   - Reuse: `determine_verdict` / `determine_net_verdict` shape, RESULTS doc writer, ledger append.
3. Pre-written gates (from `CANDIDATE_PREMISES_NEW_CLASS_2026-06.md` premise #6):
   - **Pass:** OOS net PF ≥ 1.20; positive across ≥60% of markets; monotonic in positioning-extremity quintile.
   - **Stop:** OOS net PF < 1.0; one-market-only; no monotonicity in extremity.
4. Run once, write `COT_RESULTS_YYYY-MM-DD.md`, append ledger row, update `CLOSED_RESEARCH_LANES.md` if DISCARD (per Gap E).
5. **Either outcome is acceptable** — incidental DISCARD of #6 for free is a win; "harness validated, move on" is also a win. COT does not become the program thesis regardless of result.

---

## What this spike does NOT authorize

- Promoting COT to the program thesis (it remains Phase-0.5 warm-up).
- Spending more than ~half a day on it — if `cot_reports` also fails to fetch, record COT as environment-blocked and proceed directly to the roll-yield data-source decision (manifest §5).
- Any rescue overlays on a failing COT result (closed-lane discipline applies even to warm-up spikes).

---

## Pointer

The roll-yield lane (Premise #1) remains the program thesis. This spike exists only to de-risk the futures harness skeleton on free data before the owner authorizes paid individual-contract data (Norgate / FirstRate / CSI / Pinnacle per manifest §5). Cost model for the roll-yield lane is complete (`ROLL_COST_MODEL_2026-06.md`); harness spec (#3) is the next thesis-path deliverable and does not depend on the COT spike completing.
