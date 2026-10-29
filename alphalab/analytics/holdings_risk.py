"""Holdings-based risk for a paper account.

Everything here is computed from a frozen snapshot: the positions, their prices,
the daily close series used, and the parameters. The caller stores those inputs
next to the result, keyed by their hash, so the same analysis can be replayed
later and produce the same numbers.

Two rules shape the output:

* **Missing information is never guessed.** A position without enough price
  history is reported as ``evaluated: false`` with a reason, and the analysis
  states which share of the portfolio that leaves uncovered.
* **Risk numbers are not a promise.** Every result repeats the assumptions and a
  disclaimer; nothing here is a forecast of returns.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

TRADING_DAYS = 252
METHOD = "holdings_risk_v1"
DISCLAIMER = (
    "Risk metrics describe past co-movement of these holdings; they are not a "
    "forecast and not a guarantee of future returns."
)


@dataclass(frozen=True)
class HoldingsRiskParameters:
    """Analysis parameters; changing one changes the input hash, never an old result."""

    lookback_days: int = 750
    minimum_observations: int = 60
    scenario_window: int = 20
    scenario_count: int = 3
    price_shock: float = -0.10
    volatility_multiplier: float = 1.5
    correlation_shift: float = 0.20
    confidence: float = 0.95

    def __post_init__(self) -> None:
        for name in ("lookback_days", "minimum_observations", "scenario_window", "scenario_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise ValueError(f"{name} must be a whole number of at least 2, got {value!r}")
        if self.scenario_count < 1:
            raise ValueError("scenario_count must be at least 1")
        for name in ("price_shock", "volatility_multiplier", "correlation_shift", "confidence"):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite, got {value!r}")
        if not -1.0 <= self.price_shock <= 0.0:
            raise ValueError("price_shock must be between -1 and 0 (a loss)")
        if self.volatility_multiplier <= 0:
            raise ValueError("volatility_multiplier must be positive")
        if not -1.0 <= self.correlation_shift <= 1.0:
            raise ValueError("correlation_shift must be between -1 and 1")
        if not 0.0 < self.confidence < 1.0:
            raise ValueError("confidence must be between 0 and 1")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def frozen_inputs(
    *,
    holdings: Iterable[Mapping[str, Any]],
    series: Mapping[str, Sequence[Sequence[Any]]],
    price_date: str | None,
    parameters: HoldingsRiskParameters,
    source: str = "demo",
) -> dict[str, Any]:
    """The exact inputs of one analysis, in a JSON-safe canonical shape."""
    frozen_holdings = sorted(
        (
            {
                "symbol": str(item["symbol"]).upper(),
                "quantity": float(item["quantity"]),
                "price": float(item["price"]),
                "market_value": float(
                    item.get("market_value")
                    if item.get("market_value") is not None
                    else float(item["quantity"]) * float(item["price"])
                ),
            }
            for item in holdings
        ),
        key=lambda item: item["symbol"],
    )
    frozen_series = {
        str(symbol).upper(): [[str(date), float(close)] for date, close in values]
        for symbol, values in sorted(series.items())
    }
    return {
        "holdings": frozen_holdings,
        "series": frozen_series,
        "price_date": price_date,
        "parameters": parameters.as_dict(),
        "source": source,
    }


def input_hash(inputs: Mapping[str, Any]) -> str:
    body = json.dumps(inputs, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def analyse(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Run the holdings analysis from frozen inputs only.

    ``inputs`` is the output of :func:`frozen_inputs`; the function never reads the
    database or the clock, so the same inputs always produce the same result.
    """
    parameters = HoldingsRiskParameters(**dict(inputs.get("parameters") or {}))
    holdings = list(inputs.get("holdings") or [])
    series = dict(inputs.get("series") or {})
    price_date = inputs.get("price_date")
    source = str(inputs.get("source") or "unknown")
    digest = input_hash(inputs)

    warnings: list[str] = []
    total_value = float(sum(_finite(item.get("market_value")) or 0.0 for item in holdings))
    result: dict[str, Any] = {
        "method": METHOD,
        "input_hash": digest,
        "price_date": price_date,
        "source": source,
        "as_of": None,
        "assumptions": {
            "library": "daily closes, simple returns",
            "trading_days_per_year": TRADING_DAYS,
            "lookback_days": parameters.lookback_days,
            "minimum_observations": parameters.minimum_observations,
            "scenario_window_days": parameters.scenario_window,
            "scenario_count": parameters.scenario_count,
            "confidence": parameters.confidence,
            "shocks": {
                "price_shock": parameters.price_shock,
                "volatility_multiplier": parameters.volatility_multiplier,
                "correlation_shift": parameters.correlation_shift,
            },
            "data_source": source,
            "frequency": "daily",
            "note": (
                "Missing price history is reported as unevaluated instead of being "
                "filled, interpolated or treated as zero."
            ),
        },
        "holdings": [],
        "concentration": {},
        "contributions": [],
        "portfolio": {},
        "scenarios": {"items": [], "complete": False},
        "shocks": {},
        "unevaluated": {"symbols": [], "reasons": {}, "weight_share": 0.0},
        "warnings": warnings,
        "disclaimer": DISCLAIMER,
    }

    if not holdings or total_value <= 0:
        warnings.append(
            "The account has no valued holdings, so no risk analysis could be evaluated."
        )
        result["unevaluated"]["reason"] = "no valued holdings"
        return result

    frame = _returns_frame(series)
    weights: dict[str, float] = {}
    reasons: dict[str, str] = {}
    for item in holdings:
        symbol = str(item["symbol"]).upper()
        value = _finite(item.get("market_value")) or 0.0
        weight = value / total_value if total_value > 0 else 0.0
        weights[symbol] = weight
        result["holdings"].append(
            {
                "symbol": symbol,
                "quantity": float(item["quantity"]),
                "price": float(item["price"]),
                "market_value": value,
                "weight": weight,
            }
        )
        if symbol not in frame.columns:
            reasons[symbol] = "no price history was supplied for this symbol"
            continue
        count = int(frame[symbol].notna().sum())
        if count < parameters.minimum_observations:
            reasons[symbol] = (
                f"only {count} usable observations, {parameters.minimum_observations} required"
            )

    evaluated = [item["symbol"] for item in result["holdings"] if item["symbol"] not in reasons]
    unevaluated_weight = float(sum(weights.get(symbol, 0.0) for symbol in reasons))
    result["unevaluated"] = {
        "symbols": sorted(reasons),
        "reasons": reasons,
        "weight_share": unevaluated_weight,
    }
    if reasons:
        warnings.append(
            f"{len(reasons)} position(s) covering {unevaluated_weight:.2%} of the account could "
            "not be evaluated and are excluded from the covariance inputs."
        )
    if not evaluated:
        warnings.append("No position had enough price history for a risk evaluation.")
        result["concentration"] = _concentration(result["holdings"], weights, evaluated)
        return result

    usable_weights = {symbol: weights[symbol] for symbol in evaluated}
    weight_sum = float(sum(usable_weights.values()))
    if weight_sum <= 0:
        warnings.append("Evaluated positions carry no value, so weights cannot be formed.")
        return result
    normalized = {symbol: weight / weight_sum for symbol, weight in usable_weights.items()}

    returns = frame[list(normalized)].dropna(how="all")
    covariance = returns.cov(min_periods=parameters.minimum_observations)
    covariance = covariance.reindex(index=list(normalized), columns=list(normalized))
    vector = np.array([normalized[symbol] for symbol in covariance.columns], dtype=float)
    matrix = covariance.to_numpy(dtype=float)
    annualized = matrix * TRADING_DAYS
    variance = float(vector @ annualized @ vector) if np.isfinite(annualized).all() else float("nan")
    volatility = math.sqrt(variance) if math.isfinite(variance) and variance > 0 else None

    contributions = []
    if volatility is not None:
        marginal = annualized @ vector
        for index, symbol in enumerate(covariance.columns):
            # Euler decomposition: contributions sum to the portfolio volatility.
            contribution = float(vector[index] * marginal[index] / volatility)
            contributions.append(
                {
                    "symbol": symbol,
                    "weight": normalized[symbol],
                    "weight_in_evaluated": normalized[symbol],
                    "annualized_volatility": _safe_sqrt(annualized[index, index]),
                    "contribution_to_volatility": contribution,
                    "share_of_risk": contribution / volatility,
                    "evaluated": True,
                }
            )
    else:
        warnings.append(
            "Portfolio volatility could not be estimated from the supplied history; "
            "position contributions are unevaluated."
        )
        for index, symbol in enumerate(covariance.columns):
            contributions.append(
                {
                    "symbol": symbol,
                    "weight": normalized[symbol],
                    "annualized_volatility": (
                        _safe_sqrt(annualized[index, index])
                        if np.isfinite(annualized).all()
                        else None
                    ),
                    "contribution_to_volatility": None,
                    "share_of_risk": None,
                    "evaluated": False,
                }
            )
    result["contributions"] = contributions
    result["concentration"] = _concentration(result["holdings"], weights, evaluated)
    result["portfolio"] = _portfolio_block(
        volatility=volatility,
        observations=int(returns.dropna(how="any").shape[0]),
        symbols=list(covariance.columns),
    )

    scenario_frame = returns[list(normalized)].dropna(how="any")
    result["scenarios"] = _scenarios(
        scenario_frame, normalized, parameters, warnings, evaluated, reasons
    )
    result["shocks"] = _shocks(
        volatility=volatility,
        annualized=annualized,
        weights=vector,
        symbols=list(covariance.columns),
        total_value=total_value,
        parameters=parameters,
    )
    result["as_of"] = str(returns.index.max().date()) if len(returns.index) else None
    return result


