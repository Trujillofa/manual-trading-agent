# cTrader carry sub-lane discard - 2026-06-12

## Verdict

DISCARD for the Hetzner cTrader account/broker setup.

This closes only the cTrader-account carry attempt. It does not disprove carry as an edge family on brokers/accounts that provide real nonzero overnight financing.

## Evidence

Real broker data was fetched from the live Hetzner cTrader account through `ProtoOASymbolByIdReq`.

Resolved symbols:

- `AUD/JPY`
- `NZD/JPY`
- `AUD/USD`
- `NZD/USD`
- `USD/ZAR`

Unavailable in this account:

- `USD/TRY`
- `EUR/TRY`
- `GBP/TRY`

Returned swap values for every resolved pair:

- `long = 0.0`
- `short = 0.0`

Rollover metadata:

- `swapRollover3Days = 3`, i.e. Wednesday triple swap.

The resulting gross carry falsifier with real cTrader data produced:

- Gross PF: `0.000`
- Positive carry income: `$0.00`
- Negative funding cost: `$0.00`
- Net carry after entry drag: `-$27.03`
- Verdict: `DISCARD_REAL_DATA`

## Interpretation

This cTrader account appears swap-free, has no exposed overnight financing differential for the resolved symbols, or routes financing outside the symbol-detail fields used by the Open API.

Because the strategy premise is financing yield, a zero-swap account cannot support this carry lane. There is no reason to run price P&L, IS/OOS, carry-crash stress, or parameter variants on this account.

## Stop rule

Do not continue carry research on this Hetzner cTrader account unless a cTrader UI export or broker statement contradicts the API result with nonzero long/short financing rates for a usable universe.

If a future broker/account provides nonzero swaps, open a new broker-specific carry data artifact and rerun the existing verifier and gross carry falsifier before any further work.

## Next options

1. Confirm in the cTrader UI or broker statement whether this account is swap-free.
2. If confirmed, keep this sub-lane closed.
3. If pursuing carry further, test a different broker/account with nonzero long/short swaps and a usable symbol universe.
4. If no such broker/account is available, move to the next edge family in the Grok loop, likely stat-arb.
