"""Download and normalize free CME SPAN settlement archives.

CME expanded PA2 files contain contract-month settlement prices and product
conversion metadata. They do not contain daily OHLC or contract-month open
interest, so this adapter is only one input to the term-structure Tier-A gate.
"""

from __future__ import annotations

import argparse
import csv
import ftplib
import hashlib
import json
import logging
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from research.new_edge.term_structure.data.metadata import BY_SPAN_KEY

logger = logging.getLogger(__name__)

CME_FTP_HOST = "ftp.cmegroup.com"
CME_SPAN_ARCHIVE_ROOT = "/span/archive/cme"
CME_SPAN_SOURCE_PAGE = "https://www.cmegroup.com/clearing/risk-management/span-overview.html"
CME_SPAN_LAYOUT = (
    "https://cmegroupclientsite.atlassian.net/wiki/spaces/pubsub/pages/457083445/"
    "Risk+Parameter+File+Layouts+for+the+Positional+Formats"
)

CSV_FIELDS = (
    "trade_date",
    "symbol",
    "sector",
    "exchange",
    "product_code",
    "contract_month",
    "contract_day",
    "settle",
    "settlement_price_locator",
    "contract_value_factor",
    "source_file",
    "source_sha256",
)


@dataclass(frozen=True)
class ProductParameters:
    """Price conversion parameters from a PA2 type-P record."""

    exchange: str
    product_code: str
    product_name: str
    settlement_price_locator: int
    contract_value_factor: Decimal


@dataclass(frozen=True)
class SettlementRow:
    """One normalized futures settlement."""

    trade_date: str
    symbol: str
    sector: str
    exchange: str
    product_code: str
    contract_month: str
    contract_day: str
    settle: str
    settlement_price_locator: int
    contract_value_factor: str
    source_file: str
    source_sha256: str


@dataclass(frozen=True)
class ArchiveInventory:
    """Available final-settlement PA2 files observed on CME's public FTP."""

    years: tuple[int, ...]
    files_by_year: dict[int, tuple[str, ...]]
    retrieved_at: datetime

    @property
    def first_date(self) -> date | None:
        dates = [
            _date_from_archive_name(name) for names in self.files_by_year.values() for name in names
        ]
        return min(dates) if dates else None

    @property
    def last_date(self) -> date | None:
        dates = [
            _date_from_archive_name(name) for names in self.files_by_year.values() for name in names
        ]
        return max(dates) if dates else None

    @property
    def file_count(self) -> int:
        return sum(len(names) for names in self.files_by_year.values())


def _date_from_archive_name(name: str) -> date:
    stem = name.split(".")
    if len(stem) < 3:
        raise ValueError(f"invalid CME archive name: {name}")
    return datetime.strptime(stem[1], "%Y%m%d").date()


def _decimal_field(raw: str, implied_decimals: int) -> Decimal:
    text = raw.strip()
    if not text:
        raise ValueError("blank numeric field")
    try:
        return Decimal(text).scaleb(-implied_decimals)
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric field: {raw!r}") from exc


def _parse_product(line: str) -> ProductParameters | None:
    if not line.startswith("P ") or len(line) < 75:
        return None
    exchange = line[2:5].strip()
    product_code = line[5:15].strip()
    product_type = line[15:18].strip()
    if product_type != "FUT" or (exchange, product_code) not in BY_SPAN_KEY:
        return None

    locator = int(line[33:36])
    factor = _decimal_field(line[41:55], 7)
    exponent_sign = -1 if line[72:73] == "-" else 1
    exponent = int(line[73:75].strip() or "0") * exponent_sign
    factor *= Decimal(10) ** exponent
    return ProductParameters(
        exchange=exchange,
        product_code=product_code,
        product_name=line[18:33].strip(),
        settlement_price_locator=locator,
        contract_value_factor=factor,
    )


def _contract_key(line: str) -> tuple[str, str, str, str]:
    return (
        line[2:5].strip(),
        line[5:15].strip(),
        line[29:35].strip(),
        line[35:37].strip(),
    )


def _normalized_price(raw: str, locator: int, sign: str) -> Decimal:
    value = _decimal_field(raw, locator)
    return -value if sign == "-" else value


def _iter_member_lines(archive: ZipFile) -> Iterator[str]:
    members = [name for name in archive.namelist() if name.endswith(".pa2")]
    if len(members) != 1:
        raise ValueError(f"expected exactly one .pa2 member, found {members!r}")
    with archive.open(members[0]) as stream:
        for raw_line in stream:
            yield raw_line.decode("ascii").rstrip("\r\n")


def parse_span_archive(
    archive_path: Path,
    trade_date: date,
) -> tuple[SettlementRow, ...]:
    """Parse fixed-universe futures settlements from one expanded PA2 archive."""
    source_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    products: dict[tuple[str, str], ProductParameters] = {}
    pending_prices: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    rows: list[SettlementRow] = []

    try:
        with ZipFile(archive_path) as archive:
            for line in _iter_member_lines(archive):
                product = _parse_product(line)
                if product is not None:
                    products[(product.exchange, product.product_code)] = product
                    continue
                if not line.startswith(("81", "82")) or len(line) < 123:
                    continue

                key = _contract_key(line)
                span_key = key[:2]
                if span_key not in BY_SPAN_KEY or line[25:28].strip() != "FUT":
                    continue
                if line.startswith("81"):
                    pending_prices[key] = (line[108:122], line[122:123])
                    continue

                pending = pending_prices.pop(key, None)
                if pending is None:
                    raise ValueError(f"type-82 record has no matching type-81 record: {key!r}")
                parameters = products.get(span_key)
                if parameters is None:
                    raise ValueError(f"missing type-P record for {span_key!r}")

                high_precision_raw, _flag = pending
                sign = line[117:118]
                settle = _normalized_price(
                    high_precision_raw,
                    parameters.settlement_price_locator,
                    sign,
                )
                market = BY_SPAN_KEY[span_key]
                rows.append(
                    SettlementRow(
                        trade_date=trade_date.isoformat(),
                        symbol=market.symbol,
                        sector=market.sector,
                        exchange=parameters.exchange,
                        product_code=parameters.product_code,
                        contract_month=key[2],
                        contract_day=key[3],
                        settle=format(settle, "f"),
                        settlement_price_locator=parameters.settlement_price_locator,
                        contract_value_factor=format(parameters.contract_value_factor, "f"),
                        source_file=archive_path.name,
                        source_sha256=source_sha256,
                    )
                )
    except BadZipFile as exc:
        raise ValueError(f"invalid CME SPAN zip archive: {archive_path}") from exc

    if pending_prices:
        raise ValueError(f"{len(pending_prices)} type-81 records have no matching type-82 record")
    if not rows:
        raise ValueError("archive contained no fixed-universe futures settlements")
    return tuple(rows)


