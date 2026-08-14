"""Built-in and local market-timing strategy definitions."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alphalab.strategy.repository import replace_strategy_name, validate_strategy_id
from alphalab.strategy.timing import TimingStrategyConfig
from alphalab.strategy.python_runtime import (
    python_source_sha256,
    validate_python_source,
)
from alphalab.timing_strategies import list_timing_strategy_files
from alphalab.utils.paths import RUNTIME_APP_DIR


@dataclass(frozen=True)
class TimingStrategyDefinition:
    id: str
    path: Path
    config: TimingStrategyConfig
    built_in: bool

    def as_dict(self, *, include_yaml: bool = False) -> dict:
        python_path = self.path.with_suffix(".py")
        python_source = (
            python_path.read_text(encoding="utf-8")
            if self.config.implementation.kind == "python" and python_path.exists()
            else None
        )
        warnings = self.config.validate()
        if self.config.implementation.kind == "python" and python_source is None:
            warnings = [*warnings, "Python source is missing"]
        item = {
            "id": self.id,
            "strategy_type": "market_timing",
            "name": self.config.name,
            "description": self.config.description,
            "path": str(self.path),
            "factors": [],
            "signals": self.config.signal_names,
            "implementation": self.config.implementation.kind,
            "warnings": warnings,
            "built_in": self.built_in,
            "editable": not self.built_in,
            "source": "built_in" if self.built_in else "local",
        }
        if include_yaml:
            item["yaml"] = self.path.read_text(encoding="utf-8")
            item["config"] = self.config.to_dict()
            item["python_source"] = python_source
            item["python_source_sha256"] = (
                python_source_sha256(python_source) if python_source is not None else None
            )
        return item


class TimingStrategyRepository:
    """Resolve immutable timing templates and ignored local YAML definitions."""

    def __init__(self, local_dir: str | Path | None = None):
        self.local_dir = (
            Path(local_dir) if local_dir is not None else RUNTIME_APP_DIR / "timing_strategies"
        )

    def list(self) -> list[TimingStrategyDefinition]:
        values = [self._definition(path, built_in=True) for path in list_timing_strategy_files()]
        if self.local_dir.exists():
            values.extend(
                self._definition(path, built_in=False)
                for path in sorted(self.local_dir.glob("*.yaml"))
                if path.is_file()
            )
        return sorted(values, key=lambda item: (not item.built_in, item.id))

    def get(self, strategy_id: str) -> TimingStrategyDefinition | None:
        normalized = validate_strategy_id(strategy_id)
        return next((item for item in self.list() if item.id == normalized), None)

    def clone(self, source_id: str, target_id: str) -> TimingStrategyDefinition:
        source = self.get(source_id)
        if source is None:
            raise KeyError(source_id)
        target = validate_strategy_id(target_id)
        if self.get(target) is not None:
            raise FileExistsError(target)
        payload = replace_strategy_name(source.config.to_yaml(), target)
        python_source = (
            source.path.with_suffix(".py").read_text(encoding="utf-8")
            if source.config.implementation.kind == "python"
            else None
        )
        return self.save(
            target,
            payload,
            python_source=python_source,
            create_only=True,
        )

    def save(
        self,
        strategy_id: str,
        yaml_text: str,
        *,
        python_source: str | None = None,
        create_only: bool = False,
    ) -> TimingStrategyDefinition:
        normalized = validate_strategy_id(strategy_id)
        existing = self.get(normalized)
        if existing is not None and existing.built_in:
            raise PermissionError("Built-in strategies are immutable; clone the template first")
        if create_only and existing is not None:
            raise FileExistsError(normalized)
        config = TimingStrategyConfig.from_yaml_string(yaml_text)
        if config.name != normalized:
            raise ValueError("Strategy YAML name must match the strategy id")
        hard_errors = [
            warning
            for warning in config.validate()
            if warning
            in {
                "Barebone timing research currently requires the MKT series",
                "No timing signals defined",
                "Timing signal weights must sum to a positive value",
            }
        ]
        if hard_errors:
            raise ValueError("; ".join(hard_errors))
        if config.implementation.kind == "python":
            if python_source is None:
                raise ValueError("python_source is required for a Python strategy")
            validate_python_source(
                python_source,
                config.implementation.entrypoint,
            )
        self.local_dir.mkdir(parents=True, exist_ok=True)
        path = self.local_dir / f"{normalized}.yaml"
        temporary = path.with_suffix(".yaml.tmp")
        temporary.write_text(config.to_yaml(), encoding="utf-8")
        source_path = path.with_suffix(".py")
        if config.implementation.kind == "python":
            source_temporary = source_path.with_suffix(".py.tmp")
            source_temporary.write_text(python_source or "", encoding="utf-8")
            source_temporary.replace(source_path)
        temporary.replace(path)
        if config.implementation.kind != "python":
            source_path.unlink(missing_ok=True)
        return self._definition(path, built_in=False)

    def delete(self, strategy_id: str) -> bool:
        definition = self.get(strategy_id)
        if definition is None:
            return False
        if definition.built_in:
            raise PermissionError("Built-in strategies are immutable")
        definition.path.unlink(missing_ok=False)
        definition.path.with_suffix(".py").unlink(missing_ok=True)
        return True

    @staticmethod
    def validate_yaml(yaml_text: str, python_source: str | None = None) -> dict:
        return TimingStrategyRepository.validate_config(
            TimingStrategyConfig.from_yaml_string(yaml_text),
            python_source,
        )

    @staticmethod
    def validate_dict(
        raw: dict[str, Any],
        python_source: str | None = None,
    ) -> dict:
        return TimingStrategyRepository.validate_config(
            TimingStrategyConfig.from_dict(raw),
            python_source,
        )

    @staticmethod
    def validate_config(
        config: TimingStrategyConfig,
        python_source: str | None = None,
    ) -> dict:
        warnings = config.validate()
        configured = config.implementation.kind == "configured"
        hard_errors = {
            "Barebone timing research currently requires the MKT series",
            "No timing signals defined",
            "Timing signal weights must sum to a positive value",
        }
        python_check = None
        if config.implementation.kind == "python":
            try:
                python_check = validate_python_source(
                    python_source or "",
                    config.implementation.entrypoint,
                )
            except ValueError as exc:
                warnings = [*warnings, str(exc)]
        checks = [
            {
                "code": "strategy_name",
                "status": "passed",
                "severity": "info",
                "message": f"Strategy name is {config.name}",
            },
            {
                "code": "timing_market",
                "status": "passed" if config.market_factor == "MKT" else "failed",
                "severity": "info" if config.market_factor == "MKT" else "error",
                "message": (
                    "MKT is the timed market return series"
                    if config.market_factor == "MKT"
                    else "Barebone timing research currently requires the MKT series"
                ),
            },
            {
                "code": "timing_signals",
                "status": (
                    "passed"
                    if config.signals or config.implementation.kind == "python"
                    else "failed"
                ),
                "severity": (
                    "info"
                    if config.signals or config.implementation.kind == "python"
                    else "error"
                ),
                "message": (
                    f"{len(config.signals)} timing signals are defined"
                    if config.signals
                    else "Python strategy computes market exposure from MKT history"
                    if config.implementation.kind == "python"
                    else "No timing signals defined"
                ),
            },
            {
                "code": "python_source",
                "status": (
                    "passed"
                    if config.implementation.kind == "configured" or python_check
                    else "failed"
                ),
                "severity": (
                    "info"
                    if config.implementation.kind == "configured" or python_check
                    else "error"
                ),
                "message": (
                    "Configured strategy uses registered timing signals"
                    if config.implementation.kind == "configured"
                    else f"Python source validated ({python_check['bytes']} bytes)"
                    if python_check
                    else warnings[-1]
                ),
            },
            {
                "code": "timing_weight_total",
                "status": (
                    "passed"
                    if not configured or config.total_weight > 0
                    else "failed"
                ),
                "severity": (
                    "info"
                    if not configured or config.total_weight > 0
                    else "error"
                ),
                "message": (
                    "Python strategy returns market exposure directly"
                    if not configured
                    else f"Timing signal weights sum to {config.total_weight:.6g}"
                    if config.total_weight > 0
                    else "Timing signal weights must sum to a positive value"
                ),
            },
            {
                "code": "timing_exposure",
                "status": "passed",
                "severity": "info",
                "message": (
                    f"Exposure range is {config.position.min_exposure:.0%}-"
                    f"{config.position.max_exposure:.0%}"
                ),
            },
        ]
        return {
            "valid": (
                not any(warning in hard_errors for warning in warnings)
                and (
                    config.implementation.kind == "configured"
                    or python_check is not None
                )
            ),
            "strategy_type": "market_timing",
            "implementation": config.implementation.kind,
            "name": config.name,
            "factors": [],
            "signals": config.signal_names,
            "warnings": warnings,
            "normalized_yaml": config.to_yaml(),
            "config": config.to_dict(),
            "python_source": python_source,
            "python_source_sha256": (
                python_check["sha256"] if python_check is not None else None
            ),
            "checks": checks,
        }

    @staticmethod
    def _definition(path: Path, *, built_in: bool) -> TimingStrategyDefinition:
        return TimingStrategyDefinition(
            id=path.stem,
            path=path,
            config=TimingStrategyConfig.from_yaml(path),
            built_in=built_in,
        )


__all__ = ["TimingStrategyDefinition", "TimingStrategyRepository"]
