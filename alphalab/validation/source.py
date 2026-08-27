"""Inspection and concrete-syntax edits for project validation.py files."""

from __future__ import annotations

import ast
import hashlib
import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable

import libcst as cst

MAX_VALIDATION_SOURCE_BYTES = 300_000
VALIDATOR_VERSION = "validation-sdk-v1-validator-1"
_REQUIRED_ANALYSES = {"performance", "alpha_beta"}


class ValidationSourceError(ValueError):
    def __init__(self, message: str, *, phase: str = "parse") -> None:
        super().__init__(message)
        self.phase = phase


@dataclass(frozen=True)
class ValidationParameterSpec:
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
class ValidationEntrypointSpec:
    kind: str
    id: str
    function: str
    label: str | None
    parameters: tuple[ValidationParameterSpec, ...]
    line: int


@dataclass(frozen=True)
class ValidationSourceInspection:
    valid: bool
    sdk_version: int
    source_sha256: str
    source_bytes: int
    validator_version: str
    entrypoints: tuple[ValidationEntrypointSpec, ...]
    warnings: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entrypoints"] = [asdict(item) for item in self.entrypoints]
        payload["warnings"] = [dict(item) for item in self.warnings]
        return payload


def inspect_validation_source(source: str) -> ValidationSourceInspection:
    encoded = source.encode("utf-8")
    if not source.strip():
        raise ValidationSourceError("validation source must not be empty")
    if len(encoded) > MAX_VALIDATION_SOURCE_BYTES:
        raise ValidationSourceError(
            f"validation source must not exceed {MAX_VALIDATION_SOURCE_BYTES} bytes"
        )
    try:
        tree = ast.parse(source, filename="<validation.py>")
    except SyntaxError as exc:
        raise ValidationSourceError(
            f"Python syntax error at line {exc.lineno}: {exc.msg}", phase="parse"
        ) from exc
    if _literal_assignment(tree, "VALIDATION_SDK_VERSION") != 1:
        raise ValidationSourceError(
            "validation.py must declare literal VALIDATION_SDK_VERSION = 1",
            phase="register",
        )
    entrypoints: list[ValidationEntrypointSpec] = []
    public_ids: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorator = _analysis_decorator(node)
        if decorator is None:
            continue
        if isinstance(node, ast.AsyncFunctionDef):
            raise ValidationSourceError(
                f"registered analysis {node.name} must be synchronous", phase="register"
            )
        public_id = _decorator_literal(decorator, "id")
        if not isinstance(public_id, str) or not public_id or public_id.startswith("_"):
            raise ValidationSourceError(
                f"@analysis on {node.name} requires a public literal id", phase="register"
            )
        if public_id in public_ids:
            raise ValidationSourceError(
                f"duplicate analysis id {public_id!r}", phase="register"
            )
        public_ids.add(public_id)
        _validate_signature(node)
        label = _decorator_literal(decorator, "label")
        entrypoints.append(
            ValidationEntrypointSpec(
                kind="analysis",
                id=public_id,
                function=node.name,
                label=label if isinstance(label, str) else None,
                parameters=tuple(_parameters(node, source)),
                line=int(node.lineno),
            )
        )
    missing = sorted(_REQUIRED_ANALYSES - public_ids)
    if missing:
        raise ValidationSourceError(
            f"validation.py requires @analysis ids: {', '.join(missing)}",
            phase="register",
        )
    try:
        compile(tree, "<validation.py>", "exec")
    except Exception as exc:
        raise ValidationSourceError(str(exc), phase="compile") from exc
    return ValidationSourceInspection(
        valid=True,
        sdk_version=1,
        source_sha256=hashlib.sha256(encoded).hexdigest(),
        source_bytes=len(encoded),
        validator_version=VALIDATOR_VERSION,
        entrypoints=tuple(entrypoints),
    )


