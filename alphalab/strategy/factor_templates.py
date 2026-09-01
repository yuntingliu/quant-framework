"""Canonical Python templates for the built-in factor catalog."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from typing import Any, Mapping

import libcst as cst

from alphalab.strategy.source import SourceInspection, StrategySourceError, inspect_strategy_source


@dataclass(frozen=True)
class FactorTemplate:
    id: str
    label: str
    category: str
    description: str
    inputs: tuple[str, ...]
    requirements: Mapping[str, tuple[str, ...]]
    recommended_direction: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["inputs"] = list(self.inputs)
        payload["requirements"] = {
            dataset: list(fields) for dataset, fields in self.requirements.items()
        }
        return payload


def _technical(
    factor_id: str,
    label: str,
    description: str,
    inputs: tuple[str, ...],
    source: str,
    *,
    direction: str = "higher",
) -> FactorTemplate:
    return FactorTemplate(
        id=factor_id,
        label=label,
        category="technical",
        description=description,
        inputs=inputs,
        requirements={"bars": inputs},
        recommended_direction=direction,
        source=source.strip() + "\n",
    )


def _fundamental(
    factor_id: str,
    label: str,
    description: str,
    *,
    direction: str = "higher",
) -> FactorTemplate:
    return FactorTemplate(
        id=factor_id,
        label=label,
        category="fundamental",
        description=description,
        inputs=(factor_id,),
        requirements={"fundamentals": (factor_id,)},
        recommended_direction=direction,
        source=(
            f'@factor(id="{factor_id}", label="{label}")\n'
            f"def {factor_id}(context):\n"
            f'    return context.fundamental("{factor_id}")\n'
        ),
    )


FACTOR_TEMPLATES = (
    _technical(
        "momentum_20d",
        "20 日动量",
        "最近 20 个交易日的价格涨跌幅。",
        ("close",),
        """
@factor(id="momentum_20d", label="20 日动量")
def momentum_20d(context, *, window: int = 20):
    close = context.history("close", window=window + 1)
    if len(close.index) < window + 1:
        return close.mean(axis=0) * float("nan")
    return close.iloc[-1] / close.iloc[0] - 1.0
""",
    ),
    _technical(
        "momentum_60d",
        "60 日动量",
        "最近 60 个交易日的中期价格动量。",
        ("close",),
        """
@factor(id="momentum_60d", label="60 日动量")
def momentum_60d(context, *, window: int = 60):
    close = context.history("close", window=window + 1)
    if len(close.index) < window + 1:
        return close.mean(axis=0) * float("nan")
    return close.iloc[-1] / close.iloc[0] - 1.0
""",
    ),
    _technical(
        "reversal_5d",
        "5 日反转",
        "最近 5 个交易日涨跌幅，通常以低值优先使用。",
        ("close",),
        """
@factor(id="reversal_5d", label="5 日反转")
def reversal_5d(context, *, window: int = 5):
    close = context.history("close", window=window + 1)
    if len(close.index) < window + 1:
        return close.mean(axis=0) * float("nan")
    return close.iloc[-1] / close.iloc[0] - 1.0
""",
        direction="lower",
    ),
    _technical(
        "volatility_20d",
        "20 日波动率",
        "最近 20 日收益率的年化波动率，通常以低值优先使用。",
        ("close",),
        """
@factor(id="volatility_20d", label="20 日波动率")
def volatility_20d(context, *, window: int = 20, annualization_days: int = 252):
    close = context.history("close", window=window + 1)
    returns = close.pct_change().tail(window)
    return returns.std(ddof=0) * (annualization_days ** 0.5)
""",
        direction="lower",
    ),
    _technical(
        "turnover_20d",
        "20 日平均成交量",
        "最近 20 日的平均成交量，用于刻画流动性。",
        ("volume",),
        """
@factor(id="turnover_20d", label="20 日平均成交量")
def turnover_20d(context, *, window: int = 20):
    volume = context.history("volume", window=window)
    return volume.mean()
""",
    ),
    _technical(
        "liquidity_20d",
        "20 日平均成交额",
        "最近 20 日平均成交额，用于刻画可交易容量与流动性。",
        ("amount",),
        """
@factor(id="liquidity_20d", label="20 日平均成交额")
def liquidity_20d(context, *, window: int = 20):
    amount = context.history("amount", window=window)
    return amount.mean()
""",
    ),
    _technical(
        "range_volatility_20d",
        "20 日日内振幅",
        "最近 20 日高低价差相对收盘价的均值，通常以低值优先使用。",
        ("high", "low", "close"),
        """
