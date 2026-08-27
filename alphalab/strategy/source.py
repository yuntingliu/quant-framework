"""Inspection and concrete-syntax edits for canonical SDK v1 strategy modules."""

from __future__ import annotations

import ast
import hashlib
import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable

import libcst as cst
from packaging.specifiers import InvalidSpecifier, SpecifierSet

MAX_STRATEGY_SOURCE_BYTES = 300_000
VALIDATOR_VERSION = "sdk-v1-validator-1"
_KINDS = {"universe", "factor", "schedule", "signal", "portfolio", "execution"}
_REQUIRED = {"universe": 1, "signal": 1, "portfolio": 1, "execution": 1}
_EXPECTED_POSITIONAL = {
    "universe": 1,
    "factor": 1,
    "signal": 2,
    "portfolio": 3,
    "event": 2,
    "execution": 2,
    "schedule": 2,
}


class StrategySourceError(ValueError):
    """A source-contract error with an explicit validation phase."""

    def __init__(self, message: str, *, phase: str = "parse") -> None:
        super().__init__(message)
        self.phase = phase


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    annotation: str | None
    default: Any
    editable: bool
    custom_source: str | None = None
    label: str | None = None
    description: str | None = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    step: float | int | None = None


@dataclass(frozen=True)
class EntrypointSpec:
    kind: str
    id: str
    function: str
    label: str | None
    event: str | None
    metadata: dict[str, Any]
    parameters: tuple[ParameterSpec, ...]
    line: int


@dataclass(frozen=True)
class SourceInspection:
    valid: bool
    sdk_version: int
    source_sha256: str
    source_bytes: int
    validator_version: str
    entrypoints: tuple[EntrypointSpec, ...]
    data_requirements: dict[str, Any]
    runtime_requirements: dict[str, Any]
    warnings: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entrypoints"] = [asdict(item) for item in self.entrypoints]
        payload["warnings"] = [dict(item) for item in self.warnings]
        return payload


@dataclass(frozen=True)
class StrategySourceUnit:
    path: str
    kind: str
    source: str
    source_sha256: str
    position: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_strategy_source(source: str) -> SourceInspection:
    encoded = source.encode("utf-8")
    if not source.strip():
        raise StrategySourceError("strategy source must not be empty")
    if len(encoded) > MAX_STRATEGY_SOURCE_BYTES:
        raise StrategySourceError(
            f"strategy source must not exceed {MAX_STRATEGY_SOURCE_BYTES} bytes"
        )
    try:
        tree = ast.parse(source, filename="<alphalab-strategy-sdk-v1>")
    except SyntaxError as exc:
        raise StrategySourceError(
            f"Python syntax error at line {exc.lineno}: {exc.msg}", phase="parse"
        ) from exc

    sdk_version = _literal_assignment(tree, "SDK_VERSION")
    if sdk_version != 1:
        raise StrategySourceError("strategy must declare literal SDK_VERSION = 1", phase="register")
    data_requirements = _data_requirements(tree)
    runtime_requirements = _runtime_requirements(tree)
    entrypoints: list[EntrypointSpec] = []
    public_ids: dict[str, str] = {}
    event_keys: set[str] = set()
    counts: dict[str, int] = {}

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        registration = _registered_decorator(node)
        if registration is None:
            continue
        if isinstance(node, ast.AsyncFunctionDef):
            raise StrategySourceError(
                f"registered function {node.name} must be synchronous", phase="register"
            )
        kind, decorator = registration
        public_id = _decorator_literal(decorator, "id") or node.name
        if not isinstance(public_id, str) or not public_id or public_id.startswith("_"):
            raise StrategySourceError(f"invalid public id on {node.name}", phase="register")
        if public_id in public_ids:
            raise StrategySourceError(
                f"duplicate registered id {public_id!r}: {public_ids[public_id]} and {node.name}",
                phase="register",
            )
        public_ids[public_id] = node.name
        label = _decorator_literal(decorator, "label")
        event = _event_value(decorator.args[0]) if kind == "event" and decorator.args else None
        if kind == "event":
            if event is None:
                raise StrategySourceError(
                    f"@on_event on {node.name} requires a literal Event key", phase="register"
                )
            if event in event_keys:
                raise StrategySourceError(
                    f"duplicate @on_event handler for {event}", phase="register"
                )
            event_keys.add(event)
        counts[kind] = counts.get(kind, 0) + 1
        _validate_signature(node, kind)
        metadata = _decorator_metadata(kind, decorator)
        if kind == "signal":
            metadata["factor_blend"] = _signal_factor_blend_metadata(node)
        parameters = tuple(_parameters(node, source))
        entrypoints.append(
            EntrypointSpec(
                kind=kind,
                id=public_id,
                function=node.name,
                label=label if isinstance(label, str) else None,
                event=event,
                metadata=metadata,
                parameters=parameters,
                line=int(node.lineno),
            )
        )

    for kind, required in _REQUIRED.items():
        actual = counts.get(kind, 0)
        if actual != required:
            raise StrategySourceError(
                f"strategy requires exactly {required} @{kind} function; found {actual}",
                phase="register",
            )

    _validate_factor_dependencies(tree, entrypoints)

    try:
        compile(tree, "<alphalab-strategy-sdk-v1>", "exec")
    except Exception as exc:
        raise StrategySourceError(str(exc), phase="compile") from exc
    return SourceInspection(
        valid=True,
        sdk_version=1,
        source_sha256=hashlib.sha256(encoded).hexdigest(),
        source_bytes=len(encoded),
        validator_version=VALIDATOR_VERSION,
        entrypoints=tuple(entrypoints),
        data_requirements=data_requirements,
        runtime_requirements=runtime_requirements,
        warnings=tuple(_static_warnings(tree)),
    )


