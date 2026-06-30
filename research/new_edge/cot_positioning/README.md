# COT positioning lane

This package contains the isolated listed-futures COT research lane.

The data-proof stage remains reproducible with:

```bash
python -m research.new_edge.cot_positioning.data.verify_cot_data
```

The verifier reads the official CFTC PRE Legacy Futures Only dataset, checks the
fixed 23-market universe, writes a Markdown manifest and a machine-readable
provenance record, and returns:

- exit `0` for `DATA_PASS`;
- exit `2` for `BLOCKED`.

The fixed relationship test is:

```bash
python -m research.new_edge.cot_positioning.relationship \
  --start 2010-01-01 \
  --end 2026-06-16
```

It applies verified release-date controls, excludes unsafe delayed-report
periods, uses one chronological 65/35 holdout, and returns:

- exit `0` for `RELATIONSHIP_PASS`;
- exit `2` for `RELATIONSHIP_FAIL`.

The recorded result is `RELATIONSHIP_FAIL`: the expected weak reversal in IS
changed sign OOS, and seven binding gates failed. See
`docs/research/cot_positioning/COT_RELATIONSHIP_RESULTS_2026-06.md`.

Do not add a COT reversal strategy, classifier, horizon variant, or threshold
search to this package. The fixed premise is closed.
