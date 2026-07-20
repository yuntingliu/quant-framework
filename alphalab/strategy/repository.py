"""Built-in and local strategy definitions with explicit mutability rules."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from alphalab.strategies import list_strategy_files
from alphalab.strategy.config import StrategyConfig
from alphalab.utils.paths import RUNTIME_APP_DIR

_STRATEGY_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


@dataclass(frozen=True)
class StrategyDefinition:
    id: str
    path: Path
    config: StrategyConfig
    built_in: bool

    def as_dict(self, *, include_yaml: bool = False) -> dict:
        item = {
            "id": self.id,
            "name": self.config.name,
            "description": self.config.description,
            "path": str(self.path),
            "factors": self.config.factor_names,
            "warnings": self.config.validate(),
            "built_in": self.built_in,
            "editable": not self.built_in,
            "source": "built_in" if self.built_in else "local",
        }
        if include_yaml:
            item["yaml"] = self.path.read_text(encoding="utf-8")
        return item


class StrategyRepository:
    """Resolve immutable package templates and ignored local strategy YAML."""

    def __init__(self, local_dir: str | Path | None = None):
        self.local_dir = (
            Path(local_dir) if local_dir is not None else RUNTIME_APP_DIR / "strategies"
        )

    def list(self) -> list[StrategyDefinition]:
        values = [self._definition(path, built_in=True) for path in list_strategy_files()]
        if self.local_dir.exists():
            values.extend(
                self._definition(path, built_in=False)
                for path in sorted(self.local_dir.glob("*.yaml"))
                if path.is_file()
            )
        return sorted(values, key=lambda item: (not item.built_in, item.id))

    def get(self, strategy_id: str) -> StrategyDefinition | None:
        normalized = _validate_id(strategy_id)
        for item in self.list():
            if item.id == normalized:
                return item
        return None

    def clone(self, source_id: str, target_id: str) -> StrategyDefinition:
        source = self.get(source_id)
        if source is None:
            raise KeyError(source_id)
        target = _validate_id(target_id)
        if self.get(target) is not None:
            raise FileExistsError(target)
        config = StrategyConfig.from_yaml_string(source.path.read_text(encoding="utf-8"))
        payload = config.to_yaml()
        payload = _replace_name(payload, target)
        return self.save(target, payload, create_only=True)

    def save(
        self,
        strategy_id: str,
        yaml_text: str,
        *,
        create_only: bool = False,
    ) -> StrategyDefinition:
        normalized = _validate_id(strategy_id)
        existing = self.get(normalized)
        if existing is not None and existing.built_in:
            raise PermissionError("Built-in strategies are immutable; clone the template first")
        if create_only and existing is not None:
            raise FileExistsError(normalized)
        config = StrategyConfig.from_yaml_string(yaml_text)
        if config.name != normalized:
            raise ValueError("Strategy YAML name must match the strategy id")
        warnings = config.validate()
        hard_errors = [
            warning
            for warning in warnings
            if warning in {"No factors defined", "Factor weights must sum to a positive value"}
        ]
        if hard_errors:
            raise ValueError("; ".join(hard_errors))
        self.local_dir.mkdir(parents=True, exist_ok=True)
        path = self.local_dir / f"{normalized}.yaml"
        temporary = path.with_suffix(".yaml.tmp")
        temporary.write_text(config.to_yaml(), encoding="utf-8")
        temporary.replace(path)
        return self._definition(path, built_in=False)

    def delete(self, strategy_id: str) -> bool:
        definition = self.get(strategy_id)
        if definition is None:
            return False
        if definition.built_in:
            raise PermissionError("Built-in strategies are immutable")
        definition.path.unlink(missing_ok=False)
        return True

    @staticmethod
    def validate_yaml(yaml_text: str) -> dict:
        config = StrategyConfig.from_yaml_string(yaml_text)
        return {
            "valid": not any(
                warning
                in {"No factors defined", "Factor weights must sum to a positive value"}
                for warning in config.validate()
            ),
            "name": config.name,
            "factors": config.factor_names,
            "warnings": config.validate(),
            "normalized_yaml": config.to_yaml(),
        }

    @staticmethod
    def _definition(path: Path, *, built_in: bool) -> StrategyDefinition:
        return StrategyDefinition(
            id=path.stem,
            path=path,
            config=StrategyConfig.from_yaml(path),
            built_in=built_in,
        )


def _validate_id(value: str) -> str:
    normalized = str(value).strip().lower()
    if not _STRATEGY_ID.fullmatch(normalized):
        raise ValueError(
            "strategy id must be 2-64 lowercase letters, numbers, underscores, or hyphens"
        )
    return normalized


def _replace_name(yaml_text: str, target: str) -> str:
    lines = yaml_text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("name:"):
            lines[index] = f"name: {target}"
            break
    return "\n".join(lines) + "\n"


__all__ = ["StrategyDefinition", "StrategyRepository"]
