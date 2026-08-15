"""Registered implementations for the four strategy pipeline stages."""
from __future__ import annotations

import math
from typing import Any

import numpy as np


def configured_stock_signal(context: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Combine registered PIT factor contributions into stock scores."""

    factor_count = len(context.get("factor_names") or [])
    minimum_coverage = float(context["settings"]["min_factor_coverage"])
    scores: dict[str, float] = {}
    for candidate in context["candidates"]:
        values = candidate.get("factor_scores") or {}
        coverage = len(values) / factor_count if factor_count else 1.0
        if coverage >= minimum_coverage:
            scores[str(candidate["symbol"])] = float(sum(values.values()))
    return {"scores": scores}


def configured_stock_portfolio(context: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Select the highest scores and propose equal target weights."""

    ranked = sorted(
        context["signal_scores"],
        key=context["signal_scores"].get,
        reverse=True,
    )[: int(context["limits"]["max_stocks"])]
    weight = 1.0 / len(ranked) if ranked else 0.0
    return {"weights": {symbol: weight for symbol in ranked}}


def configured_stock_risk(context: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Apply count, total-exposure, and per-name concentration limits."""

    proposed = {
        str(symbol): float(weight)
        for symbol, weight in context["proposed_weights"].items()
        if math.isfinite(float(weight)) and float(weight) > 0
    }
    ranked = dict(
        sorted(proposed.items(), key=lambda item: item[1], reverse=True)[
            : int(context["limits"]["max_stocks"])
        ]
    )
    return {
        "weights": _cap_weights(
            ranked,
            float(context["limits"]["max_weight"]),
        )
    }


def configured_stock_execution(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Use configured assumptions before core market-data and cash gates."""

    return {"execution": dict(context["configured_execution"])}


def configured_timing_signal(context: dict[str, Any]) -> dict[str, Any]:
    """Combine registered PIT market-timing signals into one bounded score."""

    returns = np.asarray(
        [float(row["value"]) for row in context["market_returns"]],
        dtype=float,
    )
    signals = list(context.get("signals") or [])
    kind_counts = {
        kind: sum(1 for signal in signals if signal["kind"] == kind)
        for kind in {signal["kind"] for signal in signals}
    }
    components: dict[str, float] = {}
    weighted_total = 0.0
    total_weight = 0.0
    for index, signal in enumerate(signals):
        kind = str(signal["kind"])
        window = int(signal["window"])
        threshold = float(signal["threshold"])
        weight = float(signal["weight"])
        minimum = max(2, window // 2)
        score = 0.0
        if len(returns) >= minimum:
            sample = returns[-window:]
            if kind == "trend":
                wealth = np.cumprod(1.0 + returns)
                moving_average = float(np.mean(wealth[-window:]))
                relative = float(wealth[-1] / moving_average - 1.0)
                score = 1.0 if relative > threshold else 0.0
            elif kind == "momentum":
                momentum = float(np.prod(1.0 + sample) - 1.0)
                score = 1.0 if momentum > threshold else 0.0
            else:
                volatility = float(np.std(sample, ddof=1) * np.sqrt(12.0))
                score = min(1.0, max(0.0, threshold / volatility)) if volatility > 0 else 0.0
        label = kind if kind_counts[kind] == 1 else f"{kind}_{index + 1}"
        components[label] = score
        weighted_total += score * weight
        total_weight += weight
    combined = weighted_total / total_weight if total_weight > 0 else 0.0
    return {"score": min(1.0, max(0.0, combined)), "components": components}


def configured_timing_portfolio(context: dict[str, Any]) -> dict[str, float]:
    """Map a 0-1 timing score linearly into proposed market exposure."""

    minimum = float(context["limits"]["min_exposure"])
    maximum = float(context["limits"]["max_exposure"])
    return {
        "market_exposure": minimum + float(context["signal_score"]) * (maximum - minimum)
    }


def configured_timing_risk(context: dict[str, Any]) -> dict[str, float]:
    """Clamp aggregate exposure to the configured hard limits."""

    minimum = float(context["limits"]["min_exposure"])
    maximum = float(context["limits"]["max_exposure"])
    proposed = float(context["proposed_exposure"])
    return {"market_exposure": min(maximum, max(minimum, proposed))}


def configured_timing_execution(context: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Use configured non-negative turnover cost assumptions."""

    return {"execution": dict(context["configured_execution"])}


def _cap_weights(weights: dict[str, float], max_weight: float) -> dict[str, float]:
    total = float(sum(weights.values()))
    if total <= 0:
        return {}
    remaining = min(1.0, total)
    active = set(weights)
    result = {symbol: 0.0 for symbol in weights}
    while active and remaining > 1e-12:
        active_total = float(sum(weights[symbol] for symbol in active))
        if active_total <= 0:
            break
        proposed = {
            symbol: weights[symbol] / active_total * remaining for symbol in active
        }
        capped = [symbol for symbol, value in proposed.items() if value > max_weight + 1e-12]
        if not capped:
            result.update(proposed)
            break
        for symbol in capped:
            result[symbol] = max_weight
            remaining -= max_weight
            active.remove(symbol)
        if len(active) * max_weight <= remaining + 1e-12:
            for symbol in active:
                result[symbol] = max_weight
            break
    return {symbol: float(weight) for symbol, weight in result.items() if weight > 1e-12}


__all__ = [
    "configured_stock_execution",
    "configured_stock_portfolio",
    "configured_stock_risk",
    "configured_stock_signal",
    "configured_timing_execution",
    "configured_timing_portfolio",
    "configured_timing_risk",
    "configured_timing_signal",
]
