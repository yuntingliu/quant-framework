"""Robust SVI smile fits, variance grids and explicit static-arbitrage diagnostics."""

from math import exp, isfinite, log, sqrt

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from .pricing import EuropeanOption, black_scholes


def svi_total_variance(k, parameters):
    minimum, b, rho, m, sigma = parameters
    x = np.asarray(k) - m
    return minimum + b * (rho * x + np.sqrt(x * x + sigma * sigma) - sigma * sqrt(1 - rho * rho))


def _delta_iv(rows, target):
    values = sorted((float(r["delta"]), float(r["iv"])) for r in rows)
    if len(values) < 2 or not values[0][0] <= target <= values[-1][0]:
        return None
    return float(np.interp(target, [x[0] for x in values], [x[1] for x in values]))


def volatility_surface(chain, *, grid_size=31):
    """Fit observed OTM smiles, never silently extrapolate or repair arbitrage."""
    if not isinstance(grid_size, int) or not 5 <= grid_size <= 101:
        raise ValueError("Surface grid_size must be in [5, 101]")
    eligible = [r for r in chain["contracts"] if r["status"] == "eligible"]
    fits, warnings = [], []
    frame = pd.DataFrame(eligible)
    if frame.empty:
        return {
            "fits": [],
            "grid": [],
            "warnings": ["No eligible IV observations"],
            "usable_for_interpolation": False,
        }
    for expiry, group in frame.groupby("expiry"):
        rows = group.to_dict("records")
        chosen = [
            r
            for r in rows
            if (r["option_type"] == "C" and r["strike"] >= r["forward"])
            or (r["option_type"] == "P" and r["strike"] < r["forward"])
        ]
        # Standard and distribution-adjusted contracts can have nearly identical strikes.
        chosen.sort(key=lambda r: (r["strike"], abs(r["multiplier"] - 10000), -r["volume"]))
        observations = {}
        for row in chosen:
            observations.setdefault(row["strike"], row)
        chosen = list(observations.values())
        if len(chosen) < 5:
            warnings.append(f"{expiry}: insufficient OTM strikes for SVI")
            continue
        t = float(chosen[0]["maturity"])
        k = np.array([log(r["strike"] / r["forward"]) for r in chosen])
        w = np.array([r["iv"] ** 2 * t for r in chosen])
        if np.ptp(k) < 0.03:
            warnings.append(f"{expiry}: insufficient observed moneyness span")
            continue
        initial = [max(1e-6, float(np.median(w)) * 0.5), 0.05, -0.3, 0.0, 0.1]
        result = least_squares(
            lambda p: svi_total_variance(k, p) - w,
            initial,
            bounds=([1e-10, 1e-8, -0.99, -1.0, 0.001], [10.0, 5.0, 0.99, 1.0, 2.0]),
            loss="soft_l1",
            f_scale=max(float(np.median(w)) * 0.1, 1e-5),
            max_nfev=1500,
        )
        parameters = [float(v) for v in result.x]
        fit_iv = np.sqrt(svi_total_variance(k, parameters) / t)
        rmse = float(np.sqrt(np.mean((fit_iv - np.sqrt(w / t)) ** 2)))
        low, high = max(-0.35, float(k.min())), min(0.35, float(k.max()))
        grid = np.linspace(low, high, grid_size)
        variance = svi_total_variance(grid, parameters)
        forward, spot = float(chosen[0]["forward"]), float(chosen[0]["spot"])
        rate, q = float(chosen[0]["rate"]), float(chosen[0]["effective_dividend_yield"])
        strikes = forward * np.exp(grid)
        calls = np.array(
            [
                black_scholes(
                    EuropeanOption(spot, float(strike), t, rate, sqrt(float(v) / t), "C", q)
                )
                for strike, v in zip(strikes, variance)
            ]
        )
        slopes = np.diff(calls) / np.diff(strikes)
        butterfly = bool((np.diff(slopes) < -1e-6).any())
        monotonicity = bool((slopes > 1e-8).any() or (slopes < -exp(-rate * t) - 1e-6).any())
        call25 = _delta_iv([r for r in rows if r["option_type"] == "C"], 0.25)
        put25 = _delta_iv([r for r in rows if r["option_type"] == "P"], -0.25)
        atm = sqrt(float(svi_total_variance(0.0, parameters)) / t) if low <= 0 <= high else None
        fits.append(
            {
                "expiry": expiry,
                "maturity": t,
                "forward": forward,
                "rate": rate,
                "parameters": parameters,
                "observations": len(chosen),
                "converged": bool(result.success),
                "rmse_iv": rmse,
                "k_min": low,
                "k_max": high,
                "atm_iv": atm,
                "call_25delta_iv": call25,
                "put_25delta_iv": put25,
                "risk_reversal_25": None if call25 is None or put25 is None else call25 - put25,
                "butterfly_25": None
                if call25 is None or put25 is None or atm is None
                else (call25 + put25) / 2 - atm,
                "butterfly_arbitrage_flag": butterfly,
                "strike_arbitrage_flag": monotonicity,
                "observed": [
                    {
                        "k": float(x),
                        "iv": r["iv"],
                        "strike": r["strike"],
                        "contract": r["order_book_id"],
                    }
                    for x, r in zip(k, chosen)
                ],
                "smile": [
                    {"k": float(x), "strike": float(strike), "iv": sqrt(float(v) / t)}
                    for x, strike, v in zip(grid, strikes, variance)
                ],
            }
        )
        if not result.success or butterfly or monotonicity or rmse > 0.05:
            warnings.append(f"{expiry}: fit quality or strike-arbitrage diagnostic requires review")
    fits.sort(key=lambda item: item["maturity"])
    grid_rows, calendar_violations = [], 0
    if fits:
        lo, hi = max(v["k_min"] for v in fits), min(v["k_max"] for v in fits)
        if lo < hi:
            common = np.linspace(lo, hi, grid_size)
            previous = None
            for fit in fits:
                values = svi_total_variance(common, fit["parameters"])
                if previous is not None:
                    calendar_violations += int(np.count_nonzero(values < previous - 1e-7))
                previous = values
                grid_rows.append(
                    {
                        "expiry": fit["expiry"],
                        "maturity": fit["maturity"],
                        "values": [
                            {
                                "k": float(k),
                                "iv": sqrt(float(v) / fit["maturity"]),
                                "total_variance": float(v),
                            }
                            for k, v in zip(common, values)
                        ],
                    }
                )
    if calendar_violations:
        warnings.append(
            "Fitted total variance decreases across maturities at shared forward moneyness"
        )
    return {
        "model": "SVI_positive_minimum_total_variance",
        "fits": fits,
        "grid": grid_rows,
        "calendar_violations": calendar_violations,
        "warnings": warnings,
        "usable_for_interpolation": bool(fits) and not warnings,
        "interpretation": "Observed-domain fit with explicit diagnostics; not a certified arbitrage-free surface. No wing extrapolation.",
    }


def surface_iv(surface, maturity, log_moneyness):
    if not surface.get("usable_for_interpolation"):
        raise ValueError("Surface diagnostics do not permit automatic interpolation")
    fits = surface["fits"]
    if (
        not isfinite(maturity)
        or not isfinite(log_moneyness)
        or not fits[0]["maturity"] <= maturity <= fits[-1]["maturity"]
    ):
        raise ValueError("Surface query outside observed maturities")
    if any(not f["k_min"] <= log_moneyness <= f["k_max"] for f in fits):
        raise ValueError("Surface query outside shared observed moneyness")
    values = [float(svi_total_variance(log_moneyness, f["parameters"])) for f in fits]
    variance = float(np.interp(maturity, [f["maturity"] for f in fits], values))
    return sqrt(variance / maturity)
