# Lane 1 — PEAD (data-proof only)

See program: `docs/research/RESEARCH_LANES_PROGRAM_2026-08.md`.

**Status:** `BLOCKED` 2026-08-22  
**Existing tree:** `research/new_edge/pead/`  
**Contract:** `docs/research/pead/PEAD_CONTRACT_2026-07.md`  
**Source audit:** `docs/research/pead/PEAD_SOURCE_AUDIT_RESULTS_2026-08.md`

## 2026-08-22 outcome

No licensed snapshot on disk, in `pinned/`, or on Hetzner. Free-tier probes (SEC, Alpha Vantage, yfinance, Twelve Data, FMP, EODHD) still have no `estimate_observed_ts`. Zacks is UNVERIFIED pending an owner-approved sample. Synthetic fixture ≠ PASS. Relationship code remains unauthorized.

## This branch may

- Point `verify_pead_data` at a licensed snapshot once available
- Update provenance / ledger with BLOCKED or DATA_PASS

## This branch must not

- Write relationship or strategy code before DATA_PASS
- Treat `synthetic_minimal` as a PASS
- Purchase Zacks / EODHD / any paid trial without a written owner decision
