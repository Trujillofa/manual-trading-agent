# ETR shadow price-basis audit

**Status:** research hygiene (not a KEEP lane)  
**Program:** `docs/research/RESEARCH_LANES_PROGRAM_2026-08.md`

## Why

Live ETR shadow events use ETR-reported prices/zones. For NASDAQ, observed levels are ~725 while Branch B yfinance `NQ=F` is ~30k. Without a documented mapping, MFE/MAE and TP1/invalidation outcomes are not comparable to broker/futures prices.

## Commands

```bash
# Against local/prod-copied logs (default paths under logs/)
.venv/bin/python -m research.new_edge.etr_shadow.audit_price_basis \
  --events logs/etr_shadow_events.jsonl \
  --polls logs/etr_shadow_polls.jsonl \
  --open logs/etr_shadow_open.json \
  --output docs/research/etr_shadow/ETR_SHADOW_PRICE_BASIS_AUDIT_2026-08.md
```

## Pass criteria for this hygiene track

1. Every asset has an explicit **price basis** label (`etr_terminal`, `yf_continuous`, `unknown`).
2. Scale ratio vs reference (if available) is reported; ratios ≫1 or ≪1 are flagged.
3. Written recommendation: keep shadow as **terminal-native evidence only**, or define a conversion — never silently mix bases in P&L claims.
