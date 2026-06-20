# Grok handoff: Volatility Regime / Range Compression Breakout

## Goal

Implement the fixed gross-first falsifier defined in:

`docs/research/vol_regime/VOL_REGIME_CONTRACT_2026-06-19.md`

Test whether compressed H1 FX ranges followed by volatility expansion produce a tradable gross
edge. This is plan item #4 after the event surprise-drift lane was discarded at net/OOS.

## Current state

Do not reopen or retune these lanes:

- FX directional TA: CLOSED.
- Daily TSMOM: CLOSED.
- Carry on Hetzner cTrader: CLOSED_DISCARD.
- Daily FX stat-arb: DISCARD.
- Event surprise drift: DISCARD at net/OOS.

Production NewsChecker / faireconomy XML repair is out of scope.

## Implementation rules

- Contract already exists; implement against it.
- One fixed parameter set only.
- No optimization, sweeps, alternate thresholds, alternate sessions, or alternate exits.
- Existing OHLC only.
- Gross-first falsifier first.
- Add costs and chronological 70/30 IS/OOS only if gross passes.
- Preserve unrelated untracked files and dirty worktree changes.

## Required deliverables

- `docs/research/vol_regime/VOL_REGIME_DATA_MANIFEST_2026-06-19.md`
- `research/new_edge/vol_regime/data/verify_vol_regime_data.py`
- `research/new_edge/vol_regime/range_compression_breakout_test.py`
- `docs/research/vol_regime/VOL_REGIME_RESULTS_2026-06-19.md`
- `research/new_edge/research_ledger.jsonl` row with `GROSS_PASS`, `DISCARD`, or `BLOCKED`
- `tests/test_vol_regime_breakout.py`

## Gates

Gross pass:

- Pooled gross PF > 1.10.
- Pooled trades >= 30.
- Max one-year gross profit concentration <= 50%.

Discard:

- Pooled gross PF <= 1.05.
- Trades < 30.
- Requires tuning to pass.

Net/OOS after gross pass:

- Chronological 70/30 split.
- OOS gross PF > 1.05.
- OOS net PF >= 1.20 after 6-pip round-trip cost.
- OOS trades >= 30.

## Verification commands

```bash
python -m research.new_edge.vol_regime.data.verify_vol_regime_data \
  --start 2016-01-01 --end 2026-06-01 \
  --output docs/research/vol_regime/VOL_REGIME_DATA_MANIFEST_2026-06-19.md

python -m research.new_edge.vol_regime.range_compression_breakout_test \
  --start 2016-01-01 --end 2026-06-01 \
  --output docs/research/vol_regime/VOL_REGIME_RESULTS_2026-06-19.md

pytest tests/test_vol_regime_breakout.py -v --tb=short
ruff check research/new_edge/vol_regime/ tests/test_vol_regime_breakout.py
```

## PR response checklist

The implementation PR body should include:

- Commands run and their output summary.
- Final verdict.
- Pooled gross PF and trade count.
- Net/OOS metrics if gross passed.
- Ledger row status.
- Explicit note that no parameter sweep or closed-lane retune was performed.

