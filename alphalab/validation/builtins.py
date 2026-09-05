"""Built-in project validation source shown in the Validation Workbench."""

DEFAULT_VALIDATION_SOURCE = '''"""Project-owned performance, attribution, risk, and evidence validation."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

from alphalab.validation_sdk import ValidationContext, analysis

VALIDATION_SDK_VERSION = 1
# 回归会按这个稳定顺序选择数据中实际存在的因子列。
FACTOR_NAMES = ("MKT", "SMB", "HML", "MOM", "RMW")


@analysis(id="performance", label="收益与回撤")
def performance(
    context: ValidationContext,
    *,
    periods_per_year: int = 252,
    risk_free_rate: float = 0.0,
) -> dict:
    """计算回测编辑器下方展示的收益、风险与样本数指标。"""
    # 先统一转成有限的浮点收益；无法解析的输入不会被当作零收益。
    returns = pd.to_numeric(context.returns, errors="coerce").dropna().astype(float)
    if returns.empty:
        # 空样本仍返回完整字段，保持工作台展示契约稳定。
        return {
            "total_return": 0.0,
            "annual_return": 0.0,
            "annual_vol": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "n_periods": 0,
        }
    # 净值使用简单收益连乘，回撤相对历史净值最高点计算。
    equity = (1.0 + returns).cumprod()
    total_return = float(equity.iloc[-1] - 1.0)
    years = len(returns) / periods_per_year
    annual_return = float(equity.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else 0.0
    annual_vol = float(returns.std(ddof=1) * np.sqrt(periods_per_year)) if len(returns) > 1 else 0.0
    period_rf = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess = returns - period_rf
    # 单点样本或零波动没有可解释的 Sharpe，明确返回 0。
    sharpe = float(excess.mean() / excess.std(ddof=1) * np.sqrt(periods_per_year)) if len(excess) > 1 and excess.std(ddof=1) > 0 else 0.0
    drawdown = equity / equity.cummax() - 1.0
    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_vol": annual_vol,
        "sharpe": sharpe,
        "max_drawdown": float(drawdown.min()),
        "n_periods": int(len(returns)),
    }


@analysis(id="alpha_beta", label="Alpha / Beta 归因")
def alpha_beta(
    context: ValidationContext,
    *,
    minimum_observations: int = 6,
    newey_west_lags: int = 3,
) -> dict:
    """运行 CAPM 和五因子 OLS，并使用 Newey-West 标准误。"""
    # 策略日收益先按月复合；因子表按月取最后一条供应商快照。
    strategy = _monthly_returns(context.returns).rename("strategy")
    factors = _monthly_factors(context.factor_returns)
    aligned = (
        strategy.to_frame()
        if factors.empty
        else pd.concat([strategy, factors], axis=1, join="inner")
    ).replace([np.inf, -np.inf], np.nan)
    complete = aligned.dropna(subset=["strategy", "MKT", "rf"]) if {"MKT", "rf"}.issubset(aligned.columns) else pd.DataFrame()
    factor_names = tuple(name for name in FACTOR_NAMES if name in complete.columns)
    multi_frame = complete.dropna(subset=list(factor_names)) if factor_names else pd.DataFrame()
    warnings = []
    # 覆盖不足只降低结论可信度，不用补零制造虚假的完整样本。
    if complete.empty:
        warnings.append("MKT/rf factor coverage is insufficient for alpha/beta attribution")
    elif len(complete) < 24:
        warnings.append("Fewer than 24 monthly observations; regression estimates are unstable")
    capm = _regression(complete, ("MKT",), minimum_observations, newey_west_lags)
    multi_factor = _regression(multi_frame, factor_names, minimum_observations, newey_west_lags)
    for result in (capm, multi_factor):
        if result.get("warning"):
            warnings.append(result["warning"])
    correlation = factors[[name for name in FACTOR_NAMES if name in factors.columns]].dropna(how="all")
    snapshot = [
        # 保存进入回归的月度输入，报告可据此复核数据对齐。
        {
            "date": period.to_timestamp("M").strftime("%Y-%m-%d"),
            **{name: _finite(value) for name, value in row.items()},
        }
        for period, row in aligned.iterrows()
    ]
    return {
        "frequency": "monthly",
        "observations": int(len(complete)),
        "coverage": float(len(complete) / len(strategy)) if len(strategy) else 0.0,
        "capm": capm,
        "multi_factor": multi_factor,
        "factor_return_correlation": {
            "labels": list(correlation.columns),
            "observations": int(len(correlation)),
            "pearson": _matrix(correlation.corr(method="pearson")),
            "spearman": _matrix(correlation.corr(method="spearman")),
        },
        "selection_score_correlation": {"labels": [], "periods": 0, "median_spearman": []},
        "research_checks": {},
        "input_snapshot": snapshot,
        "warnings": list(dict.fromkeys(warnings)),
    }


@analysis(id="risk", label="尾部风险与集中度")
def risk(
    context: ValidationContext,
    *,
    periods_per_year: int = 252,
    minimum_observations: int = 20,
    confidence_95: float = 0.95,
    confidence_99: float = 0.99,
) -> dict:
    """用冻结收益和实际持仓计算尾部风险、回撤持续期与集中度。"""
    returns = pd.to_numeric(context.returns, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna().astype(float)
    warnings = []
    sufficient = len(returns) >= int(minimum_observations)
    if not sufficient:
        warnings.append(f"At least {int(minimum_observations)} return observations are required for tail risk")
    tail_95 = _tail_loss(returns, confidence_95) if sufficient else (None, None)
    tail_99 = _tail_loss(returns, confidence_99) if sufficient else (None, None)
    equity = (1.0 + returns).cumprod() if not returns.empty else pd.Series(dtype=float)
    drawdown = equity.div(equity.cummax()).sub(1.0) if not equity.empty else equity
    downside = returns.where(returns.lt(0), 0.0)
    portfolio = _portfolio_risk(context.weights)
    turnover = _weight_turnover(context.weights)
    return {
        "status": "sufficient" if sufficient else "insufficient",
        "observations": int(len(returns)),
        "var_95": tail_95[0],
        "cvar_95": tail_95[1],
        "var_99": tail_99[0],
        "cvar_99": tail_99[1],
        "downside_volatility_annualized": (
            float(downside.std(ddof=1) * np.sqrt(periods_per_year))
            if len(downside) > 1 else None
        ),
        "current_drawdown": float(drawdown.iloc[-1]) if not drawdown.empty else None,
        "max_drawdown": float(drawdown.min()) if not drawdown.empty else None,
        "max_drawdown_duration_periods": _max_drawdown_duration(drawdown),
        "portfolio": portfolio,
        "turnover": turnover,
        "warnings": warnings,
    }


@analysis(id="research_quality", label="研究证据质量")
def research_quality(
    context: ValidationContext,
    *,
    minimum_signal_periods: int = 12,
    minimum_mean_ic: float = 0.0,
    minimum_coverage: float = 0.60,
    minimum_execution_fidelity: float = 0.80,
) -> dict:
    """汇总冻结信号证据、数据完整性和执行保真度并给出研究状态。"""
    diagnostics = dict(context.run_diagnostics)
    evidence = diagnostics.get("signal_evidence") if isinstance(diagnostics.get("signal_evidence"), dict) else {}
    rows = [dict(item) for item in evidence.get("rows", []) if isinstance(item, dict)]
    ic_values = pd.Series(
        [float(item["ic"]) for item in rows if item.get("ic") is not None],
        dtype=float,
    )
    coverage_values = [float(item["coverage"]) for item in rows if item.get("coverage") is not None]
    fidelity_payload = diagnostics.get("execution_fidelity") if isinstance(diagnostics.get("execution_fidelity"), dict) else {}
    fidelity = _finite(fidelity_payload.get("mean"))
    core_valid_value = diagnostics.get("research_valid")
    core_valid = core_valid_value is True
    invalid_reasons = [str(value) for value in diagnostics.get("research_invalid_reasons", [])]
    mean_ic = _finite(ic_values.mean()) if not ic_values.empty else None
    average_coverage = _finite(np.mean(coverage_values)) if coverage_values else None
    sufficient = len(ic_values) >= int(minimum_signal_periods)
    checks = {
        "core_research_valid": core_valid,
        "signal_evidence": sufficient,
        "mean_ic": mean_ic is not None and mean_ic >= minimum_mean_ic,
        "coverage": average_coverage is not None and average_coverage >= minimum_coverage,
        "execution_fidelity": fidelity is not None and fidelity >= minimum_execution_fidelity,
    }
    complete_evidence = (
        sufficient
        and mean_ic is not None
        and average_coverage is not None
        and fidelity is not None
    )
    if core_valid_value is False:
        status = "fail"
    elif not complete_evidence or core_valid_value is None:
        status = "insufficient"
    else:
        status = "pass" if all(checks.values()) else "fail"
    warnings = []
    if not sufficient:
        warnings.append(f"Only {len(ic_values)} signal periods have forward evidence; {int(minimum_signal_periods)} are required")
    if core_valid_value is None:
        warnings.append("The frozen run predates core research-validity diagnostics")
    warnings.extend(invalid_reasons)
    exclusions = diagnostics.get("execution_data_exclusions") if isinstance(diagnostics.get("execution_data_exclusions"), dict) else {}
    return {
        "status": status,
        "signal_periods": int(len(rows)),
        "evidence_periods": int(len(ic_values)),
        "mean_rank_ic": mean_ic,
        "newey_west_t_stat": _mean_t_stat(ic_values),
        "positive_ic_ratio": float(ic_values.gt(0).mean()) if not ic_values.empty else None,
        "average_coverage": average_coverage,
        "execution_fidelity": fidelity,
        "excluded_symbol_dates": int(exclusions.get("symbol_date_count") or 0),
        "research_invalid_reasons": invalid_reasons,
        "checks": checks,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _tail_loss(returns: pd.Series, confidence: float) -> tuple[float, float]:
    """Return positive historical VaR and expected shortfall for one confidence."""
    confidence = float(confidence)
    if confidence <= 0 or confidence >= 1:
        raise ValueError("risk confidence must be in (0, 1)")
    losses = -returns.to_numpy(dtype=float)
    value_at_risk = max(0.0, float(np.quantile(losses, confidence)))
    tail = losses[losses >= value_at_risk]
    expected_shortfall = max(value_at_risk, float(np.mean(tail))) if len(tail) else value_at_risk
    return value_at_risk, expected_shortfall


def _max_drawdown_duration(drawdown: pd.Series) -> int:
    """Count the longest consecutive run below the prior equity peak."""
    longest = 0
    current = 0
    for value in drawdown:
        if float(value) < -1e-12:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return int(longest)


def _portfolio_risk(values: pd.DataFrame) -> dict:
    """Describe concentration from the latest actual frozen weight snapshot."""
    frame = pd.DataFrame(values).apply(pd.to_numeric, errors="coerce").fillna(0.0)
    if frame.empty:
        return {
            "max_weight": None, "max_weight_observed": None, "hhi": None,
            "effective_positions": None, "holdings": 0,
        }
    latest = frame.iloc[-1]
    latest = latest.loc[latest.abs().gt(1e-12)]
    hhi = float((latest ** 2).sum()) if not latest.empty else 0.0
    return {
        "max_weight": float(latest.max()) if not latest.empty else 0.0,
        "max_weight_observed": float(frame.max(axis=1).max()),
        "hhi": hhi,
        "effective_positions": float(1.0 / hhi) if hhi > 0 else None,
        "holdings": int(len(latest)),
    }


def _weight_turnover(values: pd.DataFrame) -> dict:
    """Calculate cash-inclusive one-way turnover from actual frozen weights."""
    frame = pd.DataFrame(values).apply(pd.to_numeric, errors="coerce").fillna(0.0).sort_index()
    if frame.empty:
        return {"average": None, "maximum": None, "periods": 0}
    previous = frame.shift(1, fill_value=0.0)
    cash = 1.0 - frame.sum(axis=1)
    previous_cash = 1.0 - previous.sum(axis=1)
    turnover = ((frame - previous).abs().sum(axis=1) + (cash - previous_cash).abs()) / 2.0
    return {
        "average": float(turnover.mean()),
        "maximum": float(turnover.max()),
        "periods": int(len(turnover)),
    }


def _mean_t_stat(values: pd.Series) -> float | None:
    """Estimate the mean t statistic with the same Newey-West covariance helper."""
    series = pd.to_numeric(values, errors="coerce").dropna().astype(float)
    if len(series) < 2:
        return None
    x = np.ones((len(series), 1), dtype=float)
    residuals = series.to_numpy(dtype=float) - float(series.mean())
    covariance = _newey_west_covariance(x, residuals, min(3, len(series) - 1))
    standard_error = float(np.sqrt(max(0.0, covariance[0, 0])))
    return float(series.mean() / standard_error) if standard_error > 0 else None


def _monthly_returns(values: pd.Series) -> pd.Series:
    """把任意日期索引的周期收益按自然月复合。"""
    series = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    if series.empty:
        return pd.Series(dtype=float, index=pd.PeriodIndex([], freq="M"))
    series.index = pd.to_datetime(series.index)
    result = series.groupby(series.index.to_period("M")).apply(lambda group: float((1.0 + group).prod() - 1.0))
    result.index = pd.PeriodIndex(result.index, freq="M")
    return result.sort_index().astype(float)


def _monthly_factors(values: pd.DataFrame) -> pd.DataFrame:
    """规范化因子日期，并为每个自然月保留最后一条可用快照。"""
    if values.empty:
        return pd.DataFrame(index=pd.PeriodIndex([], freq="M"))
    frame = values.copy()
    if "date" in frame:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.set_index("date")
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    frame = frame.loc[frame.index.notna()]
    columns = [name for name in (*FACTOR_NAMES, "rf") if name in frame]
    frame = frame[columns].apply(pd.to_numeric, errors="coerce")
    frame.index = frame.index.to_period("M")
    return frame.groupby(level=0).last().sort_index()


def _regression(frame: pd.DataFrame, factor_names: tuple[str, ...], minimum_observations: int, nw_lags: int) -> dict:
    """拟合带截距的多元回归并返回可 JSON 序列化的诊断。"""
    empty = {
        "observations": 0, "alpha_monthly": None, "alpha_annualized": None,
        "betas": {name: None for name in factor_names}, "r_squared": None,
        "residual_volatility_annualized": None, "estimates": {}, "warning": None,
    }
    if frame.empty or not factor_names:
        return empty
    clean = frame.dropna(subset=["strategy", "rf", *factor_names]).copy()
    required = max(int(minimum_observations), len(factor_names) + 3)
    if len(clean) < required:
        # 参数越多，需要的最小完整月份也越多，避免欠定回归被误读。
        empty["observations"] = int(len(clean))
        empty["warning"] = f"At least {required} complete monthly observations are required"
        return empty
    y = (clean["strategy"] - clean["rf"]).to_numpy(dtype=float)
    columns = [(clean[name] - clean["rf"] if name == "MKT" else clean[name]).to_numpy(dtype=float) for name in factor_names]
    x = np.column_stack([np.ones(len(clean)), *columns])
    coefficients, _, rank, _ = np.linalg.lstsq(x, y, rcond=None)
    residuals = y - x @ coefficients
    # 异方差与自相关稳健协方差用于置信区间和 t 统计量。
    covariance = _newey_west_covariance(x, residuals, nw_lags)
    standard_errors = np.sqrt(np.maximum(0.0, np.diag(covariance)))
    t_stats = np.divide(coefficients, standard_errors, out=np.zeros_like(coefficients), where=standard_errors > 0)
    dof = len(y) - x.shape[1]
    critical = float(student_t.ppf(0.975, dof)) if dof > 0 else 1.96
    total = float(np.sum((y - y.mean()) ** 2))
    residual_sum = float(np.sum(residuals ** 2))
    names = ("alpha", *factor_names)
    estimates = {
        name: {
            "estimate": float(coefficients[index]),
            "standard_error": float(standard_errors[index]),
            "t_stat": float(t_stats[index]),
            "confidence_95": [
                float(coefficients[index] - critical * standard_errors[index]),
                float(coefficients[index] + critical * standard_errors[index]),
            ],
        }
        for index, name in enumerate(names)
    }
    alpha = float(coefficients[0])
    return {
        "observations": int(len(clean)),
        "alpha_monthly": alpha,
        "alpha_annualized": float((1.0 + alpha) ** 12 - 1.0) if alpha > -1 else -1.0,
        "betas": {name: float(coefficients[index + 1]) for index, name in enumerate(factor_names)},
        "r_squared": float(1.0 - residual_sum / total) if total > 0 else 0.0,
        "residual_volatility_annualized": float(np.std(residuals, ddof=max(1, x.shape[1])) * np.sqrt(12)),
        "estimates": estimates,
        "warning": "Factor matrix is rank deficient" if rank < x.shape[1] else None,
    }


def _newey_west_covariance(x: np.ndarray, residuals: np.ndarray, requested_lags: int) -> np.ndarray:
    """计算带 Bartlett 权重和有限样本修正的 Newey-West 协方差。"""
    observations, parameters = x.shape
    bread = np.linalg.pinv(x.T @ x)
    scores = x * residuals[:, None]
    lag_count = min(observations - 1, max(0, int(requested_lags)))
    meat = scores.T @ scores
    for lag in range(1, lag_count + 1):
        weight = 1.0 - lag / (lag_count + 1.0)
        lagged = scores[lag:].T @ scores[:-lag]
        meat += weight * (lagged + lagged.T)
    correction = observations / max(1, observations - parameters)
    return correction * bread @ meat @ bread


def _matrix(values: pd.DataFrame) -> list[list[float | None]]:
    """把相关矩阵转换为前端可安全解析的二维列表。"""
    return [[_finite(value) for value in values.loc[row, values.columns].tolist()] for row in values.index]


def _finite(value) -> float | None:
    """把 NumPy 标量转为有限 Python float；无效值返回 None。"""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) else None
'''

__all__ = ["DEFAULT_VALIDATION_SOURCE"]
