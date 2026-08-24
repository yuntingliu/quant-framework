"""Compose three version-pinned component sources into one executable module."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Mapping

from alphalab.strategy.python_runtime import python_source_sha256, validate_python_source
from alphalab.pipeline.models import STAGE_ENTRYPOINTS, STAGE_NAMES


@dataclass(frozen=True)
class ComposedStrategy:
    source: str
    source_sha256: str
    manifest: tuple[dict[str, Any], ...]


def compose_strategy(components: Mapping[str, Mapping[str, Any]]) -> ComposedStrategy:
    if set(components) != set(STAGE_NAMES):
        raise ValueError("composition requires exactly the three pipeline stages")
    definitions: set[str] = set()
    sections: list[str] = [
        '"""Frozen AlphaLab three-stage strategy. Generated from version-pinned components."""',
    ]
    manifest: list[dict[str, Any]] = []
    for stage in STAGE_NAMES:
        item = dict(components[stage])
        source = str(item["source"]).strip() + "\n"
        entrypoint = STAGE_ENTRYPOINTS[stage]
        validation = validate_python_source(source, entrypoint)
        tree = ast.parse(source)
        names = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        duplicate = sorted(definitions & names)
        if duplicate:
            raise ValueError(f"component {item['component_id']} has duplicate definitions: {duplicate}")
        definitions.update(names)
        sections.extend(
            [
                "",
                f"# --- {stage}: {item['component_id']}@{item['version']} ---",
                source.rstrip(),
            ]
        )
        manifest.append(
            {
                "stage": stage,
                "component_id": item["component_id"],
                "version": int(item["version"]),
                "entrypoint": entrypoint,
                "source_sha256": validation["sha256"],
                "parameters": dict(item.get("parameters") or {}),
            }
        )
    sections.extend(["", _RUNNER_SOURCE.strip(), ""])
    source = "\n".join(sections)
    validate_python_source(source, "run_strategy")
    return ComposedStrategy(
        source=source,
        source_sha256=python_source_sha256(source),
        manifest=tuple(manifest),
    )


_RUNNER_SOURCE = '''def run_stage(context):
    """Execute the requested stage and only the upstream stages it depends on."""
    target_stage = str(context.get("target_stage", "")).strip().lower()
    stage_order = ("selection", "portfolio", "execution")
    if target_stage not in stage_order:
        raise ValueError("target_stage must name one of the three pipeline stages")
    parameters = context.get("stage_parameters", {})

    selection = select_assets({
        **context,
        "parameters": parameters.get("selection", {}),
    })
    outputs = {"selection": selection}
    if target_stage == "selection":
        return outputs

    portfolio = construct_portfolio({
        **context,
        "parameters": parameters.get("portfolio", {}),
        "selection": selection,
    })
    outputs["portfolio"] = portfolio
    if target_stage == "portfolio":
        return outputs

    execution = configure_execution({
        **context,
        "parameters": parameters.get("execution", {}),
        "selection": selection,
        "portfolio": portfolio,
        "target_weights": dict(portfolio.get("weights", {})),
    })
    outputs["execution"] = execution
    return outputs


def run_strategy(context):
    """Execute the complete strategy for backtest and final strategy runs."""
    outputs = run_stage({**context, "target_stage": "execution"})
    return {**outputs, "weights": dict(outputs["portfolio"].get("weights", {}))}
'''


__all__ = ["ComposedStrategy", "compose_strategy"]
