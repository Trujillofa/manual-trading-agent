"""Tests for the free CME term-structure Tier-A pipeline."""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import pytest

from research.new_edge.term_structure.data.cme_span import (
    ArchiveInventory,
    CMEArchiveClient,
    SettlementRow,
    parse_span_archive,
    write_settlement_csv,
)
from research.new_edge.term_structure.data.loader import (
    CMEStitchLoader,
    MarketData,
    SyntheticLoader,
)
from research.new_edge.term_structure.data.metadata import InstrumentMetadata
from research.new_edge.term_structure.data.verify_term_structure_data import audit_inventory


def _put(chars: list[str], start: int, end: int, value: str) -> None:
    width = end - start
    assert len(value) <= width
    chars[start:end] = list(value.ljust(width))


def _product_line(
    exchange: str,
    product_code: str,
    locator: int,
    factor: int,
) -> str:
    chars = [" "] * 131
    _put(chars, 0, 2, "P ")
    _put(chars, 2, 5, exchange)
    _put(chars, 5, 15, product_code)
    _put(chars, 15, 18, "FUT")
    _put(chars, 18, 33, f"{product_code} FUTURE")
    _put(chars, 33, 36, f"{locator:03d}")
    _put(chars, 41, 55, f"{factor * 10**7:014d}")
    _put(chars, 72, 73, " ")
    _put(chars, 73, 75, "00")
    return "".join(chars)


def _risk_lines(
    exchange: str,
    product_code: str,
    contract_month: str,
    raw_settlement: int,
    sign: str = "+",
) -> tuple[str, str]:
    first = [" "] * 123
    second = [" "] * 127
    for chars, record_id in ((first, "81"), (second, "82")):
        _put(chars, 0, 2, record_id)
        _put(chars, 2, 5, exchange)
        _put(chars, 5, 15, product_code)
        _put(chars, 15, 25, product_code)
        _put(chars, 25, 28, "FUT")
        _put(chars, 29, 35, contract_month)
    _put(first, 108, 122, f"{raw_settlement:014d}")
    _put(first, 122, 123, "N")
    _put(second, 110, 117, f"{raw_settlement:07d}")
    _put(second, 117, 118, sign)
    return "".join(first), "".join(second)


def _archive(path: Path) -> Path:
    lines = [
        _product_line("NYM", "CL", locator=2, factor=1000),
        *_risk_lines("NYM", "CL", "202005", 1884),
        *_risk_lines("NYM", "CL", "202006", 3763, sign="-"),
        _product_line("CBT", "C", locator=3, factor=5000),
        *_risk_lines("CBT", "C", "202007", 319250),
    ]
    with ZipFile(path, "w") as archive:
        archive.writestr("cme.20200420.s.pa2", "\r\n".join(lines) + "\r\n")
    return path


def test_parse_span_archive_preserves_decimal_and_negative_settlements(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path / "cme.20200420.s.pa2.zip")

    rows = parse_span_archive(archive, date(2020, 4, 20))

    assert [(row.symbol, row.contract_month, Decimal(row.settle)) for row in rows] == [
        ("CL", "202005", Decimal("18.84")),
        ("CL", "202006", Decimal("-37.63")),
        ("ZC", "202007", Decimal("319.250")),
    ]
    assert rows[0].contract_value_factor == "1000.0000000"
    assert rows[2].contract_value_factor == "5000.0000000"
    assert len(rows[0].source_sha256) == 64


def test_parse_span_archive_requires_matching_type_82(tmp_path: Path) -> None:
    product = _product_line("NYM", "CL", locator=2, factor=1000)
    first, _second = _risk_lines("NYM", "CL", "202005", 1884)
    archive_path = tmp_path / "missing-pair.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("missing.pa2", f"{product}\r\n{first}\r\n")

    with pytest.raises(ValueError, match="no matching type-82"):
        parse_span_archive(archive_path, date(2020, 4, 20))


