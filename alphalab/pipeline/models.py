"""Public objects for the six-stage Python research pipeline."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping


STAGE_NAMES = ("universe", "selection", "timing", "portfolio", "risk", "execution")
STAGE_ENTRYPOINTS = {
    "universe": "build_universe",
    "selection": "select_assets",
    "timing": "compute_exposure",
    "portfolio": "construct_portfolio",
    "risk": "apply_risk",
    "execution": "configure_execution",
}
OBJECT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


def normalize_id(value: str, *, label: str = "id") -> str:
    normalized = str(value).strip().lower()
    if not OBJECT_ID.fullmatch(normalized):
        raise ValueError(
            f"{label} must be 2-64 lowercase letters, numbers, underscores, or hyphens"
        )
    return normalized


@dataclass(frozen=True)
class ComponentRef:
    component_id: str
    version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "component_id", normalize_id(self.component_id, label="component_id"))
        if int(self.version) < 1:
            raise ValueError("component version must be >= 1")
        object.__setattr__(self, "version", int(self.version))

    def to_dict(self) -> dict[str, Any]:
        return {"component_id": self.component_id, "version": self.version}


@dataclass(frozen=True)
class PipelineProject:
    id: str
    name: str
    description: str
    components: Mapping[str, ComponentRef]
    settings: Mapping[str, Any] = field(default_factory=dict)
    revision: int = 1
    built_in: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", normalize_id(self.id, label="project id"))
        if not str(self.name).strip():
            raise ValueError("project name must not be empty")
        missing = [stage for stage in STAGE_NAMES if stage not in self.components]
        unknown = sorted(set(self.components) - set(STAGE_NAMES))
        if missing or unknown:
            raise ValueError(f"project requires exactly six stages; missing={missing}, unknown={unknown}")
        object.__setattr__(
            self,
            "components",
            {
                stage: ref if isinstance(ref, ComponentRef) else ComponentRef(**dict(ref))
                for stage, ref in self.components.items()
            },
        )
        object.__setattr__(self, "settings", dict(self.settings))
        if int(self.revision) < 1:
            raise ValueError("project revision must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "revision": self.revision,
            "built_in": self.built_in,
            "editable": not self.built_in,
            "components": {stage: self.components[stage].to_dict() for stage in STAGE_NAMES},
            "settings": dict(self.settings),
        }


__all__ = [
    "ComponentRef",
    "PipelineProject",
    "STAGE_ENTRYPOINTS",
    "STAGE_NAMES",
    "normalize_id",
]
