# Research Lanes Program — 2026-08

**Branch:** `cursor/research-lanes-2026-08`  
**Base:** `main` @ Branch B ops posture (`docs/BRANCH_B_OPS_POSTURE_2026-08.md`)  
**Rule:** Research-only. Do **not** retune live Branch B for P&L. Do **not** reopen closed forex OHLC-TA / TSMOM / Hetzner-carry / stat-arb / event-drift / vol-regime / COT-reversal / HTF-fib lanes.

## Objective

Run three **new-premise** lanes under the existing honest harness discipline (gross-first → realistic costs → chronological IS/OOS → written KEEP/DISCARD). Plus one ops/research hygiene track for live ETR shadow price-basis.

## Lane board

| # | Lane | Premise (must stay new) | First allowed step | Stop / reopen rules |
|---|------|-------------------------|--------------------|---------------------|
| 1 | **PEAD data-proof** | Point-in-time US earnings surprise → short-horizon equity drift | `python -m research.new_edge.pead.data.verify_pead_data` on a **licensed** snapshot only | No relationship/strategy code until ledger `DATA_PASS`. Synthetic fixture ≠ PASS. |
| 2 | **Listed-futures costs / roll** | Authorized futures instrument-class research with **contract-correct costs** (not yfinance continuous toys for KEEP) | Re-open source gate only with owner-approved data that clears Tier-A requirements in term-structure contracts | Free CME PA2 remains BLOCKED (coverage/OI). No Tier-B until DATA_PASS. Not a TSMOM retune. |
| 3 | **Broker-true carry** | Overnight financing as primary return — **different account/broker** with nonzero long/short swaps | Prove nonzero swaps via statement/API → replace template JSON → re-run `verify_carry_data` + `gross_carry_test` | **Status 2026-08-13:** Vantage `DATA_PASS` + pip-correct `GROSS_PASS_REAL_DATA` (PF 1.155; thin). Next = richer costs / price-P&L / IS-OOS — **not** LIVE. Uniform-$10 run superseded. Hetzner cTrader zero-swap stays **CLOSED_DISCARD**. |

### Hygiene track (not an alpha KEEP path)

| Track | Goal | First step |
|-------|------|------------|
| **ETR shadow price-basis** | Make forward-shadow MFE/MAE interpretable | `python -m research.new_edge.etr_shadow.audit_price_basis` against prod logs / fixtures |

## Execution order (serial, fail-fast)

1. ETR price-basis audit (unblocks honest reading of live shadow logs).
2. PEAD: only if a real snapshot path is available; otherwise leave `BLOCKED` with provenance update.
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
| `docs/research/etr_shadow/ETR_SHADOW_PRICE_BASIS_AUDIT_2026-08.md` | Written audit findings template |
| Lane pointers | Existing PEAD / term_structure / carry trees — no closed-lane retunes |

## Ledger

Append outcomes to `research/new_edge/research_ledger.jsonl` with `branch: cursor/research-lanes-2026-08`.
