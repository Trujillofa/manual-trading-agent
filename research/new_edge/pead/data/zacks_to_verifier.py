"""Map Zacks Nasdaq tables (EEH/ES/MT) into verify_pead_data snapshot CSVs.

Data-prep only — authorized before DATA_PASS. Relationship code remains blocked.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

EEH_COLUMNS = (
    "m_ticker",
    "per_end_date",
    "per_type",
    "obs_date",
    "eps_mean_est",
)
ES_COLUMNS = (
    "m_ticker",
    "per_end_date",
    "per_type",
    "act_rpt_date",
    "act_rpt_time",
    "act_rpt_code",
    "eps_act",
    "eps_mean_est",
    "per_fisc_year",
    "per_fisc_qtr",
)
MT_COLUMNS = (
    "m_ticker",
    "ticker",
    "active_ticker_flag",
    "asset_type",
    "zacks_x_sector_desc",
)


class JoinPolicy(str, Enum):
    STRICT = "strict"
    RELAXED_AMC = "relaxed_amc"


@dataclass(frozen=True)
class CollisionReport:
    total_es_rows: int
    joined_rows: int
    same_day_total: int
    same_day_by_code: dict[str, int]
    strict_retained: int
    relaxed_retained: int


def _security_id(m_ticker: str) -> str:
    return f"ZACKS_{str(m_ticker).strip().upper()}"


def _fiscal_period(row: pd.Series) -> str:
    year = row.get("per_fisc_year")
    quarter = row.get("per_fisc_qtr")
    if pd.notna(year) and pd.notna(quarter):
        return f"{int(year)}Q{int(quarter)}"
    end = str(row.get("per_end_date", ""))[:10]
    return end or "unknown"


def _parse_act_time(act_rpt_time: object) -> tuple[int, int] | None:
    if pd.isna(act_rpt_time) or act_rpt_time is None:
        return None
    text = str(act_rpt_time).strip()
    if not text or ":" not in text:
        return None
    hour_str, minute_str = text.split(":", 1)
    return int(hour_str), int(minute_str)


def announcement_ts_from_es_row(row: pd.Series) -> pd.Timestamp:
    """Build timezone-aware announcement timestamp from ZACKS/ES fields."""
    date_text = str(row["act_rpt_date"])[:10]
    base = pd.Timestamp(date_text).tz_localize(ET)
    parsed = _parse_act_time(row.get("act_rpt_time"))
    code = str(row.get("act_rpt_code", "")).strip().upper()

    if parsed is not None:
        hour, minute = parsed
        return base.replace(hour=hour, minute=minute).tz_convert(UTC)

    defaults: dict[str, tuple[int, int]] = {
        "BTO": (9, 30),
        "DTM": (12, 0),
        "AMC": (16, 5),
    }
    hour, minute = defaults.get(code, (16, 0))
    return base.replace(hour=hour, minute=minute).tz_convert(UTC)


def estimate_observed_ts_from_obs_date(obs_date: object) -> pd.Timestamp:
    """obs_date labels revision receipt day; use start-of-day ET (conservative)."""
    date_text = str(obs_date)[:10]
    return pd.Timestamp(date_text).tz_localize(ET).tz_convert(UTC)


def _eeh_subset(
    eeh: pd.DataFrame,
    *,
    m_ticker: str,
    per_end_date: str,
    per_type: str,
) -> pd.DataFrame:
    return eeh[
        (eeh["m_ticker"] == m_ticker)
        & (eeh["per_end_date"].astype(str).str[:10] == str(per_end_date)[:10])
        & (eeh["per_type"] == per_type)
    ]


def _last_obs_date(
    eeh: pd.DataFrame,
    *,
    m_ticker: str,
    per_end_date: str,
    per_type: str,
    act_rpt_date: str,
    policy: JoinPolicy,
    act_rpt_code: str,
) -> str | None:
    subset = _eeh_subset(
        eeh,
        m_ticker=m_ticker,
        per_end_date=per_end_date,
        per_type=per_type,
    )
    if subset.empty:
        return None

    act_day = str(act_rpt_date)[:10]
    obs_dates = subset["obs_date"].astype(str).str[:10]
    prior = obs_dates[obs_dates < act_day]
    if not prior.empty:
        return str(prior.max())

    if policy == JoinPolicy.RELAXED_AMC and str(act_rpt_code).upper() == "AMC":
        same_day = obs_dates[obs_dates == act_day]
        if not same_day.empty:
            return str(same_day.max())
    return None


def _max_obs_on_or_before(
    eeh: pd.DataFrame,
    *,
    m_ticker: str,
    per_end_date: str,
    per_type: str,
    act_rpt_date: str,
) -> str | None:
    subset = _eeh_subset(
        eeh,
        m_ticker=m_ticker,
        per_end_date=per_end_date,
        per_type=per_type,
    )
    if subset.empty:
        return None
    act_day = str(act_rpt_date)[:10]
    obs_dates = subset["obs_date"].astype(str).str[:10]
    on_or_before = obs_dates[obs_dates <= act_day]
    if on_or_before.empty:
        return None
    return str(on_or_before.max())


def collision_report(eeh: pd.DataFrame, es: pd.DataFrame) -> CollisionReport:
    """Summarize same-day obs_date vs act_rpt_date collisions for sample QA."""
    same_day_by_code: dict[str, int] = {}
    joined = 0
    same_day_total = 0
    strict_retained = 0
    relaxed_retained = 0

    for _, row in es.iterrows():
        act_day = str(row["act_rpt_date"])[:10]
        max_obs = _max_obs_on_or_before(
            eeh,
            m_ticker=str(row["m_ticker"]),
            per_end_date=str(row["per_end_date"]),
            per_type=str(row["per_type"]),
            act_rpt_date=act_day,
        )
        if max_obs is None:
            continue
        joined += 1

        strict_obs = _last_obs_date(
            eeh,
            m_ticker=str(row["m_ticker"]),
            per_end_date=str(row["per_end_date"]),
            per_type=str(row["per_type"]),
            act_rpt_date=act_day,
            policy=JoinPolicy.STRICT,
            act_rpt_code=str(row.get("act_rpt_code", "")),
        )
        relaxed_obs = _last_obs_date(
            eeh,
            m_ticker=str(row["m_ticker"]),
            per_end_date=str(row["per_end_date"]),
            per_type=str(row["per_type"]),
            act_rpt_date=act_day,
            policy=JoinPolicy.RELAXED_AMC,
            act_rpt_code=str(row.get("act_rpt_code", "")),
        )
        if strict_obs is not None:
            strict_retained += 1
        if relaxed_obs is not None:
            relaxed_retained += 1

        if max_obs == act_day:
            same_day_total += 1
            code = str(row.get("act_rpt_code", "UNK")).upper()
            same_day_by_code[code] = same_day_by_code.get(code, 0) + 1

    return CollisionReport(
        total_es_rows=len(es),
        joined_rows=joined,
        same_day_total=same_day_total,
        same_day_by_code=same_day_by_code,
        strict_retained=strict_retained,
        relaxed_retained=relaxed_retained,
    )


def build_earnings_events(
    eeh: pd.DataFrame,
    es: pd.DataFrame,
    *,
    policy: JoinPolicy = JoinPolicy.STRICT,
) -> pd.DataFrame:
    """Join ZACKS/EEH consensus at last qualifying obs_date to ZACKS/ES events."""
    rows: list[dict[str, object]] = []
    for _, row in es.iterrows():
        last_obs = _last_obs_date(
            eeh,
            m_ticker=str(row["m_ticker"]),
            per_end_date=str(row["per_end_date"]),
            per_type=str(row["per_type"]),
            act_rpt_date=str(row["act_rpt_date"]),
            policy=policy,
            act_rpt_code=str(row.get("act_rpt_code", "")),
        )
        if last_obs is None:
            continue

        consensus = eeh[
            (eeh["m_ticker"] == row["m_ticker"])
            & (eeh["obs_date"].astype(str).str[:10] == last_obs)
            & (eeh["per_end_date"].astype(str).str[:10] == str(row["per_end_date"])[:10])
            & (eeh["per_type"] == row["per_type"])
        ]
        if consensus.empty:
            continue
        eps_mean = consensus.iloc[-1]["eps_mean_est"]

        rows.append(
            {
                "security_id": _security_id(str(row["m_ticker"])),
                "ticker": str(row.get("ticker", row["m_ticker"])),
                "fiscal_period": _fiscal_period(row),
                "announcement_ts": announcement_ts_from_es_row(row).isoformat(),
                "estimate_observed_ts": estimate_observed_ts_from_obs_date(last_obs).isoformat(),
                "actual_eps": float(row["eps_act"]),
                "consensus_eps": float(eps_mean),
                "tradable_session": True,
                "stable_id": True,
            }
        )

    return pd.DataFrame(rows)


def build_security_master(mt: pd.DataFrame) -> pd.DataFrame:
    """Map ZACKS/MT to verifier security_master.csv (minimal trial columns)."""
    rows: list[dict[str, str]] = []
    for _, row in mt.iterrows():
        asset = str(row.get("asset_type", "COM")).upper()
        if asset not in {"COM", "CDN"}:
            continue
        rows.append(
            {
                "security_id": _security_id(str(row["m_ticker"])),
                "ticker": str(row.get("ticker", row["m_ticker"])),
                "security_type": "common",
                "list_date": "1900-01-01",
                "delist_date": "" if str(row.get("active_ticker_flag", "Y")) == "Y" else "",
            }
        )
    return pd.DataFrame(rows)


def build_sectors(mt: pd.DataFrame, *, as_of_date: str = "2015-01-01") -> pd.DataFrame:
    """Map current ZACKS/MT sector to point-in-time placeholder for trial pin."""
    rows: list[dict[str, str]] = []
    for _, row in mt.iterrows():
        sector = row.get("zacks_x_sector_desc")
        if pd.isna(sector) or not str(sector).strip():
            continue
        rows.append(
            {
                "security_id": _security_id(str(row["m_ticker"])),
                "as_of_date": as_of_date,
                "sector": str(sector),
            }
        )
    return pd.DataFrame(rows)


def write_snapshot(
    output_dir: Path,
    *,
    eeh: pd.DataFrame,
    es: pd.DataFrame,
    mt: pd.DataFrame | None = None,
    prices: pd.DataFrame | None = None,
    policy: JoinPolicy = JoinPolicy.STRICT,
) -> dict[str, Path]:
    """Write verifier-ready CSVs under output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    events = build_earnings_events(eeh, es, policy=policy)
    events_path = output_dir / "earnings_events.csv"
    events.to_csv(events_path, index=False)
    paths["earnings_events"] = events_path

    if mt is not None:
        master_path = output_dir / "security_master.csv"
        build_security_master(mt).to_csv(master_path, index=False)
        paths["security_master"] = master_path

        sectors_path = output_dir / "sectors.csv"
        build_sectors(mt).to_csv(sectors_path, index=False)
        paths["sectors"] = sectors_path

    if prices is not None:
        prices_path = output_dir / "daily_prices.csv"
        prices.to_csv(prices_path, index=False)
        paths["daily_prices"] = prices_path

    logger.info("Wrote Zacks snapshot to %s (%d events)", output_dir, len(events))
    return paths


