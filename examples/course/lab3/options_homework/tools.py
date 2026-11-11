"""Register the worked example, then connect the three student functions here."""

from alphalab.tools import ToolRegistry, ToolSpec

from .models import ReportRequest


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
    # Register options_lab.expiry, options_lab.greeks and options_lab.stress here.
    # Use ReportRequest and the matching handler from calculations.py.