def split_strategy_source(source: str) -> tuple[str, tuple[StrategySourceUnit, ...]]:
    """Split registered factors from a validated runtime module.

    ``strategy.py`` remains a normal Python module containing project imports,
    constants, universe, signal, portfolio, event, and execution functions.
    Each factor unit contains exactly one registered ``@factor`` function.  The
    complete runtime module remains a deterministic derived artifact.
    """

    inspection = inspect_strategy_source(source)
    factor_ids = {
        item.function: item.id for item in inspection.entrypoints if item.kind == "factor"
    }
    module = cst.parse_module(source)
    strategy_body: list[cst.BaseStatement] = []
    factors: list[StrategySourceUnit] = []
    for item in module.body:
        if isinstance(item, cst.FunctionDef) and item.name.value in factor_ids:
            leading_lines = list(item.leading_lines)
            while leading_lines and leading_lines[0].comment is None:
                leading_lines.pop(0)
            factor_node = item.with_changes(leading_lines=tuple(leading_lines))
            factor_source = module.code_for_node(factor_node).rstrip() + "\n"
            factor_id = factor_ids[item.name.value]
            factors.append(
                StrategySourceUnit(
                    path=f"factors/{factor_id}.py",
                    kind="factor",
                    source=factor_source,
                    source_sha256=hashlib.sha256(factor_source.encode("utf-8")).hexdigest(),
                    position=len(factors),
                )
            )
            continue
        strategy_body.append(item)
    strategy_source = module.with_changes(body=tuple(strategy_body)).code
    return strategy_source, tuple(factors)


def assemble_strategy_source(
    strategy_source: str,
    factor_sources: Iterable[str],
) -> tuple[str, SourceInspection]:
    """Build and validate the one runtime module from canonical source units."""

    try:
        strategy_module = cst.parse_module(strategy_source)
    except cst.ParserSyntaxError as exc:
        raise StrategySourceError(f"Python syntax error: {exc}", phase="parse") from exc
    sdk_version = _literal_assignment(
        ast.parse(strategy_source, filename="<alphalab-strategy-unit>"), "SDK_VERSION"
    )
    if sdk_version != 1:
        raise StrategySourceError(
            "strategy.py must declare literal SDK_VERSION = 1", phase="register"
        )
    for item in strategy_module.body:
        if isinstance(item, cst.FunctionDef) and _cst_public_id(item)[0] == "factor":
            raise StrategySourceError(
                "strategy.py cannot contain @factor functions; edit them in the Factor Workbench",
                phase="register",
            )

    factor_nodes: list[cst.FunctionDef] = []
    factor_ids: set[str] = set()
    for source in factor_sources:
        try:
            factor_module = cst.parse_module(str(source))
        except cst.ParserSyntaxError as exc:
            raise StrategySourceError(f"factor Python syntax error: {exc}", phase="parse") from exc
        if len(factor_module.body) != 1 or not isinstance(
            factor_module.body[0], cst.FunctionDef
        ):
            raise StrategySourceError(
                "each factor file must contain exactly one complete @factor function",
                phase="register",
            )
        factor_node = factor_module.body[0]
        kind, factor_id = _cst_public_id(factor_node)
        if kind != "factor" or not factor_id:
            raise StrategySourceError(
                "each factor file must contain exactly one complete @factor function",
                phase="register",
            )
        if factor_id in factor_ids:
            raise StrategySourceError(f"duplicate factor source {factor_id!r}", phase="register")
        factor_ids.add(factor_id)
        factor_node = factor_node.with_changes(
            leading_lines=(cst.EmptyLine(), cst.EmptyLine(), *factor_node.leading_lines)
        )
        factor_nodes.append(factor_node)

    body = list(strategy_module.body)
    insertion_index = len(body)
    for index, item in enumerate(body):
        if not isinstance(item, cst.FunctionDef):
            continue
        if _cst_public_id(item)[0] in {"signal", "portfolio", "event", "execution"}:
            insertion_index = index
            break
    body[insertion_index:insertion_index] = factor_nodes
    bundled = strategy_module.with_changes(body=tuple(body)).code
    return bundled, inspect_strategy_source(bundled)


def update_parameter_default(
    source: str,
    *,
    entrypoint_id: str,
    parameter: str,
    value: Any,
) -> tuple[str, SourceInspection]:
    inspect_strategy_source(source)
    expression = _literal_cst(value)
    transformer = _ParameterTransformer(entrypoint_id, parameter, expression)
    updated = cst.parse_module(source).visit(transformer).code
    if not transformer.changed:
        raise StrategySourceError(
            f"editable parameter {entrypoint_id}.{parameter} was not found", phase="edit"
        )
    return updated, inspect_strategy_source(updated)


def update_signal_schedule(
    source: str,
    *,
    signal_id: str,
    frequency: str,
    selector: str,
    at: str,
) -> tuple[str, SourceInspection]:
    if frequency == "daily":
        expression_source = f'Daily.at("{at}")'
    else:
        factory = {"weekly": "Weekly", "monthly": "Monthly"}.get(frequency)
        if factory is None or selector not in {"first_trading_day", "last_trading_day"}:
            raise StrategySourceError("unsupported structured schedule", phase="edit")
        expression_source = f'{factory}.{selector}(at="{at}")'
    if at not in {"open", "close"}:
        raise StrategySourceError("schedule at must be open or close", phase="edit")
    transformer = _DecoratorArgumentTransformer(
        "signal", signal_id, "schedule", cst.parse_expression(expression_source)
    )
    updated = cst.parse_module(source).visit(transformer).code
    if not transformer.changed:
        raise StrategySourceError(f"signal {signal_id!r} was not found", phase="edit")
    return updated, inspect_strategy_source(updated)


def update_signal_factor_blend(
    source: str,
    *,
    signal_id: str,
    factor_weights: dict[str, float],
    normalization: str,
) -> tuple[str, SourceInspection]:
    inspection = inspect_strategy_source(source)
    if normalization not in {"raw", "rank", "zscore"}:
        raise StrategySourceError("unsupported factor normalization", phase="edit")
    signal_spec = next(
        (item for item in inspection.entrypoints if item.kind == "signal" and item.id == signal_id),
        None,
    )
    if signal_spec is None:
        raise StrategySourceError(f"signal {signal_id!r} was not found", phase="edit")
    blend = signal_spec.metadata.get("factor_blend") or {}
    if blend.get("mode") not in {"single", "structured"}:
        raise StrategySourceError(
            "custom factor logic must be edited in the signal Python function", phase="edit"
        )

    registered_factors = {item.id for item in inspection.entrypoints if item.kind == "factor"}
    normalized_weights: dict[str, float] = {}
    for raw_factor_id, raw_weight in factor_weights.items():
        factor_id = str(raw_factor_id).strip()
        if factor_id not in registered_factors:
            raise StrategySourceError(
                f"signal references unknown factor {factor_id!r}", phase="edit"
            )
        if isinstance(raw_weight, bool):
            raise StrategySourceError("factor weights must be numeric", phase="edit")
        weight = float(raw_weight)
        if not math.isfinite(weight):
            raise StrategySourceError("factor weights must be finite", phase="edit")
        if abs(weight) > 1e-12:
            normalized_weights[factor_id] = weight
    if not normalized_weights:
        raise StrategySourceError(
            "factor weights must contain at least one non-zero value", phase="edit"
        )

    parameters = blend.get("parameters") or {}
    replacement = _factor_blend_call(
        normalized_weights,
        normalization=normalization,
        parameters=parameters,
    )
    transformer = _SignalFactorBlendTransformer(
        signal_id,
        replacement,
        convert_single=blend.get("mode") == "single",
        replace_parameters=any(factor_id not in normalized_weights for factor_id in parameters),
    )
    updated = cst.parse_module(source).visit(transformer).code
    if not transformer.changed:
        raise StrategySourceError(
            f"structured factor blend for signal {signal_id!r} was not found", phase="edit"
        )
    return updated, inspect_strategy_source(updated)


