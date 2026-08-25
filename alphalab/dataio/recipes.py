"""Source templates, CST projection, and trusted-local data recipe execution."""

from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import multiprocessing
import re
import time
import traceback
import types
from dataclasses import asdict, dataclass, replace
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Callable, Mapping

import libcst as cst

from alphalab.data_sdk.v1.recipe import DataRecipeContext, RQSyncRequest
from alphalab.dataio.rq_templates import get_rq_sync_template, list_rq_sync_templates

RQDATA_PYTHON_DOCS_URL = "https://www.ricequant.com/doc/rqdata/python/index-rqdatac"
MAX_RECIPE_SOURCE_BYTES = 300_000
MAX_RECIPE_LOG_CHARS = 20_000


class DataRecipeError(ValueError):
    def __init__(self, message: str, *, phase: str, traceback_text: str | None = None) -> None:
        super().__init__(message)
        self.phase = phase
        self.traceback_text = traceback_text


@dataclass(frozen=True)
class RecipeParameter:
    name: str
    default: Any
    editable: bool
    custom_source: str | None


@dataclass(frozen=True)
class RecipeInspection:
    valid: bool
    recipe_id: str
    function: str
    label: str | None
    source_sha256: str
    source_bytes: int
    parameters: tuple[RecipeParameter, ...]
    template_id: str | None
    matched_template_id: str | None
    warnings: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["parameters"] = [asdict(item) for item in self.parameters]
        value["warnings"] = list(self.warnings)
        return value


def render_builtin_recipe(
    template_id: str,
    *,
    start: str,
    end: str,
    symbols: list[str] | tuple[str, ...] | None = None,
) -> str:
    source = _render_builtin_recipe_base(
        template_id,
        start=start,
        end=end,
        symbols=symbols,
    )
    return _add_builtin_recipe_comments(source)


