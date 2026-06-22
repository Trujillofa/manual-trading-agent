# Post-Release Event Drift contract — 2026-06-19

## Premise

Trade short-horizon directional drift in major FX pairs after high-impact macro releases, using
**surprise** (Actual vs Forecast) as the signal. The edge source is scheduled information timing
and post-release price adjustment — not chart patterns, RSI, or directional TA.

This is the first strategy falsifier on the Event/Calendar lane after **DATA_PASS** on the pinned
HF snapshot (`EVENT_DATA_MANIFEST_2026-06-19.md`).

## Why this is not a closed lane

- FX directional TA: CLOSED (gross PF ~1.0–1.07).
- Daily TSMOM: CLOSED (gross PF 1.036).
- Carry (Hetzner cTrader): DISCARD (zero swaps).
- Daily stat-arb: DISCARD (OOS gates failed).

Event drift uses release surprise and a fixed post-event holding window. It does not reuse MTF RSI,
Donchian, ADX, or pairs z-score residuals.

## Look-ahead discipline (non-negotiable)

| Field | Use |
|---|---|
| `Forecast` | Pre-release scheduled value; safe for surprise sign at `datetime_utc` |
| `Previous` | Not used in this prototype |
| `Actual` | **Post-release label only** — compare to Forecast at or after `datetime_utc`; never before release |

Entry is fixed at `datetime_utc + 30 minutes`, so Actual is known before entry. This is intentional
post-release drift, not pre-release positioning.

## Event universe (fixed, no optimization)

High-impact indicator-class releases only:

| Family | Match rule (case-insensitive) |
|---|---|
| NFP | `Non-Farm Employment Change` or `Non-Farm Payrolls` (excludes ADP) |
| CPI | `CPI` or `Consumer Price Index` in title |
| GDP | `GDP` or `Gross Domestic Product` |
| PMI | `PMI` in title (high-impact rows only) |
| Rate decision | `Rate Decision`, `Interest Rate Decision`, `Official Bank Rate`, `FOMC` rate titles |

Additional filters:

- `Impact` contains `High Impact` (excludes Non-Economic).
- Currency in G8 map below.
- Both Actual and Forecast parse as numeric; surprise sign ≠ 0.
- Event `datetime_utc` within research window.

## Currency → pair map (fixed)

One liquid major pair per event currency:

| Currency | Pair | Event currency leg |
|---|---|---|
| USD | EUR/USD | quote |
| EUR | EUR/USD | base |
| GBP | GBP/USD | base |
| JPY | USD/JPY | quote |
| AUD | AUD/USD | base |
| CAD | USD/CAD | quote |
| CHF | USD/CHF | quote |
| NZD | NZD/USD | base |

Direction rule (fixed):

- Actual > Forecast → long event currency.
- Actual < Forecast → short event currency.
- Map to pair: **BUY** if event currency is base; **SELL** if quote.

## Timing (fixed, no optimization)

| Parameter | Value |
|---|---|
| Entry delay | 30 minutes after `datetime_utc` |
| Hold | 4 hours from entry |
| Exit | entry + 4 hours |

## Data required

| Requirement | Source | Notes |
|---|---|---|
| Event calendar | Pinned HF CSV + provenance SHA256 | `research/new_edge/events/data/pinned/` |
| Intraday OHLC | Dukascopy M1 → M15 resample | Per-event-day fetch with on-disk cache |
| Window | 2016-01-01 → 2025-04-07 | Overlaps manifest end date |

Production `NewsChecker` / faireconomy live parser work is **out of scope** for this research task.

## Cost model (net runs only; gross-first uses zero friction)

From data-proof spread widening model (`verify_event_data.py`):

- Base spread majors: 2.0 pips.
- Release window: 3× base spread for 15 minutes around release.
- Slippage: 1.0 pip per side during release window.
- Round-trip conservative: **14 pips** (entry + exit at widened spread).

Costs apply only if gross-first passes.

## First falsification test (gross-only)

For each eligible event:

1. Compute surprise sign from Actual vs Forecast (post-release label discipline).
2. Map currency → pair and trade direction.
3. Entry price: M15 **open** of first bar with `timestamp >= entry_time`.
4. Exit price: M15 **close** of last bar with `timestamp <= exit_time`.
5. Gross P&L in pips (zero spread/slippage).
6. Single parameter set; **no optimization or sweeps**.

**Falsified if:** pooled gross PF ≤ 1.05, or trade count < 30.

## Pass gate (gross-first)

- Pooled gross PF > 1.10 with ≥ 30 trades over full sample.
- Gross edge not concentrated in one calendar year (> 50% of gross profit from one year → stop).

## Pass gate (net / OOS — only after gross pass)

- Chronological 70/30 IS/OOS split on event time.
- OOS gross PF > 1.05 before costs.
- Net OOS PF ≥ 1.20 after 14-pip round-trip.
- OOS trade count ≥ 30.

## Stop gate

- Gross PF ≈ 1.0 → **DISCARD** lane at surprise-drift prototype stage.
- Net edge vanishes after release-window costs.
- Returns cluster in one episode (e.g. COVID March 2020 only).
- OHLC cache coverage too sparse to reach 30 trades → **BLOCKED** (data), not a strategy pass.

## First command

```bash
python -m research.new_edge.events.post_release_drift_test \
  --start 2016-01-01 --end 2025-04-07 \
  --calendar research/new_edge/events/data/pinned/forex_factory_calendar_hf_2026-06-18.csv \
  --output docs/research/events/EVENT_DRIFT_RESULTS_2026-06-19.md
```

## Verification status

**GROSS_PASS / NET DISCARD** (2026-06-19). See `docs/research/events/EVENT_DRIFT_RESULTS_2026-06-19.md`.

- Eligible events: 1,655; filled trades: 1,647 (8 OHLC skips).
- Pooled gross PF: **1.200** (passes gross-first gate).
- After 14-pip round-trip costs: IS net PF 0.256, OOS net PF 0.375 → **DISCARD** at net/OOS stage.
- Do not optimize or retune parameters. Lane falsified after costs.