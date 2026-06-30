"""Fixed commodity universe and CME SPAN identifiers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentMetadata:
    """Static and source-derived contract metadata."""

    symbol: str
    sector: str
    venue: str
    exchange: str
    product_code: str
    contract_value_factor: float


@dataclass(frozen=True)
class MarketSpec:
    """Map one research symbol to its CME SPAN product identity."""

    symbol: str
    name: str
    sector: str
    exchange: str
    product_code: str

    @property
    def span_key(self) -> tuple[str, str]:
        """Return the exchange/product key used in expanded PA2 files."""
        return self.exchange, self.product_code


FIXED_UNIVERSE: tuple[MarketSpec, ...] = (
    MarketSpec("CL", "WTI crude oil", "energy", "NYM", "CL"),
    MarketSpec("NG", "Henry Hub natural gas", "energy", "NYM", "NG"),
    MarketSpec("RB", "RBOB gasoline", "energy", "NYM", "RB"),
    MarketSpec("HO", "NY Harbor ULSD", "energy", "NYM", "HO"),
    MarketSpec("GC", "COMEX gold", "metals", "CMX", "GC"),
    MarketSpec("SI", "COMEX silver", "metals", "CMX", "SI"),
    MarketSpec("HG", "COMEX copper", "metals", "CMX", "HG"),
    MarketSpec("ZC", "CBOT corn", "agriculture", "CBT", "C"),
    MarketSpec("ZS", "CBOT soybeans", "agriculture", "CBT", "S"),
    MarketSpec("ZW", "CBOT wheat", "agriculture", "CBT", "W"),
    MarketSpec("LE", "Live cattle", "livestock", "CME", "48"),
    MarketSpec("HE", "Lean hogs", "livestock", "CME", "LN"),
)

BY_SPAN_KEY = {market.span_key: market for market in FIXED_UNIVERSE}
BY_SYMBOL = {market.symbol: market for market in FIXED_UNIVERSE}
