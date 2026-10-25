"""Deterministic Q Merton calibration with held-out strikes and identifiability diagnostics."""

import numpy as np
from scipy.optimize import least_squares
from scipy.special import ndtr
from scipy.stats import poisson


def merton_prices(strikes, maturities, rates, forwards, kinds, parameters):
    sigma, intensity, mean, jump_vol = parameters
    t = np.asarray(maturities, dtype=float)[:, None]
    k = np.asarray(strikes, dtype=float)[:, None]
    r = np.asarray(rates, dtype=float)[:, None]
    f = np.asarray(forwards, dtype=float)[:, None]
    if (
        not np.isfinite(parameters).all()
        or sigma <= 0
        or intensity < 0
        or jump_vol < 0
        or np.any(t <= 0)
        or np.any(k <= 0)
        or np.any(f <= 0)
    ):
        raise ValueError("Invalid Merton calibration inputs")
    count = int(poisson.ppf(1 - 1e-12, max(float(np.max(intensity * t)), 1e-15))) + 1
    n = np.arange(count + 1)[None, :]
    probability = poisson.pmf(n, intensity * t)
    variance = sigma * sigma * t + n * jump_vol * jump_vol
    conditional_forward = f * np.exp(
        -intensity * np.expm1(mean + 0.5 * jump_vol * jump_vol) * t
        + n * (mean + 0.5 * jump_vol * jump_vol)
    )
    d1 = (np.log(conditional_forward / k) + variance / 2) / np.sqrt(variance)
    d2 = d1 - np.sqrt(variance)
    sign = np.where(np.asarray(kinds) == "C", 1.0, -1.0)[:, None]
    values = sign * np.exp(-r * t) * (conditional_forward * ndtr(sign * d1) - k * ndtr(sign * d2))
    return np.sum(probability * values, axis=1)


def calibrate_merton(chain):
    rows = [
        r
        for r in chain["contracts"]
        if r["status"] == "eligible"
        and 10 <= r["maturity"] * 365 <= 365
        and (
            (r["option_type"] == "C" and r["strike"] >= r["forward"])
            or (r["option_type"] == "P" and r["strike"] < r["forward"])
        )
    ]
    rows = sorted(
        rows, key=lambda r: (r["expiry"], r["strike"], abs(r["multiplier"] - 10000), -r["volume"])
    )
    unique = {}
    for row in rows:
        unique.setdefault((row["expiry"], row["strike"]), row)
    rows = list(unique.values())
    if len(rows) < 12 or len({r["expiry"] for r in rows}) < 2:
        raise ValueError("Merton calibration requires at least 12 OTM quotes across two expiries")
    if len(rows) > 64:
        rows = [rows[i] for i in np.linspace(0, len(rows) - 1, 64).astype(int)]
    strikes = np.array([r["strike"] for r in rows])
    times = np.array([r["maturity"] for r in rows])
    rates = np.array([r["rate"] for r in rows])
    forwards = np.array([r["forward"] for r in rows])
    kinds = np.array([r["option_type"] for r in rows])
    prices = np.array([r["market_price"] for r in rows])
    vega = np.maximum([r["vega_1pct"] * 100 for r in rows], 0.005 * rows[0]["spot"])
    holdout = np.arange(len(rows)) % 4 == 1

    def residual(p):
        return ((merton_prices(strikes, times, rates, forwards, kinds, p) - prices) / vega)[
            ~holdout
        ]

    vol = float(np.median([r["iv"] for r in rows]))
    results = [
        least_squares(
            residual,
            [max(0.04, min(vol, 1.5)), lam, mu, delta],
            bounds=([0.005, 0, -0.8, 0.001], [2, 8, 0.3, 0.8]),
            loss="soft_l1",
            f_scale=0.01,
            max_nfev=160,
        )
        for lam, mu, delta in [(0.3, -0.15, 0.15), (1.0, -0.05, 0.25), (2.0, -0.2, 0.1)]
    ]
    fit = min(results, key=lambda r: float(np.sum(residual(r.x) ** 2)))
    modeled = merton_prices(strikes, times, rates, forwards, kinds, fit.x)
    error = (modeled - prices) / vega
    singular = np.linalg.svd(fit.jac, compute_uv=False)
    condition = float(singular[0] / max(singular[-1], 1e-15))
    train_rmse = float(np.sqrt(np.mean(error[~holdout] ** 2)))
    test_rmse = float(np.sqrt(np.mean(error[holdout] ** 2)))
    warnings = []
    if not fit.success:
        warnings.append("calibration_not_converged")
    if test_rmse > 0.05:
        warnings.append("calibration_holdout_error")
    if condition > 1e6:
        warnings.append("calibration_weak_identification")
    sensitivity = []
    reference = min(
        range(len(rows)),
        key=lambda i: (abs(times[i] - 30 / 365), abs(np.log(strikes[i] / forwards[i]))),
    )
    for index, name in enumerate(
        ("diffusion_volatility", "jump_intensity", "jump_mean", "jump_volatility")
    ):
        for scale in (0.75, 1.25):
            scenario = fit.x.copy()
            scenario[index] *= scale
            values = merton_prices(strikes, times, rates, forwards, kinds, scenario)
            sensitivity.append(
                {
                    "parameter": name,
                    "scale": scale,
                    "reference_contract": rows[reference]["order_book_id"],
                    "base_price": float(modeled[reference]),
                    "scenario_price": float(values[reference]),
                    "price_change": float(values[reference] - modeled[reference]),
                    "mean_absolute_price_change": float(np.mean(np.abs(values - modeled))),
                }
            )
    sigma, intensity, mean, jump_vol = map(float, fit.x)
    return {
        "model": "risk_neutral_merton",
        "sensitivity": sensitivity,
        "date": chain["date"],
        "underlying": chain["underlying"],
        "price_field": chain["price_field"],
        "parameters": {
            "diffusion_volatility": sigma,
            "jump_intensity": intensity,
            "jump_mean": mean,
            "jump_volatility": jump_vol,
        },
        "training_quotes": int((~holdout).sum()),
        "holdout_quotes": int(holdout.sum()),
        "train_vega_scaled_rmse": train_rmse,
        "holdout_vega_scaled_rmse": test_rmse,
        "jacobian_condition": condition,
        "warnings": warnings,
        "usable": not warnings,
        "quotes": [
            {
                "contract": r["order_book_id"],
                "name_zh": r.get("contract_name_zh", r["order_book_id"]),
                "name_en": r.get("contract_name_en", r["order_book_id"]),
                "expiry": r["expiry"],
                "strike": r["strike"],
                "market_price": r["market_price"],
                "model_price": float(value),
                "holdout": bool(h),
            }
            for r, value, h in zip(rows, modeled, holdout)
        ],
        "interpretation": "Q calibration to dated daily OTM prices. Every fourth strike held out, never fitted. Vega-scaled errors approximate IV errors; daily references and weak identification limit inference. Not a physical jump forecast.",
    }
