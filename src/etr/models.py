"""Structured models for ETR Market Terminal reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EtrAsset = Literal["btc", "gold", "nasdaq", "oil"]

ASSET_PATHS: dict[str, str] = {
    "btc": "/analisis/btc",
    "gold": "/analisis/gold",
    "nasdaq": "/analisis/nasdaq",
    "oil": "/analisis/oil",
}

ASSET_LABELS: dict[str, str] = {
    "btc": "Bitcoin",
    "gold": "Oro",
    "nasdaq": "Nasdaq",
    "oil": "Petróleo",
}

VALID_ASSETS: tuple[str, ...] = ("btc", "gold", "nasdaq", "oil")


@dataclass(frozen=True)
class PriceZone:
    """Inclusive price range (low, high)."""

    low: float
    high: float

    def contains(self, price: float) -> bool:
        lo, hi = (self.low, self.high) if self.low <= self.high else (self.high, self.low)
        return lo <= price <= hi

    def format(self) -> str:
        return f"{self.low:g}–{self.high:g}"

    def to_dict(self) -> dict[str, float]:
        return {"low": self.low, "high": self.high}

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> PriceZone | None:
        if not isinstance(payload, dict):
            return None
        try:
            low = payload.get("low")
            high = payload.get("high")
            if low is None or high is None:
                return None
            return cls(low=float(low), high=float(high))  # type: ignore[arg-type]
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class EtrScenario:
    name: str
    direction: str  # Bajista / Alcista / unknown
    status: str  # e.g. Esperando confirmación / Activo
    role: str  # Principal / Alternativo
    activation_zone: PriceZone | None = None
    invalidation: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "direction": self.direction,
            "status": self.status,
            "role": self.role,
            "activation_zone": self.activation_zone.to_dict() if self.activation_zone else None,
            "invalidation": self.invalidation,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> EtrScenario | None:
        if not isinstance(payload, dict):
            return None
        return cls(
            name=str(payload.get("name") or ""),
            direction=str(payload.get("direction") or ""),
            status=str(payload.get("status") or ""),
            role=str(payload.get("role") or ""),
            activation_zone=PriceZone.from_dict(payload.get("activation_zone")),
            invalidation=_opt_float(payload.get("invalidation")),
            tp1=_opt_float(payload.get("tp1")),
            tp2=_opt_float(payload.get("tp2")),
            score=_opt_float(payload.get("score")),
        )


@dataclass
class EtrReport:
    asset: str
    label: str
    price: float | None
    updated_at: str | None
    context_score: float | None
    bias: str
    estado: str
    lectura_headline: str
    lectura_body: str
    h4_context: str
    m5_execution: str
    structure: str
    primary: EtrScenario | None = None
    alternative: EtrScenario | None = None
    raw_text_excerpt: str = ""
    fetched_at: str | None = None

    def price_in_primary_zone(self) -> bool | None:
        if self.price is None or self.primary is None or self.primary.activation_zone is None:
            return None
        return self.primary.activation_zone.contains(self.price)

    def score_bucket(self, low: float = 50.0, high: float = 80.0) -> str:
        if self.context_score is None:
            return "unknown"
        if self.context_score <= low:
            return "low"
        if self.context_score >= high:
            return "high"
        return "mid"

    def fingerprint(self, score_low: float = 50.0, score_high: float = 80.0) -> str:
        """Stable structure fingerprint — ignores pure price ticks."""
        primary = self.primary
        alt = self.alternative
        parts = [
            self.asset,
            _norm(self.bias),
            _norm(self.estado),
            self.score_bucket(score_low, score_high),
            _norm(primary.direction if primary else ""),
            _fmt_opt(primary.invalidation if primary else None),
            primary.activation_zone.format() if primary and primary.activation_zone else "",
            _norm(primary.status if primary else ""),
            _norm(alt.direction if alt else ""),
            _fmt_opt(alt.invalidation if alt else None),
            alt.activation_zone.format() if alt and alt.activation_zone else "",
        ]
        return "|".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "label": self.label,
            "price": self.price,
            "updated_at": self.updated_at,
            "context_score": self.context_score,
            "bias": self.bias,
            "estado": self.estado,
            "lectura_headline": self.lectura_headline,
            "lectura_body": self.lectura_body,
            "h4_context": self.h4_context,
            "m5_execution": self.m5_execution,
            "structure": self.structure,
            "primary": self.primary.to_dict() if self.primary else None,
            "alternative": self.alternative.to_dict() if self.alternative else None,
            "fetched_at": self.fetched_at,
            "fingerprint": self.fingerprint(),
            "price_in_primary_zone": self.price_in_primary_zone(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EtrReport:
        return cls(
            asset=str(payload.get("asset") or ""),
            label=str(payload.get("label") or ""),
            price=_opt_float(payload.get("price")),
            updated_at=_opt_str(payload.get("updated_at")),
            context_score=_opt_float(payload.get("context_score")),
            bias=str(payload.get("bias") or ""),
            estado=str(payload.get("estado") or ""),
            lectura_headline=str(payload.get("lectura_headline") or ""),
            lectura_body=str(payload.get("lectura_body") or ""),
            h4_context=str(payload.get("h4_context") or ""),
            m5_execution=str(payload.get("m5_execution") or ""),
            structure=str(payload.get("structure") or ""),
            primary=EtrScenario.from_dict(payload.get("primary")),
            alternative=EtrScenario.from_dict(payload.get("alternative")),
            fetched_at=_opt_str(payload.get("fetched_at")),
        )


@dataclass(frozen=True)
class EtrChange:
    field: str
    old: str
    new: str
    severity: Literal["info", "action"] = "info"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class AssetState:
    """Persisted last-known report snapshot for one asset."""

    fingerprint: str
    report: dict[str, Any]
    last_alerted_fingerprint: str | None = None
    last_polled_at: str | None = None
    last_alerted_at: str | None = None
    in_primary_zone: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "report": self.report,
            "last_alerted_fingerprint": self.last_alerted_fingerprint,
            "last_polled_at": self.last_polled_at,
            "last_alerted_at": self.last_alerted_at,
            "in_primary_zone": self.in_primary_zone,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AssetState:
        return cls(
            fingerprint=str(payload.get("fingerprint") or ""),
            report=dict(payload.get("report") or {}),
            last_alerted_fingerprint=_opt_str(payload.get("last_alerted_fingerprint")),
            last_polled_at=_opt_str(payload.get("last_polled_at")),
            last_alerted_at=_opt_str(payload.get("last_alerted_at")),
            in_primary_zone=payload.get("in_primary_zone")
            if isinstance(payload.get("in_primary_zone"), bool)
            else None,
        )


@dataclass
class EtrPollResult:
    asset: str
    report: EtrReport | None
    changes: list[EtrChange] = field(default_factory=list)
    notified: bool = False
    seeded: bool = False
    skipped: bool = False
    error: str | None = None


def _norm(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _fmt_opt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:g}"


def _opt_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
