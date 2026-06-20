# FX Benchmark / Fixing-Flow Anomaly contract — 2026-06-20

## Premise

Test whether known institutional FX benchmark fixing windows create short-lived, tradable price
pressure that is distinct from generic directional technical analysis.

Pension funds, index trackers, and corporate hedgers often route orders through daily benchmark
rates (notably the London 4pm WM/Reuters fix and the Tokyo morning USD/JPY fix). Flows can cluster
in the minutes before the fix and may produce continuation or reversal effects that are tied to a
**calendar clock**, not to RSI, Donchian channels, or session-open range breaks.

This is a gross-first falsifier. It is not a production strategy, not a parameter search, and not a
reopen of any closed lane.

## Why this is not a closed lane

Closed lanes remain closed:

| Closed lane | Why fix-flow differs |
|---|---|
| FX directional TA (M15/H1 OHLC) | No MTF RSI, SMA, ADX, Donchian, ORB, or trend-pullback stack. Entry is keyed to a **fixed benchmark clock**, not chart pattern state. |
| Daily TSMOM | No 252-bar momentum ranking or inverse-vol portfolio. Hold is minutes, not days. |
| Carry / swap (Hetzner cTrader) | No financing or rollover signal. Price-only OHLC around fix times. |
| Daily FX stat-arb | No pairs z-score or hedge-ratio residuals. Single-leg outright trades per pair. |
| Event surprise drift | No Actual/Forecast surprise, no macro release calendar, no post-NFP/CPI timing. Fix windows are **recurring daily clocks**, not scheduled data releases. |
| Vol-regime compression breakout | No H1 range-percentile compression filter. Breakout thesis is institutional flow timing, not volatility-regime expansion after quiet ranges. |

The edge source is **benchmark/fixing-flow timing**. If this lane fails, it does not license retuning
any closed lane or Branch B live gates.

## Fixed universe

Seven FX majors only:

- EUR/USD
- GBP/USD
- USD/JPY
- AUD/USD
- USD/CAD
- USD/CHF
- NZD/USD

Do not pivot to metals, indices, exotics, or crosses in this contract.

## Fixing windows (fixed, no optimization)

Two window families. Each uses the same entry/exit rule below; only the anchor clock and eligible
pairs differ.

### 1. London 16:00 fix window (all seven pairs)

| Field | Value |
|---|---|
| Local anchor | 16:00 `Europe/London` |
| UTC conversion | Resolve daily: `datetime(YYYY, MM, DD, 16, 0, tzinfo=ZoneInfo("Europe/London")).astimezone(UTC)` |
| DST note | Anchor stays 16:00 London local; UTC offset shifts with GMT/BST (typically 16:00 UTC in winter, 15:00 UTC in summer) |
| Eligible pairs | All seven majors |

### 2. Tokyo JPY fix window (USD/JPY only)

| Field | Value |
|---|---|
| Local anchor | 09:55 `Asia/Tokyo` |
| UTC conversion | Resolve daily: `datetime(YYYY, MM, DD, 9, 55, tzinfo=ZoneInfo("Asia/Tokyo")).astimezone(UTC)` → **00:55 UTC** |
| DST note | Japan has no DST; offset is fixed UTC+9 |
| Eligible pairs | USD/JPY only |
| Inclusion rule | Include this window **only if** the data verifier confirms ≥95% weekday anchor coverage with unambiguous UTC bar alignment. If verification fails → run London-only and mark Tokyo as `BLOCKED` (data), not a strategy pass. |

### 3. Month-end subset (fixed tag, not optimization)

Tag each trade at entry with `month_end=true` when the entry UTC calendar date is one of the **last
two business days** (Mon–Fri) of the calendar month.

- This tag is for **reporting and concentration checks only**.
- The primary pooled gates use **all trades** (tagged and untagged).
- Do not filter, weight, or optimize on month-end. No separate pass gate for month-end-only runs.

## Fixed prototype

One pre-defined rule. No parameter sweeps, no alternate holds, no alternate directions, no alternate
windows.

### Timeframe

M15 OHLC bars.

### Direction signal (fixed)

Pre-fix **momentum continuation**:

- Let `fix_utc` = daily anchor instant for the window family.
- Compare two prior M15 bar **closes**:
  - `c_near` = close of the bar whose timestamp is the last bar **strictly before** `fix_utc - 30 minutes`.
  - `c_far` = close of the bar whose timestamp is the last bar **strictly before** `fix_utc - 45 minutes`.
- If `c_near > c_far` → **BUY**.
- If `c_near < c_far` → **SELL**.
- If `c_near == c_far` → **no trade** (skip day).

### Entry (fixed)

- Entry time: `fix_utc - 15 minutes`.
- Entry price: M15 **open** of the first bar with `bar_open >= entry_time`.

### Exit (fixed)

- Hold: **60 minutes** from entry.
- Exit time: `entry_time + 60 minutes`.
- Exit price: M15 **close** of the last bar with `bar_close <= exit_time`.

### Trade cardinality

- At most **one trade per pair per window family per UTC calendar day**.
- London and Tokyo windows on the same day for USD/JPY are **separate** trades (different anchors).
- No pyramiding. No stop-loss. No take-profit. No trailing exit.

## Data requirements

| Requirement | Source | Notes |
|---|---|---|
| Intraday OHLC | Dukascopy M1 → M15 resample via existing project helpers | Preferred path |
| Window | 2016-01-01 → 2026-06-01 | Inclusive start, exclusive or inclusive end per verifier convention |
| Bar timestamps | UTC | All bar open/close times stored and compared in UTC after conversion |
| Timezone anchors | `zoneinfo` (`Europe/London`, `Asia/Tokyo`) | Document resolved UTC instants in data manifest |
| Tick data | **Out of scope v0** | OHLC only |
| Calendar / news | **Out of scope** | No Forex Factory, no Actual/Forecast |
| Swaps / carry | **Out of scope** | No financing fields |

