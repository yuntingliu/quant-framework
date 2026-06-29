"""Application service around the barebone framework."""
from __future__ import annotations

from pathlib import Path

from alphalab import ResultStore, StrategyConfig, create_default_engine, run_backtest
from alphalab.analytics import PerformanceMetrics
from alphalab.strategies import list_strategy_files


def list_provider_status() -> dict:
    engine = create_default_engine()
    return {
        "providers": engine.providers(),
        "latest_date": engine.get_latest_date(),
    }


def list_strategy_templates() -> list[dict]:
    rows = []
    for path in list_strategy_files():
        cfg = StrategyConfig.from_yaml(path)
        rows.append(
            {
                "id": cfg.name,
                "name": cfg.name,
                "description": cfg.description,
                "path": str(path),
                "factors": cfg.factor_names,
                "warnings": cfg.validate(),
            }
        )
    return rows


def get_strategy_template(strategy_id: str) -> dict | None:
    for item in list_strategy_templates():
        if item["id"] == strategy_id:
            path = Path(item["path"])
            item["yaml"] = path.read_text(encoding="utf-8")
            return item
    return None


def run_strategy_backtest(strategy_id: str, start_date: str, end_date: str) -> dict:
    strategy = get_strategy_template(strategy_id)
    if strategy is None:
        raise KeyError(strategy_id)
    cfg = StrategyConfig.from_yaml(strategy["path"])
    returns, weights = run_backtest(cfg, start_date, end_date)
    metrics = PerformanceMetrics.summarize(returns)
    store = ResultStore()
    try:
        store.register_strategy(cfg.name, strategy["path"], cfg.description)
        backtest_id = store.save_backtest(
            cfg.to_yaml(),
            returns,
            metrics,
            strategy_id=cfg.name,
            weights=weights,
            start_date=start_date,
            end_date=end_date,
        )
    finally:
        store.close()
    return {
        "id": backtest_id,
        "strategy_id": cfg.name,
        "metrics": metrics,
        "returns": [
            {"date": str(date)[:10], "value": float(value)}
            for date, value in returns.items()
        ],
        "weights_count": int((weights.abs() > 0).sum().sum()) if not weights.empty else 0,
    }


def list_backtests(limit: int = 20) -> list[dict]:
    store = ResultStore()
    try:
        df = store.list_backtests(limit=limit)
    finally:
        store.close()
    if df.empty:
        return []
    return df.fillna("").to_dict("records")


def store_stats() -> dict:
    store = ResultStore()
    try:
        return store.stats()
    finally:
        store.close()