def _safe_sqrt(value: float | None) -> float | None:
    if value is None or not math.isfinite(value) or value < 0:
        return None
    return math.sqrt(float(value))


def _returns_frame(series: Mapping[str, Sequence[Sequence[Any]]]) -> pd.DataFrame:
    columns: dict[str, pd.Series] = {}
    for symbol, values in series.items():
        rows = [(str(date), _finite(close)) for date, close in values]
        rows = [(date, close) for date, close in rows if close is not None and close > 0]
        if not rows:
            continue
        index = pd.to_datetime([date for date, _ in rows], errors="coerce")
        series_values = pd.Series([close for _, close in rows], index=index, dtype=float)
        series_values = series_values[~series_values.index.isna()]
        if series_values.empty:
            continue
        columns[str(symbol).upper()] = series_values[~series_values.index.duplicated(keep="last")]
    if not columns:
        return pd.DataFrame()
    frame = pd.DataFrame(columns).sort_index()
    return frame.pct_change(fill_method=None)


def _concentration(
    holdings: Sequence[Mapping[str, Any]], weights: Mapping[str, float], evaluated: Sequence[str]
) -> dict[str, Any]:
    ranked = sorted(
        (
            {"symbol": item["symbol"], "weight": weights.get(item["symbol"], 0.0)}
            for item in holdings
        ),
        key=lambda item: item["weight"],
        reverse=True,
    )
    positive = [item["weight"] for item in ranked if item["weight"] > 0]
    hhi = float(sum(weight * weight for weight in positive)) if positive else None
    return {
        "positions": len(ranked),
        "evaluated_positions": len(evaluated),
        "top1_weight": positive[0] if positive else None,
        "top3_weight": float(sum(positive[:3])) if positive else None,
        "hhi": hhi,
        "effective_positions": (1.0 / hhi) if hhi else None,
        "largest": ranked[0]["symbol"] if ranked else None,
        "evaluated": bool(evaluated),
    }


