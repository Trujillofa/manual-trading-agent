# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Tests
pytest tests/ -v --tb=short          # all tests
pytest tests/test_strategy.py -v     # single file
pytest -k "test_rsi" -v              # pattern match

# Lint & format
ruff check src/ tests/
ruff format src/ tests/

# Type check
mypy src/

# CLI usage (all commands use: python -m src.cli <command>)
python -m src.cli scan --pairs GBP/USD             # scan pairs for signals
python -m src.cli analyze GBP/USD                  # deep single-pair analysis
python -m src.cli news --hours 24                  # upcoming high-impact news
python -m src.cli dashboard --days 30              # signal dashboard + paper P&L
python -m src.cli backtest --pair GBP/USD --start 2024-01-01 --end 2024-06-01
python -m src.cli backtest-enhanced --pair GBP/USD  # enhanced with TP/SL simulation
python -m src.cli telegram-poll                    # long-running Telegram command listener

# Backtest optimization (Dukascopy M1 data, runs on Hetzner)
python scripts/run_entry_optimization.py \
  --pairs "GBP/USD,NZD/USD,AUD/JPY" --days 360 --source dukascopy \
  --variants V2 --rsi-thresholds 30/70 --buffers 2.0 \
  --confirm-bars 5 --tp-sl-ratios 1.0:3.0 --adx-threshold 25

# Docker (production on Hetzner)
docker compose up -d                 # runs scan every 15min + telegram-poll
```

## Architecture

**Multi-timeframe RSI forex scanner** that alerts via Telegram when RSI aligns across 1h/30m/15m timeframes.

**2026-06 Locked Finding (FX Directional TA):** After full-365d gross-vs-net diagnostics on ORB and trend-pullback families (M15 + H1), gross PF ~1.0–1.07 with no accessible edge before realistic costs. Live strict MTF variant is extremely sparse (same no-edge family). See `docs/research/FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md` and `research/program.md` (STOP banner + re-entry criteria). This line of research is terminated for FX majors on OHLC TA. The durable value is the unified evaluator, honest harness, audit, and the discipline. Future profitability work requires new instrument/edge/data per the re-entry rules. Agent-proof guards exist in autosearch/run_experiment. Project handoff summary: `docs/PROJECT_STATUS_2026-06.md`. Current plan for the next search: `docs/research/PROFITABILITY_PLAN_2026-06.md` (run in an isolated worktree).

### Signal Pipeline (scan command)

```
DataFetcher (yfinance) → fetch 1h, 30m, 15m OHLCV per pair
    ↓
V2 Reversal Breakout Check:
    - RSI 14 alignment (all 3 TFs < 30 or > 70)
    - Wick through 20-bar LL/HH + close reclaim (buffer 2.0 pips)
    - Confirmation window: 5 bars after alignment
    + CandlePattern detection (hammer, shooting star, doji)
    + RSI divergence detection (bullish/bearish)
    ↓
Validation gates:
    - ADX trend filter (ADX < 25 = ranging, safe for mean-reversion)
    - NewsChecker (Forex Factory 3-star events → lockout window)
    - Session filter (configurable UTC hours)
    - Active-signal suppression (Rule C): one signal per pair per direction
      until invalidated by TP hit, SL hit, RSI(15m) midline cross, or SMA flip
    ↓
Signal output → signal_audit.jsonl + Telegram notification
    TP = 1.0 × ATR(14), SL = 3.0 × ATR(14)
    V0 (RSI-only): fires on alignment alone, no breakout gate
