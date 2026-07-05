# Repository Triage & Remediation Plan

**Date:** 2026-07-05
**Status:** Proposed — awaiting approval
**Scope:** Open PRs (#30, #31, #32, #34), missing CI, type-check failures, prod log growth

---

## Executive Summary

The repo is in solid shape — **379 tests pass**, ruff is clean. However there are
4 open PRs pending review, **no CI workflow**, **24 mypy errors** in the codebase,
and a **production log growth issue** (scan.log + signal_audit.jsonl growing
unbounded at 8.4 MB in 3 days). This plan proposes actions for each.

---

## 1. Open PR Triage

### PR #31 — `fix(runtime): version scanner log rotation` → ✅ MERGE

| Field | Value |
|-------|-------|
| **Branch** | `fix/runtime-log-rotation-2026-07` |
| **Changes** | 1 commit — replaces inline entrypoint with `scripts/run_scanner_loop.sh`, adds bounded log rotation |
| **Impact** | **Fixes the unbounded log growth issue** (Issue 4 below) |

**Assessment:** This is the highest-value PR. It introduces a proper shell entrypoint
(`run_scanner_loop.sh`) that rotates `scan.log` and `signal_audit.jsonl` at 50 MiB,
retaining 25 MiB. The current prod container has no rotation at all — scan.log is 2.7 MB
and signal_audit.jsonl is 5.7 MB after just 3 days.

**Action:** Review and merge. Then redeploy the container.

---

### PR #30 — `feat(research): archive HTF Fibonacci negative result` → ✅ MERGE

| Field | Value |
|-------|-------|
| **Branch** | `research/archive-htf-fib-2026-07` |
| **Changes** | 3 commits — archives the HTF Fib research lane as a negative result, adds Pine scripts + backtest tooling |
| **Impact** | Documentation + research tooling |

**Assessment:** Clean research-lane closure. The HTF Fib strategy was backtested and
discarded. This PR records the decision and preserves the tooling. Low risk.

**Action:** Merge after CI is set up (or merge directly if research PRs are exempt).

---

### PR #32 — `research(term-structure): source gate BLOCKED decision` → ✅ MERGE

| Field | Value |
|-------|-------|
| **Branch** | `research/term-structure-source-gate-2026-07` |
| **Changes** | 1 commit — documents that the term-structure lane is BLOCKED on data source availability (CME free data audit) |
| **Impact** | Documentation only |

**Assessment:** Pure docs. Records a data-source gate decision with provenance JSON.
No code changes.

**Action:** Merge (docs-only, no risk).

---

### PR #34 — `research: define PEAD data-proof lane` → 🔲 DRAFT

| Field | Value |
|-------|-------|
| **Branch** | `docs/pead-lane-contract-2026-07` |
| **Changes** | 1 commit — defines a new research lane for Post-Earnings-Announcement Drift in FX |
| **Impact** | Documentation only (draft) |
| **Status** | **Draft PR** |

**Action:** Review the research contract. Mark ready for review or keep as draft until
the PEAD data audit is complete.

---

## 2. Missing CI Workflow → 🔴 NEW ISSUE

| Field | Value |
|-------|-------|
| **Current state** | **No `.github/workflows/` directory exists** |
| **Impact** | No automated test/lint/type-check gates on any PR or push |

**Evidence:**
```
$ ls .github/workflows/
ls: cannot access '.github/workflows/': No such file or directory
```

**Assessment:** This is the most significant gap. The repo has 379 tests, ruff config,
and mypy config in `pyproject.toml` — but nothing runs them on push. Every PR currently
relies on Copilot Code Review only (which is a reviewer, not a gate).

**Proposed Fix:** Add `.github/workflows/ci.yml` with 3 jobs:
1. **Lint** — `ruff check src/ tests/`
2. **Typecheck** — `mypy src/`
3. **Test** — `pytest tests/ -v --tb=short --cov=src`

Trigger: `push` to `main`, `pull_request` to `main`.

**Action:** Create a new PR adding the CI workflow.

---

## 3. Type-Check Failures (24 mypy errors) → 🔴 FIX NEEDED

| Field | Value |
|-------|-------|
| **Files affected** | `src/scanner/evaluator.py` (2 errors), `src/cli.py` (5+ errors), 2 others |
| **Error types** | `[operator]`, `[arg-type]`, `[union-attr]`, `[typeddict-item]`, `[assignment]` |

**Sample errors:**
```
src/scanner/evaluator.py:434: error: Unsupported operand types for > ("float" and "None") [operator]
src/cli.py:1309: error: Item "None" of "NearStateRecord | None" has no attribute "get" [union-attr]
src/cli.py:1369: error: TypedDict "NearCandidate" has no key "symbol" [typeddict-item]
```

**Assessment:** These are real type bugs — `None` not being handled in comparison
branches, TypedDict keys being accessed that don't exist. They haven't caused runtime
crashes because those code paths may not be hit often, but they're latent bugs.

**Action:** Fix in a dedicated `fix/mypy-errors` branch. Should be done alongside or
after the CI workflow (so CI catches regressions).

---

## 4. Production Log Growth → ✅ FIXED BY PR #31

| Field | Value |
|-------|-------|
| **Symptom** | scan.log (2.7 MB) + signal_audit.jsonl (5.7 MB) growing unbounded |
| **Container uptime** | 3 days (since 2026-07-02) |
| **Rate** | ~2.8 MB/day combined |
| **Fix** | PR #31 adds bounded rotation (50 MiB threshold, 25 MiB retain) |

**Assessment:** Not critical yet (8.4 MB total), but will grow indefinitely without
PR #31. Over months this would fill the disk.

**Action:** Merge PR #31 and redeploy.

---

## Summary Action Matrix

| Priority | Item | Type | Effort |
|----------|------|------|--------|
| **P0** | Add CI workflow (`.github/workflows/ci.yml`) | New PR | 1 session |
| **P1** | Merge PR #31 (log rotation) | Review + merge | 10 min |
| **P1** | Fix 24 mypy errors | New PR (`fix/mypy-errors`) | 1 session |
| **P2** | Merge PR #30 (HTF Fib archive) | Review + merge | 10 min |
| **P2** | Merge PR #32 (term structure docs) | Review + merge | 5 min |
| **P3** | Review PR #34 (PEAD draft) | Decision | 10 min |

---

## Verification Checklist

- [ ] CI workflow created and passing
- [ ] PR #31 merged and container redeployed
- [ ] mypy errors resolved (0 errors on `mypy src/`)
- [ ] PRs #30, #32 merged
- [ ] PR #34 reviewed (ready or stay draft)
- [ ] Log rotation verified in prod (scan.log < 50 MiB after 1 week)
