"""Safe vectorized expressions for user-defined cross-sectional factors."""
from __future__ import annotations

import ast
from collections.abc import Mapping

import numpy as np
import pandas as pd


class FactorExpressionError(ValueError):
    """Raised when a factor expression is unsafe or cannot be evaluated."""


_BINARY_OPERATORS = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.Pow: lambda left, right: left**right,
}
_UNARY_OPERATORS = {
    ast.UAdd: lambda value: value,
    ast.USub: lambda value: -value,
}
_FUNCTIONS = {"abs", "clip", "log", "rank", "sqrt", "zscore"}


def factor_dependencies(expression: str) -> tuple[str, ...]:
    """Return referenced base-factor names after validating the expression AST."""

    tree = _parse(expression)
    names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id not in _FUNCTIONS
    }
    if not names:
        raise FactorExpressionError("factor expression must reference at least one base factor")
    return tuple(sorted(names))


def evaluate_factor_expression(
    expression: str,
    values: Mapping[str, pd.Series],
) -> pd.Series:
    """Evaluate a validated expression against aligned cross-sectional series."""

    tree = _parse(expression)
    dependencies = factor_dependencies(expression)
    missing = sorted(set(dependencies) - set(values))
    if missing:
        raise FactorExpressionError(f"missing factor inputs: {missing}")
    result = _evaluate(tree.body, values)
    if not isinstance(result, pd.Series):
        raise FactorExpressionError("factor expression must produce a cross-sectional series")
    return pd.to_numeric(result, errors="coerce").replace([np.inf, -np.inf], np.nan)


def _parse(expression: str) -> ast.Expression:
    text = str(expression).strip()
    if not text:
        raise FactorExpressionError("factor expression must not be empty")
    if len(text) > 500:
        raise FactorExpressionError("factor expression must be at most 500 characters")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise FactorExpressionError(f"invalid factor expression: {exc.msg}") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 100:
        raise FactorExpressionError("factor expression is too complex")
    for node in nodes:
        if isinstance(node, (ast.Expression, ast.Load, ast.Name, ast.Constant)):
            continue
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            continue
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
            continue
        if isinstance(node, tuple(_BINARY_OPERATORS) + tuple(_UNARY_OPERATORS)):
            continue
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
                raise FactorExpressionError("factor expression uses an unsupported function")
            if node.keywords:
                raise FactorExpressionError("factor expression functions do not accept keyword arguments")
            continue
        raise FactorExpressionError(f"unsupported factor expression element: {type(node).__name__}")
    return tree


def _evaluate(node: ast.AST, values: Mapping[str, pd.Series]) -> pd.Series | float:
    if isinstance(node, ast.Name):
        if node.id not in values:
            raise FactorExpressionError(f"unknown factor input: {node.id}")
        return pd.to_numeric(values[node.id], errors="coerce")
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            raise FactorExpressionError("factor constants must be numeric")
        numeric = float(node.value)
        if not np.isfinite(numeric) or abs(numeric) > 1_000_000:
            raise FactorExpressionError("factor constant is outside the supported range")
        return numeric
    if isinstance(node, ast.UnaryOp):
        return _UNARY_OPERATORS[type(node.op)](_evaluate(node.operand, values))
    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left, values)
        right = _evaluate(node.right, values)
        if isinstance(node.op, ast.Pow) and not isinstance(right, float):
            raise FactorExpressionError("factor exponents must be scalar constants")
        if isinstance(node.op, ast.Pow) and abs(right) > 4:
            raise FactorExpressionError("factor exponent must be between -4 and 4")
        if isinstance(node.op, ast.Div):
            if isinstance(right, pd.Series):
                right = right.replace(0, np.nan)
            elif right == 0:
                raise FactorExpressionError("factor expression divides by zero")
        return _BINARY_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        arguments = [_evaluate(argument, values) for argument in node.args]
        return _call(node.func.id, arguments)
    raise FactorExpressionError(f"unsupported factor expression element: {type(node).__name__}")


def _call(name: str, arguments: list[pd.Series | float]) -> pd.Series:
    if name == "clip":
        if len(arguments) != 3 or not isinstance(arguments[0], pd.Series):
            raise FactorExpressionError("clip expects clip(series, lower, upper)")
        lower, upper = arguments[1], arguments[2]
        if not isinstance(lower, float) or not isinstance(upper, float) or lower > upper:
            raise FactorExpressionError("clip bounds must be ordered scalar constants")
        return arguments[0].clip(lower=lower, upper=upper)
    if len(arguments) != 1 or not isinstance(arguments[0], pd.Series):
        raise FactorExpressionError(f"{name} expects exactly one factor series")
    value = arguments[0]
    if name == "abs":
        return value.abs()
    if name == "log":
        return np.log(value.where(value > 0))
    if name == "rank":
        return value.rank(pct=True)
    if name == "sqrt":
        return np.sqrt(value.where(value >= 0))
    if name == "zscore":
        deviation = float(value.std(ddof=0))
        return (value - float(value.mean())) / deviation if deviation > 0 else value * 0.0
    raise FactorExpressionError(f"unsupported factor function: {name}")


__all__ = [
    "FactorExpressionError",
    "evaluate_factor_expression",
    "factor_dependencies",
]
