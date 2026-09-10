"""Deterministic daily-bar evidence for technical reviews, separate from execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TechnicalMetadata:
    """Explicit units and provenance; bars must already share one price basis."""

    symbol: str
    market: str
    currency: str
    price_basis: str
    volume_unit: str
    source: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in asdict(self).values()):
            raise ValueError("All technical metadata fields must be non-empty strings")


def _daily_frame(bars: pd.DataFrame, as_of: str, fields: list[str]) -> pd.DataFrame:
    frame = bars.copy()
    if "date" in frame:
        frame.index = pd.to_datetime(frame.pop("date"), errors="raise")
    elif not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("Expected a date column or DatetimeIndex")
    if frame.index.hasnans or frame.index.tz is not None:
        raise ValueError("Dates must be valid, timezone-naive exchange session dates")
    if not frame.index.equals(frame.index.normalize()):
        raise ValueError("Expected daily session dates, not intraday timestamps")
    cutoff = pd.Timestamp(as_of)
    if pd.isna(cutoff) or cutoff.tz is not None or cutoff != cutoff.normalize():
        raise ValueError("as_of must be a timezone-naive session date")
    frame = frame.loc[frame.index <= cutoff].sort_index()
    if frame.empty:
        raise ValueError("No observations at or before as_of")
    if frame.index.has_duplicates:
        raise ValueError("Duplicate sessions; supply one instrument and one daily bar per session")
    if not set(fields).issubset(frame):
        raise ValueError(f"Required fields: {fields}")
    frame = frame[fields].apply(pd.to_numeric, errors="raise").astype(float)
    if not np.isfinite(frame.to_numpy()).all():
        raise ValueError("Missing or non-finite observations must be resolved before review")
    prices = [field for field in fields if field != "volume"]
    if (frame[prices] <= 0).any().any():
        raise ValueError("Prices must be positive")
    if "volume" in frame and (frame["volume"] < 0).any():
        raise ValueError("Volume must be non-negative")
    if "open" in frame and (
        (frame.high < frame[["open", "close", "low"]].max(axis=1)).any()
        or (frame.low > frame[["open", "close", "high"]].min(axis=1)).any()
    ):
        raise ValueError("Inconsistent OHLC bounds or mixed adjustment bases")
    return frame


def _wilder(series: pd.Series, window: int) -> pd.Series:
    """Seed with the first window's arithmetic mean, then recurse with alpha=1/n."""
    result = pd.Series(np.nan, index=series.index, dtype=float)
    valid = series.dropna()
    if len(valid) >= window:
        seeded = valid.iloc[window - 1 :].copy()
        seeded.iloc[0] = valid.iloc[:window].mean()
        result.loc[seeded.index] = seeded.ewm(alpha=1 / window, adjust=False).mean()
    return result


def _indicators(frame: pd.DataFrame) -> pd.DataFrame:
    close, high, low, volume = frame.close, frame.high, frame.low, frame.volume
    values = pd.DataFrame(index=frame.index)
    for window in (1, 5, 20, 60):
        values[f"return_{window}d"] = close.pct_change(window, fill_method=None)
    for window in (5, 10, 20, 60, 120, 250):
        values[f"ma_{window}"] = close.rolling(window).mean()
    diff = close.ewm(span=12, adjust=False, min_periods=12).mean() - close.ewm(
        span=26, adjust=False, min_periods=26
    ).mean()
    values["macd_diff"] = diff
    values["macd_dea"] = diff.ewm(span=9, adjust=False, min_periods=9).mean()
    values["macd_histogram"] = 2 * (diff - values.macd_dea)
    delta = close.diff()
    for window in (6, 12, 14, 24):
        gain = _wilder(delta.clip(lower=0), window)
        loss = _wilder((-delta).clip(lower=0), window)
        total = gain + loss
        values[f"rsi_{window}"] = (100 * gain / total).mask(total.eq(0), 50)
    values["true_range"] = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    values["atr_14"] = _wilder(values.true_range, 14)
    values["atr_pct"] = values.atr_14 / close
    values["boll_middle"] = values.ma_20
    std = close.rolling(20).std(ddof=0)
    values["boll_upper"] = values.boll_middle + 2 * std
    values["boll_lower"] = values.boll_middle - 2 * std
    values["boll_width"] = 4 * std / values.boll_middle
    lowest, highest = low.rolling(9).min(), high.rolling(9).max()
    rsv = (100 * (close - lowest) / (highest - lowest)).mask(highest.eq(lowest), 50)
    k, d = 50.0, 50.0
    values["kdj_k"], values["kdj_d"] = np.nan, np.nan
    for date, value in rsv.dropna().items():
        k = (2 * k + value) / 3
        d = (2 * d + k) / 3
        values.loc[date, ["kdj_k", "kdj_d"]] = [k, d]
    values["kdj_j"] = 3 * values.kdj_k - 2 * values.kdj_d
    typical = (high + low + close) / 3
    deviation = typical.rolling(20).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    values["cci_20"] = (typical - typical.rolling(20).mean()) / (0.015 * deviation)
    values["obv"] = (np.sign(delta).fillna(0) * volume).cumsum()
    values["obv_change_5d"] = values.obv.diff(5)
    # Compare against completed prior sessions, excluding today's potential breakout.
    values["volume_vs_prior20"] = volume / volume.shift().rolling(20).mean().replace(0, np.nan)
    values["prior20_high"] = high.shift().rolling(20).max()
    values["prior20_low"] = low.shift().rolling(20).min()
    span = (high - low).replace(0, np.nan)
    body_low = frame[["open", "close"]].min(axis=1)
    body_high = frame[["open", "close"]].max(axis=1)
    values["lower_shadow_fraction"] = (body_low - low) / span
    values["upper_shadow_fraction"] = (high - body_high) / span
    values["body_fraction"] = (close - frame.open).abs() / span
    values["close_location"] = (close - low) / span
    return values


