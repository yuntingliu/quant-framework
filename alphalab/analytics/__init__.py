"""Analytics facade."""

from alphalab.analytics.attribution import FACTOR_NAMES, factor_attribution
from alphalab.analytics.factor_research import evaluate_factor
from alphalab.analytics.metrics import PerformanceMetrics
from alphalab.analytics.robustness import (
    RobustnessThresholds,
    equal_weight_benchmark,
    robustness_report,
)

__all__ = [
    "FACTOR_NAMES",
    "PerformanceMetrics",
    "RobustnessThresholds",
    "equal_weight_benchmark",
    "evaluate_factor",
    "factor_attribution",
    "robustness_report",
]