def _render_builtin_recipe_base(
    template_id: str,
    *,
    start: str,
    end: str,
    symbols: list[str] | tuple[str, ...] | None = None,
) -> str:
    template = get_rq_sync_template(template_id)
    symbol_literal = repr(tuple(symbols)) if symbols else "None"
    research = "fundamentals" in template.datasets
    research_imports = (
        "    BALANCE_FIELDS,\n"
        "    INCOME_FIELDS,\n"
        "    build_canonical_fundamentals,\n"
        "    build_factor_returns,\n"
        "    normalize_rq_financials,\n"
        "    normalize_rq_yield_curve,\n"
        if research
        else ""
    )
    research_plan = (
        """        context.expect("rq.financials.income", "rq.get_pit_financials_ex")
        context.expect("rq.financials.balance", "rq.get_pit_financials_ex")
        context.expect("canonical.fundamentals", "build_canonical_fundamentals")
        context.expect("runtime.factor_returns", "rq.get_yield_curve + build_factor_returns")
"""
        if research
        else ""
    )
    quarter_helper = (
        """
def _quarter(value):
    timestamp = pd.Timestamp(value)
    return f"{timestamp.year}q{timestamp.quarter}"
"""
        if research
        else ""
    )
    research_run = (
        """
    start_quarter = _quarter(start)
    end_quarter = _quarter(end)
    for batch in _batches(order_book_ids, 200):
        raw_income = rq.get_pit_financials_ex(
            order_book_ids=batch,
            fields=list(INCOME_FIELDS),
            start_quarter=start_quarter,
            end_quarter=end_quarter,
            statements="all",
            market=MARKET,
        )
        income = normalize_rq_financials(raw_income, INCOME_FIELDS)
        if not income.empty:
            context.publish("rq.financials.income", income)

        raw_balance = rq.get_pit_financials_ex(
            order_book_ids=batch,
            fields=list(BALANCE_FIELDS),
            start_quarter=start_quarter,
            end_quarter=end_quarter,
            statements="all",
            market=MARKET,
        )
        balance = normalize_rq_financials(raw_balance, BALANCE_FIELDS)
        if not balance.empty:
            context.publish("rq.financials.balance", balance)

    bars = context.read("rq.bars")
    fundamentals = build_canonical_fundamentals(
        context.read("rq.financials.income"),
        context.read("rq.financials.balance"),
        bars,
        asof_date=end,
    )
    context.publish("canonical.fundamentals", fundamentals)

    raw_yield_curve = rq.get_yield_curve(
        start_date=start,
        end_date=end,
        tenor="1M",
        market=MARKET,
    )
    risk_free = normalize_rq_yield_curve(raw_yield_curve, tenor="1M")
    factor_returns = build_factor_returns(bars, fundamentals, risk_free)
    context.publish("runtime.factor_returns", factor_returns)
"""
        if research
        else ""
    )
    return f"""import pandas as pd

from alphalab.data_sdk.v1 import (
{research_imports}    data_recipe,
    framework_symbols,
    normalize_rq_bars,
    normalize_rq_instruments,
    rq,
    rq_order_book_ids,
)


ASSET_TYPES = {template.instrument_types!r}
MARKET = {template.market!r}


def _batches(values, size):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]
{quarter_helper}


@data_recipe(id="research_data", label={template.label!r}, template={template.id!r})
def research_data(
    context,
    *,
    start: str = {start!r},
    end: str = {end!r},
    symbols: tuple[str, ...] | None = {symbol_literal},
):
    if context.mode == "plan":
        context.expect("rq.instruments", "rq.all_instruments", asset_types=ASSET_TYPES)
        context.expect("rq.bars", "rq.get_price", start=start, end=end)
{research_plan}        return

    instrument_frames = []
    for asset_type in ASSET_TYPES:
        raw_instruments = rq.all_instruments(type=asset_type, market=MARKET)
        normalized = normalize_rq_instruments(
            raw_instruments,
            snapshot_date=end,
            asset_type=asset_type,
        )
        if not normalized.empty:
            instrument_frames.append(normalized)
    if not instrument_frames:
        raise ValueError("RQData returned no instruments")

    instruments = pd.concat(instrument_frames, ignore_index=True)
    listed = pd.to_datetime(instruments["listed_date"], errors="coerce")
    delisted = pd.to_datetime(instruments["de_listed_date"], errors="coerce")
    active = instruments.loc[
        (listed.isna() | listed.le(pd.Timestamp(end)))
        & (delisted.isna() | delisted.ge(pd.Timestamp(start)))
    ].copy()
    if symbols:
        requested = set(framework_symbols(symbols))
        active = active.loc[active["symbol"].isin(requested)]
    if active.empty:
        raise ValueError("RQData returned no active instruments for this recipe")

    context.publish("rq.instruments", active)
    order_book_ids = rq_order_book_ids(active["symbol"].tolist())
    context.output("symbol_count", len(order_book_ids))

    published_bar_batches = 0
    for batch in _batches(order_book_ids, 200):
        adjusted_raw = rq.get_price(
            batch,
            start_date=start,
            end_date=end,
            frequency="1d",
            fields=None,
            adjust_type="pre",
            expect_df=True,
            market=MARKET,
        )
        raw_close = rq.get_price(
            batch,
            start_date=start,
            end_date=end,
            frequency="1d",
            fields=["close"],
            adjust_type="none",
            expect_df=True,
            market=MARKET,
        )
        bars = normalize_rq_bars(adjusted_raw, raw_close)
        if not bars.empty:
            context.publish("rq.bars", bars)
            published_bar_batches += 1
    if published_bar_batches == 0:
        raise ValueError("RQData returned no daily bars")
{research_run}"""


