"""Compose six version-pinned component sources into one executable module."""
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
        raise ValueError("composition requires exactly the six pipeline stages")
    definitions: set[str] = set()
    sections: list[str] = [
        '"""Frozen AlphaLab six-stage strategy. Generated from version-pinned components."""',
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


_RUNNER_SOURCE = '''def run_strategy(context):
    """Execute the complete strategy and retain every stage result for audit."""
    parameters = context.get("stage_parameters", {})

    universe = build_universe({**context, "parameters": parameters.get("universe", {})})
    selection = select_assets({
        **context,
        "parameters": parameters.get("selection", {}),
        "universe_symbols": list(universe.get("symbols", [])),
    })
    timing = compute_exposure({
        **context,
        "parameters": parameters.get("timing", {}),
        "universe": universe,
        "selection": selection,
    })
    portfolio = construct_portfolio({
        **context,
        "parameters": parameters.get("portfolio", {}),
        "universe": universe,
        "selection": selection,
        "timing": timing,
    })
    risk = apply_risk({
        **context,
        "parameters": parameters.get("risk", {}),
        "universe": universe,
        "selection": selection,
        "timing": timing,
        "portfolio": portfolio,
    })
    execution = create_orders({
        **context,
        "parameters": parameters.get("execution", {}),
        "universe": universe,
        "selection": selection,
        "timing": timing,
        "portfolio": portfolio,
        "risk": risk,
        "target_weights": dict(risk.get("weights", {})),
    })
    return {
        "universe": universe,
        "selection": selection,
        "timing": timing,
        "portfolio": portfolio,
        "risk": risk,
        "execution": execution,
        "weights": dict(risk.get("weights", {})),
    }
'''


__all__ = ["ComposedStrategy", "compose_strategy"]