```

### Key design decisions

- **config/settings.yaml** is the single source for all tunable parameters (RSI thresholds, TP/SL, news lockout, session hours, pair lists). Settings class in `src/config/settings.py` loads and validates it.
- **State files in logs/**: `active_signal_state.json` (Rule C: one record per pair while a signal is live), `near_setup_state.json`, `news_cache.json` persist between scan runs. `signal_audit.jsonl` is an append-only audit trail.
- **Async throughout**: CLI uses `asyncio.run()`, data fetchers and Telegram use async HTTP clients (httpx/aiohttp). TelegramNotifier falls back to background thread if no event loop.
- **Graceful degradation**: missing news feed doesn't block scanning, missing OANDA quote skips spread check.

### Production config (current state, 2026-05-05)

**27 pairs enabled** (all majors and minors except EUR/GBP). The promotion gate is intentionally relaxed in favour of broad coverage with the Rule C invalidation rule (see below). Five pairs retain backtest-validated per-pair overrides via `strategy.pair_overrides`; the other 22 use defaults (SMA 50, TP 1.0×ATR, SL 3.0×ATR).

**Note on historical promotion numbers (2026-06):** The per-pair PnL/PF/WR figures in the table below (and earlier reports) were derived from divergent engines (Donchian reclaim variants or yfinance backtests) that did not match the live scanner's V* + full gates + ATR + Rule C implementation. Live/Hetzner audit and honest R1 harness on the unified live entry show far lower frequency (single-digit or zero trades in multi-month windows). Treat the table as historical/scout context only; current production posture is Branch B (selective manual alert tool). See research note and sampled baselines for evidence.

Tuned (per-pair overrides):

| Pair | Config | SMA | TP/SL (ATR) | Trades (2y) | PnL % | PF | WR | Promotion date |
|---|---|---|---|---|---|---|---|---|
| GBP/CHF | default | 50 | 1.0/3.0 | 290 | +88% | 1.30 | 64% | 2026-04-27 |
| NZD/JPY | override | 20 | 2.5/2.5 | 269 | +126% | 1.29 | 52% | 2026-04-27 |
| GBP/JPY | override | 20 | 1.5/2.5 | 319 | +97% | 1.27 | 64% | 2026-04-27 |
| USD/JPY | override | 40 | 2.0/2.5 | 300 | +46% | 1.13 | 54% | 2026-04-27 |
| AUD/CAD | default | 50 | 1.0/3.0 | 264 | +13% | 1.05 | 58% | 2026-04-20 |

Default config (no override, scout-quality until backtested):
EUR/USD, GBP/USD, USD/CHF, AUD/USD, USD/CAD, NZD/USD, EUR/JPY, EUR/CHF, EUR/AUD, EUR/CAD, EUR/NZD, GBP/AUD, GBP/CAD, GBP/NZD, AUD/JPY, AUD/CHF, AUD/NZD, NZD/CAD, NZD/CHF, CAD/JPY, CAD/CHF, CHF/JPY.

Rejected (excluded from config):
EUR/GBP — negative PnL in all 48 configs tested (best: -0.30%, PF 0.66). 2026-04-20 sweep.
**Note:** This rejection was based on a 58d yfinance 15m sweep with `run_entry_optimization.py`.
Two independent tests contradict this result:
- 2026-03-31 Dukascopy M1 bakeoff: EUR/GBP V2_b0.5_c2 scored PF 3.53 (+0.87% PnL)
- 2026-05-06 Enhanced backtest (2Y yfinance 1h): PF 1.23 (+49.6% PnL, 349 trades)
EUR/GBP needs proper 180d+ Dukascopy validation via the promotion gate before reinstatement.
Do not reference the 58d sweep alone as definitive evidence of unprofitability.

**Rule C — one signal per pair per direction, until invalidated.**
After a signal fires, same-direction signals for that pair are suppressed until **any** of:
1. **TP hit** — 15m high (BUY) / low (SELL) reaches the original TP since fire time.
2. **SL hit** — 15m low (BUY) / high (SELL) reaches the original SL since fire time.
3. **RSI(15m) midline cross** — RSI ≥ 50 (BUY) or ≤ 50 (SELL) on any closed 15m bar since fire.
4. **SMA flip** — current 15m close on the opposite side of pair-SMA vs. signal direction.

Opposite-direction signals are always allowed and re-arm both sides. State persists in `logs/active_signal_state.json`. The legacy `strategy.cooldown_minutes` setting is retained for backwards compatibility but no longer gates anything.

**Pre-signal alert anti-flicker.** The ⏳ aligned-pending and 👀 near-setup alerts use a separate state-change invalidation in `near_state`. To avoid noise from RSI ticking across the 30/70 boundary, a pair must drop out of its tracked state for `INVALIDATION_MISS_THRESHOLD = 2` consecutive scans (≈ 30 min on the 15-min schedule) before the ❌ Setup Invalidated message fires.

Shared parameters:
- **RSI thresholds**: 30/70 on 1h, 30m, 15m (per `config/settings.yaml`)
- **TP/SL**: ATR-based — TP = 1.0 × ATR(14), SL = 3.0 × ATR(14)
  (Fixed 2026-06: previous live scans always fell back to fixed 30/90 pips due to
  off-by-one in calculate_atr on 14-bar slices. Live now uses the configured
  multipliers. This changes TP/SL levels for new signals; Rule C outcomes for
  historical signals are unaffected.)
- **ADX filter**: ADX(14) < 25 on 1h (mean-reversion only in ranging regime)
- **Session filter**: 06–17 UTC, 12–21 UTC
- **News lockout**: 3-star Forex Factory events; 60 min before / 30 min after
- **Lot size**: 3.0
- **Data source**: yfinance (live scanner), Dukascopy M1 (backtests)

Entry-variant naming (used in confirmation profiles and report tables):
- `V0` — RSI-only; fires on MTF alignment alone, no breakout gate
- `V1` — breakout continuation; BUY breaks below LL, SELL breaks above HH
- `V2` — reversal; wick through LL/HH + close reclaim
- `b{N}` — buffer in pips; `c{N}` — confirmation lifetime in bars after RSI alignment

### Promotion gate for per-pair overrides

As of 2026-05-05 the watchlist is broad (27 pairs); this gate now applies to **adding a per-pair override** (custom SMA/TP/SL via `strategy.pair_overrides`), not to whether a pair is scanned at all.

1. On the shortest validation window tested (currently 180d Dukascopy): **≥ 30 trades**
2. **Positive total PnL** on that window
3. **Profit factor clearly > 1** (treat PF with N < 30 as unreliable regardless of value)
4. **No regime flip across windows** — winning variant family (V1 vs V2) must be the same on 180d and 365d, and signs must agree

Failing any gate → keep on default params. Low trade count is the dominant failure mode in this dataset.

### Backtest data

- **Dukascopy fetcher** (`src/data/dukascopy_fetcher.py`): Downloads M1 bi5 binary data, resamples to h1/m30/m15
- **Bake-off script** (`scripts/run_confirmation_bakeoff.py`): Sweeps variants × buffers × confirm-bars per pair; artifacts under `results/`
- **Optimization script** (`scripts/run_entry_optimization.py`): Broader grid including RSI, TP/SL, ADX
- Latest validation: see `docs/reports/WATCHLIST_EXPANSION_2026-04-14.md`

**2026-06 research note (honest search on realistic engine)**: A short autoresearch run (research/autosearch.py + evaluate with strict IS/OOS gates: OOS trades >=30 + OOS PF >=1.20 + positive PnL on OOS) on 8 pairs did not surface any config that achieved a strict "KEEP" verdict (no best_config.json written; "keeps" for search score still failed the gates with low trades or negative pnl). This is consistent with the structural low frequency observed in the corrected live-family harness.

The live entry logic has been centralized in `src/scanner/evaluator.py` (full MTF RSI alignment + V0/V1/V2 profiles + confirm window + RSI-MA curl/hard gate + session/news/spread/ADX/Rule C + ATR(14) TP/SL with per-pair mults) and is now the single source of truth. The research harness (research/evaluate.py) now supports engine="live_mtf_rsi" (thin bar-walker driver that maintains active/alignment_state, injects mocks, calls the pure evaluate_entry, simulates TP/SL hits for P&L, returns compatible stats). 

Historical R1 runs executed on the *live* family (not Donchian) before the final evaluator parity fixes:
- Full 365d split for EUR/USD via the honest harness + live driver: 0 trades (IS and OOS), verdict DISCARD (0 < 30 trades, PF 0, PnL 0).
- 2-pair pooled (EUR/USD + GBP/USD) on full splits: IS 0 trades, OOS 1 trade total → still DISCARD (OOS trades 1 < 30, IS PnL 0).
- 3-pair pooled on the previously "tuned" pairs (GBP/CHF + GBP/JPY + USD/JPY) full splits: IS 9 trades (pf 2.06 but mean_pnl% 0.0), OOS 1 trade → DISCARD (IS trades 9 < 30, OOS trades 1 < 30).
- 8-pair recent-sample (LIVE_BT_MAX_BARS=2200 ~3 weeks recent frames, pooled across entire watchlist): IS 1 trade, OOS 0 trades → DISCARD (IS trades 1 < 30, OOS trades 0 < 30, OOS PnL 0%, etc.).
- Recent-slice sampling + direct driver runs on GBP/CHF (and spot checks on others): 0 fires in the windows.
These runs are retained as historical context only. They agree with the corrected result directionally (low N), but the corrected sampled run below is the current quantitative baseline.

Practical recent-window IS/OOS baseline on the *live* entry family (rerun 2026-06-04 after restoring the 3-TF SMA gate, same-direction-only Rule C suppression, and costed driver P&L; LIVE_BT_MAX_BARS=3000 truncate + full driver walk on the 8 cached pairs, engine="live_mtf_rsi", current settings.yaml + default V2 profile + Rule C + ATR TP/SL simulation):

verdict: **DISCARD**
score: -7.5
reasons: ['IS trades 0 < 30', 'OOS trades 0 < 30', 'OOS PF 0.00 < 1.2', 'OOS PnL 0.00% <= 0', 'IS PnL 0.00% <= 0']

IS stats: trades=0, win_rate=0, pf=0, max_consec=0
OOS stats: trades=0 (everything 0)

(Command: `LIVE_BT_MAX_BARS=3000 .venv/bin/python -m research.run_experiment --config /tmp/live_mtf_current_config.json`, where the config sets `engine="live_mtf_rsi"` and current strict defaults.) The corrected run emits per-split rejection summaries. Dominant blockers remain V2 breakout confirmation, session, ADX ranging filter, RSI-MA, and the restored SMA alignment gate.

This sampled run (recent ~30d-ish windows post-truncate, full Rule C state carry + TP/SL on subsequent bar H/L) confirms the low-volume reality from earlier harness runs and Hetzner/live audit (rare entries). All configs remain DISCARD on the strict gates (MIN_TRADES=30 on both windows + OOS PF>=1.20 + positive PnL). Low N is structural given the full gate stack + V*/confirm + Rule C + SMA alignment.

(The full non-truncated 365d version remains the gold-standard for any promotion-gate decision. The sampled corrected run is enough to keep Branch B as the operating posture, because it returns zero IS and zero OOS trades under current strict defaults.)

Pre-fix sampled numbers are retired for quantitative claims. The archived strict 2 IS / 0 OOS and relaxed 14 IS / 10 OOS counts were generated before the evaluator restored the configured 3-TF SMA alignment gate and before the driver used costed P&L. They remain useful only as historical debugging context; do not cite them as current evidence.

With the 2026-06 Rule C multi-active + re-arm fix in the driver, future runs count additional re-entries after midline/SMA invalidations (live behavior). The current strict sampled baseline still produced 0 IS / 0 OOS trades after the parity fixes.

Note on fidelity: driver multi-active list permits overlapping virtual positions (each alert resolved independently at its TP/SL) while live pops the active record on re-arm (latest wins for suppression). This can make harness trade count N slightly higher than live bookkeeping in re-arm scenarios; it is a deliberate P&L modeling choice for per-signal outcomes and does not alter the sparse/no-edge conclusion.

Historical rejection diagnostics using the live evaluator (research/diagnose_live_entry_volume.py --bars 1000 on the 8 PAIRS from research.evaluate, 800 post-warmup bars with empty active/alignment states for speed + historical mocks injected to the pure evaluate_entry) completed successfully before the final parity fixes. The counts below are retained as qualitative blocker context only; rerun the diagnostic after the SMA/Rule C/cost fixes before citing exact aligned/rejection counts.

Historical 3-tuned diagnostic numbers:
- GBP/CHF: aligned=97, fires=0. Top: 61 "15m breakout high not confirmed", 33 "trending market (ADX ? >= 25.0)", 24 "outside allowed session", 17 "15m breakout low not confirmed", 10 "trending (ADX 51)".
- GBP/JPY: aligned=45, fires=0. Top: 34 "outside allowed session", 31 "trending (ADX ?)", 4+ "RSI-MA(5) gate", 4 "trending (ADX 25)".
- USD/JPY: aligned=72, fires=0. Top: 33 "outside allowed session", 33 "trending (ADX ?)", 8 "trending (ADX 100)", 4+ "RSI-MA(5) gate".

Historical full 8-pair diagnostic results (800 bars each post-warmup, fires=0 for all):
- EUR/USD: aligned=38. Top: 26 "15m breakout low not confirmed", 20 "outside allowed session", 10 "15m breakout high not confirmed", 8 "trending (ADX 100)", 8 "trending (ADX ?)".
- GBP/USD: aligned=38. Top: 30 "outside allowed session", 21 "trending (ADX ?)", 20 "15m breakout high not confirmed", 15 "15m breakout low not confirmed".
- GBP/CHF: aligned=97 (as above).
- GBP/JPY: aligned=45 (as above).
- USD/JPY: aligned=72 (as above).
- NZD/JPY: aligned=145 (highest). Top: 78 "outside allowed session", 17 "trending (ADX ?)", 12 each for several high ADX values.
- AUD/CAD: aligned=29. Top: 18 "outside allowed session", 18 "trending (ADX ?)", 4 "RSI-MA(5) gate".
- USD/CHF: aligned=52. Top: 41 "15m breakout high not confirmed", 35 "outside allowed session", 29 "trending (ADX ?)".

Historical pooled diagnostic: 516 MTF aligned events, 0 fires (fire rate 0.0000 per bar). Top pooled rejections: 272 "outside allowed session", 190 "trending market (ADX ? >= 25.0)", 132 "15m breakout high not confirmed", 61 "15m breakout low not confirmed", then ADX 100/high values + some RSI-MA gates.

Dominant gates across the watchlist remain session filter, ADX ranging filter (including the ADX ? None-safe case), the V2 15m breakout confirmation window, RSI-MA, and now the restored 3-TF SMA alignment gate. This profile directly explains the harness R1 low-volume results (single-digit or zero trades even on best pairs across full IS/OOS windows → all DISCARD on MIN_TRADES=30 + PnL/PF gates).

The reusable script (research/diagnose_live_entry_volume.py) remains available for any window/pairs. Rerun it after evaluator parity changes whenever exact gate-count diagnostics are needed.

The reusable diagnostic tool makes it easy to re-run on any window/pairs for gate tuning insight. Unification complete (live == harness by construction for the entry decision + TP/SL + Rule C/alignment state, including re-arms after midline cross or SMA flip). The driver tracks actives as a list per pair (to support concurrent after re-arm) and passes latest for evaluator suppression check; the evaluator now restores same-direction-only Rule C suppression and the configured 3-TF SMA alignment gate. ATR fix + evaluator purity (5 injected params, no I/O) + CLI cleanup (single authoritative evaluate_entry call, dead parallel logic deleted, bars_aligned ownership, unit test coverage in tests/test_evaluator.py) also landed.

Live family is now parameterizable for search: evaluate_entry accepts `overrides: dict` (rsi_oversold/overbought or lower/upper_bound aliases, adx_threshold, buffer_pips, confirm_bars, tp_atr_mult, session_filter_enabled, pair_overrides etc). Harness (evaluate_config + backtest_live_entry) forwards from research CONFIG when engine="live_mtf_rsi". diagnose_live_entry_volume.py supports --adx-threshold / --no-session etc for quick what-if volume. This enables true R1 autosearch over the actual live entry (not just Donchian) + "relax gate X" diagnostics. See research/strategy_config.py notes + evaluator.py.

The system is best viewed as a high-quality, selective manual alert tool rather than a high-volume profitable strategy under current gates and the honest validation bar. The corrected sampled recent windows on the actual live entry produced 0 IS / 0 OOS trades under strict current defaults, so no config cleared the strict OOS gates (MIN_TRADES=30 on both IS/OOS + OOS PF>=1.20 + positive PnL) on the realistic engine. Low frequency is structural given the full gate stack + V* confirmation + Rule C + SMA alignment. Branch B path (document reality; position as selective filter/alert aid) is the evidence-based posture.

**Correctness changes ready for deploy (paper-shadow recommended):**
- ATR(14) fix (src/indicators/atr.py: needs period+1 bars; updated all call sites + doc).
- Unified pure evaluate_entry (src/scanner/evaluator.py: removed all I/O, 5 injected params for purity/backtestability, overrides= for search; Rule C re-arm fidelity 2026-06).
- CLI single source (src/cli.py: one evaluate_entry call, dead parallel MTF/RSI-MA block deleted, explicit bars_aligned + injected spread/news/now).
- Driver fidelity (research/evaluate.py: multi-active list + re-arm on midline/SMA to match live Rule C; progress + per-split rejection prints).
These are safe correctness improvements for the existing selective alert use-case. Old per-pair "promotion table" PnL claims (e.g. +88% GBP/CHF) came from divergent Donchian/yfinance engines and should be retired from production notes. Deploy via normal Docker/main promotion with extra monitoring of ATR TP/SL vs Rule C states and audit entries.

## Code Conventions

- Python 3.11+, `from __future__ import annotations` in all modules
- `str | None` not `Optional[str]`, `list[float]` not `List[float]`
- ruff for linting (line-length 100) and formatting
- structlog or `logging.getLogger(__name__)` — never `print()`
- Conventional commits: `feat(<scope>): description`