def _portfolio_block(
    *, volatility: float | None, observations: int, symbols: Sequence[str]
) -> dict[str, Any]:
    return {
        "annualized_volatility": volatility,
        "observations": observations,
        "symbols": list(symbols),
        "evaluated": volatility is not None,
        "note": (
            "Annualized with 252 trading days from daily simple returns of the evaluated "
            "positions only."
        ),
    }


def _scenarios(
    returns: pd.DataFrame,
    weights: Mapping[str, float],
    parameters: HoldingsRiskParameters,
    warnings: list[str],
    evaluated: Sequence[str],
    reasons: Mapping[str, str],
) -> dict[str, Any]:
    window = parameters.scenario_window
    block: dict[str, Any] = {
        "method": "worst historical windows for these weights",
        "window_days": window,
        "requested": parameters.scenario_count,
        "items": [],
        "complete": not reasons,
    }
    if returns.empty or len(returns) < window:
        warnings.append(
            f"Historical scenarios need {window} complete trading days; "
            f"only {len(returns)} are available."
        )
        block["unevaluated_reason"] = f"fewer than {window} complete observations"
        return block

    weight_vector = np.array([weights[symbol] for symbol in returns.columns], dtype=float)
    daily = returns.to_numpy(dtype=float) @ weight_vector
    daily = pd.Series(daily, index=returns.index)
    compounded = (1.0 + daily).cumprod()
    candidates: list[tuple[float, int]] = []
    for start in range(0, len(compounded) - window + 1):
        end_value = float(compounded.iloc[start + window - 1])
        start_value = float(compounded.iloc[start - 1]) if start > 0 else 1.0
        if start_value <= 0:
            continue
        candidates.append((end_value / start_value - 1.0, start))
    candidates.sort()
    used: list[tuple[int, int]] = []
    for value, start in candidates:
        end = start + window - 1
        if any(not (end < used_start or start > used_end) for used_start, used_end in used):
            continue
        used.append((start, end))
        block["items"].append(
            {
                "label": f"worst window {len(block['items']) + 1}",
                "start": str(returns.index[start].date()),
                "end": str(returns.index[end].date()),
                "portfolio_return": value,
                "evaluated": True,
                "symbols": list(returns.columns),
            }
        )
        if len(block["items"]) >= parameters.scenario_count:
            break
    if not block["items"]:
        warnings.append("No historical scenario window could be evaluated.")
    if reasons:
        block["partial"] = (
            "Computed from "
            + ", ".join(sorted(evaluated))
            + " only; positions without history are excluded: "
            + ", ".join(sorted(reasons))
            + "."
        )
    return block


