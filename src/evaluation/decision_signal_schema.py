"""Pydantic schema for forex DecisionSignal JSONL records (v1).

Contract: docs/research/FOREX_DECISION_SIGNAL_EVALUATION_CONTRACT_2026-06-20.md
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

ENGINE_VERSION = "forex-decision-signal-v1"
KIND_DECISION_SIGNAL = "decision_signal"
DEFAULT_SIGNAL_AUDIT_PATH = Path("logs/signal_audit.jsonl")

Direction = Literal["BUY", "SELL"]
Action = Literal["watch", "avoid", "alert"]
Source = Literal["branch_b_scan", "manual_review", "research_harness"]
LifecycleStatus = Literal["active", "expired", "invalidated", "closed"]
DataQualityLevel = Literal["good", "usable", "limited", "poor"]
FieldStatus = Literal[
    "available",
    "missing",
    "stale",
    "fallback",
    "partial",
    "not_supported",
    "fetch_failed",
]

_DATA_QUALITY_BLOCK_KEYS = frozenset(
    {
        "ohlc_m15",
        "ohlc_m30",
        "ohlc_h1",
        "spread",
        "news",
        "session",
        "broker_account",
    }
)
# Legacy FX majors/minors form (EUR/USD). Multi-asset IDs use the instrument registry.
_FX_PAIR_RE = re.compile(r"^[A-Z]{3}/[A-Z]{3}$")


def normalize_decision_symbol(symbol: str) -> str:
    """Normalize a decision-signal symbol to a canonical id.

    Accepts either:
    - a multi-asset instrument id from ``src.config.instruments`` (e.g. OIL, NASDAQ,
      XAU/USD, BTC/USD), resolved via the public registry lookup; or
    - a slash-form FX pair matching ``AAA/BBB`` after strip/upper (e.g. EUR/USD).

    Returns the registry's canonical ``id`` when registered, otherwise the uppercased
    FX pair. Raises ``ValueError`` when neither form matches.
    """
    text = symbol.strip().upper()
    if not text:
        raise ValueError(
            "symbol must be a non-empty registry instrument id "
            "(e.g. OIL, NASDAQ, XAU/USD) or a normalized FX pair like EUR/USD"
        )

    # Registry first — do not hardcode multi-asset ids here.
    from src.config.instruments import get_instrument_optional

    inst = get_instrument_optional(text)
    if inst is not None:
        return inst.id

    if _FX_PAIR_RE.fullmatch(text):
        return text

    raise ValueError(
        "symbol must be a registry instrument id (e.g. OIL, NASDAQ, XAU/USD) "
        "or a normalized FX pair like EUR/USD"
    )


def _require_utc_datetime(value: datetime, *, field_name: str) -> datetime:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError(f"{field_name} must be timezone-aware ISO 8601 UTC")
    if offset.total_seconds() != 0:
        raise ValueError(f"{field_name} must use UTC (Z or +00:00 offset)")
    return value.astimezone(UTC)


class DataQualityBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: FieldStatus


class DataQuality(BaseModel):
    overall_level: DataQualityLevel
    limitations: list[str] = Field(default_factory=list)
    blocks: dict[str, DataQualityBlock] = Field(default_factory=dict)

    @field_validator("limitations")
    @classmethod
    def limitations_are_strings(cls, value: list[str]) -> list[str]:
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("limitations entries must be non-empty strings")
        return value

    @field_validator("blocks")
    @classmethod
    def blocks_use_known_keys(
        cls, value: dict[str, DataQualityBlock]
    ) -> dict[str, DataQualityBlock]:
        unknown = sorted(set(value) - _DATA_QUALITY_BLOCK_KEYS)
        if unknown:
            raise ValueError(f"unknown data_quality.blocks keys: {', '.join(unknown)}")
        return value


class DecisionSignalRecord(BaseModel):
    kind: Literal["decision_signal"]
    signal_id: UUID
    ts: datetime
    symbol: str
    direction: Direction
    action: Action
    source: Source
    status: LifecycleStatus
    engine_version: Literal["forex-decision-signal-v1"]
    evidence_summary: str = Field(min_length=1, max_length=500)
    data_quality: DataQuality
    source_ref: str | None = None
    expires_at: datetime | None = None
    watch_conditions: list[str] | None = None
    risk_summary: str | None = Field(default=None, max_length=300)
    metadata: dict[str, Any] | None = None
    entry_ref_price: float | None = None
    tp_pips: float | None = None
    sl_pips: float | None = None
    invalidation: str | None = None

    @field_validator("ts")
    @classmethod
    def ts_is_utc(cls, value: datetime) -> datetime:
        return _require_utc_datetime(value, field_name="ts")

    @field_validator("expires_at")
    @classmethod
    def expires_at_is_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        return _require_utc_datetime(value, field_name="expires_at")

    @field_validator("symbol")
    @classmethod
    def symbol_is_known_instrument_or_fx_pair(cls, value: str) -> str:
        # strip/upper inside normalize_decision_symbol; registry ids stay canonical.
        return normalize_decision_symbol(value)

    @field_validator("watch_conditions")
    @classmethod
    def watch_conditions_are_strings(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("watch_conditions entries must be non-empty strings")
        return value


class JsonlLineValidationError(ValueError):
    def __init__(self, line_no: int, message: str) -> None:
        self.line_no = line_no
        super().__init__(f"line {line_no}: {message}")


@dataclass
class ValidationReport:
    path: Path
    validated_signals: int = 0
    skipped_rows: int = 0
    errors: list[JsonlLineValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_decision_signal(record: dict[str, Any]) -> DecisionSignalRecord:
    """Validate a single decision_signal dict; raises pydantic.ValidationError on failure."""
    return DecisionSignalRecord.model_validate(record)


def _iso_timestamp_to_utc_z(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    normalized = _require_utc_datetime(parsed, field_name="timestamp")
    text = normalized.strftime("%Y-%m-%dT%H:%M:%S")
    if normalized.microsecond:
        frac = f"{normalized.microsecond:06d}".rstrip("0")
        text = f"{text}.{frac}"
    return f"{text}Z"


def decision_signal_to_json(record: DecisionSignalRecord) -> str:
    """Serialize a validated DecisionSignalRecord to a single JSONL row."""
    payload = record.model_dump(mode="json", exclude_none=True)
    payload["ts"] = _iso_timestamp_to_utc_z(payload["ts"])
    if "expires_at" in payload:
        payload["expires_at"] = _iso_timestamp_to_utc_z(payload["expires_at"])

    blocks = payload.get("data_quality", {}).get("blocks", {})
    for block in blocks.values():
        if isinstance(block, dict) and isinstance(block.get("latest_bar_ts"), str):
            block["latest_bar_ts"] = _iso_timestamp_to_utc_z(block["latest_bar_ts"])

    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def record_decision_signal(
    record: dict[str, Any] | DecisionSignalRecord,
    *,
    path: Path = DEFAULT_SIGNAL_AUDIT_PATH,
) -> DecisionSignalRecord:
    """Validate and append one decision_signal row to the audit JSONL log."""
    validated = (
        record if isinstance(record, DecisionSignalRecord) else validate_decision_signal(record)
    )
    line = decision_signal_to_json(validated)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return validated


def parse_decision_signal_jsonl_line(line: str, *, line_no: int = 1) -> DecisionSignalRecord:
    """Parse one JSONL line as a decision_signal record."""
    stripped = line.strip()
    if not stripped:
        raise JsonlLineValidationError(line_no, "empty line")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise JsonlLineValidationError(line_no, f"invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise JsonlLineValidationError(line_no, "record must be a JSON object")
    try:
        return validate_decision_signal(payload)
    except Exception as exc:
        raise JsonlLineValidationError(line_no, str(exc)) from exc


def validate_decision_signal_jsonl(
    path: Path,
    *,
    skip_non_signal_rows: bool = True,
) -> ValidationReport:
    """Validate decision_signal rows in a JSONL file.

    When skip_non_signal_rows is True (default), rows without kind=decision_signal are ignored.
    This allows validating mixed audit logs that still contain scan_telemetry rows.
    """
    report = ValidationReport(path=path)
    if not path.exists():
        report.errors.append(JsonlLineValidationError(0, f"file not found: {path}"))
        return report

    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                report.skipped_rows += 1
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                report.errors.append(JsonlLineValidationError(line_no, f"invalid JSON: {exc.msg}"))
                continue
            if not isinstance(payload, dict):
                report.errors.append(
                    JsonlLineValidationError(line_no, "record must be a JSON object")
                )
                continue
            if payload.get("kind") != KIND_DECISION_SIGNAL:
                if skip_non_signal_rows:
                    report.skipped_rows += 1
                    continue
                report.errors.append(
                    JsonlLineValidationError(
                        line_no,
                        f"expected kind={KIND_DECISION_SIGNAL!r}, got {payload.get('kind')!r}",
                    )
                )
                continue
            try:
                validate_decision_signal(payload)
            except Exception as exc:
                report.errors.append(JsonlLineValidationError(line_no, str(exc)))
                continue
            report.validated_signals += 1

    return report
