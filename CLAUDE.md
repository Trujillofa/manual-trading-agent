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

**2026-06 research note (honest search on realistic engine)**: A short autoresearch run (research/autosearch.py + evaluate with strict IS/OOS gates: OOS trades >=30 + OOS PF >=1.20 + positive PnL on OOS) on 8 pairs did not surface any config that achieved a strict "KEEP" verdict (no best_config.json written; "keeps" for search score still failed the gates with low trades or negative pnl). This is consistent with the structural low frequency (~6-12 OOS trades even when relaxing some filters). 

The live entry logic has been centralized in `src/scanner/evaluator.py` (full MTF RSI alignment + V0/V1/V2 profiles + confirm window + RSI-MA curl/hard gate + session/news/spread/ADX/Rule C + ATR(14) TP/SL with per-pair mults) and is now the single source of truth. The research harness (research/evaluate.py) now supports engine="live_mtf_rsi" (thin bar-walker driver that maintains active/alignment_state, injects mocks, calls the pure evaluate_entry, simulates TP/SL hits for P&L, returns compatible stats). 

R1 executed on the *live* family (not Donchian): 
- Full 365d split for EUR/USD via the honest harness + live driver: 0 trades (IS and OOS), verdict DISCARD (0 < 30 trades, PF 0, PnL 0).
- 2-pair pooled (EUR/USD + GBP/USD) on full splits: IS 0 trades, OOS 1 trade total → still DISCARD (OOS trades 1 < 30, IS PnL 0).
- 3-pair pooled on the previously "tuned" pairs (GBP/CHF + GBP/JPY + USD/JPY) full splits: IS 9 trades (pf 2.06 but mean_pnl% 0.0), OOS 1 trade → DISCARD (IS trades 9 < 30, OOS trades 1 < 30).
- 8-pair recent-sample (LIVE_BT_MAX_BARS=2200 ~3 weeks recent frames, pooled across entire watchlist): IS 1 trade, OOS 0 trades → DISCARD (IS trades 1 < 30, OOS trades 0 < 30, OOS PnL 0%, etc.).
- Recent-slice sampling + direct driver runs on GBP/CHF (and spot checks on others): 0 fires in the windows.
The decisive experiment confirms the live entry family does not clear volume + strict OOS profitability gates on this data (low N is structural; even the best historical pairs produce single-digit trades across hundreds of days under the full current logic + Rule C; across all 8 pairs in recent data only a single "IS" trade total). 

Practical recent-window IS/OOS baseline on the *live* entry family (LIVE_BT_MAX_BARS=3000 truncate + full driver walk on the 8 cached pairs, engine="live_mtf_rsi", overrides=None = current settings.yaml + profiles + overrides + Rule C + ATR TP/SL simulation) completed in 8 min with per-split (IS + OOS for every pair) top rejection prints + final aggregate:

verdict: **DISCARD**
score: -7.5
reasons: ['IS trades 2 < 30', 'OOS trades 0 < 30', 'OOS PF 0.00 < 1.2', 'OOS PnL 0.00% <= 0', 'IS PnL -0.00% <= 0']

IS stats: trades=2 (tiny negative pooled PnL), win_rate=0, pf=0, max_consec=1
OOS stats: trades=0 (everything 0)