def ensure_default_risk_handler(source: str) -> tuple[str, SourceInspection]:
    """Add the editable no-op holding-risk hook used by the default strategy."""

    inspection = inspect_strategy_source(source)
    if any(item.kind == "event" for item in inspection.entrypoints):
        return source, inspection

    tree = ast.parse(source)
    occupied = {item.id for item in inspection.entrypoints}
    occupied.update(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    function_name = "holding_period_risk"
    suffix = 2
    while function_name in occupied:
        function_name = f"holding_period_risk_{suffix}"
        suffix += 1

    bound_imports: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.module != "alphalab.sdk.v1":
            continue
        bound_imports.update(alias.asname or alias.name for alias in node.names)
    missing_imports = [name for name in ("Event", "on_event") if name not in bound_imports]

    module = cst.parse_module(source)
    body = list(module.body)
    if missing_imports:
        import_statement = cst.parse_statement(
            f"from alphalab.sdk.v1 import {', '.join(missing_imports)}\n"
        )
        import_indexes = [
            index
            for index, statement in enumerate(body)
            if isinstance(statement, cst.SimpleStatementLine)
            and statement.body
            and isinstance(statement.body[0], (cst.Import, cst.ImportFrom))
        ]
        body.insert((import_indexes[-1] + 1) if import_indexes else 0, import_statement)

    function = cst.parse_module(
        f'@on_event(Event.SESSION_CLOSE, id="{function_name}", label="自定义持有期风控")\n'
        f"def {function_name}(context, state):\n"
        "    # 在每日收盘检查持仓；返回 PortfolioDecision 可调整目标仓位。\n"
        "    return None\n"
    ).body[0]
    if not isinstance(function, cst.FunctionDef):  # pragma: no cover
        raise StrategySourceError("default risk handler is invalid", phase="edit")
    function = function.with_changes(
        leading_lines=(cst.EmptyLine(), cst.EmptyLine(), *function.leading_lines)
    )
    execution_index = next(
        (
            index
            for index, statement in enumerate(body)
            if isinstance(statement, cst.FunctionDef)
            and _cst_public_id(statement)[0] == "execution"
        ),
        len(body),
    )
    body.insert(execution_index, function)
    updated = module.with_changes(body=tuple(body)).code
    return updated, inspect_strategy_source(updated)


def replace_registered_function(
    source: str,
    *,
    entrypoint_id: str,
    function_source: str,
) -> tuple[str, SourceInspection]:
    current_inspection = inspect_strategy_source(source)
    current_entrypoint = next(
        (item for item in current_inspection.entrypoints if item.id == entrypoint_id),
        None,
    )
    if current_entrypoint is None:
        raise StrategySourceError(f"entrypoint {entrypoint_id!r} was not found", phase="edit")
    replacement_module = cst.parse_module(function_source)
    replacements = [item for item in replacement_module.body if isinstance(item, cst.FunctionDef)]
    if len(replacements) != 1:
        raise StrategySourceError("replacement must contain exactly one function", phase="edit")
    replacement = replacements[0]
    replacement_kind, replacement_id = _cst_public_id(replacement)
    if replacement_kind != current_entrypoint.kind or replacement_id is None:
        raise StrategySourceError(
            f"replacement for {current_entrypoint.kind} {entrypoint_id!r} must remain a "
            f"registered @{current_entrypoint.kind} function",
            phase="edit",
        )
    transformer = _FunctionTransformer(entrypoint_id, replacement)
    updated = cst.parse_module(source).visit(transformer).code
    if not transformer.changed:
        raise StrategySourceError(f"entrypoint {entrypoint_id!r} was not found", phase="edit")
    if current_entrypoint.kind == "factor" and replacement_id != entrypoint_id:
        updated = cst.parse_module(updated).visit(
            _FactorReferenceRenameTransformer(entrypoint_id, replacement_id)
        ).code
    return updated, inspect_strategy_source(updated)


def delete_registered_function(
    source: str,
    *,
    entrypoint_id: str,
) -> tuple[str, SourceInspection]:
    current_inspection = inspect_strategy_source(source)
    current_entrypoint = next(
        (item for item in current_inspection.entrypoints if item.id == entrypoint_id),
        None,
    )
    if current_entrypoint is None:
        raise StrategySourceError(f"entrypoint {entrypoint_id!r} was not found", phase="edit")
    if current_entrypoint.kind != "factor":
        raise StrategySourceError("only @factor functions can be deleted here", phase="edit")
    direct, dynamic = _factor_reference_owners(
        source,
        current_inspection.entrypoints,
        factor_id=entrypoint_id,
        excluded_function=current_entrypoint.function,
    )
    if direct:
        raise StrategySourceError(
            f"factor {entrypoint_id!r} is still referenced by: {', '.join(direct)}",
            phase="edit",
        )
    if dynamic:
        raise StrategySourceError(
            f"factor {entrypoint_id!r} cannot be deleted safely because dynamic factor "
            f"references exist in: {', '.join(dynamic)}",
            phase="edit",
        )
    module = cst.parse_module(source)
    body = [
        item
        for item in module.body
        if not (
            isinstance(item, cst.FunctionDef)
            and _cst_public_id(item)[1] == entrypoint_id
        )
    ]
    if len(body) == len(module.body):
        raise StrategySourceError(f"entrypoint {entrypoint_id!r} was not found", phase="edit")
    updated = module.with_changes(body=tuple(body)).code
    return updated, inspect_strategy_source(updated)


def remove_factor_inputs_arguments(source: str) -> tuple[str, SourceInspection]:
    """Remove deprecated @factor(inputs=...) metadata from an editable module."""

    inspect_strategy_source(source)
    transformer = _FactorInputsTransformer()
    updated = cst.parse_module(source).visit(transformer).code
    return updated, inspect_strategy_source(updated)


def registered_function_source(source: str, *, entrypoint_id: str) -> str:
    """Return one registered function, including decorators, without module-leading trivia."""

    inspect_strategy_source(source)
    module = cst.parse_module(source)
    for item in module.body:
        if not isinstance(item, cst.FunctionDef):
            continue
        _, public_id = _cst_public_id(item)
        if public_id == entrypoint_id:
            return module.code_for_node(item.with_changes(leading_lines=())).rstrip() + "\n"
    raise StrategySourceError(f"entrypoint {entrypoint_id!r} was not found", phase="edit")


def insert_source(source: str, *, cursor: int, snippet: str) -> tuple[str, SourceInspection | None]:
    position = max(0, min(int(cursor), len(source)))
    updated = source[:position] + snippet + source[position:]
    try:
        return updated, inspect_strategy_source(updated)
    except StrategySourceError:
        return updated, None


def factor_field_snippet(field: str) -> str:
    normalized = str(field).strip()
    if not normalized.isidentifier() or normalized.startswith("_"):
        raise StrategySourceError("field must be a public Python identifier", phase="edit")
    if normalized in {"open", "high", "low", "close", "volume", "amount"}:
        return f'{normalized} = context.history("{normalized}", window=window)'
    return f'{normalized} = context.fundamental("{normalized}")'


def factor_dependency_snippet(factor_id: str, **parameters: Any) -> str:
    normalized = str(factor_id).strip()
    if not normalized:
        raise StrategySourceError("factor id must not be empty", phase="edit")
    variable = normalized.replace("-", "_")
    if not variable.isidentifier() or variable.startswith("_"):
        variable = "factor_value"
    arguments = ", ".join(f"{key}={value!r}" for key, value in parameters.items())
    suffix = f", {arguments}" if arguments else ""
    return f"{variable} = context.factor({normalized!r}{suffix})"


def _literal_assignment(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                value = node.value
                try:
                    return ast.literal_eval(value)
                except (ValueError, TypeError):
                    return None
    return None


def _mapping_assignment(tree: ast.Module, name: str) -> dict[str, Any]:
    value = _literal_assignment(tree, name)
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise StrategySourceError(
            f"{name} must be a literal string-keyed mapping", phase="register"
        )
    return value


def _data_requirements(tree: ast.Module) -> dict[str, list[str]]:
    value = _mapping_assignment(tree, "DATA_REQUIREMENTS")
    unsupported = sorted(set(value) - {"bars", "fundamentals", "instruments"})
    if unsupported:
        raise StrategySourceError(
            f"unsupported DATA_REQUIREMENTS datasets: {unsupported}", phase="register"
        )
    normalized: dict[str, list[str]] = {}
    for dataset, raw_fields in value.items():
        if not dataset.strip():
            raise StrategySourceError(
                "DATA_REQUIREMENTS dataset names must not be empty", phase="register"
            )
        if not isinstance(raw_fields, (list, tuple)):
            raise StrategySourceError(
                f"DATA_REQUIREMENTS[{dataset!r}] must be a literal list of fields",
                phase="register",
            )
        fields = list(raw_fields)
        if not all(isinstance(field, str) and field.strip() for field in fields):
            raise StrategySourceError(
                f"DATA_REQUIREMENTS[{dataset!r}] contains an invalid field",
                phase="register",
            )
        if len(fields) != len(set(fields)):
            raise StrategySourceError(
                f"DATA_REQUIREMENTS[{dataset!r}] contains duplicate fields",
                phase="register",
            )
        normalized[dataset] = fields
    return normalized


def _runtime_requirements(tree: ast.Module) -> dict[str, str]:
    value = _mapping_assignment(tree, "RUNTIME_REQUIREMENTS")
    normalized: dict[str, str] = {}
    for package, raw_specifier in value.items():
        if not package.strip() or not isinstance(raw_specifier, str):
            raise StrategySourceError(
                "RUNTIME_REQUIREMENTS must map package names to specifier strings",
                phase="register",
            )
        try:
            SpecifierSet(raw_specifier)
        except InvalidSpecifier as exc:
            raise StrategySourceError(
                f"invalid runtime requirement for {package}: {raw_specifier}",
                phase="register",
            ) from exc
        normalized[package] = raw_specifier
    return normalized


def _decorator_name(value: ast.expr) -> str | None:
    target = value.func if isinstance(value, ast.Call) else value
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _registered_decorator(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, ast.Call] | None:
    found: list[tuple[str, ast.Call]] = []
    for decorator in node.decorator_list:
        name = _decorator_name(decorator)
        if name == "on_event":
            if not isinstance(decorator, ast.Call):
                raise StrategySourceError("@on_event requires an Event argument", phase="register")
            found.append(("event", decorator))
        elif name in _KINDS:
            found.append(
                (
                    name,
                    (
                        decorator
                        if isinstance(decorator, ast.Call)
                        else ast.Call(func=decorator, args=[], keywords=[])
                    ),
                )
            )
    if len(found) > 1:
        raise StrategySourceError(
            f"function {node.name} has multiple AlphaLab registrations", phase="register"
        )
    return found[0] if found else None


def _decorator_literal(decorator: ast.Call, name: str) -> Any:
    keyword = next((item for item in decorator.keywords if item.arg == name), None)
    if keyword is None:
        return None
    try:
        return ast.literal_eval(keyword.value)
    except (ValueError, TypeError):
        return None


def _event_value(value: ast.expr) -> str | None:
    if (
        isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id == "Event"
    ):
        return value.attr.lower()
    try:
        literal = ast.literal_eval(value)
    except (ValueError, TypeError):
        return None
    return str(literal).lower() if literal is not None else None


def _schedule_metadata(value: ast.expr) -> dict[str, Any]:
    if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Attribute):
        return {"mode": "custom", "source": ast.unparse(value)}
    owner = value.func.value
    if not isinstance(owner, ast.Name) or owner.id not in {"Daily", "Weekly", "Monthly"}:
        return {"mode": "custom", "source": ast.unparse(value)}
    at_value = next((item.value for item in value.keywords if item.arg == "at"), None)
    try:
        at = ast.literal_eval(at_value) if at_value is not None else None
    except (ValueError, TypeError):
        at = None
    selector = value.func.attr
    if owner.id == "Daily" and selector == "at" and at in {"open", "close"}:
        return {"mode": "structured", "frequency": "daily", "selector": "every", "at": at}
    if selector in {"first_trading_day", "last_trading_day"} and at in {"open", "close"}:
        return {"mode": "structured", "frequency": owner.id.lower(), "selector": selector, "at": at}
    return {"mode": "custom", "source": ast.unparse(value)}


def _context_call(node: ast.Call, context_name: str, method: str) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == method
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == context_name
    )


