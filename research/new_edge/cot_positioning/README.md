# COT positioning lane

This package contains the isolated listed-futures COT research lane.

Current scope is data proof only:

```bash
python -m research.new_edge.cot_positioning.data.verify_cot_data
```

The verifier reads the official CFTC PRE Legacy Futures Only dataset, checks the
fixed 23-market universe, writes a Markdown manifest and a machine-readable
provenance record, and returns:

- exit `0` for `DATA_PASS`;
- exit `2` for `BLOCKED`.

See `docs/research/cot_positioning/COT_CONTRACT_2026-06.md`. Do not add a strategy,
backtest, or classifier in this package until the required relationship-test stage
has passed.
