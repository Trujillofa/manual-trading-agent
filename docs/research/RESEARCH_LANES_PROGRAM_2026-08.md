# Research Lanes Program — 2026-08

**Branch:** `cursor/research-lanes-2026-08`  
**Base:** `main` @ Branch B ops posture (`docs/BRANCH_B_OPS_POSTURE_2026-08.md`)  
**Rule:** Research-only. Do **not** retune live Branch B for P&L. Do **not** reopen closed forex OHLC-TA / TSMOM / Hetzner-carry / stat-arb / event-drift / vol-regime / COT-reversal / HTF-fib lanes.

## Objective

Run three **new-premise** lanes under the existing honest harness discipline (gross-first → realistic costs → chronological IS/OOS → written KEEP/DISCARD). Plus one ops/research hygiene track for live ETR shadow price-basis.

## Lane board

| # | Lane | Premise (must stay new) | First allowed step | Stop / reopen rules |
|---|------|-------------------------|--------------------|---------------------|
| 1 | **PEAD data-proof** | Point-in-time US earnings surprise → short-horizon equity drift | **BLOCKED 2026-08-22.** No licensed snapshot. Free sources still lack `estimate_observed_ts`. Zacks UNVERIFIED — owner sample required. | No relationship/strategy code until ledger `DATA_PASS`. Synthetic fixture ≠ PASS. Do not buy data without a written owner decision. |
| 2 | **Listed-futures costs / roll** | Authorized futures instrument-class research with **contract-correct costs** (not yfinance continuous toys for KEEP) | Re-open source gate only with owner-approved data that clears Tier-A requirements in term-structure contracts | Free CME PA2 remains BLOCKED (coverage/OI). No Tier-B until DATA_PASS. Not a TSMOM retune. |
| 3 | **Broker-true carry** | Overnight financing as primary return — **different account/broker** with nonzero long/short swaps | Prove nonzero swaps via statement/API → replace template JSON → re-run `verify_carry_data` + `gross_carry_test` | **CLOSED 2026-08-13:** Vantage pip-correct gross thin PASS → net+IS/OOS **DISCARD_REAL_DATA** (OOS PF 1.043 < 1.20). No leg retune. Hetzner cTrader zero-swap stays **CLOSED_DISCARD**. |

### Hygiene track (not an alpha KEEP path)

| Track | Goal | First step |
|-------|------|------------|
| **ETR shadow price-basis** | Make forward-shadow MFE/MAE interpretable | **COMPLETE 2026-08-22.** Prod Hetzner logs (3710 polls / 20 events, 2026-08-12→22): nasdaq `etr_terminal_native`; btc/gold/oil `compatible_with_yf_continuous`. No conversion table. Not KEEP. |

## Execution order (serial, fail-fast)

1. ETR price-basis audit — **done 2026-08-22** (unblocks honest reading of live shadow logs).
2. PEAD — **BLOCKED 2026-08-22** (no licensed snapshot; provenance refreshed). Next PEAD step is an owner-approved Zacks (or equivalent) sample, then `verify_pead_data` only.
3. Futures source gate: owner data decision required before spend/code beyond audit docs.
4. Carry: only after a **new** broker account proves nonzero swaps.

Do **not** run all four strategy implementations in parallel. Parallel doc/audit work is fine.

## KEEP bar (unchanged)

- Gross edge first (do not optimize a ~1.0 gross base).
- Net OOS after realistic friction.
- Chronological IS/OOS; no OOS tuning.
- ≥30 OOS trades unless a slower-bar is pre-written in the lane contract.
- Paper-shadow before any live risk.

## Explicit non-goals

- More RSI/EMA/Donchian variants on FX OHLC.
- Promoting Branch B alert volume as expectancy.
- Crypto token/unlock lanes in this repo.
- Microstructure-as-alpha without a prior gross-positive edge.

## Deliverables on this branch

| Path | Purpose |
|------|---------|
| `docs/research/RESEARCH_LANES_PROGRAM_2026-08.md` | This program |
| `research/new_edge/etr_shadow/` | Price-basis audit CLI + README |
| `docs/research/etr_shadow/ETR_SHADOW_PRICE_BASIS_AUDIT_2026-08.md` | Written audit findings (prod 2026-08-22) |
| `docs/research/pead/PEAD_SOURCE_AUDIT_RESULTS_2026-08.md` | PEAD source-audit refresh; still BLOCKED |
| Lane pointers | Existing PEAD / term_structure / carry trees — no closed-lane retunes |

## Ledger

Append outcomes to `research/new_edge/research_ledger.jsonl` with `branch: cursor/research-lanes-2026-08`.