def test_write_and_load_cme_settlement_csv_exposes_source_gaps(tmp_path: Path) -> None:
    row = SettlementRow(
        trade_date="2025-09-12",
        symbol="CL",
        sector="energy",
        exchange="NYM",
        product_code="CL",
        contract_month="202510",
        contract_day="",
        settle="62.69",
        settlement_price_locator=2,
        contract_value_factor="1000.0000000",
        source_file="cme.20250912.s.pa2.zip",
        source_sha256="a" * 64,
    )
    csv_path = tmp_path / "cme-settlements-2025-09-12.csv"

    assert write_settlement_csv([row], csv_path) == 1
    with csv_path.open(newline="", encoding="utf-8") as stream:
        assert len(list(csv.DictReader(stream))) == 1

    data = CMEStitchLoader(tmp_path).load_market(
        "CL",
        date(2025, 9, 12),
        date(2025, 9, 12),
    )
    assert data.symbol == "CL"
    assert data.roll_calendar == []
    assert data.tsmom_daily_excess_pnl.empty
    frame = data.contract_ohlc["CL202510"]
    assert frame["settle"].iloc[0] == pytest.approx(62.69)
    assert frame["open_interest"].isna().all()
    assert frame[["open", "high", "low"]].isna().all().all()


def test_audit_inventory_blocks_before_bulk_download() -> None:
    inventory = ArchiveInventory(
        years=(2013, 2014, 2025),
        files_by_year={
            2013: ("cme.20131231.s.pa2.zip",),
            2014: ("cme.20140102.s.pa2.zip",),
            2025: ("cme.20250912.s.pa2.zip",),
        },
        retrieved_at=datetime(2026, 6, 30, tzinfo=UTC),
    )

    audit = audit_inventory(
        inventory,
        sample_rows=3,
        sample_symbols=("CL", "ZC"),
    )

    assert audit.verdict == "BLOCKED"
    assert audit.available_years < 15
    assert audit.passing_markets == 0
    assert any("open interest" in issue for issue in audit.issues)
    assert any("15 complete years" in issue for issue in audit.issues)


def test_cme_loader_validates_symbol_and_date_order(tmp_path: Path) -> None:
    loader = CMEStitchLoader(tmp_path)

    with pytest.raises(ValueError, match="unsupported"):
        loader.load_market("ES", date(2025, 1, 1), date(2025, 1, 2))
    with pytest.raises(ValueError, match="start must not"):
        loader.load_market("CL", date(2025, 1, 2), date(2025, 1, 1))


def test_archive_client_download_uses_expected_public_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[str] = []

    class FakeFTP:
        def __init__(self, host: str, timeout: float) -> None:
            assert host == "ftp.cmegroup.com"
            assert timeout == 30.0

        def __enter__(self) -> FakeFTP:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def login(self) -> None:
            return None

        def retrbinary(self, command: str, callback: object) -> None:
            commands.append(command)
            assert callable(callback)
            callback(b"fixture bytes")

    monkeypatch.setattr(
        "research.new_edge.term_structure.data.cme_span.ftplib.FTP",
        FakeFTP,
    )
    output = tmp_path / "archive.zip"

    remote = CMEArchiveClient().download(date(2025, 9, 12), output)

    assert remote == "/span/archive/cme/2025/cme.20250912.s.pa2.zip"
    assert commands == ["RETR /span/archive/cme/2025/cme.20250912.s.pa2.zip"]
    assert output.read_bytes() == b"fixture bytes"


def test_synthetic_loader_slices_all_three_market_objects() -> None:
    index = pd.to_datetime(["2020-04-20", "2020-04-21"])
    frame = pd.DataFrame(
        {
            "open": [18.0, -38.0],
            "high": [19.0, -36.0],
            "low": [17.0, -40.0],
            "settle": [18.84, -37.63],
            "open_interest": [100, 90],
        },
        index=index,
    )
    pnl = pd.Series([10.0, -20.0], index=index, name="daily_excess_pnl")
    metadata = InstrumentMetadata("CL", "energy", "CME Group", "NYM", "CL", 1000.0)
    market = MarketData("CL", {"CL202005": frame}, pnl, [date(2020, 4, 21)], metadata)

    sliced = SyntheticLoader({"CL": market}).load_market(
        "CL",
        date(2020, 4, 21),
        date(2020, 4, 21),
    )

    assert len(sliced.contract_ohlc["CL202005"]) == 1
    assert sliced.contract_ohlc["CL202005"]["settle"].iloc[0] == pytest.approx(-37.63)
    assert sliced.tsmom_daily_excess_pnl.tolist() == [-20.0]
    assert sliced.roll_calendar == [date(2020, 4, 21)]
