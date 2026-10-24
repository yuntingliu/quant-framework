"""Signed option-basket Greeks, full revaluation and coherent joint Q simulation."""

from math import exp, isfinite, log, sqrt

import numpy as np

from .pricing import EuropeanOption, black_scholes, greeks


def _validate_legs(legs, days_forward):
    if not 1 <= len(legs) <= 20 or not isfinite(days_forward) or days_forward < 0:
        raise ValueError("Provide 1-20 legs and a finite nonnegative horizon")
    for leg in legs:
        if (
            leg.get("status") != "eligible"
            or not isfinite(leg["quantity"])
            or leg["quantity"] == 0
            or abs(leg["quantity"]) > 100000
            or leg["multiplier"] <= 0
            or days_forward > leg["maturity"] * 365 + 1e-9
        ):
            raise ValueError(
                "Portfolio contains an ineligible leg, invalid size or expired horizon"
            )


def basket_stress(
    legs,
    *,
    spot_shocks=(-0.4, -0.3, -0.2, -0.1, -0.05, 0.0, 0.05, 0.1, 0.2, 0.3, 0.4),
    volatility_shifts=(0.0, 0.1, 0.3),
    days_forward=0,
):
    _validate_legs(legs, days_forward)
    if not spot_shocks or not volatility_shifts or len(spot_shocks) * len(volatility_shifts) > 100:
        raise ValueError("Provide 1-100 stress cells")
    totals = {name: 0.0 for name in ("delta", "gamma", "vega_1pct", "theta_day", "rho_1pct")}
    per_underlying, positions = {}, []
    market_value = 0.0
    cash_delta, cash_gamma_1pct = 0.0, 0.0
    for leg in legs:
        option = EuropeanOption(
            leg["spot"],
            leg["strike"],
            leg["maturity"],
            leg["rate"],
            leg["iv"],
            leg["option_type"],
            leg["effective_dividend_yield"],
        )
        scale = leg["quantity"] * leg["multiplier"]
        values = greeks(option)
        asset = per_underlying.setdefault(leg["underlying"], {name: 0.0 for name in totals})
        for name, value in values.items():
            totals[name] += value * scale
            asset[name] += value * scale
        cash_delta += values["delta"] * leg["spot"] * scale
        cash_gamma_1pct += 0.5 * values["gamma"] * (leg["spot"] * 0.01) ** 2 * scale
        value = leg["market_price"] * scale
        market_value += value
        positions.append(
            {
                **leg,
                "market_value": value,
                "bs_value": black_scholes(option) * scale,
                "position_greeks": {name: v * scale for name, v in values.items()},
            }
        )
    cells = []
    for shock in spot_shocks:
        for shift in volatility_shifts:
            if not isfinite(shock) or not isfinite(shift) or shock <= -1:
                raise ValueError("Invalid finite stress shock")
            value = 0.0
            for leg in legs:
                option = EuropeanOption(
                    leg["spot"] * (1 + shock),
                    leg["strike"],
                    max(0.0, leg["maturity"] - days_forward / 365),
                    leg["rate"],
                    leg["iv"] + shift,
                    leg["option_type"],
                    leg["effective_dividend_yield"],
                )
                value += black_scholes(option) * leg["multiplier"] * leg["quantity"]
            cells.append(
                {
                    "spot_shock": shock,
                    "volatility_shift": shift,
                    "value": value,
                    "pnl": value - market_value,
                }
            )
    return {
        "market_value": market_value,
        "greeks": {**totals, "cash_delta": cash_delta, "cash_gamma_1pct": cash_gamma_1pct},
        "greeks_by_underlying": per_underlying,
        "positions": positions,
        "scenarios": cells,
        "days_forward": days_forward,
        "worst_pnl": min(c["pnl"] for c in cells),
        "best_pnl": max(c["pnl"] for c in cells),
        "units": "CNY; delta/gamma per underlying price unit, vega/rho per percentage point, theta per calendar day",
        "assumptions": [
            "Parallel spot/volatility shocks; sticky-strike IV",
            "Zero rates and effective carry held fixed per leg",
            "P&L excludes financing, dividends received, transaction costs and margin calls",
        ],
    }


