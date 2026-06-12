Real Broker Swap / Rollover Data Instructions
==========================================

This lane (carry) is currently BLOCKED on data. The file `verified_swap_rates_2026-06.json` is a **template** containing illustrative rates only.

To unblock:

1. Obtain real long/short overnight financing (swap) rates from your broker:
   - Preferred: export from account statement / trade history for a recent date.
   - Alternative: broker API (OANDA, cTrader, IB, etc.) that returns current swap rates per pair.

2. Record the following metadata (fill the JSON exactly):
   - "source_date": the date of the statement / snapshot (YYYY-MM-DD)
   - "broker": exact broker name / account type
   - "retrieved": how you obtained it ("statement export 2026-06-XX", "REST API call", etc.)
   - "units": confirm "pips per day per standard lot (positive = receive when long the pair)" or note any difference
   - "rollover_rule": document your broker's exact rule (usually "3x on Wednesday for most pairs; holidays may be 0x or 1x; some pairs have different day")
   - "notes": any important caveats (holiday calendar, triple-swap exceptions, how rates are quoted in your account currency, point value if not standard lot, etc.)

3. Replace the values inside "rates" with the actual numbers from your source. Keep the pair keys in the same format ("EUR/USD" etc.).

4. After editing the JSON, re-run **in order**:
   python -m research.new_edge.carry.data.verify_carry_data --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_DATA_MANIFEST_2026-06-11.md --quick
   python -m research.new_edge.carry.gross_carry_test --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_GROSS_RESULTS_2026-06-12.md

5. Append a fresh ledger row (the scripts will guide you).

Only if the new run with real data still produces GROSS_PASS_REAL_DATA (leg-level net carry positive after funding costs + drag, PF > 1) may the lane move past the sample gate.

At that point the next work is:
- Add full entry/turnover costs beyond the initial 3-pip drag
- Include price P&L (for risk / DD simulation)
- Chronological IS/OOS split
- Carry-crash stress periods (2008, 2015 CHF, etc.)
- Concentration and robustness checks
- Then net OOS metrics and full pass gates per CARRY_CONTRACT.

Do not start stat-arb or other lanes until this one has a clean real-data gross + subsequent gated results.

The current sample rates are useful only to prove the gross falsifier harness and leg-level accounting work correctly.