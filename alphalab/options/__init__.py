"""European ETF option analysis and pricing. No data connections at import time."""

from .analysis import analyze_chain, stress_grid
from .curves import DiscountCurve
from .features import option_features, strip_moments
from .literature import literature_catalog
from .portfolio import basket_stress, joint_monte_carlo
from .pricing import EuropeanOption, black_scholes, greeks, implied_volatility, monte_carlo
from .quality import QualityError, validate_snapshot
from .research import research_panel, walk_forward_research
from .surface import surface_iv, volatility_surface

__all__ = [
    "option_features",
    "strip_moments",
    "literature_catalog",
    "research_panel",
    "walk_forward_research",
    "DiscountCurve",
    "basket_stress",
    "joint_monte_carlo",
    "surface_iv",
    "volatility_surface",
    "EuropeanOption",
    "QualityError",
    "analyze_chain",
    "black_scholes",
    "greeks",
    "implied_volatility",
    "monte_carlo",
    "stress_grid",
    "validate_snapshot",
]