def _factor_reference_owners(
    source: str,
    entrypoints: Iterable[EntrypointSpec],
    *,
    factor_id: str,
    excluded_function: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    tree = ast.parse(source)
    function_specs = {item.function: item for item in entrypoints}
    direct: set[str] = set()
    dynamic: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name == excluded_function:
            continue
        spec = function_specs.get(node.name)
        positional = [*node.args.posonlyargs, *node.args.args]
        if spec is None or not positional:
            continue
        context_name = positional[0].arg
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            if _context_call(child, context_name, "factor"):
                reference_value = (
                    child.args[0]
                    if child.args
                    else next(
                        (item.value for item in child.keywords if item.arg == "factor_id"),
                        None,
                    )
                )
                if reference_value is None:
                    continue
                try:
                    referenced = ast.literal_eval(reference_value)
                except (TypeError, ValueError):
                    dynamic.add(spec.id)
                    continue
                if referenced == factor_id:
                    direct.add(spec.id)
            if not _context_call(child, context_name, "combine_factors"):
                continue
            values = [
                next(
                    (item.value for item in child.keywords if item.arg == "weights"),
                    child.args[0] if child.args else None,
                ),
                next(
                    (item.value for item in child.keywords if item.arg == "parameters"),
                    None,
                ),
            ]
            for value in values:
                if value is None:
                    continue
                try:
                    mapping = ast.literal_eval(value)
                except (TypeError, ValueError):
                    dynamic.add(spec.id)
                    continue
                if isinstance(mapping, dict) and factor_id in mapping:
                    direct.add(spec.id)
    return tuple(sorted(direct)), tuple(sorted(dynamic))


def _literal_factor_parameters(value: ast.expr | None) -> dict[str, dict[str, Any]] | None:
    if value is None:
        return {}
    try:
        raw = ast.literal_eval(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) and isinstance(item, dict) for key, item in raw.items()
    ):
        return None
    return {str(key): dict(item) for key, item in raw.items()}


