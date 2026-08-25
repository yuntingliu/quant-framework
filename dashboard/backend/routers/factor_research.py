"""Professional factor-definition and cross-sectional evaluation endpoints."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from alphalab.analytics import evaluate_factor
from alphalab.dataio import MissingDataError
from alphalab.factors import list_factors
from alphalab.factors.repository import FactorDefinitionRepository
from alphalab.strategy import FactorSpec, UniverseSpec
from dashboard.backend.services.data_service import _engine, research_dataset_schema

router = APIRouter(prefix="/api/factor-research", tags=["factor-research"])


class FactorEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Literal["demo", "runtime"] = "demo"
    name: str = Field(min_length=1, max_length=80)
    source: Literal["technical", "fundamental", "expression"] = "technical"
    expression: str | None = Field(default=None, max_length=500)
    direction: Literal["long", "short"] = "long"
    winsorize: float = Field(default=0.01, ge=0, lt=0.25)
    neutralize: list[Literal["market_cap"]] = Field(default_factory=list)
    start_date: str
    end_date: str
    frequency: Literal["monthly", "weekly"] = "monthly"
    quantiles: int = Field(default=5, ge=3, le=10)
    symbols: list[str] | None = Field(default=None, max_length=2_000)
    min_price: float = Field(default=0.0, ge=0)
    min_history_days: int = Field(default=60, ge=2, le=2_000)
    min_average_amount: float = Field(default=0.0, ge=0)


class CustomFactorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", max_length=240)
    expression: str = Field(min_length=1, max_length=500)
    direction: Literal["long", "short"] = "long"
    winsorize: float = Field(default=0.01, ge=0, lt=0.25)
    neutralize: list[Literal["market_cap"]] = Field(default_factory=list)


_FACTOR_INPUT_FIELDS = {
    "momentum_20d": ["close"],
    "momentum_60d": ["close"],
    "reversal_5d": ["close"],
    "volatility_20d": ["close"],
    "turnover_20d": ["volume"],
    "volume_ratio": ["volume"],
    "rsi_14": ["close"],
    "ma_deviation": ["close"],
}

_DATA_FIELD_LABELS = {
    "date": "交易日期",
    "symbol": "证券代码",
    "quarter": "报告期",
    "available_date": "可用日期",
    "open": "开盘价",
    "high": "最高价",
    "low": "最低价",
    "close": "收盘价",
    "volume": "成交量",
    "amount": "成交额",
    "raw_close": "不复权收盘价",
    "shares": "总股本",
    "market_cap": "总市值",
    "ep": "盈利收益率",
    "bp": "账面市值比",
    "roe": "净资产收益率",
    "roa": "总资产收益率",
    "profit_growth": "利润增长率",
    "revenue_growth": "收入增长率",
    "gross_margin": "毛利率",
    "leverage": "财务杠杆",
}

_RESEARCH_SOURCE_SPECS = [
    {
        "id": "market_bars",
        "name": "日频 K 线",
        "endpoint": "/api/data/market/bars",
        "frequency": "daily",
        "point_in_time": True,
    },
    {
        "id": "fundamentals",
        "name": "季度财务",
        "endpoint": "/api/data/fundamentals",
        "frequency": "quarterly",
        "point_in_time": True,
    },
]


def factor_repository() -> FactorDefinitionRepository:
    return FactorDefinitionRepository()


def _custom_factor_rows() -> list[dict]:
    repository = factor_repository()
    try:
        return [
            {
                **item,
                "source": "expression",
                "description": item["description"] or "自定义表达式因子",
                "frequency": "daily",
                "point_in_time": True,
                "custom": True,
            }
            for item in repository.list()
        ]
    finally:
        repository.close()


def _research_data_sources(profile: str) -> list[dict]:
    sources: list[dict] = []
    for spec in _RESEARCH_SOURCE_SPECS:
        fields = research_dataset_schema(profile, spec["id"])
        sources.append(
            {
                **spec,
                "profile": profile,
                "schema_source": "parquet_metadata",
                "fields": [
                    {
                        "name": field.name,
                        "label": _DATA_FIELD_LABELS.get(
                            field.name,
                            field.name.replace("_", " "),
                        ),
                        "data_type": field.data_type,
                        "nullable": field.nullable,
                        "expression_compatible": field.data_type == "number",
                    }
                    for field in fields
                ],
            }
        )
    return sources


@router.get("/library")
def factor_library(profile: Literal["demo", "runtime"] = "demo") -> dict:
    rows = [
        {
            "name": factor.name,
            "source": factor.kind,
            "description": factor.description,
            "input_fields": _FACTOR_INPUT_FIELDS.get(factor.name, [factor.name]),
            "frequency": "daily" if factor.kind == "technical" else "quarterly",
            "point_in_time": True,
            "custom": False,
        }
        for factor in list_factors()
    ]
    rows.extend(_custom_factor_rows())
    return {
        "factors": rows,
        "data_sources": _research_data_sources(profile),
        "expression_functions": ["abs", "clip", "log", "rank", "sqrt", "zscore"],
        "neutralizers": ["market_cap"],
        "packs": [
            {
                "id": "microsoft-qlib-alpha158",
                "name": "Microsoft Qlib Alpha158",
                "description": "158 个工程化量价、K 线与滚动统计特征模板",
                "feature_count": 158,
                "license": "MIT",
                "source_url": "https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/loader.py",
                "status": "adapter_required",
            },
            {
                "id": "microsoft-qlib-alpha360",
                "name": "Microsoft Qlib Alpha360",
                "description": "六组价格/成交量字段最近 60 日的 360 维归一化特征",
                "feature_count": 360,
                "license": "MIT",
                "source_url": "https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/loader.py",
                "status": "adapter_required",
            },
        ],
    }


@router.put("/factors/{factor_name}")
def save_custom_factor(factor_name: str, request: CustomFactorRequest) -> dict:
    repository = factor_repository()
    try:
        item = repository.save(
            factor_name,
            description=request.description,
            expression=request.expression,
            direction=request.direction,
            winsorize=request.winsorize,
            neutralize=request.neutralize,
            reserved_names=(factor.name for factor in list_factors()),
        )
        return {
            **item,
            "source": "expression",
            "description": item["description"] or "自定义表达式因子",
            "frequency": "daily",
            "point_in_time": True,
            "custom": True,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        repository.close()


@router.post("/evaluate")
def evaluate(request: FactorEvaluationRequest) -> dict:
    try:
        factor = FactorSpec(
            name=request.name,
            weight=1.0,
            source=request.source,
            expression=request.expression,
            direction=request.direction,
            winsorize=request.winsorize,
            neutralize=tuple(request.neutralize),
        )
        universe = UniverseSpec(
            symbols=tuple(request.symbols or ()),
            min_price=request.min_price,
            min_history_days=request.min_history_days,
            min_average_amount=request.min_average_amount,
        )
        return evaluate_factor(
            _engine(request.profile),
            factor,
            request.start_date,
            request.end_date,
            universe=universe,
            frequency=request.frequency,
            quantiles=request.quantiles,
        )
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


__all__ = ["router"]
