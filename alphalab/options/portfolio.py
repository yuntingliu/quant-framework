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
    sampling="standard",
    importance_tilt=-2.0,
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
    if (
        sampling not in {"standard", "importance"}
        or not isfinite(importance_tilt)
        or abs(importance_tilt) > 6
    ):
        raise ValueError("Invalid sampling method or importance tilt")
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
    likelihood = np.ones(paths)
    log_ratio = np.zeros(paths)
    mixture = rng.integers(0, 2, paths) if sampling == "importance" else np.zeros(paths)
    leg_samples = {}
    previous_t = 0.0
    compensator = exp(jump_mean + jump_volatility**2 / 2) - 1
    for t in times:
        dt = t - previous_t
        if dt > 0:
            common_z = rng.standard_normal(paths)
            active_count = sum(t <= last_time[name] + 1e-10 for name in names)
            tilt = importance_tilt if sampling == "importance" else 0.0
            denominator = 1 + (active_count - 1) * correlation
            common_shift = tilt * sqrt(correlation) * active_count / denominator * sqrt(dt)
            independent_shift = tilt * sqrt(1 - correlation) / denominator * sqrt(dt)
            common_z = common_z + common_shift * mixture
            log_ratio += common_shift * common_z - 0.5 * common_shift**2
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
                independent_z = rng.standard_normal(paths)
                independent_z = independent_z + independent_shift * mixture
                log_ratio += independent_shift * independent_z - 0.5 * independent_shift**2
                z = sqrt(correlation) * common_z + sqrt(1 - correlation) * independent_z
                drift = (
                    log(forward / previous_forward[name])
                    - (jump_intensity * compensator + vol * vol / 2) * dt
                )
                with np.errstate(over="raise", invalid="raise"):
                    prices[name] *= np.exp(drift + vol * sqrt(dt) * z + jump)
                previous_forward[name] = forward
            # Defensive 50/50 mixture retains the ordinary region as well as
            # rare tails. The exact likelihood is bounded by two.
            likelihood = np.exp(-np.logaddexp(np.log(0.5), np.log(0.5) + log_ratio))
        for index, leg in enumerate(legs):
            remaining = max(0.0, leg["maturity"] - days_forward / 365)
            if abs(remaining - t) < 1e-10:
                sign = 1 if leg["option_type"] == "C" else -1
                payoff = np.maximum(sign * (prices[leg["underlying"]] - leg["strike"]), 0.0)
                contribution = payoff * exp(-leg["rate"] * t) * leg["quantity"] * leg["multiplier"]
                pv += contribution
                # Lock each leg's likelihood at its own expiry. Later asset
                # increments must not add noise to a settled cash flow.
                leg_samples[index] = (
                    contribution * likelihood.copy(),
                    int(np.count_nonzero(payoff)),
                )
        previous_t = t
    diagnostics, estimate = [], np.zeros(paths)
    for index, leg in enumerate(legs):
        contribution, hits = leg_samples[index]
        estimate += contribution
        mass = np.abs(contribution)
        ess = float(mass.sum() ** 2 / (mass @ mass)) if (mass @ mass) > 0 else 0.0
        insufficient = (hits < 100 or (sampling == "importance" and ess < 30)) and leg[
            "maturity"
        ] > days_forward / 365
        diagnostics.append(
            {
                "contract": leg.get("order_book_id", str(index)),
                "positive_payoffs": hits,
                "effective_payoffs": ess,
                "value": float(contribution.mean()),
                "standard_error": float(contribution.std(ddof=1) / sqrt(paths)),
                "warning": "rare_payoff_insufficient_sampling" if insufficient else None,
            }
        )
    mean, stderr = float(estimate.mean()), float(estimate.std(ddof=1) / sqrt(paths))
    warning = (
        "rare_payoff_insufficient_sampling" if any(d["warning"] for d in diagnostics) else None
    )
    order = np.argsort(pv)
    cdf = np.cumsum(likelihood[order])
    cdf /= cdf[-1]
    quantiles = {str(p): float(np.interp(p, cdf, pv[order])) for p in (0.01, 0.05, 0.5, 0.95, 0.99)}
    return {
        "model": "joint_systemic_merton" if jump_intensity else "joint_gbm",
        "measure": "Q",
        "value": mean,
        "standard_error": stderr,
        "ci95": [mean - 1.96 * stderr, mean + 1.96 * stderr] if warning is None else None,
        "paths": paths,
        "seed": seed,
        "correlation": correlation,
        "jump_intensity": jump_intensity,
        "jump_mean": jump_mean,
        "jump_volatility": jump_volatility,
        "warning": warning,
        "sampling": sampling,
        "importance_tilt": importance_tilt if sampling == "importance" else 0.0,
        "effective_paths": float(likelihood.sum() ** 2 / (likelihood @ likelihood)),
        "likelihood_mean": float(likelihood.mean()),
        "leg_diagnostics": diagnostics,
        "payoff_quantiles": quantiles,
        "spot_shock": spot_shock,
        "volatility_shift": volatility_shift,
        "interpretation": "Joint discounted Q terminal payoffs, not physical-measure VaR. Flat ATM vol per ETF; forward curves retained; systemic jumps and supplied correlation are scenario assumptions.",
    }
