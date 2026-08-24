# How to use the Zacks MCP from this repo

**Status:** research HOWTO · no orders · not a PEAD unblock
**Contract:** [ZACKS_MCP_CONTRACT_2026-08-22.md](ZACKS_MCP_CONTRACT_2026-08-22.md)

The live Cursor config lives in `~/.cursor/mcp.json` (user-level, not
committed):

```json
{
  "mcpServers": {
    "zacks": {
      "type": "http",
      "url": "https://mcp.zacksdata.com"
    }
  }
}
```

Do **not** commit a project `.mcp.json` with secrets or LAN URLs. The
Zacks server URL is public; MT5 official MCP in the same user file is
not — keep `MT5_MCP_TOKEN` out of git.

## Tools (2026-08-22)

| Tool | Use |
|---|---|
| `get_company_snapshot` | Condensed PIT statements |
| `get_income_statement` | Income-statement detail |
| `get_balance_sheet` | Balance-sheet detail |
| `get_cash_flow` | Cash-flow detail |
| `get_etf_holdings` | Current top holdings by weight |

All statement tools take `tickers`, `period` (`A` / `Q` / both), and
`periods` (default 4, max 40). Holdings take `symbol` and `top_n` (max 100).

Attribute printed figures: "Source: Zacks Investment Research".

## What this does not do

- Earnings consensus history / `estimate_observed_ts` (PEAD stays BLOCKED)
- Historical ETF holdings as-of dates
- Broker prices, borrow, or execution
- Live trading

## First local command

```bash
.venv/bin/python -m research.new_edge.zacks_mcp.data.verify_zacks_mcp
```
