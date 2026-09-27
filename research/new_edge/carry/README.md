# Carry / Swap-Aware FX Portfolio lane (new_edge)

## Status (2026-08-13)

Vantage MT5 broker-true static carry: `DATA_PASS` → thin pip-correct `GROSS_PASS_REAL_DATA` → net+IS/OOS **`DISCARD_REAL_DATA`** (OOS PF 1.043 < 1.20).

Do **not** retune legs/costs on this prototype. Hetzner cTrader zero-swap remains CLOSED_DISCARD.

See `docs/research/lanes/LANE3_BROKER_CARRY_2026-08.md`.

## Structure

- `docs/research/carry/` — contracts, manifests, gross/net results
- `research/new_edge/carry/` — verifier, `gross_carry_test`, `net_carry_test`
- `research/new_edge/research_ledger.jsonl` — ledger

## Commands

```bash
.venv/bin/python -m research.new_edge.carry.data.verify_carry_data \
  --rates research/new_edge/carry/data/verified_swap_rates_VANTAGE_2026-08-13.json --quick ...

.venv/bin/python -m research.new_edge.carry.gross_carry_test \
  --rates ... --economics auto ...

.venv/bin/python -m research.new_edge.carry.net_carry_test \
  --rates ... --economics auto --is-end 2021-12-31 ...
```
