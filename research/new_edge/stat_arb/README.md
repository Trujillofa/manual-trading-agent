# Stat-Arb Research Lane

FX pairs statistical arbitrage — relative mispricing, not directional TA.

## Contract

`docs/research/stat_arb/STAT_ARB_CONTRACT_2026-06-18.md`

## Commands

```bash
# 1. Verify data
python -m research.new_edge.stat_arb.data.verify_stat_arb_data \
  --start 2016-01-01 --end 2026-06-01 \
  --output docs/research/stat_arb/STAT_ARB_DATA_MANIFEST_2026-06-18.md

# 2. Gross-first falsifier (zero friction)
python -m research.new_edge.stat_arb.gross_stat_arb_test \
  --start 2016-01-01 --end 2026-06-01 \
  --output docs/research/stat_arb/STAT_ARB_GROSS_RESULTS_2026-06-18.md
```

## Status

See `research/new_edge/research_ledger.jsonl` for latest verdict.