def technical_evidence(
    bars: pd.DataFrame,
    *,
    as_of: str,
    metadata: TechnicalMetadata,
    benchmark: pd.DataFrame | None = None,
    benchmark_metadata: TechnicalMetadata | None = None,
) -> dict[str, Any]:
    """Return JSON-safe observations and conditional rules, never forecast probabilities.

    No filling of missing sessions or prices is performed. The caller must supply a
    complete exchange-session series; without a calendar, session continuity is unknown.
    Recursive indicators depend on the supplied history's initialization point.
    """
    if (benchmark is None) != (benchmark_metadata is None):
        raise ValueError("Benchmark bars and metadata must be supplied together")
    frame = _daily_frame(bars, as_of, ["open", "high", "low", "close", "volume"])
    values = _indicators(frame)

    def number(value: Any) -> float | None:
        return float(value) if pd.notna(value) and np.isfinite(value) else None

    last = {name: number(value) for name, value in values.iloc[-1].items()}
    patterns: dict[str, bool | None] = {
        "lower_shadow_recovery": None,
        "three_white_soldiers_proxy": None,
        "breakout_with_volume": None,
        "ma_5_10_20_bullish": None,
    }
    if last["lower_shadow_fraction"] is not None:
        patterns["lower_shadow_recovery"] = bool(
            last["lower_shadow_fraction"] >= 0.4 and last["close_location"] >= 0.65
        )
    if len(frame) >= 3:
        tail, features = frame.iloc[-3:], values.iloc[-3:]
        patterns["three_white_soldiers_proxy"] = bool(
            (tail.close > tail.open).all()
            and (tail.close.diff().iloc[1:] > 0).all()
            and (tail.open.iloc[1:] >= tail.open.shift().iloc[1:]).all()
            and (tail.open.iloc[1:] <= tail.close.shift().iloc[1:]).all()
            and (features.upper_shadow_fraction <= 0.25).all()
            and (features.body_fraction >= 0.5).all()
        )
    if last["volume_vs_prior20"] is not None and last["prior20_high"] is not None:
        patterns["breakout_with_volume"] = bool(
            frame.close.iloc[-1] > last["prior20_high"] and last["volume_vs_prior20"] >= 1.5
        )
    if last["ma_20"] is not None:
        patterns["ma_5_10_20_bullish"] = bool(last["ma_5"] > last["ma_10"] > last["ma_20"])

    benchmark_evidence = None
    if benchmark is not None:
        if metadata.currency != benchmark_metadata.currency:
            raise ValueError("Convert benchmark and asset to the same currency before comparison")
        reference = _daily_frame(benchmark, as_of, ["close"])
        comparisons = {}
        for window in (1, 5, 20, 60):
            pair = {"start": None, "end": str(frame.index[-1].date()), "benchmark_return": None,
                    "asset_return": last[f"return_{window}d"], "excess_percentage_points": None}
            if len(frame) > window:
                start, end = frame.index[-window - 1], frame.index[-1]
                pair["start"] = str(start.date())
                if start in reference.index and end in reference.index:
                    change = reference.at[end, "close"] / reference.at[start, "close"] - 1
                    pair["benchmark_return"] = float(change)
                    pair["excess_percentage_points"] = 100 * (pair["asset_return"] - change)
            comparisons[f"{window}d"] = pair
        benchmark_evidence = {"metadata": asdict(benchmark_metadata), "comparisons": comparisons}

    warnings = [
        "Session continuity is unverified without an exchange calendar; returns count supplied bars.",
        "OBV is signed volume from this input's starting point, not observed institutional cash flow.",
        "Pattern thresholds are research rules; their predictive performance has not been established.",
    ]
    if frame.index[-1] != pd.Timestamp(as_of):
        warnings.append("Latest bar precedes requested as_of; report is stale relative to that date.")
    if len(frame) < 250:
        warnings.append("Fewer than 250 bars: long averages may be unavailable; recursive warm-up is limited.")
    if benchmark is None:
        warnings.append("No benchmark supplied: no broad-market regime or relative-strength conclusion.")
    recent = []
    for date in frame.index[-5:]:
        recent.append({"date": str(date.date()), **{k: number(v) for k, v in frame.loc[date].items()},
                       **{k: number(v) for k, v in values.loc[date].items()}})
    return {
        "schema_version": 1,
        "metadata": asdict(metadata),
        "requested_as_of": as_of,
        "observed_as_of": str(frame.index[-1].date()),
        "history_start": str(frame.index[0].date()),
        "observations": len(frame),
        "input_sha256": sha256(frame.to_csv(float_format="%.12g").encode()).hexdigest(),
        "bar": {name: number(value) for name, value in frame.iloc[-1].items()},
        "indicators": last,
        "patterns": patterns,
        "recent_observations": recent,
        "benchmark": benchmark_evidence,
        "conventions": {
            "returns": "close[t]/close[t-n]-1; n supplied sessions, not calendar days",
            "ma_boll": "SMA; Bollinger(20,2), population standard deviation ddof=0",
            "macd": "EMA(12,26), first-close seed; DEA(9) seeded at first valid DIFF; histogram=2*(DIFF-DEA)",
            "rsi_atr": "Wilder SMA seed then alpha=1/n; flat RSI=50; first TR=high-low",
            "kdj": "RSV(9), flat range=50; K and D seeded at 50, alpha=1/3; J=3K-2D",
            "cci": "TP=(H+L+C)/3, window20, mean absolute deviation, constant0.015; flat=unavailable",
            "obv": "Starts at zero; unchanged close contributes zero; unit equals input volume unit",
            "patterns": "lower shadow>=40% and close location>=65%; three soldiers: 3 rising bullish bodies, next open within prior body, body>=50%, upper wick<=25%; breakout: close>prior20 high and volume>=1.5*prior20 mean",
        },
        "scenarios": [
            {"name": "upside_confirmation", "condition": "A subsequent close exceeds prior20_high with volume_vs_prior20 >= 1.5", "probability": None},
            {"name": "structure_failure", "condition": "A subsequent close falls below prior20_low; re-evaluate regime and risk", "probability": None},
        ],
        "warnings": warnings,
    }