def write_settlement_csv(rows: Iterable[SettlementRow], output_path: Path) -> int:
    """Write normalized rows atomically and return the row count."""
    materialized = tuple(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        dir=output_path.parent,
        delete=False,
    ) as temp:
        writer = csv.DictWriter(temp, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(asdict(row) for row in materialized)
        temp_path = Path(temp.name)
    temp_path.replace(output_path)
    return len(materialized)


class CMEArchiveClient:
    """Small FTP client for the public CME SPAN archive."""

    def __init__(self, host: str = CME_FTP_HOST, timeout: float = 30.0) -> None:
        self.host = host
        self.timeout = timeout

    def inventory(self) -> ArchiveInventory:
        """List final daily settlement archives without downloading them."""
        with ftplib.FTP(self.host, timeout=self.timeout) as ftp:
            ftp.login()
            ftp.cwd(CME_SPAN_ARCHIVE_ROOT)
            years = tuple(sorted(int(name) for name in ftp.nlst() if name.isdigit()))
            files_by_year: dict[int, tuple[str, ...]] = {}
            for year in years:
                ftp.cwd(f"{CME_SPAN_ARCHIVE_ROOT}/{year}")
                names = tuple(
                    sorted(
                        name
                        for name in ftp.nlst()
                        if name.startswith(f"cme.{year}") and name.endswith(".s.pa2.zip")
                    )
                )
                files_by_year[year] = names
        return ArchiveInventory(
            years=years,
            files_by_year=files_by_year,
            retrieved_at=datetime.now(UTC),
        )

    def download(self, trade_date: date, output_path: Path) -> str:
        """Download one final PA2 archive atomically and return its FTP path."""
        remote_path = f"{CME_SPAN_ARCHIVE_ROOT}/{trade_date.year}/cme.{trade_date:%Y%m%d}.s.pa2.zip"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", dir=output_path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            try:
                with ftplib.FTP(self.host, timeout=self.timeout) as ftp:
                    ftp.login()
                    ftp.retrbinary(f"RETR {remote_path}", temp.write)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise
        temp_path.replace(output_path)
        return remote_path


def normalize_archive(
    archive_path: Path,
    trade_date: date,
    output_dir: Path,
    source_path: str | None = None,
) -> tuple[Path, Path]:
    """Normalize one archive to CSV plus a machine-readable provenance record."""
    rows = parse_span_archive(archive_path, trade_date)
    csv_path = output_dir / f"cme-settlements-{trade_date.isoformat()}.csv"
    provenance_path = output_dir / f"cme-settlements-{trade_date.isoformat()}.provenance.json"
    write_settlement_csv(rows, csv_path)

    symbols = sorted({row.symbol for row in rows})
    provenance = {
        "schema_version": 1,
        "lane": "term_structure_roll_yield",
        "stage": "tier_a_cme_ingest",
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "source": {
            "publisher": "CME Group",
            "ftp_host": CME_FTP_HOST,
            "ftp_path": source_path,
            "span_source_page": CME_SPAN_SOURCE_PAGE,
            "span_layout": CME_SPAN_LAYOUT,
        },
        "input": {
            "archive": archive_path.name,
            "sha256": rows[0].source_sha256,
            "trade_date": trade_date.isoformat(),
        },
        "output": {
            "csv": str(csv_path),
            "rows": len(rows),
            "symbols": symbols,
        },
        "known_limitations": [
            "SPAN PA2 contains settlements, not daily OHLC.",
            "SPAN PA2 contains no contract-month open interest.",
            "The public archive currently starts after the required 15-year gate.",
        ],
    }
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return csv_path, provenance_path


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value}") from exc


def main() -> int:
    """Download or normalize one CME SPAN settlement archive."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=_parse_date, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--keep-archive", action="store_true")
    args = parser.parse_args()

    source_path: str | None = None
    archive_path = args.archive
    downloaded = archive_path is None
    if archive_path is None:
        archive_path = args.output_dir / f"cme.{args.date:%Y%m%d}.s.pa2.zip"
        try:
            source_path = CMEArchiveClient().download(args.date, archive_path)
        except ftplib.all_errors as exc:
            logger.error("CME archive download failed: %s", exc)
            return 2

    try:
        csv_path, provenance_path = normalize_archive(
            archive_path,
            args.date,
            args.output_dir,
            source_path=source_path,
        )
    except (OSError, ValueError) as exc:
        logger.error("CME archive normalization failed: %s", exc)
        return 2
    finally:
        if downloaded and not args.keep_archive:
            archive_path.unlink(missing_ok=True)

    logger.info("wrote %s and %s", csv_path, provenance_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