def _signal_factor_blend_metadata(node: ast.FunctionDef) -> dict[str, Any]:
    context_name = node.args.args[0].arg
    blend_calls = [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and _context_call(child, context_name, "combine_factors")
    ]
    direct_calls = [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and _context_call(child, context_name, "factor")
    ]
    detected_ids: list[str] = []
    for call in [*blend_calls, *direct_calls]:
        if call.args:
            try:
                value = ast.literal_eval(call.args[0])
            except (TypeError, ValueError):
                value = None
            if isinstance(value, str) and value not in detected_ids:
                detected_ids.append(value)

    if len(blend_calls) == 1:
        call = blend_calls[0]
        weights_value = next(
            (item.value for item in call.keywords if item.arg == "weights"),
            call.args[0] if call.args else None,
        )
        normalization_value = next(
            (item.value for item in call.keywords if item.arg == "normalization"), None
        )
        parameters_value = next(
            (item.value for item in call.keywords if item.arg == "parameters"), None
        )
        try:
            raw_weights = ast.literal_eval(weights_value) if weights_value is not None else None
            normalization = (
                ast.literal_eval(normalization_value) if normalization_value is not None else "rank"
            )
        except (TypeError, ValueError):
            raw_weights = None
            normalization = None
        parameters = _literal_factor_parameters(parameters_value)
        if (
            isinstance(raw_weights, dict)
            and raw_weights
            and all(
                isinstance(key, str)
                and not isinstance(weight, bool)
                and isinstance(weight, (int, float))
                and math.isfinite(float(weight))
                for key, weight in raw_weights.items()
            )
            and normalization in {"raw", "rank", "zscore"}
            and parameters is not None
        ):
            return {
                "mode": "structured",
                "weights": {key: float(weight) for key, weight in raw_weights.items()},
                "normalization": normalization,
                "parameters": parameters,
            }
        return {
            "mode": "custom",
            "factor_ids": detected_ids,
            "source": ast.unparse(call),
        }

    if not blend_calls and len(direct_calls) == 1:
        call = direct_calls[0]
        try:
            factor_id = ast.literal_eval(call.args[0]) if call.args else None
            parameters = {
                item.arg: ast.literal_eval(item.value)
                for item in call.keywords
                if item.arg is not None
            }
        except (TypeError, ValueError):
            factor_id = None
            parameters = None
        if isinstance(factor_id, str) and parameters is not None:
            return {
                "mode": "single",
                "weights": {factor_id: 1.0},
                "normalization": "raw",
                "parameters": {factor_id: parameters} if parameters else {},
            }

    return {"mode": "custom", "factor_ids": detected_ids}


def _decorator_metadata(kind: str, decorator: ast.Call) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if kind == "signal":
        schedule_value = next(
            (item.value for item in decorator.keywords if item.arg == "schedule"), None
        )
        metadata["schedule"] = (
            _schedule_metadata(schedule_value) if schedule_value is not None else {"mode": "custom"}
        )
    return metadata


def _validate_signature(node: ast.FunctionDef, kind: str) -> None:
    if node.args.vararg or node.args.kwarg or node.args.posonlyargs:
        raise StrategySourceError(
            f"registered function {node.name} cannot use positional-only, *args, or **kwargs",
            phase="register",
        )
    positional = len(node.args.args)
    expected = _EXPECTED_POSITIONAL[kind]
    if positional != expected:
        raise StrategySourceError(
            f"@{kind} function {node.name} requires {expected} positional arguments; found {positional}",
            phase="register",
        )


def _parameters(node: ast.FunctionDef, source: str) -> Iterable[ParameterSpec]:
    for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        annotation = (
            ast.get_source_segment(source, argument.annotation) if argument.annotation else None
        )
        metadata = _parameter_metadata(argument.annotation)
        if default is None:
            yield ParameterSpec(
                argument.arg,
                annotation,
                None,
                False,
                None,
                **metadata,
            )
            continue
        default_source = ast.get_source_segment(source, default) or ast.unparse(default)
        try:
            value = ast.literal_eval(default)
            editable = _supported_literal(value)
        except (ValueError, TypeError):
            value = None
            editable = False
        _validate_parameter_bounds(argument.arg, value, editable, metadata)
        yield ParameterSpec(
            name=argument.arg,
            annotation=annotation,
            default=value,
            editable=editable,
            custom_source=None if editable else default_source,
            **metadata,
        )