def render_technical_evidence(report: dict[str, Any]) -> str:
    """Render an auditable report; narrative interpretation is a separate skill step."""
    meta = report["metadata"]
    lines = [f"# {meta['symbol']} 技术证据 · {report['observed_as_of']}", "",
             f"市场：{meta['market']}；币种：{meta['currency']}；价格口径：{meta['price_basis']}；成交量单位：{meta['volume_unit']}。",
             f"来源：{meta['source']}",
             f"历史：{report['history_start']}—{report['observed_as_of']}，{report['observations']} 根日线。", "",
             "| 指标 | 数值 |", "|---|---:|"]
    for name, value in {**report["bar"], **report["indicators"]}.items():
        lines.append(f"| {name} | {'数据不足/不可计算' if value is None else format(value, '.8g')} |")
    lines += ["", "收益、波动比例字段为小数（0.01 = 1%）；excess_percentage_points 为百分点。", "",
              "## 形态条件", ""]
    for name, value in report["patterns"].items():
        lines.append(f"- {name}：{'未知' if value is None else ('满足' if value else '不满足')}")
    lines += ["", "## 口径", ""] + [f"- {k}：{v}" for k, v in report["conventions"].items()]
    if report["benchmark"] is not None:
        lines += ["", "## 大盘参照", "", str(report["benchmark"])]
    lines += ["", "## 条件情景", ""]
    lines += [f"- {item['name']}：{item['condition']}；未估计概率。" for item in report["scenarios"]]
    lines += ["", "## 数据与解释边界", ""] + [f"- {text}" for text in report["warnings"]]
    lines += ["", f"输入指纹：`{report['input_sha256']}`", ""]
    return "\n".join(lines)