def _add_builtin_recipe_comments(source: str) -> str:
    """Add explanatory comments without changing the executable template AST."""

    replacements = (
        (
            "import pandas as pd\n",
            "# 本文件就是同步时执行的完整 Python；下方 rq.* 调用不会被后端模板替换。\n"
            "# rq 透明代理本机已配置的 rqdatac，context 负责预览、校验和统一落库。\n\n"
            "import pandas as pd\n",
        ),
        (
            "ASSET_TYPES = ",
            "# 模板只提供默认市场范围；可以直接修改这些常量或下面的查询逻辑。\n" "ASSET_TYPES = ",
        ),
        (
            "def _batches(values, size):\n",
            "# RQData 大批量查询按标的拆分，避免单次请求过大。\n" "def _batches(values, size):\n",
        ),
        (
            '@data_recipe(id="research_data"',
            "# template 参数只用于界面识别当前内置模板，不参与运行时分发。\n"
            '@data_recipe(id="research_data"',
        ),
        (
            '    if context.mode == "plan":\n',
            "    # 预览只声明将执行的步骤，不访问 RQData，也不会写入数据。\n"
            '    if context.mode == "plan":\n',
        ),
        (
            "    instrument_frames = []\n",
            "    # 1. 直接调用 rq.all_instruments 获取模板范围内的标的信息。\n"
            "    instrument_frames = []\n",
        ),
        (
            "        normalized = normalize_rq_instruments(\n",
            "        # 将 RQData 原始字段转换为研究仓库统一的 instruments 契约。\n"
            "        normalized = normalize_rq_instruments(\n",
        ),
        (
            "    instruments = pd.concat(instrument_frames, ignore_index=True)\n",
            "    # 2. 按上市区间过滤，避免把尚未上市或已经退市的标的加入研究范围。\n"
            "    instruments = pd.concat(instrument_frames, ignore_index=True)\n",
        ),
        (
            '    context.publish("rq.instruments", active)\n',
            "    # 发布边界负责主键检查和落库；不会改写上面的选取逻辑。\n"
            '    context.publish("rq.instruments", active)\n',
        ),
        (
            "    published_bar_batches = 0\n",
            "    # 3. 分批查询日线。前复权价格用于研究，未复权收盘价用于估值口径。\n"
            "    published_bar_batches = 0\n",
        ),
        (
            "        adjusted_raw = rq.get_price(\n",
            "        # 前复权全字段日线：open/high/low/close/volume/amount 等。\n"
            "        adjusted_raw = rq.get_price(\n",
        ),
        (
            "        raw_close = rq.get_price(\n",
            "        # 单独读取未复权 close，保存为统一契约中的 raw_close。\n"
            "        raw_close = rq.get_price(\n",
        ),
        (
            "        bars = normalize_rq_bars(adjusted_raw, raw_close)\n",
            "        # 只做字段名、代码格式和成交量单位转换，然后写入同一数据仓库。\n"
            "        bars = normalize_rq_bars(adjusted_raw, raw_close)\n",
        ),
        (
            "    start_quarter = _quarter(start)\n",
            "    # 4. 完整研究模板继续直接查询 PIT 财务报表。\n"
            "    start_quarter = _quarter(start)\n",
        ),
        (
            "        raw_income = rq.get_pit_financials_ex(\n",
            "        # 利润表：保留披露日期与调整标记，供后续 PIT 处理。\n"
            "        raw_income = rq.get_pit_financials_ex(\n",
        ),
        (
            "        raw_balance = rq.get_pit_financials_ex(\n",
            "        # 资产负债表：与利润表采用相同的季度和标的批次。\n"
            "        raw_balance = rq.get_pit_financials_ex(\n",
        ),
        (
            '    bars = context.read("rq.bars")\n',
            "    # 5. 从已经落库的原始数据构建无未来函数的标准基本面。\n"
            '    bars = context.read("rq.bars")\n',
        ),
        (
            "    raw_yield_curve = rq.get_yield_curve(\n",
            "    # 6. 直接查询无风险利率并生成统一的月度因子收益。\n"
            "    raw_yield_curve = rq.get_yield_curve(\n",
        ),
    )
    for original, commented in replacements:
        source = source.replace(original, commented, 1)
    return source


def builtin_recipe_catalog(*, start: str, end: str) -> list[dict[str, Any]]:
    return [
        {
            **item.to_dict(),
            "kind": "built_in",
            "source": render_builtin_recipe(item.id, start=start, end=end),
        }
        for item in list_rq_sync_templates()
    ]


def migrate_legacy_builtin_recipe(source: str) -> str:
    """Upgrade an untouched request-only built-in draft to executable RQ code."""

    inspection = inspect_data_recipe_source(source)
    if not inspection.template_id:
        return source
    parameters = {item.name: item.default for item in inspection.parameters if item.editable}
    start, end = parameters.get("start"), parameters.get("end")
    symbols = parameters.get("symbols")
    if not isinstance(start, str) or not isinstance(end, str):
        return source
    symbol_values = list(symbols) if isinstance(symbols, (list, tuple)) else None
    legacy = _render_legacy_builtin_recipe(
        inspection.template_id,
        start=start,
        end=end,
        symbols=symbol_values,
    )
    uncommented = _render_builtin_recipe_base(
        inspection.template_id,
        start=start,
        end=end,
        symbols=symbol_values,
    )
    if source not in {legacy, uncommented}:
        return source
    return render_builtin_recipe(
        inspection.template_id,
        start=start,
        end=end,
        symbols=symbol_values,
    )


