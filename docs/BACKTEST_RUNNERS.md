# Backtest runners — inventory

Offline research scripts. **None of these is a live-go or promote path.**
They do not send broker orders (`OrderSend` or otherwise). Paper/live alerts
stay in `src.cli scan` / Telegram, which are out of scope here.

Clock: unless noted, bar timestamps are UTC. Session filters use the bar's
UTC hour, not broker-server time.

Shared helper (not an engine): `src.backtest.cost_book.CostBook` — frozen
spread/slip/commission/size. Units are documented on the class. Runners still
own their own walk loops.

| Runner | How to run | Fill | Costs | Split | Session clock |
|---|---|---|---|---|---|
| HTF Fib | `python scripts/run_htf_fib_backtest.py --pairs GBP/USD --days 365` | Close signal, next-bar open | CostBook 2/2 pip + $3/side | 65/35; configs are preregistered (OOS is judge only) | Not session-aware; UTC bars |
| SMC | `python scripts/run_smc_backtest.py --pairs GBP/USD --days 365` | Same as HTF | Same CostBook | 65/35; **preregistered rank is IS-only** | Not session-aware; UTC bars |
| RSI+MA+HH/LL | `python scripts/run_rsi_ma_hh_ll_backtest.py --pairs EUR/USD --days 58` | Close signal, next-bar open | Same CostBook | 65/35; grid rank uses develop only | Optional UTC hour filter (`use_session`) |
| Donchian | `python scripts/run_donchian_backtest.py --pairs EUR/USD --days 365 --sweep baseline` | Close signal, next-bar open | Same CostBook | 65/35; sweep rank uses develop only | Optional UTC hour filter |
| Pivot | `python scripts/run_pivot_backtest.py --pairs EUR/USD --days 365 --entry-types WEEKLY` | Close signal, next-bar open | Same CostBook | 65/35; sweep rank uses develop only | SESSION uses UTC 07–17 / 13–22 |
| Enhanced engine | `python -m src.cli backtest-enhanced --pair EUR/USD` | Close signal, next-bar open, stop-first | Same CostBook | Replay-only (no grid). CLI prints 65/35 counts; holdout unused | Not session-aware |

Related scripts left alone: `optimize_htf_fib_backtest.py` (already IS-only rank),
`evaluate_htf_fib_accounts.py`, `research/evaluate.py` / autosearch (already
split data before the runner; SMC autosearch ranks on **validation**, not the
final holdout), `run_entry_optimization.py`, `run_confirmation_bakeoff.py`.
