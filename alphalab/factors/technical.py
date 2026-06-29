"""Small, dependency-light technical factor set."""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd


class TechnicalFactors:
    """Compute cross-sectional technical factors from OHLCV history."""

    def __init__(self) -> None:
        self._factors: dict[str, Callable[[pd.DataFrame], float | None]] = {
            "momentum_20d": self._momentum_20d,
            "momentum_60d": self._momentum_60d,
            "reversal_5d": self._reversal_5d,
            "volatility_20d": self._volatility_20d,
            "turnover_20d": self._turnover_20d,
            "volume_ratio": self._volume_ratio,
            "rsi_14": self._rsi_14,
            "ma_deviation": self._ma_deviation,
        }

    @property
    def available_factors(self) -> list[str]:
        return sorted(self._factors)

    def compute(self, name: str, data: dict[str, pd.DataFrame]) -> pd.Series:
        if name not in self._factors:
            raise KeyError(f"Unknown technical factor: {name}")
        values: dict[str, float] = {}
        for symbol, frame in data.items():
            if frame.empty:
                continue
            try:
                value = self._factors[name](frame.sort_values("date") if "date" in frame else frame)
            except Exception:
                value = None
            if value is not None and np.isfinite(value):
                values[str(symbol).upper()] = float(value)
        return pd.Series(values, name=name, dtype=float)

    def compute_multiple(self, names: list[str], data: dict[str, pd.DataFrame]) -> pd.DataFrame:
        return pd.concat([self.compute(name, data) for name in names], axis=1)

    @staticmethod
    def _close(df: pd.DataFrame) -> np.ndarray:
        return pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)

    @staticmethod
    def _volume(df: pd.DataFrame) -> np.ndarray:
        return pd.to_numeric(df.get("volume", pd.Series(dtype=float)), errors="coerce").to_numpy(dtype=float)

    def _momentum_20d(self, df: pd.DataFrame) -> float | None:
        return self._pct_change(df, 20)

    def _momentum_60d(self, df: pd.DataFrame) -> float | None:
        return self._pct_change(df, 60)

    def _reversal_5d(self, df: pd.DataFrame) -> float | None:
        return self._pct_change(df, 5)

    def _pct_change(self, df: pd.DataFrame, window: int) -> float | None:
        close = self._close(df)
        if len(close) < window + 1 or close[-window - 1] <= 0:
            return None
        return float(close[-1] / close[-window - 1] - 1.0)

    def _volatility_20d(self, df: pd.DataFrame) -> float | None:
        close = self._close(df)
        if len(close) < 21:
            return None
        returns = pd.Series(close).pct_change().dropna().iloc[-20:]
        if returns.empty:
            return None
        return float(returns.std(ddof=0) * np.sqrt(252))

    def _turnover_20d(self, df: pd.DataFrame) -> float | None:
        volume = self._volume(df)
        if len(volume) < 20:
            return None
        return float(np.nanmean(volume[-20:]))

    def _volume_ratio(self, df: pd.DataFrame) -> float | None:
        volume = self._volume(df)
        if len(volume) < 20:
            return None
        base = np.nanmean(volume[-20:])
        if not np.isfinite(base) or base <= 0:
            return None
        return float(np.nanmean(volume[-5:]) / base)

    def _rsi_14(self, df: pd.DataFrame) -> float | None:
        close = self._close(df)
        if len(close) < 15:
            return None
        delta = np.diff(close[-15:])
        gains = np.clip(delta, 0, None).mean()
        losses = np.clip(-delta, 0, None).mean()
        if losses == 0:
            return 100.0
        rs = gains / losses
        return float(100 - 100 / (1 + rs))

    def _ma_deviation(self, df: pd.DataFrame) -> float | None:
        close = self._close(df)
        if len(close) < 20:
            return None
        ma = np.nanmean(close[-20:])
        if not np.isfinite(ma) or ma <= 0:
            return None
        return float(close[-1] / ma - 1.0)


def compute_technical_factors(data: dict[str, pd.DataFrame], factors: list[str] | None = None) -> pd.DataFrame:
    calculator = TechnicalFactors()
    return calculator.compute_multiple(factors or calculator.available_factors, data)

