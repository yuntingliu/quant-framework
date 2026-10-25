"""Arbitrage-constrained power-law SSVI with an observed-domain price boundary."""

import numpy as np
from scipy.optimize import least_squares


def ssvi_variance(k, theta, rho, eta):
    phi = eta / np.sqrt(theta * (1 + theta))
    x = phi * np.asarray(k)
    return theta / 2 * (1 + rho * x + np.sqrt((x + rho) ** 2 + 1 - rho * rho))


def ssvi_surface(surface):
    fits = surface.get("fits", [])
    if not fits:
        return {
            "fits": [],
            "grid": [],
            "warnings": ["No eligible IV observations"],
            "usable_for_interpolation": False,
        }
    maturities = np.array([f["maturity"] for f in fits])
    initial_theta = np.maximum.accumulate(
        [np.median([o["iv"] ** 2 * f["maturity"] for o in f["observed"]]) for f in fits]
    )
    increments = np.maximum(np.diff(np.r_[0, initial_theta]), 1e-7)

    def decode(p):
        rho, fraction = p[:2]
        # eta²(1+|rho|)<4 and eta(1+|rho|)<4 hold for every theta>0.
        eta = fraction * 2 / np.sqrt(1 + abs(rho))
        return rho, eta, np.cumsum(p[2:])

    def residual(p):
        rho, eta, theta = decode(p)
        return np.concatenate(
            [
                (
                    np.sqrt(
                        ssvi_variance([o["k"] for o in f["observed"]], theta[i], rho, eta)
                        / f["maturity"]
                    )
                    - [o["iv"] for o in f["observed"]]
                )
                for i, f in enumerate(fits)
            ]
        )

    fit = least_squares(
        residual,
        np.r_[-0.3, 0.3, increments],
        bounds=(
            np.r_[-0.99, 0.001, np.repeat(1e-9, len(fits))],
            np.r_[0.99, 0.999, np.repeat(5.0, len(fits))],
        ),
        loss="soft_l1",
        f_scale=0.01,
        max_nfev=1000,
    )
    rho, eta, theta = decode(fit.x)
    lo, hi = max(f["k_min"] for f in fits), min(f["k_max"] for f in fits)
    warnings = []
    if not fit.success:
        warnings.append("SSVI calibration did not converge")
    if lo >= hi:
        warnings.append("No common observed moneyness interval")
    output, grid = [], []
    for i, f in enumerate(fits):
        k = np.linspace(f["k_min"], f["k_max"], 101)
        iv = np.sqrt(ssvi_variance(k, theta[i], rho, eta) / f["maturity"])
        error = np.sqrt(
            np.mean(
                (
                    np.sqrt(
                        ssvi_variance([o["k"] for o in f["observed"]], theta[i], rho, eta)
                        / f["maturity"]
                    )
                    - [o["iv"] for o in f["observed"]]
                )
                ** 2
            )
        )
        if error > 0.05:
            warnings.append(
                f"{f['expiry']}: fit quality or strike-arbitrage diagnostic requires review"
            )
        output.append(
            {
                **f,
                "theta": float(theta[i]),
                "rmse_iv": float(error),
                "calibration_usable": bool(error <= 0.05),
                "atm_iv": float(np.sqrt(theta[i] / f["maturity"]))
                if f["k_min"] <= 0 <= f["k_max"]
                else None,
                "smile": [
                    {"k": float(x), "strike": float(f["forward"] * np.exp(x)), "iv": float(v)}
                    for x, v in zip(k, iv)
                ],
            }
        )
        if lo < hi:
            common = np.linspace(lo, hi, 31)
            w = ssvi_variance(common, theta[i], rho, eta)
            grid.append(
                {
                    "expiry": f["expiry"],
                    "maturity": f["maturity"],
                    "values": [
                        {
                            "k": float(x),
                            "iv": float(np.sqrt(v / f["maturity"])),
                            "total_variance": float(v),
                        }
                        for x, v in zip(common, w)
                    ],
                }
            )
    return {
        "model": "power_law_ssvi_gamma_half",
        "fits": output,
        "grid": grid,
        "warnings": warnings,
        "parameters": {
            "rho": float(rho),
            "eta": float(eta),
            "theta": theta.tolist(),
            "maturities": maturities.tolist(),
        },
        "constraints": {
            "calendar_theta_nondecreasing": True,
            "max_theta_phi_one_plus_abs_rho": float(
                np.max(theta * eta / np.sqrt(theta * (1 + theta))) * (1 + abs(rho))
            ),
            "max_theta_phi_squared_one_plus_abs_rho": float(
                np.max(eta**2 / (1 + theta)) * (1 + abs(rho))
            ),
            "sufficient_static_variance_constraints": True,
        },
        "usable_for_interpolation": bool(
            fit.success and lo < hi and any(f["calibration_usable"] for f in output)
        ),
        "partial_coverage": any(not f["calibration_usable"] for f in output),
        "calendar_violations": 0,
        "interpretation": "Constrained power-law SSVI, gamma=1/2; monotone theta and sufficient butterfly bounds. Observed domain only; proportional-carry approximation. Market residuals remain visible.",
    }


def ssvi_iv(surface, maturity, log_moneyness):
    if not surface.get("usable_for_interpolation"):
        raise ValueError("Surface diagnostics do not permit automatic interpolation")
    f = surface["fits"]
    p = surface["parameters"]
    if (
        not np.isfinite([maturity, log_moneyness]).all()
        or not f[0]["maturity"] <= maturity <= f[-1]["maturity"]
        or any(not row["k_min"] <= log_moneyness <= row["k_max"] for row in f)
    ):
        raise ValueError("Surface query outside shared observed moneyness or maturities")
    index = int(np.searchsorted(p["maturities"], maturity))
    brackets = (
        [index]
        if index < len(f) and abs(f[index]["maturity"] - maturity) < 1e-10
        else [max(0, index - 1), min(index, len(f) - 1)]
    )
    if any(not f[i].get("calibration_usable", True) for i in brackets):
        raise ValueError("Surface calibration is unreliable at the queried maturity")
    theta = float(np.interp(maturity, p["maturities"], p["theta"]))
    return float(np.sqrt(ssvi_variance(log_moneyness, theta, p["rho"], p["eta"]) / maturity))
