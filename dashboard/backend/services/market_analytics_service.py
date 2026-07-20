"""Profile-aware market analytics derived from factor-return data."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd

from dashboard.backend.services.framework_service import factor_returns

DEFAULT_FACTORS = ("MKT", "SMB", "HML")
PERIODS_PER_YEAR = 12
VOLATILITY_WINDOW = 12


def _safe(value: Any) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    return numeric if np.isfinite(numeric) else None


def _validate_dates(start: str | None, end: str | None) -> None:
    start_date = pd.Timestamp(start) if start else None
    end_date = pd.Timestamp(end) if end else None
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start must be on or before end")


def _parse_factors(factors: Iterable[str] | None) -> list[str] | None:
    if factors is None:
        return None
    parsed = [str(factor).strip().upper() for factor in factors if str(factor).strip()]
    if not parsed:
        raise ValueError("at least one factor is required")
    return list(dict.fromkeys(parsed))


def _load_factor_frame(
    profile: str = "demo",
    start: str | None = None,
    end: str | None = None,
    factors: Iterable[str] | None = None,
) -> pd.DataFrame:
    _validate_dates(start, end)
    requested = _parse_factors(factors)
    payload = factor_returns(requested, start, end, profile)
    rows = payload["rows"]
    if not rows:
        return pd.DataFrame(columns=payload["names"], dtype=float)
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date")[payload["names"]].apply(pd.to_numeric, errors="coerce")


def _default_factor_names(frame: pd.DataFrame) -> list[str]:
    names = [name for name in DEFAULT_FACTORS if name in frame.columns]
    if names:
        return names
    return [name for name in frame.columns if name.lower() != "rf"]


def _annualized_return(series: pd.Series) -> float | None:
    values = pd.Series(series, dtype=float).dropna()
    if values.empty:
        return None
    growth = float((1.0 + values).prod())
    if growth <= 0:
        return None
    return _safe(growth ** (PERIODS_PER_YEAR / len(values)) - 1.0)


def _annualized_volatility(series: pd.Series) -> float | None:
    values = pd.Series(series, dtype=float).dropna()
    if len(values) < 2:
        return None
    return _safe(values.std(ddof=1) * np.sqrt(PERIODS_PER_YEAR))


def _annualized_sharpe(series: pd.Series) -> float | None:
    values = pd.Series(series, dtype=float).dropna()
    if len(values) < 2:
        return None
    volatility = float(values.std(ddof=1))
    if volatility <= 0:
        return None
    return _safe(values.mean() / volatility * np.sqrt(PERIODS_PER_YEAR))


def _drawdown_series(series: pd.Series) -> pd.Series:
    values = pd.Series(series, dtype=float).dropna()
    if values.empty:
        return pd.Series(dtype=float)
    nav = (1.0 + values).cumprod()
    return nav / nav.cummax() - 1.0


def _series_payload(frame: pd.DataFrame) -> dict:
    return {
        "dates": [timestamp.strftime("%Y-%m-%d") for timestamp in frame.index],
        "series": {
            column: [_safe(value) for value in frame[column].tolist()]
            for column in frame.columns
        },
    }


def compute_kpi(
    profile: str = "demo",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    frame = _load_factor_frame(profile, start, end)
    result: dict[str, Any] = {
        "profile": profile,
        "data_start": frame.index.min().strftime("%Y-%m-%d") if not frame.empty else None,
        "data_end": frame.index.max().strftime("%Y-%m-%d") if not frame.empty else None,
        "n_months": int(len(frame)),
        "mkt_ann_return": None,
        "mkt_last_12m_return": None,
        "mkt_sharpe_full": None,
        "mkt_sharpe_recent": None,
        "smb_ann_return": None,
        "smb_last_12m_return": None,
        "hml_ann_return": None,
        "hml_last_12m_return": None,
        "avg_vol": None,
        "latest_vol": None,
        "current_regime": None,
    }
    if frame.empty:
        return result

    for factor in DEFAULT_FACTORS:
        if factor not in frame:
            continue
        series = frame[factor].dropna()
        recent = series.tail(PERIODS_PER_YEAR)
        key = factor.lower()
        result[f"{key}_ann_return"] = _annualized_return(series)
        result[f"{key}_last_12m_return"] = _annualized_return(recent)
    if "MKT" in frame:
        result["mkt_sharpe_full"] = _annualized_sharpe(frame["MKT"])
        result["mkt_sharpe_recent"] = _annualized_sharpe(
            frame["MKT"].tail(PERIODS_PER_YEAR)
        )

    volatility = _rolling_volatility(frame)
    if not volatility.empty:
        result["avg_vol"] = _safe(volatility.mean())
        result["latest_vol"] = _safe(volatility.iloc[-1])
        t1 = float(volatility.quantile(1 / 3))
        t2 = float(volatility.quantile(2 / 3))
        result["current_regime"] = _regime(float(volatility.iloc[-1]), t1, t2)
    return result


def compute_cumulative_returns(
    profile: str = "demo",
    start: str | None = None,
    end: str | None = None,
    factors: Iterable[str] | None = None,
) -> dict:
    frame = _load_factor_frame(profile, start, end, factors)
    if factors is None:
        frame = frame[_default_factor_names(frame)]
    cumulative = (1.0 + frame).cumprod() - 1.0
    return {"profile": profile, **_series_payload(cumulative)}


def compute_annual_returns(
    profile: str = "demo",
    start: str | None = None,
    end: str | None = None,
    factors: Iterable[str] | None = None,
) -> dict:
    frame = _load_factor_frame(profile, start, end, factors)
    if factors is None:
        frame = frame[_default_factor_names(frame)]
    if frame.empty:
        return {"profile": profile, "years": [], "series": {}}
    annual = frame.groupby(frame.index.year).agg(
        lambda values: (1.0 + values.dropna()).prod() - 1.0
    )
    return {
        "profile": profile,
        "years": [str(year) for year in annual.index],
        "series": {
            column: [_safe(value) for value in annual[column].tolist()]
            for column in annual.columns
        },
    }


def compute_factor_stats(
    profile: str = "demo",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    frame = _load_factor_frame(profile, start, end)
    rows = []
    for factor in _default_factor_names(frame):
        series = frame[factor].dropna()
        rows.append(
            {
                "factor": factor,
                "ann_return": _annualized_return(series),
                "ann_vol": _annualized_volatility(series),
                "sharpe": _annualized_sharpe(series),
                "max_dd": _safe(_drawdown_series(series).min()),
                "pos_ratio": _safe((series > 0).mean()) if not series.empty else None,
                "skew": _safe(series.skew()) if len(series) >= 3 else None,
                "kurt": _safe(series.kurt()) if len(series) >= 4 else None,
            }
        )
    return {"profile": profile, "stats": rows}


def _drawdown_periods(drawdown: pd.Series, top_n: int) -> list[dict]:
    if drawdown.empty:
        return []
    periods: list[dict] = []
    peak_date = drawdown.index[0]
    active_start: pd.Timestamp | None = None

    def append_period(start_date: pd.Timestamp, end_date: pd.Timestamp, recovered: bool) -> None:
        segment = drawdown.loc[start_date:end_date]
        negative = segment.loc[segment < 0]
        if negative.empty:
            return
        trough = negative.idxmin()
        periods.append(
            {
                "start": start_date.strftime("%Y-%m-%d"),
                "trough": trough.strftime("%Y-%m-%d"),
                "end": end_date.strftime("%Y-%m-%d"),
                "depth": _safe(negative.min()),
                "recovery_months": (
                    round((end_date - trough).days / 30.4375, 1) if recovered else None
                ),
            }
        )

    for date, value in drawdown.items():
        if value >= 0:
            if active_start is not None:
                append_period(active_start, date, True)
                active_start = None
            peak_date = date
        elif active_start is None:
            active_start = peak_date
    if active_start is not None:
        append_period(active_start, drawdown.index[-1], False)
    return sorted(periods, key=lambda item: item["depth"] or 0.0)[:top_n]


def compute_drawdowns(
    profile: str = "demo",
    start: str | None = None,
    end: str | None = None,
    top_n: int = 5,
) -> dict:
    frame = _load_factor_frame(profile, start, end, ["MKT"])
    drawdown = _drawdown_series(frame["MKT"]) if "MKT" in frame else pd.Series(dtype=float)
    return {
        "profile": profile,
        "dates": [timestamp.strftime("%Y-%m-%d") for timestamp in drawdown.index],
        "drawdown_values": [_safe(value) for value in drawdown.tolist()],
        "top_drawdowns": _drawdown_periods(drawdown, top_n),
    }


def _rolling_volatility(frame: pd.DataFrame) -> pd.Series:
    if frame.empty or "MKT" not in frame:
        return pd.Series(dtype=float)
    return (
        frame["MKT"]
        .rolling(VOLATILITY_WINDOW, min_periods=3)
        .std(ddof=1)
        .mul(np.sqrt(PERIODS_PER_YEAR))
        .dropna()
    )


def _regime(value: float, t1: float, t2: float) -> str:
    if value <= t1:
        return "low"
    if value <= t2:
        return "normal"
    return "high"


def compute_volatility(
    profile: str = "demo",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    frame = _load_factor_frame(profile, start, end, ["MKT"])
    volatility = _rolling_volatility(frame)
    if volatility.empty:
        return {
            "profile": profile,
            "dates": [],
            "vol_values": [],
            "regimes": [],
            "t1": None,
            "t2": None,
            "regime_stats": [],
        }
    t1 = float(volatility.quantile(1 / 3))
    t2 = float(volatility.quantile(2 / 3))
    regimes = volatility.map(lambda value: _regime(float(value), t1, t2))
    stats = []
    for label in ("low", "normal", "high"):
        values = volatility.loc[regimes.eq(label)]
        stats.append(
            {
                "regime": label,
                "n_months": int(len(values)),
                "proportion": _safe(len(values) / len(volatility)),
                "mean": _safe(values.mean()),
                "median": _safe(values.median()),
                "min_val": _safe(values.min()),
                "max_val": _safe(values.max()),
            }
        )
    return {
        "profile": profile,
        "dates": [timestamp.strftime("%Y-%m-%d") for timestamp in volatility.index],
        "vol_values": [_safe(value) for value in volatility.tolist()],
        "regimes": regimes.tolist(),
        "t1": _safe(t1),
        "t2": _safe(t2),
        "regime_stats": stats,
    }


def compute_correlation(
    profile: str = "demo",
    start: str | None = None,
    end: str | None = None,
    factors: Iterable[str] | None = None,
) -> dict:
    frame = _load_factor_frame(profile, start, end, factors)
    if factors is None:
        frame = frame[_default_factor_names(frame)]
    correlation = frame.corr()
    labels = list(correlation.columns)
    return {
        "profile": profile,
        "labels": labels,
        "matrix": [
            [_safe(value) for value in correlation.loc[row, labels].tolist()]
            for row in labels
        ],
    }