def _render_legacy_builtin_recipe(
    template_id: str,
    *,
    start: str,
    end: str,
    symbols: list[str] | None,
) -> str:
    template = get_rq_sync_template(template_id)
    symbol_literal = repr(tuple(symbols)) if symbols else "None"
    return f"""from alphalab.data_sdk.v1 import RQSyncRequest, data_recipe, rq


@data_recipe(id="research_data", label={template.label!r})
def research_data(
    context,
    *,
    start: str = {start!r},
    end: str = {end!r},
    symbols: tuple[str, ...] | None = {symbol_literal},
):
    # rq 透明代理当前安装的 rqdatac，可在自定义配方中调用任意公开查询 API。
    return RQSyncRequest(
        template_id={template.id!r},
        datasets={template.datasets!r},
        start=start,
        end=end,
        symbols=symbols,
    )
"""


def inspect_data_recipe_source(source: str) -> RecipeInspection:
    encoded = source.encode("utf-8")
    if not source.strip():
        raise DataRecipeError("data recipe source must not be empty", phase="parse")
    if len(encoded) > MAX_RECIPE_SOURCE_BYTES:
        raise DataRecipeError("data recipe source is too large", phase="parse")
    try:
        tree = ast.parse(source, filename="<alphalab-data-recipe>")
        compile(tree, "<alphalab-data-recipe>", "exec")
    except (SyntaxError, ValueError) as exc:
        raise DataRecipeError(str(exc), phase="parse") from exc
    registrations: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.Call]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and _call_name(decorator.func) == "data_recipe":
                registrations.append((node, decorator))
    if len(registrations) != 1:
        raise DataRecipeError(
            "source must register exactly one @data_recipe function", phase="register"
        )
    node, decorator = registrations[0]
    if isinstance(node, ast.AsyncFunctionDef):
        raise DataRecipeError("@data_recipe must be a synchronous function", phase="register")
    if node.args.vararg or node.args.kwarg or node.args.posonlyargs or len(node.args.args) != 1:
        raise DataRecipeError(
            "@data_recipe requires one context argument and keyword-only parameters",
            phase="register",
        )
    recipe_id = _literal_keyword(decorator, "id")
    label = _literal_keyword(decorator, "label")
    if not isinstance(recipe_id, str) or not recipe_id:
        raise DataRecipeError("@data_recipe requires a literal id", phase="register")
    if label is not None and not isinstance(label, str):
        raise DataRecipeError("@data_recipe label must be a string", phase="register")
    parameters: list[RecipeParameter] = []
    for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        if default is None:
            parameters.append(RecipeParameter(argument.arg, None, False, None))
            continue
        default_source = ast.get_source_segment(source, default) or ast.unparse(default)
        try:
            value = ast.literal_eval(default)
            editable = _supported_literal(value)
        except (ValueError, TypeError):
            value = None
            editable = False
        parameters.append(
            RecipeParameter(
                argument.arg,
                value,
                editable,
                None if editable else default_source,
            )
        )
    template_id = _literal_keyword(decorator, "template") or _sync_template_id(node)
    if template_id is not None and not isinstance(template_id, str):
        raise DataRecipeError("@data_recipe template must be a string", phase="register")
    warnings = tuple(_capability_warnings(tree))
    digest = hashlib.sha256(encoded).hexdigest()
    inspection = RecipeInspection(
        valid=True,
        recipe_id=recipe_id,
        function=node.name,
        label=label,
        source_sha256=digest,
        source_bytes=len(encoded),
        parameters=tuple(parameters),
        template_id=template_id,
        matched_template_id=None,
        warnings=warnings,
    )
    matched = _match_builtin(source, inspection)
    return replace(inspection, matched_template_id=matched)


