# Repository Triage & Remediation Plan

**Date:** 2026-07-05 (updated 2026-07-06)
**Status:** Partially complete — PR triage resolved; CI and mypy remain open
**Scope:** Merged PRs (#30, #31, #32, #34), missing CI, type-check failures, prod redeploy

---

## Executive Summary

The repo is in solid shape — **379 tests pass**, ruff is clean. At triage time (2026-07-05)
four PRs were pending review; **all four are now merged** (2026-07-06). Remaining gaps:
**no CI workflow**, **24 mypy errors**, and **prod redeploy** to pick up log rotation
(PR #31). This plan records the original triage and updated follow-up actions.

---

## 1. PR Triage

All four PRs from the original triage are **merged** as of 2026-07-06. No open PRs
remain from this batch.

### PR #31 — `fix(runtime): version scanner log rotation` → ✅ MERGED (2026-07-06)

| Field | Value |
|-------|-------|
| **Branch** | `fix/runtime-log-rotation-2026-07` |
| **Changes** | 1 commit — replaces inline entrypoint with `scripts/run_scanner_loop.sh`, adds bounded log rotation |
| **Impact** | **Fixes the unbounded log growth issue** (Issue 4 below) |

**Assessment:** Highest-value item from the original triage. Introduces a proper shell
entrypoint (`run_scanner_loop.sh`) that rotates `scan.log` and `signal_audit.jsonl` at
50 MiB, retaining 25 MiB. At triage time the prod container had no rotation — scan.log
was 2.7 MB and signal_audit.jsonl was 5.7 MB after just 3 days.

**Action:** ~~Review and merge.~~ **Done.** Redeploy the prod container and verify
rotation after one week (see Verification Checklist).

---

### PR #30 — `feat(research): archive HTF Fibonacci negative result` → ✅ MERGED (2026-07-06)

| Field | Value |
|-------|-------|
| **Branch** | `research/archive-htf-fib-2026-07` |
| **Changes** | 3 commits — archives the HTF Fib research lane as a negative result, adds Pine scripts + backtest tooling |
| **Impact** | Documentation + research tooling |

**Assessment:** Clean research-lane closure. The HTF Fib strategy was backtested and
discarded. This PR records the decision and preserves the tooling. Low risk.

**Action:** ~~Merge after CI is set up.~~ **Done.**

---

### PR #32 — `research(term-structure): source gate BLOCKED decision` → ✅ MERGED (2026-07-06)

| Field | Value |
|-------|-------|
| **Branch** | `research/term-structure-source-gate-2026-07` |
| **Changes** | 1 commit — documents that the term-structure lane is BLOCKED on data source availability (CME free data audit) |
| **Impact** | Documentation only |

**Assessment:** Pure docs. Records a data-source gate decision with provenance JSON.
No code changes.

**Action:** ~~Merge (docs-only, no risk).~~ **Done.**

---

### PR #34 — `research: define PEAD data-proof lane` → ✅ MERGED (2026-07-06)

| Field | Value |
|-------|-------|
| **Branch** | `docs/pead-lane-contract-2026-07` |
| **Changes** | Defines a new research lane for Post-Earnings-Announcement Drift in US equities |
| **Impact** | Documentation only — `CONTRACT_DEFINED` in research ledger |
| **Status** | Merged (was draft at triage time) |

**Action:** ~~Review the research contract.~~ **Done.** Next permitted step per contract:
read-only `verify_pead_data` source audit — no relationship or strategy code until
`DATA_PASS`.

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

**Assessment:** These are type-safety issues — some reflect missing `None` guards or
TypedDict keys that don't exist; others may be incomplete or incorrect annotations
rather than guaranteed runtime faults. Fix with explicit guards/casts or type-definition
corrections; some paths may be latent runtime risks if hit in production.

**Action:** Fix in a dedicated `fix/mypy-errors` branch. Should be done alongside or
after the CI workflow (so CI catches regressions).

---

## 4. Production Log Growth → ✅ MERGED — redeploy pending

| Field | Value |
|-------|-------|
| **Symptom** | scan.log (2.7 MB) + signal_audit.jsonl (5.7 MB) growing unbounded |
| **Container uptime** | 3 days (since 2026-07-02, at triage time) |
| **Rate** | ~2.8 MB/day combined |
| **Fix** | PR #31 merged — `run_scanner_loop.sh` rotates at 50 MiB, retains 25 MiB |

**Assessment:** Not critical at triage time (8.4 MB total), but would grow indefinitely
without rotation. Code fix is on `main`; prod must redeploy to pick it up.

**Action:** Redeploy prod container and confirm `scan.log` / `signal_audit.jsonl` stay
bounded after one week.

---

## Summary Action Matrix

| Priority | Item | Type | Status |
|----------|------|------|--------|
| **P0** | Add CI workflow (`.github/workflows/ci.yml`) | New PR | Open |
| **P1** | Fix 24 mypy errors | New PR (`fix/mypy-errors`) | Open |
| **P1** | Redeploy prod for log rotation (PR #31) | Ops | Open |
| ~~P1~~ | ~~Merge PR #31 (log rotation)~~ | ~~Review + merge~~ | **Done** (2026-07-06) |
| ~~P2~~ | ~~Merge PR #30 (HTF Fib archive)~~ | ~~Review + merge~~ | **Done** (2026-07-06) |
| ~~P2~~ | ~~Merge PR #32 (term structure docs)~~ | ~~Review + merge~~ | **Done** (2026-07-06) |
| ~~P3~~ | ~~Review PR #34 (PEAD draft)~~ | ~~Decision~~ | **Done** (2026-07-06) |

---

## Verification Checklist

- [ ] CI workflow created and passing
- [x] PR #31 merged (2026-07-06)
- [ ] PR #31 redeployed to prod
- [ ] mypy errors resolved (0 errors on `mypy src/`)
- [x] PR #30 merged (2026-07-06)
- [x] PR #32 merged (2026-07-06)
- [x] PR #34 merged (2026-07-06)
- [ ] Log rotation verified in prod (scan.log < 50 MiB after 1 week)
- [ ] PEAD `verify_pead_data` source audit started (post-#34 contract)
