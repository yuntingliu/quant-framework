"""Register the pricing examples and connect the three position-report tools."""

from alphalab.tools import ToolRegistry, ToolSpec

from .mc_pricing import price_monte_carlo
from .models import MonteCarloRequest, ReportRequest


def case_inputs(request: ReportRequest) -> dict:
    return {
        "underlying": request.underlying,
        "as_of": request.as_of.isoformat(),
        "expiry": request.expiry.isoformat(),
        "maturity_years": request.maturity,
        "contract_count": len(request.legs),
        "scenario_count": len(request.spot_shocks) * len(request.iv_shifts),
    }


def register_lab_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="options_lab.case_inputs",
            description="Read validated option report inputs and counts",
            input_model=ReportRequest,
            handler=case_inputs,
        )
    )
    registry.register(
        ToolSpec(
            name="options_lab.monte_carlo",
            description="Price a European ETF option under GBM or Merton in CNY per ETF unit.",
            input_model=MonteCarloRequest,
            handler=price_monte_carlo,
        )
    )
    # Register options_lab.expiry, options_lab.greeks and options_lab.stress here.
    # Use ReportRequest and the matching handler from calculations.py.