def update_recipe_parameters(
    source: str, values: Mapping[str, Any]
) -> tuple[str, RecipeInspection]:
    inspection = inspect_data_recipe_source(source)
    supported = {item.name for item in inspection.parameters if item.editable}
    unknown = sorted(set(values) - supported)
    if unknown:
        raise DataRecipeError(f"parameters are not form-editable: {unknown}", phase="edit")
    module = cst.parse_module(source)
    transformer = _RecipeParameterTransformer(
        inspection.function,
        {name: cst.parse_expression(repr(value)) for name, value in values.items()},
    )
    updated = module.visit(transformer).code
    if set(values) - transformer.changed:
        raise DataRecipeError("recipe parameters changed while editing", phase="edit")
    return updated, inspect_data_recipe_source(updated)


def execute_data_recipe(
    source: str,
    *,
    mode: str,
    root: str | Path | None = None,
    timeout_seconds: float = 1800.0,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    inspection = inspect_data_recipe_source(source)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_recipe_worker,
        args=(child, source, mode, str(root) if root is not None else None),
        daemon=True,
        name="alphalab-data-recipe-v1",
    )
    process.start()
    child.close()
    deadline = time.monotonic() + timeout_seconds
    while not parent.poll(min(0.25, max(0.0, deadline - time.monotonic()))):
        if cancelled is not None and cancelled():
            _stop_process(process)
            parent.close()
            raise DataRecipeError("data recipe was cancelled", phase="cancel")
        if time.monotonic() >= deadline:
            _stop_process(process)
            parent.close()
            raise DataRecipeError(
                f"data recipe exceeded the {timeout_seconds:g}s timeout",
                phase="execute",
            )
    try:
        response = parent.recv()
    except (EOFError, OSError) as exc:
        raise DataRecipeError(
            f"data recipe worker exited unexpectedly (code={process.exitcode})",
            phase="execute",
        ) from exc
    finally:
        parent.close()
        process.join(timeout=2.0)
        if process.is_alive():
            process.terminate()
    if not isinstance(response, dict):
        raise DataRecipeError("data recipe worker protocol error", phase="output")
    if not response.get("ok"):
        error = dict(response.get("error") or {})
        raise DataRecipeError(
            str(error.get("message") or "data recipe failed"),
            phase=str(error.get("phase") or "execute"),
            traceback_text=error.get("traceback"),
        )
    return {
        **dict(response.get("value") or {}),
        "source_sha256": inspection.source_sha256,
        "stdout": str(response.get("stdout") or ""),
        "stderr": str(response.get("stderr") or ""),
    }


def probe_data_recipe(source: str, *, mode: str = "plan", root: str | Path | None = None) -> dict:
    """Execute in-process for tests and controlled callers that cannot spawn."""

    inspect_data_recipe_source(source)
    return _execute_loaded_recipe(source, mode=mode, root=str(root) if root is not None else None)


def _execute_loaded_recipe(source: str, *, mode: str, root: str | None) -> dict[str, Any]:
    module = types.ModuleType("alphalab_user_data_recipe")
    module.__file__ = "<alphalab-data-recipe>"
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    functions = [
        value
        for value in module.__dict__.values()
        if callable(value) and hasattr(value, "__alphalab_data_recipe__")
    ]
    if len(functions) != 1:
        raise DataRecipeError("runtime requires exactly one data recipe", phase="register")
    context = DataRecipeContext(mode=mode, root=root)
    returned = functions[0](context)
    sync_request: dict[str, Any] | None
    if isinstance(returned, RQSyncRequest):
        sync_request = returned.to_dict()
    elif returned is None:
        sync_request = None
    else:
        raise DataRecipeError(
            "data recipe must return RQSyncRequest or None after context.publish()",
            phase="output",
        )
    context.finalize()
    result = context.result()
    if (
        sync_request is None
        and not result["planned"]
        and not result["published"]
        and not result["outputs"]
    ):
        raise DataRecipeError(
            "data recipe produced no synchronization request or output", phase="output"
        )
    return {**result, "sync_request": sync_request}


def _recipe_worker(connection: Connection, source: str, mode: str, root: str | None) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            value = _execute_loaded_recipe(source, mode=mode, root=root)
        connection.send(
            {
                "ok": True,
                "value": value,
                "stdout": _bounded(stdout.getvalue()),
                "stderr": _bounded(stderr.getvalue()),
            }
        )
    except Exception as exc:
        phase = exc.phase if isinstance(exc, DataRecipeError) else "execute"
        connection.send(
            {
                "ok": False,
                "error": {
                    "message": f"{type(exc).__name__}: {exc}",
                    "phase": phase,
                    "traceback": _bounded(traceback.format_exc()),
                },
                "stdout": _bounded(stdout.getvalue()),
                "stderr": _bounded(stderr.getvalue()),
            }
        )
    finally:
        connection.close()


