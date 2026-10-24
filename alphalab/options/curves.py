"""Dated sovereign-yield proxies and bounded discount-factor interpolation."""

import re
from dataclasses import dataclass
from math import exp, isfinite, log1p

import numpy as np
import pandas as pd


def tenor_years(tenor: str) -> float:
    if tenor == "0S":
        return 1 / 365
    match = re.fullmatch(r"([1-9][0-9]*)([DMY])", tenor)
    if not match:
        raise ValueError(f"Unsupported curve tenor: {tenor}")
    return int(match[1]) / {"D": 365, "M": 12, "Y": 1}[match[2]]


def normalize_yield_curve(raw) -> pd.DataFrame:
    """RQ yields are annual decimals. Never guess percent units from magnitude."""
    frame = pd.DataFrame(raw).copy()
    if frame.empty:
        raise ValueError("Empty sovereign yield curve")
    if "date" not in frame:
        frame = frame.reset_index().rename(columns={frame.index.name or "index": "date"})
    if "date" not in frame:
        raise ValueError("Yield curve requires dated rows")
    records = []
    for item in frame.to_dict("records"):
        day = pd.Timestamp(item["date"]).normalize()
        for name, value in item.items():
            if name == "date" or pd.isna(value):
                continue
            try:
                maturity = tenor_years(str(name))
            except ValueError:
                continue
            annual = float(value)
            if not isfinite(annual) or not -0.2 < annual < 0.5:
                raise ValueError("Yield curve must contain annual decimal yields, not percentages")
            continuous = log1p(annual)
            records.append(
                {
                    "date": day,
                    "tenor": str(name),
                    "maturity": maturity,
                    "annual_yield": annual,
                    "continuous_rate": continuous,
                    "discount_factor": exp(-continuous * maturity),
                    "source": "rqdata_china_government_yield",
                    "convention": "annual_yield_as_zero_rate_proxy",
                }
            )
    result = pd.DataFrame(records)
    if result.empty or result.date.isna().any() or result.duplicated(["date", "tenor"]).any():
        raise ValueError("Invalid or duplicate dated yield curve")
    return result.sort_values(["date", "maturity"]).reset_index(drop=True)


@dataclass(frozen=True)
class DiscountCurve:
    date: str
    maturities: tuple[float, ...]
    rates: tuple[float, ...]
    stale_days: int = 0

    @classmethod
    def as_of(cls, frame, day, *, max_stale_days=7):
        required = {"date", "maturity", "continuous_rate"}
        if not required.issubset(frame) or frame.empty:
            raise ValueError("A local dated sovereign yield curve is required")
        dates = pd.to_datetime(frame.date, errors="coerce").dt.normalize()
        target = pd.Timestamp(day).normalize()
        available = dates.loc[dates.le(target)]
        if available.empty:
            raise ValueError("No yield curve is available at or before the analysis date")
        selected = available.max()
        stale = (target - selected).days
        if stale > max_stale_days:
            raise ValueError("Yield curve is stale; synchronize rates before analysis")
        values = frame.loc[dates.eq(selected)].sort_values("maturity")
        times = pd.to_numeric(values.maturity, errors="coerce").to_numpy(dtype=float)
        rates = pd.to_numeric(values.continuous_rate, errors="coerce").to_numpy(dtype=float)
        if (
            len(times) < 2
            or not np.isfinite(times).all()
            or not np.isfinite(rates).all()
            or (times <= 0).any()
            or (np.diff(times) <= 0).any()
        ):
            raise ValueError("Invalid discount curve nodes")
        return cls(str(selected.date()), tuple(times), tuple(rates), stale)

    def rate(self, maturity):
        if not isfinite(maturity) or maturity < 0:
            raise ValueError("Invalid curve maturity")
        times, rates = np.asarray(self.maturities), np.asarray(self.rates)
        if maturity <= times[0]:
            return float(rates[0])
        if maturity >= times[-1]:
            return float(rates[-1])
        return float(np.interp(maturity, times, times * rates) / maturity)

    def discount(self, maturity):
        return exp(-self.rate(maturity) * maturity)

    def describe(self):
        return {
            "date": self.date,
            "stale_days": self.stale_days,
            "source": "RQ China government yield curve",
            "convention": "annual_yield_as_zero_rate_proxy; log-discount interpolation",
            "nodes": [
                {"maturity": t, "continuous_rate": r} for t, r in zip(self.maturities, self.rates)
            ],
        }
