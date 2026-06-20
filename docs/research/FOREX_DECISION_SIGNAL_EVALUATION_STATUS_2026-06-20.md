# Forex DecisionSignal Infrastructure — Status Update (2026-06-20)

## Summary

The forex-only `DecisionSignal` observability layer is **implemented and wired** through PRs #13–#17.
Runtime smoke confirms `scan_telemetry` continues to append per pair while `decision_signal` rows are
correctly suppressed when all pairs resolve to `neutral`. Positive recordable-state writes are
covered by unit tests. No outcome evaluator exists yet.

Contract reference: `docs/research/FOREX_DECISION_SIGNAL_EVALUATION_CONTRACT_2026-06-20.md`

## PR Sequence (merged)

| PR | Title | Deliverable |
|---|---|---|
| [#13](https://github.com/Trujillofa/manual-trading-agent/pull/13) | docs: plan forex signal outcome evaluation layer | Infrastructure contract (`FOREX_DECISION_SIGNAL_EVALUATION_CONTRACT_2026-06-20.md`) |
| [#14](https://github.com/Trujillofa/manual-trading-agent/pull/14) | feat(evaluation): DecisionSignal JSONL schema validation | `src/evaluation/decision_signal_schema.py` — Pydantic v1 schema, `validate_decision_signal()`, `validate_decision_signal_jsonl()` |
| [#15](https://github.com/Trujillofa/manual-trading-agent/pull/15) | feat(evaluation): append validated DecisionSignal audit rows | `record_decision_signal()` — validated append-only writes to `logs/signal_audit.jsonl` |
| [#16](https://github.com/Trujillofa/manual-trading-agent/pull/16) | feat(evaluation): build Branch B DecisionSignal payloads | `src/evaluation/branch_b_decision_signal.py` — pure `build_branch_b_decision_signal()` mapper (no I/O) |
| [#17](https://github.com/Trujillofa/manual-trading-agent/pull/17) | feat(evaluation): record Branch B DecisionSignal audit rows | `src/evaluation/branch_b_audit.py` + scanner wiring in `src/cli.py` |

### Module map

```
src/evaluation/
├── decision_signal_schema.py   # schema + validate + record + JSONL validator
├── branch_b_decision_signal.py # Branch B context → DecisionSignal payload
└── branch_b_audit.py           # scan telemetry → record_decision_signal()

tests/
├── test_decision_signal_schema.py
├── test_branch_b_decision_signal.py
└── test_branch_b_audit.py
```

## Runtime Smoke Evidence

### Command

```bash
.venv/bin/python -m src.cli scan
```

Full watchlist scan (27 pairs per `config/settings.yaml`; EUR/GBP excluded).

### Audit row counts (latest scan run)

| `kind` | Rows | Notes |
|---|---:|---|
| `scan_telemetry` | 27 | One row per pair for `scan_run_id=2026-06-20T17:53:59.844046+00:00` |
| `decision_signal` | 0 | All 27 pairs resolved to `state=neutral` |

### Why zero `decision_signal` rows

`record_branch_b_scan_decision_signal()` only appends for recordable states:

- `entry` → `action=alert`
- `watch` → `action=watch`
- `aligned_pending_breakout` → `action=watch`
- `blocked` → `action=avoid`

`neutral` (and `data_unavailable`) are intentionally skipped. A full-watchlist scan where every pair
is neutral therefore produces telemetry only — this is expected gating, not a wiring failure.

### JSONL validation

```bash
.venv/bin/python -c "
from pathlib import Path
from src.evaluation.decision_signal_schema import validate_decision_signal_jsonl
report = validate_decision_signal_jsonl(Path('logs/signal_audit.jsonl'))
print(report)
"
```

Result at smoke time:

```
ok=True
validated_signals=0
skipped_rows=27651
errors=0
```

Non-`decision_signal` rows (legacy `scan_telemetry`, `alert_outcome`, etc.) are skipped by design.

### Existing summarizer

```bash
.venv/bin/python -m scripts.summarize_alerts --days 14 --format table
```

Runs successfully against the mixed audit log. No changes required for infrastructure smoke.

## Current Status

| Area | State |
|---|---|
| Infrastructure wired | ✅ PRs #13–#17 merged; `src/cli.py` calls `record_branch_b_scan_decision_signal()` after `scan_telemetry` append |
| Neutral gating at runtime | ✅ Verified — 27/27 neutral → 0 `decision_signal` rows |
| Recordable-state writes | ✅ Unit tests cover `entry`, `watch`, `aligned_pending_breakout`, `blocked` |
| Schema validation | ✅ 66 tests pass across `test_decision_signal_schema`, `test_branch_b_decision_signal`, `test_branch_b_audit` |
| Evaluator purity preserved | ✅ `src/scanner/evaluator.py` unchanged; no evaluation imports in reverse |
| Outcome evaluator | ❌ Not started (`decision_signal_outcome` sidecar per contract) |

## Explicit Non-Claims

This status update and the merged infrastructure **do not** assert:

- Any profitability or validated edge
- A new alpha research lane
- An outcome evaluator (1h / 4h / 1d fixed horizons)
- Changes to Branch B operating posture — it remains **observability / manual-alert only**

All closed alpha lanes in `docs/research/CLOSED_RESEARCH_LANES.md` stay closed. Recording
`DecisionSignal` rows does not reopen FX directional TA or any discarded lane.

## Recommended Next Gate

1. **Wait for a natural recordable Branch B state** — an `entry`, `watch`, `aligned_pending_breakout`,
   or `blocked` pair during normal scanning — and confirm a `decision_signal` row appears in
   `logs/signal_audit.jsonl` with `engine_version=forex-decision-signal-v1`.
2. **Alternatively**, run a controlled fixture/smoke later (test harness or injected scan context) if
   market conditions remain all-neutral for an extended window.
3. **Only after real `decision_signal` rows exist** should outcome-evaluation planning begin
   (`run_outcomes()`, `decision_signal_outcome` sidecar, cohort stats in `summarize_alerts.py`).

Do not tune scanner gates or alert frequency to force recordable states for infrastructure validation.

## Revision History

| Date | Change |
|---|---|
| 2026-06-20 | Initial infrastructure status after PRs #13–#17 merge and runtime smoke |