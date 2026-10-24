"""European BSM and exact-terminal risk-neutral Merton jump simulation.

Prices and Greeks are per underlying unit, not per contract. Rates, yields and
volatilities are annual decimal values; maturity uses ACT/365F.
"""

from dataclasses import dataclass, replace
from math import exp, isfinite, log, sqrt

import numpy as np
from scipy.optimize import brentq
from scipy.special import ndtr


@dataclass(frozen=True)
class EuropeanOption:
    spot: float
    strike: float
    maturity: float
    rate: float
    volatility: float
    option_type: str = "C"
    dividend_yield: float = 0.0

    def __post_init__(self):
        values = (
            self.spot,
            self.strike,
            self.maturity,
            self.rate,
            self.volatility,
            self.dividend_yield,
        )
        if not all(isfinite(x) for x in values):
            raise ValueError("All pricing inputs must be finite")
        if self.spot <= 0 or self.strike <= 0 or self.maturity < 0 or self.volatility < 0:
            raise ValueError("Positive spot/strike and nonnegative maturity/volatility required")
        if self.option_type not in ("C", "P"):
            raise ValueError("option_type must be C or P")


def price_bounds(option: EuropeanOption) -> tuple[float, float]:
    s = option.spot * exp(-option.dividend_yield * option.maturity)
    k = option.strike * exp(-option.rate * option.maturity)
    return (max(s - k, 0.0), s) if option.option_type == "C" else (max(k - s, 0.0), k)


def black_scholes(option: EuropeanOption) -> float:
    lower, _ = price_bounds(option)
    if option.maturity == 0 or option.volatility == 0:
        return lower
    t, v = option.maturity, option.volatility
    d1 = (
        log(option.spot / option.strike) + (option.rate - option.dividend_yield + v * v / 2) * t
    ) / (v * sqrt(t))
    d2 = d1 - v * sqrt(t)
    s = option.spot * exp(-option.dividend_yield * t)
    k = option.strike * exp(-option.rate * t)
    sign = 1 if option.option_type == "C" else -1
    return float(max(0.0, sign * (s * ndtr(sign * d1) - k * ndtr(sign * d2))))


def greeks(option: EuropeanOption) -> dict[str, float]:
    """Theta per calendar day; vega/rho per one percentage point."""
    if option.maturity <= 0 or option.volatility <= 0:
        raise ValueError("Greeks require positive maturity and volatility")
    s, k, t, r, v, q = (
        option.spot,
        option.strike,
        option.maturity,
        option.rate,
        option.volatility,
        option.dividend_yield,
    )
    d1 = (log(s / k) + (r - q + v * v / 2) * t) / (v * sqrt(t))
    d2 = d1 - v * sqrt(t)
    density = exp(-d1 * d1 / 2) / sqrt(2 * np.pi)
    sign = 1 if option.option_type == "C" else -1
    return {
        "delta": float(sign * exp(-q * t) * ndtr(sign * d1)),
        "gamma": exp(-q * t) * density / (s * v * sqrt(t)),
        "vega_1pct": s * exp(-q * t) * density * sqrt(t) / 100,
        "theta_day": float(
            (
                -s * exp(-q * t) * density * v / (2 * sqrt(t))
                - sign * r * k * exp(-r * t) * ndtr(sign * d2)
                + sign * q * s * exp(-q * t) * ndtr(sign * d1)
            )
            / 365
        ),
        "rho_1pct": float(sign * k * t * exp(-r * t) * ndtr(sign * d2) / 100),
    }


def implied_volatility(
    price: float, option: EuropeanOption, *, max_vol: float = 10.0, tolerance: float = 1e-10
) -> float:
    """Bracketed inversion. Expired/non-identifiable boundary prices are rejected."""
    if (
        not isfinite(price)
        or price < 0
        or not isfinite(max_vol)
        or max_vol <= 0
        or not isfinite(tolerance)
        or tolerance <= 0
    ):
        raise ValueError("Invalid price or solver settings")
    if option.maturity <= 0:
        raise ValueError("IV is undefined at expiry")
    lower, upper = price_bounds(option)
    if price < lower - tolerance or price >= upper:
        raise ValueError("Price violates European no-arbitrage bounds")
    if price <= lower + tolerance:
        raise ValueError("IV is not identifiable at the zero-volatility price boundary")

    def objective(vol):
        return black_scholes(replace(option, volatility=vol)) - price

    if objective(max_vol) < 0:
        raise ValueError("IV exceeds solver maximum volatility")
    return float(brentq(objective, 0.0, max_vol, xtol=1e-12))


def monte_carlo(
    option: EuropeanOption,
    *,
    paths: int = 100_000,
    seed: int = 42,
    jump_intensity: float = 0.0,
    jump_mean: float = -0.15,
    jump_volatility: float = 0.15,
) -> dict:
    """Exact terminal draws; no time-discretization bias for European vanilla.

    log jump ~ N(jump_mean, jump_volatility**2). Drift includes lambda*kappa
    compensation. Parameters describe a supplied Q scenario, not a calibrated
    market model. A zero-hit tail returns a warning, not evidence of zero risk.
    """
    if not isinstance(paths, int) or isinstance(paths, bool) or not 2 <= paths <= 5_000_000:
        raise ValueError("paths must be an integer in [2, 5_000_000]")
    if not all(isfinite(x) for x in (jump_intensity, jump_mean, jump_volatility)):
        raise ValueError("Jump parameters must be finite")
    if jump_intensity < 0 or jump_volatility < 0:
        raise ValueError("Jump intensity/volatility must be nonnegative")
    rng = np.random.default_rng(seed)
    t, v = option.maturity, option.volatility
    kappa = np.expm1(jump_mean + jump_volatility**2 / 2)
    n = rng.poisson(jump_intensity * t, paths)
    log_return = (
        (option.rate - option.dividend_yield - jump_intensity * kappa - v * v / 2) * t
        + v * sqrt(t) * rng.standard_normal(paths)
        + n * jump_mean
        + np.sqrt(n) * jump_volatility * rng.standard_normal(paths)
    )
    with np.errstate(over="raise", invalid="raise"):
        terminal = option.spot * np.exp(log_return)
    sign = 1 if option.option_type == "C" else -1
    payoffs = exp(-option.rate * t) * np.maximum(sign * (terminal - option.strike), 0)
    price = float(payoffs.mean())
    stderr = float(payoffs.std(ddof=1) / sqrt(paths))
    hits = int(np.count_nonzero(payoffs))
    warning = "rare_payoff_insufficient_sampling" if hits < 100 and t > 0 else None
    return {
        "model": "merton_jump_diffusion" if jump_intensity else "gbm",
        "measure": "Q",
        "price": price,
        "standard_error": stderr,
        "ci95": None if warning else [max(0.0, price - 1.96 * stderr), price + 1.96 * stderr],
        "paths": paths,
        "seed": seed,
        "positive_payoffs": hits,
        "warning": warning,
        "discounted_spot_mean": float(exp(-option.rate * t) * terminal.mean()),
        "discounted_spot_target": option.spot * exp(-option.dividend_yield * t),
        "jump_intensity": jump_intensity,
        "jump_mean": jump_mean,
        "jump_volatility": jump_volatility,
    }
