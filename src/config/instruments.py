"""Multi-asset instrument registry for scan-time metadata.

Branch B alert tooling — not a KEEP / profitability claim.
Unknown IDs raise in scan; optional lookup returns None for FX string fallbacks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AssetClass = Literal["fx", "metal_futures", "crypto", "energy_futures", "index_futures"]


@dataclass(frozen=True)
class InstrumentSpec:
    """Scan-time metadata for one product ID."""

    id: str
    display_name: str
    asset_class: AssetClass
    yf_symbol: str
    point_size: float
    currencies: tuple[str, ...] = ()
    session_windows_utc: tuple[str, ...] = ("00-24",)
    td_symbol: str | None = None  # None → never call Twelve Data for this id
    spread_filter_enabled: bool = False
    supports_backtest: bool = False
    # CME Globex-style gaps: use two windows (see _session_allowed — no wrap-around).
    notes: str = ""


# Built-in multi-asset watchlist (product IDs used in settings.yaml majors).
_DEFAULT_INSTRUMENTS: dict[str, InstrumentSpec] = {
    "XAU/USD": InstrumentSpec(
        id="XAU/USD",
        display_name="Gold",
        asset_class="metal_futures",
        yf_symbol="GC=F",
        point_size=0.1,
        currencies=("XAU", "USD"),
        # Approx Globex: avoid ~21:00–22:00 UTC daily maintenance (two windows; no wrap).
        session_windows_utc=("00-21", "22-24"),
        td_symbol=None,
        notes="Continuous GC=F; rolls approximate",
    ),
    "BTC/USD": InstrumentSpec(
        id="BTC/USD",
        display_name="Bitcoin",
        asset_class="crypto",
        yf_symbol="BTC-USD",
        point_size=1.0,
        currencies=("USD",),
        session_windows_utc=("00-24",),
        td_symbol=None,
    ),
    "OIL": InstrumentSpec(
        id="OIL",
        display_name="WTI Crude",
        asset_class="energy_futures",
        yf_symbol="CL=F",
        point_size=0.01,
        currencies=(),  # no news lockout v1
        session_windows_utc=("00-21", "22-24"),
        td_symbol=None,
        notes="Continuous CL=F WTI",
    ),
    "NASDAQ": InstrumentSpec(
        id="NASDAQ",
        display_name="Nasdaq 100 Futures",
        asset_class="index_futures",
        yf_symbol="NQ=F",
        point_size=0.25,
        currencies=("USD",),  # explicit — never string-split to NAS/DAQ
        session_windows_utc=("00-21", "22-24"),
        td_symbol=None,
        notes="Continuous NQ=F",
    ),
}

# Mutable registry so Settings.load can overlay YAML without forking maps.
_REGISTRY: dict[str, InstrumentSpec] = dict(_DEFAULT_INSTRUMENTS)


def reset_registry_to_defaults() -> None:
    """Restore built-in multi-asset defaults (tests)."""
    global _REGISTRY
    _REGISTRY = dict(_DEFAULT_INSTRUMENTS)


def register_instrument(spec: InstrumentSpec) -> None:
    """Insert or replace an instrument in the live registry."""
    _REGISTRY[spec.id] = spec


def all_instruments() -> list[InstrumentSpec]:
    return list(_REGISTRY.values())


def _compact_id(instrument_id: str) -> str:
    return (
        instrument_id.strip()
        .upper()
        .replace("/", "")
        .replace("-", "")
        .replace(" ", "")
    )


def get_instrument(instrument_id: str) -> InstrumentSpec:
    """Return spec or raise KeyError (scan path for registered majors)."""
    key = instrument_id.strip()
    if key in _REGISTRY:
        return _REGISTRY[key]
    compact = _compact_id(key)
    for rid, spec in _REGISTRY.items():
        if _compact_id(rid) == compact:
            return spec
        if rid.replace(" ", "") == key.replace(" ", ""):
            return spec
    raise KeyError(f"Unknown instrument id: {instrument_id!r}")


def get_instrument_optional(instrument_id: str) -> InstrumentSpec | None:
    try:
        return get_instrument(instrument_id)
    except KeyError:
        return None


def point_size(instrument_id: str) -> float:
    """Display/risk unit size; FX fallback matches legacy JPY heuristic."""
    inst = get_instrument_optional(instrument_id)
    if inst is not None:
        return inst.point_size
    return 0.01 if "JPY" in instrument_id.upper() else 0.0001


def session_windows(instrument_id: str, fallback: list[str] | None = None) -> list[str]:
    inst = get_instrument_optional(instrument_id)
    if inst is not None:
        return list(inst.session_windows_utc)
    return list(fallback or ["00-24"])


def yfinance_symbol_map() -> dict[str, str]:
    """Product id → yfinance ticker for DataFetcher.SYMBOL_MAP overlay."""
    return {spec.id: spec.yf_symbol for spec in _REGISTRY.values()}


def is_backtest_supported(instrument_id: str) -> bool:
    inst = get_instrument_optional(instrument_id)
    if inst is None:
        return True  # legacy FX paths
    return inst.supports_backtest


def require_backtest_supported(instrument_id: str) -> None:
    if not is_backtest_supported(instrument_id):
        raise ValueError(
            f"instrument {instrument_id!r} is not supported for backtest "
            "(multi-asset Branch B scan-only; use scan/analyze for live OHLC)"
        )


def distance_unit_label(instrument_id: str) -> str:
    """Human unit for audit/Telegram: points vs pips."""
    inst = get_instrument_optional(instrument_id)
    if inst is None or inst.asset_class == "fx":
        return "pips"
    return "points"


def apply_yaml_instruments(raw: dict[str, object]) -> None:
    """Overlay YAML instrument block onto the registry (in place)."""
    if not isinstance(raw, dict):
        raise ValueError("instruments must be a YAML object")
    for raw_id, payload in raw.items():
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise ValueError("instruments keys must be non-empty strings")
        if not isinstance(payload, dict):
            raise ValueError(f"instruments.{raw_id} must be an object")
        base = get_instrument_optional(raw_id)
        yf = payload.get("yf_symbol", base.yf_symbol if base else None)
        if not isinstance(yf, str) or not yf.strip():
            raise ValueError(f"instruments.{raw_id}.yf_symbol is required")
        ps = payload.get("point_size", base.point_size if base else None)
        if ps is None or float(ps) <= 0:
            raise ValueError(f"instruments.{raw_id}.point_size must be > 0")
        curs = payload.get("currencies", list(base.currencies) if base else [])
        if not isinstance(curs, list) or not all(isinstance(c, str) and c.strip() for c in curs):
            raise ValueError(f"instruments.{raw_id}.currencies must be a list of strings")
        sessions = payload.get(
            "session_allowed_utc",
            list(base.session_windows_utc) if base else ["00-24"],
        )
        if not isinstance(sessions, list) or not sessions:
            raise ValueError(f"instruments.{raw_id}.session_allowed_utc must be a non-empty list")
        # td_symbol: key absent → keep base; key present null → None (yfinance only)
        if "td_symbol" in payload:
            td_raw = payload["td_symbol"]
            td_symbol = None if td_raw is None else str(td_raw)
        else:
            td_symbol = base.td_symbol if base else None
        asset = base.asset_class if base else "fx"
        display = base.display_name if base else raw_id
        spread = payload.get(
            "spread_filter_enabled",
            base.spread_filter_enabled if base else False,
        )
        register_instrument(
            InstrumentSpec(
                id=raw_id.strip(),
                display_name=display,
                asset_class=asset,  # type: ignore[arg-type]
                yf_symbol=str(yf).strip(),
                point_size=float(ps),
                currencies=tuple(str(c).strip().upper() for c in curs),
                session_windows_utc=tuple(str(s).strip() for s in sessions),
                td_symbol=td_symbol,
                spread_filter_enabled=bool(spread),
                supports_backtest=bool(base.supports_backtest) if base else False,
                notes=base.notes if base else "",
            )
        )
