"""Dated option predictors with explicit maturity and finite-wing boundaries."""

from math import exp, log, sqrt

import numpy as np
import pandas as pd
from scipy.integrate import trapezoid

from .literature import FEATURES

FORMULA_VERSION = "option-research-v3-forward-atm"


def _interp(rows, column, target):
    points = sorted((float(r[column]), float(r["iv"])) for r in rows)
    if len(points) < 2 or not points[0][0] <= target <= points[-1][0]:
        return None
    unique = {}
    for x, y in points:
        unique.setdefault(x, []).append(y)
    return float(np.interp(target, list(unique), [np.median(v) for v in unique.values()]))


def _at_maturity(nodes, days, key, *, variance=False):
    points = sorted((n["maturity"], n[key]) for n in nodes if n.get(key) is not None)
    t = days / 365
    if not points or not points[0][0] - 1e-10 <= t <= points[-1][0] + 1e-10:
        return None
    x = [p[0] for p in points]
    values = [v * s if variance else v for s, v in points]
    value = float(np.interp(t, x, values))
    return value / t if variance else value


def strip_moments(rows):
    """Truncated strike-strip variance and moments of log(S_T/F).

    E[f(S_T)] = f(F)+exp(rT)*integral(f''(K)*OTM(K),dK).
    All powers 1..4 are replicated on the same observed interval. Omitted
    tails are deliberately not filled with an SVI or lognormal extrapolation.
    """
    if not rows:
        return {}
    t, forward, rate = (rows[0][k] for k in ("maturity", "forward", "rate"))
    by_strike = {}
    for row in sorted(rows, key=lambda r: (-r["volume"], abs(r["multiplier"] - 10000))):
        by_strike.setdefault(row["strike"], {}).setdefault(row["option_type"], row["market_price"])
    strikes = sorted(by_strike)
    below = [k for k in strikes if k <= forward and "P" in by_strike[k]]
    above = [k for k in strikes if k >= forward and "C" in by_strike[k]]
    if len(below) < 3 or len(above) < 3 or below[0] / forward > 0.95 or above[-1] / forward < 1.05:
        return {}
    # Stitch at the forward using interpolation inside the quoted domain.
    left, right = below[-1], above[0]
    center = float(np.interp(forward, [left, right], [by_strike[left]["P"], by_strike[right]["C"]]))
    k = np.array(
        below + ([forward] if forward not in below else []) + [v for v in above if v > forward]
    )
    q = np.array(
        [
            by_strike[v]["P"] if v < forward else center if v == forward else by_strike[v]["C"]
            for v in k
        ]
    )
    # Integrate the piecewise-linear observed strip on a dense log-strike grid.
    grid = np.exp(np.linspace(log(k[0]), log(k[-1]), 1001))
    prices = np.interp(grid, k, q)
    z = np.log(grid / forward)
    raw = [-float(trapezoid(prices / grid**2, grid)) * exp(rate * t)]
    for n in (2, 3, 4):
        second = n * ((n - 1) * z ** (n - 2) - z ** (n - 1)) / grid**2
        raw.append(float(trapezoid(second * prices, grid)) * exp(rate * t))
    mean, m2, m3, m4 = raw
    variance = m2 - mean**2
    skew = (m3 - 3 * mean * m2 + 2 * mean**3) / variance**1.5 if variance > 1e-12 else None
    kurt = (
        (m4 - 4 * mean * m3 + 6 * mean**2 * m2 - 3 * mean**4) / variance**2
        if variance > 1e-12
        else None
    )
    k0 = below[-1]
    discrete_k = np.array(sorted(set(below + above)))
    discrete_q = np.array(
        [
            (by_strike[v].get("C", 0) + by_strike[v].get("P", 0)) / 2
            if v == k0 and len(by_strike[v]) == 2
            else by_strike[v]["P"]
            if v <= k0
            else by_strike[v]["C"]
            for v in discrete_k
        ]
    )
    spacing = np.empty(len(discrete_k))
    spacing[1:-1] = (discrete_k[2:] - discrete_k[:-2]) / 2
    spacing[0], spacing[-1] = discrete_k[1] - discrete_k[0], discrete_k[-1] - discrete_k[-2]
    mf = (
        2 * exp(rate * t) / t * float(np.sum(spacing * discrete_q / discrete_k**2))
        - (forward / k0 - 1) ** 2 / t
    )
    return {
        "mf_variance": mf if mf > 0 else None,
        "raw1": mean,
        "raw2": m2,
        "raw3": m3,
        "raw4": m4,
        "q_skew": skew,
        "q_kurt": kurt,
        "k_min_over_forward": float(k[0] / forward),
        "k_max_over_forward": float(k[-1] / forward),
        "strikes": len(k),
        "truncated": True,
    }


