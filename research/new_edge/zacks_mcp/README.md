# Zacks MCP lane (`new_edge`)

Data-proof only. Cursor MCP server `zacks` → `https://mcp.zacksdata.com`.

This is **not** a PEAD unblock. The MCP exposes standardized financial
statements and **current** ETF holdings. It does not expose
`estimate_observed_ts` or announcement timestamps.

```bash
.venv/bin/python -m research.new_edge.zacks_mcp.data.verify_zacks_mcp \
  --provenance research/new_edge/zacks_mcp/data/provenance/zacks_mcp_schema_probe_2026-08.json \
  --output docs/research/zacks_mcp/ZACKS_MCP_DATA_MANIFEST_2026-08-22.md
```

Do not commit live statement values or holdings weights. Do not write
relationship or strategy code before a ledger `DATA_PASS`.