def _parameter_metadata(annotation: ast.expr | None) -> dict[str, Any]:
    empty = {
        "label": None,
        "description": None,
        "minimum": None,
        "maximum": None,
        "step": None,
    }
    if not isinstance(annotation, ast.Subscript):
        return empty
    owner = annotation.value
    owner_name = (
        owner.id
        if isinstance(owner, ast.Name)
        else owner.attr if isinstance(owner, ast.Attribute) else None
    )
    if owner_name != "Annotated":
        return empty
    elements = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else ()
    parameter_call = next(
        (
            item
            for item in elements[1:]
            if isinstance(item, ast.Call) and _call_name(item) == "Parameter"
        ),
        None,
    )
    if parameter_call is None:
        return empty
    names = ("label", "description", "minimum", "maximum", "step")
    if len(parameter_call.args) > len(names) or any(
        item.arg is None for item in parameter_call.keywords
    ):
        raise StrategySourceError(
            "Parameter metadata must use literal constructor arguments", phase="register"
        )
    values: dict[str, Any] = dict(empty)
    for name, expression in zip(names, parameter_call.args):
        values[name] = _metadata_literal(expression, name)
    for keyword in parameter_call.keywords:
        assert keyword.arg is not None
        if keyword.arg not in values:
            raise StrategySourceError(
                f"unsupported Parameter metadata field {keyword.arg!r}", phase="register"
            )
        if keyword.arg in names[: len(parameter_call.args)]:
            raise StrategySourceError(
                f"duplicate Parameter metadata field {keyword.arg!r}", phase="register"
            )
        values[keyword.arg] = _metadata_literal(keyword.value, keyword.arg)
    for name in ("label", "description"):
        if values[name] is not None and not isinstance(values[name], str):
            raise StrategySourceError(
                f"Parameter {name} must be a string or None", phase="register"
            )
    for name in ("minimum", "maximum", "step"):
        item = values[name]
        if item is not None and (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
        ):
            raise StrategySourceError(
                f"Parameter {name} must be a finite number or None", phase="register"
            )
    if values["step"] is not None and values["step"] <= 0:
        raise StrategySourceError("Parameter step must be positive", phase="register")
    if (
        values["minimum"] is not None
        and values["maximum"] is not None
        and values["minimum"] > values["maximum"]
    ):
        raise StrategySourceError("Parameter minimum must not exceed maximum", phase="register")
    return values


def _call_name(value: ast.Call) -> str | None:
    if isinstance(value.func, ast.Name):
        return value.func.id
    if isinstance(value.func, ast.Attribute):
        return value.func.attr
    return None


def _metadata_literal(expression: ast.expr, name: str) -> Any:
    try:
        return ast.literal_eval(expression)
    except (ValueError, TypeError):
        raise StrategySourceError(
            f"Parameter {name} metadata must be literal", phase="register"
        ) from None


def _validate_parameter_bounds(
    name: str,
    value: Any,
    editable: bool,
    metadata: dict[str, Any],
) -> None:
    if not editable or isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    minimum = metadata["minimum"]
    maximum = metadata["maximum"]
    if minimum is not None and value < minimum:
        raise StrategySourceError(
            f"parameter {name} default is below its minimum", phase="register"
        )
    if maximum is not None and value > maximum:
        raise StrategySourceError(
            f"parameter {name} default is above its maximum", phase="register"
        )


def _supported_literal(value: Any) -> bool:
    return (
        value is None
        or isinstance(value, (bool, int, str))
        or (isinstance(value, float) and math.isfinite(value))
    )


def _validate_factor_dependencies(
    tree: ast.Module,
    entrypoints: Iterable[EntrypointSpec],
) -> None:
    factor_ids = {item.id for item in entrypoints if item.kind == "factor"}
    function_to_id = {item.function: item.id for item in entrypoints if item.kind == "factor"}
    graph: dict[str, set[str]] = {factor_id: set() for factor_id in factor_ids}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name not in function_to_id:
            continue
        source_id = function_to_id[node.name]
        for child in ast.walk(node):
            if not (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "factor"
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == node.args.args[0].arg
                and child.args
            ):
                continue
            try:
                dependency = ast.literal_eval(child.args[0])
            except (TypeError, ValueError):
                raise StrategySourceError(
                    f"factor {source_id!r} must reference dependencies by literal id",
                    phase="register",
                ) from None
            if not isinstance(dependency, str) or dependency not in factor_ids:
                raise StrategySourceError(
                    f"factor {source_id!r} references unknown factor {dependency!r}",
                    phase="register",
                )
            graph[source_id].add(dependency)

    visiting: list[str] = []
    visited: set[str] = set()

    def visit(factor_id: str) -> None:
        if factor_id in visited:
            return
        if factor_id in visiting:
            start = visiting.index(factor_id)
            cycle = [*visiting[start:], factor_id]
            raise StrategySourceError(
                f"factor dependency cycle: {' -> '.join(cycle)}", phase="register"
            )
        visiting.append(factor_id)
        for dependency in sorted(graph[factor_id]):
            visit(dependency)
        visiting.pop()
        visited.add(factor_id)

    for factor_id in sorted(graph):
        visit(factor_id)


def _static_warnings(tree: ast.Module) -> Iterable[dict[str, Any]]:
    risky_imports = {
        "datetime",
        "httpx",
        "os",
        "pathlib",
        "random",
        "requests",
        "secrets",
        "socket",
        "subprocess",
        "time",
        "urllib",
    }
    risky_calls = {"eval", "exec", "compile", "open", "__import__"}
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        message = None
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [item.name.split(".")[0] for item in node.names]
                if isinstance(node, ast.Import)
                else [str(node.module or "").split(".")[0]]
            )
            risky = sorted(set(names) & risky_imports)
            if risky:
                message = (
                    f"trusted-local code imports capability-sensitive module(s): {', '.join(risky)}"
                )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in risky_calls
        ):
            message = f"trusted-local code calls {node.func.id}()"
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            message = "strategy mutates non-local state; prefer the bounded SDK State value"
        if message is not None:
            key = (int(getattr(node, "lineno", 0)), message)
            if key not in seen:
                seen.add(key)
                yield {"line": key[0], "code": "trusted_local_capability", "message": message}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {target.id for target in targets if isinstance(target, ast.Name)}
        if names & {"DATA_REQUIREMENTS", "RUNTIME_REQUIREMENTS"}:
            continue
        value = node.value
        if isinstance(value, (ast.Dict, ast.List, ast.Set)):
            message = (
                "module-level mutable state is shared for a run; prefer the bounded SDK State value"
            )
            key = (int(getattr(node, "lineno", 0)), message)
            if key not in seen:
                seen.add(key)
                yield {
                    "line": key[0],
                    "code": "mutable_module_state",
                    "message": message,
                }