def option_features(chain, adjusted_history, intraday_history=None):
    """Return one point-in-time predictor row and per-expiry audit diagnostics."""
    day = pd.Timestamp(chain["date"])
    result = {field: None for field in FEATURES}
    rows = [r for r in chain["contracts"] if r["status"] == "eligible"]
    nodes, diagnostics = [], []
    for expiry in sorted({r["expiry"] for r in rows}):
        group = [r for r in rows if r["expiry"] == expiry]
        copied = [{**r, "k": log(r["strike"] / r["forward"])} for r in group]
        atm = _interp(copied, "k", 0.0)
        call = _interp([r for r in group if r["option_type"] == "C"], "delta", 0.25)
        put = _interp([r for r in group if r["option_type"] == "P"], "delta", -0.25)
        moments = strip_moments(group)
        node = {
            "maturity": group[0]["maturity"],
            "atm_variance": None if atm is None else atm**2,
            "call25_variance": None if call is None else call**2,
            "put25_variance": None if put is None else put**2,
            **moments,
        }
        nodes.append(node)
        diagnostics.append({"expiry": expiry, **node})
    for days in (30, 60, 90):
        var = _at_maturity(nodes, days, "atm_variance", variance=True)
        result[f"option_atm_iv_{days}"] = sqrt(var) if var is not None and var >= 0 else None
    atm30 = result["option_atm_iv_30"]
    c, p = (
        _at_maturity(nodes, 30, key, variance=True) for key in ("call25_variance", "put25_variance")
    )
    if c is not None and p is not None:
        result["option_rr25_30"] = sqrt(c) - sqrt(p)
        if atm30 is not None:
            result["option_bf25_30"] = (sqrt(c) + sqrt(p)) / 2 - atm30
    eligible = [r for r in rows if 10 - 1e-8 <= r["maturity"] * 365 <= 60 + 1e-8]
    puts = [
        r for r in eligible if r["option_type"] == "P" and 0.80 < r["strike"] / r["spot"] < 0.95
    ]
    calls = [
        r for r in eligible if r["option_type"] == "C" and 0.95 <= r["strike"] / r["spot"] <= 1.05
    ]
    if puts and calls:
        # Across eligible maturities follow moneyness selection; DTE breaks ties.
        put = min(
            puts,
            key=lambda r: (
                abs(r["strike"] / r["spot"] - 0.95),
                abs(r["maturity"] * 365 - 30),
                -r["volume"],
            ),
        )
        call = min(
            calls,
            key=lambda r: (
                abs(r["strike"] / r["spot"] - 1),
                abs(r["maturity"] * 365 - 30),
                -r["volume"],
            ),
        )
        result["option_smirk"] = put["iv"] - call["iv"]
        result["option_smirk_volume"] = float(
            np.average([r["iv"] for r in puts], weights=[r["volume"] for r in puts])
            - np.average([r["iv"] for r in calls], weights=[r["volume"] for r in calls])
        )
    if atm30 is not None and result["option_atm_iv_60"] is not None:
        result["option_term_slope"] = result["option_atm_iv_60"] - atm30
    result["option_mf_variance_30"] = _at_maturity(nodes, 30, "mf_variance", variance=True)
    raw = [_at_maturity(nodes, 30, "raw" + str(i)) for i in range(1, 5)]
    if all(v is not None for v in raw):
        mean, m2, m3, m4 = raw
        var = m2 - mean**2
        if var > 1e-12:
            result["option_q_skew_30"] = (m3 - 3 * mean * m2 + 2 * mean**3) / var**1.5
            result["option_q_kurt_30"] = (
                m4 - 4 * mean * m3 + 6 * mean**2 * m2 - 3 * mean**4
            ) / var**2
    history = adjusted_history.loc[pd.to_datetime(adjusted_history.date).le(day)].sort_values(
        "date"
    )
    close = history.adjusted_close.astype(float)
    returns = np.log(close / close.shift()).dropna()
    if len(returns) >= 20:
        result["option_rv20"] = float(returns.tail(20).std(ddof=1) * sqrt(252))
        result["option_momentum20"] = float(close.iloc[-1] / close.iloc[-21] - 1)
        if result["option_mf_variance_30"] is not None:
            result["option_vrp_daily_30"] = (
                result["option_mf_variance_30"] - result["option_rv20"] ** 2
            )
    if intraday_history is not None and not intraday_history.empty:
        daily = history[["date"]].merge(
            intraday_history[["date", "realized_variance"]],
            on="date",
            how="left",
            validate="one_to_one",
        )
        if len(daily) and pd.notna(daily.realized_variance.iloc[-1]):
            result["option_rv5m_1"] = float(daily.realized_variance.iloc[-1])
        if len(daily) >= 20 and daily.realized_variance.tail(20).notna().all():
            rv = float(np.sqrt(daily.realized_variance.tail(20).mean() * 252))
            result["option_rv5m_20"] = rv
            if result["option_mf_variance_30"] is not None:
                result["option_vrp_hf_30"] = result["option_mf_variance_30"] - rv**2
    for key, metric in (("oi_pcr", "open_interest"), ("volume_pcr", "volume")):
        calls = sum(r[metric] for r in chain["contracts"] if r["option_type"] == "C")
        puts = sum(r[metric] for r in chain["contracts"] if r["option_type"] == "P")
        result["option_" + key] = puts / calls if calls else None
    return {
        "date": day,
        "symbol": chain["underlying"],
        **result,
        "eligible": chain["eligible"],
        "formula_version": FORMULA_VERSION,
        "diagnostics": diagnostics,
        "availability": "session_close",
    }
