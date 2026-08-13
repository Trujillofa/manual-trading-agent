# Lane 3 — Broker-true carry (new account only)

See program: `docs/research/RESEARCH_LANES_PROGRAM_2026-08.md`.

**Existing tree:** `research/new_edge/carry/`  
**Hetzner cTrader account:** CLOSED_DISCARD (all resolved swaps 0.0).

## 2026-08-13 fetch (Vantage MT5 via mt5-arch)

| Item | Result |
|------|--------|
| Source | Live Vantage login `27496181` / `VantageMarkets-Live 5` |
| Bridge | Patched `Mt5ArchBridge` v1.23 (`SYMBOL_SWAP_*`) |
| Snapshot | `research/new_edge/carry/data/mt5_symbols_VANTAGE_2026-08-13.json` |
| Carry JSON | `research/new_edge/carry/data/verified_swap_rates_VANTAGE_2026-08-13.json` |
| Modes | FX pairs = `POINTS`; BTC = `INTEREST_CURRENT` (excluded from carry-pairs export) |
| Triple swap | Wednesday (`swap_rollover3days=3`) for FX |
| Nonzero | **Yes** — 5 carry pairs exported (AUD/USD, NZD/USD, AUD/JPY, NZD/JPY, USD/ZAR) |
| Missing vs template | USD/TRY, EUR/TRY, GBP/TRY not in Market Watch / unresolved |

Example converted rates (pips/day/lot from POINTS ÷ 10 on 5/3-digit FX):

| Pair | Long | Short |
|------|------|-------|
| AUD/JPY | +0.244 | −1.218 |
| NZD/JPY | +0.058 | −0.561 |
| AUD/USD | +0.037 | −0.186 |
| NZD/USD | −0.321 | +0.133 |
| USD/ZAR | −22.846 | +2.532 |

**Implication:** This account is **not** swap-free (unlike Hetzner cTrader). Lane 3 can proceed to `verify_carry_data` / `gross_carry_test` **using the Vantage JSON** (wire `--rates` path or replace the template load path). Treat USD/ZAR magnitude and missing TRY pairs as caveats before any KEEP claim.

## This branch may

- Ingest nonzero long/short swaps from Vantage (done) or another swap-paying account
- Re-run `verify_carry_data` + `gross_carry_test` on the Vantage file
- Add TRY symbols to Market Watch and re-export if needed

## This branch must not

- Reopen the zero-swap Hetzner cTrader carry sub-lane
- Promote sample/template JSON as real broker data
- Mix XAU/BTC INTEREST/odd POINTS conversions into the FX carry harness without a separate unit contract
