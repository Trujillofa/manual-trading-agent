# HTF Fib iterative refinement result (2026-07)

Generated: 2026-07-10 15:20 UTC

Locked gates unchanged: IS/OOS trades ≥ 30, OOS net PF ≥ 1.20, positive IS/OOS net PnL. Costs: 2 pip spread, 2 pip slip/fill, $3/order.

Negative-result override required; FX majors OHLC directional TA remains closed unless KEEP under these gates.

## Refinement rounds (≥3 distinct searchable spaces)

### R1_zone_volume

- **Change:** Widen Fib entry bands (golden/mid/wide/shallow) and soft RSI; raise informative trade count without new AND filters.
- Artifact: `results/htf_fib_refinement/R1_zone_volume.md`
- Seed: score=-246.5303 DISCARD IS n=474 OOS n=305
- Best: score=-4.7488 DISCARD IS n=40 OOS n=15 OOS net PF=0.923 OOS pnl%=-0.466
- Reasons: OOS trades 15 < 30; OOS net PF 0.92 < 1.20; IS net PnL -1.53% <= 0; OOS net PnL -0.47% <= 0
- KEEP in round: False

### R2_exits

- **Change:** Exit/hold grid on soft wide-zone family: TP/SL ATR mults and max hold; candle on/off. Distinct from R1 zone focus.
- Artifact: `results/htf_fib_refinement/R2_exits.md`
- Seed: score=-206.9965 DISCARD IS n=541 OOS n=350
- Best: score=-7.0697 DISCARD IS n=25 OOS n=13 OOS net PF=0.753 OOS pnl%=-1.169
- Reasons: IS trades 25 < 30; OOS trades 13 < 30; OOS net PF 0.75 < 1.20; IS net PnL -1.97% <= 0; OOS net PnL -1.17% <= 0
- KEEP in round: False

### R3_structure

- **Change:** Invalidation mode (none/wick/close) + one-entry + EMA stack toggle on soft mid/wide zones; structural A/B vs prior rounds.
- Artifact: `results/htf_fib_refinement/R3_structure.md`
- Seed: score=-24.0104 DISCARD IS n=36 OOS n=40
- Best: score=-9.3809 DISCARD IS n=20 OOS n=18 OOS net PF=0.891 OOS pnl%=-0.701
- Reasons: IS trades 20 < 30; OOS trades 18 < 30; OOS net PF 0.89 < 1.20; IS net PnL -7.06% <= 0; OOS net PnL -0.70% <= 0
- KEEP in round: False

## Single best under fixed score (across all rounds)

- **Round:** `R1_zone_volume`
- **Verdict:** DISCARD
- **Score:** -4.7488
- **Strategy:** `soft_baseline_1d_zmid_l5r2_rsi45-55_tp2_sl2.5_hold96_invnone_sw0_vw0`
- **IS:** trades=40, net_pf=0.915, pnl%=-1.531
- **OOS:** trades=15, net_pf=0.923, pnl%=-0.466
- **Config:** `{"atr_period": 14, "combo_id": "soft_baseline", "fib_timeframe": "1d", "fib_zone": "mid", "invalidate_mode": "none", "invalidate_swing": false, "left_bars": 5, "max_hold_bars": 96, "one_entry_per_swing": true, "require_anchored_vwap": false, "require_candle": true, "require_ema_stack": false, "require_liquidity_sweep": false, "require_mtf_rsi": false, "right_bars": 2, "rsi_long": 45.0, "rsi_short": 55.0, "sl_atr": 2.5, "tp_atr": 2.0}`
- **Reasons:** OOS trades 15 < 30; OOS net PF 0.92 < 1.20; IS net PnL -1.53% <= 0; OOS net PnL -0.47% <= 0

**all-DISCARD / profit not found:** No configuration cleared the locked promotion gates across three distinct refinement rounds. Best-by-score candidate is named above (not a promotion claim).

This is consistent with the locked FX directional-TA and HTF Fib negative results: expanding Fib zones / exits / invalidation did not produce an OOS-validated net edge under realistic costs.
