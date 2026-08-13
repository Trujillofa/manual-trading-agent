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

**Implication:** This account is **not** swap-free (unlike Hetzner cTrader).

## 2026-08-13 verifier + gross (real Vantage rates)

| Step | Artifact | Verdict |
|------|----------|---------|
| Verify | `docs/research/carry/CARRY_DATA_MANIFEST_VANTAGE_2026-08-13.md` | **DATA_PASS** (5/5 OHLC via yfinance; 5/5 nonzero swaps) |
| Gross (uniform $10/pip) | `docs/research/carry/CARRY_GROSS_RESULTS_VANTAGE_2026-08-13.md` | **GROSS_PASS_REAL_DATA** audit-only (PF 8.665; ~$11.3k) — **superseded for gates** |
| Gross (MT5 tick_value) | `docs/research/carry/CARRY_GROSS_RESULTS_VANTAGE_PIPCORRECT_2026-08-13.md` | **GROSS_PASS_REAL_DATA** (PF **1.155**; net ~$395 on $100k / 10% vol / 2016–2026) |

Commands:

```bash
.venv/bin/python -m research.new_edge.carry.data.verify_carry_data \
  --rates research/new_edge/carry/data/verified_swap_rates_VANTAGE_2026-08-13.json \
  --quick --start 2016-01-01 --end 2026-08-01 \
  --output docs/research/carry/CARRY_DATA_MANIFEST_VANTAGE_2026-08-13.md

.venv/bin/python -m research.new_edge.carry.gross_carry_test \
  --rates research/new_edge/carry/data/verified_swap_rates_VANTAGE_2026-08-13.json \
  --economics auto --start 2016-01-01 --end 2026-08-01 \
  --output docs/research/carry/CARRY_GROSS_RESULTS_VANTAGE_PIPCORRECT_2026-08-13.md
```

**Authoritative economics:** `--economics auto|mt5` → account-currency $/lot/day = `swap_*_raw × tick_value`; rank by long $; pair pip_$ = `tick_value × points_per_pip`.

Legs (pip-correct): LONG AUD/JPY + AUD/USD; SHORT NZD/JPY + NZD/USD + USD/ZAR.

**Interpretation:** Premise still clears the gross gate after unit fix, but the edge is **thin** (PF 1.16). NZD/JPY short pays large funding (~−$2.5k) and nearly cancels the winners. Uniform-$10 run remains on disk as a cautionary overstatement (ZAR pip_$ was ~$0.62, not $10).

**Caveats (block KEEP):**
- Snapshot rates held constant over 10y (no historical swap path).
- Price P&L ignored by design (gross-first).
- tick_value is a **single snapshot** (JPY/ZAR USD conversion drifts).
- TRY pairs still missing; 5-pair universe only.
- Thin PF → realistic costs / price risk can easily flip to DISCARD.

**Next allowed (not LIVE):** richer costs + price-P&L / carry-crash stress + chronological IS/OOS. Not Branch B promotion. Do not retune legs to rescue PF.

## This branch may

- Ingest nonzero long/short swaps from Vantage (done) or another swap-paying account
- Re-run verifier/gross with `--rates` / `--economics auto|mt5` (done for Vantage 2026-08-13)
- Add TRY symbols to Market Watch and re-export if needed
- Proceed to richer costs + price-P&L / IS-OOS falsifiers **without** retuning legs to rescue thin PF

## This branch must not

- Reopen the zero-swap Hetzner cTrader carry sub-lane
- Promote sample/template JSON as real broker data
- Treat GROSS_PASS_REAL_DATA as KEEP / live authorization
- Mix XAU/BTC INTEREST/odd POINTS conversions into the FX carry harness without a separate unit contract