def _literal_cst(value: Any) -> cst.BaseExpression:
    if not _supported_literal(value):
        raise StrategySourceError(
            "structured parameters support only bool, int, finite float, str, or None",
            phase="edit",
        )
    return cst.parse_expression(repr(value))


def _factor_blend_call(
    weights: dict[str, float],
    *,
    normalization: str,
    parameters: dict[str, dict[str, Any]],
) -> cst.Call:
    equals = cst.AssignEqual(
        whitespace_before=cst.SimpleWhitespace(""),
        whitespace_after=cst.SimpleWhitespace(""),
    )
    arguments = [
        cst.Arg(
            value=cst.parse_expression(repr(weights)),
            keyword=cst.Name("weights"),
            equal=equals,
        ),
        cst.Arg(
            value=cst.SimpleString(repr(normalization)),
            keyword=cst.Name("normalization"),
            equal=equals,
        ),
    ]
    retained_parameters = {
        factor_id: value
        for factor_id, value in parameters.items()
        if factor_id in weights and value
    }
    if retained_parameters:
        arguments.append(
            cst.Arg(
                value=cst.parse_expression(repr(retained_parameters)),
                keyword=cst.Name("parameters"),
                equal=equals,
            )
        )
    return cst.Call(
        func=cst.Attribute(value=cst.Name("context"), attr=cst.Name("combine_factors")),
        args=tuple(arguments),
    )


class _FactorBlendCallTransformer(cst.CSTTransformer):
    def __init__(
        self,
        replacement: cst.Call,
        *,
        context_name: str,
        convert_single: bool,
        replace_parameters: bool,
    ) -> None:
        self.replacement = replacement
        self.context_name = context_name
        self.convert_single = convert_single
        self.replace_parameters = replace_parameters
        self.changed = 0

    def _is_context_call(self, node: cst.Call, method: str) -> bool:
        return (
            isinstance(node.func, cst.Attribute)
            and node.func.attr.value == method
            and isinstance(node.func.value, cst.Name)
            and node.func.value.value == self.context_name
        )

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.Call:
        method = "factor" if self.convert_single else "combine_factors"
        if not self._is_context_call(original_node, method):
            return updated_node
        self.changed += 1
        if self.convert_single:
            return self.replacement.with_changes(
                func=self.replacement.func.with_changes(value=cst.Name(self.context_name))
            )

        replacement_arguments = {
            item.keyword.value: item for item in self.replacement.args if item.keyword is not None
        }
        arguments: list[cst.Arg] = []
        replaced: set[str] = set()
        for index, argument in enumerate(updated_node.args):
            keyword = argument.keyword.value if argument.keyword is not None else None
            replaceable = {"weights", "normalization"}
            if self.replace_parameters:
                replaceable.add("parameters")
            if keyword in replaceable:
                replacement = replacement_arguments.get(keyword)
                if replacement is not None:
                    arguments.append(argument.with_changes(value=replacement.value))
                    replaced.add(keyword)
                continue
            if index == 0 and keyword is None:
                arguments.append(
                    argument.with_changes(value=replacement_arguments["weights"].value)
                )
                replaced.add("weights")
                continue
            arguments.append(argument)
        appendable = ["weights", "normalization"]
        if self.replace_parameters:
            appendable.append("parameters")
        for keyword in appendable:
            if keyword not in replaced and keyword in replacement_arguments:
                arguments.append(replacement_arguments[keyword])
        return updated_node.with_changes(args=tuple(arguments))


class _SignalFactorBlendTransformer(cst.CSTTransformer):
    def __init__(
        self,
        signal_id: str,
        replacement: cst.Call,
        *,
        convert_single: bool,
        replace_parameters: bool,
    ) -> None:
        self.signal_id = signal_id
        self.replacement = replacement
        self.convert_single = convert_single
        self.replace_parameters = replace_parameters
        self.changed = False

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        kind, public_id = _cst_public_id(original_node)
        if kind != "signal" or public_id != self.signal_id:
            return updated_node
        context_name = original_node.params.params[0].name.value
        transformer = _FactorBlendCallTransformer(
            self.replacement,
            context_name=context_name,
            convert_single=self.convert_single,
            replace_parameters=self.replace_parameters,
        )
        result = updated_node.visit(transformer)
        if transformer.changed != 1:
            raise StrategySourceError(
                "structured factor editing requires exactly one recognized factor call",
                phase="edit",
            )
        self.changed = True
        return result


def _cst_decorator_name(decorator: cst.Decorator) -> str | None:
    value = decorator.decorator
    target = value.func if isinstance(value, cst.Call) else value
    if isinstance(target, cst.Name):
        return target.value
    if isinstance(target, cst.Attribute):
        return target.attr.value
    return None


def _cst_public_id(node: cst.FunctionDef) -> tuple[str | None, str | None]:
    for decorator in node.decorators:
        name = _cst_decorator_name(decorator)
        if name not in _KINDS | {"on_event"}:
            continue
        call = decorator.decorator
        if isinstance(call, cst.Call):
            for argument in call.args:
                if (
                    argument.keyword
                    and argument.keyword.value == "id"
                    and isinstance(argument.value, cst.SimpleString)
                ):
                    return ("event" if name == "on_event" else name), ast.literal_eval(
                        argument.value.value
                    )
        return ("event" if name == "on_event" else name), node.name.value
    return None, None


class _ParameterTransformer(cst.CSTTransformer):
    def __init__(self, entrypoint_id: str, parameter: str, value: cst.BaseExpression) -> None:
        self.entrypoint_id = entrypoint_id
        self.parameter = parameter
        self.value = value
        self.changed = False

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        _, public_id = _cst_public_id(original_node)
        if public_id != self.entrypoint_id:
            return updated_node
        parameters = []
        for item in updated_node.params.kwonly_params:
            if item.name.value == self.parameter:
                if item.default is None:
                    raise StrategySourceError(
                        f"parameter {self.parameter} has no editable default", phase="edit"
                    )
                item = item.with_changes(default=self.value)
                self.changed = True
            parameters.append(item)
        return updated_node.with_changes(
            params=updated_node.params.with_changes(kwonly_params=tuple(parameters))
        )


