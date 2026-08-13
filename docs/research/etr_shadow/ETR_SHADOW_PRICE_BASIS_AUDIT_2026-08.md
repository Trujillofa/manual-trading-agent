# ETR Shadow Price-Basis Audit — 2026-08

**Generated:** 2026-08-13T19:41:52Z
**Events:** `/tmp/etr_shadow_snap/etr_shadow_events.jsonl`
**Polls:** `/tmp/etr_shadow_snap/etr_shadow_polls.jsonl`

Hygiene track only — not a KEEP / expectancy claim.

| Asset | Events | Prices | Median ETR | Ref typical | Scale ratio | Basis guess |
|---|---:|---:|---:|---:|---:|---|
| btc | 1 | 114 | 6.351e+04 | 6e+04 | 1.059 | compatible_with_yf_continuous |
| gold | 0 | 109 | 4400 | 2400 | 1.834 | compatible_with_yf_continuous |
| nasdaq | 3 | 128 | 725.4 | 2e+04 | 0.03627 | etr_terminal_native |
| oil | 4 | 138 | 86.53 | 70 | 1.236 | compatible_with_yf_continuous |

## Notes

### btc
- median/typical ratio 1.059 within 0.5–2.0 band

### gold
- median/typical ratio 1.834 within 0.5–2.0 band

### nasdaq
- median/typical ratio 0.03627 — ETR levels are NOT on the same scale as NQ=F
- NASDAQ ETR ~hundreds vs NQ=F ~tens of thousands is expected if Terminal uses an index/CFD scale

### oil
- median/typical ratio 1.236 within 0.5–2.0 band

## Recommendation

1. Treat ETR shadow MFE/MAE as **terminal-native** unless basis_guess is `compatible_with_yf_continuous`.
2. Do not convert shadow outcomes into Branch B / broker P&L without an explicit per-asset mapping table checked into this folder.
3. Keep collecting shadow evidence; do not promote from thin N.
