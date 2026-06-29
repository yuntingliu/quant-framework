"""Factor facade."""

from alphalab.factors.fundamental import FundamentalFactors, compute_fundamental_factors
from alphalab.factors.registry import FactorRecord, compute_factor, get_factor, list_factors, register_factor
from alphalab.factors.technical import TechnicalFactors, compute_technical_factors

__all__ = [
    "FactorRecord",
    "FundamentalFactors",
    "TechnicalFactors",
    "compute_factor",
    "compute_fundamental_factors",
    "compute_technical_factors",
    "get_factor",
    "list_factors",
    "register_factor",
]
