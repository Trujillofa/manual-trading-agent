"""Pure, descriptive post-release surprise scoring.

Decision-support only. Never maps to BUY/SELL, bullish/bearish, or thresholds.
Production code must not import research modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

SurpriseStatus = Literal[
    "scored",
    "pre_release",
    "missing_actual",
    "missing_forecast",
    "missing_observed_at",
    "unparseable",
    "zero_forecast",
    "non_deterministic_source",
]
SurpriseDirection = Literal["above", "below", "inline"]

SOURCE_FOREX_FACTORY = "forex_factory"

_MISSING_TOKENS = {"", "--", "-", "null", "nan", "none", "n/a"}


@dataclass(frozen=True)
class SurpriseResult:
    status: SurpriseStatus
    parsed_actual: float | None
    parsed_forecast: float | None
    raw_delta: float | None
    relative_delta_pct: float | None
    direction: SurpriseDirection | None
    observed_at: datetime | None


def is_missing_value(raw: str | None) -> bool:
    """True for blank, N/A, and other non-values. Never treat these as 0."""
    if raw is None:
        return True
    return str(raw).strip().lower() in _MISSING_TOKENS


def parse_numeric_value(raw: str | None) -> float | None:
    """Parse Faireconomy/FF-style release values. Returns None if unparseable."""
    if is_missing_value(raw):
        return None
    text = str(raw).strip().replace(",", "").replace(" ", "")
    if text in {"--", "-"}:
        return None
    if "|" in text:
        parts = [part for part in text.split("|") if part]
        if not parts:
            return None
        text = parts[0]

    multiplier = 1.0
    upper = text.upper()
    if text.endswith("%"):
        text = text[:-1]
    elif upper.endswith("BPS"):
        text = text[:-3]
        multiplier = 0.01
    elif upper.endswith("BP"):
        text = text[:-2]
        multiplier = 0.01
    elif upper.endswith("K"):
        text = text[:-1]
        multiplier = 1_000.0
    elif upper.endswith("M"):
        text = text[:-1]
        multiplier = 1_000_000.0
    elif upper.endswith("B"):
        text = text[:-1]
        multiplier = 1_000_000_000.0

    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _empty_result(
    status: SurpriseStatus,
    observed_at: datetime | None,
) -> SurpriseResult:
    return SurpriseResult(
        status=status,
        parsed_actual=None,
        parsed_forecast=None,
        raw_delta=None,
        relative_delta_pct=None,
        direction=None,
        observed_at=observed_at,
    )


def _direction(actual: float, forecast: float) -> SurpriseDirection:
    if actual > forecast:
        return "above"
    if actual < forecast:
        return "below"
    return "inline"


def score_surprise(
    *,
    actual_raw: str,
    forecast_raw: str,
    event_timestamp: datetime,
    now: datetime,
    source: str,
    observed_at: datetime | None = None,
) -> SurpriseResult:
    """Score actual vs forecast after release. Missing data stays unscored."""
    if source != SOURCE_FOREX_FACTORY:
        return _empty_result("non_deterministic_source", observed_at)
    if now < event_timestamp:
        return _empty_result("pre_release", observed_at)
    if observed_at is not None and observed_at < event_timestamp:
        return _empty_result("pre_release", observed_at)

    actual_missing = is_missing_value(actual_raw)
    forecast_missing = is_missing_value(forecast_raw)
    if actual_missing:
        return _empty_result("missing_actual", observed_at)
    if observed_at is None:
        return _empty_result("missing_observed_at", observed_at)
    if forecast_missing:
        return _empty_result("missing_forecast", observed_at)

    parsed_actual = parse_numeric_value(actual_raw)
    parsed_forecast = parse_numeric_value(forecast_raw)
    if parsed_actual is None or parsed_forecast is None:
        return _empty_result("unparseable", observed_at)

    raw_delta = parsed_actual - parsed_forecast
    direction = _direction(parsed_actual, parsed_forecast)
    if parsed_forecast == 0:
        return SurpriseResult(
            status="zero_forecast",
            parsed_actual=parsed_actual,
            parsed_forecast=parsed_forecast,
            raw_delta=raw_delta,
            relative_delta_pct=None,
            direction=direction,
            observed_at=observed_at,
        )

    relative_delta_pct = (raw_delta / abs(parsed_forecast)) * 100.0
    return SurpriseResult(
        status="scored",
        parsed_actual=parsed_actual,
        parsed_forecast=parsed_forecast,
        raw_delta=raw_delta,
        relative_delta_pct=relative_delta_pct,
        direction=direction,
        observed_at=observed_at,
    )


def format_surprise_annotation(
    *,
    forecast_raw: str,
    actual_raw: str,
    result: SurpriseResult,
) -> str:
    """Concise status clause. Omits forecast/actual/surprise when absent."""
    forecast = forecast_raw.strip()
    actual = actual_raw.strip()
    if result.status == "pre_release":
        if forecast:
            return f"scheduled | forecast {forecast}"
        return "scheduled"
    if result.status == "scored":
        pct = result.relative_delta_pct
        direction = result.direction or "inline"
        pct_txt = f"{pct:+.1f}% " if pct is not None else ""
        return f"scored | actual {actual} vs forecast {forecast} | {pct_txt}{direction}".rstrip()

    bits = ["released"]
    if result.status == "missing_actual" or (not actual and result.status != "missing_forecast"):
        bits.append("actual unavailable")
    if result.status == "missing_forecast":
        bits.append("forecast unavailable")
    elif result.status == "missing_observed_at":
        bits.append("observation time unavailable")
    elif result.status == "unparseable":
        bits.append("unscored")
    elif result.status == "zero_forecast":
        bits.append("unscored zero forecast")
    elif result.status == "non_deterministic_source":
        bits.append("unscored LLM fallback")
    if actual and result.status != "missing_actual":
        bits.append(f"actual {actual}")
    if forecast and result.status not in {"missing_forecast", "scored"}:
        bits.append(f"forecast {forecast}")
    return " | ".join(bits)


def surprise_readiness_label(has_timestamped_actual: bool) -> str:
    if has_timestamped_actual:
        return "Surprise scoring: available"
    return "Surprise scoring: BLOCKED (no timestamped actuals in live feed)"