(Artifact /tmp/live_sampled_baseline_8pair.log ; driver now emits progress every 500 bars + "top rejection reasons" at end of *every* IS/OOS split walk. All 16 splits' tops captured; session, V* "breakout ... not confirmed", ADX ?/high, RSI-MA, and occasional "confirmation window expired (N bars > C)" are the themes. V2 pairs show more breakout/confirm rejections; JPY crosses more RSI-MA + session.)

This sampled run (recent ~30d-ish windows post-truncate, full Rule C state carry + TP/SL on subsequent bar H/L exactly as live) confirms the low-volume reality from the 800-bar strict 8-pair diag (516 aligned/0 fires), the 400-bar relaxed diag (223 aligned/0 fires even with session+ADX loosened), earlier harness R1 (single-digit or 0 trades on live driver), and Hetzner/live audit (rare entries). 2 IS trades total across 8 pairs in the sampled window is the highest "volume" seen for the current production live family under honest conditions. All configs DISCARD on the strict gates (MIN_TRADES=30 on both windows + OOS PF>=1.20 + positive PnL). Low N is structural given the full gate stack + V*/confirm + Rule C.

(The full non-truncated 365d version would be the gold-standard for any promotion-gate decision but follows the same regime; the mechanism + per-split diagnostics + sampled numbers + relaxed what-ifs now give a complete, reproducible picture without multi-hour waits in dev.)

Direct apples-to-apples comparison on the *exact same recent sampled windows* (strict current production vs relaxed main blockers; note: runs used pre-fidelity-fix driver so real N slightly higher):
- Strict (current settings): IS 2 trades, OOS 0 trades → DISCARD
- Relaxed (adx=40, session off, confirm_bars=12, buffer_pips=1.0, otherwise identical full driver/Rule C/TP-SL/pure evaluator): IS 14 trades, OOS 10 trades → still DISCARD (OOS PF 0.53, negative PnL, trades <<30 on both)
Even substantial loosening of the dominant observed rejection reasons (session + ADX + V2 confirmation) only lifts volume from 2/0 to 14/10 — nowhere near the honest 30-trade gate on both windows, and the OOS edge is poor. Archived logs: results/live_sampled_baseline_strict_20260604.log and results/live_sampled_baseline_relaxed_adx40_nosession_c12_b1_20260604.log . Per-split tops for the relaxed run show the expected shift (far fewer session rejections, ADX now at 40, breakout/confirm and residual ADX/RSI-MA still prominent; Rule C "active signal not yet invalidated" appears in some splits).

With the 2026-06 Rule C multi-active + re-arm fix in the driver, future runs will count additional re-entries after midline/SMA invalidations (live behavior), so the "live frequency" match is now exact and N will be >= the above (still expected to be low given structural sparsity).

Note on fidelity: driver multi-active list permits overlapping virtual positions (each alert resolved independently at its TP/SL) while live pops the active record on re-arm (latest wins for suppression). This can make harness trade count N slightly higher than live bookkeeping in re-arm scenarios; it is a deliberate P&L modeling choice for per-signal outcomes and does not alter the sparse/no-edge conclusion.

A rejection diagnostic using the live evaluator (research/diagnose_live_entry_volume.py --bars 1000 on the 8 PAIRS from research.evaluate, 800 post-warmup bars with empty active/alignment states for speed + historical mocks injected to the pure evaluate_entry) completed successfully (exit 0, full output captured in /tmp/full_8pair_live_diag.log and task logs). This broadens the R1 "why" analysis to the full current watchlist (not just the 3 tuned).

Validated 3-tuned baseline numbers exactly:
- GBP/CHF: aligned=97, fires=0. Top: 61 "15m breakout high not confirmed", 33 "trending market (ADX ? >= 25.0)", 24 "outside allowed session", 17 "15m breakout low not confirmed", 10 "trending (ADX 51)".
- GBP/JPY: aligned=45, fires=0. Top: 34 "outside allowed session", 31 "trending (ADX ?)", 4+ "RSI-MA(5) gate", 4 "trending (ADX 25)".
- USD/JPY: aligned=72, fires=0. Top: 33 "outside allowed session", 33 "trending (ADX ?)", 8 "trending (ADX 100)", 4+ "RSI-MA(5) gate".

Full 8-pair results (800 bars each post-warmup, fires=0 for all):
- EUR/USD: aligned=38. Top: 26 "15m breakout low not confirmed", 20 "outside allowed session", 10 "15m breakout high not confirmed", 8 "trending (ADX 100)", 8 "trending (ADX ?)".
- GBP/USD: aligned=38. Top: 30 "outside allowed session", 21 "trending (ADX ?)", 20 "15m breakout high not confirmed", 15 "15m breakout low not confirmed".
- GBP/CHF: aligned=97 (as above).
- GBP/JPY: aligned=45 (as above).
- USD/JPY: aligned=72 (as above).
- NZD/JPY: aligned=145 (highest). Top: 78 "outside allowed session", 17 "trending (ADX ?)", 12 each for several high ADX values.
- AUD/CAD: aligned=29. Top: 18 "outside allowed session", 18 "trending (ADX ?)", 4 "RSI-MA(5) gate".
- USD/CHF: aligned=52. Top: 41 "15m breakout high not confirmed", 35 "outside allowed session", 29 "trending (ADX ?)".

Pooled (6400 bars): 516 MTF aligned events, 0 fires (fire rate 0.0000 per bar). Top pooled rejections: 272 "outside allowed session", 190 "trending market (ADX ? >= 25.0)", 132 "15m breakout high not confirmed", 61 "15m breakout low not confirmed", then ADX 100/ high values + some RSI-MA gates.

Dominant gates across the watchlist: session filter, ADX ranging filter (incl. the ADX ? None-safe case), and for V2-profile pairs the 15m breakout confirmation window ("not confirmed" after wick-through + reclaim within confirm_bars). RSI-MA gate contributes on JPY crosses. This profile directly explains the harness R1 low-volume results (single-digit or zero trades even on best pairs across full IS/OOS windows → all DISCARD on MIN_TRADES=30 + PnL/PF gates).

The reusable script (research/diagnose_live_entry_volume.py) is validated (reproduces prior 3-tuned exactly) and reusable for any window/pairs. The broadened diagnostic completes the R1 rejection characterization on the live entry family using the now-unified pure evaluator + driver-equivalent walk.

The reusable diagnostic tool makes it easy to re-run on any window/pairs for gate tuning insight. Unification complete (live == harness by construction for the entry decision + TP/SL + Rule C/alignment state, including re-arms after midline cross or SMA flip). The driver now tracks actives as list per pair (to support concurrent after re-arm) and passes latest for evaluator suppression check; previous sampled runs used single-active which under-counted. ATR fix + evaluator purity (5 injected params, no I/O) + CLI cleanup (single authoritative evaluate_entry call, dead parallel logic deleted, bars_aligned ownership, unit test coverage in tests/test_evaluator.py) also landed. Rule C re-arm fidelity gap fixed 2026-06.

Live family is now parameterizable for search: evaluate_entry accepts `overrides: dict` (rsi_oversold/overbought or lower/upper_bound aliases, adx_threshold, buffer_pips, confirm_bars, tp_atr_mult, session_filter_enabled, pair_overrides etc). Harness (evaluate_config + backtest_live_entry) forwards from research CONFIG when engine="live_mtf_rsi". diagnose_live_entry_volume.py supports --adx-threshold / --no-session etc for quick what-if volume. This enables true R1 autosearch over the actual live entry (not just Donchian) + "relax gate X" diagnostics. See research/strategy_config.py notes + evaluator.py.

The system is best viewed as a high-quality, selective manual alert tool rather than a high-volume profitable strategy under current gates and the honest validation bar (sampled recent windows on the actual live entry: strict 2 IS / 0 OOS trades; even relaxed main gates 14 IS / 10 OOS — both DISCARD on MIN_TRADES=30 + OOS PF/PnL; 0 fires in 6400-bar volume diag despite 516 alignments). See research/ for the live driver (backtest_live_entry), evaluate_config dispatch on engine="live_mtf_rsi", and diagnose_live_entry_volume.py. Unification complete (live == harness by construction for the entry). ATR fix + evaluator purity + CLI cleanup (single call, dead logic removed, bars_aligned ownership, test_evaluator.py) also landed. No config cleared the strict OOS gates (MIN_TRADES=30 on both IS/OOS + OOS PF>=1.20 + positive PnL) on realistic engine; low-frequency is structural given the full gate stack + V* confirmation + Rule C. Branch B path (document reality; position as selective filter/alert aid) is the evidence-based posture.

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
