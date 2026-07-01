"""Official CFTC Legacy Futures Only data access and normalization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pandas as pd

CFTC_DATASET_ID = "6dca-aqww"
CFTC_DATASET_NAME = "Legacy - Futures Only"
CFTC_RESOURCE_URL = f"https://publicreporting.cftc.gov/resource/{CFTC_DATASET_ID}.json"
CFTC_METADATA_URL = f"https://publicreporting.cftc.gov/api/views/{CFTC_DATASET_ID}"
CFTC_SOURCE_PAGE = "https://publicreporting.cftc.gov/stories/s/r4w3-av2u"
CFTC_REPORT_DESCRIPTION = "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm"

PAGE_SIZE = 50_000
USER_AGENT = "manual-trading-agent-cot-data-proof/1.0"

SOURCE_FIELDS: tuple[str, ...] = (
    "cftc_contract_market_code",
    "market_and_exchange_names",
    "report_date_as_yyyy_mm_dd",
    "open_interest_all",
    "noncomm_positions_long_all",
    "noncomm_positions_short_all",
    "commodity_group_name",
    "commodity_subgroup_name",
    "commodity_name",
    "contract_units",
    "futonly_or_combined",
)


@dataclass(frozen=True)
class MarketSpec:
    """One pre-registered CFTC contract market."""

    code: str
    symbol: str
    market_name: str
    sector: str


FIXED_UNIVERSE: tuple[MarketSpec, ...] = (
    MarketSpec("002602", "CORN", "CORN - CHICAGO BOARD OF TRADE", "grains"),
    MarketSpec("005602", "SOYBEANS", "SOYBEANS - CHICAGO BOARD OF TRADE", "grains"),
    MarketSpec("007601", "SOYBEAN_OIL", "SOYBEAN OIL - CHICAGO BOARD OF TRADE", "grains"),
    MarketSpec("026603", "SOYBEAN_MEAL", "SOYBEAN MEAL - CHICAGO BOARD OF TRADE", "grains"),
    MarketSpec("039601", "ROUGH_RICE", "ROUGH RICE - CHICAGO BOARD OF TRADE", "grains"),
    MarketSpec(
        "057642",
        "LIVE_CATTLE",
        "LIVE CATTLE - CHICAGO MERCANTILE EXCHANGE",
        "livestock",
    ),
    MarketSpec(
        "054642",
        "LEAN_HOGS",
        "LEAN HOGS - CHICAGO MERCANTILE EXCHANGE",
        "livestock",
    ),
    MarketSpec("073732", "COCOA", "COCOA - ICE FUTURES U.S.", "softs"),
    MarketSpec("083731", "COFFEE", "COFFEE C - ICE FUTURES U.S.", "softs"),
    MarketSpec("080732", "SUGAR", "SUGAR NO. 11 - ICE FUTURES U.S.", "softs"),
    MarketSpec("088691", "GOLD", "GOLD - COMMODITY EXCHANGE INC.", "metals"),
    MarketSpec("084691", "SILVER", "SILVER - COMMODITY EXCHANGE INC.", "metals"),
    MarketSpec(
        "076651",
        "PLATINUM",
        "PLATINUM - NEW YORK MERCANTILE EXCHANGE",
        "metals",
    ),
    MarketSpec(
        "075651",
        "PALLADIUM",
        "PALLADIUM - NEW YORK MERCANTILE EXCHANGE",
        "metals",
    ),
    MarketSpec(
        "232741",
        "AUD",
        "AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
        "currencies",
    ),
    MarketSpec(
        "090741",
        "CAD",
        "CANADIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
        "currencies",
    ),
    MarketSpec("099741", "EUR", "EURO FX - CHICAGO MERCANTILE EXCHANGE", "currencies"),
    MarketSpec(
        "097741",
        "JPY",
        "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE",
        "currencies",
    ),
    MarketSpec(
        "095741",
        "MXN",
        "MEXICAN PESO - CHICAGO MERCANTILE EXCHANGE",
        "currencies",
    ),
    MarketSpec(
        "092741",
        "CHF",
        "SWISS FRANC - CHICAGO MERCANTILE EXCHANGE",
        "currencies",
    ),
    MarketSpec("1170E1", "VIX", "VIX FUTURES - CBOE FUTURES EXCHANGE", "volatility"),
    MarketSpec(
        "240743",
        "NIKKEI_YEN",
        "NIKKEI STOCK AVERAGE YEN DENOM - CHICAGO MERCANTILE EXCHANGE",
        "equity_index",
    ),
    MarketSpec(
        "13874+",
        "SP500",
        "S&P 500 Consolidated - CHICAGO MERCANTILE EXCHANGE",
        "equity_index",
    ),
)


@dataclass(frozen=True)
class FetchResult:
    """Raw API response plus reproducibility metadata."""

    rows: tuple[dict[str, Any], ...]
    query_urls: tuple[str, ...]
    metadata: dict[str, Any]
    retrieved_at: datetime


def _socrata_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_query_params(
    start: date,
    end: date,
    universe: tuple[MarketSpec, ...] = FIXED_UNIVERSE,
    *,
    offset: int = 0,
    limit: int = PAGE_SIZE,
) -> dict[str, str]:
    """Build a deterministic, inclusive CFTC PRE query."""
    if start > end:
        raise ValueError(f"start {start} must not be after end {end}")
    if not universe:
        raise ValueError("universe must contain at least one market")

    codes = ",".join(_socrata_literal(market.code) for market in universe)
    start_ts = f"{start.isoformat()}T00:00:00.000"
    end_ts = f"{end.isoformat()}T00:00:00.000"
    return {
        "$select": ",".join(SOURCE_FIELDS),
        "$where": (
            f"cftc_contract_market_code in ({codes}) "
            f"and report_date_as_yyyy_mm_dd between '{start_ts}' and '{end_ts}'"
        ),
        "$order": "cftc_contract_market_code,report_date_as_yyyy_mm_dd",
        "$limit": str(limit),
        "$offset": str(offset),
    }


def fetch_legacy_rows(
    start: date,
    end: date,
    universe: tuple[MarketSpec, ...] = FIXED_UNIVERSE,
    *,
    client: httpx.Client | None = None,
) -> FetchResult:
    """Fetch all requested rows and current dataset metadata from CFTC PRE."""
    owns_client = client is None
    active_client = client or httpx.Client(
        timeout=httpx.Timeout(60.0),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    rows: list[dict[str, Any]] = []
    query_urls: list[str] = []

    try:
        metadata_response = active_client.get(CFTC_METADATA_URL)
        metadata_response.raise_for_status()
        metadata = metadata_response.json()

        offset = 0
        while True:
            params = build_query_params(start, end, universe, offset=offset)
            response = active_client.get(CFTC_RESOURCE_URL, params=params)
            response.raise_for_status()
            page = response.json()
            if not isinstance(page, list):
                raise ValueError("CFTC PRE response was not a JSON row array")
            rows.extend(page)
            query_urls.append(str(response.request.url))
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    finally:
        if owns_client:
            active_client.close()

    return FetchResult(
        rows=tuple(rows),
        query_urls=tuple(query_urls),
        metadata=metadata,
        retrieved_at=datetime.now(UTC),
    )


def normalize_rows(
    rows: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    universe: tuple[MarketSpec, ...] = FIXED_UNIVERSE,
) -> pd.DataFrame:
    """Normalize CFTC source rows and derive no-lookahead availability fields."""
    if not rows:
        return pd.DataFrame(
            columns=[
                "market_code",
                "symbol",
                "market_name",
                "sector",
                "report_date",
                "available_date",
                "open_interest",
                "noncommercial_long",
                "noncommercial_short",
                "net_noncommercial",
                "net_noncommercial_pct_oi",
                "commodity_group",
                "commodity_subgroup",
                "commodity_name",
                "contract_units",
            ]
        )

    missing_columns = sorted({field for row in rows for field in SOURCE_FIELDS if field not in row})
    if missing_columns:
        raise ValueError(f"CFTC rows missing required fields: {missing_columns}")

    frame = pd.DataFrame(rows)
    frame = frame.rename(
        columns={
            "cftc_contract_market_code": "market_code",
            "market_and_exchange_names": "market_name",
            "report_date_as_yyyy_mm_dd": "report_date",
            "open_interest_all": "open_interest",
            "noncomm_positions_long_all": "noncommercial_long",
            "noncomm_positions_short_all": "noncommercial_short",
            "commodity_group_name": "commodity_group",
            "commodity_subgroup_name": "commodity_subgroup",
        }
    )

    frame["report_date"] = pd.to_datetime(frame["report_date"], errors="coerce").dt.normalize()
    for column in ("open_interest", "noncommercial_long", "noncommercial_short"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    required_normalized = (
        "market_code",
        "market_name",
        "report_date",
        "open_interest",
        "noncommercial_long",
        "noncommercial_short",
    )
    missing_counts = {
        column: int(frame[column].isna().sum())
        for column in required_normalized
        if frame[column].isna().any()
    }
    if missing_counts:
        raise ValueError(f"CFTC rows contain null or invalid required values: {missing_counts}")
    if (frame["open_interest"] <= 0).any():
        bad_rows = int((frame["open_interest"] <= 0).sum())
        raise ValueError(f"CFTC rows contain {bad_rows} non-positive open-interest values")
    report_types = set(frame["futonly_or_combined"].astype(str))
    if report_types != {"FutOnly"}:
        raise ValueError(f"Unexpected CFTC report types: {sorted(report_types)}")

    market_map = {market.code: market for market in universe}
    unknown_codes = sorted(set(frame["market_code"]) - set(market_map))
    if unknown_codes:
        raise ValueError(f"CFTC response contained unrequested market codes: {unknown_codes}")

    frame["symbol"] = frame["market_code"].map(lambda code: market_map[code].symbol)
    frame["sector"] = frame["market_code"].map(lambda code: market_map[code].sector)
    frame["available_date"] = frame["report_date"] + pd.Timedelta(days=6)
    frame["net_noncommercial"] = frame["noncommercial_long"] - frame["noncommercial_short"]
    frame["net_noncommercial_pct_oi"] = frame["net_noncommercial"] / frame["open_interest"]

    columns = [
        "market_code",
        "symbol",
        "market_name",
        "sector",
        "report_date",
        "available_date",
        "open_interest",
        "noncommercial_long",
        "noncommercial_short",
        "net_noncommercial",
        "net_noncommercial_pct_oi",
        "commodity_group",
        "commodity_subgroup",
        "commodity_name",
        "contract_units",
    ]
    return frame[columns].sort_values(["market_code", "report_date"]).reset_index(drop=True)


def canonical_payload_sha256(rows: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    """Hash source rows deterministically for provenance."""
    ordered_rows = sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            str(row.get("cftc_contract_market_code", "")),
            str(row.get("report_date_as_yyyy_mm_dd", "")),
        ),
    )
    payload = json.dumps(
        ordered_rows,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_metadata_sha256(metadata: dict[str, Any]) -> str:
    """Hash source dataset metadata deterministically for provenance."""
    payload = json.dumps(
        metadata,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