@factor(id="range_volatility_20d", label="20 日日内振幅")
def range_volatility_20d(context, *, window: int = 20):
    high = context.history("high", window=window)
    low = context.history("low", window=window)
    close = context.history("close", window=window).replace(0.0, float("nan"))
    return ((high - low) / close).mean()
""",
        direction="lower",
    ),
    _technical(
        "volume_ratio",
        "成交量比率",
        "近 5 日平均成交量与近 20 日平均成交量之比。",
        ("volume",),
        """
@factor(id="volume_ratio", label="成交量比率")
def volume_ratio(context, *, short_window: int = 5, long_window: int = 20):
    volume = context.history("volume", window=long_window)
    baseline = volume.mean().replace(0.0, float("nan"))
    return volume.tail(short_window).mean() / baseline
""",
    ),
    _technical(
        "rsi_14",
        "14 日 RSI",
        "14 日相对强弱指标。",
        ("close",),
        """
@factor(id="rsi_14", label="14 日 RSI")
def rsi_14(context, *, window: int = 14):
    close = context.history("close", window=window + 1)
    delta = close.diff().tail(window)
    gains = delta.clip(lower=0.0).mean()
    losses = (-delta.clip(upper=0.0)).mean()
    safe_losses = losses.where(losses > 0.0, 1.0)
    score = 100.0 - 100.0 / (1.0 + gains / safe_losses)
    return score.where(losses > 0.0, 100.0)
""",
    ),
    _technical(
        "ma_deviation",
        "均线偏离",
        "收盘价相对 20 日均线的偏离程度。",
        ("close",),
        """
@factor(id="ma_deviation", label="均线偏离")
def ma_deviation(context, *, window: int = 20):
    close = context.history("close", window=window)
    if len(close.index) < window:
        return close.mean(axis=0) * float("nan")
    moving_average = close.mean().replace(0.0, float("nan"))
    return close.iloc[-1] / moving_average - 1.0