_PIN_ALIASES: dict[str, tuple[str, ...]] = {
    "eeh": ("eeh.csv", "zeeh.csv"),
    "es": ("es.csv", "zes.csv"),
    "mt": ("mt.csv",),
    "prices": ("daily_prices.csv", "prices.csv"),
}


def _resolve_pin_file(pin_dir: Path, aliases: tuple[str, ...]) -> Path | None:
    for name in aliases:
        path = pin_dir / name
        if path.is_file():
            return path
    lower_map = {p.name.lower(): p for p in pin_dir.iterdir() if p.is_file()}
    for name in aliases:
        hit = lower_map.get(name.lower())
        if hit is not None:
            return hit
    return None


def load_pin_tables(pin_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None, pd.DataFrame | None]:
    """Load Zacks pin CSVs from a pinned trial directory."""
    if not pin_dir.is_dir():
        raise FileNotFoundError(f"pin directory does not exist: {pin_dir}")

    eeh_path = _resolve_pin_file(pin_dir, _PIN_ALIASES["eeh"])
    es_path = _resolve_pin_file(pin_dir, _PIN_ALIASES["es"])
    if eeh_path is None:
        raise FileNotFoundError(f"missing EEH table in {pin_dir} (tried {_PIN_ALIASES['eeh']})")
    if es_path is None:
        raise FileNotFoundError(f"missing ES table in {pin_dir} (tried {_PIN_ALIASES['es']})")

    eeh = pd.read_csv(eeh_path)
    es = pd.read_csv(es_path)

    mt_path = _resolve_pin_file(pin_dir, _PIN_ALIASES["mt"])
    mt = pd.read_csv(mt_path) if mt_path is not None else None

    prices_path = _resolve_pin_file(pin_dir, _PIN_ALIASES["prices"])
    prices = pd.read_csv(prices_path) if prices_path is not None else None

    return eeh, es, mt, prices