def joint_monte_carlo(
    legs,
    assets,
    *,
    paths=100000,
    seed=42,
    correlation=0.8,
    jump_intensity=1.0,
    jump_mean=-0.15,
    jump_volatility=0.15,
    spot_shock=0.0,
    volatility_shift=0.0,
    days_forward=0.0,
):
    """One shared asset path per ETF: calls/puts and maturities cannot drift apart.

    A flat ATM volatility per ETF and observed forward term structure define the
    Q model. Brownian assets have equicorrelation; jumps are systemic. Result is
    discounted terminal-payoff evidence, not real-world portfolio VaR/ES.
    """
    _validate_legs(legs, days_forward)
    if not isinstance(paths, int) or not 1000 <= paths <= 250000:
        raise ValueError("Portfolio MC paths must be in [1000, 250000]")
    if (
        not all(
            isfinite(x)
            for x in (
                correlation,
                jump_intensity,
                jump_mean,
                jump_volatility,
                spot_shock,
                volatility_shift,
            )
        )
        or not 0 <= correlation <= 1
        or not 0 <= jump_intensity <= 100
        or not 0 <= jump_volatility <= 2
        or not -5 <= jump_mean <= 2
        or spot_shock <= -1
    ):
        raise ValueError("Invalid joint MC scenario parameters")
    names = sorted({leg["underlying"] for leg in legs})
    last_time = {
        name: max(
            max(0.0, leg["maturity"] - days_forward / 365)
            for leg in legs
            if leg["underlying"] == name
        )
        for name in names
    }
    if not set(names).issubset(assets):
        raise ValueError("Missing shared underlying model")
    times = sorted({max(0.0, leg["maturity"] - days_forward / 365) for leg in legs})
    rng = np.random.default_rng(seed)
    prices = {
        name: np.full(paths, assets[name]["spot"] * (1 + spot_shock), dtype=float) for name in names
    }
    previous_forward = {name: assets[name]["spot"] * (1 + spot_shock) for name in names}
    curves = {}
    for name in names:
        asset = assets[name]
        if asset["volatility"] + volatility_shift < 0:
            raise ValueError("Negative stressed underlying volatility")
        nodes = [(0.0, asset["spot"] * (1 + spot_shock))]
        for node in asset["forwards"]:
            remaining = node["maturity"] - days_forward / 365
            if remaining > 0:
                nodes.append(
                    (
                        remaining,
                        asset["spot"]
                        * (1 + spot_shock)
                        * exp(log(node["forward"] / asset["spot"]) * remaining / node["maturity"]),
                    )
                )
        nodes.sort()
        if last_time[name] > nodes[-1][0] + 1e-10:
            raise ValueError("MC maturity exceeds the observed forward domain")
        curves[name] = nodes
    pv = np.zeros(paths)
    previous_t = 0.0
    compensator = exp(jump_mean + jump_volatility**2 / 2) - 1
    for t in times:
        dt = t - previous_t
        if dt > 0:
            common_z = rng.standard_normal(paths)
            counts = rng.poisson(jump_intensity * dt, paths)
            jump = counts * jump_mean + np.sqrt(counts) * jump_volatility * rng.standard_normal(
                paths
            )
            for name in names:
                if t > last_time[name] + 1e-10:
                    continue
                curve = curves[name]
                forward = float(
                    np.exp(np.interp(t, [n[0] for n in curve], np.log([n[1] for n in curve])))
                )
                vol = assets[name]["volatility"] + volatility_shift
                z = sqrt(correlation) * common_z + sqrt(1 - correlation) * rng.standard_normal(
                    paths
                )
                drift = (
                    log(forward / previous_forward[name])
                    - (jump_intensity * compensator + vol * vol / 2) * dt
                )
                with np.errstate(over="raise", invalid="raise"):
                    prices[name] *= np.exp(drift + vol * sqrt(dt) * z + jump)
                previous_forward[name] = forward
        for leg in legs:
            remaining = max(0.0, leg["maturity"] - days_forward / 365)
            if abs(remaining - t) < 1e-10:
                sign = 1 if leg["option_type"] == "C" else -1
                payoff = np.maximum(sign * (prices[leg["underlying"]] - leg["strike"]), 0.0)
                pv += payoff * exp(-leg["rate"] * t) * leg["quantity"] * leg["multiplier"]
        previous_t = t
    mean, stderr = float(pv.mean()), float(pv.std(ddof=1) / sqrt(paths))
    hits = int(np.count_nonzero(pv))
    return {
        "model": "joint_systemic_merton" if jump_intensity else "joint_gbm",
        "measure": "Q",
        "value": mean,
        "standard_error": stderr,
        "ci95": [mean - 1.96 * stderr, mean + 1.96 * stderr] if hits >= 100 else None,
        "paths": paths,
        "seed": seed,
        "correlation": correlation,
        "jump_intensity": jump_intensity,
        "jump_mean": jump_mean,
        "jump_volatility": jump_volatility,
        "warning": "rare_payoff_insufficient_sampling" if hits < 100 else None,
        "payoff_quantiles": {
            str(p): float(np.quantile(pv, p)) for p in (0.01, 0.05, 0.5, 0.95, 0.99)
        },
        "spot_shock": spot_shock,
        "volatility_shift": volatility_shift,
        "interpretation": "Joint discounted Q terminal payoffs, not physical-measure VaR. Flat ATM vol per ETF; forward curves retained; systemic jumps and supplied correlation are scenario assumptions.",
    }
