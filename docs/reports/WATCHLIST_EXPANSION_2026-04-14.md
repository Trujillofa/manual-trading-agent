# Watchlist Expansion Validation — 2026-04-14

## Scope

Extend prior validation work (`CONFIRMATION_BAKEOFF_FULL_REPORT_2026-03-31.md`, `NZDJPY_VALIDATION_2026-04-09.md`) to the four pairs previously cited in `CLAUDE.md` as the "360-day OOS validated" production universe but **not backed by any checked-in backtest artifact**: GBP/USD, NZD/USD, AUD/JPY, EUR/CHF.

Goal: decide which, if any, should be promoted into the live watchlist alongside EUR/GBP, GBP/CHF, AUD/CAD.

## Methodology

- Data source: Dukascopy M1 bi5, resampled to 1h / 30m / 15m
- Core setup unchanged: MTF RSI alignment (1h + 30m + 15m all < 30 for BUY, > 70 for SELL)
- Sweep: V0 / V1 / V2 × buffer {0, 0.5, 1, 2} × confirm-bars {0, 1, 2, 3, 4, 5}
- Windows: 365-day single-pair bakeoffs + 180-day multi-pair comparison
- Runs executed locally per workflow note in `NZDJPY_VALIDATION_2026-04-09.md`

## Artifacts

| File | Contents |
|---|---|
| `results/confirmation_bakeoff_20260414_001319.md` / `.csv` | AUD/JPY 365d |
| `results/confirmation_bakeoff_20260414_001524.md` / `.csv` | EUR/CHF 365d |
| `results/confirmation_bakeoff_20260414_001902.md` / `.csv` | NZD/USD 365d |
| `results/confirmation_bakeoff_20260414_002005.md` / `.csv` | GBP/USD 365d |
| `results/confirmation_bakeoff_20260414_044144.md` / `.csv` | 180d comparison across EUR/GBP, GBP/CHF, AUD/CAD, GBP/USD, AUD/JPY, NZD/USD, EUR/CHF |

## Results

### 365-day single-pair best variants

| Pair | Best variant | Trades | PnL | PF |
|---|---|---:|---:|---:|
| GBP/USD | V2_b1_c1 | 14 | +0.37% | 2.44 |
| AUD/JPY | V2_b0.5_c1 | 18 | +0.36% | 1.48 |
| NZD/USD | V2_b2_c1 | 6 | +0.33% | 2.88 |
| EUR/CHF | V2_b2_c1 | 7 | +0.08% | 1.44 |

### 180-day comparison — best variant per pair

| Pair | Best variant | Trades | PnL | PF |
|---|---|---:|---:|---:|
| GBP/USD | V1_b2_c4 | 54 | +1.02% | 1.93 |
| GBP/CHF | V2_b0_c0 | 17 | +0.11% | 1.22 |
| NZD/USD | V2_b2_c1 | 2 | +0.09% | 2.50 |
| EUR/GBP | V2_b1_c4 | 8 | +0.05% | 1.31 |
| AUD/CAD | V2_b0.5_c1 | 9 | -0.01% | 0.98 |
| EUR/CHF | V2_b2_c1 | 1 | -0.03% | 0.00 |
| AUD/JPY | V2_b0.5_c1 | 10 | -0.11% | 0.79 |

### Cross-window consistency check — GBP/USD

| Variant | 365d | 180d |
|---|---|---|
| V2_b1_c1 (365d winner) | +0.37%, PF 2.44, 14 tr | +0.15%, PF 1.66, 10 tr |
| V1_b2_c4 (180d winner) | **−0.10%**, PF 0.97, 119 tr | +1.02%, PF 1.93, 54 tr |
| V2_b2_c5 (prior CLAUDE.md live profile) | +0.15%, PF 1.29, 18 tr | +0.10%, PF 1.12, 27 tr |

The two windows disagree on both the winning variant and the variant family (V1 continuation vs V2 reversal). The previously-documented live profile is not top-scoring on either window.

## Promotion gate

Applied to each candidate on the 180-day window (the shortest tested):

1. Trades ≥ 30
2. Positive PnL
3. Profit factor clearly > 1
4. No regime flip across 180d / 365d (same winning family, same sign)

## Decisions

### Add now
None.

### Keep as-is (already promoted)
- EUR/GBP — V2_b0.5_c2
- GBP/CHF — V1_b0.5_c0
- AUD/CAD — V1_b2_c0

These remain in `config/settings.yaml`. The 180-day comparison shows continued mild-positive-to-flat behavior, consistent with the 2026-03-31 report. No action.

### Shadow-run only
- **GBP/USD** — candidate profile V2_b1_c2 (23 trades, +0.35%, PF 1.67 on 365d) as a compromise across windows. **Not promoted.** Reasons: fails gate 4 (regime flip V2 ↔ V1 across windows); 365d top variant fails gate 1 (14 trades). Require ≥ 20 live-shadow signals matching the gate before reconsidering.

### Reject
- **AUD/JPY** — positive 365d, negative 180d; sign flip.
- **NZD/USD** — positive both windows but 2 and 6 trades on best variants; fails gate 1 severely.
- **EUR/CHF** — barely positive 365d, effectively dead on 180d (1 trade on best variant).
- Prior rejections preserved: NZD/JPY (see `NZDJPY_VALIDATION_2026-04-09.md`), EUR/CAD, USD/JPY, USD/CHF, GBP/CAD, AUD/NZD.

## Corrections to prior documentation

- The `CLAUDE.md` headline "GBP/USD +1.45%, PF 1.54 / NZD/USD +0.62%, PF 1.24 / AUD/JPY +0.63%, PF 1.16" from commits `d819777` and `e9debd0` is **not reproducible** from any checked-in artifact and is materially contradicted by the 365-day Dukascopy runs in this report. The section has been rewritten to reflect the current live watchlist, the shadow-run status of GBP/USD, and the promotion gate.
- The prior "V2_b2_c5" live profile for GBP/USD is retired — it was never a promoted profile in practice (the pair was never in `config/settings.yaml` under the current scan loader), and it is not top-scoring on either window tested here.

## Next steps

- Monitor promoted set (EUR/GBP, GBP/CHF, AUD/CAD) for regression.
- Shadow-run GBP/USD under V2_b1_c2 separately from the live signal path; accumulate ≥ 20 signals before reconsidering.
- Do not expand the active universe further until the promotion gate is satisfied end-to-end on a candidate.
