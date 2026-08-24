"""BTC perpetual funding snapshot (optional sentiment proxy).

Adapted from TRADING/crypto-agent ``src/execution/futures_client.py``
``get_funding_rate`` — public unsigned GET ``/fapi/v1/fundingRate``.
Not a trade signal. Missing or failed fetch must not fail the briefing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

logger = logging.getLogger(__name__)

BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
BTC_PERP_SYMBOL = "BTCUSDT"


@dataclass(frozen=True)
class FundingSnapshot:
    symbol: str
    rate: float
    funding_time_ms: int | None = None

    def rate_pct_label(self) -> str:
        return f"{self.rate * 100:.4f}%"

    def funding_time_utc(self) -> datetime | None:
        if self.funding_time_ms is None or self.funding_time_ms <= 0:
            return None
        return datetime.fromtimestamp(self.funding_time_ms / 1000.0, tz=UTC)


def parse_funding_payload(data: object, *, symbol: str = BTC_PERP_SYMBOL) -> FundingSnapshot:
    """Parse Binance fundingRate JSON (list or single object)."""
    row: object
    if isinstance(data, list):
        if not data:
            raise ValueError("empty fundingRate list")
        row = data[0]
    else:
        row = data
    if not isinstance(row, dict):
        raise TypeError(f"unexpected funding payload type: {type(data).__name__}")
    raw_rate = row.get("fundingRate", row.get("lastFundingRate"))
    if raw_rate is None:
        raise ValueError("fundingRate missing")
    raw_time = row.get("fundingTime")
    funding_time_ms: int | None
    try:
        funding_time_ms = int(raw_time) if raw_time is not None else None
    except (TypeError, ValueError):
        funding_time_ms = None
    return FundingSnapshot(
        symbol=str(row.get("symbol") or symbol),
        rate=float(raw_rate),
        funding_time_ms=funding_time_ms,
    )


async def fetch_binance_funding(
    symbol: str = BTC_PERP_SYMBOL,
    *,
    timeout: float = 8.0,
) -> FundingSnapshot:
    """Public Binance USDT-M funding print. Raises on HTTP/parse failure."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(BINANCE_FUNDING_URL, params={"symbol": symbol, "limit": 1})
        response.raise_for_status()
        payload = response.json()
    return parse_funding_payload(payload, symbol=symbol)


async def try_fetch_btc_funding() -> tuple[FundingSnapshot | None, str | None]:
    """Best-effort fetch. Never raises to the caller."""
    try:
        snapshot = await fetch_binance_funding()
        return snapshot, None
    except Exception as exc:
        logger.warning("BTC funding fetch skipped: %s", exc)
        return None, str(exc)
