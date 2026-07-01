"""Term-structure data-loader interface and free CME settlement adapter."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Protocol

import pandas as pd

from research.new_edge.term_structure.data.metadata import BY_SYMBOL, InstrumentMetadata


@dataclass(frozen=True)
class MarketData:
    """Three-object term-structure dataset required by the harness contract."""

    symbol: str
    contract_ohlc: dict[str, pd.DataFrame]
    tsmom_daily_excess_pnl: pd.Series
    roll_calendar: list[date]
    metadata: InstrumentMetadata


class TermStructureDataLoader(Protocol):
    """Load individual-contract data for one market."""

    def load_market(self, symbol: str, start: date, end: date) -> MarketData:
        """Load one market inside an inclusive date window."""


class SyntheticLoader:
    """In-memory loader for deterministic unit fixtures only."""

    def __init__(self, markets: dict[str, MarketData]) -> None:
        self.markets = markets

    def load_market(self, symbol: str, start: date, end: date) -> MarketData:
        """Return an inclusive date slice of one synthetic market."""
        if start > end:
            raise ValueError("start must not be after end")
        if symbol not in self.markets:
            raise ValueError(f"synthetic market is unavailable: {symbol}")

        market = self.markets[symbol]
        contracts = {
            contract: frame[(frame.index.date >= start) & (frame.index.date <= end)].copy()
            for contract, frame in market.contract_ohlc.items()
        }
        contracts = {contract: frame for contract, frame in contracts.items() if not frame.empty}
        pnl = market.tsmom_daily_excess_pnl
        sliced_pnl = pnl[(pnl.index.date >= start) & (pnl.index.date <= end)].copy()
        calendar = [roll_date for roll_date in market.roll_calendar if start <= roll_date <= end]
        return replace(
            market,
            contract_ohlc=contracts,
            tsmom_daily_excess_pnl=sliced_pnl,
            roll_calendar=calendar,
        )


class CMEStitchLoader:
    """Load normalized CME PA2 settlement CSVs from a local cache.

    The returned frames expose the source's missing OHLC/open-interest fields as
    nullable columns. The Tier-A verifier must therefore keep this source
    BLOCKED until a second free official source supplies those fields.
    """

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root

    def _read_rows(self) -> pd.DataFrame:
        paths = sorted(self.data_root.glob("cme-settlements-*.csv"))
        if not paths:
            raise FileNotFoundError(f"no normalized CME settlement CSVs in {self.data_root}")
        frames = [
            pd.read_csv(path, dtype={"contract_month": str, "contract_day": str}) for path in paths
        ]
        frame = pd.concat(frames, ignore_index=True)
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
        frame["settle"] = pd.to_numeric(frame["settle"], errors="raise")
        return frame

    def load_market(self, symbol: str, start: date, end: date) -> MarketData:
        """Load cached settlements while preserving the known source gaps."""
        if symbol not in BY_SYMBOL:
            raise ValueError(f"unsupported term-structure symbol: {symbol}")
        if start > end:
            raise ValueError("start must not be after end")

        frame = self._read_rows()
        selected = frame[
            (frame["symbol"] == symbol)
            & (frame["trade_date"].dt.date >= start)
            & (frame["trade_date"].dt.date <= end)
        ].copy()
        if selected.empty:
            raise ValueError(f"no {symbol} settlements between {start} and {end}")

        selected["contract_id"] = (
            selected["symbol"]
            + selected["contract_month"].astype(str)
            + selected["contract_day"].fillna("").astype(str)
        )
        contract_ohlc: dict[str, pd.DataFrame] = {}
        for contract_id, contract_frame in selected.groupby("contract_id", sort=True):
            normalized = contract_frame.set_index("trade_date")[["settle"]].sort_index()
            normalized["open"] = pd.NA
            normalized["high"] = pd.NA
            normalized["low"] = pd.NA
            normalized["open_interest"] = pd.NA
            contract_ohlc[str(contract_id)] = normalized[
                ["open", "high", "low", "settle", "open_interest"]
            ]

        first = selected.iloc[0]
        spec = BY_SYMBOL[symbol]
        metadata = InstrumentMetadata(
            symbol=symbol,
            sector=spec.sector,
            venue="CME Group",
            exchange=str(first["exchange"]),
            product_code=str(first["product_code"]),
            contract_value_factor=float(first["contract_value_factor"]),
        )
        return MarketData(
            symbol=symbol,
            contract_ohlc=contract_ohlc,
            tsmom_daily_excess_pnl=pd.Series(dtype=float, name="daily_excess_pnl"),
            roll_calendar=[],
            metadata=metadata,
        )