### UTC conversion discipline (non-negotiable)

1. Dukascopy M1 timestamps are treated as UTC (existing project convention).
2. Fix anchors are computed in local timezone, then converted to UTC per calendar day.
3. M15 resample boundaries must be documented in the data manifest (open vs close labeling).
4. Weekday-only: skip Saturday/Sunday anchors (no synthetic weekend fixes).
5. If a required entry or exit bar is missing → skip that day/pair (do not interpolate).

Production `NewsChecker`, live spread feeds, and tick data are **out of scope** for this research task.

## Cost model (net runs only; gross-first uses zero friction)

Gross-first run: zero spread, zero slippage.

Only if gross passes, apply conservative FX round-trip costs on net/OOS:

| Component | Value |
|---|---|
| Base spread | 2.0 pips per side |
| Slippage | 1.0 pip per side |
| Total round trip | **6.0 pips** |

No release-window widening (this is not an event-surprise lane). Costs apply equally to London and
Tokyo windows.

## Pass and stop gates

### Gross-first gate

Pass only if **all** are true on the pooled sample (all windows, all eligible pairs):

- Pooled gross PF **> 1.10**
- Pooled trades **≥ 30**
- No single calendar year contributes **> 50%** of gross profit

Immediate **DISCARD** if:

- Pooled gross PF **≤ 1.05**
- Trades **< 30**
- Passing would require retuning windows, hold period, pairs, direction rule, or month-end tag definition
- Edge is visible in only one pair or only one calendar year (concentration failure)

If gross PF is > 1.05 and ≤ 1.10, result is still **DISCARD** unless a documented data issue makes
the run **BLOCKED**.

### Net/OOS gate (only after gross pass)

Run only after gross-first pass:

- Chronological **70/30** split by entry time
- OOS gross PF **> 1.05** (before costs)
- OOS net PF **≥ 1.20** after 6-pip round-trip cost
- OOS trades **≥ 30**

Final status conventions:

- `CONTRACT_DRAFT`: this document only; no implementation run yet
- `GROSS_PASS`: gross gate passed; net/OOS work remains or has not yet run
- `DISCARD`: gross gate failed or net/OOS failed
- `BLOCKED`: data coverage or reproducibility prevents a valid run

## Stop rules (no retuning)

After the first fixed-prototype run:

- **DISCARD** if gross PF ≤ 1.05
- **DISCARD** if edge is one pair/year only
- **Do not** retune fix windows, hold period, pairs, direction rule, or month-end tag
- **Do not** add RSI, Donchian, ORB, ADX, SMA, or session filters to rescue a failed run
- **Do not** claim profitability before net/OOS gates pass

## Explicit non-goals

- No RSI, MTF alignment, or RSI-MA gates
- No Donchian, ORB, or trend-pullback variants
- No carry, swap, or funding signals
- No event surprise drift or macro calendar logic
- No Branch B live evaluator or DecisionSignal outcome evaluator
- No Branch B tuning or promotion-table overrides
- No tick data, order-book data, or microstructure filters in v0
- No parameter sweeps, walk-forward optimizers, or "one more variant" after a failed fixed run
- No profitability claim before net/OOS gates pass

## Required deliverables (implementation phase — not in this PR)

This PR is **contract only**. A follow-up implementation PR must produce:

1. Data proof:
   `docs/research/fix_flow/FIX_FLOW_DATA_MANIFEST_2026-06-20.md`
2. Falsifier:
   `research/new_edge/fix_flow/fixing_flow_test.py`
3. Results:
   `docs/research/fix_flow/FIX_FLOW_RESULTS_2026-06-20.md`
4. Ledger row:
   `research/new_edge/research_ledger.jsonl`
5. Tests:
   `tests/test_fixing_flow.py`

## Planned verification commands (implementation phase)

```bash
python -m research.new_edge.fix_flow.data.verify_fix_flow_data \
  --start 2016-01-01 --end 2026-06-01 \
  --output docs/research/fix_flow/FIX_FLOW_DATA_MANIFEST_2026-06-20.md

python -m research.new_edge.fix_flow.fixing_flow_test \
  --start 2016-01-01 --end 2026-06-01 \
  --output docs/research/fix_flow/FIX_FLOW_RESULTS_2026-06-20.md

pytest tests/test_fixing_flow.py -v --tb=short
ruff check research/new_edge/fix_flow/ tests/test_fixing_flow.py
```

## Ledger row template

```json
{
  "ts": "<ISO8601>",
  "lane": "fix_flow",
  "hypothesis": "M15 pre-fix momentum continuation at London 16:00 and Tokyo 09:55 anchors",
  "status": "<CONTRACT_DRAFT|GROSS_PASS|DISCARD|BLOCKED>",
  "branch": "docs/profitability-plan-2026-06",
  "command": "python -m research.new_edge.fix_flow.fixing_flow_test --start 2016-01-01 --end 2026-06-01 --output docs/research/fix_flow/FIX_FLOW_RESULTS_2026-06-20.md",
  "data_start": "2016-01-01",
  "data_end": "2026-06-01",
  "gross_pf": 0.0,
  "net_pf": 0.0,
  "oos_pf": 0.0,
  "oos_return_pct": 0.0,
  "trades_or_events": 0,
  "max_drawdown_pct": 0.0,
  "result_doc": "docs/research/fix_flow/FIX_FLOW_CONTRACT_2026-06-20.md",
  "failure_reason": "N/A - contract only"
}
```

## Verification status

**CONTRACT_DRAFT** (2026-06-20). No data proof or falsifier run yet. Implementation PR follows this
contract verbatim.