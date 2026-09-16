"""Analytics facade."""

from alphalab.analytics.attribution import FACTOR_NAMES, factor_attribution
from alphalab.analytics.factor_research import evaluate_factor
from alphalab.analytics.metrics import PerformanceMetrics
from alphalab.analytics.replication import audit_annual_return_table
from alphalab.analytics.robustness import (
    RobustnessThresholds,
    equal_weight_benchmark,
    robustness_report,
)
from alphalab.analytics.technical_evidence import (
    TechnicalMetadata,
    render_technical_evidence,
    technical_evidence,
)

__all__ = [
    "FACTOR_NAMES",
    "PerformanceMetrics",
    "RobustnessThresholds",
    "TechnicalMetadata",
    "audit_annual_return_table",
    "equal_weight_benchmark",
    "evaluate_factor",
    "factor_attribution",
    "robustness_report",
    "render_technical_evidence",
    "technical_evidence",
]
