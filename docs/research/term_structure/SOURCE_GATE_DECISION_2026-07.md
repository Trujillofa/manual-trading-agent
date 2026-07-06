# Term-Structure Roll-Yield Source Gate Decision — 2026-07-02

## Verdict: BLOCKED

This is a **source gate only**. No strategy logic, no Tier-B harness run, and no
live trading are authorized. The lane remains blocked until a concrete loader
passes the immutable gate on real data.

## Immutable gate (unchanged)

| Requirement | Threshold |
|---|---|
| Universe | Fixed 12-market commodity set (CL, NG, RB, HO, GC, SI, HG, ZC, ZS, ZW, LE, HE) |
| Markets passing | ≥10 of 12 |
| History per market | ≥15 complete years |
| Record shape | Individual contract-month daily bars |
| Required fields | open, high, low, settlement, open_interest |
| Metadata | expiry, contract identity, negative-price preservation |
| Roll calendar | OI-confirmed active contract derivation |
| Provenance | Machine-readable audit + licensing note |

## Provider comparison

| Source | Individual contracts | OHLC + OI | History (claimed) | Cost | Gate result | Evidence |
|---|---|---|---|---|---|---|
| **CME expanded PA2 (free FTP)** | yes (settlements) | settle only; no OHLC/OI | 11.69y (2014-01-02 → 2025-09-12 inventory) | free | **BLOCKED** | [CME SPAN overview](https://www.cmegroup.com/clearing/risk-management/span-overview.html), [PA2 layout](https://cmegroupclientsite.atlassian.net/wiki/spaces/pubsub/pages/457083445/Risk+Parameter+File+Layouts+for+the+Positional+Formats), `CME_FREE_DATA_AUDIT_2026-07-02.md` |
| **FirstRate Data** | yes (per-contract files in paid bundle) | daily OHLC + open interest in 1-day format | continuous from 2008-01-01; individual from CLZ08 per product page | paid (~$99.95/yr updates per symbol tier) | **UNVERIFIED — likely candidate** | [CL product page](https://firstratedata.com/i/futures/CL), [free CL sample](https://frd001.s3.us-east-2.amazonaws.com/frd_sample_futures_CL.zip), `firstrate_cl_sample_verification_2026-07.json` |
| **Norgate Data (Futures package)** | yes | settlement close + individual contracts; OI field not confirmed in public docs | ~1980 or first trade per market | paid ($270/12 mo) | **UNVERIFIED — likely candidate** | [Futures package](https://norgatedata.com/futurespackage.php), [data content tables](https://norgatedata.com/data-content-tables.php) |
| **CSI Data (Unfair Advantage)** | yes | claimed full futures history | decades (institutional) | paid | **UNVERIFIED** | vendor site only; no sample pulled |
| **Pinnacle Data Corp** | yes | commodity futures focus | deep (vendor claim) | paid | **UNVERIFIED** | vendor site only; no sample pulled |
| **Nasdaq Data Link / Quandl** | partial | varies by dataset | varies | paid | **UNVERIFIED** | per-market verification required |
| **yfinance** | no (continuous only) | no individual contract months | gaps | free | **INSUFFICIENT** | fails §2 three-object requirement |

## Sample verification performed (2026-07-02)

### Free CME path (concrete verifier)

```bash
python -m research.new_edge.term_structure.data.verify_term_structure_data \
  --output docs/research/term_structure/CME_FREE_DATA_AUDIT_2026-07-02.md \
  --provenance research/new_edge/term_structure/data/provenance/term_structure_source_gate_2026-07.json
```

Result: **BLOCKED** — 3,043 archive files, 11.69 years coverage, 0/12 markets pass,
PA2 provides settlement only. Parser smoke against a live daily PA2 file was not
re-run because the public FTP layout now publishes year-level bundles under
`/span/archive/cme/YYYY/` rather than the daily `cme.YYYYMMDD.s.pa2.zip` paths
used in the June audit; the June parser smoke (533 settlements, 12/12 symbols)
remains the best available free-path evidence.

Blocking issues (unchanged):

1. Coverage 11.69y < 15y required.
2. Missing open, high, low, open_interest on PA2 settlements.
3. Cannot derive OI-confirmed roll calendar.

### FirstRate free sample (desk verification only)

Downloaded `frd_sample_futures_CL.zip` (public sample URL on product page).

- Continuous CL 1-day CSV columns: `timestamp, open, high, low, close, volume, open interest`
- Readme states individual contracts ship as one file per contract (CLZ08 → CLZ24)
- Sample bundle contains **continuous** CL only, not individual contract files
- Cannot confirm all 12 fixed-universe symbols or 15-year depth without purchase

Desk assessment: **promising paid candidate**, not `DATA_PASS`.

## Owner decision required (purchase gate)

To move from BLOCKED toward `DATA_PASS`, the owner must authorize **one** paid
source purchase so the project can:

1. Implement a concrete paid loader (not `SyntheticLoader`).
2. Run `verify_term_structure_data` (or successor) across all 12 markets.
3. Confirm ≥10 markets × ≥15 years × full OHLC/OI + expiry metadata.
4. Record machine-readable provenance and licensing.

**Leading candidates (in order):**

1. **FirstRate Data** — explicit individual-contract files, daily OI in schema,
   commodity symbols in "Most Active 130" bundle; verify all 12 symbols before buy.
2. **Norgate Data Futures package** — decades of individual contracts; confirm OI
   field availability and all 12 symbols via trial or vendor inquiry before buy.

Do **not** purchase both. Pick one, verify, then append a new ledger row.

## Tier B status

**Not authorized.** No roll-yield strategy code, optimization, or harness execution
until `DATA_PASS` is recorded on a concrete loader.

## If owner approves GEX pivot instead

If term structure remains blocked and the owner approves pivoting, Wave 3 (SPX GEX
data proof) is the next conditional lane. Do not implement GEX in this branch.

## Machine-readable provenance

- `research/new_edge/term_structure/data/provenance/term_structure_source_gate_2026-07.json`
- `research/new_edge/term_structure/data/provenance/firstrate_cl_sample_verification_2026-07.json`