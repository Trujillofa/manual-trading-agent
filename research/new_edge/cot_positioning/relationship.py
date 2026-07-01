"""Fixed COT-positioning relationship test with chronological holdout controls."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from research.new_edge.cot_positioning.availability import (
    AvailabilityAudit,
    apply_release_controls,
)
from research.new_edge.cot_positioning.data.cftc_legacy import (
    FIXED_UNIVERSE,
    fetch_legacy_rows,
    normalize_rows,
)

ROLLING_REPORTS = 156
MIN_ROLLING_REPORTS = 104
FORWARD_DAYS = 28
IS_FRACTION = 0.65
MIN_MARKETS = 15
MIN_OOS_OBSERVATIONS = 500
SHUFFLE_SEED = 20260630
DEFAULT_SHUFFLES = 2_000


@dataclass(frozen=True)
class PriceSpec:
    """Pre-registered continuous-futures price mapping."""

    symbol: str
    ticker: str | None


PRICE_SPECS: tuple[PriceSpec, ...] = (
    PriceSpec("CORN", "ZC=F"),
    PriceSpec("SOYBEANS", "ZS=F"),
    PriceSpec("SOYBEAN_OIL", "ZL=F"),
    PriceSpec("SOYBEAN_MEAL", "ZM=F"),
    PriceSpec("ROUGH_RICE", "ZR=F"),
    PriceSpec("LIVE_CATTLE", "LE=F"),
    PriceSpec("LEAN_HOGS", "HE=F"),
    PriceSpec("COCOA", "CC=F"),
    PriceSpec("COFFEE", "KC=F"),
    PriceSpec("SUGAR", "SB=F"),
    PriceSpec("GOLD", "GC=F"),
    PriceSpec("SILVER", "SI=F"),
    PriceSpec("PLATINUM", "PL=F"),
    PriceSpec("PALLADIUM", "PA=F"),
    PriceSpec("AUD", "6A=F"),
    PriceSpec("CAD", "6C=F"),
    PriceSpec("EUR", "6E=F"),
    PriceSpec("JPY", "6J=F"),
    PriceSpec("MXN", "6M=F"),
    PriceSpec("CHF", "6S=F"),
    PriceSpec("VIX", None),
    PriceSpec("NIKKEI_YEN", "NKD=F"),
    PriceSpec("SP500", "ES=F"),
)


@dataclass(frozen=True)
class RegressionStats:
    observations: int
    intercept: float
    slope: float
    slope_standard_error: float
    t_stat: float
    one_sided_p: float


@dataclass(frozen=True)
class RelationshipResult:
    verdict: str
    reasons: tuple[str, ...]
    cutoff_date: str
    markets: int
    observations: int
    oos_observations: int
    availability: AvailabilityAudit
    in_sample: RegressionStats
    out_of_sample: RegressionStats
    oos_bucket_means: dict[str, float]
    bottom_minus_top: float
    adjacent_decreases: int
    negative_market_fraction: float
    shuffle_reversal_percentile: float
    negative_leave_one_out_fraction: float
    missing_price_symbols: tuple[str, ...]


def _last_percentile(values: np.ndarray) -> float:
    """Return the empirical percentile of the final value in one trailing window."""

    final = values[-1]
    less = float(np.sum(values < final))
    equal = float(np.sum(values == final))
    return (less + 0.5 * equal) / len(values)


def add_positioning_percentiles(frame: pd.DataFrame) -> pd.DataFrame:
    """Add a past-and-current-only rolling positioning percentile."""

    positioned = frame.sort_values(["symbol", "report_date"]).copy()
    positioned["positioning_percentile"] = positioned.groupby("symbol", group_keys=False)[
        "net_noncommercial_pct_oi"
    ].transform(
        lambda series: series.rolling(ROLLING_REPORTS, min_periods=MIN_ROLLING_REPORTS).apply(
            _last_percentile, raw=True
        )
    )
    return positioned


def fetch_prices(start: date, end: date) -> tuple[dict[str, pd.Series], tuple[str, ...]]:
    """Fetch the pre-registered continuous-futures closes from Yahoo Finance."""

    available_specs = [spec for spec in PRICE_SPECS if spec.ticker is not None]
    tickers = [spec.ticker for spec in available_specs if spec.ticker is not None]
    raw = yf.download(
        tickers,
        start=start.isoformat(),
        end=(end + timedelta(days=FORWARD_DAYS + 10)).isoformat(),
        auto_adjust=False,
        progress=False,
        threads=True,
    )
    if raw.empty:
        raise RuntimeError("Yahoo Finance returned no price rows")

    close = raw["Close"]
    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0])

    prices: dict[str, pd.Series] = {}
    missing: list[str] = [spec.symbol for spec in PRICE_SPECS if spec.ticker is None]
    for spec in available_specs:
        assert spec.ticker is not None
        if spec.ticker not in close or close[spec.ticker].dropna().empty:
            missing.append(spec.symbol)
            continue
        series = close[spec.ticker].dropna().astype(float)
        series.index = pd.to_datetime(series.index).tz_localize(None).normalize()
        series = series[~series.index.duplicated(keep="last")].sort_index()
        prices[spec.symbol] = series
    return prices, tuple(sorted(missing))


def build_observations(
    cot_frame: pd.DataFrame,
    prices: dict[str, pd.Series],
) -> tuple[pd.DataFrame, AvailabilityAudit]:
    """Create no-lookahead positioning/forward-return observations."""

    controlled, availability = apply_release_controls(cot_frame)
    positioned = add_positioning_percentiles(controlled)
    rows: list[dict[str, Any]] = []

    for row in positioned.itertuples(index=False):
        percentile = row.positioning_percentile
        if pd.isna(percentile) or row.symbol not in prices:
            continue
        series = prices[row.symbol]
        entry_position = int(series.index.searchsorted(row.effective_available_date, side="right"))
        if entry_position >= len(series):
            continue
        entry_date = series.index[entry_position]
        exit_target = entry_date + pd.Timedelta(days=FORWARD_DAYS)
        exit_position = int(series.index.searchsorted(exit_target, side="left"))
        if exit_position >= len(series):
            continue
        entry_price = float(series.iloc[entry_position])
        exit_price = float(series.iloc[exit_position])
        if entry_price <= 0 or exit_price <= 0:
            continue
        rows.append(
            {
                "symbol": row.symbol,
                "sector": row.sector,
                "report_date": row.report_date,
                "effective_available_date": row.effective_available_date,
                "entry_date": entry_date,
                "exit_date": series.index[exit_position],
                "positioning_percentile": float(percentile),
                "forward_log_return": math.log(exit_price / entry_price),
            }
        )

    observations = pd.DataFrame(rows)
    if observations.empty:
        raise ValueError("no relationship observations could be constructed")
    return observations.sort_values(["effective_available_date", "symbol"]), availability


def fit_ols(frame: pd.DataFrame) -> RegressionStats:
    """Fit one-intercept OLS and report a one-sided normal p-value for slope < 0."""

    x = frame["positioning_percentile"].to_numpy(dtype=float)
    y = frame["forward_log_return"].to_numpy(dtype=float)
    if len(x) < 3:
        raise ValueError("OLS requires at least three observations")
    x_centered = x - x.mean()
    denominator = float(np.dot(x_centered, x_centered))
    if denominator <= 0:
        raise ValueError("OLS positioning predictor has zero variance")
    slope = float(np.dot(x_centered, y - y.mean()) / denominator)
    intercept = float(y.mean() - slope * x.mean())
    residuals = y - (intercept + slope * x)
    residual_variance = float(np.dot(residuals, residuals) / (len(x) - 2))
    standard_error = math.sqrt(residual_variance / denominator)
    t_stat = slope / standard_error if standard_error > 0 else math.copysign(math.inf, slope)
    one_sided_p = 0.5 * math.erfc(-t_stat / math.sqrt(2.0))
    return RegressionStats(
        observations=len(x),
        intercept=intercept,
        slope=slope,
        slope_standard_error=standard_error,
        t_stat=t_stat,
        one_sided_p=one_sided_p,
    )


def _slope(frame: pd.DataFrame) -> float:
    return fit_ols(frame).slope


def _shuffle_reversal_percentile(frame: pd.DataFrame, shuffles: int) -> float:
    rng = np.random.default_rng(SHUFFLE_SEED)
    observed = _slope(frame)
    y = frame["forward_log_return"].to_numpy(dtype=float)
    market_indices = [
        indices.to_numpy(dtype=int)
        for _, indices in frame.reset_index(drop=True).groupby("symbol").groups.items()
    ]
    base = frame["positioning_percentile"].to_numpy(dtype=float)
    shuffled_slopes = np.empty(shuffles, dtype=float)
    for iteration in range(shuffles):
        shuffled = base.copy()
        for indices in market_indices:
            shuffled[indices] = rng.permutation(shuffled[indices])
        centered = shuffled - shuffled.mean()
        shuffled_slopes[iteration] = float(
            np.dot(centered, y - y.mean()) / np.dot(centered, centered)
        )
    return float(np.mean(shuffled_slopes >= observed))


def evaluate_relationship(
    observations: pd.DataFrame,
    availability: AvailabilityAudit,
    missing_price_symbols: tuple[str, ...],
    *,
    shuffles: int = DEFAULT_SHUFFLES,
) -> RelationshipResult:
    """Apply the fixed chronological relationship gates."""

    unique_dates = np.array(
        sorted(pd.to_datetime(observations["effective_available_date"]).unique())
    )
    if len(unique_dates) < 2:
        raise ValueError("relationship sample has fewer than two unique dates")
    split_index = max(1, min(len(unique_dates) - 1, int(len(unique_dates) * IS_FRACTION)))
    cutoff = pd.Timestamp(unique_dates[split_index - 1])
    ins = observations[observations["effective_available_date"] <= cutoff].copy()
    oos = observations[observations["effective_available_date"] > cutoff].copy()
    is_regression = fit_ols(ins)
    oos_regression = fit_ols(oos)

    oos["bucket"] = pd.cut(
        oos["positioning_percentile"],
        bins=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        labels=["Q1_low", "Q2", "Q3", "Q4", "Q5_high"],
        include_lowest=True,
    )
    bucket_series = oos.groupby("bucket", observed=False)["forward_log_return"].mean()
    bucket_means = {str(key): float(value) for key, value in bucket_series.items()}
    ordered_means = list(bucket_means.values())
    adjacent_decreases = sum(
        left > right for left, right in zip(ordered_means, ordered_means[1:], strict=False)
    )
    bottom_minus_top = ordered_means[0] - ordered_means[-1]

    market_slopes = {
        symbol: _slope(group)
        for symbol, group in oos.groupby("symbol")
        if len(group) >= 3 and group["positioning_percentile"].nunique() > 1
    }
    negative_market_fraction = (
        sum(slope < 0 for slope in market_slopes.values()) / len(market_slopes)
        if market_slopes
        else 0.0
    )
    leave_one_out_slopes = [
        _slope(oos[oos["symbol"] != symbol]) for symbol in sorted(market_slopes)
    ]
    negative_leave_one_out_fraction = (
        sum(slope < 0 for slope in leave_one_out_slopes) / len(leave_one_out_slopes)
        if leave_one_out_slopes
        else 0.0
    )
    shuffle_percentile = _shuffle_reversal_percentile(oos, shuffles)

    market_count = int(observations["symbol"].nunique())
    checks = (
        (market_count >= MIN_MARKETS, f"markets {market_count} < {MIN_MARKETS}"),
        (
            len(oos) >= MIN_OOS_OBSERVATIONS,
            f"OOS observations {len(oos)} < {MIN_OOS_OBSERVATIONS}",
        ),
        (is_regression.slope < 0, f"IS slope {is_regression.slope:.6f} is not negative"),
        (oos_regression.slope < 0, f"OOS slope {oos_regression.slope:.6f} is not negative"),
        (
            oos_regression.one_sided_p <= 0.10,
            f"OOS one-sided p {oos_regression.one_sided_p:.4f} > 0.10",
        ),
        (
            bottom_minus_top > 0,
            f"OOS bottom-minus-top return {bottom_minus_top:.6f} is not positive",
        ),
        (
            adjacent_decreases >= 3,
            f"OOS adjacent bucket decreases {adjacent_decreases} < 3",
        ),
        (
            negative_market_fraction >= 0.60,
            f"negative market slope fraction {negative_market_fraction:.1%} < 60%",
        ),
        (
            shuffle_percentile >= 0.95,
            f"shuffle reversal percentile {shuffle_percentile:.1%} < 95%",
        ),
        (
            negative_leave_one_out_fraction >= 0.80,
            "negative leave-one-market-out slope fraction "
            f"{negative_leave_one_out_fraction:.1%} < 80%",
        ),
    )
    reasons = tuple(message for passed, message in checks if not passed)
    return RelationshipResult(
        verdict="RELATIONSHIP_PASS" if not reasons else "RELATIONSHIP_FAIL",
        reasons=reasons,
        cutoff_date=cutoff.date().isoformat(),
        markets=market_count,
        observations=len(observations),
        oos_observations=len(oos),
        availability=availability,
        in_sample=is_regression,
        out_of_sample=oos_regression,
        oos_bucket_means=bucket_means,
        bottom_minus_top=bottom_minus_top,
        adjacent_decreases=adjacent_decreases,
        negative_market_fraction=negative_market_fraction,
        shuffle_reversal_percentile=shuffle_percentile,
        negative_leave_one_out_fraction=negative_leave_one_out_fraction,
        missing_price_symbols=missing_price_symbols,
    )


def build_report(result: RelationshipResult) -> str:
    """Render an auditable Markdown result."""

    lines = [
        "# COT Positioning Relationship Test",
        "",
        f"## Verdict: **{result.verdict}**",
        "",
        "This is a relationship falsifier, not a strategy backtest or trading authorization.",
        "",
        "## Sample",
        "",
        f"- Markets: {result.markets}",
        f"- Observations: {result.observations}",
        f"- OOS observations: {result.oos_observations}",
        f"- Chronological cutoff: {result.cutoff_date}",
        f"- Missing price markets: {', '.join(result.missing_price_symbols) or 'none'}",
        f"- Delayed/revised rows excluded: {result.availability.excluded_rows}",
        f"- Verified release dates applied: {result.availability.overridden_rows}",
        "",
        "## Regression",
        "",
        "| Window | N | Slope | SE | t | One-sided p |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, stats in (
        ("IS", result.in_sample),
        ("OOS", result.out_of_sample),
    ):
        lines.append(
            f"| {name} | {stats.observations} | {stats.slope:.6f} | "
            f"{stats.slope_standard_error:.6f} | {stats.t_stat:.3f} | "
            f"{stats.one_sided_p:.4f} |"
        )
    lines.extend(
        (
            "",
            "## OOS stability",
            "",
            f"- Bottom-minus-top quintile mean return: {result.bottom_minus_top:.4%}",
            f"- Adjacent quintile decreases: {result.adjacent_decreases}/4",
            f"- Markets with negative slope: {result.negative_market_fraction:.1%}",
            f"- Shuffled-signal reversal percentile: {result.shuffle_reversal_percentile:.1%}",
            f"- Negative leave-one-market-out slopes: {result.negative_leave_one_out_fraction:.1%}",
            "",
            "| Bucket | Mean four-week log return |",
            "| --- | ---: |",
        )
    )
    lines.extend(f"| {bucket} | {mean:.4%} |" for bucket, mean in result.oos_bucket_means.items())
    lines.extend(("", "## Gate failures", ""))
    if result.reasons:
        lines.extend(f"- {reason}" for reason in result.reasons)
    else:
        lines.append("- None.")
    lines.extend(
        (
            "",
            "## Limits",
            "",
            "- Yahoo continuous front-month futures can contain roll discontinuities.",
            "- A pass authorizes a separate roll-aware, cost-aware strategy data gate only.",
            "- A fail closes this fixed COT reversal relationship test without parameter rescue.",
            "",
        )
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2010, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 6, 16))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/research/cot_positioning/COT_RELATIONSHIP_RESULTS_2026-06.md"),
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("results/cot_positioning_relationship.json"),
    )
    parser.add_argument("--shuffles", type=int, default=DEFAULT_SHUFFLES)
    args = parser.parse_args()

    if args.start >= args.end:
        parser.error("--start must be before --end")
    if args.shuffles < 100:
        parser.error("--shuffles must be at least 100")

    fetched = fetch_legacy_rows(args.start, args.end, FIXED_UNIVERSE)
    cot = normalize_rows(fetched.rows, FIXED_UNIVERSE)
    prices, missing = fetch_prices(args.start, args.end)
    observations, availability = build_observations(cot, prices)
    result = evaluate_relationship(observations, availability, missing, shuffles=args.shuffles)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_report(result), encoding="utf-8")
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
    print(f"{result.verdict}: {args.output}")
    return 0 if result.verdict == "RELATIONSHIP_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
