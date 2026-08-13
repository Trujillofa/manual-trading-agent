# Branch B operating posture (2026-08)

**Status:** production mode of record for `manual-trading-agent` on Hetzner.

## What Branch B is

A **paper-mode decision-support agent**:

- Multi-asset scanner (XAU/USD, BTC/USD, OIL, NASDAQ)
- RSI multi-timeframe setup classification + EMA 20/50 crossover context
- ETR Market Terminal change-only Telegram alerts + forward shadow logs
- Audit / OHLCV persistence for later research

## What Branch B is not

- Not a KEEP / validated edge
- Not an autonomous execution system
- Not a reason to increase lot size, leverage, or capital allocation
- Not a reopen of closed FX directional-TA or other discarded research lanes

## Operator rules

1. Treat every Telegram alert as **discretionary context**.
2. Do not map alert count → trading frequency or “edge is working.”
3. Use ETR shadow / audit logs as **evidence collection**, not as live sizing inputs, until a written price-basis audit and KEEP gates pass.
4. New profitability work happens on **isolated research branches/worktrees** under the harness rules — not by retuning live Branch B gates for P&L.

## Related docs

- `docs/MANUAL_TRADING_AGENT_OPERATOR_GUIDE.md`
- `docs/PROJECT_STATUS_2026-06.md`
- `docs/research/PROGRAM_DECISION_MEMO_2026-06-20.md`
- `docs/research/PROGRAM_DECISION_MEMO_ADDENDUM_2026-06-24.md`
