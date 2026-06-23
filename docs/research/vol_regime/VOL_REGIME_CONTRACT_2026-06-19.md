# Volatility Regime / Range Compression Breakout contract — 2026-06-19

## Premise

Test whether unusually compressed H1 FX ranges tend to expand into a tradable breakout.
The edge source is volatility regime transition: quiet range compression first, directional
breakout second.

This is a gross-first falsifier. It is not a production strategy, not a parameter search, and
not a reopen of closed FX directional TA.

## Why this is not a closed lane

Closed lanes remain closed:

- FX directional TA: CLOSED. Do not retune MTF RSI, ORB, trend-pullback, Donchian, SMA, ADX,
  or M15 intraday pattern stacks.
- Daily TSMOM: CLOSED.
- Carry on Hetzner cTrader: CLOSED_DISCARD because the resolved account swaps were zero.
- Daily FX stat-arb: DISCARD because OOS gates failed.
- Event surprise drift: DISCARD after gross pass but net/OOS failure.

This lane differs because a breakout can only occur after a documented bottom-decile compression
regime. The hypothesis is expansion after compression, not unconditional directional breakout.

| Closed lane | This lane |
|---|---|
| ORB: session-open range break, no compression filter | Requires multi-bar compression before arming |
| Donchian: unconditional channel break | Break only after bottom-decile H1 range |
| MTF RSI / ADX / SMA gates | No RSI, no MTF alignment, no ADX |
| M15 intraday pattern stack | H1 volatility-regime thesis |

## Fixed universe

Seven FX majors:

- EUR/USD
- GBP/USD
- USD/JPY
- AUD/USD
- USD/CAD
- USD/CHF
- NZD/USD

Do not pivot to metals, indices, exotics, or crosses in this contract. If H1 majors fail gross,
document that metals/indices would require a separate future contract.

## Data source and window

- OHLC only.
- Preferred path: Dukascopy M1 data resampled to H1 using existing project helpers.
- Acceptable fallback: cached H1 bars if the verifier proves coverage and bar integrity.
- Window: 2016-01-01 through 2026-06-01 UTC.
- No calendar data, swaps, tick data, broker spread data, or production NewsChecker logic.

## Fixed prototype

### Timeframe

H1 bars.

### Compression definition

Use Donchian range percentile:

- `range_t = highest_high(20) - lowest_low(20)` on H1.
- Rolling history: prior 252 H1 bars.
- Compression is true when `range_t <= 10th percentile` of the rolling 252-bar range history.
- Compression must hold for at least 3 consecutive bars before an episode arms.

The first implementation MUST use this one definition only. Do not A/B test ATR percentile,
lookbacks, persistence, percentile thresholds, or timeframes.

### Breakout trigger

On the first bar after a compression episode arms:

- BUY if close is greater than the highest high of the 20-bar compression window.
- SELL if close is less than the lowest low of the 20-bar compression window.
- One trade per compression episode.
- No pyramiding.
- Ignore additional breakouts until the current episode resolves.

### Entry filter

Entry window: 07:00-17:00 UTC.

This is fixed to reduce illiquid-session noise. It is not optional for this contract and must not
be compared against an all-session run.

### Exit

- Time stop: 24 H1 bars after entry.
- Exit price: close of the exit bar.
- No trailing stop.
- No ATR stop.
- No tuned take-profit or stop-loss.

## Cost model

Gross-first run uses zero friction.

Only if the gross gate passes, run the net/OOS stage with:

- 2.0 pips base spread round trip.
- 1.0 pip slippage per side.
- Total: 6.0 pips round trip.
- No release-window widening.

## Pass and stop gates

### Gross-first gate

Pass only if all are true:

- Pooled gross PF > 1.10.
- Pooled trades >= 30.
- No calendar year contributes more than 50% of gross profit.

Immediate DISCARD if:

- Pooled gross PF <= 1.05.
- Trades < 30.
- Passing requires threshold, timeframe, universe, session, or exit tuning.

If gross PF is > 1.05 and <= 1.10, the result is still DISCARD unless there is a documented
data issue that makes the run BLOCKED.

### Net/OOS gate

Run only after gross pass:

- Chronological 70/30 split by entry time.
- OOS gross PF > 1.05.
- OOS net PF >= 1.20 after 6-pip round-trip cost.
- OOS trades >= 30.

Final status conventions:

- `GROSS_PASS`: gross gate passed and net/OOS work remains or has not yet run.
- `DISCARD`: gross gate failed or net/OOS failed.
- `BLOCKED`: data coverage or reproducibility prevents a valid run.

## Required deliverables

1. Data proof:
   `docs/research/vol_regime/VOL_REGIME_DATA_MANIFEST_2026-06-19.md`
2. Falsifier:
   `research/new_edge/vol_regime/range_compression_breakout_test.py`
3. Results:
   `docs/research/vol_regime/VOL_REGIME_RESULTS_2026-06-19.md`
4. Ledger row:
   `research/new_edge/research_ledger.jsonl`
5. Tests:
   `tests/test_vol_regime_breakout.py`

The implementation may add helper modules under `research/new_edge/vol_regime/` when needed, but
the scope remains the single fixed prototype above.

## Required verification commands

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

## Ledger row template

```json
{
  "ts": "<ISO8601>",
  "lane": "vol_regime",
  "hypothesis": "H1 range-compression breakout after bottom-decile Donchian range",
  "status": "<GROSS_PASS|DISCARD|BLOCKED>",
  "branch": "docs/profitability-plan-2026-06",
  "command": "python -m research.new_edge.vol_regime.range_compression_breakout_test --start 2016-01-01 --end 2026-06-01 --output docs/research/vol_regime/VOL_REGIME_RESULTS_2026-06-19.md",
  "data_start": "2016-01-01",
  "data_end": "2026-06-01",
  "gross_pf": <float>,
  "net_pf": <float>,
  "oos_pf": <float>,
  "oos_return_pct": 0.0,
  "trades_or_events": <int>,
  "max_drawdown_pct": 0.0,
  "result_doc": "docs/research/vol_regime/VOL_REGIME_CONTRACT_2026-06-19.md + docs/research/vol_regime/VOL_REGIME_RESULTS_2026-06-19.md",
  "failure_reason": "<reason or N/A>"
}
```

## Out of scope

- RSI, MTF alignment, ADX, SMA, ORB retuning, Donchian retuning, and trend-pullback variants.
- Event calendar data, Actual/Forecast surprise, NewsChecker, or faireconomy XML repair.
- Carry, swaps, broker financing, pairs z-score, stat-arb residuals, or microstructure filters.
- Parameter sweeps, walk-forward optimizer loops, or "one more variant" after a failed fixed run.