def build_etl_manifest(
    *,
    command: str,
    pin_dir: Path,
    output_dir: Path,
    policy: JoinPolicy,
    collision: CollisionReport,
    events_written: int,
) -> dict[str, object]:
    return {
        "command": command,
        "pin_dir": str(pin_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "join_policy": policy.value,
        "events_written": events_written,
        "collision_report": asdict(collision),
    }


def _command(args: argparse.Namespace) -> str:
    return (
        "python -m research.new_edge.pead.data.zacks_to_verifier "
        f"--pin {args.pin} --policy {args.policy} --out-snapshot {args.out_snapshot}"
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Map pinned Zacks EEH/ES (+ optional MT/prices) into verify_pead_data CSVs."
    )
    parser.add_argument("--pin", type=Path, required=True, help="Pinned Zacks trial directory")
    parser.add_argument(
        "--policy",
        required=True,
        choices=[p.value for p in JoinPolicy],
        help="EEH join policy: strict or relaxed_amc",
    )
    parser.add_argument(
        "--out-snapshot",
        type=Path,
        required=True,
        help="Output directory for verifier-ready snapshot CSVs",
    )
    args = parser.parse_args()

    if not args.pin.is_dir():
        parser.error(f"--pin must be an existing directory: {args.pin}")

    policy = JoinPolicy(args.policy)
    eeh, es, mt, prices = load_pin_tables(args.pin)
    collision = collision_report(eeh, es)

    paths = write_snapshot(
        args.out_snapshot,
        eeh=eeh,
        es=es,
        mt=mt,
        prices=prices,
        policy=policy,
    )
    events_written = len(pd.read_csv(paths["earnings_events"]))

    manifest = build_etl_manifest(
        command=_command(args),
        pin_dir=args.pin,
        output_dir=args.out_snapshot,
        policy=policy,
        collision=collision,
        events_written=events_written,
    )
    manifest_path = args.out_snapshot / "etl_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    logger.info(
        "Zacks ETL complete: policy=%s events=%d strict_retained=%d relaxed_retained=%d",
        policy.value,
        events_written,
        collision.strict_retained,
        collision.relaxed_retained,
    )
    print(
        f"events={events_written} strict_retained={collision.strict_retained} "
        f"relaxed_retained={collision.relaxed_retained} same_day={collision.same_day_total}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())