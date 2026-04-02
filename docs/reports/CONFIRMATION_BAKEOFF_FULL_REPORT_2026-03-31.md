# Confirmation Bake-off Full Report — 2026-03-31

## Scope
This report consolidates the confirmation-rule research performed for the manual trading agent. The goal was to determine which post-alignment confirmation logic performs best after MTF RSI setup alignment.

Core setup held constant in all tests:
- **BUY:** RSI 1h < 30, RSI 30m < 30, RSI 15m < 30
- **SELL:** RSI 1h > 70, RSI 30m > 70, RSI 15m > 70

Only the confirmation rule changed.

---

## Variants tested
- **V0** — no confirmation after MTF RSI alignment
- **V1** — breakout continuation confirmation
- **V2** — reversal confirmation

Sensitivity passes also tested:
- pip buffer: 0.0 / 0.5 / 1.0 / 2.0
- confirmation lifetime: 0 / 1 / 2 / 3 / 4 bars where applicable

---

## Phase 1 — Directional confirmation comparison
Pairs tested:
- EUR/GBP
- USD/JPY
- EUR/CAD
- EUR/CHF
- GBP/CHF

### Results summary
#### EUR/GBP
- V0: -0.21%, PF 0.88
- V1: -0.25%, PF 0.83
- **V2: +0.46%, PF 1.72**

#### USD/JPY
- V0: -4.78%, PF 0.37
- V1: -3.39%, PF 0.44
- **V2: -2.50%, PF 0.39** (least bad, still negative)

#### EUR/CAD
- V0: -1.35%, PF 0.60
- V1: -1.12%, PF 0.61
- **V2: -0.08%, PF 0.95**

#### EUR/CHF
- V0: -0.86%, PF 0.69
- **V1: +0.23%, PF 1.17**
- V2: -0.88%, PF 0.55

#### GBP/CHF
- V0: -0.89%, PF 0.77
- **V1: +0.68%, PF 1.32**
- V2: -0.27%, PF 0.88

### Phase 1 conclusion
There is **no universal confirmation winner**.
- EUR/GBP and EUR/CAD behaved better under **reversal confirmation (V2)**
- EUR/CHF and GBP/CHF behaved better under **breakout continuation (V1)**
- USD/JPY remained weak under all tested variants

---

## Phase 2 — Buffer sensitivity
### EUR/GBP
- V2_b0: +0.46%, PF 1.72
- **V2_b0.5: +0.72%, PF 3.09**
- V2_b1: +0.64%, PF 3.04
- V2_b2: +0.42%, PF 6.09 (too few trades: 7)

### EUR/CAD
- **V2_b0: -0.08%, PF 0.95**
- V2_b0.5: -0.23%, PF 0.84
- V2_b1: -0.31%, PF 0.79
- V2_b2: -0.21%, PF 0.86

### EUR/CHF
- **V1_b0: +0.23%, PF 1.17**
- V1_b0.5: +0.07%, PF 1.05
- V1_b1: +0.06%, PF 1.05
- V1_b2: +0.22%, PF 1.21

### GBP/CHF
- V1_b0: +0.68%, PF 1.32
- **V1_b0.5: +0.94%, PF 1.49**
- V1_b1: +0.78%, PF 1.46
- V1_b2: +0.64%, PF 1.38

### AUD/CAD candidate pass
- V1_b0: +1.39%, PF 1.43
- V1_b0.5: +1.86%, PF 1.67
- V1_b1: +2.66%, PF 2.34
- **V1_b2: +2.80%, PF 2.67**

### Phase 2 conclusion
- EUR/GBP improved materially with **V2_b0.5**
- GBP/CHF improved materially with **V1_b0.5**
- EUR/CHF remained mildly positive under **V1_b0** (V1_b2 close second)
- EUR/CAD improved under V2 but still lacked promotable edge
- AUD/CAD became a clear promotion candidate under **V1_b2**

---

## Phase 3 — Confirmation lifetime
### EUR/GBP (V2_b0.5)
- c0: +0.72%, PF 3.09
- c1: +0.78%, PF 2.80
- **c2: +0.87%, PF 3.53**
- c3: +0.77%, PF 2.74
- c4: +0.68%, PF 2.27

### GBP/CHF (V1_b0.5)
- c0 to c4: effectively unchanged
- **best remains V1_b0.5_c0**

### EUR/CHF (V1)
- V1_b0_c0 to c4: unchanged at +0.24%, PF 1.15
- V1_b2_c0 to c4: unchanged at +0.23%, PF 1.18
- operationally simplest choice remains **V1_b0_c0**

### Phase 3 conclusion
- EUR/GBP benefits from a finite confirmation lifetime: **2 bars**
- GBP/CHF does not materially benefit from delayed confirmation windows
- EUR/CHF does not materially benefit from delayed confirmation windows

---

## Final promotion map
### Promote now
| Pair | Profile | Rationale |
|---|---|---|
| EUR/GBP | **V2_b0.5_c2** | strongest reversal profile, best PF among serious trade counts |
| GBP/CHF | **V1_b0.5_c0** | best continuation profile, robust buffer improvement |
| AUD/CAD | **V1_b2_c0** | strongest result from second candidate batch |

### Tentative / monitor
| Pair | Profile | Rationale |
|---|---|---|
| EUR/CHF | **V1_b0_c0** | positive but weaker edge than the promoted set |

### Do not promote yet
- EUR/CAD
- USD/JPY
- GBP/CAD
- AUD/NZD
- USD/CHF

---

## Live-bot implications
Promoted live profiles verified in current scan output:
- EUR/GBP → V2_b0.5_c2
- GBP/CHF → V1_b0.5_c0
- AUD/CAD → V1_b2_c0

EUR/CHF should remain observational / tentative until more live behavior is seen.

---

## Important reality checks
### Verified true on server
- promoted profiles are live in current scan output
- Telegram command layer exists
- news cache + backoff exists
- Grok fallback code path exists in `news_checker.py`
- spread remains unavailable because OANDA creds are not configured

### Verified false / not yet true
- Twelve Data is **not** the active provider in current runtime config; config still states `provider: "yfinance"`
- true spread filtering is not active in production because no bid/ask credentialed quote source is configured

---

## Deliverables / evidence files
- `docs/reports/CONFIRMATION_BAKEOFF_PLAN_2026-03-31.md`
- `results/confirmation_bakeoff_20260331_143631.csv`
- `results/confirmation_bakeoff_20260331_143631.md`
- `results/confirmation_bakeoff_20260331_143822.csv`
- `results/confirmation_bakeoff_20260331_143822.md`
- `results/confirmation_bakeoff_20260331_144337.csv`
- `results/confirmation_bakeoff_20260331_144337.md`
- `results/confirmation_bakeoff_20260331_145604.csv`
- `results/confirmation_bakeoff_20260331_145604.md`
- `results/confirmation_bakeoff_20260331_161327.csv`
- `results/confirmation_bakeoff_20260331_161327.md`
- `results/confirmation_bakeoff_20260331_163719.csv`
- `results/confirmation_bakeoff_20260331_163719.md`
- `results/confirmation_bakeoff_20260331_164355.csv`
- `results/confirmation_bakeoff_20260331_164355.md`

---

## Final recommendation
Operate the bot around the promoted set only:
- EUR/GBP
- GBP/CHF
- AUD/CAD

Keep EUR/CHF visible but tentative.
Do not expand the active pair universe again until live observation of the promoted set has accumulated enough evidence.