""",
    ),
    _fundamental("ep", "盈利收益率", "盈利相对估值水平。"),
    _fundamental("bp", "账面市值比", "账面价值相对市值水平。"),
    _fundamental("roe", "净资产收益率", "衡量股东权益盈利能力。"),
    _fundamental("roa", "总资产收益率", "衡量总资产盈利能力。"),
    _fundamental("profit_growth", "利润增长率", "利润同比或可比口径增长率。"),
    _fundamental("revenue_growth", "收入增长率", "营业收入增长率。"),
    _fundamental("gross_margin", "毛利率", "主营业务毛利水平。"),
    _fundamental(
        "leverage",
        "财务杠杆",
        "资产负债或权益杠杆水平，通常以低值优先使用。",
        direction="lower",
    ),
)

_BY_ID = {item.id: item for item in FACTOR_TEMPLATES}


def list_factor_templates() -> tuple[FactorTemplate, ...]:
    return FACTOR_TEMPLATES


def get_factor_template(template_id: str) -> FactorTemplate:
    try:
        return _BY_ID[str(template_id).strip()]
    except KeyError:
        raise StrategySourceError(
            f"unknown factor template {template_id!r}", phase="edit"
        ) from None


class _RequirementsTransformer(cst.CSTTransformer):
    def __init__(self, value: cst.BaseExpression) -> None:
        self.value = value
        self.changed = False

    def leave_Assign(self, original_node: cst.Assign, updated_node: cst.Assign) -> cst.Assign:
        if any(
            isinstance(target.target, cst.Name) and target.target.value == "DATA_REQUIREMENTS"
            for target in original_node.targets
        ):
            self.changed = True
            return updated_node.with_changes(value=self.value)
        return updated_node

    def leave_AnnAssign(
        self, original_node: cst.AnnAssign, updated_node: cst.AnnAssign
    ) -> cst.AnnAssign:
        if (
            isinstance(original_node.target, cst.Name)
            and original_node.target.value == "DATA_REQUIREMENTS"
        ):
            self.changed = True
            return updated_node.with_changes(value=self.value)
        return updated_node


def _has_factor_import(source: str) -> bool:
    tree = ast.parse(source)
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "alphalab.sdk.v1"
        and any(alias.name == "factor" and alias.asname in {None, "factor"} for alias in node.names)
        for node in tree.body
    )


def _assignment_name(statement: cst.BaseStatement) -> str | None:
    if not isinstance(statement, cst.SimpleStatementLine) or len(statement.body) != 1:
        return None
    item = statement.body[0]
    if isinstance(item, cst.Assign) and len(item.targets) == 1:
        target = item.targets[0].target
        return target.value if isinstance(target, cst.Name) else None
    if isinstance(item, cst.AnnAssign) and isinstance(item.target, cst.Name):
        return item.target.value
    return None


def _decorator_name(decorator: cst.Decorator) -> str | None:
    value = decorator.decorator
    target = value.func if isinstance(value, cst.Call) else value
    if isinstance(target, cst.Name):
        return target.value
    if isinstance(target, cst.Attribute):
        return target.attr.value
    return None


def _factor_copy_identity(
    template: FactorTemplate,
    *,
    occupied: set[str],
) -> tuple[str, str]:
    copy_number = 1
    factor_id = template.id
    while factor_id in occupied:
        copy_number += 1
        factor_id = f"{template.id}_{copy_number}"
    label = template.label if copy_number == 1 else f"{template.label}（副本 {copy_number}）"
    return factor_id, label


def _copy_factor_function(
    function: cst.FunctionDef,
    *,
    factor_id: str,
    label: str,
) -> cst.FunctionDef:
    decorators: list[cst.Decorator] = []
    for decorator in function.decorators:
        value = decorator.decorator
        if _decorator_name(decorator) != "factor" or not isinstance(value, cst.Call):
            decorators.append(decorator)
            continue
        arguments: list[cst.Arg] = []
        found_id = False
        found_label = False
        for argument in value.args:
            keyword = argument.keyword.value if isinstance(argument.keyword, cst.Name) else None
            if keyword == "id":
                argument = argument.with_changes(value=cst.SimpleString(repr(factor_id)))
                found_id = True
            elif keyword == "label":
                argument = argument.with_changes(value=cst.SimpleString(repr(label)))
                found_label = True
            arguments.append(argument)
        if not found_id:
            arguments.append(
                cst.Arg(value=cst.SimpleString(repr(factor_id)), keyword=cst.Name("id"))
            )
        if not found_label:
            arguments.append(
                cst.Arg(value=cst.SimpleString(repr(label)), keyword=cst.Name("label"))
            )
        decorators.append(
            decorator.with_changes(decorator=value.with_changes(args=tuple(arguments)))
        )
    return function.with_changes(
        name=cst.Name(factor_id),
        decorators=tuple(decorators),
        leading_lines=(cst.EmptyLine(), cst.EmptyLine()),
    )


def install_factor_template(source: str, *, template_id: str) -> tuple[str, SourceInspection]:
    inspection = inspect_strategy_source(source)
    template = get_factor_template(template_id)
    registered_ids = {item.id for item in inspection.entrypoints}
    tree = ast.parse(source)
    top_level_functions = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    factor_id, label = _factor_copy_identity(
        template,
        occupied=registered_ids | top_level_functions,
    )

    requirements = {
        dataset: list(fields) for dataset, fields in inspection.data_requirements.items()
    }
    for dataset, fields in template.requirements.items():
        current = requirements.setdefault(dataset, [])
        current.extend(field for field in fields if field not in current)

    module = cst.parse_module(source)
    requirement_value = cst.parse_expression(repr(requirements))
    requirement_transformer = _RequirementsTransformer(requirement_value)
    module = module.visit(requirement_transformer)
    body = list(module.body)

    if not requirement_transformer.changed:
        assignment = cst.parse_statement(f"DATA_REQUIREMENTS = {requirements!r}\n")
        sdk_index = next(
            (
                index
                for index, statement in enumerate(body)
                if _assignment_name(statement) == "SDK_VERSION"
            ),
            -1,
        )
        body.insert(sdk_index + 1, assignment)

    if not _has_factor_import(source):
        factor_import = cst.parse_statement("from alphalab.sdk.v1 import factor\n")
        import_indexes = [
            index
            for index, statement in enumerate(body)
            if isinstance(statement, cst.SimpleStatementLine)
            and statement.body
            and isinstance(statement.body[0], (cst.Import, cst.ImportFrom))
        ]
        body.insert((import_indexes[-1] + 1) if import_indexes else 0, factor_import)

    template_module = cst.parse_module(template.source)
    function = next(
        (item for item in template_module.body if isinstance(item, cst.FunctionDef)), None
    )
    if function is None:
        raise StrategySourceError("factor template is invalid", phase="edit")
    function = _copy_factor_function(function, factor_id=factor_id, label=label)
    signal_index = next(
        (
            index
            for index, statement in enumerate(body)
            if isinstance(statement, cst.FunctionDef)
            and any(_decorator_name(item) == "signal" for item in statement.decorators)
        ),
        len(body),
    )
    body.insert(signal_index, function)
    updated = module.with_changes(body=tuple(body)).code
    return updated, inspect_strategy_source(updated)


__all__ = [
    "FACTOR_TEMPLATES",
    "FactorTemplate",
    "get_factor_template",
    "install_factor_template",
    "list_factor_templates",
]