def _shocks(
    *,
    volatility: float | None,
    annualized: np.ndarray,
    weights: np.ndarray,
    symbols: Sequence[str],
    total_value: float,
    parameters: HoldingsRiskParameters,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "price_shock": {
            "assumption": f"every held price moves {parameters.price_shock:.0%} at once",
            "loss_value": total_value * parameters.price_shock,
            "loss_share_of_holdings": parameters.price_shock,
            "evaluated": True,
        },
        "volatility": {
            "assumption": (
                f"portfolio volatility is {parameters.volatility_multiplier:g}x the observed level"
            ),
            "baseline_annualized_volatility": volatility,
            "shocked_annualized_volatility": (
                volatility * parameters.volatility_multiplier if volatility else None
            ),
            "evaluated": volatility is not None,
        },
        "correlation": {
            "assumption": (
                f"every pairwise correlation rises by {parameters.correlation_shift:g}, "
                "clipped to [-1, 1]; individual volatilities unchanged"
            ),
            "baseline_annualized_volatility": volatility,
            "shocked_annualized_volatility": None,
            "evaluated": False,
        },
        "note": (
            "Shocks are mechanical what-ifs on the current holdings, not forecasts and "
            "not a guarantee of any outcome."
        ),
    }
    if volatility is None or not np.isfinite(annualized).all() or len(symbols) < 2:
        result["correlation"]["unevaluated_reason"] = (
            "correlations need at least two evaluated positions with usable covariance"
        )
        return result
    std = np.sqrt(np.diag(annualized))
    if np.any(std <= 0):
        result["correlation"]["unevaluated_reason"] = "a position has zero estimated volatility"
        return result
    correlation = annualized / np.outer(std, std)
    np.fill_diagonal(correlation, 1.0)
    shocked = np.clip(correlation + parameters.correlation_shift, -1.0, 1.0)
    np.fill_diagonal(shocked, 1.0)
    shocked_annualized = shocked * np.outer(std, std)
    variance = float(weights @ shocked_annualized @ weights)
    if variance <= 0 or not math.isfinite(variance):
        result["correlation"]["unevaluated_reason"] = "the shocked covariance is not positive"
        return result
    result["correlation"]["shocked_annualized_volatility"] = math.sqrt(variance)
    result["correlation"]["evaluated"] = True
    return result


__all__ = [
    "DISCLAIMER",
    "HoldingsRiskParameters",
    "METHOD",
    "analyse",
    "frozen_inputs",
    "input_hash",
]