def update_validation_parameters(
    source: str,
    edits: Iterable[dict[str, Any]],
) -> tuple[str, ValidationSourceInspection]:
    inspect_validation_source(source)
    updated = source
    for edit in edits:
        entrypoint_id = str(edit.get("entrypoint_id") or "")
        parameter = str(edit.get("parameter") or "")
        transformer = _ParameterTransformer(
            entrypoint_id,
            parameter,
            _literal_expression(edit.get("value")),
        )
        updated = cst.parse_module(updated).visit(transformer).code
        if not transformer.changed:
            raise ValidationSourceError(
                f"editable parameter {entrypoint_id}.{parameter} was not found", phase="edit"
            )
    return updated, inspect_validation_source(updated)


def _literal_assignment(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                try:
                    return ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    return None
    return None


def _analysis_decorator(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Call | None:
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call) and _call_name(decorator.func) == "analysis":
            return decorator
    return None


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _decorator_literal(decorator: ast.Call, name: str) -> Any:
    for keyword in decorator.keywords:
        if keyword.arg == name:
            try:
                return ast.literal_eval(keyword.value)
            except (ValueError, TypeError):
                return None
    return None


def _validate_signature(node: ast.FunctionDef) -> None:
    if node.args.vararg or node.args.kwarg or node.args.posonlyargs:
        raise ValidationSourceError(
            f"@analysis function {node.name} cannot use positional-only, *args, or **kwargs",
            phase="register",
        )
    if len(node.args.args) != 1:
        raise ValidationSourceError(
            f"@analysis function {node.name} requires one positional context argument",
            phase="register",
        )


def _parameters(node: ast.FunctionDef, source: str) -> Iterable[ValidationParameterSpec]:
    for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        annotation = ast.get_source_segment(source, argument.annotation) if argument.annotation else None
        if default is None:
            yield ValidationParameterSpec(argument.arg, annotation, None, False)
            continue
        default_source = ast.get_source_segment(source, default) or ast.unparse(default)
        try:
            value = ast.literal_eval(default)
            editable = _supported_literal(value)
        except (ValueError, TypeError):
            value = None
            editable = False
        yield ValidationParameterSpec(
            argument.arg,
            annotation,
            value,
            editable,
            None if editable else default_source,
        )


def _supported_literal(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _literal_expression(value: Any) -> cst.BaseExpression:
    if not _supported_literal(value):
        raise ValidationSourceError("parameter value must be a finite scalar literal", phase="edit")
    return cst.parse_expression(repr(value))


def _cst_analysis_id(node: cst.FunctionDef) -> str | None:
    for decorator in node.decorators:
        call = decorator.decorator
        if not isinstance(call, cst.Call) or _cst_name(call.func) != "analysis":
            continue
        for argument in call.args:
            if argument.keyword and argument.keyword.value == "id" and isinstance(
                argument.value, cst.SimpleString
            ):
                return ast.literal_eval(argument.value.value)
    return None


def _cst_name(node: cst.BaseExpression) -> str | None:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return node.attr.value
    return None


class _ParameterTransformer(cst.CSTTransformer):
    def __init__(self, entrypoint_id: str, parameter: str, value: cst.BaseExpression) -> None:
        self.entrypoint_id = entrypoint_id
        self.parameter = parameter
        self.value = value
        self.changed = False

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        if _cst_analysis_id(original_node) != self.entrypoint_id:
            return updated_node
        parameters = []
        for item in updated_node.params.kwonly_params:
            if item.name.value == self.parameter:
                if item.default is None:
                    raise ValidationSourceError(
                        f"parameter {self.parameter} has no editable default", phase="edit"
                    )
                item = item.with_changes(default=self.value)
                self.changed = True
            parameters.append(item)
        return updated_node.with_changes(
            params=updated_node.params.with_changes(kwonly_params=tuple(parameters))
        )


__all__ = [
    "ValidationEntrypointSpec",
    "ValidationParameterSpec",
    "ValidationSourceError",
    "ValidationSourceInspection",
    "inspect_validation_source",
    "update_validation_parameters",
]