class _DecoratorArgumentTransformer(cst.CSTTransformer):
    def __init__(self, kind: str, public_id: str, argument: str, value: cst.BaseExpression) -> None:
        self.kind = kind
        self.public_id = public_id
        self.argument = argument
        self.value = value
        self.changed = False

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        kind, public_id = _cst_public_id(original_node)
        if kind != self.kind or public_id != self.public_id:
            return updated_node
        decorators = []
        for decorator in updated_node.decorators:
            if _cst_decorator_name(decorator) != self.kind or not isinstance(
                decorator.decorator, cst.Call
            ):
                decorators.append(decorator)
                continue
            arguments = list(decorator.decorator.args)
            replaced = False
            for index, item in enumerate(arguments):
                if item.keyword and item.keyword.value == self.argument:
                    arguments[index] = item.with_changes(value=self.value)
                    replaced = True
                    break
            if not replaced:
                arguments.append(cst.Arg(value=self.value, keyword=cst.Name(self.argument)))
            decorators.append(
                decorator.with_changes(
                    decorator=decorator.decorator.with_changes(args=tuple(arguments))
                )
            )
            self.changed = True
        return updated_node.with_changes(decorators=tuple(decorators))


class _FunctionTransformer(cst.CSTTransformer):
    def __init__(self, public_id: str, replacement: cst.FunctionDef) -> None:
        self.public_id = public_id
        self.replacement = replacement
        self.changed = False

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        _, current_id = _cst_public_id(original_node)
        if current_id != self.public_id:
            return updated_node
        self.changed = True
        return self.replacement.with_changes(
            leading_lines=updated_node.leading_lines,
            lines_after_decorators=updated_node.lines_after_decorators,
        )


class _FactorInputsTransformer(cst.CSTTransformer):
    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        kind, _ = _cst_public_id(original_node)
        if kind != "factor":
            return updated_node
        decorators = []
        for decorator in updated_node.decorators:
            value = decorator.decorator
            if _cst_decorator_name(decorator) != "factor" or not isinstance(value, cst.Call):
                decorators.append(decorator)
                continue
            arguments = tuple(
                argument
                for argument in value.args
                if argument.keyword is None or argument.keyword.value != "inputs"
            )
            decorators.append(decorator.with_changes(decorator=value.with_changes(args=arguments)))
        return updated_node.with_changes(decorators=tuple(decorators))


class _FactorReferenceRenameTransformer(cst.CSTTransformer):
    """Rename one factor ID only at literal SDK reference boundaries."""

    def __init__(self, old_id: str, new_id: str) -> None:
        self.old_id = old_id
        self.new_id = new_id
        self._context_names: list[str | None] = []

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        kind, _ = _cst_public_id(node)
        positional = [*node.params.posonly_params, *node.params.params]
        context_name = positional[0].name.value if kind is not None and positional else None
        self._context_names.append(context_name)

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        self._context_names.pop()
        return updated_node

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.Call:
        if not self._context_names or self._context_names[-1] is None:
            return updated_node
        function = updated_node.func
        if not (
            isinstance(function, cst.Attribute)
            and isinstance(function.value, cst.Name)
            and function.value.value == self._context_names[-1]
        ):
            return updated_node
        if function.attr.value == "factor":
            return updated_node.with_changes(args=self._rename_factor_argument(updated_node.args))
        if function.attr.value == "combine_factors":
            return updated_node.with_changes(args=self._rename_blend_arguments(updated_node.args))
        return updated_node

    def _rename_factor_argument(self, arguments: tuple[cst.Arg, ...]) -> tuple[cst.Arg, ...]:
        updated = list(arguments)
        for index, argument in enumerate(updated):
            if argument.keyword is None and not argument.star:
                updated[index] = argument.with_changes(
                    value=self._rename_string(argument.value)
                )
                break
        return tuple(updated)

    def _rename_blend_arguments(self, arguments: tuple[cst.Arg, ...]) -> tuple[cst.Arg, ...]:
        updated = list(arguments)
        positional_index = next(
            (
                index
                for index, argument in enumerate(updated)
                if argument.keyword is None and not argument.star
            ),
            None,
        )
        for index, argument in enumerate(updated):
            keyword = argument.keyword.value if argument.keyword is not None else None
            if keyword == "weights" or (keyword is None and index == positional_index):
                updated[index] = argument.with_changes(
                    value=self._rename_mapping_key(argument.value, mapping_name="weights")
                )
            elif keyword == "parameters":
                updated[index] = argument.with_changes(
                    value=self._rename_mapping_key(argument.value, mapping_name="parameters")
                )
        return tuple(updated)

    def _rename_string(self, value: cst.BaseExpression) -> cst.BaseExpression:
        if not isinstance(value, cst.SimpleString):
            return value
        try:
            literal = ast.literal_eval(value.value)
        except (SyntaxError, ValueError):
            return value
        return cst.SimpleString(repr(self.new_id)) if literal == self.old_id else value

    def _rename_mapping_key(
        self,
        value: cst.BaseExpression,
        *,
        mapping_name: str,
    ) -> cst.BaseExpression:
        if not isinstance(value, cst.Dict):
            return value
        literal_keys = [
            ast.literal_eval(element.key.value)
            for element in value.elements
            if isinstance(element, cst.DictElement)
            and isinstance(element.key, cst.SimpleString)
        ]
        if self.old_id in literal_keys and self.new_id in literal_keys:
            raise StrategySourceError(
                f"cannot rename factor {self.old_id!r} to {self.new_id!r}: "
                f"{mapping_name} already contains the destination ID",
                phase="edit",
            )
        elements: list[cst.DictElement | cst.StarredDictElement] = []
        for element in value.elements:
            if isinstance(element, cst.DictElement):
                element = element.with_changes(key=self._rename_string(element.key))
            elements.append(element)
        return value.with_changes(elements=tuple(elements))


__all__ = [
    "EntrypointSpec",
    "MAX_STRATEGY_SOURCE_BYTES",
    "ParameterSpec",
    "SourceInspection",
    "StrategySourceUnit",
    "StrategySourceError",
    "VALIDATOR_VERSION",
    "assemble_strategy_source",
    "delete_registered_function",
    "ensure_default_risk_handler",
    "factor_dependency_snippet",
    "factor_field_snippet",
    "insert_source",
    "inspect_strategy_source",
    "registered_function_source",
    "remove_factor_inputs_arguments",
    "replace_registered_function",
    "split_strategy_source",
    "update_parameter_default",
    "update_signal_factor_blend",
    "update_signal_schedule",
]