class _RecipeParameterTransformer(cst.CSTTransformer):
    def __init__(self, function: str, values: Mapping[str, cst.BaseExpression]) -> None:
        self.function = function
        self.values = dict(values)
        self.changed: set[str] = set()

    def leave_FunctionDef(
        self,
        original_node: cst.FunctionDef,
        updated_node: cst.FunctionDef,
    ) -> cst.FunctionDef:
        if original_node.name.value != self.function:
            return updated_node
        parameters = []
        for item in updated_node.params.kwonly_params:
            value = self.values.get(item.name.value)
            if value is not None:
                if item.default is None:
                    raise DataRecipeError(
                        f"parameter {item.name.value} has no editable default",
                        phase="edit",
                    )
                item = item.with_changes(default=value)
                self.changed.add(item.name.value)
            parameters.append(item)
        return updated_node.with_changes(
            params=updated_node.params.with_changes(kwonly_params=tuple(parameters))
        )


def _call_name(value: ast.expr) -> str | None:
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return None


def _literal_keyword(call: ast.Call, name: str) -> Any:
    value = next((item.value for item in call.keywords if item.arg == name), None)
    if value is None:
        return None
    try:
        return ast.literal_eval(value)
    except (ValueError, TypeError):
        return None


def _supported_literal(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    return isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value)


def _sync_template_id(node: ast.FunctionDef) -> str | None:
    for value in ast.walk(node):
        if not isinstance(value, ast.Call) or _call_name(value.func) != "RQSyncRequest":
            continue
        template_id = _literal_keyword(value, "template_id")
        return template_id if isinstance(template_id, str) else None
    return None


def _match_builtin(source: str, inspection: RecipeInspection) -> str | None:
    if not inspection.template_id:
        return None
    parameters = {item.name: item.default for item in inspection.parameters if item.editable}
    start, end = parameters.get("start"), parameters.get("end")
    symbols = parameters.get("symbols")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        expected = render_builtin_recipe(
            inspection.template_id,
            start=start,
            end=end,
            symbols=list(symbols) if isinstance(symbols, (list, tuple)) else None,
        )
    except Exception:
        return None
    return inspection.template_id if source == expected else None


def _capability_warnings(tree: ast.Module) -> list[dict[str, Any]]:
    sensitive = {"os", "pathlib", "shutil", "socket", "subprocess"}
    warnings: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        names: set[str] = set()
        if isinstance(node, ast.Import):
            names = {item.name.split(".", 1)[0] for item in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = {node.module.split(".", 1)[0]}
        found = sorted(names & sensitive)
        if found:
            warnings.append(
                {
                    "line": getattr(node, "lineno", 0),
                    "code": "capability-sensitive-import",
                    "message": f"trusted local recipe imports: {', '.join(found)}",
                }
            )
        if (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {target.id.lower() for target in targets if isinstance(target, ast.Name)}
            if names & {"password", "token", "secret", "api_key", "apikey"}:
                warnings.append(
                    {
                        "line": getattr(node, "lineno", 0),
                        "code": "possible-inline-secret",
                        "message": "keep credentials in the local environment, not recipe source",
                    }
                )
    return warnings


def _bounded(value: str) -> str:
    sanitized = re.sub(
        r"(?i)\b(password|token|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+",
        r"\1=***",
        value,
    )
    return (
        sanitized
        if len(sanitized) <= MAX_RECIPE_LOG_CHARS
        else sanitized[:MAX_RECIPE_LOG_CHARS] + "\n... output truncated"
    )


def _stop_process(process: multiprocessing.Process) -> None:
    process.terminate()
    process.join(timeout=2.0)
    if process.is_alive():
        process.kill()


__all__ = [
    "DataRecipeError",
    "RQDATA_PYTHON_DOCS_URL",
    "RecipeInspection",
    "builtin_recipe_catalog",
    "execute_data_recipe",
    "inspect_data_recipe_source",
    "migrate_legacy_builtin_recipe",
    "probe_data_recipe",
    "render_builtin_recipe",
    "update_recipe_parameters",
]
