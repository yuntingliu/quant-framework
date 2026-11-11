"""AlphaLab's European Monte Carlo pricing adapter."""

from alphalab.options import EuropeanOption, monte_carlo

from .models import MonteCarloRequest


def price_monte_carlo(request: MonteCarloRequest) -> dict:
    option = EuropeanOption(
        spot=request.spot, strike=request.strike, maturity=request.maturity,
        rate=request.rate, volatility=request.volatility, option_type=request.kind,
        dividend_yield=request.dividend_yield,
    )
    result = monte_carlo(
        option, paths=request.paths, seed=request.seed, jump_intensity=request.jump_intensity,
        jump_mean=request.jump_mean, jump_volatility=request.jump_volatility,
    )
    return {
        "requested_model": request.model, "kind": request.kind, "strike": request.strike,
        "price_unit": "CNY per ETF unit", **result,
    }
