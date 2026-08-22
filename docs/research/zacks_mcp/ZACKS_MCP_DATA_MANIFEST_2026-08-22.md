# Zacks MCP Data Manifest — 2026-08-22

Generated `2026-08-22T18:32:46Z` from `research/new_edge/zacks_mcp/data/provenance/zacks_mcp_schema_probe_2026-08.json`.

Source: Zacks Investment Research (MCP `https://mcp.zacksdata.com`). Numeric statement and holdings values are not stored in-repo.

| Field | Value |
|---|---|
| Schema verdict | `SCHEMA_PASS` |
| Alpha / KEEP-path verdict | `BLOCKED` |
| Annual statement years observed | 5 |
| ETF as-of history parameter | `False` |
| PEAD fields present | none |

## Issues

- annual statement history is 5y; at least 10y required for a factor KEEP path
- get_etf_holdings has no as-of/history parameter; holdings-change backtests are unauthorized
- estimate_observed_ts / announcement timestamps are absent; PEAD remains BLOCKED

## Allowed next step

Owner may pin a licensed historical extract (statements >=10y and/or dated ETF holdings) and re-run this verifier. Relationship and strategy code stay unauthorized until the ledger records `DATA_PASS`.
