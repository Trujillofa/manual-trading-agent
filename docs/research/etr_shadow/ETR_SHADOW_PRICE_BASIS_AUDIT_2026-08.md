# ETR Shadow Price-Basis Audit — 2026-08

**Generated:** 2026-08-22T17:56:25Z  
**Events:** `/tmp/etr_shadow_prod_2026-08-22/etr_shadow_events.jsonl` (20 closed rows)  
**Polls:** `/tmp/etr_shadow_prod_2026-08-22/etr_shadow_polls.jsonl` (3710 rows)  
**Open book:** `/tmp/etr_shadow_prod_2026-08-22/etr_shadow_open.json` (empty `{}`)  
**Source:** Hetzner prod `/home/emilio/manual-trading-agent/logs` (scp 2026-08-22 via Tailscale `crypto-agent`)  
**Window:** 2026-08-12T15:30:30Z → 2026-08-22T17:45:00Z  
**References:** yfinance last close 2026-08-21/22 — BTC-USD 77.3k, GC=F 4624, NQ=F 29.39k, CL=F 87.06

Hygiene track only — not a KEEP / expectancy claim.

## Scale table

Typical prices in `ASSET_REFERENCE` were refreshed to the 2026-08 yfinance closes above (prior gold typical 2400 was stale and made a same-scale market look like a 1.83 stretch).

| Asset | Events | Prices | Median ETR | Ref typical | Scale ratio | Basis guess |
|---|---:|---:|---:|---:|---:|---|
| btc | 2 | 938 | 6.348e+04 | 7.7e+04 | 0.8244 | compatible_with_yf_continuous |
| gold | 2 | 941 | 4393 | 4600 | 0.955 | compatible_with_yf_continuous |
| nasdaq | 7 | 974 | 725.7 | 2.94e+04 | 0.02468 | etr_terminal_native |
| oil | 9 | 995 | 86.53 | 87 | 0.9946 | compatible_with_yf_continuous |

## Notes

### btc

- Median/typical ratio 0.824 within the 0.5–2.0 band.
- Poll range 62.6k–78.7k. Median sits below BTC-USD spot (~77.3k); the high end of the poll window matches spot. Same order of magnitude — treat as compatible, not as a mapped product.

### gold

- Median/typical ratio 0.955 within the 0.5–2.0 band.
- ETR ~4393 vs GC=F ~4624 is a small futures/session offset, not a scale break. The 2026-08-13 draft labeled gold compatible against a stale 2400 typical (ratio 1.834); the label was right, the reference was not.

### nasdaq

- Median/typical ratio 0.02468 — ETR levels are **not** on the same scale as `NQ=F`.
- ETR ~726 vs NQ=F ~29.4k (~40×). That is not a clean 10× or 100×. Do **not** invent a conversion.
- Shadow MFE/MAE on this asset are terminal points (observed MFE 0–9, MAE 0–2.4). Treating them as NQ futures points would be a category error.

### oil

- Median/typical ratio 0.995 within the 0.5–2.0 band. ETR and `CL=F` are interchangeable for scale checks.

## Shadow book (descriptive only)

Closed events 2026-08-12 → 2026-08-20: **20** (oil 9, nasdaq 7, btc 2, gold 2).  
Status mix: hit_invalidation 10, expired 5, hit_tp1 3, bias_flip 2.  
Open book at pull time: **empty**.  
Poll coverage is balanced (~926–929 rows per asset) over ~10 days.

This is not a P&L sample. Horizon is the shadow default (24h). Do not compute PF / win-rate / expectancy from these 20 rows.

## Pass criteria

| Criterion | Result |
|---|---|
| Every asset has an explicit basis label | **Pass** — btc/gold/oil `compatible_with_yf_continuous`; nasdaq `etr_terminal_native` |
| Scale ratio reported; extremes flagged | **Pass** — nasdaq 0.0247 flagged; others in 0.5–2.0 |
| Written rec: keep terminal-native unless compatible; never mix bases | **Pass** — see below |

## Recommendation

1. Read shadow MFE/MAE in **ETR terminal units**. For btc / gold / oil those units are close enough to yfinance continuous for qualitative comparison. For nasdaq they are a different product scale.
2. Do **not** convert shadow outcomes into Branch B / broker / `NQ=F` P&L. No mapping table is checked in; none is authorized from this audit.
3. Keep collecting shadow evidence. N=20 over 8 days does not license promotion.
4. Local checkout `logs/` has no `etr_shadow_*.jsonl` (last `etr_state` poll 2026-08-11). Prod Hetzner is the authority until local sync exists.

## vs 2026-08-13 vanished `/tmp/etr_shadow_snap`

Same qualitative call: nasdaq terminal-native, others compatible. This rerun replaces that snap with 10 days of prod polls (3710 vs ~hundreds) and current yfinance typicals.
