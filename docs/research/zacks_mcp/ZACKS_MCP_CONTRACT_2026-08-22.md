# Zacks MCP Lane Contract — 2026-08-22

## Decision

Official Zacks MCP (`https://mcp.zacksdata.com`, Cursor server id `zacks`
in `~/.cursor/mcp.json`) is a new data-proof lane. This contract authorizes
a schema inventory and, only after `DATA_PASS`, one later relationship
falsifier. It does not authorize a trading strategy, parameter search,
PEAD relationship code, paper trading, or production integration.

## Premise

Point-in-time North American financial statements (income, balance sheet,
cash flow, condensed snapshot) and daily ETF holdings from Zacks can
support a **fundamentals-factor** or **holdings-overlap** edge that this
repo has never tested. That is a different instrument class and data
family from FX OHLC-TA, carry, event-drift, COT, and PEAD surprises.

## Why this is not a closed lane

- Not FX directional TA / HTF Fib / TSMOM / vol-regime (no OHLC pattern).
- Not the discarded FX event-drift lane (no macro calendar surprise).
- Not PEAD. PEAD requires pre-announcement consensus observation
  timestamps. Live MCP tools on 2026-08-22 are snapshot, income,
  balance, cash-flow, and ETF holdings only.
- Not a reopen of Vantage/Hetzner carry.

## Data required

| Domain | Required for DATA_PASS | 2026-08-22 MCP fact |
|---|---|---|
| Statement PIT fields | ticker, period_end, period type, revenue, earnings, cash flow, assets/equity | Present on snapshot/income tools |
| Statement history | >=10 annual years, >=500 names for a factor test | Probe: 5 annual years (AAPL/NEM); `periods=40` still returned 5 |
| ETF holdings history | dated as-of snapshots, not only today | `get_etf_holdings(symbol, top_n)` — no as-of param; as_of is current day |
| Prices + costs | daily adjusted OHLC, commissions, borrow | **Not in this MCP** |
| PEAD estimates | `estimate_observed_ts`, announcement tz | **Absent** — PEAD stays BLOCKED |

Values are in millions of the reporting currency unless noted. Attribute
every number to Zacks Investment Research. Do not pin live statement
values or holdings weights in git unless a written license allows it.

## Cost model

Not applicable until a relationship test is authorized. Any later equity
test must use round-trip commission + spread + borrow **before** KEEP.
ETF-holdings research that cannot date the holdings is observation-only.

## First falsification test

Schema inventory only:

1. Required MCP tools and columns are present.
2. PEAD timestamp fields are absent (or, if a future tool adds them, they
   still do not count as PEAD `DATA_PASS` without the PEAD contract gates).
3. Annual statement years >= 10 **and/or** ETF holdings expose a historical
   as-of parameter.

Fail any KEEP-path gate → `BLOCKED`, not DISCARD.

## Pass gate

Ledger may record `DATA_PASS` only when a licensed extract proves:

- >=10 years of point-in-time statements **or** dated ETF holdings history
  covering the test universe, and
- a separate, documented price/cost source, and
- redistribution terms that allow a local pin.

Schema-only `SCHEMA_PASS` is not `DATA_PASS`.

## Stop gate

- Treat current ETF holdings as a tradable historical signal → stop.
- Treat this MCP as a PEAD sample → stop.
- Write strategy/relationship code before `DATA_PASS` → stop.
- Reopen a closed FX/TA/carry/event lane with Zacks as decoration → stop.

## First command

```bash
.venv/bin/python -m research.new_edge.zacks_mcp.data.verify_zacks_mcp
```
