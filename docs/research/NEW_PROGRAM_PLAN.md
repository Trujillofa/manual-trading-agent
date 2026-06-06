# New Profitability Program — Plan (2026-06, rev. metals/indices/FX)

A *new research program* to keep looking for profitability, satisfying the
falsifiable re-entry criteria from the FX negative result
([report](FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md)). Runs in an **isolated
git worktree** so it cannot disturb `main` or the deployed Branch B scanner.

**Scope (per operator):** forex, **indices**, **metals (gold/silver)**, other CFDs.
**No crypto** (already covered by a separate agent on Hetzner).

> **This is a new program, not "one more strategy."** It changes **instrument**
> (metals/indices, not just FX majors), **edge source** (trend / cross-sectional
> momentum, not single-pair intraday directional TA), and **timeframe** (daily/H4,
> not M15/H1). That triple change is exactly why it's a legitimate re-entry — and
> why it sidesteps the friction wall that killed the FX intraday work.

## 0. The thesis (why this has a real prior)
The FX failure was **intraday directional TA on efficient majors**, where costs were
~30% of an ATR-sized stop. This program flips all three weak assumptions:
- **Daily horizon** → daily ATR (e.g. gold ~$20–30) dwarfs spread/commission → friction
  drops to a few %. The wall disappears.
- **Trendy/less-efficient instruments** → metals and indices trend more persistently than
  EUR/USD-class majors.
- **Diversified portfolio of weak edges** → time-series + cross-sectional momentum across
  ~10–15 weakly-correlated instruments is the **most robustly documented systematic edge**
  (managed-futures / CTA style). It works *as a portfolio*, not per-instrument.

Honest caveat: trend-following has had a weaker post-2010 decade and retail costs differ;
this is a *better* prior than what failed, not a guarantee. The honest harness decides.

## 1. What carries over (reuse — do NOT rebuild)
- `research/evaluate.py` — IS/OOS chronological split, **held-out OOS judge**, costed
  bar-walker driver, entry-mode seam, **gross-vs-net diagnostic**, KEEP gates.
- `src/scanner/evaluator.py` pattern — pure, side-effect-free entry functions.
- `src/data/dukascopy_fetcher.py` — same bi5 pipeline; **extend** for new instruments.
- The **discipline**: gross-first, fail-fast budget, OOS-gated KEEP, anti-overfit, portfolio metrics.

## 2. What is new (must be built)
- **Data adapter (extend Dukascopy fetcher):**
  - Metals: add `XAUUSD`, `XAGUSD` point values (trivial — same as FX bi5).
  - Indices: add Dukascopy instrument codes (e.g. USA500, USATECH100, DEU40, GBR100, JPN225)
    + point values + **relax the weekday-zero-bar quality gate** for instrument trading hours.
  - Cache daily/H4 parquet per instrument (mirror existing cache layout).
- **Honest cost model for daily holds:** spread **+ overnight swap/financing** (matters at daily
  horizon) + commission. Do not understate — costs decided the FX outcome.
- **Portfolio layer:** volatility targeting / inverse-vol position sizing; aggregate equity curve;
  per-instrument + portfolio metrics. (Time-series momentum is naturally portfolio-level.)
- **Cross-sectional ranking** (for hypothesis #2): rank the universe by momentum, long top / short bottom.
- **Guard scoping:** the FX STOP guard blocks `research/{autosearch,run_experiment}` unconditionally.
  Either scope it to FX-majors-M15/H1 specifically, or run the new program via a **new entrypoint**
  (e.g. `research/multiasset/run.py`) so it doesn't trip the FX stop. (Cleanest: scope the guard.)

## 3. Universe (starting set)
- **Metals:** XAU/USD (gold), XAG/USD (silver). *(easy data)*
- **Indices:** US500, US-Tech100, GER40, UK100, JP225 (start with what Dukascopy serves cleanly).
- **FX:** majors + a few crosses — **allowed here** because the bet is *daily cross-sectional/trend*,
  a different edge/timeframe than the locked intraday-directional finding.

## 4. Hypotheses (priors + order)
| # | Hypothesis | Why | Prior |
|---|---|---|---|
| **1 (start)** | **Time-series momentum (absolute trend-following), daily, vol-targeted, portfolio** | Most replicated anomaly across metals/indices/FX; daily → low friction; diversified | **Highest** |
| 2 | **Cross-sectional momentum** (rank universe, long top / short bottom) | Relative momentum factor; market-neutral-ish; different bet than #1 | High |
| 3 | Breakout/Donchian + vol filter, daily, on metals/indices | Classic CTA entry; trends in metals | Medium |
| 4 (defer) | Carry (rate-diff ranked FX) / calendar | Genuinely different edge | Needs non-OHLC data |

## 5. Methodology (carry the honest bar over verbatim)
1. **Gross-first diagnostic** (costs=0 vs realistic incl. swap). Gross PF ≈ 1.0 → no edge, stop that hypothesis.
2. **IS/OOS chronological split**; KEEP only on held-out: **portfolio** OOS PF ≥ 1.2–1.3, sufficient trades,
   positive net PnL after swap+spread, IS/OOS consistency, acceptable Monte-Carlo DD, **and** a
   risk-adjusted bar (Sharpe/MAR) since this is portfolio-level.
3. **Fail-fast budget:** ~2–3 structurally different hypotheses; if all gross ≈ 1.0, write a
   negative-result report for this universe and lock it (same as FX).
4. **Anti-overfit:** never tune against OOS; minimal params; no per-instrument cherry-picking;
   judge the *portfolio*, not the best symbol.

## 6. Phased execution
- **Phase 0:** worktree + extend fetcher (metals first, then indices) + daily cost/swap model + cache + guard scoping.
- **Phase 1:** implement hypothesis #1 (time-series momentum, vol-targeted) behind the seam; run gross/net
  + IS/OOS portfolio diagnostic. **Decision gate:** gross clearly >1.1 and positive → pursue (net survival);
  gross ≈ 1.0 → hypothesis #2 or stop.
- **Phase 2:** iterate one lever at a time (OOS-judged) **or** pivot per the gate. Merge to `main` /
  consider operating only if it clears the portfolio KEEP gates and survives Monte-Carlo + paper-shadow.

## 7. Worktree setup (isolation)
```bash
# from the main repo root
./scripts/worktree-create.sh research-multiasset-momentum main
cd ../manual-trading-agent-research-multiasset-momentum
python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
# build under research/multiasset/ ; reuse research/evaluate.py judge + extend dukascopy_fetcher
```
- Branch: `research-multiasset-momentum` (worktree dir `../manual-trading-agent-research-multiasset-momentum`).
- Base `main` so it inherits the harness, evaluator, fetcher, and discipline.
- Deployed Branch B scanner on `main`/prod is untouched. Clean up dead directions with
  `./scripts/worktree-cleanup.sh`.

## 8. Success / stop criteria
- **Success:** hypothesis clears the **portfolio** OOS KEEP gates with realistic costs (incl. swap),
  positive risk-adjusted return, survives Monte-Carlo → harden, paper-shadow, then consider operating.
- **Stop:** hypothesis budget exhausted with gross ≈ 1.0 → write this universe's negative-result report,
  lock it, and decide whether to try another edge/data type or accept the search is done.
