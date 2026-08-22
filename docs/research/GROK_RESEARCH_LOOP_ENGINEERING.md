# Grok Research Loop Engineering

This document defines the structured research loop that Grok (or any researcher) should follow when exploring new profitable strategy families in this project.

## Core Principles
- Never reopen closed lanes without a genuinely new premise (different edge source, data source, instrument class, or timeframe structure).
- Always define a "lane contract" BEFORE writing any strategy code.
- Verify data availability, units, and costs first.
- Run gross-first diagnostics.
- Use chronological IS/OOS.
- Produce a machine-readable ledger entry + human-readable result doc for every lane.
- Every lane must end as KEEP, DISCARD, or BLOCKED.

## The Loop (run one lane at a time)

1. **Read the guardrails**
   - `docs/PROJECT_STATUS_2026-06.md`
   - `docs/research/CLOSED_RESEARCH_LANES.md`
   - `docs/research/PROFITABILITY_PLAN_2026-06.md`
   - `docs/research/FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md`
   - `research/program.md`
   - Any lane-specific data manifests

2. **Pick the highest-priority open lane**
   - See prioritized list in `PROFITABILITY_PLAN_2026-06.md`
   - Carry/funding/swap is currently priority #1.

3. **Write the lane contract** (before any code)
   - Location: `docs/research/<lane>/CARRY_CONTRACT_YYYY-MM-DD.md` (or equivalent)
   - Must include:
     - Premise (one paragraph)
     - Why this is not a closed lane
     - Data required (fields, sources, units, history)
     - Cost model (spread, commission, slippage, swap, rollover, etc.)
     - First falsification test
     - Pass gate (exact metrics)
     - Stop gate (exact conditions that force DISCARD)
     - First command to run

4. **Verify data & cost model**
   - Build the smallest verifier possible.
   - Confirm broker swap units, rollover rules, daily OHLC coverage, etc.
   - Produce a `CARRY_DATA_MANIFEST_YYYY-MM-DD.md` or equivalent.
   - If data is missing or units are ambiguous → BLOCKED (not DISCARD).

5. **Build the smallest falsifying backtest**
   - No optimization yet.
   - Gross-first (costs = 0) to see if any edge exists at all.

6. **Run gross-first diagnostics**
   - If gross PF ≈ 1.0 (or equivalent portfolio metric near zero edge) → DISCARD.

7. **Add realistic costs + chronological IS/OOS**
   - Only if gross edge is clearly positive.

8. **Robustness & concentration checks**

9. **Write the result**
   - `docs/research/<lane>/<LANE>_RESULTS_YYYY-MM-DD.md`
   - Must contain verdict, numbers, failure reason, next action.

10. **Append to machine-readable ledger**
    - `research/new_edge/research_ledger.jsonl`
    - One JSON line per run with all key fields.

11. **Move to next open lane only after the current one has a written verdict.**

## Lane Ledger (research/new_edge/research_ledger.jsonl)
Schema (one JSON object per line):
```json
{
  "ts": "2026-06-11T...",
  "lane": "carry",
  "hypothesis": "weekly positive-swap FX portfolio",
  "status": "BLOCKED | DISCARD | KEEP",
  "branch": "...",
  "command": "...",
  "data_start": "...",
  "data_end": "...",
  "gross_pf": 0.0,
  "net_pf": 0.0,
  "oos_pf": 0.0,
  "trades_or_events": 0,
  "result_doc": "path/to/results.md",
  "failure_reason": "..."
}
```

## Current Status (as of 2026-07-02)
- FX intraday directional TA: CLOSED (locked negative)
- HTF pivot/Fibonacci directional TA: **DISCARD** (additional evidence for FX directional-TA closure; OOS net PF 0.07; see `docs/research/HTF_FIB_NEGATIVE_RESULT_2026-06.md`)
- Daily multi-asset TSMOM: CLOSED (gross edge ~1.03–1.04, weak Sharpe)
- Carry / swap / funding (Hetzner cTrader): CLOSED / DISCARD (all resolved pairs returned 0.0 swap)
- FX stat-arb pairs (daily prototype): DISCARD (gross pass on EUR/GBP + AUD/NZD, but OOS net PF < 1.20 and OOS trades < 30)
- Event / Calendar: **DATA_PASS** for HF snapshot; surprise-drift prototype **DISCARD** (OOS net PF 0.375)
- Vol-regime compression breakout: **DISCARD** (OOS net PF 0.782)
- COT positioning reversal (23-market fixed universe): relationship test failed OOS; lane closed
- Term-structure roll yield (commodity futures, Tier A): **DATA_BLOCKED** until source gate passes (individual contract-month records, ≥10/12 markets, ≥15 years)
- Zacks MCP (`zacks` in `~/.cursor/mcp.json`): **SCHEMA_PASS / ALPHA_BLOCKED** (2026-08-22). Statements + current ETF holdings only; 5y annual history; no `estimate_observed_ts`. Does **not** unblock PEAD. See `docs/research/zacks_mcp/ZACKS_MCP_CONTRACT_2026-08-22.md`.
- **Next action:** Run the term-structure **source gate only** (`docs/research/term_structure/ROLL_YIELD_DATA_MANIFEST_2026-06.md`). Evaluate free CME settlement stitching vs paid providers. Record `DATA_PASS` or `BLOCKED` in the ledger. Do **not** write strategy logic until the gate clears. Do **not** reopen HTF Fibonacci or any closed FX directional-TA lane.

## How to Start a New Lane
1. Create the worktree if needed: `./scripts/worktree-create.sh research-new-edge-program main`
2. Write the contract first.
3. Verify data.
4. Only then write code.
5. Always update the ledger and produce a result doc.

This loop exists so we stop rediscovering the same dead ends and actually make progress on genuinely different edges.
