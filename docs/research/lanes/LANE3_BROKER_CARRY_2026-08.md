# Lane 3 — Broker-true carry (new account only)

See program: `docs/research/RESEARCH_LANES_PROGRAM_2026-08.md`.

**Existing tree:** `research/new_edge/carry/`  
**Hetzner cTrader account:** CLOSED_DISCARD (all resolved swaps 0.0).

## This branch may

- Ingest nonzero long/short swaps from a **different** broker/account
- Re-run `verify_carry_data` + `gross_carry_test` on real rates

## This branch must not

- Reopen the zero-swap Hetzner cTrader carry sub-lane
- Promote sample/template JSON as real broker data
